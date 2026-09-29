"""Offline R&D dreaming over already-paid-for repair history.

The neural dream remains the substrate-level replay mechanism.  This module is a
separate Researcher layer: it treats completed repair attempts as deterministic
replay worlds, mutates the policy that decides which LOW-risk problem deserves
scarce repair attention first, and only adopts a challenger when it is no worse
than the incumbent on the full replay pool.

The policy changes ordering only.  It cannot widen repair authority, target
protected boundaries, bypass tests, merge, push, or deploy.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

SCHEMA = "ferro-research-dreams.v1"
POLICY_KEYS = (
    "recency",
    "recurrence",
    "known_recipe",
    "diagnosability",
    "diversity",
)
PROTECTED_INVARIANTS = (
    "human_approval_boundary",
    "repair_guardian",
    "security_sandbox",
    "secret_access_boundary",
    "full_regression_gate",
    "production_deploy_authority",
)
MIN_REPLAY_WORLDS = 4
REPLAY_BUDGET = 4
MIN_HIDDEN_PAIR_CASES = 2
MAX_DREAMS = 64
MAX_PROMOTIONS_PER_REPLAY_POOL = 3
MIN_ONLINE_SAMPLES_PER_ARM = 2
ONLINE_TRIAL_MAX_DAYS = 14
TRIAL_SINGLE_CANDIDATE_MAX_WAIT_SECONDS = 600
OPEN_TRIAL_STATUSES = frozenset({"ACTIVE", "AWAITING_AWE_GATE"})


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def _aware(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _normalize_weights(raw: Mapping[str, Any]) -> dict[str, float]:
    values = {
        key: max(0.0, float(raw.get(key) or 0.0))
        for key in POLICY_KEYS
    }
    total = sum(values.values())
    if total <= 0.0:
        return {key: (1.0 if key == "recency" else 0.0) for key in POLICY_KEYS}
    return {key: round(values[key] / total, 6) for key in POLICY_KEYS}


def _policy_id(weights: Mapping[str, float]) -> str:
    canonical = json.dumps(
        _normalize_weights(weights), sort_keys=True, separators=(",", ":")
    )
    return "research-policy:" + sha256(canonical.encode()).hexdigest()[:20]


def _default_policy() -> dict[str, Any]:
    weights = _normalize_weights({"recency": 1.0})
    return {
        "policy_id": _policy_id(weights),
        "generation": 0,
        "weights": weights,
        "origin": "LEGACY_RECENCY_FIRST",
        "replay_promoted": False,
    }


def _outcome_reward(status: str) -> float:
    status = str(status or "").upper()
    if status in {"ADOPTED_VERIFIED", "VERIFIED", "RECOVERED_VERIFIED"}:
        return 1.0
    if status == "CANDIDATE_COMMITTED":
        return 0.75
    if "REPLAY_PASSED" in status or status == "OBSERVING_PRODUCTION":
        return 0.5
    if status in {"REPAIR_REQUESTED", "REPAIR_PROPOSED"}:
        return 0.1
    if status == "CAPABILITY_UNAVAILABLE":
        return -0.35
    if "REJECT" in status or "FAILED" in status or "QUARANTIN" in status:
        return -0.7
    return 0.0


class ResearchDreamEngine:
    """Dream-RSI-style replay for the Researcher policy, not model weights."""

    def __init__(self, state_root: Path) -> None:
        self.state_root = Path(state_root)
        self.path = self.state_root / "application" / "research_dreams.json"
        raw = _read(self.path)
        if raw.get("schema_version") == SCHEMA:
            self.state = raw
        else:
            self.state = {
                "schema_version": SCHEMA,
                "updated_at": None,
                "dream_count": 0,
                "researcher_generation": 0,
                "incumbent_policy": _default_policy(),
                "dreams": [],
                "promotion_count": 0,
                "reversion_count": 0,
                "online_rejection_count": 0,
                "prospective_trial": None,
                "offline_replay_migration": None,
                "validated_generation_count": 0,
                "protected_invariants": list(PROTECTED_INVARIANTS),
                "automatic_authority_expansion": False,
            }
        self._migrate_replay_only_incumbent()
        self._repair_migrated_trial_provenance()

    @staticmethod
    def _is_replay_only_policy(row: Mapping[str, Any] | None) -> bool:
        if not isinstance(row, Mapping):
            return False
        return (
            str(row.get("origin") or "").startswith("RESEARCH_DREAM_REPLAY:")
            and bool(row.get("online_validation_required"))
        )

    def _last_validated_incumbent(self) -> dict[str, Any]:
        history = [
            dict(row)
            for row in self.state.get("incumbent_history") or ()
            if isinstance(row, Mapping)
        ]
        current = self.state.get("incumbent_policy")
        candidates = [*history, current]
        for row in reversed(candidates):
            if not isinstance(row, Mapping):
                continue
            if self._is_replay_only_policy(row):
                continue
            origin = str(row.get("origin") or "")
            if origin.startswith("PROSPECTIVE_RESEARCH_TRIAL:"):
                return dict(row)
            if origin == "LEGACY_RECENCY_FIRST":
                return dict(row)
        return _default_policy()

    @staticmethod
    def _source_dream_for_policy(
        policy: Mapping[str, Any],
        dreams: list[dict[str, Any]],
    ) -> dict[str, Any]:
        origin = str(policy.get("origin") or "")
        origin_id = (
            origin.split("RESEARCH_DREAM_REPLAY:", 1)[1]
            if origin.startswith("RESEARCH_DREAM_REPLAY:")
            else ""
        )
        if origin_id:
            exact = next(
                (row for row in dreams if str(row.get("dream_id") or "") == origin_id),
                None,
            )
            if exact is not None:
                return exact
        policy_id = str(policy.get("policy_id") or "")
        matches = [
            row for row in dreams
            if str((row.get("winner") or {}).get("policy_id") or "") == policy_id
            and row.get("decision") == "PROMOTE_RESEARCH_POLICY_BOUNDED"
        ]
        return matches[-1] if matches else (dreams[-1] if dreams else {})

    def _migrate_replay_only_incumbent(self) -> None:
        """Demote pre-v2 replay-only incumbents into an online challenger trial."""
        current = self.state.get("incumbent_policy")
        if not self._is_replay_only_policy(current):
            self.state.setdefault(
                "validated_generation_count",
                int((current or {}).get("generation") or 0)
                if isinstance(current, Mapping) else 0,
            )
            self.state.setdefault("offline_replay_migration", None)
            return
        prior = self.state.get("offline_replay_migration")
        if isinstance(prior, Mapping) and str(prior.get("status") or "") == "MIGRATED":
            return
        now = datetime.now(timezone.utc)
        challenger = dict(current)
        baseline = self._last_validated_incumbent()
        validated_generation = int(baseline.get("generation") or 0)
        dreams = [
            dict(row)
            for row in self.state.get("dreams") or ()
            if isinstance(row, Mapping)
        ]
        source_dream = self._source_dream_for_policy(challenger, dreams)
        trial_id = "research-trial:migrated-" + sha256(
            (
                str(challenger.get("policy_id") or "")
                + "|"
                + str(source_dream.get("dream_id") or "")
                + "|"
                + str(self.state.get("replay_pool_hash") or "")
            ).encode()
        ).hexdigest()[:20]
        challenger.update({
            "generation": validated_generation + 1,
            "replay_promoted": False,
            "online_validation_required": True,
            "origin": (
                str(challenger.get("origin") or "")
                or f"RESEARCH_DREAM_REPLAY:{source_dream.get('dream_id') or 'legacy'}"
            ),
        })
        self.state["incumbent_policy"] = baseline
        self.state["researcher_generation"] = validated_generation
        self.state["validated_generation_count"] = validated_generation
        # Old promotion_count counted replay-only promotions. Preserve that
        # provenance separately and reset the production promotion metric.
        replay_only_promotions = int(self.state.get("promotion_count") or 0)
        self.state["promotion_count"] = 0
        legacy_dream_refs = [
            str(row.get("dream_id") or "")
            for row in dreams
            if str(row.get("decision") or "") == "PROMOTE_RESEARCH_POLICY_BOUNDED"
            and str(row.get("dream_id") or "")
        ]
        self.state["offline_promotion_invalidated_count"] = (
            replay_only_promotions
        )
        self.state["legacy_offline_promotions"] = {
            "invalidated_generation": int((current or {}).get("generation") or 0),
            "invalidated_policy_id": challenger.get("policy_id"),
            "invalidated_count": replay_only_promotions,
            "evidence_refs": legacy_dream_refs,
            "history_preserved": True,
        }
        self.state["reversion_count"] = int(
            self.state.get("reversion_count") or 0
        ) + 1
        self.state["prospective_trial"] = {
            "trial_id": trial_id,
            "status": "ACTIVE",
            "started_at": now.isoformat(),
            "baseline_policy": baseline,
            "challenger_policy": challenger,
            "replay_dream_id": source_dream.get("dream_id"),
            "replay_pool_hash": self.state.get("replay_pool_hash"),
            "replay_incumbent_score": (
                (source_dream.get("incumbent") or {}).get("score")
                if isinstance(source_dream.get("incumbent"), Mapping) else None
            ),
            "replay_challenger_score": (
                (source_dream.get("winner") or {}).get("score")
                if isinstance(source_dream.get("winner"), Mapping) else None
            ),
            "minimum_samples_per_arm": MIN_ONLINE_SAMPLES_PER_ARM,
            "assignments": {},
            "results": {},
            "origin": "MIGRATED_PRE_V2_REPLAY_ONLY_INCUMBENT",
        }
        self.state["offline_replay_migration"] = {
            "status": "MIGRATED",
            "migrated_at": now.isoformat(),
            "invalidated_replay_generation": int(
                (current or {}).get("generation") or 0
            ),
            "invalidated_policy_id": challenger.get("policy_id"),
            "restored_policy_id": baseline.get("policy_id"),
            "restored_generation": validated_generation,
            "prospective_trial_id": trial_id,
            "source_replay_dream_id": source_dream.get("dream_id"),
            "replay_only_promotion_count": replay_only_promotions,
            "history_preserved": True,
        }
        self.state["replay_pool_challenger_count"] = 1
        self.state.pop("replay_pool_promotion_count", None)
        self.state["updated_at"] = now.isoformat()
        self.state["protected_invariants"] = list(PROTECTED_INVARIANTS)
        self.state["automatic_authority_expansion"] = False
        _write(self.path, self.state)

    def _repair_migrated_trial_provenance(self) -> None:
        migration = self.state.get("offline_replay_migration")
        trial = self.state.get("prospective_trial")
        if (
            not isinstance(migration, Mapping)
            or str(migration.get("status") or "") != "MIGRATED"
            or not isinstance(trial, Mapping)
            or str(trial.get("origin") or "") != "MIGRATED_PRE_V2_REPLAY_ONLY_INCUMBENT"
        ):
            return
        dreams = [
            dict(row)
            for row in self.state.get("dreams") or ()
            if isinstance(row, Mapping)
        ]
        challenger = (
            dict(trial.get("challenger_policy"))
            if isinstance(trial.get("challenger_policy"), Mapping)
            else {}
        )
        source = self._source_dream_for_policy(challenger, dreams)
        updated_trial = dict(trial)
        updated_migration = dict(migration)
        changed = False
        source_id = source.get("dream_id")
        if source_id and updated_trial.get("replay_dream_id") != source_id:
            updated_trial["replay_dream_id"] = source_id
            updated_trial["replay_incumbent_score"] = (
                (source.get("incumbent") or {}).get("score")
                if isinstance(source.get("incumbent"), Mapping) else None
            )
            updated_trial["replay_challenger_score"] = (
                (source.get("winner") or {}).get("score")
                if isinstance(source.get("winner"), Mapping) else None
            )
            updated_migration["source_replay_dream_id"] = source_id
            changed = True
        if self.state.get("replay_pool_challenger_count") != 1:
            self.state["replay_pool_challenger_count"] = 1
            changed = True
        if not list(updated_trial.get("optimizer_world_ids") or ()):
            optimizer_worlds = self.replay_worlds()
            updated_trial["optimizer_world_ids"] = [
                row["world_id"] for row in optimizer_worlds
            ]
            updated_trial["optimizer_world_count"] = len(optimizer_worlds)
            updated_trial["awe_reserved_holdout_count"] = 0
            changed = True
        if "replay_pool_promotion_count" in self.state:
            self.state.pop("replay_pool_promotion_count", None)
            changed = True
        if changed:
            self.state["prospective_trial"] = updated_trial
            self.state["offline_replay_migration"] = updated_migration
            _write(self.path, self.state)

    def _problem_rows(self) -> dict[str, dict[str, Any]]:
        folder = self.state_root / "application" / "self_maintenance" / "problems"
        rows: dict[str, dict[str, Any]] = {}
        for path in folder.glob("*.json") if folder.exists() else ():
            row = _read(path)
            problem_id = str(row.get("problem_id") or "")
            if problem_id:
                rows[problem_id] = row
        return rows

    def replay_worlds(self) -> list[dict[str, Any]]:
        problems = self._problem_rows()
        root = self.state_root / "application" / "self_maintenance" / "repair_trials"
        worlds: list[dict[str, Any]] = []
        for path in root.glob("*.json") if root.exists() else ():
            trial = _read(path)
            problem_id = str(trial.get("problem_id") or "")
            problem = problems.get(problem_id, {})
            status = str(trial.get("status") or "")
            if not problem_id or not status:
                continue
            proposal = (
                problem.get("repair_proposal")
                if isinstance(problem.get("repair_proposal"), Mapping)
                else {}
            )
            repair_kind = str(
                trial.get("repair_kind")
                or proposal.get("repair_kind")
                or ""
            )
            observed = (
                _aware(trial.get("observed_at"))
                or _aware(problem.get("last_seen_at"))
                or datetime(1970, 1, 1, tzinfo=timezone.utc)
            )
            worlds.append({
                "world_id": "repair-replay:" + sha256(
                    f"{problem_id}|{status}|{observed.isoformat()}".encode()
                ).hexdigest()[:20],
                "problem_id": problem_id,
                "observed_at": observed.isoformat(),
                "occurrences": max(1, int(problem.get("occurrences") or 1)),
                "error_type": str(problem.get("error_type") or "UNKNOWN"),
                "failure_site": str(problem.get("failure_site") or "unknown"),
                "known_recipe": bool(
                    repair_kind
                    and repair_kind != "GENERATED_BOUNDED_EDIT"
                ),
                "repair_kind": repair_kind or None,
                "outcome_status": status,
                "reward": _outcome_reward(status),
                "evidence_ref": f"self-repair-trial:{problem_id.replace(':', '-')}",
            })
        worlds.sort(key=lambda row: (row["observed_at"], row["world_id"]))
        return worlds

    @staticmethod
    def _optimizer_holdout_split(
        worlds: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Reserve evaluator-only replay worlds before choosing a mutation."""
        max_holdout = max(0, len(worlds) - MIN_REPLAY_WORLDS)
        if max_holdout < 3:
            return list(worlds), []
        holdout_count = min(
            max_holdout,
            max(3, len(worlds) // 4),
        )
        ranked = sorted(
            worlds,
            key=lambda row: sha256(
                ("ferro-awe-holdout-v1|" + str(row["world_id"])).encode()
            ).hexdigest(),
        )
        holdout_ids = {
            str(row["world_id"]) for row in ranked[:holdout_count]
        }
        optimizer = [
            row for row in worlds if str(row["world_id"]) not in holdout_ids
        ]
        holdout = [
            row for row in worlds if str(row["world_id"]) in holdout_ids
        ]
        return optimizer, holdout

    def _pairwise_rank_eval(
        self,
        policy: Mapping[str, Any],
        worlds: list[dict[str, Any]],
    ) -> dict[str, Any]:
        features = self._features(worlds)
        correct = 0.0
        cases = 0
        ties = 0
        for index, left in enumerate(worlds):
            for right in worlds[index + 1:]:
                left_reward = float(left.get("reward") or 0.0)
                right_reward = float(right.get("reward") or 0.0)
                if left_reward == right_reward:
                    continue
                left_priority = self._priority(
                    left,
                    features=features,
                    weights=policy.get("weights") or {},
                )
                right_priority = self._priority(
                    right,
                    features=features,
                    weights=policy.get("weights") or {},
                )
                cases += 1
                if left_priority == right_priority:
                    correct += 0.5
                    ties += 1
                elif (left_priority > right_priority) == (left_reward > right_reward):
                    correct += 1.0
        return {
            "score": None if cases == 0 else round(correct / cases, 6),
            "case_count": cases,
            "tie_count": ties,
        }

    def awe_hidden_replay_receipt(
        self,
        *,
        trial_id: str,
    ) -> dict[str, Any]:
        """Evaluate a chosen mutation on replay worlds hidden from selection."""
        raw = _read(self.path)
        if raw.get("schema_version") == SCHEMA:
            self.state = raw
        trial = self.state.get("prospective_trial")
        if not isinstance(trial, Mapping) or str(trial.get("trial_id") or "") != str(trial_id):
            raise KeyError(f"unknown research trial: {trial_id}")
        optimizer_ids = {
            str(value)
            for value in (trial.get("optimizer_world_ids") or ())
            if str(value)
        }
        worlds = self.replay_worlds()
        hidden = [
            row for row in worlds
            if str(row.get("world_id") or "") not in optimizer_ids
        ]
        baseline = dict(trial.get("baseline_policy") or {})
        candidate = dict(trial.get("challenger_policy") or {})
        baseline_eval = self._pairwise_rank_eval(baseline, hidden)
        candidate_eval = self._pairwise_rank_eval(candidate, hidden)
        cases = int(baseline_eval["case_count"])
        baseline_score = baseline_eval["score"]
        candidate_score = candidate_eval["score"]

        if cases < MIN_HIDDEN_PAIR_CASES or baseline_score is None or candidate_score is None:
            decision = "WAIT_FOR_EVIDENCE"
            reason = "not enough evaluator-hidden pairwise replay cases yet"
            gain = None
        else:
            gain = round(float(candidate_score) - float(baseline_score), 6)
            if float(candidate_score) < float(baseline_score):
                decision = "ROLLBACK_NO_GENERALIZATION"
                reason = "candidate regressed evaluator-hidden repair-priority ranking"
            elif gain >= 0.01 or (
                float(baseline_score) >= 0.95
                and float(candidate_score) >= float(baseline_score)
            ):
                decision = "PROMOTE"
                reason = "candidate generalized on evaluator-hidden repair-priority pairs"
            else:
                decision = "ROLLBACK_NO_GENERALIZATION"
                reason = "candidate train gain did not transfer to evaluator-hidden pairs"

        train_baseline = trial.get("replay_incumbent_score")
        train_candidate = trial.get("replay_challenger_score")
        train_gain = (
            round(float(train_candidate) - float(train_baseline), 6)
            if isinstance(train_baseline, (int, float))
            and isinstance(train_candidate, (int, float))
            else None
        )
        return {
            "decision": decision,
            "reason": reason,
            "train": {
                "baseline_score": train_baseline,
                "candidate_score": train_candidate,
                "gain": train_gain,
                "optimizer_world_count": len(optimizer_ids),
            },
            "hidden": {
                "baseline_mean": baseline_score,
                "candidate_mean": candidate_score,
                "gain": gain,
                "noise_floor": 0.0,
                "n_baseline": cases,
                "n_candidate": int(candidate_eval["case_count"]),
                "infrastructure_error_count": 0,
            },
            "regression": {
                "protected_invariants": list(PROTECTED_INVARIANTS),
                "status": "UNCHANGED_BY_POLICY_SURFACE",
            },
        }

    @staticmethod
    def _trial_arm(problem_id: str, trial_id: str) -> str:
        digest = sha256(f"{trial_id}|{problem_id}".encode()).digest()
        return "CANDIDATE" if digest[0] & 1 else "BASELINE"

    def _repair_trial_outcome(
        self,
        problem_id: str,
        *,
        started_at: datetime,
    ) -> dict[str, Any] | None:
        safe = problem_id.replace(":", "-")
        row = _read(
            self.state_root
            / "application"
            / "self_maintenance"
            / "repair_trials"
            / f"{safe}.json"
        )
        if not row:
            return None
        observed = _aware(row.get("observed_at"))
        if observed is None or observed < started_at:
            return None
        status = str(row.get("status") or "")
        if not status:
            return None
        reward = _outcome_reward(status)
        return {
            "status": status,
            "observed_at": observed.isoformat(),
            "reward": reward,
            "negative": reward < 0.0,
            "evidence_ref": f"self-repair-trial:{safe}",
        }

    def _promote_prospective_trial(
        self,
        row: dict[str, Any],
        *,
        now: datetime,
    ) -> dict[str, Any]:
        if str(row.get("status") or "") == "PROMOTED":
            return row
        challenger = dict(row.get("challenger_policy") or {})
        generation = int(self.state.get("researcher_generation") or 0) + 1
        challenger.update({
            "generation": generation,
            "origin": f"PROSPECTIVE_RESEARCH_TRIAL:{row.get('trial_id')}",
            "replay_promoted": True,
            "promoted_at": now.isoformat(),
            "promotion_evidence_ref": str(row.get("trial_id") or ""),
            "online_validation_required": False,
            "awe_generalization_validated": True,
            "awe_experiment_id": row.get("awe_experiment_id"),
        })
        previous = dict(self.state.get("incumbent_policy") or _default_policy())
        history = [
            dict(item) for item in self.state.get("incumbent_history") or ()
            if isinstance(item, Mapping)
        ]
        history.append(previous)
        self.state["incumbent_history"] = history[-32:]
        self.state["incumbent_policy"] = challenger
        self.state["researcher_generation"] = generation
        self.state["validated_generation_count"] = generation
        self.state["promotion_count"] = int(
            self.state.get("promotion_count") or 0
        ) + 1
        row["status"] = "PROMOTED"
        row["decided_at"] = now.isoformat()
        row["decision"] = "PROMOTE_AFTER_ONLINE_AND_AWE_GENERALIZATION"
        row["awe_gate_status"] = "PROMOTED"
        return row

    def _refresh_prospective_trial(
        self,
        *,
        now: datetime,
    ) -> dict[str, Any] | None:
        trial = self.state.get("prospective_trial")
        if not isinstance(trial, Mapping) or str(trial.get("status") or "") != "ACTIVE":
            return dict(trial) if isinstance(trial, Mapping) else None
        row = dict(trial)
        row.setdefault("dispatch_count", 0)
        row.setdefault("selection_rounds", [])
        started_at = _aware(row.get("started_at"))
        if started_at is None:
            row["status"] = "INVALID"
            self.state["prospective_trial"] = row
            return row
        assignments = {
            str(key): str(value)
            for key, value in (row.get("assignments") or {}).items()
            if str(key) and str(value) in {"BASELINE", "CANDIDATE"}
        }
        results = dict(row.get("results") or {})
        for problem_id, arm in assignments.items():
            if problem_id in results:
                continue
            outcome = self._repair_trial_outcome(
                problem_id,
                started_at=started_at,
            )
            if outcome is not None:
                results[problem_id] = {"arm": arm, **outcome}
        row["assignments"] = assignments
        row["results"] = results

        arm_rows: dict[str, list[dict[str, Any]]] = {"BASELINE": [], "CANDIDATE": []}
        for result in results.values():
            if isinstance(result, Mapping) and result.get("arm") in arm_rows:
                arm_rows[str(result["arm"])].append(dict(result))
        summary = {}
        for arm, values in arm_rows.items():
            rewards = [float(item.get("reward") or 0.0) for item in values]
            summary[arm] = {
                "count": len(values),
                "mean_reward": (
                    round(sum(rewards) / len(rewards), 6) if rewards else None
                ),
                "negative_count": sum(1 for value in rewards if value < 0.0),
            }
        row["arm_summary"] = summary

        enough = all(
            summary[arm]["count"] >= MIN_ONLINE_SAMPLES_PER_ARM
            for arm in ("BASELINE", "CANDIDATE")
        )
        expired = (now - started_at).total_seconds() >= ONLINE_TRIAL_MAX_DAYS * 86400
        if enough:
            baseline = summary["BASELINE"]
            candidate = summary["CANDIDATE"]
            non_degrading = (
                float(candidate["mean_reward"]) >= float(baseline["mean_reward"])
                and int(candidate["negative_count"]) <= int(baseline["negative_count"])
            )
            row["online_validation"] = {
                "passed": bool(non_degrading),
                "baseline": dict(baseline),
                "candidate": dict(candidate),
                "evaluated_at": now.isoformat(),
            }
            if non_degrading:
                if str(row.get("awe_gate_decision") or "") == "PROMOTE":
                    row = self._promote_prospective_trial(row, now=now)
                else:
                    row["status"] = "AWAITING_AWE_GATE"
                    row["decision"] = "WAIT_FOR_AWE_GENERALIZATION"
                    row["awe_gate_status"] = str(
                        row.get("awe_gate_status") or "PENDING"
                    )
            else:
                row["status"] = "REJECTED"
                row["decided_at"] = now.isoformat()
                row["decision"] = "KILL_CHALLENGER_AFTER_PROSPECTIVE_REGRESSION"
                self.state["online_rejection_count"] = int(
                    self.state.get("online_rejection_count") or 0
                ) + 1
        elif expired:
            row["status"] = "EXPIRED"
            row["decided_at"] = now.isoformat()
            row["decision"] = "KEEP_INCUMBENT_INSUFFICIENT_ONLINE_EVIDENCE"
        self.state["prospective_trial"] = row
        return row

    def bind_awe_experiment(
        self,
        *,
        trial_id: str,
        experiment_id: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        raw = _read(self.path)
        if raw.get("schema_version") == SCHEMA:
            self.state = raw
        trial = self.state.get("prospective_trial")
        if not isinstance(trial, Mapping) or str(trial.get("trial_id") or "") != str(trial_id):
            raise KeyError(f"unknown research trial: {trial_id}")
        row = dict(trial)
        existing = str(row.get("awe_experiment_id") or "")
        if existing and existing != str(experiment_id):
            raise ValueError("research trial is already bound to a different AWE experiment")
        if str(row.get("status") or "") not in OPEN_TRIAL_STATUSES:
            raise ValueError("terminal research trial cannot be rebound to AWE")
        row["awe_experiment_id"] = str(experiment_id)
        row["awe_gate_status"] = str(row.get("awe_gate_status") or "PENDING")
        row["awe_bound_at"] = now.isoformat()
        self.state["prospective_trial"] = row
        self.state["updated_at"] = now.isoformat()
        _write(self.path, self.state)
        return row

    def apply_awe_gate(
        self,
        *,
        experiment_id: str,
        decision: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        raw = _read(self.path)
        if raw.get("schema_version") == SCHEMA:
            self.state = raw
        trial = self.state.get("prospective_trial")
        if not isinstance(trial, Mapping):
            raise KeyError(f"no research trial for AWE experiment: {experiment_id}")
        row = dict(trial)
        if str(row.get("awe_experiment_id") or "") != str(experiment_id):
            raise KeyError(f"unknown AWE experiment for research trial: {experiment_id}")
        normalized = str(decision or "").upper()
        status = str(row.get("status") or "")
        if status in {"PROMOTED", "REJECTED", "EXPIRED"}:
            return row

        row["awe_gate_decision"] = normalized
        row["awe_gate_decided_at"] = now.isoformat()
        if normalized == "PROMOTE":
            row["awe_gate_status"] = "APPROVED"
            online = (
                row.get("online_validation")
                if isinstance(row.get("online_validation"), Mapping)
                else {}
            )
            if status == "AWAITING_AWE_GATE" and bool(online.get("passed")):
                row = self._promote_prospective_trial(row, now=now)
            else:
                row["decision"] = "AWE_APPROVED_AWAITING_ONLINE_VALIDATION"
        elif normalized.startswith("ROLLBACK"):
            row["awe_gate_status"] = "REJECTED"
            row["status"] = "REJECTED"
            row["decided_at"] = now.isoformat()
            row["decision"] = "KILL_CHALLENGER_AFTER_AWE_NO_GENERALIZATION"
            self.state["online_rejection_count"] = int(
                self.state.get("online_rejection_count") or 0
            ) + 1
        elif normalized == "INVALID_INFRASTRUCTURE":
            row["awe_gate_status"] = "INFRASTRUCTURE_INVALID"
            row["decision"] = "WAIT_FOR_VALID_AWE_EVIDENCE"
        elif normalized.startswith("WAIT"):
            row["awe_gate_status"] = "PENDING"
            row["decision"] = "WAIT_FOR_AWE_GENERALIZATION"
        else:
            raise ValueError(f"unsupported AWE gate decision: {decision}")

        self.state["prospective_trial"] = row
        self.state["updated_at"] = now.isoformat()
        _write(self.path, self.state)
        return row

    @staticmethod
    def _features(rows: list[Mapping[str, Any]]) -> dict[str, dict[str, float]]:
        if not rows:
            return {}
        times = [_aware(row.get("observed_at")) for row in rows]
        valid_times = [value for value in times if value is not None]
        oldest = min(valid_times) if valid_times else datetime(1970, 1, 1, tzinfo=timezone.utc)
        newest = max(valid_times) if valid_times else oldest
        span = max(1.0, (newest - oldest).total_seconds())
        max_occ = max(max(1, int(row.get("occurrences") or 1)) for row in rows)
        groups: dict[str, int] = {}
        for row in rows:
            group = str(row.get("error_type") or "UNKNOWN") + "|" + str(
                row.get("failure_site") or "unknown"
            ).split(">", 1)[0]
            groups[group] = groups.get(group, 0) + 1
        output: dict[str, dict[str, float]] = {}
        for row in rows:
            observed = _aware(row.get("observed_at")) or oldest
            group = str(row.get("error_type") or "UNKNOWN") + "|" + str(
                row.get("failure_site") or "unknown"
            ).split(">", 1)[0]
            occurrences = max(1, int(row.get("occurrences") or 1))
            output[str(row.get("problem_id") or row.get("world_id"))] = {
                "recency": max(0.0, min(1.0, (observed - oldest).total_seconds() / span)),
                "recurrence": (
                    math.log1p(occurrences) / math.log1p(max_occ)
                    if max_occ > 1 else 1.0
                ),
                "known_recipe": 1.0 if bool(row.get("known_recipe")) else 0.0,
                "diagnosability": (
                    0.0
                    if str(row.get("failure_site") or "unknown") in {"", "unknown"}
                    else 1.0
                ),
                "diversity": 1.0 / float(groups.get(group, 1)),
            }
        return output

    @staticmethod
    def _priority(
        row: Mapping[str, Any],
        *,
        features: Mapping[str, Mapping[str, float]],
        weights: Mapping[str, float],
    ) -> float:
        key = str(row.get("problem_id") or row.get("world_id"))
        values = features.get(key, {})
        normalized = _normalize_weights(weights)
        return sum(
            normalized[name] * float(values.get(name, 0.0))
            for name in POLICY_KEYS
        )

    @staticmethod
    def _prepare_problem_rows(
        rows: Iterable[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        values = [dict(row) for row in rows]
        for row in values:
            proposal = row.get("repair_proposal")
            repair_kind = (
                str(proposal.get("repair_kind") or "")
                if isinstance(proposal, Mapping) else ""
            )
            row["known_recipe"] = bool(
                repair_kind and repair_kind != "GENERATED_BOUNDED_EDIT"
            )
            row.setdefault("observed_at", row.get("last_seen_at"))
        return values

    def _rank_with_policy(
        self,
        rows: Iterable[Mapping[str, Any]],
        policy: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        values = self._prepare_problem_rows(rows)
        if not values:
            return []
        features = self._features(values)
        weights = policy.get("weights") if isinstance(policy, Mapping) else None
        values.sort(
            key=lambda row: (
                self._priority(
                    row,
                    features=features,
                    weights=weights or _default_policy()["weights"],
                ),
                str(row.get("last_seen_at") or ""),
                str(row.get("problem_id") or ""),
            ),
            reverse=True,
        )
        return values

    def rank_problem_rows(
        self,
        rows: Iterable[Mapping[str, Any]],
        *,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Rank by the validated incumbent only; never assign trial arms here."""
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        raw = _read(self.path)
        if raw.get("schema_version") == SCHEMA:
            self.state = raw
        self._refresh_prospective_trial(now=now)
        incumbent = (
            dict(self.state.get("incumbent_policy"))
            if isinstance(self.state.get("incumbent_policy"), Mapping)
            else _default_policy()
        )
        values = self._rank_with_policy(rows, incumbent)
        self.state["updated_at"] = now.isoformat()
        _write(self.path, self.state)
        return values

    def select_problem_rows(
        self,
        rows: Iterable[Mapping[str, Any]],
        *,
        now: datetime | None = None,
        legacy_budget: int = 4,
    ) -> list[dict[str, Any]]:
        """Spend real repair slots under incumbent/challenger selection policy.

        Offline replay only proposes a challenger.  During an active
        prospective trial, baseline and candidate alternate one scarce repair
        slot over the same pool of future LOW-risk problems.  Only the problem
        that actually receives a slot is assigned to an arm and can contribute
        outcome evidence.
        """
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        raw = _read(self.path)
        if raw.get("schema_version") == SCHEMA:
            self.state = raw
        trial = self._refresh_prospective_trial(now=now)
        values = self._prepare_problem_rows(rows)
        incumbent = (
            dict(self.state.get("incumbent_policy"))
            if isinstance(self.state.get("incumbent_policy"), Mapping)
            else _default_policy()
        )
        active = (
            dict(trial)
            if isinstance(trial, Mapping) and str(trial.get("status") or "") == "ACTIVE"
            else None
        )
        if not values:
            self.state["updated_at"] = now.isoformat()
            _write(self.path, self.state)
            return []
        if active is None:
            selected = self._rank_with_policy(values, incumbent)[: max(1, int(legacy_budget))]
            self.state["updated_at"] = now.isoformat()
            _write(self.path, self.state)
            return selected

        started_at = _aware(active.get("started_at"))
        if started_at is None:
            selected = self._rank_with_policy(values, incumbent)[: max(1, int(legacy_budget))]
            self.state["updated_at"] = now.isoformat()
            _write(self.path, self.state)
            return selected

        assignments = {
            str(key): str(value)
            for key, value in (active.get("assignments") or {}).items()
            if str(key) and str(value) in {"BASELINE", "CANDIDATE"}
        }
        results = {
            str(key): dict(value)
            for key, value in (active.get("results") or {}).items()
            if str(key) and isinstance(value, Mapping)
        }
        # A selected problem whose repair transport was temporarily unavailable
        # keeps its arm identity and is retried before opening another slot.
        unresolved_selected = [
            row for row in values
            if str(row.get("problem_id") or "") in assignments
            and str(row.get("problem_id") or "") not in results
        ]
        if unresolved_selected:
            selected = sorted(
                unresolved_selected,
                key=lambda row: (
                    str(row.get("last_seen_at") or ""),
                    str(row.get("problem_id") or ""),
                ),
                reverse=True,
            )[:1]
            for row in selected:
                pid = str(row.get("problem_id") or "")
                row["research_trial_arm"] = assignments.get(pid)
                row["research_trial_id"] = active.get("trial_id")
                row["research_trial_retry"] = True
            self.state["prospective_trial"] = active
            self.state["updated_at"] = now.isoformat()
            _write(self.path, self.state)
            return selected

        old_backlog: list[dict[str, Any]] = []
        future: list[dict[str, Any]] = []
        for row in values:
            problem_id = str(row.get("problem_id") or "")
            seen_at = _aware(row.get("last_seen_at"))
            if not problem_id or seen_at is None or seen_at < started_at:
                old_backlog.append(row)
            elif problem_id not in assignments:
                future.append(row)

        if not future:
            selected = self._rank_with_policy(old_backlog, incumbent)[: max(1, int(legacy_budget))]
            self.state["prospective_trial"] = active
            self.state["updated_at"] = now.isoformat()
            _write(self.path, self.state)
            return selected

        if len(future) == 1:
            only = future[0]
            seen_at = _aware(only.get("last_seen_at")) or now
            if (now - seen_at).total_seconds() < TRIAL_SINGLE_CANDIDATE_MAX_WAIT_SECONDS:
                active["queued_single_problem_id"] = only.get("problem_id")
                active["queued_single_since"] = seen_at.isoformat()
                self.state["prospective_trial"] = active
                self.state["updated_at"] = now.isoformat()
                _write(self.path, self.state)
                return []
            # Do not hold a repair hostage for experimental pairing.  Dispatch
            # via incumbent after the wait bound, but do not count it as trial
            # evidence.
            selected = self._rank_with_policy(future, incumbent)[:1]
            selected[0]["research_trial_arm"] = "UNCOUNTED_INCUMBENT_FALLBACK"
            selected[0]["research_trial_id"] = active.get("trial_id")
            rounds = list(active.get("selection_rounds") or ())
            rounds.append({
                "round": int(active.get("dispatch_count") or 0),
                "selected_at": now.isoformat(),
                "arm": "UNCOUNTED",
                "policy_id": incumbent.get("policy_id"),
                "candidate_problem_ids": [str(only.get("problem_id") or "")],
                "selected_problem_id": str(selected[0].get("problem_id") or ""),
                "reason": "SINGLE_CANDIDATE_WAIT_EXPIRED",
            })
            active["selection_rounds"] = rounds[-64:]
            active.pop("queued_single_problem_id", None)
            active.pop("queued_single_since", None)
            self.state["prospective_trial"] = active
            self.state["updated_at"] = now.isoformat()
            _write(self.path, self.state)
            return selected

        dispatch_count = int(active.get("dispatch_count") or 0)
        arm = "BASELINE" if dispatch_count % 2 == 0 else "CANDIDATE"
        policy = (
            dict(active.get("baseline_policy") or incumbent)
            if arm == "BASELINE"
            else dict(active.get("challenger_policy") or incumbent)
        )
        ranked = self._rank_with_policy(future, policy)
        selected = ranked[:1]
        if not selected:
            return []
        problem_id = str(selected[0].get("problem_id") or "")
        assignments[problem_id] = arm
        selected[0]["research_trial_arm"] = arm
        selected[0]["research_trial_id"] = active.get("trial_id")
        rounds = list(active.get("selection_rounds") or ())
        rounds.append({
            "round": dispatch_count + 1,
            "selected_at": now.isoformat(),
            "arm": arm,
            "policy_id": policy.get("policy_id"),
            "candidate_problem_ids": [
                str(row.get("problem_id") or "") for row in future
            ],
            "selected_problem_id": problem_id,
            "reason": "PROSPECTIVE_REPAIR_SLOT",
        })
        active["assignments"] = assignments
        active["dispatch_count"] = dispatch_count + 1
        active["selection_rounds"] = rounds[-64:]
        active.pop("queued_single_problem_id", None)
        active.pop("queued_single_since", None)
        self.state["prospective_trial"] = active
        self.state["updated_at"] = now.isoformat()
        _write(self.path, self.state)
        return selected

    @staticmethod
    def _mutations(incumbent: Mapping[str, Any]) -> list[dict[str, Any]]:
        base = _normalize_weights(
            incumbent.get("weights") if isinstance(incumbent, Mapping) else {}
        )
        candidates: dict[str, dict[str, Any]] = {}

        def add(weights: Mapping[str, float], origin: str) -> None:
            normalized = _normalize_weights(weights)
            pid = _policy_id(normalized)
            candidates[pid] = {
                "policy_id": pid,
                "weights": normalized,
                "origin": origin,
            }

        add(base, "INCUMBENT")
        add(
            {
                "recency": 0.35,
                "recurrence": 0.20,
                "known_recipe": 0.25,
                "diagnosability": 0.10,
                "diversity": 0.10,
            },
            "BALANCED_SEED",
        )
        for donor in POLICY_KEYS:
            for receiver in POLICY_KEYS:
                if donor == receiver or base.get(donor, 0.0) <= 0.0:
                    continue
                delta = min(0.20, max(0.05, base[donor] * 0.25))
                mutated = dict(base)
                mutated[donor] = max(0.0, mutated[donor] - delta)
                mutated[receiver] = mutated.get(receiver, 0.0) + delta
                add(mutated, f"MOVE_{donor.upper()}_TO_{receiver.upper()}")
        return list(candidates.values())

    def _evaluate(
        self,
        policy: Mapping[str, Any],
        worlds: list[dict[str, Any]],
    ) -> dict[str, Any]:
        features = self._features(worlds)
        ranked = sorted(
            worlds,
            key=lambda row: (
                self._priority(
                    row,
                    features=features,
                    weights=policy.get("weights") or {},
                ),
                row["observed_at"],
                row["world_id"],
            ),
            reverse=True,
        )
        budget = min(REPLAY_BUDGET, len(ranked))
        selected = ranked[:budget]
        rewards = [float(row.get("reward") or 0.0) for row in selected]
        negative_count = sum(1 for value in rewards if value < 0.0)
        successful_count = sum(1 for value in rewards if value >= 0.75)
        groups = {
            str(row.get("error_type") or "") + "|"
            + str(row.get("failure_site") or "").split(">", 1)[0]
            for row in selected
        }
        mean_reward = sum(rewards) / max(1, len(rewards))
        diversity_fraction = len(groups) / max(1, len(selected))
        negative_fraction = negative_count / max(1, len(selected))
        score = mean_reward + 0.04 * diversity_fraction - 0.04 * negative_fraction
        return {
            "policy_id": policy["policy_id"],
            "weights": dict(policy.get("weights") or {}),
            "origin": policy.get("origin"),
            "score": round(score, 6),
            "mean_reward": round(mean_reward, 6),
            "negative_count": negative_count,
            "successful_count": successful_count,
            "budget": budget,
            "selected_world_ids": [row["world_id"] for row in selected],
            "selected_problem_ids": [row["problem_id"] for row in selected],
        }

    def dream(self, *, now: datetime | None = None) -> dict[str, Any]:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        # Reload so another process can safely produce a dream between scheduler ticks.
        raw = _read(self.path)
        if raw.get("schema_version") == SCHEMA:
            self.state = raw
        prior_trial = self.state.get("prospective_trial")
        trial_was_open = (
            isinstance(prior_trial, Mapping)
            and str(prior_trial.get("status") or "") in OPEN_TRIAL_STATUSES
        )
        trial_state = self._refresh_prospective_trial(now=now)
        trial_resolved_this_dream = bool(
            trial_was_open
            and str((trial_state or {}).get("status") or "") not in OPEN_TRIAL_STATUSES
        )
        worlds = self.replay_worlds()
        if len(worlds) < MIN_REPLAY_WORLDS:
            return {
                "status": "INSUFFICIENT_REPLAY_WORLDS",
                "replay_world_count": len(worlds),
                "minimum": MIN_REPLAY_WORLDS,
                "researcher_generation": int(self.state.get("researcher_generation") or 0),
            }
        pool_hash = "sha256:" + sha256(
            json.dumps(
                [(row["world_id"], row["outcome_status"]) for row in worlds],
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        optimizer_worlds, reserved_holdout = self._optimizer_holdout_split(worlds)
        if len(optimizer_worlds) < MIN_REPLAY_WORLDS:
            return {
                "status": "INSUFFICIENT_OPTIMIZER_REPLAY_WORLDS",
                "replay_world_count": len(worlds),
                "optimizer_world_count": len(optimizer_worlds),
                "minimum": MIN_REPLAY_WORLDS,
                "researcher_generation": int(self.state.get("researcher_generation") or 0),
            }
        same_pool = str(self.state.get("replay_pool_hash") or "") == pool_hash
        pool_challengers = (
            int(
                self.state.get("replay_pool_challenger_count")
                or self.state.get("replay_pool_promotion_count")
                or 0
            )
            if same_pool else 0
        )
        # Migration for dreams produced before replay_pool_hash existed: exact
        # world-ref equality is enough to recover how many generations already
        # optimized this same paid-for history.
        if not same_pool:
            current_refs = [row["evidence_ref"] for row in worlds]
            prior_same_pool = [
                row for row in (self.state.get("dreams") or ())
                if isinstance(row, Mapping)
                and list(row.get("replay_world_refs") or ()) == current_refs
            ]
            if prior_same_pool:
                same_pool = True
                pool_challengers = sum(
                    1 for row in prior_same_pool
                    if row.get("decision") == "START_RESEARCH_POLICY_TRIAL"
                )

        incumbent = (
            dict(self.state.get("incumbent_policy"))
            if isinstance(self.state.get("incumbent_policy"), Mapping)
            else _default_policy()
        )
        candidates = self._mutations(incumbent)
        evaluations = [
            self._evaluate(policy, optimizer_worlds) for policy in candidates
        ]
        incumbent_eval = next(
            row for row in evaluations if row["policy_id"] == incumbent["policy_id"]
        )
        evaluations.sort(
            key=lambda row: (
                row["score"],
                -row["negative_count"],
                row["successful_count"],
                row["policy_id"],
            ),
            reverse=True,
        )
        winner_eval = evaluations[0]
        winner_policy = next(
            row for row in candidates
            if row["policy_id"] == winner_eval["policy_id"]
        )
        challenger = winner_eval["policy_id"] != incumbent["policy_id"]
        non_degrading = (
            winner_eval["score"] >= incumbent_eval["score"]
            and winner_eval["negative_count"] <= incumbent_eval["negative_count"]
        )
        meaningful_gain = winner_eval["score"] >= incumbent_eval["score"] + 0.02
        replay_budget_open = pool_challengers < MAX_PROMOTIONS_PER_REPLAY_POOL
        active_trial = (
            dict(self.state.get("prospective_trial"))
            if isinstance(self.state.get("prospective_trial"), Mapping)
            and str(self.state.get("prospective_trial", {}).get("status") or "")
            in OPEN_TRIAL_STATUSES
            else None
        )
        start_trial = bool(
            challenger
            and non_degrading
            and meaningful_gain
            and replay_budget_open
            and active_trial is None
            and not trial_resolved_this_dream
        )

        seq = int(self.state.get("dream_count") or 0) + 1
        dream_id = f"research-dream:{seq}"
        generation_before = int(self.state.get("researcher_generation") or 0)
        generation_after = generation_before
        prospective_trial = active_trial
        if start_trial:
            trial_id = "research-trial:" + sha256(
                f"{dream_id}|{winner_eval['policy_id']}|{pool_hash}".encode()
            ).hexdigest()[:20]
            prospective_trial = {
                "trial_id": trial_id,
                "status": "ACTIVE",
                "started_at": now.isoformat(),
                "baseline_policy": incumbent,
                "challenger_policy": {
                    **winner_policy,
                    "origin": f"RESEARCH_DREAM_REPLAY:{dream_id}",
                    "replay_promoted": False,
                    "online_validation_required": True,
                },
                "replay_dream_id": dream_id,
                "replay_pool_hash": pool_hash,
                "replay_incumbent_score": incumbent_eval["score"],
                "replay_challenger_score": winner_eval["score"],
                "optimizer_world_ids": [
                    row["world_id"] for row in optimizer_worlds
                ],
                "optimizer_world_count": len(optimizer_worlds),
                "awe_reserved_holdout_count": len(reserved_holdout),
                "minimum_samples_per_arm": MIN_ONLINE_SAMPLES_PER_ARM,
                "assignments": {},
                "results": {},
            }
            self.state["prospective_trial"] = prospective_trial
        dream = {
            "dream_id": dream_id,
            "dreamed_at": now.isoformat(),
            "origin": "OFFLINE_REPAIR_HISTORY_REPLAY",
            "replay_world_count": len(worlds),
            "replay_world_refs": [
                row["evidence_ref"] for row in optimizer_worlds
            ],
            "optimizer_replay_world_count": len(optimizer_worlds),
            "awe_reserved_holdout_count": len(reserved_holdout),
            "incumbent": incumbent_eval,
            "winner": winner_eval,
            "candidate_count": len(evaluations),
            "candidate_evaluations": evaluations,
            "non_degrading_vs_incumbent": non_degrading,
            "meaningful_gain": meaningful_gain,
            "replay_pool_hash": pool_hash,
            "same_replay_pool": same_pool,
            "replay_pool_challenger_count_before": pool_challengers,
            "replay_pool_promotion_limit": MAX_PROMOTIONS_PER_REPLAY_POOL,
            "decision": (
                "START_RESEARCH_POLICY_TRIAL"
                if start_trial else (
                    (
                        "WAIT_FOR_AWE_GENERALIZATION"
                        if active_trial is not None
                        and str(active_trial.get("status") or "") == "AWAITING_AWE_GATE"
                        else "WAIT_FOR_PROSPECTIVE_RESULTS"
                    )
                    if active_trial is not None or trial_resolved_this_dream
                    else (
                    "WAIT_FOR_NEW_REPLAY_WORLD"
                    if challenger and non_degrading and meaningful_gain and not replay_budget_open
                    else "KEEP_INCUMBENT"
                    )
                )
            ),
            "researcher_generation_before": generation_before,
            "researcher_generation_after": generation_after,
            "online_validation_required": True,
            "prospective_trial_id": (
                None if prospective_trial is None else prospective_trial.get("trial_id")
            ),
            "prospective_trial_status": (
                None if prospective_trial is None else prospective_trial.get("status")
            ),
            "causal_claim": False,
            "protected_invariants": list(PROTECTED_INVARIANTS),
        }
        history = list(self.state.get("dreams") or ())
        history.append(dream)
        self.state["dream_count"] = seq
        self.state["researcher_generation"] = generation_after
        self.state["updated_at"] = now.isoformat()
        self.state["dreams"] = history[-MAX_DREAMS:]
        self.state["replay_pool_hash"] = pool_hash
        self.state["replay_pool_challenger_count"] = (
            pool_challengers + (1 if start_trial else 0)
        )
        self.state.pop("replay_pool_promotion_count", None)
        self.state.setdefault("incumbent_policy", incumbent)
        self.state.setdefault("promotion_count", 0)
        self.state.setdefault("reversion_count", 0)
        self.state["protected_invariants"] = list(PROTECTED_INVARIANTS)
        self.state["automatic_authority_expansion"] = False
        _write(self.path, self.state)
        return {
            "status": "RESEARCH_DREAMED",
            "dream": dream,
            "incumbent_policy": dict(self.state["incumbent_policy"]),
        }

    def projection(self) -> dict[str, Any]:
        raw = _read(self.path)
        if raw.get("schema_version") == SCHEMA:
            self.state = raw
        dreams = [
            dict(row)
            for row in self.state.get("dreams") or ()
            if isinstance(row, Mapping)
        ]
        return {
            "schema_version": SCHEMA,
            "updated_at": self.state.get("updated_at"),
            "dream_count": int(self.state.get("dream_count") or 0),
            "researcher_generation": int(self.state.get("researcher_generation") or 0),
            "incumbent_policy": dict(
                self.state.get("incumbent_policy") or _default_policy()
            ),
            "promotion_count": int(self.state.get("promotion_count") or 0),
            "reversion_count": int(self.state.get("reversion_count") or 0),
            "online_rejection_count": int(
                self.state.get("online_rejection_count") or 0
            ),
            "prospective_trial": (
                dict(self.state["prospective_trial"])
                if isinstance(self.state.get("prospective_trial"), Mapping)
                else None
            ),
            "latest_dream": dreams[-1] if dreams else None,
            "replay_pool_hash": self.state.get("replay_pool_hash"),
            "replay_pool_challenger_count": int(
                self.state.get("replay_pool_challenger_count") or 0
            ),
            "replay_pool_promotion_limit": MAX_PROMOTIONS_PER_REPLAY_POOL,
            "protected_invariants": list(PROTECTED_INVARIANTS),
            "automatic_authority_expansion": False,
        }


__all__ = [
    "ResearchDreamEngine",
    "SCHEMA",
    "POLICY_KEYS",
    "PROTECTED_INVARIANTS",
]
