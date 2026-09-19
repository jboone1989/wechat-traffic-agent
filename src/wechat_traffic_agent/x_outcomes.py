"""Bounded public X outcome evidence from verified browser publications."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import time
from typing import Any, Protocol

from .channel_evidence import ChannelEvidence, ChannelEvidenceStore
from .content_admission import _write_once
from .web_editorial import digest
from .browser_runtime import BrowserRuntimeUnavailable, BrowserTaskResult, BrowserTelemetryLedger
from .x_browser import (
    TinyFishProfileClient,
    XBrowserSessionUnavailable,
    _draft_sha256,
    _post_id_from_url,
    tinyfish_x_enabled,
)

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
    try:
        value=json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value,dict) else {}

def _parse_compact_count(raw: str) -> int:
    text=str(raw).strip().replace(",", "")
    match=re.search(r"([0-9]+(?:\.[0-9]+)?)\s*([KMB]?)", text, re.I)
    if not match:
        raise ValueError(f"invalid compact count: {raw!r}")
    value=float(match.group(1)); unit=match.group(2).upper()
    return int(round(value*{"":1,"K":1000,"M":1_000_000,"B":1_000_000_000}[unit]))

def _count_or_none(raw: str | None) -> int | None:
    if not raw or not re.search(r"[0-9]", raw):
        return None
    return _parse_compact_count(raw)

def _published_at_from_status_id(post_id: str) -> datetime:
    try:
        value=int(post_id)
    except ValueError as exc:
        raise ValueError("X post id must be numeric") from exc
    if value <= 0:
        raise ValueError("X post id must be positive")
    timestamp_ms=(value >> 22) + X_EPOCH_MS
    return datetime.fromtimestamp(timestamp_ms/1000, timezone.utc)

class TinyFishXMetricsTransport:
    """Read only aggregate X counters via the authenticated TinyFish profile."""
    def __init__(self, *, client: TinyFishProfileClient, timeout_ms: int=30_000) -> None:
        self.client=client; self.timeout_ms=int(timeout_ms)

    @classmethod
    def from_environment(cls) -> "TinyFishXMetricsTransport":
        import os
        return cls(client=TinyFishProfileClient(
            api_key=os.environ.get("TINYFISH_API_KEY", ""),
            profile_id=os.environ.get("TINYFISH_X_PROFILE_ID", ""),
            base_url=os.environ.get("TINYFISH_BASE_URL", "https://agent.tinyfish.ai"),
        ), timeout_ms=int(os.environ.get("CONTENT_TINYFISH_X_TIMEOUT_MS", "30000")))

    async def collect(self, *, url: str) -> XPublicMetrics:
        post_id=_post_id_from_url(url)
        if not post_id:
            raise ValueError("X outcome URL must be a public x.com status URL")
        import asyncio
        setup=await asyncio.to_thread(self.client.start_setup, url=url, timeout_seconds=180)
        session_id=str(setup.get("session_id") or ""); cdp_url=str(setup.get("cdp_url") or "")
        if not session_id or not cdp_url:
            raise RuntimeError("TinyFish profile did not return a browser setup session")
        try:
            from playwright.async_api import async_playwright
            async with async_playwright() as playwright:
                browser=await playwright.chromium.connect_over_cdp(cdp_url)
                page=browser.contexts[0].pages[0]
                page.set_default_timeout(self.timeout_ms)
                await page.goto(url, wait_until="domcontentloaded")
                article=page.locator(f'article:has(a[href*="/status/{post_id}"])').first
                await article.wait_for(state="visible", timeout=min(self.timeout_ms, 15_000))
                await page.wait_for_timeout(500)
                values={}
                for metric,testid in (("replies","reply"),("reposts","retweet"),("likes","like")):
                    node=article.locator(f'[data-testid="{testid}"]').first
                    if not await node.count():
                        values[metric]=None; continue
                    label=await node.get_attribute("aria-label")
                    text=await node.inner_text()
                    values[metric]=_count_or_none(label) if label else _count_or_none(text)
                analytics=article.locator(f'a[href*="/status/{post_id}/analytics"]').first
                if not await analytics.count():
                    analytics=article.locator('a[href$="/analytics"]').first
                values["views"]=_count_or_none(await analytics.inner_text()) if await analytics.count() else None
                metrics=XPublicMetrics(**values)
                if all(getattr(metrics,name) is None for name in METRICS):
                    raise RuntimeError("no public X engagement counters were parseable")
                return metrics
        finally:
            try:
                import asyncio
                await asyncio.to_thread(self.client.cancel, session_id=session_id)
            except Exception:
                pass

class LocalHarnessXMetricsTransport:
    """Read public X counters through Ferro's owned Chrome runtime."""

    def __init__(
        self, *, state_root: Path, timeout_ms: int = 30_000, browser_factory=None
    ) -> None:
        self.state_root = Path(state_root)
        self.timeout_ms = int(timeout_ms)
        self.browser_factory = browser_factory
        self.telemetry = BrowserTelemetryLedger(self.state_root)

    @classmethod
    def from_environment(cls, *, state_root: Path) -> "LocalHarnessXMetricsTransport":
        if os.environ.get("CONTENT_LOCAL_X_BROWSER_ENABLED", "").strip().lower() not in {"1", "true", "yes", "on"}:
            raise BrowserRuntimeUnavailable("CONTENT_LOCAL_X_BROWSER_ENABLED is false")
        if importlib.util.find_spec("jev_ultrafast") is None:
            raise BrowserRuntimeUnavailable("jev-ultrafast is not installed")
        if importlib.util.find_spec("browser_harness") is None:
            raise BrowserRuntimeUnavailable("browser-harness is not installed")
        return cls(
            state_root=state_root,
            timeout_ms=int(os.environ.get("CONTENT_LOCAL_X_BROWSER_TIMEOUT_MS", "30000")),
        )

    def _factory(self):
        if self.browser_factory is not None:
            return self.browser_factory
        try:
            from jev_ultrafast.browser import Browser
        except ImportError as exc:
            raise BrowserRuntimeUnavailable("local Browser Harness runtime is not installed") from exc
        return Browser

    async def collect(self, *, url: str) -> XPublicMetrics:
        started = time.perf_counter()
        try:
            metrics = await asyncio.to_thread(self._collect_sync, url)
        except Exception as exc:
            self.telemetry.append(
                task_type="X_PUBLIC_METRICS_READ",
                site="x.com",
                result=BrowserTaskResult(
                    provider="LOCAL_CDP",
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
                provider="LOCAL_CDP",
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
        browser = self._factory()(url)
        try:
            deadline = time.monotonic() + min(15.0, max(2.0, self.timeout_ms / 1000))
            marker = f'/status/{post_id}'
            while time.monotonic() < deadline:
                found = browser.evaluate(
                    "(() => [...document.querySelectorAll('article')].some("
                    + "a => [...a.querySelectorAll('a')].some(x => (x.getAttribute('href')||'').includes("
                    + repr(marker)
                    + "))))()"
                )
                if found:
                    break
                time.sleep(0.1)
            else:
                raise RuntimeError("X public post article did not render")

            raw = browser.evaluate(
                """(() => {
                  const marker=%s;
                  const article=[...document.querySelectorAll('article')].find(
                    a => [...a.querySelectorAll('a')].some(x => (x.getAttribute('href')||'').includes(marker))
                  );
                  if(!article) return null;
                  const counter=(testid) => {
                    const n=article.querySelector('[data-testid="'+testid+'"]');
                    return n ? (n.getAttribute('aria-label') || n.innerText || '') : '';
                  };
                  let analytics=[...article.querySelectorAll('a')].find(
                    x => (x.getAttribute('href')||'').includes(marker+'/analytics')
                  );
                  if(!analytics) analytics=article.querySelector('a[href$="/analytics"]');
                  return {
                    replies:counter('reply'),
                    reposts:counter('retweet'),
                    likes:counter('like'),
                    views:analytics ? (analytics.innerText || '') : ''
                  };
                })()""" % json.dumps(marker)
            )
            if not isinstance(raw, dict):
                raise RuntimeError("X public counters were unavailable")
            metrics = XPublicMetrics(
                views=_count_or_none(str(raw.get("views") or "")),
                replies=_count_or_none(str(raw.get("replies") or "")),
                reposts=_count_or_none(str(raw.get("reposts") or "")),
                likes=_count_or_none(str(raw.get("likes") or "")),
            )
            if all(getattr(metrics, name) is None for name in METRICS):
                raise RuntimeError("no public X engagement counters were parseable")
            return metrics
        finally:
            try:
                browser.close()
            except Exception:
                pass


class XMetricsTransportRouter:
    """Prefer the authenticated TinyFish profile; keep local Chrome as read-only fallback."""

    def __init__(self, *, state_root: Path) -> None:
        self.state_root = Path(state_root)

    async def collect(self, *, url: str) -> XPublicMetrics:
        errors: list[str] = []
        if tinyfish_x_enabled():
            try:
                return await TinyFishXMetricsTransport.from_environment().collect(url=url)
            except Exception as exc:
                errors.append("TINYFISH:" + type(exc).__name__)

        if os.environ.get("CONTENT_LOCAL_X_BROWSER_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}:
            try:
                local = LocalHarnessXMetricsTransport.from_environment(state_root=self.state_root)
                return await local.collect(url=url)
            except Exception as exc:
                errors.append("LOCAL_CDP:" + type(exc).__name__)

        raise XBrowserSessionUnavailable(
            "; ".join(errors) or "no X metrics browser provider is configured"
        )


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
            if not text.strip() or not attr: continue
            post_id=str(receipt.get("post_id") or ""); url=str(receipt.get("url") or "")
            if not post_id or not url: continue
            if receipt.get("draft_sha256") != _draft_sha256(cycle=cycle_dir.name,draft=draft):
                raise ValueError("X publication receipt draft binding mismatch")
            if _post_id_from_url(url) != post_id:
                raise ValueError("X publication receipt URL/post id mismatch")
            published_at=_published_at_from_status_id(post_id)
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
        return rows

    @staticmethod
    def _report(status: str, **extra: Any) -> dict[str, Any]:
        return {"status":status,"source":SOURCE,"publication_authority":False,"learning_authority":False,"causal_claim":False,**extra}

__all__=[
    "LocalHarnessXMetricsTransport","MAX_CAPTURE_LAG","METRICS","SOURCE","WINDOWS",
    "TinyFishXMetricsTransport","XMetricsTransport","XMetricsTransportRouter",
    "XOutcomeCollector","XPublicMetrics","_parse_compact_count","_published_at_from_status_id"
]
