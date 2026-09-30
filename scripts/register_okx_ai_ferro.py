#!/usr/bin/env python3
"""Register or reuse Ferro's OKX.AI A2A ASP identity and submit it for listing."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from wechat_traffic_agent.okx_ai_marketplace import (  # noqa: E402
    OKXAIMarketplace,
    OnchainOSCLI,
    ferro_registration_argv,
)


def _agent_id(row: dict) -> str:
    return str(row.get("agentId") or row.get("agent_id") or row.get("id") or "").strip()


def _agent_name(row: dict) -> str:
    return str(row.get("name") or row.get("agentName") or "").strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--state-root",
        default=os.environ.get("CONTENT_STATE_ROOT", "/home/ubuntu/content-agent-state"),
    )
    parser.add_argument(
        "--picture-url",
        default=os.environ.get("CONTENT_OKX_AI_PICTURE_URL", ""),
    )
    parser.add_argument("--preferred-language", default="en-US")
    parser.add_argument("--no-activate", action="store_true")
    args = parser.parse_args()

    state_root = Path(args.state_root)
    cli = OnchainOSCLI()
    market = OKXAIMarketplace(
        state_root=state_root,
        cli=cli,
        enabled=True,
        writes_enabled=True,
    )

    existing = [
        row for row in market.owned_asps()
        if _agent_name(row).casefold() == "ferro"
    ]
    created = False
    create_response = None
    if existing:
        asp_id = _agent_id(existing[0])
    else:
        if not args.picture_url:
            raise SystemExit(
                "CONTENT_OKX_AI_PICTURE_URL or --picture-url is required for first registration"
            )
        create_response = cli.run(
            *ferro_registration_argv(picture_url=args.picture_url)
        ).payload
        created = True
        owned = [
            row for row in market.owned_asps()
            if _agent_name(row).casefold() == "ferro"
        ]
        if len(owned) != 1:
            raise RuntimeError(
                "Ferro ASP creation returned but identity could not be uniquely resolved"
            )
        asp_id = _agent_id(owned[0])

    services = market.service_list(agent_id=asp_id)
    activation = None
    if not args.no_activate:
        activation = cli.run(
            "agent", "activate",
            "--agent-id", asp_id,
            "--preferred-language", args.preferred_language,
        ).payload

    receipt = {
        "schema_version": "ferro-okx-ai-registration.v1",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "agent_id": asp_id,
        "created": created,
        "create_response": create_response,
        "services": services,
        "activation": activation,
    }
    path = state_root / "application" / "okx_ai_marketplace" / "registration.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "status": "SUBMITTED_FOR_LISTING" if activation is not None else "REGISTERED",
        "agent_id": asp_id,
        "service_count": len(services),
        "receipt": str(path),
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
