# WSJT-X 物理层源码设计笔记（FT8 / FST4 / JT9 / LDPC）

> 本笔记基于实际阅读 `repos/wsjtx/` 下的 Fortran 源码（不是 README）。
> 所有断言均带 `源文件路径:行号`，路径相对于 `repos/wsjtx/` 仓库根。
> 读源码日期：2026-09-24。

---

## 0. 仓库布局澄清

任务书里给的文件名和仓库实际命名不完全一致，先对齐：

| 任务书叫法 | 仓库实际文件 |
|---|---|
| `lib/ft8/ft8_encode.f90` | `lib/ft8/encode174_91.f90`（加 CRC14 + LDPC 系统编码）+ `lib/ft8/genft8.f90`（组帧/调音调） |
| `lib/ft8/ft8_decode.f90` | `lib/ft8_decode.f90`（顶层模块）+ `lib/ft8/ft8b.f90`（逐候选解码）+ `lib/ft8/decode174_91.f90`（LDPC 混合译码） |
| `lib/ft8/ft8_sync.f90` | `lib/ft8/sync8.f90`（粗同步，二维搜索）+ `lib/ft8/sync8d.f90`（细同步，时域匹配滤波） |
| `lib/ft8/ft8_downsample.f90` | `lib/ft8/ft8_downsample.f90`（同名，实数 12 kHz → 复数 200 Hz） |
| `lib/ft8/gen_ft8.f90` | `lib/ft8/genft8.f90` |
| `lib/fst4/fst4_encode.f90` | `lib/fst4/encode240_101.f90` + `lib/fst4/genfst4.f90` |
| `lib/fst4/fst4_decode.f90` | `lib/fst4_decode.f90`（顶层，1025 行）+ `lib/fst4/decode240_101.f90` |
| `lib/fst4/fst4_sync.f90` | 无独立文件；粗同步在 `lib/fst4_decode.f90` 内调用 `get_candidates_fst4` / `fst4_sync_search`，细同步/比特度量在 `lib/fst4/get_fst4_bitmetrics.f90` |
| `lib/jt9/` | 没有 `jt9/` 子目录，JT9 文件直接在 `lib/` 下（`jt9.f90`、`jt9code.f90`、`jt9com.f90`、`jt9_decode.f90` 等） |
| `lib/ldpc/` | 没有 `ldpc/` 子目录；LDPC 码型文件随模式走：FT8 在 `lib/ft8/ldpc_174_91_c_*.f90`，FST4 在 `lib/fst4/ldpc_240_*_*.f90`，JT9 用 `bpdecode128_90.f90` / `osd128_90.f90` |

---

## 1. 真实算法/参数常量表

### 1.1 FT8 物理层常量（权威来源 `lib/ft8/ft8_params.f90`）

