# NOAA APT 与 METEOR LRPT 源码学习笔记

> 基于 noaa-apt (Rust, martinber) 和 meteor_demod (C, digitelektro) 的源码精读。
> 所有结论带 `文件:行号` 引用，路径相对于各仓库根目录。

---

## 1. NOAA APT 真实参数

### 1.1 帧结构与像素率

| 参数 | 值 | 来源 |
|---|---|---|
| 行率 | 2 行/秒 | `noaa-apt/docs/how-it-works.md:290` |
| 视频采样率 | 4160 Hz（= 2080 px/行 × 2 行/秒） | `noaa-apt/src/decode.rs:14` (`FINAL_RATE = 4160`) |
| 每行像素 | 2080 | `noaa-apt/src/decode.rs:35` (`PX_PER_ROW = 2080`) |
| 每通道像素 | 1040 | `noaa-apt/src/decode.rs:32` (`PX_PER_CHANNEL = 1040`) |
| 同步帧长度 | 39 像素 | `noaa-apt/src/decode.rs:17` (`PX_SYNC_FRAME = 39`) |
| 深空/分标记 | 47 像素 | `noaa-apt/src/decode.rs:20` (`PX_SPACE_DATA = 47`) |
| 图像数据 | 909 像素 | `noaa-apt/src/decode.rs:23` (`PX_CHANNEL_IMAGE_DATA = 909`) |
| 遥测 | 45 像素 | `noaa-apt/src/decode.rs:27` (`PX_TELEMETRY_DATA = 45`) |
| 校验 | 39+47+909+45 = 1040 | `noaa-apt/src/decode.rs:31` 注释 |

### 1.2 副载波与调制

- **AM 副载波频率：2400 Hz**。`noaa-apt/src/decode.rs:38` (`CARRIER_FREQ: u32 = 2400`)。
- **调制方式：先 AM 后 FM**。卫星端用视频信号幅度调制 2400 Hz 副载波（AM），再 FM 调制到 137 MHz 载波。见 `noaa-apt/docs/how-it-works.md:272`："The signal is modulated first on AM and then on FM."
- **信号幅度 = 像素亮度**。`noaa-apt/docs/how-it-works.md:288`："The signal amplitude represents the brightness of each pixel."
- **8 bit/像素**。`noaa-apt/docs/how-it-works.md:286`。
- **工作采样率建议**：默认高质量 profile 为 **20800 Hz**（= 4160 × 5），见 `noaa-apt/src/default_settings.toml:134`。中等质量 16640 Hz（`:122`），快速 12480 Hz（`:110`）。
- **重采样低通截止**：4800 Hz（约 2× 副载波），见 `noaa-apt/src/default_settings.toml:137`。
- **解调后低通截止**：`FINAL_RATE/2 = 2080 Hz`，见 `noaa-apt/src/decode.rs:95`。

### 1.3 同步向量 A/B 的具体波形

文档明确说明（`noaa-apt/docs/how-it-works.md:307-323`）：

- **Channel A sync**：**1040 Hz 方波，7 个周期**。在图像上表现为 7 条黑白相间竖条，白色条略窄。
- **Channel B sync**：**832 脉冲/秒**（即方波频率 832/2 = 416 Hz？或脉冲重复率 832 Hz）。白色条略宽。
- A+B 两个同步头交替出现，在音频上形成特征性的 "tick-tock" 声。

从 `FINAL_RATE = 4160` 推算：
- Sync A 方波频率 1040 Hz → 周期 = 4160/1040 = **4 像素**（2 黑 + 2 白）。7 个周期 = 28 像素。加上前后保护带共 39 像素。
- Sync B 832 脉冲/秒 → 每脉冲 = 4160/832 = **5 像素**（约 2.5 黑 + 2.5 白）。这就是 B 比 A "白色条更宽" 的原因。

### 1.4 Telemetry 遥测

