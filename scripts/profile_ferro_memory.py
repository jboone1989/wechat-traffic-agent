#!/usr/bin/env python3
"""Read-only Ferro host/cgroup memory profile. No changes to services."""
from pathlib import Path
import json
import os
import time

CG = Path("/sys/fs/cgroup/system.slice/content-agent.service")


def text(path):
    try:
        return Path(path).read_text(errors="replace")
    except (OSError, UnicodeError):
        return ""


def pairs(path):
    result = {}
    for line in text(path).splitlines():
        p = line.split()
        if len(p) >= 2 and p[1].lstrip("-").isdigit():
            result[p[0].rstrip(":")] = int(p[1])
    return result


def mib(value):
    if value is None:
        return None
    return round(value / 1024 / 1024, 2)


def process(pid):
    proc = Path("/proc") / str(pid)
    argv = [item for item in text(proc / "cmdline").split(chr(0)) if item]
    # Do not expose process arguments; they can hold bearer tokens or passwords.
    display = next((Path(arg).name for arg in argv[1:] if arg.endswith(".py")), "")
    if not display and "-m" in argv:
        pos = argv.index("-m")
        display = argv[pos + 1] if pos + 1 < len(argv) else ""
    display = display or (Path(argv[0]).name if argv else "unknown")
    status = pairs(proc / "status")
    rss = status.get("VmRSS", 0) * 1024
    rollup = pairs(proc / "smaps_rollup")
    # /proc/smaps_rollup metrics are in kB.
    pss = rollup.get("Pss")
    private = rollup.get("Private_Clean", 0) + rollup.get("Private_Dirty", 0)
    swap = status.get("VmSwap", 0) * 1024
    return {
        "pid": pid,
        "ppid": status.get("PPid"),
        "rss_mib": mib(rss),
        "pss_mib": mib(pss * 1024) if pss is not None else None,
        "private_mib": mib(private * 1024) if pss is not None else None,
        "swap_mib": mib(swap),
        "threads": status.get("Threads"),
        "cmd": display,
    }


def children_from_cgroup():
    pids = set()
    paths = [CG]
    while paths:
        path = paths.pop()
        pids.update(int(v) for v in text(path / "cgroup.procs").split() if v.isdigit())
        try:
            paths += [p for p in path.iterdir() if p.is_dir()]
        except OSError:
            pass
    return sorted(pids)


def main():
    host = pairs("/proc/meminfo")
    pids = children_from_cgroup()
    processes = []
    for pid in pids:
        try:
            processes.append(process(pid))
        except OSError:
            pass
    stat = pairs(CG / "memory.stat")
    events = pairs(CG / "memory.events")
    other = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdecimal() or int(entry.name) in pids:
            continue
        info = process(int(entry.name))
        if info["rss_mib"] and info["rss_mib"] > 35:
            other.append(info)
    out = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "host": {
            "MemTotal_mib": mib(host.get("MemTotal", 0) * 1024),
            "MemAvailable_mib": mib(host.get("MemAvailable", 0) * 1024),
            "MemFree_mib": mib(host.get("MemFree", 0) * 1024),
            "SwapTotal_mib": mib(host.get("SwapTotal", 0) * 1024),
            "SwapFree_mib": mib(host.get("SwapFree", 0) * 1024),
            "Cached_mib": mib(host.get("Cached", 0) * 1024),
            "Buffers_mib": mib(host.get("Buffers", 0) * 1024),
        },
        "cgroup": {
            "memory_current_mib": mib(int(text(CG / "memory.current") or "0")),
            "memory_peak_mib": mib(int(text(CG / "memory.peak") or "0")),
            "memory_max": text(CG / "memory.max").strip(),
            "memory_high": text(CG / "memory.high").strip(),
            "swap_current_mib": mib(int(text(CG / "memory.swap.current") or "0")),
            "swap_max": text(CG / "memory.swap.max").strip(),
            "stat_mib": {k: mib(v) for k,v in stat.items()
              if k in {"anon","file","kernel","slab","slab_reclaimable","slab_unreclaimable","kernel_stack",
                       "sock","shmem","file_mapped",
                       "inactive_file","active_file","swapcached","pagetables"}},
            "events": events,
            "events_local": pairs(CG / "memory.events.local"),
            "pressure": text(CG / "memory.pressure").strip(),
        },
        "children": sorted(processes,key=lambda p:p["rss_mib"] or 0,reverse=True),
        "other_large_processes": sorted(other,key=lambda p:p["rss_mib"] or 0,reverse=True)[:18],
    }
    print(json.dumps(out,ensure_ascii=False))


if __name__=="__main__":
    main()
