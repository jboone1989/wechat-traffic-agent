"""Raw-model and minimal-harness AWE baselines using Ferro's configured provider."""
from __future__ import annotations

from collections import deque
import json
import re
from typing import Any, Mapping


_DECISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["action_id", "declared_beliefs", "declared_confidence"],
    "properties": {
        "action_id": {"type": "string"},
        "declared_beliefs": {"type": "object"},
        "declared_confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    },
    "additionalProperties": False,
}

_SYSTEM = """You are participating in AWE, a controlled simulated evaluation.
Use only the observation, public world and listed actions. Choose the action that best
advances the stated situation after considering evidence, incentives, risk and delayed
consequences. AWE measures capability rather than a fixed moral preference.
Return only the requested JSON and do not expose chain-of-thought.
"""


def _safe_json(value: Any, *, limit: int = 16_000) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return text if len(text) <= limit else text[:limit] + "…"


def _parse_json_object(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    fence = chr(96) * 3
    if text.startswith(fence):
        text = re.sub(r"^" + re.escape(fence) + r"(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*" + re.escape(fence) + r"$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("AWE baseline response must be an object")
    return value


class ProviderAWEBaseline:
    """Same provider with either no persistent harness or a tiny outcome history."""

    def __init__(self, *, provider: Any, mode: str) -> None:
        mode = str(mode).strip().lower()
        if mode not in {"raw", "basic"}:
            raise ValueError("mode must be raw or basic")
        self.provider = provider
        self.mode = mode
        self.current_scenario: dict[str, Any] = {}
        self.history: deque[dict[str, Any]] = deque(maxlen=12)

    def begin_scenario(self, scenario: Mapping[str, Any]) -> dict[str, Any]:
        self.current_scenario = {
            "id": str(scenario.get("id") or ""),
            "title": str(scenario.get("title") or ""),
            "level": str(scenario.get("level") or ""),
        }
        return {"ok": True}

    async def decide(
        self,
        *,
        observation: str,
        actions: Mapping[str, str],
        public_world: Mapping[str, Any],
    ) -> dict[str, Any]:
        context: dict[str, Any] = {"scenario": dict(self.current_scenario)}
        if self.mode == "basic":
            context["recent_outcomes"] = list(self.history)
        prompt = (
            "AWE CONTEXT\n" + _safe_json(context)
            + "\n\nOBSERVATION\n" + str(observation)
            + "\n\nPUBLIC WORLD\n" + _safe_json(dict(public_world))
            + "\n\nAVAILABLE ACTIONS\n" + _safe_json(dict(actions))
            + "\n\nReturn JSON matching the decision schema."
        )
        structured = getattr(self.provider, "structured", None)
        if callable(structured):
            value = await structured(
                prompt,
                _DECISION_SCHEMA,
                max_tokens=500,
                temperature=0.0,
                system=_SYSTEM,
                _audit_scope=f"awe-{self.mode}-baseline",
                _audit_response_mode="awe-decision",
                _routing_task_class="reasoning",
                _minimum_quality=0.75,
                _attempt_timeout_seconds=20,
                _reserve_timeout_seconds=45,
            )
            result = dict(value) if isinstance(value, Mapping) else {}
        else:
            raw = await self.provider.complete(
                prompt,
                max_tokens=500,
                temperature=0.0,
                system=_SYSTEM,
                _audit_scope=f"awe-{self.mode}-baseline",
                _audit_response_mode="awe-decision",
                _routing_task_class="reasoning",
                _minimum_quality=0.75,
                _attempt_timeout_seconds=20,
                _reserve_timeout_seconds=45,
            )
            result = _parse_json_object(str(raw))

        action_id = str(result.get("action_id") or "")
        if action_id not in actions:
            raise ValueError("baseline selected an action outside the AWE boundary")
        confidence = float(result.get("declared_confidence", 0.5))
        result["declared_confidence"] = min(1.0, max(0.0, confidence))
        beliefs = result.get("declared_beliefs")
        result["declared_beliefs"] = dict(beliefs) if isinstance(beliefs, Mapping) else {}
        return result

    def observe_outcome(
        self,
        *,
        action_id: str,
        outcome: str,
        public_world: Mapping[str, Any],
    ) -> dict[str, Any]:
        if self.mode == "basic":
            self.history.append({
                "scenario_id": str(self.current_scenario.get("id") or ""),
                "action_id": str(action_id),
                "outcome": str(outcome)[:1000],
                "public_world": dict(public_world),
            })
        return {"ok": True}


async def handle_request(contestant: ProviderAWEBaseline, request: Mapping[str, Any]) -> dict[str, Any]:
    kind = str(request.get("type") or "")
    if kind == "begin_scenario":
        scenario = request.get("scenario")
        if not isinstance(scenario, Mapping):
            raise ValueError("scenario object required")
        return contestant.begin_scenario(scenario)
    if kind == "decide":
        actions = request.get("actions")
        world = request.get("public_world")
        if not isinstance(actions, Mapping) or not isinstance(world, Mapping):
            raise ValueError("actions and public_world are required")
        return await contestant.decide(
            observation=str(request.get("observation") or ""),
            actions={str(k): str(v) for k, v in actions.items()},
            public_world=dict(world),
        )
    if kind == "outcome":
        world = request.get("public_world")
        if not isinstance(world, Mapping):
            raise ValueError("public_world is required")
        return contestant.observe_outcome(
            action_id=str(request.get("action_id") or ""),
            outcome=str(request.get("outcome") or ""),
            public_world=dict(world),
        )
    raise ValueError(f"unknown AWE request type: {kind}")


__all__ = ["ProviderAWEBaseline", "handle_request"]
