from importlib.util import spec_from_file_location, module_from_spec
import json
from pathlib import Path


def sampler():
    spec = spec_from_file_location("host_health", Path(__file__).parents[1] / "scripts/record_host_health.py")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_snapshot_preserves_boot_swap_process_and_oom_evidence(tmp_path):
    proc = tmp_path / "proc"
    groups = tmp_path / "groups"
    (proc / "sys/kernel/random").mkdir(parents=True)
    (proc / "sys/kernel/random/boot_id").write_text("boot-a\n")
    (proc / "uptime").write_text("19.4 20.1\n")
    (proc / "meminfo").write_text("MemTotal: 4096 kB\nMemAvailable: 128 kB\nSwapFree: 12 kB\n")
    (proc / "vmstat").write_text("pswpin 44\npswpout 99\noom_kill 3\nunrelated 8\n")
    (proc / "pressure").mkdir()
    for kind in ("cpu", "memory", "io"):
        (proc / "pressure" / kind).write_text("some avg10=60.00 total=44\n")
    for pid, rss, group in (("10", 150, "/agent"), ("11", 900, "/model")):
        (proc / pid).mkdir()
        (proc / pid / "status").write_text(f"Name:\tworker-{pid}\nVmRSS:\t{rss} kB\nVmSwap:\t88 kB\n")
        (proc / pid / "cgroup").write_text("0::" + group + "\n")
        (groups / group.lstrip("/")).mkdir(parents=True)
        (groups / group.lstrip("/") / "memory.events").write_text("oom 2\noom_kill 1\n")
    data = sampler().snapshot(proc, groups)
    assert data["boot_id"] == "boot-a"
    assert data["memory_kib"]["MemAvailable"] == 128
    assert data["vmstat"] == {"pswpin": 44, "pswpout": 99, "oom_kill": 3}
    assert data["processes"][0]["pid"] == 11
    assert data["processes"][0]["swap_kib"] == 88
    assert data["cgroups"]["/model"]["memory.events"] == "oom 2\noom_kill 1"
    assert data["errors"] == []


def test_record_survives_new_boot_and_bounds_history(tmp_path):
    module = sampler()
    module.record(tmp_path, {"boot_id": "previous"})
    history = tmp_path / "history.jsonl"
    history.write_text("x" * (10*1024*1024 + 1))
    module.record(tmp_path, {"boot_id": "new"})
    assert (tmp_path / "history.previous.jsonl").stat().st_size == 10*1024*1024 + 1
    assert json.loads((tmp_path / "latest.json").read_text()) == {"boot_id": "new"}
    assert json.loads(history.read_text()) == {"boot_id": "new"}
    assert not (tmp_path / "latest.tmp").exists()
