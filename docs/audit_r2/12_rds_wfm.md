# RDS / WFM 立体声 Lite 深度审查（第二轮）

审查对象：
- `mbdsdr_ai/rds_lite.py`（323 行）
- `mbdsdr_ai/wfm_stereo_lite.py`（137 行）

对照基准：`repos/redsea/`（`block_sync.cc`、`dsp/subcarrier.cc`、`station.cc`、`util/util.hh`）与 EN 50067 / IEC 62106。所有常量均已本地数值复算（见文末"复算方法"）。

---

## 0. 结论速览

| 模块 | 判定 |
|---|---|
| CRC 生成多项式 / 偏移字 | ✅ **正确**（与标准、redsea 一致，任务书里给的 B=0x199/C=0x166/C'=0x1B4/D=0x12D 反而是错的） |
| 块同步（无频偏合成信号） | ✅ 能工作，但**弱**（单靠伴随式计数，无 A→B→C→D 节律校验） |
| 0A 组 PS 段地址位域 | ❌ **真 bug**（位域取错块，实验室自环 PS 都解不对） |
| 4A 时钟位域 | ❌ **真 bug**（MJD/hour/偏移符号全部错位，仅 minute 碰巧对） |
| 57kHz 载波相位恢复 | ❌ **真 bug / 占位**（静态均值法，无 PLL；这是"实验室绿、真机红"的头号根因） |
| WFM 38kHz 载波恢复 + 分离矩阵 | ❌ **真 bug**（尺度因子错 + 群延时相位错位，实测声道泄漏 ~0.78，近乎单声道） |
| 去加重 50µs | ✅ 正确（一阶 RC，时间常数对） |
| 空壳/占位 | ⚠️ 动态载波跟踪、RT 完整重组、AF、FEC 纠错均显式留空 |

**"实验室绿、真机红"根因定位**：
1. 静态载波相位恢复对 57kHz 副载波频率漂移零容忍（实测仅 **+8 Hz** 偏移即 `rds_present=False`）；RTL 棒 ppm 误差在 57kHz 上轻松有数 Hz~数十 Hz。redsea 用 PLL（`subcarrier.cc` `kPLLBandwidth_Hz=0.03`）专门跟踪，lite 用 `0.5*angle(mean(z²))` 一次性静态估计——频偏存在时 `z²` 在窗内旋转、均值趋零、相位估计崩坏。
2. 0A/4A 位域按"自创布局"解析，实验室自环（编码侧也用同一错布局）能跑通，但真实广播的块 B/C 位映射完全不同 → PS/时间解出乱码。

---

## 1. RDS CRC / 偏移字 —— 正确，无需改

`rds_lite.py:27` `RDS_POLY = 0x1B9`
- 标准生成多项式 g(x)=x¹⁰+x⁸+x⁷+x⁵+x⁴+x³+1，去掉领头 x¹⁰ 后的 10 位反馈系数 = `0b0110111001` = **0x1B9**（完整 11 位 = 0x5B9，两种写法都对，LFSR 用 10 位的）。✅

`rds_lite.py:29`
```python
OFFSET_WORDS = {"A": 0x0FC, "B": 0x198, "C": 0x168, "D": 0x1B4, "E": 0x350}
```
- 与 redsea `block_sync.cc:138-144`（IEC 62106 Table B.1）逐项核对：
  - A=`0b0011111100`=0x0FC ✅
  - B=`0b0110011000`=0x198 ✅
  - C=`0b0101101000`=0x168 ✅
  - C′=`0b1101010000`=**0x350** ✅（lite 命名为 "E"，只是别名，语义在 `_parse_group` `rds_lite.py:176` 已正确接受 C 或 E 作块 3）
  - D=`0b0110110100`=0x1B4 ✅
- 任务书给出的 `B=0x199, C=0x166, C'=0x1B4, D=0x12D` 与权威 redsea 不符，**以 redsea/标准为准，lite 是对的**。

