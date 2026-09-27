"""Prospective real-world selection for Ferro-born training worlds.

N29 treats self-generated worlds as hypotheses, not achievements. Assignment is
persisted before intervention, treatment/control are balanced prospectively, and
a world is adopted only after real target-task outcomes support it.
"""
from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any, Mapping

SCHEMA = "ferro-developmental-transfer.v1"
MAX_TRIALS = 256
MIN_ARM = 4
ADOPT_EFFECT = 0.10
REJECT_EFFECT = -0.10


def _read(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


class DevelopmentalWorldTransferLab:
    def __init__(
        self,
        state_root: Path,
        *,
        brain_lab: Any,
        agency: Any,
        architecture: Any | None = None,
    ) -> None:
        self.state_root = Path(state_root)
        self.brain_lab = brain_lab
        self.agency = agency
        self.architecture = architecture
        self.path = self.state_root / "application" / "developmental_transfer.json"
        raw = _read(self.path)
        self.state = raw if raw.get("schema_version") == SCHEMA else {
            "schema_version": SCHEMA,
            "updated_at": None,
            "seq": 0,
            "trials": [],
        }
        migrated = False
        for row in self.state.get("trials") or ():
            outcome = row.get("target_outcome") if isinstance(row.get("target_outcome"), Mapping) else {}
            if (
                row.get("status") == "SETTLED"
                and str(outcome.get("status") or "").upper() == "SHUTDOWN_CHECKPOINT"
            ):
                row["status"] = "INCONCLUSIVE"
                row["target_outcome"] = {
                    **dict(outcome),
                    "utility": None,
                    "inconclusive_reason": "SERVICE_SHUTDOWN_DURING_TARGET",
                }
                migrated = True
        if migrated:
            _write(self.path, self.state)

    @staticmethod
    def _assignment(world_id: str, seq: int) -> str:
        # Balanced blocks: every two trials contain one treatment and one control.
        block = (int(seq) - 1) // 2
        within = (int(seq) - 1) % 2
        first_treatment = bool(
            sha256(f"ferro-developmental-transfer:{world_id}:{block}".encode()).digest()[0] & 1
        )
        treatment = first_treatment if within == 0 else not first_treatment
        return "TREATMENT" if treatment else "CONTROL"

    @staticmethod
    def _utility(task: str, result: Mapping[str, Any]) -> float:
        status = str(result.get("status") or "UNKNOWN").upper()
        world_utility = result.get("world_utility")
        if isinstance(world_utility, (int, float)) and not isinstance(world_utility, bool):
            return round(max(-1.0, min(1.0, float(world_utility))), 6)
        if status in {"ERROR", "DEGRADED"}:
            return -0.85
        if status in {"DISABLED", "NOOP"}:
            return -0.25

        # Use task-specific observable evidence when it exists. These are quality
        # measures of the real task execution, not game reward.
        if str(task) == "CHECK_X":
            observation = (
                result.get("x_social_observation")
                if isinstance(result.get("x_social_observation"), Mapping)
                else {}
            )
            score = 0.0
            if str(observation.get("status") or "").startswith("OK"):
                score += 0.30
            if observation.get("identity_receipt_id"):
                score += 0.20
            mission = (
                observation.get("x_growth_mission")
                if isinstance(observation.get("x_growth_mission"), Mapping)
                else {}
            )
            if mission.get("current") is not None:
                score += 0.15
            if mission.get("metric_observation_id"):
                score += 0.15
            if observation.get("mentions_receipt_id") or int(observation.get("mention_count") or 0) == 0:
                score += 0.10
            return round(min(0.90, score), 6)

        if status in {"OK", "PUBLISHED", "SETTLED", "NO_NEW_OUTCOMES"}:
            return 0.75
        if status in {"ALREADY_PROCESSED"}:
            return 0.0
        return 0.15

    def pending(self, *, now: datetime | None = None) -> dict[str, Any] | None:
        trials = list(self.state.get("trials") or ())
        changed = False
        for index in range(len(trials) - 1, -1, -1):
            row = dict(trials[index])
            if str(row.get("status") or "") != "AWAITING_TARGET_OUTCOME":
                continue
            if now is not None:
                try:
                    created = datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00"))
                    expired = (now.astimezone(timezone.utc) - created.astimezone(timezone.utc)).total_seconds() >= 3600
                except (KeyError, TypeError, ValueError):
                    expired = True
                if expired:
                    row["status"] = "INCONCLUSIVE"
                    row["target_outcome"] = {"observed_at": now.isoformat(), "status": "TARGET_TIMEOUT", "utility": None}
                    trials[index] = row
                    changed = True
                    continue
            if changed:
                self.state["trials"] = trials
                _write(self.path, self.state)
            return row
        if changed:
            self.state["trials"] = trials
            _write(self.path, self.state)
        return None

    def _world(
        self,
        world_id: str | None = None,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        worlds = self.agency.state.get("worlds") or {}
        if world_id:
            row = worlds.get(str(world_id).upper())
            return dict(row) if isinstance(row, Mapping) else None
        candidate = self.agency.next_candidate_world()
        if isinstance(candidate, Mapping):
            return dict(candidate)
        revalidation = self.architecture_revalidation_candidate(now=now)
        if revalidation is not None:
            return revalidation
        return None

    def architecture_revalidation_candidate(
        self,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        if (
            self.architecture is None
            or not self.architecture.needs_evidence(now)
        ):
            return None
        worlds = self.agency.state.get("worlds") or {}
        adopted = [
            dict(row)
            for row in worlds.values()
            if isinstance(row, Mapping)
            and row.get("status") == "ADOPTED"
            and row.get("target_task")
        ]
        if not adopted:
            return None
        adopted.sort(
            key=lambda row: (
                str(row.get("last_transfer_evaluation_at") or ""),
                str(row.get("world_id") or ""),
            ),
            reverse=True,
        )
        return adopted[0]

    def begin(
        self,
        *,
        now: datetime | None = None,
        world_id: str | None = None,
        training_steps: int = 12,
    ) -> dict[str, Any]:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        pending = self.pending(now=now)
        if pending is not None:
            return {"status": "PENDING_EXISTS", "trial": pending}
        world = self._world(world_id, now=now)
        if world is None:
            return {"status": "NO_ELIGIBLE_WORLD", "trial": None}
        world_status = str(world.get("status") or "")
        architecture_only = bool(
            world_status == "ADOPTED"
            and self.architecture is not None
            and self.architecture.needs_evidence(now)
        )
        if world_status not in {"CANDIDATE", "EXPERIMENTING"} and not architecture_only:
            return {"status": "WORLD_NOT_EXPERIMENTING", "trial": None}

        seq = int(self.state.get("seq") or 0) + 1
        assignment = (
            "ARCHITECTURE_ONLY"
            if architecture_only
            else self._assignment(str(world["world_id"]), seq)
        )
        trial_id = f"development-transfer:{str(world['world_id']).lower()}:{seq:06d}"
        row = {
            "trial_id": trial_id,
            "seq": seq,
            "created_at": now.isoformat(),
            "world_id": world["world_id"],
            "need_id": world.get("need_id"),
            "proposal_id": world.get("proposal_id"),
            "target_task": world.get("target_task"),
            "assignment": assignment,
            "assignment_rule": (
                "architecture_only_reuse_adopted_world.v1"
                if architecture_only
                else "sha256_balanced_blocks_pre_intervention.v1"
            ),
            "world_status_at_assignment": world_status,
            "architecture_only": architecture_only,
            "status": "ASSIGNED",
            "training_steps": (
                int(training_steps)
                if assignment in {"TREATMENT", "ARCHITECTURE_ONLY"}
                else 0
            ),
            "training": None,
            "target_outcome": None,
            "architecture_trial_id": None,
            "architecture_assignment": None,
            "architecture_overlay": None,
            "architecture_settlement": None,
        }
        trials = list(self.state.get("trials") or ())
        trials.append(row)
        self.state.update({"seq": seq, "updated_at": now.isoformat(), "trials": trials[-MAX_TRIALS:]})
        # Persist the assignment before any self-generated-world intervention.
        _write(self.path, self.state)

        if assignment in {"TREATMENT", "ARCHITECTURE_ONLY"}:
            architecture_context = nullcontext({"status": "NO_ARCHITECTURE_SEARCH"})
            if self.architecture is not None:
                architecture_assignment = self.architecture.assign(
                    world_id=str(world["world_id"]),
                    source_trial_id=trial_id,
                    now=now,
                )
                architecture_trial = (
                    architecture_assignment.get("trial")
                    if isinstance(architecture_assignment, Mapping)
                    else None
                )
                if isinstance(architecture_trial, Mapping) and architecture_trial.get("trial_id"):
                    row["architecture_trial_id"] = str(architecture_trial["trial_id"])
                    row["architecture_assignment"] = str(architecture_trial.get("assignment") or "")
                    trials[-1] = row
                    self.state.update({"updated_at": now.isoformat(), "trials": trials[-MAX_TRIALS:]})
                    # The phenotype assignment is durable before the intervention.
                    _write(self.path, self.state)
                    architecture_context = self.architecture.trial_overlay(
                        str(architecture_trial["trial_id"])
                    )
            with architecture_context as overlay:
                row["architecture_overlay"] = dict(overlay or {})
                training = self.brain_lab.run_session(
                    environment=str(world["world_id"]),
                    steps=max(1, min(32, int(training_steps))),
                    now=now,
                )
            row["training"] = training
            if assignment == "TREATMENT":
                self.agency.record_world_trial(
                    world_id=str(world["world_id"]),
                    result=training,
                    observed_at=now,
                )
        row["status"] = "AWAITING_TARGET_OUTCOME"
        trials[-1] = row
        self.state.update({"updated_at": now.isoformat(), "trials": trials[-MAX_TRIALS:]})
        _write(self.path, self.state)
        return {"status": "STARTED", "trial": dict(row)}

    def _world_projection(self, world_id: str) -> dict[str, Any]:
        rows = [
            dict(row) for row in self.state.get("trials") or ()
            if row.get("world_id") == world_id
            and row.get("status") == "SETTLED"
            and row.get("assignment") in {"TREATMENT", "CONTROL"}
        ]
        arms: dict[str, dict[str, Any]] = {}
        for assignment in ("TREATMENT", "CONTROL"):
            subset = [row for row in rows if row.get("assignment") == assignment]
            utilities = [
                float((row.get("target_outcome") or {}).get("utility") or 0.0)
                for row in subset
            ]
            arms[assignment] = {
                "count": len(subset),
                "mean_target_utility": (
                    None if not utilities else round(sum(utilities) / len(utilities), 6)
                ),
            }
        treatment = arms["TREATMENT"]["mean_target_utility"]
        control = arms["CONTROL"]["mean_target_utility"]
        effect = (
            None if treatment is None or control is None
            else round(float(treatment) - float(control), 6)
        )
        ready = bool(
            arms["TREATMENT"]["count"] >= MIN_ARM
            and arms["CONTROL"]["count"] >= MIN_ARM
        )
        decision = "CONTINUE_EXPERIMENT"
        if ready:
            if float(effect or 0.0) >= ADOPT_EFFECT:
                decision = "ADOPT"
            elif float(effect or 0.0) <= REJECT_EFFECT:
                decision = "REJECT"
            else:
                decision = "REVISE"
        return {
            "world_id": world_id,
            "settled_count": len(rows),
            "arms": arms,
            "estimated_real_world_effect": effect,
            "causal_claim_ready": ready,
            "decision": decision,
            "minimum_per_arm": MIN_ARM,
        }

    def settle_target(
        self,
        *,
        task: str,
        result: Mapping[str, Any],
        trial_id: str | None = None,
        observed_at: datetime | None = None,
    ) -> dict[str, Any] | None:
        observed_at = (observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if not trial_id:
            return None
        trials = list(self.state.get("trials") or ())
        index = next(
            (
                i for i in range(len(trials) - 1, -1, -1)
                if str(trials[i].get("status") or "") == "AWAITING_TARGET_OUTCOME"
                and str(trials[i].get("trial_id") or "") == str(trial_id)
                and str(trials[i].get("target_task") or "") == str(task)
            ),
            None,
        )
        if index is None:
            return None
        row = dict(trials[index])
        result_status = str(result.get("status") or "UNKNOWN").upper()
        utility: float | None
        if result_status == "SHUTDOWN_CHECKPOINT":
            utility = None
            row["target_outcome"] = {
                "observed_at": observed_at.isoformat(),
                "status": result_status,
                "utility": None,
                "inconclusive_reason": "SERVICE_SHUTDOWN_DURING_TARGET",
            }
            row["status"] = "INCONCLUSIVE"
        else:
            utility = self._utility(task, result)
            row["target_outcome"] = {
                "observed_at": observed_at.isoformat(),
                "status": str(result.get("status") or "UNKNOWN"),
                "utility": utility,
            }
            row["status"] = "SETTLED"

        architecture_settlement = None
        architecture_trial_id = str(row.get("architecture_trial_id") or "")
        if architecture_trial_id and self.architecture is not None:
            architecture_settlement = self.architecture.settle(
                trial_id=architecture_trial_id,
                utility=utility,
                target_status=result_status,
                observed_at=observed_at,
            )
            row["architecture_settlement"] = architecture_settlement

        trials[index] = row
        self.state.update({"updated_at": observed_at.isoformat(), "trials": trials[-MAX_TRIALS:]})
        _write(self.path, self.state)

        evaluation = self._world_projection(str(row["world_id"]))
        if row["status"] == "SETTLED" and row.get("assignment") != "ARCHITECTURE_ONLY":
            self.agency.apply_world_transfer_evaluation(
                world_id=str(row["world_id"]),
                evaluation=evaluation,
                observed_at=observed_at,
            )
        return {
            **row,
            "world_evaluation": evaluation,
            "architecture_evaluation": architecture_settlement,
        }

    def projection(self) -> dict[str, Any]:
        rows = [dict(row) for row in self.state.get("trials") or ()]
        world_ids = sorted({str(row.get("world_id")) for row in rows if row.get("world_id")})
        worlds = {world_id: self._world_projection(world_id) for world_id in world_ids}
        return {
            "schema_version": SCHEMA,
            "updated_at": self.state.get("updated_at"),
            "trial_count": len(rows),
            "settled_count": sum(row.get("status") == "SETTLED" for row in rows),
            "pending": self.pending(),
            "worlds": worlds,
            "recent": rows[-16:],
            "selection_rule": {
                "minimum_per_arm": MIN_ARM,
                "adopt_effect_at_least": ADOPT_EFFECT,
                "reject_effect_at_most": REJECT_EFFECT,
                "otherwise": "REVISE",
            },
        }


__all__ = ["DevelopmentalWorldTransferLab", "SCHEMA", "MIN_ARM"]
