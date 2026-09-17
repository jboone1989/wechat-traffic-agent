"""Public OKX Orbit outcome evidence from bounded, read-only page observations.

This collector never authenticates, clicks engagement controls, calls private OKX
endpoints, or backfills missed historical metrics. It records only public counters
observed within a bounded lag after fixed post-age windows mature.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Protocol

from .channel_evidence import ChannelEvidence, ChannelEvidenceStore
from .content_admission import _write_once
from .web_editorial import digest

SOURCE = "okx-orbit-public-page:v1"
SNAPSHOT_SCHEMA = "okx-orbit-public-metrics-snapshot:v1"
WINDOWS = (("24h", timedelta(hours=24)), ("48h", timedelta(hours=48)), ("7d", timedelta(days=7)))
MAX_CAPTURE_LAG = timedelta(minutes=20)
DISTRIBUTION_COHORT = "okx-orbit-public-feed:fixed-age-window:v1"
METRICS = {
    "views": "Public cumulative view count displayed on the OKX Orbit post page near the fixed-age window.",
    "comments": "Public cumulative comment/reply count displayed on the OKX Orbit post page near the fixed-age window.",
    "quotes": "Public cumulative quote count displayed on the OKX Orbit post page near the fixed-age window.",
    "likes": "Public cumulative like count displayed on the OKX Orbit post page near the fixed-age window.",
}


@dataclass(frozen=True)
class OrbitPublicMetrics:
    views: int | None
    comments: int | None
    quotes: int | None
    likes: int | None


class OrbitMetricsTransport(Protocol):
    async def collect(self, *, url: str) -> OrbitPublicMetrics: ...


def _aware_utc(value: datetime) -> datetime:
    if value.utcoffset() is None:
        raise ValueError("timestamp timezone is required")
    return value.astimezone(timezone.utc)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _parse_compact_count(raw: str) -> int:
    text = str(raw).strip().replace(",", "")
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*([KMB]?)", text, re.I)
    if not match:
        raise ValueError(f"invalid compact count: {raw!r}")
    value = float(match.group(1))
    multiplier = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[match.group(2).upper()]
    return int(round(value * multiplier))


def _metric_from_text(blob: str, label: str) -> int | None:
    aliases = {
        "views": ("views", "view", "浏览", "瀏覽"),
        "comments": ("comments", "comment", "replies", "reply", "评论", "評論", "回复", "回覆"),
        "quotes": ("quotes", "quote", "引用"),
        "likes": ("likes", "like", "赞", "讚"),
    }[label]
    number = r"([0-9]+(?:[.,][0-9]+)?\s*[KMB]?)"
    for alias in aliases:
        escaped = re.escape(alias)
        for pattern in (rf"{escaped}\s*[:·]?\s*{number}", rf"{number}\s*{escaped}"):
            match = re.search(pattern, blob, re.I)
            if match:
                return _parse_compact_count(match.group(1).replace(" ", ""))
    return None


class PlaywrightOrbitMetricsTransport:
    """Read public engagement counters from the normal Orbit post page."""

    def __init__(self, *, headless: bool = True, timeout_ms: int = 30_000) -> None:
        self.headless = bool(headless)
        self.timeout_ms = int(timeout_ms)

    async def collect(self, *, url: str) -> OrbitPublicMetrics:
        if not re.match(r"^https://(?:www\.)?okx\.com/(?:[a-z]{2}-[a-z]{2}/)?orbit/(?:post|insight)/", url, re.I):
            raise ValueError("Orbit public outcome URL must be an OKX public post/insight URL")
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:  # pragma: no cover - deployment dependency boundary
            raise RuntimeError("Playwright is required for OKX Orbit outcome collection") from exc

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=self.headless)
            page = await browser.new_page(viewport={"width": 1280, "height": 900})
            page.set_default_timeout(self.timeout_ms)
            try:
                await page.goto(url, wait_until="domcontentloaded")
                await page.wait_for_timeout(1200)
                body = await page.locator("body").inner_text()
                metadata = await page.locator("[aria-label], [title], button, [role='button']").evaluate_all(
                    "els => els.slice(0, 1500).map(e => [e.innerText || '', e.getAttribute('aria-label') || '', e.getAttribute('title') || ''].join(' ')).join('\\n')"
                )
                blob = body + "\n" + str(metadata)
                metrics = OrbitPublicMetrics(**{name: _metric_from_text(blob, name) for name in METRICS})
                if all(getattr(metrics, name) is None for name in METRICS):
                    raise RuntimeError("no public Orbit engagement counters were parseable")
                return metrics
            finally:
                await browser.close()


class OKXOrbitOutcomeCollector:
    def __init__(self, *, state_root: Path, account: str = "https://www.okx.com/orbit",
                 transport: OrbitMetricsTransport | None = None,
                 max_capture_lag: timedelta = MAX_CAPTURE_LAG) -> None:
        self.state_root = Path(state_root)
        self.account = str(account).strip().rstrip("/")
        self.transport = transport or PlaywrightOrbitMetricsTransport()
        self.max_capture_lag = max_capture_lag
        if not self.account:
            raise ValueError("OKX Orbit outcome account must be non-empty")
        self.store = ChannelEvidenceStore(self.state_root / "content" / "channel_evidence")
        self.snapshot_root = self.state_root / "content" / "channel_evidence_sources"

    async def collect(self, *, now: datetime) -> dict[str, Any]:
        now = _aware_utc(now)
        publications = self._publications()
        if not publications:
            return self._report("NO_ELIGIBLE_PUBLICATIONS")

        existing = {
            (row.publication_id, row.window_start, row.window_end, row.metric, row.source)
            for row in self.store.as_of(now)
        }
        recorded: list[dict[str, Any]] = []
        missed: list[dict[str, str]] = []
        pending = 0

        for publication in publications:
            published_at = _aware_utc(datetime.fromisoformat(str(publication["published_at"])))
            for label, delta in WINDOWS:
                window_end = published_at + delta
                metric_keys = [
                    (publication["publication_id"], published_at, window_end, metric, SOURCE)
                    for metric in METRICS
                ]
                if all(key in existing for key in metric_keys):
                    continue
                if now < window_end:
                    pending += 1
                    continue
                if now > window_end + self.max_capture_lag:
                    missed.append({"publication_id": publication["publication_id"], "window": label})
                    continue

                metrics = await self.transport.collect(url=publication["url"])
                snapshot = {
                    "schema_version": SNAPSHOT_SCHEMA,
                    "publication_id": publication["publication_id"],
                    "post_id": publication["post_id"],
                    "url": publication["url"],
                    "window": label,
                    "window_start": published_at.isoformat(),
                    "window_end": window_end.isoformat(),
                    "observed_at": now.isoformat(),
                    "capture_lag_seconds": (now - window_end).total_seconds(),
                    "metrics": {name: getattr(metrics, name) for name in METRICS},
                    "source": SOURCE,
                    "privacy": "Public aggregate counters only; no viewer identity, cookie, IP, or authenticated session is used.",
                }
                snapshot_id = digest(snapshot)
                _write_once(
                    self.snapshot_root / f"{snapshot_id}.json",
                    json.dumps(snapshot, sort_keys=True, ensure_ascii=False, indent=2) + "\n",
                )
                for metric, definition in METRICS.items():
                    value = getattr(metrics, metric)
                    row = ChannelEvidence(
                        channel="okx-orbit",
                        account=self.account,
                        publication_id=publication["publication_id"],
                        artifact_id=publication["artifact_id"],
                        published_content_sha256=publication["content_sha256"],
                        agent_candidate_sha256=publication["content_sha256"],
                        topic=publication["topic"],
                        title_version="okx-orbit:no-title:v1",
                        structure_version=publication["structure_version"],
                        strategy_version=publication["strategy_version"],
                        objective=f"okx_{metric}",
                        distribution_cohort=DISTRIBUTION_COHORT,
                        metric=metric,
                        metric_definition=definition,
                        unit="count",
                        value=float(value) if value is not None else None,
                        published_at=published_at,
                        window_start=published_at,
                        window_end=window_end,
                        observed_at=now,
                        revision=1,
                        source=SOURCE,
                        evidence_refs=(
                            f"okx-orbit-publication:{publication['post_id']}",
                            f"okx-orbit-snapshot:sha256:{snapshot_id}",
                        ),
                    )
                    path = self.store.record(row)
                    existing.add((row.publication_id, row.window_start, row.window_end, row.metric, row.source))
                    recorded.append({
                        "publication_id": publication["publication_id"], "window": label,
                        "metric": metric, "value": value, "evidence_path": str(path),
                    })

        status = "RECORDED" if recorded else "MISSED_WINDOW" if missed else "NO_MATURE_WINDOWS"
        return self._report(status, eligible=len(publications), pending=pending, recorded=recorded, missed=missed)

    def _publications(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        root = self.state_root / "content" / "continuous" / "cycles"
        for cycle_dir in sorted(root.glob("*")):
            receipt = _read_json(cycle_dir / "okx_orbit_publication.json")
            drafts = _read_json(cycle_dir / "social_expression_drafts.json")
            website = _read_json(cycle_dir / "publication.json")
            if receipt.get("status") != "POSTED" or drafts.get("status") != "DRAFT_READY":
                continue
            draft_rows = drafts.get("drafts") if isinstance(drafts.get("drafts"), dict) else {}
            draft = draft_rows.get("okx") if isinstance(draft_rows.get("okx"), dict) else {}
            text = str(draft.get("text") or "").strip()
            attr = website.get("outcome_attribution") if isinstance(website.get("outcome_attribution"), dict) else {}
            if not text or not attr:
                continue
            published_at = str(receipt.get("published_at") or "")
            url = str(receipt.get("url") or "")
            post_id = str(receipt.get("post_id") or "")
            if not published_at or not post_id or not url:
                continue
            content_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
            intent = str(draft.get("intent") or "UNKNOWN")
            format_name = str(draft.get("format") or "SHORT_POST")
            resolution = drafts.get("evidence_decision") if isinstance(drafts.get("evidence_decision"), dict) else {}
            resolution_row = resolution.get("resolution") if isinstance(resolution.get("resolution"), dict) else {}
            rows.append({
                "publication_id": f"okx-orbit:{post_id}",
                "post_id": post_id,
                "url": url,
                "published_at": published_at,
                "artifact_id": str(website.get("artifact_id") or attr.get("artifact_id") or cycle_dir.name),
                "content_sha256": content_sha256,
                "topic": str(attr.get("topic") or "unknown"),
                "structure_version": f"okx-orbit:{format_name}:{intent}:v1",
                "strategy_version": f"okx-social-evidence:{str(resolution_row.get('action') or 'UNKNOWN')}:v1",
            })
        return rows

    @staticmethod
    def _report(status: str, **extra: Any) -> dict[str, Any]:
        return {
            "status": status,
            "source": SOURCE,
            "publication_authority": False,
            "learning_authority": False,
            "causal_claim": False,
            **extra,
        }


__all__ = [
    "MAX_CAPTURE_LAG", "METRICS", "OKXOrbitOutcomeCollector", "OrbitMetricsTransport",
    "OrbitPublicMetrics", "PlaywrightOrbitMetricsTransport", "SOURCE", "WINDOWS",
    "_parse_compact_count",
]