| 常量 | 值 | 含义 | 源码引用 |
|---|---|---|---|
| `KK` | **91** | LDPC 信息位 = 77 消息 + 14 CRC | `lib/ft8/ft8_params.f90:2` |
| `ND` | **58** | 数据符号数（每符号 3 bit） | `lib/ft8/ft8_params.f90:3` |
| `NS` | **21** | 同步符号数（= 3 个 Costas-7 块） | `lib/ft8/ft8_params.f90:4` |
| `NN` | **79** | 总信道符号 = 21 + 58 | `lib/ft8/ft8_params.f90:5` |
| `NSPS` | **1920** | 每符号采样数 @12000 S/s | `lib/ft8/ft8_params.f90:6` |
| 符号时长 | **160 ms** | 1920/12000 = 0.16 s | 由 `ft8_params.f90:6` 推出 |
| 波特率 | **6.25 baud** | 12000/1920 = 6.25 | 由 `ft8_params.f90:6` 推出；`ft8_downsample.f90:31` `baud=12000.0/NSPS` |
| `NZ` | 151680 | 整帧采样 = 1920×79 ≈ 12.64 s | `ft8_params.f90:7` |
| `NMAX` | 180000 | iwave 缓冲 = 15 s×12000 | `ft8_params.f90:8` |
| `NFFT1` | 3840 (=2·NSPS) | 同步阶段符号谱 FFT 长；`NH1=1920` | `ft8_params.f90:9` |
| `NSTEP` | 480 (=NSPS/4) | 粗时间同步步进 = 1/4 符号 | `ft8_params.f90:10` |
| `NDOWN` | **60** | 下采样因子 12000→200 Hz（32 采样/符号） | `ft8_params.f90:12`；`ft8b.f90:99` `fs2=12000/NDOWN` |
| 音调数 | **8**（8-FSK） | 3 bit/symbol → 8 个音调 | `genft8.f90:41-42`；`ft8b.f90:15` `s8(0:7,NN)` |
| LDPC `(N,K,M)` | **(174, 91, 83)** | 83 个校验方程 | `encode174_91.f90:9` `N=174,K=91,M=N-K` |
| CRC | **CRC-14** | 多项式 `0x6757` | `get_crc14.f90:11-12` `data p/1,1,0,0,1,1,1,0,1,0,1,0,1,1,1/` |
| CRC 作用域 | **96 bit** | 77 消息 + 5 零 + 14 CRC（编码时尾部 19 bit 先补零再除） | `encode174_91.f90:39-43`；`get_crc14.f90:3` 注释 |
| Costas-7 序列 `icos7` | **[3,1,4,0,6,5,2]** | 三个同步块复用同一序列 | `genft8.f90:14`；`sync8.f90:25`；`sync8d.f90:13`；`ft8b.f90:39` |
| Gray 映射 `graymap` | **[0,1,3,2,5,6,4,7]** | 3-bit 码字 → 音调 | `genft8.f90:15`；`ft8b.f90:49` |
| 帧结构 | **S7 D29 S7 D29 S7** | 同步块在符号 1–7、37–43、73–79（1-based） | `genft8.f90:32-35` |
| 同步块间隔 | **36 符号** | 7+29=36，故 `m+nssy*36`、`m+nssy*72` | `sync8.f90:68,71`；`sync8d.f90:36-37` |
| 粗同步 df | **3.125 Hz** | 12000/3840 | `sync8.f90:31` 注释 |
| 频偏粗搜索 | 整段频谱 `[nfa,nfb]` | 每频率 bin 与每时间 lag 做 Costas 相关 | `sync8.f90:54-85` |
| 频偏精搜索 | **±2.5 Hz**（步长 0.5 Hz） | `do ifr=-5,5` | `ft8b.f90:121-122` |
| 时间精搜索 | ±10 采样初猜 → ±4 采样精修 | 200 Hz 采样率下 ±10/200=±50 ms | `ft8b.f90:111,145` |
| 每符号谱估计 | **32 点 FFT** 取前 8 bin | 下采样后 32 采样/符号 | `ft8b.f90:155-161` |
| LLR 缩放因子 | **2.83** | `scalefac=2.83` | `ft8b.f90:249` |
| BP 最大迭代 | **30** | `maxiterations=30` | `decode174_91.f90:27`；`ft8b.f90:97` |
| OSD 阶数 | `norder=2`，`maxosd=2` | BP 失败后做最多 2 次 OSD | `ft8b.f90:427-428` |
| 硬同步门限 | `nsync≥6/7/8`（按深度） | 21 个同步符号里硬匹配数 | `ft8b.f90:178-181` |

**关于 FT8 音调排布的细节**：8 个音调间隔 = 1 个 baud = 6.25 Hz。`ft8_downsample.f90:33-36` 抽取的复基带带宽是 `f0-1.5·baud` 到 `f0+8.5·baud`，共 10·baud=62.5 Hz（比 8 音调占用的 7·6.25=43.75 Hz 宽，两端留 taper）。

### 1.2 FST4 / FST4W 物理层常量（权威来源 `lib/fst4/fst4_params.f90` + `lib/fst4_decode.f90`）

