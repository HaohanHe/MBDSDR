# SPDX-License-Identifier: MIT
"""窄屏闸门自检：构造 24px 越界样本，闸门必须报红（roadmap W54 / P0-13）。

背景：``scripts/ui_narrow_breakdown.py`` 曾只在专注态量，把滑杆列越界量成
+6px，真实详细态是 +24px（差 18px），拿它验收任何布局改动都是假绿。
P0-13 修完后，如果没有人钉住「闸门真能抓到 24px」，它可以再退回去而全绿。

为什么拿构造样本、为什么跑子进程：

* 本机 DPR1.5，真窗口四个状态越界实测全为 0（见闸门 commit e0900f6e 的
  实测注记），构造不出 24px；而按宿主 DPR/字体出数的用例换个机器就假红。
  这里改用固定尺寸样本（固定高容器 + 固定高子控件）直接喂闸门的量尺与
  红榜判定——不依赖字体、DPI、窗口最小尺寸。
* 子进程里建 Qt 控件：pytest 进程内建真 ``QApplication`` 会触发产品侧
  防抖写盘，把同内容回写进真实 ``%APPDATA%/mbdsdr/desktop_settings.json``
  （仅 mtime 变），被 tests/conftest.py 的真实配置守卫判红（实测）。子进程
  显式把 HOME/APPDATA 指到临时目录，主进程不建 Qt，两边都干净。

反证：把 ``slider_column_report`` 的越界公式从
``cw.y() + cw.height() - avail - 1`` 改成夹到 0、或把 ``red_entries`` 的
判据从 ``> 0`` 改成 ``> 1000``，本文件必红（两次均已实测）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap


def _find_root() -> str:
    """仓库根：正常就是 tests/ 的上一级；找不到就向上走，便于在临时目录里复跑。"""
    cand = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for _ in range(4):
        if os.path.isfile(os.path.join(cand, "scripts", "ui_narrow_breakdown.py")):
            return cand
        cand = os.path.dirname(cand)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


_ROOT = _find_root()
_GATE_PATH = os.path.join(_ROOT, "scripts", "ui_narrow_breakdown.py")

#: 构造样本：容器固定高 100，5 根固定高 25 的竖滑杆（合计 125）。
#: 闸门公式 ``max(0, y + h - avail - 1)`` → 125 - 100 - 1 = **24px**。
_SAMPLE_HEIGHT = 100
_SAMPLE_SLIDERS = 5
_SAMPLE_SLIDER_H = 25
_SAMPLE_OVERFLOW = _SAMPLE_SLIDERS * _SAMPLE_SLIDER_H - _SAMPLE_HEIGHT - 1

#: 子进程探针：只做「造假样本 → 调闸门量尺/红榜判定 → 打印 JSON」，
#: 不建窗口、不读产品配置。
_PROBE = textwrap.dedent(
    """
    import importlib.util, json, os

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QSlider, QVBoxLayout, QWidget

    spec = importlib.util.spec_from_file_location("gate_probe", os.environ["GATE_PATH"])
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)

    app = QApplication.instance() or QApplication([])
    N, H, CH = 5, 25, 100

    def make(container_h):
        col = QWidget()
        lay = QVBoxLayout(col)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        sliders = []
        for _ in range(N):
            s = QSlider(Qt.Vertical)
            s.setFixedSize(8, H)
            lay.addWidget(s)
            sliders.append(s)
        col.setFixedSize(16, container_h)
        col.show()
        app.processEvents()
        lay.activate()
        app.processEvents()
        # 显式摆位：QVBoxLayout 空间不足时会把子控件重叠摆开（实测 5×25 挤进
        # 100 高时 y=0/20/40/60/80，只量到 +4px），那是布局自己的补偿；越界
        # 样本要的是「按原始高度排到底、底边冲出去」，所以这里自己定 y。
        for i, s in enumerate(sliders):
            s.setGeometry(4, i * H, 8, H)
        app.processEvents()
        return col

    out = {}
    for key, h in (("over", CH), ("fit", N * H + 5)):
        rep = gate.slider_column_report(make(h))
        out[key] = {"report": rep, "red": gate.red_entries(rep, {})}
    print(json.dumps(out, ensure_ascii=False))
    """
)


def _run_probe(tmp_path) -> dict:
    env = dict(os.environ)
    for k in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
        env[k] = str(tmp_path)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["MBDSDR_NULL_AUDIO"] = "1"
    env["MBDSDR_NO_HW"] = "1"
    env["MBDSDR_OFFLINE"] = "1"
    env["GATE_PATH"] = _GATE_PATH
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=_ROOT, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=180,
    )
    assert proc.returncode == 0, (
        f"探针失败（退出码 {proc.returncode}）：\n{proc.stderr[-2000:]}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_gate_catches_constructed_24px_overflow(tmp_path):
    """构造样本必须被量到 +24px，并且进红榜时带责任控件。"""
    assert _SAMPLE_OVERFLOW == 24, "样本口径被改动：本用例的锚点就是 24px"

    out = _run_probe(tmp_path)
    report = out["over"]["report"]

    assert report.get("actual") == _SAMPLE_HEIGHT
    assert report["overflow_px"] == 24, (
        f"闸门没抓到构造的 24px 越界，量到 {report['overflow_px']}px：{report}")

    offenders = report["offenders"]
    assert offenders, "越界 > 0 但没有责任控件，红榜会变成一条无法归因的空记录"
    assert offenders[0]["label"] == "QSlider#5", (
        f"责任控件应指向最后那根滑杆（最远越界），实得 {offenders[0]['label']}")
    assert offenders[0]["overflow"] == 24

    reds = out["over"]["red"]
    assert len(reds) == 1, f"红榜条数不对：{reds}"
    assert reds[0]["part"] == "dbSliderCol"
    assert reds[0]["overflow_px"] == 24
    assert any("QSlider#5" in who for who in reds[0]["who"]), (
        f"红榜没写清责任控件：{reds[0]['who']}")


def test_gate_stays_green_when_column_fits(tmp_path):
    """反证的另一半：不越界时不许假红（否则闸门永远红，等于没闸门）。"""
    out = _run_probe(tmp_path)
    report = out["fit"]["report"]

    assert report["overflow_px"] == 0, report
    assert report["offenders"] == []
    assert out["fit"]["red"] == []


def test_gate_verdict_state_is_default_not_legacy():
    """闸门的验收口径必须是 ``default``（读数条 + 工具栏可见、焦点关）。

    这是 P0-13 的根因：旧口径量的是 ``legacy_gate`` 中间态（+6px），
    ``default`` 才是用户点「详细」后的真实形态（+24px）。顺序也有语义：
    ``release_check.py`` 读的是最后一个状态，所以 ``default`` 必须留最后。
    """
    import importlib.util

    saved = {k: os.environ.get(k) for k in
             ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
              "QT_QPA_PLATFORM", "MBDSDR_NULL_AUDIO", "MBDSDR_NO_HW",
              "MBDSDR_OFFLINE")}
    try:
        spec = importlib.util.spec_from_file_location(
            "ui_narrow_breakdown_selfcheck", _GATE_PATH)
        gate = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gate)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    names = [name for name, _ in gate.STATES]
    assert names == ["focus_mode", "legacy_gate", "detail_open", "default"], names

    by_name = dict(gate.STATES)
    assert by_name["default"] == {"detail": True, "toolbars": True,
                                  "focus": False}
    assert by_name["legacy_gate"] == {"detail": False, "toolbars": True,
                                      "focus": False}
