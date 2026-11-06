#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""真软件四根「没接上的线」的接线回归（offscreen，无硬件、无网络）。

背景：2026-11-06 只读审计发现桌面端存在「库里有能力、面板里说没有」的断点。
本文件把每一处接线钉死，防止再次退化成"看得见但没数据"：

1. **中栏 ADS-B 地图从来没有数据**：`update_aircraft` 全仓只有测试调用，
   主窗口只喂右栏小面板。现在 `SpectrumPanel.set_iq_data` 发布到 `iq_bus`，
   地图面板自己订阅（不改主窗口一行）。
2. **星象仪搜索只覆盖 54 颗亮星**：搜 Sadr / Alcyone / NGC 7000 全 0 结果，
   而星表有 83,479 颗（430 个专名）、深空目录 117 条。现在走
   `StarCatalog.search_by_name` 与 `DSOCatalog.search`。
3. **中国星官图层零入口**：渲染与数据都做完了，却没有按钮/快捷键。
   现在控制条有「星官」按钮、快捷键 C，且两者共用一条状态。
4. **气象面板 FY-4 一口回绝**：库层 `decode_fy4_iq_to_image` 早已实现并有
   逐像素往返测试，面板却说「raw IQ 不支持」。现在真接出图链路。

全部数据都是**合成测试信号**（Mode S 呼号帧 / FY-4 xRIT 段），不代表真实
航班或真实卫星图像。
"""

import json
import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
_DESKTOP_DIR = os.path.join(REPO_ROOT, "desktop")
if _DESKTOP_DIR not in sys.path:
    sys.path.insert(0, _DESKTOP_DIR)


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


# ======================================================================
# 1. IQ 总线
# ======================================================================
def test_iq_bus_keeps_only_latest_frame(qapp):
    from desktop.iq_bus import IqBus

    bus = IqBus()
    assert bus.latest() is None
    a = np.ones(64, dtype=np.complex64)
    b = np.ones(128, dtype=np.complex64)
    bus.publish(a, 2.4e6, 1090e6)
    first = bus.latest()
    assert first is not None and first.seq == 1
    bus.publish(b, 2.4e6, 1090e6)
    second = bus.latest()
    assert second is not None and second.seq == 2
    assert second.iq is b, "总线只存引用，不拷贝"
    assert bus.seq() == 2


def test_iq_bus_ignores_garbage(qapp):
    from desktop.iq_bus import IqBus

    bus = IqBus()
    bus.publish(None, 2.4e6, 1090e6)
    bus.publish(np.zeros(0, dtype=np.complex64), 2.4e6, 1090e6)
    bus.publish(np.ones(8, dtype=np.complex64), 0.0, 1090e6)
    bus.publish(np.ones(8, dtype=np.complex64), float("nan"), 1090e6)
    assert bus.latest() is None
    assert bus.seq() == 0


def test_iq_bus_latest_since_and_clear(qapp):
    from desktop.iq_bus import IqBus

    bus = IqBus()
    iq = np.ones(32, dtype=np.complex64)
    bus.publish(iq, 1.2e6, 1.09e9)
    frame = bus.latest()
    assert frame is not None
    assert bus.latest_since(frame.seq) is None, "同一个 seq 不该重复返回"
    bus.publish(iq, 1.2e6, 1.09e9)
    assert bus.latest_since(frame.seq) is not None
    bus.clear()
    assert bus.latest() is None


# ======================================================================
# 2. 中栏地图：从总线取 IQ 并解码
# ======================================================================
def _synthetic_adsb_iq():
    """合成一帧 Mode S 呼号报文（TC=1），不含任何真实航班信息。"""
    from mbdsdr_ai import adsb_lite
    me = bytearray(7)
    me[0] = (1 << 3)  # TC=1 识别与类别
    frame = adsb_lite.build_long_frame(0x17, 0x765432, bytes(me))
    return adsb_lite.synthesize_modes_iq([frame], sample_rate=2_000_000)


def test_center_map_decodes_from_bus(qapp):
    from desktop.iq_bus import reset_iq_bus
    from adsb_map_panel import AdsbMapPanel

    bus = reset_iq_bus()          # 干净单例（必须用 desktop.iq_bus，
    # 否则与面板里的 import 路径不是同一个模块对象，总线对不上）
    panel = AdsbMapPanel()
    assert panel.decoded_frames_total() == 0
    assert panel.aircraft_count() == 0

    iq = _synthetic_adsb_iq()
    bus.publish(iq, 2_000_000.0, 1_090_000_000.0)
    panel._poll_iq_bus()
    assert panel.decoded_frames_total() >= 1, "1090MHz 的 IQ 应被解码"
    assert len(panel._tracker.all()) >= 1, "跟踪表应收到飞机"

    # 同一 seq 再 poll 不得重复解码（否则列表会被同一帧刷屏）
    before = panel.decoded_frames_total()
    panel._poll_iq_bus()
    assert panel.decoded_frames_total() == before


def test_center_map_skips_other_bands(qapp):
    from desktop.iq_bus import reset_iq_bus
    from adsb_map_panel import AdsbMapPanel

    bus = reset_iq_bus()
    panel = AdsbMapPanel()
    iq = _synthetic_adsb_iq()
    bus.publish(iq, 2_000_000.0, 145_000_000.0)   # 2m 业余频段
    panel._poll_iq_bus()
    assert panel.decoded_frames_total() == 0

    bus.publish(iq, 500_000.0, 1_090_000_000.0)   # 频段对但带宽太窄
    panel._poll_iq_bus()
    assert panel.decoded_frames_total() == 0


def test_center_map_observer_never_invented(qapp, tmp_path, monkeypatch):
    """没有站址配置时距离列必须是 --，不得编造坐标。"""
    from desktop.iq_bus import reset_iq_bus
    from adsb_map_panel import AdsbMapPanel

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    reset_iq_bus()
    panel = AdsbMapPanel()
    panel._observer_checked_at = 0.0
    panel._refresh_observer_from_config()
    assert panel._observer is None


def test_center_map_observer_reads_gui_config(qapp, tmp_path, monkeypatch):
    from adsb_map_panel import AdsbMapPanel

    home = tmp_path / "home"
    (home / ".mbdsdr").mkdir(parents=True)
    (home / ".mbdsdr" / "gui_config.json").write_text(
        json.dumps({"observer_lat": 39.9, "observer_lon": 116.4}),
        encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    panel = AdsbMapPanel()
    panel._observer_checked_at = 0.0
    panel._refresh_observer_from_config()
    assert panel._observer == pytest.approx((39.9, 116.4))


# ======================================================================
# 3. 星象仪搜索 + 星官图层
# ======================================================================
@pytest.mark.parametrize("text,kind", [
    ("vega", "star"),
    ("sadr", "star"),
    ("alcyone", "star"),
    ("ngc 7000", "dso"),
    ("北美洲", "dso"),
    ("m42", "dso"),
    ("猎户座大星云", "dso"),
])
def test_sky_search_covers_full_catalog(qapp, text, kind):
    from rf_sky_view import RFSkyView

    view = RFSkyView()
    view._on_search_text(text)
    kinds = {r["kind"] for r in view._search_results}
    assert kind in kinds, f"搜索 {text!r} 没命中 {kind}：{view._search_results[:3]}"


def test_sky_search_empty_state_is_explicit(qapp):
    """搜不到时给可见提示，而不是静默无反应。"""
    from rf_sky_view import RFSkyView

    view = RFSkyView()
    view._on_search_text("zzzzzz")
    assert view._search_results == []
    assert view._search_popup.count() == 1, "空结果应有一条不可选提示"
    from PySide6.QtCore import Qt as _Qt
    item = view._search_popup.item(0)
    assert not bool(item.flags() & _Qt.ItemIsSelectable), "提示项必须不可选"


def test_sky_search_needs_station_notice(qapp):
    """无地面站时点搜索结果要提示，不静默 return。"""
    from rf_sky_view import RFSkyView

    view = RFSkyView()
    view._camera_notice = ""
    assert view._ground_station() is None
    assert view._require_station_for_search() is False
    assert "地面站" in view._camera_notice


def test_xingguan_layer_toggle_shared_state(qapp):
    from rf_sky_view import RFSkyViewPanel
    from mbdsdr_ai.skyengine import constellations as _c

    panel = RFSkyViewPanel()
    assert panel.chinese_layer_visible() is False
    panel.set_chinese_layer(True)
    assert panel.chinese_layer_visible() is True
    assert _c.SYSTEM_CHINESE in panel.sky_view.constellation_systems()
    assert _c.SYSTEM_IAU in panel.sky_view.constellation_systems(), \
        "开星官不应把 IAU 一起关掉"
    assert "中国星官" in panel.sky_view.constellation_systems_text()
    assert panel._xingguan_button.isChecked() is True

    # 快捷键路径（画布直接切）必须同步按钮，不能两套显示
    panel.sky_view.set_chinese_layer(False)
    assert panel._xingguan_button.isChecked() is False


# ======================================================================
# 4. 气象面板 FY-4 真实出图
# ======================================================================
def test_weather_panel_fy4_iq_to_image(qapp, tmp_path):
    """IQ → FY-4 图：面板不再回绝，能出 PNG 并带上诚实边界说明。"""
    from weather_panel import DecodeWorker
    sys.path.insert(0, os.path.join(REPO_ROOT, "tests"))
    from test_fy4_iq_chain import (_test_image, _vcdus_from_image,
                                   _vcdus_to_iq)

    img = _test_image()
    vcdus = _vcdus_from_image(img)
    iq, _, _ = _vcdus_to_iq(vcdus, scheme="bpsk")
    iq_path = tmp_path / "fy4.iq"
    iq.astype(np.complex64).tofile(str(iq_path))
    out_png = tmp_path / "fy4.png"

    worker = DecodeWorker({"family": "fy4", "iq_file": str(iq_path),
                           "out_png": str(out_png), "satellite": "FY-4A"})
    got = []
    worker.finished.connect(got.append)
    worker.run()

    assert got, "worker 没有发出 finished"
    res = got[-1]
    assert res.get("ok") is True, res
    assert res.get("width") == img.shape[1]
    assert res.get("height") == img.shape[0]
    assert out_png.exists()
    note = (res.get("meta") or {}).get("note", "")
    assert "DVB-S2" in note, "必须如实写明真实下行是 DVB-S2，本链路是经典 CCSDS"


def test_weather_panel_fy4_honest_failure(qapp, tmp_path):
    """纯噪声 IQ 必须诚实失败并带诊断，不出半截图。"""
    from weather_panel import DecodeWorker

    noise = (np.random.default_rng(7).normal(size=200_000)
             + 1j * np.random.default_rng(8).normal(size=200_000)) \
        .astype(np.complex64)
    iq_path = tmp_path / "noise.iq"
    noise.tofile(str(iq_path))
    out_png = tmp_path / "noise.png"

    worker = DecodeWorker({"family": "fy4", "iq_file": str(iq_path),
                           "out_png": str(out_png), "satellite": "FY-4A"})
    got = []
    worker.finished.connect(got.append)
    worker.run()
    assert got, "worker 没有发出 finished"
    res = got[-1]
    assert res.get("ok") is False
    assert res.get("error"), "失败必须给原因"
    assert not out_png.exists(), "失败时不得留下半截图"
