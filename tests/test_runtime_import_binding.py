from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


def test_production_venv_agent_runtime_binding():
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    code = (
        "import json, agent_runtime, agent_runtime.runtime as r;"
        "print(json.dumps({"
        "'agent_runtime_file': agent_runtime.__file__,"
        "'runtime_file': r.__file__,"
        "'has_observe_external_experience': hasattr(r.AgentRuntime, 'observe_external_experience')"
        "}))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd="/tmp",
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(proc.stdout)
    assert payload["has_observe_external_experience"], payload
