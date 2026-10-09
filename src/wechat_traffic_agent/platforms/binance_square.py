"""Binance Square OpenAPI publication adapter for audited Ferro research.

Only the creator's dedicated posting key is accepted; trading API keys are
never used. Square is an opt-in distribution channel, not an Agent controller.
"""
from __future__ import annotations

import os
import re
from typing import Any

import httpx

from wechat_traffic_agent.publication.errors import NonRetryablePublication
from wechat_traffic_agent.publication.models import DistributionTask, PublicationEvent

ADD_CONTENT_URL = "https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add"
POST_URL = "https://www.binance.com/square/post/"
SUPPORTED_COINS = frozenset({"BTC", "ETH", "BNB", "SOL", "XRP", "DOGE", "ADA", "AVAX", "LINK", "TRX"})
MAX_POST_CHARS = 1500


class SquarePublicationUnverified(NonRetryablePublication):
    """The remote call may have posted; never blindly submit it again."""


class SquarePublicationRejected(NonRetryablePublication):
    """Square rejected the submission; changing the payload/key is required."""


def square_enabled() -> bool:
    return os.environ.get("CONTENT_BINANCE_SQUARE_ENABLED", "").lower() in {"1", "true", "yes", "on"}


def square_writes_enabled() -> bool:
    return os.environ.get("CONTENT_BINANCE_SQUARE_WRITES_ENABLED", "").lower() in {"1", "true", "yes", "on"}


def square_share_text(event: PublicationEvent) -> str:
    """Use the audited article, not new unverified market or investment claims."""
    if event.visibility != "PUBLIC" or not event.canonical_url.startswith("https://"):
        raise ValueError("Square syndication requires a public HTTPS article")
    coins = [str(topic).upper() for topic in event.topics]
    tags = " ".join("$" + symbol for symbol in dict.fromkeys(coins) if symbol in SUPPORTED_COINS)
    suffix = "\n\n原文与研究证据：" + event.canonical_url + ("\n" + tags if tags else "")
    title = " ".join(event.title.split())[:160]
    summary = " ".join(event.summary.split())
    budget = MAX_POST_CHARS - len(title) - len(suffix) - 2
    if not title or budget <= 0:
        raise ValueError("Square article title or canonical URL is invalid")
    if len(summary) > budget:
        summary = summary[:max(0, budget - 1)].rsplit(" ", 1)[0].rstrip(" ,.;") + "…"
    return (title + ("\n\n" + summary if summary else "") + suffix).strip()


class BinanceSquareClient:
    """Small synchronous HTTP transport; injectable client makes tests network-free."""

    def __init__(self, *, key: str | None = None, client: httpx.Client | None = None):
        self.key = key if key is not None else os.environ.get("BINANCE_SQUARE_OPENAPI_KEY", "")
        self.client = client

    def post_text(self, text: str) -> str:
        if not self.key.strip():
            raise SquarePublicationRejected("BINANCE_SQUARE_OPENAPI_KEY is missing")
        if not text.strip() or len(text) > MAX_POST_CHARS:
            raise SquarePublicationRejected("Square text must contain 1-1500 characters")
        request = self.client or httpx.Client(timeout=20.0)
        try:
            response = request.post(ADD_CONTENT_URL, headers={
                "X-Square-OpenAPI-Key": self.key,
                "Content-Type": "application/json",
                "clienttype": "binanceSkill",
            }, json={"contentType": 1, "bodyTextOnly": text})
        except httpx.RequestError as exc:
            raise SquarePublicationUnverified("Square network result unknown; manual reconciliation required") from exc
        finally:
            if self.client is None:
                request.close()
        return self._validated_id(response)

    @staticmethod
    def _validated_id(response: httpx.Response) -> str:
        try:
            payload: Any = response.json()
        except ValueError as exc:
            raise SquarePublicationUnverified("Square returned non-JSON; verify account before re-posting") from exc
        if not isinstance(payload, dict):
            raise SquarePublicationUnverified("Square returned an invalid receipt")
        code = str(payload.get("code") or "")
        if response.status_code >= 400 or code != "000000":
            if response.status_code >= 500 or response.status_code == 504:
                raise SquarePublicationUnverified(f"Square response HTTP {response.status_code}; reconcile manually")
            raise SquarePublicationRejected(f"Square rejected post: HTTP {response.status_code}, code {code or 'unknown'}")
        data = payload.get("data")
        identity = str(data.get("id") or "") if isinstance(data, dict) else ""
        if not re.fullmatch(r"[0-9]{1,30}", identity):
            raise SquarePublicationUnverified("Square returned success without a verifiable post ID")
        return identity


def publish_square(event: PublicationEvent, task: DistributionTask, *, client: BinanceSquareClient | None = None) -> dict:
    if task.channel != "binance_square":
        raise ValueError("not a Binance Square distribution task")
    if not square_enabled() or not square_writes_enabled():
        raise SquarePublicationRejected("Binance Square publishing is disabled")
    post_id = (client or BinanceSquareClient()).post_text(square_share_text(event))
    return {"status": "POSTED", "post_id": post_id, "url": POST_URL + post_id}
