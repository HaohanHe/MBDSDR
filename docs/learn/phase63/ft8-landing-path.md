# FT8 落地路径方案（MBDSDR 内部）

> 日期：2026-10-10  作者：MBDSDR 工程轮
> HEAD：MBDSDR @ 764866a
> 配套文档：`ft8-mechanism-study.md`（机制学习，干净室）
> 约束：本轮**只设计方案，不写 cpp 代码**。下列伪代码仅为接口签名示意，
> 不得进 `cpp/src/` 或 `cpp/tests/`。后续轮在不参考 GPL 源码文本的前提下自写。

---

## 0. 设计原则

1. **复用既有数字模式分支结构**：FT8 不是新架构，而是 `vfo_manager.cpp`
   里第 N 个数字模式分支（POCSAG/ACARS/NAVTEX/m17/VOR 之后）。
2. **窄带优先**：FT8 信号只占 ~62.5 Hz，channelizer 切一个 ~500 Hz–1 kHz
   通道即可，不需要 12 kHz 宽通道。
3. **合成测试先行**：先有确定性 FT8 合成器，再写解码器，最后接三通道。
4. **诚实空态**：无信号/未同步/CRC 失败时，所有字段读默认空值，不伪造
   呼号/网格/SNR。
5. **不预置呼号**：合成器用 `CALL1/CALL2` 占位符；真实接收时字段由
   解码填充，无解码则空。

---

## 1. 解调链接入点

### 1.1 vfo_manager 新分支 `isFt8()`

参照 `vfo_manager.cpp:209-235`（NAVTEX 分支）的结构，新增：

```
// ---- FT8 (8-FSK, 6.25 Hz tone spacing, 15s T/R slot) ----------------
if (isFt8()) {
    ifTarget = 48000.0;           // 与其它数字模式一致，48k IF
    chBw     = core::kBwFt8Hz;    // 新常量，建议 1000 Hz（见 §1.2）
    channelizer.configure(sr, ifTarget, chBw, 31);
    channelizer.setVfoOffsetHz(freqHz - sourceCenterHz);
    const double ifRate = channelizer.effectiveOutputRateHz();  // 48000
    ft8 = std::make_unique<Ft8Decoder>(ifRate);  // 新类，见 §1.3
    // 复位其它解码器（同 NAVTEX 分支样板）...
    resampler.configure(ifRate, 48000.0, 31);
    needsRebuild = false;
    return;
}
```

**mode 字符串**：`"FT8"`（加入 `bandwidth_preset.h:defaultBandwidthHzForMode`
与 `vfo_manager::isFt8()` 判断）。

### 1.2 通道带宽决策

FT8 信号本体 62.5 Hz，但频偏搜索范围需要留余量：
- wsjtx `sync8.f90:46-47` 的 `nfa..nfb` 由调用者给，典型 ±2 kHz；
- MBDSDR 的 VFO 已经把用户中心频率对准信号，channelizer 只需切出
  ±500 Hz 通道（1 kHz 总带宽）即可覆盖 ±几 kHz 频偏 + 搜索余量。
- **建议 `kBwFt8Hz = 1000.0`**（在 `bandwidth_preset.h` 新增）。
  理由：channelizer 48 kHz → 1 kHz 通道是 48 倍整降，既有 rational
  resampler 路径可走；62.5 Hz 信号在 1 kHz 通道里只占 6%，但 Costas
  搜索本来就在符号谱上做频偏维扫描，通道宽一点对搜索有利。

### 1.3 Ft8Decoder 类接口（自写，非抄码）

```
namespace mbdsdr { namespace dsp {

class Ft8Decoder {
public:
    explicit Ft8Decoder(double sampleRateHz);   // 48000 Hz IF
    void reset();
    // 喂入 channelizer 输出的 baseband IQ（48k S/s，complex float）
    // 解码器内部攒 15 s 窗（720000 样本），对齐 UTC 秒边界后做一次解码。
    void process(const std::complex<float>* iq, int n);

    // 最近一窗的解码结果（多信号候选列表）
    struct Candidate {
        double freqOffsetHz;     // 相对 VFO 中心的频偏
        double timeOffsetSec;    // 相对 UTC 秒边界的时偏
        double snrDb;            // 估算 SNR（Costas 峰归一化值）
        QString decodedText;     // 成功解码文本（"CQ CALL1 GRID" 等），空串=未解码
    };
    std::vector<Candidate> takeResults();   // 移动出结果，清空内部队列

    bool enabled() const;
    void setEnabled(bool on);
};

}}
```

