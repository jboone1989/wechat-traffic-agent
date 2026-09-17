from datetime import datetime, timedelta, timezone
import pytest
from pydantic import ValidationError
from wechat_traffic_agent.channel_evidence import ChannelEvidence,ChannelEvidenceStore

T=datetime(2026,1,1,tzinfo=timezone.utc)

def row(**kw):
    d=dict(channel='website',account='site',publication_id='p1',artifact_id='a1',published_content_sha256='a'*64,
           agent_candidate_sha256='a'*64,topic='validation',title_version='title:v1',structure_version='structure:v1',strategy_version='strategy:v1',
           objective='reads',distribution_cohort='organic',metric='views',metric_definition='analytics:pageviews:v1',unit='count',value=10,
           published_at=T,window_start=T,window_end=T+timedelta(days=1),observed_at=T+timedelta(days=2),revision=1,source='analytics-export',evidence_refs=['report:1'])
    d.update(kw);return ChannelEvidence(**d)

def test_revision_cutoff_unknown_and_restart(tmp_path):
    store=ChannelEvidenceStore(tmp_path)
    first=row(); path=store.record(first);assert store.record(first)==path
    store.record(row(value=20,revision=2,observed_at=T+timedelta(days=3)))
    assert store.as_of(T+timedelta(days=2))[0].value==10
    restored=ChannelEvidenceStore(tmp_path)
    assert restored.as_of(T+timedelta(days=4))[0].value==20
    with pytest.raises(FileExistsError): store.record(row(value=30))
    store.record(row(publication_id='p2',value=None))
    report=store.report(channel='website',account='site',cutoff=T+timedelta(days=4),dimension='topic')
    variant=report['groups'][0]['variants'][0]
    assert variant['observations']==1 and variant['unknown']==1 and variant['mean']==20
    assert report['publication_authority'] is False
    assert store.report(channel='wechat',account='site',cutoff=T+timedelta(days=4),dimension='topic')['status']=='NO_EVIDENCE'

@pytest.mark.parametrize('change',[{'metric_definition':'other'}, {'distribution_cohort':'paid'}, {'objective':'conversion'},
    {'window_end':T+timedelta(hours=12)}, {'agent_candidate_sha256':'b'*64}, {'agent_candidate_sha256':None},
    {'unit':'seconds'}, {'source':'different-provider'}])
def test_incompatible_measurements_and_editor_changes_are_not_pooled(tmp_path,change):
    s=ChannelEvidenceStore(tmp_path);s.record(row());s.record(row(publication_id='p2',**change))
    report=s.report(channel='website',account='site',cutoff=T+timedelta(days=4),dimension='strategy_version')
    assert len(report['groups'])==2

@pytest.mark.parametrize('change',[{'value':True},{'value':float('nan')},{'value':float('inf')},{'value':-1},
    {'unit':'ratio','value':1.1},{'value':1.5},{'observed_at':T},{'window_end':T}, {'evidence_refs':[]},
    {'evidence_refs':['same','same']},{'window_start':T.replace(tzinfo=None)}])
def test_invalid_or_immature_evidence_rejected(change):
    with pytest.raises(ValidationError): row(**change)


# Website access-log outcome collection ---------------------------------------
import asyncio
import gzip
import json
from pathlib import Path

from wechat_traffic_agent.web_outcome_evidence import WebsiteOutcomeCollector
from test_content_loop import NOW, setup


def _combined(ts, route, *, ua="Mozilla/5.0", status=200, ip="203.0.113.77", method="GET"):
    stamp = ts.strftime("%d/%b/%Y:%H:%M:%S %z")
    return f'{ip} - - [{stamp}] "{method} {route} HTTP/1.1" {status} 123 "-" "{ua}"\n'


def _website_publication(root: Path, *, published_at: datetime, route="/articles/future-evidence/"):
    cycle = root / "content/continuous/cycles/v2-future"
    cycle.mkdir(parents=True, exist_ok=True)
    publication = {
        "revision": "a" * 64,
        "artifact_id": "continuous-future",
        "outcome_attribution": {
            "schema_version": "website-publication-attribution.v1",
            "published_at": published_at.isoformat(),
            "public_route": route,
            "published_content_sha256": "b" * 64,
            "agent_candidate_sha256": "b" * 64,
            "topic": "model-evaluation",
            "title_version": "sha256:" + "c" * 64,
            "structure_version": "sha256:" + "d" * 64,
            "strategy_version": "lesson:" + "e" * 64,
        },
    }
    (cycle / "publication.json").write_text(json.dumps(publication))
    return publication


def test_future_publication_freezes_activation_time_and_attribution(tmp_path):
    runtime, worker, publisher = setup(tmp_path)
    activated = NOW + timedelta(minutes=3)
    publisher.clock = lambda: activated
    result = asyncio.run(worker.run_once(now=NOW))
    attr = result["publication"]["outcome_attribution"]
    assert attr["schema_version"] == "website-publication-attribution.v1"
    assert attr["published_at"] == activated.isoformat()
    assert attr["public_route"].startswith("/articles/")
    assert attr["topic"] == "rare"
    assert attr["strategy_version"].startswith("lesson:")
    assert attr["published_content_sha256"] == attr["agent_candidate_sha256"]
    publication_files = list((tmp_path / "state/content/continuous/cycles").glob("*/publication.json"))
    assert len(publication_files) == 1
    persisted = json.loads(publication_files[0].read_text())
    assert persisted["outcome_attribution"] == attr


