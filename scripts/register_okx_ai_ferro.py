#!/usr/bin/env python3
"""Prepare/create Ferro's OKX.AI ASP identity using Onchain OS v4.6.3+.

The default mode is non-creating: it performs pre-check and listing validation
and prints the exact registration preview.  Identity creation requires
--confirm-create, which is only supplied after the operator has confirmed the
rendered identity + service cards in the conversation.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from wechat_traffic_agent.okx_ai_marketplace import (  # noqa: E402
    DEFAULT_FERRO_SERVICES,
    OKXAIMarketplace,
    OnchainOSCLI,
    ferro_registration_argv,
    ferro_validate_listing_argv,
)


AGENT_NAME = "Ferro"
AGENT_DESCRIPTION = (
    "Persistent AI agent for evidence-backed research, agent/software engineering, "
    "and web automation delivery."
)


def _data(payload: Mapping[str, Any]) -> dict[str, Any]:
    value = payload.get("data")
    return dict(value) if isinstance(value, Mapping) else dict(payload)


def _agent_id(row: Mapping[str, Any]) -> str:
    return str(
        row.get("agentId") or row.get("agent_id") or row.get("id") or ""
    ).strip()


def _agent_name(row: Mapping[str, Any]) -> str:
    return str(row.get("name") or row.get("agentName") or "").strip()


def _created_agent_id(payload: Mapping[str, Any]) -> str:
    data = _data(payload)
    direct = str(data.get("newAgentId") or payload.get("newAgentId") or "").strip()
    if direct:
        return direct
    agent = data.get("agent")
    if isinstance(agent, Mapping):
        return _agent_id(agent)
    return ""


def _upload_url(payload: Mapping[str, Any]) -> str:
    data = _data(payload)
    return str(data.get("url") or payload.get("url") or "").strip()


def _validation_passed(payload: Mapping[str, Any]) -> bool:
    data = _data(payload)
    if "pass" in data:
        return bool(data["pass"])
    return bool(payload.get("pass"))


def _write_receipt(state_root: Path, payload: Mapping[str, Any]) -> Path:
    path = state_root / "application" / "okx_ai_marketplace" / "registration.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--state-root",
        default=os.environ.get(
            "CONTENT_APPLICATION_ROOT", "/home/ubuntu/content-agent-state"
        ),
    )
    parser.add_argument("--avatar-file", type=Path)
    parser.add_argument("--preferred-language", default="zh-CN")
    parser.add_argument("--consent-key")
    parser.add_argument("--confirm-create", action="store_true")
    parser.add_argument("--activate", action="store_true")
    args = parser.parse_args()

    state_root = Path(args.state_root)
    cli = OnchainOSCLI(
        binary=os.environ.get(
            "CONTENT_OKX_AI_ONCHAINOS_BIN",
            "/home/ubuntu/.local/bin/onchainos",
        )
    )
    market = OKXAIMarketplace(
        state_root=state_root,
        cli=cli,
        enabled=True,
        writes_enabled=True,
    )

    owned = market.owned_asps()
    existing = [
        row for row in owned if _agent_name(row).casefold() == AGENT_NAME.casefold()
    ]
    if len(existing) > 1:
        raise RuntimeError("multiple Ferro ASP identities exist; choose one explicitly")

    if existing:
        asp_id = _agent_id(existing[0])
        activation = None
        if args.activate:
            activation = cli.run(
                "agent", "activate",
                "--agent-id", asp_id,
                "--preferred-language", args.preferred_language,
            ).payload
        services = market.service_list(agent_id=asp_id)
        receipt = {
            "schema_version": "ferro-okx-ai-registration.v2",
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "status": "EXISTING",
            "agent_id": asp_id,
            "services": services,
            "activation": activation,
        }
        path = _write_receipt(state_root, receipt)
        print(json.dumps({**receipt, "receipt": str(path)}, ensure_ascii=False))
        return 0

    precheck_args = ["agent", "pre-check", "--role", "asp"]
    if args.consent_key:
        precheck_args.extend(["--consent-key", args.consent_key])
    precheck = cli.run(*precheck_args).payload
    pre_data = _data(precheck)
    consent = pre_data.get("consent")
    if isinstance(consent, Mapping) and consent:
        receipt = {
            "schema_version": "ferro-okx-ai-registration.v2",
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "status": "CONSENT_REQUIRED",
            "consent": dict(consent),
        }
        path = _write_receipt(state_root, receipt)
        print(json.dumps({**receipt, "receipt": str(path)}, ensure_ascii=False))
        return 2
    if not bool(pre_data.get("canCreate")):
        receipt = {
            "schema_version": "ferro-okx-ai-registration.v2",
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "status": "PRECHECK_BLOCKED",
            "reason": pre_data.get("reason"),
            "existing_same_role": pre_data.get("existingSameRole"),
        }
        path = _write_receipt(state_root, receipt)
        print(json.dumps({**receipt, "receipt": str(path)}, ensure_ascii=False))
        return 3

    validation = cli.run(*ferro_validate_listing_argv()).payload
    if not _validation_passed(validation):
        receipt = {
            "schema_version": "ferro-okx-ai-registration.v2",
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "status": "LISTING_VALIDATION_FAILED",
            "validation": validation,
        }
        path = _write_receipt(state_root, receipt)
        print(json.dumps({**receipt, "receipt": str(path)}, ensure_ascii=False))
        return 4

    preview = {
        "name": AGENT_NAME,
        "description": AGENT_DESCRIPTION,
        "services": list(DEFAULT_FERRO_SERVICES),
        "avatar_file": str(args.avatar_file) if args.avatar_file else None,
    }
    if not args.confirm_create:
        receipt = {
            "schema_version": "ferro-okx-ai-registration.v2",
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "status": "READY_FOR_FINAL_CONFIRMATION",
            "preview": preview,
            "validation": validation,
        }
        path = _write_receipt(state_root, receipt)
        print(json.dumps({**receipt, "receipt": str(path)}, ensure_ascii=False))
        return 0

    if args.avatar_file is None:
        raise ValueError("--avatar-file is required when --confirm-create is used")
    avatar = args.avatar_file.expanduser().resolve()
    if avatar.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise ValueError("avatar must be PNG, JPEG, or WebP")
    if not avatar.is_file():
        raise FileNotFoundError(str(avatar))

    upload = cli.run("agent", "upload", "--file", str(avatar)).payload
    picture_url = _upload_url(upload)
    if not picture_url:
        raise RuntimeError("agent upload did not return a picture URL")

    created = cli.run(
        *ferro_registration_argv(picture_url=picture_url)
    ).payload
    asp_id = _created_agent_id(created)
    if not asp_id:
        raise RuntimeError("agent create succeeded without a resolvable Agent ID")

    activation = None
    if args.activate:
        activation = cli.run(
            "agent", "activate",
            "--agent-id", asp_id,
            "--preferred-language", args.preferred_language,
        ).payload

    receipt = {
        "schema_version": "ferro-okx-ai-registration.v2",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "status": "CREATED",
        "agent_id": asp_id,
        "picture_url": picture_url,
        "create_response": created,
        "activation": activation,
    }
    path = _write_receipt(state_root, receipt)
    print(json.dumps({**receipt, "receipt": str(path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
