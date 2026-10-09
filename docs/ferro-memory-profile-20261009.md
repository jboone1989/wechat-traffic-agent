# Ferro memory diagnosis — 2026-10-09

## Measured on VM-0-9-ubuntu (Asia/Shanghai)

Using read-only `scripts/profile_ferro_memory.py` on host cgroup
`/sys/fs/cgroup/system.slice/content-agent.service`, not by summing process RSS.

| Observation (UTC) | Release | Service cgroup | Host MemAvailable | Swap in service | Notes |
|---|---|---:|---:|---:|---|
| 02:44:54 | d1b8d73-learning-evidence | 971.56 MiB | 1514.10 MiB | 121.19 MiB | 1 GiB ceiling, 13,788 memory.max events since start; no recorded OOM kill |
| 02:45–02:46 | 8e07021-modular | 230.41 MiB at startup | 1674.80 MiB | 0 | Restart/reset; not a steady-state measurement |
| 02:46:38 | 8e07021-modular | 609.16 MiB | 1555.71 MiB | 0 | ~1 minute after deployment |
| 02:47:20 | 8e07021-modular | 654.27 MiB | 1577.05 MiB | 0 | ~1.5 minutes after deployment; cgroup peak 654.78 MiB |

The VM has 3659.89 MiB physical RAM and 1988 MiB total swap.
The service is subject to `memory.max=1073741824` (1 GiB),
`memory.high=max` (not configured), `memory.swap.max=268435456` (256 MiB).

## Why total service usage is not the sum of Python RSS

The old service cgroup included roughly 400 MiB anonymous pages,
221 MiB file cache, and 340 MiB **kernel memory**, mostly charged slab.
That was observed directly in `memory.stat`, not inferred from Python RSS.
It caused memory.max pressure without an OOM kill in that particular period.
On the new release after restart, the same cgroup had ~434 MiB anonymous
memory, ~214 MiB file cache, ~6 MiB kernel memory. This strongly indicates
a large amount of the previous cgroup usage was not Python objects.
The cause and future slab growth rate remain unverified.

New release child RSS, ~02:47 UTC:
- Content cognition worker: **~237 MiB** (largest in main cgroup).
- Self repair: ~66 MiB.
- Check X: ~57 MiB.
- Reflect: ~51 MiB.
- Check outcomes: ~49 MiB.
- Resource review: ~46 MiB.
- Supervisor: ~13 MiB.

**Important**: `run_ferro_neural_worker.py` has ~318 MiB RSS and
~300 MiB PSS but is launched via `systemd-run --user --scope`, outside
`content-agent.service` memory accounting. It still consumes physical
machine memory. Therefore the main service's MemoryMax does NOT protect
against total Ferro memory usage. Browser-related child processes can be
transient and are separately observed.

## Recommendation, based on actual host capacity

1. **Raise main service ceiling moderately to 1.5 GiB**:
   `MemoryHigh=1280M`, `MemoryMax=1536M`, keep
   `MemorySwapMax=256M`. A 2 GiB main-service cap is not warranted with
   only ~1.5 GiB currently available on the host and an external neural scope.
2. Keep neural and wallet WebSocket in separate bounded units, but **do not**
   enable another long-running worker on this constrained VM until observed
   resource usage and host free-memory reserve allow it.
3. Repeat samples after extended real workload; compare cgroup
   `slab_reclaimable`, `slab_unreclaimable`, `file`, `anon`,
   `memory.events`, and browser process lifetime. A large kernel slab
   increase requires specific investigation; simply adding Python memory
   profiling will not pinpoint it.
4. Ensure the active release is `8e07021-modular` or later before attributing
   memory reductions to code deletion; older releases are not representative.

### Change status

- Proposed drop-in: `deploy/systemd/ferro-memory-budget.conf` (repo only).
- Live runtime change was **not applied**. The connector rejected the
  direct `systemctl set-property` command as **hard-denied**, so an
  authorized host administrator must apply it. Do not claim deployment.
- Authorized host command (does not require service restart):

```bash
sudo systemctl set-property content-agent.service MemoryHigh=1280M MemoryMax=1536M
systemctl show content-agent.service -p MemoryHigh -p MemoryMax -p MemoryCurrent
```

- To maintain `MemorySwapMax=256M`, leave the existing live setting
  unchanged (or verify it with the same read-only tool).
