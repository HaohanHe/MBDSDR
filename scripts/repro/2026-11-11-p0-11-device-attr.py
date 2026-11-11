#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""P0-11 独立复现脚本：BladeRF/Lime 连接成功路径是否会因 `.device` 抛
AttributeError，把后半段（增益范围/参数下发/读数回填）整段跳过且无提示。

roadmap 行 P0-11 的断言（原文）：
    「BladeRF/Lime 连接成功路径 `.device` AttributeError，半链路中断无提示」
    证据等级：仅静态推断，未实测。

做法：offscreen Qt + 把 HOME/USERPROFILE/APPDATA/LOCALAPPDATA 全部重定向到
临时目录（绝不读写用户真实配置），构造真实 ``MainWindow``，用一个**没有
``device`` 属性**的后端桩调用 ``_connect_backend``，并用计数器观测
``_apply_connect_params`` / ``_backfill_real_rf_readings`` 是否执行。

判定：
  * 连接返回 False，或上面两个步骤有一次没执行，或设备槽没有「设备名称
    不可读」字样 → 断言的「半链路中断」成立 → 复现。
  * 连接返回 True、两步都执行、设备槽明确写出「设备名称不可读」→ 未复现。

退出码（三态）：
    0  复现成功
    1  未复现
    2  无法判定（环境不满足：Qt/字体/构造失败；或反证对照组失效）

反证对照组：把 ``MainWindow._resolve_device_display_name`` 临时换回
「直取 ``backend.device.name``」，同一后端桩必须抛 ``AttributeError``。

用法：
    python scripts/repro/2026-11-11-p0-11-device-attr.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TMP_ROOT = r"D:\mbdsdr\output\tmp"


