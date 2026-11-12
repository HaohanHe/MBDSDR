# SPDX-License-Identifier: MIT
"""设备会话锁（gpredict 第三档）：面板层的置灰、回显与断开写回。

验收对照 ``docs/pending-patches/2026-11-13-device-session-lock-not-implemented.md``
§二-5 的五条行为：

  1. 连接成功 → 三个参数控件置灰；断开 → 恢复可编辑；
  2. 会话期间另一面板改 store → 本面板回显不变（``request_for`` 冻结被真正用上）；
  3. 会话期间非法输入（``abc`` / ``0``）→ 控件不可编辑，不会写进 store；
  4. 断开 → 写回一次、``is_occupied`` 变 False；重复断开幂等；
  5. 身份确认失败 → 控件保持可编辑，不产生会话。

另补一条文档 §二-4 的判据：写回时采样率解析失败不写回（不得把 0 Hz / 非法值
塞进 store），并在状态行给出可读提示。
"""
from __future__ import annotations

import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DESKTOP = os.path.join(_ROOT, "desktop")
for _p in (_ROOT, _DESKTOP):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import connect_request_store as store  # noqa: E402

_PARAM_WIDGETS = ("sr_edit", "gain_edit", "ppm_edit")


@pytest.fixture(autouse=True)
def _clean_store(isolated_profile):
    store.reset()
    yield
    store.reset()


def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv)


def _dev(serial: str = "A-0001", label: str = "RTL-A") -> dict:
    return {"driver": "rtlsdr", "label": label, "serial": serial,
            "device_args": {"index": 0}}


@pytest.fixture
def panel(monkeypatch):
    _app()
    import desktop.device_panel as dp

    # 构造面板会异步枚举外部设备：本文件全部用手喂列表，禁掉真枚举。
    monkeypatch.setattr(dp.DevicePanel, "refresh_devices_async",
                        lambda self: None)
    widget = dp.DevicePanel()
    yield widget
    widget.close()


def _key(panel) -> str:
    return panel._identities[0].key


def _enabled_state(panel):
    return {name: getattr(panel, name).isEnabled() for name in _PARAM_WIDGETS}


def _texts(panel):
    return tuple(getattr(panel, name).text() for name in _PARAM_WIDGETS)


def test_session_grays_and_restores_parameter_controls(panel):
    """行为①：连接成功置灰，断开恢复。"""
    a = _dev()
    panel._apply_devices([a])
    key = _key(panel)
    store.update(key, sample_rate_hz=2_048_000.0, gain_db=30.0, ppm=0)

    panel.set_connected(True, device=a)

    assert store.is_occupied(key), "连接成功后应登记会话占用"
    assert _enabled_state(panel) == {name: False for name in _PARAM_WIDGETS}, \
        "会话期间三个参数控件必须置灰"

    panel.set_connected(False)

    assert _enabled_state(panel) == {name: True for name in _PARAM_WIDGETS}, \
        "断开后三个参数控件必须恢复可编辑"


def test_render_stays_on_session_snapshot_while_other_panel_writes(panel):
    """行为②：会话中回显连上那台的生效值，且不被别的面板新意图拉走。"""
    a = _dev()
    b = _dev(serial="B-0002", label="RTL-B")
    panel._apply_devices([a, b])
    key_a, key_b = panel._identities[0].key, panel._identities[1].key
    store.update(key_a, sample_rate_hz=2_048_000.0, gain_db=30.0, ppm=0)
    store.update(key_b, sample_rate_hz=1_024_000.0, gain_db=10.0, ppm=7)
    panel.dev_combo.setCurrentIndex(1)          # 面板此刻显示的是 B
    assert float(panel.sr_edit.text()) == 1_024_000.0

    panel.set_connected(True, device=a)         # 实际连的是 A

    # 回显必须切到「连上的 A 的会话生效值」，而不是停在下拉框里的 B。
    assert float(panel.sr_edit.text()) == 2_048_000.0, \
        "连接后没有回显实际连上设备的本次生效值"
    before = _texts(panel)

    # 另一入口在会话中尝试改 A 的请求：写门拒绝（不是静默忽略）。
    result = store.update(key_a, sample_rate_hz=1_500_000.0, gain_db=40.0, ppm=5)

    assert result.rejected is True, "会话占用期间的写入必须被写门拒绝"
    assert result.reason, "拒绝必须带可读原因"
    assert store.intent_for(key_a).sample_rate_hz == 2_048_000.0, \
        "被拒的写入不得改动意图值"
    assert store.request_for(key_a).sample_rate_hz == 2_048_000.0, \
        "占用期间的读侧冻结快照失效"
    assert _texts(panel) == before, (
        "会话期间回显被拒绝的写入拉走了：request_for 的冻结快照"
        "没有真正被面板用上")


