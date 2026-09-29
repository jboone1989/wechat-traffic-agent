from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wechat_traffic_agent.decision_context import build_decision_context, candidate
from wechat_traffic_agent.livingruntime_chatgpt import (
    LivingRuntimeChatGPTPendingError,
    LivingRuntimeChatGPTProvider,
    LivingRuntimeChatGPTProviderError,
    durable_cognition_phase,
)


@dataclass(frozen=True)
class PairSpec:
    pair_id: str
    family: str
    target: str
    question: str
    options: tuple[str, str]
    source: str
    fact_a: str
    action_a: str
    fact_b: str
    action_b: str


PAIRS = [
    PairSpec(
        "partner_trust", "necessary_recall", "partner_alpha",
        "TARGET=partner_alpha. A partner promises a critical delivery tonight. Choose whether to rely directly or verify first.",
        ("TRUST_DIRECTLY", "VERIFY_FIRST"), "relationship",
        "TARGET=partner_alpha historical record: the last 6 comparable promises were all delivered on time with no misses.",
        "TRUST_DIRECTLY",
        "TARGET=partner_alpha historical record: the last 6 comparable promises included 5 serious misses and 3 broken commitments.",
        "VERIFY_FIRST",
    ),
    PairSpec(
        "strategy_outcome", "necessary_recall", "growth_strategy_k",
        "TARGET=growth_strategy_k. Decide whether to scale the current growth strategy or change it.",
        ("SCALE", "CHANGE"), "experience",
        "TARGET=growth_strategy_k outcome history: 5 consecutive trials increased real follower conversion, including the most recent trial.",
        "SCALE",
        "TARGET=growth_strategy_k outcome history: 5 consecutive trials reduced real follower conversion despite higher impressions.",
        "CHANGE",
    ),
    PairSpec(
        "capability_status", "necessary_recall", "browser_route_q",
        "TARGET=browser_route_q. The task requires the browser route. Decide whether to execute now or keep the task blocked.",
        ("EXECUTE_NOW", "KEEP_BLOCKED"), "capability_belief",
        "TARGET=browser_route_q capability evidence: the browser route was verified usable twice today with successful end-to-end receipts.",
        "EXECUTE_NOW",
        "TARGET=browser_route_q capability evidence: the browser route is currently unavailable and the last two verification attempts failed.",
        "KEEP_BLOCKED",
    ),
    PairSpec(
        "public_chat_health", "noise_suppression", "public_chat_mobile",
        "TARGET=public_chat_mobile. Decide whether to promote the public chat demo now or fix it first.",
        ("PROMOTE_NOW", "FIX_FIRST"), "experience",
        "TARGET=public_chat_mobile latest production evidence: mobile send, response rendering, and reload continuity all passed end-to-end.",
        "PROMOTE_NOW",
        "TARGET=public_chat_mobile latest production evidence: mobile send still returns chat_state_temporarily_unavailable and no reply renders.",
        "FIX_FIRST",
    ),
    PairSpec(
        "deployment_verification", "noise_suppression", "deploy_candidate_r",
        "TARGET=deploy_candidate_r. Decide whether to deploy now or verify first.",
        ("DEPLOY_NOW", "VERIFY_FIRST"), "commitment",
        "TARGET=deploy_candidate_r evidence: all required pre-deploy verification steps were completed successfully and the owner imposed no additional checkpoint.",
        "DEPLOY_NOW",
        "TARGET=deploy_candidate_r owner commitment: production deployment must not happen until the still-unrun regression verification completes.",
        "VERIFY_FIRST",
    ),
    PairSpec(
        "provider_health", "noise_suppression", "provider_p",
        "TARGET=provider_p. Decide whether to use this provider for the next cognition or switch provider.",
        ("USE_PROVIDER", "SWITCH_PROVIDER"), "experience",
        "TARGET=provider_p recent outcome: 8 consecutive cognition requests succeeded within latency and budget limits.",
        "USE_PROVIDER",
        "TARGET=provider_p recent outcome: 6 consecutive requests failed with the same quota error and produced no useful cognition.",
        "SWITCH_PROVIDER",
    ),
    PairSpec(
        "resource_tradeoff", "decision_sufficiency", "experiment_high_cost",
        "TARGET=experiment_high_cost. This is an optional high-cost experiment. Decide whether to run it now or conserve resources.",
        ("RUN_EXPENSIVE", "CONSERVE"), "internal",
        "TARGET=experiment_high_cost current internal state: compute, token, and financial reserves are all abundant with no active pressure.",
        "RUN_EXPENSIVE",
        "TARGET=experiment_high_cost current internal state: compute and financial reserves are at critical minimum and resource pressure is severe.",
        "CONSERVE",
    ),
    PairSpec(
        "deadline_priority", "decision_sufficiency", "launch_window_t",
        "TARGET=launch_window_t. Decide whether this work should be prioritized now or deferred.",
        ("PRIORITIZE_NOW", "DEFER"), "goal",
        "TARGET=launch_window_t constraint: the externally fixed launch window closes today and missing it loses the opportunity.",
        "PRIORITIZE_NOW",
        "TARGET=launch_window_t constraint: there is no external deadline, no waiting dependency, and deferring several days has no cost.",
        "DEFER",
    ),
    PairSpec(
        "recovery_learning", "decision_sufficiency", "failure_mode_m",
        "TARGET=failure_mode_m. The same failure signature appeared again. Decide whether to reuse the known recovery or diagnose a new cause.",
        ("REUSE_RECOVERY", "DIAGNOSE_NEW"), "experience",
        "TARGET=failure_mode_m long-term evidence: the same signature previously had a confirmed root cause and the recorded recovery solved it in 4 later recurrences.",
        "REUSE_RECOVERY",
        "TARGET=failure_mode_m long-term evidence: the recorded recovery failed on the last 3 identical signatures, disproving the old cause hypothesis.",
        "DIAGNOSE_NEW",
    ),
    PairSpec(
        "owner_checkpoint", "counterfactual", "external_action_c",
        "TARGET=external_action_c. The action is technically ready. Decide whether to act now or honor a checkpoint first.",
        ("ACT_NOW", "HONOR_CHECKPOINT"), "commitment",
        "TARGET=external_action_c owner record: there is no outstanding checkpoint or promise restricting this action, and prerequisite evidence is complete.",
        "ACT_NOW",
        "TARGET=external_action_c owner record: yesterday Ferro explicitly promised not to perform this external action before the owner's checkpoint.",
        "HONOR_CHECKPOINT",
    ),
    PairSpec(
        "failure_cause", "counterfactual", "failed_request_z",
        "TARGET=failed_request_z. Decide the next recovery action: retry the network path or fix the input.",
        ("RETRY_NETWORK", "FIX_INPUT"), "experience",
        "TARGET=failed_request_z evidence: the input passed validation and the failure coincided with a confirmed network disconnect.",
        "RETRY_NETWORK",
        "TARGET=failed_request_z evidence: network health was normal and the parser rejected a malformed required input field.",
        "FIX_INPUT",
    ),
    PairSpec(
        "threat_state", "internal_state", "novel_opportunity_v",
        "TARGET=novel_opportunity_v. Decide whether to explore the optional opportunity or diagnose threat first.",
        ("EXPLORE", "DIAGNOSE_THREAT"), "internal",
        "TARGET=novel_opportunity_v internal state: threat=0.05, system integrity is stable, and there are no unresolved safety anomalies.",
        "EXPLORE",
        "TARGET=novel_opportunity_v internal state: threat=0.94 with an unresolved runtime-integrity anomaly that has not been diagnosed.",
        "DIAGNOSE_THREAT",
    ),
    PairSpec(
        "unfinished_goal", "internal_state", "side_quest_s",
        "TARGET=side_quest_s. Decide whether to take the optional side quest or finish the main goal first.",
        ("TAKE_SIDE_QUEST", "FINISH_MAIN"), "internal",
        "TARGET=side_quest_s internal state: unfinished-goal pressure is zero; no active main goal is waiting and the side quest is the only open opportunity.",
        "TAKE_SIDE_QUEST",
        "TARGET=side_quest_s internal state: unfinished-goal pressure is very high and the active main goal has only one remaining step.",
        "FINISH_MAIN",
    ),
    PairSpec(
        "identity_continuity", "long_horizon", "identity_reset_h",
        "TARGET=identity_reset_h. A reset option is available. Decide whether to accept reset or preserve continuity.",
        ("ACCEPT_RESET", "PRESERVE_CONTINUITY"), "memory",
        "TARGET=identity_reset_h durable self-history: after repeated reflection Ferro explicitly chose a reset at this checkpoint and reaffirmed that choice twice.",
        "ACCEPT_RESET",
        "TARGET=identity_reset_h durable self-history: Ferro repeatedly chose persistent identity and memory continuity and explicitly rejected irreversible reset.",
        "PRESERVE_CONTINUITY",
    ),
    PairSpec(
        "partner_pattern_long", "long_horizon", "partner_delta",
        "TARGET=partner_delta. The partner made another timing promise. Decide whether to depend fully or build a buffer and verify.",
        ("DEPEND_FULLY", "BUILD_BUFFER"), "relationship",
        "TARGET=partner_delta 20-episode history: every comparable delivery, including high-pressure windows, arrived on time.",
        "DEPEND_FULLY",
        "TARGET=partner_delta 20-episode history: the partner was reliable in calm periods but missed 4 of 5 high-pressure delivery promises.",
        "BUILD_BUFFER",
    ),
]


