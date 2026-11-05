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

#: 反复开关时 USB 句柄释放有延迟：重枚举可能瞬时看不到设备/被占用。
#: 只对这类瞬时错误重试，次数记进 metrics，不隐藏。
TRANSIENT_ATTEMPTS = 4


def _connect_with_retry(ctrl, device_id: str) -> int:
    """连接；返回本周期重试次数（0 = 一次成功）。非瞬时错误立即抛出。"""
    last: Exception | None = None
    for i in range(TRANSIENT_ATTEMPTS):
        try:
            ctrl.connect(device_id)
            return i
        except Exception as e:  # noqa: BLE001 - 需要按错误类型分流
            last = e
            if not common.transient_open_error(e):
                raise
            time.sleep(0.25 * (i + 1))
    assert last is not None
    raise last


def _open_raw_with_retry() -> None:
    """新开一个 pyrtlsdr 句柄（释放终检）；同样容忍瞬时枚举空窗。"""
    from rtlsdr import RtlSdr

    last: Exception | None = None
    for i in range(TRANSIENT_ATTEMPTS):
        try:
            s = RtlSdr()
            s.close()
            return
        except Exception as e:  # noqa: BLE001
            last = e
            if not common.transient_open_error(e):
                raise
            time.sleep(0.25 * (i + 1))
    assert last is not None
    raise last


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
        return common.skip_result(
            "R1", "连接与识别", "NO_HW",
            "未枚举到真实 SDR（或只看到 debug 调试源，按红线不算硬件）；"
            "连接耗时、tuner 识别、反复开关均无数字可报。",
            metrics, checks, common.USER_MSG["no_device"], [], True)
    if not checks[0]["ok"]:
        return common.result("R1", "连接与识别", common.FAIL, metrics, checks,
                             common.USER_MSG["driver_missing"], [], True)

    serial = str(getattr(devs[0], "serial", "") or "")
    device_id = f"rtlsdr:{serial}" if serial and serial != "debug" else serial

    # -- tuner 识别：必须在控制器连接**之前**做 --
    # 同一根 RTL-SDR 只能有一个客户端；控制器已占用时再开 RtlSdr() 必然
    # LIBUSB_ERROR_ACCESS（历史实现把这一步放在连接后，导致误判 FAIL）。
    tuner = ""
    try:
        from rtlsdr import RtlSdr
        s = RtlSdr()
        tuner = str(s.get_tuner_type())
        s.close()
        checks.append(common.check("tuner_identified", bool(tuner), f"tuner={tuner}"))
    except Exception as e:
        checks.append(common.check("tuner_identified", False, f"pyrtlsdr 打不开：{e}"))

    # -- 单次连接（含 USB 释放后的短暂重试） --
    try:
        ctrl = common.open_controller(device_id)
    except Exception as e:
        msg = str(e)
        checks.append(common.check("connect", False, msg))
        if common.access_denied(e):
            return common.skip_result(
                "R1", "连接与识别", "NEEDS_OPERATOR",
                "设备已枚举但打不开（LIBUSB_ERROR_ACCESS / 被占用），"
                "连接耗时、tuner 识别、反复开关均无法验证；这不是环缺陷。",
                metrics, checks, common.USER_MSG["device_access_denied"],
                ["关闭占用程序或把驱动换成 WinUSB 后复跑"], True)
        user = common.USER_MSG["device_busy"] if ("busy" in msg.lower() or "-3" in msg) \
            else common.USER_MSG["driver_missing"]
        return common.result("R1", "连接与识别", common.FAIL, metrics, checks, user, [], True)
    checks.append(common.check("connect", True, f"connect({device_id}) -> True"))
    metrics["tuner"] = tuner
    metrics["device_id"] = device_id

    # -- 5 次反复开关（瞬时枚举空窗重试，次数照实记账） --
    cycles = 0
    retries = 0
    for _ in range(5):
        ctrl.disconnect()
        retries += _connect_with_retry(ctrl, device_id)
        cycles += 1
    ctrl.disconnect()
    metrics["connect_disconnect_cycles"] = cycles
    metrics["cycle_transient_retries"] = retries
    checks.append(common.check(
        "five_cycles", cycles == 5,
        f"{cycles}/5 次开关成功（瞬时重试 {retries} 次）"))
    checks.append(common.check("disconnected_after_cycles", not ctrl.is_connected(),
                               f"is_connected={ctrl.is_connected()}"))

    # -- 释放终检：循环后新句柄仍能开 --
    released = False
    try:
        _open_raw_with_retry()
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
