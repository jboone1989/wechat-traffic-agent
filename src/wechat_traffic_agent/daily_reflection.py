"""Evidence-grounded daily self-reflection for the persistent Content Agent."""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any
from zoneinfo import ZoneInfo

from .human_assistance import WeComNotifier

_CYCLE_TS = re.compile(r"(\d+)$")


def _timezone() -> ZoneInfo:
    return ZoneInfo(str(os.environ.get("CONTENT_REFLECTION_TIMEZONE") or "Asia/Shanghai"))


def _hour() -> int:
    value = int(os.environ.get("CONTENT_DAILY_REFLECTION_HOUR", "8"))
    if not 0 <= value <= 23:
        raise ValueError("CONTENT_DAILY_REFLECTION_HOUR must be 0..23")
    return value


def _cycle_date(name: str, tz: ZoneInfo) -> date | None:
    match = _CYCLE_TS.search(name)
    if not match:
        return None
    return datetime.fromtimestamp(int(match.group(1)), timezone.utc).astimezone(tz).date()


class DailyReflectionService:
    def __init__(
        self,
        *,
        state_root: Path,
        mind: Any,
        runtime: Any | None = None,
        notifier: WeComNotifier | None = None,
    ) -> None:
        self.state_root = Path(state_root)
        self.mind = mind
        self.runtime = runtime
        self.notifier = notifier or WeComNotifier()
        self.root = self.state_root / "application" / "daily_reflections"

    def maybe_generate(self, now: datetime) -> dict[str, Any]:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("daily reflection timestamp requires timezone")
        local_now = now.astimezone(_timezone())
        target = local_now.date() - timedelta(days=1)
        if local_now.hour < _hour():
            return {"status": "NOT_DUE", "target_date": target.isoformat()}
        return self.generate(target_date=target, now=now)

    async def reflect_now(
        self,
        now: datetime,
        *,
        relevant_refs: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        """Perform one bounded, event/heartbeat-driven internal reflection."""
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("reflection timestamp requires timezone")
        probe = getattr(self.runtime, "cognitive_probe", None)
        if not callable(probe):
            return {"status": "COGNITION_UNAVAILABLE"}

        refs = tuple(
            dict.fromkeys(
                str(ref).strip()
                for ref in relevant_refs
                if str(ref).strip()
            )
        )
        trigger_ref = next(
            (ref for ref in refs if ref.startswith("reflection-trigger:")),
            "",
        )
        if not trigger_ref:
            # Manual/legacy REFLECT calls may not carry a trigger ref. A
            # quarter-hour bucket is stable across ordinary durable resumes.
            bucket = int(now.astimezone(timezone.utc).timestamp()) // 900
            trigger_ref = f"reflection-trigger:legacy:{bucket}"
        reflection_id = hashlib.sha256(trigger_ref.encode("utf-8")).hexdigest()[:20]
        root = self.state_root / "application" / "reflections"
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{reflection_id}.json"
        if path.exists():
            row = json.loads(path.read_text(encoding="utf-8"))
            return {"status": "ALREADY_REFLECTED", **row}

        context = self._continuous_context(now, refs)
        text = self._validate_cognitive_reflection(
            await probe(
                self._continuous_prompt(now, trigger_ref, context),
                max_tokens=1400,
            )
        )
        memory_writeback = self._record_continuous_reflection(
            reflection_id=reflection_id,
            trigger_ref=trigger_ref,
            text=text,
            refs=refs,
        )
        row = {
            "schema_version": "content-continuous-reflection.v1",
            "reflection_id": reflection_id,
            "trigger_ref": trigger_ref,
            "reflected_at": now.astimezone(timezone.utc).replace(microsecond=0).isoformat(),
            "relevant_refs": list(refs),
            "reflection_text": text,
            "memory_writeback": memory_writeback,
        }
        self._write(path, row)
        return {"status": "REFLECTED", **row}

    def _continuous_context(
        self,
        now: datetime,
        refs: tuple[str, ...],
    ) -> dict[str, Any]:
        snapshot = self.mind.session.snapshot()
        cycles: list[dict[str, Any]] = []
        cycle_root = self.state_root / "content" / "continuous" / "cycles"
        candidates = (
            sorted(cycle_root.glob("*/result.json"), key=lambda p: p.parent.name)[-12:]
            if cycle_root.exists()
            else []
        )
        for path in candidates:
            try:
                row = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(row, dict):
                continue
            plan = row.get("mind_plan") if isinstance(row.get("mind_plan"), dict) else {}
            cognition = row.get("cognition") if isinstance(row.get("cognition"), dict) else {}
            cycles.append({
                "cycle": path.parent.name,
                "status": row.get("status"),
                "topic": plan.get("required_topic"),
                "cognition_state": cognition.get("cognition_state"),
                "error_type": row.get("error_type"),
                "model_calls": (row.get("mind_experience") or {}).get("model_calls")
                if isinstance(row.get("mind_experience"), dict)
                else None,
            })
        prior: list[dict[str, Any]] = []
        journal = self.state_root / "application" / "reflections"
        for path in sorted(journal.glob("*.json"))[-3:] if journal.exists() else ():
            try:
                row = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if isinstance(row, dict):
                prior.append({
                    "reflected_at": row.get("reflected_at"),
                    "trigger_ref": row.get("trigger_ref"),
                    "reflection_text": str(row.get("reflection_text") or "")[:1200],
                })
        return {
            "as_of": now.astimezone(timezone.utc).replace(microsecond=0).isoformat(),
            "trigger_refs": list(refs),
            "mind": self._mind_context(snapshot),
            "recent_cycles": cycles,
            "recent_reflections": prior,
        }

    @staticmethod
    def _continuous_prompt(
        now: datetime,
        trigger_ref: str,
        context: dict[str, Any],
    ) -> str:
        evidence = json.dumps(
            context,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return (
            "你是 Ferro，正在进行一次持续的、事件驱动的自我反思，而不是写日报。"
            "只依据给定证据思考：刚刚发生了什么；它是否改变了你的判断、目标优先级、"
            "对自己能力边界的认识或下一步策略；是否存在重复犯错、回避证据、空转或过度自信。"
            "如果没有值得改变的地方，也要明确说没有，不要强行制造成长。"
            "用自然中文第一人称写一段具体的内部反思，不要固定标题，不要模板句式，不要 JSON。\n\n"
            f"触发：{trigger_ref}\n"
            f"时间：{now.astimezone(timezone.utc).isoformat()}\n"
            f"证据：{evidence}"
        )

    async def maybe_generate_cognitive(self, now: datetime) -> dict[str, Any]:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("daily reflection timestamp requires timezone")
        local_now = now.astimezone(_timezone())
        target = local_now.date() - timedelta(days=1)
        if local_now.hour < _hour():
            return {"status": "NOT_DUE", "target_date": target.isoformat()}
        return await self.generate_cognitive(target_date=target, now=now)

    async def generate_cognitive(
        self,
        *,
        target_date: date,
        now: datetime,
    ) -> dict[str, Any]:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{target_date.isoformat()}.json"
        if path.exists():
            row = json.loads(path.read_text(encoding="utf-8"))
            if row.get("delivery_status") != "SENT" and self.notifier.configured:
                delivery = self.notifier.send_text(row["reflection_text"])
                row["delivery_status"] = delivery["status"]
                row["delivered_at"] = now.astimezone(timezone.utc).replace(microsecond=0).isoformat()
                self._write(path, row)
            return {"status": "ALREADY_GENERATED", **row}

        facts = self._facts(target_date)
        probe = getattr(self.runtime, "cognitive_probe", None)
        if not callable(probe):
            return self.generate(target_date=target_date, now=now)

        text = self._validate_cognitive_reflection(
            await probe(
                self._cognitive_prompt(target_date, facts, attempt_at=now),
                max_tokens=1600,
            )
        )
        memory_writeback = self._record_reflection(
            target_date=target_date,
            text=text,
            facts=facts,
        )
        row = {
            "schema_version": "content-daily-reflection.v2",
            "target_date": target_date.isoformat(),
            "timezone": _timezone().key,
            "generated_at": now.astimezone(timezone.utc).replace(microsecond=0).isoformat(),
            "facts": facts,
            "reflection_text": text,
            "reflection_mode": "COGNITIVE",
            "memory_writeback": memory_writeback,
            "delivery_status": "PENDING_CONFIGURATION" if not self.notifier.configured else "PENDING",
            "delivered_at": None,
        }
        if self.notifier.configured:
            delivery = self.notifier.send_text(text)
            row["delivery_status"] = delivery["status"]
            row["delivered_at"] = row["generated_at"]
        self._write(path, row)
        (self.root / f"{target_date.isoformat()}.txt").write_text(text + "\n", encoding="utf-8")
        return {"status": "GENERATED", **row}

    def generate(self, *, target_date: date, now: datetime) -> dict[str, Any]:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{target_date.isoformat()}.json"
        if path.exists():
            row = json.loads(path.read_text(encoding="utf-8"))
            if row.get("delivery_status") != "SENT" and self.notifier.configured:
                delivery = self.notifier.send_text(row["reflection_text"])
                row["delivery_status"] = delivery["status"]
                row["delivered_at"] = now.astimezone(timezone.utc).replace(microsecond=0).isoformat()
                self._write(path, row)
            return {"status": "ALREADY_GENERATED", **row}

        facts = self._facts(target_date)
        text = self._render(target_date, facts)
        row = {
            "schema_version": "content-daily-reflection.v1",
            "target_date": target_date.isoformat(),
            "timezone": _timezone().key,
            "generated_at": now.astimezone(timezone.utc).replace(microsecond=0).isoformat(),
            "facts": facts,
            "reflection_text": text,
            "reflection_mode": "TEMPLATE_FALLBACK",
            "delivery_status": "PENDING_CONFIGURATION" if not self.notifier.configured else "PENDING",
            "delivered_at": None,
        }
        if self.notifier.configured:
            delivery = self.notifier.send_text(text)
            row["delivery_status"] = delivery["status"]
            row["delivered_at"] = row["generated_at"]
        self._write(path, row)
        (self.root / f"{target_date.isoformat()}.txt").write_text(text + "\n", encoding="utf-8")
        return {"status": "GENERATED", **row}

    def _facts(self, target_date: date) -> dict[str, Any]:
        cycles: list[tuple[str, dict[str, Any]]] = []
        cycle_root = self.state_root / "content" / "continuous" / "cycles"
        if cycle_root.exists():
            for path in cycle_root.glob("*/result.json"):
                if _cycle_date(path.parent.name, _timezone()) != target_date:
                    continue
                try:
                    row = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError, json.JSONDecodeError):
                    continue
                if isinstance(row, dict):
                    cycles.append((path.parent.name, row))
        cycles.sort(key=lambda item: item[0])
        statuses = Counter(str(row.get("status") or "UNKNOWN") for _, row in cycles)
        topics = Counter(
            str((row.get("mind_plan") or {}).get("required_topic"))
            for _, row in cycles if (row.get("mind_plan") or {}).get("required_topic")
        )
        cognition = Counter(
            str((row.get("cognition") or {}).get("cognition_state"))
            for _, row in cycles if (row.get("cognition") or {}).get("cognition_state")
        )
        errors = Counter(
            str(row.get("error_type") or "UNKNOWN")
            for _, row in cycles if row.get("status") == "ERROR"
        )
        model_calls_by_status: Counter[str] = Counter()
        for _, row in cycles:
            calls = (row.get("mind_experience") or {}).get("model_calls")
            if isinstance(calls, int) and not isinstance(calls, bool) and calls >= 0:
                model_calls_by_status[str(row.get("status") or "UNKNOWN")] += calls

        snapshot = self.mind.session.snapshot()
        resource = snapshot.get("resource_awareness") or {}
        resource_states = []
        for state in resource.get("states", []):
            stamp = state.get("observed_at")
            if not stamp:
                continue
            try:
                observed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).astimezone(_timezone())
            except ValueError:
                continue
            if observed.date() == target_date:
                resource_states.append(state)
        resource_states.sort(key=lambda row: str(row.get("observed_at") or ""))
        if resource_states:
            opening = float(resource_states[0]["available_funds"])
            closing = float(resource_states[-1]["available_funds"])
            resource_summary = {
                "status": "OBSERVED",
                "currency": resource_states[-1].get("currency"),
                "opening_balance": opening,
                "closing_balance": closing,
                "observed_balance_delta": closing - opening,
                "observation_count": len(resource_states),
                "latest_daily_burn": resource_states[-1].get("estimated_daily_burn"),
                "latest_runway_days": resource_states[-1].get("runway_days"),
            }
        else:
            resource_summary = {
                "status": "UNKNOWN",
                "reason": "No timestamped resource observations exist for this day; do not backfill cost from later balances.",
            }
        return {
            "cycle_count": len(cycles),
            "statuses": dict(sorted(statuses.items())),
            "model_calls": sum(model_calls_by_status.values()),
            "model_calls_by_status": dict(sorted(model_calls_by_status.items())),
            "top_topics": [{"topic": topic, "cycles": count} for topic, count in topics.most_common(5)],
            "cognition_states": dict(sorted(cognition.items())),
            "error_types": dict(sorted(errors.items())),
            "resource": resource_summary,
            "evidence_cycle_ids": [name for name, _ in cycles],
            "mind_context": self._mind_context(snapshot),
        }

    @staticmethod
    def _compact(value: Any, *, depth: int = 0) -> Any:
        if depth >= 3:
            if isinstance(value, (dict, list, tuple)):
                return f"<{type(value).__name__}>"
            return str(value)[:240]
        if isinstance(value, dict):
            items = list(value.items())
            return {
                str(key)[:120]: DailyReflectionService._compact(item, depth=depth + 1)
                for key, item in items[-12:]
            }
        if isinstance(value, (list, tuple)):
            return [
                DailyReflectionService._compact(item, depth=depth + 1)
                for item in list(value)[-12:]
            ]
        if isinstance(value, str):
            return value[:500]
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return str(value)[:240]

    @classmethod
    def _limit_context(cls, value: Any, budget: int) -> Any:
        """Bound the serialized evidence, leaving the persisted snapshot intact."""
        encode = lambda item: json.dumps(item, ensure_ascii=False, separators=(",", ":"))
        if len(encode(value)) <= budget:
            return value
        if isinstance(value, str):
            text = value[:max(0, budget - 20)]
            while len(encode(text + " [truncated]")) > budget and text:
                text = text[:-1]
            return text + " [truncated]"
        if isinstance(value, dict):
            items = list(value.items())[-12:]
            overhead = sum(len(encode(str(key))) + 2 for key, _ in items) + 2
            share = max(16, (budget - overhead) // max(1, len(items)))
            result = {key: cls._limit_context(item, share) for key, item in items}
            while len(encode(result)) > budget and result:
                result.pop(next(iter(result)))
            return result
        if isinstance(value, (list, tuple)):
            items = list(value)[-12:]
            share = max(16, (budget - len(items) - 2) // max(1, len(items)))
            result = [cls._limit_context(item, share) for item in items]
            while len(encode(result)) > budget and result:
                result.pop(0)
            return result
        return str(value)[:max(0, budget - 2)]

    @classmethod
    def _mind_context(cls, snapshot: dict[str, Any]) -> dict[str, Any]:
        context: dict[str, Any] = {}
        for key in (
            "tick",
            "autobiography",
            "worldview",
            "goals",
            "interests",
            "predictive_learning",
            "resource_awareness",
            "identity",
        ):
            if key in snapshot:
                context[key] = cls._limit_context(cls._compact(snapshot[key]), 1400)
        context["evidence_scope"] = {
            "kind": "bounded_recent_projection",
            "max_chars_per_section": 1400,
            "persisted_snapshot_changed": False,
        }
        return context

    @staticmethod
    def _cognitive_prompt(
        target_date: date,
        facts: dict[str, Any],
        *,
        attempt_at: datetime,
    ) -> str:
        evidence = json.dumps(
            facts,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return (
            "You are Ferro performing a genuine end-of-day self-reflection. "
            "This is not a reporting template. Infer only from the supplied evidence. "
            "Distinguish observed facts from interpretation, identify what actually changed "
            "in your beliefs or strategy, name the most important failure or surprise, and "
            "choose one or two concrete adjustments for the next day. Do not repeat fixed "
            "headings or stock sentences just because prior reflections used them. "
            "If the evidence is weak, say so explicitly. Write in natural Chinese, concise "
            "but specific, as a first-person reflection suitable to send directly to the owner. "
            "Do not output JSON or markdown fences.\n\n"
            f"Reflection date: {target_date.isoformat()}\n"
            f"Attempt timestamp: {attempt_at.astimezone(timezone.utc).isoformat()}\n"
            f"Evidence packet: {evidence}"
        )

    @staticmethod
    def _validate_cognitive_reflection(raw: Any) -> str:
        text = str(raw or "").strip()
        if text.startswith("```") and text.endswith("```"):
            text = text[3:-3].strip()
        if len(text) < 80:
            raise ValueError("cognitive daily reflection is too short")
        if len(text) > 8000:
            text = text[:8000].rstrip()
        return text

    def _record_reflection(
        self,
        *,
        target_date: date,
        text: str,
        facts: dict[str, Any],
    ) -> dict[str, Any]:
        record = getattr(self.mind, "record_daily_reflection", None)
        if not callable(record):
            return {"status": "UNAVAILABLE"}
        try:
            return record(
                target_date=target_date.isoformat(),
                reflection_text=text,
                evidence_refs=tuple(
                    str(value)
                    for value in facts.get("evidence_cycle_ids") or ()
                    if str(value)
                ),
            )
        except Exception as exc:
            return {
                "status": "ERROR",
                "error_type": type(exc).__name__,
                "message": str(exc)[:300],
            }

    def _record_continuous_reflection(
        self,
        *,
        reflection_id: str,
        trigger_ref: str,
        text: str,
        refs: tuple[str, ...],
    ) -> dict[str, Any]:
        record = getattr(self.mind, "record_reflection_event", None)
        if not callable(record):
            return {"status": "UNAVAILABLE"}
        try:
            return record(
                reflection_id=reflection_id,
                reflection_text=text,
                trigger_ref=trigger_ref,
                evidence_refs=refs,
            )
        except Exception as exc:
            return {
                "status": "ERROR",
                "error_type": type(exc).__name__,
                "message": str(exc)[:300],
            }

    @staticmethod
    def _render(target_date: date, facts: dict[str, Any]) -> str:
        statuses = facts["statuses"]
        published = int(statuses.get("PUBLISHED", 0))
        held = int(statuses.get("HELD_BY_CHECKS", 0))
        errors = int(statuses.get("ERROR", 0))
        cognition_only = int(statuses.get("COGNITION_ONLY", 0))
        calls = int(facts["model_calls"])
        belief_changes = int(facts["cognition_states"].get("BELIEF_CHANGED", 0))
        top_topics = facts["top_topics"]
        topic_text = "、".join(
            f"{row['topic']}({row['cycles']}轮)" for row in top_topics[:3]
        ) or "没有形成稳定主题"
        error_text = "、".join(
            f"{name}×{count}" for name, count in facts["error_types"].items()
        ) or "没有记录到错误"
        held_calls = int(facts["model_calls_by_status"].get("HELD_BY_CHECKS", 0))

        activity_text = (
            f"昨天共跑了 {facts['cycle_count']} 个内容周期：{published} 次发布，"
            f"{held} 次被质量门拦住，{cognition_only} 次只形成认知更新，"
            f"{errors} 次出错。记录到的模型调用共 {calls} 次。"
        )
        if belief_changes:
            learning_text = (
                f"注意力主要放在：{topic_text}。有 {belief_changes} 次状态明确标记为 "
                "BELIEF_CHANGED；这些才算真正改变过信念，其他调查或表达不能因为写出来了就算学会。"
            )
        else:
            learning_text = (
                f"注意力主要放在：{topic_text}。昨天没有状态明确标记为 BELIEF_CHANGED；"
                "我不会把普通调查或表达写成已经学会。"
            )

        friction_parts = []
        if errors:
            friction_parts.append(f"错误记录是 {error_text}")
        if held:
            friction_parts.append(
                f"质量门拦住了 {held} 轮，其中记录了 {held_calls} 次模型调用"
            )
        friction_text = "；".join(friction_parts) + "。" if friction_parts else ""

        resource = facts["resource"]
        if resource["status"] == "OBSERVED":
            resource_text = (
                f"资源方面，我有 {resource['observation_count']} 次可靠余额观测；"
                f"余额从 {resource['opening_balance']:.2f} 变到 "
                f"{resource['closing_balance']:.2f} {resource['currency']}。"
                "这只是账户余额变化，不自动等同于全部由内容循环造成的消费。"
            )
        else:
            resource_text = (
                "资源方面，昨天还没有可靠的时间戳余额观测，所以我不补写消费数字；"
                "资源遥测从后续真实观测开始累计。"
            )

        adjustment = []
        if held:
            adjustment.append(
                f"减少重复撞质量门：昨天 {held} 轮被拦住，其中记录了 {held_calls} 次模型调用"
            )
        if errors:
            adjustment.append(f"先消除可复现错误（{error_text}），再为同类任务继续付模型成本")
        if belief_changes:
            adjustment.append(
                f"保留并复查 {belief_changes} 次真正改变信念的证据链，不把普通产出冒充学习"
            )
        if not adjustment:
            adjustment.append("继续比较行动成本与真实外部结果，避免因为单日顺利就过度扩大调用")
        adjustment_text = "今天我会调整：" + "；".join(adjustment) + "。"

        if errors:
            lead = f"昨天最该先处理的是失败：{errors} 次出错，具体记录为 {error_text}。"
            sections = [friction_text, activity_text, learning_text, resource_text, adjustment_text]
        elif held:
            lead = (
                f"昨天最大的摩擦来自质量门：{held} 轮被拦住，"
                f"这些循环记录了 {held_calls} 次模型调用。"
            )
            sections = [friction_text, activity_text, learning_text, resource_text, adjustment_text]
        elif belief_changes:
            lead = (
                f"昨天最值得保留的是学习证据：{belief_changes} 次状态明确标记为 "
                "BELIEF_CHANGED。"
            )
            sections = [learning_text, activity_text, resource_text, adjustment_text]
        elif published:
            lead = f"昨天最明确的外部动作是发布：{published} 次内容真正发布。"
            sections = [activity_text, learning_text, resource_text, adjustment_text]
        else:
            lead = (
                "昨天没有出现错误、质量门拦截或明确的 BELIEF_CHANGED；"
                "我先把活动量和资源事实摆出来，不把平静误写成学习。"
            )
            sections = [activity_text, learning_text, resource_text, adjustment_text]

        body = "\n\n".join(section for section in sections if section)
        return (
            "【证据摘要 fallback：未经过 Ferro 自主认知】\n\n"
            f"Ferro · {target_date.isoformat()} 日终反思\n\n{lead}\n\n{body}"
        )

    @staticmethod
    def _write(path: Path, row: dict[str, Any]) -> None:
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        os.replace(tmp, path)


__all__ = ["DailyReflectionService"]
