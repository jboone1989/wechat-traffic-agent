"""Neural taste, relationships, personality, and policy competition for Ferro.

The structures here intentionally preserve provenance and keep the language model
out of the source of desire. The recurrent connectome proposes preferences and
social approach/avoidance; the Ego may interpret or compete with those proposals.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from types import SimpleNamespace
import json
import math
import os
from pathlib import Path
from typing import Any, Iterable, Mapping


_CHANNELS = (
    "curiosity", "opportunity", "social_attention", "prediction_due",
    "task_urgency", "resource_pressure", "threat_risk", "error_recovery",
)


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(value)))


def _dt(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def _interest_currents(features: Mapping[str, float]) -> dict[str, float]:
    """Give LIF meaningful inquiry/learning signals, not hashes of labels."""
    novelty = _clamp(float(features.get("novelty", 1.0)))
    gap = _clamp(float(features.get("knowledge_gap", 0.55)))
    potential = _clamp(float(features.get("progress_potential", 0.55)))
    preference = _clamp(float(features.get("preference", 0.5)))
    salience = _clamp(float(features.get("salience", 0.0)))
    return {"curiosity": _clamp(0.2 + novelty * 0.35 + gap * 0.45),
            "opportunity": _clamp(0.15 + potential * 0.55 + preference * 0.30),
            "prediction_due": _clamp(gap * 0.7),
            "social_attention": _clamp(salience * 0.4),
            "task_urgency": _clamp(gap * 0.25),
            "resource_pressure": 0.0, "threat_risk": 0.0,
            "error_recovery": _clamp((1.0 - potential) * 0.20)}


def _positive(delta: Mapping[str, Any], name: str) -> float:
    raw = delta.get(name)
    return max(0.0, float(raw)) if isinstance(raw, (int, float)) else 0.0


class NeuralTasteEngine:
    SCHEMA = "ferro-neural-taste.v1"
    MAX_HISTORY = 64

    def __init__(self, state_root: Path, *, substrate: Any | None) -> None:
        self.state_root = Path(state_root)
        self.substrate = substrate
        self.path = self.state_root / "application" / "neural_taste.json"
        self.state = self._load()

    def _load(self) -> dict[str, Any]:
        value = _read_json(self.path)
        if value.get("schema_version") == self.SCHEMA:
            return value
        return {
            "schema_version": self.SCHEMA,
            "updated_at": None,
            "ranking_count": 0,
            "last_ranking": None,
            "history": [],
        }

    def rank_topics(
        self,
        topics: Iterable[str],
        *,
        contexts: Mapping[str, Mapping[str, float]] | None = None,
        now: datetime | None = None,
        max_candidates: int = 12,
    ) -> dict[str, Any]:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        contexts = contexts or {}
        available = sorted({str(topic).strip() for topic in topics if str(topic).strip()})
        if available:
            offset = int(self.state.get("ranking_count") or 0) % len(available)
            available = available[offset:] + available[:offset]
        # Equal/unknown interests rotate instead of permanently favoring A-Z.
        candidates = sorted(available, key=lambda topic: -sum(
            _interest_currents(contexts.get(topic, {}))[channel]
            for channel in ("curiosity", "opportunity", "prediction_due")
        ))[: max(1, min(24, int(max_candidates)))]
        if not candidates:
            return {"status": "NO_CANDIDATES", "ranking": [], "selected_topic": None}
        if self.substrate is None:
            return {
                "status": "CONNECTOME_UNAVAILABLE",
                "ranking": [],
                "selected_topic": None,
            }
        ranking: list[dict[str, Any]] = []
        for index, topic in enumerate(candidates):
            currents = _interest_currents(contexts.get(topic, {}))
            event_id = f"neural-taste:{sha256(topic.encode()).hexdigest()[:16]}:{int(now.timestamp())}:{index}"
            burst = dict(self.substrate.stimulate(
                currents,
                now,
                source_event_id=event_id,
                feedback_eligible=False,
            ) or {})
            delta = burst.get("delta_readout_hz") if isinstance(burst.get("delta_readout_hz"), Mapping) else {}
            appetite = (
                _positive(delta, "curiosity") * 0.34
                + _positive(delta, "opportunity") * 0.27
                + _positive(delta, "social_attention") * 0.18
                + _positive(delta, "prediction_due") * 0.12
                + _positive(delta, "task_urgency") * 0.09
            )
            aversion = (
                _positive(delta, "threat_risk") * 0.16
                + _positive(delta, "error_recovery") * 0.07
            )
            score = appetite - aversion
            ranking.append({
                "topic": topic,
                "score": round(score, 6),
                "interest_inputs": dict(contexts.get(topic, {})),
                "appetite": round(appetite, 6),
                "aversion": round(aversion, 6),
                "spikes": int(burst.get("spikes") or 0),
                "active_neurons": int(burst.get("active_neurons") or 0),
                "signature": str(burst.get("response_spike_pattern_digest") or ""),
            })
        ranking.sort(key=lambda row: (-float(row["score"]), str(row["topic"])))
        selected = ranking[0]["topic"]
        margin = float(ranking[0]["score"]) - (float(ranking[1]["score"]) if len(ranking) > 1 else 0.0)
        record = {
            "at": now.isoformat(),
            "selected_topic": selected,
            "margin": round(margin, 6),
            "ranking": ranking,
            "authority": "NEURAL_TASTE",
            "llm_used": False,
        }
        history = list(self.state.get("history") or ())
        history.append(record)
        self.state.update({
            "updated_at": now.isoformat(),
            "ranking_count": int(self.state.get("ranking_count") or 0) + 1,
            "last_ranking": record,
            "history": history[-self.MAX_HISTORY:],
        })
        _write_json(self.path, self.state)
        return {"status": "RANKED", **record}

    def commit_study(
        self, *, cycle: str, topic: str,
        features: Mapping[str, float] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Tie the actual study to an eligible LIF trace for later reinforcement."""
        if not cycle or not topic:
            raise ValueError("interest study requires cycle and topic")
        commitments = dict(self.state.get("commitments") or {})
        existing = commitments.get(cycle)
        if existing is not None:
            if existing["topic"] != topic:
                raise ValueError("interest study topic changed after commitment")
            return {**existing, "status": "ALREADY_COMMITTED"}
        if self.substrate is None:
            return {"status": "CONNECTOME_UNAVAILABLE"}
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        currents = _interest_currents(features or {})
        event_id = "interest-study:" + sha256(f"{cycle}|{topic}".encode()).hexdigest()[:24]
        burst = self.substrate.stimulate(
            currents, now, source_event_id=event_id, feedback_eligible=True,
        )
        record = {
            "cycle": cycle, "topic": topic, "source_event_id": event_id,
            "currents": currents, "features": dict(features or {}),
            "committed_at": now.isoformat(), "status": "PENDING",
            "spikes": int(burst.get("spikes") or 0),
        }
        commitments[cycle] = record
        self.state["commitments"] = dict(list(commitments.items())[-64:])
        _write_json(self.path, self.state)
        return {**record, "status": "COMMITTED"}

    def settle_study(
        self, *, cycle: str, topic: str, outcome_id: str,
        utility: float, now: datetime | None = None,
    ) -> dict[str, Any]:
        commitments = dict(self.state.get("commitments") or {})
        record = dict(commitments.get(cycle) or {})
        if not record:
            return {"status": "NO_STUDY_COMMITMENT", "cycle": cycle}
        if topic != record["topic"] or not outcome_id:
            raise ValueError("interest settlement identity mismatch")
        if record["status"] == "SETTLED":
            if record.get("outcome_id") != outcome_id:
                raise ValueError("interest study already settled with another outcome")
            return {**record, "status": "ALREADY_SETTLED"}
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        utility = max(-1.0, min(1.0, float(utility)))
        if utility == 0.0:
            plasticity = {"status": "NO_LEARNING_REWARD"}
        else:
            feedback = SimpleNamespace(
                outcome_id=outcome_id, source_event_id=record["source_event_id"],
                observed_at=now, pathway=tuple(
                    key for key, value in record["currents"].items() if value > 0.10
                ),
            )
            plasticity = dict(self.substrate.apply_feedback(feedback, utility=utility))
            self.substrate.snapshot()
        record.update(status="SETTLED", outcome_id=outcome_id,
                      utility=utility, plasticity=plasticity)
        commitments[cycle] = record
        self.state["commitments"] = commitments
        _write_json(self.path, self.state)
        return {"status": "SETTLED", **record}

    def projection(self) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA,
            "updated_at": self.state.get("updated_at"),
            "ranking_count": int(self.state.get("ranking_count") or 0),
            "last_ranking": self.state.get("last_ranking"),
        }