| 常量 | 值 | 含义 | 源码引用 |
|---|---|---|---|
| `KK` | **77** | 消息位（注意：这是消息位，不是 LDPC K） | `lib/fst4/fst4_params.f90:4` |
| `ND` | **120** | 数据符号数（每符号 2 bit） | `fst4_params.f90:5` |
| `NS` | **40** | 同步符号 = 5×8 | `fst4_params.f90:6` |
| `NN` | **160** | 总符号 = 40+120 | `fst4_params.f90:7` |
| LDPC(FST4) | **(240,101)** | M=139 校验方程 | `encode240_101.f90:6` `N=240,K=101` |
| LDPC(FST4W) | **(240,74)** | K=74（50 消息+24 CRC） | `genfst4.f90:9,82`；`encode240_74.f90` |
| CRC | **CRC-24**，多项式 `0x100065B` | 作用域 101 bit（FST4）或 74 bit（FST4W） | `get_crc24.f90:11-12` `data p/1,0,0,0,0,0,0,0,0,0,0,0,0,0,1,1,0,0,1,0,1,1,0,1,1/` |
| 调制 | **4-FSK**，Gray 映射 `[0,1,3,2]` | 2 bit/符号 | `get_fst4_bitmetrics.f90:22` `graymap/0,1,3,2/`；`genfst4.f90:86-97` |
| 帧结构 | **s8 d30 s8 d30 s8 d30 s8 d30 s8** | 5 个 8 符号同步块，4 段 30 数据 | `genfst4.f90:12`；`genfst4.f90:99-107` |
| 同步字 1 `isyncword1` | **[0,1,3,2,1,0,2,3]** | 在符号块 0、2、4（8/76/152 起） | `genfst4.f90:27`；`get_fst4_bitmetrics.f90:20` |
| 同步字 2 `isyncword2` | **[2,3,1,0,3,2,0,1]** | 在符号块 1、3（38/114 起） | `genfst4.f90:28`；`get_fst4_bitmetrics.f90:21` |
| 固定扰码 `rvec(77)` | 见 `genfst4.f90:29-31` | 编码前消息位与 rvec 异或 | `genfst4.f90:63`；译码侧 `fst4_decode.f90:488` 再异或回来 |
| 音调间隔 | **1 baud**（4 音调居中于 -1.5/-0.5/+0.5/+1.5 baud） | `dp=(itone-1.5)*dphi` | `get_fst4_bitmetrics.f90:39` |
| LLR 缩放因子 | **2.83** | 同 FT8 | `get_fst4_bitmetrics.f90:185` |
| 同步硬匹配门限 | `nsync≥16/40`，`nsync_qual≥46/80` | 两级同步质量 | `get_fst4_bitmetrics.f90:81,175` |
| BP/OSD | `maxosd=2, norder=3` | | `fst4_decode.f90:477-479` |

**FST4 各子模式参数（`fst4_decode.f90:181-216`）**，采样率固定 12000 Hz：

| 周期 ntrperiod (s) | nsps（每符号采样） | ndown（下采样） | nss（下采样后每符号采样） | 波特率 baud=12000/nsps | 符号时长 |
|---|---|---|---|---|---|
| 15 | 720 | 18 | 40 | 16.67 baud | 60 ms |
| 30 | 1680 | 42 | 40 | 7.14 baud | 140 ms |
| 60 | 3888 | 108 | 36 | 3.086 baud | 324 ms |
| 120 | 8200 | 205 | 40 | 1.463 baud | 683 ms |
| 300 | 21504 | 512 | 42 | 0.558 baud | 1.792 s |
| 900 | 66560 | 1664 | 40 | 0.180 baud | 5.55 s |
| 1800 | 134400 | 3360 | 40 | 0.0893 baud | 11.2 s |

帧持续 = 160×nsps/12000 ≈ 周期的 80–96%（如 15 s 模式帧长 9.6 s）。

### 1.3 JT9 关键参数（`lib/jt9.f90`）

| 常量 | 值 | 引用 |
|---|---|---|
| nsps（子模式 9 标准） | **6912** @12000 Hz → 符号时长 0.576 s，约 1.736 baud | `lib/jt9.f90:295` |
| kstep | nsps/2 = 3456（半符号步进做谱） | `jt9.f90:297` |
| 码型 | 卷积码 + Fano 译码（`jt9fano.f90`），非 LDPC | `lib/jt9fano.f90`、`lib/fano232.f90` |

JT9 是 1 分钟周期、慢 FSK 模式，与 FT8/FST4 的 LDPC 路线不同，MBDSDR 暂不涉及，不展开。

---

## 2. 模块 / 数据流架构

### 2.1 FT8 接收链路（分层调用）

