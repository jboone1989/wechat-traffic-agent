from __future__ import annotations

from types import SimpleNamespace

from wechat_traffic_agent.memory_semantic import SemanticRelationalMemoryService
from wechat_traffic_agent.memory_retrieval import MemoryRouter


def test_semantic_revision_preserves_history_and_current_truth(tmp_path):
    service = SemanticRelationalMemoryService(tmp_path)
    first = service.remember_semantic(
        text="Use TinyFish as the primary browser.",
        claim_key="browser.primary",
        source_type="OWNER_CONVERSATION",
        source_ref="operator-chat:1",
        source_text="请记住浏览器方案",
        authenticated_owner=True,
    )
    second = service.remember_semantic(
        text="Use JEV as the primary browser.",
        claim_key="browser.primary",
        source_type="OWNER_CONVERSATION",
        source_ref="operator-chat:2",
        source_text="修改之前的浏览器方案",
        authenticated_owner=True,
        supersedes=first["memory_id"],
    )
    old = service.store.get_memory(first["memory_id"])
    new = service.store.get_memory(second["memory_id"])
    assert old["status"] == "SUPERSEDED"
    assert old["superseded_by"] == second["memory_id"]
    assert new["supersedes"] == first["memory_id"]
    router = MemoryRouter(tmp_path, store=service.store)
    current = router.recall("primary browser", source_type="OWNER_CONVERSATION")
    assert [row["memory_id"] for row in current] == [second["memory_id"]]
    historical = router.recall("以前 primary browser", source_type="OWNER_CONVERSATION")
    assert {row["memory_id"] for row in historical} >= {first["memory_id"], second["memory_id"]}


def test_relation_has_structured_payload_and_revision(tmp_path):
    service = SemanticRelationalMemoryService(tmp_path)
    first = service.remember_relation(
        subject="Ferro", predicate="uses", object_="TinyFish",
        source_type="OWNER_CONVERSATION", source_ref="operator-chat:3",
        source_text="记住 Ferro 使用 TinyFish", authenticated_owner=True,
    )
    second = service.remember_relation(
        subject="Ferro", predicate="uses", object_="JEV",
        source_type="OWNER_CONVERSATION", source_ref="operator-chat:4",
        source_text="修改 Ferro 的浏览器关系", authenticated_owner=True,
        supersedes=first["memory_id"],
    )
    assert service.store.get_memory(first["memory_id"])["status"] == "SUPERSEDED"
    assert service.store.get_memory(second["memory_id"])["payload"]["object"] == "JEV"


def test_worldview_adapter_does_not_copy_authoritative_claims(tmp_path):
    service = SemanticRelationalMemoryService(tmp_path)
    class Mind:
        def public_projection(self):
            return {
                "beliefs": [{"proposition": "p", "confidence": 0.7}],
                "recent_belief_changes": [{"proposition": "p"}],
            }
    projection = service.worldview_projection(Mind())
    assert projection["authority"] == "virtualbrain"
    assert projection["beliefs"][0]["proposition"] == "p"
    assert service.store.list_memories() == []
