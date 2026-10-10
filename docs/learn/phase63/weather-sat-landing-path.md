# 气象卫星数字云图落地路径方案（LRPT 优先，MBDSDR 内部）

> 日期：2026-10-10  作者：MBDSDR 工程轮（phase63）
> 配套文档：`weather-sat-digital-study.md`（机制学习，干净室）
> 约束：本轮**只设计方案，不写 cpp 代码**。下列伪代码仅为接口签名示意，
> 不进 `cpp/src/` 或 `cpp/tests/`。后续轮在不参考 GPL 源码文本的前提下自写。
> 目标缺口：补齐 P2 中气象卫星**数字**云图（LRPT 为主，HRPT/LRIT 后续）。

---

## 0. 设计原则

1. **Python 原型优先**：LRPT 物理链（解调+同步+Viterbi+RS）先用 numpy/scipy
   在 `experiments/` 跑通并验证参数，再决定是否下沉 C++。与 FT8 落地路径
   同一打法。
2. **LRPT 先行**：LRPT（Meteor-M，72 ksym/s QPSK）带宽适中、消费级 SDR 可收，
   是数字云图性价比最高的入口。HRPT（裸 BPSK）、LRIT/HRIT（GOES，L 波段）
   物理层与 LRPT 同宗，后续复用工件。
3. **复用既有 APT 后处理**：数字解出的图像 ndarray 直接喂 `image_enhance.py`
   的 `enhance_apt()`，不另写一套增强。
4. **诚实空态**：未同步 / Viterbi 失锁 / RS 不可纠时，状态字段报默认空值，
   不伪造图像、不伪造 SNR。
5. **不预置呼号/电台**：云图产品无呼号概念；合成测试用占位数据。

---

## 1. 总体链路（自写，机制见 study 笔记 §2/§4）

```
复基带 IQ (source)
  → AGC
  → RRC 匹配滤波 (alpha=0.6, 4×过采样)
  → Gardner 定时恢复 (每符号抽 1 软 I/Q)
  → Costas 载波恢复 (QPSK tanh 误差 / BPSK I·Q 误差)
  → 64-bit 编码后同步 (8 相位模糊搜索, 阈值 45/64)
  → 相位/swap 旋转解模糊
  → Viterbi K=7 r=1/2 软判决
  → (HRIT) NRZ-M 差分解码
  → CCSDS 解扰 (PN x^8+x^7+x^5+x^3+1, init 0xff, 1020B 表)
  → 4 路交织 RS(255,223) 解交织 + 纠错
  → [锁帧判据: 4 RS 块全可纠]
  → CADU/VCDU → (后期) 虚拟信道解复用 → 图像段重组 → ndarray
  → image_enhance.enhance_apt() → 显示/存 PNG
```

---

## 2. 分轮拆解（Python 原型优先，4 轮）

### 轮次 1：QPSK/BPSK 解调 + 帧同步（Python 原型 + 单测）

**范围**（`experiments/weather_digital/`，纯 numpy/scipy）：
- `demod_qpsk.py`：AGC → RRC 匹配滤波 → Gardner TED → Costas 载波环，
  输出每符号软 I/Q int8 量级。机制参照 meteor_demod `demod.c:192-204`。
- `sync_correlator.py`：64-bit 滑窗汉明相关，8 重相位/IQ-swap 假设，
  阈值 45/64。机制参照 SatDump `correlator.cpp:135-174`。
- `synth_lrpt.py`：固定种子合成 LRPT QPSK 信号（含可注入频偏/时偏/AWGN）。

**验收**：
- 无噪合成 → 解调输出星座图贴 4 象限，Costas 锁定，同步召回 100%
  （100 次随机频偏 ±5 kHz / 时偏全找回）。
- 同步报出的 phase/swap 与合成注入一致；解模糊后符号无象限反转。
- 不写 cpp。

### 轮次 2：RS(223,255) + 卷积 Viterbi 解码（Python 原型）

**范围**：
- `viterbi_k7.py`：K=7 r=1/2，多项式 {79,109}，64 状态 ACS + 回溯，
  软判决输入。机制参照 `viterbi27.h:8`（多项式为公开 CCSDS 事实）。
- `rs_ccsds.py`：RS(255,223)，GF(256) CCSDS 本原多项式，首根 112/index 11，
  4 路解交织。**优先评估引入 MIT 许可的 `libcorrect` 或自写 BM+Chien**；
  对偶基变换按 CCSDS tal 矩阵自述重写。