- 每通道每行末尾 45 像素为遥测条带。
- 水平位置：Channel A 遥测在像素偏移 **994** 起，宽 44；Channel B 在 **2034** 起，宽 44。见 `noaa-apt/src/telemetry.rs:149-150`。
- 每个遥测条带垂直方向分为 16 个楔形（wedge），每个楔形高 8 行像素。
- **固定对比楔形（1-9号）值**：31, 63, 95, 127, 159, 191, 224, 255, 0（0-255 量级）。见 `noaa-apt/src/telemetry.rs:130`。
- 楔形 10-15 为红外温度计（黑体辐射计、贴片温度等），楔形 16 为通道 ID。
- 通道识别：将 wedge 16 的值与 wedge 1-9 比较，最近匹配即为通道号。见 `noaa-apt/src/telemetry.rs:97-117`。
- 对比度自动调整：用 wedge 9（=0，黑电平和 wedge 8（=255，白电平）做拉伸。见 `noaa-apt/src/noaa_apt.rs:146-148`。

---

## 2. APT 同步检测算法

### 2.1 方法：时域互相关（非 Goertzel、非过零检测）

noaa-apt **不使用 Goertzel 算法，也不使用过零检测**。它使用最朴素的**滑动窗互相关**：

```rust
// noaa-apt/src/decode.rs:225-254
for i in 0..signal.len() - guard.len() {
    let mut corr: f32 = 0.;
    for j in 0..guard.len() {
        match guard[j] {
            1 => corr += signal[i + j],
            -1 => corr -= signal[i + j],
            _ => unreachable!(),
        }
    }
    // ... peak picking
}
```

见 `noaa-apt/src/decode.rs:225-233`。这是 O(N×M) 的暴力互相关，对每个滑动位置累加/减去信号采样。

### 2.2 同步模板（guard）的生成

`generate_sync_frame()` 函数生成同步 A 的模板：

```rust
// noaa-apt/src/decode.rs:186-198
repeat(-1).take(sync_pulse_width)           // 2 像素低电平
.chain(
    repeat(-1).take(sync_pulse_width)       // 再 2 像素低
    .chain(repeat(1).take(sync_pulse_width)) // 2 像素高
    .cycle()
    .take(7 * 2 * sync_pulse_width),        // 7 个完整周期 = 28 像素
)
.chain(repeat(-1).take(8 * pixel_width))     // 尾部 8 像素低
```

其中 `sync_pulse_width = pixel_width * 2`（`decode.rs:182`），即每个电平持续 2 个像素。

**模板在 4160 Hz 下的实际波形**（38 个采样，对应 38 像素）：

```
像素: 0  1  2  3  4  5  6  7  8  9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30-37
电平: -1 -1 -1 -1 +1 +1 -1 -1 +1 +1 -1 -1 +1 +1 -1 -1 +1 +1 -1 -1 +1 +1 -1 -1 +1 +1 -1 -1 +1 +1 -1 -1...
       |-- 4px 低 --||----- 7 个周期方波（2px 高 / 2px 低交替）-----||----- 8px 低 -----|
```

测试用例验证见 `noaa-apt/src/decode.rs:274-318`。

### 2.3 峰拾取策略

- **最小峰间距**：`samples_per_work_row * 8 / 10`，即行距的 80%。见 `noaa-apt/src/decode.rs:216`。
- **局部峰保持**：如果新相关值比前一个峰大且在最小距离内，替换前一个峰。见 `noaa-apt/src/decode.rs:250-253`。
- **缺失行补偿**：如果当前位置按行距推算应该已有 N 个峰但实际不足，直接填入当前位置（允许丢行）。见 `noaa-apt/src/decode.rs:244-246`。
- 找到 ≥5 个同步帧才继续解码，否则报错。见 `noaa-apt/src/decode.rs:112-118`。

### 2.4 AM 解调公式

同步检测是在**解调后的视频信号**上做的。解调使用两采样点包络检测：

```
y[i] = sqrt(x[i-1]² + x[i]² - 2·x[i-1]·x[i]·cos(φ)) / sin(φ)
```

其中 `φ = 2π·2400/work_rate`。见 `noaa-apt/src/dsp.rs:360-377`。

这是从 apt137 项目借鉴的快速 AM 解调法，不需要 Hilbert 变换，只用当前和前一个采样。

---

## 3. METEOR LRPT 真实参数

