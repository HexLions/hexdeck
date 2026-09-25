"""The host card reads /proc, and says whose /proc it was.

Every test builds a small tree of the files the kernel writes, so the numbers
are the same on the machine that runs the suite as in a container.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.adapters import get_adapter
from app.adapters.base import AdapterError, Context
from app.services import hoststats

STAT = "cpu  1000 0 500 8000 100 0 0 0 0 0\ncpu0 500 0 250 4000 50 0 0 0 0 0\n"
MEMINFO = """MemTotal:       32000000 kB
MemFree:         2000000 kB
MemAvailable:   18000000 kB
Buffers:          500000 kB
Cached:         12000000 kB
SwapTotal:       4000000 kB
SwapFree:        3000000 kB
"""


def _tree(tmp_path: Path, *, host: bool = False, stat: str = STAT) -> Path:
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "stat").write_text(stat, encoding="utf-8")
    (proc / "meminfo").write_text(MEMINFO, encoding="utf-8")
    (proc / "loadavg").write_text("0.42 0.51 0.60 2/512 1234\n", encoding="utf-8")
    (proc / "uptime").write_text("1140000.00 8000000.00\n", encoding="utf-8")
    (proc / "cpuinfo").write_text("processor\t: 0\nprocessor\t: 1\n", encoding="utf-8")
    if host:
        (proc / "host_marker").write_text("", encoding="utf-8")
    zone = tmp_path / "sys" / "class" / "thermal" / "thermal_zone0"
    zone.mkdir(parents=True)
    (zone / "temp").write_text("46800\n", encoding="utf-8")
    (zone / "type").write_text("x86_pkg_temp\n", encoding="utf-8")
    return tmp_path


def test_the_numbers_come_out_of_the_files(tmp_path: Path) -> None:
    base = _tree(tmp_path)
    reading = hoststats.read(None, disk_path="", base=base)
    assert reading["available"] and reading["host"] is False
    # 9600 busy of 9600 + 8100 idle since boot.
    assert reading["cpu"] == pytest.approx(15.6, abs=0.2)
    assert reading["memory"]["percent"] == pytest.approx(43.8, abs=0.2)
    assert reading["memory"]["swap_percent"] == pytest.approx(25.0, abs=0.2)
    assert reading["load"] == (0.42, 0.51, 0.60)
    assert reading["cores"] == 2
    assert reading["temperature"] == (46.8, "x86_pkg_temp")
    assert hoststats.duration(reading["uptime"]) == "13d 4h"


def test_the_processor_share_is_the_difference_between_two_looks(tmp_path: Path) -> None:
    base = _tree(tmp_path)
    first = hoststats.read(None, disk_path="", base=base)
    # Between the two looks: 100 jiffies busy, 100 idle.
    (base / "proc" / "stat").write_text("cpu  1100 0 500 8100 100 0 0 0 0 0\n", encoding="utf-8")
    second = hoststats.read(first["cpu_sample"], disk_path="", base=base)
    assert second["cpu"] == pytest.approx(50.0, abs=0.1), "half of the jiffies since the last look were busy"


def test_the_mounted_files_are_recognised_as_the_machines_own(tmp_path: Path) -> None:
    assert hoststats.read(None, disk_path="", base=_tree(tmp_path, host=True))["host"] is True


def test_a_machine_without_proc_is_told_plainly(tmp_path: Path) -> None:
    assert hoststats.read(None, disk_path="", base=tmp_path / "nothing")["available"] is False


async def test_the_card_says_when_it_is_only_seeing_the_container(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base = _tree(tmp_path)
    monkeypatch.setattr(hoststats, "HOST_PROC", base / "proc")
    monkeypatch.setattr(hoststats, "HOST_SYS", base / "sys")
    ctx = Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)
    data = await get_adapter("core").fetch("host", {}, {"disk": ""}, ctx)
    assert data.primary["label"] == "CPU" and data.metrics["memory"] > 0
    assert data.meta["host"] is True, "the mounted files are the machine's own"
    assert not data.meta["notice"]
    labels = [row["label"] for row in data.secondary]
    assert labels[:2] == ["Memory", "Swap"] and "Temp" in labels and "Up" in labels


async def test_without_proc_the_card_names_the_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hoststats, "HOST_PROC", Path("/nowhere"))
    monkeypatch.setattr(hoststats, "roots", lambda base=None: (Path("/nowhere"), Path("/nowhere"), False))
    ctx = Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)
    with pytest.raises(AdapterError) as failure:
        await get_adapter("core").fetch("host", {}, {}, ctx)
    assert "/proc" in str(failure.value)


def test_the_demo_draws() -> None:
    assert get_adapter("core").demo("host", {}, 0).secondary