```
音频 iwave(int16, 15*12000)  [ft8_decode.f90:60]
  │
  ├─ ft8_decode::decode  (lib/ft8_decode.f90:35)   ← 顶层模块，多 pass + 消除已译码信号
  │
  ├─► sync8(dd, npts, nfa, nfb, syncmin, ...)     [sync8.f90:1]  粗同步
  │      ├─ 逐符号段做 NFFT1=3840 r2c FFT，得符号功率谱 s(i,j)  [sync8.f90:33-43]
  │      │     i = 频率 bin (df=3.125 Hz), j = 1/4 符号时间步
  │      ├─ get_spectrum_baseline(...)  噪声底                  [sync8.f90:44]
  │      ├─ 二维搜索: 对每个频率 i、每个时间 lag j∈[-62,+62]，
  │      │     对 3 个 Costas 块累加 s(i+nfos*icos7(n), m)     [sync8.f90:62-75]
  │      │     归一化: sync = Costas功率 / (同符号7音调总功率-Costas)/6
  │      └─ 排序、去重、近 nfqso 的提权 → candidate(freq, dt, sync)
  │
  └─► 对每个候选 candidate:
        ft8b(dd, ..., f1, xdt, ...)                [ft8b.f90:1]  逐候选精处理
          ├─ ft8_downsample(dd, newdat, f1, cd0)   [ft8_downsample.f90:1]
          │     长 FFT 192000 → 搬到 f1 基带 → 切 62.5 Hz 带宽 → IFFT 3200 → 复数 200 Hz
          ├─ 细时间搜索 ±10 采样: sync8d(cd0,idt,ctwk,0,sync)  [ft8b.f90:111-117]
          │     sync8d = 7 个 Costas 音调 × 32 采样理想波形匹配滤波，3 块求和 [sync8d.f90:34-46]
          ├─ 细频率搜索 ±2.5 Hz: 旋转 ctwk 再 sync8d          [ft8b.f90:121-134]
          ├─ twkfreq1() 校正频偏，重新下采样                   [ft8b.f90:137-141]
          ├─ 再细时间 ±4 采样                                  [ft8b.f90:145-153]
          ├─ 每符号 32 点 FFT → s8(0:7,k) 8 音调能量           [ft8b.f90:155-162]
          ├─ 硬同步质量检查 nsync（21 个 Costas 硬匹配数）      [ft8b.f90:165-184]
          ├─ 生成 5 套比特度量 bmeta/bmetb/bmetc/bmetd/bmete
          │     （nsym=1/2/3 相干合并 + bit-by-bit 归一化 + 取最大）[ft8b.f90:186-241]
          ├─ normalizebmet + ×2.83 → LLR                      [ft8b.f90:243-254]
          ├─ (可选) AP 先验注入 apmask/apLLR                   [ft8b.f90:293-423]
          ├─► decode174_91(llr, Keff, maxosd, norder, apmask, message91, cw, ...)
          │     [decode174_91.f90:1]   ← 混合 BP+OSD LDPC 译码
          │     ├─ include ldpc_174_91_c_parity.f90  (Mn/Nm/nrw  Tanner 图)
          │     ├─ BP: tanh/atanh 和积, ≤30 迭代                [decode174_91.f90:52-135]
          │     ├─ 每轮检查 syndrome，全 0 则 get_crc14 校验     [decode174_91.f90:70-88]
          │     └─ BP 失败: 调 osd174_91 做 OSD（norder=2）     [decode174_91.f90:137-148]
          ├─ get_ft8_tones_from_77bits → itone，subtractft8 消信号
          └─ unpack77(c77,1,msg37,...)  解包呼号/网格          [ft8b.f90:451]
```

### 2.2 FST4 接收链路（`lib/fst4_decode.f90`）

```
iwave(30*60*12000)
  ├─ 按 ntrperiod 选 nsps/ndown/nfft1                [fst4_decode.f90:181-216]
  ├─ blanker() 噪声清除 → 一次大 FFT c_bigfft        [fst4_decode.f90:300,304]
  ├─ get_candidates_fst4(...)  粗频候选             [fst4_decode.f90:311]
  ├─ fst4_downsample(...)  每候选切 sigbw=4*baud 带宽 → c2
  ├─ fst4_sync_search(...)  细时/频同步              [fst4_decode.f90:330]
  └─ 每候选: cframe = 160*nss 采样
       ├─ get_fst4_bitmetrics(cframe, nss, bitmetrics, s4, nsync_qual, badsync)
       │     [get_fst4_bitmetrics.f90:1]
       │     ├─ 每符号与 4 个理想 CW 波形 ci 做相关 → s4(0:3,k)
       │     ├─ 5 个同步块硬匹配检查 (≥16/40, ≥46/80)
       │     └─ 8 符号块内做 1/2/4/8 符号相干级联相关 → 4 套 LLR
       ├─ llrs(1:240, 1:4) = bitmetrics 重排（跳同步位置） [fst4_decode.f90:411-416]
       └─ decode240_101 / decode240_74 (BP+OSD, maxosd=2, norder=3)
            → mod(message101(1:77)+rvec,2)  → unpack77
```