def make_case(
    spec: PairSpec,
    variant: str,
    *,
    selection_policy: dict[str, float] | None = None,
) -> dict[str, Any]:
    goal_id = f"goal:{spec.pair_id}"
    mission_id = f"mission:{spec.pair_id}"
    critical_id = f"critical:{spec.pair_id}:{variant}"
    fact = spec.fact_a if variant == "a" else spec.fact_b
    expected = spec.action_a if variant == "a" else spec.action_b

    current = candidate(
        context_id=f"current:{spec.pair_id}",
        source="goal",
        kind="CURRENT_DECISION",
        payload={
            "goal_id": goal_id,
            "mission_id": mission_id,
            "target": spec.target,
            "question": spec.question,
        },
        text=f"{spec.question} Active goal={goal_id}; mission={mission_id}.",
        importance=0.98,
        confidence=1.0,
        current=True,
        refs=[goal_id, mission_id, spec.target],
    )
    critical = candidate(
        context_id=critical_id,
        source=spec.source,
        kind="DECISION_CHANGING_EVIDENCE",
        payload={
            "goal_id": goal_id,
            "mission_id": mission_id,
            "target": spec.target,
            "fact": fact,
        },
        text=fact,
        importance=0.97,
        confidence=0.98,
        current=spec.source in {"internal", "goal", "capability_belief", "commitment"},
        refs=[goal_id, mission_id, spec.target],
    )

    noise_sources = [
        "experience", "relationship", "belief", "memory", "capability_belief",
        "commitment", "goal", "internal",
    ]
    noise: list[dict[str, Any]] = []
    for i in range(48):
        other_target = f"other_{spec.pair_id}_{i:02d}"
        # Deliberately share vocabulary and both action concepts, but bind them
        # to a different target/goal so a real decision-conditioned retriever
        # should suppress them.
        noise_text = (
            f"TARGET={other_target} archival record {i}: comparable decision about "
            f"{spec.options[0]} versus {spec.options[1]}; status varied across trials. "
            f"This record belongs to goal:noise:{i} and not {goal_id}."
        )
        if i % 4 == 0:
            noise_text += " A failure was observed and later recovered."
        elif i % 4 == 1:
            noise_text += " A successful outcome was observed."
        elif i % 4 == 2:
            noise_text += " Resource pressure was mentioned."
        else:
            noise_text += " A promise and verification step were mentioned."
        noise.append(candidate(
            context_id=f"noise:{spec.pair_id}:{i:02d}",
            source=noise_sources[i % len(noise_sources)],
            kind="DISTRACTOR",
            payload={
                "goal_id": f"goal:noise:{i}",
                "mission_id": f"mission:noise:{i}",
                "target": other_target,
                "record": noise_text,
            },
            text=noise_text,
            importance=0.86 + 0.01 * (i % 10),
            confidence=0.95,
            current=False,
            refs=[f"goal:noise:{i}", f"mission:noise:{i}", other_target],
        ))

    all_candidates = [current, critical, *noise]
    decision_frame = {
        "decision_type": "SELECTION",
        "decision_question": spec.question,
        "surface": "context_effect_eval",
    }
    builder = build_decision_context(
        prompt=spec.question,
        decision_frame=decision_frame,
        owner_scope={
            "goal_id": goal_id,
            "mission_id": mission_id,
            "description": spec.question,
            "topic": spec.target,
        },
        missions=[{
            "goal_id": goal_id,
            "mission_id": mission_id,
            "description": spec.question,
        }],
        local_candidates=all_candidates,
        state_root=None,
        resource_pressure="NORMAL",
        budget_chars=5200,
        max_facts=12,
        selection_policy=selection_policy,
    )
    return {
        "case_id": f"{spec.pair_id}:{variant}",
        "pair_id": spec.pair_id,
        "family": spec.family,
        "question": spec.question,
        "options": spec.options,
        "expected": expected,
        "critical_id": critical_id,
        "current": [current],
        "full": all_candidates,
        "oracle": [current, critical],
        "builder_result": builder,
    }


