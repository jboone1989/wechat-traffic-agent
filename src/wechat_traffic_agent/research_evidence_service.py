"""Goal evidence search as an independent in-process feature.

Consumes immutable per-cycle request values; publishes an explicit result.
No import from the orchestrator and no access to its private fields.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
import json

from .content_loop import ReceiptRuntime, _put
from .traffic_learning import TrafficLearningContext


@dataclass(frozen=True)
class EvidencePolicies:
    candidate_available: Callable
    focus_matches: Callable
    focus_query: Callable
    focus_anchors: Callable
    allow_output_fallback: Callable


@dataclass(frozen=True)
class EvidenceRequest:
    folder: Path
    cycle: str
    now: datetime
    cycle_now: datetime
    snapshot: dict
    required_topic: str | None
    public_goal: dict
    mind_plan: dict
    owner_goal: dict | None
    owner_seed_items: list
    prefetched_evidence_pool: dict | None
    owner_focus_fast_path: bool


@dataclass(frozen=True)
class EvidenceAdmission:
    evidence: list
    exploration: dict
    traffic_payload: dict
    effective_topic: str | None
    goal_active: bool
    early_result: dict | None


@dataclass(frozen=True)
class EvidenceResult:
    evidence_pool: dict
    traffic_context: Any
    traffic_payload: dict
    effective_topic: str | None
    goal_active: bool
    topic_context: list
    explorer_runtime: Any
    learning_memory: Any
    learning_focus: Any
    exploration: dict


class ResearchEvidenceService:
    def __init__(self, *, explorer, inbox, traffic_learning, runtime, mind,
                 state_root, publication_surface, activity, policies: EvidencePolicies):
        self.explorer = explorer
        self.inbox = inbox
        self.traffic_learning = traffic_learning
        self.runtime = runtime
        self.mind = mind
        self.state_root = state_root
        self.publication_surface = publication_surface
        self.activity = activity
        self.policies = policies

    async def _candidate_pool(self, folder, cycle, now, required_topic,
                              owner_seed_items, prefetched_evidence_pool):
        mind_goal_active = required_topic is not None
        candidates_path = folder / 'evidence_candidates.json'
        if candidates_path.exists():
            evidence_pool = json.loads(candidates_path.read_text())
        elif prefetched_evidence_pool is not None:
            evidence_pool = prefetched_evidence_pool
            _put(candidates_path, evidence_pool)
        elif owner_seed_items:
            evidence_pool = {
                'items': [
                    row for row in owner_seed_items
                    if self.policies.candidate_available(self.inbox, row)
                ],
                'failures': [],
                'observed_at': now.isoformat(),
                'signal_kind': 'owner-agent preflight public evidence',
                'seed_source': 'OWNER_AGENT_EPISODE_SEARCH_WORLD',
            }
            _put(candidates_path, evidence_pool)
        else:
            self.activity(cycle, 'OBSERVE', 'I’m scanning available signals',
                           'I’m checking configured feeds, local evidence, and available research sources for something worth pursuing.')
            evidence_pool = await self.explorer.collect(now=now, active=mind_goal_active)
            evidence_pool['items'] = [row for row in evidence_pool['items']
                                      if self.policies.candidate_available(self.inbox, row)]
            _put(candidates_path, evidence_pool)
            self.activity(cycle, 'OBSERVE', 'I finished the first scan',
                           f"Collected {len(evidence_pool.get('items', []))} usable candidate signals.", status='completed')
        return evidence_pool

    def _traffic_view(self, folder, cycle_now, snapshot, evidence_pool,
                      required_topic, public_goal):
        traffic_path = folder / 'traffic_context.json'
        if traffic_path.exists():
            traffic_context = TrafficLearningContext.model_validate_json(traffic_path.read_text(encoding='utf-8'))
        else:
            traffic_context = self.traffic_learning.build(
                website=snapshot, as_of=cycle_now, trend_candidates=evidence_pool['items']
            )
            _put(traffic_path, traffic_context.model_dump(mode='json'))
        traffic_payload = traffic_context.model_dump(mode='json')
        ranked_topics = sorted(traffic_context.topics, key=lambda row: (-row.priority_score, row.topic))
        traffic_topic = next(
            (row.topic for row in ranked_topics if row.search_rows > 0 or row.trend_hits > 0), None
        )
        effective_topic = required_topic or traffic_topic
        goal_active = effective_topic is not None
        # A reality-born inquiry may introduce a cognitive topic that does not yet exist in the
        # Website taxonomy. It can constrain research without forcing a fake pre-existing tag.
        traffic_payload['cognitive_topic'] = required_topic
        traffic_payload['selected_topic'] = effective_topic
        goal_origin = str(public_goal.get('origin') or '')
        traffic_payload['selection_authority'] = (
            'EXTERNAL_MISSION'
            if required_topic and goal_origin == 'EXTERNAL_MISSION'
            else 'VIRTUALBRAIN_GOAL'
            if required_topic
            else 'TRAFFIC_OPPORTUNITY'
            if traffic_topic
            else 'UNCONSTRAINED'
        )
        return traffic_context, traffic_payload, effective_topic, goal_active

    async def _learning_focus(self, folder, cycle, snapshot, effective_topic):
        topic_context = []
        for row in snapshot['articles']:
            artifact = row['artifact']
            if effective_topic and artifact['artifact_id'] in snapshot['topics'].get(effective_topic, []):
                topic_context.append({
                    'artifact_id': artifact['artifact_id'],
                    'title': artifact['title'],
                    'summary': artifact['summary'][:500],
                })
        explorer_runtime = ReceiptRuntime(self.runtime, folder / 'explorer_calls')
        learning_memory = None
        learning_focus = None
        learning_result = None
        if (callable(getattr(self.mind, 'record_research_belief', None))
                or str(self.publication_surface).startswith('X_')):
            from .learning_research import research_memory, choose_research_focus
            learning_memory = research_memory(self.mind, self.state_root, topic=str(effective_topic or ''))
            learning_focus = await choose_research_focus(
                runtime=explorer_runtime, folder=folder, topic=str(effective_topic or ''),
                memory=learning_memory, state_root=self.state_root,
            )
            self.activity(cycle, 'LEARN', 'I’m following a concrete research question',
                           learning_focus.question)
        return topic_context, explorer_runtime, learning_memory, learning_focus

    async def _initial_exploration(self, folder, cycle, now, evidence_pool,
                                   effective_topic, learning_focus, owner_focus_fast_path,
                                   topic_context, explorer_runtime):
        self.activity(cycle, 'OBSERVE', 'I’m matching evidence to my current goal',
                       f"Filtering candidates for {effective_topic or 'the best traffic opportunity'}.")
        initial_candidates = list(evidence_pool['items'])
        research_sources = {'items': [], 'failures': []}
        if learning_focus is not None:
            research_path = folder / 'learning_initial_search.json'
            if research_path.exists():
                research_sources = json.loads(research_path.read_text(encoding='utf-8'))
            else:
                research_sources = await self.explorer.search_web_query(
                    query=learning_focus.search_query,
                    goal_topic=str(effective_topic or 'autonomous-research'), now=now,
                )
                _put(research_path, research_sources)
            initial_candidates = list({
                row['evidence_id']: row
                for row in [*initial_candidates, *research_sources.get('items', [])]
            }.values())
        if owner_focus_fast_path and effective_topic and learning_focus is None:
            initial_candidates = [
                row for row in initial_candidates
                if self.policies.focus_matches(row, effective_topic)
            ]
        initial = await self.explorer.filter_for_goal(
            runtime=explorer_runtime,
            goal_topic=(learning_focus.question if learning_focus is not None
                        else str(effective_topic or '')),
            topic_context=topic_context,
            candidates=initial_candidates,
        )
        exploration = dict(initial)
        source_failures = [*evidence_pool.get('failures', []),
                           *research_sources.get('failures', [])]
        return initial, exploration, source_failures

    async def _web_pool(self, folder, cycle, now, effective_topic, mind_plan,
                        owner_goal, owner_focus_fast_path, learning_focus):
        web_path = folder / 'web_search.json'
        if web_path.exists():
            web_pool = json.loads(web_path.read_text())
        else:
            search_web = getattr(self.explorer, 'search_web', None)
            search_mission_web = getattr(
                self.explorer, 'search_web_query', None
            )
            mission_discovery = (
                mind_plan.get('mission_discovery')
                if isinstance(mind_plan.get('mission_discovery'), dict)
                else {}
            )
            mission_query = str(
                mission_discovery.get('search_query') or ''
            ).strip()
            self.activity(cycle, 'OBSERVE', 'I don’t have a strong local match, so I’m searching the web',
                           f"Looking for fresh public evidence about {effective_topic}.")
            owner_query = (
                self.policies.focus_query(owner_goal, effective_topic)
                if owner_focus_fast_path and effective_topic
                else ''
            )
            if learning_focus is not None and callable(search_mission_web):
                web_pool = await search_mission_web(
                    query=learning_focus.search_query,
                    goal_topic=str(effective_topic), now=now,
                )
            elif mission_query and callable(search_mission_web):
                web_pool = await search_mission_web(
                    query=mission_query,
                    goal_topic=str(effective_topic),
                    now=now,
                )
            elif owner_query and callable(search_mission_web):
                web_pool = await search_mission_web(
                    query=owner_query,
                    goal_topic=str(effective_topic),
                    now=now,
                )
            elif search_web is not None:
                web_pool = await search_web(
                    goal_topic=str(effective_topic), now=now
                )
            else:
                web_pool = {'items': [], 'failures': [], 'query': None}
            web_pool['items'] = [row for row in web_pool['items']
                                 if self.policies.candidate_available(self.inbox, row)]
            if owner_focus_fast_path and effective_topic and learning_focus is None:
                web_pool['items'] = [
                    row for row in web_pool['items']
                    if self.policies.focus_matches(row, effective_topic)
                ]
                web_pool['owner_focus_query'] = owner_query
                web_pool['owner_focus_anchors'] = list(
                    self.policies.focus_anchors(effective_topic)
                )
            _put(web_path, web_pool)
        return web_pool

    async def _web_fallback(self, folder, cycle, now, effective_topic, mind_plan,
                            owner_goal, owner_focus_fast_path, learning_focus,
                            explorer_runtime, topic_context, initial, exploration,
                            source_failures):
        web_pool = await self._web_pool(
            folder, cycle, now, effective_topic, mind_plan,
            owner_goal, owner_focus_fast_path, learning_focus,
        )
        search_web = getattr(self.explorer, 'search_web', None)
        source_failures.extend(web_pool.get('failures', []))
        if web_pool['items']:
            web_filtered = await self.explorer.filter_for_goal(
                runtime=explorer_runtime, goal_topic=str(effective_topic),
                topic_context=topic_context, candidates=web_pool['items'],
            )
            exploration = dict(web_filtered)
            exploration['initial_candidate_count'] = initial.get('candidate_count', 0)
            exploration['web_candidate_count'] = web_filtered.get('candidate_count', 0)
            exploration['candidate_count'] = (exploration['initial_candidate_count'] +
                                              exploration['web_candidate_count'])
            exploration['fallback'] = 'web-search'
        elif search_web is not None:
            exploration['initial_candidate_count'] = initial.get('candidate_count', 0)
            exploration['web_candidate_count'] = 0
            exploration['fallback'] = 'web-search'
        return exploration, source_failures

    async def _exploration(self, request, evidence_pool, effective_topic,
                           goal_active, topic_context, explorer_runtime, learning_focus):
        folder, cycle, now = request.folder, request.cycle, request.now
        exploration_path = folder / 'exploration.json'
        if exploration_path.exists():
            return json.loads(exploration_path.read_text())
        initial, exploration, source_failures = await self._initial_exploration(
            folder, cycle, now, evidence_pool, effective_topic, learning_focus,
            request.owner_focus_fast_path, topic_context, explorer_runtime,
        )
        if goal_active and not initial['items'] and learning_focus is None:
            exploration, source_failures = await self._web_fallback(
                folder, cycle, now, effective_topic, request.mind_plan,
                request.owner_goal, request.owner_focus_fast_path, learning_focus,
                explorer_runtime, topic_context, initial, exploration, source_failures,
            )
        exploration['source_failures'] = source_failures
        _put(exploration_path, exploration)
        self.activity(cycle, 'OBSERVE', 'I finished checking the evidence',
                       f"Reviewed {exploration.get('candidate_count', 0)} candidates; {len(exploration.get('items', []))} remain relevant.",
                       status='completed')
        return exploration

    async def _no_evidence(self, request, result, *, effective_topic, goal_active,
                           exploration):
        from .learning_application import assess_application
        await assess_application(
            runtime=result.explorer_runtime, folder=request.folder,
            state_root=self.state_root,
            topic=str(effective_topic or 'autonomous-research'),
        )
        if self.mind is not None and hasattr(self.mind, 'record_research_belief'):
            cognition = {
                'cognition_state': 'NO_MATERIAL_CHANGE', 'worth_saying': False,
                'expression_kind': 'NONE',
                'what_changed': 'No admissible fresh evidence changed my current view.',
                'what_matters': 'Keep observing rather than manufacture a conclusion from an empty evidence set.',
                'belief_proposition': None,
                'rationale': 'A persistent agent can complete a cognition cycle without forcing an expression.',
            }
            _put(request.folder / 'cognition.json', cognition)
            self.activity(request.cycle, 'DECIDE', 'Nothing changed enough to justify publishing',
                          cognition['what_matters'], status='completed')
            return {
                'status': 'COGNITION_ONLY', 'cycle': request.cycle,
                'mind_plan': request.mind_plan, 'cognition': cognition,
                'candidate_count': exploration.get('candidate_count', 0),
                'source_failures': exploration.get('source_failures',
                                                  result.evidence_pool.get('failures', [])),
            }
        status = 'NO_RELEVANT_EVIDENCE' if goal_active else 'NO_FRESH_EVIDENCE'
        self.activity(request.cycle, 'OBSERVE', 'I don’t have enough evidence to write yet',
                      'The current evidence scan did not produce an admissible item, so this cycle stops here.',
                      status='stopped')
        return {
            'status': status, 'cycle': request.cycle, 'mind_plan': request.mind_plan,
            'candidate_count': exploration.get('candidate_count', 0),
            'source_failures': exploration.get('source_failures',
                                              result.evidence_pool.get('failures', [])),
        }

    async def admit(self, request: EvidenceRequest, result: EvidenceResult,
                    *, goal_origin: str) -> EvidenceAdmission:
        exploration = result.exploration
        evidence = exploration['items']
        topic, active = result.effective_topic, result.goal_active
        payload = result.traffic_payload
        if (not evidence and result.evidence_pool.get('items')
                and self.policies.allow_output_fallback(
                    goal_active=active, goal_origin=goal_origin,
                    owner_goal=request.owner_goal,
                )):
            evidence = list(result.evidence_pool['items'][:12])
            exploration = {**exploration, 'items': evidence, 'fallback': 'output-first-unconstrained'}
            _put(request.folder / 'output_fallback.json', {
                'reason': 'GOAL_EVIDENCE_MISS', 'candidate_count': len(evidence),
                'original_topic': topic, 'selection_authority': 'OUTPUT_FALLBACK',
            })
            topic, active = None, False
            payload['selected_topic'] = None
            payload['selection_authority'] = 'OUTPUT_FALLBACK'
            self.activity(
                request.cycle, 'OBSERVE',
                'My current interest had no usable match, so I’m switching to a viable story',
                f"Keeping the missed goal in memory while considering {len(evidence)} real evidence items for this production slot.",
                status='completed',
            )
        early = None
        if not evidence:
            early = await self._no_evidence(
                request, result, effective_topic=topic,
                goal_active=active, exploration=exploration,
            )
        return EvidenceAdmission(evidence, exploration, payload, topic, active, early)

    async def run(self, request: EvidenceRequest) -> EvidenceResult:
        pool = await self._candidate_pool(
            request.folder, request.cycle, request.now, request.required_topic,
            request.owner_seed_items, request.prefetched_evidence_pool,
        )
        traffic, payload, topic, active = self._traffic_view(
            request.folder, request.cycle_now, request.snapshot, pool,
            request.required_topic, request.public_goal,
        )
        context, runtime, memory, focus = await self._learning_focus(
            request.folder, request.cycle, request.snapshot, topic,
        )
        exploration = await self._exploration(
            request, pool, topic, active, context, runtime, focus,
        )
        return EvidenceResult(
            pool, traffic, payload, topic, active, context, runtime,
            memory, focus, exploration,
        )
