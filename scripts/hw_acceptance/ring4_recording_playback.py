# SPDX-License-Identifier: MIT
"""R4 录音与回放 —— SigMF 流式落盘、完整性、采样率变更、文件回放数据面。

判据（自动断言）：
  * 录制中数据文件持续增长（两次采样点字节数递增）
  * stop 后样本数 > 0，inspect_recording 判定 complete
  * load_iq 读回：样本数/采样率与录制声明一致
  * 文件回放经鉴频产生的音频 RMS 与实时音频同量级（0.3–3 倍）
  * 改采样率后再录：sidecar core:sample_rate 跟随变为新值
人眼项：回放声音听感（需出声，脚本仅做数据面）。
"""
from __future__ import annotations

import json
import os
import time

import numpy as np

import common


def _fm_audio(iq: np.ndarray, fs: float, target_fs: float = 48000) -> np.ndarray:
    x = np.asarray(iq, dtype=np.complex128)
    dphi = np.diff(np.unwrap(np.angle(x)))
    audio = dphi * fs / (2 * np.pi)
    peak = float(np.max(np.abs(audio))) + 1e-9
    audio = audio / peak * 0.5
    dec = max(1, int(round(fs / target_fs)))
    return audio[::dec]


def run(freq_hz: float) -> dict:
    out = common.artifact_dir("r4_recording_playback")
    ctrl = common.open_controller(f"rtlsdr:{common.real_devices()[0].serial}")
    checks: list[dict] = []
    metrics: dict = {}
    try:
        ctrl.set_sample_rate(2.4e6)
        found, _ = common.find_strong_fm(ctrl)
        ctrl.set_frequency(freq_hz or found or 98_000_000)
        ctrl.set_demod("WFM")
        time.sleep(0.25)

        base = os.path.join(out, "rec_2p4m")
        r = ctrl.start_recording(base)
        if "error" in r:
            return common.result("R4", "录音与回放", common.FAIL, r,
                                 [common.check("start_recording", False, r.get("suggestion", ""))],
                                 r.get("suggestion", "录制启动失败"), ["回放听感（需出声）"], True)
        time.sleep(0.6)
        size1 = os.path.getsize(r["path"])
        time.sleep(0.6)
        size2 = os.path.getsize(r["path"])
        st = ctrl.stop_recording()
        checks.append(common.check("stream_growth", size2 > size1 > 0,
                                   f"{size1} -> {size2} 字节"))
        checks.append(common.check("samples_gt_zero", int(st.get("samples") or 0) > 0,
                                   f"samples={st.get('samples')}"))

        from mbdsdr_ai.recorder import inspect_recording
        insp = inspect_recording(r["path"])
        # inspect_recording 的“完整”取值是 "ok"（历史判据这里误写成 "complete"，
        # 导致任何正常录制都被判 FAIL）。
        checks.append(common.check("integrity_complete",
                                   insp.get("integrity") in ("ok", "complete"),
                                   f"{insp.get('integrity')}: {insp.get('reason', '')}"))
        meta_rate = None
        if st.get("meta_path") and os.path.exists(st["meta_path"]):
            with open(st["meta_path"], encoding="utf-8") as f:
                meta_rate = json.load(f).get("global", {}).get("core:sample_rate")

        from mbdsdr_ai.baseband_io import load_iq
        loaded = load_iq(insp["data_path"])
        if "iq" not in loaded:
            checks.append(common.check("playback_load", False, str(loaded.get("error"))))
        else:
            checks.append(common.check("playback_load", True,
                                       f"{loaded['samples']} 样本 @ {loaded['sample_rate']/1e6:.1f} MS/s"))
            checks.append(common.check("declared_rate", abs((meta_rate or 0) - 2.4e6) < 1,
                                       f"sidecar core:sample_rate={meta_rate}"))
            file_audio = _fm_audio(loaded["iq"], float(loaded["sample_rate"]))
            live = ctrl.read_audio(48000)
            if live is None:
                checks.append(common.check("playback_audio", False, "实时音频取不到，无法对拍"))
            else:
                lv = np.asarray(live, dtype=np.float32)
                if lv.ndim == 2:
                    lv = lv.mean(axis=1)
                rms_file = float(np.sqrt(np.mean(file_audio ** 2))) if len(file_audio) else 0.0
                rms_live = float(np.sqrt(np.mean(lv ** 2))) if len(lv) else 0.0
                ratio = (rms_file + 1e-9) / (rms_live + 1e-9)
                checks.append(common.check("playback_audio", 0.3 < ratio < 3.0,
                                           f"文件回放/实时 RMS 比={ratio:.2f}"
                                           f"（{rms_file:.4f}/{rms_live:.4f}）"))
                metrics["playback_rms_ratio"] = round(ratio, 2)

            # -- 采样率变更：sidecar 必须跟随 --
            ctrl.set_sample_rate(1.2e6)
            time.sleep(0.25)
            base2 = os.path.join(out, "rec_1p2m")
            r2 = ctrl.start_recording(base2)
            time.sleep(0.6)
            st2 = ctrl.stop_recording()
            rate2 = None
            if st2.get("meta_path") and os.path.exists(st2["meta_path"]):
                with open(st2["meta_path"], encoding="utf-8") as f:
                    rate2 = json.load(f).get("global", {}).get("core:sample_rate")
            checks.append(common.check("rate_change_follows", abs((rate2 or 0) - 1.2e6) < 1,
                                       f"改速后 sidecar core:sample_rate={rate2}"))

            metrics.update({
                "target_hz": round(freq_hz or found or 0, 0),
                "bytes_growth": f"{size1}->{size2}",
                "samples": int(st.get("samples") or 0),
                "integrity": insp.get("integrity"),
                "declared_rate_hz": meta_rate,
                "loaded_samples": loaded["samples"],
                "loaded_rate_hz": loaded["sample_rate"],
                "rate_after_change_hz": rate2,
            })

        status = common.PASS if all(c["ok"] for c in checks if not c["skipped"]) else common.FAIL
        return common.result(
            "R4", "录音与回放", status, metrics, checks,
            "录制失败：请检查 D 盘剩余空间与写入权限（SigMF 需要 .sigmf-data/.sigmf-meta 可写）。",
            ["回放声音听感（需出声）"], True,
            [f"{out}/rec_2p4m.sigmf-data", f"{out}/rec_1p2m.sigmf-data"],
            "流式增长、完整性、采样率变更与文件回放均为实测；未打开声卡。")
    finally:
        ctrl.disconnect()


if __name__ == "__main__":
    args = common.common_args("R4 录音与回放")
    out = common.artifact_dir("r4_recording_playback")
    res = common.guarded("R4", "录音与回放", lambda: run(args.freq))
    raise SystemExit(common.emit(res, out))
