"""Persistent VirtualBrain interest and endogenous-goal adapter for Content Agent."""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

MIND_SCHEMA = "content-mind:v1"
# ContractV1AgencySession only accepts capability-backed experience authorities from
# AutobiographicalSelf.TRUSTED_EXPERIENCE_SOURCES. "session" is the canonical
# host/session ingestion authority used by VirtualBrain's own experience→goal eval.
AUTHORITY_ID = "session"
MAX_PLANS = 128
MAX_EVENT_IDS = 512
MIN_PERSISTENT_WORLD_INTEREST_IMPORTANCE = 0.7


class ContentMind:
    """Keep one VirtualBrain agency session as the Content Agent's longitudinal mind."""

    def __init__(self, *, root: Path, subject_id: str) -> None:
        from virtualbrain.adapter.service import ContractV1AgencySession
        from virtualbrain.research.provenance import TrustedIngestionAuthority

        self.root = Path(root) / "application" / "content_mind"
        self.subject_id = subject_id
        self.root.mkdir(parents=True, exist_ok=True)
        self.authority = TrustedIngestionAuthority(AUTHORITY_ID, self._authority_key())
        self.session = ContractV1AgencySession(subject_id, trusted_authorities=[self.authority])
        self._plans: dict[str, dict[str, Any]] = {}
        self._observed_event_ids: list[str] = []
        self._restore()

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
        expected = {"schema_version", "subject_id", "subject", "plans", "observed_event_ids"}
        if not isinstance(row, dict) or set(row) != expected:
            raise ValueError("Content Mind checkpoint fields mismatch")
        if row["schema_version"] != MIND_SCHEMA or row["subject_id"] != self.subject_id:
            raise ValueError("Content Mind checkpoint identity mismatch")
        if not isinstance(row["plans"], dict) or not isinstance(row["observed_event_ids"], list):
            raise ValueError("Content Mind checkpoint collections are invalid")
        self.session.restore(row["subject"])
        self._plans = deepcopy(row["plans"])
        self._observed_event_ids = [str(item) for item in row["observed_event_ids"]]

    def _persist(self) -> None:
        payload = {
            "schema_version": MIND_SCHEMA,
            "subject_id": self.subject_id,
            "subject": self.session.snapshot(),
            "plans": self._plans,
            "observed_event_ids": self._observed_event_ids,
        }
        tmp = self.checkpoint_path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, self.checkpoint_path)

    def _prior_inquiry_topic(self, inquiry_id: str) -> str | None:
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
                updates.append({
                    "topic": topic, "status": "SHORT_TERM_ONLY", "interest": None,
                    "importance": row["importance"],
                })
                continue
            event_id = f"world-attention:{topic}:{evidence_id}"
            if event_id in self._observed_event_ids:
                interest = self.session.interests.get(topic)
                updates.append({
                    "topic": topic, "status": "ALREADY_OBSERVED",
                    "interest": interest.__dict__ if interest else None,
                })
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
            updates.append({
                "topic": topic, "status": "OBSERVED",
                "interest": interest.__dict__ if interest else None,
            })
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
        latest_plan = self._plans[sorted(self._plans)[-1]] if self._plans else None
        return {
            "mind_schema": MIND_SCHEMA,
            "mind_tick": int(self.session.tick),
            "interests": interests[:8],
            "open_inquiries": inquiries[:8],
            "latest_plan": deepcopy(latest_plan),
            "learning_boundary": "world attention and process experience are connected; audience outcomes are not connected yet",
        }


__all__ = ["ContentMind", "MIND_SCHEMA", "MIN_PERSISTENT_WORLD_INTEREST_IMPORTANCE"]