**内部流水线（自写，机制见 `ft8-mechanism-study.md` §5）**：
1. 15 s 窗攒齐后，做一次 720000→匹配长度的 FFT，切 ~62.5 Hz 通带，
   重采样到 200 S/s（32 sps）——对应 wsjtx `ft8_downsample.f90` 机制。
2. 在 48k 原始音频上每 0.04 s（1/4 符号）做 3840 点 FFT，构造符号谱
   `s(f,t)`——对应 `sync8.f90:28-43`。
3. 频偏×时偏二维 Costas 相关搜索，取峰——对应 `sync8.f90:54-85`。
4. 对每个候选峰，提取 79 个符号窗的 8-tone 功率，转 3-bit LLR。
5. log-domain BP 解码 LDPC(174,91)，CRC14 早停。
6. 成功则 unpack 77-bit → 文本（标准消息类型 i5bit=0 先做，自由文本
   后续轮再加）。

---

## 2. 合成测试数据生成器设计

### 2.1 位置与模式

扩展既有 `cpp/src/dsp/test_signal.h` 的 `TestSignalSource`，新增一种
`modulation = "ft8"`。参照现有 `"tone"/"am"/"fm"/"bpsk"/"qpsk"` 模式
（test_signal.h:5-8）。

### 2.2 生成器流程（自写）

```
// 输入：固定种子 + 可选消息文本（默认 "CQ CALL1 GRID"）
// 输出：在 fs_（默认 2.4 MHz）上叠加一个 FT8 信号 IQ

1. pack77(msg) -> 77 bit            // 自写最小版：只支持 CQ/CALL+GRID 格式
2. crc14(msg77) -> 91 bit           // poly 0x6757（公开协议参数）
3. ldpc_encode(91) -> 174 bit      // 用 §3 轮写的生成矩阵
4. 每 3 bit -> graymap[indx] -> tone 0..7
5. 按 S7 D29 S7 D29 S7 帧布局填 itone[79]
6. 对每个符号 k：
     f_tone = f_center + (itone[k]-3.5) * 6.25 Hz   // 8 tone 居中
     生成 0.16 s 的 CW 段（continuous phase）
7. 15 s 窗 = 79 符号 × 0.16 s + 末尾补零到 15 s
8. 重复播放，每 15 s 对齐 UTC 秒边界（本地时钟即可）
```

### 2.3 可测试性要求

- **确定性**：固定种子 → 固定 IQ 波形，两次运行字节级一致。
- **可注入频偏/时偏/噪声**：
  - `setFt8FreqOffsetHz(double)`：VFO 中心与信号中心的偏差（测试 Costas
    频偏捕获范围）。
  - `setFt8TimeOffsetSec(double)`：信号起始相对 15 s 边界的偏移（测试
    时偏捕获 ±2.5 s）。
  - `setFt8NoiseSigma(double)`：AWGN 噪声强度（测试弱信号 SNR 门限）。
- **默认关闭**：`modulation_` 默认 `"tone"`，FT8 模式需显式 opt-in，
  与 `fmStereo_` 的开关哲学一致（test_signal.h:45-53）。

### 2.4 与既有 TestSignalSource 的关系

不替换现有模式，只新增 `"ft8"` 分支。`rebuildDigitalSymbols()` 类似
地新增 `rebuildFt8Symbols()`，预生成一个 15 s 循环的复数波形数组，
`readIQ()` 按相位游标循环播放。

---

## 3. 三通道工具规划

参照 `agent_tools.cpp:813-939` 的 CTCSS/CDCSS set/get 双工具模式。

### 3.1 `set_ft8`（写工具）

```
签名：set_ft8(enabled: bool [必填], gate_audio: bool [可选])
语义：
  - enabled 缺失/非 bool -> errResult（不静默 toggle，对齐 execSetCtcss:824-826）
  - enabled=true  -> engine->setFt8Enabled(true)，Ft8Decoder 开始 process
  - enabled=false -> engine->setFt8Enabled(false)，停止解码，清空结果队列
  - gate_audio 可选（FT8 是数据模式，本来就没音频；保留字段为未来
    "解码命中时点亮指示器"预留，本轮默认 false 且不接 UI）
成功返回：
  { ok:true, enabled:bool, gate_audio:bool, message:"FT8 解码已开启/关闭" }
```

