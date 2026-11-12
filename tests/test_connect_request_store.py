# SPDX-License-Identifier: MIT
"""设备连接请求的单一持有者 + 身份键稳定性/唯一性证明。

背景（docs/redundancy-register.md §5.11.2 P2 / 用户 2026-11-12 裁定方案 A）：

    设备面板与模块面板各持一份 ``_sample_rate/_gain/_ppm/_bandwidth``，
    “先连哪块用哪份”，另一块静默显示旧值。

本文件守住四件事：

1. 身份键**唯一且稳定**：serial / uri 在重启与重枚举后不变；
2. 没有 serial 的设备**证明不了稳定**：键降级为 PROVISIONAL 并显式告警；
3. 撞键（两台设备同一键）必须**告警 + 区分**，绝不静默共用一份请求；
4. 两个面板读到的是同一份请求，载荷由 store 统一生成。
"""
import os
import re
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DESKTOP = os.path.join(_ROOT, "desktop")
for _p in (_ROOT, _DESKTOP):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402

import connect_request_store as store  # noqa: E402


def _rtl(index: int, serial: str = "") -> dict:
    """一台 RTL-SDR 的设备 dict（形状与 sdr_backend.list_devices 一致）。"""
    return {
        "driver": "rtlsdr",
        "label": f"RTL-SDR #{index} (R820T)",
        "serial": serial,
        "index": index,
        "freq_range": (24e6, 1766e6),
        "sample_rate_range": (225001.0, 3200000.0),
        "gain_range": (0.0, 49.6),
        "capability_status": {
            "sample_rate_range": "declared",
            "freq_range": "declared",
            "gain_range": "declared",
        },
        "device_args": {"driver": "rtlsdr", "index": index},
    }


@pytest.fixture(autouse=True)
def _clean_store():
    store.reset()
    yield
    store.reset()


# ---------------------------------------------------------------- 身份键证明
def test_serial_key_survives_restart_and_reenumeration():
    """有 serial 的设备：重启 / 重枚举后键不变，且两台设备键不同。"""
    devices = [_rtl(0, serial="REAL-0001"), _rtl(1, serial="REAL-0002")]
    first = store.resolve(devices)

    # 模拟重启：全新的 dict 对象、同样内容重新枚举一次。
    second = store.resolve([_rtl(0, serial="REAL-0001"),
                            _rtl(1, serial="REAL-0002")])

    assert [i.key for i in first] == [i.key for i in second], (
        "同一台设备重启后身份键变了，请求存储会凭空丢参数")

    # 拔插导致枚举序号重排（0↔1）：有序列号的设备键必须不跟序号走。
    reshuffled = store.resolve([_rtl(1, serial="REAL-0001"),
                                _rtl(0, serial="REAL-0002")])
    assert reshuffled[0].key == first[0].key
    assert reshuffled[1].key == first[1].key

    assert first[0].key != first[1].key, "两台有序列号的设备撞键"
    assert all(i.quality == store.STABLE for i in first)
    assert all(i.warning == "" for i in first)


def test_uri_key_survives_reenumeration():
    """Pluto 这类按 URI 定位的设备：URI 就是稳定身份。"""
    dev = {"driver": "plutosdr", "label": "PlutoSDR", "serial": "",
           "uri": "ip:192.168.2.1", "device_args": {"uri": "ip:192.168.2.1"}}
    a = store.resolve([dict(dev)])[0]
    b = store.resolve([dict(dev)])[0]
    assert a.key == b.key
    assert a.quality == store.STABLE
    assert a.warning == ""


