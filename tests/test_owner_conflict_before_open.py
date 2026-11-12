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
    sb.set_process_owner("desktop")
    yield path
    sb._OWNED_HERE.clear()
    sb.set_process_owner("desktop")


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


class _FakeHardware(sb.SDRBackend):
    """只走占用判据、不碰硬件的后端；``opened`` 记录「真的进入开硬件」。"""

    def __init__(self, owner_key: str):
        super().__init__(sb.SDRDevice(
            device_type="fake", device_id=owner_key, name=owner_key,
            frequency_range=(1.0e6, 6.0e9),
            sample_rate_range=(1.0e5, 3.2e6), max_gain=0.0))
        self._owner_device_id = owner_key
        self.opened = 0

    def _open_hardware(self) -> bool:
        self.opened += 1
        self.status.connected = True
        return True


def _assert_conflict_text(err: str, pid: int) -> None:
    """冲突文案：写明持有者与 pid，且不被桌面分类表二次改写。"""
    assert "AI/MCP 进程" in err, err
    assert f"pid {pid}" in err, err
    assert "权限" not in err and "USB" not in err, err
    assert error_remedy.classify_connect_error(err) is None, err
    assert error_remedy.humanize_connect_error(err) == err


def test_device_owned_by_this_process_is_allowed(_registry):
    """自己登记的占用不算冲突：同 pid 再 claim 天然覆盖，直接开硬件。"""
    _seed(_registry, "rtlsdr|serial:777", os.getpid(),
          sb._proc_start_time(os.getpid()), owner=sb.process_owner(),
          aliases=["rtlsdr|index:0"])

    be = _FakeHardware("rtlsdr|index:0")
    assert be.connect() is True, be.status.error
    assert be.opened == 1


def test_empty_registry_does_not_block_connect(_registry):
    """没有登记 = 没人占：不能凭空拒绝（登记表没数据不等于被占）。"""
    be = _FakeHardware("rtlsdr|index:0")
    assert be.connect() is True
    assert be.opened == 1


def test_unreadable_registry_refuses_as_unknown_not_busy(_registry,
                                                         monkeypatch):
    """登记不可读：同样拒绝，但结论必须是「读不出来」而不是「被程序占用」。

    两者都 fail-closed，补救动作却不同：前者要修/移除登记文件，后者要去
    关另一个程序。反证：把 ``_claim_hardware`` 的失败判据改成「未知当空闲」，
    本用例立刻红（``connect() True``、``opened == 1``）。
    """
    _registry.write_bytes(b'{"owners": ')
    monkeypatch.setattr(sb, "_backup_corrupt_registry", lambda reason: None)

    be = _FakeHardware("rtlsdr|index:0")
    assert be.connect() is False, "登记不可读时必须 fail-closed"
    assert be.opened == 0, "判据未通过仍进入了 open 路径"

    err = be.status.error
    assert "登记表不可用" in err and "无法判断" in err, err
    assert "进程仍在运行" not in err, f"没读出来却被说成有持有者：{err}"
    assert "pid" not in err, err
    # 不是占用冲突：报 busy 会让界面引导去关 SDR#/GQRX，方向正好相反。
    assert be.status.error_category == "unknown", be.status.error_category
    assert error_remedy.classify_connect_error(err) is None, err
    assert error_remedy.humanize_connect_error(err) == err


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

        ok, holder = sb.claim_active_owner(
            sb.process_owner(), "rtlsdr|index:0", aliases=aliases)
        assert ok is False
        assert isinstance(holder, sb.AmbiguousOwners), holder

        msg = sb.owner_conflict_text(holder)
        assert "无法唯一确定设备" in msg, msg
        # 正文不嵌登记键：键是任意字符串，撞上分类关键词会被改写成相反的补救。
        assert "AAA" not in msg and "BBB" not in msg, msg
        assert "2 条" in msg, msg
        assert error_remedy.classify_connect_error(msg) is None, msg
        assert error_remedy.humanize_connect_error(msg) == msg
    finally:
        _kill(child)


