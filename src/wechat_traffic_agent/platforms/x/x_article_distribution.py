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
