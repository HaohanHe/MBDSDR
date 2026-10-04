# Phase35 L1 — SDR++ Radio Frontend Deep Read

> Source: `repos/sdrpp/decoder_modules/radio/` + `repos/sdrpp/core/src/dsp/`
> 我方对照: `cpp/src/dsp/` (demod / agc / squelch / wfm_stereo / fsk_demod / digital_demod / anr)
> 2026-10-05. Clean-room: 只学机制不抄代码.

---

## 1. 解调链组织

```
VFO (complex IF)
  → IF chain:  NoiseBlanker → PowerSquelch → FM-IF NR     (pre-demod, complex)
  → Demodulator (per-mode: NFM / WFM / AM / SSB / CW)
  → AF chain:  CTCSS → RationalResampler → HPF → Deemph   (post-demod, stereo)
  → Audio sink (48 kHz)
```

证据: `radio_module.h:85-110` (chain 初始化), `:419-562` (selectDemod 接线).
每解调器自报 IF SR / 默认带宽 / 允许项 (`demod.h:34-61`).
NFM: IF=50k, bw=12.5k (`nfm.h:56-58`); WFM: IF=250k, bw=150k (`wfm.h:268-270`).

**关键**: PowerSquelch 在 **IF 域 (解调前)**, 关时整块 IQ 置零 (`power_squelch.h:43-46`),
省算力且避免开合爆音. CTCSS 在 AF 域 (需解码音频亚音).

---

## 2. NBFM 解调

### 2.1 正交鉴频器
`quadrature.h:39-46`: `out = normalizePhase(φ_i - φ_{i-1}) · 1/deviation`.
相邻复数样本相位差 × 归一化增益. 无载波环.

我方对照: `demod.cpp:155-160` (NFM) / `:198-201` (WFM) 等价:
`y = iq·conj(prev); angle = atan2; disc = gain·angle`. 机制一致.

### 2.2 鉴频后低通
`fm.h:110-130`: cutoff=bw/2, trans=bw·0.1, Nuttall-windowed sinc. 可选开关.

**我方 gap**: `demod.cpp:93-95,166-170` — NuttallLpf 单测通过, 但 `firEnabled_=false`
live chain 关闭 (block-streaming 跟 channelizer 交互有问题, multi_vfo 281→2.64).
邻道抑制目前靠 VFO 带宽, 没有鉴频后 LPF 兜底. **真实 gap, 待修**.

### 2.3 去加重
`deephasis.h:58-94`: 单极点 IIR `out[i] = α·in[i] + (1-α)·out[i-1]`, `α = dt/(τ+dt)`.
模板化 float / stereo_t. SDR++ 在 AF 链 48 kHz 重采样后做 (`radio_module.h:105,110`).

我方在 IF SR 下做 (`demod.cpp:133-134, 183-184`). 数值差异不大, 但 SDR++ 的位置
更"正确" (去加重本是音频域操作). 未来重构可挪到重采样后.

---

## 3. WFM 立体声解调

`broadcast_fm.h:144-191` 核心链:

1. 鉴频 → 实数 MPX (`:146`)
2. Real→Complex (`:149`)
3. **导频带通 FIR**: 18.75–19.25 kHz, BW 500 Hz (`:43`)
4. **PLL 锁 19k 导频** (`:46,153`): 二阶 critically-damped, BW≈10 Hz,
   clamp 18.75–19.25 kHz. 输出复数 phasor.
5. **延迟补偿** (`:47-48,156-157`): MPX 和导频路径各延迟 (FIR_taps/2)+1 样本,
   补偿导频 FIR 群延迟.
6. **两次混频** (`:160-162`): conj(PLL) → MPX·e^{-jφ} → 结果·e^{-jφ} = MPX·e^{-j2φ},
   直接把 38 kHz DSB 边带搬到 baseband.
7. 取实部 ×2.0 恢复增益 (`:174-177`)
8. L = M + S, R = M − S (`:180-181`)
9. L/R 各过 15 kHz LPF FIR (`:49,185-186`)
10. RDS 路径: 频偏 -57 kHz (`:52,167`) + 重采样到 5 kHz (`:53,170`)

### 我方立体声对照 (`wfm_stereo.cpp:112-216`)

| 维度 | SDR++ | 我方 |
|------|-------|------|
| 导频提取 | 带通 FIR → 相位 PLL | NCO 直接混频 → I/Q 低通 → atan2 鉴相 |
| 环路 | 二阶 PCL (critically damped) | 二阶 P+I (kp, ki) |
| 延迟补偿 | 显式 delay line | 无 (靠积分器消静态相位) |
| 立体声切换 | **无**, 开了就一直解 | quality/blend 平滑过渡 (`:186-215`) |
| M/S 对齐 | L/R 各自 FIR | 同一组 taps 逐样本 push |

**可借鉴**: SDR++ 导频带通 FIR 抗噪更好 (导频滤干净再给 PLL). 我方在弱信号下
PLL 输入含更多带外噪声, 但积分器能拉回来. **我们的 blend 切换是优势**.

