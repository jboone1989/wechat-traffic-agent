import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
from types import SimpleNamespace

import httpx
import pytest

from test_binance_market_structure import market_report, NOW
from wechat_traffic_agent.platforms.binance_square import BinanceSquareClient
from wechat_traffic_agent.platforms.binance_square_editorial import SquareDraftStore, SquareDraftWorker
from wechat_traffic_agent.sources.binance_market_structure import analyze, read_market_structure_candidates, save_market_structure
from wechat_traffic_agent.square_cognition import SquareCognitionExpressionService, SquareResearchExpression
from wechat_traffic_agent.owner_agent_episode import OwnerAgentDecision
from wechat_traffic_agent.orchestration.content import ContentTaskMethods
from wechat_traffic_agent.orchestration.social import SocialTaskMethods
from wechat_traffic_agent.owner_agent_episode import OwnerAgentEpisode


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False))


def fresh_market():
    report = market_report()
    now = datetime.now(timezone.utc)
    shift = int((now - NOW).total_seconds() * 1000)
    report['observed_at'] = now.isoformat()
    for row in report['observations']:
        if row['name'] == 'spot_24h':
            row['data']['closeTime'] += shift
        elif row['name'] == 'mark_funding':
            for field in ('time', 'nextFundingTime'):
                row['data'][field] += shift
        elif row['name'] == 'open_interest_history':
            for value in row['data']:
                value['timestamp'] += shift
        else:
            for candle in row['data']:
                candle[0] += shift
                candle[6] += shift
    report['metrics'] = analyze(report)
    return report


def episode(root):
    folder = root / 'content/continuous/cycles/research-1'
    quote = '价格回升时未平仓数量没有增长，不能据此直接认定新增多头正在接力。'
    learning = {'cycle': folder.name, 'progress': 'LEARNED', 'progressed': True,
                'learning_delta': '从具体来源核对了持仓数量与价格变动的不同含义。',
                'focus': {'question': 'ETH反弹是否已经有新持仓配合？'},
                'facts': [{'evidence_id': 'source-1', 'quote': quote, 'observation': quote}],
                'sources': [{'evidence_id': 'source-1', 'title': 'source',
                             'url': 'https://example.org/evidence', 'evidence_text': quote}],
                'next_query': 'ETH synchronized price and open interest changes'}
    cognition = {'worth_saying': True, 'expression_kind': 'RESEARCH_NOTE', 'what_changed': quote}
    write(folder / 'learning_result.json', learning)
    write(folder / 'cognition.json', cognition)
    write(root / 'square_editorial/editorial_preferences.json', {'production_mode': 'COGNITION_LED'})
    return folder, learning, cognition


def service(root, *, action='SHARE', passed=True):
    calls = []
    body = 'ETH 价格回升而持仓数量未增长，这组观察还不足以认定新增多头接力。来源：https://example.org/evidence'
    async def structured(runtime, prompt, schema):
        calls.append('compose')
        assert schema is SquareResearchExpression
        assert 'verified_facts' in prompt and 'recent_published' in prompt
        return SquareResearchExpression(action=action, rationale='研究记录包含可以核对的具体认识，需要与历史表达比较。',
            body=body, symbols=['ETH'], reader_question='是否有新增持仓？',
            thesis='尚未看到持仓扩张', invalidation='观察范围之外不能作整体推断')
    async def review(**kwargs):
        calls.append('semantic_review')
        result = {'passed': passed, 'rationale': '通过原始证据与近期表达核对具体认识和推断边界。',
                  'learning_cycle': kwargs['learning']['cycle'],
                  'text_sha256': sha256(kwargs['text'].encode()).hexdigest()}
        write(kwargs['folder'] / 'square_expression_review.json', result)
        return result
    async def collect():
        calls.append('collect')
        return fresh_market()
    return SquareCognitionExpressionService(root, structured=structured, semantic_review=review, collect=collect), calls


def prepare(run, folder, learning, cognition):
    return asyncio.run(run.prepare(runtime=object(), folder=folder, learning=learning,
                    cognition=cognition, mind_plan={}, worldview={'status': 'UNCHANGED'}))


def test_real_research_and_semantic_review_produce_one_bound_draft(tmp_path):
    folder, learning, cognition = episode(tmp_path)
    run, calls = service(tmp_path)
    result = prepare(run, folder, learning, cognition)
    assert result['status'] == 'SQUARE_EXPRESSION_READY'
    assert result['publication_confirmed'] is False
    assert result['draft_status'] == 'REVIEWED'
    assert calls == ['compose', 'semantic_review', 'collect']
    doc = SquareDraftStore(tmp_path).get(result['draft_id'])
    assert doc['research_provenance']['learning_cycle'] == folder.name
    assert doc['reviewer'] == 'ferro-cognition-expression'
    assert prepare(run, folder, learning, cognition)['draft_id'] == result['draft_id']
    assert len(SquareDraftStore(tmp_path).list()) == 1
    assert calls == ['compose', 'semantic_review', 'collect']


@pytest.mark.parametrize('reason', ['no_learning', 'no_facts', 'not_worth_saying', 'repeat', 'review_failed'])
def test_snapshot_or_repeated_expression_is_not_enough(tmp_path, reason):
    folder, learning, cognition = episode(tmp_path)
    run, calls = service(tmp_path, action='CONTINUE_RESEARCH' if reason == 'repeat' else 'SHARE',
                         passed=reason != 'review_failed')
    if reason == 'no_learning':
        learning = None
    elif reason == 'no_facts':
        learning['facts'][0]['quote'] = 'This quote is absent from the actual source.'
        write(folder / 'learning_result.json', learning)
    elif reason == 'not_worth_saying':
        cognition['worth_saying'] = False
        write(folder / 'cognition.json', cognition)
    result = prepare(run, folder, learning, cognition)
    assert result['status'] == 'COGNITION_ONLY'
    assert not SquareDraftStore(tmp_path).list()
    assert 'collect' not in calls


