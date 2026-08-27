from __future__ import annotations

import pytest

from wechat_traffic_agent.near_copy import near_copy_verdict


def test_identical_title_is_flagged_as_high_similarity() -> None:
    verdict = near_copy_verdict(
        produced_texts=("低粉爆款文案是这样写的",),
        source_titles=("低粉爆款文案是这样写的",),
        source_bodies=(),
    )
    assert verdict.is_near_copy
    assert any("高度相似" in reason for reason in verdict.reasons)


def test_title_with_few_chars_changed_still_flagged() -> None:
    # 改几个字的"伪改写"必须被抓到
    verdict = near_copy_verdict(
        produced_texts=("跨平台同题：通勤效率的观察",),
        source_titles=("跨平台同题：通勤效率观察",),
        source_bodies=(),
    )
    assert verdict.is_near_copy


def test_long_contiguous_fragment_from_body_is_flagged() -> None:
    body = (
        "这是低粉爆款的正文，互动远超账号体量。作者只有六百粉丝，"
        "但评论区的真实反馈说明内容踩中了身份认同。"
    )
    verdict = near_copy_verdict(
        produced_texts=("开头钩子：作者只有六百粉丝，但评论区已经沸腾",),
        source_titles=("与正文无关的标题",),
        source_bodies=(body,),
    )
    assert verdict.is_near_copy
    assert any("连续" in reason or "片段" in reason for reason in verdict.reasons)


def test_genuinely_rewritten_pattern_passes() -> None:
    verdict = near_copy_verdict(
        produced_texts=(
            "反差句式「小账号，大爆发」+ 用具体倍数制造好奇缺口",
            "第一屏承诺：三步拆出可复用的结构",
        ),
        source_titles=("跨平台同题：通勤效率观察", "微博样本一"),
        source_bodies=("这是大号普通内容的正文，数据平稳。",),
    )
    assert not verdict.is_near_copy
    assert verdict.reasons == ()


def test_short_generic_overlap_does_not_false_positive() -> None:
    # 8 字以内的偶合重叠（通用词组）不应触发
    verdict = near_copy_verdict(
        produced_texts=("这是一个全新的钩子结构描述",),
        source_titles=("这是另一个热点事件的记录",),
        source_bodies=(),
    )
    assert not verdict.is_near_copy
