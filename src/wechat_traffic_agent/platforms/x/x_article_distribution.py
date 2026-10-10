"""Distribute audited website articles as native X Articles through the outbox."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from wechat_traffic_agent.content_artifact import ContentArtifact
from wechat_traffic_agent.editorial.channel_records import ChannelRecords
from wechat_traffic_agent.publication.errors import NonRetryablePublication
from wechat_traffic_agent.publication.models import DistributionTask, PublicationEvent
from wechat_traffic_agent.x_article_media import ArticleImageSpec, prepare_article_media

X_ARTICLE_DISTRIBUTION_DIR = "content/x_article_distribution"


class ArticleImagePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    images: tuple[ArticleImageSpec, ...] = ()


def render_article_text(artifact: ContentArtifact) -> str:
    blocks = []
    for section in artifact.sections:
        blocks.append(section.heading)
        blocks.append("\n\n".join(section.paragraphs))
    return "\n\n".join(blocks)


def _image_plan_prompt(artifact: ContentArtifact) -> str:
    outline = "\n".join(
        section.heading + ": " + section.paragraphs[0][:200]
        for section in artifact.sections)
    return (
        "ARTICLE_IMAGE_PLAN: Declare images for Ferro's X Article. "
        'Return JSON only: {"images": [ArticleImageSpec ...]} with at most one COVER. '
        "modes: GRAPHIC (deterministic original cover or labelled inline diagram), "
        "GENERATE (prompt for image generation), SEARCH (query for licensed Commons photo). "
        "GENERATE requires prompt; SEARCH requires query; inline GRAPHIC requires 2-6 diagram_labels; "
        "every image needs alt_text of at least 5 characters; caption is optional. "
        "An empty list is valid and yields an original graphic cover. "
        "Never claim generated art is a real product screenshot or evidence. "
        "Inline images should set after_text to an exact sentence copied from the article text "
        "that the image follows. Choose images only when they aid comprehension.\n"
        + json.dumps({"title": artifact.title, "summary": artifact.summary,
                      "topics": list(artifact.tags), "outline": outline},
                     ensure_ascii=False))


async def _article_image_plan(runtime, artifact: ContentArtifact, artifact_dir: Path) -> list[dict]:
    plan_path = artifact_dir / "image_plan.json"
    if plan_path.exists():
        return json.loads(plan_path.read_text(encoding="utf-8"))
    from wechat_traffic_agent.content_loop import ReceiptRuntime, _structured

    def checked(plan: ArticleImagePlan) -> None:
        if sum(1 for image in plan.images if image.role == "COVER") > 1:
            raise ValueError("an X Article has at most one COVER image")

    plan = await _structured(
        ReceiptRuntime(runtime, artifact_dir / "image-plan-model-calls"),
        _image_plan_prompt(artifact), ArticleImagePlan,
        validate=checked, max_tokens=2000)
    images = [image.model_dump(mode="json") for image in plan.images]
    payload = json.dumps(images, ensure_ascii=False, indent=2) + "\n"
    pending = artifact_dir / "image_plan.json.pending"
    pending.write_text(payload, encoding="utf-8")
    pending.replace(plan_path)
    return images


def _load_artifact(state_root: Path, artifact_id: str) -> ContentArtifact:
    records = ChannelRecords(state_root)
    with records.repo.connect() as db:
        row = db.execute("SELECT content_json FROM articles WHERE artifact_id=?",
                         (artifact_id,)).fetchone()
    if row is None:
        raise NonRetryablePublication("article content is missing for " + artifact_id)
    return ContentArtifact.model_validate_json(row["content_json"])


def publish_x_article(event: PublicationEvent, task: DistributionTask, *,
                      state_root: Path, model_runtime) -> dict:
    from wechat_traffic_agent.platforms.x.x_browser import (
        X_PREMIUM_LONG_POST_MAX_CHARS,
        XBrowserTransportRouter,
        x_browser_writes_enabled,
    )
    if task.channel != "x_article":
        raise ValueError("X Article adapter cannot publish other channels")
    if not x_browser_writes_enabled():
        raise RuntimeError("X publishing is disabled")
    artifact_dir = Path(state_root) / X_ARTICLE_DISTRIBUTION_DIR / event.artifact_id
    receipt_path = artifact_dir / "publication_receipt.json"
    if receipt_path.exists():
        prior = json.loads(receipt_path.read_text(encoding="utf-8"))
        return {"status": "POSTED", "url": prior["url"], "post_id": prior["post_id"]}
    artifact = _load_artifact(Path(state_root), event.artifact_id)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    text = render_article_text(artifact)
    images = asyncio.run(_article_image_plan(model_runtime, artifact, artifact_dir))
    media = asyncio.run(prepare_article_media(
        draft={"title": artifact.title, "images": images}, folder=artifact_dir))
    for image in media["inline"]:
        caption = image.get("caption") or image["alt_text"]
        anchor = image.get("after_text")
        if anchor:
            if text.count(anchor) != 1:
                raise NonRetryablePublication(
                    "image anchor is missing or ambiguous: " + anchor[:80])
            if caption not in text:
                text = text.replace(anchor, anchor + "\n\n" + caption, 1)
        else:
            text += "\n\n" + caption
    credits = [row.get("attribution") for row in [media["cover"], *media["inline"]]
               if row.get("attribution")]
    if credits:
        text += "\n\nImage credits:\n" + "\n".join(credits)
    if len(text) > X_PREMIUM_LONG_POST_MAX_CHARS:
        raise NonRetryablePublication("article text exceeds the X Article length bound")
    publication = asyncio.run(
        XBrowserTransportRouter(state_root=Path(state_root)).publish_article(
            title=artifact.title, text=text, image_path=media["cover"]["path"],
            inline_images=media["inline"],
            transaction_path=artifact_dir / "x_article_transaction.json"))
    post_id = str(publication.post_id or "").strip()
    url = str(publication.url or "").strip()
    if not post_id or not url.startswith("https://x.com/") or post_id not in url:
        raise RuntimeError("Jev did not return a verified X Article URL")
    pending = artifact_dir / "publication_receipt.json.pending"
    pending.write_text(json.dumps(
        {"url": url, "post_id": post_id, "event_id": event.event_id,
         "published_at": datetime.now(timezone.utc).isoformat()},
        ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    pending.replace(receipt_path)
    return {"status": "POSTED", "url": url, "post_id": post_id}
