# SPDX-License-Identifier: MIT
"""集成测试里「真的连上一台 SDR」的唯一帮助函数。

为什么单独抽出来（2026-11-12）：``test_full_integration.py`` 与
``test_full_integration_v2.py`` 各自抄了一份 ``_sdr_connect_or_skip``，
里面把 device_id 写死成 ``rtl_0``，连不上就 ``pytest.skip``。设备身份键
换成 ``driver|serial:...`` 之后，那两份用例**必然 skip**——用例是绿的，
整条 SDR 工具链却一次都没被验证过。绿色但已经死了的测试比红的测试更贵：
红的一眼能看见，这种要等到设备真的坏了才会发现。

判据（用户 2026-11-12 裁定）：

* **拿不到硬件**（枚举为空）→ 允许 ``pytest.skip``；
* **拿不到 id / 接口契约变了 / 枚举到了却连不上** → 必须**红**，
  并且说清原因。

这两件事以前被同一个 skip 混在一起，正是「静默空转」的根因。
"""
from __future__ import annotations

import json
from typing import Any, Optional

import pytest


def _tool_result_fields(result: Any):
    return (getattr(result, "success", None), getattr(result, "content", "") or "")


def first_enumerated_device_id(tr) -> Optional[str]:
    """从 ``sdr_list_devices`` 取第一台设备 id；枚举为空返回 ``None``。

    除「枚举为空」外的任何异常返回都抛 ``AssertionError``：调用失败、
    返回不是 JSON 列表、设备条目缺 ``device_id``——这些都不是「没硬件」，
    是工具面契约变了。把它们也当成 skip，就等于让工具链在契约破掉之后
    继续绿着。
    """
    try:
        r = tr.call("sdr_list_devices", {})
    except Exception as e:  # noqa: BLE001 - 工具调用本身炸了同样是契约问题
        raise AssertionError(
            f"sdr_list_devices 调用异常，工具接口契约已变：{type(e).__name__}: {e}") from e
    success, content = _tool_result_fields(r)
    if success is not True:
        raise AssertionError(
            f"sdr_list_devices 失败（枚举本身不可用，不是「没插设备」）：{content[:300]}")
    try:
        devices = json.loads(content or "[]")
    except Exception as e:  # noqa: BLE001
        raise AssertionError(
            f"sdr_list_devices 返回的不是 JSON：{e}: {content[:300]}") from e
    if not isinstance(devices, list):
        raise AssertionError(
            f"sdr_list_devices 返回类型应为列表，实际 {type(devices).__name__}: {content[:300]}")
    if not devices:
        return None
    first = devices[0]
    if not isinstance(first, dict):
        raise AssertionError(f"sdr_list_devices 设备条目应为对象，实际 {first!r}")
    device_id = first.get("device_id")
    if not device_id:
        raise AssertionError(
            f"sdr_list_devices 枚举到了设备但缺 device_id 字段：{first!r}")
    return str(device_id)


def connect_first_device_or_skip(tr) -> str:
    """连接枚举到的第一台设备，返回它的 id。

    只有**枚举为空**才 skip（本机确实没有硬件）。枚举到设备却连不上必须红：
    「改成会 skip」不算修好——那样换 id 之后用例照样绿着空转。
    """
    device_id = first_enumerated_device_id(tr)
    if device_id is None:
        pytest.skip(
            "缺：sdr_list_devices 枚举为空——本机没有可用 SDR"
            "（RTL-SDR 0bda:2838/2832 与 SoapySDR 均未命中）｜"
            "可跑：插上 RTL-SDR 后先 `rtl_test -t` 确认能枚举；Linux 还需 udev 规则"
            "（/etc/udev/rules.d/99-rtlsdr.rules）并把用户加进 plugdev 组｜"
            "区分：「枚举为空」= 没插或没权限；「插了但打不开」要到下一步 sdr_connect "
            "才暴露，那条路径本函数判红、不 skip"
        )
    try:
        r = tr.call("sdr_connect", {"device_id": device_id})
    except Exception as e:  # noqa: BLE001
        raise AssertionError(
            f"已枚举到设备 {device_id!r}，但 sdr_connect 抛异常："
            f"{type(e).__name__}: {e}") from e
    success, content = _tool_result_fields(r)
    if success is not True:
        raise AssertionError(
            f"已从 sdr_list_devices 枚举到设备 {device_id!r}，但 sdr_connect 失败："
            f"{content[:400]}")
    return device_id
