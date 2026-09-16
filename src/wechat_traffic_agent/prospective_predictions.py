from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .content_service import _extract_json

SCHEMA_VERSION = "content-prospective-predictions.v1"


def _aware(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


class PredictionProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    action: Literal["NONE", "RECORD"]
    statement: str | None = Field(default=None, max_length=600)
    horizon_hours: int | None = Field(default=None, ge=1, le=8784)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    verification_query: str | None = Field(default=None, max_length=240)
    falsifier: str | None = Field(default=None, max_length=600)
    rationale: str = Field(min_length=10, max_length=800)

    @model_validator(mode="after")
    def validate_action(self):
        values = (self.statement, self.horizon_hours, self.confidence, self.verification_query, self.falsifier)
        if self.action == "RECORD":
            if any(value is None for value in values):
                raise ValueError("recorded prediction requires statement, horizon, confidence, query, and falsifier")
            if len(str(self.statement).strip()) < 10 or len(str(self.verification_query).strip()) < 2 or len(str(self.falsifier).strip()) < 10:
                raise ValueError("recorded prediction fields are too short")
        elif any(value is not None for value in values):
            raise ValueError("NONE prediction proposal must not carry prediction fields")
        return self


class PredictionWorldviewRevision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    action: Literal["DEFER", "COMMIT"]
    stance: float = Field(ge=-1.0, le=1.0)
    reliability: float = Field(ge=0.0, le=1.0)
    support: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=10, max_length=800)


class PredictionEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    evidence_id: str = Field(min_length=1, max_length=220)
    evidence_quote: str = Field(min_length=5, max_length=400)
    observed_at: datetime
    source: str = Field(min_length=1, max_length=300)
    title: str = Field(min_length=1, max_length=400)
    source_revision: str = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def validate_time(self):
        object.__setattr__(self, "observed_at", _aware(self.observed_at, "prediction evidence observed_at"))
        return self


class PredictionCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    check_id: str = Field(min_length=1, max_length=120)
    status: Literal["INCONCLUSIVE", "CONFIRMED", "REFUTED"]
    checked_at: datetime
    evidence: tuple[PredictionEvidence, ...] = Field(max_length=8)
    reliability: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=10, max_length=800)
    prediction_error: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_time(self):
        object.__setattr__(self, "checked_at", _aware(self.checked_at, "prediction check checked_at"))
        if self.status == "INCONCLUSIVE" and self.prediction_error is not None:
            raise ValueError("inconclusive prediction check cannot have prediction_error")
        if self.status != "INCONCLUSIVE" and self.prediction_error is None:
            raise ValueError("resolved prediction check requires prediction_error")
        return self


class ProspectivePrediction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[SCHEMA_VERSION] = SCHEMA_VERSION
    prediction_id: str = Field(min_length=1, max_length=120)
    cycle: str = Field(min_length=1, max_length=120)
    topic: str = Field(min_length=1, max_length=120)
    proposition: str = Field(min_length=10, max_length=600)
    proposition_stance: float = Field(ge=-1.0, le=1.0)
    statement: str = Field(min_length=10, max_length=600)
    confidence: float = Field(ge=0.0, le=1.0)
    created_at: datetime
    due_at: datetime
    verification_query: str = Field(min_length=2, max_length=240)
    falsifier: str = Field(min_length=10, max_length=600)
    source_evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=16)
    status: Literal["OPEN", "RESOLVED"] = "OPEN"
    checks: tuple[PredictionCheck, ...] = Field(default=(), max_length=32)

    @model_validator(mode="after")
    def validate_temporal_boundary(self):
        created = _aware(self.created_at, "prediction created_at")
        due = _aware(self.due_at, "prediction due_at")
        object.__setattr__(self, "created_at", created)
        object.__setattr__(self, "due_at", due)
        if due < created + timedelta(hours=1):
            raise ValueError("prediction due_at must be at least one hour after created_at")
        if due > created + timedelta(days=366):
            raise ValueError("prediction horizon exceeds one year")
        if len(self.source_evidence_refs) != len(set(self.source_evidence_refs)):
            raise ValueError("prediction source_evidence_refs must be unique")
        if self.status == "RESOLVED":
            if not self.checks or self.checks[-1].status not in {"CONFIRMED", "REFUTED"}:
                raise ValueError("resolved prediction requires a terminal check")
        elif any(row.status in {"CONFIRMED", "REFUTED"} for row in self.checks):
            raise ValueError("open prediction cannot contain a terminal check")
        return self

    @property
    def outcome(self) -> PredictionCheck | None:
        if self.status != "RESOLVED" or not self.checks:
            return None
        return self.checks[-1]


class PredictionAssessmentEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    evidence_id: str = Field(min_length=1, max_length=220)
    evidence_quote: str = Field(min_length=5, max_length=400)


class PredictionAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    status: Literal["INCONCLUSIVE", "CONFIRMED", "REFUTED"]
    evidence: tuple[PredictionAssessmentEvidence, ...] = Field(max_length=8)
    reliability: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=10, max_length=800)

    @model_validator(mode="after")
    def terminal_requires_evidence(self):
        if self.status in {"CONFIRMED", "REFUTED"} and not self.evidence:
            raise ValueError("terminal prediction assessment requires evidence")
        return self


def _source_time(row: dict[str, Any]) -> datetime | None:
    # A web page discovered after the prediction may still have existed beforehand.
    # For web-search evidence, only an explicit publication timestamp can prove the
    # underlying event/article is prospective. Agent-network artifacts are themselves
    # dated observations, so their observed_at is an admissible event time fallback.
    raw = row.get("published_at")
    if not raw and row.get("source_kind") == "agent-network":
        raw = row.get("observed_at")
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return _aware(value, "prediction source time")


async def assess_prediction(*, runtime: Any, prediction: ProspectivePrediction, items: list[dict[str, Any]], now: datetime) -> tuple[PredictionAssessment, tuple[PredictionEvidence, ...]]:
    checked = _aware(now, "prediction assessment now")
    candidates: list[dict[str, Any]] = []
    registry: dict[str, tuple[dict[str, Any], datetime]] = {}
    for row in items:
        evidence_id = str(row.get("evidence_id") or "").strip()
        event_time = _source_time(row)
        if not evidence_id or event_time is None or event_time <= prediction.created_at or event_time > checked:
            continue
        registry[evidence_id] = (row, event_time)
        candidates.append({
            "evidence_id": evidence_id, "source": str(row.get("source") or ""),
            "title": str(row.get("title") or "")[:300], "summary": str(row.get("summary") or "")[:900],
            "event_time": event_time.isoformat(),
        })
    if not candidates:
        return PredictionAssessment(
            status="INCONCLUSIVE", evidence=(), reliability=0.0,
            rationale="No post-prediction evidence is available at or after the prediction horizon.",
        ), ()
    task = (
        "EVALUATE_DATED_PREDICTION: Evaluate only the supplied dated prediction against post-prediction evidence. "
        "CONFIRMED means the evidence directly satisfies the prediction; REFUTED means it directly satisfies the falsifier or contradicts the prediction; "
        "otherwise return INCONCLUSIVE. Do not reward plausibility, hindsight, or partial narrative fit. Every cited quote must be one contiguous exact substring "
        "of the supplied title or summary. Return JSON only.\n"
        + json.dumps({"now": checked.isoformat(), "prediction": prediction.model_dump(mode="json"), "candidates": candidates}, ensure_ascii=False)
        + "\nSchema: " + json.dumps(PredictionAssessment.model_json_schema())
    )
    raw = await runtime.cognitive_probe(task, max_tokens=1400)
    assessment = PredictionAssessment.model_validate_json(_extract_json(raw))
    seen: set[str] = set()
    evidence: list[PredictionEvidence] = []
    for ref in assessment.evidence:
        if ref.evidence_id in seen or ref.evidence_id not in registry:
            raise ValueError("prediction assessment must reference supplied evidence exactly once")
        row, event_time = registry[ref.evidence_id]
        text = str(row.get("title") or "") + "\n" + str(row.get("summary") or "")
        if ref.evidence_quote not in text:
            raise ValueError("prediction assessment quote must be an exact source substring")
        seen.add(ref.evidence_id)
        evidence.append(PredictionEvidence(
            evidence_id=ref.evidence_id, evidence_quote=ref.evidence_quote, observed_at=event_time,
            source=str(row.get("source") or row.get("url") or "unknown"),
            title=str(row.get("title") or ref.evidence_id)[:400],
            source_revision=str(row.get("source_revision") or ref.evidence_id)[:300],
        ))
    return assessment, tuple(evidence)


