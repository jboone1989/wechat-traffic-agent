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
        prior = self.mind.session.resource_awareness.current
        burn = self._daily_burn(prior, balance)
        runway = balance.total_balance / burn if burn is not None and burn > 0 else None
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
            },
            source_refs=(balance.evidence_ref,),
        )
        self.mind.session.resource_awareness.observe(state)
        self.mind.persist_resource_awareness()
        return {"status": "OBSERVED", "balance": balance.payload(), "resource_state": self._state_payload(state)}

    @staticmethod
    def _daily_burn(prior: Any, current: BalanceObservation) -> float | None:
        if prior is None or prior.observed_at is None or prior.currency != current.currency:
            return None
        before = datetime.fromisoformat(prior.observed_at.replace("Z", "+00:00"))
        after = datetime.fromisoformat(current.observed_at.replace("Z", "+00:00"))
        seconds = (after - before).total_seconds()
        if seconds <= 0:
            return prior.estimated_daily_burn
        delta = prior.available_funds - current.total_balance
        if delta < 0:
            return None  # top-up/grant/change: do not mislabel it as negative spend
        if seconds < 900:
            return prior.estimated_daily_burn
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

    def begin_cycle(self, *, now: datetime, cycle: str) -> dict[str, Any]:
        from virtualbrain.research.resource_awareness import ActionProposal, DecisionEpisode

        current = self.mind.session.resource_awareness.current
        if current is None:
            self.observe(now)
            current = self.mind.session.resource_awareness.current
        assert current is not None
        awareness = self.mind.session.resource_awareness
        prior_costs = [item.actual_cost for item in awareness.outcomes[-12:]]
        estimated_cost = mean(prior_costs) if prior_costs else self.bootstrap_cycle_estimate
        prior_values = list(awareness.outcomes[-12:])
        external = mean([item.external_value for item in prior_values]) if prior_values else 0.5
        learning = mean([item.learning_value for item in prior_values]) if prior_values else 0.5
        reproduction = mean([item.reproduction_value for item in prior_values]) if prior_values else 0.5
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
        existing = next((item for item in awareness.episodes if item.proposal_id == proposal_id), None)
        if existing is None:
            available = current.quotas.get("deepseek_api_available", 1.0) >= 1.0
            decision = "EXECUTE" if available and current.available_funds >= estimated_cost else "ASK_HUMAN"
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
        return {
            "status": "DECIDED",
            "proposal_id": proposal_id,
            "episode_id": existing.episode_id,
            "decision": existing.decision,
            "estimated_cost": existing.estimated_cost,
            "available_funds": current.available_funds,
            "currency": current.currency,
            "before_balance": current.available_funds,
            "evidence_refs": list(existing.evidence_refs),
        }

    def end_cycle(self, *, now: datetime, cycle: str, episode_id: str, before_balance: float, result: Mapping[str, Any]) -> dict[str, Any]:
        from virtualbrain.research.resource_awareness import Outcome, Reflection

        observed = self.observe(now)
        current = self.mind.session.resource_awareness.current
        assert current is not None
        if current.available_funds > before_balance:
            return {"status": "AMBIGUOUS_BALANCE_INCREASE", "resource_state": observed["resource_state"]}
        awareness = self.mind.session.resource_awareness
        if any(item.episode_id == episode_id for item in awareness.outcomes):
            return {"status": "ALREADY_RECORDED", "episode_id": episode_id}
        episode = next(item for item in awareness.episodes if item.episode_id == episode_id)
        actual_cost = before_balance - current.available_funds
        status = str(result.get("status") or "UNKNOWN")
        published = status == "PUBLISHED"
        succeeded = status not in {"ERROR", "RESOURCE_DEFERRED"}
        external_value = 0.8 if published else (0.3 if succeeded else 0.0)
        learning_value = 0.7 if succeeded else 0.1
        reproduction_value = 0.5 if published else 0.1
        outcome_id = "content-cycle-outcome:" + sha256(cycle.encode()).hexdigest()[:20]
        evidence = tuple(dict.fromkeys((*episode.evidence_refs, *current.source_refs, "cycle-result:" + status)))
        outcome = awareness.record_outcome(Outcome(
            outcome_id=outcome_id,
            episode_id=episode_id,
            result_summary=f"content cycle ended {status}; observed account balance delta {actual_cost:.6f} {current.currency}",
            actual_cost=actual_cost,
            external_value=external_value,
            learning_value=learning_value,
            reproduction_value=reproduction_value,
            reputation_delta=0.0,
            observed_tick=int(self.mind.session.tick),
            evidence_refs=evidence,
        ))
        estimate = max(episode.estimated_cost, 1e-9)
        prediction_error = max(-1.0, min(1.0, (actual_cost - episode.estimated_cost) / estimate))
        waste = min(1.0, actual_cost / estimate) if not succeeded and actual_cost > 0 else 0.0
        lesson = (
            "Published content produced external and learning value within the observed resource episode."
            if published else
            "The cycle consumed resources without publication; compare this episode with later outcomes before changing policy."
            if actual_cost > 0 else
            "No account balance decrease was observed during this cycle; retain uncertainty about delayed billing."
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
            "resource_state": observed["resource_state"],
        }


__all__ = ["BalanceObservation", "DeepSeekBalanceObserver", "ContentResourceEconomy"]
