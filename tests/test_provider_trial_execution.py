import json
from datetime import datetime, timezone

from wechat_traffic_agent.human_assistance import HumanAssistanceService

NOW = datetime(2026, 9, 17, 15, 0, tzinfo=timezone.utc)


def test_human_request_can_be_fulfilled_by_verified_trial_evidence(tmp_path):
    service = HumanAssistanceService(state_root=tmp_path)
    row = service.request(
        kind="MODEL_RESOURCE_ACCESS",
        summary="Need provider access",
        requested_action="Configure NVIDIA_API_KEY securely",
        now=NOW,
        details={"credential_env": "NVIDIA_API_KEY"},
    )
    resolved = service.fulfill_matching(
        kind="MODEL_RESOURCE_ACCESS", detail_key="credential_env",
        detail_value="NVIDIA_API_KEY", now=NOW,
        evidence_refs=("provider-trial-plan:x", "model-trial:y"),
    )
    assert [item["request_id"] for item in resolved] == [row["request_id"]]
    saved = json.loads(next((tmp_path / "application" / "human_assistance").glob("*.json")).read_text())
    assert saved["resolution_status"] == "FULFILLED"
    assert saved["resolution_evidence_refs"] == ["provider-trial-plan:x", "model-trial:y"]
