# Phase54 接收链能力包络 + 软判决 Viterbi + 工具化

HEAD=704e035 接手。三任务全部在 Python 链（干净室 MIT，无 GPL 复制）。

## 块2：软判决 Viterbi LLR
- `ccsds_rx.py ViterbiDecoder.decode_soft(pairs, final_state)`：实值软符号对，欧氏距离分支度量（期望 bit1→+1/bit0→-1，与 bpsk_modulate_bits 一致）。
- `ssdv_phy.py demod_bpsk_soft(iq,fs,symrate,f_offset)`：粗定时下不做 sign，返回符号中心匹配滤波实值软采样。
- **真实增益**（info 4000bit，seed 54，3 reps 均值）：

| sd | 硬判决 BER | 软判决 BER | 增益 |
|---|---|---|---|
| 0.3/0.5 | 0 | 0 | 干净 |
| 0.8 | 0.1262 | 0.0089 | 14× |
| 1.0 | 0.3471 | 0.1546 | 2.2× |
| 1.5/2.0 | ~0.49 | ~0.49 | waterfall 外，无改善（诚实）|

## 块1：能力包络网格（seed 5400，CSV=docs/learn/phase54/envelope.csv）
| 维度 | 可恢复 | 失效面 |
|---|---|---|
| SNR sd（+100Hz CFO, AFC+PLL） | sd≤0.8 全 36/36 | sd=1.0 asm=1 但 mcu=0（边界）；sd≥1.3 asm=0 |
| 扫频斜率（sd=0.5, AFC+PLL） | 0→1000Hz/帧 全 36/36 | （本档内 PLL 稳） |
| CW @+600Hz（sd=0.5, notch） | amp≤6 全 36/36 | amp≥12 asm=0 |
| 遮挡（resync） | gap 1/2/4 帧全恢复 36/36 | （本档内可恢复） |

失效面理论：低 SNR 时鉴相/鉴频噪声压过信号→ASM 不同步；CW 强于信号主瓣时 notchn 与信号主瓣混淆。

## 块3：Python 工具注册
- `tool_registry.py register_ssdv_rx_tools()` 注册 `ssdv_ccsds_decode`（参数 iq_path/sample_rate_hz/symrate_hz/frame_bits/afc/pll/notch_cw/resync/timing）。
- 注册前=0 → 注册后=1（新增 1）。无设备/空 IQ 诚实 success=False，不 mock 出图。
- 纯 Python 链，**未虚构任何 C++ ControlHub/HTTP 桥接**。

## 未解决
- 软判决仅在卷积链隔离验证 BER 增益；demod_bpsk_soft→decode_soft→descramble→RS 全链未串。
- gap0 对照（无遮挡单帧）resync 单窗化实验小瑕疵（不影响主结论）。
- 真机过境 IQ 未验证。
