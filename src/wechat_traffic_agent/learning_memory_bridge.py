"""Ferro-specific wiring from learning receipts into the reusable memory capability.

This is deliberately not part of Agent Runtime's generic Memory API.  The
receipt schemas (cycle/topic/facts/application assessment) belong to Ferro's
current learning product.  This bridge translates those domain records into
generic durable memories while MemoryService stays provider-oriented.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent_runtime.memory.retrieval import fingerprint

if TYPE_CHECKING:
    from .memory_service import MemoryService


class LearningMemoryBridge:
    def __init__(self, service: "MemoryService") -> None:
        self.service = service

    def ingest_research(
        self,
        row: dict[str, Any],
        *,
        sync: bool = True,
    ) -> None:
        for index, fact in enumerate(row.get("facts") or []):
            memory_id = (
                "research-fact:"
                + fingerprint(
                    [row["cycle"], index, fact]
                )[:28]
            )
            if self.service.store.get_memory(memory_id) is not None:
                continue

            source_body = next(
                (
                    str(
                        source.get("evidence_text")
                        or source.get("summary")
                        or ""
                    )
                    for source in row.get("sources", [])
                    if source["evidence_id"]
                    == fact["evidence_id"]
                ),
                None,
            )
            current = self.service.sources.current(
                fact["evidence_id"]
            )
            status = "ACTIVE"
            if current:
                source = (
                    self.service.sources.read_original(
                        fact["evidence_id"]
                    )
                    or {}
                )
                active_body = str(
                    source.get("evidence_text")
                    or source.get("summary")
                    or ""
                )
                if (
                    source_body is not None
                    and fingerprint(source_body)
                    != current["source_version"]
                ) or fact["quote"] not in active_body:
                    status = "DISPUTED"

            self.service.store.append_memory(
                {
                    "memory_id": memory_id,
                    "memory_type": "SEMANTIC",
                    "kind": "RESEARCH_OBSERVATION",
                    "status": status,
                    "text": fact["observation"],
                    "created_at": (
                        row.get("observed_at")
                        or datetime.now(
                            timezone.utc
                        ).isoformat()
                    ),
                    "source_type": "RESEARCH_RECEIPT",
                    "source_ref": row["cycle"],
                    "topics": [row.get("topic", "")],
                    "evidence_refs": [
                        fact["evidence_id"]
                    ],
                    "payload": {
                        "cycle": row["cycle"],
                        "fact": fact,
                        "source_urls": row.get(
                            "source_urls",
                            [],
                        ),
                        "next_question": row.get(
                            "next_question",
                            "",
                        ),
                        "derived_from": [
                            "source:"
                            + fact["evidence_id"]
                        ],
                    },
                }
            )
        answer = str(row.get("answer") or "").strip()
        facts = row.get("facts") or []
        understanding_id = "research-understanding:" + str(row["cycle"])
        if answer and facts and self.service.store.get_memory(understanding_id) is None:
            # Preserve the explanation, not only its isolated observations.
            # It remains a qualified synthesis linked to the same source revisions.
            fact_memories = [self.service.store.get_memory(
                "research-fact:" + fingerprint([row["cycle"], index, fact])[:28]
            ) for index, fact in enumerate(facts)]
            status = "ACTIVE" if all(
                memory and memory["status"] == "ACTIVE" for memory in fact_memories
            ) else "DISPUTED"
            self.service.store.append_memory({
                "memory_id": understanding_id,
                "memory_type": "SEMANTIC",
                "kind": "RESEARCH_UNDERSTANDING",
                "status": status,
                "text": answer + ("\n\nEvidence-backed explanation:\n" + "\n".join(
                    step["explanation"] for step in row.get("reasoning_steps", [])
                ) if row.get("reasoning_steps") else ""),
                "created_at": row.get("observed_at") or datetime.now(timezone.utc).isoformat(),
                "source_type": "RESEARCH_RECEIPT",
                "source_ref": row["cycle"],
                "topics": [row.get("topic", "")],
                "evidence_refs": list(dict.fromkeys(fact["evidence_id"] for fact in facts)),
                "payload": {
                    "cycle": row["cycle"], "focus": row.get("focus"),
                    "question_status": row.get("question_status", "PARTIAL"),
                    "reasoning_steps": row.get("reasoning_steps", []),
                    "epistemic_status": "SOURCE_GROUNDED_SYNTHESIS",
                    "fact_memory_ids": [memory["memory_id"] for memory in fact_memories if memory],
                    "receipt_ref": "content/continuous/cycles/" + str(row["cycle"]) + "/learning_result.json",
                    "derived_from": list(dict.fromkeys("source:" + fact["evidence_id"] for fact in facts)),
                },
            })
        if sync:
            self.service.sync()

    def ingest_application(
        self,
        row: dict[str, Any],
    ) -> None:
        memory_id = (
            "learning-application:"
            + str(row["cycle"])
        )
        assessment = row["assessment"]
        if (
            self.service.store.get_memory(memory_id)
            is None
        ):
            self.service.store.append_memory(
                {
                    "memory_id": memory_id,
                    "memory_type": "EPISODIC",
                    "kind": "LEARNING_APPLICATION_OUTCOME",
                    "text": (
                        row["application"]["prediction"]
                        + " Outcome "
                        + assessment["status"]
                        + ": "
                        + assessment["rationale"]
                        + " Correction "
                        + assessment.get(
                            "correction",
                            "",
                        )
                    ),
                    "importance": 0.9,
                    "confidence": 1.0,
                    "topics": [row["topic"]],
                    "source_type": "APPLICATION_RECEIPT",
                    "source_ref": row["cycle"],
                    "created_at": (
                        row.get("checked_at")
                        or datetime.now(
                            timezone.utc
                        ).isoformat()
                    ),
                    "evidence_refs": [
                        item["evidence_id"]
                        for item in row.get(
                            "evidence",
                            [],
                        )
                    ],
                    "payload": {
                        **row,
                        "derived_from": [
                            "source:"
                            + item["evidence_id"]
                            for item in row.get(
                                "evidence",
                                [],
                            )
                        ],
                    },
                }
            )
        self.service.sync()

    def migrate_receipts(
        self,
        state_root: str | Path,
    ) -> None:
        with self.service.store._connect() as db:
            value = db.execute(
                "SELECT value FROM memory_service_meta "
                "WHERE key='receipt_imports'"
            ).fetchone()
        imported = (
            json.loads(value[0])
            if value
            else {}
        )
        root = (
            Path(state_root)
            / "content"
            / "continuous"
            / "cycles"
        )
        changed = False

        for path in sorted(
            root.glob("*/learning_result.json")
        ):
            stamp = path.stat().st_mtime_ns
            key = str(path)
            if imported.get(key) == stamp:
                continue
            row = json.loads(
                path.read_text(
                    encoding="utf-8"
                )
            )
            for source in row.get("sources", []):
                self.service.ingest_source(
                    source,
                    topic=row.get("topic", ""),
                    observed_at=row.get(
                        "observed_at",
                        "",
                    ),
                )
            self.ingest_research(
                row,
                sync=False,
            )
            imported[key] = stamp
            changed = True

        for path in sorted(
            root.glob(
                "*/learning_application_result.json"
            )
        ):
            stamp = path.stat().st_mtime_ns
            key = str(path)
            if imported.get(key) == stamp:
                continue
            self.ingest_application(
                json.loads(
                    path.read_text(
                        encoding="utf-8"
                    )
                )
            )
            imported[key] = stamp
            changed = True

        if changed:
            self.service.sync()
            with self.service.store._connect() as db:
                db.execute(
                    "INSERT OR REPLACE INTO "
                    "memory_service_meta VALUES("
                    "'receipt_imports',?)",
                    (json.dumps(imported),),
                )


__all__ = ["LearningMemoryBridge"]
