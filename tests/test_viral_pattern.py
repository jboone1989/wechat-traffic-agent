from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest

from wechat_traffic_agent.fixture import FixtureProvider
from wechat_traffic_agent.viral_pattern import ViralPatternAnalyzer
from wechat_traffic_agent.viral_radar import ScanReport, ViralCandidate, ViralRadar


def _scan_report(top_n: int = 8):
    provider = FixtureProvider()
    radar = ViralRadar(
        hot_provider=provider,
        reader=provider,
        platforms=("weibo", "zhihu", "bilibili"),
        top_n=top_n,
    )
    return asyncio.run(radar.scan())


VALID_PATTERN_BODY = {
    "target_audience": "被通勤消耗的都市上班族",
    "scene": "早晚高峰地铁上刷手机",
    "core_desire": "想把通勤时间变成可支配时间",
    "pain_or_tension": "每天两小时被白白浪费又无力改变",
    "title_pattern": "反差句式「小账号，大爆发」并用具体倍数制造好奇缺口",
    "opening_hook": "先给一个反常识观察，再承诺三步拆出可复用结构",
    "curiosity_gap": "不直接说结论，把关键数字留到第二屏",
    "first_screen_promise": "读完你能拿走一张可迁移的结构清单",
    "emotional_trigger": "身份认同：说的就是我",
    "information_delta": "给出一个没人统计过的互动比例事实",
    "conflict_structure": "小体量 vs 大结果的落差",
    "pacing": "短句推进，每两句换一次信息密度",
    "payoff": "结尾交付清单式总结并点名下一步动作",
    "interaction_trigger": "以低门槛问题邀请读者报出自己的处境",
    "comment_demands": "评论区大量求模板和经验贴",
    "save_reason": "结构清单可反复对照使用",
    "share_reason": "替读者说出了身份认同的潜台词",
    "reusable_elements": ["反差式标题", "两屏内兑现承诺", "清单化收尾"],
    "platform_specific_elements": ["热榜位次带来的曝光加成"],
    "observed_facts": [
        {"statement": "评论区重复出现『说的就是我』式表态", "evidence_refs": ["hot:weibo:weibo-5"]},
    ],
    "hypotheses": [
        {"statement": "该内容可能因身份认同促进分享转发", "evidence_refs": ["hot:weibo:weibo-5"]},
    ],
    "confidence": 0.7,
}


def _valid_response(ref: str = "hot:weibo:weibo-5") -> str:
    body = dict(VALID_PATTERN_BODY)
    body["observed_facts"] = [
        {"statement": "样本进入平台热榜前十", "evidence_refs": [ref]},
    ]
    body["hypotheses"] = [
        {"statement": "低粉高互动可能来自身份认同驱动", "evidence_refs": [ref]},
    ]
    return json.dumps(body, ensure_ascii=False)


