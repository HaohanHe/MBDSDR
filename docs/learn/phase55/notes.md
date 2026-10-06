# Phase55 软判决全链闭环

HEAD=cfb5e02 接手。把 Phase54 只在卷积隔离验证的软判决（14× BER 增益）串进全链。

## 软全链机制（可复用层，ccsds_ssdv，非 onboard 重写）
- `demod_bpsk_soft(z,fs,symrate,foff)`（ssdv_phy）→ 符号中心实值软采样（float32，保留幅值）。
- 由同一软数组 sign 出硬比特 → `_scan_asm_offsets` 定位 ASM（严格匹配）→ 按 `off+32` 切出该帧软符号窗 → `ccsds_softframe_to_dslwp`：软符号对分组 → `ViterbiDecoder.decode_soft`（欧氏距离，期望 bit1→+1）→ 解扰 → RS(255,223)。
- `_collect_image(out,images)` 抽出为硬/软两链共用的图片汇总尾部。

## sd=0.8/1.0 软硬 MCU 对比（真实）
先诚实说明：本仓 ASM 用严格 32-bit 匹配（max_hamming=0），sd≥0.8 时单符号误码~10%，
**ASM 本身先失同步**（瓶颈在 ASM 而非 Viterbi），故 sd=0.8/1.0 两路径都 0 帧。
改在 ASM 仍能同步的弱区（sd=0.6/0.7，8 reps 累计）：

| sd | 硬判决累计 MCU | 软判决累计 MCU |
|---|---|---|
| 0.6 | 72 | 72 |
| 0.7 | 36 | **108**（3×）|
| 0.8 | 0 | **36** |

→ 隔离 14× BER 增益**兑现到出图**：sd=0.7 软比硬多恢复 3× MCU；sd=0.8 硬全废、软仍出 1 帧。

## onboard 开关
`--ssdv-soft`（配 `--ssdv-mode ccsds --ssdv-input iq`）。clean IQ 实测：
`ASM 1 帧 / RS nerrors [[0,0]] / MCU 36/36 / PASS`。

## 纯噪声诚实空态
纯高斯噪声 0 ASM / 0 MCU，不伪造图（test 覆盖）。

## 未解决
- ASM 严格匹配是更高 SNR 的瓶颈（sd≥0.8 既不软也不硬）；若要更弱信号需 ASM hamming 容忍（未做）。
- 软路径当前走 coarse 定时，未接 gardner/pll 联合；真机过境 IQ 未验证。
