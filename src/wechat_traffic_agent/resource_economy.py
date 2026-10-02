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
        from .runtime_backend import chatgpt_only_mode
        from .agent_runtime_chatgpt import AgentRuntimeChatGPTProvider

        chatgpt_only = chatgpt_only_mode()
        chatgpt_available = False
        if chatgpt_only:
            chatgpt_available = AgentRuntimeChatGPTProvider.from_environment(
                fallback=None
            ).available
            balance = BalanceObservation(
                observed_at=_iso(now), is_available=False, currency="CNY",
                total_balance=0.0, granted_balance=0.0, topped_up_balance=0.0,
                evidence_ref=(
                    "livingruntime-chatgpt:available"
                    if chatgpt_available
                    else "livingruntime-chatgpt:unavailable"
                ),
            )
            balance_observed = False
        else:
            try:
                balance = self.observer.observe(now)
                balance_observed = True
            except (RuntimeError, ValueError) as exc:
                # A failed account sensor is unknown, never a measured zero balance.
                balance = BalanceObservation(
                    observed_at=_iso(now), is_available=False, currency="CNY",
                    total_balance=0.0, granted_balance=0.0, topped_up_balance=0.0,
                    evidence_ref="deepseek-balance:unobserved:" + type(exc).__name__,
                )
                balance_observed = False
        awareness = self.mind.session.resource_awareness
        # Provider APIs may report a small negative account balance after usage.
        # Preserve that raw observation as evidence, but ResourceState.available_funds
        # means spendable funds and is non-negative by contract.
        spendable_funds = max(0.0, float(balance.total_balance))
        provider_debt = max(0.0, -float(balance.total_balance))
        burn = None if chatgpt_only else self._daily_burn(awareness.states, balance)
        runway = spendable_funds / burn if burn is not None and burn > 0 else None
        from .llm_resources import eligible_free_resources, pool_metrics
        llm_metrics = pool_metrics(self.state_root)
        free_routes = eligible_free_resources(self.state_root, now)
        state = ResourceState(
            observed_tick=int(self.mind.session.tick),
            observed_at=balance.observed_at,
            currency=balance.currency,
            available_funds=spendable_funds,
            period_budget=self.monthly_budget,
            carryover_funds=None,
            spent_today=None,
            spent_period=None,
            rolling_7d_spend=None,
            rolling_30d_spend=None,
            estimated_daily_burn=burn,
            runway_days=runway,
            quotas={
                "chatgpt_cognition_authoritative": 1.0 if chatgpt_only else 0.0,
                "chatgpt_cognition_available": 1.0 if chatgpt_available else 0.0,
                "deepseek_api_available": 1.0 if balance_observed and balance.is_available else 0.0,
                "deepseek_balance_observed": 1.0 if balance_observed else 0.0,
                "llm_eligible_free_route_count": (
                    0.0 if chatgpt_only else float(len(free_routes))
                ),
                "deepseek_granted_balance": max(0.0, float(balance.granted_balance)),
                "deepseek_topped_up_balance": max(0.0, float(balance.topped_up_balance)),
                "deepseek_account_debt": provider_debt,
                **llm_metrics,
            },
            source_refs=(balance.evidence_ref,),
        )
        self.mind.session.resource_awareness.observe(state)
        self.mind.persist_resource_awareness()
        routing_policy = self.sync_llm_routing_policy(now=now)
        return {
            "status": (
                "CHATGPT_ONLY"
                if chatgpt_only and chatgpt_available
                else "CHATGPT_UNAVAILABLE"
                if chatgpt_only
                else "OBSERVED"
                if balance_observed
                else "PARTIALLY_OBSERVED"
            ),
            "balance": balance.payload() if balance_observed else None,
            "balance_sensor": (
                "NOT_APPLICABLE"
                if chatgpt_only
                else "OBSERVED"
                if balance_observed
                else "UNKNOWN"
            ),
            "resource_state": self._state_payload(state),
            "llm_routing_policy": routing_policy,
        }

    def sync_llm_routing_policy(self, *, now: datetime) -> dict[str, Any]:
        """Translate VirtualBrain resource awareness into a secret-free Runtime routing policy."""
        current = self.mind.session.resource_awareness.current
        if current is None:
            pressure = "UNKNOWN"
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
        from .agent_runtime_chatgpt import AgentRuntimeChatGPTProvider
        from .runtime_backend import chatgpt_only_mode
        chatgpt_provider = AgentRuntimeChatGPTProvider.from_environment(fallback=None)
        chatgpt_only = chatgpt_only_mode()
        chatgpt_available = bool(chatgpt_provider.available) if chatgpt_only else False
        if chatgpt_only:
            # ChatGPT is the configured cognition capacity. Preserve the observed
            # DeepSeek balance as financial evidence, but do not reinterpret an
            # empty API balance as lack of reasoning capacity. If the bridge
            # itself disappears, fail closed instead of silently changing model.
            pressure = "NORMAL" if chatgpt_available else "UNKNOWN"

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
        windfall_active = bool(windfall_resources) and not chatgpt_only
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
        max_failovers = (
            0 if chatgpt_only
            else 2 if pressure in {"CRITICAL", "CONSERVE"}
            else 4
        )
        payload = {
            "schema_version": "vb-llm-routing-policy.v1",
            "observed_at": _iso(now),
            "routing_mode": "CHATGPT_ONLY" if chatgpt_only else "FREE_FIRST_RESERVE_PAID",
            "pressure": pressure,
            "prefer_free": not chatgpt_only,
            "chatgpt_available": chatgpt_available,
            "minimum_quality": float(
                os.environ.get("CONTENT_LLM_MINIMUM_QUALITY") or 0.70
            ),
            "max_failovers": max_failovers,
            "preferred_lifecycles": (
                [] if chatgpt_only
                else ["TEMPORARY_FREE", "RECURRING_FREE", "STABLE_PAID"]
            ),
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
            "prefer_free": payload["prefer_free"],
            "chatgpt_available": chatgpt_available,
            "max_failovers": max_failovers,
            "paid_reserve_floor": reserve_floor,
            "windfall_active": windfall_active,
            "windfall_bonus": windfall_bonus,
            "windfall_resource_count": len(windfall_resources),
            "windfall_mode": payload["windfall_mode"],
            "mind_tick": payload["mind_tick"],
            "evidence_refs": list(evidence_refs),
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
        delta = prior.available_funds - max(0.0, float(current.total_balance))
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

    def begin_cycle(self, *, now: datetime, cycle: str) -> dict[str, Any]:
        from virtualbrain.research.resource_awareness import ActionProposal, DecisionEpisode

        current = self.mind.session.resource_awareness.current
        observed_at = None
        if current is not None:
            try:
                observed_at = datetime.fromisoformat(str(current.observed_at).replace("Z", "+00:00"))
            except (TypeError, ValueError):
                pass
        if current is None or observed_at is None or observed_at.tzinfo is None or not 0 <= (now.astimezone(timezone.utc) - observed_at.astimezone(timezone.utc)).total_seconds() <= 7200:
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
        from .runtime_backend import chatgpt_only_mode
        chatgpt_only = chatgpt_only_mode()
        estimated_cost = (
            0.0
            if chatgpt_only
            else mean(cost_evidence)
            if cost_evidence
            else self.bootstrap_cycle_estimate
        )
        prior_values = [
            item for item in recent_outcomes
            if "ALREADY_PROCESSED" not in item.result_summary and "BUSY" not in item.result_summary
        ]
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
            if chatgpt_only:
                decision = "EXECUTE"
            else:
                from .llm_resources import eligible_free_resources
                free_available = bool(eligible_free_resources(self.state_root, now))
                paid_available = (
                    current.quotas.get("deepseek_balance_observed", 1.0) >= 1.0
                    and current.quotas.get("deepseek_api_available", 1.0) >= 1.0
                    and current.available_funds >= estimated_cost
                )
                decision = "EXECUTE" if free_available or paid_available else "ASK_HUMAN"
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
        cost_observed = observed.get("balance_sensor") == "OBSERVED"
        current = self.mind.session.resource_awareness.current
        assert current is not None
        if cost_observed and current.available_funds > before_balance:
            return {"status": "AMBIGUOUS_BALANCE_INCREASE", "resource_state": observed["resource_state"]}
        awareness = self.mind.session.resource_awareness
        if any(item.episode_id == episode_id for item in awareness.outcomes):
            return {"status": "ALREADY_RECORDED", "episode_id": episode_id}
        episode = next(item for item in awareness.episodes if item.episode_id == episode_id)
        actual_cost = before_balance - current.available_funds if cost_observed else 0.0
        status = str(result.get("status") or "UNKNOWN")
        published = status == "PUBLISHED"
        succeeded = status not in {"ERROR", "RESOURCE_DEFERRED"}
        def observed_value(key: str) -> float:
            value = result.get(key)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                return 0.0
            return max(0.0, min(1.0, float(value)))
        external_value = observed_value("external_value")
        if "external_value" not in result:
            external_value = observed_value("world_utility")
        learning_value = observed_value("learning_value")
        reproduction_value = observed_value("reproduction_value")
        outcome_id = "content-cycle-outcome:" + sha256(cycle.encode()).hexdigest()[:20]
        evidence = tuple(dict.fromkeys((*episode.evidence_refs, *current.source_refs, "cycle-result:" + status,
                                        "cost-sensor:observed" if cost_observed else "cost-sensor:unknown")))
        outcome = awareness.record_outcome(Outcome(
            outcome_id=outcome_id,
            episode_id=episode_id,
            result_summary=(f"content cycle ended {status}; observed account balance delta {actual_cost:.6f} {current.currency}"
                            if cost_observed else f"content cycle ended {status}; account cost unknown"),
            actual_cost=actual_cost,
            external_value=external_value,
            learning_value=learning_value,
            reproduction_value=reproduction_value,
            reputation_delta=0.0,
            observed_tick=int(self.mind.session.tick),
            evidence_refs=evidence,
        ))
        estimate = max(episode.estimated_cost, 1e-9)
        prediction_error = max(-1.0, min(1.0, (actual_cost - episode.estimated_cost) / estimate)) if cost_observed else 0.0
        waste = min(1.0, actual_cost / estimate) if not succeeded and actual_cost > 0 else 0.0
        lesson = (
            "Publication was observed; downstream external value remains unknown until measured."
            if published else
            "The cycle consumed resources without publication; compare this episode with later outcomes before changing policy."
            if actual_cost > 0 else
            "Account cost was not observed; retain uncertainty about provider billing."
            if not cost_observed else
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
            "status": "RECORDED" if cost_observed else "RECORDED_COST_UNKNOWN",
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