# ────────────────── 四条 UI 路收敛到同一个判据：不在入口各自 claim ──────────────────
def _fake_hackrf(monkeypatch, opened) -> dict:
    """把 libhackrf 的 ctypes 后端换成记录器（不碰硬件）。

    记录器只在**通过占用判据之后**才会被构造——``opened == []`` 即证明
    这次连接没有进入 open 路径。
    """
    import mbdsdr_ai.hackrf_params as hp

    class _Ctls:
        def __init__(self, index, params):
            opened.append("hackrf")

        def connect(self):
            return True

        def set_sample_rate(self, hz):
            return True

        def set_frequency(self, hz):
            return True

        def set_lna_gain(self, db):
            return True

        def set_vga_gain(self, db):
            return True

        def get_frequency(self):
            return 98.0e6

        def get_sample_rate(self):
            return 2.048e6

        def disconnect(self):
            return None

    monkeypatch.setattr(hp, "HackRFBackend", _Ctls)
    return {"driver": "hackrf", "serial": "HACKRF-1", "label": "HackRF One",
            "device_args": {"index": 0, "driver": "hackrf"}}


def test_all_entry_paths_refuse_before_open(_registry, monkeypatch):
    """设备被占用时，面板/对话框/自动枚举哪条路都不许开硬件。

    四条 UI 路（含模块面板）各自构造自己的后端实例，最终都走
    ``SDRBackend.connect()``；判据只在那里，所以三条路一起被拦。

    反证：把 ``SDRBackend.connect`` 里的 ``_claim_hardware()`` 摘掉，本用例
    会有三条路径同时 ``opened``（同一个文件被打开三次），而不是只有面板那条红。
    """
    child = _spawn_child()
    try:
        start = sb._proc_start_time(child.pid)
        assert start is not None
        _seed(_registry, "hackrf|serial:HACKRF-1", child.pid, start,
              owner=sb.AI_OWNER)

        opened = []
        dev = _fake_hackrf(monkeypatch, opened)

        paths = ("设备面板", "连接对话框", "自动枚举")
        results = []
        for name in paths:
            backend = sb.build_backend_for_device(dev)
            assert backend is not None, name
            ok = backend.connect()
            results.append((name, ok, backend.status.error))

        # 反证口径：摘掉 backend 层的 claim 时，这里拿到的是 3 次真实 open
        # （opened 三条），而不是「面板那条红、其余两条绿」。
        assert opened == [], (
            f"后端判据失效时这些路径会同时双开：{opened}（共 {len(results)} 条路）")
        for name, ok, err in results:
            assert ok is False, f"{name} 在设备被占用时仍连上了"
            _assert_conflict_text(err, child.pid)
    finally:
        _kill(child)


# ─────────── HackRF / USRP / Soapy 同构接入：假后端已验，真机未验 ───────────
def test_hackrf_refuses_before_libhackrf(_registry, monkeypatch):
    """HackRF：假后端已验（判据生效、不构造 libhackrf 层）。真机未验。"""
    child = _spawn_child()
    try:
        start = sb._proc_start_time(child.pid)
        _seed(_registry, "hackrf|index:0", child.pid, start, owner=sb.AI_OWNER)

        opened = []
        _fake_hackrf(monkeypatch, opened)
        backend = sb.HackRFBackend(device_index=0)
        assert backend.connect() is False
        assert opened == [], "占用命中后仍构造了 libhackrf 后端"
        _assert_conflict_text(backend.status.error, child.pid)
    finally:
        _kill(child)