---

## 4. AGC 平滑 (Attack/Release)

`agc.h:70-110`:
```
每样本: inAmp = |x|
  amp = (inAmp > amp) ? amp·(1-a) + inAmp·a : amp·(1-d) + inAmp·d
  gain = min(setPoint/amp, maxGain)
  if (inAmp·gain > maxOutputAmp):          // 削波前瞻
      扫描 block 剩余样本找 maxAmp;
      amp = maxAmp; gain = min(setPoint/amp, maxGain);
```

- attack/decay 是**每样本归一化系数** (非时间常数), 调用方换算.
- **削波前瞻** (`:91-104`): 检测到当前增益会削波时, 向后扫整个 block 找峰值,
  直接跳 amp. 避免突发强信号前几个样本被削.

我方对照 (`agc.cpp:38-56`): block-based, 无削波前瞻. 有 maxGain ceiling.
ComplexCarrierAgc (`:81-88`) 是每样本的, 机制等价.

**可借鉴**: 削波前瞻 O(n) 扫描, 成本低收益高, 突发信号瞬态更干净. **值得加**.

---

## 5. 静噪 (Squelch)

### SDR++ PowerSquelch (`power_squelch.h:33-50`)
- 整块平均幅度 → dB → 过门限. **无迟滞, 无 hangover**.
- 文件头: `// TODO: Rewrite better!!!!!` (`:4`) — 自己都知道粗糙.

### 我方 (`squelch.cpp:25-44`)
- 平滑 RMS (快 attack 5ms, 慢 decay 50ms)
- **Hangover 200ms** (`squelch.h:42`): 开了之后保持一段时间才关.

**结论: 我方静噪更好**. SDR++ 是占位实现. 不用抄. CTCSS 静噪我方没有, **YAGNI**.

---

## 6. 反例核查 ("以为有但实际没有/不同")

1. **"radio 链上有 AGC"** — 没有. IF/AF chain 都没 AGC block (`radio_module.h:94-110`).
   AGC 类存在但 radio 模块没用, 靠硬件增益 + 静噪. 我方加 audio AGC 是合理的.

2. **"我们 NFM 有鉴频后低通"** — live chain 是关的 (`firEnabled_=false`).
   单测开着, 联调问题. **待修 bug, 不是特性**.

3. **"SDR++ 立体声有自动单/立体声切换"** — 没有. 导频丢了就出噪声.
   我方 blend 是优势, 别被上游带偏.

4. **"SDR++ 静噪有迟滞"** — 没有. TODO 注释自曝. 我们的 hangover 更好.

5. **"去加重在鉴频后做"** — SDR++ 在 AF 链 48 kHz 重采样后. 位置更正确.

6. **"NFM 有立体声"** — 没有. 输出 mono→stereo 只是复制左右 (`fm.h:87-94`).
   立体声是 WFM 广播独有.

---

## 7. 可借鉴点 (优先级)

| 优先级 | 机制 | 来源 | 我方现状 | 建议 |
|--------|------|------|----------|------|
| P0 | 鉴频后 FIR 低通 | fm.h:110 | 代码有, live 关闭 | **修 block 流式, 开起来** |
| P1 | AGC 削波前瞻 | agc.h:91-104 | 无 | 加 block 峰值扫描 |
| P2 | 静噪搬 IF 域 | power_squelch.h | 解调后音频域 | 可搬, 省算力防爆音 |
| P3 | 导频带通 BPF | broadcast_fm.h:43 | 直接混频 | 弱信号场景可加 |
| P4 | 去加重移重采样后 | radio_module.h:105 | IF SR 下做 | 重构时挪位置 |
| P5 | CTCSS 静噪 | radio_module.h:861 | 无 | YAGNI, 对讲场景再加 |

---

## 8. 差距判定 (补齐 vs YAGNI)

**需要补齐**:
- 鉴频后 Nuttall LPF live 关闭 — 不是"选择不做", 是"做了没跑通". 邻道串扰直接影响质量.
- AGC 无削波前瞻 — 突发强信号前几个样本削波. O(n) 扫描成本低.

**YAGNI (现在不需要)**:
- CTCSS 亚音静噪: 需窄带音频解码, 无对讲用户.
- FM-IF NR (谱减 IF 降噪): 我们已有 ANR 音频域谱减, IF 域对语音收益有限.
- NoiseBlanker 接 IF 链: 有代码没接线, 用户没报脉冲噪声问题.
- RDS 解码: wfm_stereo 有 raw MPX tap, 但解码器后续再说.
- SNR-based squelch: SDR++ 自己都 TODO 没实现.

**我们比 SDR++ 好的**:
- 静噪平滑 + hangover (上游是粗糙块开关)
- 立体声/单声道 blend 自动切换 (上游没有)
- M/S 对称 FIR 对齐 (上游靠显式 delay line)

---

*笔记结束. 下一个 Phase: digital_demod / fsk_demod 对照上游数字解调链.*
