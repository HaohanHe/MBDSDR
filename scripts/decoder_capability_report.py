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
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC_PATH = os.path.join(ROOT, "docs", "learn", "decoder-capability-matrix.md")

sys.path.insert(0, ROOT)

TRUTH_LABELS = {
    "real": "真实",
    "partial": "部分",
    "no_sample": "缺样本",
}

# mode -> (证据位置, 复跑命令, 一句话结论)
# 只写仓内能直接复跑的东西；外部第三方样本按需下载，离线诚实 skip。
EVIDENCE = {
    "adsb": (
        "tests/test_adsb_dump1090_golden.py（dump1090 官方 modes1.bin 逐帧对拍）"
        "；tests/test_adsb_consistency.py（合成链）；"
        "本机 1090MHz 真录 2026-10-29 三次 0 CRC 帧 = 负证据（如实保留）",
        "python -m pytest tests/test_adsb_dump1090_golden.py tests/test_adsb_consistency.py -q",
        "真（外部真值）：dump1090 官方样本两条帧逐字命中；但本机 OTA 真录尚未破零",
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
        "半个真：通用入口只到解调骨架；图像链真录证据在 LRPT 真录链",
    ),
}

# 合成 AWGN 灵敏度基线快照（scripts/decode_sensitivity.py 的实测结果）。
# 只用于「同实现内相对比较」，不是法规/接收机门限；换机器/换 numpy 版本会
# 有 ±1 档抖动，故 tests/test_decode_sensitivity.py 只校验快照与实跑结果相差
# 不超过 2 dB，并把「以实跑为准、快照过期要刷新」写进文档正文。
SENSITIVITY_SNAPSHOT_DATE = "2026-11-04"
SENSITIVITY_SEED = 20261104
SENSITIVITY_TRIALS = 20
SENSITIVITY_SNAPSHOT = {
    "ais": {"threshold_snr_db": 8.0, "threshold_100pct_snr_db": 10.0},
    "adsb": {"threshold_snr_db": 6.0, "threshold_100pct_snr_db": 8.0},
}

# 不走 decode_router 能力表的独立解码链（同一张矩阵里一并交代）
EXTRA_CHAINS = [
    {
        "mode": "lrpt（LRPT 图像链）",
        "truth": "real",
        "truth_label": "真实",
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
    out.append("## 能力矩阵")
    out.append("")
    out.append("| 模式 | 真实度 | 证据位置 | 复跑方式 | 最小采样条件 | 结论 |")
    out.append("|---|---|---|---|---|---|")
    for r in rows:
        out.append(
            "| `{mode}` | **{truth}** | {evidence} | `{rerun}` | {cond} | {verdict} |".format(
                mode=r["mode"], truth=r["truth_label"], evidence=r["evidence"],
                rerun=r["rerun"], cond=r["condition"], verdict=r["verdict"],
            )
        )
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
    out.append("上表回答「是不是真的」；这张小表回答「同一实现里谁更耐噪」。"
               "口径是**复基带突发内平均每样本 SNR**，信号由仓内编码器合成、"
               "注入白噪声后交给本仓解码器，成功 = CRC/FCS 通过且字段与合成真值一致。")
    out.append("")
    out.append("| 模式 | 90% 成功率所需 SNR | 首次 100% 的 SNR | 采样条件 |")
    out.append("|---|---|---|---|")
    out.append(
        f"| `ais` | {SENSITIVITY_SNAPSHOT['ais']['threshold_snr_db']:g} dB | "
        f"{SENSITIVITY_SNAPSHOT['ais']['threshold_100pct_snr_db']:g} dB | "
        "GMSK 9600 bit/s，fs=48000（9600×5），单帧 Type 1 |")
    out.append(
        f"| `adsb` | {SENSITIVITY_SNAPSHOT['adsb']['threshold_snr_db']:g} dB | "
        f"{SENSITIVITY_SNAPSHOT['adsb']['threshold_100pct_snr_db']:g} dB | "
        "1090ES PPM 1 Mbit/s，fs=4e6（4 样本/bit），DF17 识别帧 |")
    out.append("")
    out.append(f"快照日期 {SENSITIVITY_SNAPSHOT_DATE}，种子 {SENSITIVITY_SEED}，"
               f"每档 {SENSITIVITY_TRIALS} 次。**以实跑为准**，过期就重新生成：")
    out.append("")
    out.append("```bash")
    out.append("python scripts/decode_sensitivity.py --modes ais,adsb \\")
    out.append("    --snr -10:16:2 --trials 20 --seed 20261104 --md sensitivity.md")
    out.append("```")
    out.append("")
    out.append("三条必须一起读的限定：合成波形无多径/无频偏/无采样时钟误差；"
               "该 SNR 不是天线口面 SNR，也不是任何法规门限；"
               "它只证明「本实现相对本实现的耐噪性」，不能证明「能在天上收到」。")
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