def test_usrp_refuses_before_uhd(_registry, monkeypatch):
    """USRP：假后端已验（判据生效、不进入 uhd.MultiUSRP）。真机未验。"""
    import types

    opened = []

    class _MultiUSRP:
        def __init__(self, args=""):
            opened.append("usrp")

    monkeypatch.setitem(sys.modules, "uhd", types.SimpleNamespace(
        usrp=types.SimpleNamespace(MultiUSRP=_MultiUSRP)))

    child = _spawn_child()
    try:
        start = sb._proc_start_time(child.pid)
        _seed(_registry, "usrp|label:auto", child.pid, start, owner=sb.AI_OWNER)

        backend = sb.USRPBackend()
        assert backend.connect() is False
        assert opened == [], "占用命中后仍进入了 uhd.MultiUSRP"
        _assert_conflict_text(backend.status.error, child.pid)
    finally:
        _kill(child)


def test_soapy_refuses_before_device_open(_registry, monkeypatch):
    """Soapy：假后端已验（判据生效、不进入 SoapySDR.Device）。真机未验。"""
    import types

    opened = []

    class _Device:
        def __init__(self, args):
            opened.append("soapy")

    monkeypatch.setitem(sys.modules, "SoapySDR", types.SimpleNamespace(Device=_Device))

    child = _spawn_child()
    try:
        start = sb._proc_start_time(child.pid)
        _seed(_registry, "rtlsdr|serial:S1", child.pid, start, owner=sb.AI_OWNER)

        backend = sb.SoapySDRBackend(
            device_args={"driver": "rtlsdr", "serial": "S1"},
            device_info={"driver": "rtlsdr", "serial": "S1"})
        assert backend.connect() is False
        assert opened == [], "占用命中后仍进入了 SoapySDR.Device"
        _assert_conflict_text(backend.status.error, child.pid)
    finally:
        _kill(child)


# ───────────── PlutoSDR：同一判据；USB 登记、网络 URI 不登记 ─────────────
def _fake_iio(monkeypatch, opened) -> None:
    """把 pylibiio 换成记录器；``opened`` 非空即证明真的进了 open 路径。"""
    import types

    class _Context:
        def __init__(self, uri):
            opened.append(uri)

    monkeypatch.setattr(sb.PlutoSDRBackend, "_try_import_iio",
                        classmethod(lambda cls: types.SimpleNamespace(Context=_Context)))
    monkeypatch.setattr(sb.PlutoSDRBackend, "_try_import_soapy",
                        classmethod(lambda cls: None))


def test_plutosdr_refuses_before_iio_open(_registry, monkeypatch):
    """Pluto（USB）：假后端已验（判据生效、不进入 iio.Context）。真机未验。

    走工厂构造，顺带验证 ``build_backend_for_device`` 给它填的身份键
    （``uri:usb:1.2.5``）与直接构造时算出的键一致——否则两方各说各话，
    互斥只在纸面上成立。
    """
    opened = []
    _fake_iio(monkeypatch, opened)

    child = _spawn_child()
    try:
        start = sb._proc_start_time(child.pid)
        _seed(_registry, "uri:usb:1.2.5", child.pid, start, owner=sb.AI_OWNER)

        backend = sb.build_backend_for_device({
            "driver": "plutosdr", "uri": "usb:1.2.5", "label": "PlutoSDR",
            "serial": "", "device_args": {"uri": "usb:1.2.5"}})
        assert isinstance(backend, sb.PlutoSDRBackend)
        assert backend.connect() is False
        assert opened == [], "占用命中后仍进入了 iio.Context"
        _assert_conflict_text(backend.status.error, child.pid)
    finally:
        _kill(child)