async def propose_prediction(
    *, runtime: Any, cycle: str, topic: str, proposition: str, proposition_stance: float,
    worldview_confidence: float, source_evidence: dict[str, Any], now: datetime,
) -> PredictionProposal:
    point = _aware(now, "prediction proposal now")
    task = (
        "PROPOSE_DATED_PREDICTION: Decide whether this durable crypto/finance belief supports one genuinely testable future-world prediction. "
        "Prediction is optional: choose NONE unless a later public observation can clearly confirm or refute it. "
        "If RECORD, write one bounded statement with a horizon of 1..8784 hours, a calibrated confidence, a concise web verification query, "
        "and a concrete falsifier. Do not turn investment advice, vague directionality, or a source headline into a prediction. "
        "The prediction must be narrower than or equal to the durable proposition, never broader. Return JSON only.\n"
        + json.dumps({
            "now": point.isoformat(), "cycle": cycle, "topic": topic, "proposition": proposition,
            "proposition_stance": proposition_stance, "worldview_confidence": worldview_confidence,
            "source_evidence": source_evidence,
        }, ensure_ascii=False)
        + "\nSchema: " + json.dumps(PredictionProposal.model_json_schema())
    )
    raw = await runtime.cognitive_probe(task, max_tokens=1200)
    return PredictionProposal.model_validate_json(_extract_json(raw))


async def assess_worldview_revision(
    *, runtime: Any, prediction: ProspectivePrediction, current_belief: dict[str, Any] | None,
) -> PredictionWorldviewRevision:
    outcome = prediction.outcome
    if outcome is None:
        raise ValueError("worldview revision requires a resolved prediction")
    task = (
        "REVISE_WORLDVIEW_FROM_PREDICTION: Decide whether this matured dated prediction outcome justifies revising the SAME durable proposition. "
        "A prediction can be wrong while the broader proposition remains useful, so DEFER is valid and preferred unless the post-prediction evidence changes the durable claim. "
        "If COMMIT, return the updated stance for the exact proposition, plus evidence reliability and support. "
        "Do not invent a new proposition and do not reason from the outcome label alone; use the supplied post-prediction evidence. Return JSON only.\n"
        + json.dumps({
            "proposition": prediction.proposition, "prior_belief": current_belief,
            "prediction": {"statement": prediction.statement, "confidence": prediction.confidence,
                           "falsifier": prediction.falsifier, "created_at": prediction.created_at.isoformat(),
                           "due_at": prediction.due_at.isoformat()},
            "outcome": outcome.model_dump(mode="json"),
        }, ensure_ascii=False)
        + "\nSchema: " + json.dumps(PredictionWorldviewRevision.model_json_schema())
    )
    raw = await runtime.cognitive_probe(task, max_tokens=1200)
    return PredictionWorldviewRevision.model_validate_json(_extract_json(raw))


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    os.replace(tmp, path)


