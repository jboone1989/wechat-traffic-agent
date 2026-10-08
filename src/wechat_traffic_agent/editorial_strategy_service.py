"""Prospective editorial strategy and process-variant decisions.

Strategy forecasts happen before the strategy is committed. Process memory
only affects the selected execution variant, not which evidence is true.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json

from .content_loop import _put
from .content_action_prediction import bind_action_prediction, forecast_strategy_candidates
from .content_strategy import bind_content_strategy
from .traffic_learning import TopicTrafficSignal


@dataclass(frozen=True)
class StrategyDecisionRequest:
    folder: Path
    cycle: str
    now: object
    selection: object
    traffic_context: object
    market_pulse: bool
    experiment: object | None


@dataclass(frozen=True)
class StrategyDecisionResult:
    strategy: object
    strategy_payload: dict
    mind_context: dict | None
    process_skill: dict | None
    process_steps: list
    retry_verification_once: bool
    action_prediction: object
    action_prediction_payload: dict


class EditorialStrategyService:
    def __init__(self, *, state_root, account, mind, memory_life, evolution_bridge, activity):
        self.state_root, self.account = state_root, account
        self.mind, self.memory_life = mind, memory_life
        self.evolution_bridge, self.activity = evolution_bridge, activity

    def _mind_context(self, request):
        path = request.folder / 'mind_strategy_context.json'
        if path.exists():
            return json.loads(path.read_text(encoding='utf-8'))
        if self.mind is None or not hasattr(self.mind, 'strategy_context'):
            return None
        context = self.mind.strategy_context(topic=request.selection.topic)
        _put(path, context)
        return context

    def _forecasts(self, request, mind_context):
        folder, cycle, now = request.folder, request.cycle, request.now
        strategy_path = folder / 'content_strategy.json'
        path = folder / 'action_forecasts.json'
        if strategy_path.exists() and not path.exists():
            raise ValueError('cannot create prospective action forecasts after strategy selection has begun')
        if path.exists():
            forecasts = json.loads(path.read_text(encoding='utf-8'))
        else:
            forecasts = forecast_strategy_candidates(
                state_root=self.state_root, account=self.account,
                topic=request.selection.topic, as_of=now,
                mind_strategy_context=mind_context,
            )
            _put(path, forecasts)
        self.activity(
            cycle, 'PREDICT', 'I forecasted the outcomes of six possible content strategies',
            'Each candidate now has explicit 1h, 4h, and 24h pageview expectations, uncertainty, expected utility, and information gain.',
            status='completed',
        )
        return forecasts

    def _evolution_context(self, request):
        path = request.folder / 'evolution_strategy_context.json'
        if path.exists():
            stored = json.loads(path.read_text(encoding='utf-8'))
            return stored if isinstance(stored, dict) else {}
        if self.evolution_bridge is None:
            return {}
        context = self.evolution_bridge.preferred_profile(
            cycle=request.cycle, now=request.now,
        )
        _put(path, context)
        return context

    def _process_variant(self, request):
        default_steps = [
            'select evidence', 'draft content', 'run evidence and safety checks',
            'publish only after checks pass',
        ]
        process_skill = self.memory_life.select_process_variant(
            action='content-create-verify-publish',
            context={
                'satisfied_preconditions': ['required capabilities and resources are available'],
                'cycle': request.cycle, 'topic': request.selection.topic,
            },
        )
        steps = list((process_skill or {}).get('steps') or default_steps)
        _put(request.folder / 'memory_process_variant.json', {
            'status': 'SELECTED' if process_skill is not None else 'BASELINE_NO_PRIOR_PROCEDURE',
            'action': 'content-create-verify-publish',
            'procedure_id': str((process_skill or {}).get('procedure_id') or ''),
            'variant_id': str((process_skill or {}).get('variant_id') or ''),
            'policy_action': str((process_skill or {}).get('policy_action') or 'DIRECT'),
            'policy_reason': str((process_skill or {}).get('policy_reason') or ''),
            'steps': steps, 'causal_claim': False,
        })
        retry_once = str((process_skill or {}).get('policy_action') or 'DIRECT') == 'RETRY_ONCE_SAFE'
        return process_skill, steps, retry_once

    def decide(self, request: StrategyDecisionRequest) -> StrategyDecisionResult:
        context = self._mind_context(request)
        forecasts = self._forecasts(request, context)
        evolution = self._evolution_context(request)
        preferred = str(evolution.get('profile_name') or '').strip() or None
        preferred = preferred or ('follow-up-update' if request.market_pulse else None)
        signal = next(
            (row for row in request.traffic_context.topics if row.topic == request.selection.topic),
            None,
        )
        if signal is None:
            signal = TopicTrafficSignal(
                topic=request.selection.topic, article_count=0, orphan_article_count=0,
                search_rows=0, impressions=0, clicks=0, ctr=None, average_position=None,
                pageview_samples_24h=0, mean_pageviews_24h=None, trend_hits=0,
                demand_score=0.0, priority_score=0.0, exposure_status='UNKNOWN',
            )
        strategy = bind_content_strategy(
            request.folder / 'content_strategy.json',
            state_root=self.state_root, account=self.account, cycle=request.cycle,
            topic=request.selection.topic, traffic_signal=signal, decided_at=request.now,
            opening_mode=(request.experiment.treatment_value if request.experiment is not None else None),
            preferred_profile_name=preferred, mind_strategy_context=context,
            action_forecasts=forecasts,
        )
        skill, steps, retry = self._process_variant(request)
        prediction = bind_action_prediction(
            request.folder / 'action_prediction.json',
            cycle=request.cycle, topic=request.selection.topic,
            strategy_id=strategy.strategy_id, selected_profile_name=strategy.profile.name,
            created_at=request.now, candidate_forecasts=forecasts,
        )
        if self.mind is not None and hasattr(self.mind, 'register_action_prediction'):
            self.mind.register_action_prediction(prediction)
        if self.evolution_bridge is not None:
            self.evolution_bridge.record_strategy_decision(
                cycle=request.cycle, strategy=strategy, prediction=prediction,
                trial_context=evolution,
            )
        self.activity(
            request.cycle, 'DECIDE', 'I chose the content strategy from prospective forecasts',
            f"Using {strategy.profile.name} with {strategy.profile.structure_pattern}; authority={strategy.selection_mode}.",
            status='completed',
        )
        return StrategyDecisionResult(
            strategy, strategy.model_dump(mode='json'), context, skill, steps,
            retry, prediction, prediction.model_dump(mode='json'),
        )
