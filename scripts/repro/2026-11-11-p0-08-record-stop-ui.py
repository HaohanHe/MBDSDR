#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""P0-08 独立复现脚本：录音停止/失败后按钮与文案是否仍骗人。

roadmap 断言（原文）：
    「内部停止后按钮不复位、取消/失败仍留『录音完成 (N 秒)』，
      开始失败弹两次」  证据等级：实测（gap-recording §1，真 MainWindow）。

三条路径（offscreen + 独立 profile，真实产品方法）：
  A 正常停止：按钮必须复位，终态为 completed；
  B 内部中止（写盘失败）：按钮必须复位，终态必须是 failed + 原因，
    状态栏不得出现「录制完成」；
  C 未连接后端就点录音：只能弹 1 次警告，且按钮复位。

退出码（三态）：
    0  复现成功   —— 任一 pat 出现旧行为
    1  未复现     —— 三条路径都符合预期
    2  无法判定   —— 构造/录制初始化失败

反证对照：把 ``MBDSDR_REPRO_ROOT`` 指向 bf06e9a4^（或其之前）重跑，
必须重新出现「按钮不复位 / 中止仍说完成 / 失败弹两次」。

用法：
    python scripts/repro/2026-11-11-p0-08-record-stop-ui.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import types

ROOT = os.environ.get("MBDSDR_REPRO_ROOT") or os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TMP_ROOT = r"D:\mbdsdr\output\tmp"


