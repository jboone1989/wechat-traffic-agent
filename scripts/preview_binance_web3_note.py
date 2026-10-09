#!/usr/bin/env python3
"""Preview researched note without sending it to any platform."""
import json
from pathlib import Path
from publish_binance_web3_evidence_note import SNAPSHOT,build_note
doc=build_note(json.loads(SNAPSHOT.read_text(encoding="utf-8")))
print(json.dumps({"title":doc["title"],"char_count":len(doc["body"]),
                  "body":doc["body"],"evidence_sources":doc["evidence_sources"],
                  "contracts":doc["contracts"],"observed_at":doc["observed_at"]},ensure_ascii=False))
