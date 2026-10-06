"""Temporary daily original-post goal and durable operator delivery."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
from hashlib import sha256
import fcntl
import json
from pathlib import Path
from zoneinfo import ZoneInfo


def _read(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def _write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _time(value):
    stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("posting timestamps require a timezone")
    return stamp


class DailyPostingGoal:
    def __init__(self, state_root):
        self.state_root = Path(state_root)
        self.root = self.state_root / "content/x/posting_goal"
        self.config_path = self.state_root / "content/x/posting_contract.json"

    @contextmanager
    def _lock(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / "monitor.lock").open("a") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            yield

    def publications(self):
        rows = {}
        history = self.state_root / "content/x/history.jsonl"
        try:
            stream = history.open(encoding="utf-8")
        except FileNotFoundError:
            return []
        with stream:
            items = []
            for line in stream:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue  # A concurrent append may leave its last line incomplete.
                if isinstance(item, dict) and item.get("schema_version") == "content-agent-x-history.v1":
                    items.append(item)
        for item in items:
            if item.get("event_type") != "EXPRESSION" or not item.get("x_post_id"):
                continue
            folder = self.state_root / "content/continuous/cycles" / str(item["cycle"])
            receipt = _read(folder / "x_browser_publication.json")
            if receipt.get("status") != "POSTED":
                receipt = _read(folder / "x_social_expression.json")
            if receipt.get("status") != "POSTED":
                receipt = _read(folder / "x_native_publication_verification.json")
            post_id = str(item["x_post_id"])
            url = str(receipt.get("url") or "")
            if (receipt.get("status") != "POSTED" or str(receipt.get("post_id")) != post_id
                    or not url.startswith("https://x.com/") or post_id not in url):
                continue
            rows[post_id] = {
                "post_id": post_id, "url": url, "cycle": item["cycle"],
                "format": receipt.get("format") or "SHORT_POST",
                "published_at": receipt.get("published_at") or item["recorded_at"],
            }
        return sorted(rows.values(), key=lambda row: row["published_at"])

    def status(self, now):
        config = _read(self.config_path)
        if not config:
            return {"enabled": False, "remaining": 0}
        zone = ZoneInfo(config["timezone"])
        local = now.astimezone(zone)
        date = local.date().isoformat()
        midnight = datetime.combine(local.date() + timedelta(days=1), datetime.min.time(), zone)
        posts = [row for row in self.publications() if _time(row["published_at"]).astimezone(zone).date() == local.date()]
        minimum = int(config["minimum_original_posts"])
        progress = _read(self.root / "progress.json")
        first_attempt = max(_time(config["activated_at"]), datetime.combine(local.date(), datetime.min.time(), zone))
        next_attempt = (progress.get("next_attempt_at") if progress.get("date") == date else None) or first_attempt.isoformat()
        return {
            "enabled": bool(config["enabled"]), "temporary": True, "timezone": config["timezone"],
            "date": date, "minimum": minimum, "published": len(posts),
            "remaining": max(0, minimum - len(posts)), "deadline": midnight.isoformat(),
            "next_attempt_at": next_attempt, "goal_id": "ferro-daily-post:" + date,
            "notifications_enabled": bool(config.get("notifications_enabled", True)),
        }

    def owner_goal(self, now):
        status = self.status(now)
        if not status["enabled"] or not status["remaining"]:
            return None
        return {
            "goal_id": status["goal_id"], "origin": "USER", "priority": 1.0,
            "created_at": now.isoformat(), "daily_posting_contract": status,
            "description": (
                f"临时硬目标：北京时间 {status['date']} 结束前，在 X 真实发布至少 {status['minimum']} 条原创帖子或图文。"
                f"已发布 {status['published']} 条，还差 {status['remaining']} 条。"
                "只计已验证发布，回复和草稿不计。自由选择加密货币、AI、金融及交叉方向，充分使用搜索；"
                "按证据质量选择短帖、长帖或图文。同一证据可以支持不同有用表达，不重复灌水。"
                "每次发布的链接通过现有企业微信通知；流程阻塞需修复或明确求助。"
                "这是用户当前明确的最低数量要求，取代此前20条仅作建议的设置。"
                "不要因某个选题无证据就结束整个目标，应换问题或搜索。"
                "只关闭此临时数量规则后，才恢复完全自主的数量决策。"
            ),
            "reason": "OWNER_REQUIRED_TEMPORARY_DAILY_MINIMUM",
        }

    def schedule(self, wakeful, now):
        from .wakeful_runtime import PlannedWake
        status = self.status(now)
        wake_id = "content:DAILY_POST_GOAL"
        if not status["enabled"]:
            wakeful.core.cancel(wake_id)
            return status
        if status["remaining"]:
            # Keep overdue work at its original due time so repeated waits cannot
            # move it behind a continually replenished internal-task backlog.
            at = _time(status["next_attempt_at"])
        else:
            at = _time(status["deadline"])
        existing = next((row for row in wakeful.core.snapshot().pending_plans if row.wake_id == wake_id), None)
        if existing is None or existing.at != at or status["goal_id"] not in existing.relevant_refs:
            wakeful.core.schedule(PlannedWake(wake_id=wake_id, at=at,
                reason="owner-required temporary daily original posting minimum",
                task="CONTENT_COGNITION", relevant_refs=(status["goal_id"],), preemptible=False))
        return status

    def _queue(self, key, title, message, now):
        event_id = sha256(key.encode()).hexdigest()[:24]
        path = self.root / "notifications" / (event_id + ".json")
        if path.exists():
            return
        _write(path, {"event_id": event_id, "key": key, "title": title, "message": message,
            "created_at": now.isoformat(), "status": "PENDING",
            "notification_id": "note_zzferro_" + now.strftime("%Y%m%d%H%M%S") + "_" + event_id})

    def _deliver(self, config, now):
        operator_root = Path(config["operator_state_dir"])
        results = []
        for path in sorted((self.root / "notifications").glob("*.json")):
            row = _read(path)
            if row["status"] == "DELIVERED":
                continue
            notification_id = row["notification_id"]
            delivery_path = operator_root / "operator/deliveries" / ("notification__" + notification_id + "__wecom.json")
            delivery = _read(delivery_path)
            if delivery.get("status") == "DELIVERED":
                row.update(status="DELIVERED", delivered_at=delivery["delivered_at"], delivery_ref=str(delivery_path))
            else:
                target = operator_root / "mailbox/notifications" / (notification_id + ".json")
                if not target.exists():
                    _write(target, {"notification_id": notification_id, "created_at": row["created_at"],
                        "agent_id": "content-agent", "cycle": 0, "kind": "FERRO_X_POSTING",
                        "title": row["title"], "message": row["message"],
                        "evidence_refs": ["ferro-x-posting:" + row["event_id"]]})
                row.update(status="ENQUEUED", last_checked_at=now.isoformat(),
                    delivery_status=delivery.get("status", "PENDING"), last_error=delivery.get("last_error"))
            _write(path, row)
            results.append({"event_id": row["event_id"], "status": row["status"], "notification_id": notification_id})
        return results

    def observe(self, task, result, now):
        if not self.config_path.exists():
            return
        with self._lock():
            status = self.status(now)
            progress = _read(self.root / "progress.json")
            failures = 0
            def contains_status(value, expected):
                if isinstance(value, dict):
                    if value.get("status") == expected:
                        return True
                    return any(contains_status(item, expected) for item in value.values())
                if isinstance(value, list):
                    return any(contains_status(item, expected) for item in value)
                return False

            succeeded = False
            if task == "CONTENT_COGNITION":
                previous_count = int(progress.get("published", status["published"])) if progress.get("date") == status["date"] else status["published"]
                succeeded = status["published"] > previous_count or contains_status(result, "POSTED")
                failures = 0 if succeeded or not status["enabled"] or not status["remaining"] else (int(progress.get("consecutive_no_publication", 0)) + 1 if progress.get("date") == status["date"] else 1)
                left = max(60, int((_time(status["deadline"]) - now).total_seconds()))
                delay = min(3600, left // (status["remaining"] + 1)) if succeeded else 300
                _write(self.root / "progress.json", {
                    "date": status["date"], "published": status["published"],
                    "last_attempt_at": now.isoformat(), "next_attempt_at": (now + timedelta(seconds=max(60, delay))).isoformat(),
                    "consecutive_no_publication": failures, "last_result_status": result.get("status"),
                })
            errors = []
            def compact_error(value, marker):
                raw = str(value.get("detail") or value.get("message") or value.get("reason") or marker)
                lines = []
                for raw_line in raw.replace("\r", "\n").splitlines():
                    line = raw_line.strip()
                    if not line:
                        continue
                    if line.startswith("Fetching ") and "%|" in line:
                        continue
                    if "unauthenticated requests to the HF Hub" in line:
                        continue
                    lines.append(line)
                return " ".join(lines)[:700] or marker

            def scan(value):
                if isinstance(value, dict):
                    marker = str(value.get("status") or "")
                    if marker in {"ERROR", "DEGRADED", "RESOURCE_DEFERRED", "OWNER_COGNITION_PROVIDER_UNAVAILABLE",
                                  "X_PUBLICATION_CAPABILITY_UNAVAILABLE", "PUBLICATION_UNVERIFIED",
                                  "X_RESOURCE_BLOCKED", "HELD_BY_X_SAFETY", "X_OBSERVATION_FAILED"} or marker.endswith(("_UNAVAILABLE", "_FAILED", "_ERROR", "_BLOCKED")):
                        errors.append({"status": marker, "error_type": value.get("error_type"),
                            "message": compact_error(value, marker)})
                    for item in value.values():
                        scan(item)
                elif isinstance(value, list):
                    for item in value:
                        scan(item)
            scan(result)
            if errors or (status["enabled"] and status["remaining"] and failures >= 3):
                detail = json.dumps(errors[:3], ensure_ascii=False) if errors else "连续三次生产尝试没有真实发布，可能是选题或证据获取停滞。"
                key = ("unresolved:" if failures >= 3 else "blocker:") + status["date"] + ":" + sha256(detail.encode()).hexdigest()[:12]
                if task == "CONTENT_COGNITION":
                    title = "Ferro 下一轮内容生产需要协助" if failures >= 3 else "Ferro 下一轮内容生产阻塞"
                else:
                    title = "Ferro 发帖流程需要协助" if failures >= 3 else "Ferro 发帖流程阻塞"
                if task == "CONTENT_COGNITION" and status["published"]:
                    publication_note = (
                        f"此前 {status['published']} 条均已验证发布；当前错误发生在后续生产尝试，"
                        "不会撤销或重发这些帖子。\n"
                    )
                elif status["published"]:
                    publication_note = (
                        f"当前已验证发布计数仍为 {status['published']}；本次任务错误不会改变该计数。\n"
                    )
                else:
                    publication_note = ""
                quota_limited = any("CHATGPTWEBUSAGE_LIMIT" in row["message"] or "You have hit your limit" in row["message"] for row in errors)
                if quota_limited:
                    next_step = (
                        "ChatGPT 网页当前达到使用上限；原待处理请求保持不变，没有重复提交。"
                        "系统稍后再试，额度恢复后从该请求继续。"
                    )
                elif failures >= 3:
                    next_step = "多次自动尝试仍未解决，需要检查对应模型、浏览器或主机通道；待处理工作仍保留。"
                elif status["enabled"] and task == "CONTENT_COGNITION":
                    next_step = "当前轮次未完成，5分钟后重试；若持续失败再升级为需要协助。"
                else:
                    next_step = "该任务保持未完成状态并继续重试；已发布内容不受影响。"
                self._queue(key, title,
                    f"北京时间 {status['date']} 已发布 {status['published']}/{status['minimum']}。\n"
                    + publication_note
                    + f"当前失败任务：{task}。\n"
                    + f"阻塞：{detail}\n"
                    + next_step, now)

    def reconcile(self, now, *, watch_heartbeat=False):
        config = _read(self.config_path)
        if not config:
            return {"status": "DISABLED"}
        with self._lock():
            status = self.status(now)
            if config.get("notifications_enabled", True):
                since = _time(config["notification_since"])
                for post in self.publications():
                    if _time(post["published_at"]) >= since:
                        self._queue("post:" + post["post_id"], "Ferro 已发布",
                            f"形式：{post['format']}\n{post['url']}\n"
                            f"北京时间 {status['date']} 已发布 {status['published']}/{status['minimum']}。", now)
                if watch_heartbeat:
                    heartbeat = _read(self.state_root / "application/ferro-heartbeat.json")
                    if not heartbeat or now - _time(heartbeat["observed_at"]) > timedelta(minutes=5):
                        self._queue("heartbeat:" + str(heartbeat.get("observed_at")),
                            "Ferro 服务停止推进",
                            "内容服务心跳超过5分钟没有更新，独立监测仍在运行。请检查 content-agent.service 和主机状态。", now)
                    progress = _read(self.root / "progress.json")
                    due = _time(status["next_attempt_at"])
                    if status["enabled"] and status["remaining"] and now - due > timedelta(minutes=15):
                        self._queue("stalled:" + str(progress.get("last_attempt_at") or status["date"]),
                            "Ferro 每日发帖目标停滞",
                            f"今天已发布 {status['published']}/{status['minimum']}，计划生产超过15分钟仍未完成。"
                            "请检查当前模型、图片生成、X浏览器与自动修复回执。", now)
                    yesterday_end = datetime.combine(now.astimezone(ZoneInfo(config["timezone"])).date(), datetime.min.time(), ZoneInfo(config["timezone"]))
                    if status["enabled"] and _time(config["activated_at"]) < yesterday_end:
                        previous = self.status(yesterday_end - timedelta(seconds=1))
                        if previous["published"] < previous["minimum"]:
                            self._queue("shortfall:" + previous["date"], "Ferro 每日发帖目标未达成",
                                f"{previous['date']} 真实发布 {previous['published']}/{previous['minimum']}，未达目标；请处理阻塞。", now)
                deliveries = self._deliver(config, now)
            else:
                deliveries = []
            _write(self.root / "status.json", {**status, "observed_at": now.isoformat(), "deliveries": deliveries})
            return {"status": "OK", **status, "deliveries": deliveries}