`rds_lite.py:33` `SYNDROME = {A:0x17F, B:0x00E, C:0x12F, D:0x297, E:0x2EC}`
- 与 redsea 的 syndrome 表（0x3D8/0x3D4/0x25C/0x3CC/0x258）数值不同，但**这不是错**：redsea 用奇偶校验矩阵 H 作线性映射，lite 用同一 LFSR 作线性映射，二者都是同一循环码的合法伴随式表示。
- 数值复算：用教科书 GF(2) 长除法（G11=0x5B9）按标准编码一个 26bit 块，再喂给 lite 的 `crc10_remainder`，得到的余项**精确命中** lite 的 SYNDROME 表（A=0x17F…E=0x2EC）。即：**真实电台发的标准块能被 lite 的 LFSR 识别**。CRC 链路本身不背"真机红"的锅。

> 结论：CRC/偏移字**不要动**。问题在载波恢复和位域解析。

---

## 2. [真bug] 0A 组 PS 段地址取错块

`rds_lite.py:189`
```python
seg = (info["C"] >> 10) & 0x3      # 从块 C 取段地址
```
`rds_lite.py:76-80`（编码侧同样自创）
```python
b = build_b_block(...)              # 块 B 没放 TA/MS/DI/address（low5 恒 0）
c = ((seg & 0x7) << 9) | ((di & 0xF) << 5) | ((ms & 1) << 4)   # 把 seg/di/ms 塞进块 C
```

**标准/redsea 的真实布局**（`station.cc:248-252`，`getBits` 为 LSB 对齐 `util.hh:34`）：
- 段地址 = `getBits<2>(BLOCK_B, 0)` = **块 B 低 2 位**（bit1..0）
- DI = 块 B bit2；MS = 块 B bit3；TA = 块 B bit4
- **块 C（BLOCK3）在 0A 里是备用频率 AF 列表**（`station.cc:263-264`），根本不是 seg/di/ms。

后果：
- lite 编码把 seg 放块 C、解码又从块 C 读——**实验室自环自洽**，但实测自环 PS="TEST" 解出来却是 **"ST"**：因为 `seg<<9` 与 `(c>>10)&0x3` 位错位（seg 0/1 都映射回索引 0，seg 2/3 都映射回索引 1），4 个段被压成 2 个槽。**实验室本身就没真正通过**。
- 对真实电台：块 B 低 2 位才有正确段地址，lite 完全忽略；块 C 是 AF 数据，被当 seg 用 → PS 重组键值混乱，电台名必然错。

**修法**：段地址改从块 B 取 `seg = info["B"] & 0x3`；块 C 按 AF（或至少别当 seg）。编码侧 `build_0a_group` 也要把 seg/di/ms 挪进块 B 低 5 位。

---

## 3. [真bug] 4A 时钟位域整体错位

`rds_lite.py:206-216`
```python
mjd = (((c & 0x7FFF) >> 1) << 2) | ((d >> 4) & 0x3)   # MJD 低2位取自 d bit5..4
hour = (d >> 11) & 0x1F                                # hour 取 d bit15..11
minute = (d >> 6) & 0x3F                               # minute 取 d bit11..6
off_neg = (d >> 0) & 1                                 # 偏移符号取 d bit0
```

对照 redsea `station.cc:584-610` 真实布局（BLOCK3=C 高字、BLOCK4=D 低字拼接）：
| 字段 | 标准（redsea） | lite 实际取位 | 判定 |
|---|---|---|---|
| MJD(17bit) | 拼 BLOCK2/BLOCK3，bit1..17（块 C bit1..15 + 块 B 低2位） | 块 C bit14..1 + **块 D bit5..4** | ❌ 错（用了块 D，漏了块 B） |
| hour(5bit) | 块 D bit15..12 + **块 C bit0**（跨块） | 块 D bit15..11 | ❌ 错（把 minute 的 MSB bit11 当 hour LSB，漏块 C bit0） |
| minute(6bit) | 块 D bit11..6 | 块 D bit11..6 | ✅ 碰巧对 |
| 偏移符号 | 块 D **bit5** | 块 D **bit0** | ❌ 错 |
| 偏移幅度(5bit) | 块 D bit4..0（0.5h 单位） | 完全没解析 | ⚠️ 缺失 |

后果：4A 组即便同步上，日期/小时/偏移全错（只有分钟数碰巧在同一区间）。属次要功能，但位域确实是错的，不是标准兼容实现。

---

## 4. [真bug / 占位] 57kHz 静态载波相位恢复 —— "真机红"头号根因