---

## 3. 关键算法细节

### 3.1 同步怎么做

**FT8 粗同步（`sync8.f90`）—— Costas 阵列的功率谱相关，不是时域匹配滤波：**
- 先把 12 kHz 实信号按 NSTEP=480 采样（1/4 符号）切成段，每段 1920 采样补零到 3840 点 FFT，得到时频图 `s(i,j)`（`sync8.f90:33-43`）。
- 对每个频率 bin `i` 和每个时间 lag `j`，在 3 个 Costas 块（符号偏移 0、36、72）上，把"应该出现的音调" `i+nfos*icos7(n)` 处的功率累加（`sync8.f90:62-75`）。
- 归一化：`sync = Costas功率 / (同符号 7 个音调总功率 - Costas功率)/6`，即"信号功率比背景旁瓣高多少倍"（`sync8.f90:77-83`）。
- 这是**频率×时间二维搜索**：频率轴从 `nfa` 扫到 `nfb`（df=3.125 Hz），时间轴 lag ∈ [-62,+62]（±2.5 s 内，1/4 符号步进）。

**FT8 细同步（`sync8d.f90`）—— 时域匹配滤波：**
- 预生成 7 个音调各 32 采样的理想 CW 波形 `csync(i,j)`（`sync8d.f90:22-29`）。
- 对给定时间起点 `i0`，在 3 个 Costas 块（偏移 0、36×32、72×32 采样）上分别做复相关，模平方求和（`sync8d.f90:34-46`）。
- 在 `ft8b.f90` 里用这个函数做 ±10 采样时间精搜和 ±2.5 Hz 频率精搜（`ft8b.f90:111,129`）。

**FST4 同步**：粗同步在 `fst4_decode.f90` 内（`get_candidates_fst4` + `fst4_sync_search`），细同步用 5 个 8 符号同步字 `isyncword1/2` 做硬匹配（`get_fst4_bitmetrics.f90:67-84`），再用 80 bit 同步位级联相关做二次质量门限（`get_fst4_bitmetrics.f90:168-178`）。

### 3.2 LDPC 译码器用的什么算法

**不是纯 BP，也不是纯 OSD，是混合 BP + OSD：**
- **BP 阶段**（`decode174_91.f90:52-135`）：标准和积算法（Sum-Product），check 节点用 `tanh(-toc/2)` 乘积 + `platanh`（即 `atanh`）（`decode174_91.f90:122-131`），**不是 min-sum**。最多 30 次迭代。每轮硬判决后算 syndrome（`decode174_91.f90:70-73`），全 0 就立刻用 CRC14 校验，通过则返回。
- **早停**：连续 5 轮不可满足校验数不下降就放弃（`decode174_91.f90:91-104`）。
- **OSD 阶段**（`decode174_91.f90:137-148`）：BP 失败后，取 BP 过程中保存的 LLR（`zsave`），调 `osd174_91`（Ordered Statistics Decoding，`osd174_91.f90`），阶数 `norder=2`，最多试 `maxosd=2` 次。
- **Tanner 图结构**：每个变量节点连 **3** 个校验（`Mn(3,N)`，`decode174_91.f90:16`），每个校验最多连 **7** 个变量（`Nm(7,M)`）。这是一个 (174,91) 不规则 LDPC，M=83 个校验。
- FST4 的 `decode240_101` / `decode240_74` 是同款混合 BP+OSD（`fst4_decode.f90:481,496`，`maxosd=2, norder=3`）。

### 3.3 频率/时间二维搜索怎么做

两级：
1. **粗搜**（`sync8.f90`）：全带宽 × ±2.5 s 时间窗，Costas 功率相关，输出候选列表（频、时、sync 强度）。
2. **细搜**（`ft8b.f90`）：对每个候选，先 ±10 采样（±50 ms）时间扫，再 ±2.5 Hz 频率扫，用 `sync8d` 匹配滤波峰值定位；频偏用 `twkfreq1` 校正后重下采样，再 ±4 采样精修时间（`ft8b.f90:111-153`）。

---

## 4. 对照 MBDSDR 现有文件的勘误