### 3.1 物理层参数

| 参数 | 值 | 来源 |
|---|---|---|
| 符号率 | 72000 baud | `meteor_demod/src/main.c:19` (`#define SYM_RATE 72000`) |
| 调制方式 | QPSK（默认） | `meteor_demod/src/main.c:75` (`mode = QPSK`) |
| RRC 滚降系数 α | 0.6 | `meteor_demod/src/main.c:26` (`#define RRC_ALPHA 0.6`) |
| RRC FIR 阶数 | 64（即 129 抽头） | `meteor_demod/src/main.c:27` (`#define RRC_FIR_ORDER 64`) |
| 插值倍数 | 4× | `meteor_demod/src/main.c:30` (`#define INTERP_FACTOR 4`) |
| 卷积码 | r=1/2, K=7 | （本仓库不实现，输出软符号给外部 Viterbi） |

### 3.2 Costas 环参数

| 参数 | 值 | 来源 |
|---|---|---|
| 环路带宽默认 | 100 Hz | `meteor_demod/src/include/pll.h:13` (`#define COSTAS_BW 100`) |
| 阻尼系数 | 1/√2 ≈ 0.707 | `meteor_demod/src/include/pll.h:14` (`#define COSTAS_DAMP 1/M_SQRT2`) |
| 初始 NCO 频率 | 0.001 rad/sample | `meteor_demod/src/include/pll.h:15` |
| 频率限幅 | ±0.8 rad/sample | `meteor_demod/src/include/pll.h:16` |
| 误差滑动平均窗 | 2500 样本 | `meteor_demod/src/include/pll.h:17` |

### 3.3 Costas 环 α/β 系数计算

二阶 PLL 的 α（比例）和 β（积分）由阻尼和带宽决定：

```c
// meteor_demod/src/pll.c:96-98
denom = (1.0 + 2.0*damping*bw + bw*bw);
self->alpha = (4*damping*bw)/denom;
self->beta  = (4*bw*bw)/denom;
```

这里 `bw` 已经归一化到符号率（rad/symbol），见 `meteor_demod/src/demod.c:37`：
```c
pll_bw = 2*M_PI*pll_bw/sym_rate;
```

**锁定检测**（QPSK）：当误差滑动平均 < 0.5 时认为锁定，带宽缩小为 1/3；> 0.55 时失锁，恢复原带宽。见 `meteor_demod/src/pll.c:70-77`。

### 3.4 AGC 参数

| 参数 | 值 | 来源 |
|---|---|---|
| 平均窗长 | 1024×64 = 65536 样本 | `meteor_demod/src/include/agc.h:8` |
| 目标幅度 | 180 | `meteor_demod/src/include/agc.h:9` |
| 最大增益 | 20 | `meteor_demod/src/include/agc.h:10` |
| 直流偏置窗 | 256×1024 = 262144 | `meteor_demod/src/include/agc.h:11` |

AGC 实现：先减直流偏置（滑动平均），再用幅度滑动平均归一化。见 `meteor_demod/src/agc.c:27-38`。

### 3.5 符号定时恢复（Gardner 算法）

```c
// meteor_demod/src/demod.c:193-200
if (resync_offset >= resync_period/2 && resync_offset < resync_period/2+1) {
    mid = agc_apply(self->agc, tmp);          // 中点采样
} else if (resync_offset >= resync_period) {
    cur = agc_apply(self->agc, tmp);          // 终点采样
    resync_offset -= resync_period;
    resync_error = (cimagf(cur) - cimagf(before)) * cimagf(mid);  // Gardner 误差
    resync_offset += (resync_error*resync_period/2000000.0);      // 调整
    before = cur;
    cur = costas_mix(self->cst, cur);
    costas_correct_phase(self->cst, costas_delta(cur, cur));
}
```

注意：这里 Gardner 误差只取了 Q 路（cimagf），因为 QPSK 的 I/Q 误差在两个路上对称。环路增益常数为 `1/2000000`（`demod.c:199`）。

### 3.6 Costas 误差检测器

