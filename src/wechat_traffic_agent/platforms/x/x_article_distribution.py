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
    plan_path.write_text(json.dumps(images, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    return images