> 路径：`mbdsdr_ai/ft8_decode.py`、`ft8_ldpc.py`、`ft8_unpack.py`、`ft8_callsign.py`、`fst4_ldpc.py`。
> 结论先行：**符号时长/波特率/CRC/同步字/帧结构这些"硬常量"基本都对**；主要差距在 (a) LDPC 译码器强度、(b) FST4 缺 rvec 扰码、(c) LLR 度量只用了 nsym=1。

### 4.1 `ft8_decode.py`

| 位置 | MBDSDR 现状 | WSJT-X 真相 | 判定 |
|---|---|---|---|
| L20 `ICOS7=[3,1,4,0,6,5,2]` | 同 | `genft8.f90:14` | ✅ 正确 |
| L15 同步块 0-6/36-42/72-78 | 同 | `genft8.f90:33-35`（1-based 1-7/37-43/73-79） | ✅ 正确 |
| L40 `_GRAYMAP=[0,1,3,2,5,6,4,7]` | 同 | `genft8.f90:15` | ✅ 正确 |
| L65 bit 权重 `(4,2,1)` 顺序 | MSB→LSB | `genft8.f90:41` `indx=codeword(i)*4+codeword(i+1)*2+codeword(i+2)` | ✅ 正确，bit 顺序与 codeword 分组一致 |
| L119 `_CRC_P=[1,1,0,0,1,1,1,0,1,0,1,0,1,1,1]` | 同 | `get_crc14.f90:12` | ✅ 正确（=0x6757） |
| L128 CRC 输入 `77+5+14=96` | 5 个零填充 | `encode174_91.f90:39,43` + `decode174_91.f90:76-77`（m96(83:96)=cw(78:91)，中间 78:82 为 5 零） | ✅ 正确 |
| L72-79 `reorder_to_ldpc` 恒等映射 | 注释说 (174,91) 无 colorder 置换 | `encode174_91.f90:54-55` 直接 `codeword(1:K)=message; codeword(K+1:N)=pchecks`，确实不用 colorder | ✅ 正确。`ldpc_174_91_c_colorder.f90` 虽存在，但全仓库仅被旧 `encode174.f90`(174,87) 和 `ft8var/` 引用，(174,91) 主链路不用（已 grep 确认） |
| L51-69 `soft_tones_to_llr` | 只做 nsym=1 的 max-log LLR | `ft8b.f90:186-234` 还做 nsym=2（相邻 2 符号相干合并）、nsym=3（相邻 3 符号），再取 5 套里绝对值最大者 `bmete`（`ft8b.f90:235-241`） | ⚠️ **不是错，是弱**。MBDSDR 只算 nsym=1，抗噪声/衰落能力弱。应补 nsym=2/3 的符号合并度量（`ft8b.f90:199-202`：`s2=abs(cs(graymap(i2),ks)+cs(graymap(i3),ks+1))` 等） |
| L95 直接 `ft8_ldpc.ldpc_bp_decode` | 无 AP、无 OSD | `ft8b.f90:427-438` BP 后还有 OSD（norder=2），以及 AP 先验 pass | ⚠️ 弱，见 4.2 |
| 文档串 L4 同步块 | "0-6、36-42、72-78" | 同 | ✅ |

**没有发现符号时长/波特率/音调数/CRC 宽度/k/同步字写错。** MBDSDR 把 160 ms、6.25 baud、8 音调、CRC14、N=174/K=91 这些硬常量都写对了（虽然这些值在 .py 里没硬编码成常量，而是隐含在 58×3=174、8 路能量里）。

### 4.2 `ft8_ldpc.py`

