"""Synchronous OpenAI Responses provider for Ferro's public HTTP chat."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
from typing import Any, Mapping
import urllib.error
import urllib.request


class OpenAIPublicChatError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = True) -> None:
        super().__init__(str(code))
        self.code = str(code)
        self.retryable = bool(retryable)


@dataclass
class OpenAIResponsesProvider:
    api_key: str = field(repr=False)
    model: str = "gpt-5.6-luna"
    base_url: str = "https://api.openai.com/v1"
    timeout_seconds: float = 18.0
    reasoning_effort: str = "none"
    last_metadata: dict[str, Any] = field(default_factory=dict, init=False, repr=False)

    async def complete(self, prompt: str, **kwargs: Any) -> str:
        return await asyncio.to_thread(self._complete_sync, prompt, kwargs)

    def _complete_sync(self, prompt: str, kwargs: dict[str, Any]) -> str:
        requested = int(kwargs.get("max_tokens", 512))
        body: dict[str, Any] = {
            "model": str(kwargs.get("model") or self.model),
            "input": str(prompt),
            "store": False,
            "max_output_tokens": max(64, requested),
            "reasoning": {
                "effort": str(
                    kwargs.get("reasoning_effort")
                    or self.reasoning_effort
                    or "none"
                )
            },
            "text": {"verbosity": "low"},
        }
        if kwargs.get("system"):
            body["instructions"] = str(kwargs["system"])

        request = urllib.request.Request(
            self.base_url.rstrip("/") + "/responses",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        timeout_seconds = max(
            0.1, float(kwargs.get("timeout_seconds") or self.timeout_seconds)
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise OpenAIPublicChatError(
                f"HTTP_{exc.code}",
                retryable=exc.code not in {400, 401, 403},
            ) from exc
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise OpenAIPublicChatError("REQUEST_FAILED") from exc

        if not isinstance(payload, Mapping):
            raise OpenAIPublicChatError("INVALID_RESPONSE")
        status = str(payload.get("status") or "")
        if status == "failed":
            raise OpenAIPublicChatError("RESPONSE_FAILED")
        text = _response_output_text(payload).strip()
        if not text:
            if status == "incomplete":
                raise OpenAIPublicChatError("INCOMPLETE_WITHOUT_TEXT")
            raise OpenAIPublicChatError("EMPTY_RESPONSE")

        usage = payload.get("usage")
        usage = dict(usage) if isinstance(usage, Mapping) else {}
        self.last_metadata = {
            "input_tokens": _nonnegative_int(usage.get("input_tokens")),
            "output_tokens": _nonnegative_int(usage.get("output_tokens")),
            "total_tokens": _nonnegative_int(usage.get("total_tokens")),
            "response_id": str(payload.get("id") or "") or None,
            "status": status or None,
        }
        return text


def _response_output_text(payload: Mapping[str, Any]) -> str:
    shortcut = payload.get("output_text")
    if isinstance(shortcut, str) and shortcut:
        return shortcut
    parts: list[str] = []
    output = payload.get("output")
    if not isinstance(output, list):
        return ""
    for item in output:
        if not isinstance(item, Mapping) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, Mapping):
                continue
            if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                parts.append(str(part["text"]))
    return "".join(parts)


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


__all__ = ["OpenAIPublicChatError", "OpenAIResponsesProvider"]
