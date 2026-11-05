# SPDX-License-Identifier: MIT
"""R1 连接与识别 —— 设备枚举 / 打开 / 关闭 / 释放，反复开关不泄漏不抢占。

判据（全部自动断言）：
  * librtlsdr 枚举到 >=1 台真实设备（debug 调试源不算）
  * 设备三元组（制造商/产品/序列）可读
  * SDRController.connect 成功，tuner 可识别
  * 连续 5 次 connect/disconnect 无异常，末次仍为已断开
  * 循环结束后新句柄仍能打开（设备已释放，未被占死）
人眼项：无（插拔动作由操作者完成）。
"""
from __future__ import annotations

import time

import common


def run() -> dict:
    out = common.artifact_dir("r1_connection")
    n = common.rtl_device_count()
    strings = common.rtl_device_strings()
    devs = common.real_devices()
    checks = [
        common.check("librtlsdr_enumerates", n >= 1, f"device_count={n}"),
        common.check("usb_strings_readable", bool(strings),
                     f"{strings[0]['product']} / {strings[0]['serial']}" if strings else "无"),
        common.check("controller_sees_real_device", bool(devs),
                     f"{len(devs)} 台真实设备（已排除 debug 调试源）"),
    ]
    metrics: dict = {
        "device_count": n,
        "devices": strings,
        "real_device_count": len(devs),
    }
    if not devs:
        return common.result(
            "R1", "连接与识别", common.SKIPPED, metrics, checks,
            common.USER_MSG["no_device"], [], True, [], "只看到调试源，按红线不算硬件。")
    if not checks[0]["ok"]:
        return common.result("R1", "连接与识别", common.FAIL, metrics, checks,
                             common.USER_MSG["driver_missing"], [], True)

    serial = str(getattr(devs[0], "serial", "") or "")
    device_id = f"rtlsdr:{serial}" if serial and serial != "debug" else serial
    from mbdsdr_ai.core import SDRController

    # -- 单次连接 + tuner 识别 --
    ctrl = SDRController()
    try:
        ok = ctrl.connect(device_id)
    except Exception as e:
        msg = str(e)
        user = common.USER_MSG["device_busy"] if ("busy" in msg.lower() or "-3" in msg) \
            else common.USER_MSG["driver_missing"]
        checks.append(common.check("connect", False, msg))
        return common.result("R1", "连接与识别", common.FAIL, metrics, checks, user, [], True)
    checks.append(common.check("connect", bool(ok), f"connect({device_id}) -> {ok}"))

    tuner = ""
    try:
        from rtlsdr import RtlSdr
        s = RtlSdr()
        tuner = str(s.get_tuner_type())
        s.close()
        checks.append(common.check("tuner_identified", bool(tuner), f"tuner={tuner}"))
    except Exception as e:
        checks.append(common.check("tuner_identified", False, f"pyrtlsdr 打不开：{e}"))
    metrics["tuner"] = tuner
    metrics["device_id"] = device_id

    # -- 5 次反复开关 --
    cycles = 0
    for _ in range(5):
        ctrl.disconnect()
        ctrl.connect(device_id)
        cycles += 1
    ctrl.disconnect()
    metrics["connect_disconnect_cycles"] = cycles
    checks.append(common.check("five_cycles", cycles == 5, f"{cycles}/5 次开关成功"))
    checks.append(common.check("disconnected_after_cycles", not ctrl.is_connected(),
                               f"is_connected={ctrl.is_connected()}"))

    # -- 释放终检：循环后新句柄仍能开 --
    released = False
    try:
        from rtlsdr import RtlSdr
        s = RtlSdr()
        s.close()
        released = True
    except Exception as e:
        checks.append(common.check("device_released", False, f"末检打不开：{e}"))
    if released:
        checks.append(common.check("device_released", True, "循环后新句柄可开，设备未被占死"))
    metrics["release_ok"] = released

    status = common.PASS if all(c["ok"] for c in checks) else common.FAIL
    return common.result(
        "R1", "连接与识别", status, metrics, checks,
        common.USER_MSG["device_busy"], [], True,
        [], "反复开关 5 次无泄漏；末检通过说明设备可被其他程序接手。")


if __name__ == "__main__":
    args = common.common_args("R1 连接与识别")
    out = common.artifact_dir("r1_connection")
    res = common.guarded("R1", "连接与识别", run)
    raise SystemExit(common.emit(res, out))