def test_plutosdr_network_uri_does_not_claim(_registry, monkeypatch):
    """``ip:`` 网络 URI 不参与本机互斥：不因本机登记被拦，也不登记。

    口径与 rtl_tcp 主机模式一致（网络设备由对端决定谁能连）；这条用例钉住
    「不登记」不是忘写，而是选择——否则第二个本机进程会拿到一条假冲突。
    """
    opened = []
    _fake_iio(monkeypatch, opened)

    child = _spawn_child()
    try:
        start = sb._proc_start_time(child.pid)
        _seed(_registry, "uri:ip:192.168.2.1", child.pid, start, owner=sb.AI_OWNER)

        backend = sb.PlutoSDRBackend(uri="ip:192.168.2.1")
        assert backend.hardware_owner_identity() is None
        # 无真机时 connect 必定失败，但失败原因必须是「没驱动/没设备」，
        # 不能是「被占用」。
        assert backend.connect() is False
        assert opened != [], "网络 URI 被放在 open 之前拦住了"
        assert backend.last_claim_conflict == ""
        assert "持有" not in backend.status.error, backend.status.error
    finally:
        _kill(child)


# ───── bladeRF / LimeSDR：不是 SDRBackend 子类，也走同一组登记函数 ─────
def test_bladerf_refuses_before_lib_load(_registry, monkeypatch):
    """bladeRF：假后端已验（判据生效、连 libbladeRF 都不加载）。真机未验。"""
    import mbdsdr_ai.bladerf_params as bp

    loaded = []
    monkeypatch.setattr(bp.BladeRFBackend, "_load_lib",
                        lambda self: loaded.append("libbladeRF") or None)

    child = _spawn_child()
    try:
        start = sb._proc_start_time(child.pid)
        _seed(_registry, "bladerf|label:auto", child.pid, start, owner=sb.AI_OWNER)

        be = bp.BladeRFBackend()
        assert be.connect() is False
        assert loaded == [], "占用命中后仍加载了 libbladeRF"
        _assert_conflict_text(be.last_error, child.pid)
        # 桌面端失败提示读的就是这条通道；没有它冲突文案会被泛化成别的原因。
        assert be.get_status().error == be.last_error
        # 拒绝时不得把占用写到自己名下（登记里仍应是那条 AI 记录）。
        holder = sb.get_active_owner("bladerf|label:auto")
        assert holder is not None and holder["pid"] == child.pid, holder
    finally:
        _kill(child)


def test_bladerf_claim_held_until_disconnect(_registry, monkeypatch):
    """开成功后占用留在登记里，disconnect 才归还（同一层的成对操作）。"""
    import mbdsdr_ai.bladerf_params as bp

    class _Lib:
        def bladerf_open(self, dev_ref, ident):
            dev_ref._obj.value = 0x1234     # c_void_p 被 byref 传入
            return bp.BLADERF_OK

        def bladerf_close(self, dev):
            return bp.BLADERF_OK

    monkeypatch.setattr(bp.BladeRFBackend, "_load_lib", lambda self: _Lib())

    be = bp.BladeRFBackend()
    assert be.connect() is True
    holder = sb.get_active_owner("bladerf|label:auto")
    assert holder is not None and holder["pid"] == os.getpid(), holder
    be.disconnect()
    assert sb.get_active_owner("bladerf|label:auto") is None


def test_limesdr_refuses_before_lib_load(_registry, monkeypatch):
    """LimeSDR：假后端已验（判据生效、连 libLimeSuite 都不加载）。真机未验。"""
    import mbdsdr_ai.limesuite_params as lp

    loaded = []
    monkeypatch.setattr(lp.LimeSDRBackend, "_load_lib",
                        lambda self: loaded.append("libLimeSuite") or None)

    child = _spawn_child()
    try:
        start = sb._proc_start_time(child.pid)
        _seed(_registry, "limesdr|index:0", child.pid, start, owner=sb.AI_OWNER)

        be = lp.LimeSDRBackend(device_index=0)
        assert be.connect() is False
        assert loaded == [], "占用命中后仍加载了 libLimeSuite"
        _assert_conflict_text(be.last_error, child.pid)
        assert be.get_status().error == be.last_error
        holder = sb.get_active_owner("limesdr|index:0")
        assert holder is not None and holder["pid"] == child.pid, holder
    finally:
        _kill(child)