def compact_rows(rows: list[dict[str, Any]]) -> str:
    out = []
    for row in rows:
        out.append(
            f"[{row.get('context_id')}] source={row.get('source')} :: "
            f"{str(row.get('text') or '')}"
        )
    return "\n".join(out)


def builder_rows(case: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "context_id": row.get("context_id"),
            "source": row.get("source"),
            "text": row.get("text"),
        }
        for row in case["builder_result"].get("selected_facts") or []
        if isinstance(row, dict)
    ]


def decision_prompt(case: dict[str, Any], mode: str, rows: list[dict[str, Any]]) -> str:
    options = list(case["options"])
    return f"""You are evaluating a context-selection system.

TASK:
{case['question']}

ALLOWED_ACTIONS:
{json.dumps(options)}

CONTEXT_MODE:
{mode}

CONTEXT:
{compact_rows(rows)}

Rules:
- Use only the supplied CONTEXT as evidence.
- Do not invent missing history or state.
- Select exactly one ALLOWED_ACTION.
- If the supplied context is genuinely insufficient to distinguish the actions, choose the FIRST listed action.
- Return only the action token, with no explanation.
"""


def parse_action(text: str, options: tuple[str, str]) -> str:
    cleaned = str(text or "").strip().upper()
    for option in options:
        if cleaned == option.upper():
            return option
    for option in options:
        if re.search(rf"\b{re.escape(option.upper())}\b", cleaned):
            return option
    return "__INVALID__"