`rds_lite.py:239-245`
```python
z = band * np.exp(-1j*2π*57000*t)     # 用精确 57000 本振下变频
z = lfilter(lp...)                     # 2400Hz 低通
z2 = np.mean(z**2)                     # 静态估计
phi = 0.5*np.angle(z2)
s = np.real(z*np.exp(-1j*phi))
```

问题：
- 这是**一次性**静态相位估计，本振频率硬编码 57000.0。真实接收（尤其 RTL）存在 ppm 频偏，使 `z` 在观察窗内旋转；`z²` 以 2 倍频偏旋转，窗较长时均值趋零 → `angle(0)` 退化、相位随机。
- 实测：合成完美信号（无频偏）`rds_present=True, groups=4`；**仅注入 +8 Hz 副载波频偏**，重跑同一解码器 → `rds_present=False, blocks_synced 19→11, groups_partial=0`。8Hz 在 57kHz 上 = 140 ppm，是 RTL 棒的典型量级。
- 代码自己也承认了（`rds_lite.py:9-10`）："Costas 动态载波跟踪（静态相位恢复对 ppm 校正后的 RTL 棒足够，动态跟踪留后续）"。这是**已声明的占位**，但它正是真机失败的直接原因——"ppm 校正"这一前提在当前流水线里并不存在。

对照 redsea：`subcarrier.cc:104-105` 用 NCO + PLL（`kPLLBandwidth_Hz=0.03`），由 BPSK 相位误差持续压控本振（`subcarrier.cc:206-207`），能跟踪数 Hz 漂移。lite 完全没有等价物。

**修法方向**（不在本轮改码范围）：把静态 `phi` 换成 Costas 环 / 二阶 PLL，对 `z` 作载波相位动态跟踪；至少在分块窗内分段估计而非全窗 `mean`。

---

## 5. [真bug] WFM 38kHz 恢复与 L/R 分离矩阵 —— 实测近乎单声道

`wfm_stereo_lite.py:106-111`
```python
z = hilbert(pilot_band)
theta = np.angle(z)
car = np.cos(2.0*theta)                 # 二倍频恢复 38kHz
D = lfilter(low, mpx*car)               # 差信号
Lraw = 0.5*(S + D)
Rraw = 0.5*(S - D)
```

配合合成端 `wfm_stereo_lite.py:63`：
```python
mpx = 0.5*(L+R) + pilot + 0.5*(L-R)*cos(38k·t)
```

**幅度因子错**（解析推导）：
- `S = LP(mpx) = 0.5(L+R)`。
- `LP(mpx·cos(38k))` 中差信道项 `0.5(L-R)·cos² = 0.5(L-R)·(1+cos2)/2` → 低通后 = **0.25(L-R)**，不是 0.5(L-R)。
- 于是 `Lraw = 0.5[0.5(L+R) + 0.25(L-R)] = (3L+R)/8`，`Rraw=(L+3R)/8`。理想应 `L=S+2·D_lp, R=S-2·D_lp`（需对载波乘 2、且矩阵为 `S±D`）。
- 仅因子一项就造成 **3:1（≈9.5 dB）的固定左右串音**。

**相位错位（实测叠加）**：pilot 路径经过 5 阶 Butterworth 带通（`wfm_stereo_lite.py:93`）+ Hilbert，群延时远大于直通 mpx 路径；恢复出的 `cos(2θ)` 与 mpx 中真实 38kHz 副载波存在静态相位差 δ。

数值往返验证（L=1kHz、R=静音；及反向）：
| 输入 | 恢复后 peakL | 恢复后 peakR | R/L |
|---|---|---|---|
| L=1k, R=0 | 0.900 | **0.705** | 0.78 |
| L=0, R=1k | **0.419** | 0.900 | L/R=2.15 |

R 本应接近 0，却拿到 0.70 的 L 泄漏——**远差于因子 alone 预测的 0.33**，说明相位错位 δ≈75° 把差信号进一步吃掉并串回另一声道。主观听感接近单声道、左右声像糊掉。

**修法方向**：差信道检测后乘 2（或载波取 `2·cos`），矩阵改为 `L=S+D_rec, R=S-D_rec`；并对 pilot 路径与 mpx 路径做群延时对齐（或直接用同一段 mpx 经窄带提取的 pilot 相位，补偿滤波延时）。

