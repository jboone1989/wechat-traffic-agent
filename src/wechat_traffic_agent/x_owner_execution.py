"""Persist and validate an owner-directed X execution request."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any
from .content_loop import _put

SCHEMA = "ferro-owner-publication-request.v1"


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def owner_x_publication_request(folder: Path) -> dict[str, Any]:
    request = _read(folder / "owner_publication_request.json")
    plan = _read(folder / "mind_plan.json")
    goal = plan.get("goal") or {}
    decision = request.get("decision") or {}
    if not (
        request.get("schema_version") == SCHEMA
        and request.get("cycle") == folder.name
        and request.get("episode_id")
        and request.get("owner_goal_id")
        and request.get("owner_goal_id") == goal.get("goal_id")
        and decision.get("action") == "RUN_CONTENT_PIPELINE"
        and decision.get("publication_surface") in {"X_SHORT_POST", "X_LONG_POST", "X_ARTICLE"}
        and decision.get("topic") == plan.get("required_topic")
        and str(request.get("mission_id") or "") == str(plan.get("external_mission_id") or "")
    ):
        return {}
    return request


def prepare_owner_x_expression(*, folder: Path, source_evidence: dict[str, Any],
                               cognition: dict[str, Any], worldview: Any,
                               prediction: Any, mind_plan: dict[str, Any]) -> dict[str, Any]:
    request = owner_x_publication_request(folder)
    if not request:
        raise ValueError("owner X execution request does not match its persisted cycle plan")
    if not cognition.get("worth_saying"):
        raise ValueError("owner X execution requires a useful persisted cognition")
    if not (source_evidence.get("evidence_id") and source_evidence.get("summary")
            and source_evidence.get("evidence_refs")):
        raise ValueError("owner X execution requires an attributed source")
    payload = {
        "schema_version": "ferro-owner-x-expression-context.v1",
        "cycle": folder.name, "source_evidence": source_evidence,
        "owner_request": request, "publication_authority": False,
    }
    _put(folder / "x_expression_context.json", payload)
    return {
        "status": "X_EXPRESSION_READY", "cycle": folder.name,
        "mind_plan": mind_plan, "cognition": cognition,
        "worldview_update": worldview, "prediction": prediction,
        "publication_surface": request["decision"]["publication_surface"],
        "source_evidence_id": source_evidence["evidence_id"],
    }
