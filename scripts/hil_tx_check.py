#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""发射 HIL（hardware-in-the-loop）人工验证清单。

用途
----
在真实 SDR 上验证发射链时，给操作员一份**必须逐项人工确认**的清单。本脚本
**不发波、不连接设备、不签发 token**，只做两件事：

1. 探测是否存在可用 SDR；没有就打印「未连接硬件，无法验证」并以退出码
   ``3`` 结束——这是 ``not_verified`` 的诚实结论，不伪造通过。
2. 有硬件时打印需要人工确认的三类硬指标：频段、功率、频谱掩码，以及安全
   前置条件。脚本退出码 ``0`` 只代表「清单已给出」，**不代表验证通过**。

退出码
------
  0  有可用 SDR，已打印人工验证清单（结果仍需人工记录，默认 not_verified）
  3  未连接硬件（或缺少 SoapySDR），无法验证
  4  参数/环境问题（如无法导入 mbdsdr_ai）

用法
----
    python scripts/hil_tx_check.py
    python scripts/hil_tx_check.py --device "driver=hackrf,serial=..."
"""
from __future__ import annotations

import argparse
import os
import sys

# 允许从仓库根目录直接运行
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

EXIT_OK = 0
EXIT_NO_HARDWARE = 3
EXIT_ENV_ERROR = 4


def _available_sdrs():
    """返回可用 SDR 列表；探测失败如实返回空列表（不假装有设备）。"""
    from mbdsdr_ai.hal import SoapySDRBackend

    try:
        devices = SoapySDRBackend().list_devices()
    except Exception as e:  # noqa: BLE001 - 探测失败不能变成「有硬件」
        print(f"硬件探测失败（{type(e).__name__}: {e}），无法验证")
        return []
    return [d for d in devices if getattr(d, "is_available", False)]


def _bands_from_env():
    raw = (os.environ.get("MBDSDR_TX_BANDS") or "").strip()
    return raw or ""


def _print_checklist(devices, device_hint: str) -> None:
    print("=== 发射 HIL 人工验证清单（not_verified，需操作员逐项确认） ===")
    print(f"设备筛选: {device_hint or '（未指定，已检测到以下可用设备）'}")
    for d in devices:
        print(f"  - {d.name} | driver={d.driver} | serial={d.serial or '（未知）'}")
    if not devices:
        print("  - （无）")

    bands = _bands_from_env()
    if bands:
        print(f"法规频段（MBDSDR_TX_BANDS）: {bands}")
    else:
        print("法规频段（MBDSDR_TX_BANDS）: 未配置——先配置允许频段，否则不存在"
              "可判定的合规频段")

    print("\n[1] 频段")
    print("  [ ] 发射频点落在 MBDSDR_TX_BANDS 声明的允许频段内")
    print("  [ ] 已在当地法规/执照允许范围内，且不占用禁发频段")
    print("  [ ] 频点与设备 TX 量程一致（HAL 的 tx_range 已核对）")

    print("\n[2] 功率")
    print("  [ ] TX 增益/衰减值已记录（软件值不等于天线口实测功率）")
    print("  [ ] 使用假负载或屏蔽箱，确认不会向空中意外辐射")
    print("  [ ] 实测功率/EIRP 在法规与设备允许范围内，功放未过驱动")
    print("  [ ] 天线/馈线连接正确，驻波与温升无异常")

    print("\n[3] 频谱掩码")
    print("  [ ] 占用带宽符合目标模式与法规要求")
    print("  [ ] 邻道泄漏/带外杂散低于允许掩码")
    print("  [ ] 谐波与镜像分量已用频谱仪核查")
    print("  [ ] 发射结束后频谱回到噪声底（无持续杂散/自激）")

    print("\n[结果记录]")
    print("  默认结论: not_verified（未上真机/未逐项确认）")
    print("  只有上述项目全部有实测证据时，才可改为 verified，并写明仪器、"
          "频点、功率与测量时间。")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="发射 HIL 人工验证清单（不发波、不连接设备）")
    parser.add_argument(
        "--device", default="",
        help="只提示要核对的设备（driver=...,serial=...）；脚本不会连接它")
    args = parser.parse_args(argv)

    try:
        devices = _available_sdrs()
    except ImportError as e:
        print(f"无法导入 mbdsdr_ai（{e}），无法验证")
        return EXIT_ENV_ERROR

    if not devices:
        print("未连接硬件，无法验证")
        print("（未检测到可用的 SDR；插好设备并安装对应 SoapySDR 驱动后重试）")
        return EXIT_NO_HARDWARE

    _print_checklist(devices, args.device)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
