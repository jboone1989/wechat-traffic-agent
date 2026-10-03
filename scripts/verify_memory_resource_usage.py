"""Measure actual bilingual memory inference under the production cgroup."""
import importlib.util
import json
from pathlib import Path
import sys

spec = importlib.util.spec_from_file_location("resource_models", Path(__file__).resolve().parents[1] / "src/wechat_traffic_agent/memory_models.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
models = module.LocalRetrievalModels(Path.home())
operation = sys.argv[1]
relevant = "Persistent agent checkpoints restore the saved workflow state after a host restart. "
irrelevant = "A tomato garden needs sunlight and water; cooking pasta requires a pot. "
if operation == "embed":
    texts = [(relevant if i % 2 == 0 else irrelevant) * 40 for i in range(128)]
    result = models.embed(texts)
    assert len(result) == 128 and len(result[0]) == 384
    assert all(abs(sum(v*v for v in row) - 1) < 0.0001 for row in result)
elif operation == "query":
    result = models.embed(["How can a persistent agent recover saved work after a server restart?"], query=True)
    assert len(result) == 1 and len(result[0]) == 384
elif operation == "rerank":
    texts = [(relevant if i % 2 == 0 else irrelevant) * 40 for i in range(32)]
    result = models.rerank("How can a persistent agent recover saved work after a server restart?", texts)
    assert len(result) == 32
    assert min(result[::2]) > max(result[1::2]), result
    assert max(result[::2]) - min(result[::2]) < 0.0001
else:
    raise ValueError(operation)
metrics = json.loads((Path(models.cache) / "inference-metrics.jsonl").read_text().splitlines()[-1])
assert metrics["max_rss_kib"] < 1024*1024, metrics
report = {"verified": True, "operation": operation, "result_count": len(result), "metrics": metrics}
destination = Path.home() / "content-agent-state/host-health/acceptance"
destination.mkdir(parents=True, exist_ok=True)
(destination / (operation + ".json")).write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps(report))
