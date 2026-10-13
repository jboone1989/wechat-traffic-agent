"""Bounded public X outcome evidence through Agent Runtime's X browser."""
from __future__ import annotations
from wechat_traffic_agent.publication.cycle_records import NAMES, read_cycle_record, write_cycle_record, cycle_record_exists, publication_paths

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import time
from typing import Any, Protocol

from agent_runtime.integrations import BROWSER_NAVIGATE

from wechat_traffic_agent.channel_evidence import ChannelEvidence, ChannelEvidenceStore
from wechat_traffic_agent.content_admission import _write_once
from wechat_traffic_agent.web_editorial import digest
from wechat_traffic_agent.browser_runtime import (
    BrowserRuntimeUnavailable,
    BrowserTaskResult,
    BrowserTelemetryLedger,
)
from wechat_traffic_agent.x_browser import (
    XBrowserSessionUnavailable,
    _draft_sha256,
    _post_id_from_url,
)
from wechat_traffic_agent.x_local_browser import build_agent_runtime_x_browser

SOURCE = "x-public-page:v1"
SNAPSHOT_SCHEMA = "x-public-metrics-snapshot:v1"
WINDOWS = (("24h", timedelta(hours=24)), ("48h", timedelta(hours=48)), ("7d", timedelta(days=7)))
MAX_CAPTURE_LAG = timedelta(minutes=20)
DISTRIBUTION_COHORT = "x-public-post:fixed-age-window:v1"
X_EPOCH_MS = 1_288_834_974_657
METRICS = {
    "views": "Public cumulative view count displayed for the X post near the fixed-age window.",
    "replies": "Public cumulative reply count displayed for the X post near the fixed-age window.",
    "reposts": "Public cumulative repost count displayed for the X post near the fixed-age window.",
    "likes": "Public cumulative like count displayed for the X post near the fixed-age window.",
}


@dataclass(frozen=True)
class XPublicMetrics:
    views: int | None
    replies: int | None
    reposts: int | None
    likes: int | None


class XMetricsTransport(Protocol):
    async def collect(self, *, url: str) -> XPublicMetrics: ...


def _aware_utc(value: datetime) -> datetime:
    if value.utcoffset() is None:
        raise ValueError("timestamp timezone is required")
    return value.astimezone(timezone.utc)


def _read_json(path: Path) -> dict[str, Any]:
    if Path(path).name in NAMES:
        return read_cycle_record(path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _parse_compact_count(raw: str) -> int:
    text = str(raw).strip().replace(",", "")
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*([KMB]?)", text, re.I)
    if not match:
        raise ValueError(f"invalid compact count: {raw!r}")
    value = float(match.group(1))
    unit = match.group(2).upper()
    return int(round(value * {"": 1, "K": 1000, "M": 1_000_000, "B": 1_000_000_000}[unit]))


def _count_or_none(raw: str | None) -> int | None:
    if not raw or not re.search(r"[0-9]", raw):
        return None
    return _parse_compact_count(raw)


def _published_at_from_status_id(post_id: str) -> datetime:
    try:
        value = int(post_id)
    except ValueError as exc:
        raise ValueError("X post id must be numeric") from exc
    if value <= 0:
        raise ValueError("X post id must be positive")
    timestamp_ms = (value >> 22) + X_EPOCH_MS
    return datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc)


