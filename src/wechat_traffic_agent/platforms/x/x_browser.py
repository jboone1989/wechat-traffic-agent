"""Provider-routed X Web publishing with independently verified outcomes."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Protocol

from wechat_traffic_agent.public_activity import emit_public_activity
from wechat_traffic_agent.x_social import XHistoryLedger

X_HOME_URL = "https://x.com/home"
X_COMPOSE_URL = "https://x.com/compose/post"
X_PREMIUM_LONG_POST_MAX_CHARS = 25_000


class XBrowserSessionUnavailable(RuntimeError):
    """No browser transport can safely start the requested X session."""


class XBrowserPublicationUnverified(RuntimeError):
    """A browser write could not be tied to a public X post URL."""


@dataclass(frozen=True)
class XBrowserPublishResult:
    post_id: str
    url: str
    handle: str
    duplicate_found: bool = False
    provider: str = "UNKNOWN"
    observed_text: str | None = None
    verified_links: tuple[dict[str, Any], ...] = ()


class XBrowserTransport(Protocol):
    async def publish(self, *, text: str) -> XBrowserPublishResult: ...


def _enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def x_browser_enabled() -> bool:
    return _enabled("CONTENT_X_BROWSER_ENABLED") or _enabled("CONTENT_X_PUBLICATION_ENABLED")


def x_browser_writes_enabled() -> bool:
    return _enabled("CONTENT_X_BROWSER_WRITES_ENABLED") or _enabled("CONTENT_X_PUBLICATION_ENABLED")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _draft_sha256(*, cycle: str, draft: dict[str, Any]) -> str:
    raw = json.dumps({"cycle": cycle, "draft": draft}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _normalize_text(value: str) -> str:
    return " ".join(value.split())


def _post_id_from_url(url: str) -> str | None:
    match = re.search(r"https://x\.com/(?:[^/?#]+/status|i/article)/(\d+)(?:[/?#]|$)", url)
    return match.group(1) if match else None


class XBrowserTransportRouter:
    """Single X Web route through Agent Runtime's persistent browser profile."""

    def __init__(self, *, state_root: Path, account_id: str | None = None) -> None:
        self.state_root = Path(state_root)
        self.account_id = account_id

    async def identity(self) -> dict[str, Any]:
        from wechat_traffic_agent.x_local_browser import LocalHarnessXTransport
        return await LocalHarnessXTransport.from_environment(
            state_root=self.state_root, **({"account_id": self.account_id} if self.account_id else {})
        ).identity()

    async def publish(self, *, text: str) -> XBrowserPublishResult:
        from wechat_traffic_agent.x_local_browser import LocalHarnessXTransport

        transport = LocalHarnessXTransport.from_environment(
            state_root=self.state_root, **({"account_id": self.account_id} if self.account_id else {})
        )
        return await transport.publish(text=text)

    async def publish_article(self, **kwargs):
        from wechat_traffic_agent.x_local_browser import LocalHarnessXTransport
        return await LocalHarnessXTransport.from_environment(state_root=self.state_root, **({"account_id": self.account_id} if self.account_id else {})).publish_article(**kwargs)

    async def discover(self, *, source="MENTIONS", query=None, max_results=20):
        from wechat_traffic_agent.x_local_browser import LocalHarnessXTransport
        return await LocalHarnessXTransport.from_environment(state_root=self.state_root, **({"account_id": self.account_id} if self.account_id else {})).discover(source=source,query=query,max_results=max_results)

    async def reply(self, *, text, target_url):
        from wechat_traffic_agent.x_local_browser import LocalHarnessXTransport
        return await LocalHarnessXTransport.from_environment(state_root=self.state_root, **({"account_id": self.account_id} if self.account_id else {})).reply(text=text,target_url=target_url)


