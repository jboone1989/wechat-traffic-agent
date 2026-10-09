"""Draft generation, exact-quote verification and bounded repair boundaries."""
import asyncio
import ast
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from wechat_traffic_agent.content_artifact import ContentKind
from wechat_traffic_agent.draft_verification_service import (
    DraftVerificationService, VerificationRequest,
)
from wechat_traffic_agent.draft_repair_service import DraftRepairRequest, DraftRepairService
from wechat_traffic_agent.draft_generation_service import (
    DraftGenerationPolicies, DraftGenerationRequest, DraftGenerationService,
)
from wechat_traffic_agent.draft_outcome_service import DraftOutcomeService, DraftOutcomeRequest


class Draft:
    kind = ContentKind.NOTE
    title = "Test note"
    summary = "I reason carefully from one documented fact"
    sections = (SimpleNamespace(
        heading="Observation", paragraphs=("I verify the source evidence.",),
    ),)

    def model_dump(self, **kwargs):
        return {"title": self.title, "summary": self.summary,
                "sections": [{"heading": self.sections[0].heading,
                              "paragraphs": list(self.sections[0].paragraphs)}]}


def _request(tmp_path, *, retry=False):
    source = {
        "evidence_id": "source-1", "title": "Source",
        "summary": "An observed fact.", "evidence_refs": ["quote-1"],
    }
    return VerificationRequest(
        folder=tmp_path, cycle="cycle-1", artifact=Draft(),
        lesson=SimpleNamespace(techniques=(object(),)),
        lesson_payload={"techniques": [{"name": "structured reasoning"}]},
        technique_requirements={"required_indices": [0], "deferred_indices": []},
        selected=source, market_companion=None, counter_payload=None,
        related=[], snapshot={"articles": []}, floor_plan=object(),
        is_short_note=False, minimum_words=None,
        perspective_expression=True, perspective_required=True,
        retry_verification_once=retry, process_skill={"variant_id": "variant-1"},
        existing=[], topic="ai", scope_downgraded=False,
    )


def _check(*, quote="An observed fact.", technique_quote="I verify the source evidence."):
    return SimpleNamespace(
        grounded=True, useful=True,
        applications=(SimpleNamespace(technique_index=0, article_quote=technique_quote),),
        support=(SimpleNamespace(section_index=0, source_id="source-1",
                                 source_quote=quote),),
        model_copy=lambda update: SimpleNamespace(
            grounded=True, useful=True, applications=update["applications"],
            support=update["support"],
        ),
    )


def test_exact_quote_verifier_withholds_unsupported_claims(tmp_path, monkeypatch):
    import wechat_traffic_agent.draft_verification_service as mod
    monkeypatch.setattr(mod, "evaluate_evidence_floor", lambda **kwargs: SimpleNamespace(
        status="PASS", model_dump=lambda **kw: {"status": "PASS"},
    ))
    async def bad_response(*args, **kwargs):
        return _check(quote="A source quotation that does not exist.")
    verifier = DraftVerificationService(
        activity=lambda *args, **kw: None, structured=bad_response,
        required_applied=lambda observed, requirements: set(requirements["required_indices"]) <= observed,
        hypothetical_sections=lambda article: set(), title_key=lambda title: title.casefold(),
    )
    response = asyncio.run(verifier.verify(_request(tmp_path), runtime=object()))
    assert response.passed is False
    assert response.support_valid == set()
    assert response.grounding_valid == set()
    assert response.attempts == 1


def test_frozen_artifact_retry_never_weakens_gate(tmp_path, monkeypatch):
    import wechat_traffic_agent.draft_verification_service as mod
    monkeypatch.setattr(mod, "evaluate_evidence_floor", lambda **kwargs: SimpleNamespace(
        status="PASS", model_dump=lambda **kw: {"status": "PASS"},
    ))
    calls = []
    async def check(*args, **kwargs):
        calls.append(1)
        return _check(quote="unverifiable source sentence")
    verifier = DraftVerificationService(
        activity=lambda *args, **kw: None, structured=check,
        required_applied=lambda indices, req: set(req["required_indices"]) <= indices,
        hypothetical_sections=lambda article: set(), title_key=lambda title: title.casefold(),
    )
    output = asyncio.run(verifier.verify(_request(tmp_path, retry=True), object()))
    assert output.passed is False and output.attempts == 2
    assert len(calls) == 2
    receipt = json.loads((tmp_path / "memory_process_retry.json").read_text())
    assert receipt["retry_passed"] is False
    assert receipt["frozen_artifact"] is True
    assert receipt["frozen_evidence"] is True


def test_writer_and_verifier_do_not_import_content_runner():
    root = Path(__file__).resolve().parents[1] / "src" / "wechat_traffic_agent"
    for filename in (
        "draft_generation_service.py", "draft_verification_service.py",
        "draft_repair_service.py", "draft_outcome_service.py",
        "expression_decision_service.py",
    ):
        tree = ast.parse((root / filename).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert "content_goal_loop" not in str(node.module), filename
                assert "live_agent" not in str(node.module), filename
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert node.end_lineno - node.lineno + 1 <= 100, (
                    filename, node.name, node.end_lineno - node.lineno + 1,
                )


def test_run_entrypoint_keeps_shrinking():
    root = Path(__file__).resolve().parents[1] / "src" / "wechat_traffic_agent"
    tree = ast.parse((root / "content_goal_loop.py").read_text(encoding="utf-8"))
    klass = next(n for n in tree.body if isinstance(n, ast.ClassDef)
                 and n.name == "ContinuousContentLoop")
    method = next(n for n in klass.body
                  if isinstance(n, ast.AsyncFunctionDef) and n.name == "_run")
    assert method.end_lineno - method.lineno + 1 <= 50
    for phase in klass.body:
        if (isinstance(phase, (ast.FunctionDef, ast.AsyncFunctionDef))
                and phase.name.startswith("_cycle_")):
            assert phase.end_lineno - phase.lineno + 1 <= 100, phase.name
