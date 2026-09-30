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

_ACTIVATION_TEMPLATE = """FERRO_CORTEX_ACTIVATE
request_id={request_id}

Use the connected LivingRuntime Remote Beta 2 app as the authoritative cognition source.
1. Read exactly this request_id from LivingRuntime Remote.
2. If it is already COMPLETED or TIMED_OUT, stop.
3. Otherwise claim it with watcher_id="chatgpt-web-ferro-cortex" and claim_seconds=300.
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
    if last_started and now - last_started < max(10, cooldown):
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


def _composer_present(browser: Any) -> bool:
    return bool(browser.evaluate(
        """(() => Boolean(
          document.querySelector('#prompt-textarea') ||
          document.querySelector('textarea[placeholder]') ||
          document.querySelector('[contenteditable="true"][data-lexical-editor="true"]') ||
          document.querySelector('main [contenteditable="true"]')
        ))()"""
    ))


def _login_required(browser: Any) -> bool:
    return bool(browser.evaluate(
        """(() => [...document.querySelectorAll('button,a,[role="button"]')].some(el => {
          const text = (el.innerText || el.textContent || '').trim().toLowerCase();
          return text === 'log in' && Boolean(el.getClientRects().length);
        }))()"""
    ))


def _focus_composer(browser: Any) -> bool:
    return bool(browser.evaluate(
        """(() => {
          const el =
            document.querySelector('#prompt-textarea') ||
            document.querySelector('textarea[placeholder]') ||
            document.querySelector('[contenteditable="true"][data-lexical-editor="true"]') ||
            document.querySelector('main [contenteditable="true"]');
          if (!el) return false;
          el.focus();
          return document.activeElement === el || el.contains(document.activeElement);
        })()"""
    ))


def _composer_text(browser: Any) -> str:
    return str(browser.evaluate(
        """(() => {
          const el =
            document.querySelector('#prompt-textarea') ||
            document.querySelector('textarea[placeholder]') ||
            document.querySelector('[contenteditable="true"][data-lexical-editor="true"]') ||
            document.querySelector('main [contenteditable="true"]');
          if (!el) return '';
          return typeof el.value === 'string' ? el.value : (el.innerText || el.textContent || '');
        })()"""
    ) or "")


def _user_message_visible(browser: Any, request_id: str) -> bool:
    marker = json.dumps(request_id)
    return bool(browser.evaluate(
        f"""(() => [...document.querySelectorAll('[data-message-author-role="user"], article')].some(
          el => (el.innerText || el.textContent || '').includes({marker})
        ))()"""
    ))


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
        state = {
            "schema_version": 1,
            "status": "RUNNING",
            "request_id": request_id,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "started_at_epoch": started,
        }
        _write_json(state_path, state)

        browser = None
        try:
            from .chatgpt_cortex_browser_control import ChatGPTCortexBrowserControl

            cortex_browser = ChatGPTCortexBrowserControl(state_root=state_root)
            os.environ["BU_CDP_URL"] = cortex_browser.cdp_url()
            os.environ["BU_NAME"] = (
                "ferro_chatgpt_cortex_" + cortex_browser.account_id[-20:]
            )[:64]
            from browser_harness.admin import ensure_daemon
            from jev_ultrafast.browser import Browser

            ensure_daemon()
            browser = Browser(_DEFAULT_URL)
            deadline = time.monotonic() + 25.0
            login_required = False
            while time.monotonic() < deadline:
                try:
                    if _login_required(browser):
                        login_required = True
                        break
                    if _composer_present(browser):
                        break
                except (OSError, RuntimeError, ValueError):
                    pass
                time.sleep(0.25)
            if login_required:
                url = str(browser.evaluate("location.href") or "")
                result = {
                    **state,
                    "status": "LOGIN_REQUIRED",
                    "url": url[:240],
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                }
                _write_json(state_path, result)
                _append_event(events_path, {**result, "at": result["finished_at"]})
                browser.close()
                return result
            if time.monotonic() >= deadline:
                url = str(browser.evaluate("location.href") or "")
                result = {
                    **state,
                    "status": "LOGIN_REQUIRED",
                    "url": url[:240],
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                }
                _write_json(state_path, result)
                _append_event(events_path, {**result, "at": result["finished_at"]})
                browser.close()
                return result
            if _login_required(browser):
                url = str(browser.evaluate("location.href") or "")
                result = {
                    **state,
                    "status": "LOGIN_REQUIRED",
                    "url": url[:240],
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                }
                _write_json(state_path, result)
                _append_event(events_path, {**result, "at": result["finished_at"]})
                browser.close()
                return result

            if not _focus_composer(browser):
                raise RuntimeError("ChatGPT composer could not be focused")
            prompt = activation_prompt(request_id)
            browser.call("Input.insertText", text=prompt)
            rendered = _composer_text(browser)
            if request_id not in rendered:
                raise RuntimeError("ChatGPT activation fill verification failed")

            for kind in ("keyDown", "keyUp"):
                browser.call(
                    "Input.dispatchKeyEvent",
                    type=kind,
                    key="Enter",
                    code="Enter",
                )

            verify_deadline = time.monotonic() + 12.0
            sent = False
            while time.monotonic() < verify_deadline:
                try:
                    if _user_message_visible(browser, request_id):
                        sent = True
                        break
                except (OSError, RuntimeError, ValueError):
                    pass
                time.sleep(0.2)
            if not sent:
                raise RuntimeError("ChatGPT activation message was not observed after send")

            result = {
                **state,
                "status": "ACTIVATED",
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "elapsed_ms": round((time.time() - started) * 1000),
                "target_id": str(getattr(browser, "target", "") or "")[:160],
            }
            _write_json(state_path, result)
            _append_event(events_path, {**result, "at": result["finished_at"]})
            # Deliberately leave the target alive. The ChatGPT turn may continue
            # using LivingRuntime Remote after this activator process exits.
            return result
        except (OSError, RuntimeError, ValueError) as exc:
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
            if browser is not None:
                try:
                    browser.close()
                except (OSError, RuntimeError, ValueError):
                    pass
            return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request-id", required=True)
    args = parser.parse_args()
    request_id = str(args.request_id or "").strip()
    activation_prompt(request_id)
    result = _send_activation(request_id, state_root=_state_root())
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") in {"ACTIVATED", "COALESCED_ACTIVE"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
