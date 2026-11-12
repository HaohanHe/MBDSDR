# SPDX-License-Identifier: MIT
"""反方向的冲突：AI 已登记持有 → 桌面去连必须先报「谁持有」。

真机暴露的缺陷不是「拒绝得不对」，而是**补救方向指错**：桌面端的部分连接
入口不查登记就 open，libusb 返回 ``LIBUSB_ERROR_ACCESS``，而桌面端的有序
分类表把「权限」排在「占用」之前——用户被引去改 udev / 装 WinUSB，
真实原因却是另一个进程持有设备，照着改一百遍也不会好。

修法是顺序对调：**登记优先（我们自己的确定状态），libusb 错误靠后（现象）**。
本文件把这条顺序和用户可见的结论一起钉住：

- 命中登记 → 不进入 open 路径，``status.error`` 直接写明持有者与 pid；
- 这句话必须原样透传（``classify_connect_error`` 判不出码，
  ``humanize_connect_error`` 不替换），不能被泛化成「权限不足」或
  「请关闭 SDR#/GQRX」。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import error_remedy  # noqa: E402
import mbdsdr_ai.sdr_backend as sb  # noqa: E402


@pytest.fixture(autouse=True)
def _registry(tmp_path, monkeypatch):
    path = tmp_path / "device_owner.json"
    monkeypatch.setenv(sb.OWNER_REGISTRY_ENV, str(path))
    sb._OWNED_HERE.clear()
    yield path
    sb._OWNED_HERE.clear()


def _seed(path, device_id: str, pid: int, proc_start, owner: str,
          aliases=None) -> None:
    rec = {
        "owner": owner, "pid": pid, "proc_start": proc_start,
        "device_id": device_id, "claimed_at": time.time(),
    }
    if aliases:
        rec["aliases"] = list(aliases)
    path.write_text(json.dumps({
        "version": 1,
        "owners": {device_id: rec},
    }), encoding="utf-8")


def _spawn_child() -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])


def _kill(child: subprocess.Popen) -> None:
    if child.poll() is None:
        child.kill()
    child.wait(timeout=10)


def test_ai_owned_device_is_refused_before_open(_registry, monkeypatch):
    """AI 侧已登记持有 → 桌面 connect 直接报「由 AI/MCP 进程持有」，不 open。"""
    child = _spawn_child()
    try:
        # AI 侧按序列号登记（它 open 之前读得到），别名里带枚举序号——
        # 桌面端此刻读不到序列号，只能按 rtlsdr|index:0 去问。
        _seed(_registry, "rtlsdr|serial:777", child.pid,
              sb._proc_start_time(child.pid), owner=sb.AI_OWNER,
              aliases=["rtlsdr|index:0"])

        setup_calls = []
        monkeypatch.setattr("mbdsdr_ai.windows_setup.setup_rtlsdr_windows",
                            lambda *a, **k: setup_calls.append(1))

        backend = sb.RTLSDRBackend(device_index=0)
        assert backend.connect() is False

        # 顺序判据：登记命中就不许走到 open 路径。
        assert setup_calls == [], "登记命中时仍走到了打开设备的代码"
        assert backend.status.connected is False

        err = backend.status.error
        assert "AI/MCP 进程" in err, err
        assert f"pid {child.pid}" in err, err
        # 用户可见的结论必须是「谁持有」，不是「权限不足」。
        assert "权限" not in err and "USB" not in err, err

        # 桌面端的二次分类不能把它改写成别的补救方向。
        assert error_remedy.classify_connect_error(err) is None, err
        assert error_remedy.humanize_connect_error(err) == err
    finally:
        _kill(child)


def test_device_owned_by_this_process_is_allowed(_registry):
    """自己登记的占用不算冲突：桌面面板 claim 后自己 connect 必须放行。"""
    _seed(_registry, "rtlsdr|serial:777", os.getpid(),
          sb._proc_start_time(os.getpid()), owner="desktop",
          aliases=["rtlsdr|index:0"])

    assert sb.peer_owner_message("rtlsdr|index:0") is None


def test_empty_registry_does_not_block_connect(_registry):
    """没有登记 = 没人占：不能凭空拒绝（登记表没数据不等于被占）。"""
    assert sb.peer_owner_message("rtlsdr|index:0") is None


def test_ambiguous_alias_message_survives_desktop_classification(_registry):
    """歧义结论（无法唯一确定）同样不能被分类表改写成「权限不足」。"""
    child = _spawn_child()
    try:
        aliases = ["rtlsdr|index:0"]
        _seed(_registry, "rtlsdr|serial:AAA", child.pid,
              sb._proc_start_time(child.pid), owner="desktop", aliases=aliases)
        # 第二条：同一组别名 → 歧义
        path = _registry
        data = json.loads(path.read_text(encoding="utf-8"))
        data["owners"]["rtlsdr|serial:BBB"] = {
            "owner": "desktop", "pid": child.pid,
            "proc_start": sb._proc_start_time(child.pid),
            "device_id": "rtlsdr|serial:BBB", "claimed_at": time.time(),
            "aliases": list(aliases),
        }
        path.write_text(json.dumps(data), encoding="utf-8")

        msg = sb.peer_owner_message("rtlsdr|index:0")
        assert msg and "无法唯一确定设备" in msg, msg
        assert "AAA" in msg and "BBB" in msg, msg
        assert error_remedy.classify_connect_error(msg) is None, msg
        assert error_remedy.humanize_connect_error(msg) == msg
    finally:
        _kill(child)
