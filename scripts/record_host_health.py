"""Independent evidence for host pressure, process growth and cgroup OOMs."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time


def counters(path):
    return {key.rstrip(":"): int(value.split()[0])
            for line in path.read_text().splitlines()
            for key, value in [line.split(None, 1)]}


def snapshot(proc=Path("/proc"), cgroups=Path("/sys/fs/cgroup")):
    processes = []
    errors = []
    for directory in proc.iterdir():
        if not directory.name.isdigit():
            continue
        try:
            values = {}
            for line in (directory / "status").read_text().splitlines():
                if ":" in line:
                    key, value = line.split(":", 1)
                    values[key] = value.strip()
            membership = (directory / "cgroup").read_text().strip()
            group = next(line.split(":", 2)[2] for line in membership.splitlines()
                         if line.startswith("0::"))
            processes.append({"pid": int(directory.name), "name": values["Name"],
                              "rss_kib": int(values.get("VmRSS", "0").split()[0]),
                              "swap_kib": int(values.get("VmSwap", "0").split()[0]),
                              "cgroup": group})
        except (FileNotFoundError, ProcessLookupError):
            continue
        except (PermissionError, ValueError, KeyError, StopIteration) as error:
            errors.append({"pid": directory.name, "error": str(error)})
    groups = {}
    for group in {row["cgroup"] for row in processes} | {"/"}:
        directory = cgroups / group.lstrip("/")
        data = {}
        for name in ("memory.current", "memory.peak", "memory.max",
                     "memory.swap.current", "memory.swap.max", "memory.events"):
            try:
                data[name] = (directory / name).read_text().strip()
            except FileNotFoundError:
                continue
        if data:
            groups[group] = data
    return {"observed_at": datetime.now(timezone.utc).isoformat(),
            "boot_id": (proc / "sys/kernel/random/boot_id").read_text().strip(),
            "uptime": (proc / "uptime").read_text().strip(),
            "memory_kib": counters(proc / "meminfo"),
            "vmstat": {k: v for k, v in counters(proc / "vmstat").items()
                       if k in ("pswpin", "pswpout", "pgmajfault", "oom_kill")},
            "pressure": {kind: (proc / "pressure" / kind).read_text().strip()
                         for kind in ("memory", "cpu", "io")},
            "processes": sorted(processes, key=lambda row: -row["rss_kib"])[:12],
            "cgroups": groups, "errors": errors}


def record(root, data):
    root.mkdir(parents=True, exist_ok=True)
    temporary = root / "latest.tmp"
    temporary.write_text(json.dumps(data) + "\n")
    temporary.replace(root / "latest.json")
    history = root / "history.jsonl"
    if history.exists() and history.stat().st_size > 10 * 1024 * 1024:
        history.replace(root / "history.previous.jsonl")
    with history.open("a") as output:
        output.write(json.dumps(data) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    while True:
        data = snapshot()
        record(args.root, data)
        if args.once:
            print(json.dumps(data))
            return
        time.sleep(15)


if __name__ == "__main__":
    main()