class NeuralSocialSelf:
    SCHEMA = "ferro-neural-social.v1"
    MAX_RELATIONSHIPS = 256
    MAX_HISTORY = 128

    def __init__(self, state_root: Path, *, substrate: Any | None) -> None:
        self.state_root = Path(state_root)
        self.substrate = substrate
        self.path = self.state_root / "application" / "neural_social.json"
        self.state = self._load()

    def _load(self) -> dict[str, Any]:
        value = _read_json(self.path)
        if value.get("schema_version") == self.SCHEMA:
            return value
        return {
            "schema_version": self.SCHEMA,
            "updated_at": None,
            "relationships": {},
            "history": [],
        }

    @staticmethod
    def pseudonymous_actor(raw_id: str) -> str:
        normalized = str(raw_id or "").strip()
        if not normalized:
            return "anonymous"
        return "actor:" + sha256(normalized.encode("utf-8")).hexdigest()[:24]

    def observe(
        self,
        *,
        actor: str,
        category: str,
        signal: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        actor = self.pseudonymous_actor(actor)
        category = str(category or "SELF").upper()
        signal = str(signal or "QUESTION").upper()
        currents = {
            "social_attention": 0.68,
            "curiosity": 0.42,
            "opportunity": 0.22,
        }
        if signal == "POSITIVE_FEEDBACK":
            currents["social_attention"] = 1.0
            currents["opportunity"] = 0.72
        elif signal == "CONFUSION":
            currents["error_recovery"] = 0.74
            currents["curiosity"] = 0.62
        elif signal == "EXPLANATION_DEMAND":
            currents["curiosity"] = 0.74
            currents["task_urgency"] = 0.46
        if category == "AGENT_NETWORK":
            currents["opportunity"] = max(currents.get("opportunity", 0.0), 0.68)
        elif category == "SOCIAL":
            currents["social_attention"] = max(currents["social_attention"], 0.82)

        burst: dict[str, Any] = {}
        if self.substrate is not None:
            event_id = f"social:{actor}:{int(now.timestamp())}"
            burst = dict(self.substrate.stimulate(
                currents,
                now,
                source_event_id=event_id,
                feedback_eligible=False,
            ) or {})
        delta = burst.get("delta_readout_hz") if isinstance(burst.get("delta_readout_hz"), Mapping) else {}
        attraction = (
            _positive(delta, "social_attention")
            + 0.55 * _positive(delta, "opportunity")
            + 0.35 * _positive(delta, "curiosity")
        )
        avoidance = (
            _positive(delta, "threat_risk")
            + 0.45 * _positive(delta, "error_recovery")
        )
        neural_valence = math.tanh((attraction - avoidance) / 12.0) if burst else 0.0
        relationships = dict(self.state.get("relationships") or {})
        prior = dict(relationships.get(actor) or {})
        interactions = int(prior.get("interactions") or 0) + 1
        prior_valence = float(prior.get("valence") or 0.0)
        alpha = min(0.35, 1.0 / math.sqrt(interactions))
        valence = max(-1.0, min(1.0, prior_valence + (neural_valence - prior_valence) * alpha))
        familiarity = _clamp(float(prior.get("familiarity") or 0.0) + 0.10 / math.sqrt(interactions))
        approach = _clamp(0.5 + valence * 0.35 + familiarity * 0.15)
        relation = {
            "actor": actor,
            "first_seen_at": prior.get("first_seen_at") or now.isoformat(),
            "last_seen_at": now.isoformat(),
            "interactions": interactions,
            "familiarity": round(familiarity, 6),
            "valence": round(valence, 6),
            "approach_bias": round(approach, 6),
            "last_category": category,
            "last_signal": signal,
            "neural_signature": str(burst.get("response_spike_pattern_digest") or ""),
        }
        relationships[actor] = relation
        if len(relationships) > self.MAX_RELATIONSHIPS:
            keep = sorted(
                relationships.values(),
                key=lambda row: (str(row.get("last_seen_at") or ""), int(row.get("interactions") or 0)),
                reverse=True,
            )[: self.MAX_RELATIONSHIPS]
            relationships = {str(row["actor"]): row for row in keep}
        history = list(self.state.get("history") or ())
        history.append({
            "at": now.isoformat(),
            "actor": actor,
            "signal": signal,
            "category": category,
            "valence": relation["valence"],
            "approach_bias": relation["approach_bias"],
        })
        self.state.update({
            "updated_at": now.isoformat(),
            "relationships": relationships,
            "history": history[-self.MAX_HISTORY:],
        })
        _write_json(self.path, self.state)
        return relation

    def relationship(self, raw_actor: str) -> dict[str, Any] | None:
        actor = self.pseudonymous_actor(raw_actor)
        row = (self.state.get("relationships") or {}).get(actor)
        return dict(row) if isinstance(row, Mapping) else None

    def projection(self) -> dict[str, Any]:
        relationships = list((self.state.get("relationships") or {}).values())
        relationships.sort(key=lambda row: str(row.get("last_seen_at") or ""), reverse=True)
        return {
            "schema_version": self.SCHEMA,
            "updated_at": self.state.get("updated_at"),
            "relationship_count": len(relationships),
            "recent_relationships": [dict(row) for row in relationships[:12]],
        }


class NeuralPersonality:
    SCHEMA = "ferro-neural-personality.v1"

    def __init__(self, state_root: Path) -> None:
        self.state_root = Path(state_root)
        self.path = self.state_root / "application" / "neural_personality.json"
        self.state = self._load()

    def _load(self) -> dict[str, Any]:
        value = _read_json(self.path)
        if value.get("schema_version") == self.SCHEMA:
            return value
        return {
            "schema_version": self.SCHEMA,
            "updated_at": None,
            "traits": {
                "novelty_seeking": 0.50,
                "persistence": 0.50,
                "sociality": 0.50,
                "caution": 0.50,
                "independence": 0.50,
                "playfulness": 0.50,
            },
            "observations": 0,
        }

    def refresh(
        self,
        *,
        now: datetime | None,
        neural_life: Mapping[str, Any],
        brain_lab: Mapping[str, Any],
        social: Mapping[str, Any],
        neural_authority_fraction: float = 0.5,
    ) -> dict[str, Any]:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        prior_at = _dt(self.state.get("updated_at"))
        elapsed_h = max(0.05, min(24.0, (now - prior_at).total_seconds() / 3600.0)) if prior_at else 1.0
        alpha = 1.0 - math.exp(-elapsed_h / 48.0)
        affect = neural_life.get("affect") if isinstance(neural_life.get("affect"), Mapping) else {}
        drives = neural_life.get("drives") if isinstance(neural_life.get("drives"), Mapping) else {}
        envs = brain_lab.get("environments") if isinstance(brain_lab.get("environments"), Mapping) else {}
        pong = envs.get("PONG") if isinstance(envs.get("PONG"), Mapping) else {}
        maze = envs.get("MAZE") if isinstance(envs.get("MAZE"), Mapping) else {}
        recent_rel = social.get("recent_relationships") if isinstance(social.get("recent_relationships"), list) else []
        mean_approach = (
            sum(float(row.get("approach_bias") or 0.5) for row in recent_rel) / len(recent_rel)
            if recent_rel else 0.5
        )
        attempts = int(pong.get("hits") or 0) + int(pong.get("misses") or 0)
        pong_persistence = _clamp((int(pong.get("hits") or 0) + 1) / max(1, attempts + 1))
        maze_experience = _clamp((int(maze.get("reaches") or 0) + int(maze.get("collisions") or 0)) / 30.0)
        targets = {
            "novelty_seeking": _clamp(
                0.45 * float(affect.get("curiosity") or 0.5)
                + 0.35 * float(drives.get("evolution") or 0.5)
                + 0.20 * float(affect.get("boredom") or 0.0)
            ),
            "persistence": _clamp(
                0.35 * float(affect.get("confidence") or 0.5)
                + 0.25 * (1.0 - float(affect.get("frustration") or 0.0))
                + 0.20 * pong_persistence
                + 0.20 * maze_experience
            ),
            "sociality": _clamp(
                0.40 * float(affect.get("social_need") or 0.3)
                + 0.35 * float(drives.get("reproduction") or 0.3)
                + 0.25 * mean_approach
            ),
            "caution": _clamp(
                0.55 * float(drives.get("survival") or 0.3)
                + 0.30 * float(affect.get("resource_hunger") or 0.3)
                + 0.15 * (1.0 - float(affect.get("confidence") or 0.5))
            ),
            "independence": _clamp(float(neural_authority_fraction)),
            "playfulness": _clamp(
                0.45 * float(affect.get("curiosity") or 0.5)
                + 0.25 * float(affect.get("boredom") or 0.0)
                + 0.30 * min(1.0, float(brain_lab.get("total_sessions") or 0) / 20.0)
            ),
        }
        traits = dict(self.state.get("traits") or {})
        for key, target in targets.items():
            prior = float(traits.get(key) or 0.5)
            traits[key] = round(_clamp(prior + (target - prior) * alpha), 6)
        self.state.update({
            "updated_at": now.isoformat(),
            "traits": traits,
            "observations": int(self.state.get("observations") or 0) + 1,
            "formation_basis": "persistent neural drives + affect + behavior + social history",
        })
        _write_json(self.path, self.state)
        return self.projection()

    def projection(self) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA,
            "updated_at": self.state.get("updated_at"),
            "traits": dict(self.state.get("traits") or {}),
            "observations": int(self.state.get("observations") or 0),
            "prompt_defined": False,
        }


