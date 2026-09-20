"""Privacy-preserving aggregate audience analytics for the LivingRuntime public site."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .web_outcome_evidence import aggregate_http_pageviews
from .website_engagement import RETURN_GAP, WebsiteEngagementEvent, WebsiteEngagementStore

WINDOWS = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}


def _utc(value: datetime) -> datetime:
    if value.utcoffset() is None:
        raise ValueError("audience analytics time must be timezone-aware")
    return value.astimezone(timezone.utc)


def _returning_visitors(events: list[WebsiteEngagementEvent]) -> int:
    by_visitor: dict[str, list[WebsiteEngagementEvent]] = defaultdict(list)
    for event in events:
        if event.event_type == "page_view":
            by_visitor[event.visitor_hash].append(event)
    returned = 0
    for rows in by_visitor.values():
        rows.sort(key=lambda row: row.observed_at)
        first = rows[0]
        if any(
            row.session_hash != first.session_hash
            and row.observed_at >= first.observed_at + RETURN_GAP
            for row in rows[1:]
        ):
            returned += 1
    return returned


def _trend(events: list[WebsiteEngagementEvent], *, start: datetime, end: datetime) -> list[dict[str, Any]]:
    duration = end - start
    hourly = duration <= timedelta(days=2)
    buckets: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.event_type != "page_view":
            continue
        observed = event.observed_at.astimezone(timezone.utc)
        if hourly:
            bucket_at = observed.replace(minute=0, second=0, microsecond=0)
            key = bucket_at.isoformat()
        else:
            bucket_at = observed.replace(hour=0, minute=0, second=0, microsecond=0)
            key = bucket_at.date().isoformat()
        row = buckets.setdefault(key, {"bucket": key, "pageviews": 0, "_visitors": set()})
        row["pageviews"] += 1
        row["_visitors"].add(event.visitor_hash)
    result = []
    for key in sorted(buckets):
        row = buckets[key]
        result.append({
            "bucket": row["bucket"],
            "pageviews": int(row["pageviews"]),
            "visitors": len(row["_visitors"]),
        })
    return result


class AudienceAnalytics:
    """Aggregate public traffic without returning visitor-level identifiers."""

    def __init__(self, state_root: Path, *, access_log: Path | None = None) -> None:
        self.state_root = Path(state_root)
        self.store = WebsiteEngagementStore(
            self.state_root / "content" / "website_engagement"
        )
        self.access_log = Path(access_log) if access_log is not None else None

    def _http_snapshot(self, *, start: datetime, end: datetime) -> dict[str, Any] | None:
        if self.access_log is None:
            return None
        try:
            payload = aggregate_http_pageviews(self.access_log, start=start, end=end)
        except (FileNotFoundError, OSError, ValueError):
            return {"status": "UNAVAILABLE"}
        return {"status": "OK", **payload}

    def snapshot(
        self,
        *,
        window: str = "24h",
        now: datetime | None = None,
    ) -> dict[str, Any]:
        if window not in WINDOWS:
            raise ValueError("window must be one of: 24h, 7d, 30d")
        end = _utc(now or datetime.now(timezone.utc)).replace(microsecond=0)
        requested_start = end - WINDOWS[window]
        http = self._http_snapshot(start=requested_start, end=end)
        http_ok = bool(http and http.get("status") == "OK")

        coverage_start = self.store.coverage_start()
        first_party_start = requested_start
        all_events: list[WebsiteEngagementEvent] = []
        if coverage_start is not None:
            coverage_start = _utc(coverage_start)
            first_party_start = max(requested_start, coverage_start)
            all_events = self.store.events(
                start=first_party_start,
                end=end + timedelta(seconds=1),
            )

        excluded_operator_events = [
            row for row in all_events
            if getattr(row, "audience_class", "public") == "operator"
        ]
        events = [
            row for row in all_events
            if getattr(row, "audience_class", "public") != "operator"
        ]
        pageviews = [row for row in events if row.event_type == "page_view"]
        visitors = {row.visitor_hash for row in pageviews}
        sessions = {row.session_hash for row in pageviews}
        returning = _returning_visitors(events)
        engaged = {
            row.visitor_hash
            for row in events
            if row.event_type == "engaged_30s"
        }
        internal_clicks = sum(
            row.event_type == "internal_link_click" for row in events
        )

        page_counts = Counter(row.route for row in pageviews)
        acquisition_counts = Counter(
            getattr(row, "acquisition", "unknown") or "unknown"
            for row in pageviews
        )
        device_counts = Counter(
            getattr(row, "device_class", "unknown") or "unknown"
            for row in pageviews
        )

        ferro_visitors = {
            row.visitor_hash for row in pageviews
            if row.route.startswith("/agents/ferro/")
            or row.route in {"/live/", "/neural/", "/brain/", "/talk/"}
        }
        neural_visitors = {
            row.visitor_hash for row in pageviews
            if row.route in {"/neural/", "/agents/ferro/neural/"}
        }
        talk_visitors = {
            row.visitor_hash for row in pageviews
            if row.route in {"/talk/", "/agents/ferro/talk/"}
        }

        def ranked(counter: Counter[str], *, limit: int = 12) -> list[dict[str, Any]]:
            return [
                {"name": name, "count": int(count)}
                for name, count in counter.most_common(limit)
            ]

        first_party_top_pages = [
            {"route": route, "pageviews": int(count)}
            for route, count in page_counts.most_common(16)
        ]
        first_party_trend = _trend(events, start=first_party_start, end=end)
        selected_pageviews = int(http["pageviews"]) if http_ok else len(pageviews)
        selected_top_pages = list(http["top_pages"]) if http_ok else first_party_top_pages
        selected_trend = list(http["trend"]) if http_ok else first_party_trend
        first_party_partial = coverage_start is None or coverage_start > requested_start
        if coverage_start is None and http_ok:
            status = "HTTP_ONLY"
        elif coverage_start is None:
            status = "NO_COVERAGE"
        else:
            status = "OK"

        visitor_count = len(visitors)
        return {
            "status": status,
            "window": window,
            "window_start": requested_start.isoformat(),
            "window_end": end.isoformat(),
            "coverage_start": coverage_start.isoformat() if coverage_start else None,
            "coverage_partial": first_party_partial,
            "summary": {
                "visitors": visitor_count,
                "pageviews": selected_pageviews,
                "pageview_source": "NGINX_HTTP" if http_ok else "FIRST_PARTY",
                "http_pageviews": int(http["pageviews"]) if http_ok else None,
                "first_party_pageviews": len(pageviews),
                "sessions": len(sessions),
                "returning_visitors": returning,
                "returning_rate": round(returning / visitor_count, 4) if visitor_count else 0.0,
                "engaged_readers_30s": len(engaged),
                "internal_link_clicks": internal_clicks,
                "excluded_operator_events": len(excluded_operator_events),
            },
            "top_pages": selected_top_pages,
            "acquisition": ranked(acquisition_counts),
            "devices": ranked(device_counts),
            "trend": selected_trend,
            "funnel": [
                {"stage": "Visit", "visitors": visitor_count},
                {"stage": "View Ferro", "visitors": len(ferro_visitors)},
                {"stage": "Open Neural Life", "visitors": len(neural_visitors)},
                {"stage": "Talk", "visitors": len(talk_visitors)},
            ],
            "first_party": {
                "status": "OK" if coverage_start is not None else "NO_COVERAGE",
                "window_start": first_party_start.isoformat(),
                "coverage_start": coverage_start.isoformat() if coverage_start else None,
                "coverage_partial": first_party_partial,
                "top_pages": first_party_top_pages,
                "trend": first_party_trend,
            },
            "http": http,
            "privacy": {
                "raw_ip_persisted": False,
                "raw_user_agent_persisted": False,
                "raw_referrer_persisted": False,
                "visitor_level_rows_exposed": False,
                "coarse_acquisition_only": True,
                "coarse_device_class_only": True,
                "known_operator_browser_events_excluded": True,
                "historical_unclassified_events_may_include_operator": True,
                "http_aggregate_retains_client_identifiers": False,
            },
        }


__all__ = ["AudienceAnalytics", "WINDOWS"]
