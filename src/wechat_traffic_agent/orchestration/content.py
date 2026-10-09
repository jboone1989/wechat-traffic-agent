"""Wakeful content task logic; the host injects runner components."""
from __future__ import annotations

import asyncio
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any

class ContentTaskMethods:
    """Content capability handlers for the shared wakeful host."""

    def _runtime_revision(self) -> str:
        """Return the deployed Ferro revision when release metadata is available."""
        release = Path(__file__).resolve().parents[2] / ".ferro-release.json"
        try:
            payload = json.loads(release.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return "unknown"
        if not isinstance(payload, dict):
            return "unknown"
        revision = str(
            payload.get("gitCommit")
            or payload.get("releaseId")
            or ""
        ).strip()
        return revision[:128] or "unknown"


    @staticmethod
    def _recovery_attempts(cycles_root: Path, prefix: str):
        completed: list[tuple[Path, str, str, str | None]] = []
        if cycles_root.exists():
            for folder in sorted(cycles_root.glob(prefix + "*")):
                result_file = folder / "result.json"
                if not result_file.exists():
                    continue
                try:
                    payload = json.loads(result_file.read_text(encoding="utf-8"))
                except (OSError, ValueError, json.JSONDecodeError):
                    continue
                runtime_revision = None
                recovery_file = folder / "recovery.json"
                if recovery_file.exists():
                    try:
                        recovery_payload = json.loads(
                            recovery_file.read_text(encoding="utf-8")
                        )
                    except (OSError, ValueError, json.JSONDecodeError):
                        recovery_payload = None
                    if isinstance(recovery_payload, dict):
                        value = str(
                            recovery_payload.get("runtime_revision") or ""
                        ).strip()
                        runtime_revision = value or None
                completed.append((
                    folder,
                    str(payload.get("status") or "").upper(),
                    str(payload.get("error_type") or ""),
                    runtime_revision,
                ))

        return completed

    @staticmethod
    def _mission_attempts(cycles_root: Path, source_cycle: str, goal_id: str):
        completed: list[tuple[dict[str, Any], str]] = []
        if cycles_root.exists():
            for folder in cycles_root.glob("v2-mission-followup-*"):
                metadata_path = folder / "mission_followup.json"
                result_file = folder / "result.json"
                if not metadata_path.exists() or not result_file.exists():
                    continue
                try:
                    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                    payload = json.loads(result_file.read_text(encoding="utf-8"))
                except (OSError, ValueError, json.JSONDecodeError):
                    continue
                if str(metadata.get("followup_of") or "") != source_cycle:
                    continue
                if str(metadata.get("goal_id") or "") != goal_id:
                    continue
                completed.append(
                    (metadata, str(payload.get("status") or "").upper())
                )
        completed.sort(key=lambda item: int(item[0].get("attempt") or 0))

        return completed


    def _content_recovery(
        self,
        *,
        preflight: dict[str, Any],
        now: datetime,
    ) -> dict[str, Any] | None:
        """Derive a fresh immutable cycle after a failed scheduled cycle.

        The failed result remains untouched. At most three recovery cycles are
        allowed for one base cycle, and a successful recovery suppresses
        duplicate work for the rest of that base window.
        """
        if str(preflight.get("status") or "") != "ALREADY_PROCESSED":
            return None
        result_path_raw = str(preflight.get("result_path") or "").strip()
        if not result_path_raw:
            return None
        result_path = Path(result_path_raw)
        try:
            prior = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        prior_status = str(prior.get("status") or "").upper()
        retryable_statuses = {"ERROR", "DEGRADED", "RECOVERY_INVALIDATED"}
        if prior_status not in retryable_statuses:
            return None

        base_cycle = str(preflight.get("cycle") or "").strip()
        base_epoch = base_cycle.rsplit("-", 1)[-1]
        state_root = getattr(self, "state_root", None)
        if not base_cycle or not base_epoch.isdigit() or state_root is None:
            return None

        cycles_root = Path(state_root) / "content" / "continuous" / "cycles"
        prefix = f"v2-recovery-{base_epoch}-"
        completed = self._recovery_attempts(cycles_root, prefix)

        for folder, status, _error_type, _revision in reversed(completed):
            if status not in retryable_statuses:
                return {
                    "status": "ALREADY_RECOVERED",
                    "recovery_of": base_cycle,
                    "recovery_cycle": folder.name,
                    "previous_status": prior_status,
                    "attempt_count": len(completed),
                }

        # Provider outages have their own durable cooldown/backoff evidence and
        # recovery scheduler. Counting those transient availability failures
        # against the content-recovery cap can permanently strand an active
        # owner goal after three provider windows, even when a route later
        # recovers. Keep the three-attempt guard for content/application
        # failures, but let provider recovery continue at its bounded cooldown
        # cadence.
        current_revision = self._runtime_revision()
        bounded_failures = [
            row
            for row in completed
            if row[2] != "LLMProviderError"
            and (
                current_revision == "unknown"
                or row[3] == current_revision
            )
        ]
        if len(bounded_failures) >= 3:
            return {
                "status": "RECOVERY_LIMIT_REACHED",
                "recovery_of": base_cycle,
                "previous_status": prior_status,
                "attempt_count": len(completed),
                "bounded_failure_attempt_count": len(bounded_failures),
            }

        attempt = len(completed) + 1
        suffix = int(now.timestamp())
        recovery_cycle = f"v2-recovery-{base_epoch}-{attempt}-{suffix}"
        while (cycles_root / recovery_cycle).exists():
            suffix += 1
            recovery_cycle = f"v2-recovery-{base_epoch}-{attempt}-{suffix}"
        metadata = {
            "schema_version": "content-cycle-recovery.v1",
            "status": "READY",
            "recovery_of": base_cycle,
            "recovery_cycle": recovery_cycle,
            "previous_status": prior_status,
            "attempt": attempt,
            "triggered_at": now.isoformat(),
            "preserves_failed_cycle": True,
            "runtime_revision": current_revision,
        }
        folder = cycles_root / recovery_cycle
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / "recovery.json.tmp"
        tmp.write_text(
            json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n",
            encoding="utf-8",
        )
        tmp.replace(folder / "recovery.json")
        return metadata

    def _mission_followup_identity(
        self,
        *,
        prior: dict[str, Any],
        result_path: Path,
    ) -> dict[str, str] | None:
        """Resolve which external mission owns a no-op or interrupted discovery cycle."""
        error_path = result_path.parent / "mission_discovery_error.json"
        error: dict[str, Any] | None = None
        if error_path.exists():
            try:
                payload = json.loads(error_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                payload = None
            if isinstance(payload, dict):
                error = payload

        # An interrupted discovery artifact is authoritative for this cycle even
        # when the ordinary fallback path later published something. That
        # publication did not execute the mission-bound discovery, so it must
        # not erase the mission retry identity.
        if error is None:
            mind_plan = prior.get("mind_plan")
            if isinstance(mind_plan, dict):
                goal = mind_plan.get("goal")
                if isinstance(goal, dict) and str(goal.get("origin") or "") == "EXTERNAL_MISSION":
                    goal_id = str(goal.get("goal_id") or "").strip()
                    mission_id = str(mind_plan.get("external_mission_id") or "").strip()
                    if goal_id:
                        return {
                            "goal_id": goal_id,
                            "mission_id": mission_id,
                            "reason": "EXTERNAL_MISSION_NO_MATERIAL_CHANGE",
                        }
            return None
        mission_id = str(error.get("mission_id") or "").strip()
        step_id = str(error.get("roadmap_step_id") or "").strip()
        experiment_id = str(error.get("experiment_id") or "").strip()

        # Compatibility for discovery errors written before mission identity was
        # persisted in the error artifact.
        if not mission_id:
            state_root = getattr(self, "state_root", None)
            if state_root is None:
                return None
            try:
                from wechat_traffic_agent.x_growth_mission import x_growth_mission_context

                mission = x_growth_mission_context(Path(state_root))
            except Exception:  # noqa: BLE001
                return None
            if str(mission.get("status") or "") != "ACTIVE":
                return None
            mission_id = str(mission.get("mission_id") or "").strip()
            roadmap = mission.get("roadmap") if isinstance(mission.get("roadmap"), dict) else {}
            candidates = [
                row for row in list(roadmap.get("steps") or ())
                if isinstance(row, dict)
                and row.get("status") in {"RUNNING", "READY"}
                and "content_creation" in set(row.get("resource_tags") or ())
            ]
            candidates.sort(
                key=lambda row: (
                    0 if row.get("status") == "RUNNING" else 1,
                    str(row.get("step_id") or ""),
                )
            )
            if candidates:
                step_id = str(candidates[0].get("step_id") or "").strip()
            experiment = mission.get("active_experiment")
            if isinstance(experiment, dict):
                experiment_id = str(experiment.get("experiment_id") or "").strip()

        if not mission_id:
            return None
        anchor = step_id or experiment_id or "research"
        return {
            "goal_id": f"external-mission-retry:{mission_id}:{anchor}",
            "mission_id": mission_id,
            "reason": "EXTERNAL_MISSION_DISCOVERY_INTERRUPTED",
        }

    def _content_mission_followup(
        self,
        *,
        preflight: dict[str, Any],
        now: datetime,
    ) -> dict[str, Any] | None:
        """Derive a bounded fresh cycle when an external mission learned nothing useful."""
        if str(preflight.get("status") or "") != "ALREADY_PROCESSED":
            return None
        result_path_raw = str(preflight.get("result_path") or "").strip()
        if not result_path_raw:
            return None
        result_path = Path(result_path_raw)
        try:
            prior = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        identity = self._mission_followup_identity(
            prior=prior,
            result_path=result_path,
        )
        if identity is None:
            return None
        prior_status = str(prior.get("status") or "").upper()
        interrupted_discovery = (
            str(identity.get("reason") or "")
            == "EXTERNAL_MISSION_DISCOVERY_INTERRUPTED"
        )
        if prior_status != "COGNITION_ONLY" and not interrupted_discovery:
            return None

        source_cycle = str(preflight.get("cycle") or "").strip()
        goal_id = str(identity.get("goal_id") or "").strip()
        mission_id = str(identity.get("mission_id") or "").strip()
        state_root = getattr(self, "state_root", None)
        if not source_cycle or not goal_id or state_root is None:
            return None

        cycles_root = Path(state_root) / "content" / "continuous" / "cycles"
        completed = self._mission_attempts(cycles_root, source_cycle, goal_id)

        for metadata, status in reversed(completed):
            if status != "COGNITION_ONLY":
                return {
                    "status": "ALREADY_FOLLOWED_UP",
                    "followup_of": source_cycle,
                    "followup_cycle": metadata.get("followup_cycle"),
                    "goal_id": goal_id,
                    "mission_id": mission_id,
                    "attempt_count": len(completed),
                    "result_status": status,
                }

        if len(completed) >= 3:
            return {
                "status": "FOLLOWUP_LIMIT_REACHED",
                "followup_of": source_cycle,
                "goal_id": goal_id,
                "mission_id": mission_id,
                "attempt_count": len(completed),
            }

        attempt = len(completed) + 1
        suffix = int(now.timestamp())
        followup_cycle = f"v2-mission-followup-{attempt}-{suffix}"
        while (cycles_root / followup_cycle).exists():
            suffix += 1
            followup_cycle = f"v2-mission-followup-{attempt}-{suffix}"
        metadata = {
            "schema_version": "content-mission-followup.v1",
            "status": "READY",
            "followup_of": source_cycle,
            "followup_cycle": followup_cycle,
            "goal_id": goal_id,
            "mission_id": mission_id,
            "attempt": attempt,
            "triggered_at": now.isoformat(),
            "reason": str(identity.get("reason") or "EXTERNAL_MISSION_NO_MATERIAL_CHANGE"),
            "publication_authority": False,
        }
        folder = cycles_root / followup_cycle
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / "mission_followup.json.tmp"
        tmp.write_text(
            json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n",
            encoding="utf-8",
        )
        tmp.replace(folder / "mission_followup.json")
        return metadata

    def _content(
        self, now: datetime, *, focus_topic: str | None,
        relevant_refs: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        result, rows = self._runtime_tick(now)
        rows.update({"task": "CONTENT_COGNITION",
                     "status": "OK" if result is not None else "DEGRADED"})
        self._maintain_content_site(rows, now)
        if result is None or not result.generation_ready:
            return rows
        if self.continuous_loop is not None:
            self._recover_social_stage_backlog(rows=rows, now=now)
        owner = self._start_owner_content(rows, now, focus_topic, relevant_refs)
        if owner is None:
            return rows
        (focus_topic, owner_goal, owner_cycle_id, owner_active,
         owner_bound, owner_seed, control) = owner
        self._observe_site_editorial(rows, now)
        if self.continuous_loop is None:
            return rows
        self._observe_search_console(rows, now)
        cycle = self._prepare_content_cycle(rows, now, focus_topic, owner_cycle_id, owner_active)
        if cycle is None:
            return rows
        preflight, directed_id, cycle_id, recovery, mission_followup = cycle
        decision = self._admit_content_resources(rows, now, preflight, cycle_id)
        if decision is None:
            return rows
        options = self._content_execution_options(
            rows, now, focus_topic, owner_goal, owner_seed, owner_active,
            control, decision, cycle_id, directed_id,
        )
        continuous = self._run_async(self.continuous_loop.run_once(**options))
        rows["continuous_content"] = continuous
        self._finish_content_recovery(rows, recovery, continuous, now)
        self._finish_content_mission(rows, mission_followup, continuous, now)
        self._emit_content_prediction(rows, continuous, now)
        self._settle_content_resources(rows, decision, continuous, cycle_id, now)
        if continuous.get("status") in {"ERROR", "RESOURCE_DEFERRED", "ALREADY_PROCESSED"}:
            self._handle_content_nonpublication(rows, continuous, preflight, cycle_id, now)
            self._complete_content_owner(rows, owner_goal, owner_active, owner_bound, continuous, now)
            return rows
        self._run_social_stage(rows=rows, continuous=continuous, now=now)
        self._schedule_content_followup(rows, continuous, now)
        self._complete_content_owner(rows, owner_goal, owner_active, owner_bound, continuous, now)
        rows["cognition_state_event"] = self.state_events.emit("MARK_COGNITION", observed_at=now)
        return rows

    def _maintain_content_site(self, rows, now):
        publisher = getattr(getattr(self, "continuous_loop", None), "publisher", None)
        maintainer = getattr(publisher, "maintain", None)
        if callable(maintainer):
            try:
                rows["website_maintenance"] = maintainer(now=now)
            except Exception as exc:
                rows["website_maintenance"] = self._error(exc)
                rows["website_maintenance"]["status"] = "NEEDS_REPAIR"

    def _start_owner_content(self, rows, now, focus_topic, relevant_refs):
        control = {}
        owner_goal = self._active_owner_goal_context(
            relevant_refs=relevant_refs,
            now=now,
        )
        owner_directed_cycle_id: str | None = None
        owner_episode_active = False
        owner_seed_evidence: list[dict[str, Any]] = []
        owner_episode_running = bool(
            owner_goal is not None
            and getattr(self, "owner_agent_episode", None) is not None
            and self.owner_agent_episode.running_for(
                str(owner_goal.get("goal_id") or "")
            )
        )
        owner_episode_state = (
            self.owner_agent_episode.projection_for(
                str(owner_goal.get("goal_id") or "")
            )
            if (
                owner_goal is not None
                and getattr(self, "owner_agent_episode", None) is not None
            )
            else {}
        )
        owner_episode_bound = bool(
            isinstance(owner_episode_state, dict)
            and str(owner_episode_state.get("status") or "")
            in {"RUNNING", "DECIDED"}
        )
        if (
            owner_goal is not None
            and focus_topic is None
        ):
            control = self._owner_agent_control(
                owner_goal=owner_goal,
                now=now,
            )
            rows["owner_agent_episode"] = control
            if (
                str(control.get("status") or "")
                == "OWNER_COGNITION_PROVIDER_UNAVAILABLE"
            ):
                rows["status"] = "OWNER_COGNITION_PROVIDER_UNAVAILABLE"
                rows["owner_cognition_blocker"] = control
                return None
            if str(control.get("status") or "") == "UNRESOLVED_ACTION_REQUIRED":
                rows["status"] = "OWNER_ACTION_REQUIRED_BUT_UNRESOLVED"
                return None
            if str(control.get("status") or "") == "DECIDED":
                decision = (
                    control.get("decision")
                    if isinstance(control.get("decision"), dict)
                    else {}
                )
                action = str(decision.get("action") or "")
                owner_episode_active = True
                owner_directed_cycle_id = str(
                    control.get("cycle_id") or ""
                ).strip() or None
                if action == "WAIT":
                    wait_seconds = int(decision.get("wait_seconds") or 300)
                    delay = max(60, min(wait_seconds, 21600))
                    rows["owner_wait"] = self.task_scheduler.schedule(
                        "CONTENT_COGNITION",
                        now + timedelta(seconds=delay),
                        reason="resume owner goal after explicit wait",
                        relevant_refs=(str(owner_goal.get("goal_id") or ""),),
                    )
                    wait_outcome = {
                        "status": "WAIT_SCHEDULED",
                        "wait_seconds": wait_seconds,
                        "wait_for": decision.get("wait_for") or decision.get("rationale"),
                        "next_action": decision.get("next_action"),
                        "goal_progress_claimed": False,
                    }
                    complete = getattr(self.owner_agent_episode, "complete", None)
                    if callable(complete):
                        rows["owner_agent_episode_completion"] = complete(
                            outcome=wait_outcome,
                            now=now,
                            goal_id=str(owner_goal.get("goal_id") or ""),
                        )
                    rows["status"] = "OWNER_AGENT_WAITING"
                    return None
                if action == "RUN_CONTENT_PIPELINE":
                    focus_topic = str(decision.get("topic") or "").strip() or None
                    owner_seed_evidence = self._owner_agent_seed_evidence(control)
        return (focus_topic, owner_goal, owner_directed_cycle_id, owner_episode_active,
                owner_episode_bound, owner_seed_evidence, control)

    def _observe_site_editorial(self, rows, now):
        if self.web_worker is not None:
            try:
                rows["web_editorial"] = self._run_async(self.web_worker.run_once(now=now))
            except Exception as exc:  # noqa: BLE001
                rows["web_editorial"] = self._error(exc)
                rows["status"] = "DEGRADED"

    def _observe_search_console(self, rows, now):
        if self.gsc_collector is not None:
            try:
                rows["google_search_console"] = self.gsc_collector.collect(now=now)
            except Exception as exc:  # noqa: BLE001
                rows["google_search_console"] = self._error(exc)
                rows["status"] = "DEGRADED"

    def _prepare_content_cycle(self, rows, now, focus_topic, owner_directed_cycle_id, owner_episode_active):
        focus_cycle_id = (
            owner_directed_cycle_id
            if owner_episode_active and focus_topic
            else f"v2-bootstrap-{int(now.timestamp())}" if focus_topic else None
        )
        directed_cycle_id = focus_cycle_id
        preflight = self.continuous_loop.preflight(now=now, cycle_id=directed_cycle_id)
        base_cycle_id = str(preflight["cycle"])
        recovery = None
        mission_followup = None
        if focus_topic is None:
            recovery = self._content_recovery(preflight=preflight, now=now)
            if recovery is not None:
                rows["content_cycle_recovery"] = recovery
                if recovery["status"] == "RECOVERY_LIMIT_REACHED":
                    rows["resource_decision"] = {
                        "status": "SKIPPED_NOOP",
                        "decision": "NO_RESOURCE_ACTION",
                        "cycle": base_cycle_id,
                    }
                    rows["continuous_content"] = {
                        "status": recovery["status"],
                        "cycle": base_cycle_id,
                        "recovery_cycle": recovery.get("recovery_cycle"),
                        "previous_status": recovery.get("previous_status"),
                        "attempt_count": recovery.get("attempt_count"),
                    }
                    return None
                if recovery["status"] == "ALREADY_RECOVERED":
                    recovered_cycle = str(recovery.get("recovery_cycle") or "")
                    recovered_preflight = self.continuous_loop.preflight(
                        now=now,
                        cycle_id=recovered_cycle,
                    )
                    mission_followup = self._content_mission_followup(
                        preflight=recovered_preflight,
                        now=now,
                    )
                    if mission_followup is None:
                        rows["resource_decision"] = {
                            "status": "SKIPPED_NOOP",
                            "decision": "NO_RESOURCE_ACTION",
                            "cycle": base_cycle_id,
                        }
                        rows["continuous_content"] = {
                            "status": recovery["status"],
                            "cycle": base_cycle_id,
                            "recovery_cycle": recovery.get("recovery_cycle"),
                            "previous_status": recovery.get("previous_status"),
                            "attempt_count": recovery.get("attempt_count"),
                        }
                        return None
                elif recovery["status"] == "READY":
                    directed_cycle_id = str(recovery["recovery_cycle"])
                    preflight = self.continuous_loop.preflight(
                        now=now, cycle_id=directed_cycle_id
                    )
            else:
                mission_followup = self._content_mission_followup(
                    preflight=preflight,
                    now=now,
                )

            if mission_followup is not None:
                rows["content_mission_followup"] = mission_followup
                if mission_followup["status"] in {
                    "ALREADY_FOLLOWED_UP",
                    "FOLLOWUP_LIMIT_REACHED",
                }:
                    rows["resource_decision"] = {
                        "status": "SKIPPED_NOOP",
                        "decision": "NO_RESOURCE_ACTION",
                        "cycle": base_cycle_id,
                    }
                    rows["continuous_content"] = {
                        "status": mission_followup["status"],
                        "cycle": base_cycle_id,
                        "followup_cycle": mission_followup.get("followup_cycle"),
                        "followup_of": mission_followup.get("followup_of"),
                        "attempt_count": mission_followup.get("attempt_count"),
                        "result_status": mission_followup.get("result_status"),
                    }
                    return None
                directed_cycle_id = str(mission_followup["followup_cycle"])
                preflight = self.continuous_loop.preflight(
                    now=now,
                    cycle_id=directed_cycle_id,
                )
        cycle_id = str(preflight["cycle"])
        return preflight, directed_cycle_id, cycle_id, recovery, mission_followup

    def _admit_content_resources(self, rows, now, preflight, cycle_id):
        if preflight["status"] == "ALREADY_PROCESSED":
            decision = {"status": "SKIPPED_NOOP", "decision": "NO_RESOURCE_ACTION", "cycle": cycle_id}
        else:
            try:
                decision = self.resource_economy.begin_cycle(now=now, cycle=cycle_id)
            except Exception as exc:  # noqa: BLE001
                decision = {"status": "UNAVAILABLE", "decision": "ASK_HUMAN", **self._error(exc)}
        rows["resource_decision"] = decision
        if decision.get("decision") not in {"EXECUTE", "NO_RESOURCE_ACTION"}:
            rows["continuous_content"] = {"status": "RESOURCE_DEFERRED", "cycle": cycle_id, "resource_decision": decision}
            rows["status"] = "DEFERRED"
            return None

        return decision

    def _content_execution_options(
        self, rows, now, focus_topic, owner_goal, owner_seed_evidence,
        owner_episode_active, control, decision, cycle_id, directed_cycle_id,
    ):
        if owner_goal is not None:
            rows["owner_goal_context"] = {
                "goal_id": owner_goal["goal_id"],
                "origin": owner_goal["origin"],
                "priority": owner_goal["priority"],
            }
        owner_content_cognition = None
        if owner_goal is not None:
            owner_content_cognition = getattr(
                self,
                "owner_cognition_runtime",
                None,
            )
            if owner_content_cognition is None:
                from wechat_traffic_agent.runtime_backend import load_owner_cognition_runtime

                owner_content_cognition = load_owner_cognition_runtime(
                    state_root=Path(self.state_root)
                    if getattr(self, "state_root", None) is not None
                    else None,
                    behavioral_context_provider=self._owner_behavioral_context,
                )
                self.owner_cognition_runtime = owner_content_cognition
        content_run_kwargs = dict(
            now=now,
            cycle_id=directed_cycle_id,
            focus_topic=focus_topic,
            owner_goal=owner_goal,
            seed_evidence=owner_seed_evidence,
        )
        if owner_goal is not None:
            content_run_kwargs["cognition_runtime"] = owner_content_cognition
            if owner_episode_active and decision.get("decision") in {"EXECUTE", "NO_RESOURCE_ACTION"}:
                owner_decision = (control.get("decision") or {})
                surface = str(owner_decision.get("publication_surface") or "AUTO")
                if (
                    surface in {"X_SHORT_POST", "X_LONG_POST", "X_ARTICLE"}
                    and getattr(self.continuous_loop, "publisher", None) is not None
                ):
                    # Older durable episodes can retain an X-only decision
                    # after the owner has made Website the primary channel.
                    # Preserve the cognition and topic, but never let that
                    # stored surface bypass first-party publication.
                    rows["website_first_routing"] = {
                        "status": "WEBSITE_FIRST", "previous_surface": surface,
                    }
                    surface = "WEBSITE"
                if surface == "WEBSITE":
                    content_run_kwargs["publication_surface"] = surface
                if surface in {"X_SHORT_POST", "X_LONG_POST", "X_ARTICLE"}:
                    from wechat_traffic_agent.content_loop import _put
                    from wechat_traffic_agent.x_growth_mission import x_growth_mission_context
                    from wechat_traffic_agent.x_owner_execution import SCHEMA

                    mission = x_growth_mission_context(Path(self.state_root))
                    folder = Path(self.state_root) / "content" / "continuous" / "cycles" / cycle_id
                    _put(folder / "owner_publication_request.json", {
                        "schema_version": SCHEMA, "cycle": cycle_id,
                        "episode_id": control.get("episode_id"),
                        "owner_goal_id": owner_goal["goal_id"],
                        "mission_id": mission.get("mission_id"),
                        "decision": owner_decision,
                        "publication_authority": False,
                    })
                    content_run_kwargs["publication_surface"] = surface

        return content_run_kwargs

    def _finish_content_recovery(self, rows, recovery, continuous, now):
        if recovery is not None and recovery.get("status") == "READY":
            recovery_path = (
                Path(self.state_root)
                / "content"
                / "continuous"
                / "cycles"
                / str(recovery["recovery_cycle"])
                / "recovery.json"
            )
            finished = {
                **recovery,
                "status": (
                    "FAILED"
                    if str(continuous.get("status") or "").upper()
                    in {"ERROR", "DEGRADED"}
                    else "COMPLETED"
                ),
                "result_status": str(continuous.get("status") or "UNKNOWN"),
                "completed_at": datetime.now(now.tzinfo).isoformat(),
            }
            tmp = recovery_path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(
                    finished,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )
            tmp.replace(recovery_path)
            rows["content_cycle_recovery"] = finished

    def _finish_content_mission(self, rows, mission_followup, continuous, now):
        if mission_followup is not None and mission_followup.get("status") == "READY":
            followup_path = (
                Path(self.state_root)
                / "content"
                / "continuous"
                / "cycles"
                / str(mission_followup["followup_cycle"])
                / "mission_followup.json"
            )
            result_status = str(continuous.get("status") or "UNKNOWN").upper()
            if result_status in {"ERROR", "DEGRADED"}:
                followup_status = "FAILED"
            elif result_status == "COGNITION_ONLY":
                followup_status = "NO_MATERIAL_CHANGE"
            else:
                followup_status = "COMPLETED"
            finished_followup = {
                **mission_followup,
                "status": followup_status,
                "result_status": str(continuous.get("status") or "UNKNOWN"),
                "completed_at": datetime.now(now.tzinfo).isoformat(),
            }
            tmp = followup_path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(
                    finished_followup,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )
            tmp.replace(followup_path)
            rows["content_mission_followup"] = finished_followup

    def _emit_content_prediction(self, rows, continuous, now):
        action_prediction = continuous.get("action_prediction") if isinstance(continuous, dict) else None
        prediction_id = str((action_prediction or {}).get("prediction_id") or "")
        if prediction_id:
            try:
                rows["neural_action_commitment"] = self.state_events.emit(
                    "ACTION_COMMITMENT",
                    {"prediction_id": prediction_id},
                    observed_at=now,
                )
            except Exception as exc:  # noqa: BLE001
                rows["neural_action_commitment"] = self._error(exc)
                rows["status"] = "DEGRADED"

    def _settle_content_resources(self, rows, decision, continuous, cycle_id, now):
        if decision.get("decision") == "EXECUTE":
            try:
                rows["resource_cycle_result"] = self.resource_economy.end_cycle(
                    now=datetime.now(now.tzinfo), cycle=cycle_id,
                    episode_id=str(decision["episode_id"]), before_balance=float(decision["before_balance"]),
                    result=continuous,
                )
            except Exception as exc:  # noqa: BLE001
                rows["resource_cycle_result"] = self._error(exc)
                rows["status"] = "DEGRADED"

    def _handle_content_nonpublication(self, rows, continuous, preflight, cycle_id, now):
        if continuous.get("status") == "ERROR":
            rows["status"] = "DEGRADED"
            self._record_content_failure(rows, continuous, now)
        elif continuous.get("status") == "ALREADY_PROCESSED":
            self._replay_prior_content_publication(rows, preflight, cycle_id, now)

    def _record_content_failure(self, rows, continuous, now):
        error_type = str(continuous.get("error_type") or "")
        if error_type == "LLMProviderError":
            try:
                rows["provider_recovery"] = self.task_scheduler.schedule(
                    "CONTENT_COGNITION",
                    now + timedelta(seconds=300),
                    reason="retry content cognition after provider outage",
                )
            except Exception as exc:  # noqa: BLE001
                rows["provider_recovery"] = self._error(exc)
        else:
            state_root = getattr(self, "state_root", None)
            cycle = str(continuous.get("cycle") or "").strip()
            if state_root is not None and cycle:
                result_path = (
                    Path(state_root)
                    / "content"
                    / "continuous"
                    / "cycles"
                    / cycle
                    / "result.json"
                )
                followup_identity = self._mission_followup_identity(
                    prior=continuous,
                    result_path=result_path,
                )
                if (
                    followup_identity is not None
                    and str(followup_identity.get("reason") or "")
                    == "EXTERNAL_MISSION_DISCOVERY_INTERRUPTED"
                ):
                    owner_goal_id = str(
                        continuous.get("owner_goal_id") or ""
                    ).strip()
                    rows["mission_followup_schedule"] = {
                        **self.task_scheduler.schedule(
                            "CONTENT_COGNITION",
                            now + timedelta(seconds=5),
                            reason=str(
                                followup_identity.get("reason") or ""
                            ),
                            relevant_refs=(
                                (owner_goal_id,)
                                if owner_goal_id
                                else ()
                            ),
                        ),
                        "seconds": 5,
                        "goal_id": str(
                            followup_identity.get("goal_id") or ""
                        ),
                        "mission_id": str(
                            followup_identity.get("mission_id") or ""
                        ),
                    }
        self_maintenance = getattr(self, "self_maintenance", None)
        if self_maintenance is not None:
            try:
                rows["self_maintenance"] = self_maintenance.ingest_failure(continuous, now=now)
                self.task_scheduler.schedule(
                    "SELF_REPAIR",
                    now + timedelta(seconds=60),
                    reason="repair content failure",
                )
            except Exception as exc:  # noqa: BLE001
                rows["self_maintenance"] = self._error(exc)

    def _replay_prior_content_publication(self, rows, preflight, cycle_id, now):
        result_path_raw = str(preflight.get("result_path") or "").strip()
        if result_path_raw:
            try:
                prior = json.loads(
                    Path(result_path_raw).read_text(encoding="utf-8")
                )
            except (OSError, ValueError, json.JSONDecodeError):
                prior = {}
            if str(prior.get("status") or "").upper() in {"PUBLISHED", "X_EXPRESSION_READY"}:
                self._run_social_stage(
                    rows=rows,
                    continuous=prior,
                    now=now,
                )
                rows["social_stage_replay"] = {
                    "status": "REPLAYED",
                    "cycle": str(prior.get("cycle") or cycle_id),
                    "reason": "PUBLISHED_CYCLE_DOWNSTREAM_RECOVERY",
                }
                rows["cognition_state_event"] = self.state_events.emit(
                    "MARK_COGNITION",
                    observed_at=now,
                )

    def _schedule_content_followup(self, rows, continuous, now):
        followup_identity = None
        state_root = getattr(self, "state_root", None)
        cycle = str(continuous.get("cycle") or "").strip()
        if state_root is not None and cycle:
            followup_identity = self._mission_followup_identity(
                prior=continuous,
                result_path=(
                    Path(state_root)
                    / "content"
                    / "continuous"
                    / "cycles"
                    / cycle
                    / "result.json"
                ),
            )
        continuous_status = str(continuous.get("status") or "").upper()
        followup_reason = (
            str(followup_identity.get("reason") or "")
            if followup_identity is not None
            else ""
        )
        should_schedule_followup = (
            followup_identity is not None
            and (
                continuous_status == "COGNITION_ONLY"
                or followup_reason == "EXTERNAL_MISSION_DISCOVERY_INTERRUPTED"
            )
        )
        if should_schedule_followup:
            owner_goal_id = str(
                continuous.get("owner_goal_id") or ""
            ).strip()
            reason = str(
                followup_identity.get("reason")
                or "EXTERNAL_MISSION_NO_MATERIAL_CHANGE"
            )
            rows["mission_followup_schedule"] = {
                **self.task_scheduler.schedule(
                    "CONTENT_COGNITION",
                    now + timedelta(seconds=5),
                    reason=reason,
                    relevant_refs=(
                        (owner_goal_id,)
                        if owner_goal_id
                        else ()
                    ),
                ),
                "seconds": 5,
                "goal_id": str(followup_identity.get("goal_id") or ""),
                "mission_id": str(followup_identity.get("mission_id") or ""),
                "reason": reason,
            }

    def _complete_content_owner(self, rows, owner_goal, owner_active, owner_bound, continuous, now):
        if not (owner_active or owner_bound):
            return
        complete = getattr(self.owner_agent_episode, "complete", None)
        if not callable(complete):
            return
        owner_outcome = continuous
        if (continuous.get("status") not in {"ERROR", "RESOURCE_DEFERRED", "ALREADY_PROCESSED"}
                and continuous.get("publication_surface") in {"X_SHORT_POST", "X_LONG_POST", "X_ARTICLE"}):
            owner_outcome = {
                **continuous,
                **rows.get("x_social_expression", {"status": "X_PUBLICATION_UNRESOLVED"}),
            }
        rows["owner_agent_episode_completion"] = complete(
            outcome=owner_outcome,
            now=now,
            goal_id=(str(owner_goal.get("goal_id") or "") if isinstance(owner_goal, dict) else None),
        )