def _hr(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def main() -> int:
    os.makedirs(TMP_ROOT, exist_ok=True)
    profile = tempfile.mkdtemp(prefix="p0-11-profile-", dir=TMP_ROOT)

    # 先隔离，再 import（MainWindow 构造会读 DesktopSettings / QSettings）
    for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
        os.environ[key] = profile
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    sys.path.insert(0, ROOT)
    sys.path.insert(0, os.path.join(ROOT, "desktop"))

    try:
        import numpy as np
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QApplication

        QSettings.setDefaultFormat(QSettings.IniFormat)
        for scope in (QSettings.UserScope, QSettings.SystemScope):
            QSettings.setPath(QSettings.IniFormat, scope,
                              os.path.join(profile, "qsettings"))
        import layout_store as ls
        ls.reset_layout_store_singleton()
        ls._default_store = ls.LayoutStore(os.path.join(profile, "layouts.json"))  # noqa: SLF001

        app = QApplication.instance() or QApplication(sys.argv)
        import main_window as mw
    except Exception as exc:  # noqa: BLE001
        print(f"[环境不满足] Qt/MainWindow 导入失败：{type(exc).__name__}: {exc}")
        shutil.rmtree(profile, ignore_errors=True)
        return 2

    class _DeviceNoName:
        def __init__(self, max_gain: float = 25.0):
            self.max_gain = max_gain

    class _StubBackendNoDevice:
        """BladeRF / LimeSDR 式：后端根本没有 ``device`` 属性。"""

        def __init__(self):
            self.status = __import__("types").SimpleNamespace(
                error="", gain_db=20.0, demod_mode="FM", connected=False)
            self.connected = False
            self.freq = 98.0e6
            self.sr = 2.048e6

        def connect(self):
            self.connected = True
            return True

        def disconnect(self):
            self.connected = False

        def get_status(self):
            return self.status

        def get_sample_rate(self):
            return self.sr

        def set_sample_rate(self, r):
            self.sr = float(r)

        def get_frequency(self):
            return self.freq

        def set_frequency(self, f):
            self.freq = float(f)

        def set_bandwidth(self, _b):
            pass

        def set_ppm(self, _p):
            pass

        def set_offset_tuning(self, _b):
            return True

        def supports_offset_tuning(self):
            return True

        def set_agc(self, _b):
            return True

        def set_gain(self, _g):
            pass

        def listGains(self, *_a, **_k):
            return []

        def read_samples(self, n):
            return np.zeros(int(n), dtype=np.complex64)

        def subscribe_iq(self, _name):
            __import__("types")
            import types
            return types.SimpleNamespace(read=self.read_samples)

    print(f"repo    : {ROOT}")
    print(f"python  : {sys.version.split()[0]}")
    print(f"临时档案: {profile}（HOME/USERPROFILE/APPDATA/LOCALAPPDATA 全重定向）")

    window = None
    try:
        _hr("构造 MainWindow（offscreen）")
        try:
            window = mw.MainWindow()
            app.processEvents()
        except Exception as exc:  # noqa: BLE001
            print(f"[环境不满足] MainWindow 构造失败：{type(exc).__name__}: {exc}")
            return 2

        counts = {"params": 0, "backfill": 0}
        orig_params = window._apply_connect_params
        orig_backfill = window._backfill_real_rf_readings

        def _params(*a, **k):
            counts["params"] += 1
            return orig_params(*a, **k)

        def _backfill(*a, **k):
            counts["backfill"] += 1
            return orig_backfill(*a, **k)

        window._apply_connect_params = _params
        window._backfill_real_rf_readings = _backfill

        _hr("实测：后端没有 .device 时走连接成功路径")
        backend = _StubBackendNoDevice()
        print(f"  后端桩 hasattr(backend, 'device') = {hasattr(backend, 'device')}")
        print("  请求：MainWindow._connect_backend(backend)")
        crashed = None
        ok = False
        try:
            ok = window._connect_backend(backend)
        except Exception as exc:  # noqa: BLE001
            crashed = exc
        label = ""
        try:
            label = window.conn_label.text()
        except Exception:  # noqa: BLE001
            pass
        print(f"  实际：返回值={ok!r} 异常={type(crashed).__name__ if crashed else '无'}")
        print(f"        _apply_connect_params 执行 {counts['params']} 次，"
              f"_backfill_real_rf_readings 执行 {counts['backfill']} 次")
        print(f"        设备槽文本={label.strip()!r}")
        print("  期望：返回值=True，两步各 1 次，槽内出现「设备名称不可读」")

        half_link = (crashed is not None or not ok
                     or counts["params"] < 1 or counts["backfill"] < 1
                     or "设备名称不可读" not in label)
        if crashed is None and ok:
            try:
                window._teardown_connection()
                app.processEvents()
            except Exception:  # noqa: BLE001
                pass

        _hr("反证对照组：把取名字换回「直取 backend.device.name」")
        legacy_reproduced = False
        saved = mw.MainWindow._resolve_device_display_name
        try:
            mw.MainWindow._resolve_device_display_name = staticmethod(
                lambda backend: (backend.device.name, True))
            backend2 = _StubBackendNoDevice()
            try:
                legacy_ok = window._connect_backend(backend2)
                print(f"  实际：返回值={legacy_ok!r}（未抛异常）")
            except AttributeError as exc:
                legacy_reproduced = True
                print(f"  实际：AttributeError: {exc}（旧行为已复现）")
            except Exception as exc:  # noqa: BLE001
                print(f"  实际：其它异常 {type(exc).__name__}: {exc}")
            if not legacy_reproduced and isinstance(locals().get("legacy_ok"), bool):
                print("  提示：旧实现未抛异常，可能是调用点已另有守卫")
        finally:
            mw.MainWindow._resolve_device_display_name = saved

        _hr("结论")
        print(f"当前实现复现 P0-11（半链路）: {half_link}")
        print(f"反证对照组可复现旧行为      : {legacy_reproduced}")
        if not legacy_reproduced:
            print("\n[无法判定] 反证对照组没能复现 AttributeError，本脚本不可用于判定。")
            return 2
        if half_link:
            print("\n[复现成功] 连接成功路径被设备名打断或未给出「不可读」提示。")
            return 0
        print("\n[未复现] 无 device 的后端仍完整走完连接后半段，并明确提示名称不可读。")
        return 1
    finally:
        if window is not None:
            try:
                window._teardown_connection()
            except Exception:  # noqa: BLE001
                pass
            try:
                window.close()
                window.deleteLater()
                app.processEvents()
            except Exception:  # noqa: BLE001
                pass
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
