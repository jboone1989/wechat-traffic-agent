"""Operator import and read-only reporting of real channel measurements."""
import argparse
from datetime import datetime
import json
from pathlib import Path
from .channel_evidence import ChannelEvidence, ChannelEvidenceStore
from .traffic_learning import SearchDemandObservation, SearchDemandStore


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    record = commands.add_parser("import")
    record.add_argument("--input", type=Path, required=True)
    search_record = commands.add_parser("import-search")
    search_record.add_argument("--input", type=Path, required=True)
    report = commands.add_parser("report")
    report.add_argument("--channel", required=True)
    report.add_argument("--account", required=True)
    report.add_argument("--as-of", required=True)
    report.add_argument("--dimension", choices=["topic", "title_version", "structure_version", "strategy_version"], required=True)
    args = parser.parse_args(argv)
    store = ChannelEvidenceStore(args.root)
    try:
        if args.command == "import":
            row = ChannelEvidence.model_validate_json(args.input.read_text())
            result = {"status": "RECORDED", "path": str(store.record(row))}
        elif args.command == "import-search":
            row = SearchDemandObservation.model_validate_json(args.input.read_text())
            result = {"status": "RECORDED", "path": str(SearchDemandStore(args.root).record(row)),
                      "source": row.source, "query": row.query}
        else:
            result = store.report(channel=args.channel, account=args.account,
                                  cutoff=datetime.fromisoformat(args.as_of.replace("Z", "+00:00")), dimension=args.dimension)
    except (ValueError, OSError) as exc:
        print(json.dumps({"status": "ERROR", "message": str(exc)}))
        return 1
    print(json.dumps(result, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
