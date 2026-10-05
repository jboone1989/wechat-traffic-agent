"""Verify an actual pending reflection cannot crash the content worker."""
import json
from pathlib import Path
import runpy


def test_pending_reflection_with_empty_source_refs_has_receipt_provenance(tmp_path):
    worker = runpy.run_path(str(Path(__file__).resolve().parents[1] /
                                "scripts/run_content_cognition_worker.py"))
    commit = worker["_commit_pending_reflections"]
    path = tmp_path/"application/reflection_results/reflection-one.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"status": "PENDING_CONTENT_MIND_COMMIT",
        "target_date": "2026-10-05", "reflection_text": "The prior search repeated an unresolved question.",
        "evidence_refs": []}))
    class Mind:
        calls = []
        def record_daily_reflection(self, **data):
            assert data["evidence_refs"]
            assert all((tmp_path/ref).exists() for ref in data["evidence_refs"])
            self.calls.append(data)
    mind = Mind()
    assert commit(tmp_path, mind) == 1
    assert mind.calls[0]["evidence_refs"] == ("application/reflection_results/reflection-one.json",)
    assert json.loads(path.read_text())["status"] == "COMMITTED"
    assert commit(tmp_path, mind) == 0
    assert len(mind.calls) == 1
