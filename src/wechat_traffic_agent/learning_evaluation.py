"""Paired learning evaluation using Ferro's existing isolated AWE contestant.

This is a task/dataset/scorer adapter, not a new memory engine. The only
experimental difference is whether the same Ferro contestant can retrieve
durable memories. Same model, prompt, task and decision options in both arms.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from math import comb
from pathlib import Path
import sqlite3
from uuid import uuid4

from .awe_contestant import AWE_SENTINEL
from .external_eval_contestant import FerroExternalEvalContestant


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    split: str
    observation: str
    actions: dict[str, str]
    gold_action: str
    source_refs: tuple[str, ...]

    @classmethod
    def load(cls, value: dict):
        if value.get("split") not in {"retention", "near_transfer", "far_transfer"}:
            raise ValueError("split must measure retention or transfer")
        actions = value.get("actions")
        if not isinstance(actions, dict) or len(actions) < 2:
            raise ValueError("benchmark requires at least two candidate actions")
        if value.get("gold_action") not in actions:
            raise ValueError("expected action must be supplied as an option")
        refs = tuple(value.get("source_refs") or ())
        if not refs or any(not str(x).strip() for x in refs):
            raise ValueError("gold requires independent source references")
        return cls(
            case_id=str(value["case_id"]), split=value["split"],
            observation=str(value["observation"]), actions=dict(actions),
            gold_action=str(value["gold_action"]), source_refs=refs,
        )


def load_benchmark(path: Path):
    raw = Path(path).read_bytes()
    payload = json.loads(raw)
    cases = [BenchmarkCase.load(item) for item in payload["cases"]]
    if len({c.case_id for c in cases}) != len(cases) or not cases:
        raise ValueError("case IDs must be distinct and nonempty")
    if not payload.get("benchmark_id") or not payload.get("version"):
        raise ValueError("benchmark identity/version required")
    return payload, cases, sha256(raw).hexdigest()


def snapshot_digest(snapshot_root: Path) -> str:
    """Freeze a readable fingerprint without ever modifying the memory corpus."""
    from .memory_system import CanonicalMemoryStore
    store = CanonicalMemoryStore(snapshot_root)
    rows = store.list_memories(limit=None)
    digest = sha256()
    for row in sorted(rows, key=lambda item: item["memory_id"]):
        digest.update(json.dumps({
            "id": row["memory_id"], "status": row.get("status"),
            "text": row.get("text"), "evidence_refs": row.get("evidence_refs"),
            "payload": row.get("payload"),
        }, sort_keys=True, default=str).encode("utf-8"))
    return digest.hexdigest()


def paired_p_value(wins: int, losses: int) -> float:
    """Exact paired sign test on discordant outcomes, two-sided."""
    n = wins + losses
    if not n:
        return 1.0
    k = min(wins, losses)
    return min(1., 2 * sum(comb(n, j) for j in range(k + 1)) / (2 ** n))


def scorecard(rows: list[dict]) -> dict:
    by_split = {}
    for split in ("retention", "near_transfer", "far_transfer"):
        valid = [r for r in rows if r["split"] == split and r["status"] == "SCORED"]
        win = sum(r["with_memory_correct"] and not r["without_memory_correct"] for r in valid)
        loss = sum(r["without_memory_correct"] and not r["with_memory_correct"] for r in valid)
        by_split[split] = {
            "scored": len(valid), "wins": win, "losses": loss,
            "no_memory_correct": sum(bool(r["without_memory_correct"]) for r in valid),
            "with_memory_correct": sum(bool(r["with_memory_correct"]) for r in valid),
            "paired_p_value": paired_p_value(win, loss),
        }
    scored = sum(s["scored"] for s in by_split.values())
    wins = sum(s["wins"] for s in by_split.values())
    losses = sum(s["losses"] for s in by_split.values())
    # Fixed threshold is a cautious reporting gate, NOT causal proof:
    # independent withheld cases and a controlled frozen corpus are essential.
    supported = scored >= 20 and wins + losses >= 10 and wins > losses
    supported = supported and paired_p_value(wins, losses) <= .05
    return {"cases_scored": scored, "wins": wins, "losses": losses,
            "by_split": by_split, "evidence_of_memory_benefit": supported,
            "long_term_capability_improvement_proven": False}


class LearningEvalLedger:
    def __init__(self, path: Path):
        self.path = Path(path)

    def save(self, run: dict, outcomes: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path, timeout=15) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY, benchmark_id TEXT, benchmark_version TEXT,
                benchmark_sha256 TEXT, snapshot_sha256 TEXT, model_id TEXT,
                started_at TEXT, results_json TEXT)""")
            db.execute("""CREATE TABLE IF NOT EXISTS cases (
                run_id TEXT, case_id TEXT, split TEXT, status TEXT,
                without_memory_action TEXT, with_memory_action TEXT,
                without_memory_correct INTEGER, with_memory_correct INTEGER,
                source_refs_json TEXT, error TEXT,
                PRIMARY KEY (run_id,case_id))""")
            with db:
                db.execute(
                    "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?)",
                    (run["run_id"], run["benchmark_id"], run["benchmark_version"],
                     run["benchmark_sha256"], run["snapshot_sha256"], run["model_id"],
                     run["started_at"], json.dumps(run["scorecard"], sort_keys=True)),
                )
                db.executemany(
                    "INSERT INTO cases VALUES (?,?,?,?,?,?,?,?,?,?)",
                    [(run["run_id"], r["case_id"], r["split"], r["status"],
                      r.get("without_memory_action"), r.get("with_memory_action"),
                      r.get("without_memory_correct"), r.get("with_memory_correct"),
                      json.dumps(r["source_refs"]), r.get("error")) for r in outcomes],
                )