def test_fabricated_receipt_argument_is_rejected_before_generation(tmp_path):
    folder, learning, cognition = episode(tmp_path)
    learning['learning_delta'] = 'Supplied value differs from the actual persisted inquiry.'
    run, calls = service(tmp_path)
    with pytest.raises(ValueError, match='persisted'):
        prepare(run, folder, learning, cognition)
    assert calls == []


@pytest.mark.parametrize('file', ['learning_result.json', 'cognition.json', 'square_expression_review.json'])
def test_changed_brain_or_review_receipt_blocks_transport(tmp_path, monkeypatch, file):
    folder, learning, cognition = episode(tmp_path)
    run, _ = service(tmp_path)
    result = prepare(run, folder, learning, cognition)
    record = json.loads((folder / file).read_text())
    record['unexpected_change'] = True
    write(folder / file, record)
    sent = []
    def handle(request):
        sent.append(request)
        return httpx.Response(200, json={'code': '000000', 'data': {'id': '123'}})
    client = BinanceSquareClient(key='test', client=httpx.Client(transport=httpx.MockTransport(handle)))
    monkeypatch.setenv('CONTENT_BINANCE_SQUARE_ENABLED', '1')
    monkeypatch.setenv('CONTENT_BINANCE_SQUARE_WRITES_ENABLED', '1')
    assert SquareDraftWorker(tmp_path, client=client).run_once() is None
    assert sent == []
    assert SquareDraftStore(tmp_path).get(result['draft_id'])['status'] == 'NEEDS_REVIEW'


def test_native_square_surface_is_not_forced_through_website_first():
    decision = OwnerAgentDecision(action='RUN_CONTENT_PIPELINE', topic='eth-market-structure',
        publication_surface='BINANCE_SQUARE', rationale='Investigate the actual ETH question and express a supported discovery.')
    runner = SimpleNamespace(owner_cognition_runtime=object(), continuous_loop=SimpleNamespace(publisher=object()))
    options = ContentTaskMethods._content_execution_options(runner, {}, datetime.now(timezone.utc),
        decision.topic, {'goal_id': 'goal', 'origin': 'OWNER', 'priority': 1}, [], True,
        {'decision': decision.model_dump(mode='json')}, {'decision': 'EXECUTE'}, 'cycle', 'cycle')
    assert options['publication_surface'] == 'BINANCE_SQUARE'


def test_market_snapshot_enters_shared_research_as_evidence_not_post(tmp_path):
    report = fresh_market()
    save_market_structure(tmp_path, report)
    rows = read_market_structure_candidates(tmp_path, datetime.now(timezone.utc))
    assert {r['title'].split()[0] for r in rows} == {'BTCUSDT', 'ETHUSDT'}
    assert all(r['source_kind'] == 'public-api' and r['evidence_text'] == r['summary'] for r in rows)
    assert all('body' not in r and 'publication_authority' not in r for r in rows)


def test_cognition_led_mode_rejects_numerically_valid_bare_draft(tmp_path):
    episode(tmp_path)
    store = SquareDraftStore(tmp_path)
    doc = store.create(body='ETH 的具体行情观察。', symbols=['ETH'], evidence=fresh_market(),
                       reader_question='什么发生了变化？', thesis='一个有证据的观察', invalidation='仅限观测窗口')
    with pytest.raises(ValueError, match='research provenance'):
        store.review(doc['draft_id'], reviewer='numeric-checker', evidence_check='数字已核对')


def test_owner_cognition_sees_square_preference_and_research_history(tmp_path):
    episode(tmp_path)
    prompts = []
    class Runtime:
        async def cognitive_probe(self, prompt, max_tokens):
            prompts.append(prompt)
            return json.dumps({'action': 'RUN_CONTENT_PIPELINE', 'topic': 'eth-market-structure',
                'publication_surface': 'BINANCE_SQUARE',
                'rationale': 'Research the specific owner question through durable inquiry before deciding to share.'})
    choice = asyncio.run(OwnerAgentEpisode(tmp_path)._decide(runtime=Runtime(),
        owner_goal={'goal_id': 'goal', 'description': 'Research BTC/ETH for Binance Square'},
        mission_context={}, related_memory=[], state={}))
    assert choice.publication_surface == 'BINANCE_SQUARE'
    assert "owner's current preferred research expression is native Binance Square" in prompts[0]
    assert 'research_trajectory' in prompts[0] and 'related_memory' in prompts[0]


def test_square_handoff_does_not_require_website_or_run_x_stage(tmp_path):
    folder, learning, cognition = episode(tmp_path)
    run, _ = service(tmp_path)
    result = prepare(run, folder, learning, cognition)
    rows = {}
    runner = SimpleNamespace(state_root=tmp_path, continuous_loop=SimpleNamespace(publisher=object()))
    SocialTaskMethods._run_social_stage(runner, rows=rows, continuous=result, now=datetime.now(timezone.utc))
    assert rows['binance_square_expression']['status'] == 'PUBLICATION_PENDING'
    assert 'x_syndication' not in rows