class LocalHarnessXMetricsTransport:
    """Read public X counters only through Agent Runtime's persistent X profile."""

    provider_name = "AGENT_RUNTIME_BROWSER"

    def __init__(self, *, state_root: Path, timeout_ms: int = 30_000) -> None:
        self.state_root = Path(state_root)
        self.timeout_ms = int(timeout_ms)
        self.telemetry = BrowserTelemetryLedger(self.state_root)
        self.browser = build_agent_runtime_x_browser()

    @classmethod
    def from_environment(cls, *, state_root: Path) -> "LocalHarnessXMetricsTransport":
        return cls(state_root=state_root)

    async def collect(self, *, url: str) -> XPublicMetrics:
        started = time.perf_counter()
        try:
            metrics = await asyncio.to_thread(self._collect_sync, url)
        except Exception as exc:
            self.telemetry.append(
                task_type="X_PUBLIC_METRICS_READ",
                site="x.com",
                result=BrowserTaskResult(
                    provider=self.provider_name,
                    status="FAILED",
                    elapsed_ms=round((time.perf_counter() - started) * 1000),
                    action_count=0,
                    failure_reason=type(exc).__name__,
                ),
            )
            raise
        self.telemetry.append(
            task_type="X_PUBLIC_METRICS_READ",
            site="x.com",
            result=BrowserTaskResult(
                provider=self.provider_name,
                status="COMPLETED",
                elapsed_ms=round((time.perf_counter() - started) * 1000),
                action_count=0,
                final_url=url,
            ),
        )
        return metrics

    def _collect_sync(self, url: str) -> XPublicMetrics:
        post_id = _post_id_from_url(url)
        if not post_id:
            raise ValueError("X outcome URL must be a public x.com status URL")
        from wechat_traffic_agent.x_local_browser import LocalHarnessXTransport
        from wechat_traffic_agent.x_jev_metrics import jev_x_metrics
        marker=("/i/article/" if "/i/article/" in url else "/status/")+post_id
        body=LocalHarnessXTransport.from_environment(state_root=self.state_root)._run_jev_sync(jev_x_metrics(url,marker))
        if body.get("status")!="OBSERVED":
            raise BrowserRuntimeUnavailable(str(body.get("detail") or body))
        metrics = XPublicMetrics(
            views=_count_or_none(str(body.get("views") or "")),
            replies=_count_or_none(str(body.get("replies") or "")),
            reposts=_count_or_none(str(body.get("reposts") or "")),
            likes=_count_or_none(str(body.get("likes") or "")),
        )
        if all(getattr(metrics, name) is None for name in METRICS):
            raise RuntimeError("no public X engagement counters were parseable")
        return metrics


class XMetricsTransportRouter:
    """Single X metrics route through Agent Runtime."""

    def __init__(self, *, state_root: Path) -> None:
        self.state_root = Path(state_root)

    async def collect(self, *, url: str) -> XPublicMetrics:
        try:
            transport = LocalHarnessXMetricsTransport.from_environment(
                state_root=self.state_root
            )
            return await transport.collect(url=url)
        except BrowserRuntimeUnavailable as exc:
            raise XBrowserSessionUnavailable(str(exc)) from exc


