"""What the machine HexDeck runs on is doing, read straight from ``/proc``.

No library and no agent: every number here is a file the kernel writes. The
one thing worth knowing is *whose* files they are. Inside a container ``/proc``
is the container's own view, so the memory is the container's limit and the
disk is the image's layer. Mounting the host's ``/proc`` and ``/sys`` read-only
under ``/host`` makes the same code read the machine instead:

    volumes:
      - /proc:/host/proc:ro
      - /sys:/host/sys:ro

Which of the two was read is carried out with the numbers, so the card can say
so rather than quietly showing the wrong machine.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

#: Where the host's own files are expected when somebody mounted them.
HOST_PROC = Path("/host/proc")
HOST_SYS = Path("/host/sys")


def roots(base: Path | None = None) -> tuple[Path, Path, bool]:
    """``(proc, sys, is_the_host)``: the host's files when they are mounted.

    ``base`` exists for the tests, which build a small tree of the handful of
    files this module reads; nothing in the app passes it.
    """
    if base is not None:
        return base / "proc", base / "sys", (base / "proc" / "host_marker").exists()
    if (HOST_PROC / "stat").is_file():
        return HOST_PROC, HOST_SYS if HOST_SYS.is_dir() else Path("/sys"), True
    return Path("/proc"), Path("/sys"), False


def _text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def cpu_sample(proc: Path) -> tuple[float, float] | None:
    """``(busy, total)`` jiffies from the first line of ``/proc/stat``."""
    line = _text(proc / "stat").splitlines()[:1]
    if not line or not line[0].startswith("cpu "):
        return None
    numbers = [float(part) for part in line[0].split()[1:] if part.replace(".", "").isdigit()]
    if len(numbers) < 4:
        return None
    total = sum(numbers)
    idle = numbers[3] + (numbers[4] if len(numbers) > 4 else 0.0)
    return total - idle, total


def cpu_percent(proc: Path, previous: tuple[float, float] | None) -> tuple[float | None, tuple[float, float] | None]:
    """How busy the processors were since the last look.

    Without a previous sample this is the average since boot, which is a real
    number and not a zero: a card that has just been added says something.
    """
    now = cpu_sample(proc)
    if now is None:
        return None, None
    if previous is None or now[1] <= previous[1]:
        return (round(100.0 * now[0] / now[1], 1) if now[1] else None), now
    busy, total = now[0] - previous[0], now[1] - previous[1]
    return (round(100.0 * busy / total, 1) if total else None), now


def _meminfo(proc: Path) -> dict[str, float]:
    values: dict[str, float] = {}
    for line in _text(proc / "meminfo").splitlines():
        name, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].replace(".", "").isdigit():
            values[name.strip()] = float(parts[0]) * 1024  # every line is in kB
    return values


def memory(proc: Path) -> dict[str, float] | None:
    """Total, used and the percentage, counting cache as free like ``free -h``."""
    values = _meminfo(proc)
    total = values.get("MemTotal") or 0.0
    if not total:
        return None
    # MemAvailable is the kernel's own answer to "how much can a program still
    # get"; older kernels without it fall back to free plus what is reclaimable.
    available = values.get("MemAvailable")
    if available is None:
        available = values.get("MemFree", 0.0) + values.get("Cached", 0.0) + values.get("Buffers", 0.0)
    used = max(0.0, total - available)
    swap_total = values.get("SwapTotal") or 0.0
    swap_used = max(0.0, swap_total - values.get("SwapFree", 0.0))
    return {
        "total": total,
        "used": used,
        "percent": round(100.0 * used / total, 1),
        "swap_total": swap_total,
        "swap_used": swap_used,
        "swap_percent": round(100.0 * swap_used / swap_total, 1) if swap_total else 0.0,
    }


def load(proc: Path) -> tuple[float, float, float] | None:
    parts = _text(proc / "loadavg").split()
    if len(parts) < 3:
        return None
    try:
        return float(parts[0]), float(parts[1]), float(parts[2])
    except ValueError:
        return None


def cores(proc: Path) -> int:
    """How many processors the numbers above are shared by."""
    lines = [line for line in _text(proc / "cpuinfo").splitlines() if line.startswith("processor")]
    return len(lines) or (os.cpu_count() or 1)


def uptime(proc: Path) -> float | None:
    parts = _text(proc / "uptime").split()
    try:
        return float(parts[0]) if parts else None
    except ValueError:
        return None


def temperature(sys_root: Path) -> tuple[float, str] | None:
    """The warmest sensor the machine offers, and what it calls itself.

    Thermal zones first, because they are there on a Pi and on most boards;
    hwmon second, which is where a desktop's package sensor usually sits.
    """
    warmest: tuple[float, str] | None = None
    for zone in sorted((sys_root / "class" / "thermal").glob("thermal_zone*")):
        raw = _text(zone / "temp").strip()
        if not raw.lstrip("-").isdigit():
            continue
        celsius = int(raw) / 1000.0
        name = _text(zone / "type").strip() or zone.name
        if -50.0 < celsius < 150.0 and (warmest is None or celsius > warmest[0]):
            warmest = (round(celsius, 1), name)
    if warmest is not None:
        return warmest
    for chip in sorted((sys_root / "class" / "hwmon").glob("hwmon*")):
        label = _text(chip / "name").strip() or chip.name
        for sensor in sorted(chip.glob("temp*_input")):
            raw = _text(sensor).strip()
            if not raw.lstrip("-").isdigit():
                continue
            celsius = int(raw) / 1000.0
            if -50.0 < celsius < 150.0 and (warmest is None or celsius > warmest[0]):
                warmest = (round(celsius, 1), _text(sensor.with_name(sensor.name.replace("_input", "_label"))).strip() or label)
    return warmest


def disk(path: str) -> dict[str, float] | None:
    """Usage of one file system, as the process that asks can see it."""
    try:
        stats = os.statvfs(path)
    except (OSError, AttributeError, ValueError):
        return None
    total = float(stats.f_blocks * stats.f_frsize)
    if not total:
        return None
    free = float(stats.f_bavail * stats.f_frsize)
    used = total - float(stats.f_bfree * stats.f_frsize)
    return {"total": total, "used": used, "free": free, "percent": round(100.0 * used / total, 1)}


def read(previous: tuple[float, float] | None = None, disk_path: str = "/", base: Path | None = None) -> dict[str, Any]:
    """Everything the card shows, in one pass over the files."""
    proc, sys_root, is_host = roots(base)
    if not (proc / "stat").is_file():
        return {"available": False, "host": False, "proc": str(proc)}
    percent, sample = cpu_percent(proc, previous)
    return {
        "available": True,
        "host": is_host,
        "proc": str(proc),
        "at": time.time(),
        "cpu": percent,
        "cpu_sample": sample,
        "cores": cores(proc),
        "memory": memory(proc),
        "load": load(proc),
        "uptime": uptime(proc),
        "temperature": temperature(sys_root),
        "disk": disk(disk_path) if disk_path else None,
    }


def gigabytes(value: float) -> str:
    """Bytes as the two units a card has room for."""
    if value >= 1024.0**4:
        return f"{value / 1024.0 ** 4:.1f} TB"
    return f"{value / 1024.0 ** 3:.1f} GB"


def duration(seconds: float) -> str:
    """``13d 4h``, how long the machine has been up, in the two largest units."""
    minutes, hours = int(seconds // 60) % 60, int(seconds // 3600) % 24
    days = int(seconds // 86400)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"
