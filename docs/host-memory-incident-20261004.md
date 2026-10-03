# VM09 memory incident, 2026-10-04

VM09 has 3.6 GiB RAM and 1.9 GiB swap. At 01:01:51 Asia/Shanghai,
systemd reported `content-agent.service` killed by OOM, with a 1 GiB
memory peak and 256 MiB swap peak. Commit 20f13c5 moved serialized model
inference into transient user services, but left them without a memory
limit. The cross-encoder also used its default batch size of 64, so
all 32 retrieval candidates could enter one inference batch.

After the next reboot, vmstat showed roughly 100 MiB free, 1.3 GiB
swap occupied and sustained swap traffic during model activity. This
identifies a reproducible memory-pressure defect; it does not establish
that every historical SSH outage had the same cause.

The controlled comparison used the same 32 long candidate documents.
The old worker was placed in a separate 1 GiB cgroup with zero swap,
where it failed in 2.322 seconds with `Result=oom-kill`, SIGKILL and
`MemoryPeak=1073741824`. The corrected worker completed all 32 in
16.557 seconds at 724344 KiB maximum RSS. It preserves the same model,
candidate list, text, order and relevance scoring. All 128 embedding
inputs completed at 678888 KiB maximum RSS; query inference used
674468 KiB. No truncation or lexical fallback was introduced.

Inference now uses batch size 1, disables the ONNX CPU allocation arena,
and runs with explicit `MemoryMax=1G` and `MemorySwapMax=0`. Failure is
reported through the existing MEMORY_MODEL_FAILED path. Completed
inference receipts are recorded in the shared model cache.

The independent user service `ferro-host-health.service` samples every
15 seconds. It records boot identity, available RAM, swap, kernel OOM
counter, PSI, largest resident processes and their cgroup memory events.
It does not depend on Ferro's task scheduler or browser. The latest
snapshot is atomically replaced; two bounded history files survive
reboots under `content-agent-state/host-health`.

Reproduce the corrected worker with:
```
python scripts/verify_memory_resource_usage.py embed
python scripts/verify_memory_resource_usage.py query
python scripts/verify_memory_resource_usage.py rerank
```
Use the production interpreter with installed models. Verify both
the returned document count and resident memory receipt. Host recovery
and actual X publication are separate acceptance checks.
