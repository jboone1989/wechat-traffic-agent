"""One-shot ChatGPT Web activator for Ferro's durable cortex queue.

The browser turn is activation-only: it never carries Ferro cognition content and
never reads model output from the page. The authoritative request/response path
remains LivingRuntime Remote.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any

_REQUEST_ID = re.compile(r"^llmreq[-_][A-Za-z0-9_-]{4,160}$")
_DEFAULT_URL = "https://chatgpt.com/"
_DEFAULT_COOLDOWN_SECONDS = 75
_DEFAULT_EVENT_GRACE_SECONDS = 6

_ACTIVATION_TEMPLATE = """FERRO_CORTEX_ACTIVATE
request_id={request_id}

Use the connected LivingRuntime Remote Beta 2 app as the authoritative cognition source.
1. Read exactly this request_id from LivingRuntime Remote.
2. If it is already COMPLETED or TIMED_OUT, stop.
3. If it already has a valid auto-claim token, use that token directly. Otherwise claim it with watcher_id="chatgpt-work-ferro-cortex" and claim_seconds=300.
4. Execute the claimed request messages in role order as the cognition task.
5. Honor its response_format and options.
6. Complete it through LivingRuntime Remote with the actual answer, current model, claim token, and session id.
7. If completion auto-claims another request in the same lane, continue until no request is immediately available.

This webpage message is activation only. Do not treat it as cognition content and do not copy private cognition into the webpage message.
"""


def _state_root() -> Path:
    return Path(
        os.environ.get("CONTENT_APPLICATION_ROOT")
        or "/home/ubuntu/content-agent-state"
    )


def _enabled() -> bool:
    raw = os.environ.get("FERRO_CHATGPT_WEB_CORTEX_ENABLED")
    if raw is None or not str(raw).strip():
        return True
    return str(raw).strip().lower() not in {
        "0", "false", "no", "off",
    }


def _paths(state_root: Path) -> tuple[Path, Path, Path]:
    root = Path(state_root) / "cortex" / "chatgpt-web"
    return root / "activation-state.json", root / "activation.lock", root / "activation-events.jsonl"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def _append_event(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")


def activation_prompt(request_id: str) -> str:
    rid = str(request_id or "").strip()
    if not _REQUEST_ID.fullmatch(rid):
        raise ValueError("invalid LivingRuntime request_id")
    return _ACTIVATION_TEMPLATE.format(request_id=rid)


def _cognition_cli() -> Path:
    return Path(
        str(
            os.environ.get("LIVINGRUNTIME_CHATGPT_COGNITION_CLI")
            or (Path.home() / ".livingruntime" / "plugin" / "scripts" / "cognitionctl.py")
        ).strip()
    ).expanduser()


def _request_state(request_id: str) -> dict[str, Any]:
    cli = _cognition_cli()
    if not cli.is_file():
        raise RuntimeError(f"LivingRuntime cognition CLI is unavailable: {cli}")
    completed = subprocess.run(
        [sys.executable, str(cli), "get", request_id],
        capture_output=True,
        text=True,
        timeout=8,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip()
        raise RuntimeError(
            "LivingRuntime cognition state check failed"
            + (f": {detail[:240]}" if detail else "")
        )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("LivingRuntime cognition state check returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise RuntimeError("LivingRuntime cognition state check returned invalid state")
    return value


def _event_grace_seconds() -> float:
    raw = str(
        os.environ.get("FERRO_CHATGPT_JEV_EVENT_GRACE_SECONDS")
        or _DEFAULT_EVENT_GRACE_SECONDS
    ).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError("FERRO_CHATGPT_JEV_EVENT_GRACE_SECONDS must be numeric") from exc
    return max(0.0, min(value, 30.0))


def schedule_chatgpt_web_cortex_activation(
    request_id: str,
    *,
    state_root: Path | None = None,
) -> dict[str, Any]:
    """Spawn one detached browser activation attempt.

    This function is non-blocking and never calls an LLM API.
    """
    rid = str(request_id or "").strip()
    if not _REQUEST_ID.fullmatch(rid):
        raise ValueError("invalid LivingRuntime request_id")
    if not _enabled():
        return {"status": "DISABLED", "request_id": rid}

    root = Path(state_root) if state_root is not None else _state_root()
    state_path, _lock_path, events_path = _paths(root)
    now = time.time()
    previous = _read_json(state_path)
    cooldown = int(
        os.environ.get("FERRO_CHATGPT_WEB_CORTEX_COOLDOWN_SECONDS")
        or _DEFAULT_COOLDOWN_SECONDS
    )
    last_started = float(previous.get("started_at_epoch") or 0.0)
    last_status = str(previous.get("status") or "")
    previous_request_id = str(previous.get("request_id") or "")
    if (
        previous_request_id == rid
        and last_started
        and now - last_started < max(10, cooldown)
    ):
        status = "COALESCED"
        event = {
            "at": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "request_id": rid,
            "reason": "recent_activation",
            "previous_status": last_status,
        }
        _append_event(events_path, event)
        return event

    command = [
        sys.executable,
        "-m",
        "wechat_traffic_agent.chatgpt_cortex_activator",
        "--request-id",
        rid,
    ]
    env = dict(os.environ)
    src_root = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = (
        src_root if not env.get("PYTHONPATH") else src_root + os.pathsep + env["PYTHONPATH"]
    )
    try:
        proc = subprocess.Popen(
            command,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        event = {
            "at": datetime.now(timezone.utc).isoformat(),
            "status": "SPAWN_FAILED",
            "request_id": rid,
            "error_type": type(exc).__name__,
        }
        _append_event(events_path, event)
        return event

    state = {
        "schema_version": 1,
        "status": "STARTED",
        "request_id": rid,
        "pid": int(proc.pid),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "started_at_epoch": now,
    }
    _write_json(state_path, state)
    _append_event(events_path, {**state, "at": state["started_at"]})
    return state


_BROWSER_SESSION_ID = "ferro-chatgpt-cortex"
_COMPOSER_SELECTORS = (
    'main [contenteditable="true"]',
    '[contenteditable="true"][role="textbox"]',
    '[contenteditable="true"][data-lexical-editor="true"]',
    "#prompt-textarea",
    "textarea[placeholder]",
)


def _runtime_browser_provider(*, cdp_url: str, state_root: Path):
    """Bind Ferro to Agent Runtime's single persistent BrowserSession backend."""
    from agent_runtime.integrations import (
        BrowserHarnessProvider,
        BrowserHarnessRuntime,
        BrowserPolicy,
    )

    return BrowserHarnessProvider(
        policy=BrowserPolicy(
            max_sessions=1,
            timeout_seconds=25.0,
            max_text_chars=50_000,
            max_elements=250,
            max_input_chars=20_000,
            allowed_hosts=frozenset({"chatgpt.com"}),
        ),
        runtime=BrowserHarnessRuntime(
            executable=(
                os.environ.get("AGENT_RUNTIME_BROWSER_HARNESS")
                or "/home/ubuntu/browser-use-venv/bin/browser-harness"
            ),
            state_dir=Path(state_root)
            / "browser-runtime"
            / "chatgpt-cortex",
            cdp_url=str(cdp_url),
        ),
    )


