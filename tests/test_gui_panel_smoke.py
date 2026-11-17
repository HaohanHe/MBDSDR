# SPDX-License-Identifier: MIT
"""`scripts/gui_panel_smoke.py` 的主题接线（离屏子进程，不建真实桌面）。

锁三件事：
  * 应用真实主题入口（``desktop/themes.py:Theme.apply``）能被脚本调用并生效；
  * ``--no-theme`` 对照组确实不套主题；
  * report.json 记录 ``theme_applied`` / ``theme_entry``，且面板调色板与
    主题令牌一致（套主题）/ 不一致（未套主题）。

用子进程而不是在 pytest 进程内建 QApplication：主题 apply 会改 app 级
QPalette/QSS，同进程会污染后续用例；子进程跑完即退出，也顺带验证脚本
「结束无残留进程」。
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "gui_panel_smoke.py"


def _run(out_dir: Path, *extra: str, only: str = "status") -> dict:
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("PYTHONPATH", None)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--only", only,
         "--out", str(out_dir), *extra],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    assert proc.returncode == 0, (
        f"脚本退出码 {proc.returncode}\nstdout:\n{proc.stdout}\n"
        f"stderr:\n{proc.stderr}")
    report_path = out_dir / "report.json"
    assert report_path.is_file()
    return json.loads(report_path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def reports(tmp_path_factory):
    base = tmp_path_factory.mktemp("gui_smoke")
    themed = _run(base / "themed")
    plain = _run(base / "plain", "--no-theme")
    return themed, plain


def test_theme_entry_can_be_applied(reports):
    themed, _ = reports
    assert themed["theme_applied"] is True
    assert themed["theme_entry"] == "desktop/themes.py:Theme.apply"
    assert themed["theme_name"]

    row = next(r for r in themed["panels"] if r["id"] == "status")
    assert row["status"] == "ok"
    assert row["palette"]["matches_theme_colors"] is True
    # 主题暗底白字：#000000 / #ffffff，对比度 21:1。
    assert row["palette"]["bg"] == "#000000"
    assert row["palette"]["text"] == "#ffffff"


def test_no_theme_flag_really_skips_theme(reports):
    themed, plain = reports
    assert plain["theme_applied"] is False
    assert plain["theme_entry"] is None

    row = plain["panels"][0]
    assert row["status"] == "ok"
    assert row["palette"]["matches_theme_colors"] is None
    # 平台默认离屏调色板与主题调色板必须是两套值——否则 --no-theme 没生效。
    assert row["palette"]["bg"] != themed["panels"][0]["palette"]["bg"]


def test_report_records_theme_fields_and_png(reports):
    for report in reports:
        assert isinstance(report["theme_applied"], bool)
        assert "theme_entry" in report
        for row in report["panels"]:
            if row["status"] == "ok":
                assert (Path(report["out_dir"]) / row["screenshot"]).is_file()


def test_default_settle_recorded(reports):
    """默认 --settle-ms 800：顶层与每个面板都记配置值，且确实等过。"""
    for report in reports:
        assert report["settle_ms"] == 800
        for row in report["panels"]:
            assert row["settle_ms"] == 800
            assert isinstance(row["texts"], dict)
            if row["status"] == "ok":
                assert row["settle_elapsed_ms"] is not None
                assert row["settle_elapsed_ms"] >= 0


def test_settle_zero_is_runnable(tmp_path):
    """--settle-ms 0 允许：不等稳定态，但报告字段齐全、面板照常出图。"""
    report = _run(tmp_path / "zero", "--settle-ms", "0")
    assert report["settle_ms"] == 0
    row = next(r for r in report["panels"] if r["id"] == "status")
    assert row["status"] == "ok"
    assert row["settle_ms"] == 0
    assert row["settle_elapsed_ms"] == 0.0


def test_device_and_gain_capture_final_texts(tmp_path):
    """无硬件环境（脚本自设 MBDSDR_NO_HW=1）下等完稳定态：

    device 必须写「未检测到设备」而非残留「正在枚举设备…」；gain 三个值
    标签是断开态的「—」。两者都不依赖真实硬件。
    """
    device_report = _run(tmp_path / "device", only="device")
    device_row = next(r for r in device_report["panels"] if r["id"] == "device")
    if device_row["status"] == "skipped":
        pytest.skip("device_panel 不可用：工厂返回 None（缺可选依赖）")
    assert device_row["status"] == "ok"
    texts = device_row["texts"]
    assert "未检测到设备" in texts["device_combo"]
    assert "未检测到设备" in texts["status"]
    assert "正在枚举" not in texts["status"]

    gain_report = _run(tmp_path / "gain", only="gain")
    gain_row = next(r for r in gain_report["panels"] if r["id"] == "gain")
    if gain_row["status"] == "skipped":
        pytest.skip("gain_panel 不可用：工厂返回 None（缺可选依赖）")
    assert gain_row["status"] == "ok"
    for key in ("lna", "mixer", "vga"):
        assert gain_row["texts"][key] == "—"
