from wechat_traffic_agent.capability_worker import (
    PUBLIC_WORKER_CAPABILITIES,
    PublicCapabilityWorkerApplication,
)
from wechat_traffic_agent.network_capabilities import (
    CAP_CONTENT_DRAFT,
    CAP_CONTENT_EVIDENCE,
    CAP_CONTENT_RESEARCH,
    CAP_CONTENT_REVIEW,
)


class _Runtime:
    cycle = 7

    def available_capabilities(self):
        return ("web.search", "web.fetch", CAP_CONTENT_RESEARCH, CAP_CONTENT_EVIDENCE)


class _Manager:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_public_worker_surface_excludes_generation_and_publish_capabilities(tmp_path):
    assert PUBLIC_WORKER_CAPABILITIES == {CAP_CONTENT_RESEARCH, CAP_CONTENT_EVIDENCE}
    assert CAP_CONTENT_DRAFT not in PUBLIC_WORKER_CAPABILITIES
    assert CAP_CONTENT_REVIEW not in PUBLIC_WORKER_CAPABILITIES

    manager = _Manager()
    app = PublicCapabilityWorkerApplication(
        root=tmp_path,
        worker_id="worker.public.1",
        runtime=_Runtime(),
        capability_manager=manager,
    )
    status = app.status_observation()

    assert status["generation_ready"] is True
    assert status["publish_mode"] == "none"
    assert not hasattr(app, "subject")
    assert not hasattr(app, "content_mind")
    app.close()
    assert manager.closed is True