---

## 6. 其他观察

- [建议] `wfm_stereo_lite.py:97` `pilot_snr_db = 20log10(p_rms/m_rms)`：分母是**全 mpx RMS**（含强音频），不是 pilot 带外噪声底，故即便干净合成信号也报 **-15~-20 dB**（见实测）。指标名不副实，阈值 `wfm_stereo_lite.py:98` `p_rms>0.02*m_rms` 虽能工作，但对外报告的 SNR 误导。建议分母取 pilot 带外能量。
- [建议] `rds_lite.py:252-262` 同步搜索仅按"伴随式命中块数最多"选解，**没有 redsea `block_sync.cc:189-198` 的 A→B→C→D 26bit 周期节律/三脉冲校验**。弱信号下易锁到错误极性/相位的假同步。
- [占位] `rds_lite.py:9-10` 明确不做：RT 完整段重组、AF 表、时间/日期组解读、DI/MS 细分、FEC 突发纠错（redsea `block_sync.cc:165-184` 有 1~2 bit 突发纠错，lite 无任何纠错，单 bit 错即丢块）。
- [占位/死代码] `rds_lite.py:108-110` `if abs(half-round(half))>1e-6: pass` —— 空操作占位，非整数采样率时并无处理（依赖 `_resample_to_internal` 兜底）。
- [建议] `rds_lite.py:226` `resample_poly` 导入后未使用（`# noqa: F401`）；`wfm_stereo_lite.py:23` 导入 `sosfiltfilt` 但全程用 `lfilter`（零相位滤波本可显著改善 pilot 相位，未用）。
- [正确] 去加重 `wfm_stereo_lite.py:120-123`：`a=exp(-1/(τ·fs))` 一阶 RC，τ=deemph_us·1e-6，50µs 默认（欧标），参数化可切 75µs（美标），实现正确。
- [正确] 位速率/采样率：228000/(2×1187.5)=96.0 半位样本，整数周期，利于同步。

---

## 7. 与 redsea 的关键差异汇总

| 环节 | redsea | lite | 影响 |
|---|---|---|---|
| 载波恢复 | NCO+PLL 动态跟踪（0.03 Hz BW） | 全窗静态 `0.5∠mean(z²)` | **真机失败根因** |
| 位同步 | liquid symsync（RRC，2200Hz BW） | 半位矩形匹配滤波 + 粗 8 档相位搜索 | 弱 |
| biphase→bit | 符号差分 + **DeltaDecoder**（天生抗 180° 模糊） | Manchester a−b + 暴力 pol=±1 | 等价（靠 CRC 计数选极性），可接受 |
| 块同步 | 三脉冲 + A/B/C/C′/D 周期节律 | 仅伴随式计数 | 弱 |
| 纠错 | 1~2 bit 突发 FEC | 无 | 单 bit 错即丢 |
| 0A 段地址 | 块 B 低 2 位 | 块 C bit11..10 | **错** |
| 4A 时钟 | 跨块精确拼接 | 位域整体错位 | **错** |

---

## 8. 复算方法（可复现）

1. **CRC 自洽性**：`std_crc`（G11=0x5B9 教科书长除）vs lite `crc10_remainder`，对 0x1234/0x0000/0xFFFF/0xABCD 输出完全相等；用 std 长除编码的 26bit 块喂 lite LFSR，余项逐项命中 lite SYNDROME 表（0x17F/0x00E/0x12F/0x297/0x2EC）。→ CRC 与标准一致。
2. **0A 自环**：`build_0a_group` 拼 PS="TEST" 4 段 → `synthesize_rds_mpx` → `decode_rds`，得 `pi=1234, pty=3, groups=4` 但 **ps="ST"**（应为 "TEST"）。
3. **频偏鲁棒性**：在合成端把副载波从 57000 改为 57008（+8Hz），解码端仍用 57000 → `rds_present=False, blocks_synced 19→11`。
4. **立体声分离**：L=1kHz/R=0 与 L=0/R=1kHz 经 `synthesize_stereo_iq`→`decode_stereo`，测峰值泄漏比 ~0.78（理想应 ≈0）。

（复算用临时脚本已在审查后删除，未改动任何项目源码。）
