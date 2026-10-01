#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
实验：OTA/录制摄取诚实空态（handoff）
=========================================

管线支持 --recording <file> 或 --recordings-dir <dir>：
  - 有 SigMF 录制 -> 经 common.datasource.SigMFReplay（包 playback.IQPlayback）
    打开，上报真实 sample_rate / center_freq / datetime，并跑多普勒前端观测提取
    （extract_doppler_observations）。
  - 无录制 -> 明确打印"未提供录制数据，OTA/录制结果为空态"，退出码 0（非失败），
    只写一份空态 manifest。**绝不**用合成数据补 OTA 格。

云内（无硬件、无录制）跑本脚本应输出空态；真机用户把 RTL-SDR 的 SigMF 录制
放进目录后重跑即可得到 recorded 口径产物。
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np  # noqa: F401

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from experiments.common import datasource, manifest  # noqa: E402

OUT_DIR = os.path.join(ROOT, "paper", "experiments")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--recording", default=None,
                    help="具体 SigMF .sigmf-data 或 .wav 录制文件")
    ap.add_argument("--recordings-dir", default=None,
                    help="扫描该目录下所有 .sigmf-data")
    ap.add_argument("--seed", type=int, default=20261001)
    ap.add_argument("--out", default=OUT_DIR)
    args = ap.parse_args()

    kind, source, msg = datasource.resolve_source(args.recording,
                                                  args.recordings_dir)
    print(f"[OTA handoff] {msg}")

    if kind == "none":
        mpath = manifest.write_manifest(
            out_dir=args.out, script=__file__, seed=args.seed,
            data_origin="ota",
            params={"recording": args.recording,
                    "recordings_dir": args.recordings_dir,
                    "status": "empty_state_no_recording"},
            n_samples=0,
            extra={"note": "未提供录制数据，OTA/录制结果为空态；不合成冒充 OTA"},
            filename="manifest_ota_handoff.json")
        print("[空态] n_samples=0；不产出 recorded/ota 数据图。")
        print(f"[写] {mpath}")
        print("\n如何接真机录制：")
        print("  1) RTL-SDR 录制出标准 SigMF（cpp recorder 已写 .sigmf-data + .sigmf-meta）")
        print("  2) 本脚本 --recordings-dir <dir> 或 --recording <xxx.sigmf-data>")
        print("  3) 重跑 -> 自动 playback.IQPlayback 读取并跑 extract_doppler_observations")
        return 0

    if isinstance(source, datasource.SigMFReplay):
        h = source.open()
        print(f"[录制] {h.data_path}")
        print(f"  fs={h.sample_rate_hz:.0f} Hz, f0={h.center_freq_hz:.0f} Hz, "
              f"datetime={h.datetime}, samples={h.n_samples}")
        iq = source.read(min(h.n_samples, 4_000_000))
        source.close()
        if h.sample_rate_hz > 0 and iq.size > 0:
            from mbdsdr_ai import orbit_determination as od
            obs = od.extract_doppler_observations(
                iq, h.sample_rate_hz,
                f0=h.center_freq_hz or od.F0_DEFAULT_HZ)
            print(f"[录制] extract_doppler_observations -> {len(obs)} 个有效多普勒观测")
            n_obs = len(obs)
        else:
            print("[录制] sample_rate 未知或样本为空，跳过观测提取（诚实空态）")
            n_obs = 0
        mpath = manifest.write_manifest(
            out_dir=args.out, script=__file__, seed=args.seed,
            data_origin="recorded",
            params={"data_path": h.data_path, "sample_rate_hz": h.sample_rate_hz,
                    "center_freq_hz": h.center_freq_hz,
                    "n_doppler_observations": n_obs},
            n_samples=n_obs,
            filename="manifest_ota_handoff.json")
        print(f"[写] {mpath}")
    else:
        print("[录制] WAV 回放源已就绪（load_iq_file）；本 handoff 仅演示空态路径。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
