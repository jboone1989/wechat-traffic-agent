"""Native Square drafts and reviewed-copy outbox, independent of website events."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re

from .binance_square import (
    BinanceSquareClient, MAX_POST_CHARS, POST_URL, SquarePublicationRejected,
    SquarePublicationUnverified, square_enabled, square_writes_enabled,
)
from wechat_traffic_agent.sources.binance_market_structure import analyze
from wechat_traffic_agent.sources.live_cache import live_cache_root
from wechat_traffic_agent.editorial.channel_records import ChannelRecords

def validate_mining_tags(body, symbols):
    """Text API supports cashtags; metadata alone creates no reader entry point."""
    tags = set(re.findall(r"(?<![A-Za-z0-9_])\$([A-Z][A-Z0-9]{0,14})(?![A-Za-z0-9_])", body))
    if not tags or len(tags) > 3 or tags != set(symbols):
        raise ValueError("content mining requires 1-3 relevant body cashtags matching symbols")


MAX_EVIDENCE_AGE_SECONDS = 1200
REVIEW_FIELDS = ("reader_question", "thesis", "invalidation", "evidence_check", "reviewer")


def digest(payload):
    return sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":")).encode()).hexdigest()


def copy_digest(doc):
    return digest({key: doc.get(key) for key in (
        "body", "evidence_sha256", "observed_at", "symbols", "research_provenance", *REVIEW_FIELDS,
    )})


class SquareDraftStore:
    def __init__(self, state_root):
        self.root = Path(state_root) / "square_editorial"
        self.records = ChannelRecords(state_root)
        self.evidence = live_cache_root(state_root) / "square_evidence"

    def lock(self):
        return self.records.transaction()

    def get(self, draft_id):
        return self.records.get("binance_square", draft_id)

    def list(self):
        return self.records.all("binance_square")

    def save(self, doc):
        article_id = doc.get("article_id") or "square-" + doc["draft_id"]
        return self.records.put(article_id, "binance_square", doc)

    def create(self, *, body, evidence, symbols, reader_question, thesis, invalidation,
               research_provenance=None, article_id=None):
        if not body.strip() or len(body) > MAX_POST_CHARS:
            raise ValueError("Square draft must contain 1-1500 characters")
        if not set(symbols) or not set(symbols) <= {"BTC", "ETH"}:
            raise ValueError("Square editorial focus is BTC/ETH")
        observed = datetime.fromisoformat(evidence["observed_at"])
        if observed.utcoffset() is None:
            raise ValueError("evidence must have an aware observation time")
        evidence_hash = digest(evidence)
        identity = digest({"body": body, "evidence_sha256": evidence_hash})
        with self.lock():
            existing = self.get(identity)
            if existing:
                return existing
            self.evidence.mkdir(parents=True, exist_ok=True)
            evidence_path = self.evidence / (evidence_hash + ".json")
            if not evidence_path.exists():
                evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
            doc = {"draft_id": identity, "status": "DRAFT", "body": body,
                   "symbols": sorted(set(symbols)), "observed_at": observed.isoformat(),
                   "evidence_sha256": evidence_hash, "reader_question": reader_question,
                   "thesis": thesis, "invalidation": invalidation, "attempts": 0,
                   "created_at": datetime.now(timezone.utc).isoformat()}
            if article_id is not None:
                doc["article_id"] = article_id
            if research_provenance is not None:
                doc["research_provenance"] = research_provenance
            self.save(doc)
            return doc

    def validate(self, doc, now):
        if not doc.get("body", "").strip() or len(doc["body"]) > MAX_POST_CHARS:
            raise ValueError("invalid Square text length")
        if not doc.get("symbols") or not set(doc["symbols"]) <= {"BTC", "ETH"}:
            raise ValueError("unsupported editorial topic")
        if any(not str(doc.get(key, "")).strip() for key in REVIEW_FIELDS):
            raise ValueError("editorial review is incomplete")
        if not any("\u4e00" <= char <= "\u9fff" for char in doc["body"]):
            raise ValueError("a Chinese Square draft is required")
        observed = datetime.fromisoformat(doc["observed_at"])
        age = (now - observed).total_seconds()
        if age < 0 or age > MAX_EVIDENCE_AGE_SECONDS:
            raise ValueError("market evidence expired or is from the future")
        evidence_hash = doc["evidence_sha256"]
        if len(evidence_hash) != 64 or any(c not in "0123456789abcdef" for c in evidence_hash):
            raise ValueError("invalid evidence hash")
        evidence = json.loads((self.evidence / (evidence_hash + ".json")).read_text())
        if digest(evidence) != evidence_hash or evidence["observed_at"] != doc["observed_at"]:
            raise ValueError("market evidence changed after review")
        if evidence.get("status") != "OK" or set(evidence.get("metrics", {})) != {"BTCUSDT", "ETHUSDT"}:
            raise ValueError("incomplete market structure evidence")
        if evidence["metrics"] != analyze(evidence):
            raise ValueError("derived market metrics do not match raw source evidence")
        if doc.get("review_sha256") != copy_digest(doc):
            raise ValueError("copy or editorial assessment changed after review")
        preferences_path = self.root / "editorial_preferences.json"
        preferences = json.loads(preferences_path.read_text()) if preferences_path.exists() else {}
        if preferences.get("content_mining", {}).get("objective") == "WRITE_TO_EARN":
            validate_mining_tags(doc["body"], doc["symbols"])
        self.validate_research(doc)

    def validate_research(self, doc):
        preference_path = self.root / "editorial_preferences.json"
        preferences = json.loads(preference_path.read_text()) if preference_path.exists() else {}
        provenance = doc.get("research_provenance")
        if not provenance:
            if preferences.get("production_mode") == "COGNITION_LED":
                raise ValueError("Ferro cognition-led publication requires real research provenance")
            return
        cycle = provenance.get("learning_cycle", "")
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", cycle):
            raise ValueError("invalid research cycle")
        folder = self.root.parent / "content" / "continuous" / "cycles" / cycle
        records = {}
        for name, field in (("learning_result.json", "learning_sha256"),
                            ("cognition.json", "cognition_sha256"),
                            ("square_expression_review.json", "semantic_review_sha256")):
            records[name] = json.loads((folder / name).read_text())
            if digest(records[name]) != provenance.get(field):
                raise ValueError("Ferro research or semantic review changed: " + name)
        from wechat_traffic_agent.learning_expression_quality import verified_learning_facts
        if not verified_learning_facts(records["learning_result.json"]):
            raise ValueError("research has no source-verified discovery")
        if not records["cognition.json"].get("worth_saying"):
            raise ValueError("Ferro cognition did not select expression")
        review = records["square_expression_review.json"]
        if not review.get("passed") or review.get("text_sha256") != sha256(doc["body"].encode()).hexdigest():
            raise ValueError("final Square body lacks its matching semantic review")

    def review(self, draft_id, *, reviewer, evidence_check, now=None):
        now = now or datetime.now(timezone.utc)
        with self.lock():
            doc = self.get(draft_id)
            if doc["status"] not in {"DRAFT", "NEEDS_REVIEW"}:
                raise ValueError("attempted or completed drafts cannot be reviewed again")
            doc.update(reviewer=reviewer, evidence_check=evidence_check,
                       reviewed_at=now.isoformat(), status="REVIEWED")
            doc["review_sha256"] = copy_digest(doc)
            self.validate(doc, now)
            self.save(doc)
            return doc


class SquareDraftWorker:
    def __init__(self, state_root, *, client=None):
        self.store = SquareDraftStore(state_root)
        self.client = client or BinanceSquareClient()

    def run_once(self, *, now=None):
        if not square_enabled() or not square_writes_enabled() or not self.client.key.strip():
            return None
        now = now or datetime.now(timezone.utc)
        # Persist the claim before any network call. A crash remains PUBLISHING and
        # requires reconciliation, never an automatic second submission.
        with self.store.lock():
            for doc in sorted(self.store.list(), key=lambda row: row.get("draft_id", "")):
                if doc.get("status") != "REVIEWED":
                    continue
                try:
                    self.store.validate(doc, now)
                except (ValueError, OSError, KeyError, TypeError, ArithmeticError) as exc:
                    doc.update(status="NEEDS_REVIEW", error=str(exc))
                    self.store.save(doc)
                    continue
                doc.update(status="PUBLISHING", attempts=1, attempted_at=now.isoformat())
                self.store.save(doc)
                break
            else:
                return None
        try:
            post_id = self.client.post_text(doc["body"])
            doc.update(status="SUCCESS", post_id=post_id, url=POST_URL + post_id,
                       published_at=datetime.now(timezone.utc).isoformat())
        except SquarePublicationRejected as exc:
            doc.update(status="REJECTED", error=str(exc))
        except Exception as exc:
            doc.update(status="UNVERIFIED", error=str(exc))
        with self.store.lock():
            self.store.save(doc)
        return doc
