"""Read-only facade for Binance's official baw tracker CLI.

No shell, wallet action, trading permission or login management. CLI is optional.
Official reference: skills/binance-web3/binance-wallet-tracker/references/cli.md
"""
from __future__ import annotations

import json
import re
from pathlib import Path
import shutil
import subprocess
from typing import Any

CHAINS = frozenset({"56", "CT_501", "8453", "1"})
PUBLIC_TAGS = frozenset({"smy", "kol"})
MAX_RESULT_BYTES = 2_000_000


class TrackerCliUnavailable(RuntimeError):
    pass


class TrackerCliFailure(RuntimeError):
    pass


def ensure_chain(chain: str) -> str:
    chain = str(chain)
    if chain not in CHAINS:
        raise ValueError("unsupported Binance wallet-tracker chain")
    return chain


def _decode_cli(output: bytes) -> Any:
    if len(output) > MAX_RESULT_BYTES:
        raise TrackerCliFailure("tracker output exceeds data budget")
    text = output.decode("utf-8")
    try:
        return json.loads(text)
    except ValueError as exc:
        raise TrackerCliFailure("tracker returned non-JSON output") from exc


class BawTrackerCli:
    def __init__(self, binary: str = "baw", *, executor=subprocess.run):
        self.binary = binary
        self.executor = executor

    def available(self) -> bool:
        return bool(shutil.which(self.binary) or Path(self.binary).is_file())

    def _run(self, args: list[str], *, timeout=22) -> Any:
        if not self.available():
            raise TrackerCliUnavailable("baw CLI missing; install only after approved setup")
        command = [self.binary, "tracker", *args, "--json"]
        try:
            proc = self.executor(command, capture_output=True, timeout=timeout,
                                 check=False)
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            raise TrackerCliFailure(type(exc).__name__) from exc
        if proc.returncode:
            raise TrackerCliFailure(f"baw tracker exited {proc.returncode}")
        return _decode_cli(proc.stdout)

    def token_query(self, chain="56", tag="smy", *, period="24h", group_id=None):
        if period not in {"1m", "5m", "1h", "4h", "24h"}:
            raise ValueError("invalid token monitor period")
        args = ["token", "query", "-c", ensure_chain(chain), "--period", period,
                "--token-size", "20"]
        args += _scope_args(tag, group_id)
        return self._run(args)

    def trade_query(self, chain="56", tag="smy", *, group_id=None):
        return self._run(["tx", "query", "-c", ensure_chain(chain),
                          *_scope_args(tag, group_id)])

    def follow_list(self, chain="56"):
        return self._run(["follow", "-c", ensure_chain(chain)])

    def group_list(self, chain="56"):
        return self._run(["group", "list", "-c", ensure_chain(chain)])

    def address_list(self, chain="56", *, group_id: int, page=1):
        if not isinstance(group_id, int) or group_id < 1 or not (1 <= page <= 50):
            raise ValueError("invalid group or page")
        return self._run(["address", "list", "-c", ensure_chain(chain),
                          "-g", str(group_id), "--page", str(page), "--size", "20"])


def _scope_args(tag: str | None, group_id: int | None) -> list[str]:
    if group_id is not None:
        if tag is not None or not isinstance(group_id, int) or group_id < 1:
            raise ValueError("private group and public tag are mutually exclusive")
        return ["--group-id", str(group_id)]
    if tag not in PUBLIC_TAGS:
        raise ValueError("public tracker queries need tag smy/kol")
    return ["--tag-type", tag]


