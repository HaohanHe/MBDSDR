#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
实验2：AX.25/AFSK Bell202 解调性能（论文数据）。
矩阵：采样率 {11025,22050,44100} × SNR {clean,20,15,10,5,0} dB
链路：44100 合成 -> 降采样到目标率 -> 解调（模拟真实声卡采集）。
指标：FCS(CRC16) 通过率。
"""
import sys, os, csv, math, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mbdsdr_ai import ax25

SR_STEP = {11025: 4, 22050: 2, 44100: 1}
SNRS = [None, 20, 15, 10, 5, 0]
TRIALS = 10


def main():
    m44 = ax25.AFSKModem(sample_rate=44100)
    rows = []
    print(f"{'采样率':>8}" + "".join(f"{('clean' if s is None else str(s)+'dB'):>10}" for s in SNRS))
    for sr in (11025, 22050, 44100):
        step = SR_STEP[sr]
        mc = ax25.AFSKModem(sample_rate=sr)
        line = f"{sr:>8}"
        for snr in SNRS:
            passed = 0
            for k in range(TRIALS):
                f = ax25.build_aprs_position_frame("BI4MIB", 43.88 + k * 0.001, 125.32 + k * 0.001)
                a = m44.modulate(f)[::step].copy()
                if snr is not None:
                    rng = random.Random(1000 * sr + k)
                    sp = max(1e-9, (a ** 2).mean())
                    a = a + rng.gauss(0, math.sqrt(sp / 10 ** (snr / 10.0)))
                out = mc.demodulate(a)
                if out and out[0].fcs_valid:
                    passed += 1
            line += f"{passed}/{TRIALS:>9}"
            rows.append({"sample_rate": sr, "snr_db": "clean" if snr is None else snr,
                         "pass": passed, "trials": TRIALS})
        print(line)
    os.makedirs("paper/experiments", exist_ok=True)
    out = "paper/experiments/ax25_demod_performance.csv"
    with open(out, "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=["sample_rate", "snr_db", "pass", "trials"])
        wr.writeheader(); wr.writerows(rows)
    print(f"\n已写 {out}")


if __name__ == "__main__":
    main()