class XBrowserPublisher:
    def __init__(
        self,
        *,
        state_root: Path,
        transport: XBrowserTransport | None = None,
        enabled: bool | None = None,
        writes_enabled: bool | None = None,
        growth_mission: Any | None = None,
        mission_baseline_observer: Any | None = None,
    ) -> None:
        self.state_root = Path(state_root)
        self.transport = transport
        self.enabled = x_browser_enabled() if enabled is None else bool(enabled)
        self.writes_enabled = x_browser_writes_enabled() if writes_enabled is None else bool(writes_enabled)
        self.growth_mission = growth_mission
        self.mission_baseline_observer = mission_baseline_observer

    def _folder(self, cycle: str) -> Path:
        return self.state_root / "content" / "continuous" / "cycles" / cycle

    def _start_mission_experiment(
        self,
        *,
        cycle: str,
        post_id: str,
        now: datetime,
    ) -> dict[str, Any] | None:
        if self.growth_mission is None or not str(post_id).strip():
            return None
        receipt_id = f"x-browser-publication:{cycle}:{str(post_id).strip()}"
        try:
            return self.growth_mission.start_experiment_from_publication(
                cycle=cycle,
                publication_receipt_id=receipt_id,
                post_id=str(post_id).strip(),
                now=now,
            )
        except Exception as exc:
            return {
                "status": "DEGRADED",
                "error_type": type(exc).__name__,
                "message": str(exc)[:300],
            }

    async def after_content_cycle(self, *, cycle: str, now: datetime) -> dict[str, Any]:
        if not cycle:
            return {"status": "NO_CYCLE"}
        if not self.enabled:
            return {"status": "DISABLED", "cycle": cycle}
        if not self.writes_enabled:
            return {"status": "WRITE_DISABLED", "cycle": cycle}
        folder = self._folder(cycle)
        payload = _read_json(folder / "social_expression_drafts.json")
        if payload.get("status") != "DRAFT_READY":
            return {"status": "NO_READY_DRAFT", "cycle": cycle}
        strategy = (
            payload.get("x_strategy")
            if isinstance(payload.get("x_strategy"), dict)
            else {}
        )
        if not strategy:
            return {
                "status": "L1_X_STRATEGY_MISSING",
                "cycle": cycle,
            }
        if (
            str(strategy.get("post_timing") or "") == "HOLD"
            or str(strategy.get("interaction_mode") or "ORIGINAL") != "ORIGINAL"
        ):
            return {
                "status": "SKIPPED",
                "cycle": cycle,
                "reason": "L1_X_STRATEGY",
                "x_strategy": strategy,
            }
        drafts = payload.get("drafts") if isinstance(payload.get("drafts"), dict) else {}
        draft = drafts.get("x") if isinstance(drafts.get("x"), dict) else {}
        if draft.get("channel") != "X":
            return {"status": "NO_X_DRAFT", "cycle": cycle}
        fmt = str(draft.get("format") or "")
        if fmt not in {"SHORT_POST", "LONG_POST", "ARTICLE"}:
            return {"status": "UNSUPPORTED_FORMAT", "cycle": cycle, "format": fmt}
        text = str(draft.get("text") or "")
        if not text.strip() or len(text) > X_PREMIUM_LONG_POST_MAX_CHARS:
            return {"status": "INVALID_DRAFT", "cycle": cycle}
        if fmt == "ARTICLE" and not str(draft.get("title") or "").strip():
            return {"status":"INVALID_ARTICLE_TITLE", "cycle":cycle}
        receipt_path = folder / "x_browser_publication.json"
        digest = _draft_sha256(cycle=cycle, draft=draft)
        existing = _read_json(receipt_path)
        if existing.get("status") == "POSTED":
            if existing.get("draft_sha256") != digest:
                return {"status": "RECEIPT_DRAFT_MISMATCH", "cycle": cycle}
            transition = self._start_mission_experiment(
                cycle=cycle,
                post_id=str(existing.get("post_id") or ""),
                now=now,
            )
            if transition is not None:
                existing["mission_experiment_transition"] = transition
                _write_json(receipt_path, existing)
            return {
                "status": "ALREADY_POSTED",
                "cycle": cycle,
                "publication": existing,
                "mission_experiment_transition": transition,
            }
        learning = _read_json(folder / "learning_result.json")
        from wechat_traffic_agent.learning_expression_quality import learning_required_for_sharing
        if not learning and learning_required_for_sharing(self.state_root):
            return {"status": "HELD_BY_LEARNING_QUALITY", "cycle": cycle,
                    "reason": "NO_VERIFIED_LEARNING_RECORD"}
        if learning:
            checked_texts = {text, str(draft.get("title") or "") + "\n" + text}
            checked_hashes = {hashlib.sha256(value.encode("utf-8")).hexdigest() for value in checked_texts}
            reviews = [_read_json(folder / name) for name in (
                "x_learning_quality_0.json", "x_learning_quality_1.json",
                "x_longform_learning_quality_0.json", "x_longform_learning_quality_1.json")]
            if not any(review.get("passed") is True and review.get("learning_cycle") == learning.get("cycle")
                       and review.get("text_sha256") in checked_hashes for review in reviews):
                return {"status": "HELD_BY_LEARNING_QUALITY", "cycle": cycle,
                        "reason": "CURRENT_DRAFT_HAS_NO_PASSED_LEARNING_REVIEW"}
        baseline = None
        binding_method = getattr(self.growth_mission, "publication_binding", None)
        binding = binding_method(cycle=cycle) if callable(binding_method) else None
        if isinstance(binding, dict) and binding.get("bound"):
            observer = self.mission_baseline_observer
            if observer is None:
                baseline = {"status": "MEASUREMENT_UNAVAILABLE", "reason": "No follower baseline observer"}
            else:
                try:
                    baseline = await observer(cycle=cycle, now=now)
                except Exception as exc:
                    baseline = {"status": "MEASUREMENT_FAILED", "error_type": type(exc).__name__, "detail": str(exc)}
            _write_json(folder / "x_publication_measurement.json", baseline)
        transport = self.transport
        if transport is None:
            transport = XBrowserTransportRouter(state_root=self.state_root)
        if fmt == "ARTICLE":
            from wechat_traffic_agent.x_article_media import prepare_article_media
            import httpx
            try:
                media=await prepare_article_media(draft=draft,folder=folder)
            except (RuntimeError, httpx.HTTPError, OSError, ValueError) as exc:
                failed={"status":"ARTICLE_MEDIA_FAILED","cycle":cycle,"error_type":type(exc).__name__,"detail":str(exc)}
                _write_json(folder/"x_article_media_failure.json",failed)
                return failed
            for image in media["inline"]:
                caption = image.get("caption") or image["alt_text"]
                anchor = image.get("after_text")
                if anchor:
                    if text.count(anchor) != 1:
                        return {"status": "INVALID_IMAGE_PLACEMENT", "cycle": cycle}
                    if caption not in text:
                        text = text.replace(anchor, anchor + "\n\n" + caption, 1)
                else:
                    text += "\n\n" + caption
            credits=[r.get("attribution") for r in [media["cover"],*media["inline"]] if r.get("attribution")]
            if credits:
                text += "\n\nImage credits:\n" + "\n".join(credits)
        emit_public_activity(self.state_root, cycle=cycle, phase="X", title="Publishing on X",
                             detail=text[:220], status="working", kind="social")
        try:
            if fmt == "ARTICLE":
                published=await transport.publish_article(title=str(draft["title"]),text=text,image_path=media["cover"]["path"],
                    inline_images=media["inline"],
                    transaction_path=folder/"x_article_transaction.json")
            else:
                published = await transport.publish(text=text)
        except XBrowserPublicationUnverified as exc:
            return {"status": "PUBLICATION_UNVERIFIED", "cycle": cycle, "detail": str(exc)}
        except (XBrowserSessionUnavailable, RuntimeError) as exc:
            return {"status": "SESSION_UNAVAILABLE", "cycle": cycle, "detail": str(exc)}
        observed_text = getattr(published, "observed_text", None)
        public_text = observed_text if observed_text is not None else text
        verified_links = list(getattr(published, "verified_links", ()))
        publication = {
            "schema_version": "content-x-browser-publication.v1", "status": "POSTED",
            "cycle": cycle, "published_at": (datetime.now(timezone.utc) if fmt=="ARTICLE" else now).isoformat(), "draft_sha256": digest,
            "format":fmt, "article_media":media if fmt=="ARTICLE" else None,
            "mission_baseline": baseline,
            "published_text":public_text, "verified_links":verified_links,
            "post_id": published.post_id, "url": published.url, "handle": published.handle,
            "duplicate_found": published.duplicate_found,
            "source": f"{published.provider} + X Web UI", "publication_authority": True,
        }
        _write_json(receipt_path, publication)
        mission_experiment_transition = self._start_mission_experiment(
            cycle=cycle,
            post_id=published.post_id,
            now=now,
        )
        if mission_experiment_transition is not None:
            publication["mission_experiment_transition"] = mission_experiment_transition
            _write_json(receipt_path, publication)
        history = XHistoryLedger(self.state_root)
        if not history.has_event(cycle=cycle, event_type="EXPRESSION"):
            cognition = _read_json(folder / "cognition.json")
            worldview = _read_json(folder / "worldview_update.json")
            prediction = _read_json(folder / "prediction.json")
            plan = _read_json(folder / "mind_plan.json")
            history.append({
                "event_type": "EXPRESSION", "cycle": cycle,
                "cognition": cognition, "expression_kind": cognition.get("expression_kind") or fmt,
                "text": public_text, "verified_links":verified_links, "x_post_id": published.post_id, "x_url": published.url,
                "runtime_receipt_id": None, "worldview": worldview, "prediction": prediction,
                "inquiry_refs": list((plan.get("goal") or {}).get("inquiry_refs") or ()) if isinstance(plan.get("goal"), dict) else [],
                "source_evidence_refs": [],
                "lineage": {
                    "cycle": cycle,
                    "source": "social_expression_drafts.json",
                    "browser_publication_receipt": "x_browser_publication.json",
                    "mission_experiment_transition": mission_experiment_transition,
                    "future_cognition": True,
                },
            })
        emit_public_activity(self.state_root, cycle=cycle, phase="X", title="Posted on X",
                             detail=f"Published the persisted X draft as {published.post_id}.",
                             status="completed", kind="social")
        return {
            "status": "POSTED",
            "cycle": cycle,
            "publication": publication,
            "mission_experiment_transition": mission_experiment_transition,
        }


__all__ = [
    "XBrowserPublicationUnverified", "XBrowserPublishResult", "XBrowserPublisher",
    "XBrowserSessionUnavailable", "XBrowserTransportRouter",
    "x_browser_enabled", "x_browser_writes_enabled", "_post_id_from_url",
]
