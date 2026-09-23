from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request


def test_guardian_canary_starts_real_minimal_runtime_without_content_credentials(tmp_path: Path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    state_root = tmp_path / "candidate-state"
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "FERRO_GUARDIAN_HEALTH_NONCE": "guardian-canary-test",
        "FERRO_CANDIDATE_STATE_ROOT": str(state_root),
        "FERRO_CANDIDATE_PORT": str(port),
    }
    process = subprocess.Popen(
        [sys.executable, "scripts/run_content_agent.py", "--guardian-canary"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        payload = None
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if process.poll() is not None:
                stdout, stderr = process.communicate(timeout=1)
                raise AssertionError(
                    f"guardian canary exited early rc={process.returncode}\nstdout={stdout}\nstderr={stderr}"
                )
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/guardian-health", timeout=1
                ) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                break
            except Exception:
                time.sleep(0.1)

        assert payload is not None
        assert payload["status"] == "ok"
        assert payload["healthy"] is True
        assert payload["guardianNonce"] == "guardian-canary-test"
        assert payload["capabilities"] == ["ferro-runtime", "network-health"]
        assert state_root.exists()
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