async def settle_predictions(
    *, ledger: "ProspectivePredictionLedger", runtime: Any, items: list[dict[str, Any]], now: datetime,
    mind: Any | None, cycle: str,
) -> tuple[dict[str, Any], ...]:
    point = _aware(now, "prediction settlement now")
    results: list[dict[str, Any]] = []
    for prediction in ledger.due(point):
        try:
            assessment, evidence = await assess_prediction(runtime=runtime, prediction=prediction, items=items, now=point)
            updated = ledger.check(
                prediction.prediction_id, checked_at=point, status=assessment.status, evidence=evidence,
                reliability=assessment.reliability, rationale=assessment.rationale,
            )
            results.append({
                "prediction_id": prediction.prediction_id, "check_status": assessment.status,
                "resolved": updated.status == "RESOLVED", "prediction_error": (updated.outcome.prediction_error if updated.outcome else None),
            })
        except Exception as exc:
            results.append({"prediction_id": prediction.prediction_id, "check_status": "ERROR", "error_type": type(exc).__name__})

    if mind is None or not hasattr(mind, "observe_prediction_outcome"):
        return tuple(results)
    for prediction in ledger.resolved():
        marker = ledger.root / "applied" / (prediction.prediction_id.split(":")[-1] + ".json")
        if marker.exists():
            continue
        outcome = prediction.outcome
        if outcome is None:
            continue
        refs = tuple(dict.fromkeys([prediction.prediction_id, *[row.evidence_id for row in outcome.evidence]]))
        try:
            life = mind.observe_prediction_outcome(
                prediction_id=prediction.prediction_id, topic=prediction.topic, statement=prediction.statement,
                status=outcome.status, confidence=prediction.confidence, prediction_error=float(outcome.prediction_error or 0.0),
                reliability=outcome.reliability, evidence_refs=refs,
            )
            revision_payload: dict[str, Any] = {"status": "NO_WORLDVIEW_ADAPTER"}
            if hasattr(mind, "record_research_belief") and outcome.evidence:
                revision_path = ledger.root / "worldview_revisions" / (prediction.prediction_id.split(":")[-1] + ".json")
                if revision_path.exists():
                    decision = PredictionWorldviewRevision.model_validate_json(revision_path.read_text(encoding="utf-8"))
                else:
                    current = next((row for row in mind.public_projection().get("beliefs", ())
                                    if row.get("proposition") == prediction.proposition), None)
                    decision = await assess_worldview_revision(runtime=runtime, prediction=prediction, current_belief=current)
                    _atomic_json(revision_path, decision.model_dump(mode="json"))
                if decision.action == "COMMIT":
                    primary = outcome.evidence[0]
                    source = {
                        "evidence_id": primary.evidence_id, "title": primary.title,
                        "source_revision": primary.source_revision, "source": primary.source,
                        "observed_at": primary.observed_at.isoformat(), "evidence_refs": [primary.evidence_id],
                    }
                    revision_payload = mind.record_research_belief(
                        cycle=f"{cycle}:prediction-outcome:{prediction.prediction_id.split(':')[-1]}",
                        topic=prediction.topic,
                        question="What does the matured dated prediction outcome change about this durable proposition?",
                        source_evidence=source, proposition=prediction.proposition, stance=decision.stance,
                        reliability=decision.reliability, support=decision.support, commit=True,
                    )
                else:
                    revision_payload = {"status": "DEFERRED", "rationale": decision.rationale}
            _atomic_json(marker, {
                "prediction_id": prediction.prediction_id, "outcome_status": outcome.status,
                "life_experience": life, "worldview_revision": revision_payload,
            })
            results.append({
                "prediction_id": prediction.prediction_id, "learning_status": "APPLIED",
                "life_experience": life, "worldview_revision": revision_payload,
            })
        except Exception as exc:
            results.append({"prediction_id": prediction.prediction_id, "learning_status": "ERROR", "error_type": type(exc).__name__})
    return tuple(results)