def test_invalid_publication_clock_does_not_activate_candidate(tmp_path):
    runtime, worker, publisher = setup(tmp_path)
    before = (publisher.public / "current").resolve()
    publisher.clock = lambda: NOW.replace(tzinfo=None)
    result = asyncio.run(worker.run_once(now=NOW))
    assert result["status"] == "ERROR"
    assert result["error_type"] == "ValueError"
    assert (publisher.public / "current").resolve() == before
    assert not (publisher.public / "current.next").exists()
    assert not list((tmp_path / "state/content/continuous/cycles").glob("*/publication.json"))


def test_legacy_publications_are_not_backfilled(tmp_path):
    legacy = tmp_path / "state/content/continuous/cycles/v2-old"
    legacy.mkdir(parents=True)
    (legacy / "publication.json").write_text(json.dumps({"revision": "f" * 64, "artifact_id": "old"}))
    log = tmp_path / "access.log"
    log.write_text(_combined(NOW, "/"))
    result = WebsiteOutcomeCollector(
        state_root=tmp_path / "state", access_log=log, account="https://example.com"
    ).collect(now=NOW + timedelta(days=8))
    assert result["status"] == "NO_ELIGIBLE_PUBLICATIONS"
    assert result["legacy"] == 1
    assert not (tmp_path / "state/content/channel_evidence").exists()


def test_mature_window_records_pageviews_without_client_data(tmp_path):
    state = tmp_path / "state"
    publication = _website_publication(state, published_at=NOW)
    log = tmp_path / "access.log"
    route = publication["outcome_attribution"]["public_route"]
    secret_ua = "PrivateBrowser/123"
    log.write_text("".join([
        _combined(NOW - timedelta(minutes=1), "/", ip="198.51.100.1"),
        _combined(NOW + timedelta(hours=1), route, ip="198.51.100.2", ua=secret_ua),
        _combined(NOW + timedelta(hours=2), route + "?utm=x", ip="198.51.100.3"),
        _combined(NOW + timedelta(hours=3), route, ip="198.51.100.4", ua="curl/8.0"),
        _combined(NOW + timedelta(hours=4), route, ip="198.51.100.5", status=404),
        _combined(NOW + timedelta(hours=25), "/", ip="198.51.100.6"),
    ]))
    collector = WebsiteOutcomeCollector(state_root=state, access_log=log, account="https://example.com")
    result = collector.collect(now=NOW + timedelta(hours=25))
    assert result["status"] == "RECORDED"
    assert len(result["recorded"]) == 3
    recorded = {row["window"]: row["pageviews"] for row in result["recorded"]}
    assert recorded == {"1h": 0, "4h": 2, "24h": 2}
    rows = ChannelEvidenceStore(state / "content/channel_evidence").as_of(NOW + timedelta(hours=25))
    assert len(rows) == 3
    assert sorted(row.value for row in rows) == [0, 2, 2]
    assert all(row.objective == "website_pageviews" for row in rows)
    assert all(row.agent_candidate_sha256 == row.published_content_sha256 for row in rows)
    persisted = "\n".join(
        p.read_text() for base in (state / "content/channel_evidence", state / "content/channel_evidence_sources")
        for p in base.rglob("*.json")
    )
    for forbidden in ("198.51.100.", secret_ua, "curl/8.0"):
        assert forbidden not in persisted
    assert collector.collect(now=NOW + timedelta(hours=26))["status"] == "NO_MATURE_WINDOWS"


def test_rotated_gzip_logs_are_included_and_bot_requests_excluded(tmp_path):
    state = tmp_path / "state"
    publication = _website_publication(state, published_at=NOW)
    route = publication["outcome_attribution"]["public_route"]
    current = tmp_path / "access.log"
    current.write_text(_combined(NOW + timedelta(hours=25), "/"))
    with gzip.open(tmp_path / "access.log.1.gz", "wt", encoding="utf-8") as handle:
        handle.write(_combined(NOW - timedelta(minutes=1), "/"))
        handle.write(_combined(NOW + timedelta(hours=5), route))
        handle.write(_combined(NOW + timedelta(hours=6), route, ua="SomeCrawler/1.0"))
    result = WebsiteOutcomeCollector(
        state_root=state, access_log=current, account="https://example.com"
    ).collect(now=NOW + timedelta(hours=25))
    assert result["status"] == "RECORDED"
    recorded = {row["window"]: row["pageviews"] for row in result["recorded"]}
    assert recorded == {"1h": 0, "4h": 0, "24h": 1}


def test_immature_or_incomplete_log_window_never_becomes_zero(tmp_path):
    state = tmp_path / "state"
    _website_publication(state, published_at=NOW)
    log = tmp_path / "access.log"
    log.write_text(_combined(NOW + timedelta(hours=1), "/"))
    collector = WebsiteOutcomeCollector(state_root=state, access_log=log, account="https://example.com")
    assert collector.collect(now=NOW + timedelta(hours=12))["status"] == "INSUFFICIENT_LOG_COVERAGE"
    result = collector.collect(now=NOW + timedelta(hours=25))
    assert result["status"] == "INSUFFICIENT_LOG_COVERAGE"
    assert not (state / "content/channel_evidence").exists()


def test_website_outcome_collector_has_no_control_authority():
    source = (Path(__file__).resolve().parents[1] / "src/wechat_traffic_agent/web_outcome_evidence.py").read_text()
    assert "from .content_mind" not in source
    assert "from .strategy_learning" not in source
    assert "import ContentMind" not in source
    assert "import StrategyLearner" not in source
    assert ".publish(" not in source
    assert '"learning_authority": False' in source
    assert '"publication_authority": False' in source
