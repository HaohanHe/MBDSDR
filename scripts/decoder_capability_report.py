# SPDX-License-Identifier: MIT
"""解码器能力矩阵生成器 + 可复跑入口（P5.4）。

一句话用途：回答「这个解码器到底是不是真的」——
看生成表格的「真实度」与「证据」两列即可，再把「复跑方式」原样粘到终端
就能自己验一遍。

数据来源单一化：
  * 模式清单 / 采样率约束 / 最小采样条件 / 真实度 —— 来自
    `mbdsdr_ai.decode_router.DIGITAL_MODE_CAPABILITIES`（P5.2 能力表）；
  * 证据位置 / 复跑命令 / 人话结论 —— 本文件的 EVIDENCE 表；
  * 输出 `docs/learn/decoder-capability-matrix.md` 整篇由本脚本生成，
    `tests/test_decoder_capability_matrix.py` 会校验文档与生成结果逐字一致，
    改能力表却忘了刷新文档会直接红。

用法：
    python scripts/decoder_capability_report.py              # 打印表
    python scripts/decoder_capability_report.py --write      # 刷新文档
    python scripts/decoder_capability_report.py --verify     # 跑每条最小命令
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC_PATH = os.path.join(ROOT, "docs", "learn", "decoder-capability-matrix.md")

sys.path.insert(0, ROOT)


def _load_sensitivity():
    """同一份口径来源：灵敏度脚本里的 MODE_BASIS / 默认网格，避免两处漂移。"""
    import importlib.util

    path = os.path.join(ROOT, "scripts", "decode_sensitivity.py")
    spec = importlib.util.spec_from_file_location("decode_sensitivity", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SENSITIVITY = _load_sensitivity()

TRUTH_LABELS = {
    "real": "真实",
    "partial": "部分",
    "no_sample": "缺样本",
}

# 证据来历：三类，互斥。external-truth 才允许出现在「真实」栏。
TRUTH_SOURCE_LABELS = {
    "external-truth": "外部真值",
    "self-generated": "自生成",
    "synthetic-loopback": "合成回环",
}

#: 全仓 golden/fixture 的 provenance.json（逐文件来历声明）。
PROVENANCE_GLOBS = ("tests", "mbdsdr_ai/tests")

#: 没有入仓 golden、但属于「自生成/回环」的链，单独登记（文件必须存在）。
LOOPBACK_CHAINS = [
    {
        "name": "M17（C++ 侧）",
        "truth_source": "synthetic-loopback",
        "evidence": "cpp/tests/test_m17_decoder.cpp",
        "note": "仓内编码→仓内解码回环，无入仓 golden；不得写成外部对拍",
    },
    {
        "name": "CCSDS RX 链",
        "truth_source": "synthetic-loopback",
        "evidence": "mbdsdr_ai/tests/test_ccsds_rx.py",
        "note": "仓内卷积编码→Viterbi 回环；CCSDS TC/CLTU 的黄金向量是官方 PDF 转录（见资产表）",
    },
]


def load_provenance() -> list:
    """读取全仓 provenance.json，返回 [(相对目录, 声明 dict)]。"""
    found = []
    for top in PROVENANCE_GLOBS:
        base = os.path.join(ROOT, top)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            if "provenance.json" not in filenames:
                continue
            path = os.path.join(dirpath, "provenance.json")
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            rel = os.path.relpath(dirpath, ROOT).replace(os.sep, "/")
            found.append((rel, data))
    return sorted(found)


# mode -> (证据位置, 复跑命令, 一句话结论)
# 只写仓内能直接复跑的东西；外部第三方样本按需下载，离线诚实 skip。
EVIDENCE = {
    "adsb": (
        "tests/test_adsb_dump1090_golden.py（dump1090 官方 modes1.bin 逐帧对拍）"
        "；tests/test_adsb_consistency.py（合成链）；"
        "本机 1090MHz 真录 2026-10-29 三次 0 CRC 帧 = 负证据（如实保留）",
        "python -m pytest tests/test_adsb_dump1090_golden.py tests/test_adsb_consistency.py -q",
        "真（外部真值）：dump1090 官方样本两条帧逐字命中；通用入口已做多帧扫描"
        "（每个前导独立 CRC），但本机 OTA 真录尚未破零",
    ),
    "ft8": (
        "tests/test_ft8_wsjtx_golden.py（WSJT-X 官方 210703_133430.wav，"
        "5/14 精确命中、零假接受）；tests/test_ft8_cross_impl_fixture.py",
        "python -m pytest tests/test_ft8_wsjtx_golden.py tests/test_ft8_cross_impl_fixture.py -q",
        "真（外部真值）：官方样本 5/14 命中；通用 WAV 入口未接线（not_implemented）",
    ),
    "ft4": (
        "mbdsdr_ai/tests/test_decoders_ft4_wspr.py（自体往返 + 窄带拒绝契约）",
        "python -m pytest mbdsdr_ai/tests/test_decoders_ft4_wspr.py -q",
        "半个真：算法链自洽，缺 OTA 真录对拍",
    ),
    "wspr": (
        "mbdsdr_ai/tests/test_decoders_ft4_wspr.py（四音网格 + 时间/频率偏移搜索）",
        "python -m pytest mbdsdr_ai/tests/test_decoders_ft4_wspr.py -q",
        "半个真：算法链自洽，缺 OTA 真录对拍",
    ),
    "pocsag": (
        "tests/test_pocsag_roundtrip.py（BCH(31,21) 纠错 + 报文往返）",
        "python -m pytest tests/test_pocsag_roundtrip.py -q",
        "半个真：合成链自洽，缺真实寻呼台真录",
    ),
    "acars": (
        "tests/test_acars_roundtrip.py / tests/test_acars_consistency.py"
        "（2400baud MSK 往返 + 字段一致性）",
        "python -m pytest tests/test_acars_roundtrip.py tests/test_acars_consistency.py -q",
        "半个真：合成链自洽，缺真实航司报文真录",
    ),
    "ais": (
        "tests/test_ais_decoder.py（GMSK→HDLC→FCS-16→M.1371 字段，合成链）",
        "python -m pytest tests/test_ais_decoder.py -q",
        "尚不算真：无 AIS 信道真录，只有协议性质钉扎",
    ),
    "navtex": (
        "tests/test_navtex_decoder.py（SITOR-B 100bd + CCIR 476 + 时间分集，合成链）",
        "python -m pytest tests/test_navtex_decoder.py -q",
        "尚不算真：无 518kHz 真录，只有协议性质钉扎",
    ),
    "aprs": (
        "tests/test_decode_pipeline_e2e.py（合成 2 帧：NFM 鉴频→Bell202 AFSK"
        "→NRZI→HDLC→CRC-16→AX.25，呼号/digipeater 路径/payload 逐字节对拍；"
        "噪声/截断/CRC 三条失败原因码）",
        "python -m pytest tests/test_decode_pipeline_e2e.py -q",
        "合成验证（synthetic, NOT OTA）：链路已接线，但只有仓内调制 fixture "
        "逐字节对拍，无 OTA 证据",
    ),
    "meteor": (
        "tests/test_meteor_lrpt.py（合成向量）；真录证据在 LRPT 链见下一行",
        "python -m pytest tests/test_meteor_lrpt.py -q",
        "半个真：通用入口只到解调骨架（输出 symbols/bits，不产图像）；"
        "图像链真录证据在 LRPT 真录链",
    ),
}

# 合成 AWGN 灵敏度基线快照（scripts/decode_sensitivity.py 的实测结果，2026-11-06）。
# 只用于「同实现内相对比较」，不是法规/接收机门限；换机器/换 numpy 版本会
# 有 ±1 次抖动，故 tests/test_decode_sensitivity.py 只校验快照与实跑结果相差
# 不超过 2 dB，并把「以实跑为准、快照过期要刷新」写进文档正文。
# 每模式口径（噪声域/带宽/SNR）与档位网格不在这里重复，统一取自
# scripts/decode_sensitivity.py 的 MODE_BASIS / MODE_DEFAULT_GRID。
SENSITIVITY_SNAPSHOT_DATE = "2026-11-06"
SENSITIVITY_SEED = 20261104
SENSITIVITY_TRIALS = 20
SENSITIVITY_SNAPSHOT = {
    "ais": {
        "threshold_snr_db": 8.0, "threshold_100pct_snr_db": 10.0,
        "first_success_snr_db": 6.0, "critical_band_db": [6.0, 10.0],
        "first_failure_code": "nothing_detected", "monotonic": True,
    },
    "adsb": {
        "threshold_snr_db": 2.0, "threshold_100pct_snr_db": 2.0,
        "first_success_snr_db": -2.0, "critical_band_db": [-2.0, 2.0],
        "first_failure_code": "preamble_not_found", "monotonic": True,
    },
    "aprs": {
        "threshold_snr_db": 0.0, "threshold_100pct_snr_db": 2.0,
        "first_success_snr_db": -2.0, "critical_band_db": [-2.0, 2.0],
        "first_failure_code": "crc_failed", "monotonic": True,
    },
    "ft4": {
        "threshold_snr_db": -20.0, "threshold_100pct_snr_db": -20.0,
        "first_success_snr_db": -22.0, "critical_band_db": [-22.0, -20.0],
        "first_failure_code": "ldpc_parity_failed", "monotonic": True,
    },
    "pocsag": {
        "threshold_snr_db": 6.0, "threshold_100pct_snr_db": 8.0,
        "first_success_snr_db": 2.0, "critical_band_db": [2.0, 8.0],
        "first_failure_code": "address_not_recovered", "monotonic": True,
    },
    "acars": {
        "threshold_snr_db": 2.0, "threshold_100pct_snr_db": 2.0,
        "first_success_snr_db": -2.0, "critical_band_db": [-2.0, 2.0],
        "first_failure_code": "nothing_detected", "monotonic": True,
    },
    "navtex": {
        "threshold_snr_db": 36.0, "threshold_100pct_snr_db": 36.0,
        "first_success_snr_db": 32.0, "critical_band_db": [32.0, 36.0],
        "first_failure_code": "nothing_detected", "monotonic": True,
    },
    "apt": {
        "threshold_snr_db": 4.0, "threshold_100pct_snr_db": 8.0,
        "first_success_snr_db": 4.0, "critical_band_db": [4.0, 8.0],
        "first_failure_code": "no_sync", "monotonic": True,
    },
    "wspr": {
        "threshold_snr_db": -12.0, "threshold_100pct_snr_db": -12.0,
        "first_success_snr_db": -16.0, "critical_band_db": [-16.0, -12.0],
        "first_failure_code": "sync_not_found", "monotonic": True,
    },
    "lrpt": {
        "threshold_snr_db": 16.0, "threshold_100pct_snr_db": 16.0,
        "first_success_snr_db": 12.0, "critical_band_db": [12.0, 16.0],
        "first_failure_code": "sync_not_found", "monotonic": True,
    },
}

# 频偏容差快照（同一轮实测，2026-11-06；每档 10 次，固定各模式高 SNR 档）。
# rates 的键是 kHz，负/正各一档；缺失键 = 该偏移超过 0.45·fs（奈奎斯特不可
# 表示），文档里显式写 N/A，不伪造失败。临界频偏按「从 0 向外连续达标」定义，
# 不是「所有达标档的最大值」——差分鉴相的周期折叠会在远端档位假恢复。
SENSITIVITY_OFFSET_TRIALS = 10
SENSITIVITY_OFFSET_GRID_KHZ = (0.5, 1.0, 2.0, 5.0, 10.0)
OFFSET_SNAPSHOT = {
    "ais": {
        "critical_offset_khz": 1.0, "monotonic": False,
        "rates": {-10.0: 1.0, -5.0: 0.0, -2.0: 0.0, -1.0: 1.0, -0.5: 1.0,
                  0.0: 1.0, 0.5: 1.0, 1.0: 1.0, 2.0: 0.0, 5.0: 0.0, 10.0: 1.0},
    },
    "adsb": {
        "critical_offset_khz": 10.0, "monotonic": True,
        "rates": {-10.0: 1.0, -5.0: 1.0, -2.0: 1.0, -1.0: 1.0, -0.5: 1.0,
                  0.0: 1.0, 0.5: 1.0, 1.0: 1.0, 2.0: 1.0, 5.0: 1.0, 10.0: 1.0},
    },
    "aprs": {
        "critical_offset_khz": 0.0, "monotonic": True,
        "rates": {-10.0: 0.0, -5.0: 0.0, -2.0: 0.0, -1.0: 0.0, -0.5: 0.0,
                  0.0: 1.0, 0.5: 0.0, 1.0: 0.0, 2.0: 0.0, 5.0: 0.0, 10.0: 0.0},
    },
    "ft4": {
        "critical_offset_khz": 0.0, "monotonic": True,
        "rates": {-5.0: 0.0, -2.0: 0.0, -1.0: 0.0, -0.5: 0.0,
                  0.0: 1.0, 0.5: 0.0, 1.0: 0.0, 2.0: 0.0, 5.0: 0.0},
    },
    "pocsag": {
        "critical_offset_khz": 0.0, "monotonic": False,
        "rates": {-5.0: 1.0, -2.0: 0.0, -1.0: 0.0, -0.5: 0.0,
                  0.0: 1.0, 0.5: 0.0, 1.0: 0.0, 2.0: 0.0, 5.0: 0.0},
    },
    "acars": {
        "critical_offset_khz": 0.0, "monotonic": False,
        "rates": {-10.0: 0.0, -5.0: 0.0, -2.0: 0.0, -1.0: 0.0, -0.5: 0.0,
                  0.0: 1.0, 0.5: 0.0, 1.0: 0.0, 2.0: 0.0, 5.0: 1.0, 10.0: 0.0},
    },
    "navtex": {
        "critical_offset_khz": 0.0, "monotonic": True,
        "rates": {-5.0: 0.0, -2.0: 0.0, -1.0: 0.0, -0.5: 0.0,
                  0.0: 1.0, 0.5: 0.0, 1.0: 0.0, 2.0: 0.0, 5.0: 0.0},
    },
    "apt": {
        "critical_offset_khz": 1.0, "monotonic": False,
        "rates": {-10.0: 1.0, -5.0: 0.0, -2.0: 0.0, -1.0: 1.0, -0.5: 1.0,
                  0.0: 1.0, 0.5: 1.0, 1.0: 1.0, 2.0: 0.0, 5.0: 0.0, 10.0: 0.2},
    },
    "wspr": {
        "critical_offset_khz": 0.0, "monotonic": True,
        "rates": {0.0: 1.0},
    },
    "lrpt": {
        "critical_offset_khz": 10.0, "monotonic": True,
        "rates": {-10.0: 1.0, -5.0: 1.0, -2.0: 1.0, -1.0: 1.0, -0.5: 1.0,
                  0.0: 1.0, 0.5: 1.0, 1.0: 1.0, 2.0: 1.0, 5.0: 1.0, 10.0: 1.0},
    },
}

# 不走 decode_router 能力表的独立解码链（同一张矩阵里一并交代）
EXTRA_CHAINS = [
    {
        "mode": "lrpt（LRPT 图像链）",
        "truth": "real",
        "truth_label": "真实",
        "truth_source": "external-truth",
        "evidence": (
            "tests/test_lrpt_meteor_real_golden.py（TU Berlin 79MB Meteor-M2 真录，"
            "SHA-256 钉真值）；tests/test_lrpt_iq_chain.py；"
            "tests/test_lrpt_cross_impl_fixture.py"
        ),
        "rerun": (
            "python -m pytest tests/test_lrpt_meteor_real_golden.py "
            "tests/test_lrpt_iq_chain.py -q"
        ),
        "condition": "Meteor-M2 137.9MHz .cf32 ≥ 20s，重采样到整数倍符号率（如 144kS/s = 2sps）",
        "verdict": "真（真录）：79MB 过境真录帧同步锁定（ham=0 完美命中）",
    },
    {
        "mode": "dvbs2-sof（DVB-S2 SOF 同步层）",
        "truth": "partial",
        "truth_label": "部分",
        "truth_source": "synthetic-loopback",
        "evidence": (
            "tests/test_dvbs2_sof_sync.py（ETSI EN 302 307-1 §5.1.2 同步字/π 旋转，"
            "合成链 + 标准原文口径）"
        ),
        "rerun": "python -m pytest tests/test_dvbs2_sof_sync.py -q",
        "condition": "卫星下行符号率级复基带 .cf32 ≥ 3 个 PLFRAME，CFO ≪ 符号率/26",
        "verdict": "半个真：标准层逐位钉扎，缺 DVB-S2 OTA 真录",
    },
    {
        "mode": "dvbs2-vcdu（DATAFIELD→VCDU 重组）",
        "truth": "partial",
        "truth_label": "部分",
        "truth_source": "synthetic-loopback",
        "evidence": (
            "tests/test_dvbs2_datafield_vcdu.py（BBHEADER CRC-8/DFL/SYNCD + VCDU 主头，"
            "合成链自洽 + 编码侧互逆对拍）"
        ),
        "rerun": "python -m pytest tests/test_dvbs2_datafield_vcdu.py -q",
        "condition": "同 dvbs2-sof；且 DFL/SYNCD 整字节、帧长 892B",
        "verdict": "半个真：全链自洽，缺 DVB-S2 OTA 真录",
    },
]


def build_rows() -> list:
    """合并能力表（单一事实源）与本文件证据表，产出矩阵行。"""
    from mbdsdr_ai.decode_router import DIGITAL_MODE_CAPABILITIES

    rows = []
    for mode in sorted(DIGITAL_MODE_CAPABILITIES):
        cap = DIGITAL_MODE_CAPABILITIES[mode]
        if mode not in EVIDENCE:
            raise SystemExit(
                f"能力表新增模式 {mode} 但本报告无证据/复跑登记——先补 EVIDENCE")
        evidence, rerun, verdict = EVIDENCE[mode]
        rows.append({
            "mode": mode,
            "truth": cap.evidence,
            "truth_label": TRUTH_LABELS[cap.evidence],
            "truth_source": cap.truth_source,
            "evidence": evidence,
            "rerun": rerun,
            "condition": cap.min_sample_condition,
            "verdict": verdict,
        })
    rows.extend(EXTRA_CHAINS)
    return rows


def render() -> str:
    rows = build_rows()
    out = []
    out.append("# 解码器能力矩阵：这个解码器到底是不是真的")
    out.append("")
    out.append("> SPDX-License-Identifier: MIT")
    out.append(">")
    out.append("> **本文件由 `scripts/decoder_capability_report.py --write` 生成，请勿手改。**")
    out.append("> 改能力就改 `mbdsdr_ai/decode_router.py` 或本脚本的 EVIDENCE 表，再重新生成；")
    out.append("> `tests/test_decoder_capability_matrix.py` 会校验文档与生成结果逐字一致。")
    out.append("")
    out.append("## 一句话怎么读这张表")
    out.append("")
    out.append("- **真实度 = 真实**：有外部真值或真录对拍（第三方样本按需下载、SHA-256 钉值，"
               "离线诚实 skip）；「结论」列会写出命中多少条。")
    out.append("- **真实度 = 部分**：只有自洽合成链（自家编码→自家解码）或标准原文逐位钉扎，"
               "**没有 OTA 实绩**——能证明「代码符合协议」，不能证明「能在天上收到」。")
    out.append("- **真实度 = 缺样本**：连一段真录都没有，只有协议数学性质；"
               "「最小采样条件」列写明补到什么样一段信号才能破零。")
    out.append("")
    out.append("**「来历」列是硬约束**：只有 `external-truth`（外部真值/标准原文/第三方"
               "实现输出）才允许出现在「真实」栏；`self-generated`（自家编码器产出）与"
               "`synthetic-loopback`（自家编码→自家解码回环）一律不得算作验证。"
               "由 `tests/test_golden_provenance.py` 守卫。")
    out.append("")
    out.append("## 能力矩阵")
    out.append("")
    out.append("| 模式 | 真实度 | 来历 | 证据位置 | 复跑方式 | 最小采样条件 | 结论 |")
    out.append("|---|---|---|---|---|---|---|")
    for r in rows:
        out.append(
            "| `{mode}` | **{truth}** | {src} | {evidence} | `{rerun}` | {cond} | {verdict} |".format(
                mode=r["mode"], truth=r["truth_label"],
                src=TRUTH_SOURCE_LABELS[r["truth_source"]],
                evidence=r["evidence"],
                rerun=r["rerun"], cond=r["condition"], verdict=r["verdict"],
            )
        )
    out.append("")
    out.append("## 资产来历（golden / fixture）")
    out.append("")
    out.append("每个 `provenance.json` 逐文件声明来历、来源名称、SHA-256 与日期；"
               "缺声明或把 `self-generated` 标成外部真值都会被守卫测试拦下。"
               "**自生成的黄金值只能证明「代码与自己的编码器一致」，不能算验证。**")
    out.append("")
    out.append("| 目录 | 整目录类型 | 文件数 | 三类分布 |")
    out.append("|---|---|---|---|")
    for rel, data in load_provenance():
        files = data.get("files", {})
        counts = {}
        for item in files.values():
            counts[item.get("provenance", "?")] = counts.get(
                item.get("provenance", "?"), 0) + 1
        dist = "、".join(
            f"{TRUTH_SOURCE_LABELS.get(k, k)} {v}" for k, v in sorted(counts.items())
        ) or "—"
        out.append(
            f"| `{rel}` | {TRUTH_SOURCE_LABELS.get(data.get('provenance'), data.get('provenance'))} "
            f"| {len(files)} | {dist} |")
    out.append("")
    out.append("没有入仓 golden、但属于「自家编码→自家解码回环」的链：")
    out.append("")
    out.append("| 链 | 来历 | 证据 | 说明 |")
    out.append("|---|---|---|---|")
    for item in LOOPBACK_CHAINS:
        out.append(
            f"| {item['name']} | {TRUTH_SOURCE_LABELS[item['truth_source']]} "
            f"| `{item['evidence']}` | {item['note']} |")
    out.append("")
    out.append("## 复跑整张表")
    out.append("")
    out.append("```bash")
    out.append("# 只跑全部最小命令（离线、不开声卡、不碰硬件）")
    out.append("python scripts/decoder_capability_report.py --verify")
    out.append("# 或逐条复制上表「复跑方式」列")
    out.append("```")
    out.append("")
    out.append("环境（仓内约定，保证不露窗不发声）：")
    out.append("")
    out.append("```bash")
    out.append("export MBDSDR_NULL_AUDIO=1 MBDSDR_NO_HW=1 QT_QPA_PLATFORM=offscreen")
    out.append("```")
    out.append("")
    out.append("## 灵敏度基线（合成 AWGN，仅供相对比较）")
    out.append("")
    out.append("> 全部为**合成 fixture + AWGN，非 OTA**。不同模式的噪声域/带宽口径"
               "不同，**dB 值不可横向比较**；这张表只钉方向性、90% 档、100% 档、"
               "临界区间与首个失败码，不钉具体 dB 数值。")
    out.append("")
    out.append("| 模式 | 口径（噪声域｜带宽｜SNR） | 档位网格 | 90% 档 | 100% 档 "
               "| 临界区间 | 首个失败码 | 是否单调 |")
    out.append("|---|---|---|---|---|---|---|---|")
    for mode in SENSITIVITY_SNAPSHOT:
        snap = SENSITIVITY_SNAPSHOT[mode]
        basis = SENSITIVITY.MODE_BASIS[mode]
        grid = SENSITIVITY.MODE_DEFAULT_GRID[mode]
        band = snap["critical_band_db"]
        mono = ("是" if snap["monotonic"]
                else "否（边缘效应，未解释机理）")
        out.append(
            f"| `{mode}` | {basis['domain']}｜{basis['bandwidth']}｜{basis['snr']} | "
            f"{grid[0]:g}..{grid[-1]:g} dB（{len(grid)} 档 / "
            f"{SENSITIVITY_TRIALS} 次） | "
            f"{snap['threshold_snr_db']:g} dB | "
            f"{snap['threshold_100pct_snr_db']:g} dB | "
            f"{band[0]:g}..{band[1]:g} dB | "
            f"`{snap['first_failure_code']}` | {mono} |"
        )
    out.append("")
    out.append("临界区间 = 「首个成功率 > 0 的档」到「首次 100% 的档」；相邻档成功数"
               "允许回落 ≤1 次（20 次/档下的 ±1 次抖动），回落更多即判非单调。"
               "每个模式的噪声域/带宽/SNR 口径由同一份 `MODE_BASIS` 生成，"
               "改口径必须改 `scripts/decode_sensitivity.py` 并刷新本文件。")
    out.append("")
    out.append("### 频偏容差（与 SNR 正交的另一维）")
    out.append("")
    out.append(f"固定各模式自己的高 SNR 档，每偏移 {SENSITIVITY_OFFSET_TRIALS} 次；"
               "负/正两翼都扫。`N/A` = 该偏移超过 `0.45·fs`（奈奎斯特不可表示），"
               "**不伪造失败**（WSPR fs=375 S/s 只可能扫 0；FT4/NavTex fs=12 kS/s "
               "扫不到 ±10 kHz；POCSAG fs=22.05 kS/s 同理）。")
    out.append("")
    out.append("| 模式 | 0 kHz | ±0.5 kHz | ±1 kHz | ±2 kHz | ±5 kHz | ±10 kHz "
               "| 临界频偏 | 是否单调 |")
    out.append("|---|---|---|---|---|---|---|---|---|")

    def _rate_cell(rates, k):
        if k is None:
            v = rates.get(0.0)
            return "—" if v is None else f"{v * 100:.0f}%"
        neg, pos = rates.get(-float(k)), rates.get(float(k))
        if neg is None or pos is None:
            return "N/A"
        if neg == pos:
            return f"{neg * 100:.0f}%"
        return f"{neg * 100:.0f}%/{pos * 100:.0f}%"

    for mode in OFFSET_SNAPSHOT:
        snap = OFFSET_SNAPSHOT[mode]
        cells = [_rate_cell(snap["rates"], None)]
        cells += [_rate_cell(snap["rates"], k) for k in SENSITIVITY_OFFSET_GRID_KHZ]
        crit = snap["critical_offset_khz"]
        crit_txt = ("—" if crit is None
                    else (f"{crit:g} kHz（扫描上界）"
                          if crit == SENSITIVITY_OFFSET_GRID_KHZ[-1]
                          else f"{crit:g} kHz"))
        mono = ("是" if snap["monotonic"] else "否（边缘效应，未解释机理）")
        out.append(f"| `{mode}` | " + " | ".join(cells)
                   + f" | {crit_txt} | {mono} |")
    out.append("")
    out.append("临界频偏 = **从 0 向外连续达标**的最大 |偏移|（两翼取较差侧），"
               "不是「所有达标档里的最大值」。AIS 用 1 符号差分相位判决"
               "（`angle(x[n+sps]·conj(x[n]))`），恒定频偏只给判决量加一个固定"
               "相位旋转；偏移接近符号率整数倍时旋转回到 2π 附近、极性又被判对，"
               "于是 ±10 kHz 反而 100%——**那是周期折叠，不是容差**。POCSAG / "
               "ACARS / APT 在个别档位也看到同型恢复（-5 kHz 或 +5 kHz 突然回到 "
               "100%），一律判非单调、标「边缘效应，未解释机理」，不挑好看档位当"
               "结论。")
    out.append("")
    out.append("**ADS-B 的两种观测并存**：本表用库内合成 8 MS/s / 3 帧 fixture"
               "（`decode_pipeline/adsb_three_frames.cf32`），固定该模式高 SNR 档"
               "（2 dB）、每偏移 10 次，±10 kHz 全过（10/10）。另一组用**同一** "
               "fixture、SNR 0 dB、6 次/偏置，则出现 0 kHz 5/6、±2 kHz 5/6、"
               "±5 kHz 1/6、±10 kHz 5/6、±20 kHz 4/6 的**非单调**（包络检波 + "
               "抽取滤波/位定时边缘效应）。两组只差 SNR 与样本量：临界附近 6 次"
               "样本本就抖，**不拿任一组的「恢复」当卖点**，只钉「高 SNR 下容差"
               "≥10 kHz、临界附近不稳」。")
    out.append("")
    out.append("**POCSAG 的 fixture 敏感性**：上表固定 1200 bd / 地址 54321 / HELLO。"
               "探索阶段在同一解码器上换 512/2400 bd 或其他地址时，出现过高速档"
               "回落（如 25 dB 反而掉到 17/20）与低于 100% 的平台——未解释机理，"
               "故本表只报这一组固定 fixture 的档位，不外推到其他波特率/地址。")
    out.append("")
    out.append("### 未做灵敏度基线的模式（缺什么写清楚，不写「已完善」）")
    out.append("")
    for mode, item in SENSITIVITY.NOT_DONE.items():
        out.append(f"- `{mode}`：" + "；".join(item["missing"]))
    out.append("")
    out.append(f"快照日期 {SENSITIVITY_SNAPSHOT_DATE}，种子 {SENSITIVITY_SEED}，"
               f"每档 {SENSITIVITY_TRIALS} 次、每偏移 {SENSITIVITY_OFFSET_TRIALS} 次。"
               "**以实跑为准**，过期就重新生成：")
    out.append("")
    out.append("```bash")
    out.append("python scripts/decode_sensitivity.py --trials 20 --seed 20261104 \\")
    out.append("    --offset-trials 10 \\")
    out.append("    --out paper/experiments/decode_sensitivity.json \\")
    out.append("    --md paper/experiments/decode_sensitivity.md")
    out.append("```")
    out.append("")
    out.append("三条必须一起读的限定：合成波形无多径/无采样时钟误差"
               "（频偏是另行单列的正交维度，见上表）；该 SNR 按各模式自己的域与"
               "fs/2 噪声带宽定义，不是天线口面 SNR，也不是任何法规门限；"
               "它只证明「本实现相对本实现的耐噪性」，不能证明「能在天上收到」。"
               "**全部结论的输入都是仓内编码器合成的 fixture（synthetic, NOT OTA）**，"
               "不是 OTA 实测。")
    out.append("")
    out.append("## 离线批处理与结构化审计")
    out.append("")
    out.append("要把一段/一目录捕获按这张表批量跑一遍，用批处理入口（同样离线、不碰硬件）：")
    out.append("")
    out.append("```bash")
    out.append("python scripts/decode_captures.py --mode ais --file cap.cf32 --fs 192000 --out report.json")
    out.append("python scripts/decode_captures.py --mode all --dir captures/ --out report.json")
    out.append("```")
    out.append("")
    out.append("- 报告逐文件给出 `ok` / `status` / `error_code` / `num_messages` 与 SHA-256；"
               "**零报文是合法结果**，不折叠成成功或失败。")
    out.append("- 退出码只表示「报告是否生成成功」，不表示「解出东西没有」。")
    out.append("- 另设 `MBDSDR_DECODE_AUDIT_DIR=logs/` 后，每次解码追加一行 "
               "`decode_audit.jsonl`（只记元数据与输入指纹，**不抄报文正文**）。")
    out.append("")
    out.append("## 死分支与可达性登记")
    out.append("")
    out.append("- **ACARS handler 内的 `unsupported_format` 是死分支**：路由层中央格式门"
               "（`decode_router` 的 `input_formats` 检查）先拦，handler 里的 else 分支"
               "在当前能力表下不可达；保留只为防御，不计入实际失败路径。")
    out.append("- **`not_implemented` 仅 ft8 通用入口可达**：能力表 `dependency=\"\"`，"
               "handler 直接返回该码；其余模式均已接线。")
    out.append("- **`missing_dependency` 现在有真实可达路径**：路由先 `find_spec`，再"
               "`importlib.import_module`；环境缺 numpy/scipy 等第三方库时模块文件存在但"
               "导入抛 `ImportError`，走该码（不再只能靠 monkeypatch 复现）。")
    out.append("- **路由层运行期校验**：IQ 模式先校验采样率整数倍"
               "（ADS-B 1MHz / AIS 9600 / Meteor 72k），再校验「最短一帧」样本量；"
               "不足返回 `sample_rate_mismatch` / `insufficient_samples`，"
               "绝不 `ok=true` + 0 条。")
    out.append("")
    out.append("## 诚实边界")
    out.append("")
    out.append("- 「真实」不等于「现在能在你的设备上收到」：外部真值证明解码链正确，"
               "能否收到还取决于天线、增益、过境时间与多普勒补偿。")
    out.append("- 标 `not_implemented` 的模式（仅 ft8 通用入口）不会假装解码，"
               "调用会得到明确错误码而不是空结果。")
    out.append("- 缺真录的模式（ais/navtex/aprs）在补到真录之前，"
               "任何「空口已解码」的宣称都不成立。")
    out.append("- **APRS 已接线但不等于「空口支持」**：`run_aprs_pipeline` 真实"
               "执行 NFM 鉴频→Bell202 AFSK→NRZI→HDLC→CRC-16→AX.25，但现有证据"
               "仅为仓内合成 fixture（synthetic, NOT OTA）逐字节对拍；"
               "不得写成「OTA 验证通过」。失败时给 `nothing_detected` / "
               "`truncated_frame` / `crc_failed` 等具体原因码。")
    out.append("- **本矩阵与 UI 都不声称「实时解码」**：解码是离线/同步处理，"
               "实时率与跟不上时的后果见 `mbdsdr_ai.decode_pipeline."
               "performance_summary` / `behind_report`；后者明说积压恒为 0 且"
               "观测不到 SDR 缓冲层溢出。")
    out.append("- **两种「丢」的用词**：`dropped_frames` 只计 FCS/CRC 校验失败，"
               "不包含「跟不上导致样本没被处理」——后者不归本模块计数。")
    out.append("")
    return "\n".join(out) + "\n"


def cmd_write() -> int:
    text = render()
    os.makedirs(os.path.dirname(DOC_PATH), exist_ok=True)
    with open(DOC_PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print(f"已写入 {DOC_PATH}（{len(text.splitlines())} 行）")
    return 0


def verify_commands() -> list:
    """整张表的复跑命令：EVIDENCE 十行 + EXTRA_CHAINS 三条。

    ft4/wspr 共用同一条命令，按命令去重（一条命令覆盖两行），避免白跑。
    返回 [(模式, 命令)]，顺序与能力表一致。
    """
    pairs = [(mode, ev[1]) for mode, ev in EVIDENCE.items()]
    pairs += [(chain["mode"], chain["rerun"]) for chain in EXTRA_CHAINS]
    seen, out = set(), []
    for mode, cmd in pairs:
        if cmd in seen:
            continue
        seen.add(cmd)
        out.append((mode, cmd))
    return out


def cmd_verify() -> int:
    env = dict(os.environ)
    env.update({
        "MBDSDR_NULL_AUDIO": "1",
        "MBDSDR_NO_HW": "1",
        "QT_QPA_PLATFORM": "offscreen",
    })
    failed = []
    for mode, rerun in verify_commands():
        print(f"[{mode}] {rerun}")
        rc = subprocess.run(rerun, cwd=ROOT, shell=True, env=env).returncode
        if rc != 0:
            failed.append(mode)
    if failed:
        print(f"失败: {failed}")
        return 1
    print("全部最小命令通过")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true", help="刷新 docs 文档")
    ap.add_argument("--verify", action="store_true", help="逐条跑最小命令")
    args = ap.parse_args()
    if args.write:
        return cmd_write()
    if args.verify:
        return cmd_verify()
    print(render())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
