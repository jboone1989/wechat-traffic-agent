# Ferro runtime and Square distribution

The runtime lives at `/home/ubuntu/.local/share/ferro/venv`. System services use
the stable `/home/ubuntu/content-agent-venv-current/bin/python` alias rather than
a release-specific virtualenv. Keep this runtime, the alias, active code releases,
the FlyDrones vendor source, connectome data and authenticated browser profiles
when cleaning the server.

Rebuild the environment, when needed, as ubuntu:

```bash
python3 /home/ubuntu/content-agent/current/scripts/bootstrap_ferro_runtime.py
```

The bootstrap installs the declared dependencies, local Runtime/VirtualBrain/
Agent Network SDK/FlyDrones packages, the SearXNG and Jina capability packs,
Browser Harness and the pinned Jev package. It restores the existing Browser
Harness IPC frame patch and validates package requirements. A single current
dependency snapshot is kept at `/home/ubuntu/.local/share/ferro/requirements.txt`.

The main service and existing X monitor/outcomes services have a final
`zzzzzzzz-runtime-environment.conf` drop-in setting the stable executable path
for Browser Harness and putting the shared runtime's bin directory on PATH.

Square scheduling uses:

- `ferro-square-distribution.timer`: one pending task every two minutes.
- `ferro-square-monitor.timer`: inspect runtime, heartbeat, queue and receipts
  every two minutes.
- `ferro-binance-research.timer`: refresh read-only research observations every
  twenty minutes; it does not publish.

Deployment units are in `deploy/systemd`. Install the corresponding service and
timer files into `/etc/systemd/system`, reload systemd, and enable those timers.
Do not put a posting key in unit files or shell arguments. Existing private
Square settings and key files are loaded by the worker.

Worker ticks are saved to `application/binance_square_worker.json`. The monitor
writes `application/binance_square_health.json`, including the separate
one-time research receipt and normal outbox successes. A clean empty queue is
healthy. Missing credentials, disabled writes, stopped scheduling, stale
heartbeats, failed tasks and stalled unverified submissions produce distinct
issues. Uncertain submissions require reconciliation; do not blindly requeue.

Publication is verified only by a successful platform response containing a
post ID. Engagement remains `UNAVAILABLE` with null counters until a verified
analytics transport is configured. A VM09 public-page probe on 2026-10-09
returned HTTP 202 with an empty body; this is not evidence of zero readership.