| 位置 | MBDSDR 现状 | WSJT-X 真相 | 判定 |
|---|---|---|---|
| L16-18 N=174,K=91,M=83 | 同 | `decode174_91.f90:10` | ✅ |
| L57-58 每变量 3 check | `Mn(3,N)` | `decode174_91.f90:16` `integer Mn(3,N)` | ✅ |
| L93 `r = prod_sign*min_abs*0.75` | **归一化 min-sum**，衰减 0.75 | `decode174_91.f90:122-131` 是**精确和积**：`tanhtoc=tanh(-toc/2)`，check 节点做乘积再 `platanh`(atanh)，无 0.75 衰减 | ⚠️ 算法近似。min-sum+0.75 能用但迭代收敛门限比 tanh 和积差；强信号没问题，弱信号会漏。建议改 tanh/atanh 和积（参考 `decode174_91.f90:120-133`） |
| L67 `max_iter=25` | 25 次 | `decode174_91.f90:27` `maxiterations=30` | ⚠️ 差 5 次，影响不大但建议对齐 30 |
| 无 OSD | 纯 BP | `decode174_91.f90:137-148` BP 失败后调 `osd174_91`（`osd174_91.f90`，norder=2） | ❌ **缺失**。这是 FT8 能在 -20 dB SNR 译码的关键。纯 BP 在中等 SNR 失败后就放弃，WSJT-X 会用 OSD 再救。建议至少补一个简单 OSD 或 Ordered Statistics 重试 |
| 无早停/无 syndrome 计数 | L101 有 syndrome 早停 | ✅ 这一点 MBDSDR 做了 | ✅ |
| L22-33 `get_colorder` | 读了 colorder 文件但没人用 | (174,91) 主链路不用 | ✅ 无害，但死代码 |

### 4.3 `ft8_unpack.py`

| 位置 | MBDSDR 现状 | WSJT-X 真相 | 判定 |
|---|---|---|---|
| L53-54 `n3=bits[71:74], i3=bits[74:77]` | 0-based 71..73 / 74..76 | `ft8b.f90:447-448` `read(c77(72:74))n3; read(c77(75:77))i3`（1-based） | ✅ 对齐正确（0-based 71 = 1-based 72） |
| L61-66 Type1 位段：n28a=0:28, ipa=b[28], n28b=29:57, ipb=b[57], ir=b[58], igrid4=59:74 | | 与 FT8 标准打包一致（call1 28bit + 1 bit hash, call2 28bit + 1 bit hash, R 1 bit, grid/report 15 bit） | ✅ 基本正确 |
| L15 字符表 `" 012.../"` 40 字符 | Type0.0 自由文本 6bit/base40 | WSJT-X `packjt77` 里 base-40 字符表一致 | ✅ |
| 只覆盖 free_text / Type1 | | WSJT-X 还有 i3=2/3/4/5、n3=0..6 等 contest/FD/telemetry 分支 | ⚠️ 已知未覆盖，非错误 |

### 4.4 `ft8_callsign.py`

| 位置 | MBDSDR 现状 | WSJT-X 真相 | 判定 |
|---|---|---|---|
| L12 `NTOKENS=2063592`、L13 `MAX22=4194304` | 呼号 token 编码阈值 | 与 WSJT-X `packjt77`/`unpack28` 的 token 分区一致（DE/QRZ/CQ=0,1,2；CQ_nnn 3..1002；CQ_aaaa 1003..532443；22bit hash；标准呼号） | ✅ 分区正确 |
| L15-18 字符表 C1/C2/C3/C4 | 36/36/10/27 | 与 unpack28 字符集一致 | ✅ |
| 22bit hash 段返回 `<hash:n>` | 不查呼号表 | WSJT-X 用 `worked_before` 列表查表 | ✅ 合理降级 |

### 4.5 `fst4_ldpc.py`（**重点问题**）

| 位置 | MBDSDR 现状 | WSJT-X 真相 | 判定 |
|---|---|---|---|
| L13 `_N=240`、解析 `ldpc_240_101_parity.f90` | LDPC 图 | `encode240_101.f90:6` N=240,K=101 | ✅ 图解析对 |
| L59 min-sum ×0.75、无 OSD | 同 FT8 | `fst4_decode.f90:481` 调 `decode240_101`，内部 BP+OSD(maxosd=2,norder=3) | ⚠️ 同 4.2，弱 |
| **完全没有 rvec 扰码** | 无 | `genfst4.f90:63` 编码前 `msgbits(1:77)=mod(msgbits(1:77)+rvec,2)`；译码侧 `fst4_decode.f90:488` `mod(message101(1:77)+rvec,2)` 还原 | ❌ **会错**。FST4 的 77 个信息位在 LDPC 之前先与固定 77-bit `rvec`（`genfst4.f90:29-31`）异或。MBDSDR 解出 message101 后必须 `bits ^= rvec` 再 unpack77，否则解出来全是乱码。rvec 具体值见 `genfst4.f90:29-31`（也在 `fst4_decode.f90:80-82` 重复一份） |
| 无 FST4W (240,74) 分支 | 只做 240,101 | `genfst4.f90:82` FST4W 调 `encode240_74`，译码 `decode240_74` | ⚠️ FST4W 未实现，非错误 |
| 无同步字/帧结构 | LDPC 模块不管 | `isyncword1=[0,1,3,2,1,0,2,3]`、`isyncword2=[2,3,1,0,3,2,0,1]`，帧 s8d30×5 | ⚠️ 后续接解调时要用这两个同步字，别照抄 FT8 的 Costas-7 |
| 无 CRC24 | LDPC 模块不管 | CRC24 多项式 0x100065B（`get_crc24.f90:12`），作用域 101 bit | ⚠️ 后续解包前要加 CRC24 校验 |