def test_serialless_key_is_not_stable_and_must_warn():
    """无 serial 的 RTL：只能按枚举序号暂记——必须证明它在重枚举后会变。

    证明方式：拔掉序号 0 的设备后重新枚举，另一台物理设备序号从 1 变成 0，
    键随之改变。这正是“不能当稳定身份”的反证。
    """
    before = store.resolve([_rtl(0, serial=""), _rtl(1, serial="")])
    # 模拟拔掉 index 0 再重枚举：剩下的那台变成 index 0。
    after = store.resolve([_rtl(0, serial="")])

    assert before[1].key == "rtlsdr|index:1"
    assert after[0].key == "rtlsdr|index:0"
    assert before[1].key != after[0].key, (
        "无序列号设备的键在重枚举后没变——若真如此才能当稳定身份")
    assert before[0].quality == store.PROVISIONAL
    assert before[0].warning, "无序列号设备必须显式告警，不许静默当稳定身份"
    assert "序号" in before[0].warning


def test_two_serialless_same_model_devices_do_not_share_request():
    """两台同型号、serial 都为空的设备：键唯一，且请求互不串。"""
    idents = store.resolve([_rtl(0, serial=""), _rtl(1, serial="")])
    assert len({i.key for i in idents}) == 2, "两台空 serial 设备被压成同一个键"

    store.update(idents[0].key, sample_rate_hz=2_048_000,
                 gain_db=31, ppm=12, bandwidth_hz=150_000)
    other = store.request_for(idents[1].key)
    assert other.sample_rate_hz is None
    assert other.gain_db is None
    assert other.ppm is None
    assert other.bandwidth_hz is None, "一台设备的参数串到了另一台"


def test_identity_collision_warns_and_never_silently_shares(caplog):
    """反证：故意让两台设备映射到同一键，必须告警 + 区分，不许静默共用。"""
    twin_a = _rtl(0, serial="SAME-SERIAL")
    twin_b = _rtl(1, serial="SAME-SERIAL")

    with caplog.at_level("WARNING"):
        idents = store.resolve([twin_a, twin_b])

    assert len({i.key for i in idents}) == 2, (
        "撞键后仍然共用一个键 = 静默共用一份请求")
    assert all("身份键冲突" in i.warning for i in idents)
    assert any("身份键冲突" in r.message for r in caplog.records), (
        "撞键只改了键却没告警")

    store.update(idents[0].key, gain_db=40)
    assert store.request_for(idents[1].key).gain_db is None


def test_update_rejects_zero_sample_rate():
    key = store.resolve([_rtl(0, serial="A")])[0].key
    store.update(key, sample_rate_hz=2_048_000)
    store.update(key, sample_rate_hz=0)     # 0 Hz 会让后端崩，必须拒绝
    assert store.request_for(key).sample_rate_hz == 2_048_000


# ------------------------------------------------------------- 面板共享同一份
@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture
def panels(qapp, monkeypatch):
    from desktop.device_panel import DevicePanel
    from desktop.module_panel import ModulePanel

    # 测试不碰真实硬件：枚举由用例直接喂 _apply_devices。
    monkeypatch.setattr(DevicePanel, "refresh_devices_async", lambda self: None)
    monkeypatch.setattr(ModulePanel, "refresh_devices_async", lambda self: None)
    dp, mp = DevicePanel(), ModulePanel()
    yield dp, mp
    dp.close()
    mp.close()


def test_two_panels_read_the_same_request(panels):
    dp, mp = panels
    # 两个面板各自枚举一次 → dict 对象不同、内容相同（真实场景就是这样）。
    dp._apply_devices([_rtl(0, serial="REAL-0001")])
    mp._apply_devices([_rtl(0, serial="REAL-0001")])

    assert dp._identities[0].key == mp._identities[0].key, (
        "同一台设备在两个面板里派生出了不同的身份键")

    # 在设备面板改参数（用户编辑 → editingFinished）。
    dp.sr_edit.setText("2048000")
    dp.sr_edit.editingFinished.emit()
    dp.gain_edit.setText("31")
    dp.gain_edit.editingFinished.emit()
    dp.ppm_edit.setText("12")
    dp.ppm_edit.editingFinished.emit()

    # 模块面板没有重新选择设备，只是被请求变更通知刷新。
    assert mp.sr_combo.currentData() == 2_048_000, (
        "模块面板仍显示自己的旧采样率，设备面板的改动被静默丢弃")
    assert mp.gain_slider.value() == 31
    assert mp.bw_spin.value() == pytest.approx(150_000)

    captured = []
    mp.device_selected.connect(captured.append)
    mp._on_connect()

    assert captured, "模块面板没有发出连接请求"
    payload = captured[0]
    assert payload["_sample_rate"] == 2_048_000
    assert payload["_gain"] == 31.0
    assert payload["_ppm"] == 12, "设备面板设的 PPM 在模块面板连接时被丢掉"
    assert payload["_bandwidth"] == pytest.approx(150_000)


