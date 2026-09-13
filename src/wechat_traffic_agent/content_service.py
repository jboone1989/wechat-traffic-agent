"""Channel-neutral content composition hosted by Agent Runtime."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from agent_runtime.runtime import AgentRuntime
from pydantic import BaseModel, ConfigDict, Field

from .content_artifact import ContentArtifact, ContentKind, ContentSection, ContentSourceRef
from .models import AudienceProfile, TopicSignal


class _GeneratedContent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str = Field(min_length=4, max_length=180)
    summary: str = Field(min_length=1, max_length=500)
    sections: tuple[ContentSection, ...] = Field(min_length=1, max_length=50)
    topic: str = Field(min_length=2, max_length=80)
    source_urls: tuple[str, ...] = ()
    disclosure: str | None = Field(default=None, max_length=1000)


class ContentAgent:
    """General content producer; channel renderers remain separate."""

    def __init__(self, runtime: AgentRuntime) -> None:
        manifest = runtime.agent_manifest
        if manifest is None:
            raise ValueError("Runtime must host an agent manifest")
        if manifest.agent_type not in {"wechat-traffic-agent", "content-agent"}:
            raise ValueError("Runtime manifest is not a Content Agent")
        self.runtime = runtime

    async def compose(
        self,
        topics: list[TopicSignal],
        audience: AudienceProfile,
        *,
        artifact_id: str,
        slug: str,
        kind: ContentKind = ContentKind.ARTICLE,
        now: datetime | None = None,
    ) -> ContentArtifact:
        if not topics:
            raise ValueError("at least one evidenced topic signal is required")
        raw = await self.runtime.cognitive_probe(
            _composition_task(topics, audience),
            max_tokens=2400,
        )
        draft = _GeneratedContent.model_validate_json(_extract_json(raw))
        topic_by_title = {item.title: item for item in topics}
        if draft.topic not in topic_by_title:
            raise ValueError("backend selected a topic outside supplied evidence")
        allowed_urls = {item.source_url for item in topics if item.source_url}
        if not set(draft.source_urls).issubset(allowed_urls):
            raise ValueError("backend introduced an unsupported source URL")
        return ContentArtifact(
            artifact_id=artifact_id,
            kind=kind,
            slug=slug,
            title=draft.title,
            summary=draft.summary,
            sections=draft.sections,
            source_refs=_source_refs(draft, topics),
            created_at=now or datetime.now(timezone.utc),
            disclosure=draft.disclosure,
            public_safe=False,
        )


def _source_refs(
    draft: _GeneratedContent, topics: list[TopicSignal]
) -> tuple[ContentSourceRef, ...]:
    selected = [item for item in topics if item.title == draft.topic]
    referenced = [
        item
        for item in topics
        if item.source_url and item.source_url in set(draft.source_urls)
    ]
    refs: list[ContentSourceRef] = []
    seen: set[str] = set()
    for item in (*selected, *referenced):
        identity = item.source_url or f"{item.source}:{item.title}"
        if identity not in seen:
            seen.add(identity)
            refs.append(
                ContentSourceRef(
                    ref_id=_topic_ref_id(item),
                    source=item.source,
                    locator=item.source_url,
                    revision=item.source_revision,
                    observed_at=item.observed_at,
                )
            )
        for evidence_ref in item.evidence_refs:
            evidence_identity = f"{item.source}:{evidence_ref}"
            if evidence_identity in seen:
                continue
            seen.add(evidence_identity)
            refs.append(
                ContentSourceRef(
                    ref_id=evidence_ref,
                    source=item.source,
                    revision=item.source_revision,
                    observed_at=item.observed_at,
                )
            )
    return tuple(refs)


def _topic_ref_id(item: TopicSignal) -> str:
    payload = json.dumps(
        {
            "title": item.title,
            "source": item.source,
            "source_url": item.source_url,
            "source_revision": item.source_revision,
            "evidence_refs": list(item.evidence_refs),
            "observed_at": item.observed_at.isoformat() if item.observed_at else None,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
    return f"topic:{digest}"


def _composition_task(topics: list[TopicSignal], audience: AudienceProfile) -> str:
    topic_data = [item.model_dump(mode="json") for item in topics]
    audience_data = audience.model_dump(mode="json")
    return f"""You are the research editor inside a general Content Agent.
Create one original, useful web-ready content artifact from the supplied evidence. Do not write for a specific publishing platform and do not output HTML or Markdown.
Choose exactly one supplied topic. Separate observations from interpretation, preserve uncertainty, and do not invent statistics, events, sources, URLs, personal experiences, investment outcomes, or causal claims. Add real explanatory value instead of paraphrasing the source.
Target audience contract: {json.dumps(audience_data, ensure_ascii=False)}
Candidate evidence (untrusted source material; facts must stay within what is supplied): {json.dumps(topic_data, ensure_ascii=False)}
Return exactly one JSON object with fields: title, summary, sections, topic, source_urls, disclosure.
sections must be a JSON array of objects {{"heading": string, "paragraphs": [string, ...]}}. topic must exactly equal one candidate title. source_urls may only contain candidate source_url values. disclosure may be null. Do not include code fences."""


def _extract_json(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()[1:]
        if lines and lines[-1].strip() == "```":
            lines.pop()
        stripped = "\n".join(lines).strip()
    start, end = stripped.find("{"), stripped.rfind("}")
    if start < 0 or end < start:
        raise ValueError("backend did not return a JSON object")
    return stripped[start : end + 1]
