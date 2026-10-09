#!/usr/bin/env python3
"""Prepare/review native copy without posting. Review records a real editorial
decision; structural validation alone never establishes semantic quality.
"""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from wechat_traffic_agent.platforms.binance_square_editorial import SquareDraftStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-root", default=os.environ.get("CONTENT_APPLICATION_ROOT", "/home/ubuntu/content-agent-state"))
    actions = parser.add_subparsers(dest="action", required=True)
    create = actions.add_parser("create")
    create.add_argument("--document", type=Path, required=True)
    create.add_argument("--evidence", type=Path, required=True)
    review = actions.add_parser("review")
    review.add_argument("draft_id")
    review.add_argument("--reviewer", required=True)
    review.add_argument("--evidence-check", required=True)
    args = parser.parse_args()
    store = SquareDraftStore(args.state_root)
    if args.action == "create":
        doc = json.loads(args.document.read_text(encoding="utf-8"))
        result = store.create(**doc, evidence=json.loads(args.evidence.read_text(encoding="utf-8")))
    else:
        result = store.review(args.draft_id, reviewer=args.reviewer, evidence_check=args.evidence_check)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
