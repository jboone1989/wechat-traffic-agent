"""AIHot as Ferro's single routine-news source.

Discovery and editorial selection belong to the independent site. Research may
still fetch primary evidence on demand; this module never treats summaries as
proof of a claim.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
import hashlib
import json

import httpx


def _site_origin(value: str) -> str:
    parsed = urlsplit(value.strip().rstrip("/"))
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("AIHot news URL must be an absolute HTTPS origin")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("AIHot news URL must be an origin, not an arbitrary path")
    return value.strip().rstrip("/")


def _item(row: dict, now: datetime) -> dict | None:
    if not isinstance(row, dict):
        return None
    ident = str(row.get("id") or "").strip()
    links = row.get("links")
    source = row.get("source")
    url = str(links.get("original") or "").strip() if isinstance(links, dict) else ""
    title = str(row.get("title") or "").strip()
    summary = str(row.get("summary") or "").strip()
    stamp = row.get("publishedAt") or row.get("discoveredAt")
    if not ident or not title or not summary or not isinstance(stamp, str):
        return None
    if urlsplit(url).scheme not in {"http", "https"}:
        return None
    try:
        published = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if published.utcoffset() is None or not now - timedelta(days=7) <= published <= now:
        return None
    publisher = str(source.get("name") or "source").strip() if isinstance(source, dict) else "source"
    # Identity follows the site's canonical material ID. Different summaries of the
    # same article cannot trick Ferro into considering it a new news event.
    return {
        "news_id": "aihot:" + ident,
        "title": title[:300],
        "summary": summary[:2000],
        "url": url[:1000],
        "publisher_feed": publisher[:200],
        "published_at": published.astimezone(timezone.utc).isoformat(),
    }


async def collect_site_news(
    base_url: str,
    now: datetime,
    *,
    client: httpx.AsyncClient | None = None,
    max_pages: int = 3,
) -> dict:
    """Return the legacy content-loop shape from AIHot's public v1 API."""
    origin = _site_origin(base_url)
    if now.utcoffset() is None:
        raise ValueError("timezone-aware now required")
    if not 1 <= max_pages <= 3:
        raise ValueError("page budget out of range")
    rows: dict[str, dict] = {}
    failures: list[dict] = []
    own = client is None
    http = client or httpx.AsyncClient(timeout=20, trust_env=False, follow_redirects=False)
    try:
        cursor: str | None = None
        for _ in range(max_pages):
            params = {"mode": "selected", "window": "7d", "by": "published", "limit": "100"}
            if cursor:
                params["cursor"] = cursor
            try:
                response = await http.get(f"{origin}/api/v1/items", params=params, headers={"Accept": "application/json"})
                response.raise_for_status()
                if len(response.content) > 2_000_000:
                    raise ValueError("AIHot response exceeds budget")
                payload = response.json()
                if not isinstance(payload, dict) or payload.get("schemaVersion") != 1 or not isinstance(payload.get("items"), list):
                    raise ValueError("unexpected AIHot items schema")
                for raw in payload["items"]:
                    item = _item(raw, now)
                    if item:
                        rows[item["news_id"]] = item
                page = payload.get("page") or {}
                cursor = page.get("nextCursor") if page.get("hasMore") else None
                if not isinstance(cursor, str) or not cursor:
                    break
            except (httpx.HTTPError, ValueError, json.JSONDecodeError) as exc:
                failures.append({"source": "aihot", "error_type": type(exc).__name__})
                break
    finally:
        if own:
            await http.aclose()
    return {
        "items": sorted(rows.values(), key=lambda r: (r["published_at"], r["news_id"]), reverse=True)[:60],
        "failures": failures,
        "observed_at": now.isoformat(),
        "signal_kind": "AIHot selected publisher coverage; summaries are research leads, not primary evidence",
    }


async def collect_site_spot_market(base_url: str, now: datetime, *, client: httpx.AsyncClient | None = None) -> list[dict]:
    """Collect five major assets from AIHot's normalized, exchange-attributed spot API."""
    origin = _site_origin(base_url)
    own = client is None
    http = client or httpx.AsyncClient(timeout=20, trust_env=False, follow_redirects=False)
    assets = ("BTC", "ETH", "SOL", "XRP", "BNB")
    rows = []
    try:
        for asset in assets:
            endpoint = origin + "/api/market/spot"
            response = await http.get(endpoint, params={"asset": asset}, headers={"Accept": "application/json"})
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict) or data.get("schemaVersion") != 1 or data.get("asset") != asset:
                raise ValueError("invalid AIHot market schema")
            quotes = data.get("observations")
            if not isinstance(quotes, list) or len(quotes) == 0:
                raise ValueError("missing market quotes for " + asset)
            for quote in quotes:
                if not isinstance(quote, dict) or quote.get("exchange") not in ("Binance", "OKX"):
                    raise ValueError("invalid market quote")
                price = float(quote["priceUsdt"])
                move = float(quote["change24hPercent"])
                if not 0 < price < 10_000_000 or not -100 <= move <= 1000:
                    raise ValueError("market price or move out of range")
                source_url = str(quote["sourceUrl"])
                if urlsplit(source_url).scheme != "https":
                    raise ValueError("market provenance must be HTTPS")
                rows.append({
                    "asset": asset, "exchange": quote["exchange"], "price": price,
                    "change": move, "at": str(quote["exchangeAt"]), "source_url": source_url,
                })
    finally:
        if own:
            await http.aclose()
    revision = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
    summaries = [f'{r["asset"]} {r["exchange"]}: {r["price"]:g} USDT, 24h {r["change"]:+.2f}%'
                 for r in rows]
    return [{
        "evidence_id": "aihot-market:" + revision[:32],
        "source_kind": "public-api",
        "source": "AIHot spot markets (Binance and OKX)",
        "title": "Major crypto spot market snapshot",
        "summary": "; ".join(summaries)[:2000],
        "url": origin + "/api/market/spot?asset=BTC",
        "observed_at": now.isoformat(),
        "source_revision": "sha256:" + revision,
        "evidence_refs": ("aihot-market:sha256:" + revision,),
        "published_at": None,
    }]
