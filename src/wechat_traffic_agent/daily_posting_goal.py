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
        config = _read(self.config_path)
        if config.get("target_channel") == "binance_square":
            rows = {}
            for path in (self.state_root / "square_editorial/drafts").glob("*.json"):
                row = _read(path)
                post_id = str(row.get("post_id") or "")
                url = str(row.get("url") or "")
                if (row.get("status") == "SUCCESS" and post_id.isdigit()
                        and url == "https://www.binance.com/square/post/" + post_id
                        and row.get("published_at")):
                    rows[post_id] = {"post_id": post_id, "url": url,
                        "cycle": (row.get("research_provenance") or {}).get("learning_cycle"),
                        "format": "SQUARE_SHORT_POST", "published_at": row["published_at"]}
            return sorted(rows.values(), key=lambda row: row["published_at"])
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
        from .publication.event_store import PublicationEventStore
        outbox = PublicationEventStore(self.state_root)
        square_goal = config.get("target_channel") == "binance_square"
        website_first = not square_goal and any(outbox.events.glob("*.json"))
        attempted = outbox.attempt_count(local_date=local.date(), timezone_info=zone) if website_first else 0
        minimum = int(config["minimum_original_posts"])
        measured = attempted if website_first else len(posts)
        progress = _read(self.root / "progress.json")
        first_attempt = max(_time(config["activated_at"]), datetime.combine(local.date(), datetime.min.time(), zone))
        next_attempt = (progress.get("next_attempt_at") if progress.get("date") == date else None) or first_attempt.isoformat()
        return {
            "enabled": bool(config["enabled"]), "temporary": True, "timezone": config["timezone"],
            "date": date, "minimum": minimum, "published": len(posts),
            "target_channel": config.get("target_channel", "website"),
            "attempted": attempted, "metric": ("VERIFIED_SQUARE_POSTS" if square_goal else "DISTRIBUTION_ATTEMPTS" if website_first else "VERIFIED_X_POSTS"),
            "remaining": max(0, minimum - measured), "deadline": midnight.isoformat(),
            "next_attempt_at": next_attempt, "goal_id": "ferro-daily-post:" + date,
            "notifications_enabled": bool(config.get("notifications_enabled", True)),
        }

    def owner_goal(self, now):
        status = self.status(now)
        if not status["enabled"] or not status["remaining"]:
            return None
        if status.get("target_channel") == "binance_square":
            return {"goal_id": status["goal_id"], "origin": "USER", "priority": 1.0,
                "created_at": now.isoformat(), "daily_posting_contract": status,
                "description": (
                    f"北京时间 {status['date']} 结束前，币安广场每日目标 {status['minimum']} 篇，"
                    f"已验证成功 {status['published']} 篇，剩余 {status['remaining']} 篇。"
                    "以内容挖矿为目标，优先将真实研究发现写成独立可读的中文原生广场帖子。"
                    "网站研究与同步继续，但官网分发尝试、X帖、草稿及失败不抵扣币安目标。"
                    "增加不同读者问题的覆盖：及时观察、事件机制、概念辨析、对照解释与判断更新。"
                    "同一研究可支持多个实质不同的问题，每篇独立审稿；只换标题或数字不算新内容。"
                    "遵循内容挖矿要求，使用有依据的相关币种标签；不以催促交易、夸大收益凑数。"
                    "某一问题无证据时继续搜索或换问题；收益以实际返佣反馈判断，不保证百篇即有佣金。"),
                "reason": "OWNER_REQUIRED_DAILY_SQUARE_TARGET"}
        return {
            "goal_id": status["goal_id"], "origin": "USER", "priority": 1.0,
            "created_at": now.isoformat(), "daily_posting_contract": status,
            "description": (
                f"临时官网分发目标：北京时间 {status['date']} 结束前，完成至少 {status['minimum']} 次官网内容分发尝试。"
                f"已验证 X 发布 {status['published']} 条，还差 {status['remaining']} 条。"
                "用户最新要求：Ferro 的自有网站是首发渠道和权威内容库存；先完成网站内容研究、"
                "审核、公开发布与持续维护，再将网站内容提炼成带原文链接的 X 帖子。"
                "X 仅是推广同步渠道，X 浏览器故障不能阻塞官网生产或维护。"
                "只计由真实官网 PublicationEvent 派生的分发尝试；X 成功链接另行统计。"
                "自由选择加密货币、AI、金融及交叉方向，充分使用搜索，按证据质量选择网站文章深度。"
                "网站上线后派生 X 短帖或长帖，同一证据不重复灌水。"
                "网站 URL 及成功 X 同步链接都应通过现有企业微信通知；阻塞需明确记录。"
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
                        "agent_id": "content-agent", "cycle": 0, "kind": ("FERRO_WEBSITE" if row["key"].startswith("website:") else "FERRO_X_POSTING"),
                        "title": row["title"], "message": row["message"],
                        "evidence_refs": [("ferro-website:" if row["key"].startswith("website:") else "ferro-x-posting:") + row["event_id"]]})
                row.update(status="ENQUEUED", last_checked_at=now.isoformat(),
                    delivery_status=delivery.get("status", "PENDING"), last_error=delivery.get("last_error"))
            _write(path, row)
            results.append({"event_id": row["event_id"], "status": row["status"], "notification_id": notification_id})
        return results

    def notify_website_publication(self, *, artifact_id, content_sha256, title, url, now):
        """Enqueue each audited first-party article once, independently of X."""
        config = _read(self.config_path)
        if not config or not config.get("notifications_enabled", True):
            return {"status": "NOTIFICATIONS_DISABLED"}
        if not (str(artifact_id).strip() and str(content_sha256).strip()
                and str(title).strip() and str(url).startswith("https://")):
            raise ValueError("website notice requires a verified publication identity and URL")
        key = "website:" + str(artifact_id) + ":" + str(content_sha256)
        with self._lock():
            self._queue(key, "Ferro 官网已发布",
                f"{title}\n{url}\n官网首发已完成；X 同步为后续独立任务。", now)
            deliveries = self._deliver(config, now)
        return {"status": "ENQUEUED", "key": key, "deliveries": deliveries}

    def notify_distribution_result(self, *, task, now):
        """Deliver the real X URL or a distinct, explicit failed attempt to WeCom."""
        config = _read(self.config_path)
        if not config or not config.get("notifications_enabled", True):
            return {"status": "NOTIFICATIONS_DISABLED"}
        status = str(task.status)
        if status not in {"SUCCESS", "RETRY_PENDING", "FAILED_PERMANENT"}:
            return {"status": "NOT_TERMINAL_ATTEMPT"}
        key = "distribution:" + task.distribution_id + ":" + status + ":" + str(task.attempts)
        if status == "SUCCESS":
            if not str(task.published_url or "").startswith("https://x.com/"):
                raise ValueError("successful X distribution requires a verified X URL")
            title = "Ferro 官网内容已同步到 X"
            message = ("PublicationEvent: " + task.event_id + "\\n"
                       + task.published_url + "\\n官网首发保持独立。")
        else:
            title = "Ferro X 分发失败，官网发布不受影响"
            message = ("PublicationEvent: " + task.event_id + "\\n"
                       + "状态: " + status + "; attempt: " + str(task.attempts)
                       + "\\n错误: " + str(task.last_error or "unverified")
                       + "\\n官网仍是已发布的事实源。")
        with self._lock():
            self._queue(key, title, message, now)
            deliveries = self._deliver(config, now)
        return {"status": "ENQUEUED", "key": key, "deliveries": deliveries}

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
                count = status["attempted"] if status["metric"] == "DISTRIBUTION_ATTEMPTS" else status["published"]
                previous_count = int(progress.get("count", count)) if progress.get("date") == status["date"] else count
                succeeded = count > previous_count or contains_status(result, "POSTED")
                failures = 0 if succeeded or not status["enabled"] or not status["remaining"] else (int(progress.get("consecutive_no_publication", 0)) + 1 if progress.get("date") == status["date"] else 1)
                left = max(60, int((_time(status["deadline"]) - now).total_seconds()))
                delay = min(3600, left // (status["remaining"] + 1)) if succeeded else 300
                _write(self.root / "progress.json", {
                    "date": status["date"], "published": status["published"], "attempted": status["attempted"], "count": count,
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
                                  "X_RESOURCE_BLOCKED", "HELD_BY_X_SAFETY", "X_OBSERVATION_FAILED",
                                  "AUTH_REQUIRED", "LOGIN_REQUIRED", "HUMAN_REQUIRED"} or marker.endswith(("_UNAVAILABLE", "_FAILED", "_ERROR", "_BLOCKED")):
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
                # A traceback, changing browser target, or nested diagnostic is
                # not a new operator incident. Notify once on first occurrence,
                # then at most once when the same incident becomes persistent.
                statuses = {item["status"] for item in errors}
                if statuses & {"CHATGPT_UNAVAILABLE", "OWNER_COGNITION_PROVIDER_UNAVAILABLE",
                               "X_OBSERVATION_FAILED"}:
                    incident = "CHATGPT_BROWSER"
                elif statuses & {"PUBLICATION_UNVERIFIED", "X_PUBLICATION_CAPABILITY_UNAVAILABLE"}:
                    incident = "X_PUBLICATION"
                elif statuses & {"X_RESOURCE_BLOCKED", "RESOURCE_DEFERRED"}:
                    incident = "RESOURCE"
                elif statuses & {"HELD_BY_X_SAFETY"}:
                    incident = "X_SAFETY"
                elif statuses:
                    incident = sorted(statuses - {"DEGRADED"} or statuses)[0][:80]
                else:
                    incident = "NO_VERIFIED_PUBLICATION"
                phase = "persistent" if failures >= 3 else "initial"
                key = ":".join(("incident", status["date"], task, incident, phase))
                manual_required = any(
                    item["status"] in {"AUTH_REQUIRED", "LOGIN_REQUIRED", "HUMAN_REQUIRED"}
                    or "manual login required" in item["message"].lower()
                    for item in errors
                )
                if task == "CONTENT_COGNITION":
                    title = ("Ferro 下一轮内容生产需要协助" if manual_required
                             else "Ferro 下一轮内容生产自动恢复中" if failures >= 3
                             else "Ferro 下一轮内容生产阻塞")
                else:
                    title = ("Ferro 发帖流程需要协助" if manual_required
                             else "Ferro 发帖流程自动恢复中" if failures >= 3
                             else "Ferro 发帖流程阻塞")
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
                    next_step = ("需要人工登录或授权以解除阻塞。" if manual_required else
                                 "自动重试仍未解决，已记录故障类别并继续排查；无需重复人工介入。")
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
                        self._queue("heartbeat:" + status["date"],
                            "Ferro 服务停止推进",
                            "内容服务心跳超过5分钟没有更新，独立监测仍在运行。请检查 content-agent.service 和主机状态。", now)
                    progress = _read(self.root / "progress.json")
                    due = _time(status["next_attempt_at"])
                    if status["enabled"] and status["remaining"] and now - due > timedelta(minutes=15):
                        self._queue("stalled:" + status["date"],
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
