"""Wakeful social task logic; the host injects runner components."""
from __future__ import annotations
from wechat_traffic_agent.publication.cycle_records import NAMES, read_cycle_record, write_cycle_record, cycle_record_exists, publication_paths

import asyncio
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any

class SocialTaskMethods:
    """Social capability handlers for the shared wakeful host."""

    def _x(self, now: datetime) -> dict[str, Any]:
        if self.x_loop is None:
            return {"task": "CHECK_X", "status": "DISABLED"}
        try:
            result = self._run_async(self.x_loop.observe(now=now))
            reconciliation = self._reconcile_x_publication_backlog(now=now)
            if (result.get("discovery") or {}).get("source","MENTIONS")=="MENTIONS":
                count = int(result.get("new_mentions") or 0)
                if count > 0:
                    result["state_event"] = self.state_events.emit(
                        "X_MENTIONS",
                        {"count": count},
                        observed_at=now,
                    )
            return {
                "task": "CHECK_X",
                "status": "OK" if str(result.get("status") or "").startswith("OK") else "DEGRADED",
                "x_social_observation": result,
                "x_publication_reconciliation": reconciliation,
            }
        except Exception as exc:  # noqa: BLE001
            return {"task": "CHECK_X", "status": "DEGRADED", "x_social_observation": self._error(exc)}

    def _reconcile_x_publication_backlog(self, *, now: datetime) -> dict[str, Any]:
        publisher = getattr(self, "x_browser_publisher", None)
        state_root = getattr(self, "state_root", None)
        if publisher is None or state_root is None:
            return {"status": "NO_PUBLISHER"}
        cycles_root = Path(state_root) / "content" / "continuous" / "cycles"
        if not cycles_root.is_dir():
            return {"status": "NO_PENDING_PUBLICATION"}
        try:
            folders = sorted(
                (path for path in cycles_root.iterdir() if path.is_dir()),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )[:200]
        except OSError:
            return {"status": "NO_PENDING_PUBLICATION"}
        for folder in folders:
            try:
                receipt = read_cycle_record(folder / "x_social_expression.json")
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if str(receipt.get("status") or "") != "PUBLICATION_UNVERIFIED":
                continue
            try:
                draft = json.loads(
                    (folder / "social_expression_drafts.json").read_text(encoding="utf-8")
                )
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if str(draft.get("status") or "") != "DRAFT_READY":
                continue
            result = self._run_async(
                publisher.after_content_cycle(cycle=folder.name, now=now)
            )
            status = str(result.get("status") or "")
            if status in {"POSTED", "ALREADY_POSTED"}:
                publication = (
                    result.get("publication")
                    if isinstance(result.get("publication"), dict)
                    else {}
                )
                normalized = {
                    "status": "POSTED" if status == "POSTED" else "ALREADY_EXPRESSED",
                    "cycle": folder.name,
                    "post_id": publication.get("post_id"),
                    "url": publication.get("url"),
                    "transport": publication.get("source"),
                }
                write_cycle_record(folder / "x_social_expression.json", normalized)
                return {
                    "status": "RECONCILED",
                    "cycle": folder.name,
                    "publication": normalized,
                }
            return {
                "status": "STILL_UNVERIFIED",
                "cycle": folder.name,
                "result": result,
            }
        return {"status": "NO_PENDING_PUBLICATION"}

    def _okx_ai_marketplace(self, now: datetime) -> dict[str, Any]:
        marketplace = getattr(self, "okx_ai_marketplace", None)
        if marketplace is None:
            return {
                "task": "OKX_AI_MARKETPLACE",
                "status": "ERROR",
                "error_type": "OKXAIMarketplaceUnavailable",
                "message": "OKX.AI marketplace component is not initialized",
            }
        return marketplace.tick(now=now)

    def _social_stage_backlog_candidate(self) -> dict[str, Any] | None:
        """Find one recent published cycle whose downstream social stage is incomplete."""
        state_root = getattr(self, "state_root", None)
        if state_root is None:
            return None
        cycles_root = (
            Path(state_root) / "content" / "continuous" / "cycles"
        )
        if not cycles_root.is_dir():
            return None
        try:
            folders = sorted(
                (path for path in cycles_root.iterdir() if path.is_dir()),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )[:200]
        except OSError:
            return None
        blocker = {}
        try:
            blocker = json.loads(
                (
                    Path(state_root)
                    / "content"
                    / "x"
                    / "publication_blocker.json"
                ).read_text(encoding="utf-8")
            )
        except (OSError, ValueError, json.JSONDecodeError):
            blocker = {}
        blocker_active = False
        provider_only_blocker = (
            isinstance(blocker, dict)
            and (
                str(blocker.get("blocker") or "") == "X_CREDITS_DEPLETED"
                or str(blocker.get("resource") or "") == "X_API_CREDITS"
            )
        )
        if (
            isinstance(blocker, dict)
            and str(blocker.get("status") or "") == "ACTIVE"
            and not provider_only_blocker
        ):
            try:
                retry_after = datetime.fromisoformat(
                    str(blocker.get("retry_after") or "").replace("Z", "+00:00")
                )
                blocker_active = retry_after.tzinfo is not None and datetime.now(timezone.utc) < retry_after.astimezone(timezone.utc)
            except ValueError:
                blocker_active = False
        published_candidate: dict[str, Any] | None = None
        for folder in folders:
            result_path = folder / "result.json"
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(result, dict):
                continue
            result_status = str(result.get("status") or "").upper()
            if result_status not in {"PUBLISHED", "HELD_BY_CHECKS", "X_EXPRESSION_READY"}:
                continue
            if (
                result_status in {"HELD_BY_CHECKS", "X_EXPRESSION_READY"}
                and getattr(getattr(self, "continuous_loop", None), "publisher", None) is not None
            ):
                continue  # First-party website content now takes priority over legacy X-only work.
            try:
                cognition = json.loads(
                    (folder / "cognition.json").read_text(encoding="utf-8")
                )
            except (OSError, ValueError, json.JSONDecodeError):
                cognition = {}
            if not bool(
                cognition.get("worth_saying")
                if isinstance(cognition, dict)
                else False
            ):
                continue
            if result_status in {"HELD_BY_CHECKS", "X_EXPRESSION_READY"}:
                from wechat_traffic_agent.x_owner_execution import owner_x_publication_request
                if result_status == "X_EXPRESSION_READY" and not owner_x_publication_request(folder):
                    continue
                x_loop = getattr(self, "x_loop", None)
                growth = getattr(x_loop, "growth_mission", None)
                binding_method = getattr(growth, "publication_binding", None)
                if not callable(binding_method) and result_status != "X_EXPRESSION_READY":
                    continue
                try:
                    binding = ({"bound": True, "status": "BOUND_X_EXPRESSION"}
                               if result_status == "X_EXPRESSION_READY" else binding_method(
                        cycle=str(result.get("cycle") or folder.name)
                    ))
                except Exception:  # noqa: BLE001
                    continue
                if (
                    not isinstance(binding, dict)
                    or not bool(binding.get("bound"))
                    or str(binding.get("status") or "") != "BOUND_X_EXPRESSION"
                ):
                    continue
                x_receipt_path = folder / "x_social_expression.json"
                try:
                    x_receipt = read_cycle_record(x_receipt_path)
                except (OSError, ValueError, json.JSONDecodeError):
                    x_receipt = {}
                x_status = str(x_receipt.get("status") or "")
                if x_status in {
                    "POSTED", "ALREADY_EXPRESSED", "SKIPPED", "RECOVERY_INVALIDATED",
                    "HELD_BY_LEARNING_QUALITY", "PUBLICATION_UNVERIFIED",
                }:
                    # Once a concrete draft has been submitted, publication
                    # verification belongs to CHECK_X. Never re-enter model
                    # expression just because the browser receipt is pending.
                    continue
                if blocker_active and x_status in {
                    "X_CREDITS_DEPLETED",
                    "X_RESOURCE_BLOCKED",
                }:
                    # A known external resource outage is not unfinished
                    # cognition.  Wait for the bounded retry window instead of
                    # burning another model turn and X API request every cycle.
                    continue
                return {
                    "cycle": str(result.get("cycle") or folder.name),
                    "result": result,
                    "reason": "MISSING_X_MISSION_EXPRESSION",
                }
            social_path = folder / "social_expression_drafts.json"
            try:
                social = json.loads(social_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                social = {}
            social_status = (
                str(social.get("status") or "")
                if isinstance(social, dict)
                else ""
            )
            if social_status in {"DRAFT_READY", "SKIPPED", "RECOVERY_INVALIDATED"}:
                continue
            if published_candidate is None:
                published_candidate = {
                    "cycle": str(result.get("cycle") or folder.name),
                    "result": result,
                    "reason": (
                        "MISSING_SOCIAL_DRAFT"
                        if not social_path.exists()
                        else f"SOCIAL_DRAFT_{social_status or 'INVALID'}"
                    ),
                }
            # Keep scanning: a mission-bound X-only expression backlog is a
            # stalled external commitment and takes precedence over generic
            # social drafting.
        return published_candidate

    def _run_social_stage(
        self,
        *,
        rows: dict[str, Any],
        continuous: dict[str, Any],
        now: datetime,
    ) -> None:
        """Run idempotent downstream social work for an already-published cycle."""
        cycle = str(continuous.get("cycle") or "")
        if continuous.get("publication_surface") == "BINANCE_SQUARE":
            from wechat_traffic_agent.platforms.binance_square_editorial import SquareDraftStore
            identity = continuous.get("draft_id")
            if identity:
                try:
                    draft = SquareDraftStore(self.state_root).get(identity)
                    rows["binance_square_expression"] = {
                        "status": "POSTED" if draft['status'] == 'SUCCESS' else 'PUBLICATION_PENDING'
                            if draft['status'] == 'REVIEWED' else draft['status'],
                        "draft_id": identity, "draft_status": draft['status'],
                        "url": draft.get('url'), "post_id": draft.get('post_id'),
                    }
                except (OSError, ValueError, KeyError) as exc:
                    rows["binance_square_expression"] = self._error(exc)
            return
        # Only publish downstream social extracts from an audited website
        # article. Old owner X-only episodes may still be in the backlog, but
        # they must not preempt new first-party publishing.
        site_publisher = getattr(getattr(self, "continuous_loop", None), "publisher", None)
        if site_publisher is not None:
            folder = Path(self.state_root) / "content" / "continuous" / "cycles" / cycle
            publication_path = folder / "publication.json"
            if not cycle_record_exists(publication_path):
                rows["x_syndication"] = {
                    "status": "WEBSITE_PUBLICATION_REQUIRED", "cycle": cycle,
                }
                return
            try:
                site_receipt = read_cycle_record(publication_path)
            except (ValueError, OSError) as exc:
                rows["x_syndication"] = self._error(exc)
                rows["status"] = "DEGRADED"
                return
            if not (
                isinstance(site_receipt, dict)
                and isinstance(site_receipt.get("outcome_attribution"), dict)
                and site_receipt["outcome_attribution"].get("public_route")
                and (site_receipt.get("audit") or {}).get("passed") is True
            ):
                rows["x_syndication"] = {"status": "WEBSITE_PUBLICATION_UNVERIFIED", "cycle": cycle}
                return
            if hasattr(site_publisher, "config"):
                route = str(site_receipt["outcome_attribution"]["public_route"])
                site_url = str(site_receipt.get("site_url") or (
                    site_publisher.config.base_url.rstrip("/") + route
                ))
                rows["website_publication"] = {
                    "status": "PUBLISHED", "cycle": cycle,
                    "url": site_url, "artifact_id": site_receipt.get("artifact_id"),
                }
                try:
                    from wechat_traffic_agent.daily_posting_goal import DailyPostingGoal
                    title = ""
                    draft = continuous.get("draft_path")
                    if draft and Path(draft).is_file():
                        content_draft = json.loads(Path(draft).read_text(encoding="utf-8"))
                        title = str((content_draft.get("artifact") or {}).get("title") or "")
                    rows["website_notification"] = DailyPostingGoal(self.state_root).notify_website_publication(
                        artifact_id=site_receipt.get("artifact_id"),
                        content_sha256=site_receipt["outcome_attribution"].get("published_content_sha256"),
                        title=title or site_url.rsplit("/", 2)[-2],
                        url=site_url, now=now,
                    )
                except (OSError, ValueError, KeyError) as exc:
                    rows["website_notification"] = self._error(exc)
            if site_receipt.get("publication_event_id"):
                # X is a separate durable outbox; cognition never waits on Jev.
                rows["x_syndication"] = {
                    "status": "QUEUED",
                    "event_id": site_receipt["publication_event_id"],
                    "cycle": cycle,
                }
                return
        direct_x = continuous.get("status") == "X_EXPRESSION_READY"
        social_loop = getattr(self, "social_expression_loop", None)
        if social_loop is not None and not direct_x:
            try:
                rows["social_expression_drafts"] = self._run_async(
                    social_loop.after_content_cycle(result=continuous, now=now)
                )
            except Exception as exc:  # noqa: BLE001
                rows["social_expression_drafts"] = self._social_stage_error(
                    exc, stage="social_expression_drafts", continuous=continuous, now=now
                )
                rows["status"] = "DEGRADED"
        for name, publisher in (
            ("x_browser_publication", getattr(self, "x_browser_publisher", None)),
            ("okx_orbit_publication", getattr(self, "okx_orbit_publisher", None)),
        ):
            if publisher is None or direct_x:
                continue
            # Original short posts use the Jev autonomy lane and its independent safety decision.
            if name == "x_browser_publication" and getattr(self, "x_loop", None) is not None:
                folder = Path(self.state_root) / "content" / "continuous" / "cycles" / str(continuous.get("cycle") or "")
                try:
                    draft_payload = json.loads((folder / "social_expression_drafts.json").read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    draft_payload = {}
                if not isinstance(draft_payload, dict):
                    draft_payload = {}
                strategy = draft_payload.get("x_strategy")
                if not isinstance(strategy, dict):
                    strategy = {}
                if (
                    draft_payload.get("status") == "DRAFT_READY"
                    and strategy.get("interaction_mode", "ORIGINAL") == "ORIGINAL"
                    and strategy.get("x_format") == "SHORT_POST"
                ):
                    rows[name] = {"status": "DELEGATED_TO_X_AUTONOMY", "cycle": str(continuous.get("cycle") or "")}
                    continue
            try:
                rows[name] = self._run_async(
                    publisher.after_content_cycle(
                        cycle=str(continuous.get("cycle") or ""),
                        now=now,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                rows[name] = self._social_stage_error(
                    exc, stage=name, continuous=continuous, now=now
                )
                rows["status"] = "DEGRADED"
        x_loop = getattr(self, "x_loop", None)
        if x_loop is not None:
            try:
                x_result = self._run_async(
                    x_loop.after_content_cycle(result=continuous, now=now)
                )
                rows["x_social_expression"] = x_result
                state_root = getattr(self, "state_root", None)
                cycle = str(continuous.get("cycle") or "").strip()
                if state_root is not None and cycle and isinstance(x_result, dict):
                    write_cycle_record(
                        Path(state_root) / "content/continuous/cycles" / cycle / "x_social_expression.json",
                        x_result,
                    )
                    if str(x_result.get("status") or "") == "PUBLICATION_UNVERIFIED":
                        rows["x_publication_reconciliation"] = self.task_scheduler.schedule(
                            "CHECK_X",
                            now + timedelta(seconds=5),
                            reason="reconcile unverified X publication without rerunning cognition",
                            relevant_refs=(cycle,),
                        )
            except Exception as exc:  # noqa: BLE001
                rows["x_social_expression"] = self._social_stage_error(
                    exc, stage="x_social_expression", continuous=continuous, now=now
                )
                rows["status"] = "DEGRADED"

    def _social_stage_error(self, exc: Exception, *, stage: str, continuous: dict[str, Any], now: datetime) -> dict[str, Any]:
        """Keep the final browser cause beside the failed cycle for recovery."""
        from wechat_traffic_agent.llm_audit import _redact

        error = {
            **self._error(exc),
            "error_detail": _redact(str(exc)),
            "observed_at": now.isoformat(),
            "cycle": str(continuous.get("cycle") or ""),
        }
        if isinstance(exc, ValueError) and str(exc) == "model call interrupted without receipt; skip this cycle":
            error.update(status="RECOVERY_INVALIDATED", reason="INCOMPLETE_MODEL_RECEIPT",
                         recovery_action="START_NEW_CYCLE_WITH_PRESERVED_EVIDENCE")
        state_root = getattr(self, "state_root", None)
        if state_root is not None and error["cycle"]:
            path = Path(state_root) / "content" / "continuous" / "cycles" / error["cycle"] / f"{stage}.error.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(error, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
            tmp.replace(path)
            if error.get("status") == "RECOVERY_INVALIDATED":
                stage_path = path.with_name(f"{stage}.json")
                tmp = stage_path.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(error, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
                tmp.replace(stage_path)
        return error

    def _recover_social_stage_backlog(
        self,
        *,
        rows: dict[str, Any],
        now: datetime,
        x_mission_only: bool = False,
    ) -> dict[str, Any] | None:
        backlog = self._social_stage_backlog_candidate()
        if backlog is None:
            return None
        if (
            x_mission_only
            and str(backlog.get("reason") or "") != "MISSING_X_MISSION_EXPRESSION"
        ):
            return None
        replay_rows: dict[str, Any] = {"status": "OK"}
        self._run_social_stage(
            rows=replay_rows,
            continuous=dict(backlog["result"]),
            now=now,
        )
        recovery = {
            "status": "REPLAYED",
            "cycle": str(backlog["cycle"]),
            "reason": str(backlog["reason"]),
            "results": replay_rows,
        }
        rows["social_stage_backlog_recovery"] = recovery
        return recovery
