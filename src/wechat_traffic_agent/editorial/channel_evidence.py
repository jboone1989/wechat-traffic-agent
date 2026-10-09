"""Versioned channel outcome evidence and descriptive comparisons, never control."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from wechat_traffic_agent.channel_evidence_sqlite import EvidenceTable
from wechat_traffic_agent.web_editorial import digest


class ChannelEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    schema_version: Literal["content.channel-evidence.v1"] = "content.channel-evidence.v1"
    channel: str = Field(min_length=1, max_length=80)
    account: str = Field(min_length=1, max_length=200)
    publication_id: str = Field(min_length=1, max_length=200)
    artifact_id: str = Field(min_length=1, max_length=200)
    published_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    agent_candidate_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    topic: str = Field(min_length=1, max_length=200)
    title_version: str = Field(min_length=1, max_length=200)
    structure_version: str = Field(min_length=1, max_length=200)
    strategy_version: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=1, max_length=80)
    distribution_cohort: str = Field(min_length=1, max_length=200)
    metric: str = Field(min_length=1, max_length=80)
    metric_definition: str = Field(min_length=1, max_length=200)
    unit: Literal["count", "ratio", "seconds"]
    value: float | None = Field(default=None, ge=0)
    published_at: datetime
    window_start: datetime
    window_end: datetime
    observed_at: datetime
    revision: int = Field(ge=1, le=10000, strict=True)
    source: str = Field(min_length=1, max_length=200)
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=50)

    @field_validator("published_at", "window_start", "window_end", "observed_at")
    @classmethod
    def aware(cls, value):
        if value.utcoffset() is None:
            raise ValueError("timestamps must be timezone-aware")
        return value

    @field_validator("value", mode="before")
    @classmethod
    def numeric(cls, value):
        if isinstance(value, bool) or (value is not None and not isinstance(value, (int, float))):
            raise ValueError("metric value must be a number or null")
        return value

    @field_validator("evidence_refs")
    @classmethod
    def refs(cls, value):
        if len(set(value)) != len(value) or any(not x.strip() or len(x) > 1000 for x in value):
            raise ValueError("unique nonblank bounded evidence references required")
        return value

    @model_validator(mode="after")
    def valid_measurement(self):
        if not self.published_at <= self.window_start < self.window_end <= self.observed_at:
            raise ValueError("require publication <= window start < window end <= observed time")
        if self.value is not None:
            if self.unit == "ratio" and self.value > 1:
                raise ValueError("ratio must be within [0,1]")
            if self.unit == "count" and not self.value.is_integer():
                raise ValueError("count must be integral")
        return self

    def identity(self):
        # Corrections can update values/metadata but cannot create a second sample.
        payload = self.model_dump(mode="json")
        payload["window_start"] = self.window_start.astimezone(timezone.utc).isoformat()
        payload["window_end"] = self.window_end.astimezone(timezone.utc).isoformat()
        return {k: payload[k] for k in (
            "channel", "account", "publication_id", "window_start", "window_end", "metric", "source")}


class ChannelEvidenceStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _table(self) -> EvidenceTable:
        return EvidenceTable(self.root)

    def record(self, row: ChannelEvidence) -> Path:
        row = ChannelEvidence.model_validate_json(row.model_dump_json())
        return self._table().write(
            row, decode=ChannelEvidence.model_validate_json, identity_hash=digest,
        )

    def as_of(self, cutoff: datetime, *, channel: str | None = None,
              account: str | None = None) -> list[ChannelEvidence]:
        return self._table().as_of(
            cutoff, decode=ChannelEvidence.model_validate_json, identity_hash=digest,
            channel=channel, account=account,
        )

    def migrate_legacy(self) -> dict:
        return self._table().count(
            decode=ChannelEvidence.model_validate_json, identity_hash=digest,
        )

    def all_evidence_refs(self) -> set[str]:
        return self._table().evidence_refs(
            decode=ChannelEvidence.model_validate_json, identity_hash=digest,
        )

    def report(self, *, channel: str, account: str, cutoff: datetime, dimension: str):
        if dimension not in {"topic", "title_version", "structure_version", "strategy_version"}:
            raise ValueError("unsupported comparison dimension")
        groups = {}
        rows = self.as_of(cutoff, channel=channel, account=account)
        for row in rows:
            # Group all comparability constraints, including the age of the measured window.
            scope = {k: getattr(row, k) for k in ("metric", "metric_definition", "unit", "objective", "distribution_cohort", "source")}
            scope["window_seconds"] = (row.window_end - row.window_start).total_seconds()
            scope["window_offset_seconds"] = (row.window_start - row.published_at).total_seconds()
            scope["attribution"] = ("AGENT_UNMODIFIED" if row.agent_candidate_sha256 == row.published_content_sha256
                                    else "UNKNOWN" if row.agent_candidate_sha256 is None else "EDITOR_MODIFIED")
            key = digest(scope)
            group = groups.setdefault(key, {"scope": scope, "variants": {}})
            variant = group["variants"].setdefault(getattr(row, dimension), {"known_values": [], "unknown": 0, "evidence_refs": []})
            if row.value is None:
                variant["unknown"] += 1
            else:
                variant["known_values"].append(row.value)
            variant["evidence_refs"].extend(row.evidence_refs)
        output = []
        for key, group in sorted(groups.items()):
            variants = []
            for name, values in sorted(group["variants"].items()):
                known = values["known_values"]
                variants.append({"variant": name, "observations": len(known), "unknown": values["unknown"],
                                 "mean": sum(known) / len(known) if known else None,
                                 "evidence_refs": sorted(set(values["evidence_refs"]))})
            output.append({"scope": group["scope"], "variants": variants})
        return {"status": "DESCRIPTIVE_ONLY" if rows else "NO_EVIDENCE", "channel": channel,
                "account": account, "as_of": cutoff.isoformat(), "dimension": dimension, "groups": output,
                "limitations": ["Observational differences do not establish causal strategy improvement.",
                                "No sample-size adequacy or significance claim; unknown data stays unknown.",
                                "Imported source references are attestations, not independently verified analytics."],
                "publication_authority": False}