def compare_runs(ledger_path: Path, before_run: str, after_run: str) -> dict:
    """Measure forgetting and transfer after an actual memory revision.

    Requires matching model and exact frozen benchmark. A difference is a
    measured behavior/accuracy change; not automatically a learning cause.
    """
    with sqlite3.connect(f"file:{Path(ledger_path)}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        before = db.execute("SELECT * FROM runs WHERE run_id=?", (before_run,)).fetchone()
        after = db.execute("SELECT * FROM runs WHERE run_id=?", (after_run,)).fetchone()
        if before is None or after is None:
            raise ValueError("both completed evaluation runs required")
        if (before["benchmark_sha256"], before["model_id"]) != (
            after["benchmark_sha256"], after["model_id"]
        ):
            raise ValueError("comparison requires identical benchmark and model")
        before_mode = json.loads(before["results_json"]).get("retrieval_mode")
        after_mode = json.loads(after["results_json"]).get("retrieval_mode")
        if before_mode != after_mode:
            raise ValueError("comparison requires identical retrieval configuration")
        if before["snapshot_sha256"] == after["snapshot_sha256"]:
            raise ValueError("learning progress requires a changed memory snapshot")
        a = {
            row["case_id"]: row for row in db.execute(
                "SELECT * FROM cases WHERE run_id=?", (before_run,)
            )
        }
        b = {
            row["case_id"]: row for row in db.execute(
                "SELECT * FROM cases WHERE run_id=?", (after_run,)
            )
        }
    if set(a) != set(b):
        raise ValueError("paired runs require exactly matching task IDs")
    groups = {}
    for split in ("retention", "near_transfer", "far_transfer"):
        rows = [(a[k], b[k]) for k in sorted(a) if a[k]["split"] == split]
        scored = [(x, y) for x, y in rows if x["status"] == y["status"] == "SCORED"]
        regressions = sum(x["with_memory_correct"] == 1 and y["with_memory_correct"] == 0
                          for x, y in scored)
        improvements = sum(x["with_memory_correct"] == 0 and y["with_memory_correct"] == 1
                           for x, y in scored)
        groups[split] = {"total": len(rows), "scored": len(scored),
                         "regressions": regressions, "improvements": improvements,
                         "paired_p_value": paired_p_value(improvements, regressions)}
    complete = bool(a) and all(x["scored"] == x["total"] for x in groups.values())
    return {"before_run": before_run, "after_run": after_run,
            "by_split": groups, "coverage_complete": complete,
            "no_regressions_observed": (
                complete and all(x["regressions"] == 0 for x in groups.values())
            ), "long_term_capability_improvement_proven": False}


async def evaluate_learning(*, provider, snapshot_root: Path,
                            benchmark_path: Path, ledger_path: Path,
                            model_id: str, retrieval_mode: str = "lexical") -> dict:
    """Evaluate on isolated snapshot, never on Ferro's production state."""
    root = Path(snapshot_root).resolve()
    if not (root / AWE_SENTINEL).is_file():
        raise ValueError("evaluation requires isolated external-benchmark snapshot")
    payload, cases, digest = load_benchmark(benchmark_path)
    initial_snapshot = snapshot_digest(root)
    arms = {
        False: FerroExternalEvalContestant(state_root=root, provider=provider,
                                          memory_enabled=False, retrieval_mode=retrieval_mode),
        True: FerroExternalEvalContestant(state_root=root, provider=provider,
                                         memory_enabled=True, retrieval_mode=retrieval_mode),
    }
    outcomes = []
    for i, case in enumerate(cases):
        result = {"case_id": case.case_id, "split": case.split,
                  "source_refs": list(case.source_refs), "status": "SCORED"}
        # Alternating order reduces systematic provider-order confounding.
        try:
            for enabled in ((False, True) if i % 2 == 0 else (True, False)):
                agent = arms[enabled]
                agent.begin_episode(
                    benchmark_id=payload["benchmark_id"], episode_id=case.case_id,
                )
                choice = await agent.choose(
                    observation=case.observation, actions=case.actions,
                    context={"evaluation_split": case.split},
                )
                prefix = "with_memory" if enabled else "without_memory"
                result[prefix + "_action"] = choice["action_id"]
                result[prefix + "_correct"] = int(choice["action_id"] == case.gold_action)
        except Exception as exc:
            result["status"], result["error"] = "ERROR", type(exc).__name__
        outcomes.append(result)
    if snapshot_digest(root) != initial_snapshot:
        raise RuntimeError("benchmark mutated its learning memory corpus")
    report = scorecard(outcomes)
    review_status = payload.get("review_status", "PROVISIONAL")
    report["benchmark_review_status"] = review_status
    report["retrieval_mode"] = retrieval_mode
    if review_status != "APPROVED":
        report["evidence_of_memory_benefit"] = False
    run = {
        "run_id": uuid4().hex, "benchmark_id": payload["benchmark_id"],
        "benchmark_version": payload["version"], "benchmark_sha256": digest,
        "snapshot_sha256": initial_snapshot, "model_id": str(model_id),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "scorecard": report,
    }
    LearningEvalLedger(ledger_path).save(run, outcomes)
    return {**run, "cases": outcomes}