class StubRuntime:
    """Offline stand-in for the Agent Runtime advisory backend."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.tasks: list[str] = []
        self.max_tokens_seen: list[int] = []

    async def cognitive_probe(self, task: str, max_tokens: int) -> str:
        self.tasks.append(task)
        self.max_tokens_seen.append(max_tokens)
        if not self._responses:
            raise AssertionError("unexpected extra cognitive_probe call")
        return self._responses.pop(0)


def _analyzer(stub: StubRuntime, *, top_k: int = 5) -> ViralPatternAnalyzer:
    return ViralPatternAnalyzer(runtime=stub, reader=FixtureProvider(), top_k=top_k)


def test_top_k_bounds_and_deterministic_selection() -> None:
    report = _scan_report()
    with pytest.raises(ValueError):
        ViralPatternAnalyzer(runtime=StubRuntime([]), reader=FixtureProvider(), top_k=0)
    with pytest.raises(ValueError):
        ViralPatternAnalyzer(runtime=StubRuntime([]), reader=FixtureProvider(), top_k=11)

    responses = [_valid_response(next(iter(c.evidence_refs))) for c in report.candidates[:3]]
    stub = StubRuntime(responses)
    patterns = asyncio.run(_analyzer(stub, top_k=3).analyze(report))
    assert [p.candidate_id for p in patterns] == [
        c.candidate_id for c in report.candidates[:3]
    ]
    assert all(len(p.observed_facts) >= 1 for p in patterns)


def test_pattern_has_separate_observation_and_hypothesis() -> None:
    report = _scan_report()
    ref = sorted(report.candidates[0].evidence_refs)[0]
    stub = StubRuntime(["```json\n" + _valid_response(ref) + "\n```"])  # markdown 围栏也要能剥
    patterns = asyncio.run(_analyzer(stub, top_k=1).analyze(report))
    pattern = patterns[0]
    # 结构性分离：观察都带证据引用，假设独立成列且措辞是解释性的
    assert pattern.observed_facts
    assert all(fact.evidence_refs for fact in pattern.observed_facts)
    assert pattern.hypotheses
    assert all(hyp.statement != fact.statement for hyp in pattern.hypotheses for fact in pattern.observed_facts)
    # 分析器确定性回填 source_refs = 候选证据 ∪ 引用证据
    assert ref in pattern.source_refs


def _lone_candidate_report() -> ScanReport:
    """单候选报告：硬规则失败的唯一候选，不引入其他候选的噪音。"""
    return _single_candidate_report(
        url="https://fixture.example/weibo/1",
        excerpt="这是微博样本一的正文，包含一个真实观察。",
    )


def test_observed_fact_without_evidence_reference_is_rejected() -> None:
    body = json.loads(_valid_response("hot:weibo:wb-1"))
    body["observed_facts"] = [{"statement": "无证据的断言", "evidence_refs": []}]
    stub = StubRuntime([json.dumps(body, ensure_ascii=False)])
    analyzer = _analyzer(stub)
    # 单候选失败被隔离后全批皆败：RuntimeError 聚合并保留原始原因
    with pytest.raises(RuntimeError, match="evidence"):
        asyncio.run(analyzer.analyze(_lone_candidate_report()))
    assert len(analyzer.failures) == 1
    assert analyzer.failures[0].startswith("weibo:wb-1")


def test_unsupported_evidence_source_is_rejected() -> None:
    body = json.loads(_valid_response("hot:weibo:wb-1"))
    body["observed_facts"] = [
        {"statement": "引用了不存在的来源", "evidence_refs": ["hot:tumblr:not-real"]}
    ]
    stub = StubRuntime([json.dumps(body, ensure_ascii=False)])
    analyzer = _analyzer(stub)
    with pytest.raises(RuntimeError, match="unsupported source"):
        asyncio.run(analyzer.analyze(_lone_candidate_report()))
    assert len(analyzer.failures) == 1
    assert analyzer.failures[0].startswith("weibo:wb-1")


def test_near_copy_of_sample_body_is_rejected() -> None:
    report = _lone_candidate_report()
    target = report.candidates[0]
    provider = FixtureProvider()
    if target.url:
        body = asyncio.run(provider.read(target.url, platform=target.platform)).items.text
    else:
        body = target.text_excerpt or ""
    assert len("".join(body.split())) >= 12
    body_json = json.loads(json.dumps(VALID_PATTERN_BODY))  # deep copy
    body_json["opening_hook"] = f"开头直接抄原文试试：{body}"  # 整段连续复用必被抓
    ref = sorted(target.evidence_refs)[0]
    body_json["observed_facts"] = [
        {"statement": "样本进入平台热榜", "evidence_refs": [ref]}
    ]
    body_json["hypotheses"] = [
        {"statement": "可能由身份认同驱动传播", "evidence_refs": [ref]}
    ]
    stub = StubRuntime([json.dumps(body_json, ensure_ascii=False)])
    analyzer = _analyzer(stub)
    with pytest.raises(RuntimeError, match="near-copy"):
        asyncio.run(analyzer.analyze(_lone_candidate_report()))
    assert len(analyzer.failures) == 1
    assert analyzer.failures[0].startswith("weibo:wb-1")


def _single_candidate_report(*, url: str | None, excerpt: str) -> ScanReport:
    candidate = ViralCandidate(
        candidate_id="weibo:wb-1",
        platform="weibo",
        content_id="wb-1",
        url=url,
        title="微博样本一",
        text_excerpt=excerpt,
        rank=1,
        observed_at=datetime(2026, 8, 27, 9, 0, tzinfo=timezone.utc),
        likes=8000,
        final_score=0.8,
        evidence_refs=("hot:weibo:wb-1",),
    )
    return ScanReport(
        scan_id="test-scan",
        observed_at=candidate.observed_at,
        platforms=(),
        candidates=(candidate,),
    )


def test_comments_are_offered_as_citable_evidence() -> None:
    stub = StubRuntime([_valid_response("comment:weibo:wb-1:c-1")])
    analyzer = _analyzer(stub, top_k=1)
    patterns = asyncio.run(analyzer.analyze(_single_candidate_report(url="https://fixture.example/weibo/1", excerpt="截取")))
    task = stub.tasks[0]
    assert "说的就是我" in task  # 评论原文作为证据进入提示词
    assert "comment:weibo:wb-1:c-1" in task
    assert "comment:weibo:wb-1:c-1" in patterns[0].source_refs


def test_candidate_without_url_falls_back_to_excerpt() -> None:
    stub = StubRuntime([_valid_response("hot:weibo:wb-1")])
    analyzer = _analyzer(stub, top_k=1)
    asyncio.run(
        analyzer.analyze(
            _single_candidate_report(url=None, excerpt="这是微博样本一的正文，包含一个真实观察。")
        )
    )
    assert "这是微博样本一的正文" in stub.tasks[0]  # 无 url 时用雷达摘要兜底


def test_one_bad_candidate_does_not_abort_the_batch() -> None:
    report = _scan_report(top_n=8)
    selected = [c for c in report.candidates if c.final_score is not None][:2]
    bad_ref, good_ref = (sorted(c.evidence_refs)[0] for c in selected)
    stub = StubRuntime(["这不是 JSON", _valid_response(good_ref)])
    analyzer = _analyzer(stub, top_k=2)
    patterns = asyncio.run(analyzer.analyze(report))
    # 单候选输出不合法只淘汰它自己，其余照常出 pattern
    assert [p.candidate_id for p in patterns] == [selected[1].candidate_id]
    assert len(analyzer.failures) == 1
    assert analyzer.failures[0].startswith(selected[0].candidate_id)
    assert bad_ref in stub.tasks[0]


def test_all_candidates_failing_raises_aggregated_error() -> None:
    report = _scan_report(top_n=8)
    selected_count = len([c for c in report.candidates if c.final_score is not None][:2])
    stub = StubRuntime(["不是 JSON"] * selected_count)
    analyzer = _analyzer(stub, top_k=2)
    with pytest.raises(RuntimeError, match=f"all {selected_count} candidate analyses failed"):
        asyncio.run(analyzer.analyze(report))
