"""Bounded Google Search Console ingestion for Website traffic learning."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

from wechat_traffic_agent.traffic_learning import SearchDemandObservation, SearchDemandStore
from wechat_traffic_agent.web_editorial import digest

TOKEN_URL = "https://oauth2.googleapis.com/token"
API_ROOT = "https://www.googleapis.com/webmasters/v3"
SOURCE = "google-search-console:v1"
PACIFIC = ZoneInfo("America/Los_Angeles")


def _enabled(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=".pending-gsc-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


class GoogleSearchConsoleCollector:
    """Import finalized Search Analytics rows without granting publication authority."""

    def __init__(self, *, state_root: Path, account: str, property_url: str,
                 client_id: str, client_secret: str, refresh_token: str,
                 refresh_hours: int = 6, lookback_days: int = 7,
                 client: httpx.Client | None = None) -> None:
        if not account.strip() or not property_url.strip():
            raise ValueError("GSC account and property must be non-empty")
        if not client_id.strip() or not refresh_token.strip():
            raise ValueError("GSC client_id and refresh_token are required")
        if not 1 <= refresh_hours <= 24 or not 1 <= lookback_days <= 30:
            raise ValueError("GSC refresh_hours must be 1..24 and lookback_days 1..30")
        self.state_root = Path(state_root)
        self.account = account.strip().rstrip("/")
        self.property_url = property_url.strip()
        self.client_id = client_id.strip()
        self.client_secret = client_secret.strip()
        self.refresh_token = refresh_token.strip()
        self.refresh_interval = timedelta(hours=refresh_hours)
        self.lookback_days = lookback_days
        self.store = SearchDemandStore(self.state_root / "content" / "search_demand")
        self.sync_path = self.state_root / "content" / "search_demand" / "gsc_sync.json"
        self.client = client or httpx.Client(timeout=30, follow_redirects=False, trust_env=False)
        self._owns_client = client is None

    @classmethod
    def from_environment(cls, *, state_root: Path, account: str,
                         client: httpx.Client | None = None) -> "GoogleSearchConsoleCollector":
        if not _enabled(os.environ.get("CONTENT_GSC_ENABLED")):
            raise ValueError("CONTENT_GSC_ENABLED is not enabled")
        return cls(
            state_root=state_root,
            account=account,
            property_url=os.environ.get("CONTENT_GSC_PROPERTY", ""),
            client_id=os.environ.get("CONTENT_GSC_CLIENT_ID", ""),
            client_secret=os.environ.get("CONTENT_GSC_CLIENT_SECRET", ""),
            refresh_token=os.environ.get("CONTENT_GSC_REFRESH_TOKEN", ""),
            refresh_hours=int(os.environ.get("CONTENT_GSC_REFRESH_HOURS", "6")),
            lookback_days=int(os.environ.get("CONTENT_GSC_LOOKBACK_DAYS", "7")),
            client=client,
        )

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def _last_success(self) -> datetime | None:
        if not self.sync_path.exists():
            return None
        payload = json.loads(self.sync_path.read_text(encoding="utf-8"))
        value = datetime.fromisoformat(str(payload["observed_at"]).replace("Z", "+00:00"))
        if value.utcoffset() is None:
            raise ValueError("persisted GSC observed_at must be timezone-aware")
        return value.astimezone(timezone.utc)

    def _access_token(self) -> str:
        data = {
            "client_id": self.client_id,
            "refresh_token": self.refresh_token,
            "grant_type": "refresh_token",
        }
        if self.client_secret:
            data["client_secret"] = self.client_secret
        try:
            response = self.client.post(TOKEN_URL, data=data)
            response.raise_for_status()
            token = response.json().get("access_token")
        except (httpx.HTTPError, ValueError) as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            suffix = f" HTTP {status}" if status else ""
            raise RuntimeError("GSC OAuth refresh failed" + suffix) from None
        if not isinstance(token, str) or not token.strip():
            raise RuntimeError("GSC OAuth refresh returned no access token")
        return token.strip()

    def _query_page(self, *, token: str, start_date: date, end_date: date,
                    start_row: int) -> list[dict]:
        site = quote(self.property_url, safe="")
        url = f"{API_ROOT}/sites/{site}/searchAnalytics/query"
        payload = {
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
            "dimensions": ["date", "query", "page"],
            "type": "web",
            "dataState": "final",
            "rowLimit": 25000,
            "startRow": start_row,
        }
        try:
            response = self.client.post(url, headers={"Authorization": "Bearer " + token}, json=payload)
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            suffix = f" HTTP {status}" if status else ""
            raise RuntimeError("GSC Search Analytics query failed" + suffix) from None
        rows = body.get("rows", [])
        if not isinstance(rows, list):
            raise RuntimeError("GSC Search Analytics rows must be a list")
        return rows

    def _observation(self, *, row: dict, observed_at: datetime) -> SearchDemandObservation:
        keys = row.get("keys")
        if not isinstance(keys, list) or len(keys) != 3:
            raise RuntimeError("GSC row must contain date, query, page keys")
        day = date.fromisoformat(str(keys[0]))
        query_text, page = str(keys[1]).strip(), str(keys[2]).strip()
        if not query_text or not page:
            raise RuntimeError("GSC query and page must be non-empty")
        clicks_raw, impressions_raw = float(row.get("clicks", 0)), float(row.get("impressions", 0))
        if clicks_raw < 0 or impressions_raw < 0 or not clicks_raw.is_integer() or not impressions_raw.is_integer():
            raise RuntimeError("GSC clicks and impressions must be non-negative integers")
        clicks, impressions = int(clicks_raw), int(impressions_raw)
        start = datetime.combine(day, time.min, tzinfo=PACIFIC)
        end = start + timedelta(days=1)
        identity_ref = digest({
            "property": self.property_url, "date": day.isoformat(),
            "query": query_text, "page": page, "type": "web",
        })
        return SearchDemandObservation(
            site=self.account, query=query_text, page=page,
            clicks=clicks, impressions=impressions,
            ctr=(clicks / impressions if impressions else 0.0),
            position=(float(row["position"]) if row.get("position") is not None else None),
            window_start=start, window_end=end, observed_at=observed_at,
            revision=1, source=SOURCE, evidence_ref="gsc:" + identity_ref,
        )

    def _reconcile(self, row: SearchDemandObservation) -> dict:
        key = digest(row.identity())
        folder = self.store.root / key
        latest = None
        latest_path = None
        for path in sorted(folder.glob("*.json")) if folder.exists() else ():
            candidate = SearchDemandObservation.model_validate_json(path.read_text(encoding="utf-8"))
            if digest(candidate.identity()) != key or path.name != f"{candidate.revision:05d}.json":
                raise ValueError("search-demand filename/identity mismatch")
            if latest is None or candidate.revision > latest.revision:
                latest, latest_path = candidate, path
        if latest is not None:
            material = ("clicks", "impressions", "ctr", "position")
            if all(getattr(latest, name) == getattr(row, name) for name in material):
                return {"status": "UNCHANGED", "path": str(latest_path), "revision": latest.revision}
            if latest.revision >= 10000:
                raise ValueError("search-demand revision budget exhausted")
            row = row.model_copy(update={"revision": latest.revision + 1})
        path = self.store.record(row)
        return {"status": "RECORDED", "path": str(path), "revision": row.revision}

    def collect(self, *, now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        if now.utcoffset() is None:
            raise ValueError("GSC collection time must be timezone-aware")
        now = now.astimezone(timezone.utc).replace(microsecond=0)
        previous = self._last_success()
        if previous is not None and now - previous < self.refresh_interval:
            return {
                "status": "SKIPPED_RECENT", "last_success_at": previous.isoformat(),
                "source": SOURCE, "publication_authority": False,
            }
        local_today = now.astimezone(PACIFIC).date()
        end_date = local_today - timedelta(days=1)
        start_date = end_date - timedelta(days=self.lookback_days - 1)
        token = self._access_token()
        start_row, pages, seen, recorded, unchanged = 0, 0, 0, 0, 0
        while True:
            rows = self._query_page(token=token, start_date=start_date, end_date=end_date,
                                    start_row=start_row)
            pages += 1
            for raw in rows:
                row = self._observation(row=raw, observed_at=now)
                result = self._reconcile(row)
                seen += 1
                if result["status"] == "RECORDED":
                    recorded += 1
                else:
                    unchanged += 1
            if len(rows) < 25000:
                break
            start_row += len(rows)
            if start_row >= 50000:
                break
        receipt = {
            "schema_version": "gsc-search-sync.v1",
            "status": "RECORDED",
            "source": SOURCE,
            "property": self.property_url,
            "account": self.account,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "data_state": "final",
            "rows_seen": seen,
            "rows_recorded": recorded,
            "rows_unchanged": unchanged,
            "pages": pages,
            "observed_at": now.isoformat(),
            "publication_authority": False,
        }
        _atomic_json(self.sync_path, receipt)
        return receipt


__all__ = ["GoogleSearchConsoleCollector"]
