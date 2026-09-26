from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import math
import os
from pathlib import Path
import sys
from typing import Any, AsyncIterator


_DURABLE_PHASE_ID: ContextVar[str] = ContextVar(
    "livingruntime_chatgpt_durable_phase_id",
    default="",
)
_DURABLE_REQUEST_ID: ContextVar[str] = ContextVar(
    "livingruntime_chatgpt_durable_request_id",
    default="",
)


@contextmanager
def durable_cognition_phase(phase_id: str, *, request_id: str | None = None):
    phase_token = _DURABLE_PHASE_ID.set(str(phase_id or "").strip())
    request_token = _DURABLE_REQUEST_ID.set(str(request_id or "").strip())
    try:
        yield
    finally:
        _DURABLE_REQUEST_ID.reset(request_token)
        _DURABLE_PHASE_ID.reset(phase_token)


class LivingRuntimeChatGPTProviderError(RuntimeError):
    pass


class LivingRuntimeChatGPTPendingError(BaseException):
    """Control-flow suspension: durable request exists, response is not ready.

    This intentionally inherits from BaseException, like asyncio cancellation,
    so broad business-level exception handlers cannot accidentally convert a
    suspended cognition phase into an application failure.
    """

    def __init__(self, request_id: str) -> None:
        self.request_id = str(request_id)
        super().__init__(f"LivingRuntime cognition is waiting for ChatGPT: {self.request_id}")


