#!/usr/bin/env python3
"""Run one read-only knowledge-transfer check using Ferro's real memory and route.

The output root is isolated from live learning ledgers. No publication, messaging
or trading capability is invoked. Model and web calls retain their real receipts.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--state-root", type=Path, required=True)
parser.add_argument("--output-root", type=Path, required=True)
parser.add_argument("--topic", default="ai-agent-reliability")
parser.add_argument("--environment-pid", type=int)
parser.add_argument("--inspect-only", action="store_true")
args = parser.parse_args()
if args.environment_pid:
    # Reuse the running service's configured providers and shared browser.
    # Never print inherited configuration or credentials.
    for entry in Path(f"/proc/{args.environment_pid}/environ").read_bytes().split(b"\0"):
        if b"=" in entry:
            key, value = entry.split(b"=", 1)
            os.environ[key.decode()] = value.decode()
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ["CONTENT_APPLICATION_ROOT"] = str(args.output_root)
from wechat_traffic_agent.learning_research import (
    research_memory, choose_research_focus, _research_prompt_memory,
)
from wechat_traffic_agent.learning_application import assess_application, application_feedback
from wechat_traffic_agent.content_loop import ReceiptRuntime, _put
from wechat_traffic_agent.content_explorer import _web_candidate
from wechat_traffic_agent.sources.runtime_web import RuntimeWebSearch
from wechat_traffic_agent.agent_runtime_chatgpt import AgentRuntimeChatGPTProvider
from wechat_traffic_agent.runtime_app import build_content_application

memory = research_memory(None, args.state_root, args.topic)
projection = _research_prompt_memory(memory, args.topic)
summary = {
    "actual_history_count": len(memory["research"]["history"]),
    "actual_fact_count": sum(len(row["facts"]) for row in memory["research"]["history"]),
    "knowledge_refs": len(memory["knowledge_catalog"]),
    "settled_action_outcomes": len(memory["outcome_feedback"]["settled_actions"]),
    "resolved_world_predictions": len(memory["outcome_feedback"]["resolved_predictions"]),
    "reflection_refs": len(memory["recent_reflections"]),
    "plan_memory_chars": len(json.dumps(projection, ensure_ascii=False)),
}
args.output_root.mkdir(parents=True, exist_ok=True)
_put(args.output_root / "memory_inspection.json", summary)
print(json.dumps({"stage": "MEMORY", **summary}), flush=True)
if args.inspect_only:
    raise SystemExit(0)

async def main():
    provider = AgentRuntimeChatGPTProvider.from_environment()
    if not provider.available:
        raise RuntimeError("the existing ChatGPT browser route is not ready")
    class Cognition:
        agent_manifest = None
        async def cognitive_probe(self, task, max_tokens, output_schema=None):
            return await provider.complete(task, max_tokens=max_tokens,
                reasoning_effort="low", _routing_task_class="owner_dialogue")
    folder = args.output_root / "application-test"
    runtime = ReceiptRuntime(Cognition(), folder / "model_calls")
    focus = await choose_research_focus(runtime=runtime, folder=folder,
        topic=args.topic, memory=memory)
    print(json.dumps({"stage": "PRECOMMIT", "focus": focus.model_dump(mode="json")}), flush=True)
    if focus.application is None:
        print(json.dumps({"status": "DEFERRED", "reason": focus.application_deferred_reason}), flush=True)
        return
    app = build_content_application(root=args.output_root / "runtime-state",
                                    manifest_path=ROOT / "agent.yaml")
    try:
        now = datetime.now(timezone.utc)
        source = RuntimeWebSearch(app.runtime)
        rows = await source.search(query=focus.search_query, now=now)
        pool = {"items": [_web_candidate(row) for row in rows], "failures": [],
                "query": focus.search_query}
        _put(folder / "learning_initial_search.json", pool)
        print(json.dumps({"stage": "ACQUIRED", "source_count": len(rows),
                          "urls": [row["url"] for row in rows]}), flush=True)
        receipt = await assess_application(runtime=runtime, folder=folder,
            state_root=args.output_root, topic=args.topic)
        updated = {**memory, "application_feedback": application_feedback(args.output_root)}
        next_focus = await choose_research_focus(runtime=runtime,
            folder=args.output_root / "next-plan", topic=args.topic, memory=updated)
        summary = {
            "status": "PASS", "application_status": receipt["assessment"]["status"],
            "prediction": receipt["application"]["prediction"],
            "assessment": receipt["assessment"], "brier_score": receipt["brier_score"],
            "evidence_count": len(receipt["evidence"]),
            "next_focus": next_focus.model_dump(mode="json"),
            "next_plan_received_feedback": bool(updated["application_feedback"]),
            "general_improvement_proven": False,
        }
        _put(args.output_root / "acceptance.json", summary)
        print(json.dumps(summary, ensure_ascii=False), flush=True)
    finally:
        app.close()
asyncio.run(main())
