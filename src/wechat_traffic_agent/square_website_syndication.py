"""Adapt verified website research into reviewed native Square copy.

Only cognition prepares copy. The separate SquareDraftWorker posts it.
"""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from .editorial.channel_records import ChannelRecords
from .square_cognition import SquareCognitionExpressionService, square_publication_context


class SquareWebsiteSyndication:
    def __init__(self, state_root, *, service=None):
        self.state_root = Path(state_root)
        self.records = ChannelRecords(state_root)
        self.service = service or SquareCognitionExpressionService(state_root)

    def enqueue(self, *, folder, result):
        preferences = square_publication_context(self.state_root, limit=1)["preferences"]
        if not preferences.get("syndicate_website") or result.get("status") != "PUBLISHED":
            return None
        folder = Path(folder).resolve()
        if folder.parent != (self.state_root / "content/continuous/cycles").resolve():
            raise ValueError("website syndication requires its canonical research cycle")
        if not (folder / "learning_result.json").exists() or not (folder / "cognition.json").exists():
            return {"status": "NO_RESEARCH_RECEIPTS", "cycle": folder.name}
        publication = result.get("publication") or {}
        publication = {key: publication.get(key) for key in
                       ("artifact_id", "site_url", "url", "published_at", "revision")
                       if publication.get(key) is not None}
        task = {"status": "PENDING", "cycle": folder.name,
                "publication": publication,
                "created_at": datetime.now(timezone.utc).isoformat()}
        article_id = publication.get("artifact_id")
        if not article_id:
            raise ValueError("website syndication requires its article record")
        prior = self.records.get("square_preparation", article_id)
        if prior:
            return prior
        self.records.put(article_id, "square_preparation", task)
        return task

    async def run_once(self, *, runtime, now=None):
        now = now or datetime.now(timezone.utc)
        for task in self.records.all("square_preparation"):
            if task.get("status") not in {"PENDING", "PREPARATION_FAILED"}:
                continue
            if task.get("retry_at") and datetime.fromisoformat(task["retry_at"]) > now:
                continue
            folder = self.state_root / "content/continuous/cycles" / task["cycle"]
            try:
                result = await self.service.prepare(
                    runtime=runtime, folder=folder,
                    learning=json.loads((folder / "learning_result.json").read_text()),
                    cognition=json.loads((folder / "cognition.json").read_text()),
                    mind_plan=json.loads((folder / "mind_plan.json").read_text()),
                    worldview=(json.loads((folder / "worldview_update.json").read_text())
                               if (folder / "worldview_update.json").exists() else None),
                    website_publication=task.get("publication"),
                )
                receipt = {**result, "website_publication": task.get("publication")}
                state = {key: receipt.get(key) for key in
                         ("status", "cycle", "draft_id", "draft_status", "rationale")}
                self.records.put(task["article_id"], "square_preparation", state)
                return receipt
            except Exception as exc:
                task.update(status="PREPARATION_FAILED", error_type=type(exc).__name__,
                            error=str(exc)[:1000],
                            retry_at=(now + timedelta(seconds=300)).isoformat())
                self.records.put(task["article_id"], "square_preparation", task)
                return task
        return None
