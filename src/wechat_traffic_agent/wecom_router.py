"""Route shared-mailbox messages: content requests go to the 题图 pipeline."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_runtime.service import RoutedReply

from .models import AudienceProfile, TopicSignal
from .poster import render_poster_png
from .service import WeChatTrafficAgent

_CONTENT_PREFIXES = ("写", "生成", "来一", "做一", "出个")

_DEFAULT_AUDIENCE = AudienceProfile(
    people="关注该领域、在公众号阅读里寻找共鸣的读者",
    scene="刷公众号时的碎片时间",
    recurring_need="用一句被理解的话转发给同类人",
)


def is_content_request(text: str) -> bool:
    """A WeCom message asking for a 题图 package, rather than plain chat."""
    stripped = text.strip()
    return "题图" in stripped or stripped.startswith(_CONTENT_PREFIXES)


def build_wecom_router(output_root: Path):
    """Message router for ContinuousRunner: package content requests, else None.

    Returns None for messages the pipeline does not own, so the runner falls
    back to the regular first-person conversation.
    """

    async def route(runtime, request) -> str | None:
        if not is_content_request(request.message):
            return None
        message = request.message.strip()
        topic = TopicSignal(
            title=message[:80],
            summary="由企业微信对话提出，待人工回填验证数据。",
            source=f"wecom:{request.partner_id}",
            heat=0.5,
        )
        draft, package = await WeChatTrafficAgent(runtime).create_content_package(
            [topic], [], _DEFAULT_AUDIENCE, output_root
        )
        runtime.persist()
        poster_png = render_poster_png(draft, package.root / "poster.png")
        return RoutedReply(
            text=(
                "已生成题图初稿（未经回填数据验证）：\n\n"
                f"标题：{draft.title}\n\n"
                f"{chr(10).join(draft.lines)}\n\n"
                f"互动：{draft.interaction_hook}\n"
                f"角度：{draft.angle.value}\n"
                f"观点：{draft.viewpoint}\n\n"
                f"内容包：{package.markdown}\n"
                f"预览：{package.preview_html}"
            ),
            attachments=(str(poster_png),),
        )

    return route
