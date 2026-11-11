#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""P0-04 独立复现脚本：WAV 解 0 帧是否被当成成功（无法区分三态）。

roadmap 行 P0-04 的断言（原文）：
    「WAV 解 0 帧被当成成功，无法区分没信号／前导不对／算法失败」
    证据等级：仅静态推断，未实测。

做法：现场生成 PCM16 单声道 WAV，走公开入口
``decode_router.run_digital_mode(..., mode='acars')``。ACARS 依赖是纯
Python（``mbdsdr_ai.acars_decoder``），不需要外部工具与硬件。

判定只针对**原断言**（「0 帧 → 成功」）：
  * 静音 WAV（0 帧、无能量）应当 ok=False / status=empty /
    error_code=nothing_detected；若仍 ok=True 且 0 条 → 原断言复现。
  * 另一段「有能量但解不出可信帧」的噪声 WAV 作为**同族探针**单独统计，
    不参与本断言判定（它的失败形态是「解出乱码帧」而不是「0 帧」）。

退出码（三态）：
    0  复现成功   —— 静音 WAV 仍被当成成功（ok=True 且 0 条）
    1  未复现     —— 静音 WAV 显式失败并带 nothing_detected
    2  无法判定   —— 环境不满足 / 反证对照组失效

反证对照组：把 ``_finalize_honest_status`` 换成空操作（等价于旧实现不做
最后一步收敛），静音 WAV 必须重新变成 ok=True + 0 条。对照组失效则本脚本
不可用于判定。

产物：临时 WAV 写 ``D:\\mbdsdr\\output\\tmp\\p0-04-<pid>``，结束后删除。

用法：
    python scripts/repro/2026-11-11-p0-04-empty-frame-evidence.py
"""
from __future__ import annotations

import os
import shutil
import sys
import wave

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
TMP_ROOT = r"D:\mbdsdr\output\tmp"


def _hr(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def _write_wav(path: str, samples: np.ndarray, fs: int = 48000) -> None:
    pcm = np.clip(samples, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype("<i2")
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(fs)
        wf.writeframes(pcm.tobytes())


def _describe(res: dict) -> str:
    return (f"ok={res.get('ok')!r} status={res.get('status')!r} "
            f"error_code={res.get('error_code')!r} "
            f"num_messages={res.get('num_messages')!r} "
            f"preamble_hits={res.get('preamble_hits')!r} "
            f"crc_ok={res.get('crc_ok_count')!r}")


def main() -> int:
    try:
        import mbdsdr_ai.decode_router as router
        from mbdsdr_ai import decode_router
    except Exception as exc:  # noqa: BLE001
        print(f"[环境不满足] 无法导入 mbdsdr_ai.decode_router: {exc!r}")
        return 2

    os.makedirs(TMP_ROOT, exist_ok=True)
    work = os.path.join(TMP_ROOT, f"p0-04-{os.getpid()}")
    os.makedirs(work, exist_ok=True)
    silent = os.path.join(work, "silent.wav")

    try:
        fs = 48000
        n = fs  # 1.0 s
        _write_wav(silent, np.zeros(n, dtype=np.float64), fs)

        print(f"repo      : {ROOT}")
        print(f"python    : {sys.version.split()[0]}")
        print(f"样本目录  : {work}")
        print(f"静音文件  : {silent}（1.0s / 48kHz / PCM16 / RMS=0）")
        print(f"入口      : decode_router.run_digital_mode(mode='acars')")
        print(f"三态码常量: {getattr(decode_router, 'ERR_NOTHING_DETECTED', None)!r} / "
              f"{getattr(decode_router, 'ERR_PREAMBLE_NOT_FOUND', None)!r} / "
              f"{getattr(decode_router, 'ERR_SIGNAL_PRESENT_NO_FRAME', None)!r}")

        _hr("实测 A（原断言）：静音 WAV —— 0 帧是否被当成成功")
        r_silent = decode_router.run_digital_mode(silent, "acars")
        print("  请求：解一段 1s 全零 PCM16 WAV（无任何信号）")
        print(f"  实际：{_describe(r_silent)}")
        print("  期望：ok=False, status='empty', error_code='nothing_detected'")
        claim_reproduced = (r_silent.get("ok") is True
                            and int(r_silent.get("num_messages") or 0) == 0)
        silent_ok = (r_silent.get("ok") is False
                     and r_silent.get("error_code")
                     == decode_router.ERR_NOTHING_DETECTED
                     and r_silent.get("status") == "empty")
        print(f"  原断言复现={claim_reproduced}；符合三态={silent_ok}")

        _hr("反证对照组：_finalize_honest_status 置空（=旧实现不做收敛）")
        saved = router._finalize_honest_status
        legacy_reproduced = False
        try:
            router._finalize_honest_status = lambda result, signal_present: None
            r_legacy = decode_router.run_digital_mode(silent, "acars")
            print(f"  静音：{_describe(r_legacy)}")
            legacy_reproduced = (r_legacy.get("ok") is True
                                 and int(r_legacy.get("num_messages") or 0) == 0)
            print(f"  对照组复现「ok=True 且 0 条」: {legacy_reproduced}")
        finally:
            router._finalize_honest_status = saved

        _hr("同族探针（不参与本断言判定）：有能量但无可信帧")
        print("  5 个固定种子 × 1s 均匀噪声 WAV；统计「ok=True 且 0 条 CRC 通过」")
        hits = 0
        for seed in (20261111, 1, 2, 3, 4):
            p = os.path.join(work, f"noise-{seed}.wav")
            _write_wav(p, np.random.default_rng(seed).uniform(-0.2, 0.2, n), fs)
            r = decode_router.run_digital_mode(p, "acars")
            bad = (r.get("ok") is True
                   and int(r.get("num_messages") or 0) > 0
                   and int(r.get("crc_ok_count") or 0) == 0
                   and int(r.get("preamble_hits") or 0) == 0)
            hits += 1 if bad else 0
            first = (r.get("messages") or [{}])[0]
            print(f"  seed={seed:<9d} {_describe(r)}")
            if bad:
                print(f"    首条报文（乱码）：{str(first.get('text', ''))[:60]!r}")
        print(f"  命中「无前导、0 条 CRC 通过、仍报 decoded」：{hits}/5")

        _hr("结论")
        print(f"原断言（0 帧→成功）复现 : {claim_reproduced}")
        print(f"反证对照组可复现旧行为  : {legacy_reproduced}")
        print(f"同族探针命中            : {hits}/5（另立条目，不计入本断言）")

        if not legacy_reproduced:
            print("\n[无法判定] 反证对照组没能复现旧行为，本脚本不可用于判定。")
            return 2
        if claim_reproduced:
            print("\n[复现成功] 静音 WAV 仍返回 ok=True 且 0 条。")
            return 0
        print("\n[未复现] 静音 WAV 已显式失败并带 nothing_detected；"
              "原断言「0 帧当成成功」不成立。")
        return 1
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
