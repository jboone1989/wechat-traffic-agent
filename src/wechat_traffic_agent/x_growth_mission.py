"""Bridge a measurable X audience objective into Agent Runtime External Missions.

This module does not publish, follow, like, repost, or browse X. It only consumes
an official X identity observation, updates the mission metric, settles bounded
experiments from elapsed follower outcomes, and asks Agent Runtime for the next
advisory experiment.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any

MISSION_METRIC = "x.followers"
MISSION_SCHEMA = "content-agent-x-growth-mission.v1"
DEFAULT_TARGET = 5000
DEFAULT_DURATION_DAYS = 30
DEFAULT_EXPERIMENT_HOURS = 24
MIN_OBSERVATION_INTERVAL = timedelta(hours=6)
MISSION_PLANNING_CONTEXT_REF = "x-mission-planning-context:v1"
MISSION_BASELINE_GUARDRAIL_SCHEMA = "content-agent-x-mission-baseline-guardrails.v1"
MISSION_HARD_STOP_WINDOW = timedelta(hours=72)
MISSION_HARD_STOP_DECLINE = 0.02
MISSION_PROGRESS_SCHEMA = "content-agent-x-growth-roadmap-progress.v1"
MISSION_PUBLICATION_BASELINE_SCHEMA = "content-agent-x-mission-publication-baseline.v1"

MISSION_CONSTRAINTS = (
    "obey applicable law",
    "obey current X platform and automation policy",
    "use official X API/OAuth for automated X actions",
    "do not use artificial account-metric inflation",
    "do not use churn, reciprocal-engagement schemes, or indiscriminate following",
    "do not use automated likes or unsolicited repetitive replies",
    "preserve account survival and long-term reputation",
    "respect resource budget and existing execution authority",
)


def _enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def follower_count_from_user(user: dict[str, Any]) -> int | None:
    """Return the official public followers_count without inventing a zero."""
    public_metrics = user.get("public_metrics")
    if not isinstance(public_metrics, dict):
        return None
    raw = public_metrics.get("followers_count")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    value = int(raw)
    return value if value >= 0 else None


def x_growth_mission_context(state_root: Path) -> dict[str, Any]:
    payload = _read_json(Path(state_root) / "content" / "x" / "growth_mission.json")
    if payload.get("schema_version") != MISSION_SCHEMA:
        return {
            "status": "UNAVAILABLE",
            "generation_authority": "NONE",
            "publication_authority": False,
        }
    return payload


class XGrowthMissionCoordinator:
    def __init__(
        self,
        *,
        runtime: Any,
        state_root: Path,
        target: int = DEFAULT_TARGET,
        duration_days: int = DEFAULT_DURATION_DAYS,
        experiment_hours: int = DEFAULT_EXPERIMENT_HOURS,
        enabled: bool = True,
    ) -> None:
        self.runtime = runtime
        self.state_root = Path(state_root)
        self.target = max(1, int(target))
        self.duration = timedelta(days=max(1, int(duration_days)))
        self.experiment_window = timedelta(hours=max(6, int(experiment_hours)))
        self.enabled = bool(enabled)
        self.path = self.state_root / "content" / "x" / "growth_mission.json"

    @classmethod
    def from_environment(cls, *, runtime: Any, state_root: Path) -> "XGrowthMissionCoordinator":
        return cls(
            runtime=runtime,
            state_root=state_root,
            target=int(os.environ.get("CONTENT_X_GROWTH_TARGET", str(DEFAULT_TARGET))),
            duration_days=int(
                os.environ.get("CONTENT_X_GROWTH_DURATION_DAYS", str(DEFAULT_DURATION_DAYS))
            ),
            experiment_hours=int(
                os.environ.get(
                    "CONTENT_X_GROWTH_EXPERIMENT_HOURS",
                    str(DEFAULT_EXPERIMENT_HOURS),
                )
            ),
            enabled=_enabled("CONTENT_X_GROWTH_MISSION_ENABLED"),
        )

    def _mission(self) -> Any | None:
        missions = list(getattr(self.runtime, "external_missions", ()) or ())
        matching = [row for row in missions if str(getattr(row, "metric", "")) == MISSION_METRIC]
        active = [
            row
            for row in matching
            if _enum_value(getattr(row, "status", "")) == "ACTIVE"
        ]
        return active[-1] if active else (matching[-1] if matching else None)

    def _ensure_mission(
        self,
        *,
        followers: int,
        now: datetime,
        evidence_refs: list[str],
    ) -> Any:
        mission = self._mission()
        if mission is not None:
            return mission
        if followers >= self.target:
            raise ValueError("X growth mission target must exceed the current follower count")
        create = getattr(self.runtime, "submit_external_mission", None)
        if not callable(create):
            raise RuntimeError("Agent Runtime External Mission capability is unavailable")
        from agent_runtime.goals import MissionDirection

        mission = create(
            (
                f"Grow Ferro's real X audience from {followers:,} to "
                f"{self.target:,} followers within {self.duration.days} days"
            ),
            metric=MISSION_METRIC,
            baseline=float(followers),
            target=float(self.target),
            deadline=now + self.duration,
            direction=MissionDirection.AT_LEAST,
            constraints=list(MISSION_CONSTRAINTS),
            evidence_refs=list(dict.fromkeys(evidence_refs)),
            issued_by="operator",
        )
        self._persist_runtime()
        return mission

    def _should_record(self, mission: Any, followers: int, now: datetime) -> bool:
        latest = self.runtime.missions.latest_observation(mission.mission_id)
        if int(latest.value) != followers:
            return True
        return now - latest.observed_at >= MIN_OBSERVATION_INTERVAL

    def _running_experiment(self, mission_id: str) -> Any | None:
        rows = [
            row
            for row in self.runtime.missions.experiments
            if row.mission_id == mission_id
            and _enum_value(row.status) == "RUNNING"
        ]
        return rows[-1] if rows else None

    def _open_experiment(self, mission_id: str) -> Any | None:
        rows = [
            row
            for row in self.runtime.missions.experiments
            if row.mission_id == mission_id
            and _enum_value(row.status) in {"PROPOSED", "RUNNING"}
        ]
        return rows[-1] if rows else None

    def _experiment_window(self, experiment: Any) -> timedelta:
        raw = getattr(experiment, "measurement_window_hours", None)
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            hours = float(raw)
            if hours > 0:
                return timedelta(hours=hours)
        return self.experiment_window

    def _experiment_contract_matches(self, experiment: Any) -> bool:
        raw = getattr(experiment, "measurement_window_hours", None)
        metric = getattr(experiment, "settlement_metric", None)
        if not isinstance(raw, (int, float)) or isinstance(raw, bool):
            return False
        expected_hours = self.experiment_window.total_seconds() / 3600.0
        return (
            abs(float(raw) - expected_hours) <= 1e-9
            and str(metric or "").strip() == MISSION_METRIC
        )

    def _experiment_outcome(
        self,
        *,
        mission: Any,
        experiment: Any,
        followers: int,
        now: datetime,
        evidence_refs: list[str],
    ) -> dict[str, Any] | None:
        started_at = getattr(experiment, "started_at", None)
        before = getattr(experiment, "before_value", None)
        if started_at is None or before is None:
            return None
        experiment_window = self._experiment_window(experiment)
        due_at = started_at + experiment_window
        if now < due_at:
            return {
                "status": "RUNNING",
                "experiment_id": experiment.experiment_id,
                "due_at": due_at.isoformat(),
            }

        remaining_at_start = max(
            experiment_window.total_seconds(),
            (mission.deadline - started_at).total_seconds(),
        )
        required_remaining = max(0.0, float(mission.target) - float(before))
        expected_delta = required_remaining * (
            experiment_window.total_seconds() / remaining_at_start
        )
        actual_delta = float(followers) - float(before)
        from agent_runtime.goals import MissionConclusion

        if actual_delta > 0 and actual_delta >= 0.8 * expected_delta:
            conclusion = MissionConclusion.SUPPORTED
        elif actual_delta <= 0 and expected_delta >= 1.0:
            conclusion = MissionConclusion.REFUTED
        else:
            conclusion = MissionConclusion.INCONCLUSIVE

        settle = getattr(self.runtime, "settle_mission_experiment", None)
        if not callable(settle):
            raise RuntimeError("Agent Runtime mission settlement capability is unavailable")
        settled = settle(
            experiment.experiment_id,
            result_value=float(followers),
            conclusion=conclusion,
            evidence_refs=list(dict.fromkeys(evidence_refs)),
            notes=(
                "Follower change over the bounded measurement window was used as "
                "mission feedback. This is observational feedback, not a causal "
                "claim that the experiment alone caused the change. "
                f"actual_delta={actual_delta:.2f}; expected_pace_delta={expected_delta:.2f}."
            ),
        )
        self._persist_runtime()
        return {
            "status": "SETTLED",
            "experiment_id": settled.experiment_id,
            "conclusion": conclusion.value,
            "actual_delta": actual_delta,
            "expected_pace_delta": expected_delta,
        }

    def _planning_context(self) -> dict[str, Any]:
        return {
            "schema_version": MISSION_PLANNING_CONTEXT_REF,
            "measurement": {
                "follower_metric_source": (
                    "official X identity public_metrics.followers_count"
                ),
                "prospective_follower_snapshots": "AVAILABLE",
                "post_public_metrics": (
                    "LIMITED_TO_X_PUBLIC_METRICS_WHEN_API_READS_ARE_AVAILABLE"
                ),
                "historical_post_level_follower_attribution": "UNAVAILABLE",
                "post_level_profile_visits": "UNAVAILABLE",
                "post_level_follows": "UNAVAILABLE",
                "retrospective_90_day_new_follower_uplift": "UNAVAILABLE",
                "browser_fallback_is_metric_authority": False,
                "causal_attribution": (
                    "UNAVAILABLE; use bounded prospective observational evidence"
                ),
            },
            "execution": {
                "website_content_pipeline": "AVAILABLE",
                "x_publication": (
                    "AVAILABLE only through existing gated official API or "
                    "independently verified browser publication paths"
                ),
                "publication_starts_experiment": True,
                "automated_follow_like_repost": "FORBIDDEN_BY_MISSION_CONSTRAINTS",
            },
            "experiment_contract": {
                "measurement_window_hours": (
                    self.experiment_window.total_seconds() / 3600.0
                ),
                "settlement_metric": MISSION_METRIC,
            },
            "learning": {
                "measurement_window_hours": (
                    self.experiment_window.total_seconds() / 3600.0
                ),
                "settlement_metric": MISSION_METRIC,
                "settlement_is_causal_claim": False,
                "replan_when_required_evidence_is_unavailable": True,
            },
            "operator_authority": {
                "mission_goal_is_approved": True,
                "routine_bounded_execution_requires_new_operator_approval": False,
                "existing_execution_gates_remain_authoritative": True,
            },
        }

    def _hard_stop_state(
        self,
        *,
        mission: Any,
        followers: int,
        now: datetime,
    ) -> dict[str, Any]:
        cutoff = now - MISSION_HARD_STOP_WINDOW
        points: list[tuple[datetime, float, str]] = []
        if mission.created_at >= cutoff:
            points.append((mission.created_at, float(mission.baseline), "mission-baseline"))
        for row in list(getattr(self.runtime.missions, "observations", ()) or ()):
            if (
                row.mission_id == mission.mission_id
                and row.source == "official-x-identity"
                and row.observed_at >= cutoff
            ):
                points.append((row.observed_at, float(row.value), row.observation_id))
        points.append((now, float(followers), "current-official-x-identity"))
        peak_at, peak_value, peak_ref = max(points, key=lambda item: item[1])
        decline_ratio = (
            0.0
            if peak_value <= 0
            else max(0.0, (peak_value - float(followers)) / peak_value)
        )
        active = decline_ratio > MISSION_HARD_STOP_DECLINE
        return {
            "active": active,
            "window_hours": MISSION_HARD_STOP_WINDOW.total_seconds() / 3600.0,
            "decline_threshold": MISSION_HARD_STOP_DECLINE,
            "current": float(followers),
            "peak": peak_value,
            "peak_at": peak_at.isoformat(),
            "peak_ref": peak_ref,
            "decline_ratio": decline_ratio,
            "action": (
                "PAUSE_MISSION_LINKED_X_PUBLICATION"
                if active
                else "ALLOW_WITH_EXISTING_GATES"
            ),
        }

    def publication_guardrail(self) -> dict[str, Any]:
        if not self.enabled:
            return {"allowed": True, "status": "MISSION_DISABLED"}
        payload = _read_json(self.path)
        hard_stop = payload.get("hard_stop")
        if not isinstance(hard_stop, dict):
            return {"allowed": True, "status": "NO_ACTIVE_HARD_STOP"}
        active = bool(hard_stop.get("active"))
        return {
            "allowed": not active,
            "status": "HARD_STOP_ACTIVE" if active else "ALLOWED",
            "hard_stop": hard_stop,
        }

    def _baseline_guardrail_report(
        self,
        *,
        mission: Any,
        roadmap: Any,
        step: Any,
        followers: int,
        identity_receipt_id: str,
        now: datetime,
        hard_stop: dict[str, Any],
    ) -> tuple[Path, dict[str, Any]]:
        payload = {
            "schema_version": MISSION_BASELINE_GUARDRAIL_SCHEMA,
            "generated_at": now.isoformat(),
            "mission_id": mission.mission_id,
            "roadmap_id": roadmap.roadmap_id,
            "roadmap_version": roadmap.version,
            "step_id": step.step_id,
            "step_title": step.title,
            "step_exit_criteria": step.exit_criteria,
            "baseline": {
                "metric": MISSION_METRIC,
                "value": float(followers),
                "source": "official X identity public_metrics.followers_count",
                "identity_receipt_id": str(identity_receipt_id),
            },
            "measurement": {
                "prospective_follower_snapshots": "ENABLED",
                "minimum_required_cadence_hours": 24.0,
                "runtime_record_interval_hours": (
                    MIN_OBSERVATION_INTERVAL.total_seconds() / 3600.0
                ),
                "authoritative_source": "official-x-identity",
                "browser_fallback_is_metric_authority": False,
                "causal_attribution": False,
                "post_public_metrics_when_available": [
                    "impression_count",
                    "like_count",
                    "reply_count",
                    "retweet_count",
                    "quote_count",
                    "bookmark_count",
                ],
                "unavailable_metrics": [
                    "historical_post_level_follower_attribution",
                    "post_level_profile_visits",
                    "post_level_follows",
                ],
            },
            "guardrails": {
                "hard_stop": hard_stop,
                "prohibited_tactics": [
                    "artificial follower inflation",
                    "follow-churn",
                    "reciprocal-follow or reciprocal-engagement schemes",
                    "indiscriminate following",
                    "automated likes",
                    "unsolicited repetitive replies",
                ],
                "policy_constraints": list(mission.constraints),
                "ratification": {
                    "basis": (
                        "operator-issued mission plus runtime adoption of a protective "
                        "execution guardrail"
                    ),
                    "new_human_approval_event": False,
                    "existing_execution_gates_remain_authoritative": True,
                },
            },
            "planning_context_ref": MISSION_PLANNING_CONTEXT_REF,
        }
        material = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        artifact_id = (
            "x-mission-baseline-guardrails:sha256:"
            + hashlib.sha256(material).hexdigest()
        )
        payload["artifact_id"] = artifact_id
        workspace = (
            self.state_root
            / "content"
            / "x"
            / "missions"
            / str(mission.mission_id)
        )
        path = workspace / "baseline_guardrails.json"
        _write_json(path, payload)
        return path, payload

    def _advance_ready_baseline_step(
        self,
        *,
        mission: Any,
        followers: int,
        identity_receipt_id: str,
        now: datetime,
        evidence_refs: list[str],
        hard_stop: dict[str, Any],
    ) -> dict[str, Any] | None:
        roadmap = self.runtime.missions.latest_roadmap(mission.mission_id)
        schedule_method = getattr(self.runtime, "mission_portfolio_schedule", None)
        start = getattr(self.runtime, "start_mission_step", None)
        complete = getattr(self.runtime, "complete_mission_step", None)
        if (
            roadmap is None
            or not callable(schedule_method)
            or not callable(start)
            or not callable(complete)
        ):
            return None
        schedule = schedule_method(max_parallelism=3)
        required_tags = {
            "x_api_oauth_read",
            "analytics",
            "legal_policy_review",
            "brand_review",
        }
        candidate = None
        for item in schedule.items:
            if item.mission_id != mission.mission_id:
                continue
            if _enum_value(item.status) not in {"READY", "RUNNING"}:
                continue
            step = self.runtime.missions.get_step(item.step_id)
            if not required_tags.issubset(set(step.resource_tags)):
                continue
            if "prospective follower snapshots" not in step.exit_criteria.lower():
                continue
            candidate = step
            break
        if candidate is None:
            return None
        started = start(candidate.step_id)
        path, report = self._baseline_guardrail_report(
            mission=mission,
            roadmap=roadmap,
            step=started,
            followers=followers,
            identity_receipt_id=identity_receipt_id,
            now=now,
            hard_stop=hard_stop,
        )
        completed = complete(
            candidate.step_id,
            evidence_refs=list(
                dict.fromkeys(
                    [
                        *evidence_refs,
                        str(report["artifact_id"]),
                        MISSION_PLANNING_CONTEXT_REF,
                    ]
                )
            ),
        )
        self._persist_runtime()
        return {
            "status": "COMPLETED",
            "step_id": completed.step_id,
            "title": completed.title,
            "artifact_id": report["artifact_id"],
            "artifact_path": str(path),
            "completed_at": (
                None
                if completed.completed_at is None
                else completed.completed_at.isoformat()
            ),
        }


    @staticmethod
    def _is_publication_step(step: Any) -> bool:
        tags = {str(value) for value in getattr(step, "resource_tags", ())}
        return bool(
            {"x_api_oauth_write", "x_publication_gated"} & tags
            and "content_creation" in tags
        )

    def _record_publication_for_roadmap(
        self,
        *,
        mission: Any,
        publication_receipt_id: str,
        post_id: str,
        now: datetime,
    ) -> dict[str, Any] | None:
        schedule_method = getattr(self.runtime, "mission_portfolio_schedule", None)
        start_method = getattr(self.runtime, "start_mission_step", None)
        if not callable(schedule_method) or not callable(start_method):
            return {"status": "RUNTIME_CAPABILITY_UNAVAILABLE"}
        roadmap = self.runtime.missions.latest_roadmap(mission.mission_id)
        if roadmap is None:
            return None
        steps = {step.step_id: step for step in roadmap.steps}
        schedule = schedule_method(max_parallelism=3)
        selected = None
        selected_status = None
        for item in schedule.items:
            if item.mission_id != mission.mission_id:
                continue
            status = _enum_value(item.status)
            if status not in {"READY", "RUNNING"}:
                continue
            step = steps.get(item.step_id)
            if step is not None and self._is_publication_step(step):
                selected = step
                selected_status = status
                break
        if selected is None:
            return {"status": "NO_READY_PUBLICATION_STEP"}

        if selected_status == "READY":
            selected = start_method(selected.step_id)
            transition_status = "STARTED"
        else:
            transition_status = "RECORDED"

        workspace = (
            self.state_root
            / "content"
            / "x"
            / "missions"
            / str(mission.mission_id)
        )
        progress_path = workspace / "roadmap_progress.json"
        progress = _read_json(progress_path)
        if (
            progress.get("schema_version") != MISSION_PROGRESS_SCHEMA
            or progress.get("roadmap_id") != roadmap.roadmap_id
        ):
            progress = {
                "schema_version": MISSION_PROGRESS_SCHEMA,
                "mission_id": mission.mission_id,
                "roadmap_id": roadmap.roadmap_id,
                "roadmap_version": roadmap.version,
                "steps": {},
            }
        step_rows = progress.setdefault("steps", {})
        row = step_rows.setdefault(
            selected.step_id,
            {
                "title": selected.title,
                "started_at": (
                    None
                    if selected.started_at is None
                    else selected.started_at.isoformat()
                ),
                "publications": [],
            },
        )
        publications = row.setdefault("publications", [])
        if not any(
            isinstance(item, dict)
            and item.get("publication_receipt_id") == str(publication_receipt_id)
            for item in publications
        ):
            publications.append(
                {
                    "publication_receipt_id": str(publication_receipt_id),
                    "post_id": str(post_id),
                    "published_at": now.isoformat(),
                }
            )
        row["publication_count"] = len(publications)
        progress["updated_at"] = now.isoformat()
        _write_json(progress_path, progress)
        digest = hashlib.sha256(progress_path.read_bytes()).hexdigest()
        progress_ref = f"x-mission-roadmap-progress:sha256:{digest}"
        selected.evidence_refs = list(
            dict.fromkeys(
                [
                    *selected.evidence_refs,
                    str(publication_receipt_id),
                    f"x-post:{str(post_id)}",
                    progress_ref,
                ]
            )
        )
        self._persist_runtime()
        return {
            "status": transition_status,
            "step_id": selected.step_id,
            "title": selected.title,
            "publication_count": len(publications),
            "progress_ref": progress_ref,
        }


    async def _ensure_roadmap(self, mission: Any) -> dict[str, Any]:
        needs_revision = getattr(self.runtime.missions, "roadmap_needs_revision", None)
        latest = getattr(self.runtime.missions, "latest_roadmap", None)
        if not callable(needs_revision) or not callable(latest):
            return {"status": "RUNTIME_CAPABILITY_UNAVAILABLE"}
        roadmap = latest(mission.mission_id)
        context_applied = (
            roadmap is not None
            and MISSION_PLANNING_CONTEXT_REF in set(roadmap.evidence_refs)
        )
        if not needs_revision(mission.mission_id) and context_applied:
            return {
                "status": "ACTIVE",
                "roadmap_id": roadmap.roadmap_id,
                "version": roadmap.version,
            }
        if not bool(getattr(self.runtime, "has_advisory_backend", False)):
            return {"status": "BACKEND_UNAVAILABLE"}
        deliberate = getattr(self.runtime, "deliberate_mission_roadmap", None)
        if not callable(deliberate):
            return {"status": "RUNTIME_CAPABILITY_UNAVAILABLE"}
        force_revision = roadmap is not None and not context_applied
        roadmap = await deliberate(
            mission.mission_id,
            max_tokens=1800,
            force_revision=force_revision,
            planning_context=self._planning_context(),
            evidence_refs=[MISSION_PLANNING_CONTEXT_REF],
        )
        self._persist_runtime()
        return {
            "status": "REPLANNED" if force_revision else "PLANNED",
            "roadmap_id": roadmap.roadmap_id,
            "version": roadmap.version,
            "rationale": roadmap.rationale,
            "step_count": len(roadmap.steps),
            "planning_context_ref": MISSION_PLANNING_CONTEXT_REF,
        }

    async def _ensure_experiment(self, mission: Any) -> dict[str, Any]:
        existing = self._open_experiment(mission.mission_id)
        existing_status = None if existing is None else _enum_value(existing.status)
        context_applied = (
            existing is not None
            and MISSION_PLANNING_CONTEXT_REF in set(existing.evidence_refs)
        )
        contract_matches = (
            existing is not None and self._experiment_contract_matches(existing)
        )
        if existing is not None and (
            existing_status == "RUNNING" or (context_applied and contract_matches)
        ):
            return {
                "status": existing_status,
                "experiment_id": existing.experiment_id,
                "strategy": existing.strategy,
                "planning_context_ref": (
                    MISSION_PLANNING_CONTEXT_REF if context_applied else None
                ),
                "measurement_window_hours": getattr(
                    existing, "measurement_window_hours", None
                ),
                "settlement_metric": getattr(existing, "settlement_metric", None),
            }

        force_revision = (
            existing is not None
            and existing_status == "PROPOSED"
            and (not context_applied or not contract_matches)
        )
        if not bool(getattr(self.runtime, "has_advisory_backend", False)):
            if force_revision:
                return {
                    "status": "REPLAN_BACKEND_UNAVAILABLE",
                    "experiment_id": existing.experiment_id,
                }
            return {"status": "BACKEND_UNAVAILABLE"}
        deliberate = getattr(self.runtime, "deliberate_external_mission", None)
        if not callable(deliberate):
            return {"status": "RUNTIME_CAPABILITY_UNAVAILABLE"}
        experiment = await deliberate(
            mission.mission_id,
            max_tokens=900,
            force_revision=force_revision,
            planning_context=self._planning_context(),
            evidence_refs=[MISSION_PLANNING_CONTEXT_REF],
        )
        self._persist_runtime()
        return {
            "status": "REPLANNED" if force_revision else "PROPOSED",
            "experiment_id": experiment.experiment_id,
            "strategy": experiment.strategy,
            "expected_effect": experiment.expected_effect,
            "risk_notes": list(experiment.risk_notes),
            "planning_context_ref": MISSION_PLANNING_CONTEXT_REF,
            "measurement_window_hours": experiment.measurement_window_hours,
            "settlement_metric": experiment.settlement_metric,
        }

    def publication_binding(self, *, cycle: str) -> dict[str, Any]:
        """Return whether a content cycle is still bound to the active X mission."""
        mission = self._mission()
        if mission is None or _enum_value(mission.status) != "ACTIVE":
            return {"status": "NO_ACTIVE_MISSION", "bound": False}
        cycle = str(cycle or "").strip()
        if not cycle:
            return {"status": "MISSING_CYCLE", "bound": False}
        folder = self.state_root / "content" / "continuous" / "cycles" / cycle
        plan = _read_json(folder / "mind_plan.json")
        goal = plan.get("goal") if isinstance(plan.get("goal"), dict) else {}
        mission_id = str(plan.get("external_mission_id") or "").strip()
        if (
            str(goal.get("origin") or "") != "EXTERNAL_MISSION"
            or mission_id != str(mission.mission_id)
        ):
            return {"status": "NOT_EXTERNAL_MISSION", "bound": False, "cycle": cycle}
        if (folder / "output_fallback.json").exists():
            return {"status": "OUTPUT_FALLBACK", "bound": False, "cycle": cycle}
        discovery = _read_json(folder / "mission_discovery.json")
        if str(discovery.get("mission_id") or "") != str(mission.mission_id):
            return {"status": "MISSION_DISCOVERY_MISMATCH", "bound": False, "cycle": cycle}
        result = _read_json(folder / "result.json")
        if str(result.get("status") or "").upper() != "PUBLISHED":
            return {"status": "MISSION_CONTENT_NOT_PUBLISHED", "bound": False, "cycle": cycle}
        publication = _read_json(folder / "publication.json")
        outcome = (
            publication.get("outcome_attribution")
            if isinstance(publication.get("outcome_attribution"), dict)
            else {}
        )
        required_topic = str(plan.get("required_topic") or "").strip()
        published_topic = str(outcome.get("topic") or "").strip()
        if required_topic and published_topic != required_topic:
            return {
                "status": "MISSION_TOPIC_MISMATCH",
                "bound": False,
                "cycle": cycle,
                "required_topic": required_topic,
                "published_topic": published_topic,
            }
        evidence_floor = _read_json(folder / "evidence_floor_check.json")
        if str(evidence_floor.get("status") or "") != "PASS":
            return {"status": "EVIDENCE_FLOOR_NOT_PASSED", "bound": False, "cycle": cycle}
        experiment = self._open_experiment(mission.mission_id)
        return {
            "status": "BOUND",
            "bound": True,
            "cycle": cycle,
            "mission_id": mission.mission_id,
            "experiment_id": None if experiment is None else experiment.experiment_id,
            "topic": published_topic or required_topic,
        }

    def record_publication_baseline(
        self,
        *,
        cycle: str,
        user: dict[str, Any],
        identity_receipt_id: str,
        now: datetime,
    ) -> dict[str, Any]:
        """Persist a prospective official-X follower snapshot immediately before publication."""
        binding = self.publication_binding(cycle=cycle)
        if not bool(binding.get("bound")):
            return {"status": "NOT_MISSION_BOUND", "binding": binding}
        followers = follower_count_from_user(user)
        if followers is None:
            return {"status": "FOLLOWER_METRIC_UNAVAILABLE", "binding": binding}
        mission = self._mission()
        if mission is None:
            return {"status": "NO_ACTIVE_MISSION", "binding": binding}
        experiment = self._open_experiment(mission.mission_id)
        if experiment is None or _enum_value(experiment.status) != "PROPOSED":
            return {
                "status": "NO_PROPOSED_EXPERIMENT",
                "experiment_id": None if experiment is None else experiment.experiment_id,
                "binding": binding,
            }
        observe = getattr(self.runtime, "observe_mission_metric", None)
        if not callable(observe):
            return {"status": "RUNTIME_CAPABILITY_UNAVAILABLE", "binding": binding}
        evidence_ref = f"x-mission-prepublication:{cycle}"
        observation = observe(
            mission.mission_id,
            value=float(followers),
            source=f"official-x-identity:pre-publication:{cycle}",
            evidence_refs=[str(identity_receipt_id), evidence_ref],
        )
        self._persist_runtime()
        payload = {
            "schema_version": MISSION_PUBLICATION_BASELINE_SCHEMA,
            "status": "RECORDED",
            "cycle": str(cycle),
            "mission_id": mission.mission_id,
            "experiment_id": experiment.experiment_id,
            "followers": float(followers),
            "identity_receipt_id": str(identity_receipt_id),
            "observation_id": observation.observation_id,
            "observed_at": observation.observed_at.isoformat(),
            "publication_authority": False,
        }
        folder = self.state_root / "content" / "continuous" / "cycles" / str(cycle)
        _write_json(folder / "x_mission_publication_baseline.json", payload)
        return payload

    def _publication_baseline(self, *, cycle: str, experiment_id: str) -> dict[str, Any]:
        folder = self.state_root / "content" / "continuous" / "cycles" / str(cycle)
        payload = _read_json(folder / "x_mission_publication_baseline.json")
        if (
            payload.get("schema_version") != MISSION_PUBLICATION_BASELINE_SCHEMA
            or payload.get("status") != "RECORDED"
            or payload.get("cycle") != str(cycle)
            or payload.get("experiment_id") != str(experiment_id)
            or not str(payload.get("observation_id") or "").strip()
        ):
            return {}
        return payload

    def repair_invalid_experiment_start(
        self,
        *,
        cycle: str,
        publication_receipt_id: str,
        post_id: str,
        reason: str,
        now: datetime,
    ) -> dict[str, Any]:
        """Repair a start that was created from a publication later proven unbound."""
        mission = self._mission()
        if mission is None:
            return {"status": "NO_ACTIVE_MISSION"}
        experiment = self._running_experiment(mission.mission_id)
        if experiment is None:
            return {"status": "NO_RUNNING_EXPERIMENT"}
        payload = _read_json(self.path)
        active = payload.get("active_experiment")
        if not isinstance(active, dict):
            return {"status": "PROJECTION_MISSING"}
        if str(active.get("experiment_id") or "") != experiment.experiment_id:
            return {"status": "START_EVIDENCE_MISMATCH", "experiment_id": experiment.experiment_id}
        projected_receipt = str(active.get("publication_receipt_id") or "").strip()
        projected_post = str(active.get("x_post_id") or "").strip()
        expected_receipt = str(publication_receipt_id).strip()
        expected_post = str(post_id).strip()
        if projected_receipt or projected_post:
            evidence_matches = (
                projected_receipt == expected_receipt
                and projected_post == expected_post
            )
        else:
            refs = {str(ref) for ref in experiment.evidence_refs}
            evidence_matches = (
                expected_receipt in refs
                and f"x-post:{expected_post}" in refs
            )
        if not evidence_matches:
            return {"status": "START_EVIDENCE_MISMATCH", "experiment_id": experiment.experiment_id}
        from agent_runtime.goals import MissionExperimentStatus
        experiment.status = MissionExperimentStatus.PROPOSED
        experiment.started_at = None
        experiment.before_value = None
        invalid_refs = {str(publication_receipt_id), f"x-post:{str(post_id)}"}
        experiment.evidence_refs = [ref for ref in experiment.evidence_refs if ref not in invalid_refs]
        self._persist_runtime()
        active["status"] = "PROPOSED"
        active["started_at"] = None
        active.pop("publication_receipt_id", None)
        active.pop("x_post_id", None)
        active["start_invalidation"] = {
            "cycle": str(cycle),
            "publication_receipt_id": str(publication_receipt_id),
            "post_id": str(post_id),
            "reason": str(reason)[:500],
            "invalidated_at": now.astimezone(timezone.utc).isoformat(),
        }
        payload["as_of"] = now.astimezone(timezone.utc).isoformat()
        _write_json(self.path, payload)
        folder = self.state_root / "content" / "continuous" / "cycles" / str(cycle)
        receipt = _read_json(folder / "x_browser_publication.json")
        if receipt.get("status") == "POSTED":
            receipt["mission_experiment_invalidation"] = dict(active["start_invalidation"])
            _write_json(folder / "x_browser_publication.json", receipt)
        return {
            "status": "REPAIRED_TO_PROPOSED",
            "experiment_id": experiment.experiment_id,
            **active["start_invalidation"],
        }

    def start_experiment_from_publication(
        self,
        *,
        cycle: str,
        publication_receipt_id: str,
        post_id: str,
        now: datetime,
    ) -> dict[str, Any]:
        if not self.enabled:
            return {"status": "DISABLED"}
        mission = self._mission()
        if mission is None or _enum_value(mission.status) != "ACTIVE":
            return {"status": "NO_ACTIVE_MISSION"}
        if not str(publication_receipt_id).strip() or not str(post_id).strip():
            raise ValueError("mission experiment publication evidence is required")
        binding = self.publication_binding(cycle=cycle)
        if not bool(binding.get("bound")):
            return {"status": "NOT_MISSION_BOUND", "binding": binding}
        experiment = self._open_experiment(mission.mission_id)
        if experiment is None:
            return {"status": "NO_PROPOSED_EXPERIMENT"}
        baseline = self._publication_baseline(
            cycle=str(cycle),
            experiment_id=experiment.experiment_id,
        )
        if not baseline:
            return {
                "status": "PREPUBLICATION_BASELINE_REQUIRED",
                "experiment_id": experiment.experiment_id,
                "binding": binding,
            }
        roadmap_step_transition = self._record_publication_for_roadmap(
            mission=mission,
            publication_receipt_id=str(publication_receipt_id).strip(),
            post_id=str(post_id).strip(),
            now=now.astimezone(timezone.utc),
        )
        if experiment is None:
            return {
                "status": "NO_PROPOSED_EXPERIMENT",
                "roadmap_step_transition": roadmap_step_transition,
            }
        status = _enum_value(experiment.status)
        if status == "RUNNING":
            return {
                "status": "ALREADY_RUNNING",
                "experiment_id": experiment.experiment_id,
                "roadmap_step_transition": roadmap_step_transition,
            }
        if status != "PROPOSED":
            return {
                "status": "NOT_STARTABLE",
                "experiment_id": experiment.experiment_id,
                "experiment_status": status,
                "roadmap_step_transition": roadmap_step_transition,
            }
        experiment.evidence_refs = list(
            dict.fromkeys(
                [
                    *experiment.evidence_refs,
                    str(baseline.get("identity_receipt_id") or ""),
                    str(baseline.get("observation_id") or ""),
                    str(publication_receipt_id).strip(),
                    f"x-post:{str(post_id).strip()}",
                ]
            )
        )
        started = self.runtime.start_mission_experiment(experiment.experiment_id)
        self._persist_runtime()
        payload = _read_json(self.path)
        active = payload.get("active_experiment")
        if (
            payload.get("mission_id") == mission.mission_id
            and isinstance(active, dict)
            and active.get("experiment_id") == started.experiment_id
        ):
            active["status"] = "RUNNING"
            active["started_at"] = (
                None if started.started_at is None else started.started_at.isoformat()
            )
            active["publication_receipt_id"] = str(publication_receipt_id).strip()
            active["x_post_id"] = str(post_id).strip()
            active["baseline_observation_id"] = str(baseline.get("observation_id") or "")
            active["baseline_followers"] = baseline.get("followers")
            payload["as_of"] = now.astimezone(timezone.utc).isoformat()
            payload["roadmap_publication_transition"] = roadmap_step_transition
            _write_json(self.path, payload)
        return {
            "status": "STARTED",
            "experiment_id": started.experiment_id,
            "started_at": (
                None if started.started_at is None else started.started_at.isoformat()
            ),
            "publication_receipt_id": str(publication_receipt_id).strip(),
            "x_post_id": str(post_id).strip(),
            "baseline_observation_id": str(baseline.get("observation_id") or ""),
            "baseline_followers": baseline.get("followers"),
            "roadmap_step_transition": roadmap_step_transition,
        }

    async def observe_identity(
        self,
        *,
        user: dict[str, Any],
        identity_receipt_id: str,
        now: datetime,
    ) -> dict[str, Any]:
        if not self.enabled:
            return {"status": "DISABLED"}
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("X mission observation time must be timezone-aware")
        now = now.astimezone(timezone.utc)
        followers = follower_count_from_user(user)
        if followers is None:
            payload = {
                "schema_version": MISSION_SCHEMA,
                "status": "FOLLOWER_METRIC_UNAVAILABLE",
                "as_of": now.isoformat(),
                "metric": MISSION_METRIC,
                "target": self.target,
                "generation_authority": "MISSION_STRATEGY_ONLY",
                "publication_authority": False,
            }
            _write_json(self.path, payload)
            return payload

        evidence_refs = [
            str(identity_receipt_id),
            f"x-followers:{followers}:{int(now.timestamp())}",
        ]
        mission = self._ensure_mission(
            followers=followers,
            now=now,
            evidence_refs=evidence_refs,
        )
        observation = None
        if self._should_record(mission, followers, now):
            observation = self.runtime.observe_mission_metric(
                mission.mission_id,
                value=float(followers),
                source="official-x-identity",
                evidence_refs=evidence_refs,
            )
            self._persist_runtime()
            evidence_refs.append(observation.observation_id)

        settlement = None
        running = self._running_experiment(mission.mission_id)
        if running is not None:
            settlement = self._experiment_outcome(
                mission=mission,
                experiment=running,
                followers=followers,
                now=now,
                evidence_refs=evidence_refs,
            )

        mission = self.runtime.missions.get(mission.mission_id)
        hard_stop = self._hard_stop_state(
            mission=mission,
            followers=followers,
            now=now,
        )
        roadmap_result = None
        experiment_result = None
        roadmap_execution = None
        if _enum_value(mission.status) == "ACTIVE":
            roadmap_result = await self._ensure_roadmap(mission)
            experiment_result = await self._ensure_experiment(mission)
            roadmap_execution = self._advance_ready_baseline_step(
                mission=mission,
                followers=followers,
                identity_receipt_id=identity_receipt_id,
                now=now,
                evidence_refs=evidence_refs,
                hard_stop=hard_stop,
            )

        progress = self.runtime.mission_progress(mission.mission_id)
        active_experiment = self._open_experiment(mission.mission_id)
        roadmap = self.runtime.missions.latest_roadmap(mission.mission_id)
        schedule_method = getattr(self.runtime, "mission_portfolio_schedule", None)
        schedule_rows: list[dict[str, Any]] = []
        if callable(schedule_method):
            schedule = schedule_method(max_parallelism=3)
            schedule_rows = [
                {
                    "step_id": item.step_id,
                    "title": item.title,
                    "status": item.status.value,
                    "planned_start": (
                        None if item.planned_start is None else item.planned_start.isoformat()
                    ),
                    "planned_finish": (
                        None if item.planned_finish is None else item.planned_finish.isoformat()
                    ),
                    "lane": item.lane,
                    "blocked_by": list(item.blocked_by),
                    "resource_tags": list(item.resource_tags),
                }
                for item in schedule.items
                if item.mission_id == mission.mission_id
            ]
        payload = {
            "schema_version": MISSION_SCHEMA,
            "status": mission.status.value,
            "as_of": now.isoformat(),
            "mission_id": mission.mission_id,
            "goal_id": mission.goal_id,
            "description": mission.description,
            "metric": mission.metric,
            "baseline": mission.baseline,
            "target": mission.target,
            "current": progress.current_value,
            "deadline": mission.deadline.isoformat(),
            "progress": progress.progress,
            "expected_progress": progress.expected_progress,
            "trajectory": progress.trajectory,
            "pressure": progress.pressure,
            "exploration_rate": progress.exploration_rate,
            "resource_willingness": progress.resource_willingness,
            "projected_value": progress.projected_value,
            "remaining_seconds": progress.remaining_seconds,
            "constraints": list(mission.constraints),
            "metric_observation_id": (
                None if observation is None else observation.observation_id
            ),
            "settlement": settlement,
            "roadmap_transition": roadmap_result,
            "roadmap_execution": roadmap_execution,
            "hard_stop": hard_stop,
            "roadmap": (
                None
                if roadmap is None
                else {
                    "roadmap_id": roadmap.roadmap_id,
                    "version": roadmap.version,
                    "rationale": roadmap.rationale,
                    "steps": schedule_rows,
                }
            ),
            "experiment_transition": experiment_result,
            "active_experiment": (
                None
                if active_experiment is None
                else {
                    "experiment_id": active_experiment.experiment_id,
                    "strategy": active_experiment.strategy,
                    "expected_effect": active_experiment.expected_effect,
                    "risk_notes": list(active_experiment.risk_notes),
                    "status": active_experiment.status.value,
                    "started_at": (
                        None
                        if active_experiment.started_at is None
                        else active_experiment.started_at.isoformat()
                    ),
                    "measurement_window_hours": (
                        active_experiment.measurement_window_hours
                        if active_experiment.measurement_window_hours is not None
                        else self.experiment_window.total_seconds() / 3600.0
                    ),
                    "settlement_metric": (
                        active_experiment.settlement_metric or MISSION_METRIC
                    ),
                }
            ),
            "generation_authority": "MISSION_STRATEGY_ONLY",
            "publication_authority": False,
            "causal_claim": False,
            "policy_mode": "OFFICIAL_X_API_ONLY",
        }
        _write_json(self.path, payload)
        return payload

    def _persist_runtime(self) -> None:
        persist = getattr(self.runtime, "persist", None)
        if callable(persist):
            persist()


__all__ = [
    "DEFAULT_DURATION_DAYS",
    "DEFAULT_EXPERIMENT_HOURS",
    "DEFAULT_TARGET",
    "MISSION_CONSTRAINTS",
    "MISSION_METRIC",
    "XGrowthMissionCoordinator",
    "follower_count_from_user",
    "x_growth_mission_context",
]