**写工具手动模式 gate 原则**：FT8 是被动监听模式，不发射、不 PTT。
`set_ft8(enabled=true)` 只开解码器，不改变 VFO 频率、不切换 mode——
mode 由用户在 UI 选 `"FT8"` 或 `set_mode` 工具显式切。这样 AI 不会因为
开了 FT8 就把电台调到 7.074 MHz 之类的 FT8 常用频点。

### 3.2 `get_ft8_status`（读工具）

```
签名：get_ft8_status()
返回：
{
  ok: true,
  enabled: bool,               // 解码器开关
  active: bool,               // 最近 15 s 窗是否有成功解码
  candidates: [               // 最近一窗的候选列表（最多 5 条）
    {
      freq_offset_hz: double,  // 相对 VFO 中心的频偏
      time_offset_sec: double,
      snr_db: double,          // 估算 SNR（无单位，Costas 峰归一化）
      decoded_text: string     // 空串=同步但未解码；"CQ CALL1 GRID" 等
    }, ...
  ]
}
```

**诚实空态**：无信号时 `candidates: []`、`active: false`，不伪造候选。
与 `execGetCtcssStatus:868-879` 的 `ctcssPresent()` 哲学一致。

### 3.3 注册位置

`agent_tools.cpp:1521-1524` 的工具表新增两行：
```
{"set_ft8", &execSetFt8},
{"get_ft8_status", &execGetFt8Status},
```
并在 SpectrumEngine 上新增 `ft8Enabled()/setFt8Enabled()/ft8Candidates()`
访问器（参照 `ctcssEnabled()/ctcssPresent()` 路径）。

---

## 4. 论文弱信号实验点衔接

### 4.1 既有基础

- `docs/learn/phase7/paper/main.md`：WCL 投稿草稿，核心卖点是
  **固定种子 Monte-Carlo + Wilson 95% CI + Eb/N0↔in-band SNR 标定**。
  现有 10 个合成实验覆盖 BPSK/QPSK/SSDV 等。
- `docs/learn/phase55/_PHASE55_SPEC.md`：软判决全链闭环 + Costas PLL
  状态透出。FT8 的 LDPC BP 本身就是软判决链路，与 phase55 思路同源。
- `docs/learn/wsjtx_ft8_real.md`：已有 Python 往返验证（22 passed），
  证明参数正确。

### 4.2 FT8 作为新实验点的价值

FT8 是公认的**亚噪声弱信号模式**（-20 dB SNR 仍可解码），比现有
BPSK/QPSK 实验更极端。可新增实验：

1. **Decode success vs SNR**：合成 FT8 信号注入 AWGN，从 -30 dB 到
   +10 dB 扫 SNR，画成功率曲线 + Wilson CI。这是论文新图。
2. **频偏捕获范围**：注入 ±0..±2 kHz 频偏，测 Costas 同步成功率。
3. **时偏捕获范围**：注入 ±0..±2.5 s 时偏，测同步成功率（理论边界
   来自 `sync8.f90:5-7` 的 JZ=62）。
4. **软判决 vs 硬判决**：BP LLR 路径 vs 硬判决校验子路径的成功率
   对比——与 phase55 的"软判决 14× 增益"叙事呼应。

### 4.3 衔接方式

- 合成器（§2）输出 IQ 后，既可以喂 C++ Ft8Decoder，也可以导出 WAV
  喂 Python `experiments/` runner。两边共用同一份参数表（本笔记 §7
  速查表）。
- 实验产物命名遵循 phase7 paper 约定：`ft8_decode_vs_snr__synthetic__
  N<N>__<UTC>.csv/.png`。
- **不引入 OTA 声明**：所有数字来自合成信号，paper 里明确标注
  `data-origin: synthetic`。

---

## 5. 工作拆解（4 轮）

### 轮次 1：DSP 调制器 + Costas 粗同步（Python 原型 + 单测）

**范围**：
- Python 写 `experiments/ft8/modem.py`：pack77 → CRC14 → LDPC 编码 →
  Gray → S/D 帧布局 → tone 合成 IQ（12 kHz 采样）。
- Python 写 `experiments/ft8/sync.py`：符号谱 + Costas 二维搜索，输出
  候选峰列表。
- 固定种子往返：合成 → 同步能找回频偏/时偏（误差 <0.5 Hz / <0.04 s）。

**验收标准**：
- 无噪时同步召回率 100%（100 次随机频偏/时偏注入全部找回峰）。
- 合成 IQ 与 `wsjtx_ft8_real.md` §8 的参考向量在 tone 序列上一致
  （不对比波形，只对比 itone[79] 整数序列）。
- 不写任何 cpp。

