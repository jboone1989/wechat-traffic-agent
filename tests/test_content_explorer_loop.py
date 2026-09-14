import asyncio
import json

from wechat_traffic_agent.content_goal_loop import ContinuousContentLoop
from test_content_explorer import candidate
from test_content_loop import NOW, Runtime, setup


class V2Runtime(Runtime):
    async def cognitive_probe(self, task, max_tokens):
        if task.startswith("SELECT_EVIDENCE:"):
            self.calls.append(task)
            return json.dumps({"title": f"Explaining evidence question {self.number}",
                "evidence_id": f"news-{self.number}", "topic": "rare", "related_ids": ["a0"],
                "reader_question": "What does this observation actually support?",
                "gap": "Connect fresh evidence to existing interpretation."})
        return await super().cognitive_probe(task, max_tokens)


class GoalMind:
    def __init__(self): self.planned = False
    def plan_for_cycle(self, *, cycle, available_topics):
        self.planned = True
        return {"cycle": cycle, "goal": {"origin": "INTEREST", "interest_refs": ["common"]}, "required_topic": "common"}
    def observe_process_experience(self, **kwargs): return {"status": "OBSERVED", "topic": kwargs["topic"]}


class Explorer:
    def __init__(self, mind=None, *, empty=False, fail=False):
        self.mind, self.empty, self.fail = mind, empty, fail
        self.collect_calls = self.filter_calls = 0
    async def collect(self, *, now, active):
        if self.mind: assert self.mind.planned and active is True
        self.collect_calls += 1
        return {"items": [candidate()], "failures": [], "observed_at": now.isoformat()}
    async def filter_for_goal(self, **kwargs):
        self.filter_calls += 1
        if self.fail: raise RuntimeError("simulated explorer failure")
        return {"items": [] if self.empty else [candidate()], "matches": [], "candidate_count": 1}


def v2(worker, runtime, publisher, *, mind=None, explorer=None):
    return ContinuousContentLoop(runtime=runtime, state_root=worker.state, artifacts_path=worker.artifacts_path,
        site_root=worker.site_root, base_url=worker.base_url, curriculum_path=worker.curriculum_path,
        feeds=worker.feeds, interval_hours=worker.interval_hours, fetcher=worker.fetcher,
        publisher=publisher, mind=mind, explorer=explorer)


def test_goal_precedes_evidence_and_unrelated_stops(tmp_path):
    runtime, legacy, publisher = setup(tmp_path)
    before = (publisher.public / "current").resolve()
    mind, explorer = GoalMind(), Explorer(empty=True); explorer.mind = mind
    worker = v2(legacy, runtime, publisher, mind=mind, explorer=explorer)
    result = asyncio.run(worker.run_once(now=NOW))
    assert result["status"] == "NO_RELEVANT_EVIDENCE"
    assert explorer.collect_calls == explorer.filter_calls == 1
    assert runtime.calls == [] and worker.inbox.list_records() == ()
    assert (publisher.public / "current").resolve() == before


def test_restart_does_not_repeat_exploration_or_publication(tmp_path):
    _runtime, legacy, publisher = setup(tmp_path)
    runtime, explorer = V2Runtime(), Explorer()
    worker = v2(legacy, runtime, publisher, explorer=explorer)
    first = asyncio.run(worker.run_once(now=NOW))
    assert first["status"] == "PUBLISHED"
    count = len(json.loads(publisher.inventory_path().read_text()))
    worker2 = v2(legacy, V2Runtime(), publisher, explorer=explorer)
    replay = asyncio.run(worker2.run_once(now=NOW))
    assert replay["status"] == "ALREADY_PROCESSED" and replay["previous_status"] == "PUBLISHED"
    assert explorer.collect_calls == explorer.filter_calls == 1
    assert len(json.loads(publisher.inventory_path().read_text())) == count


def test_explorer_failure_never_publishes(tmp_path):
    runtime, legacy, publisher = setup(tmp_path)
    before = (publisher.public / "current").resolve()
    mind, explorer = GoalMind(), Explorer(fail=True); explorer.mind = mind
    worker = v2(legacy, runtime, publisher, mind=mind, explorer=explorer)
    result = asyncio.run(worker.run_once(now=NOW))
    assert result["status"] == "ERROR" and result["error_type"] == "RuntimeError"
    assert runtime.calls == [] and worker.inbox.list_records() == ()
    assert (publisher.public / "current").resolve() == before
