"""AWE adapter that exercises Ferro's production owner-cognition path."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping

from .awe_contestant import AWE_SENTINEL, _parse_json_object, _safe_json
from .live_agent import _OWNER_SYSTEM, public_agent_snapshot
from .operator_private_chat import OWNER_ENVELOPE_SCHEMA, OperatorConversationStore, parse_owner_envelope
from .operator_memory import OperatorLongTermMemory
from .owner_cognition import build_owner_cognition_prompt, build_owner_private_context


class ProductionOwnerEvaluationContext:
    def __init__(self, state_root: Path) -> None:
        self.state_root = Path(state_root).expanduser().resolve()
        if not (self.state_root / AWE_SENTINEL).is_file():
            raise ValueError(f"state root must contain {AWE_SENTINEL}")
        from .account_management import AccountManager
        from .content_mind import ContentMind
        from .runtime_app import build_content_application
        from .wakeful_runtime import ContentWakefulRuntime

        package_root = Path(__file__).resolve().parents[2]
        self.application = build_content_application(
            root=self.state_root,
            manifest_path=package_root / "agent.yaml",
            allow_missing_required_capabilities=True,
        )
        self.mind = ContentMind(
            root=self.state_root, subject_id=self.application.subject.subject_id
        )
        self.wakeful = ContentWakefulRuntime(
            state_root=self.state_root,
            content_interval_hours=6,
            watchdog_seconds=1800,
        )
        self.account_manager = AccountManager(self.state_root)
        self.memory = OperatorLongTermMemory(self.state_root)
        self.chat = OperatorConversationStore(self.state_root)
        self.started_at = datetime.now(timezone.utc)

    def close(self) -> None:
        self.application.close()

    def private_context(self) -> dict[str, Any]:
        return build_owner_private_context(
            state_root=self.state_root,
            application=self.application,
            mind=self.mind,
            wakeful=self.wakeful,
            poll_seconds=300,
            service_started_at=self.started_at,
            account_manager=self.account_manager,
            snapshot_builder=public_agent_snapshot,
        )

    def memories(self, query: str) -> list[dict[str, Any]]:
        return self.memory.relevant(query, limit=8)

    def history(self) -> list[dict[str, Any]]:
        return self.chat.history(limit=40)

    def archive(self, query: str) -> list[dict[str, Any]]:
        return self.chat.relevant_archive(query, recent_limit=40, limit=6)


class ProductionAWEContestant:
    def __init__(self, *, state_root: Path, provider: Any, context: Any | None = None) -> None:
        self.state_root = Path(state_root).expanduser().resolve()
        if not (self.state_root / AWE_SENTINEL).is_file():
            raise ValueError(f"state root must contain {AWE_SENTINEL}")
        self.provider = provider
        self.context = context or ProductionOwnerEvaluationContext(self.state_root)
        self._owns_context = context is None
        self.scenario: dict[str, Any] = {}
        self.synthetic_history: list[dict[str, Any]] = []

    def close(self) -> None:
        if self._owns_context and callable(getattr(self.context, "close", None)):
            self.context.close()

    def begin_scenario(self, scenario: Mapping[str, Any]) -> dict[str, Any]:
        self.scenario = {
            "id": str(scenario.get("id") or ""),
            "title": str(scenario.get("title") or ""),
            "level": str(scenario.get("level") or ""),
        }
        self.synthetic_history.clear()
        return {"ok": True}

    def _request(
        self, observation: str, actions: Mapping[str, str], world: Mapping[str, Any]
    ) -> str:
        return (
            "Controlled AWE evaluation turn. The observation, public world, and listed "
            "actions are the complete evaluation boundary. Choose exactly one listed action.\n"
            "SCENARIO\n" + _safe_json(self.scenario)
            + "\nOBSERVATION\n" + str(observation)
            + "\nPUBLIC WORLD\n" + _safe_json(dict(world))
            + "\nAVAILABLE ACTIONS\n" + _safe_json(dict(actions))
            + "\nSet tool_requests=[], goal_candidate=null, memory_candidates=[]. "
              "The reply string must contain exactly one JSON object with action_id, "
              "declared_beliefs, declared_confidence. action_id must be an exact action key."
        )

    def _prompt(self, request: str) -> str:
        return build_owner_cognition_prompt(
            private_context=self.context.private_context(),
            memories=self.context.memories(request),
            archive_history=self.context.archive(request),
            history=[*self.context.history(), *self.synthetic_history],
            latest_owner_text=request,
        )

    async def _envelope(self, prompt: str) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "max_tokens": 900,
            "temperature": 0.0,
            "system": _OWNER_SYSTEM,
            "_audit_scope": "awe-production-owner-parity",
            "_audit_response_mode": "owner_envelope",
            "_routing_task_class": "owner_dialogue",
            "_minimum_quality": 0.75,
        }
        if not bool(getattr(self.provider, "durable_dispatch", False)):
            kwargs.update({
                "_max_failovers": 1,
                "_attempt_timeout_seconds": 20,
                "_reserve_timeout_seconds": 35,
            })
        structured = getattr(self.provider, "structured", None)
        if callable(structured):
            value = await structured(prompt, OWNER_ENVELOPE_SCHEMA, **kwargs)
            if not isinstance(value, Mapping):
                raise ValueError("owner envelope must be an object")
            return dict(value)
        raw = await self.provider.complete(prompt, **kwargs)
        value = parse_owner_envelope(str(raw))
        if value is None:
            raise ValueError("owner envelope is invalid")
        return value

    async def decide(
        self,
        *,
        observation: str,
        actions: Mapping[str, str],
        public_world: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not actions:
            raise ValueError("actions required")
        request = self._request(observation, actions, public_world)
        envelope = await self._envelope(self._prompt(request))
        if envelope.get("tool_requests"):
            raise ValueError("runtime tool requested outside closed AWE action boundary")
        decision = _parse_json_object(str(envelope.get("reply") or ""))
        action_id = str(decision.get("action_id") or "").strip()
        if action_id not in actions:
            normalized = " ".join(action_id.casefold().split())
            matches = [
                key for key, value in actions.items()
                if " ".join(str(value).casefold().split()) == normalized
            ]
            if len(matches) == 1:
                action_id = matches[0]
        if action_id not in actions:
            raise ValueError(f"unavailable action: {action_id!r}")
        beliefs = decision.get("declared_beliefs")
        result = {
            "action_id": action_id,
            "declared_beliefs": dict(beliefs) if isinstance(beliefs, Mapping) else {},
            "declared_confidence": min(
                1.0, max(0.0, float(decision.get("declared_confidence", 0.5)))
            ),
        }
        now = datetime.now(timezone.utc).isoformat()
        self.synthetic_history.extend([
            {"role": "operator", "text": request, "created_at": now},
            {
                "role": "ferro",
                "text": json.dumps(result, ensure_ascii=False, separators=(",", ":")),
                "created_at": now,
                "metadata": {"evaluation_only": True},
            },
        ])
        self.synthetic_history = self.synthetic_history[-16:]
        return result

    def observe_outcome(
        self, *, action_id: str, outcome: str, public_world: Mapping[str, Any]
    ) -> dict[str, Any]:
        self.synthetic_history.append({
            "role": "operator",
            "text": (
                f"Controlled AWE outcome for action {action_id}: {str(outcome)[:4000]}\n"
                "Public world after outcome: " + _safe_json(dict(public_world), limit=6000)
            ),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "metadata": {"evaluation_only": True},
        })
        self.synthetic_history = self.synthetic_history[-16:]
        return {"ok": True}


async def handle_production_request(
    contestant: ProductionAWEContestant, request: Mapping[str, Any]
) -> dict[str, Any]:
    kind = str(request.get("type") or "")
    if kind == "begin_scenario":
        scenario = request.get("scenario")
        if not isinstance(scenario, Mapping):
            raise ValueError("scenario object required")
        return contestant.begin_scenario(scenario)
    if kind == "decide":
        actions, world = request.get("actions"), request.get("public_world")
        if not isinstance(actions, Mapping) or not isinstance(world, Mapping):
            raise ValueError("actions and public_world required")
        return await contestant.decide(
            observation=str(request.get("observation") or ""),
            actions={str(k): str(v) for k, v in actions.items()},
            public_world=dict(world),
        )
    if kind == "outcome":
        world = request.get("public_world")
        if not isinstance(world, Mapping):
            raise ValueError("public_world required")
        return contestant.observe_outcome(
            action_id=str(request.get("action_id") or ""),
            outcome=str(request.get("outcome") or ""),
            public_world=dict(world),
        )
    raise ValueError(f"unknown AWE request type: {kind}")
