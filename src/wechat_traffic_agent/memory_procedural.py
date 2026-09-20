from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Iterable, Mapping

from .memory_formation import FormationContext, MemoryFormationPipeline
from .memory_retrieval import MemoryRouter
from .memory_system import CanonicalMemoryStore


def _variant_id(steps: Iterable[str], capabilities: Iterable[str]) -> str:
    payload = json.dumps({
        "steps": [str(item) for item in steps if str(item).strip()],
        "capabilities": [str(item) for item in capabilities if str(item).strip()],
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "procvar:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def _variant_score(row: Mapping[str, Any]) -> float:
    executions = max(0, int(row.get("execution_count") or 0))
    success = max(0, int(row.get("success_count") or 0))
    success_rate = (success / executions) if executions else 0.5
    mean_cost = max(0.0, float(row.get("mean_cost") or 0.0))
    mean_latency = max(0.0, float(row.get("mean_latency_ms") or 0.0))
    return success_rate - min(0.15, math.log1p(mean_cost) * 0.02) - min(0.10, mean_latency / 120000.0)


class ProcedurePolicy:
    """Bounded policy over evidence-backed procedure variants."""

    MIN_SAMPLES = 3
    EXPLORATION_PERCENT = 20

    @classmethod
    def select_variant(
        cls,
        procedure: Mapping[str, Any],
        *,
        context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        payload = procedure.get("payload") if isinstance(procedure.get("payload"), Mapping) else procedure
        variants = [dict(row) for row in payload.get("variants") or [] if isinstance(row, Mapping)]
        if not variants:
            return None
        default_id = str(payload.get("default_variant_id") or variants[0].get("variant_id") or "")
        satisfied = set(str(item) for item in (context or {}).get("satisfied_preconditions") or ())
        eligible = []
        for row in variants:
            required = set(str(item) for item in row.get("preconditions") or ())
            if satisfied and not required.issubset(satisfied):
                continue
            eligible.append(row)
        if not eligible:
            eligible = variants
        mature = [row for row in eligible if int(row.get("execution_count") or 0) >= cls.MIN_SAMPLES]
        under = [row for row in eligible if int(row.get("execution_count") or 0) < cls.MIN_SAMPLES]
        context_key = json.dumps(dict(context or {}), sort_keys=True, default=str)
        fingerprint = int(hashlib.sha256(context_key.encode("utf-8")).hexdigest()[:8], 16)
        if under and len(eligible) > 1 and fingerprint % 100 < cls.EXPLORATION_PERCENT:
            selected = min(under, key=lambda row: (int(row.get("execution_count") or 0), str(row.get("variant_id") or "")))
            reason = "BOUNDED_EXPLORATION"
        elif mature:
            selected = max(mature, key=lambda row: (_variant_score(row), str(row.get("variant_id") or "")))
            reason = "MATURE_BEST"
        else:
            selected = next((row for row in eligible if row.get("variant_id") == default_id), eligible[0])
            reason = "BASELINE_UNTIL_MATURE"
        result = dict(selected)
        result.setdefault("policy_action", "DIRECT")
        result["policy_reason"] = reason
        result["policy_score"] = _variant_score(selected)
        return result

    @classmethod
    def promote_best(cls, variants: list[dict[str, Any]], default_variant_id: str) -> tuple[str, str]:
        current = next((row for row in variants if row.get("variant_id") == default_variant_id), None)
        mature = [row for row in variants if int(row.get("execution_count") or 0) >= cls.MIN_SAMPLES]
        if not mature:
            return default_variant_id, "NO_MATURE_CHALLENGER"
        best = max(mature, key=lambda row: (_variant_score(row), str(row.get("variant_id") or "")))
        if current is None:
            return str(best["variant_id"]), "PROMOTED_NO_BASELINE"
        if str(best.get("variant_id")) != default_variant_id and _variant_score(best) > _variant_score(current) + 0.02:
            return str(best["variant_id"]), "PROMOTED_BETTER_VARIANT"
        return default_variant_id, "BASELINE_RETAINED"


class ProceduralMemoryService:
    """M6 reusable procedures + evidence-gated skill variants."""

    def __init__(
        self,
        state_root,
        *,
        store: CanonicalMemoryStore | None = None,
        formation: MemoryFormationPipeline | None = None,
    ) -> None:
        self.store = store or CanonicalMemoryStore(state_root)
        self.formation = formation or MemoryFormationPipeline(state_root, store=self.store)
        self.router = MemoryRouter(state_root, store=self.store)

    @staticmethod
    def _initial_variant(
        *,
        preconditions: Iterable[str],
        capabilities: Iterable[str],
        steps: Iterable[str],
        success_count: int,
        failure_count: int,
        evidence_refs: Iterable[str],
        policy_action: str = "DIRECT",
    ) -> dict[str, Any]:
        pre = [str(item) for item in preconditions if str(item).strip()]
        caps = [str(item) for item in capabilities if str(item).strip()]
        step_rows = [str(item) for item in steps if str(item).strip()]
        executions = int(success_count) + int(failure_count)
        return {
            "variant_id": _variant_id(step_rows, caps),
            "preconditions": pre,
            "capabilities": caps,
            "steps": step_rows,
            "success_count": int(success_count),
            "failure_count": int(failure_count),
            "execution_count": executions,
            "cost_sum": 0.0,
            "latency_sum_ms": 0.0,
            "mean_cost": 0.0,
            "mean_latency_ms": 0.0,
            "last_outcome": "",
            "policy_action": str(policy_action or "DIRECT"),
            "evidence_refs": list(dict.fromkeys(str(item) for item in evidence_refs if str(item))),
        }

    def learn_procedure(
        self,
        *,
        name: str,
        description: str,
        preconditions: Iterable[str],
        capabilities: Iterable[str],
        steps: Iterable[str],
        verification: Iterable[str],
        failure_modes: Iterable[str] = (),
        recovery: Iterable[str] = (),
        expected_cost: Mapping[str, Any] | None = None,
        source_type: str,
        source_ref: str,
        evidence_refs: Iterable[str],
        supersedes: str = "",
        success_count: int = 0,
        failure_count: int = 0,
        last_outcome: str = "",
        variants: Iterable[Mapping[str, Any]] | None = None,
        default_variant_id: str = "",
        variant_policy_status: str = "",
    ) -> dict[str, Any]:
        name = str(name).strip()
        if not name:
            raise ValueError("procedure requires a name")
        evidence = [str(item).strip() for item in evidence_refs if str(item).strip()]
        if not evidence:
            raise ValueError("procedure requires verified evidence")
        pre = [str(item) for item in preconditions if str(item).strip()]
        caps = [str(item) for item in capabilities if str(item).strip()]
        step_rows = [str(item) for item in steps if str(item).strip()]
        variant_rows = [dict(row) for row in (variants or []) if isinstance(row, Mapping)]
        if not variant_rows:
            variant_rows = [self._initial_variant(
                preconditions=pre,
                capabilities=caps,
                steps=step_rows,
                success_count=success_count,
                failure_count=failure_count,
                evidence_refs=evidence,
            )]
        default_id = str(default_variant_id or variant_rows[0].get("variant_id") or "")
        payload = {
            "procedure_schema": 2,
            "name": name,
            "preconditions": pre,
            "capabilities": caps,
            "steps": step_rows,
            "verification": [str(item) for item in verification if str(item).strip()],
            "failure_modes": [str(item) for item in failure_modes if str(item).strip()],
            "recovery": [str(item) for item in recovery if str(item).strip()],
            "expected_cost": dict(expected_cost or {}),
            "success_count": int(success_count),
            "failure_count": int(failure_count),
            "execution_count": int(success_count) + int(failure_count),
            "last_outcome": str(last_outcome or ""),
            "variants": variant_rows,
            "default_variant_id": default_id,
            "variant_policy_status": str(variant_policy_status or "BASELINE"),
            "variant_min_samples": ProcedurePolicy.MIN_SAMPLES,
            "derived_from": evidence,
        }
        result = self.formation.form_many(
            [{
                "memory_type": "PROCEDURAL",
                "kind": "PROCEDURE",
                "text": f"Procedure {name}: {description}",
                "claim_key": "procedure:" + name.casefold(),
                "importance": 0.9,
                "confidence": 1.0,
                "evidence_refs": evidence,
                "topics": ["procedure", name],
                "payload": payload,
                "supersedes": supersedes,
            }],
            context=FormationContext(
                source_type=source_type,
                source_ref=source_ref,
                access_scope="PRIVATE_OWNER",
            ),
        )
        if not result["saved"]:
            raise ValueError(str(result["decisions"][0].get("reason") or "procedure_rejected"))
        return dict(result["saved"][0])

    def record_outcome(
        self,
        procedure_id: str,
        *,
        success: bool,
        evidence_ref: str,
        failure_mode: str = "",
        recovery_step: str = "",
        variant_steps: Iterable[str] = (),
        observed_cost: float = 0.0,
        latency_ms: float = 0.0,
    ) -> dict[str, Any]:
        current = self.store.get_memory(procedure_id)
        if current is None or current.get("memory_type") != "PROCEDURAL":
            raise KeyError(procedure_id)
        evidence_ref = str(evidence_ref).strip()
        if not evidence_ref:
            raise ValueError("procedure outcome requires evidence_ref")
        if float(observed_cost) < 0 or float(latency_ms) < 0:
            raise ValueError("procedure outcome cost/latency must be non-negative")
        payload = dict(current.get("payload") or {})
        failures = list(payload.get("failure_modes") or [])
        recovery = list(payload.get("recovery") or [])
        if failure_mode and failure_mode not in failures:
            failures.append(failure_mode)
        if recovery_step and recovery_step not in recovery:
            recovery.append(recovery_step)
        success_count = int(payload.get("success_count") or 0) + (1 if success else 0)
        failure_count = int(payload.get("failure_count") or 0) + (0 if success else 1)
        refs = list(dict.fromkeys([*(current.get("evidence_refs") or []), evidence_ref]))
        name = str(payload.get("name") or current.get("text") or "procedure")
        description = str(current.get("text") or "")
        if description.startswith(f"Procedure {name}: "):
            description = description[len(f"Procedure {name}: "):]

        variants = [dict(row) for row in payload.get("variants") or [] if isinstance(row, Mapping)]
        if not variants:
            variants = [self._initial_variant(
                preconditions=payload.get("preconditions") or [],
                capabilities=payload.get("capabilities") or [],
                steps=payload.get("steps") or [],
                success_count=int(payload.get("success_count") or 0),
                failure_count=int(payload.get("failure_count") or 0),
                evidence_refs=current.get("evidence_refs") or [],
            )]
        chosen_steps = [str(item) for item in variant_steps if str(item).strip()] or list(payload.get("steps") or [])
        variant_id = _variant_id(chosen_steps, payload.get("capabilities") or [])
        variant = next((row for row in variants if row.get("variant_id") == variant_id), None)
        if variant is None:
            variant = self._initial_variant(
                preconditions=payload.get("preconditions") or [],
                capabilities=payload.get("capabilities") or [],
                steps=chosen_steps,
                success_count=0,
                failure_count=0,
                evidence_refs=[],
            )
            variants.append(variant)
        variant["success_count"] = int(variant.get("success_count") or 0) + (1 if success else 0)
        variant["failure_count"] = int(variant.get("failure_count") or 0) + (0 if success else 1)
        variant["execution_count"] = int(variant["success_count"]) + int(variant["failure_count"])
        variant["cost_sum"] = float(variant.get("cost_sum") or 0.0) + float(observed_cost)
        variant["latency_sum_ms"] = float(variant.get("latency_sum_ms") or 0.0) + float(latency_ms)
        variant["mean_cost"] = float(variant["cost_sum"]) / max(1, int(variant["execution_count"]))
        variant["mean_latency_ms"] = float(variant["latency_sum_ms"]) / max(1, int(variant["execution_count"]))
        variant["last_outcome"] = "SUCCESS" if success else "FAILURE"
        variant["evidence_refs"] = list(dict.fromkeys([
            *(variant.get("evidence_refs") or []), evidence_ref,
        ]))

        # A verified failure may create one bounded challenger for the *next* execution.
        # We never credit the failed outcome to the challenger because it was not executed.
        if not success and recovery_step and "retry once" in recovery_step.casefold():
            challenger_steps = list(chosen_steps)
            if recovery_step not in challenger_steps:
                challenger_steps.append(recovery_step)
            challenger_id = _variant_id(challenger_steps, payload.get("capabilities") or [])
            if not any(row.get("variant_id") == challenger_id for row in variants):
                variants.append(self._initial_variant(
                    preconditions=payload.get("preconditions") or [],
                    capabilities=payload.get("capabilities") or [],
                    steps=challenger_steps,
                    success_count=0,
                    failure_count=0,
                    evidence_refs=[evidence_ref],
                    policy_action="RETRY_ONCE_SAFE",
                ))

        old_default = str(payload.get("default_variant_id") or variants[0].get("variant_id") or "")
        new_default, policy_status = ProcedurePolicy.promote_best(variants, old_default)
        revised = self.learn_procedure(
            name=name,
            description=description,
            preconditions=payload.get("preconditions") or [],
            capabilities=payload.get("capabilities") or [],
            steps=payload.get("steps") or [],
            verification=payload.get("verification") or [],
            failure_modes=failures,
            recovery=recovery,
            expected_cost=payload.get("expected_cost") or {},
            source_type="VERIFIED_EXECUTION_OUTCOME",
            source_ref=evidence_ref,
            evidence_refs=refs,
            supersedes=procedure_id,
            success_count=success_count,
            failure_count=failure_count,
            last_outcome="SUCCESS" if success else "FAILURE",
            variants=variants,
            default_variant_id=new_default,
            variant_policy_status=policy_status,
        )
        stored = self.store.get_memory(revised["memory_id"])
        if stored is None:
            raise RuntimeError("procedure revision missing")
        return stored

    def select_variant(
        self,
        procedure_id: str,
        *,
        context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        current = self.store.get_memory(procedure_id)
        if current is None or current.get("memory_type") != "PROCEDURAL":
            raise KeyError(procedure_id)
        payload = dict(current.get("payload") or {})
        if not payload.get("variants"):
            baseline = self._initial_variant(
                preconditions=payload.get("preconditions") or [],
                capabilities=payload.get("capabilities") or [],
                steps=payload.get("steps") or [],
                success_count=int(payload.get("success_count") or 0),
                failure_count=int(payload.get("failure_count") or 0),
                evidence_refs=current.get("evidence_refs") or [],
            )
            payload["variants"] = [baseline]
            payload["default_variant_id"] = baseline["variant_id"]
        return ProcedurePolicy.select_variant(payload, context=context)

    def retrieve(self, query: str, *, limit: int = 5) -> list[dict[str, Any]]:
        return self.router.recall(query, limit=limit, memory_types=["PROCEDURAL"])


__all__ = ["ProcedurePolicy", "ProceduralMemoryService"]
