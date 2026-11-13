# SPDX-License-Identifier: MIT
"""WeatherPanel 离线解码参数：非法输入当场说「没有开始」，不许装成在排队。

旧行为：``start_decode`` 先亮进度条，再用裸 ``float()`` 解析采样率/符号率；
空值或带单位的写法抛出 ``ValueError``，异常逃出 Qt 槽函数（只进 stderr）——
进度条停在 0、按钮照旧可点，用户以为任务在后台排队，于是反复点。

反证：把 ``start_decode`` 的参数校验删掉，本文件四条用例必红。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "desktop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import weather_panel as wp  # noqa: E402


@pytest.fixture(scope="module")
def qtapp(isolated_profile):
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture
def panel(qtapp, tmp_path, monkeypatch):
    monkeypatch.setattr(wp, "_OUT_DIR", str(tmp_path / "weather_out"))
    p = wp.WeatherPanel()
    # 离线分支只需一个非空文件路径；真正起线程的 _start_worker 由用例替换。
    p._iq_file = "fake.iq"
    p.src_combo.setCurrentIndex(0)
    started = []
    warnings = []
    monkeypatch.setattr(p, "_start_worker", lambda params: started.append(params))
    monkeypatch.setattr(
        QMessageBox, "warning",
        staticmethod(lambda *a, **k: warnings.append(a)))
    yield p, started, warnings
    p.close()


def test_empty_sample_rate_reports_not_started(panel):
    p, started, warnings = panel
    p.sr_edit.setText("")
    p.sym_edit.setText("72.0")

    p.start_decode()

    assert started == [], "参数为空时不得起解码线程"
    assert warnings, "必须弹出可见提示"
    assert "不能为空" in str(warnings[0][2])
    assert "未开始解码" in p.status_label.text()
    assert not p.progress.isVisibleTo(p), "没开始就不许亮进度条"


def test_unit_suffix_in_sample_rate_is_rejected_with_example(panel):
    p, started, warnings = panel
    p.sr_edit.setText("2.048M")
    p.sym_edit.setText("72.0")

    p.start_decode()

    assert started == []
    assert warnings, "带单位后缀必须提示"
    msg = str(warnings[0][2])
    assert "不是数字" in msg
    assert "2.048" in msg and "MS/s" in msg, "提示里要写清允许的写法"
    assert "未开始解码" in p.status_label.text()
    assert not p.progress.isVisibleTo(p)


def test_non_positive_symbol_rate_is_rejected(panel):
    p, started, warnings = panel
    p.sr_edit.setText("2.048")
    p.sym_edit.setText("0")

    p.start_decode()

    assert started == []
    assert warnings and "正数" in str(warnings[0][2])
    assert "未开始解码" in p.status_label.text()
    assert not p.progress.isVisibleTo(p)


def test_valid_numbers_start_with_parsed_values(panel):
    p, started, warnings = panel
    p.sr_edit.setText("2.048")
    p.sym_edit.setText("72.0")

    p.start_decode()

    assert warnings == []
    assert len(started) == 1
    assert started[0]["sample_rate"] == pytest.approx(2.048e6)
    assert started[0]["sps"] == max(1, round(2.048e6 / 72_000))
    assert p.progress.isVisibleTo(p)
