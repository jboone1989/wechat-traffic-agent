"""Privacy-minimized first-party Website engagement evidence.

Browser-generated visitor/session tokens are HMACed immediately. Raw tokens, IPs,
user agents, referrers and page text are never persisted by this module.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import tempfile
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .channel_evidence import ChannelEvidence, ChannelEvidenceStore
from .web_editorial import digest

SOURCE = "first-party-engagement:v1"
EVENT_SCHEMA = "website-engagement-event.v1"
COVERAGE_SCHEMA = "website-engagement-coverage.v1"
MAX_BODY_BYTES = 2048
TOKEN_RE = r"^[0-9a-f]{32}$"
WINDOWS = (("24h", timedelta(hours=24)), ("48h", timedelta(hours=48)), ("7d", timedelta(days=7)))
RETURN_GAP = timedelta(hours=6)
METRICS = {
    "tracked_pageviews": ("Count of accepted first-party page_view events on the exact publication route.", "count"),
    "engaged_readers_30s": ("Unique privacy-HMACed visitors with at least 30 seconds of visible article time.", "count"),
    "internal_link_clicks": ("Accepted same-origin internal-link click events originating on the publication route.", "count"),
    "return_visitors": ("Unique privacy-HMACed visitors who viewed the publication then returned in a different session at least six hours later within the window.", "count"),
}

class WebsiteEngagementInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["website-engagement-event.v1"] = EVENT_SCHEMA
    event_id: str = Field(pattern=TOKEN_RE)
    visitor_token: str = Field(pattern=TOKEN_RE)
    session_token: str = Field(pattern=TOKEN_RE)
    event_type: Literal["page_view", "engaged_30s", "internal_link_click"]
    route: str = Field(min_length=1, max_length=500)
    target_route: str | None = Field(default=None, max_length=500)
    acquisition: Literal["direct", "google", "x", "social", "search", "referral", "other", "unknown"] = "unknown"
    device_class: Literal["desktop", "mobile", "tablet", "other", "unknown"] = "unknown"

    @field_validator("route", "target_route")
    @classmethod
    def local_path_only(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlsplit(value)
        if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError("engagement routes must be same-origin paths without query/fragment")
        if not parsed.path.startswith("/") or "//" in parsed.path:
            raise ValueError("engagement route must be a normalized absolute path")
        return parsed.path

    @model_validator(mode="after")
    def event_shape(self):
        if self.event_type == "internal_link_click":
            if self.target_route is None or self.target_route == self.route:
                raise ValueError("internal_link_click requires a distinct target_route")
        elif self.target_route is not None:
            raise ValueError("target_route is only valid for internal_link_click")
        if self.event_type == "engaged_30s" and not self.route.startswith("/articles/"):
            raise ValueError("engaged_30s is only accepted for article routes")
        return self

class WebsiteEngagementEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["website-engagement-stored.v1"] = "website-engagement-stored.v1"
    event_id: str = Field(pattern=TOKEN_RE)
    visitor_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    session_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    event_type: Literal["page_view", "engaged_30s", "internal_link_click"]
    route: str
    target_route: str | None = None
    acquisition: Literal["direct", "google", "x", "social", "search", "referral", "other", "unknown"] = "unknown"
    device_class: Literal["desktop", "mobile", "tablet", "other", "unknown"] = "unknown"
    audience_class: Literal["public", "operator"] = "public"
    observed_at: datetime
    source: Literal["first-party-engagement:v1"] = SOURCE
    publication_authority: Literal[False] = False
    causal_claim: Literal[False] = False

    @field_validator("observed_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("engagement timestamp must be timezone-aware")
        return value

class WebsiteEngagementStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.key_path = self.root / "visitor_hmac.key"
        self.coverage_path = self.root / "coverage.json"
        self.events_root = self.root / "events"

    def _key(self) -> bytes:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "wb") as handle:
                handle.write(secrets.token_bytes(32)); handle.flush(); os.fsync(handle.fileno())
        os.chmod(self.key_path, 0o600)
        key = self.key_path.read_bytes()
        if len(key) != 32:
            raise ValueError("engagement HMAC key must be exactly 32 bytes")
        return key

    def operator_audience_marker(self) -> str:
        return hmac.new(self._key(), b"operator-audience:v1", hashlib.sha256).hexdigest()

    def valid_operator_audience_marker(self, marker: str | None) -> bool:
        if not marker:
            return False
        expected = self.operator_audience_marker()
        try:
            return hmac.compare_digest(marker, expected)
        except TypeError:
            return False

    def mark_started(self, *, observed_at: datetime) -> dict:
        observed_at = _utc(observed_at)
        payload = {"schema_version": COVERAGE_SCHEMA, "started_at": observed_at.isoformat(), "source": SOURCE, "publication_authority": False}
        if self.coverage_path.exists():
            saved = json.loads(self.coverage_path.read_text(encoding="utf-8"))
            if saved.get("schema_version") != COVERAGE_SCHEMA:
                raise ValueError("engagement coverage schema mismatch")
            return saved
        _write_once_json(self.coverage_path, payload)
        return payload

    def coverage_start(self) -> datetime | None:
        if not self.coverage_path.exists():
            return None
        payload = json.loads(self.coverage_path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != COVERAGE_SCHEMA:
            raise ValueError("engagement coverage schema mismatch")
        return _utc(datetime.fromisoformat(str(payload["started_at"])))

    def record_payload(self, payload: dict, *, observed_at: datetime, audience_class: Literal["public", "operator"] = "public") -> dict:
        raw = WebsiteEngagementInput.model_validate(payload)
        observed_at = _utc(observed_at).replace(microsecond=0)
        key = self._key()
        event = WebsiteEngagementEvent(event_id=raw.event_id, visitor_hash=_hmac_token(key, raw.visitor_token), session_hash=_hmac_token(key, raw.session_token), event_type=raw.event_type, route=raw.route, target_route=raw.target_route, acquisition=raw.acquisition, device_class=raw.device_class, audience_class=audience_class, observed_at=observed_at)
        path = self.events_root / observed_at.strftime("%Y-%m-%d") / f"{event.event_id}.json"
        stable = _stable_event(event)
        if path.exists():
            existing = WebsiteEngagementEvent.model_validate_json(path.read_text(encoding="utf-8"))
            if _stable_event(existing) != stable:
                raise ValueError("engagement event_id conflicts with existing event")
            return {"status": "DUPLICATE", "event_id": event.event_id, "publication_authority": False}
        try:
            _write_once_json(path, event.model_dump(mode="json"))
        except FileExistsError:
            existing = WebsiteEngagementEvent.model_validate_json(path.read_text(encoding="utf-8"))
            if _stable_event(existing) != stable:
                raise ValueError("engagement event_id conflicts with existing event") from None
            return {"status": "DUPLICATE", "event_id": event.event_id, "publication_authority": False}
        return {"status": "RECORDED", "event_id": event.event_id, "publication_authority": False}

    def events(self, *, start: datetime, end: datetime) -> list[WebsiteEngagementEvent]:
        start, end = _utc(start), _utc(end)
        if not start < end:
            raise ValueError("engagement event range must be non-empty")
        rows = []
        for path in sorted(self.events_root.glob("*/*.json")):
            row = WebsiteEngagementEvent.model_validate_json(path.read_text(encoding="utf-8"))
            if path.name != f"{row.event_id}.json":
                raise ValueError("engagement event filename mismatch")
            if start <= row.observed_at.astimezone(timezone.utc) < end:
                rows.append(row)
        return rows

class WebsiteEngagementOutcomeCollector:
    def __init__(self, *, state_root: Path, account: str) -> None:
        self.state_root = Path(state_root); self.account = str(account).strip().rstrip("/")
        if not self.account:
            raise ValueError("engagement outcome account must be non-empty")
        self.events = WebsiteEngagementStore(self.state_root / "content" / "website_engagement")
        self.evidence = ChannelEvidenceStore(self.state_root / "content" / "channel_evidence")
        self.snapshot_root = self.state_root / "content" / "channel_evidence_sources"

    def collect(self, *, now: datetime) -> dict:
        now = _utc(now); coverage_start = self.events.coverage_start()
        if coverage_start is None: return _report("NO_ENGAGEMENT_COVERAGE")
        publications = self._publications()
        if not publications: return _report("NO_ELIGIBLE_PUBLICATIONS", coverage_start=coverage_start.isoformat())
        existing = {(r.publication_id, r.window_start, r.window_end, r.metric, r.source) for r in self.evidence.as_of(now)}
        recorded, coverage_missing = [], []
        for publication in publications:
            attr = publication["outcome_attribution"]; published_at = _utc(datetime.fromisoformat(attr["published_at"]))
            for label, delta in WINDOWS:
                window_end = published_at + delta
                if window_end > now: continue
                if coverage_start > published_at:
                    coverage_missing.append({"publication_id": publication["revision"], "window": label}); continue
                # Skip already-recorded metric windows BEFORE producing a
                # new snapshot. Otherwise each observer tick changes
                # observed_at and generates another unused JSON file.
                missing_metrics = [
                    metric for metric in METRICS
                    if (publication["revision"], published_at, window_end, metric, SOURCE)
                    not in existing
                ]
                if not missing_metrics:
                    continue
                metrics = _metrics_for_route(self.events.events(start=published_at, end=window_end), route=attr["public_route"], window_end=window_end)
                snapshot = {"schema_version":"website-engagement-snapshot.v1","publication_id":publication["revision"],"artifact_id":publication["artifact_id"],"route":attr["public_route"],"window":label,"window_start":published_at.isoformat(),"window_end":window_end.isoformat(),"observed_at":now.isoformat(),"metrics":metrics,"coverage_start":coverage_start.isoformat(),"privacy":"Only HMACed random visitor/session tokens are persisted; no IP, user-agent, referrer, or page text.","causal_claim":False}
                snapshot_id = digest(snapshot); _write_once_json(self.snapshot_root / f"{snapshot_id}.json", snapshot)
                for metric in missing_metrics:
                    value = metrics[metric]
                    key=(publication["revision"], published_at, window_end, metric, SOURCE)
                    definition, unit = METRICS[metric]
                    row = ChannelEvidence(channel="website",account=self.account,publication_id=publication["revision"],artifact_id=publication["artifact_id"],published_content_sha256=attr["published_content_sha256"],agent_candidate_sha256=attr["agent_candidate_sha256"],topic=attr["topic"],title_version=attr["title_version"],structure_version=attr["structure_version"],strategy_version=attr["strategy_version"],objective="website_engagement",distribution_cohort="first-party-anonymous-events:v1",metric=metric,metric_definition=definition,unit=unit,value=float(value),published_at=published_at,window_start=published_at,window_end=window_end,observed_at=now,revision=1,source=SOURCE,evidence_refs=(f"website-publication:{publication['revision']}",f"website-engagement-snapshot:sha256:{snapshot_id}"))
                    path=self.evidence.record(row); existing.add(key); recorded.append({"publication_id":publication["revision"],"window":label,"metric":metric,"value":value,"evidence_path":str(path)})
        status = "RECORDED" if recorded else "INSUFFICIENT_ENGAGEMENT_COVERAGE" if coverage_missing else "NO_MATURE_WINDOWS"
        return _report(status, recorded=recorded, coverage_missing=coverage_missing, coverage_start=coverage_start.isoformat())

    def _publications(self) -> list[dict]:
        rows=[]; root=self.state_root / "content" / "continuous" / "cycles"
        for path in sorted(root.glob("*/publication.json")):
            raw=json.loads(path.read_text(encoding="utf-8")); attr=raw.get("outcome_attribution") if isinstance(raw,dict) else None
            if not isinstance(attr,dict): continue
            required={"published_at","public_route","published_content_sha256","agent_candidate_sha256","topic","title_version","structure_version","strategy_version"}
            if required <= set(attr): rows.append(raw)
        return rows

def ingest_engagement_body(store: WebsiteEngagementStore, body: bytes, *, observed_at: datetime, audience_class: Literal["public", "operator"] = "public") -> dict:
    if len(body) > MAX_BODY_BYTES: raise ValueError("engagement request body exceeds 2048-byte budget")
    try: payload=json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError,json.JSONDecodeError) as exc: raise ValueError("engagement request body must be valid UTF-8 JSON") from exc
    if not isinstance(payload,dict): raise ValueError("engagement request body must be a JSON object")
    result=store.record_payload(payload,observed_at=observed_at,audience_class=audience_class); store.mark_started(observed_at=observed_at); return result

def _metrics_for_route(events:list[WebsiteEngagementEvent],*,route:str,window_end:datetime)->dict[str,int]:
    views=[e for e in events if e.event_type=="page_view" and e.route==route]
    engaged={e.visitor_hash for e in events if e.event_type=="engaged_30s" and e.route==route}
    clicks=[e for e in events if e.event_type=="internal_link_click" and e.route==route]
    first={}
    for e in views:
        c=first.get(e.visitor_hash)
        if c is None or e.observed_at<c.observed_at: first[e.visitor_hash]=e
    returned=set()
    for e in events:
        if e.event_type!="page_view": continue
        o=first.get(e.visitor_hash)
        if o is None or e.session_hash==o.session_hash: continue
        if o.observed_at+RETURN_GAP<=e.observed_at<window_end: returned.add(e.visitor_hash)
    return {"tracked_pageviews":len(views),"engaged_readers_30s":len(engaged),"internal_link_clicks":len(clicks),"return_visitors":len(returned)}

def _stable_event(event): return (event.event_id,event.visitor_hash,event.session_hash,event.event_type,event.route,event.target_route,event.acquisition,event.device_class,event.audience_class,event.source)
def _hmac_token(key:bytes,token:str)->str: return hmac.new(key,token.encode("ascii"),hashlib.sha256).hexdigest()
def _utc(value:datetime)->datetime:
    if value.utcoffset() is None: raise ValueError("engagement timestamp timezone is required")
    return value.astimezone(timezone.utc)
def _write_once_json(path:Path,payload:dict)->None:
    text=json.dumps(payload,sort_keys=True,ensure_ascii=False,indent=2)+"\n"; path.parent.mkdir(parents=True,exist_ok=True,mode=0o700); fd,name=tempfile.mkstemp(prefix=".pending-engagement-",dir=path.parent)
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as handle: handle.write(text); handle.flush(); os.fsync(handle.fileno())
        try: os.link(name,path)
        except FileExistsError:
            if path.read_text(encoding="utf-8") != text: raise
    finally: Path(name).unlink(missing_ok=True)
def _report(status:str,**extra)->dict: return {"status":status,"source":SOURCE,"publication_authority":False,"learning_authority":False,"causal_claim":False,**extra}

__all__=["MAX_BODY_BYTES","WebsiteEngagementInput","WebsiteEngagementEvent","WebsiteEngagementStore","WebsiteEngagementOutcomeCollector","ingest_engagement_body","SOURCE"]