class LivingRuntimeChatGPTProvider:
    """Use a LivingRuntime Remote cognition queue as an LLM provider.

    The durable queue is owned by LivingRuntime Remote. This adapter intentionally
    treats ChatGPT conversations as replaceable compute workers: every request is
    self-contained and Ferro remains the source of truth for identity, memory,
    goals, and world state.
    """

    provider_name = "livingruntime-chatgpt"
    model_name = "chatgpt-session"

    def __init__(
        self,
        *,
        fallback: Any | None = None,
        cli_path: Path,
        enabled: bool,
        agent_id: str = "ferro",
        timeout_seconds: float = 900.0,
        dispatch_grace_seconds: float = 900.0,
        foreground_wait_seconds: float = 5.0,
        durable_dispatch: bool = True,
    ) -> None:
        self.fallback = fallback
        self.cli_path = Path(cli_path)
        self.enabled = bool(enabled)
        self.agent_id = str(agent_id or "ferro").strip()
        timeout = float(timeout_seconds)
        if not math.isfinite(timeout) or not 10.0 <= timeout <= 900.0:
            raise ValueError("LivingRuntime ChatGPT timeout must be within 10..900 seconds")
        self.timeout_seconds = timeout
        dispatch_grace = float(dispatch_grace_seconds)
        if not math.isfinite(dispatch_grace) or not 1.0 <= dispatch_grace <= 900.0:
            raise ValueError(
                "LivingRuntime ChatGPT dispatch grace must be within 1..900 seconds"
            )
        self.dispatch_grace_seconds = dispatch_grace
        foreground_wait = float(foreground_wait_seconds)
        if not math.isfinite(foreground_wait) or not 1.0 <= foreground_wait <= 120.0:
            raise ValueError(
                "LivingRuntime ChatGPT foreground wait must be within 1..120 seconds"
            )
        self.foreground_wait_seconds = foreground_wait
        self.durable_dispatch = bool(durable_dispatch)
        self.last_provenance: dict[str, Any] | None = None

    @classmethod
    def from_environment(cls, *, fallback: Any | None = None) -> "LivingRuntimeChatGPTProvider":
        raw_enabled = os.environ.get("LIVINGRUNTIME_CHATGPT_PROVIDER_ENABLED")
        enable_marker = (
            Path.home() / ".livingruntime" / "ferro-chatgpt-provider.enabled"
        )
        if raw_enabled is None or not str(raw_enabled).strip():
            enabled = enable_marker.is_file()
        else:
            enabled = str(raw_enabled).strip().lower() in {"1", "true", "yes", "on"}
        default_cli = (
            Path.home() / ".livingruntime" / "plugin" / "scripts" / "cognitionctl.py"
        )
        cli = Path(
            str(
                os.environ.get("LIVINGRUNTIME_CHATGPT_COGNITION_CLI")
                or default_cli
            ).strip()
        ).expanduser()
        agent_id = str(
            os.environ.get("LIVINGRUNTIME_CHATGPT_AGENT_ID") or "ferro"
        ).strip()
        raw_timeout = str(
            os.environ.get("LIVINGRUNTIME_CHATGPT_TIMEOUT_SECONDS") or "900"
        ).strip()
        try:
            timeout = float(raw_timeout)
        except ValueError as exc:
            raise ValueError(
                "LIVINGRUNTIME_CHATGPT_TIMEOUT_SECONDS must be numeric"
            ) from exc
        raw_dispatch_grace = str(
            os.environ.get("LIVINGRUNTIME_CHATGPT_DISPATCH_GRACE_SECONDS") or "900"
        ).strip()
        try:
            dispatch_grace = float(raw_dispatch_grace)
        except ValueError as exc:
            raise ValueError(
                "LIVINGRUNTIME_CHATGPT_DISPATCH_GRACE_SECONDS must be numeric"
            ) from exc
        raw_foreground_wait = str(
            os.environ.get("LIVINGRUNTIME_CHATGPT_FOREGROUND_WAIT_SECONDS") or "5"
        ).strip()
        try:
            foreground_wait = float(raw_foreground_wait)
        except ValueError as exc:
            raise ValueError(
                "LIVINGRUNTIME_CHATGPT_FOREGROUND_WAIT_SECONDS must be numeric"
            ) from exc
        durable_dispatch = str(
            os.environ.get("LIVINGRUNTIME_CHATGPT_DURABLE_DISPATCH") or "1"
        ).strip().lower() in {"1", "true", "yes", "on"}
        return cls(
            fallback=fallback,
            cli_path=cli,
            enabled=enabled,
            agent_id=agent_id,
            timeout_seconds=timeout,
            dispatch_grace_seconds=dispatch_grace,
            foreground_wait_seconds=foreground_wait,
            durable_dispatch=durable_dispatch,
        )

    @property
    def available(self) -> bool:
        return self.enabled and self.cli_path.is_file()

    def _request_id(
        self,
        prompt: str,
        *,
        response_format: dict[str, Any],
        task_class: str,
    ) -> str:
        existing_request_id = _DURABLE_REQUEST_ID.get().strip()
        if existing_request_id:
            return existing_request_id
        phase_id = _DURABLE_PHASE_ID.get().strip()
        identity = {
            "agent_id": self.agent_id,
            "task_class": task_class,
            "phase_id": phase_id,
            "prompt": "" if phase_id else str(prompt),
            "response_format": response_format,
        }
        encoded = json.dumps(
            identity,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return "llmreq_" + hashlib.sha256(encoded).hexdigest()[:32]

    async def _remote_call(
        self,
        prompt: str,
        *,
        response_format: dict[str, Any],
        kwargs: dict[str, Any],
    ) -> str:
        if not self.enabled:
            raise LivingRuntimeChatGPTProviderError("LivingRuntime ChatGPT provider is disabled")
        if not self.cli_path.is_file():
            raise LivingRuntimeChatGPTProviderError(
                f"LivingRuntime cognition CLI is unavailable: {self.cli_path}"
            )

        system = kwargs.get("system")
        messages: list[dict[str, str]] = []
        if system is not None and str(system).strip():
            messages.append({"role": "system", "content": str(system)})
        messages.append({"role": "user", "content": str(prompt)})

        options: dict[str, Any] = {}
        max_tokens = kwargs.get("max_tokens")
        if max_tokens is not None:
            try:
                tokens = int(max_tokens)
            except (TypeError, ValueError):
                tokens = 0
            if tokens > 0:
                options["max_output_tokens"] = min(tokens, 32768)
        temperature = kwargs.get("temperature")
        if temperature is not None:
            try:
                value = float(temperature)
            except (TypeError, ValueError):
                value = None
            if value is not None and math.isfinite(value):
                options["temperature"] = max(0.0, min(value, 2.0))

        task_class = str(kwargs.get("_routing_task_class") or "content_cognition").strip()
        request_id = self._request_id(
            prompt,
            response_format=response_format,
            task_class=task_class,
        )
        metadata: dict[str, Any] = {
            "source": "ferro",
            "routing_task_class": task_class,
        }
        minimum_quality = kwargs.get("_minimum_quality")
        if minimum_quality is not None:
            try:
                metadata["minimum_quality"] = float(minimum_quality)
            except (TypeError, ValueError):
                pass

        effective_timeout = self.timeout_seconds
        caller_budget = kwargs.get("_reserve_timeout_seconds")
        if caller_budget is None:
            caller_budget = kwargs.get("_attempt_timeout_seconds")
        if caller_budget is not None:
            try:
                budget = float(caller_budget)
            except (TypeError, ValueError):
                budget = None
            if budget is not None and math.isfinite(budget) and budget > 0:
                # Leave a small margin for cognitionctl serialization/process
                # teardown so the outer advisory budget remains authoritative.
                effective_timeout = min(
                    effective_timeout,
                    max(1.0, budget - 3.0),
                )

        cli_timeout_seconds = max(
            1,
            min(int(math.ceil(effective_timeout)), 900),
        )
        dispatch_timeout_seconds = (
            0
            if self.durable_dispatch
            else max(
                1,
                min(int(math.ceil(self.dispatch_grace_seconds)), cli_timeout_seconds),
            )
        )
        payload = {
            "request_id": request_id,
            "agent_id": self.agent_id,
            "purpose": task_class[:512] or "content_cognition",
            "messages": messages,
            "response_format": response_format,
            "options": options,
            "metadata": metadata,
            "timeout_seconds": cli_timeout_seconds,
            "dispatch_timeout_seconds": dispatch_timeout_seconds,
        }
        body = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(body) > 262144:
            raise LivingRuntimeChatGPTProviderError(
                "LivingRuntime cognition request exceeds 256 KiB"
            )

        foreground_wait_seconds = max(
            1,
            min(
                int(math.ceil(self.foreground_wait_seconds)),
                max(1, cli_timeout_seconds - 1),
            ),
        )
        resume_existing = bool(_DURABLE_REQUEST_ID.get().strip())
        command = (
            [
                sys.executable,
                str(self.cli_path),
                "wait",
                request_id,
                "--wait-timeout-seconds",
                str(foreground_wait_seconds),
            ]
            if resume_existing
            else [
                sys.executable,
                str(self.cli_path),
                "call",
                "--wait-timeout-seconds",
                str(foreground_wait_seconds),
            ]
        )
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(None if resume_existing else body),
                timeout=float(foreground_wait_seconds) + 3.0,
            )
        except asyncio.TimeoutError as exc:
            proc.kill()
            await proc.wait()
            if self.durable_dispatch:
                raise LivingRuntimeChatGPTPendingError(request_id) from exc
            raise LivingRuntimeChatGPTProviderError(
                "LivingRuntime ChatGPT cognition timed out"
            ) from exc
        except BaseException:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
            raise

        if proc.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            if len(detail) > 1000:
                detail = detail[-1000:]
            if (
                self.durable_dispatch
                and "waiting for cognition response exceeded timeout" in detail
            ):
                raise LivingRuntimeChatGPTPendingError(request_id)
            raise LivingRuntimeChatGPTProviderError(
                "LivingRuntime cognition CLI failed"
                + (f": {detail}" if detail else "")
            )
        try:
            result = json.loads(stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LivingRuntimeChatGPTProviderError(
                "LivingRuntime cognition CLI returned invalid JSON"
            ) from exc
        if not isinstance(result, dict) or result.get("status") != "COMPLETED":
            raise LivingRuntimeChatGPTProviderError(
                "LivingRuntime cognition request did not complete"
            )
        response = result.get("response")
        if not isinstance(response, dict) or not isinstance(response.get("text"), str):
            raise LivingRuntimeChatGPTProviderError(
                "LivingRuntime cognition response is missing text"
            )
        self.last_provenance = {
            "provider": response.get("provider") or self.provider_name,
            "request_id": result.get("request_id"),
            "session_id": response.get("session_id"),
            "model": response.get("model"),
            "started_at": result.get("created_at"),
            "completed_at": response.get("completed_at") or result.get("finished_at"),
            "response_sha256": response.get("sha256"),
        }
        return response["text"]

    async def complete(self, prompt: str, **kwargs: Any) -> str:
        try:
            return await self._remote_call(
                prompt,
                response_format={"type": "text"},
                kwargs=kwargs,
            )
        except LivingRuntimeChatGPTPendingError:
            raise
        except (LivingRuntimeChatGPTProviderError, OSError):
            if self.fallback is None:
                raise
            return str(await self.fallback.complete(prompt, **kwargs))

    async def structured(
        self,
        prompt: str,
        schema: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        try:
            text = await self._remote_call(
                prompt,
                response_format={"type": "json_schema", "schema": schema},
                kwargs=kwargs,
            )
            value = json.loads(text)
            if not isinstance(value, dict):
                raise ValueError("structured response must be a JSON object")
            return value
        except (
            LivingRuntimeChatGPTPendingError,
        ):
            raise
        except (
            LivingRuntimeChatGPTProviderError,
            OSError,
            ValueError,
            json.JSONDecodeError,
        ):
            structured = getattr(self.fallback, "structured", None)
            if self.fallback is None or not callable(structured):
                raise
            value = await structured(prompt, schema, **kwargs)
            if not isinstance(value, dict):
                raise ValueError("fallback structured response must be a JSON object")
            return value

    async def stream(self, prompt: str, **kwargs: Any) -> AsyncIterator[str]:
        # V1 is request/response. Preserve the protocol by yielding the completed
        # response as one chunk; a future transport can add streamed receipts.
        yield await self.complete(prompt, **kwargs)


__all__ = [
    "durable_cognition_phase",
    "LivingRuntimeChatGPTProvider",
    "LivingRuntimeChatGPTProviderError",
    "LivingRuntimeChatGPTPendingError",
]
