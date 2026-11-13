# SPDX-License-Identifier: MIT
"""WeatherPanel 离线解码参数：非法输入当场说「没有开始」，不许装成在排队。

旧行为：``start_decode`` 先亮进度条，再用裸 ``float()`` 解析采样率/符号率；
空值抛出 ``ValueError``，异常逃出 Qt 槽函数（只进 stderr）——进度条停在 0、
按钮照旧可点，用户以为任务在后台排队，于是反复点。

采样率语法另有 B1：这边拒 ``2.048M``、设备面板接受。现统一走
``device_panel.parse_sample_rate``（default_scale=1e6，本栏标签是 MS/s）。

中心频率框另有 C3：可编辑但 ``start_decode`` 从不读它，敲了不生效也不报错。
现改为只读展示，值来源写进 tooltip。

反证：把 ``start_decode`` 的参数校验删掉 / 采样率换回裸 ``float()`` /
去掉 ``setReadOnly(True)``，对应用例必红。
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


def test_unit_suffix_in_sample_rate_is_accepted_like_device_panel(panel):
    """B1：天气面板与设备面板共用 parse_sample_rate，「2.048M」必须同样能用。

    旧实现这边裸 float() 拒收带单位写法、设备面板却接受——同一个应用里
    同一个字段名两种语法，用户会把习惯带过去。反证：把这里换回裸 float()
    或不再传 default_scale=1e6，本用例必红（前者 ValueError 不起线程，
    后者纯数字「2.048」被当成 2.048 Hz）。
    """
    p, started, warnings = panel
    p.sr_edit.setText("2.048M")
    p.sym_edit.setText("72.0")

    p.start_decode()

    assert warnings == []
    assert len(started) == 1
    assert started[0]["sample_rate"] == pytest.approx(2.048e6)
    assert started[0]["sps"] == max(1, round(2.048e6 / 72_000))


def test_hz_suffix_sample_rate_is_accepted(panel):
    """同源语法也认 Hz 写法：2048000Hz 与 2.048M 等价。"""
    p, started, warnings = panel
    p.sr_edit.setText("2048000Hz")
    p.sym_edit.setText("72.0")

    p.start_decode()

    assert warnings == []
    assert started[0]["sample_rate"] == pytest.approx(2.048e6)


def test_unparseable_sample_rate_reports_not_started(panel):
    """真正无法解析的写法仍要当场说「没有开始」，并给出可照抄的写法。"""
    p, started, warnings = panel
    p.sr_edit.setText("abc")
    p.sym_edit.setText("72.0")

    p.start_decode()

    assert started == []
    assert warnings, "无法解析必须提示"
    msg = str(warnings[0][2])
    assert "无法识别" in msg
    assert "2.048" in msg and "2.048M" in msg and "2048000Hz" in msg
    assert "未开始解码" in p.status_label.text()
    assert not p.progress.isVisibleTo(p)


def test_hz_typed_bare_number_is_rejected_not_read_as_msps(panel):
    """本栏按 MS/s 计：照设备面板裸填 2048000 会被读成 2.048 THz，必须拒绝。

    这是「习惯带过去」的另一种形态——静默算成巨值比报错更坏。
    """
    p, started, warnings = panel
    p.sr_edit.setText("2048000")
    p.sym_edit.setText("72.0")

    p.start_decode()

    assert started == []
    assert warnings and "超出常见范围" in str(warnings[0][2])
    assert "未开始解码" in p.status_label.text()


def test_center_frequency_box_is_read_only_and_not_fed_to_decode(panel):
    """C3：中心频率框只读，值来源写清，且解码参数里没有它。

    旧实现：框可编辑，但 start_decode 从不读它——敲了不生效也不报错。
    反证：去掉 setReadOnly(True) → 本用例红。
    """
    p, started, warnings = panel
    assert p.freq_edit.isReadOnly(), "只读展示框不允许用户敲了没用"
    tip = p.freq_edit.toolTip()
    assert "只读" in tip and "制式预设" in tip and "基带" in tip, tip

    p.sr_edit.setText("2.048")
    p.sym_edit.setText("72.0")
    p.start_decode()

    assert warnings == []
    assert started, "参数合法时仍应起解码"
    assert not any("freq" in k for k in started[0]), (
        "解码走录制基带，参数里不该出现中心频率")


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
