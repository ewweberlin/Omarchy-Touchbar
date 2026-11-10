"""Hardware and system samplers.

Plain sysfs/procfs reads -- no external tools, no root. Each sampler keeps the
state it needs to turn a counter into a rate and degrades to None when the
underlying source is missing (e.g. running the preview on another machine).
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path

HWMON = Path("/sys/class/hwmon")
DRM = Path("/sys/class/drm")


def _read(path: Path | str) -> str | None:
    try:
        return Path(path).read_text(encoding="ascii", errors="replace").strip()
    except OSError:
        return None


def _read_int(path: Path | str) -> int | None:
    value = _read(path)
    if value is None:
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def _hwmon_dir(name: str) -> Path | None:
    try:
        entries = list(HWMON.iterdir())
    except OSError:
        return None
    for entry in entries:
        if _read(entry / "name") == name:
            return entry
    return None


def _glob_first(pattern: str) -> Path | None:
    matches = sorted(Path("/").glob(pattern.lstrip("/")))
    return matches[0] if matches else None


def _cpu_times() -> tuple[int, int] | None:
    line = _read("/proc/stat")
    if not line:
        return None
    first = line.splitlines()[0].split()
    if not first or first[0] != "cpu":
        return None
    try:
        values = [int(v) for v in first[1:]]
    except ValueError:
        return None
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return sum(values), idle


class CpuSampler:
    def __init__(self) -> None:
        self._prev = _cpu_times()
        self.temp_path = None
        coretemp = _hwmon_dir("coretemp")
        if coretemp:
            # Prefer the package sensor; some kernels expose only cores.
            for label in sorted(coretemp.glob("temp*_label")):
                if "Package" in (_read(label) or ""):
                    self.temp_path = label.with_name(label.name.replace("_label", "_input"))
                    break
            if self.temp_path is None:
                inputs = sorted(coretemp.glob("temp*_input"))
                self.temp_path = inputs[0] if inputs else None

    def sample(self) -> tuple[float | None, float | None]:
        usage = None
        current = _cpu_times()
        if current and self._prev:
            total_delta = current[0] - self._prev[0]
            idle_delta = current[1] - self._prev[1]
            if total_delta > 0:
                usage = max(0.0, min(100.0, 100.0 * (1 - idle_delta / total_delta)))
        self._prev = current
        temp = None
        if self.temp_path:
            raw = _read_int(self.temp_path)
            if raw is not None:
                temp = raw / 1000.0
        return usage, temp


class GpuSampler:
    """GPU load from per-client i915 engine busy counters in fdinfo.

    i915 exposes `drm-engine-render` per DRM client. Summing the per-client
    deltas over wall time gives utilisation without root and without
    intel-gpu-tools (which is not installed here).
    """

    def __init__(self) -> None:
        self._prev: dict[str, int] = {}
        self._prev_ts = time.monotonic()
        self.clock_path = _glob_first("/sys/class/drm/card*/gt_cur_freq_mhz")
        self.max_path = _glob_first("/sys/class/drm/card*/gt_max_freq_mhz")
        self._uid = os.getuid()

    def _scan(self) -> dict[str, int]:
        busy: dict[str, int] = {}
        try:
            pids = [p for p in os.listdir("/proc") if p.isdigit()]
        except OSError:
            return busy
        for pid in pids:
            base = f"/proc/{pid}"
            try:
                if os.stat(base).st_uid != self._uid:
                    continue
                fds = os.listdir(base + "/fd")
            except OSError:
                continue
            for fd in fds:
                try:
                    target = os.readlink(f"{base}/fd/{fd}")
                except OSError:
                    continue
                if "/dev/dri/" not in target:
                    continue
                try:
                    with open(f"{base}/fdinfo/{fd}", encoding="ascii", errors="replace") as fh:
                        data = fh.read(4096)
                except OSError:
                    continue
                client = None
                render_ns = None
                for line in data.splitlines():
                    if line.startswith("drm-client-id:"):
                        client = line.split(":", 1)[1].strip()
                    elif line.startswith("drm-engine-render:"):
                        try:
                            render_ns = int(line.split(":", 1)[1].split()[0])
                        except (ValueError, IndexError):
                            render_ns = None
                if client is not None and render_ns is not None:
                    busy[client] = max(render_ns, busy.get(client, 0))
        return busy

    def sample(self) -> tuple[float | None, float | None]:
        now = time.monotonic()
        busy = self._scan()
        usage = None
        elapsed = now - self._prev_ts
        if elapsed > 0 and self._prev:
            delta_ns = 0
            for client, value in busy.items():
                previous = self._prev.get(client)
                if previous is not None and value >= previous:
                    delta_ns += value - previous
            usage = max(0.0, min(100.0, 100.0 * (delta_ns / 1e9) / elapsed))
        self._prev = busy
        self._prev_ts = now
        mhz = float(_read_int(self.clock_path)) if self.clock_path else None
        return usage, mhz


class RamSampler:
    def sample(self) -> tuple[float | None, float | None, float | None]:
        data = _read("/proc/meminfo")
        if not data:
            return None, None, None
        values = {}
        for line in data.splitlines():
            key, _, rest = line.partition(":")
            try:
                values[key] = int(rest.strip().split()[0]) / 1024.0 / 1024.0
            except (ValueError, IndexError):
                continue
        total = values.get("MemTotal")
        available = values.get("MemAvailable")
        if not total or available is None:
            return None, None, None
        used = max(0.0, total - available)
        return 100.0 * used / total, used, total


class FanSampler:
    def __init__(self) -> None:
        # applesmc exposes fans through the ACPI device, and the hwmon
        # directory for it has no attribute files of its own, so look one level
        # down via the hwmon `device` link.
        self.fans: list[tuple[Path, Path | None]] = []
        for hwmon in sorted(HWMON.glob("hwmon*")):
            for candidate in (hwmon, hwmon / "device"):
                for fan in sorted(candidate.glob("fan*_input")):
                    maximum = fan.with_name(fan.name.replace("_input", "_max"))
                    self.fans.append((fan, maximum if maximum.exists() else None))
            if self.fans:
                break

    def sample(self) -> tuple[float | None, float | None]:
        best: tuple[float, float] | None = None
        for fan, maximum in self.fans:
            rpm = _read_int(fan)
            if rpm is None:
                continue
            limit = _read_int(maximum) if maximum else None
            # Report the fastest fan against its own ceiling.
            if best is None or rpm > best[0]:
                best = (float(rpm), float(limit or 7000))
        if best is None:
            return None, None
        return best


class NetSampler:
    def __init__(self) -> None:
        self.interface = self._default_interface()
        self._prev = self._read_counters()
        self._prev_ts = time.monotonic()

    @staticmethod
    def _default_interface() -> str | None:
        data = _read("/proc/net/route")
        if not data:
            return None
        best = None
        for line in data.splitlines()[1:]:
            fields = line.split()
            if len(fields) < 8 or fields[1] != "00000000":
                continue
            try:
                metric = int(fields[6])
            except ValueError:
                metric = 0
            if best is None or metric < best[0]:
                best = (metric, fields[0])
        return best[1] if best else None

    def _read_counters(self) -> tuple[int, int] | None:
        if not self.interface:
            self.interface = self._default_interface()
            if not self.interface:
                return None
        base = Path("/sys/class/net") / self.interface / "statistics"
        rx = _read_int(base / "rx_bytes")
        tx = _read_int(base / "tx_bytes")
        if rx is None or tx is None:
            return None
        return (rx, tx)

    def sample(self) -> tuple[float | None, float | None]:
        now = time.monotonic()
        current = self._read_counters()
        down = up = None
        elapsed = now - self._prev_ts
        if current and self._prev and elapsed > 0:
            down = max(0.0, (current[0] - self._prev[0]) / elapsed)
            up = max(0.0, (current[1] - self._prev[1]) / elapsed)
        self._prev = current
        self._prev_ts = now
        return down, up


class DiskSampler:
    def __init__(self, path: str = "/") -> None:
        self.path = path

    def sample(self) -> tuple[float | None, float | None]:
        try:
            stats = os.statvfs(self.path)
        except OSError:
            return None, None
        total = stats.f_blocks * stats.f_frsize
        free = stats.f_bavail * stats.f_frsize
        if total <= 0:
            return None, None
        used = total - free
        return 100.0 * used / total, free / 1024**3


@dataclass
class Sample:
    cpu_pct: float | None = None
    cpu_temp: float | None = None
    gpu_pct: float | None = None
    gpu_mhz: float | None = None
    ram_pct: float | None = None
    ram_used_gb: float | None = None
    ram_total_gb: float | None = None
    fan_rpm: float | None = None
    fan_max: float | None = None
    net_down: float | None = None
    net_up: float | None = None
    disk_pct: float | None = None
    disk_free_gb: float | None = None

    def as_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class Stats:
    interval: float = 1.0
    sample: Sample = field(default_factory=Sample)
    _last: float = 0.0
    _cpu: CpuSampler = field(default_factory=CpuSampler)
    _gpu: GpuSampler = field(default_factory=GpuSampler)
    _ram: RamSampler = field(default_factory=RamSampler)
    _fan: FanSampler = field(default_factory=FanSampler)
    _net: NetSampler = field(default_factory=NetSampler)
    _disk: DiskSampler = field(default_factory=DiskSampler)

    def maybe_refresh(self, now: float) -> bool:
        if now - self._last < self.interval:
            return False
        self._last = now
        self.refresh()
        return True

    def refresh(self) -> Sample:
        s = self.sample
        s.cpu_pct, s.cpu_temp = self._cpu.sample()
        s.gpu_pct, s.gpu_mhz = self._gpu.sample()
        s.ram_pct, s.ram_used_gb, s.ram_total_gb = self._ram.sample()
        s.fan_rpm, s.fan_max = self._fan.sample()
        s.net_down, s.net_up = self._net.sample()
        s.disk_pct, s.disk_free_gb = self._disk.sample()
        return s
