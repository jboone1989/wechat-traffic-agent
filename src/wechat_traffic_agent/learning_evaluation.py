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


def load_review_receipt(path: Path | None, *, benchmark_sha256: str,
                        case_ids: list[str]) -> dict:
    """External gold-review attestation bound to an immutable benchmark hash.

    This checks completeness and provenance, not the reviewer's identity or
    correctness. Human verification remains a separate responsibility.
    """
    if path is None:
        return {"status": "PROVISIONAL", "receipt_sha256": None}
    raw = Path(path).read_bytes()
    receipt = json.loads(raw)
    if receipt.get("benchmark_sha256") != benchmark_sha256:
        raise ValueError("review receipt does not match frozen benchmark")
    if sorted(receipt.get("reviewed_case_ids") or []) != sorted(case_ids):
        raise ValueError("gold review must cover every case exactly once")
    if not str(receipt.get("reviewed_by") or "").strip():
        raise ValueError("gold review needs reviewer attribution")
    if not str(receipt.get("reviewed_at") or "").strip():
        raise ValueError("gold review needs a review timestamp")
    if not receipt.get("source_evidence_checked"):
        raise ValueError("reviewer must attest source evidence was checked")
    return {
        "status": "REVIEW_RECEIPT_RECORDED",
        "receipt_sha256": sha256(raw).hexdigest(),
        "reviewed_by": str(receipt["reviewed_by"]),
    }


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


def _source_clusters(rows: list[dict]) -> list[list[dict]]:
    """Merge overlapping source-ref sets, including transitive overlaps."""
    parents: dict[str, str] = {}

    def root(value: str) -> str:
        parents.setdefault(value, value)
        if parents[value] != value:
            parents[value] = root(parents[value])
        return parents[value]

    def union(a: str, b: str) -> None:
        parents[root(b)] = root(a)

    for row in rows:
        refs = sorted({str(v) for v in row.get("source_refs") or []})
        for ref in refs[1:]:
            union(refs[0], ref)
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        refs = sorted({str(v) for v in row.get("source_refs") or []})
        key = root(refs[0]) if refs else "missing:" + str(row.get("case_id"))
        grouped.setdefault(key, []).append(row)
    return list(grouped.values())


def _independent_signs(clusters: list[list[dict]]) -> tuple[int, int]:
    net = [
        sum(int(bool(row["with_memory_correct"])) -
            int(bool(row["without_memory_correct"])) for row in group)
        for group in clusters
    ]
    return sum(value > 0 for value in net), sum(value < 0 for value in net)



def scorecard(rows: list[dict]) -> dict:
    by_split = {}
    valid = [r for r in rows if r["status"] == "SCORED"]
    for split in ("retention", "near_transfer", "far_transfer"):
        subset = [r for r in valid if r["split"] == split]
        win = sum(r["with_memory_correct"] and not r["without_memory_correct"] for r in subset)
        loss = sum(r["without_memory_correct"] and not r["with_memory_correct"] for r in subset)
        clusters = _source_clusters(subset)
        independent_win, independent_loss = _independent_signs(clusters)
        by_split[split] = {
            "scored": len(subset), "wins": win, "losses": loss,
            "no_memory_correct": sum(bool(r["without_memory_correct"]) for r in subset),
            "with_memory_correct": sum(bool(r["with_memory_correct"]) for r in subset),
            "independent_source_groups": len(clusters),
            "source_group_wins": independent_win,
            "source_group_losses": independent_loss,
            "paired_p_value": paired_p_value(independent_win, independent_loss),
        }
    clusters = _source_clusters(valid)
    wins, losses = _independent_signs(clusters)
    scored = len(valid)
    independent_sources = len(clusters)
    # Provisional heuristic, not a proof. All splits and distinct sources
    # must be represented to reduce paraphrase/repeated-source inflation.
    supported = (
        scored >= 24 and independent_sources >= 12
        and all(s["scored"] >= 6 and s["independent_source_groups"] >= 3
                for s in by_split.values())
        and wins + losses >= 10 and wins > losses
        and paired_p_value(wins, losses) <= .05
    )
    return {
        "cases_scored": scored,
        "wins": sum(bool(r["with_memory_correct"]) and not bool(r["without_memory_correct"]) for r in valid),
        "losses": sum(bool(r["without_memory_correct"]) and not bool(r["with_memory_correct"]) for r in valid),
        "independent_source_groups": independent_sources,
        "source_group_wins": wins,
        "source_group_losses": losses,
        "paired_source_group_p_value": paired_p_value(wins, losses),
        "by_split": by_split,
        "evidence_of_memory_benefit": supported,
        "long_term_capability_improvement_proven": False,
    }


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
                            model_id: str, retrieval_mode: str = "lexical",
                            review_receipt: Path | None = None) -> dict:
    """Evaluate on isolated snapshot, never on Ferro's production state."""
    root = Path(snapshot_root).resolve()
    if not (root / AWE_SENTINEL).is_file():
        raise ValueError("evaluation requires isolated external-benchmark snapshot")
    payload, cases, digest = load_benchmark(benchmark_path)
    review = load_review_receipt(
        review_receipt, benchmark_sha256=digest,
        case_ids=[case.case_id for case in cases],
    )
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
    review_status = review["status"]
    report["benchmark_review_status"] = review_status
    report["review_receipt_sha256"] = review["receipt_sha256"]
    report["retrieval_mode"] = retrieval_mode
    if review_status != "REVIEW_RECEIPT_RECORDED":
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
