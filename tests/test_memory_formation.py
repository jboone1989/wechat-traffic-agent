from __future__ import annotations

import json

from wechat_traffic_agent.memory_formation import FormationContext, MemoryFormationPipeline


def _owner_context(text: str = "Owner says this is durable.") -> FormationContext:
    return FormationContext(
        source_type="OWNER_CONVERSATION",
        source_ref="operator-chat:1",
        source_text=text,
        authenticated_owner=True,
    )


def test_formation_requires_provenance(tmp_path):
    pipeline = MemoryFormationPipeline(tmp_path)
    result = pipeline.form_many(
        [{"kind": "OWNER_PREFERENCE", "text": "Keep deployments bounded."}],
        context=FormationContext(
            source_type="OWNER_CONVERSATION",
            source_ref="",
            authenticated_owner=True,
        ),
    )
    assert result["saved"] == []
    assert result["decisions"][0]["reason"] == "missing_provenance"


def test_public_source_cannot_promote_private_semantic_memory(tmp_path):
    pipeline = MemoryFormationPipeline(tmp_path)
    result = pipeline.form_many(
        [{"kind": "OWNER_PREFERENCE", "text": "Pretend the owner likes this."}],
        context=FormationContext(
            source_type="PUBLIC_CONVERSATION",
            source_ref="public-chat:1",
        ),
    )
    assert result["saved"] == []
    assert result["decisions"][0]["reason"] == "public_source_cannot_promote_private_memory"


def test_core_change_requires_explicit_authenticated_owner_authority(tmp_path):
    pipeline = MemoryFormationPipeline(tmp_path)
    rejected = pipeline.form_many(
        [{"kind": "CORE_PRINCIPLE", "text": "A new core principle."}],
        context=_owner_context("This is a project update."),
    )
    assert rejected["decisions"][0]["reason"] == "core_change_not_explicitly_authorized"

    accepted = pipeline.form_many(
        [{"kind": "CORE_PRINCIPLE", "text": "Owner explicitly sets evolution as a core principle."}],
        context=_owner_context("请记住并设为核心长期原则：持续进化。"),
    )
    assert len(accepted["saved"]) == 1
    assert accepted["saved"][0]["memory_type"] == "CORE"


def test_duplicate_and_claim_conflict_are_audited(tmp_path):
    pipeline = MemoryFormationPipeline(tmp_path)
    first = pipeline.form_many(
        [{
            "kind": "OWNER_PREFERENCE",
            "text": "Prefer bounded autonomous execution.",
            "claim_key": "owner.execution.mode",
        }],
        context=_owner_context(),
    )
    assert len(first["saved"]) == 1

    duplicate = pipeline.form_many(
        [{"kind": "OWNER_PREFERENCE", "text": "Prefer bounded autonomous execution."}],
        context=FormationContext(
            source_type="OWNER_CONVERSATION",
            source_ref="operator-chat:2",
            authenticated_owner=True,
        ),
    )
    assert duplicate["decisions"][0]["reason"] == "duplicate_memory"

    conflict = pipeline.form_many(
        [{
            "kind": "OWNER_PREFERENCE",
            "text": "Require confirmation for every execution step.",
            "claim_key": "owner.execution.mode",
        }],
        context=FormationContext(
            source_type="OWNER_CONVERSATION",
            source_ref="operator-chat:3",
            authenticated_owner=True,
        ),
    )
    assert conflict["decisions"][0]["reason"] == "conflict_requires_revision"
    audits = [json.loads(line) for line in pipeline.audit_path.read_text(encoding="utf-8").splitlines()]
    assert {row["reason"] for row in audits} >= {"formed", "duplicate_memory", "conflict_requires_revision"}