```c
// meteor_demod/src/pll.c:117-120
error = (lut_tanh(crealf(sample)) * cimagf(sample)) -
        (lut_tanh(cimagf(cosample)) * crealf(cosample));
return error/50;
```

这是 QPSK 常用的 `sign(I)·Q - sign(Q)·I` 误差检测器（用 tanh 近似 sign，软判决）。误差再除以 50 归一化。

---

## 4. METEOR 解调链全景

meteor_demod 仓库只负责到**软符号输出**，后续 Viterbi/去交织/解扰/图像合成由外部工具（如 medet、meteor_decode）完成。完整链路：

```
IQ 采样文件
    │
    ▼
[Source: wavfile.c] 读入复数 IQ，可配采样率和位深
    │
    ▼
[Interpolator: interpolator.c] 4× 零阶保持插值 + RRC 匹配滤波
    │  filter_rrc(order=64, factor=4, osf=sr/sym_rate, alpha=0.6)
    │  见 meteor_demod/src/interpolator.c:41
    │  输出采样率 = input_sr × 4
    ▼
[AGC: agc.c] 直流去除 + 幅度归一化（目标 180，最大增益 20）
    │  见 meteor_demod/src/agc.c:22-39
    ▼
[Gardner 定时恢复: demod.c:193-200]
    │  在插值后的采样流中，每符号取 mid 和 cur 两个点
    │  用 Gardner 误差调整采样相位
    ▼
[Costas 环: pll.c] 载波恢复
    │  NCO 旋转 + 误差检测 + α/β 修正
    │  见 meteor_demod/src/pll.c:40-87
    ▼
[符号输出: demod.c:152-168]
    │  每个判决时刻输出 I/Q 各一个 int8_t 软符号（clamp(real/2), clamp(imag/2)）
    │  写入输出文件（原始软比特流）
    ▼
[外部：Viterbi 解码]  ← 本仓库不含
    │  K=7, r=1/2, G1=171, G2=133 (CCSDS 标准)
    ▼
[外部：去交织]  ← 本仓库不含
    │  LRPT 使用卷积交织（深度见 LRPT 标准）
    ▼
[外部：解扰]  ← 本仓库不含
    │  多项式 x^8+x^5+x^4+1 (LRPT 专用)
    ▼
[外部：VCDU/帧同步 → 图像合成]  ← 本仓库不含
```

**关键设计观察**：
1. RRC 滤波器在插值器内部做（`interpolator.c:100`），零阶保持上采样后卷积。
2. AGC 在 Gardner 定时环内逐样本应用（`demod.c:194,196`），不是在 Costas 之后。
3. Costas 环在符号判决点才更新相位（`demod.c:203-204`），不是逐样本。
4. 输出是**软符号**（int8_t -128~127），不是硬判决比特，供 Viterbi 使用。

---

## 5. 可迁移到 MBDSDR 的点（对照 mbdsdr_ai 现有代码）

### 5.1 noaa_apt_lite.py 的问题

对照文件：`mbdsdr_ai/noaa_apt_lite.py`

#### 问题 1：解调方式错误——用了 FM 鉴频而非 AM 包络检测

`noaa_apt_lite.py:122-132` 使用 Hilbert 变换取**瞬时频率**（FM 解调）：
```python
z = hilbert(band)
inst = np.angle(z[1:] * np.conj(z[:-1])) / (2.0 * np.pi) * fs
```

但 noaa-apt 明确说明 APT 是 **AM 调制**（`how-it-works.md:272,282,288`），2400 Hz 是 AM 副载波，信号幅度代表亮度。noaa-apt 用两采样点包络检测（`dsp.rs:373`）：
```rust
output[i] = (prev_sq + curr_sq - prev*curr*cosphi2).sqrt() / sinphi;
```

**后果**：用 FM 鉴频解调 AM 信号，输出近似常数（AM 信号幅度变化不会反映为频率变化），同步头和图像全部丢失。
**修复**：改为 AM 包络检测——先带通滤波 2400 Hz，再用 Hilbert 取解析信号求模（或直接用 noaa-apt 的两采样点公式）。

#### 问题 2：同步向量模板错误——脉冲宽度和序列都不对

