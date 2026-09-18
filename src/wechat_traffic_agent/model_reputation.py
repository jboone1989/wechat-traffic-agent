"""Refresh provider-neutral model reputation from external public leaderboards."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping
import urllib.error
import urllib.parse
import urllib.request

from agent_runtime.llm.reputation import ModelReputationBook, canonical_model_name


SCHEMA = "external-model-reputation.v1"
OPENROUTER_BENCHMARKS = "https://openrouter.ai/api/v1/benchmarks?source=artificial-analysis&max_results=500"
OPENROUTER_RANKINGS = "https://openrouter.ai/api/v1/datasets/rankings-daily"
OPENROUTER_TASKS = "https://openrouter.ai/api/v1/classifications/task?window=7d"
LMARENA_TEXT = "https://raw.githubusercontent.com/lmarena/arena-catalog/main/data/leaderboard-text.json"


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("reputation observation requires timezone")
    return value.astimezone(timezone.utc).replace(microsecond=0)


def _rank_percentiles(values: Mapping[str, float]) -> dict[str, float]:
    """Convert incomparable raw scales into within-snapshot 0..1 ranks."""
    rows = sorted(
        (
            (key, float(value))
            for key, value in values.items()
            if math.isfinite(float(value))
        ),
        key=lambda item: (-item[1], item[0]),
    )
    if not rows:
        return {}
    if len(rows) == 1:
        return {rows[0][0]: 1.0}
    return {
        key: 1.0 - (index / (len(rows) - 1))
        for index, (key, _value) in enumerate(rows)
    }


def _max_by_model(rows: list[Mapping[str, Any]], field: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for row in rows:
        model = canonical_model_name(str(row.get("model_permaslug") or ""))
        raw = row.get(field)
        if not model or isinstance(raw, bool) or not isinstance(raw, (int, float)):
            continue
        value = float(raw)
        if not math.isfinite(value):
            continue
        out[model] = max(out.get(model, float("-inf")), value)
    return out


class ExternalModelReputationObserver:
    """Read leaderboards; never maintain model scores by hand."""

    def __init__(self, *, state_root: Path) -> None:
        self.state_root = Path(state_root)
        self.path = (
            self.state_root
            / "application"
            / "model_reputation"
            / "latest.json"
        )

    def observe(
        self,
        *,
        now: datetime,
        minimum_interval: timedelta = timedelta(hours=6),
    ) -> dict[str, Any]:
        now_utc = _utc(now)
        current = self._read()
        if current is not None:
            try:
                observed = datetime.fromisoformat(
                    str(current.get("observed_at") or "").replace("Z", "+00:00")
                )
            except ValueError:
                observed = None
            if (
                observed is not None
                and observed.tzinfo is not None
                and now_utc - observed.astimezone(timezone.utc) < minimum_interval
            ):
                return self._summary(current, status="NOT_DUE")

        key = str(os.environ.get("OPENROUTER_API_KEY") or "").strip()
        source_status: dict[str, str] = {}
        aa_rows: list[Mapping[str, Any]] = []
        daily_rows: list[Mapping[str, Any]] = []
        task_payload: Mapping[str, Any] = {}
        arena_payload: Mapping[str, Any] = {}

        if key:
            try:
                bench = self._get_json(OPENROUTER_BENCHMARKS, api_key=key)
                aa_rows = [
                    row for row in bench.get("data") or ()
                    if isinstance(row, Mapping)
                    and row.get("source") == "artificial-analysis"
                ]
                source_status["artificial_analysis"] = "OK"
                aa_as_of = str((bench.get("meta") or {}).get("as_of") or "")
            except Exception as exc:  # noqa: BLE001
                source_status["artificial_analysis"] = type(exc).__name__
                aa_as_of = ""

            try:
                rankings = self._get_json(OPENROUTER_RANKINGS, api_key=key)
                daily_rows = [
                    row for row in rankings.get("data") or ()
                    if isinstance(row, Mapping)
                    and str(row.get("model_permaslug") or "") != "other"
                ]
                source_status["openrouter_usage"] = "OK"
                usage_as_of = str((rankings.get("meta") or {}).get("as_of") or "")
            except Exception as exc:  # noqa: BLE001
                source_status["openrouter_usage"] = type(exc).__name__
                usage_as_of = ""

            try:
                task = self._get_json(OPENROUTER_TASKS, api_key=key)
                task_payload = (
                    task.get("data") if isinstance(task.get("data"), Mapping) else {}
                )
                source_status["openrouter_tasks"] = "OK"
                task_as_of = str(task_payload.get("as_of") or "")
            except Exception as exc:  # noqa: BLE001
                source_status["openrouter_tasks"] = type(exc).__name__
                task_as_of = ""
        else:
            aa_as_of = usage_as_of = task_as_of = ""
            source_status["artificial_analysis"] = "CREDENTIAL_MISSING"
            source_status["openrouter_usage"] = "CREDENTIAL_MISSING"
            source_status["openrouter_tasks"] = "CREDENTIAL_MISSING"

        try:
            arena_payload = self._get_json(LMARENA_TEXT)
            source_status["lmarena"] = "OK"
        except Exception as exc:  # noqa: BLE001
            source_status["lmarena"] = type(exc).__name__

        models = self._merge(
            aa_rows=aa_rows,
            daily_rows=daily_rows,
            task_payload=task_payload,
            arena_payload=arena_payload,
            source_refs={
                "artificial_analysis": aa_as_of,
                "openrouter_usage": usage_as_of,
                "openrouter_tasks": task_as_of,
                "lmarena": "lmarena/arena-catalog:leaderboard-text.json",
            },
        )
        payload = {
            "schema_version": SCHEMA,
            "observed_at": now_utc.isoformat(),
            "model_count": len(models),
            "source_status": source_status,
            "models": models,
        }
        self._write(payload)
        return self._summary(payload, status="OBSERVED")

    def book(self) -> ModelReputationBook:
        return ModelReputationBook.from_payload(self._read() or {})

    def _merge(
        self,
        *,
        aa_rows: list[Mapping[str, Any]],
        daily_rows: list[Mapping[str, Any]],
        task_payload: Mapping[str, Any],
        arena_payload: Mapping[str, Any],
        source_refs: Mapping[str, str],
    ) -> list[dict[str, Any]]:
        intelligence = _rank_percentiles(_max_by_model(aa_rows, "intelligence_index"))
        coding = _rank_percentiles(_max_by_model(aa_rows, "coding_index"))
        agentic = _rank_percentiles(_max_by_model(aa_rows, "agentic_index"))

        usage_totals: dict[str, float] = defaultdict(float)
        if daily_rows:
            dates = sorted({str(row.get("date") or "") for row in daily_rows if row.get("date")})
            keep = set(dates[-7:])
            for row in daily_rows:
                if str(row.get("date") or "") not in keep:
                    continue
                model = canonical_model_name(str(row.get("model_permaslug") or ""))
                if not model:
                    continue
                try:
                    usage_totals[model] += float(row.get("total_tokens") or 0)
                except (TypeError, ValueError):
                    continue
        popularity = _rank_percentiles(usage_totals)

        task_usage: dict[str, dict[str, float]] = defaultdict(dict)
        for row in task_payload.get("classifications") or ():
            if not isinstance(row, Mapping):
                continue
            tag = str(row.get("tag") or "")
            for model_row in row.get("models") or ():
                if not isinstance(model_row, Mapping):
                    continue
                model = canonical_model_name(str(model_row.get("id") or ""))
                if not model:
                    continue
                try:
                    share = float(model_row.get("tag_usage_share") or 0.0)
                except (TypeError, ValueError):
                    continue
                task_usage[model][tag] = max(task_usage[model].get(tag, 0.0), share)

        arena_full = self._arena_category(arena_payload, "full")
        arena_coding = self._arena_category(arena_payload, "coding")
        arena_chinese = self._arena_category(arena_payload, "chinese")

        all_models = set(intelligence) | set(coding) | set(agentic) | set(popularity)
        all_models |= set(arena_full) | set(arena_coding) | set(arena_chinese)
        all_models |= set(task_usage)
        out: list[dict[str, Any]] = []
        for model in sorted(all_models):
            source_count = 0
            refs: list[str] = []
            if model in intelligence or model in coding or model in agentic:
                source_count += 1
                refs.append("artificial-analysis:" + (source_refs.get("artificial_analysis") or "current"))
            if model in popularity:
                source_count += 1
                refs.append("openrouter-usage:" + (source_refs.get("openrouter_usage") or "current"))
            if model in task_usage:
                source_count += 1
                refs.append("openrouter-task-usage:" + (source_refs.get("openrouter_tasks") or "current"))
            if model in arena_full or model in arena_coding or model in arena_chinese:
                source_count += 1
                refs.append("lmarena:" + (source_refs.get("lmarena") or "current"))

            overall = intelligence.get(model, arena_full.get(model, 0.5))
            code = max(coding.get(model, 0.5), arena_coding.get(model, 0.5))
            human = arena_full.get(model, 0.5)
            pop = popularity.get(model, 0.5)
            agent = agentic.get(model, 0.5)
            chinese = arena_chinese.get(model, 0.5)

            tags = task_usage.get(model, {})
            if tags:
                code_usage = max(
                    (value for tag, value in tags.items() if tag.startswith("code:")),
                    default=0.0,
                )
                agent_usage = max(
                    (value for tag, value in tags.items() if tag.startswith("agent:")),
                    default=0.0,
                )
                if code_usage:
                    code = max(code, min(1.0, 0.5 + code_usage))
                if agent_usage:
                    agent = max(agent, min(1.0, 0.5 + agent_usage))

            out.append({
                "canonical_name": model,
                "overall": round(overall, 6),
                "coding": round(code, 6),
                "agentic": round(agent, 6),
                "human_preference": round(human, 6),
                "popularity": round(pop, 6),
                "chinese": round(chinese, 6),
                "confidence": round(min(0.95, 0.50 + (0.12 * source_count)), 6),
                "source_refs": refs,
            })
        return out

    @staticmethod
    def _arena_category(payload: Mapping[str, Any], category: str) -> dict[str, float]:
        raw = payload.get(category)
        if not isinstance(raw, Mapping):
            return {}
        scores: dict[str, float] = {}
        for name, row in raw.items():
            if not isinstance(row, Mapping):
                continue
            try:
                rating = float(row.get("rating"))
            except (TypeError, ValueError):
                continue
            model = canonical_model_name(str(name))
            if model:
                scores[model] = max(scores.get(model, float("-inf")), rating)
        return _rank_percentiles(scores)

    @staticmethod
    def _get_json(url: str, *, api_key: str | None = None) -> Mapping[str, Any]:
        headers = {"Accept": "application/json", "User-Agent": "LivingRuntime/1.0"}
        if api_key:
            headers["Authorization"] = "Bearer " + api_key
        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=30.0) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"HTTP_{exc.code}") from exc
        except TimeoutError as exc:
            raise RuntimeError("TIMEOUT") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError("NETWORK_ERROR") from exc
        if not isinstance(payload, Mapping):
            raise ValueError("leaderboard response is not an object")
        return payload

    def _read(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA:
            return None
        return payload

    def _write(self, payload: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(dict(payload), sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        tmp.replace(self.path)

    @staticmethod
    def _summary(payload: Mapping[str, Any], *, status: str) -> dict[str, Any]:
        return {
            "status": status,
            "observed_at": payload.get("observed_at"),
            "model_count": int(payload.get("model_count") or 0),
            "source_status": dict(payload.get("source_status") or {}),
        }


def load_model_reputation_book(state_root: Path) -> ModelReputationBook:
    observer = ExternalModelReputationObserver(state_root=state_root)
    return observer.book()


__all__ = [
    "ExternalModelReputationObserver",
    "load_model_reputation_book",
]