def test_module_panel_edit_is_visible_to_device_panel(panels):
    """反方向同样成立：模块面板改 → 设备面板渲染同一份。"""
    dp, mp = panels
    dp._apply_devices([_rtl(0, serial="REAL-0001")])
    mp._apply_devices([_rtl(0, serial="REAL-0001")])

    mp.gain_slider.setValue(44)
    assert dp.gain_edit.text() == "44"

    mp.bw_spin.setValue(200_000)
    assert store.request_for(mp._identities[0].key).bandwidth_hz == 200_000


# ------------------------------------------------------------------ 防回退守卫
def test_panels_do_not_write_connect_payload_keys_directly():
    """守卫：载荷四个键只许 connect_request_store 写，面板不得再自持副本。"""
    panel_files = [
        os.path.join(_DESKTOP, "device_panel.py"),
        os.path.join(_DESKTOP, "module_panel.py"),
    ]
    pattern = re.compile(
        r"""\[\s*["'](?:_sample_rate|_gain|_ppm|_bandwidth)["']\s*\]\s*=""")
    for path in panel_files:
        with open(path, "r", encoding="utf-8") as f:
            source = f.read()
        hits = pattern.findall(source)
        assert not hits, (
            f"{os.path.basename(path)} 又直接写连接载荷键 {hits}："
            "必须走 connect_request_store.update/fill_payload")

    store_source = open(os.path.join(_DESKTOP, "connect_request_store.py"),
                        encoding="utf-8").read()
    assert 'payload["_gain"]' in store_source
    assert 'payload["_ppm"]' in store_source
    assert 'payload["_bandwidth"]' in store_source


# ------------------------------------------------ 桌面端与 AI 必须算出同一个键
def test_desktop_and_ai_compute_the_same_identity_key():
    """同一份枚举，桌面端 store 与 AI sdr_backend 的键/质量/告警逐字相同。

    「桌面端占着、AI 被拒」完全建立在两侧键同名上：各写一套口径迟早漂移
    （AI 旧口径 ``rtl_0`` 连序列号都丢了）。桌面端现在直接委托 AI 侧实现，
    这条用例钉死两边输出一致，改任何一侧的口径都会红。
    """
    from mbdsdr_ai import sdr_backend as sb

    devices = [_rtl(0, serial="REAL-0001"), _rtl(1, serial=""), _rtl(2, serial="")]
    desktop = [(i.key, i.quality, i.warning) for i in store.resolve(devices)]
    ai = sb.resolve_device_identities(devices)
    assert desktop == ai


def test_collision_suffix_is_identical_on_both_sides():
    """撞键加的 ``#n`` 后缀也是同一套：两侧对同批设备的排序一致。"""
    from mbdsdr_ai import sdr_backend as sb

    devices = [_rtl(0, serial="SAME"), _rtl(1, serial="SAME")]
    desktop = [i.key for i in store.resolve(devices)]
    ai = [k for k, _q, _w in sb.resolve_device_identities(devices)]
    assert desktop == ai
    assert desktop == ["rtlsdr|serial:SAME#1", "rtlsdr|serial:SAME#2"]
