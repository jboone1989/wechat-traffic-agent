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

### Live memory limit — applied and verified

The operator applied the approved systemd memory adjustment. Live cgroup
values were **verified** after the change:

- `MemoryHigh=1342177280` bytes = **1280 MiB**.
- `MemoryMax=1610612736` bytes = **1536 MiB**.
- `MemorySwapMax=268435456` bytes = **256 MiB** (unchanged).

Later measurement on the modular release showed a service charge around
**1006 MiB** (no longer pinned to the 1 GiB limit), around **272 MiB
reclaimable kernel slab** and **279 MiB file cache**. Prior `memory.max`
events are cumulative since this service started, not evidence of continuing
limit violations after the increase. Host `MemAvailable` remained around
1.5–1.7 GiB during investigation. Do not widen MemoryMax to 2 GiB merely
because reclaimable caches are present.

### Browser / cache investigation — 2026-10-09 03:00–03:06 UTC

- **Verified same persistent browser endpoint:** both X and ChatGPT
  `runtime.json` metadata point to **CDP port 9600**. The profile labels
  are logical session groups, not separate Chrome processes proven by name.
- **CLI helpers versus browser instances:** `browser-harness` launches a
  command subprocess, which spawns `browser_harness.daemon`. Sampling most
  often saw one parent–child pair; one sampling interval observed a peak of
  **two concurrent calls (four helper processes)** under the same content
  cognition parent. That does **not** establish a duplicate persistent Chrome.
  The Agent Runtime adapter explicitly supplies an existing CDP endpoint
  and does not start a second browser on account creation.
- **Kernel accounting:** old/new cgroup statistics repeatedly showed
  `slab_reclaimable` around **272 MiB** and `file` around **279 MiB**.
  Slab can be reclaimed under pressure; these values are **not** equivalent
  to a 272 MiB userspace leak. Root-only `/proc/slabinfo` and slab sysfs
  details were unavailable through this connector, so attribution to
  `dentry`, overlayfs, sockets or specific files is **unproven**.
- **State-event receipts:** the acknowledged receipt folder held about
  **17,000 JSON files**, occupying **23.4 MiB** on disk. A dry-run of
  `compact_state_event_receipts.py` against 24-hour-old records reported
  **0 eligible cleanups / 0 estimated bytes reclaimed**. These records
  may contribute to filesystem metadata churn, but cannot explain all
  charged slab without kernel cache attribution.
- **Browser Harness historical artifacts:** around 71 old rejected
  conversation snapshots (~8.36 MiB) and 23 old outbound files
  (~1.45 MiB). These were inventoried by filename and file size only;
  content was **not** read or removed.
- The browser account/login state, historical receipts, and kernel cache
  were left untouched. No speculative `drop_caches`, process kills, or
  multiple-service reconfiguration.

Reproduce using `python scripts/diagnose_ferro_slab.py` (read-only;
prints no profile credentials or process command-line tokens) alongside
`python scripts/profile_ferro_memory.py`. This script samples helper
processes for eight seconds and reports only metadata, not session content.

**Outcome:** the cgroup cap is now properly adjusted, and no redundant
persistent browser was established. The precise creator of the reclaimable
slab remains an open investigation, not a justification for deleting files
or restarting production Ferro.