### 轮次 2：LDPC 解码器 + 77-bit unpack

**范围**：
- Python 写 `experiments/ft8/ldpc.py`：从 `ldpc_174_91_c_parity.f90`
  （GPL）解析 Mn/Nm/nrw 数据（公开协议参数），自写 log-domain BP。
- Python 写 `experiments/ft8/unpack77.py`：标准消息（i5bit=0）的
  呼号/网格/报告 unpack。
- 端到端：合成 → 同步 → LLR → BP → CRC14 → 文本，无噪 100% 还原。
- 加噪扫 SNR：画 decode success vs SNR 曲线，找到 50% 门限（预期
  ~ -20 dB 量级）。

**验收标准**：
- 无噪 100/100 消息还原。
- SNR=-15 dB 时成功率 >80%（与公开 FT8 性能量级一致）。
- CRC14 误检率 <1%（1000 次噪声盲解码）。

### 轮次 3：C++ 接入 vfo_manager + 三通道工具

**范围**：
- C++ 写 `cpp/src/dsp/ft8_decoder.h/.cpp`：把轮次 1-2 的 Python 算法
  移植成 streaming C++（不抄 GPL Fortran，按机制笔记重写）。
- `vfo_manager.cpp` 新增 `isFt8()` 分支（§1.1）。
- `bandwidth_preset.h` 新增 `kBwFt8Hz = 1000.0`。
- `test_signal.h` 新增 `"ft8"` 调制模式（§2）。
- `agent_tools.cpp` 新增 `set_ft8` / `get_ft8_status`（§3）。
- SpectrumEngine 新增 `ft8Enabled/ft8Candidates` 访问器。

**验收标准**：
- `ctest` 不回归（既有 129+ 通过）。
- 新增 `test_ft8.cpp`：合成器 → C++ 解码器往返，无噪 100% 还原。
- `set_ft8(enabled=true)` 后 `get_ft8_status()` 在合成信号下能看到
  `active:true` 与候选列表；关闭后 `active:false`、`candidates:[]`。
- 无信号时诚实空态，无伪造候选。

### 轮次 4：UI + 文档 + 论文实验接入

**范围**：
- UI：模式下拉加 `"FT8"`；状态面板显示最近候选列表（呼号/频偏/SNR）。
- `experiments/` runner 接入 FT8 实验（§4.2 的 4 个实验），输出 CSV/PNG。
- 更新 `docs/learn/phase7/paper/main.md`：新增 FT8 实验小节（数字来自
  合成，标注 `data-origin: synthetic`）。
- 用户文档：`docs/learn/phase63/ft8-user.md`（操作手册：怎么选 FT8
  模式、怎么看候选列表）。

**验收标准**：
- UI 在 offscreen 模式下能渲染 FT8 模式选项（QT_QPA_PLATFORM=offscreen
  冒烟测试）。
- 4 个实验 CSV 生成，Wilson CI 计算正确。
- paper 草稿新增小节，无 OTA 声明。

---

## 6. 红线与纪律

- **干净室**：MBDSDR 内所有 FT8 代码由后续轮自写，不复制 wsjtx Fortran/C
  文本；本笔记的 file:line 引用仅作机制证据。
- **不预置呼号**：合成器示例用 `CALL1/CALL2`；真实接收时字段空则空。
- **不发射**：FT8 工具是被动接收，不接 PTT，不做 Tx。
- **不 commit/push**：本轮及后续轮均只写文件，git 操作由人工触发。
- **隔离文件勿动**：`cpp/tests/ui_diag_freeze.cpp`、`cpp/scratch/*` 不碰。
- **GPL 措辞中立**：本文档不出现"比赛/competition"字样；活动参数
  （频率、时隙、呼号）只进 docs，不进 cpp 默认配置。

---

## 7. 未决问题（留给后续轮）

1. **UTC 对齐**：wsjtx 用系统时钟对齐 15 s 槽；MBDSDR 的 StreamEngine
   是自由运行块流，需要在 Ft8Decoder 内部维护"下一个 15 s 边界"的相位
   游标。本轮未定具体策略（建议：启动时对齐本地秒，累积相位漂移）。
2. **多信号分离**：wsjtx `subtractft8.f90` 做连续干扰抵消（SIC），
   轮次 1-3 先不做，只解最强候选；轮次 4 后视需要再加。
3. **自由文本/遥测消息**：i5bit≠0 的分支本轮不做，只支持 i5bit=0
   标准消息。
4. **JS8Call 不做**：编码族不同，另起笔记再说。
