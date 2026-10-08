"""Capability contract observation is a side-effect boundary, not model context retrieval.

It receives a completed cognition snapshot and explicit runtime ports. The
observer alone may record contract violations and request diagnosis.
"""
from __future__ import annotations

from typing import Any, Mapping


_INTEROCEPTION_FIELDS = (
    "schema_version", "status", "updated_at", "distress", "confidence_in_self",
    "active_episode_ids", "suspected_faults", "symptoms", "latest_observation_id",
)


def _bounded_interoception(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value[key] for key in _INTEROCEPTION_FIELDS if value.get(key) is not None}


class CognitionContractObserver:
    def __init__(self, *, state_root, monitor_factory, fault_factory,
                 maintenance_factory=None):
        self.state_root = state_root
        self.monitor_factory = monitor_factory
        self.fault_factory = fault_factory
        self.maintenance_factory = maintenance_factory

    def _diagnose(self, snapshot, sensed, report, *, now, domain_runtime, app_runtime):
        diagnostic_failure = sensed.get("diagnostic_failure")
        if not (bool(sensed.get("should_diagnose")) and isinstance(diagnostic_failure, dict)):
            return
        top_contract = next(
            (row for row in list(report.get("violations") or [])
             if isinstance(row, Mapping)), {},
        )
        subsystem = str(top_contract.get("subsystem") or "context")
        failure_site = (
            "behavioral_state.py:capability_contract"
            if subsystem == "neural"
            else "cognition_context.py:capability_contract"
        )
        if self.maintenance_factory is None:
            from .self_maintenance import SelfMaintenanceService
            factory = SelfMaintenanceService
        else:
            factory = self.maintenance_factory
        maintenance = factory(
            state_root=self.state_root,
            evolution_runtime=domain_runtime,
            capability_runtime=app_runtime,
        )
        snapshot["capability_contract_diagnosis"] = maintenance.ingest_failure(
            {
                **diagnostic_failure,
                "task": "CONTENT_COGNITION",
                "error_type": "CAPABILITY_CONTRACT_VIOLATION",
                "failure_site": failure_site,
                "message": (
                    "capability contract violation: "
                    + str(top_contract.get("kind") or "unknown")
                    + "; expected=" + str(top_contract.get("expected") or "")[:300]
                    + "; observed=" + str(top_contract.get("observed") or "")[:400]
                ),
            },
            now=now,
        )

    def observe(self, snapshot, *, full_phenotype_prior, full_behavioral_state,
                context_receipt, now, domain_runtime, app_runtime):
        if self.state_root is None:
            return snapshot
        contract_snapshot = {
            **snapshot,
            "phenotype_prior": full_phenotype_prior,
            "behavioral_state": full_behavioral_state,
            "context_receipt": context_receipt,
        }
        report = self.monitor_factory(self.state_root).observe_context(
            contract_snapshot, now=now,
        )
        snapshot["capability_contracts"] = report
        if not (report.get("violations") and not report.get("duplicate")):
            return snapshot
        sensed = self.fault_factory(self.state_root).observe(
            "CONTENT_COGNITION",
            {
                "task": "CONTENT_COGNITION",
                "status": "OK",
                "cycle": report.get("observation_id"),
                "capability_contracts": report,
            },
            now=now,
        )
        self._diagnose(snapshot, sensed, report, now=now,
                       domain_runtime=domain_runtime, app_runtime=app_runtime)
        snapshot["interoception"] = _bounded_interoception(dict(sensed.get("projection") or {}))
        return snapshot
