"""Fast bounded decision layer between Ferro's LIF state and deep cognition.

L0 (VirtualBrain/LIF) produces persistent internal state.  This module is L1:
it chooses among an explicit finite action set.  It never generates prose or
silently falls back to an LLM.  L2 (LivingRuntime ChatGPT) is entered only when
CONTENT_COGNITION is the selected action or another task explicitly requires it.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import time
from typing import Any, Mapping, Protocol

import httpx


class FastDecisionError(RuntimeError):
    """A bounded L1 decision failed and must be surfaced to interoception."""


class FastDecisionUnavailable(FastDecisionError):
    """The configured L1 provider is not available."""


@dataclass(frozen=True)
class FastDecisionResult:
    decision_id: str
    provider: str
    model: str
    choice: str
    confidence: float
    probabilities: dict[str, float]
    latency_ms: int
    decided_at: str


class FastDecisionProvider(Protocol):
    provider_name: str

    def choose(
        self,
        *,
        decision: str,
        state: Mapping[str, Any],
        choices: Mapping[str, str],
        instructions: Mapping[str, Any] | None = None,
    ) -> FastDecisionResult: ...


class FastDecisionLedger:
    """Persist bounded receipts without prompts, secrets, or free-form state."""

    def __init__(self, state_root: Path) -> None:
        self.path = Path(state_root) / "application" / "fast_decisions.jsonl"

    def append(
        self,
        result: FastDecisionResult,
        *,
        decision: str,
        state: Mapping[str, Any],
    ) -> None:
        state_encoded = json.dumps(
            state,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        row = {
            "schema_version": "ferro-fast-decision-receipt.v1",
            **asdict(result),
            "decision": str(decision)[:120],
            "state_sha256": hashlib.sha256(state_encoded).hexdigest(),
            "state_keys": sorted(str(key)[:80] for key in state.keys())[:64],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")


class JevFastDecisionProvider:
    """Use TypeSafe/Jev System One directly as Ferro's bounded L1 chooser."""

    provider_name = "JEV"

    def __init__(
        self,
        *,
        api_key: str,
        state_root: Path,
        model: str = "jev-latest",
        endpoint: str = "https://api.typesafe.ai/v1/systemone",
        timeout_seconds: float = 8.0,
        client: Any | None = None,
    ) -> None:
        key = str(api_key or "").strip()
        if not key:
            raise FastDecisionUnavailable("TYPESAFE_API_KEY is not configured")
        self.api_key = key
        self.model = str(model or "jev-latest").strip()
        self.endpoint = str(endpoint).strip()
        self.timeout_seconds = float(timeout_seconds)
        if not 0.5 <= self.timeout_seconds <= 30.0:
            raise ValueError("fast decision timeout must be within 0.5..30 seconds")
        self.client = client or httpx.Client(
            timeout=self.timeout_seconds,
        )
        self.ledger = FastDecisionLedger(Path(state_root))

    @classmethod
    def from_environment(cls, *, state_root: Path) -> "JevFastDecisionProvider":
        timeout_raw = str(
            os.environ.get("CONTENT_FAST_DECISION_TIMEOUT_SECONDS") or "8"
        ).strip()
        try:
            timeout_seconds = float(timeout_raw)
        except ValueError as exc:
            raise ValueError(
                "CONTENT_FAST_DECISION_TIMEOUT_SECONDS must be numeric"
            ) from exc
        return cls(
            api_key=os.environ.get("TYPESAFE_API_KEY", ""),
            state_root=state_root,
            model=os.environ.get("CONTENT_FAST_DECISION_JEV_MODEL", "jev-latest"),
            endpoint=os.environ.get(
                "CONTENT_FAST_DECISION_JEV_ENDPOINT",
                "https://api.typesafe.ai/v1/systemone",
            ),
            timeout_seconds=timeout_seconds,
        )

    @staticmethod
    def _validate_answer(
        answer: Mapping[str, Any],
        choices: Mapping[str, str],
    ) -> tuple[str, float, dict[str, float]]:
        ids = set(choices)
        choice = str(answer.get("choice") or "")
        raw_probabilities = answer.get("probabilities")
        if not isinstance(raw_probabilities, Mapping):
            raise FastDecisionError("Jev answer omitted probabilities")
        probabilities: dict[str, float] = {}
        for key, value in raw_probabilities.items():
            key = str(key)
            if key not in ids or not isinstance(value, (int, float)):
                raise FastDecisionError("Jev returned an invalid probability map")
            number = float(value)
            if not math.isfinite(number) or not 0.0 <= number <= 1.0:
                raise FastDecisionError("Jev returned an invalid probability value")
            probabilities[key] = number
        confidence = answer.get("confidence")
        if not isinstance(confidence, (int, float)):
            raise FastDecisionError("Jev answer omitted confidence")
        confidence = float(confidence)
        if (
            choice not in ids
            or set(probabilities) != ids
            or not math.isfinite(confidence)
            or not 0.0 <= confidence <= 1.0
            or abs(sum(probabilities.values()) - 1.0) >= 0.02
            or probabilities[choice] < max(probabilities.values()) - 1e-6
        ):
            raise FastDecisionError("Jev returned an invalid bounded choice")
        return choice, confidence, probabilities

    def choose(
        self,
        *,
        decision: str,
        state: Mapping[str, Any],
        choices: Mapping[str, str],
        instructions: Mapping[str, Any] | None = None,
    ) -> FastDecisionResult:
        if len(choices) < 2:
            raise ValueError("fast decision requires at least two choices")
        decision = str(decision or "").strip()
        if not decision:
            raise ValueError("decision name is required")
        normalized_choices = {
            str(key): str(value)
            for key, value in choices.items()
            if str(key).strip() and str(value).strip()
        }
        if len(normalized_choices) != len(choices):
            raise ValueError("fast decision choices must be non-empty strings")
        body = {
            "model": self.model,
            "state": dict(state),
            "questions": {
                decision: {
                    "type": "choice",
                    "criteria": normalized_choices,
                    "instructions": dict(instructions or {}),
                }
            },
        }
        started = time.perf_counter()
        try:
            response = self.client.post(
                self.endpoint,
                json=body,
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
        except httpx.HTTPError as exc:
            raise FastDecisionError(
                f"Jev connection failed: {type(exc).__name__}"
            ) from exc
        if bool(getattr(response, "is_error", False)):
            raise FastDecisionError(
                f"Jev returned HTTP {int(getattr(response, 'status_code', 0) or 0)}"
            )
        try:
            payload = response.json()
        except (ValueError, TypeError) as exc:
            raise FastDecisionError("Jev returned invalid JSON") from exc
        answers = payload.get("answers") if isinstance(payload, Mapping) else None
        if not isinstance(answers, Mapping):
            raise FastDecisionError("Jev response omitted answers")
        answer = answers.get(decision)
        if not isinstance(answer, Mapping):
            raise FastDecisionError(f"Jev response omitted {decision!r}")
        choice, confidence, probabilities = self._validate_answer(
            answer,
            normalized_choices,
        )
        now = datetime.now(timezone.utc)
        identity = json.dumps(
            {
                "decision": decision,
                "choice": choice,
                "at": now.isoformat(),
                "probabilities": probabilities,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        result = FastDecisionResult(
            decision_id="fastdec_" + hashlib.sha256(identity).hexdigest()[:24],
            provider=self.provider_name,
            model=str(payload.get("model") or self.model),
            choice=choice,
            confidence=round(confidence, 6),
            probabilities={
                key: round(value, 6)
                for key, value in probabilities.items()
            },
            latency_ms=round((time.perf_counter() - started) * 1000),
            decided_at=now.isoformat(),
        )
        self.ledger.append(result, decision=decision, state=state)
        return result


def load_fast_decision_provider(*, state_root: Path) -> FastDecisionProvider:
    provider = str(
        os.environ.get("CONTENT_FAST_DECISION_PROVIDER") or "jev"
    ).strip().lower()
    if provider == "jev":
        return JevFastDecisionProvider.from_environment(state_root=state_root)
    raise FastDecisionUnavailable(
        f"unsupported CONTENT_FAST_DECISION_PROVIDER={provider!r}"
    )


__all__ = [
    "FastDecisionError",
    "FastDecisionLedger",
    "FastDecisionProvider",
    "FastDecisionResult",
    "FastDecisionUnavailable",
    "JevFastDecisionProvider",
    "load_fast_decision_provider",
]