def ws_args(*, mode="smy", chain="56", duration=15, address=None,
            address_list=None, continuous=False) -> list[str]:
    """Build only documented read-only subscriptions; finite duration by default."""
    if continuous and duration != 0:
        raise ValueError("continuous WS must specify duration=0")
    if not continuous and not 1 <= duration <= 60:
        raise ValueError("bounded WS duration must be 1..60 seconds")
    args = ["ws"]
    if mode == "smy":
        args.append("--smy")
    elif mode == "kol":
        args.extend(["--kol", "-c", ensure_chain(chain)])
    elif mode == "wallet":
        name = {"56": "BSC", "CT_501": "SOL", "8453": "BASE", "1": "ETH"}[ensure_chain(chain)]
        args.extend(["--wallet", name])
    elif mode == "address":
        if not address or len(address) > 100:
            raise ValueError("address required")
        args.extend(["--address", address, "-c", ensure_chain(chain)])
    elif mode == "address-list":
        if not address_list or not Path(address_list).is_file():
            raise ValueError("existing address list required")
        args.extend(["--address-list", str(address_list), "-c", ensure_chain(chain)])
    elif mode == "following":
        args.extend(["--following", "-c", ensure_chain(chain)])
    else:
        raise ValueError("unsupported tracker WS mode")
    return args + ["--duration", str(duration)]


def capture_ws(cli: BawTrackerCli, *, mode="smy", chain="56", duration=15,
               address=None, address_list=None, executor=subprocess.run) -> list[dict]:
    """Short WebSocket session, never a main-loop resident process."""
    args = ws_args(mode=mode, chain=chain, duration=duration, address=address,
                   address_list=address_list)
    if not cli.available():
        raise TrackerCliUnavailable("baw CLI is unavailable")
    try:
        process = executor([cli.binary, "tracker", *args, "--json"],
                           capture_output=True, timeout=duration + 15, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise TrackerCliFailure(type(exc).__name__) from exc
    if process.returncode:
        raise TrackerCliFailure(f"tracker ws returned {process.returncode}")
    if len(process.stdout) > MAX_RESULT_BYTES:
        raise TrackerCliFailure("WS data exceeded 2MB capture limit")
    rows = []
    for line in process.stdout.splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if isinstance(data, dict):
            rows.append(data)
    return rows[:200]


def minimum_ws_version(cli: BawTrackerCli, *, executor=subprocess.run) -> tuple[int, ...]:
    """Continuous streams depend on the upstream CLI's built-in reconnect (>=1.9.1)."""
    if not cli.available():
        raise TrackerCliUnavailable("baw CLI missing")
    try:
        result = executor([cli.binary, "--version"], capture_output=True,
                          check=False, timeout=8)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TrackerCliFailure("cannot inspect CLI version") from exc
    value = result.stdout.decode("utf-8", "replace")
    versions = re.findall(r"(?<![0-9])([0-9]+)[.]([0-9]+)[.]([0-9]+)", value)
    if result.returncode or not versions:
        raise TrackerCliFailure("CLI version check did not return semver")
    version = tuple(map(int, versions[-1]))
    if version < (1, 9, 1):
        raise TrackerCliFailure("continuous WS requires baw >=1.9.1")
    return version


def stream_ws(cli: BawTrackerCli, store, *, mode="smy", chain="56",
              address=None, address_list=None, popen=subprocess.Popen,
              version_executor=subprocess.run, now_fn=None) -> dict:
    """Consume the official auto-reconnecting, unbounded WS stream.

    Process-level restart belongs to an external supervisor; do not implement
    a second reconnect loop around baw >=1.9.1.
    """
    from datetime import datetime, timezone
    minimum_ws_version(cli, executor=version_executor)
    args = ws_args(mode=mode, chain=chain, duration=0, address=address,
                   address_list=address_list, continuous=True)
    process = popen([cli.binary, "tracker", *args, "--json"],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    counts = {"new": 0, "updated": 0, "unchanged": 0, "invalid": 0}
    clock = now_fn or (lambda: datetime.now(timezone.utc))
    try:
        for raw in process.stdout:
            if len(raw) > 32768:
                counts["invalid"] += 1
                continue
            try:
                value = json.loads(raw)
            except ValueError:
                counts["invalid"] += 1
                continue
            if not isinstance(value, dict):
                continue
            current = store.ingest([value], kind=mode, now=clock())
            for key in counts:
                counts[key] += current[key]
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    if process.returncode:
        raise TrackerCliFailure(f"continuous wallet WebSocket stopped: {process.returncode}")
    return counts
