"""Privacy-preserving Website outcome evidence from bounded Nginx access logs.

This module only creates immutable ChannelEvidence. It has no publication,
ContentMind, or strategy-selection authority. Legacy publications without an
explicit publication-attribution contract are intentionally ignored.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import gzip
import json
from pathlib import Path
import re
from typing import Iterable
from urllib.parse import urlsplit

from wechat_traffic_agent.channel_evidence import ChannelEvidence, ChannelEvidenceStore
from wechat_traffic_agent.content_admission import _write_once
from wechat_traffic_agent.web_editorial import digest
from wechat_traffic_agent.website_experiment import WebsiteExperimentBinding

SOURCE = "nginx-access-log:v1"
ATTRIBUTION_SCHEMAS = {
    "website-publication-attribution.v1",
    "website-publication-attribution.v2",
}
SNAPSHOT_SCHEMA = "website-pageview-snapshot:v1"
METRIC = "http_pageviews"
METRIC_DEFINITION = (
    "Count of non-obvious-bot HTTP GET 200 requests to the exact canonical "
    "article route during the window; not unique users or verified readers."
)
WINDOWS = (("1h", timedelta(hours=1)), ("4h", timedelta(hours=4)), ("24h", timedelta(hours=24)), ("48h", timedelta(hours=48)), ("7d", timedelta(days=7)))
MAX_LOG_FILES = 16
MAX_COMPRESSED_BYTES = 128 * 1024 * 1024
MAX_DECOMPRESSED_BYTES = 512 * 1024 * 1024
MAX_LOG_LINES = 2_000_000
_LOG_RE = re.compile(
    r'^\S+\s+\S+\s+\S+\s+\[([^\]]+)\]\s+"(\S+)\s+(\S+)\s+[^\"]+"\s+(\d{3})\s+\S+\s+"[^\"]*"\s+"([^\"]*)"'
)
_BOT_MARKERS = (
    "bot", "spider", "crawler", "slurp", "curl/", "wget/", "python-requests",
    "go-http-client", "healthcheck", "uptime", "monitoring",
)


@dataclass(frozen=True)
class _RequestEvent:
    observed_at: datetime
    route: str
    eligible: bool


@dataclass(frozen=True)
class _LogRead:
    events: tuple[_RequestEvent, ...]
    first_seen: datetime | None
    last_seen: datetime | None
    parsed_lines: int
    files: tuple[str, ...]


class WebsiteOutcomeCollector:
    """Create descriptive Website pageview evidence without retaining client data."""

    def __init__(self, *, state_root: Path, access_log: Path, account: str) -> None:
        self.state_root = Path(state_root)
        self.access_log = Path(access_log)
        self.account = str(account).strip().rstrip("/")
        if not self.account:
            raise ValueError("Website outcome account must be non-empty")
        self.store = ChannelEvidenceStore(self.state_root / "content" / "channel_evidence")
        self.snapshot_root = self.state_root / "content" / "channel_evidence_sources"

    def collect(self, *, now: datetime) -> dict:
        now = _aware_utc(now)
        publications, legacy = self._publications()
        if not publications:
            return _report("NO_ELIGIBLE_PUBLICATIONS", legacy=legacy)
        existing = {
            (row.publication_id, row.window_start, row.window_end, row.metric, row.source)
            for row in self.store.as_of(now)
        }
        due = []
        for publication in publications:
            attr = publication["outcome_attribution"]
            published_at = _aware_utc(datetime.fromisoformat(attr["published_at"]))
            for label, delta in WINDOWS:
                window_end = published_at + delta
                key = (publication["revision"], published_at, window_end, METRIC, SOURCE)
                if window_end <= now and key not in existing:
                    due.append((publication, attr, label, published_at, window_end))
        if not due:
            return _report("NO_MATURE_WINDOWS", legacy=legacy, eligible=len(publications))

        logs = _read_logs(self.access_log)
        recorded = []
        coverage_missing = []
        for publication, attr, label, window_start, window_end in due:
            if logs.first_seen is None or logs.last_seen is None or not (
                logs.first_seen <= window_start and logs.last_seen >= window_end
            ):
                coverage_missing.append({
                    "publication_id": publication["revision"], "window": label,
                })
                continue
            route = attr["public_route"]
            pageviews = sum(
                1 for event in logs.events
                if event.eligible and event.route == route and window_start <= event.observed_at < window_end
            )
            snapshot = {
                "schema_version": SNAPSHOT_SCHEMA,
                "publication_id": publication["revision"],
                "artifact_id": publication["artifact_id"],
                "route": route,
                "window": label,
                "window_start": window_start.isoformat(),
                "window_end": window_end.isoformat(),
                "observed_at": now.isoformat(),
                "pageviews": pageviews,
                "metric_definition": METRIC_DEFINITION,
                "strategy_version": attr["strategy_version"],
                "practice_version": attr.get("practice_version"),
                "experiment": attr.get("experiment"),
                "coverage": {
                    "first_seen": logs.first_seen.isoformat(),
                    "last_seen": logs.last_seen.isoformat(),
                    "parsed_lines": logs.parsed_lines,
                    "files": list(logs.files),
                },
                "privacy": "No client IP, raw user-agent, referrer, or request line is persisted.",
            }
            snapshot_id = digest(snapshot)
            _write_once(
                self.snapshot_root / f"{snapshot_id}.json",
                json.dumps(snapshot, sort_keys=True, ensure_ascii=False, indent=2) + "\n",
            )
            row = ChannelEvidence(
                channel="website",
                account=self.account,
                publication_id=publication["revision"],
                artifact_id=publication["artifact_id"],
                published_content_sha256=attr["published_content_sha256"],
                agent_candidate_sha256=attr["agent_candidate_sha256"],
                topic=attr["topic"],
                title_version=attr["title_version"],
                structure_version=attr["structure_version"],
                strategy_version=attr["strategy_version"],
                objective="website_pageviews",
                distribution_cohort="public-web-http-requests:v1",
                metric=METRIC,
                metric_definition=METRIC_DEFINITION,
                unit="count",
                value=float(pageviews),
                published_at=window_start,
                window_start=window_start,
                window_end=window_end,
                observed_at=now,
                revision=1,
                source=SOURCE,
                evidence_refs=(
                    f"website-publication:{publication['revision']}",
                    f"nginx-pageview-snapshot:sha256:{snapshot_id}",
                    *(
                        (f"website-experiment:{attr['experiment']['experiment_id']}",)
                        if isinstance(attr.get('experiment'), dict) else ()
                    ),
                ),
            )
            path = self.store.record(row)
            recorded.append({
                "publication_id": publication["revision"],
                "window": label,
                "pageviews": pageviews,
                "evidence_path": str(path),
            })
        status = "RECORDED" if recorded else "INSUFFICIENT_LOG_COVERAGE"
        return _report(
            status, legacy=legacy, eligible=len(publications), recorded=recorded,
            coverage_missing=coverage_missing,
        )

    def _publications(self) -> tuple[list[dict], int]:
        rows: list[dict] = []
        legacy = 0
        root = self.state_root / "content" / "continuous" / "cycles"
        for path in sorted(root.glob("*/publication.json")):
            raw = json.loads(path.read_text(encoding="utf-8"))
            attr = raw.get("outcome_attribution") if isinstance(raw, dict) else None
            if not isinstance(attr, dict) or attr.get("schema_version") not in ATTRIBUTION_SCHEMAS:
                legacy += 1
                continue
            required = {
                "published_at", "public_route", "published_content_sha256",
                "agent_candidate_sha256", "topic", "title_version",
                "structure_version", "strategy_version",
            }
            if not required <= set(attr):
                raise ValueError("publication outcome attribution is incomplete")
            published_at = _aware_utc(datetime.fromisoformat(attr["published_at"]))
            if not str(attr["public_route"]).startswith("/"):
                raise ValueError("publication route must be absolute")
            if attr.get("schema_version") == "website-publication-attribution.v2":
                binding = WebsiteExperimentBinding.model_validate(attr.get("experiment"))
                if binding.decided_at.astimezone(timezone.utc) > published_at:
                    raise ValueError("Website experiment was decided after publication")
                if attr["strategy_version"] != binding.strategy_version:
                    raise ValueError("Website experiment strategy attribution does not match binding")
            rows.append(raw)
        return rows, legacy


def aggregate_http_pageviews(
    access_log: Path,
    *,
    start: datetime,
    end: datetime,
) -> dict:
    """Aggregate non-bot HTTP pageviews without retaining client identifiers."""
    start, end = _aware_utc(start), _aware_utc(end)
    if not start < end:
        raise ValueError("HTTP audience window must be non-empty")
    logs = _read_logs(Path(access_log))
    eligible = [
        event for event in logs.events
        if event.eligible and start <= event.observed_at < end
    ]
    route_counts = Counter(event.route for event in eligible)
    hourly = (end - start) <= timedelta(days=2)
    buckets: dict[str, int] = {}
    for event in eligible:
        observed = event.observed_at.astimezone(timezone.utc)
        if hourly:
            bucket = observed.replace(minute=0, second=0, microsecond=0).isoformat()
        else:
            bucket = observed.date().isoformat()
        buckets[bucket] = buckets.get(bucket, 0) + 1
    return {
        "pageviews": len(eligible),
        "top_pages": [
            {"route": route, "pageviews": int(count)}
            for route, count in route_counts.most_common(20)
        ],
        "trend": [
            {"bucket": bucket, "pageviews": int(buckets[bucket])}
            for bucket in sorted(buckets)
        ],
        "coverage": {
            "first_seen": logs.first_seen.isoformat() if logs.first_seen else None,
            "last_seen": logs.last_seen.isoformat() if logs.last_seen else None,
            "parsed_lines": logs.parsed_lines,
            "files": list(logs.files),
            "partial": (
                logs.first_seen is None
                or logs.last_seen is None
                or logs.first_seen > start
                or logs.last_seen < end
            ),
        },
        "metric_definition": METRIC_DEFINITION,
        "privacy": (
            "Aggregated from parsed requests in memory; client IP, raw user-agent, "
            "referrer and request lines are not returned or persisted by this helper."
        ),
    }


def _report(status: str, **extra) -> dict:
    return {
        "status": status,
        "source": SOURCE,
        "publication_authority": False,
        "learning_authority": False,
        **extra,
    }


def _read_logs(access_log: Path) -> _LogRead:
    paths = _log_paths(access_log)
    total_compressed = sum(path.stat().st_size for path in paths)
    if total_compressed > MAX_COMPRESSED_BYTES:
        raise ValueError("Nginx log input exceeds compressed-byte budget")
    events: list[_RequestEvent] = []
    first_seen = last_seen = None
    parsed_lines = 0
    decompressed_bytes = 0
    for path in paths:
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                decompressed_bytes += len(line.encode("utf-8", errors="replace"))
                if decompressed_bytes > MAX_DECOMPRESSED_BYTES:
                    raise ValueError("Nginx log input exceeds decompressed-byte budget")
                if parsed_lines >= MAX_LOG_LINES:
                    raise ValueError("Nginx log input exceeds line budget")
                match = _LOG_RE.match(line)
                if match is None:
                    continue
                try:
                    observed_at = datetime.strptime(match.group(1), "%d/%b/%Y:%H:%M:%S %z").astimezone(timezone.utc)
                except ValueError:
                    continue
                parsed_lines += 1
                first_seen = observed_at if first_seen is None or observed_at < first_seen else first_seen
                last_seen = observed_at if last_seen is None or observed_at > last_seen else last_seen
                method, target, status, user_agent = match.group(2), match.group(3), match.group(4), match.group(5)
                route = urlsplit(target).path
                bot = any(marker in user_agent.casefold() for marker in _BOT_MARKERS)
                events.append(_RequestEvent(
                    observed_at=observed_at,
                    route=route,
                    eligible=(method == "GET" and status == "200" and not bot),
                ))
    return _LogRead(tuple(events), first_seen, last_seen, parsed_lines, tuple(path.name for path in paths))


def _log_paths(access_log: Path) -> tuple[Path, ...]:
    if not access_log.is_absolute():
        raise ValueError("Nginx access log path must be absolute")
    matches = [p for p in access_log.parent.glob(access_log.name + "*") if p.is_file()]
    matches.sort(key=lambda p: (p.stat().st_mtime_ns, p.name), reverse=True)
    if not matches:
        raise FileNotFoundError(str(access_log))
    if len(matches) > MAX_LOG_FILES:
        matches = matches[:MAX_LOG_FILES]
    return tuple(matches)


def _aware_utc(value: datetime) -> datetime:
    if value.utcoffset() is None:
        raise ValueError("timestamp timezone is required")
    return value.astimezone(timezone.utc)


__all__ = ["WebsiteOutcomeCollector", "SOURCE", "WINDOWS"]