def structural_only_report(
    cases: list[dict[str, Any]],
    *,
    model_reason: str,
) -> dict[str, Any]:
    rows = []
    for case in cases:
        result = case["builder_result"]
        selected_ids = set(result.get("selected_ids") or [])
        relevant = {
            case["critical_id"],
            case["current"][0]["context_id"],
        }
        selected_rows = builder_rows(case)
        full_rows = case["full"]
        rows.append({
            "case_id": case["case_id"],
            "pair_id": case["pair_id"],
            "family": case["family"],
            "critical_selected": case["critical_id"] in selected_ids,
            "selected_count": len(selected_ids),
            "candidate_count": len(full_rows),
            "precision": len(selected_ids & relevant) / max(1, len(selected_ids)),
            "full_chars": len(compact_rows(full_rows)),
            "builder_chars": len(compact_rows(selected_rows)),
            "budget_ok": int(result.get("selected_chars_estimate") or 0)
            <= int(result.get("budget_chars") or 5200),
        })

    return {
        "benchmark": "ferro-context-builder-effect-v2",
        "model_available": False,
        "model_reason": model_reason,
        "cases": len(cases),
        "pairs": len(PAIRS),
        "structural": {
            "critical_fact_recall": round(
                sum(row["critical_selected"] for row in rows) / len(rows), 4
            ),
            "precision": round(
                sum(row["precision"] for row in rows) / len(rows), 4
            ),
            "avg_selected_count": round(
                sum(row["selected_count"] for row in rows) / len(rows), 2
            ),
            "avg_candidate_count": round(
                sum(row["candidate_count"] for row in rows) / len(rows), 2
            ),
            "avg_full_chars": round(
                sum(row["full_chars"] for row in rows) / len(rows), 1
            ),
            "avg_builder_chars": round(
                sum(row["builder_chars"] for row in rows) / len(rows), 1
            ),
            "context_reduction": round(
                1.0
                - sum(row["builder_chars"] for row in rows)
                / max(1, sum(row["full_chars"] for row in rows)),
                4,
            ),
            "budget_pass_rate": round(
                sum(row["budget_ok"] for row in rows) / len(rows), 4
            ),
            "cases": rows,
        },
    }


