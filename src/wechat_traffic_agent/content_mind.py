"""Persistent VirtualBrain interest and endogenous-goal adapter for Content Agent."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from hashlib import sha256
import json
import os
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Mapping

from .memory_life import MemoryLifeRuntime
from .memory_prior import MemoryDecisionPrior

MIND_SCHEMA = "content-mind:v1"
# ContractV1AgencySession only accepts capability-backed experience authorities from
# AutobiographicalSelf.TRUSTED_EXPERIENCE_SOURCES. "session" is the canonical
# host/session ingestion authority used by VirtualBrain's own experience→goal eval.
AUTHORITY_ID = "session"
MAX_PLANS = 128
MAX_EVENT_IDS = 512
MAX_STRATEGY_OUTCOMES = 512
MIN_STRATEGY_PROFILE_SAMPLES = 2
MIN_PERSISTENT_WORLD_INTEREST_IMPORTANCE = 0.7


class ContentMind:
    """Keep one VirtualBrain agency session as the Content Agent's longitudinal mind."""

    def __init__(self, *, root: Path, subject_id: str) -> None:
        from virtualbrain.adapter.service import ContractV1AgencySession
        from virtualbrain.research.provenance import TrustedIngestionAuthority

        self.state_root = Path(root)
        self.root = self.state_root / "application" / "content_mind"
        self.subject_id = subject_id
        self.root.mkdir(parents=True, exist_ok=True)
        self.memory_life = MemoryLifeRuntime(self.state_root)
        self.memory_prior = MemoryDecisionPrior(self.state_root, store=self.memory_life.store)
        from .world_learning import WorldLearningLedger
        self.world_learning = WorldLearningLedger(self.state_root)
        self.authority = TrustedIngestionAuthority(AUTHORITY_ID, self._authority_key())
        self.session = ContractV1AgencySession(subject_id, trusted_authorities=[self.authority])
        if not hasattr(self.session, "register_prediction") or not hasattr(self.session, "predictive_learning"):
            raise RuntimeError("Content Agent requires VirtualBrain >=0.3 causal learning")
        if not hasattr(self.session, "resource_awareness"):
            raise RuntimeError("Content Agent requires VirtualBrain resource awareness")
        self._plans: dict[str, dict[str, Any]] = {}
        self._observed_event_ids: list[str] = []
        self._strategy_outcomes: dict[str, dict[str, Any]] = {}
        self._inquiry_topics: dict[str, str] = {}
        self._restore()
        self._life_memory_last_sync = self._sync_life_memory(limit=256)

    @property
    def checkpoint_path(self) -> Path:
        return self.root / "checkpoint.json"

    def _authority_key(self) -> bytes:
        path = self.root / "authority.key"
        if path.exists():
            key = path.read_bytes()
            if len(key) < 32:
                raise ValueError("Content Mind authority key is invalid")
            return key
        key = os.urandom(32)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(key)
        return key

    def _restore(self) -> None:
        if not self.checkpoint_path.exists():
            return
        row = json.loads(self.checkpoint_path.read_text(encoding="utf-8"))
        legacy = {"schema_version", "subject_id", "subject", "plans", "observed_event_ids"}
        strategy_version = legacy | {"strategy_outcomes"}
        current = strategy_version | {"inquiry_topics"}
        allowed = {frozenset(legacy), frozenset(strategy_version), frozenset(current)}
        if not isinstance(row, dict) or frozenset(row) not in allowed:
            raise ValueError("Content Mind checkpoint fields mismatch")
        if row["schema_version"] != MIND_SCHEMA or row["subject_id"] != self.subject_id:
            raise ValueError("Content Mind checkpoint identity mismatch")
        if not isinstance(row["plans"], dict) or not isinstance(row["observed_event_ids"], list):
            raise ValueError("Content Mind checkpoint collections are invalid")
        if "strategy_outcomes" in row and not isinstance(row["strategy_outcomes"], dict):
            raise ValueError("Content Mind strategy outcomes are invalid")
        if "inquiry_topics" in row and not isinstance(row["inquiry_topics"], dict):
            raise ValueError("Content Mind inquiry topics are invalid")
        self.session.restore(row["subject"])
        self._plans = deepcopy(row["plans"])
        self._observed_event_ids = [str(item) for item in row["observed_event_ids"]]
        self._strategy_outcomes = deepcopy(row.get("strategy_outcomes") or {})
        self._inquiry_topics = {str(key): str(value) for key, value in (row.get("inquiry_topics") or {}).items()}

    def _sync_life_memory(self, *, limit: int = 64) -> dict[str, Any]:
        try:
            snapshot = self.session.snapshot()
            autobiography = snapshot.get("autobiography") if isinstance(snapshot, Mapping) else {}
            events = list((autobiography or {}).get("events") or [])
            return self.memory_life.backfill_life_history(events, limit=limit)
        except Exception as exc:
            return {"status": "ERROR", "error_type": type(exc).__name__, "recorded": 0, "already_recorded": 0}

    def _persist(self) -> None:
        payload = {
            "schema_version": MIND_SCHEMA,
            "subject_id": self.subject_id,
            "subject": self.session.snapshot(),
            "plans": self._plans,
            "observed_event_ids": self._observed_event_ids,
            "strategy_outcomes": self._strategy_outcomes,
            "inquiry_topics": self._inquiry_topics,
        }
        tmp = self.checkpoint_path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, self.checkpoint_path)
        self._life_memory_last_sync = self._sync_life_memory(limit=64)

    def persist_resource_awareness(self) -> None:
        """Persist provider-neutral resource state after a governed external observation."""
        self._persist()

    def _prior_inquiry_topic(self, inquiry_id: str) -> str | None:
        mapped = self._inquiry_topics.get(str(inquiry_id))
        if isinstance(mapped, str) and mapped.strip():
            return mapped
        for key in reversed(sorted(self._plans)):
            topic = (self._plans[key].get("world_inquiry_topics") or {}).get(inquiry_id)
            if isinstance(topic, str) and topic.strip():
                return topic
        return None

    def _observe_world_attention(self, observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Let attended reality change salience/familiarity without inventing preference."""
        updates: list[dict[str, Any]] = []
        for row in observations:
            topic = row["topic"]
            evidence_id = row["evidence_id"]
            if row["importance"] < MIN_PERSISTENT_WORLD_INTEREST_IMPORTANCE:
                update = {
                    "topic": topic, "status": "SHORT_TERM_ONLY", "interest": None,
                    "importance": row["importance"],
                }
                if row.get("learning_kind", "WORLD_EVENT") != "WORLD_EVENT":
                    update["learning_kind"] = row["learning_kind"]
                updates.append(update)
                continue
            event_id = f"world-attention:{topic}:{evidence_id}"
            if event_id in self._observed_event_ids:
                interest = self.session.interests.get(topic)
                update = {
                    "topic": topic, "status": "ALREADY_OBSERVED",
                    "interest": interest.__dict__ if interest else None,
                }
                if row.get("learning_kind", "WORLD_EVENT") != "WORLD_EVENT":
                    update["learning_kind"] = row["learning_kind"]
                updates.append(update)
                continue
            refs = list(dict.fromkeys(
                str(ref).strip() for ref in (row.get("evidence_refs") or (evidence_id,)) if str(ref).strip()
            ))
            if evidence_id not in refs:
                refs.insert(0, evidence_id)
            self.session.ingest_life_experience(
                {
                    "event_id": event_id, "tick": int(self.session.tick),
                    "summary": f"The world feed drew attention to {topic}: {row['question']}",
                    "evidence_refs": refs, "significance": row["importance"],
                    "source": "world-feed-attention", "confidence": 1.0,
                    # Attention is not success/failure. Neutral reward plus neutral effort keeps
                    # preference unchanged while VirtualBrain learns familiarity and salience.
                    "outcome": 0.5, "domain": "crypto-finance-world",
                },
                authority_id=AUTHORITY_ID,
                interest_feedback={
                    "name": topic, "novelty": row["novelty"],
                    "effort": 0.5, "learning_rate": 0.1,
                },
            )
            self._observed_event_ids.append(event_id)
            self._observed_event_ids = self._observed_event_ids[-MAX_EVENT_IDS:]
            interest = self.session.interests.get(topic)
            update = {
                "topic": topic, "status": "OBSERVED",
                "interest": interest.__dict__ if interest else None,
            }
            if row.get("learning_kind", "WORLD_EVENT") != "WORLD_EVENT":
                update["learning_kind"] = row["learning_kind"]
            updates.append(update)
        return updates

    def plan_for_cycle(
        self, *, cycle: str, available_topics: Iterable[str],
        world_observations: Iterable[Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
        """Advance cognition after exposing the mind to bounded real-world observations."""
        if cycle in self._plans:
            return deepcopy(self._plans[cycle])
        observations = []
        for raw in world_observations:
            if not isinstance(raw, Mapping):
                continue
            topic, question = str(raw.get("topic") or "").strip(), str(raw.get("question") or "").strip()
            if not topic or not question:
                continue
            observations.append({
                "evidence_id": str(raw.get("evidence_id") or ""),
                "topic": topic, "question": question,
                "importance": min(1.0, max(0.0, float(raw.get("importance") or 0.0))),
                "novelty": min(1.0, max(0.0, float(raw.get("novelty") or 0.0))),
                "why_now": str(raw.get("why_now") or "")[:300],
                "learning_kind": str(raw.get("learning_kind") or "WORLD_EVENT"),
                "evidence_refs": list(dict.fromkeys(
                    str(ref).strip() for ref in (raw.get("evidence_refs") or ()) if str(ref).strip()
                )),
            })
        world_interest_updates = self._observe_world_attention(observations)
        persistent_interests = set(self.session.interests.snapshot()["items"])
        topics = tuple(sorted(
            {str(item) for item in available_topics if str(item).strip()}
            | {row["topic"] for row in observations}
            | persistent_interests
        ))
        before = self.session.inquiry.inquiries
        open_questions = {state.get("question") for state in before.values() if state.get("status") == "OPEN"}
        recent_events = tuple({
            "kind": "curiosity",
            "data": {
                "question": row["question"],
                "intensity": row["importance"],
                "personal_relevance": max(row["importance"], row["novelty"]),
                "hypotheses": {"worth-investigating": 0.5},
            },
        } for row in observations if row["question"] not in open_questions)
        readout = self.session.idle_tick(
            body_state={},
            world_state={"available_content_topics": list(topics), "world_observations": observations},
            recent_events=recent_events,
        )
        goal = (
            json.loads(json.dumps(readout["goal_proposals"][0], ensure_ascii=False, sort_keys=True))
            if readout["goal_proposals"] else None
        )
        after = self.session.inquiry.inquiries
        inquiry_topics: dict[str, str] = {}
        by_question = {row["question"]: row["topic"] for row in observations}
        for inquiry_id, state in after.items():
            if inquiry_id not in before and state.get("question") in by_question:
                inquiry_topics[inquiry_id] = by_question[state["question"]]
                self._inquiry_topics[inquiry_id] = inquiry_topics[inquiry_id]
        required_topic = None
        if goal and goal.get("origin") == "INTEREST":
            refs = [str(item) for item in goal.get("interest_refs") or ()]
            required_topic = next((item for item in refs if item in topics), None)
        elif goal and goal.get("origin") == "INQUIRY":
            refs = [str(item) for item in goal.get("inquiry_refs") or ()]
            if refs:
                required_topic = inquiry_topics.get(refs[0]) or self._prior_inquiry_topic(refs[0])
        plan = {
            "cycle": cycle, "mind_tick": int(readout["tick"]), "goal": goal,
            "required_topic": required_topic, "available_topics": list(topics),
            "world_observations": observations, "world_inquiry_topics": inquiry_topics,
            "world_interest_updates": world_interest_updates,
        }
        self._plans[cycle] = plan
        if len(self._plans) > MAX_PLANS:
            oldest = sorted(self._plans)[: len(self._plans) - MAX_PLANS]
            for key in oldest:
                self._plans.pop(key, None)
        self._persist()
        return deepcopy(plan)

    def record_research_belief(
        self, *, cycle: str, topic: str, question: str, source_evidence: Mapping[str, Any],
        proposition: str, stance: float, reliability: float, support: float,
        inquiry_id: str | None = None, inquiry_origin: str = "self_question",
        commit: bool = True,
    ) -> dict[str, Any]:
        """Attach trusted research evidence to an inquiry and optionally revise worldview."""
        topic, question, proposition = (str(value).strip() for value in (topic, question, proposition))
        if not topic or not question or not proposition:
            raise ValueError("research belief requires topic, question, and proposition")
        stance, reliability, support = float(stance), float(reliability), float(support)
        if not -1.0 <= stance <= 1.0 or not 0.0 <= reliability <= 1.0 or not 0.0 <= support <= 1.0:
            raise ValueError("research belief scores are out of range")
        raw_id = str(source_evidence.get("evidence_id") or "").strip()
        title = str(source_evidence.get("title") or "").strip()
        revision = str(source_evidence.get("source_revision") or "").strip()
        if not raw_id or not title or not revision:
            raise ValueError("research belief requires identified source evidence")
        evidence_key = json.dumps(
            {"evidence_id": raw_id, "source_revision": revision},
            sort_keys=True, ensure_ascii=False, separators=(",", ":"),
        )
        evidence_id = "world-evidence:" + sha256(evidence_key.encode()).hexdigest()[:24]
        epistemic = self.session.epistemic
        for revision_row in self.session.worldview.snapshot()["history"]:
            if (revision_row["proposition"] == proposition
                    and evidence_id in revision_row["payload"]["evidence_refs"]):
                existing = self.session.worldview.claim(proposition)
                return {
                    "status": "ALREADY_RECORDED", "topic": topic,
                    "inquiry_id": str(revision_row["provenance"]["source_event_id"]).removeprefix("inquiry:"),
                    "evidence_id": evidence_id, "proposition": proposition,
                    "stance": float(existing.stance) if existing else 0.0,
                    "confidence": float(existing.confidence) if existing else 0.0,
                }
        if evidence_id not in epistemic.evidence:
            # This claim is deliberately about observation, not source truth. Trust here means
            # ContentMind can prove what source material it actually consumed.
            epistemic_claim = "Observed source material: " + title[:300]
            self.session.ingest_trusted_evidence({
                "evidence_id": evidence_id, "source": AUTHORITY_ID,
                "claim": epistemic_claim, "value": 1.0, "reliability": 1.0,
            }, authority_id=AUTHORITY_ID)

        inquiries = self.session.inquiry.inquiries
        active_id = str(inquiry_id or "").strip()
        inquiry_origin = str(inquiry_origin or "self_question").strip()
        if active_id and active_id in inquiries and inquiries[active_id]["status"] != "OPEN":
            active_id = ""
        if active_id and active_id not in inquiries:
            self.session.inquiry.open(
                inquiry_id=active_id, question=question, origin=inquiry_origin,
                personal_relevance=0.7, hypotheses={proposition: 0.5},
            )
            self._inquiry_topics[active_id] = topic
        if not active_id:
            digest = sha256(f"{cycle}|{topic}|{question}".encode()).hexdigest()[:16]
            active_id = f"research-{digest}"
            if active_id not in inquiries:
                self.session.inquiry.open(
                    inquiry_id=active_id, question=question, origin=inquiry_origin,
                    personal_relevance=0.7, hypotheses={proposition: 0.5},
                )
            self._inquiry_topics[active_id] = topic
        state = self.session.inquiry.inquiries[active_id]
        if proposition not in state["hypotheses"]:
            self.session.add_inquiry_hypothesis(active_id, hypothesis=proposition, confidence=0.5)
            state = self.session.inquiry.inquiries[active_id]
        if evidence_id not in state["evidence_refs"]:
            self.session.inquiry.add_evidence(
                inquiry_id=active_id, evidence_ref=evidence_id,
                hypothesis=proposition, support=support,
            )
        if not commit:
            self._persist()
            return {
                "status": "DEFERRED", "topic": topic, "inquiry_id": active_id,
                "evidence_id": evidence_id, "proposition": proposition,
            }

        state = self.session.inquiry.inquiries[active_id]
        if state["status"] == "OPEN":
            self.session.inquiry.resolve(inquiry_id=active_id)
        before = self.session.worldview.claim(proposition)
        claim = self.session.resolve_inquiry_to_worldview(
            active_id, proposition=proposition, stance=stance, reliability=reliability,
            authority_id=AUTHORITY_ID,
        )
        self._persist()
        return {
            "status": "REVISED" if before is not None else "FORMED",
            "topic": topic, "inquiry_id": active_id, "evidence_id": evidence_id,
            "proposition": claim.proposition, "stance": float(claim.stance),
            "confidence": float(claim.confidence),
            "previous": (before.__dict__ if before is not None else None),
        }

    def observe_prediction_outcome(
        self, *, prediction_id: str, topic: str, statement: str, status: str,
        confidence: float, prediction_error: float, reliability: float, evidence_refs: Iterable[str],
    ) -> dict[str, Any]:
        """Turn a matured external-world prediction into owned VirtualBrain experience."""
        prediction_id, topic, statement = (str(value).strip() for value in (prediction_id, topic, statement))
        if not prediction_id or not topic or not statement:
            raise ValueError("prediction outcome requires prediction id, topic, and statement")
        if status not in {"CONFIRMED", "REFUTED"}:
            raise ValueError("prediction outcome must be terminal")
        confidence, prediction_error, reliability = float(confidence), float(prediction_error), float(reliability)
        if any(not 0.0 <= value <= 1.0 for value in (confidence, prediction_error, reliability)):
            raise ValueError("prediction outcome scores are out of range")
        refs = list(dict.fromkeys(str(ref).strip() for ref in evidence_refs if str(ref).strip()))
        if not refs:
            raise ValueError("prediction outcome requires evidence_refs")
        event_id = "prediction-outcome:" + prediction_id
        if event_id in self._observed_event_ids:
            return {
                "status": "ALREADY_OBSERVED", "event_id": event_id, "prediction_id": prediction_id,
                "outcome_status": status, "prediction_error": prediction_error,
            }
        outcome = 1.0 if status == "CONFIRMED" else 0.0
        self.session.ingest_life_experience(
            {
                "event_id": event_id, "tick": int(self.session.tick),
                "summary": f"Dated world prediction {status.lower()}: {statement[:500]}",
                "evidence_refs": refs, "significance": reliability,
                "source": "prospective-world-outcome", "confidence": reliability,
                "outcome": outcome, "domain": "crypto-finance-world",
                "prediction_error": prediction_error,
                "identity_relevance": 0.5,
                "emotional_intensity": 0.0,
            },
            authority_id=AUTHORITY_ID,
        )
        self._observed_event_ids.append(event_id)
        self._observed_event_ids = self._observed_event_ids[-MAX_EVENT_IDS:]
        self._persist()
        return {
            "status": "OBSERVED", "event_id": event_id, "prediction_id": prediction_id,
            "outcome_status": status, "prediction_confidence": confidence,
            "prediction_error": prediction_error, "reliability": reliability,
        }

    def register_action_prediction(self, prediction: Any) -> dict[str, Any]:
        """Register Content's selected 1h/4h/24h forecasts in VirtualBrain core."""
        from datetime import timedelta
        from .content_action_prediction import ContentActionPrediction, WINDOW_HOURS

        row = prediction if isinstance(prediction, ContentActionPrediction) else ContentActionPrediction.model_validate(prediction)
        created = row.created_at
        registered: list[str] = []
        world_commitments: list[dict[str, Any]] = []
        existing = self.session.predictive_learning.predictions
        for metric in row.selected.metrics:
            brain_prediction_id = f"{row.prediction_id}:{metric.window}"
            if brain_prediction_id in existing:
                continue
            refs = list(dict.fromkeys([row.prediction_id, row.strategy_id, *metric.evidence_refs]))
            self.session.register_prediction(
                prediction_id=brain_prediction_id, domain="content", context_key=row.topic,
                action_key=row.selected_profile_name, target=f"website:http_pageviews:{metric.window}",
                expected=metric.expected_pageviews, confidence=metric.confidence,
                created_at=created.isoformat(), evidence_cutoff_at=created.isoformat(),
                due_at=(created + timedelta(hours=WINDOW_HOURS[metric.window])).isoformat(),
                evidence_refs=refs, lower=metric.lower_bound, upper=metric.upper_bound,
            )
            world_commitments.append({
                "action_kind": "CONTENT_ACTION_PREDICTION",
                "prediction": (
                    f"{row.selected_profile_name} on {row.topic} will produce about "
                    f"{metric.expected_pageviews:.2f} pageviews by {metric.window}"
                ),
                "expected_utility": 0.5,
                "confidence": metric.confidence,
                "evidence_refs": refs,
                "now": created,
                "windows": ((metric.window, timedelta(hours=WINDOW_HOURS[metric.window])),),
                "metadata": {
                    "brain_prediction_id": brain_prediction_id,
                    "content_prediction_id": row.prediction_id,
                    "topic": row.topic,
                    "profile_name": row.selected_profile_name,
                    "expected_pageviews": metric.expected_pageviews,
                },
            })
            registered.append(brain_prediction_id)
        if registered:
            # Persist the canonical VirtualBrain prediction first.  A crash may
            # omit supplemental world-learning state, but must never leave a
            # durable orphan action whose canonical prediction disappeared.
            self._persist()
            for commitment in world_commitments:
                self.world_learning.commit(**commitment)
        return {"status": "REGISTERED" if registered else "ALREADY_REGISTERED", "prediction_ids": registered}

    def observe_action_prediction_outcomes(self, outcomes: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
        """Settle Content outcomes through VirtualBrain's shared predictive-learning core."""
        from virtualbrain.research.predictive_learning import PredictionAssessment
        from .content_action_prediction import ERROR_INQUIRY_THRESHOLD

        def content_error(prediction: Mapping[str, Any], observed: float) -> PredictionAssessment:
            expected = float(prediction["expected"])
            actual = float(observed)
            denom = actual + expected + 1.0
            signed = max(-1.0, min(1.0, (actual - expected) / denom))
            return PredictionAssessment(min(1.0, abs(actual - expected) / denom), signed)

        applied: list[dict[str, Any]] = []
        inquiries_opened: list[dict[str, Any]] = []
        duplicates = 0
        rows = sorted(
            (dict(raw) for raw in outcomes if isinstance(raw, Mapping)),
            key=lambda row: (str(row.get("observed_at") or ""), str(row.get("prediction_id") or ""), str(row.get("window") or "")),
        )
        for row in rows:
            prediction_id = str(row.get("prediction_id") or "").strip()
            publication_id = str(row.get("publication_id") or "").strip()
            topic = str(row.get("topic") or "").strip()
            profile = str(row.get("profile_name") or "").strip()
            window = str(row.get("window") or "").strip()
            refs = list(dict.fromkeys(str(ref).strip() for ref in row.get("evidence_refs", ()) if str(ref).strip()))
            if not prediction_id or not publication_id or not topic or not profile or window not in ERROR_INQUIRY_THRESHOLD or not refs:
                raise ValueError("action prediction outcome is incomplete")
            error = float(row.get("prediction_error"))
            reliability = float(row.get("reliability"))
            expected = float(row.get("expected_pageviews"))
            actual = float(row.get("actual_pageviews"))
            if any(not 0.0 <= value <= 1.0 for value in (error, reliability)) or min(expected, actual) < 0:
                raise ValueError("action prediction outcome scores are invalid")
            event_id = f"content-action-outcome:{prediction_id}:{window}"
            if event_id in self._observed_event_ids:
                duplicates += 1
                continue
            brain_prediction_id = f"{prediction_id}:{window}"
            observed_at = datetime.fromisoformat(
                str(row.get("observed_at")).replace("Z", "+00:00")
            )
            if observed_at.tzinfo is None or observed_at.utcoffset() is None:
                raise ValueError("action prediction outcome observed_at must be timezone-aware")
            result = self.session.settle_prediction(
                prediction_id=brain_prediction_id,
                outcome_id=f"content-outcome:{publication_id}:{window}",
                observed=actual, observed_at=str(row.get("observed_at")), evidence_refs=refs,
                maturity=str(row.get("maturity")), reliability=reliability,
                learning_authority=(str(row.get("maturity")) == "MATURE"),
                metric=content_error, inquiry_threshold=ERROR_INQUIRY_THRESHOLD[window],
            )
            world_action = self.world_learning.find_by_metadata(
                "brain_prediction_id", brain_prediction_id
            )
            if world_action is not None:
                self.world_learning.settle(
                    action_id=str(world_action["action_id"]),
                    window=window,
                    actual_utility=(actual / (actual + expected + 1.0)),
                    evidence_refs=refs,
                    now=observed_at,
                    counterfactual=(
                        "Observational forecast settlement only; no randomized control, "
                        "so causal attribution remains unresolved."
                    ),
                    causal_factors={},
                )
            core_error = float(result["normalized_error"])
            if abs(core_error - error) > 1e-9:
                raise ValueError("Content and VirtualBrain prediction-error calculations diverged")
            self.session.ingest_life_experience(
                {
                    "event_id": event_id, "tick": int(self.session.tick),
                    "summary": (
                        f"Content action prediction for {profile} on {topic} at {window}: "
                        f"forecast {expected:.2f} pageviews, observed {actual:.2f}; prediction error {error:.3f}."
                    ),
                    "evidence_refs": refs, "significance": min(1.0, max(0.1, error * reliability)),
                    "source": "content-action-prediction-outcome", "confidence": reliability,
                    "outcome": max(0.0, 1.0 - error), "domain": "content-prediction",
                    "prediction_error": error, "identity_relevance": 0.4, "emotional_intensity": 0.0,
                },
                authority_id=AUTHORITY_ID,
            )
            inquiry_id = result.get("inquiry_id")
            if inquiry_id:
                self._inquiry_topics[str(inquiry_id)] = topic
                inquiry = self.session.inquiry.inquiries[str(inquiry_id)]
                for hypothesis in (
                    "topic-demand-mismatch", "title-promise-mismatch", "structure-fit-mismatch",
                    "distribution-timing", "traffic-baseline-miscalibrated",
                ):
                    if hypothesis not in inquiry["hypotheses"]:
                        self.session.add_inquiry_hypothesis(str(inquiry_id), hypothesis=hypothesis, confidence=0.2)
                inquiries_opened.append({
                    "inquiry_id": inquiry_id, "question": self.session.inquiry.inquiries[str(inquiry_id)]["question"],
                    "prediction_id": prediction_id, "window": window, "prediction_error": error,
                })
            self._observed_event_ids.append(event_id)
            self._observed_event_ids = self._observed_event_ids[-MAX_EVENT_IDS:]
            applied.append({
                "event_id": event_id, "prediction_id": prediction_id, "window": window,
                "prediction_error": error, "reliability": reliability,
                "virtualbrain_outcome_id": result["outcome_id"],
            })
        if applied:
            self._persist()
        return {
            "status": "OBSERVED" if applied else "NO_NEW_OUTCOMES",
            "applied": applied, "duplicates": duplicates, "inquiries_opened": inquiries_opened,
        }

    def observe_strategy_outcomes(self, outcomes: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
        """Ingest mature Website outcomes as persistent, non-causal strategy experience."""
        applied: list[dict[str, Any]] = []
        duplicates = 0
        rows = sorted(
            (dict(raw) for raw in outcomes if isinstance(raw, Mapping)),
            key=lambda row: (str(row.get("observed_at") or ""), str(row.get("outcome_id") or "")),
        )
        for row in rows:
            outcome_id = str(row.get("outcome_id") or "").strip()
            topic = str(row.get("topic") or "").strip()
            profile = str(row.get("profile_name") or "").strip()
            refs = list(dict.fromkeys(
                str(ref).strip() for ref in row.get("evidence_refs", ()) if str(ref).strip()
            ))
            raw_pageviews = row.get("pageviews_24h")
            if not outcome_id or not topic or not profile or not refs:
                raise ValueError("strategy outcome requires id, topic, profile, and evidence_refs")
            if isinstance(raw_pageviews, bool) or not isinstance(raw_pageviews, (int, float)) or raw_pageviews < 0:
                raise ValueError("strategy outcome requires non-negative 24h pageviews")
            pageviews = float(raw_pageviews)
            if outcome_id in self._strategy_outcomes:
                duplicates += 1
                continue
            prior = [
                float(item["pageviews_24h"])
                for item in self._strategy_outcomes.values()
                if item.get("topic") == topic and isinstance(item.get("pageviews_24h"), (int, float))
            ]
            baseline = float(median(prior)) if prior else None
            if baseline is None or (baseline == 0.0 and pageviews == 0.0):
                reward = 0.5
            elif baseline == 0.0:
                reward = 1.0
            else:
                reward = pageviews / (pageviews + baseline)
            event_id = "content-strategy-outcome:" + outcome_id
            self.session.ingest_life_experience(
                {
                    "event_id": event_id, "tick": int(self.session.tick),
                    "summary": (
                        f"Website strategy {profile} on topic {topic} produced "
                        f"{int(pageviews) if pageviews.is_integer() else pageviews} pageviews in the mature 24h window."
                    ),
                    "evidence_refs": refs, "significance": min(1.0, 0.25 + 0.1 * len(prior)),
                    "source": "website-audience-outcome", "confidence": 1.0,
                    "outcome": reward, "domain": "content-audience",
                    "identity_relevance": 0.25, "emotional_intensity": 0.0,
                },
                authority_id=AUTHORITY_ID,
            )
            stored = {
                "outcome_id": outcome_id, "topic": topic, "profile_name": profile,
                "pageviews_24h": pageviews, "baseline_pageviews_24h": baseline,
                "relative_reward": reward, "observed_at": str(row.get("observed_at") or ""),
                "publication_id": str(row.get("publication_id") or ""),
                "evidence_refs": refs,
            }
            cycle = str(row.get("cycle") or "")
            stored["cycle"] = cycle
            strategy_ref = next((ref for ref in refs if str(ref).startswith("strategy:")), "")
            memory_use = self.memory_prior.observatory.decision_use(strategy_ref, cycle=cycle)
            if memory_use is not None:
                helped = None if reward == 0.5 else bool(reward > 0.5)
                settlement = self.memory_prior.observatory.settle(
                    str(memory_use["memory_use_id"]),
                    outcome_ref=outcome_id,
                    outcome_success=bool(reward >= 0.5),
                    memory_helped=helped,
                    evidence_ref=refs[0],
                    detail=(
                        f"mature audience relative_reward={reward:.6f}; observational association only"
                    ),
                )
                stored["memory_use_settlement_id"] = str(settlement["memory_use_id"])
            self._strategy_outcomes[outcome_id] = stored
            applied.append(deepcopy(stored))
        if len(self._strategy_outcomes) > MAX_STRATEGY_OUTCOMES:
            ordered = sorted(
                self._strategy_outcomes.items(),
                key=lambda item: (str(item[1].get("observed_at") or ""), item[0]),
            )
            for key, _ in ordered[: len(self._strategy_outcomes) - MAX_STRATEGY_OUTCOMES]:
                self._strategy_outcomes.pop(key, None)
        if applied:
            self._persist()
        return {
            "status": "OBSERVED" if applied else "NO_NEW_OUTCOMES",
            "applied": applied, "duplicates": duplicates,
            "total_strategy_outcomes": len(self._strategy_outcomes),
            "causal_claim": False,
        }

    def strategy_context(self, *, topic: str) -> dict[str, Any]:
        """Expose learned audience-response beliefs for the next prospective decision."""
        topic = str(topic).strip()
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in self._strategy_outcomes.values():
            if row.get("topic") == topic:
                grouped.setdefault(str(row.get("profile_name") or ""), []).append(row)
        counts: dict[str, int] = {}
        mean_rewards: dict[str, float | None] = {}
        mean_pageviews: dict[str, float | None] = {}
        refs: list[str] = []
        for profile, values in grouped.items():
            if not profile:
                continue
            rewards = [float(row["relative_reward"]) for row in values]
            views = [float(row["pageviews_24h"]) for row in values]
            counts[profile] = len(values)
            mean_rewards[profile] = sum(rewards) / len(rewards) if rewards else None
            mean_pageviews[profile] = sum(views) / len(views) if views else None
            for row in values:
                refs.extend(row.get("evidence_refs") or ())
        predictive = self.session.predictive_learning.calibration_context(domain="content")
        prediction_calibration_by_profile: dict[str, dict[str, float | int]] = {}
        for row in predictive.get("calibration", []):
            if row.get("target") != "website:http_pageviews:24h":
                continue
            profile = str(row.get("action_key") or "")
            if not profile:
                continue
            prediction_calibration_by_profile[profile] = {
                "mature_samples": int(row.get("prospective_count", row.get("count", 0))),
                "prospective_samples": int(row.get("prospective_count", row.get("count", 0))),
                "historical_prior_samples": int(row.get("historical_prior_count", 0)),
                "effective_learning_samples": float(row.get("effective_learning_samples", row.get("count", 0))),
                "mean_error": float(row["mean_error"]),
                "mean_signed_error": float(row["mean_signed_error"]),
                "mean_reliability": float(row["mean_reliability"]),
            }
        memory_prior = self.memory_prior.strategy_prior(topic=topic)
        return {
            "topic": topic, "outcome_count": sum(counts.values()),
            "samples_by_profile": counts,
            "mean_relative_reward_by_profile": mean_rewards,
            "mean_pageviews_24h_by_profile": mean_pageviews,
            "prediction_calibration_by_profile": prediction_calibration_by_profile,
            "memory_prior": memory_prior,
            "evidence_refs": list(dict.fromkeys([
                *(str(ref) for ref in refs if str(ref).strip()),
                *(str(ref) for ref in memory_prior.get("evidence_refs") or [] if str(ref).strip()),
            ])),
            "minimum_profile_samples": MIN_STRATEGY_PROFILE_SAMPLES,
            "causal_claim": False,
        }

    def observe_process_experience(
        self,
        *,
        cycle: str,
        topic: str,
        passed: bool,
        model_calls: int,
        evidence_refs: Iterable[str],
    ) -> dict[str, Any]:
        """Learn topic preference only from bounded process outcome, never audience claims."""
        event_id = f"content-process:{cycle}"
        if event_id in self._observed_event_ids:
            interest = self.session.interests.get(topic)
            return {"status": "ALREADY_OBSERVED", "interest": interest.__dict__ if interest else None}
        topic = str(topic).strip()
        if not topic:
            raise ValueError("Content Mind process experience requires a topic")
        refs = list(dict.fromkeys(str(ref).strip() for ref in evidence_refs if str(ref).strip()))
        if not refs:
            raise ValueError("Content Mind process experience requires evidence_refs")
        before = self.session.interests.get(topic)
        novelty = 1.0 if before is None else max(0.0, 1.0 - float(before.familiarity))
        effort = min(1.0, max(0.05, int(model_calls) / 8.0))
        outcome = 1.0 if passed else 0.0
        self.session.ingest_life_experience(
            {
                "event_id": event_id,
                "tick": int(self.session.tick),
                "summary": (
                    f"Content process cycle {cycle} explored topic {topic}; "
                    f"automated process checks {'passed' if passed else 'did not pass'}."
                ),
                "evidence_refs": refs,
                "significance": 0.0,
                "source": "content-process",
                "confidence": 1.0,
                "outcome": outcome,
                "domain": "content-process",
            },
            authority_id=AUTHORITY_ID,
            interest_feedback={
                "name": topic,
                "novelty": novelty,
                "effort": effort,
                "learning_rate": 0.15,
            },
        )
        self._observed_event_ids.append(event_id)
        self._observed_event_ids = self._observed_event_ids[-MAX_EVENT_IDS:]
        self._persist()
        interest = self.session.interests.get(topic)
        return {
            "status": "OBSERVED",
            "outcome_kind": "PROCESS_ONLY",
            "audience_outcome_used": False,
            "model_calls": int(model_calls),
            "effort": effort,
            "interest": interest.__dict__ if interest else None,
        }

    def public_projection(self) -> dict[str, Any]:
        """Return bounded inspectable state for later public presentation."""
        snapshot = self.session.snapshot()
        interests = []
        for name, state in snapshot["interests"]["items"].items():
            interests.append({"name": name, **{k: float(v) for k, v in state.items()}})
        interests.sort(key=lambda row: (-row["salience"], -row["preference"], row["name"]))
        inquiries = [
            {"inquiry_id": key, "question": value.get("question"), "origin": value.get("origin"),
             "status": value.get("status")}
            for key, value in snapshot["inquiry"]["inquiries"].items()
            if value.get("status") == "OPEN"
        ]
        worldview = snapshot["worldview"]
        revisions_by_claim: dict[str, int] = {}
        for row in worldview["history"]:
            proposition = str(row["proposition"])
            revisions_by_claim[proposition] = revisions_by_claim.get(proposition, 0) + 1
        beliefs = [
            {"proposition": proposition, "stance": float(state["stance"]),
             "confidence": float(state["confidence"]),
             "revision_count": revisions_by_claim.get(proposition, 0)}
            for proposition, state in worldview["claims"].items()
        ]
        beliefs.sort(key=lambda row: (-row["confidence"], -abs(row["stance"]), row["proposition"]))
        recent_belief_changes = [
            {"proposition": row["proposition"], "before": row["before"], "after": row["after"],
             "evidence_refs": list(row["payload"]["evidence_refs"])}
            for row in worldview["history"][-8:]
        ]
        latest_plan = self._plans[sorted(self._plans)[-1]] if self._plans else None
        resource_state = snapshot.get("resource_awareness", {})
        resource_states = resource_state.get("states", []) if isinstance(resource_state, dict) else []
        current_resource = None
        if resource_states:
            raw_resource = resource_states[-1]
            balance = float(raw_resource.get("available_funds") or 0.0)
            budget = float(raw_resource.get("period_budget") or 0.0)
            runway = raw_resource.get("runway_days")
            ratio = (balance / budget) if budget > 0 else None
            if balance <= 0 or (ratio is not None and ratio < 0.10) or (runway is not None and float(runway) < 3):
                pressure = "CRITICAL"
            elif (ratio is not None and ratio < 0.25) or (runway is not None and float(runway) < 7):
                pressure = "CONSERVE"
            elif (ratio is not None and ratio < 0.50) or (runway is not None and float(runway) < 14):
                pressure = "WATCH"
            else:
                pressure = "NORMAL"
            current_resource = {
                "observed_at": raw_resource.get("observed_at"),
                "currency": raw_resource.get("currency"),
                "pressure": pressure,
                "provider_available": bool((raw_resource.get("quotas") or {}).get("deepseek_api_available", 0.0)),
            }
        return {
            "mind_schema": MIND_SCHEMA,
            "mind_tick": int(self.session.tick),
            "interests": interests[:8],
            "open_inquiries": inquiries[:8],
            "beliefs": beliefs[:8],
            "recent_belief_changes": recent_belief_changes,
            "latest_plan": deepcopy(latest_plan),
            "resource_awareness": {
                "current": current_resource,
                "observation_count": len(resource_states),
                "decision_count": len(resource_state.get("episodes", [])) if isinstance(resource_state, dict) else 0,
                "outcome_count": len(resource_state.get("outcomes", [])) if isinstance(resource_state, dict) else 0,
                "reflection_count": len(resource_state.get("reflections", [])) if isinstance(resource_state, dict) else 0,
            },
            "strategy_learning": {
                "outcome_count": len(self._strategy_outcomes),
                "topics": sorted({
                    str(row.get("topic")) for row in self._strategy_outcomes.values() if row.get("topic")
                }),
            },
            "memory_system": {
                "life_sync": deepcopy(self._life_memory_last_sync),
                "authority_boundary": "VirtualBrain owns worldview/prediction state; Memory stores evidence-backed lived experience",
            },
            "learning_boundary": (
                "world attention, world prediction error, prospective content-action prediction error, process experience, "
                "and mature Website audience outcomes are connected; audience outcomes remain descriptive and non-causal"
            ),
        }


__all__ = ["ContentMind", "MIND_SCHEMA", "MIN_PERSISTENT_WORLD_INTEREST_IMPORTANCE"]
