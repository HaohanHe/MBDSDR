# SPDX-License-Identifier: MIT
"""ADS-B 飞机表行数上限：行数判据 + 性能反证
=============================================

``_refresh_table`` 在每批解码帧后整表重建（``_after_update`` → 这里），成本
跟随在视飞机数：实测 100 架 3.7 ms、500 架 26 ms、2000 架 66 ms。钉住的
契约：

  * 在视飞机超过 ``adsb_panel.MAX_RENDERED_AIRCRAFT``(300) 时只渲染前 300 行
    （``tracker.all()`` 按最近更新排序，截的是尾部旧行）；
  * 表头仍报总架数，并明说「列表只显示最近 300 架，另 N 架未显示」；
  * 不足 300 架时不出现该提示；
  * 数据侧不受影响（tracker 仍保留全部，导出/地图照旧）。

反证（``test_removing_cap_restores_full_render_and_slowdown``）：上限放大到
无穷后，行数必须回到全部、同进程耗时必须显著变长。删掉 ``_refresh_table``
的切片，本文件立刻红。

样本：1200 架合成飞机，每档预热 1 + 采样 3 取中位；复跑入口
``scripts/repro/2026-11-14-list-render-baseline.py --only adsb``。
"""
import os
import statistics
import sys
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

AIRCRAFT = 1200


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def _aircraft(n):
    now = time.time()
    return [SimpleNamespace(icao_hex=f"{0xA00000 + i:06x}",
                            callsign=f"CA{i:04d}", lat=30.0 + i * 0.001,
                            lon=120.0 + i * 0.001, altitude_ft=10000 + i,
                            groundspeed_kt=420.0, track_deg=90.0,
                            last_seen=now - i * 0.1)
            for i in range(n)]


class _FakeTracker:
    def __init__(self, acs):
        self._acs = acs

    def all(self):
        return self._acs

    def __len__(self):
        return len(self._acs)


@pytest.fixture()
def panel(qapp):
    from adsb_panel import AdsbPanel
    dlg = AdsbPanel()
    yield dlg
    dlg.deleteLater()
    qapp.processEvents()


def _feed(panel, acs):
    panel._tracker = _FakeTracker(acs)
    panel._refresh_table()
    panel._refresh_header()


def test_cap_bounds_rows_and_header_reports_remainder(panel):
    import adsb_panel as ap
    _feed(panel, _aircraft(AIRCRAFT))

    assert ap.MAX_RENDERED_AIRCRAFT == 300
    assert panel.table.rowCount() == ap.MAX_RENDERED_AIRCRAFT
    text = panel.header.text()
    assert "在视飞机 1200 架" in text
    assert "另 900 架未显示" in text, text
    assert len(panel._tracker) == AIRCRAFT  # 数据侧没被截


def test_no_hidden_note_when_rows_fit(panel):
    _feed(panel, _aircraft(40))
    assert panel.table.rowCount() == 40
    assert "未显示" not in panel.header.text()
    assert "在视飞机 40 架" in panel.header.text()


def _refresh_median_ms(panel, rounds=3):
    samples = []
    for n in range(1 + rounds):
        t0 = time.perf_counter()
        panel._refresh_table()
        if n >= 1:
            samples.append((time.perf_counter() - t0) * 1000.0)
    return statistics.median(samples)


def test_removing_cap_restores_full_render_and_slowdown(panel, monkeypatch):
    import adsb_panel as ap
    _feed(panel, _aircraft(AIRCRAFT))
    capped_ms = _refresh_median_ms(panel)
    assert panel.table.rowCount() == ap.MAX_RENDERED_AIRCRAFT

    monkeypatch.setattr(ap, "MAX_RENDERED_AIRCRAFT", 10 ** 9)
    panel._refresh_table()
    uncapped_ms = _refresh_median_ms(panel)

    assert panel.table.rowCount() == AIRCRAFT, (
        f"去掉上限后仍只渲染 {panel.table.rowCount()} 行——上限没有真正生效")
    assert uncapped_ms > capped_ms * 3, (
        f"去掉上限没有明显变慢：截断 {capped_ms:.1f} ms vs "
        f"全量 {uncapped_ms:.1f} ms（{AIRCRAFT} 行）")