`noaa_apt_lite.py:36-39` 定义的 `_SYNC_WORD`：
```python
_SYNC_WORD = np.array([
    1, 1, 1, 0, 0, 0, 1, 1, 0, 0, 0, 1, 1, 1, 0, 0, 1, 1, 1, 0,
    0, 0, 1, 1, 0, 0, 0, 1, 1, 1, 0, 0, 1, 1, 1, 0, 0, 0, 1, 1,
])
```

这有 40 个元素，脉冲宽度为 2-3 像素不等（111, 000, 11, 000...），完全不是标准波形。

**正确模板**（对照 `decode.rs:186-198` 和测试 `decode.rs:298-316`）：

在 4160 Hz 下，38 采样（-1=黑, +1=白）：
```
[-1,-1,-1,-1, +1,+1,-1,-1, +1,+1,-1,-1, +1,+1,-1,-1,
 +1,+1,-1,-1, +1,+1,-1,-1, +1,+1,-1,-1, +1,+1,
 -1,-1,-1,-1,-1,-1,-1,-1]
```

即：4 个黑 → 7 个周期方波（每周期 4 像素 = 2 黑 2 白）→ 8 个黑。
转换成 0/1 表示：`[0,0,0,0, 1,1,0,0, 1,1,0,0, 1,1,0,0, 1,1,0,0, 1,1,0,0, 1,1,0,0, 1,1, 0,0,0,0,0,0,0,0]`

这是 **38 个采样**，不是 40。脉冲宽度严格为 2 像素（对应 1040 Hz 方波）。

#### 问题 3：行结构偏移可能错误

`noaa_apt_lite.py:31-32`：
```python
APT_IMG_A_OFFSET = APT_SYNC_LEN + APT_SPACE_LEN  # 86
APT_IMG_B_OFFSET = APT_HALF + APT_SYNC_LEN + APT_SPACE_LEN  # 1126
```

按 noaa-apt 的参数（`decode.rs:17-32`），Channel A 图像数据从偏移 `39+47=86` 开始，宽 909。这个计算是对的。
但 Channel B 应该从 `1040+39+47 = 1126` 开始——这也对。
**注意**：noaa-apt 实际同步模板是 38 像素（guard），而 PX_SYNC_FRAME 定义为 39。差 1 像素是因为 guard 尾部少算了 1 个黑像素，实际互相关时不影响。

#### 问题 4：缺少 Sync B 的区分

noaa-apt 只用 Sync A 做行同步（`decode.rs:171` 只生成 A 模板）。mbdsdr 也是这样做的，可以接受。但如果要做更高质量的解码，可以利用 Sync B（832 Hz）做辅助校验。

### 5.2 meteor_sat.py 的问题

对照文件：`mbdsdr_ai/meteor_sat.py`

#### 问题 5：Meteor-M2 调制方式标注错误

`meteor_sat.py:223`：
```python
modulation="OQPSK",
```

但 meteor_demod 仓库默认是 QPSK（`main.c:75`），且 LRPT 标准就是 **QPSK**（不是 OQPSK）。OQPSK 是 Meteor-M2 的某些其他模式？实际上，Meteor-M2 LRPT 下行是 QPSK，符号率 72 kbaud。OQPSK 应该是标注错误。

#### 问题 6：缺少去交织步骤

`meteor_sat.py:384-389` 的链路描述：
```
QPSK解调 → 符号转比特 → Viterbi解码 → 解扰 → CADU提取
```

**缺少去交织（deinterleave）**。LRPT 在卷积编码后有卷积交织（深度约 8 行 × 256 字节），必须在 Viterbi 之前做去交织。实际顺序应该是：
```
QPSK解调 → 去交织 → Viterbi解码 → 解扰 → CADU提取
```

等等，实际上 CCSDS 标准是：卷积编码 → 交织 → 传输。所以接收端是：接收 → 去交织 → Viterbi 译码。但 LRPT 的交织是在 Viterbi 之后？不对。

让我重新理清：CCSDS 建议的顺序是：
1. 数据流加同步字
2. 卷积编码（r=1/2）
3. 卷积交织
4. QPSK 调制发送