def test_invalid_input_during_session_is_not_written(panel):
    """行为③：置灰期间非法输入进不了 store。"""
    a = _dev()
    panel._apply_devices([a])
    key = _key(panel)
    store.update(key, sample_rate_hz=2_048_000.0, gain_db=30.0, ppm=0)
    panel.set_connected(True, device=a)
    intent_before = store.intent_for(key)

    assert not panel.sr_edit.isEnabled(), "会话期间控件不可编辑（行为③的前提）"
    # 即使有人绕过置灰把非法文本塞进控件，也不得写进 store。
    for text in ("abc", "0"):
        panel.sr_edit.setText(text)
        panel.sr_edit.editingFinished.emit()
        panel.gain_edit.setText(text)
        panel.gain_edit.editingFinished.emit()
        panel.ppm_edit.setText(text)
        panel.ppm_edit.editingFinished.emit()

    assert store.intent_for(key) == intent_before, \
        "会话期间的非法输入被写进了唯一请求存储"
    assert store.request_for(key) == intent_before, "占用期间的生效值被非法输入改写"


def test_disconnect_writes_back_once_and_clears_occupancy(panel, monkeypatch):
    """行为④：断开写回一次、清占用态，重复断开幂等。"""
    a = _dev()
    panel._apply_devices([a])
    key = _key(panel)
    store.update(key, sample_rate_hz=2_048_000.0, gain_db=30.0, ppm=12)
    panel.set_connected(True, device=a)
    # 会话中另一入口试图改意图：写门拒绝，断开时仍以本次会话生效值写回。
    assert store.update(key, sample_rate_hz=1_024_000.0, gain_db=40.0, ppm=3).rejected

    calls = []
    real_fill = store.fill_payload
    monkeypatch.setattr(
        store, "fill_payload",
        lambda payload, k: calls.append(k) or real_fill(payload, k))

    panel.set_connected(False)

    assert not store.is_occupied(key), "断开后必须清掉占用态"
    req = store.request_for(key)
    assert req.sample_rate_hz == 2_048_000.0, "采样率没有按会话生效值写回"
    assert req.gain_db == 30.0, "增益没有按会话生效值写回"
    assert req.ppm == 12, "PPM 没有按会话生效值写回"
    assert calls == [key], "断开写回必须且只能走一次 fill_payload"

    # 幂等：重复断开不再写回，也不覆盖断开后产生的新意图。
    store.update(key, sample_rate_hz=1_500_000.0)
    panel.set_connected(False)
    assert calls == [key], "重复 set_connected(False) 又写回了一次"
    assert store.request_for(key).sample_rate_hz == 1_500_000.0, \
        "重复断开把断开后的新意图覆盖掉了"