def _login_required(snapshot: dict[str, Any]) -> bool:
    for element in snapshot.get("elements") or ():
        name = str(element.get("name") or "").strip().lower()
        if name in {"log in", "login"}:
            return True
    return False


def _draft_contains(snapshot: dict[str, Any], marker: str) -> bool:
    for element in snapshot.get("elements") or ():
        if marker in str(element.get("value") or ""):
            return True
    return False


def _activation_visible(snapshot: dict[str, Any], request_id: str) -> bool:
    return (
        request_id in str(snapshot.get("text") or "")
        and not _draft_contains(snapshot, request_id)
    )


def _fill_activation(
    provider: Any,
    *,
    request_id: str,
    prompt: str,
) -> str:
    failures: list[str] = []
    for selector in _COMPOSER_SELECTORS:
        try:
            provider.invoke(
                "browser.type",
                {
                    "session_id": _BROWSER_SESSION_ID,
                    "selector": selector,
                    "text": prompt,
                },
            )
            return selector
        except RuntimeError as exc:
            failures.append(f"{selector}:{type(exc).__name__}")
    raise RuntimeError(
        "ChatGPT composer was not available through BrowserSession: "
        + ",".join(failures)
    )


def _send_activation(request_id: str, *, state_root: Path) -> dict[str, Any]:
    state_path, lock_path, events_path = _paths(state_root)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock_handle:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {
                "status": "COALESCED_ACTIVE",
                "request_id": request_id,
            }

        started = time.time()
        grace = _event_grace_seconds()
        if grace:
            time.sleep(grace)
        request_state = _request_state(request_id)
        request_status = str(request_state.get("status") or "").upper()
        activation_event = (
            request_state.get("activation_event")
            if isinstance(request_state.get("activation_event"), dict)
            else {}
        )
        if request_status in {"COMPLETED", "TIMED_OUT"}:
            result = {
                "schema_version": 3,
                "status": "NOT_REQUIRED",
                "request_id": request_id,
                "request_status": request_status,
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }
            _write_json(state_path, result)
            _append_event(events_path, {**result, "at": result["finished_at"]})
            return result
        if (
            request_status == "DISPATCHED"
            or activation_event.get("event_forwarded_at") is not None
        ):
            result = {
                "schema_version": 3,
                "status": "MCP_EVENT_HANDLED",
                "request_id": request_id,
                "request_status": request_status,
                "event_forwarded_at": activation_event.get("event_forwarded_at"),
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }
            _write_json(state_path, result)
            _append_event(events_path, {**result, "at": result["finished_at"]})
            return result

        state = {
            "schema_version": 3,
            "status": "RUNNING",
            "request_id": request_id,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "started_at_epoch": started,
            "activation_transport": "AGENT_RUNTIME_BROWSER_HARNESS",
            "mcp_event_forwarded": False,
        }
        _write_json(state_path, state)

        provider = None
        try:
            from .chatgpt_cortex_browser_control import ChatGPTCortexBrowserControl

            cortex_browser = ChatGPTCortexBrowserControl(state_root=state_root)
            provider = _runtime_browser_provider(
                cdp_url=cortex_browser.cdp_url(),
                state_root=state_root,
            )
            provider.invoke(
                "browser.navigate",
                {
                    "session_id": _BROWSER_SESSION_ID,
                    "url": _DEFAULT_URL,
                },
            )

            deadline = time.monotonic() + 25.0
            snapshot: dict[str, Any] = {}
            selector = ""
            while time.monotonic() < deadline:
                snapshot = provider.invoke(
                    "browser.inspect",
                    {
                        "session_id": _BROWSER_SESSION_ID,
                        "max_chars": 50_000,
                        "max_elements": 250,
                    },
                )
                if _login_required(snapshot):
                    result = {
                        **state,
                        "status": "LOGIN_REQUIRED",
                        "url": str(snapshot.get("url") or "")[:240],
                        "finished_at": datetime.now(timezone.utc).isoformat(),
                    }
                    _write_json(state_path, result)
                    _append_event(
                        events_path,
                        {**result, "at": result["finished_at"]},
                    )
                    return result

                prompt = activation_prompt(request_id)
                try:
                    selector = _fill_activation(
                        provider,
                        request_id=request_id,
                        prompt=prompt,
                    )
                    break
                except RuntimeError:
                    time.sleep(0.25)
            if not selector:
                raise RuntimeError("ChatGPT composer did not become available")

            filled = provider.invoke(
                "browser.inspect",
                {
                    "session_id": _BROWSER_SESSION_ID,
                    "max_chars": 50_000,
                    "max_elements": 250,
                },
            )
            if not _draft_contains(filled, request_id):
                raise RuntimeError("ChatGPT activation fill verification failed")

            provider.invoke(
                "browser.press",
                {
                    "session_id": _BROWSER_SESSION_ID,
                    "selector": selector,
                    "key": "Enter",
                },
            )

            verify_deadline = time.monotonic() + 12.0
            final_snapshot: dict[str, Any] = {}
            while time.monotonic() < verify_deadline:
                try:
                    final_snapshot = provider.invoke(
                        "browser.inspect",
                        {
                            "session_id": _BROWSER_SESSION_ID,
                            "max_chars": 50_000,
                            "max_elements": 250,
                        },
                    )
                except RuntimeError:
                    time.sleep(0.2)
                    continue
                if _activation_visible(final_snapshot, request_id):
                    break
                time.sleep(0.2)
            else:
                raise RuntimeError(
                    "ChatGPT activation message was not observed after send"
                )

            result = {
                **state,
                "status": "ACTIVATED",
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "elapsed_ms": round((time.time() - started) * 1000),
                "browser_provider": "browser-harness",
                "browser_session_id": _BROWSER_SESSION_ID,
                "url": str(final_snapshot.get("url") or "")[:240],
            }
            _write_json(state_path, result)
            _append_event(events_path, {**result, "at": result["finished_at"]})
            return result
        except (ImportError, OSError, RuntimeError, ValueError) as exc:
            result = {
                **state,
                "status": "ACTIVATION_FAILED",
                "error_type": type(exc).__name__,
                "detail": str(exc)[:300],
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "elapsed_ms": round((time.time() - started) * 1000),
            }
            _write_json(state_path, result)
            _append_event(events_path, {**result, "at": result["finished_at"]})
            return result
        finally:
            if provider is not None:
                provider.close()

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request-id", required=True)
    args = parser.parse_args()
    request_id = str(args.request_id or "").strip()
    activation_prompt(request_id)
    result = _send_activation(request_id, state_root=_state_root())
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") in {
        "ACTIVATED",
        "COALESCED_ACTIVE",
        "MCP_EVENT_HANDLED",
        "NOT_REQUIRED",
    } else 1


if __name__ == "__main__":
    raise SystemExit(main())