class ProspectivePredictionLedger:
    """Immutable-before-outcome ledger for dated, evidence-backed world predictions."""

    def __init__(self, state_root: Path) -> None:
        self.root = Path(state_root) / "content" / "predictions"
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "ledger.json"
        self._records = self._load()

    def _load(self) -> dict[str, ProspectivePrediction]:
        if not self.path.exists():
            return {}
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or set(raw) != {"schema_version", "predictions"}:
            raise ValueError("prediction ledger fields mismatch")
        if raw["schema_version"] != SCHEMA_VERSION or not isinstance(raw["predictions"], dict):
            raise ValueError("prediction ledger schema mismatch")
        records: dict[str, ProspectivePrediction] = {}
        for key, value in raw["predictions"].items():
            row = ProspectivePrediction.model_validate(value)
            if key != row.prediction_id:
                raise ValueError("prediction ledger key/id mismatch")
            records[key] = row
        return records

    def _persist(self) -> None:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "predictions": {key: row.model_dump(mode="json") for key, row in sorted(self._records.items())},
        }
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)

    def record(
        self, *, cycle: str, topic: str, proposition: str, proposition_stance: float,
        statement: str, confidence: float, created_at: datetime, due_at: datetime,
        verification_query: str, falsifier: str, source_evidence_refs: tuple[str, ...],
    ) -> ProspectivePrediction:
        created = _aware(created_at, "prediction created_at")
        due = _aware(due_at, "prediction due_at")
        identity = json.dumps({
            "cycle": cycle, "topic": topic, "proposition": proposition, "statement": statement,
            "created_at": created.isoformat(), "due_at": due.isoformat(),
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        prediction_id = "prediction:" + sha256(identity.encode()).hexdigest()[:24]
        row = ProspectivePrediction(
            prediction_id=prediction_id, cycle=cycle, topic=topic, proposition=proposition,
            proposition_stance=proposition_stance, statement=statement, confidence=confidence,
            created_at=created, due_at=due, verification_query=" ".join(verification_query.split()),
            falsifier=falsifier, source_evidence_refs=tuple(dict.fromkeys(source_evidence_refs)),
        )
        existing = self._records.get(prediction_id)
        if existing is not None:
            if existing.model_dump(mode="json") != row.model_dump(mode="json"):
                raise ValueError("prediction identity collision")
            return existing
        self._records[prediction_id] = row
        self._persist()
        return row

    def due(self, now: datetime) -> tuple[ProspectivePrediction, ...]:
        point = _aware(now, "prediction due now")
        rows = [row for row in self._records.values() if row.status == "OPEN" and row.due_at <= point]
        rows.sort(key=lambda row: (row.due_at, row.prediction_id))
        return tuple(rows)

    def open(self) -> tuple[ProspectivePrediction, ...]:
        rows = [row for row in self._records.values() if row.status == "OPEN"]
        rows.sort(key=lambda row: (row.due_at, row.prediction_id))
        return tuple(rows)

    def resolved(self) -> tuple[ProspectivePrediction, ...]:
        rows = [row for row in self._records.values() if row.status == "RESOLVED"]
        rows.sort(key=lambda row: row.outcome.checked_at if row.outcome else row.due_at, reverse=True)
        return tuple(rows)

    def check(
        self, prediction_id: str, *, checked_at: datetime,
        status: Literal["INCONCLUSIVE", "CONFIRMED", "REFUTED"],
        evidence: tuple[PredictionEvidence, ...], reliability: float, rationale: str,
    ) -> ProspectivePrediction:
        row = self._records.get(prediction_id)
        if row is None:
            raise ValueError("unknown prediction_id")
        if row.status == "RESOLVED":
            return row
        checked = _aware(checked_at, "prediction checked_at")
        if checked < row.due_at:
            raise ValueError("prediction cannot be evaluated before due_at")
        if any(item.observed_at <= row.created_at or item.observed_at > checked for item in evidence):
            raise ValueError("prediction outcome evidence violates prospective time boundary")
        evidence_ids = tuple(item.evidence_id for item in evidence)
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("prediction check evidence ids must be unique")
        actual = 1.0 if status == "CONFIRMED" else 0.0 if status == "REFUTED" else None
        error = abs(row.confidence - actual) if actual is not None else None
        identity = json.dumps({
            "prediction_id": prediction_id, "checked_at": checked.isoformat(), "status": status,
            "evidence_ids": evidence_ids,
        }, sort_keys=True, separators=(",", ":"))
        check_id = "prediction-check:" + sha256(identity.encode()).hexdigest()[:24]
        existing = next((item for item in row.checks if item.check_id == check_id), None)
        if existing is not None:
            return row
        check = PredictionCheck(
            check_id=check_id, status=status, checked_at=checked, evidence=evidence,
            reliability=reliability, rationale=rationale, prediction_error=error,
        )
        updated = row.model_copy(update={
            "checks": (*row.checks, check),
            "status": "RESOLVED" if status in {"CONFIRMED", "REFUTED"} else "OPEN",
        })
        updated = ProspectivePrediction.model_validate(updated.model_dump(mode="python"))
        self._records[prediction_id] = updated
        self._persist()
        return updated

    def projection(self, *, limit: int = 8) -> dict[str, list[dict]]:
        return {
            "open_predictions": [row.model_dump(mode="json") for row in self.open()[:limit]],
            "recent_outcomes": [row.model_dump(mode="json") for row in self.resolved()[:limit]],
        }


__all__ = [
    "PredictionAssessment", "PredictionAssessmentEvidence", "PredictionCheck", "PredictionEvidence",
    "PredictionProposal", "PredictionWorldviewRevision", "ProspectivePrediction", "ProspectivePredictionLedger",
    "SCHEMA_VERSION", "assess_prediction", "assess_worldview_revision", "propose_prediction", "settle_predictions",
]
