"""Evidence-to-action transfer loop for Ferro's existing procedural memory.

Uses the actual checked/published content cycle, not self-assessed learning.
Assignments are precommitted and replayable, results are immutable, and a
baseline holdout permits conservative comparisons without extra model calls.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3


ACTION = "content-create-verify-publish"
DEFAULT_STEPS = (
    "select evidence", "draft content", "run evidence and safety checks",
    "publish only after checks pass",
)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(value):
    return sha256(_json(value).encode("utf-8")).hexdigest()[:24]


class LearningTransfer:
    def __init__(self, state_root: Path):
        self.path = Path(state_root) / "content" / "learning" / "transfer.sqlite3"

    @contextmanager
    def _db(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=15)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA busy_timeout=15000")
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS trials (
                cycle TEXT PRIMARY KEY, action TEXT NOT NULL, topic TEXT NOT NULL,
                candidate_key TEXT NOT NULL, arm TEXT NOT NULL,
                selected_json TEXT NOT NULL, assigned_at TEXT NOT NULL,
                outcome TEXT, success INTEGER, evidence_json TEXT, settled_at TEXT
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS transfer_compare ON trials(action,candidate_key,topic,arm,success)")
            db.execute("""CREATE TABLE IF NOT EXISTS world_outcomes (
                outcome_id TEXT NOT NULL, revision_id TEXT NOT NULL,
                cycle TEXT NOT NULL, pageviews_24h REAL NOT NULL,
                observed_at TEXT NOT NULL, source_refs_json TEXT NOT NULL,
                PRIMARY KEY (outcome_id, revision_id),
                FOREIGN KEY(cycle) REFERENCES trials(cycle)
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS world_cycle ON world_outcomes(cycle)")
            with db:
                yield db

    def _comparison(self, db, action, candidate_key, topic):
        rows = db.execute(
            "SELECT arm,success FROM trials WHERE action=? AND candidate_key=? "
            "AND topic=? AND success IS NOT NULL",
            (action, candidate_key, topic),
        ).fetchall()
        by_arm = {
            arm: [int(r["success"]) for r in rows if r["arm"] == arm]
            for arm in ("BASELINE", "LEARNED")
        }
        n0, n1 = map(lambda arm: len(by_arm[arm]), ("BASELINE", "LEARNED"))
        summary = {"baseline": n0, "learned": n1, "eligible_comparisons": min(n0, n1)}
        if n0 < 6 or n1 < 6:
            return {**summary, "decision": "INSUFFICIENT_EVIDENCE"}
        lift = sum(by_arm["LEARNED"]) / n1 - sum(by_arm["BASELINE"]) / n0
        decision = "PREFER_LEARNED" if lift > .15 else "PREFER_BASELINE" if lift < -.15 else "CONTINUE_TRIAL"
        return {**summary, "decision": decision, "observed_success_delta": round(lift, 5),
                "causal_improvement_proven": False}

    @staticmethod
    def _candidate(candidate):
        if not isinstance(candidate, dict) or not candidate.get("variant_id"):
            return None
        if not candidate.get("steps"):
            return None
        # A prose-only or DIRECT variant changes no executed behavior.
        # Only an implemented policy with a live execution hook may count
        # as the learned trial arm.
        if candidate.get("policy_action") != "RETRY_ONCE_SAFE":
            return None
        return {
            "procedure_id": str(candidate.get("procedure_id") or ""),
            "variant_id": str(candidate["variant_id"]),
            "steps": list(candidate["steps"])[:12],
            "policy_action": str(candidate.get("policy_action") or "DIRECT"),
        }

    def assign(self, *, cycle: str, topic: str, candidate: dict | None) -> dict:
        """Record the selected variant *before* action, or restore it on retry."""
        chosen = self._candidate(candidate)
        candidate_key = _fingerprint(chosen) if chosen else "no-qualified-skill"
        topic = str(topic or "general")[:160]
        cycle = str(cycle)
        with self._db() as db:
            existing = db.execute("SELECT * FROM trials WHERE cycle=?", (cycle,)).fetchone()
            if existing:
                if existing["topic"] != topic or existing["action"] != ACTION:
                    raise ValueError("learning assignment cannot change its task identity")
                return json.loads(existing["selected_json"])
            comparison = self._comparison(db, ACTION, candidate_key, topic)
            # Deterministic pseudo-random holdout precommitted from cycle ID.
            # Split on task identity rather than outcome. Keep 20% control
            # after sufficient learned-arm evidence; other arms remain 50/50.
            draw = int(_fingerprint([cycle, ACTION])[:8], 16) % 100
            decision = comparison["decision"]
            learned = bool(chosen) and (
                draw >= (20 if decision == "PREFER_LEARNED" else
                         90 if decision == "PREFER_BASELINE" else 50)
            )
            arm = "LEARNED" if learned else "BASELINE"
            selected = {
                "cycle": cycle, "topic": topic, "action": ACTION,
                "arm": arm, "candidate_key": candidate_key,
                "procedure_id": (chosen or {}).get("procedure_id", ""),
                "variant_id": (chosen or {}).get("variant_id", "") if learned else "",
                "steps": chosen["steps"] if learned else list(DEFAULT_STEPS),
                "policy_action": chosen["policy_action"] if learned else "DIRECT",
                "comparison_at_assignment": comparison,
            }
            db.execute(
                "INSERT INTO trials (cycle,action,topic,candidate_key,arm,selected_json,assigned_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (cycle, ACTION, topic, candidate_key, arm, _json(selected),
                 datetime.now(timezone.utc).isoformat()),
            )
            return selected

    def settle(self, *, cycle: str, outcome: str, evidence_refs: list[str],
               publication_receipt: dict | None = None, verified_checks: bool = False) -> dict:
        """Admit measured result only from verifiable check/publication receipts."""
        with self._db() as db:
            row = db.execute("SELECT * FROM trials WHERE cycle=?", (str(cycle),)).fetchone()
            if row is None:
                raise ValueError("task was not precommitted before execution")
            refs = sorted(set(str(x) for x in evidence_refs if str(x).strip()))
            published = (outcome == "PUBLISHED" and isinstance(publication_receipt, dict)
                         and bool(publication_receipt.get("outcome_attribution", {}).get("public_route")))
            success = (1 if published else 0 if verified_checks and outcome in
                       {"HELD_BY_CHECKS", "HELD_BY_EVIDENCE_FLOOR"} else None)
            measurement = {"outcome": outcome, "success": success, "evidence_refs": refs,
                           "publication_route": (publication_receipt or {}).get("outcome_attribution", {}).get("public_route")
                           if published else None, "verified_checks": bool(verified_checks)}
            if row["settled_at"] is not None:
                if row["outcome"] != outcome or json.loads(row["evidence_json"]) != measurement:
                    raise ValueError("settled learning result cannot be overwritten")
                return {"status": "ALREADY_SETTLED", "measurement": measurement}
            db.execute(
                "UPDATE trials SET outcome=?, success=?, evidence_json=?, settled_at=? WHERE cycle=?",
                (outcome, success, _json(measurement), datetime.now(timezone.utc).isoformat(), str(cycle)),
            )
            comparison = self._comparison(db, row["action"], row["candidate_key"], row["topic"])
        return {"status": "MEASURED" if success is not None else "UNVERIFIED",
                "arm": row["arm"], "measurement": measurement, "comparison": comparison}

    def comparison(self, *, topic: str, candidate: dict | None) -> dict:
        chosen = self._candidate(candidate)
        if not chosen:
            return {"decision": "NO_QUALIFIED_SKILL"}
        with self._db() as db:
            return self._comparison(db, ACTION, _fingerprint(chosen), str(topic or "general")[:160])

    def observe_world(self, outcomes: list[dict]) -> dict:
        """Attach mature 24h nginx observations to previously run real trials.

        A pageview observation is a measured external change, NOT proof
        that the selected process arm caused it.
        """
        recorded = 0
        with self._db() as db:
            for row in outcomes:
                cycle = str(row.get("cycle") or "")
                if not cycle or not row.get("outcome_id") or not row.get("evidence_refs"):
                    continue
                try:
                    pageviews = float(row["pageviews_24h"])
                except (TypeError, ValueError, KeyError):
                    continue
                if not 0 <= pageviews < 1e12:
                    continue
                trial = db.execute(
                    "SELECT outcome FROM trials WHERE cycle=? AND settled_at IS NOT NULL", (cycle,)
                ).fetchone()
                if trial is None or trial["outcome"] != "PUBLISHED":
                    continue
                source_refs = _json(sorted(set(map(str, row["evidence_refs"]))))
                version = _fingerprint([pageviews, source_refs])
                fields = (str(row["outcome_id"]), version, cycle, pageviews,
                          str(row.get("observed_at") or ""), source_refs)
                originals = db.execute(
                    "SELECT revision_id,cycle,pageviews_24h,source_refs_json FROM world_outcomes "
                    "WHERE outcome_id=?", (fields[0],)
                ).fetchall()
                for previous in originals:
                    if previous["cycle"] != cycle:
                        raise ValueError("external outcome identity changed its cycle")
                    if previous["source_refs_json"] == source_refs and previous["pageviews_24h"] != pageviews:
                        raise ValueError("revised outcome must use a new evidence identity")
                if any(old["revision_id"] == version for old in originals):
                    continue
                db.execute(
                    "INSERT INTO world_outcomes VALUES (?,?,?,?,?,?)", fields
                )
                recorded += 1
        return {"attached": recorded, "causal_improvement_proven": False}

    def audit(self) -> dict:
        with self._db() as db:
            counts = [dict(row) for row in db.execute(
                "SELECT arm,COUNT(*) trials,COUNT(success) scored,"
                " SUM(CASE WHEN success=1 THEN 1 ELSE 0 END) passed "
                "FROM trials GROUP BY arm"
            ).fetchall()]
            external = db.execute("SELECT COUNT(DISTINCT outcome_id) FROM world_outcomes").fetchone()[0]
            revisions = db.execute("SELECT COUNT(*) FROM world_outcomes").fetchone()[0]
            return {"trials_by_arm": counts, "mature_world_outcomes": external,
                    "world_outcome_revisions": revisions,
                    "causal_improvement_proven": False}
