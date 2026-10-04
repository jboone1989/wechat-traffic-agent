#!/usr/bin/env python3
"""Independent reflection worker; writes receipts, never shared mutable state."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import signal
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wechat_traffic_agent.behavioral_state import build_behavioral_state, build_phenotype_prior
from wechat_traffic_agent.runtime_backend import load_independent_ferro_cognition_runtime
from wechat_traffic_agent.state_events import FerroStateClient
from wechat_traffic_agent.task_scheduler import IndependentTaskScheduler
from wechat_traffic_agent.worker_runtime import WorkerHeartbeat


INTERVAL_SECONDS = 6 * 3600


def _state_root() -> Path:
    raw = os.environ.get("CONTENT_APPLICATION_ROOT")
    return Path(raw).expanduser().resolve() if raw else (ROOT / "state" / "content_application").resolve()


def _read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_receipt(state_root: Path, now: datetime, text: str, evidence_refs: list[str]) -> Path:
    root = state_root / "application" / "reflection_results"
    root.mkdir(parents=True, exist_ok=True)
    digest = sha256((now.isoformat() + text).encode("utf-8")).hexdigest()[:24]
    path = root / f"reflection-{digest}.json"
    payload = {
        "schema_version": "ferro-independent-reflection.v1",
        "status": "PENDING_CONTENT_MIND_COMMIT",
        "created_at": now.isoformat(),
        "target_date": now.date().isoformat(),
        "reflection_text": text,
        "evidence_refs": evidence_refs,
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def main() -> int:
    state_root = _state_root()
    scheduler = IndependentTaskScheduler(state_root)
    heartbeat = WorkerHeartbeat(state_root, "reflect")
    state_client = FerroStateClient(state_root)
    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    def behavioral_context():
        behavioral_state = build_behavioral_state(state_client.behavioral_projection())
        return {
            "phenotype_prior": build_phenotype_prior(behavioral_state),
            "behavioral_state": behavioral_state,
        }

    cognition = load_independent_ferro_cognition_runtime(
        state_root=state_root,
        behavioral_context_provider=behavioral_context,
    )

    if not scheduler.pending("REFLECT"):
        scheduler.schedule(
            "REFLECT",
            datetime.now(timezone.utc) + timedelta(minutes=5),
            reason="initial independent reflection",
        )

    heartbeat.write(status="RUNNING")
    try:
        while not stopping:
            now = datetime.now(timezone.utc)
            wake = scheduler.claim_due("REFLECT", now)
            if wake is None:
                heartbeat.write(status="RUNNING", detail="waiting")
                time.sleep(1.0)
                continue

            heartbeat.write(status="RUNNING", detail="reflecting")
            content_result = _read(
                state_root / "application" / "workers" / "content-cognition-result.json"
            )
            outcome_result = _read(
                state_root / "application" / "workers" / "check-outcomes-result.json"
            )
            resource_result = _read(
                state_root / "application" / "workers" / "resource-review-result.json"
            )
            posting_status = _read(state_root / "content" / "x" / "posting_goal" / "status.json")
            prompt = (
                "REFLECT: Write a concise internal reflection about what materially changed, "
                "what failed, and what should change next. This is not a daily diary. If no "
                "meaningful change occurred, say so explicitly. Do not invent facts. "
                "Return plain text, <=1200 characters.\n"
                + json.dumps(
                    {
                        "content_result": content_result,
                        "outcome_result": outcome_result,
                        "resource_result": resource_result,
                        "posting_status": posting_status,
                    },
                    ensure_ascii=False,
                    default=str,
                )[:18000]
            )
            try:
                text = __import__("asyncio").run(
                    cognition.cognitive_probe(prompt, max_tokens=500)
                ).strip()
                receipt = _write_receipt(
                    state_root,
                    now,
                    text[:4000],
                    [str(ref) for ref in wake.relevant_refs if str(ref)],
                )
                status = "OK"
                detail = receipt.name
            except Exception as exc:
                status = "ERROR"
                detail = f"{type(exc).__name__}: {str(exc)[:300]}"
                scheduler.schedule(
                    "REFLECT",
                    now + timedelta(minutes=15),
                    reason="retry failed independent reflection",
                    relevant_refs=tuple(wake.relevant_refs),
                )

            scheduler.schedule(
                "REFLECT",
                datetime.now(timezone.utc) + timedelta(seconds=INTERVAL_SECONDS),
                reason="periodic independent reflection",
            )
            heartbeat.write(status="RUNNING", detail=f"{status}:{detail}")
        return 0
    finally:
        heartbeat.write(status="STOPPED")


if __name__ == "__main__":
    raise SystemExit(main())