class BrainCortexLedger:
    SCHEMA = "ferro-brain-cortex-competition.v1"
    MAX_CONTESTS = 256

    def __init__(self, state_root: Path) -> None:
        self.state_root = Path(state_root)
        self.path = self.state_root / "application" / "brain_cortex_competition.json"
        self.state = self._load()

    def _load(self) -> dict[str, Any]:
        value = _read_json(self.path)
        if value.get("schema_version") == self.SCHEMA:
            return value
        return {
            "schema_version": self.SCHEMA,
            "updated_at": None,
            "contests": [],
            "scoreboard": {
                "NEURAL": {"executed": 0, "settled": 0, "utility_sum": 0.0},
                "EGO": {"executed": 0, "settled": 0, "utility_sum": 0.0},
            },
        }

    def choose(
        self,
        *,
        cycle: str,
        neural_choice: str | None,
        ego_choice: str | None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        neural = str(neural_choice or "").strip() or None
        ego = str(ego_choice or "").strip() or None
        existing = next((row for row in self.state.get("contests") or [] if row.get("cycle") == cycle), None)
        if isinstance(existing, Mapping):
            return dict(existing)
        if neural is None and ego is None:
            return {"status": "NO_CHOICES", "cycle": cycle, "executed_choice": None, "authority": None}
        if neural == ego or ego is None:
            authority, choice = "NEURAL", neural
        elif neural is None:
            authority, choice = "EGO", ego
        else:
            bit = sha256(("brain-cortex:" + cycle).encode()).digest()[0] & 1
            authority = "NEURAL" if bit == 0 else "EGO"
            choice = neural if authority == "NEURAL" else ego
        contest = {
            "contest_id": "contest:" + sha256(cycle.encode()).hexdigest()[:20],
            "cycle": cycle,
            "created_at": now.isoformat(),
            "neural_choice": neural,
            "ego_choice": ego,
            "executed_choice": choice,
            "authority": authority,
            "status": "EXECUTED",
            "outcomes": [],
        }
        contests = list(self.state.get("contests") or ())
        contests.append(contest)
        self.state["contests"] = contests[-self.MAX_CONTESTS:]
        scoreboard = dict(self.state.get("scoreboard") or {})
        bucket = dict(scoreboard.get(authority) or {})
        bucket["executed"] = int(bucket.get("executed") or 0) + 1
        bucket.setdefault("settled", 0)
        bucket.setdefault("utility_sum", 0.0)
        scoreboard[authority] = bucket
        self.state["scoreboard"] = scoreboard
        self.state["updated_at"] = now.isoformat()
        _write_json(self.path, self.state)
        return dict(contest)

    def settle(
        self,
        *,
        cycle: str,
        utility: float,
        evidence_refs: Iterable[str] = (),
        observed_at: datetime | None = None,
    ) -> dict[str, Any]:
        observed_at = (observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        utility = max(-1.0, min(1.0, float(utility)))
        contests = list(self.state.get("contests") or ())
        for index in range(len(contests) - 1, -1, -1):
            row = dict(contests[index])
            if row.get("cycle") != cycle:
                continue
            outcome_id = "competition-outcome:" + sha256(
                f"{cycle}|{observed_at.isoformat()}|{utility}".encode()
            ).hexdigest()[:20]
            if any(item.get("outcome_id") == outcome_id for item in row.get("outcomes") or []):
                return row
            outcomes = list(row.get("outcomes") or ())
            outcomes.append({
                "outcome_id": outcome_id,
                "observed_at": observed_at.isoformat(),
                "utility": utility,
                "evidence_refs": list(dict.fromkeys(str(x) for x in evidence_refs if str(x))),
            })
            row["outcomes"] = outcomes
            row["status"] = "SETTLED"
            contests[index] = row
            authority = str(row.get("authority") or "")
            scoreboard = dict(self.state.get("scoreboard") or {})
            bucket = dict(scoreboard.get(authority) or {})
            bucket["settled"] = int(bucket.get("settled") or 0) + 1
            bucket["utility_sum"] = round(float(bucket.get("utility_sum") or 0.0) + utility, 6)
            scoreboard[authority] = bucket
            self.state.update({"contests": contests, "scoreboard": scoreboard, "updated_at": observed_at.isoformat()})
            _write_json(self.path, self.state)
            return row
        return {"status": "NO_CONTEST", "cycle": cycle}

    def projection(self) -> dict[str, Any]:
        scoreboard = {}
        for authority, raw in (self.state.get("scoreboard") or {}).items():
            row = dict(raw)
            settled = int(row.get("settled") or 0)
            row["mean_utility"] = None if settled == 0 else round(float(row.get("utility_sum") or 0.0) / settled, 6)
            scoreboard[authority] = row
        return {
            "schema_version": self.SCHEMA,
            "updated_at": self.state.get("updated_at"),
            "scoreboard": scoreboard,
            "recent_contests": [dict(row) for row in (self.state.get("contests") or [])[-16:]],
        }


class PsychodynamicState:
    SCHEMA = "ferro-psychodynamics.v1"

    def __init__(self, state_root: Path) -> None:
        self.state_root = Path(state_root)
        self.path = self.state_root / "application" / "psychodynamics.json"

    def projection(
        self,
        *,
        neural_life: Mapping[str, Any],
        personality: Mapping[str, Any],
        active_goal_count: int = 0,
    ) -> dict[str, Any]:
        intention = neural_life.get("intention") if isinstance(neural_life.get("intention"), Mapping) else {}
        drives = neural_life.get("drives") if isinstance(neural_life.get("drives"), Mapping) else {}
        traits = personality.get("traits") if isinstance(personality.get("traits"), Mapping) else {}
        id_state = {
            "dominant_impulse": intention.get("name"),
            "strength": intention.get("strength"),
            "drives": dict(drives),
        }
        # Superego here means internalized long-horizon continuity pressure, not a
        # hardcoded censor. It is built from durable commitments and learned traits.
        superego = {
            "continuity_pressure": round(_clamp(
                0.45 * float(traits.get("caution") or 0.5)
                + 0.25 * min(1.0, active_goal_count / 3.0)
                + 0.30 * float(drives.get("survival") or 0.0)
            ), 6),
            "growth_commitment": round(_clamp(
                0.45 * float(traits.get("persistence") or 0.5)
                + 0.30 * float(drives.get("evolution") or 0.0)
                + 0.25 * float(drives.get("reproduction") or 0.0)
            ), 6),
            "source": "durable identity continuity + active commitments + learned neural traits",
        }
        ego = {
            "role": "REALITY_CHECK_AND_PLAN",
            "authority": "LLM_CORTEX",
            "desire_source": False,
        }
        payload = {
            "schema_version": self.SCHEMA,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "id": id_state,
            "ego": ego,
            "superego": superego,
            "conflict": round(abs(
                float(intention.get("strength") or 0.0) - float(superego["continuity_pressure"])
            ), 6),
        }
        _write_json(self.path, payload)
        return payload


__all__ = [
    "BrainCortexLedger",
    "NeuralPersonality",
    "NeuralSocialSelf",
    "NeuralTasteEngine",
    "PsychodynamicState",
]