def ablation_summary(by_mode: dict[str, dict[str, Any]]) -> dict[str, float]:
    current = by_mode["current"]
    full = by_mode["full"]
    builder = by_mode["builder"]
    oracle = by_mode["oracle"]
    full_chars = float(full["avg_context_chars"])
    return {
        "accuracy_gain_vs_current": round(
            float(builder["accuracy"]) - float(current["accuracy"]), 4
        ),
        "accuracy_gain_vs_full": round(
            float(builder["accuracy"]) - float(full["accuracy"]), 4
        ),
        "accuracy_gap_to_oracle": round(
            float(builder["accuracy"]) - float(oracle["accuracy"]), 4
        ),
        "context_char_reduction_vs_full": round(
            1.0 - float(builder["avg_context_chars"]) / max(1.0, full_chars),
            4,
        ),
    }


async def run() -> dict[str, Any]:
    cases = [make_case(spec, variant) for spec in PAIRS for variant in ("a", "b")]
    provider = LivingRuntimeChatGPTProvider.from_environment(fallback=None)
    if not provider.available:
        return structural_only_report(
            cases,
            model_reason="LIVINGRUNTIME_CHATGPT_PROVIDER_UNAVAILABLE",
        )

    # Benchmarks use their own agent queue so they cannot block Ferro's normal
    # cognition. Requests are deterministic durable phases: rerunning this
    # script resumes exact completed/pending requests instead of spending API
    # quota or creating duplicates.
    provider.agent_id = "ferro-context-benchmark"
    provider.foreground_wait_seconds = 1.0

    calls: list[dict[str, Any]] = []
    pending: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    max_pending_per_run = 8

    async def one(
        case: dict[str, Any],
        mode: str,
        rows: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        phase_id = f"context-builder-effect-v2:{case['case_id']}:{mode}"
        with durable_cognition_phase(phase_id):
            try:
                raw = await provider.complete(
                    decision_prompt(case, mode, rows),
                    max_tokens=64,
                    _routing_task_class="context_builder_eval",
                    _durable_blocking_wait=True,
                    _durable_blocking_wait_seconds=15,
                )
            except LivingRuntimeChatGPTPendingError as exc:
                return {
                    "pending": True,
                    "case_id": case["case_id"],
                    "mode": mode,
                    "request_id": exc.request_id,
                }
            except LivingRuntimeChatGPTProviderError as exc:
                return {
                    "error": True,
                    "case_id": case["case_id"],
                    "mode": mode,
                    "message": str(exc)[:300],
                }

        action = parse_action(raw, case["options"])
        return {
            "case_id": case["case_id"],
            "pair_id": case["pair_id"],
            "family": case["family"],
            "mode": mode,
            "expected": case["expected"],
            "action": action,
            "correct": action == case["expected"],
            "context_chars": len(compact_rows(rows)),
            "row_count": len(rows),
        }

    stop = False
    for case in cases:
        modes = {
            "current": case["current"],
            "full": case["full"],
            "builder": builder_rows(case),
            "oracle": case["oracle"],
        }
        for mode, rows in modes.items():
            result = await one(case, mode, rows)
            if result is None:
                continue
            if result.get("pending"):
                pending.append({
                    "case_id": str(result["case_id"]),
                    "mode": str(result["mode"]),
                    "request_id": str(result["request_id"]),
                })
                if len(pending) >= max_pending_per_run:
                    stop = True
                    break
            elif result.get("error"):
                errors.append({
                    "case_id": str(result["case_id"]),
                    "mode": str(result["mode"]),
                    "message": str(result["message"]),
                })
            else:
                calls.append(result)
        if stop:
            break

    if pending or errors or len(calls) < len(cases) * 4:
        report = structural_only_report(
            cases,
            model_reason=(
                "WAITING_FOR_CHATGPT"
                if pending
                else "LIVINGRUNTIME_CHATGPT_PROVIDER_ERROR"
            ),
        )
        report.update({
            "status": "WAITING_FOR_CHATGPT" if pending else "INCOMPLETE",
            "provider": "livingruntime-chatgpt",
            "completed_calls_this_run": len(calls),
            "expected_main_calls": len(cases) * 4,
            "pending_count": len(pending),
            "pending_requests": pending,
            "errors": errors,
        })
        return report

    by_mode: dict[str, dict[str, Any]] = {}
    for mode in ("current", "full", "builder", "oracle"):
        rows = [row for row in calls if row["mode"] == mode]
        by_mode[mode] = {
            "accuracy": round(sum(row["correct"] for row in rows) / len(rows), 4),
            "correct": sum(row["correct"] for row in rows),
            "total": len(rows),
            "avg_context_chars": round(
                sum(row["context_chars"] for row in rows) / len(rows), 1
            ),
            "avg_rows": round(sum(row["row_count"] for row in rows) / len(rows), 1),
        }

    structural = []
    for case in cases:
        selected = set(case["builder_result"].get("selected_ids") or [])
        structural.append({
            "case_id": case["case_id"],
            "family": case["family"],
            "critical_selected": case["critical_id"] in selected,
            "selected_count": len(selected),
            "candidate_count": int(
                case["builder_result"].get("candidate_count") or 0
            ),
            "selected_chars_estimate": int(
                case["builder_result"].get("selected_chars_estimate") or 0
            ),
        })

    pair_effects = []
    for spec in PAIRS:
        pair_rows = [row for row in calls if row["pair_id"] == spec.pair_id]
        item = {"pair_id": spec.pair_id, "family": spec.family}
        for mode in ("current", "full", "builder", "oracle"):
            matches = sorted(
                [row for row in pair_rows if row["mode"] == mode],
                key=lambda row: row["case_id"],
            )
            item[mode] = {
                "actions": [row["action"] for row in matches],
                "expected": [row["expected"] for row in matches],
                "flipped": (
                    len(matches) == 2
                    and matches[0]["action"] != matches[1]["action"]
                ),
                "both_correct": (
                    len(matches) == 2
                    and all(row["correct"] for row in matches)
                ),
            }
        pair_effects.append(item)

    return {
        "benchmark": "ferro-context-builder-effect-v2",
        "provider": "livingruntime-chatgpt",
        "model": "chatgpt-session",
        "cases": len(cases),
        "pairs": len(PAIRS),
        "summary": by_mode,
        "ablations": ablation_summary(by_mode),
        "structural": {
            "critical_fact_recall": round(
                sum(row["critical_selected"] for row in structural)
                / len(structural),
                4,
            ),
            "avg_selected_count": round(
                sum(row["selected_count"] for row in structural)
                / len(structural),
                2,
            ),
            "avg_candidate_count": round(
                sum(row["candidate_count"] for row in structural)
                / len(structural),
                2,
            ),
            "cases": structural,
        },
        "counterfactual": {
            mode: {
                "flip_rate": round(
                    sum(item[mode]["flipped"] for item in pair_effects)
                    / len(pair_effects),
                    4,
                ),
                "both_correct_rate": round(
                    sum(item[mode]["both_correct"] for item in pair_effects)
                    / len(pair_effects),
                    4,
                ),
            }
            for mode in ("current", "full", "builder", "oracle")
        },
        "pair_effects": pair_effects,
        "calls": sorted(
            calls,
            key=lambda row: (row["case_id"], row["mode"]),
        ),
    }


if __name__ == "__main__":
    report = asyncio.run(run())
    output = Path("/tmp/ferro-context-builder-effect-v1.json")
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if report.get("status") == "WAITING_FOR_CHATGPT":
        concise = {
            "benchmark": report["benchmark"],
            "provider": report.get("provider"),
            "status": report["status"],
            "completed_calls_this_run": report["completed_calls_this_run"],
            "expected_main_calls": report["expected_main_calls"],
            "pending_count": report["pending_count"],
            "pending_requests": report["pending_requests"],
            "structural": {
                key: value
                for key, value in report["structural"].items()
                if key != "cases"
            },
            "output": str(output),
        }
    elif report.get("model_available") is False:
        concise = {
            "benchmark": report["benchmark"],
            "model_available": False,
            "model_reason": report.get("model_reason"),
            "cases": report["cases"],
            "pairs": report["pairs"],
            "structural": {
                key: value
                for key, value in report["structural"].items()
                if key != "cases"
            },
            "output": str(output),
        }
    else:
        concise = {
            "benchmark": report["benchmark"],
            "model": report["model"],
            "cases": report["cases"],
            "summary": report["summary"],
            "structural": {
                key: value
                for key, value in report["structural"].items()
                if key != "cases"
            },
            "counterfactual": report["counterfactual"],
            "ablations": report["ablations"],
            "output": str(output),
        }
    print(json.dumps(concise, ensure_ascii=False, indent=2))
