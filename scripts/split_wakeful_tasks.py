"""Mechanical extraction of Wakeful task handlers into feature-specific mixins.

Run once on the old flat class. The final dispatcher keeps the stable public
WakefulContentTaskRunner import and shares the existing runtime components.
"""
from __future__ import annotations

import ast
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1] / "src" / "wechat_traffic_agent"
SOURCE = ROOT / "wakeful_tasks.py"
GROUPS = {
    "owner": (
        "_owner_behavioral_context", "_owner_activated_external_goal_ids",
        "_owner_active_goal_proposals", "_active_owner_goal_context",
        "_owner_agent_mission_context", "_owner_agent_control",
        "_owner_agent_seed_evidence",
    ),
    "content": (
        "_runtime_revision", "_content_recovery", "_mission_followup_identity",
        "_content_mission_followup", "_content",
    ),
    "social": (
        "_x", "_reconcile_x_publication_backlog", "_okx_ai_marketplace",
        "_social_stage_backlog_candidate", "_run_social_stage",
        "_social_stage_error", "_recover_social_stage_backlog",
    ),
    "neural": (
        "_fast_decision", "_neural_interoception", "_neural_play",
        "_neural_play_impl", "_neural_dream", "_neural_transfer",
        "_neural_development", "_neural_development_transfer",
        "_neural_evolution_consolidation",
    ),
    "resources": (
        "_windfall_pending_path", "_record_windfall_pending",
        "_load_windfall_pending", "_consume_windfall_pending",
        "_resource", "_self_repair", "_self_repair_recovery", "_reflect",
        "_windfall_deliberation", "_fallback_evolution_hypothesis",
        "_evolution_deliberation",
    ),
    "observation": (
        "_runtime_tick", "_observe", "_outcomes", "_verify_prediction",
    ),
}
CLASS_NAMES = {
    "owner": "OwnerTaskMethods",
    "content": "ContentTaskMethods",
    "social": "SocialTaskMethods",
    "neural": "NeuralTaskMethods",
    "resources": "ResourceTaskMethods",
    "observation": "ObservationTaskMethods",
}
HEADER = '''from __future__ import annotations

import asyncio
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any

'''
RELATIVE = re.compile(r"(?m)^([ \t]*from[ \t]+)\.([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)([ \t]+import\b)")
RELATIVE_ROOT = re.compile(r"(?m)^([ \t]*from[ \t]+)\.([ \t]+import\b)")


def run() -> None:
    text = SOURCE.read_text(encoding="utf-8")
    if text.count("class WakefulContentTaskRunner:") != 1:
        raise RuntimeError("Wakeful runner already split or unexpectedly modified")
    tree = ast.parse(text)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "WakefulContentTaskRunner")
    methods = {m.name: m for m in cls.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))}
    lines = text.splitlines(keepends=True)
    excluded = set()
    destdir = ROOT / "orchestration"
    if destdir.exists():
        raise RuntimeError("orchestration directory exists; inspect before rerunning")
    outputs = {}
    for group, method_names in GROUPS.items():
        spans = []
        for name in method_names:
            if name not in methods or name in excluded:
                raise RuntimeError(f"missing or duplicate task method: {name}")
            excluded.add(name)
            method = methods[name]
            start = min([method.lineno] + [dec.lineno for dec in method.decorator_list])
            end = method.end_lineno
            spans.append((start, end, "".join(lines[start-1:end])))
        spans.sort()
        combined = "\n\n".join(item[2].rstrip("\n") for item in spans) + "\n"
        if "_ShutdownRequested" in combined or "super()" in combined:
            raise RuntimeError("extraction would create a circular or super dependency")
        combined = RELATIVE.sub(r"\1wechat_traffic_agent.\2\3", combined)
        combined = RELATIVE_ROOT.sub(r"\1wechat_traffic_agent\2", combined)
        module_text = (
            f'"""Wakeful {group} task logic; the host injects runner components."""\n'
            + HEADER + f"class {CLASS_NAMES[group]}:\n"
            + f'    """{group.capitalize()} capability handlers for the shared wakeful host."""\n\n'
            + combined
        )
        ast.parse(module_text)
        outputs[group] = module_text
        for start, end, _ in spans:
            excluded.update((f"@lines:{i}" for i in range(start, end+1)))
    delete = {int(x.split(":")[1]) for x in excluded if x.startswith("@lines:")}
    imports = "\n".join(
        f"from .orchestration.{group} import {CLASS_NAMES[group]}" for group in GROUPS
    )
    parents = ", ".join(CLASS_NAMES[group] for group in GROUPS)
    out = "".join(line for n, line in enumerate(lines, 1) if n not in delete)
    out = out.replace(
        "class WakefulContentTaskRunner:",
        imports + "\n\nclass WakefulContentTaskRunner(" + parents + "):",
        1,
    )
    ast.parse(out)
    destdir.mkdir()
    (destdir / "__init__.py").write_text('"""Independent Wakeful task responsibilities under one host."""\n')
    for group, content in outputs.items():
        (destdir / f"{group}.py").write_text(content, encoding="utf-8")
    SOURCE.write_text(out, encoding="utf-8")
    print("extracted", len([x for x in excluded if not x.startswith("@lines:")]), "methods")
    for group, content in outputs.items():
        print(group, len(content.splitlines()), "lines")
    print("runner", len(out.splitlines()), "lines")


if __name__ == "__main__":
    run()