- `derand.py`：PN LFSR 1020 字节表异或。

**验收**：
- 无噪端到端：合成 → 解调 → 同步 → Viterbi → 解扰 → RS，能还原注入的
  已知伪 CADU（逐字节比对）。
- 加噪扫 SNR：画 **BER vs Eb/N0** 曲线，50% 可纠门限落在 CCSDS 理论量级
  （r=1/2 K=7 + RS(255,223) 约 ~2-3 dB Eb/N0 量级）。
- RS 误纠率：纯噪声注入 1000 次，误判为"可纠" <1%。

### 轮次 3：LRPT 帧/包重组 + 三通道工具（写工具手动模式 gate，诚实空态）

**范围**：
- `cadu_reassemble.py`：CADU（1ACFFC1D + 1020B）流 → VCDU 头解析 →
  （Meteor-M）MSU-MR 段按段号拼回 2D ndarray。本期先打通"锁帧后能吐出
  一块对齐的 ndarray"，图像解压（Huffman/IDCT）放后期。
- 三通道工具候选（参照 FT8 `set_ft8`/`get_ft8_status` 模式）：
  - `set_lrpt(enabled: bool, gate_audio: bool=False)`：写工具，开/关 LRPT 解码。
    **手动模式 gate**：被动监听，不发射、不 PTT、不自动切频率；mode 由用户
    在 UI 选 `"LRPT"` 或 `set_mode` 切。enabled 缺失/非 bool → errResult，
    不静默 toggle。
  - `get_lrpt_status()`：读工具，返回
    `{enabled, active, costas_lock:bool, viterbi_ber:float, rs_avg:int,
     frames:int, image_preview:ndarray|null}`。
    **诚实空态**：无信号时 `active:false`、`image_preview:null`、
    `viterbi_ber` 报 NaN/空，不伪造图像与 SNR。

**验收**：
- 合成信号下 `set_lrpt(enabled=true)` → `get_lrpt_status()` 看到
  `costas_lock:true`、`frames` 递增；关闭后 `active:false`、空态。
- 无信号诚实空态，无伪造图像。

### 轮次 4：C++ 接入 + 三通道云图 + 论文实验

**范围**：
- C++ 把轮次 1-2 的 Python 算法移植成 streaming `cpp/src/dsp/lrpt_decoder.*`
  （不抄 GPL C++，按机制笔记重写）。
- `vfo_manager.cpp` 新增 `isLrpt()` 分支（参照 FT8 落地路径 §1.1 结构），
  通道带宽按 72 ksym/s × 1.6 ≈ 120-150 kHz 切。
- 解出 ndarray → 接 `image_enhance.enhance_apt()` 统一后处理 → 存 PNG。
- `experiments/` 接入弱信号实验（见 §3）。

**验收**：
- ctest 不回归；合成往返无噪 100%。
- `set_lrpt`/`get_lrpt_status` 三通道闭环。
- 数字云图与 APT 云图共用 `enhance_apt()`，后处理一致。

---

## 3. 论文实验衔接点（复用既有框架）

### 3.1 复用既有资产

- `experiments/common/runner.py`：`FixedSeed(seed)` 用 blake2b(seed,tag) 派生
  子种子（runner.py:102-114），`wilson_ci(k,n,z=1.96)`（runner.py:33）——
  **固定种子 Monte-Carlo + Wilson 95% CI 框架直接复用**。
- `experiments/common/ebno.py`：`snr_db_to_ebn0_db()` 与 `MODE_TABLE`。
  需在 `MODE_TABLE` 新增一行：
  ```
  "lrpt_qpsk_72k": {
      "label": "Meteor-M LRPT QPSK 72 ksym/s",
      "rb": 144000.0,         # QPSK 2 bit/symbol × 72k
      "b": <复基带 fs, 建议 ~1.5MHz>,
      "b_source": "main.c:19 SYM_RATE=72000; study 笔记 §2.1",
      "reports_ebn0": True,
  }
  ```

### 3.2 可做的实验点

1. **BER / 弱信号 SNR 曲线**：合成 LRPT 注入 AWGN，扫 Eb/N0 -5..+10 dB，
   画 (a) Viterbi 后 BER、(b) RS 后帧成功率，带 Wilson CI 误差棒。这是
   论文新图，与现有 BPSK/QPSK 实验并列。
