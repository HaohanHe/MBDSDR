#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""采样率逐档回读检查（scripts/sample_rate_readback_check.py）
=============================================================

对每一档采样率：下发 → 读回后端当前值 → 判定「一致 / 不一致 / 被拒」。

判定口径（与 tests/test_sample_rate_options.py、main_window._on_sample_rate_changed 一致）：
  * 后端返回 False            → 被拒（不得把请求值记成生效值）
  * 读回 != 请求（>1 Hz）     → 不一致（静默吸附/夹到别的值，属假成功）
  * 读回 == 请求              → 一致

用法：
  python scripts/sample_rate_readback_check.py                # auto：有 RTL 就用真机，否则注入三种假后端
  python scripts/sample_rate_readback_check.py --mode real    # 强制真机（无设备直接报错退出）
  python scripts/sample_rate_readback_check.py --mode clamp   # 只跑“静默吸附”假后端，证明检查能抓到假成功
  python scripts/sample_rate_readback_check.py --rates 250000,1024000

退出码：0 = 全部一致或全被拒（无假成功）；1 = 抓到不一致（或在 real 模式下无设备）。
真机结果不可在本仓复跑；无设备时脚本会明确打印「注入验证」。
"""

import argparse
import sys

RATES_HZ = [
    250_000, 1_024_000, 1_536_000, 1_792_000, 1_920_000,
    2_048_000, 2_160_000, 2_400_000, 2_560_000, 2_800_000,
    2_880_000, 3_200_000,
]

#: RTL 后端实际会吸附到的 11 档（与 mbdsdr_ai.sdr_backend.RTLSDRBackend.SAMPLE_RATES 对齐）
RTL_TIERS = [250_000, 1_024_000, 1_536_000, 1_792_000, 1_920_000,
             2_048_000, 2_160_000, 2_400_000, 2_560_000, 2_880_000, 3_200_000]


def _nearest(tiers, value):
    return min(tiers, key=lambda t: (abs(t - value), t))


class _FakeBackend:
    """最小后端协议：set_sample_rate(rate) -> bool；get_sample_rate() -> float。"""

    label = "fake"

    def __init__(self, current=2_048_000):
        self._current = float(current)

    def get_sample_rate(self):
        return self._current

    def set_sample_rate(self, rate):
        raise NotImplementedError


class HonestFake(_FakeBackend):
    """如实接受：读回 = 请求值。"""

    label = "honest（如实接受）"

    def set_sample_rate(self, rate):
        self._current = float(rate)
        return True


class ClampFake(_FakeBackend):
    """静默接受但实际吸附到最近档（模拟 RTL：2.800M → 2.880M）。"""

    label = "clamp（静默吸附）"

    def set_sample_rate(self, rate):
        self._current = float(_nearest(RTL_TIERS, float(rate)))
        return True


class RejectFake(_FakeBackend):
    """档外显式拒绝：返回 False，当前值不变。"""

    label = "reject（显式拒绝）"

    def set_sample_rate(self, rate):
        if float(rate) not in RTL_TIERS:
            return False
        self._current = float(rate)
        return True


def _judge(backend, requested):
    ret = backend.set_sample_rate(float(requested))
    readback = float(backend.get_sample_rate())
    if ret is False:
        return "被拒", readback, f"当前保持 {readback:,.0f} Hz"
    delta = readback - float(requested)
    if abs(delta) > 1.0:
        return "不一致", readback, f"差 {delta:+,.0f} Hz"
    return "一致", readback, ""


def _check(backend, rates):
    print(f"\n后端：{backend.label}")
    print(f"{'请求 Hz':>10}  {'返回':>6}  {'读回 Hz':>10}  {'判定':<6} 说明")
    print("-" * 72)
    bad = rejected = ok = 0
    for rate in rates:
        verdict, readback, note = _judge(backend, rate)
        ret_txt = "True" if verdict != "被拒" else "False"
        print(f"{rate:>10,}  {ret_txt:>6}  {readback:>10,.0f}  {verdict:<6} {note}")
        if verdict == "一致":
            ok += 1
        elif verdict == "被拒":
            rejected += 1
        else:
            bad += 1
    print(f"小结：一致 {ok} / 被拒 {rejected} / 不一致 {bad}"
          f"（共 {len(rates)} 档）")
    return bad


def _real_backend():
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if root not in sys.path:
        sys.path.insert(0, root)
    from mbdsdr_ai.sdr_backend import RTLSDRBackend

    backend = RTLSDRBackend(device_index=0)
    backend.label = "real（RTL-SDR 真机）"
    if not backend.connect():
        raise RuntimeError("真机连接失败（无设备或驱动不可用）")
    return backend


def main(argv=None):
    ap = argparse.ArgumentParser(description="采样率逐档回读检查")
    ap.add_argument("--mode", default="auto",
                    choices=["auto", "real", "honest", "clamp", "reject"],
                    help="auto=有真机用真机，否则注入；其余强制指定")
    ap.add_argument("--rates", default="",
                    help="逗号分隔的请求值（Hz），默认 12 档并集")
    args = ap.parse_args(argv)

    rates = ([int(x) for x in args.rates.split(",") if x.strip()]
             if args.rates else RATES_HZ)

    if args.mode in ("auto", "real"):
        try:
            bad = _check(_real_backend(), rates)
            print("\n结果：真机实测（可复跑仅限同型号设备 + 相同驱动）")
            return 1 if bad else 0
        except Exception as exc:
            if args.mode == "real":
                print(f"真机不可用：{type(exc).__name__}: {exc}")
                return 1
            print(f"真机不可用（{type(exc).__name__}: {exc}），改用注入验证。")

    print("\n注意：真机不可复跑，以下为注入验证——用可控假后端证明检查能区分三种后果。")
    total_bad = 0
    modes = {"honest": [HonestFake()], "clamp": [ClampFake()], "reject": [RejectFake()]}
    if args.mode in modes:
        chosen = modes[args.mode]
    else:
        chosen = [HonestFake(), ClampFake(), RejectFake()]
    for backend in chosen:
        total_bad += _check(backend, rates)

    if total_bad:
        print(f"\n结果：抓到 {total_bad} 档不一致（假成功后端被检查识别）——检查有效。")
        return 1
    print("\n结果：未抓到不一致。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
