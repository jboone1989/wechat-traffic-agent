"""Bridge a measurable X audience objective into Agent Runtime External Missions.

This module does not publish, follow, like, repost, or browse X. It only consumes
an official X identity observation, updates the mission metric, settles bounded
experiments from elapsed follower outcomes, and asks Agent Runtime for the next
advisory experiment.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
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
        due_at = started_at + self.experiment_window
        if now < due_at:
            return {
                "status": "RUNNING",
                "experiment_id": experiment.experiment_id,
                "due_at": due_at.isoformat(),
            }

        remaining_at_start = max(
            self.experiment_window.total_seconds(),
            (mission.deadline - started_at).total_seconds(),
        )
        required_remaining = max(0.0, float(mission.target) - float(before))
        expected_delta = required_remaining * (
            self.experiment_window.total_seconds() / remaining_at_start
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

    async def _ensure_roadmap(self, mission: Any) -> dict[str, Any]:
        needs_revision = getattr(self.runtime.missions, "roadmap_needs_revision", None)
        latest = getattr(self.runtime.missions, "latest_roadmap", None)
        if not callable(needs_revision) or not callable(latest):
            return {"status": "RUNTIME_CAPABILITY_UNAVAILABLE"}
        if not needs_revision(mission.mission_id):
            roadmap = latest(mission.mission_id)
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
        roadmap = await deliberate(mission.mission_id, max_tokens=1800)
        self._persist_runtime()
        return {
            "status": "PLANNED",
            "roadmap_id": roadmap.roadmap_id,
            "version": roadmap.version,
            "rationale": roadmap.rationale,
            "step_count": len(roadmap.steps),
        }

    async def _ensure_experiment(self, mission: Any) -> dict[str, Any]:
        existing = self._open_experiment(mission.mission_id)
        if existing is not None:
            return {
                "status": _enum_value(existing.status),
                "experiment_id": existing.experiment_id,
                "strategy": existing.strategy,
            }

        if not bool(getattr(self.runtime, "has_advisory_backend", False)):
            return {"status": "BACKEND_UNAVAILABLE"}
        deliberate = getattr(self.runtime, "deliberate_external_mission", None)
        if not callable(deliberate):
            return {"status": "RUNTIME_CAPABILITY_UNAVAILABLE"}
        experiment = await deliberate(mission.mission_id, max_tokens=900)
        self._persist_runtime()
        return {
            "status": "PROPOSED",
            "experiment_id": experiment.experiment_id,
            "strategy": experiment.strategy,
            "expected_effect": experiment.expected_effect,
            "risk_notes": list(experiment.risk_notes),
        }

    def start_experiment_from_publication(
        self,
        *,
        publication_receipt_id: str,
        post_id: str,
        now: datetime,
    ) -> dict[str, Any]:
        if not self.enabled:
            return {"status": "DISABLED"}
        mission = self._mission()
        if mission is None or _enum_value(mission.status) != "ACTIVE":
            return {"status": "NO_ACTIVE_MISSION"}
        experiment = self._open_experiment(mission.mission_id)
        if experiment is None:
            return {"status": "NO_PROPOSED_EXPERIMENT"}
        status = _enum_value(experiment.status)
        if status == "RUNNING":
            return {
                "status": "ALREADY_RUNNING",
                "experiment_id": experiment.experiment_id,
            }
        if status != "PROPOSED":
            return {
                "status": "NOT_STARTABLE",
                "experiment_id": experiment.experiment_id,
                "experiment_status": status,
            }
        if not str(publication_receipt_id).strip() or not str(post_id).strip():
            raise ValueError("mission experiment publication evidence is required")
        experiment.evidence_refs = list(
            dict.fromkeys(
                [
                    *experiment.evidence_refs,
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
            payload["as_of"] = now.astimezone(timezone.utc).isoformat()
            _write_json(self.path, payload)
        return {
            "status": "STARTED",
            "experiment_id": started.experiment_id,
            "started_at": (
                None if started.started_at is None else started.started_at.isoformat()
            ),
            "publication_receipt_id": str(publication_receipt_id).strip(),
            "x_post_id": str(post_id).strip(),
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
        roadmap_result = None
        experiment_result = None
        if _enum_value(mission.status) == "ACTIVE":
            roadmap_result = await self._ensure_roadmap(mission)
            experiment_result = await self._ensure_experiment(mission)

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
                        self.experiment_window.total_seconds() / 3600.0
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
