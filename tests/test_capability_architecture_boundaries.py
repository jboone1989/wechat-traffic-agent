from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "wechat_traffic_agent"

# Migration inventory only. Deleting legacy modules is allowed; adding another
# Ferro-owned memory module is not.
LEGACY_MEMORY_MODULES = {
    "memory_ann.py",
    "memory_consolidation.py",
    "memory_continuity.py",
    "memory_domain_evolution.py",
    "memory_domain_policy.py",
    "memory_episode.py",
    "memory_evolution.py",
    "memory_evolution_bridge.py",
    "memory_formation.py",
    "memory_knowledge.py",
    "memory_life.py",
    "memory_models.py",
    "memory_observation.py",
    "memory_prior.py",
    "memory_prior_replay.py",
    "memory_procedural.py",
    "memory_retrieval.py",
    "memory_runtime.py",
    "memory_salience.py",
    "memory_self.py",
    "memory_semantic.py",
    "memory_service.py",
    "memory_session.py",
    "memory_system.py",
    "memory_vector.py",
}


def test_no_new_memory_subsystems_are_added_to_ferro():
    current = {path.name for path in PACKAGE.glob("memory_*.py")}
    unexpected = current - LEGACY_MEMORY_MODULES
    assert not unexpected, (
        "Memory is a reusable Runtime capability. New Ferro-owned memory "
        f"modules are forbidden: {sorted(unexpected)}"
    )


def test_runtime_memory_adapter_is_the_new_boundary():
    adapter = PACKAGE / "runtime_memory_adapter.py"
    assert adapter.is_file()
    source = adapter.read_text(encoding="utf-8")
    assert "MEMORY_CAPABILITY_ID" in source
    assert "LegacyFerroMemoryCapabilityProvider" in source


def test_local_semantic_retrieval_implementation_does_not_return_to_ferro():
    models = (PACKAGE / "memory_models.py").read_text(encoding="utf-8")
    ann = (PACKAGE / "memory_ann.py").read_text(encoding="utf-8")
    assert "agent_runtime.memory.local_retrieval" in models
    assert "agent_runtime.memory.local_retrieval" in ann
    for forbidden in ("fastembed", "hnswlib", "systemd-run", "TextEmbedding"):
        assert forbidden not in models
        assert forbidden not in ann