接收端：QPSK 解调 → 去交织 → Viterbi 译码 → 去同步字 → 解扰。

但 meteor_demod 只输出软符号，Viterbi 和去交织都在外部。mbdsdr 的 demodulate_lrpt 直接 Viterbi 解码没有去交织步骤，这是缺失的。

#### 问题 7：ASM 同步字可能错误

`meteor_sat.py:441-444` 定义的 ASM：
```python
ASM = np.array([1,1,0,0,1,0,1,1,1,1,0,0,0,1,0,0,
                 0,1,1,0,0,1,0,1,1,1,1,0,0,0,1,0,
                 0,0,1,1,0,0,1,0,1,1,1,1,0,0,0,1,
                 0,0,0,1,1,0,0,1,0,1,1,1,1,0,0,0])
```

这是 64 比特，看起来像是重复的模式。LRPT 的实际 ASM 是 **0x0218A7A7B52DB87E**（64 比特，但通常 CCSDS ASM 是 32 比特：0x1ACFFC1D）。mbdsdr 这个 ASM 看起来是伪造的占位符。

#### 问题 8：Costas 环参数未参考 meteor_demod 的实际值

mbdsdr 用 `demod.py` 里的 QPSKDemodulator，但没有对照 meteor_demod 的实际参数：
- Costas 带宽应从 100 Hz 开始（`pll.h:13`）
- 阻尼 1/√2（`pll.h:14`）
- 锁定阈值：QPSK 误差滑动平均 < 0.5 时带宽缩为 1/3（`pll.c:71-72`）
- Gardner 环路增益常数：`resync_period/2000000.0`（`demod.c:199`）
- RRC α=0.6, 阶数 64（`main.c:26-27`）

这些具体数值应该直接搬过来调参。

### 5.3 sstv_decoder.py 对照

SSTV 与 APT 是完全不同的模式（SSTV 是 1900/1200 Hz 移频键控，APT 是 2400 Hz AM），两者不混淆即可。`sstv_decoder.py:10` 正确识别了 VIS 头（1900 Hz break + 1200 Hz 引导），这部分没有与 APT 混淆的问题。

---

## 6. 总结：MBDSDR 优先修复清单

| 优先级 | 问题 | 文件 | 修复方向 |
|---|---|---|---|
| P0 | APT 解调方式错（FM→AM） | noaa_apt_lite.py:122 | 改为包络检测/Hilbert 取模 |
| P0 | APT 同步模板波形错 | noaa_apt_lite.py:36 | 改为 38 采样 2px 方波（见 §2.2） |
| P1 | Meteor 缺去交织步骤 | meteor_sat.py:414 | Viterbi 前加卷积去交织 |
| P1 | Meteor 调制标注错（OQPSK→QPSK） | meteor_sat.py:223 | 改为 QPSK |
| P2 | Costas 环参数未调 | demod.py | 对照 pll.c 用 bw=100Hz, damp=1/√2 |
| P2 | ASM 同步字是占位符 | meteor_sat.py:441 | 查 LRPT 标准 ASM |

---

## 附录：阅读的源码文件清单

noaa-apt（Rust）：
1. `src/main.rs` — 入口，模式分发
2. `src/noaa_apt.rs` — 高层接口，图像处理
3. `src/decode.rs` — **核心**：帧结构常量、AM 解调、同步互相关
4. `src/filters.rs` — Kaiser 窗 FIR 滤波器设计
5. `src/telemetry.rs` — 遥测楔形解码
6. `src/dsp.rs` — AM 解调公式、重采样
7. `src/default_settings.toml` — 默认参数
8. `docs/how-it-works.md` — 官方原理说明

meteor_demod（C）：
9. `src/main.c` — 入口，默认参数
10. `src/demod.c` — **核心**：解调线程，Gardner + Costas 集成
11. `src/pll.c` — Costas 环实现
12. `src/agc.c` — AGC 实现
13. `src/interpolator.c` — RRC 插值滤波
14. `src/filters.c` — RRC 滤波器系数计算
15. `src/include/pll.h` — Costas 常量定义
16. `src/include/agc.h` — AGC 常量定义