def _hr(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


class _StubBackend:
    def __init__(self):
        self.status = types.SimpleNamespace(error="", gain_db=12.5,
                                            demod_mode="FM", connected=True)
        self.freq = 145.5e6
        self.sr = 2.048e6

    def get_sample_rate(self):
        return self.sr

    def get_frequency(self):
        return self.freq

    def read_samples(self, n):
        import numpy as np
        return np.zeros(int(n), dtype=np.complex64)

    def subscribe_iq(self, _name):
        return types.SimpleNamespace(read=self.read_samples)

    def set_frequency(self, f):
        self.freq = float(f)

    def set_demod(self, _m):
        pass


def main() -> int:
    profile = tempfile.mkdtemp(prefix="p0-08-profile-", dir=TMP_ROOT)
    rec_dir = tempfile.mkdtemp(prefix="p0-08-rec-", dir=TMP_ROOT)
    for k in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
        os.environ[k] = profile
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ.setdefault("MBDSDR_OFFLINE", "1")
    os.environ.setdefault("MBDSDR_NO_HW", "1")
    os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")
    sys.path.insert(0, ROOT)
    sys.path.insert(0, os.path.join(ROOT, "desktop"))

    try:
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QApplication, QMessageBox
        QSettings.setDefaultFormat(QSettings.IniFormat)
        for scope in (QSettings.UserScope, QSettings.SystemScope):
            QSettings.setPath(QSettings.IniFormat, scope,
                              os.path.join(profile, "qsettings"))
        app = QApplication.instance() or QApplication(sys.argv[:1])
        import main_window as mw
        window = mw.MainWindow()
        app.processEvents()
    except Exception as exc:  # noqa: BLE001
        print(f"[无法判定] Qt/MainWindow 构造失败：{type(exc).__name__}: {exc}")
        return 2

    try:
        window.settings.set("record_dir", rec_dir)
        window.settings.set("record_dir_inside_program_ack", True)
        window.settings.flush()
    except Exception:  # noqa: BLE001
        pass
    window._confirm_record_dir_safe = lambda: True  # 目录确认走非交互

    def _start() -> bool:
        window._active_sdr_backend = _StubBackend()
        window._recording = False
        window._record_guard = False
        window._start_recording()
        app.processEvents()
        if not window._recording:
            return False
        window._record_stream_fh.write(b"\x00" * (8 * 2048))
        window._record_samples_total = 2048
        # 直接 setChecked 会发 toggled → _toggle_record 把录音立刻停掉，
        # 变成探针假象；这里只模拟「按钮已按下」的视觉态，屏蔽信号。
        for btn in (window.record_btn, window.control_panel.record_button):
            btn.blockSignals(True)
            btn.setChecked(True)
            btn.blockSignals(False)
        return True

    def _outcome():
        try:
            return window.control_panel._record_outcome
        except Exception:  # noqa: BLE001
            return getattr(window.control_panel, "_record_outcome", None)

    def _status_text():
        try:
            return window.statusBar().currentMessage()
        except Exception:  # noqa: BLE001
            return ""

    print(f"repo   : {ROOT}")
    print(f"python : {sys.version.split()[0]}")
    print(f"临时档案: {profile}；录音目录: {rec_dir}")
    print(f"control_panel 结果态字段存在: {hasattr(window.control_panel, '_record_outcome')}")

    problems = []

    _hr("路径 A：正常停止（用户点停止）")
    if not _start():
        print("[无法判定] 无法进入录音态（_start_recording 未置真）。")
        return 2
    window._apply_recording(False)
    app.processEvents()
    a_btn = (window.record_btn.isChecked(),
             window.control_panel.record_button.isChecked())
    a_out = _outcome()
    print(f"  按钮 isChecked：主={a_btn[0]} 面板={a_btn[1]}（期望 False/False）")
    print(f"  结果态：{a_out!r}（期望 completed）")
    print(f"  段数：{len(getattr(window, '_record_segments', []) or [])}；"
          f"状态栏：{_status_text()!r}")
    if a_btn[0] or a_btn[1]:
        problems.append("A: 停止后按钮未复位")
    if a_out not in (None, "completed"):
        problems.append(f"A: 正常停止结果态异常 {a_out!r}")

    _hr("路径 B：内部中止（写盘失败 error=...）")
    if not _start():
        print("[无法判定] 无法进入第二次录音态。")
        return 2
    window._stop_recording(error="写盘失败: disk full")
    app.processEvents()
    b_btn = (window.record_btn.isChecked(),
             window.control_panel.record_button.isChecked())
    b_out = _outcome()
    b_msg = _status_text()
    print(f"  按钮 isChecked：主={b_btn[0]} 面板={b_btn[1]}（期望 False/False）")
    print(f"  结果态：{b_out!r}（期望 failed）")
    print(f"  状态栏：{b_msg!r}")
    if b_btn[0] or b_btn[1]:
        problems.append("B: 内部中止后按钮未复位")
    if b_out != "failed":
        problems.append(f"B: 中止结果态不是 failed（{b_out!r}）")
    if "录制完成" in b_msg or "录制完成" in str(b_out or ""):
        problems.append("B: 中止仍显示「录制完成」")

    _hr("路径 C：未连接后端点录音，警告弹几次")
    calls = []
    orig_warn = QMessageBox.warning

    def _warn(*a, **k):
        calls.append(a)
        return QMessageBox.Ok

    QMessageBox.warning = staticmethod(_warn)
    window._active_sdr_backend = None
    window._recording = False
    window._record_guard = False
    # 不预先 setChecked：那会额外触发一次 toggled→_toggle_record，
    # 把「两次弹窗」做成探针假象。真实点击只走一次入口。
    try:
        window._toggle_record()
        app.processEvents()
    except Exception as exc:  # noqa: BLE001
        print(f"  _toggle_record 异常：{exc!r}")
    finally:
        QMessageBox.warning = orig_warn
    print(f"  警告次数：{len(calls)}（期望 1）")
    print(f"  主按钮 isChecked：{window.record_btn.isChecked()}（期望 False）")
    if len(calls) > 1:
        problems.append(f"C: 开始失败弹了 {len(calls)} 次")
    if window.record_btn.isChecked():
        problems.append("C: 开始失败后主按钮未复位")

    try:
        window.close()
    except Exception:  # noqa: BLE001
        pass
    shutil.rmtree(profile, ignore_errors=True)
    shutil.rmtree(rec_dir, ignore_errors=True)

    print(f"\n结论：当前实现复现 P0-08 : {bool(problems)}")
    if problems:
        for p in problems:
            print(f"[复现] {p}")
        return 0
    print("[未复现] 三路径的按钮态与终态文案都符合预期。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