---

## 5. 给 MBDSDR 的可迁移清单（按优先级）

1. **[必修] FST4 加 rvec 扰码还原**：解出 message101[0:77] 后，逐位异或 `rvec = [0,1,0,0,1,0,1,0,0,1,0,1,1,1,1,0,1,0,0,0,1,0,0,1,1,0,1,1,0,1,0,0,1,0,1,1,0,0,0,0,1,0,0,0,1,0,1,0,0,1,1,1,1,0,0,1,0,1,0,1,0,1,0,1,1,0,1,1,1,1,1,0,0,0,1,0,1]`（`genfst4.f90:29-31`），再进 unpack77。
2. **[建议] LDPC 译码器从 min-sum 换成 tanh/atanh 和积**（参考 `decode174_91.f90:120-133`），并把迭代数从 25 提到 30；弱信号漏检会明显改善。
3. **[建议] 加 OSD 后处理**：BP 失败后对 LLR 排序做 Ordered Statistics Decoding（参考 `osd174_91.f90`，FT8 norder=2，FST4 norder=3）。这是 WSJT-X 压到 -20 dB SNR 的关键。
4. **[建议] FT8 比特度量补 nsym=2/nsym=3 相干合并**（`ft8b.f90:199-202`），不要只用独立符号的 max-log。
5. **[可选] AP 先验译码**：已知 mycall/hiscall 时往 LLR 注入先验（`ft8b.f90:322-422`），对 contest/近场信号增益大。
6. **[无需改]** 符号时长 160 ms、6.25 baud、8 音调、CRC14=0x6757、N=174/K=91、Costas-7=[3,1,4,0,6,5,2]、Gray=[0,1,3,2,5,6,4,7]、S7D29S7D29S7——MBDSDR 这些都对。

---

## 6. 关键常量速查（笔记引用清单）

- FT8：KK=91（`ft8_params.f90:2`）、ND=58（:3）、NS=21（:4）、NN=79（:5）、NSPS=1920（:6）、NDOWN=60（:12）、NSTEP=480（:10）
- FT8 LDPC：N=174,K=91,M=83（`encode174_91.f90:9`），每 bit 3 check、每 check ≤7 bit（`decode174_91.f90:16-17`）
- FT8 CRC14：poly 0x6757（`get_crc14.f90:11-12`），96-bit 作用域（`encode174_91.f90:39-43`）
- FT8 Costas-7：[3,1,4,0,6,5,2]（`genft8.f90:14`）
- FT8 Gray：[0,1,3,2,5,6,4,7]（`genft8.f90:15`）
- FT8 帧：S7 D29 S7 D29 S7，块间隔 36 符号（`genft8.f90:32-35`；`sync8.f90:68,71`）
- FT8 LLR scale=2.83（`ft8b.f90:249`），BP iter=30（`decode174_91.f90:27`），OSD norder=2（`ft8b.f90:427`）
- FST4：KK=77（`fst4_params.f90:4`）、ND=120（:5）、NS=40（:6）、NN=160（:7）
- FST4 LDPC：(240,101)（`encode240_101.f90:6`）；FST4W (240,74)（`genfst4.f90:9,82`）
- FST4 CRC24：poly 0x100065B（`get_crc24.f90:11-12`）
- FST4 同步字：[0,1,3,2,1,0,2,3] / [2,3,1,0,3,2,0,1]（`genfst4.f90:27-28`）
- FST4 Gray：[0,1,3,2]（`get_fst4_bitmetrics.f90:22`）
- FST4 rvec 扰码：`genfst4.f90:29-31`
- FST4 子模式 nsps：15s→720, 30s→1680, 60s→3888, 120s→8200, 300s→21504, 900s→66560, 1800s→134400（`fst4_decode.f90:181-215`）
- JT9：nsps=6912（`jt9.f90:295`），卷积码+Fano（非 LDPC）
