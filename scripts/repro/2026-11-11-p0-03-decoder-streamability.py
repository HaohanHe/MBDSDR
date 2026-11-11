#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""P0-03 独立复现脚本：`start_decoder` 对文件级解码器是否静默空跑。

roadmap 行 P0-03 的断言（原文）：
    「`start_decoder` 对 8 类解码器静默空跑，返回 `ok=True` 恒 0 条」
    证据等级：仅静态推断，未实测。

本脚本不跑 pytest、不占硬件、不写任何产品文件，只调 ``SDRController`` 的
公开方法，把「请求 / 实际 / 期望」打出来。

退出码（三态）：
    0  复现成功   —— 至少一类文件级解码器返回了句柄（等于假成功）
    1  未复现     —— 全部 8 类都抛 ``DecoderNotStreamableError``
    2  无法判定   —— 环境不满足（import 失败等）或反证对照组失效

反证（`--legacy` 也在同一次运行里执行）：
    把模块级 ``STREAMABLE_DECODERS`` 改回「全部解码器都可流式」，同一调用
    就会返回句柄、消息恒 0 —— 证明本脚本能抓住旧行为，不是空跑变绿。

用法：
    python scripts/repro/2026-11-11-p0-03-decoder-streamability.py
"""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

FILE_ONLY = ("aprs", "noaa_apt", "meteor", "sstv", "ft8", "wspr", "pocsag", "acars")
STREAMABLE = ("adsb", "rds")


def _hr(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def main() -> int:
    try:
        import mbdsdr_ai.core.controller as ctrl_mod
        from mbdsdr_ai.core.controller import SDRController
        from mbdsdr_ai.core.errors import (
            DecoderNotAvailableError,
            DecoderNotStreamableError,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[环境不满足] 无法导入 mbdsdr_ai.core.controller: {exc!r}")
        return 2

    print(f"repo    : {ROOT}")
    print(f"python  : {sys.version.split()[0]}")
    print(f"模块常量: STREAMABLE_DECODERS={getattr(ctrl_mod, 'STREAMABLE_DECODERS', None)}")
    print(f"          FILE_ONLY_DECODERS={getattr(ctrl_mod, 'FILE_ONLY_DECODERS', None)}")

    # ---------------- 实测 1：8 类文件级解码器 ----------------
    _hr("实测 A：8 类文件级解码器调用 start_decoder（无硬件、未连接）")
    ctrl = SDRController()
    rows = []
    fake_success = []
    for dt in FILE_ONLY:
        try:
            handle = ctrl.start_decoder(dt)
        except DecoderNotStreamableError as exc:
            rows.append((dt, "DecoderNotStreamableError", str(exc)[:60]))
            print(f"  {dt:10s} -> 拒绝：DecoderNotStreamableError（期望）")
        except DecoderNotAvailableError as exc:
            rows.append((dt, "DecoderNotAvailableError", str(exc)[:60]))
            print(f"  {dt:10s} -> 拒绝：DecoderNotAvailableError")
        except Exception as exc:  # noqa: BLE001
            rows.append((dt, type(exc).__name__, str(exc)[:60]))
            print(f"  {dt:10s} -> 其它异常：{type(exc).__name__}: {exc}")
        else:
            # 旧行为：返回句柄，前端看到「已启动」
            rows.append((dt, "RETURNED_HANDLE", handle))
            fake_success.append(dt)
            time.sleep(0.3)
            msgs = ctrl.get_decoder_messages(handle)
            print(f"  {dt:10s} -> 返回句柄 {handle}，0.3s 后消息数={len(msgs)}（假成功）")
            ctrl.stop_decoder(handle)

    # ---------------- 实测 2：可流式解码器仍应可用（API 没被一刀切） ----------------
    _hr("实测 B：可流式解码器（adsb）必须仍返回句柄")
    streamable_ok = 0
    for dt in STREAMABLE:
        try:
            handle = ctrl.start_decoder(dt)
            streamable_ok += 1
            print(f"  {dt:10s} -> 返回句柄 {handle}（期望：可流式）")
            ctrl.stop_decoder(handle)
        except Exception as exc:  # noqa: BLE001
            print(f"  {dt:10s} -> 异常 {type(exc).__name__}: {exc}（异常：可流式被误拦）")

    # ---------------- 反证对照组：把常量改回旧语义 ----------------
    _hr("反证对照组：STREAMABLE_DECODERS 改回「全部可流式」后重跑 aprs")
    legacy_reproduced = False
    saved = ctrl_mod.STREAMABLE_DECODERS
    try:
        ctrl_mod.STREAMABLE_DECODERS = ctrl_mod.SUPPORTED_DECODERS
        ctrl2 = SDRController()
        try:
            handle = ctrl2.start_decoder("aprs")
        except Exception as exc:  # noqa: BLE001
            print(f"  对照组未能返回句柄：{type(exc).__name__}: {exc}")
        else:
            time.sleep(0.5)
            n = len(ctrl2.get_decoder_messages(handle))
            ctrl2.stop_decoder(handle)
            legacy_reproduced = True
            print(f"  对照组返回句柄 {handle}，0.5s 后消息数={n}"
                  f"（旧行为已复现：不报错、无产出）")
    finally:
        ctrl_mod.STREAMABLE_DECODERS = saved

    # ---------------- 结论 ----------------
    _hr("结论")
    current_reproduced = bool(fake_success)
    print(f"当前实现复现 P0-03 : {current_reproduced}"
          f"（假成功类型={fake_success or '无'}）")
    print(f"可流式解码器可用数 : {streamable_ok}/{len(STREAMABLE)}")
    print(f"反证对照组可复现旧行为: {legacy_reproduced}")

    if not legacy_reproduced:
        print("\n[无法判定] 反证对照组没能复现旧行为，本脚本不可用于判定当前实现。")
        return 2
    if current_reproduced:
        print("\n[复现成功] 文件级解码器仍被 start_decoder 当成可流式 → 假成功。")
        return 0
    print("\n[未复现] 8 类文件级解码器全部被显式拒绝，不存在「返回 ok 恒 0 条」。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
