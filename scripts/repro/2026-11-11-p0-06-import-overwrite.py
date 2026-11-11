#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""P0-06 独立复现脚本：只导入 theme 是否会把频率/站址/AI 参数冲成默认。

roadmap 断言（原文）：
    「导入即全量覆盖：只导入 theme 会清观测站、频率回 98 MHz」
    证据等级：实测（repro_settings_gap.py import-overwrite）。

做法（一个进程、独立 profile）：
  1. 构造真 MainWindow；
  2. 写入并确认 5 个哨兵值：frequency_hz=145.5 MHz、tuning_step_hz、
     observer_lat/lon、ai_temperature；
  3. 生成只含 ``{"schema_version": 1, "settings": {"theme": "dark"}}`` 的导入件；
  4. 调产品非交互入口 ``SettingsPanel.import_from()``；
  5. 逐项读回，报告是否被改动。

退出码（三态）：
    0  复现成功   —— 至少一个未出现在导入件里的哨兵值被改成默认
    1  未复现     —— 5 个哨兵值全部保持不变（合并语义生效）
    2  无法判定   —— 构造/导入失败

反证对照：把 ``MBDSDR_REPRO_ROOT`` 指向修复提交父提交（6f8f5fcf^）后重跑，
必须重新出现 frequency_hz → 98e6、observer_* → null、ai_temperature → 默认。

用法：
    python scripts/repro/2026-11-11-p0-06-import-overwrite.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

ROOT = os.environ.get("MBDSDR_REPRO_ROOT") or os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TMP_ROOT = r"D:\mbdsdr\output\tmp"

SENTINELS = {
    "frequency_hz": 145.5e6,
    "tuning_step_hz": 1000,
    "observer_lat": 31.2304,
    "observer_lon": 121.4737,
    "ai_temperature": 0.42,
}


def main() -> int:
    profile = tempfile.mkdtemp(prefix="p0-06-profile-", dir=TMP_ROOT)
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ.setdefault("MBDSDR_OFFLINE", "1")
    os.environ.setdefault("MBDSDR_NO_HW", "1")
    os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")
    for k in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
        os.environ[k] = profile
    sys.path.insert(0, ROOT)
    sys.path.insert(0, os.path.join(ROOT, "desktop"))

    print(f"repo   : {ROOT}")
    print(f"python : {sys.version.split()[0]}")
    print(f"临时档案: {profile}")

    try:
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QApplication, QMessageBox
        QSettings.setDefaultFormat(QSettings.IniFormat)
        for scope in (QSettings.UserScope, QSettings.SystemScope):
            QSettings.setPath(QSettings.IniFormat, scope,
                              os.path.join(profile, "qsettings"))
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
        QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
        QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)
        QMessageBox.critical = staticmethod(lambda *a, **k: QMessageBox.Ok)
        app = QApplication.instance() or QApplication(sys.argv[:1])
        from config_schema import SCHEMA, CONFIG_SCHEMA_VERSION
        from main_window import MainWindow
        window = MainWindow()
        app.processEvents()
    except Exception as exc:  # noqa: BLE001
        print(f"[无法判定] Qt/MainWindow 构造失败：{type(exc).__name__}: {exc}")
        return 2

    panel = window.settings_panel

    def _read(key):
        item = SCHEMA[key]
        try:
            val = panel._current_value(item)
        except Exception:  # noqa: BLE001
            val = None
        if val is None:
            ctl = panel._rows.get(key)
            if hasattr(ctl, "text"):
                try:
                    val = float(ctl.text())
                except (TypeError, ValueError):
                    val = ctl.text()
        return val

    def _write(key, value):
        item = SCHEMA[key]
        if item.storage != "settings":
            # gui_config / ai_config 直接写存储，避免 QLineEdit 未提交焦点
            panel._store_value(item, value)
            panel._refresh_from_storage()
            return "store_value"
        ctl = panel._rows.get(key)
        if ctl is not None:
            if item.kind == "enum":
                idx = list(item.choice_values).index(value)
                ctl.setCurrentIndex(idx)
            elif item.kind == "bool":
                ctl.setChecked(bool(value))
            elif hasattr(ctl, "setValue"):
                ctl.setValue(int(value) if item.kind == "int" else float(value))
            elif hasattr(ctl, "setText"):
                ctl.setText(str(value))
            else:
                raise TypeError(f"{key}: 未知控件 {type(ctl)!r}")
            return "widget"
        panel._store_value(item, value)
        panel._refresh_from_storage()
        return "store_value"

    print("\n" + "=" * 78)
    print("实测：写入哨兵值 → 只导入 theme → 逐项读回")
    print("=" * 78)
    for key, value in SENTINELS.items():
        mode = _write(key, value)
        print(f"  写入 {key:<18} = {value!r:<12}（{mode}）")
    try:
        window.settings.flush()
    except Exception:  # noqa: BLE001
        pass
    app.processEvents()

    before = {k: _read(k) for k in SENTINELS}
    print(f"  导入前：{before}")

    payload = {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "settings": {"theme": "dark"},
    }
    payload_path = os.path.join(profile, "theme-only.json")
    with open(payload_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)

    try:
        result = panel.import_from(payload_path)
        app.processEvents()
    except Exception as exc:  # noqa: BLE001
        print(f"[无法判定] import_from 抛错：{type(exc).__name__}: {exc}")
        return 2
    print(f"  导入件：{payload}")
    print(f"  ImportResult：{result!r}")

    after = {k: _read(k) for k in SENTINELS}
    print(f"  导入后：{after}")

    diffs = []
    for key, want in before.items():
        got = after.get(key)
        same = (got == want)
        if isinstance(want, float) and isinstance(got, (int, float)):
            same = abs(float(got) - float(want)) < 1e-6
        print(f"  {key:<18} 导入前={want!r:<12} 导入后={got!r:<12} "
              f"{'不变' if same else '被改'}")
        if not same:
            diffs.append((key, want, got))

    try:
        window.close()
    except Exception:  # noqa: BLE001
        pass

    reproduced = bool(diffs)
    print(f"\n结论：当前实现复现 P0-06（导入件外键被冲掉）: {reproduced}")
    if reproduced:
        print(f"[复现] {len(diffs)} 个哨兵被改：{diffs}")
        return 0
    print("[未复现] 未出现在导入件里的哨兵值全部保持不变（合并语义）。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
