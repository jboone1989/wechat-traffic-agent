from __future__ import annotations

import asyncio
import json
import math
import os
from pathlib import Path
import sys
from typing import Any, AsyncIterator


class LivingRuntimeChatGPTProviderError(RuntimeError):
    pass


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
        timeout_seconds: float = 120.0,
    ) -> None:
        self.fallback = fallback
        self.cli_path = Path(cli_path)
        self.enabled = bool(enabled)
        self.agent_id = str(agent_id or "ferro").strip()
        timeout = float(timeout_seconds)
        if not math.isfinite(timeout) or not 10.0 <= timeout <= 180.0:
            raise ValueError("LivingRuntime ChatGPT timeout must be within 10..180 seconds")
        self.timeout_seconds = timeout
        self.last_provenance: dict[str, Any] | None = None

    @classmethod
    def from_environment(cls, *, fallback: Any | None = None) -> "LivingRuntimeChatGPTProvider":
        raw_enabled = str(
            os.environ.get("LIVINGRUNTIME_CHATGPT_PROVIDER_ENABLED") or ""
        ).strip().lower()
        enabled = raw_enabled in {"1", "true", "yes", "on"}
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
            os.environ.get("LIVINGRUNTIME_CHATGPT_TIMEOUT_SECONDS") or "120"
        ).strip()
        try:
            timeout = float(raw_timeout)
        except ValueError as exc:
            raise ValueError(
                "LIVINGRUNTIME_CHATGPT_TIMEOUT_SECONDS must be numeric"
            ) from exc
        return cls(
            fallback=fallback,
            cli_path=cli,
            enabled=enabled,
            agent_id=agent_id,
            timeout_seconds=timeout,
        )

    @property
    def available(self) -> bool:
        return self.enabled and self.cli_path.is_file()

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

        payload = {
            "agent_id": self.agent_id,
            "purpose": task_class[:512] or "content_cognition",
            "messages": messages,
            "response_format": response_format,
            "options": options,
            "metadata": metadata,
            "timeout_seconds": max(5, min(int(math.ceil(self.timeout_seconds)), 180)),
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

        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(self.cli_path),
            "call",
            "--wait-timeout-seconds",
            str(max(1, int(math.ceil(self.timeout_seconds)))),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(body),
                timeout=self.timeout_seconds + 3.0,
            )
        except asyncio.TimeoutError as exc:
            proc.kill()
            await proc.wait()
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
    "LivingRuntimeChatGPTProvider",
    "LivingRuntimeChatGPTProviderError",
]