def test_write_back_goes_through_gate_and_updates_intent(panel):
    """断开写回必须真的落到 store：用「实机生效值 ≠ 旧意图」证明没被门拒。

    快照来自 ``begin_session(initial=...)``：主窗口下发的是调用方参数，可能
    与旧意图不同。写回若在占用态直接调 ``update``（被写门拒绝），意图会停在
    30.0；正确实现先 ``end_session`` 再用同一个 ``update`` 写入，意图变 33.0。
    """
    a = _dev()
    panel._apply_devices([a])
    key = _key(panel)
    store.update(key, sample_rate_hz=2_048_000.0, gain_db=30.0, ppm=0)
    store.begin_session(key, initial=store.ConnectRequest(
        sample_rate_hz=2_048_000.0, gain_db=33.0, ppm=0))
    panel.sr_edit.setText("2.048e+06")

    panel.set_connected(True, device=a)    # 已占用：不覆盖首次快照
    panel.set_connected(False)

    assert not store.is_occupied(key)
    assert store.intent_for(key).gain_db == 33.0, \
        "断开写回没落到 store（很可能在占用态调 update，被自己的写门拒了）"


def test_identity_failure_keeps_editable_and_creates_no_session(panel, monkeypatch):
    """行为⑤：身份确认失败不置灰、不产生会话。"""
    calls = []
    real_begin = store.begin_session
    monkeypatch.setattr(
        store, "begin_session",
        lambda key: calls.append(key) or real_begin(key))

    panel.set_connected(True)  # 无设备、无 pending、无 label → 身份确认不了

    assert panel._current_dev_key is None, "身份确认失败时不应记下设备键"
    assert calls == [], "身份确认失败不得调用 begin_session（不产生会话）"
    assert _enabled_state(panel) == {name: True for name in _PARAM_WIDGETS}, \
        "身份确认失败时参数控件必须保持可编辑"


def test_write_back_skips_fields_missing_from_snapshot(panel):
    """边界①：会话快照里没有的字段不写回，store 保留原值。

    快照里 ``sample_rate_hz`` 为 None 常见于连接由别的入口发起；此时界面上的
    采样率是预填默认值，不是本次实际下发值，写回去等于伪造意图。
    """
    a = _dev()
    panel._apply_devices([a])
    key = _key(panel)
    store.update(key, gain_db=30.0, ppm=0)      # 会话快照没有采样率
    panel.set_connected(True, device=a)
    # 故意选一个与界面预填默认值（1.024M）不同的采样率：若写回误用预填值，
    # 会以 1_024_000.0 出现在 store 里；写门拒绝后这里必须仍是 None。
    assert store.update(key, sample_rate_hz=1_536_000.0).rejected

    panel.set_connected(False)

    req = store.request_for(key)
    assert req.sample_rate_hz is None, \
        "快照里没有采样率，却用界面预填值把它写回了 store"
    # 快照里有的字段仍应按本次生效值写回（只跳过缺失字段，不是整笔不写）。
    assert req.gain_db == 30.0, "快照里有的增益没有写回"
    assert req.ppm == 0, "快照里有的 PPM 没有写回"


def test_write_back_parse_failure_does_not_touch_store(panel):
    """文档 §二-4：采样率解析失败时不写回，也不把 0 Hz 写进 store。"""
    a = _dev()
    panel._apply_devices([a])
    key = _key(panel)
    store.update(key, sample_rate_hz=2_048_000.0, gain_db=30.0, ppm=0)
    panel.set_connected(True, device=a)
    # 会话中另一入口的意图会被写门拒绝；写回失败时 store 必须原样保留会话值。
    assert store.update(key, sample_rate_hz=1_024_000.0, gain_db=40.0).rejected
    panel.sr_edit.setText("abc")

    panel.set_connected(False)

    assert not store.is_occupied(key), "无论写回成功与否，占用态都必须清掉"
    req = store.request_for(key)
    assert req.sample_rate_hz == 2_048_000.0, \
        "解析失败时不得写回（更不得写入 0 Hz / 非法值）"
    assert req.gain_db == 30.0, "采样率解析失败时整笔写回应中止，增益不应被改写"
    assert "无法解析" in panel.status_label.text(), \
        "写回失败必须给用户可读提示，不能静默"