class XOutcomeCollector:
    def __init__(self, *, state_root: Path, transport: XMetricsTransport | None=None,
                 max_capture_lag: timedelta=MAX_CAPTURE_LAG) -> None:
        self.state_root=Path(state_root)
        self.transport=transport or XMetricsTransportRouter(state_root=self.state_root)
        self.max_capture_lag=max_capture_lag
        self.store=ChannelEvidenceStore(self.state_root/"content"/"channel_evidence")
        self.snapshot_root=self.state_root/"content"/"channel_evidence_sources"

    async def collect(self, *, now: datetime) -> dict[str, Any]:
        now=_aware_utc(now); publications=self._publications()
        if not publications:
            return self._report("NO_ELIGIBLE_PUBLICATIONS")
        existing={(r.publication_id,r.window_start,r.window_end,r.metric,r.source) for r in self.store.as_of(now)}
        recorded=[]; missed=[]; pending=0
        for publication in publications:
            published_at=publication["published_at"]
            for label,delta in WINDOWS:
                window_end=published_at+delta
                keys=[(publication["publication_id"],published_at,window_end,m,SOURCE) for m in METRICS]
                if all(k in existing for k in keys): continue
                if now < window_end:
                    pending+=1; continue
                if now > window_end+self.max_capture_lag:
                    missed.append({"publication_id":publication["publication_id"],"window":label}); continue
                metrics=await self.transport.collect(url=publication["url"])
                snapshot={
                    "schema_version":SNAPSHOT_SCHEMA,"publication_id":publication["publication_id"],
                    "post_id":publication["post_id"],"url":publication["url"],"window":label,
                    "window_start":published_at.isoformat(),"window_end":window_end.isoformat(),
                    "observed_at":now.isoformat(),"capture_lag_seconds":(now-window_end).total_seconds(),
                    "metrics":{name:getattr(metrics,name) for name in METRICS},"source":SOURCE,
                    "privacy":"Public aggregate counters only. An authenticated render session may be used; no private account data is retained.",
                }
                snapshot_id=digest(snapshot)
                _write_once(self.snapshot_root/f"{snapshot_id}.json",json.dumps(snapshot,sort_keys=True,ensure_ascii=False,indent=2)+"\n")
                for metric,definition in METRICS.items():
                    value=getattr(metrics,metric)
                    row=ChannelEvidence(
                        channel="x",account=publication["account"],publication_id=publication["publication_id"],
                        artifact_id=publication["artifact_id"],published_content_sha256=publication["content_sha256"],
                        agent_candidate_sha256=publication["content_sha256"],topic=publication["topic"],
                        title_version="x:no-title:v1",structure_version=publication["structure_version"],
                        strategy_version=publication["strategy_version"],objective=f"x_{metric}",
                        distribution_cohort=DISTRIBUTION_COHORT,metric=metric,metric_definition=definition,
                        unit="count",value=float(value) if value is not None else None,
                        published_at=published_at,window_start=published_at,window_end=window_end,observed_at=now,
                        revision=1,source=SOURCE,evidence_refs=(f"x-publication:{publication['post_id']}",f"x-snapshot:sha256:{snapshot_id}"),
                    )
                    path=self.store.record(row); existing.add((row.publication_id,row.window_start,row.window_end,row.metric,row.source))
                    recorded.append({"publication_id":publication["publication_id"],"window":label,"metric":metric,"value":value,"evidence_path":str(path)})
        status="RECORDED" if recorded else "MISSED_WINDOW" if missed else "NO_MATURE_WINDOWS"
        return self._report(status,eligible=len(publications),pending=pending,recorded=recorded,missed=missed)

    def _publications(self) -> list[dict[str, Any]]:
        rows=[]; root=self.state_root/"content"/"continuous"/"cycles"
        for cycle_dir in sorted(root.glob("*")):
            receipt=_read_json(cycle_dir/"x_browser_publication.json")
            drafts=_read_json(cycle_dir/"social_expression_drafts.json")
            website=_read_json(cycle_dir/"publication.json")
            if receipt.get("status")!="POSTED" or drafts.get("status")!="DRAFT_READY": continue
            draft_rows=drafts.get("drafts") if isinstance(drafts.get("drafts"),dict) else {}
            draft=draft_rows.get("x") if isinstance(draft_rows.get("x"),dict) else {}
            text=str(draft.get("text") or "")
            attr=website.get("outcome_attribution") if isinstance(website.get("outcome_attribution"),dict) else {}
            if not text.strip(): continue
            if not attr:
                cognition=_read_json(cycle_dir/"cognition.json")
                attr={"topic":cognition.get("topic") or "public_cognition", "artifact_id":cycle_dir.name}
            post_id=str(receipt.get("post_id") or ""); url=str(receipt.get("url") or "")
            if not post_id or not url: continue
            if receipt.get("draft_sha256") != _draft_sha256(cycle=cycle_dir.name,draft=draft):
                raise ValueError("X publication receipt draft binding mismatch")
            if _post_id_from_url(url) != post_id:
                raise ValueError("X publication receipt URL/post id mismatch")
            published_at=(datetime.fromisoformat(str(receipt["published_at"])) if draft.get("format")=="ARTICLE"
                          else _published_at_from_status_id(post_id))
            format_name=str(draft.get("format") or "SHORT_POST"); intent=str(draft.get("intent") or "UNKNOWN")
            resolution=drafts.get("evidence_decision") if isinstance(drafts.get("evidence_decision"),dict) else {}
            rr=resolution.get("resolution") if isinstance(resolution.get("resolution"),dict) else {}
            handle=str(receipt.get("handle") or "").lstrip("@")
            rows.append({
                "publication_id":f"x:{post_id}","post_id":post_id,"url":url,"published_at":published_at,
                "account":f"https://x.com/{handle}" if handle else "https://x.com",
                "artifact_id":str(website.get("artifact_id") or attr.get("artifact_id") or cycle_dir.name),
                "content_sha256":hashlib.sha256(text.encode("utf-8")).hexdigest(),"topic":str(attr.get("topic") or "unknown"),
                "structure_version":f"x:{format_name}:{intent}:v1",
                "strategy_version":f"x-social-evidence:{str(rr.get('action') or 'UNKNOWN')}:v1",
            })
        # Autonomous expressions and replies do not require a website artifact.
        from wechat_traffic_agent.x_social import XHistoryLedger
        seen={row["post_id"] for row in rows}
        for event in XHistoryLedger(self.state_root).recent(limit=1000):
            post_id=str(event.get("x_post_id") or "")
            url=str(event.get("x_url") or "")
            text=str(event.get("text") or "")
            if not post_id or post_id in seen or not text or _post_id_from_url(url)!=post_id:
                continue
            cycle=str(event.get("cycle") or "")
            if event.get("event_type")=="EXPRESSION":
                receipt=_read_json(root/cycle/"x_social_expression.json")
                if receipt.get("status")!="POSTED" or str(receipt.get("post_id") or "")!=post_id or receipt.get("url")!=url:
                    continue
                if event.get("runtime_receipt_id")!=receipt.get("receipt_id"):
                    continue
                if receipt.get("content_sha256") and receipt["content_sha256"]!=hashlib.sha256(text.encode("utf-8")).hexdigest():
                    raise ValueError("X autonomous publication content binding mismatch")
                if receipt.get("text") and receipt["text"]!=text:
                    raise ValueError("X autonomous publication text binding mismatch")
                kind="SHORT_POST"
            elif event.get("event_type")=="REPLY":
                if not event.get("publication_verified") or not event.get("target_post_id"):
                    continue
                if event.get("runtime_receipt_id")!=f"x-jev-reply:{event['target_post_id']}:{post_id}":
                    continue
                kind="REPLY"
            else:
                continue
            seen.add(post_id)
            cognition=event.get("cognition") or {}
            rows.append({"publication_id":f"x:{post_id}", "post_id":post_id, "url":url,
                "published_at":_published_at_from_status_id(post_id),
                "account":url.split("/status/",1)[0], "artifact_id":cycle,
                "content_sha256":hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "topic":str(cognition.get("topic") or cognition.get("category") or "public_cognition"),
                "structure_version":f"x:{kind}:v1", "strategy_version":"x-autonomy:jev:v1"})
        rows.extend(_omnipost_syndications(self.state_root, seen))
        return rows

    @staticmethod
    def _report(status: str, **extra: Any) -> dict[str, Any]:
        return {"status":status,"source":SOURCE,"publication_authority":False,"learning_authority":False,"causal_claim":False,**extra}