2. **同步捕获范围**：注入 ±0..±10 kHz 频偏，测 Costas + 相关同步成功率。
3. **软判决 vs 硬判决**：Viterbi 软 LLR 路径 vs 硬判决的 RS 后帧成功率对比
   ——呼应 phase55 "软判决增益" 叙事。
4. **RS 交织增益**：4 路交织 vs 无交织在突发误码下的帧成功率对比。

### 3.3 衔接方式

- 合成器（轮次 1）输出 IQ 后既喂 C++ 也可导出喂 Python runner；共用同一份
  参数表（study 笔记 §7 速查表）。
- 产物命名遵循 phase7 paper 约定：`lrpt_ber_vs_ebno__synthetic__N<N>__<UTC>.csv/.png`。
- **不引入 OTA 声明**：数字全来自合成信号，paper 标注 `data-origin: synthetic`。

---

## 4. 与 image_enhance.py APT 管线的接合点

| 现有件 | 接合方式 |
|--------|---------|
| `image_enhance.py:261 enhance_apt()` | LRPT 数字解码吐 2D ndarray (H,W) uint8 → 直接 `enhance_apt(img, lut='jet'/'iron')`，与 APT 云图共用 auto_stretch + LUT。 |
| `image_enhance.py:307 save_enhanced_png()` | 数字云图同样用它存 PNG。 |
| `noaa_apt_lite.py`（模拟 APT） | 不替换、不修改；数字 LRPT 是并列的第二条云图入口。 |

**结论**：`image_enhance.py` 设计为"吃 ndarray 的纯函数"，正是数字云图与模拟
APT 汇合的天然接缝——数字链路只需负责"把软符号还原成 ndarray"，之后零改动复用。

---

## 5. HRPT / LRIT / HRIT 的后续占位（本轮不展开）

- **HRPT**：物理层更简单（无 FEC，纯 60-bit 同步 + 10-bit 字切分，
  `noaa_deframer.cpp:13-110`），但要高增益天线、665 kbps BPSK。等 LRPT
  跑通后，解调侧复用 Costas（BPSK 误差检测器 `costas.cc:124` 比 QPSK 更简单），
  解链侧**跳过 Viterbi/RS** 直接做同步成帧——是 LRPT 链路的"减法版"。
- **LRIT/HRIT（GOES）**：物理层与 LRPT **同宗**（同 K7 r1/2、同 RS(255,223)×4、
  同 ASM 0x1ACFFC1D，见 study §4），只需换 BPSK 解调 + 符号率（293883/927000）
  + xRIT 文件分发。Viterbi/RS/解扰工件可直接复用，边际成本低。
- 本轮只落地 LRPT，后两者写方案不写码。

---

## 6. 红线与纪律

- **干净室**：MBDSDR 内所有数字云图代码由后续轮自写，不复制 SatDump/
  meteor_demod/goestools 的 C/C++ 文本；本方案的 file:line 仅作机制证据。
- **不预置呼号/电台**：云图无呼号；合成用占位。
- **不发射**：全是被动接收，不接 PTT，不做 Tx。
- **不 git add/commit/push**：本轮及后续轮只写文件，git 由人工触发。
- **隔离文件勿动**：`cpp/*`、`ui_diag_freeze.cpp`、`cpp/scratch/*` 本轮不碰。
- **GPL 措辞专业中立**：不出现"比赛/competition"字样；频率/符号率等
  活动参数只进 docs，不进 cpp 默认配置；产物带 MIT 头。
- **不落大块代码**：本方案内仅伪代码签名；真实实现留待后续轮。

---

## 7. 诚实未完成项

1. **VCDU → 图像段重组细节**：CADU 之后的虚拟信道解复用、MSU-MR 段号拼回、
   Huffman/IDCT 解压本轮未逐行读码，列为轮次 3 后期/轮次 4。
2. **xRIT 文件格式**：GOES LRIT/HRIT 的文件头字段、段重组、WT 压缩属应用层，
   本轮只确认"有文件分发协议"，未展开。
3. **libcorrect 是否引入**：RS 是自写 BM+Chien 还是直接用 MIT 的 libcorrect，
   轮次 2 决策（需确认 libcorrect 许可与体积）。
4. **C++ 接入细节**：vfo_manager 分支、通道带宽常量名、SpectrumEngine 访问器
   签名，留到轮次 4 对照 FT8 接入样板再定。
5. **OTA 实测**：本轮全合成验证，真实天线/天空接收不在本轮范围。
