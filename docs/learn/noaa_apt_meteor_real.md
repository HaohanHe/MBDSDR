# NOAA APT + METEOR LRPT：真读源码后的移植笔记

> 本笔记对应提交：`feat(sat): noaa-apt真实APT同步+meteor_demod QPSK/Viterbi/LRPT，往返验证`
> 原则：**只读 README 不算数，必须读 .rs/.cpp/.c 源码**。所有常量在代码里以
> `来源: <repo> src/<file>:<行号>` 标注。

---

## 一、noaa-apt（Rust）真读记录

源码位置：`repos/noaa-apt/src/`。关键文件与结论：

### 1.1 常量（decode.rs）
| 常量 | 值 | 来源 |
|---|---|---|
| 最终视频采样率 `FINAL_RATE` | **4160 Hz**（一像素一样本） | `decode.rs:14` |
| AM 副载波 `CARRIER_FREQ` | **2400 Hz** | `decode.rs:38` |
| 每通道像素 `PX_PER_CHANNEL` | **1040** = 39+47+909+45 | `decode.rs:32` |
| 每图像行 `PX_PER_ROW` | **2080**（两通道） | `decode.rs:35` |
| 同步帧 `PX_SYNC_FRAME` | 39 | `decode.rs:17` |
| 空间数据 `PX_SPACE_DATA` | 47 | `decode.rs:20` |
| 图像数据 `PX_CHANNEL_IMAGE_DATA` | **909** | `decode.rs:23` |
| 遥测 `PX_TELEMETRY_DATA` | 45 | `decode.rs:27` |

- 行率 = 2080 像素/行 × 2 行/秒 = 4160，与 FINAL_RATE 自洽（**2 行/秒**）。
- 注意：很多资料说"11025Hz"，那是**输入音频常见采样率**；noaa-apt 内部工作在
  work_rate=**12480Hz**（standard profile，=4160×3），最终重采样到 4160。

### 1.2 处理链（decode.rs:43-162）
1. 重采样 input → work_rate=12480，带 DC 去除低通（cutout=4800Hz≈2×载波，atten=30，
   delta=1000Hz）；
2. AM 解调（`dsp.rs:350-383 demodulate`）——**apt137 两采样鉴别器**：
   ```
   phi = 2π·fc/fs
   y[i] = sqrt(prev² + curr² − 2·cos(phi)·prev·curr) / sin(phi)
   ```
   对被 cos(2π·fc·t) 调制的信号，输出即慢变包络（视频幅度）。
3. 解调后低通到 FINAL_RATE/2 = 2080Hz（`decode.rs:95-100`）；
4. 重采样到 4160（`decode.rs:154-159`）；
5. **行同步**：`generate_sync_frame()`（`decode.rs:171-199`）生成 38 样本 ±1 方波
   guard；`find_sync()`（`decode.rs:204-263`）滑动互相关找峰，最小峰距 = 行距×0.8。
   guard 在 4160 下 = 2 个 -1 + 7 个完整周期(2×-1,2×+1) + 8 个 -1 尾 = 38 样本；
6. 按 2080 样本/行对齐，切 A/B 通道各 909 像素图像区。

### 1.3 滤波器（filters.rs）
- Kaiser 窗 sinc FIR。beta 公式 `filters.rs:154-161`：>50dB 用 `0.1102·(A−8.7)`，
  <21dB 为 0，中间 `0.5842·(A−21)^0.4 + 0.07886·(A−21)`。
- 长度 `filters.rs:164`：`ceil((A−8)/(2.285·Δω))+1`，强制奇数。

### 1.4 校准点（对照旧 lite 版）
- 旧版用 Hilbert 包络近似；新版主路径改用 **dsp.rs:373 的 apt137 鉴别器**，并补
  work_rate=12480 级与 Kaiser 低通，常量全部对齐 decode.rs。

---

## 二、meteor_demod（C）真读记录

源码位置：`repos/meteor_demod/src/`。**它只做 QPSK/OQPSK 解调**，后级 Viterbi/去交织/
帧解析是标准 LRPT 遥测链。

### 2.1 解调链（demod.c / pll.c）
- RRC 匹配滤波插值 → AGC → **Costas 环**载波恢复 → **Gardner** 位同步 → 输出软 I/Q。
- 符号率 `SYM_RATE = 72000`（`main.c:19`）。
- RRC α=**0.6**、阶数 64（`main.c:26-27`），插值倍率 4（`main.c:30`）。
- Costas：`pll.c:45-57` NCO 混频 + 二阶环；误差 `pll.c:117-120`
  `tanh(I)·Q − tanh(Q)·I` 再 /50；BW=100、阻尼=1/√2（`pll.h`）。
- Gardner 误差 `demod.c:198`：`(Im(cur)−Im(before))·Im(mid)`。

### 2.2 LRPT 后级链常量
- 差分 QPSK（DQPSK）：接收端 `z[n]·conj(z[n−1])` 象限恢复双比特。
- 卷积码 **K=7, r=1/2**，生成多项式 **G1=0x79(八进制171) / G2=0x5F(八进制137)**。
  > 注意 Meteor-M2 的 G2=137，**不是**通用 CCSDS 的 133。
- 卷积去交织：Forney 型，I=36 分支、相邻分支延迟 J=2048 符号（Viterbi 之前）。
- LRPT 帧同步字 **0x1DFCDC**（24bit），其后解析 VCID/APID。

### 2.3 踩坑：Viterbi 回溯
- 仓库里 `demod.ViterbiDecoder` 的回溯**只存决策位、没存前序状态**，导致非平凡序列
  恒解码成全零（无噪往返 ~50% 误码）。
- 修复：在 `meteor_sat.meteor_viterbi_decode` 自包含实现，每步为每个 next_state 记录
  **最佳前序状态**，回溯时用前序状态表重建路径 → 无噪往返 100%。

---

## 三、往返验证（tests/apt_meteor_roundtrip.py，7/7 通过）
1. APT 合成音频 → 解调：120 行对齐 ≥100、锁定率 ≥0.8，A/B 结构正确；
2. APT 同步 guard = 38 样本 ±1、14 个跳变半周期；
3. 真实 `syn_robot36.wav`（SSTV）跑解码不崩溃，优雅返回 no_sync；
4. METEOR QPSK 调制 → 低噪加噪 → Viterbi：BER<0.01，高噪 BER 上升；
5. Viterbi 已知数据编码→解码：50/200/800 bit 全 100% 恢复；
6. DQPSK 编码/差分解码互逆；
7. LRPT 同步字 0x1DFCDC 检出并解析 VCID/APID。

## 四、交付物
- `mbdsdr_ai/noaa_apt_lite.py` — noaa-apt 忠实移植（鉴别器/同步/行结构）。
- `mbdsdr_ai/meteor_sat.py` — meteor_demod 常量 + DQPSK + 0x79/0x5F Viterbi + LRPT 帧。
- `tests/apt_meteor_roundtrip.py` — 往返验证。
- 注册工具：`noaa_apt_decode`、`meteor_lrpt_demod`、`meteor_viterbi_decode`。
