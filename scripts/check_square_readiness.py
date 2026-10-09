#!/usr/bin/env python3
"""Read-only Binance Square activation check (never prints or loads secret values)."""
from __future__ import annotations

import json
import os
from pathlib import Path


def main() -> int:
    root = Path(os.environ.get("CONTENT_APPLICATION_ROOT", "/home/ubuntu/content-agent-state"))
    config_file = Path("/home/ubuntu/.config/ferro/binance-square.env")
    config_text = config_file.read_text(encoding="utf-8") if config_file.exists() else ""
    config = {}
    for line in config_text.splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            name, value = line.split("=", 1)
            config[name.strip()] = value.strip().strip("'\"")
    key_present = (
        bool(os.environ.get("BINANCE_SQUARE_OPENAPI_KEY", "").strip())
        or bool(config.get("BINANCE_SQUARE_OPENAPI_KEY", "").strip())
    )
    state_events = root / "publication" / "events"
    state = {
        "state_root_exists": root.is_dir(),
        "square_publishing_key_configured": key_present,
        "posting_key_config_present": config_file.exists(),
        "consumer_enabled": config.get("CONTENT_BINANCE_SQUARE_ENABLED", os.environ.get("CONTENT_BINANCE_SQUARE_ENABLED", "")) == "1",
        "writes_enabled": config.get("CONTENT_BINANCE_SQUARE_WRITES_ENABLED", os.environ.get("CONTENT_BINANCE_SQUARE_WRITES_ENABLED", "")) == "1",
        "published_event_count": len(list(state_events.glob("*.json"))) if state_events.exists() else 0,
        "subscriber_started": (root / "distribution" / "subscriptions" / "binance_square.json").is_file(),
        "existing_square_success_count": sum(
            1 for file in (root / "distribution" / "success").glob("*.json")
            if json.loads(file.read_text(encoding="utf-8")).get("channel") == "binance_square"
        ),
        "no_secret_values_printed": True,
    }
    print(json.dumps(state, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
