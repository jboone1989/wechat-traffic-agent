"""Real, bounded resource observations and cycle economics for Content Agent."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from statistics import mean
from typing import Any, Mapping
import urllib.error
import urllib.request


def _iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("resource observation time requires timezone")
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _positive_env(name: str, default: float) -> float:
    raw = str(os.environ.get(name) or default)
    value = float(raw)
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _api_root() -> str:
    raw = str(
        os.environ.get("DEEPSEEK_BASE_URL")
        or os.environ.get("VIRTUALBRAIN_LLM_BASE_URL")
        or "https://api.deepseek.com"
    ).rstrip("/")
    for suffix in ("/anthropic", "/v1"):
        if raw.endswith(suffix):
            raw = raw[: -len(suffix)]
    return raw.rstrip("/")


def _api_key() -> str | None:
    return str(os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("VIRTUALBRAIN_LLM_API_KEY") or "").strip() or None


def _resource_pressure(*, available_funds: float, period_budget: float, runway_days: float | None) -> str:
    ratio = (available_funds / period_budget) if period_budget > 0 else None
    if available_funds <= 0 or (ratio is not None and ratio < 0.10) or (
        runway_days is not None and runway_days < 3
    ):
        return "CRITICAL"
    if (ratio is not None and ratio < 0.25) or (
        runway_days is not None and runway_days < 7
    ):
        return "CONSERVE"
    if (ratio is not None and ratio < 0.50) or (
        runway_days is not None and runway_days < 14
    ):
        return "WATCH"
    return "NORMAL"


@dataclass(frozen=True)
class BalanceObservation:
    observed_at: str
    is_available: bool
    currency: str
    total_balance: float
    granted_balance: float
    topped_up_balance: float
    evidence_ref: str

    def payload(self) -> dict[str, Any]:
        return {
            "observed_at": self.observed_at,
            "is_available": self.is_available,
            "currency": self.currency,
            "total_balance": self.total_balance,
            "granted_balance": self.granted_balance,
            "topped_up_balance": self.topped_up_balance,
            "evidence_ref": self.evidence_ref,
        }


class DeepSeekBalanceObserver:
    def __init__(self, *, state_root: Path, timeout_seconds: float = 10.0) -> None:
        self.root = Path(state_root) / "application" / "resource_observations"
        self.timeout_seconds = timeout_seconds

    def observe(self, now: datetime) -> BalanceObservation:
        key = _api_key()
        if key is None:
            raise RuntimeError("DeepSeek balance observation requires configured API credentials")
        request = urllib.request.Request(
            _api_root() + "/user/balance",
            headers={"Authorization": "Bearer " + key, "Accept": "application/json"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"DeepSeek balance HTTP error {exc.code}") from exc
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("DeepSeek balance observation failed") from exc
        if type(raw) is not dict or type(raw.get("balance_infos")) is not list:
            raise ValueError("DeepSeek balance response is invalid")
        rows = [item for item in raw["balance_infos"] if isinstance(item, Mapping)]
        selected = next((item for item in rows if str(item.get("currency")) == "CNY"), rows[0] if rows else None)
        if selected is None:
            raise ValueError("DeepSeek balance response contains no balances")
        observed_at = _iso(now)
        canonical = {
            "observed_at": observed_at,
            "is_available": bool(raw.get("is_available", False)),
            "currency": str(selected.get("currency") or "").upper(),
            "total_balance": float(selected["total_balance"]),
            "granted_balance": float(selected["granted_balance"]),
            "topped_up_balance": float(selected["topped_up_balance"]),
        }
        digest = sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        observation = BalanceObservation(**canonical, evidence_ref="deepseek-balance:sha256:" + digest)
        self._persist(observation)
        return observation

    def _persist(self, observation: BalanceObservation) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = observation.payload()
        name = observation.observed_at.replace(":", "").replace("+00:00", "Z") + "-" + observation.evidence_ref[-12:] + ".json"
        path = self.root / name
        if not path.exists():
            path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        tmp = self.root / "latest.json.tmp"
        tmp.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        os.replace(tmp, self.root / "latest.json")


class ContentResourceEconomy:
    """Connect provider balance evidence to VirtualBrain resource cognition."""

    def __init__(self, *, state_root: Path, mind: Any) -> None:
        self.state_root = Path(state_root)
        self.mind = mind
        self.observer = DeepSeekBalanceObserver(state_root=self.state_root)
        self.monthly_budget = _positive_env("CONTENT_MONTHLY_BUDGET_CNY", 100.0)
        self.bootstrap_cycle_estimate = _positive_env("CONTENT_RESOURCE_BOOTSTRAP_CYCLE_ESTIMATE_CNY", 1.0)

    def observe(self, now: datetime) -> dict[str, Any]:
        from virtualbrain.research.resource_awareness import ResourceState

        balance = self.observer.observe(now)
        awareness = self.mind.session.resource_awareness
        burn = self._daily_burn(awareness.states, balance)
        runway = balance.total_balance / burn if burn is not None and burn > 0 else None
        from .llm_resources import pool_metrics
        llm_metrics = pool_metrics(self.state_root)
        state = ResourceState(
            observed_tick=int(self.mind.session.tick),
            observed_at=balance.observed_at,
            currency=balance.currency,
            available_funds=balance.total_balance,
            period_budget=self.monthly_budget,
            carryover_funds=None,
            spent_today=None,
            spent_period=None,
            rolling_7d_spend=None,
            rolling_30d_spend=None,
            estimated_daily_burn=burn,
            runway_days=runway,
            quotas={
                "deepseek_api_available": 1.0 if balance.is_available else 0.0,
                "deepseek_granted_balance": balance.granted_balance,
                "deepseek_topped_up_balance": balance.topped_up_balance,
                **llm_metrics,
            },
            source_refs=(balance.evidence_ref,),
        )
        self.mind.session.resource_awareness.observe(state)
        self.mind.persist_resource_awareness()
        routing_policy = self.sync_llm_routing_policy(now=now)
        return {
            "status": "OBSERVED",
            "balance": balance.payload(),
            "resource_state": self._state_payload(state),
            "llm_routing_policy": routing_policy,
        }

    def sync_llm_routing_policy(self, *, now: datetime) -> dict[str, Any]:
        """Translate VirtualBrain resource awareness into a secret-free Runtime routing policy."""
        current = self.mind.session.resource_awareness.current
        if current is None:
            pressure = "NORMAL"
            funds = None
            runway = None
            evidence_refs = (f"vb-mind-tick:{int(self.mind.session.tick)}",)
        else:
            pressure = _resource_pressure(
                available_funds=float(current.available_funds),
                period_budget=float(current.period_budget),
                runway_days=(
                    None if current.runway_days is None else float(current.runway_days)
                ),
            )
            funds = float(current.available_funds)
            runway = (
                None if current.runway_days is None else float(current.runway_days)
            )
            evidence_refs = tuple(dict.fromkeys(
                [*current.source_refs, f"vb-mind-tick:{int(self.mind.session.tick)}"]
            ))
        pool_path = (
            self.state_root / "application" / "llm_resource_pool" / "resources.json"
        )
        windfall_resources: list[dict[str, Any]] = []
        try:
            manifest = json.loads(pool_path.read_text(encoding="utf-8"))
            rows = manifest.get("resources") if isinstance(manifest, dict) else []
            for row in rows if isinstance(rows, list) else []:
                if not isinstance(row, dict):
                    continue
                if row.get("pricing_class") != "FREE" or not row.get("windfall_eligible"):
                    continue
                expires_raw = str(row.get("expires_at") or "")
                if expires_raw:
                    expires = datetime.fromisoformat(expires_raw.replace("Z", "+00:00"))
                    if expires.tzinfo is None or expires.utcoffset() is None:
                        continue
                    if expires.astimezone(timezone.utc) <= now.astimezone(timezone.utc):
                        continue
                windfall_resources.append(row)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            windfall_resources = []
        windfall_active = bool(windfall_resources)
        windfall_bonus = {
            "NORMAL": 0.42,
            "WATCH": 0.38,
            "CONSERVE": 0.30,
            "CRITICAL": 0.18,
        }.get(pressure, 0.30)
        high_value_task_classes = [
            "self_upgrade",
            "code_audit",
            "deep_research",
            "long_reflection",
            "architecture_review",
        ]
        reserve_default = max(5.0, self.monthly_budget * 0.20)
        reserve_floor = float(
            os.environ.get("CONTENT_LLM_RESERVE_FLOOR_CNY") or reserve_default
        )
        max_failovers = 2 if pressure in {"CRITICAL", "CONSERVE"} else 4
        payload = {
            "schema_version": "vb-llm-routing-policy.v1",
            "observed_at": _iso(now),
            "routing_mode": "FREE_FIRST_RESERVE_PAID",
            "pressure": pressure,
            "prefer_free": True,
            "minimum_quality": float(
                os.environ.get("CONTENT_LLM_MINIMUM_QUALITY") or 0.70
            ),
            "max_failovers": max_failovers,
            "preferred_lifecycles": [
                "TEMPORARY_FREE", "RECURRING_FREE", "STABLE_PAID"
            ],
            "windfall_active": windfall_active,
            "windfall_bonus": windfall_bonus,
            "high_value_task_classes": high_value_task_classes,
            "windfall_resource_ids": [
                str(row.get("resource_id") or "") for row in windfall_resources
                if row.get("resource_id")
            ],
            "windfall_mode": (
                "OPPORTUNITY_FEAST" if windfall_active else "NONE"
            ),
            "paid_available_funds": funds,
            "paid_reserve_floor": reserve_floor,
            "runway_days": runway,
            "mind_tick": int(self.mind.session.tick),
            "evidence_refs": list(evidence_refs),
        }
        folder = self.state_root / "application" / "llm_resource_pool"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "policy.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, path)
        return {
            "status": "SYNCED",
            "routing_mode": payload["routing_mode"],
            "pressure": pressure,
            "prefer_free": True,
            "max_failovers": max_failovers,
            "paid_reserve_floor": reserve_floor,
            "windfall_active": windfall_active,
            "windfall_bonus": windfall_bonus,
            "windfall_resource_count": len(windfall_resources),
            "windfall_mode": payload["windfall_mode"],
            "mind_tick": payload["mind_tick"],
            "evidence_refs": list(evidence_refs),
        }


    def settle_audience_outcomes(self, *, now: datetime) -> dict[str, Any]:
        """Settle resource external value only from mature world-derived audience reward."""
        from virtualbrain.research.resource_awareness import OutcomeSettlement

        awareness = self.mind.session.resource_awareness
        latest = getattr(awareness, "latest_outcome_settlement", None)
        record = getattr(awareness, "record_outcome_settlement", None)
        if not callable(latest) or not callable(record):
            return {"status": "UNSUPPORTED_VIRTUALBRAIN", "settled": []}
        settled: list[dict[str, Any]] = []
        for outcome in awareness.outcomes:
            cycle_ref = next(
                (ref for ref in outcome.evidence_refs if str(ref).startswith("cycle:")),
                None,
            )
            if cycle_ref is None:
                continue
            cycle = str(cycle_ref).split(":", 1)[1]
            publication_path = (
                self.state_root / "content" / "continuous" / "cycles" / cycle / "publication.json"
            )
            if not publication_path.exists():
                continue
            publication = json.loads(publication_path.read_text(encoding="utf-8"))
            publication_id = str(publication.get("revision") or "")
            if not publication_id:
                continue
            audience = self.mind.audience_outcome_for_publication(publication_id)
            if not isinstance(audience, Mapping):
                continue
            reward = audience.get("relative_reward")
            if isinstance(reward, bool) or not isinstance(reward, (int, float)):
                continue
            current = latest(outcome.outcome_id)
            if current is not None and current.external_value is not None:
                continue
            revision = 1 if current is None else int(current.revision) + 1
            refs = tuple(dict.fromkeys([
                *outcome.evidence_refs,
                *tuple(audience.get("evidence_refs") or ()),
                f"audience-outcome:{audience.get('outcome_id')}",
            ]))
            settlement_id = "content-cycle-settlement:" + sha256(
                f"{outcome.outcome_id}|{revision}|{publication_id}|{float(reward):.12f}".encode()
            ).hexdigest()[:24]
            row = record(OutcomeSettlement(
                settlement_id=settlement_id,
                outcome_id=outcome.outcome_id,
                revision=revision,
                external_value=float(reward),
                learning_value=(
                    None if current is None else current.learning_value
                ),
                reproduction_value=(
                    None if current is None else current.reproduction_value
                ),
                observed_tick=int(self.mind.session.tick),
                evidence_refs=refs,
            ))
            settled.append({
                "settlement_id": row.settlement_id,
                "outcome_id": row.outcome_id,
                "revision": row.revision,
                "external_value": row.external_value,
                "publication_id": publication_id,
                "evidence_refs": list(row.evidence_refs),
            })
        if settled:
            self.mind.persist_resource_awareness()
        return {
            "status": "SETTLED" if settled else "NO_NEW_SETTLEMENTS",
            "settled": settled,
        }


    @staticmethod
    def _daily_burn(history: tuple[Any, ...], current: BalanceObservation) -> float | None:
        """Estimate burn from a real elapsed window even when observations arrive every few minutes."""
        after = datetime.fromisoformat(current.observed_at.replace("Z", "+00:00"))
        fallback = history[-1].estimated_daily_burn if history else None
        candidate = None
        for state in reversed(history):
            if state.observed_at is None or state.currency != current.currency:
                continue
            before = datetime.fromisoformat(state.observed_at.replace("Z", "+00:00"))
            seconds = (after - before).total_seconds()
            if seconds >= 900:
                candidate = (state, seconds)
                break
        if candidate is None:
            return fallback
        prior, seconds = candidate
        delta = prior.available_funds - current.total_balance
        if delta < 0:
            return None  # top-up/grant/change: do not mislabel it as negative spend
        return delta * 86400.0 / seconds

    @staticmethod
    def _state_payload(state: Any) -> dict[str, Any]:
        return {
            "observed_tick": state.observed_tick,
            "observed_at": state.observed_at,
            "currency": state.currency,
            "available_funds": state.available_funds,
            "period_budget": state.period_budget,
            "estimated_daily_burn": state.estimated_daily_burn,
            "runway_days": state.runway_days,
            "source_refs": list(state.source_refs),
        }

    def _record_balance_increase_event(
        self, *, now: datetime, cycle: str, before_balance: float, current: Any
    ) -> dict[str, Any]:
        payload = {
            "schema_version": "balance-increase-event.v1",
            "cycle": cycle,
            "observed_at": _iso(now),
            "currency": current.currency,
            "before_balance": float(before_balance),
            "after_balance": float(current.available_funds),
            "increase": float(current.available_funds) - float(before_balance),
            "source": "UNKNOWN",
            "classification": "UNRESOLVED",
            "evidence_refs": list(current.source_refs),
        }
        event_id = "balance-increase:" + sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:24]
        payload["event_id"] = event_id
        root = self.state_root / "application" / "resource_observations" / "balance_increase_events"
        root.mkdir(parents=True, exist_ok=True)
        target = root / f"{event_id}.json"
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        if target.exists():
            if target.read_text(encoding="utf-8") != text:
                raise ValueError("balance increase event conflicts with existing evidence")
        else:
            target.write_text(text, encoding="utf-8")
        return payload

    @staticmethod
    def _settled_dimension(awareness: Any, outcomes: list[Any], name: str) -> float:
        values: list[float] = []
        latest = getattr(awareness, "latest_outcome_settlement", None)
        if callable(latest):
            for outcome in outcomes:
                settlement = latest(outcome.outcome_id)
                value = getattr(settlement, name, None) if settlement is not None else None
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    values.append(float(value))
        return mean(values) if values else 0.5

    @property
    def hibernation_path(self) -> Path:
        return self.state_root / "application" / "resource_observations" / "hibernation.json"

    def _routing_policy(self) -> dict[str, Any]:
        path = self.state_root / "application" / "llm_resource_pool" / "policy.json"
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return {}
        return row if isinstance(row, dict) else {}

    def hibernation_state(self) -> dict[str, Any] | None:
        try:
            row = json.loads(self.hibernation_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        return row if isinstance(row, dict) and row.get("status") == "HIBERNATE" else None


    @property
    def force_wake_override_path(self) -> Path:
        return self.hibernation_path.parent / "hibernation_override.json"

    def _force_wake_override(self, now: datetime) -> dict[str, Any] | None:
        try:
            row = json.loads(self.force_wake_override_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(row, dict) or row.get("status") != "ACTIVE":
            return None
        try:
            expires = datetime.fromisoformat(str(row.get("expires_at") or "").replace("Z", "+00:00"))
        except ValueError:
            self.force_wake_override_path.unlink(missing_ok=True)
            return None
        if expires.tzinfo is None or expires.astimezone(timezone.utc) <= now.astimezone(timezone.utc):
            self.force_wake_override_path.unlink(missing_ok=True)
            return None
        return row

    def _consume_force_wake_override(self, *, now: datetime, decision: str) -> None:
        override = self._force_wake_override(now)
        if override is None:
            return
        payload = {
            **override,
            "status": "CONSUMED",
            "consumed_at": _iso(now),
            "decision": str(decision),
        }
        root = self.hibernation_path.parent / "force_wake_consumption_receipts"
        root.mkdir(parents=True, exist_ok=True)
        receipt_id = sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:24]
        target = root / f"{receipt_id}.json"
        if not target.exists():
            target.write_text(
                json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
        self.force_wake_override_path.unlink(missing_ok=True)

    def _enter_hibernation(self, *, now: datetime, pressure: str, evidence_refs: tuple[str, ...]) -> dict[str, Any]:
        payload = {
            "schema_version": "content-hibernation.v1",
            "status": "HIBERNATE",
            "entered_at": _iso(now),
            "pressure": pressure,
            "reason": "RESOURCE_CRITICAL",
            "allowed_tasks": ["RESOURCE_REVIEW", "CHECK_OUTCOMES"],
            "blocked_tasks": ["OBSERVE_WORLD", "CONTENT_COGNITION", "REFLECT", "CHECK_X", "VERIFY_PREDICTION"],
            "evidence_refs": list(evidence_refs),
        }
        self.hibernation_path.parent.mkdir(parents=True, exist_ok=True)
        if self.hibernation_path.exists():
            existing = self.hibernation_state()
            if existing is not None:
                return existing
        self.hibernation_path.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        return payload

    def _exit_hibernation(self, *, now: datetime, reason: str, evidence_refs: tuple[str, ...]) -> dict[str, Any] | None:
        current = self.hibernation_state()
        if current is None:
            return None
        receipt = {
            "schema_version": "content-hibernation-recovery.v1",
            "status": "RECOVERED",
            "recovered_at": _iso(now),
            "reason": str(reason),
            "prior": current,
            "evidence_refs": list(evidence_refs),
        }
        receipt_id = sha256(
            json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:24]
        root = self.hibernation_path.parent / "hibernation_recovery_receipts"
        root.mkdir(parents=True, exist_ok=True)
        target = root / f"{receipt_id}.json"
        if not target.exists():
            target.write_text(
                json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
        self.hibernation_path.unlink(missing_ok=True)
        return receipt

    def force_wake(self, *, now: datetime, operator_ref: str) -> dict[str, Any]:
        """Authorize exactly one content cycle while retaining an auditable receipt."""
        operator_ref = str(operator_ref).strip()
        if not operator_ref:
            raise ValueError("operator force wake requires operator_ref")
        current = self.hibernation_state()
        if current is None:
            return {"status": "NOT_HIBERNATING"}
        authorized_at = _iso(now)
        receipt_payload = {
            "schema_version": "resource-force-wake.v1",
            "status": "AUTHORIZED",
            "authorized_at": authorized_at,
            "operator_ref": operator_ref,
            "scope": "ONE_CONTENT_CYCLE",
            "prior_hibernation": current,
        }
        receipt_id = "force-wake:" + sha256(
            json.dumps(receipt_payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:24]
        receipt_payload["receipt_id"] = receipt_id
        receipt_root = self.hibernation_path.parent / "operator_force_wake_receipts"
        receipt_root.mkdir(parents=True, exist_ok=True)
        receipt_path = receipt_root / f"{receipt_id.split(':', 1)[1]}.json"
        if not receipt_path.exists():
            receipt_path.write_text(
                json.dumps(receipt_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
        override = {
            "schema_version": "operator-force-wake-override.v1",
            "status": "ACTIVE",
            "authorized_at": authorized_at,
            "expires_at": _iso(now + timedelta(minutes=30)),
            "receipt_ref": receipt_id,
            "scope": "ONE_CONTENT_CYCLE",
        }
        tmp = self.force_wake_override_path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(override, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, self.force_wake_override_path)
        recovery = self._exit_hibernation(
            now=now,
            reason="OPERATOR_FORCE_WAKE",
            evidence_refs=tuple(dict.fromkeys([*current.get("evidence_refs", ()), operator_ref, receipt_id])),
        )
        return {
            "status": "FORCED_AWAKE",
            "receipt": receipt_payload,
            "override": override,
            "recovery": recovery,
        }

    def begin_cycle(self, *, now: datetime, cycle: str) -> dict[str, Any]:
        from virtualbrain.research.resource_awareness import ActionProposal, DecisionEpisode

        current = self.mind.session.resource_awareness.current
        if current is None:
            self.observe(now)
            current = self.mind.session.resource_awareness.current
        assert current is not None
        awareness = self.mind.session.resource_awareness
        recent_outcomes = list(awareness.outcomes[-12:])
        # A provider balance that did not move is not proof the work was free; billing may lag.
        # Known no-op replays are also not generation-cost evidence. Learn the cost prior only
        # from positive, actually observed balance decreases on non-noop episodes.
        cost_evidence = [
            item.actual_cost for item in recent_outcomes
            if item.actual_cost > 0.0 and "ALREADY_PROCESSED" not in item.result_summary and "BUSY" not in item.result_summary
        ]
        estimated_cost = mean(cost_evidence) if cost_evidence else self.bootstrap_cycle_estimate
        prior_values = [
            item for item in recent_outcomes
            if "ALREADY_PROCESSED" not in item.result_summary and "BUSY" not in item.result_summary
        ]
        external = self._settled_dimension(awareness, prior_values, "external_value")
        learning = self._settled_dimension(awareness, prior_values, "learning_value")
        reproduction = self._settled_dimension(awareness, prior_values, "reproduction_value")
        digest = sha256(cycle.encode()).hexdigest()[:20]
        proposal_id = "content-cycle:" + digest
        episode_id = "content-cycle-decision:" + digest
        evidence = tuple(current.source_refs) + ("cycle:" + cycle,)
        if not any(item.proposal_id == proposal_id for item in awareness.proposals):
            awareness.register_proposal(ActionProposal(
                proposal_id=proposal_id,
                action="run autonomous content cycle",
                capability_id="content.continuous",
                estimated_cost=estimated_cost,
                expected_external_value=external,
                expected_learning_value=learning,
                expected_reproduction_value=reproduction,
                created_tick=int(self.mind.session.tick),
                evidence_refs=evidence,
            ))
        pressure = _resource_pressure(
            available_funds=float(current.available_funds),
            period_budget=float(current.period_budget),
            runway_days=None if current.runway_days is None else float(current.runway_days),
        )
        routing = self._routing_policy()
        windfall_active = bool(routing.get("windfall_active"))
        prefer_free = bool(routing.get("prefer_free", True))
        force_override = self._force_wake_override(now)
        if pressure == "CRITICAL" and force_override is None:
            hibernation = self._enter_hibernation(
                now=now, pressure=pressure, evidence_refs=evidence
            )
        else:
            self._exit_hibernation(
                now=now, reason="RESOURCE_PRESSURE_RECOVERED", evidence_refs=evidence
            )
            hibernation = None
        existing = next((item for item in awareness.episodes if item.proposal_id == proposal_id), None)
        if existing is None:
            paid_available = current.quotas.get("deepseek_api_available", 1.0) >= 1.0
            if pressure == "CRITICAL" and force_override is None:
                decision = "DEFER"
            elif pressure == "CRITICAL":
                decision = (
                    "EXECUTE"
                    if (prefer_free and windfall_active)
                    or (paid_available and current.available_funds >= estimated_cost)
                    else "ASK_HUMAN"
                )
            elif pressure == "CONSERVE":
                decision = "EXECUTE" if prefer_free and windfall_active else "DEFER"
            else:
                decision = (
                    "EXECUTE"
                    if paid_available and current.available_funds >= estimated_cost
                    else "ASK_HUMAN"
                )
            existing = awareness.record_decision(DecisionEpisode(
                episode_id=episode_id,
                proposal_id=proposal_id,
                decision=decision,
                options_considered=("EXECUTE", "DEFER", "ASK_HUMAN"),
                predicted_total_value=mean((external, learning, reproduction)),
                estimated_cost=estimated_cost,
                created_tick=int(self.mind.session.tick),
                evidence_refs=evidence,
            ))
            self.mind.persist_resource_awareness()
            if force_override is not None:
                self._consume_force_wake_override(now=now, decision=decision)
        return {
            "status": "DECIDED",
            "proposal_id": proposal_id,
            "episode_id": existing.episode_id,
            "decision": existing.decision,
            "estimated_cost": existing.estimated_cost,
            "available_funds": current.available_funds,
            "currency": current.currency,
            "before_balance": current.available_funds,
            "pressure": pressure,
            "prefer_free": prefer_free,
            "windfall_active": windfall_active,
            "hibernation": hibernation,
            "force_wake_override": None if force_override is None else force_override.get("receipt_ref"),
            "defer_interval_multiplier": 2 if pressure == "CONSERVE" else 1,
            "evidence_refs": list(existing.evidence_refs),
        }

    def end_cycle(self, *, now: datetime, cycle: str, episode_id: str, before_balance: float, result: Mapping[str, Any]) -> dict[str, Any]:
        from virtualbrain.research.resource_awareness import Outcome, Reflection

        observed = self.observe(now)
        current = self.mind.session.resource_awareness.current
        assert current is not None
        if current.available_funds > before_balance:
            event = self._record_balance_increase_event(
                now=now, cycle=cycle, before_balance=before_balance, current=current
            )
            return {
                "status": "AMBIGUOUS_BALANCE_INCREASE",
                "balance_increase_event": event,
                "resource_state": observed["resource_state"],
            }
        awareness = self.mind.session.resource_awareness
        if any(item.episode_id == episode_id for item in awareness.outcomes):
            return {"status": "ALREADY_RECORDED", "episode_id": episode_id}
        episode = next(item for item in awareness.episodes if item.episode_id == episode_id)
        actual_cost = before_balance - current.available_funds
        status = str(result.get("status") or "UNKNOWN")
        published = status == "PUBLISHED"
        succeeded = status not in {"ERROR", "RESOURCE_DEFERRED"}
        outcome_id = "content-cycle-outcome:" + sha256(cycle.encode()).hexdigest()[:20]
        evidence = tuple(dict.fromkeys((*episode.evidence_refs, *current.source_refs, "cycle-result:" + status)))
        outcome = awareness.record_outcome(Outcome(
            outcome_id=outcome_id,
            episode_id=episode_id,
            result_summary=(
                f"content cycle ended {status}; observed account balance delta "
                f"{actual_cost:.6f} {current.currency}; value settlement PENDING_AUDIENCE"
            ),
            actual_cost=actual_cost,
            external_value=None,
            learning_value=None,
            reproduction_value=None,
            reputation_delta=0.0,
            observed_tick=int(self.mind.session.tick),
            evidence_refs=evidence,
        ))
        estimate = max(episode.estimated_cost, 1e-9)
        prediction_error = max(-1.0, min(1.0, (actual_cost - episode.estimated_cost) / estimate))
        waste = min(1.0, actual_cost / estimate) if not succeeded and actual_cost > 0 else 0.0
        lesson = (
            "Publication completed, but external, learning, and reproduction value remain unknown until world evidence matures."
            if published else
            "The cycle consumed resources without publication; keep value unknown and compare with later evidence before changing policy."
            if actual_cost > 0 else
            "No account balance decrease was observed during this cycle; retain uncertainty about delayed billing and value."
        )
        reflection = awareness.record_reflection(Reflection(
            reflection_id="content-cycle-reflection:" + sha256(cycle.encode()).hexdigest()[:20],
            outcome_id=outcome.outcome_id,
            prediction_error=prediction_error,
            waste_score=waste,
            over_conservatism_score=0.0,
            lesson=lesson,
            created_tick=int(self.mind.session.tick),
            evidence_refs=evidence,
        ))
        self.mind.persist_resource_awareness()
        return {
            "status": "RECORDED",
            "episode_id": episode_id,
            "outcome_id": outcome.outcome_id,
            "reflection_id": reflection.reflection_id,
            "actual_cost": actual_cost,
            "currency": current.currency,
            "prediction_error": prediction_error,
            "waste_score": waste,
            "settlement_status": "PENDING_AUDIENCE",
            "resource_state": observed["resource_state"],
        }


__all__ = ["BalanceObservation", "DeepSeekBalanceObserver", "ContentResourceEconomy"]