def _syndication_topic(state_root: Path, doc: dict[str, Any]) -> str:
    """Topic of the Ferro cycle behind a Square draft, else its first symbol."""
    cycle=str((doc.get("research_provenance") or {}).get("learning_cycle")
              or (doc.get("observation_provenance") or {}).get("cycle") or "")
    if re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", cycle):
        cognition=_read_json(state_root/"content"/"continuous"/"cycles"/cycle/"cognition.json")
        if str(cognition.get("topic") or "").strip():
            return str(cognition["topic"]).strip()
    symbols=doc.get("symbols") or []
    return str(symbols[0]) if symbols else "unknown"


def _omnipost_syndications(state_root: Path, seen: set[str]) -> list[dict[str, Any]]:
    """X posts that omnipost cross-posted from reviewed Square drafts."""
    from wechat_traffic_agent.editorial.channel_records import ChannelRecords
    rows=[]
    for doc in ChannelRecords(state_root).all("binance_square"):
        receipt=doc.get("x_syndication") or {}
        post_id=str(receipt.get("post_id") or ""); url=str(receipt.get("url") or "")
        text=str(doc.get("body") or "")
        if (receipt.get("status")!="SUCCESS" or not post_id or post_id in seen or not text.strip()
                or _post_id_from_url(url)!=post_id):
            continue
        seen.add(post_id)
        rows.append({"publication_id":f"x:{post_id}", "post_id":post_id, "url":url,
            "published_at":_published_at_from_status_id(post_id),
            "account":url.split("/status/",1)[0], "artifact_id":str(doc.get("draft_id") or post_id),
            "content_sha256":hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "topic":_syndication_topic(state_root, doc),
            "structure_version":"x:OMNIPOST_SYNDICATION:v1",
            "strategy_version":"square-omnipost-syndication:v1"})
    return rows

__all__=[
    "LocalHarnessXMetricsTransport","MAX_CAPTURE_LAG","METRICS","SOURCE","WINDOWS",
    "XMetricsTransport","XMetricsTransportRouter","XOutcomeCollector","XPublicMetrics",
    "_parse_compact_count","_published_at_from_status_id"
]
