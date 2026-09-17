from pathlib import Path
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def test_persistent_content_framework_dependencies_are_declared():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = tuple(data["project"]["dependencies"])
    assert any(item.startswith("agent-runtime>=0.3") for item in dependencies)
    assert any(item.startswith("agent-network-sdk>=0.2") for item in dependencies)
    assert any(item.startswith("virtualbrain>=0.2") for item in dependencies)
    assert any(item.startswith("uvicorn>=0.30") for item in dependencies)
