# SDR++ `meteor_demodulator` 精读笔记（Phase19-B）

> 范围：`repos/sdrpp/decoder_modules/meteor_demodulator/src/`（真读源码原文，非 README）。
>  GPL 声明：SDR++ 整体为 **GPLv3**（`repos/sdrpp/license` 文件即 GPL-3.0 全文，第 1-2 行）。
>  本笔记只做机制学习，**不复制任何代码**；MBDSDR 侧一律 MIT 干净室重写。

---

## 0. 一句话结论（先读）

SDR++ 的 `meteor_demodulator` **只做到"软符号录制"为止**：QPSK/OQPSK 解调链（RRC→AGC→Costas→MM 位同步）→ 把判决后 I/Q 软值写成 `.s` 文件。**它不含帧同步、不含 Viterbi、不含 RS、不解交织、不做 JPEG/图像重组**——spec 里设想的后半段链路在 SDR++ 源码里根本不存在，下游解码是外部离线工具（LRPT 译码器）的事。这是与 Phase19 规格假设的最大出入，必须如实记录。

---

## 1. 文件清单与规模

| 文件 | 行数 | 职责 |
|---|---|---|
| `src/main.cpp` | 288 | SDR++ 模块外壳：VFO 建立、DSP 链装配、UI 菜单、.s 录制 |
| `src/meteor_demod.h` | 196 | 解调链 Processor（RRC+AGC+Costas+MM） |
| `src/meteor_costas.h` | 59 | Meteor 专用 Costas 环（QPSK + broken-mod 变体） |
| `src/meteor_demodulator_interface.h` | 5 | 对外 MCP 式接口命令枚举（仅 START/STOP 录制） |

---

## 2. GPL 片段注明（逐文件）

- `main.cpp:21-27` `SDRPP_MOD_INFO{... "meteor_demodulator", "Ryzerth", 0.1.0 ...}` —— 模块元信息，无独立许可头；整体继承 SDR++ GPLv3。
- `meteor_dem.h:1-7` 仅 `#pragma once` + 头文件包含，无版权头。
- `meteor_costas.h:1-3` 同上。
- 全部源码中 **没有任何一行被复制进 MBDSDR**；下文引用只做 `file:line` 机制索引。

---

## 3. 机制总结（file:line 索引）

### 3.1 顶层 DSP 链装配（main.cpp）

- `main.cpp:39` `#define INPUT_SAMPLE_RATE 150000` —— 输入采样率固定 150 ksps。
- `main.cpp:65-72` 建链顺序：
  1. `vfo = vfoManager.createVFO(...)`（REF_CENTER，带宽=输入采样率）
  2. `demod.init(vfo->output, 72000.0f /*符号率*/, INPUT_SAMPLE_RATE /*采样率*/, 33 /*RRC taps*/, 0.6f /*beta*/, 0.1f /*agc rate*/, 0.005f /*costas bw*/, brokenModulation, oqpsk, 1e-6 /*omega gain*/, 0.01 /*mu gain*/)` —— **LRPT 72k QPSK 的全部物理参数集中在这一行**。
  3. `split.init(&demod.out)` → 分两路：一路给星座图（reshape 成 1024 块），一路给录制 sink。
  4. `reshape.init(&symSinkStream, 1024, (72000/30)-1024)` —— 星座图只抽 1024/2400 个符号显示，不是全量。
- `main.cpp:193-203` `sinkHandler`：**解调输出的唯一终点是写盘**——
  ```
  writeBuffer[2i]   = clamp(re*84, -127, 127);
  writeBuffer[2i+1] = clamp(im*84, -127, 127);
  recFile.write(...)
  ```
  即按 int8 软符号落 `.s` 文件（`main.cpp:208` 文件名 `meteor_<timestamp>.s`）。**没有任何帧级处理**。
- `main.cpp:151-163` UI 只有两个开关：`brokenModulation`（已知卫星星座旋转修正）、`oqpsk`。
- `meteor_demodulator_interface.h:3-6` 对外接口仅 `METEOR_DEMODULATOR_IFACE_CMD_START/STOP` 录制控制——**没有"取一帧/取图像"接口**。

### 3.2 解调链本体（meteor_dem.h）

- `meteor_dem.h:150-167` `process()` 四级串行：
  1. `rrc.process` —— RRC 匹配滤波（`meteor_dem.h:31` `taps::rootRaisedCosine(rrcTapCount=33, beta=0.6, symbolrate=72000, samplerate=150000)`）
  2. `agc.process` —— FastAGC（`:33` 目标增益 1.0、速率 `agcRate=0.1`）
  3. `costas.process` —— 载波恢复（见 3.3）
  4. `recov.process` —— **M&M（Mueller&Müller）时钟恢复**，输出恢复采样率的复符号（`:35` `recov.init(..., samplerate/symbolrate=2.0833, omegaGain=1e-6, muGain=0.01, omegaRelLimit=0.01)`）
- `meteor_dem.h:155-164` OQPSK 修正：仅做"单样本 I 路延迟互换"（`lastI` 缓存一个采样），并留 `// TODO: Additional 1/24th sample delay` —— 作者自承 OQPSK 非完美。
- `meteor_dem.h:45-87` 全部 setter（setSymbolrate/setRRCParams/setAGCRate/setCostasBandwidth/setMMParams…）都走 `tempStop→改参→tempStart` 热更新，运行时可调。

### 3.3 MeteorCostas（meteor_costas.h）

- `meteor_costas.h:24-30` 标准 Costas：`out[i] = in[i] * phasor(-pcl.phase); pcl.advance(errorFunction(out[i]))`。
- `meteor_costas.h:53` **正常 QPSK 误差函数**：`err = step(re)*im - step(im)*re`（四象限 QPSK 经典科斯塔斯环）。
- `meteor_costas.h:36-50` **brokenModulation 变体**：不做判决式误差，而是取当前相位与四个硬编码星座相位
  `PHASE1=0.47439988279190737, PHASE2=2.1777839908413044, PHASE3=3.8682349942715186, PHASE4=-0.29067248091319986`
  中最近者的相位差 × 幅度作误差。这是为 **Meteor-M2 个别卫星星座整体旋转/偏移已知缺陷** 量身定制的工程补丁，不是通用 QPSK。
- `meteor_costas.h:55` 误差一律 `clamp[-1,1]` 进 PLL。

---

## 4. MBDSDR 对照（自有实现定位）

| 环节 | SDR++ meteor_demodulator | MBDSDR 现状 |
|---|---|---|
| 采样率/符号率 | 150ksps / 72k（main.cpp:39,66） | `mbdsdr_ai/meteor_sat.py:406` `METEOR_SYM_RATE=72000` |
| RRC | 33 taps β=0.6（meteor_dem.h:31） | `meteor_sat.py:408-410` α=0.6, order=64, interp=4（参数口径不同，只学机制） |
| AGC | FastAGC rate=0.1（:33） | 委托 `demod.py:QPSKDemodulator`（meteor_sat.py:30-36,583） |
| 载波恢复 | MeteorCostas（meteor_costas.h:53/36-50） | 同上，demod.py 内 Costas |
| 位同步 | M&M（meteor_dem.h:35） | demod.py Gardner |
| OQPSK 偏移 | 单样本延迟（:155-164） | meteor_sat.py:673 仅支持 QPSK/OQPSK 入口，OQPSK 偏移对齐 TODO（:674） |
| **帧同步** | **无** | `meteor_sat.py:424` `LRPT_SYNC_WORD=0x1DFCDC`（24bit）；`lrpt_find_frames` :549-572 解析 VCID/APID；`ccsds_rx.py:43` 仓库共识 ASM=0x1ACFFC1D（32bit），`AsmFramer` :234-309 支持汉明容错 |
| **Viterbi** | **无** | `ccsds_rx.py:158-227` 硬判决 K=7 r=1/2（G1=0o133/G2=0o171）；`meteor_sat.py:484-534` 自包含软判决版（G1=0x79/G2=0x5F，Meteor-M2 专用多项式）；`viterbi_encode` :458-481 可逆编码 |
| **解交织** | **无** | `meteor_sat.py:348-395` Forney 卷积交织/去交织（I=36 分支, J=2048 符号延迟） |
| **RS 外码** | **无** | `mbdsdr_ai/fec.py:148-325` 完整 RS(255,223) CCSDS（BM+Chien+Forney）；但 **meteor_sat.py 的 LRPT 链并未接 RS** |
| **解扰** | **无** | `fec.py:28-62` 255B CCSDS PN 表 + `Scrambler` :332-351 |
| **图像重组/JPEG** | **无** | `meteor_sat.py:750-762` `compose_visible_image()` **明写 TODO 返回 None**——VCDU→行重组未实现 |
| 输出 | .s 软符号文件 | —— |

### 4.1 参数口径冲突（必须诚实记录）

1. **spec 说"帧同步 0x7A7A"——全仓库 grep 无任何出处**（仅 `_PHASE19_SPEC.md:18` 自己出现一次）。仓库内实际存在三套口径：
   - `meteor_sat.py:424` `0x1DFCDC`（24bit，自造 LRPT 头同步）
   - `ccsds_rx.py:43` / `gk2a_lrit.py:73` / `fengyun_sat.py:1013` `0x1ACFFC1D`（32bit CCSDS ASM，仓库共识）
   - `docs/audit_r2/58_remaining_modules.md:167` 已审计指出 `meteor_sat.py:721-724 extract_cadu` 的 64bit pattern 是**伪造占位符**，真实 ASM 是 32bit。
2. **Viterbi 多项式三套口径并存**：`meteor_sat.py:234-235` G1=0x79/G2=0x5F（八进制 171/137）；同文件 :244-245 注释又称 satdump_adapter 用十进制 (79,109)=(0x4F,0x6D) 位反转存储；`ccsds_rx.py:50-52` 用 0o133/0o171 标准 CCSDS。**未与真实录制数据对齐前，任何移植都是在未收敛的参数上重建。**
3. **RS 是否在 Meteor-M2 LRPT 链上**：spec 把 RS 列入 LRPT 环节，但自有 `meteor_sat.py` 整条 LRPT 链（:650-711）**没有 RS 步骤**；`docs/learn/satellite_rx_deepdive.md:12` 的 RS(255,223) 描述对应 GEO HRIT/LRIT 拼接链，非 Meteor-M2 极轨链。

---

## 5. 差距判定

- SDR++ 提供的可学机制 = **模拟前端**（RRC/FastAGC/定制 Costas/M&M/OQPSK 延迟修正）。这部分 C++ 侧目前**没有** 72k QPSK 解调块；python 侧已由 `demod.py:QPSKDemodulator` 覆盖。
- SDR++ **不提供** 数字后端任何参考（帧同步/Viterbi/RS/解交织/重组）——spec 设想的后半段"照 SDR++ 移植"无参照系可言，只能照 CCSDS 标准自写。
- MBDSDR 真实差距不在 C++，而在：**(a) 帧同步字/多项式等链路参数未与真实样本对齐；(b) VCDU→图像行重组整个缺失**。

---

## 6. C++ 移植必要性评估（YAGNI 诚实决策）

### 决策：**不做**（本 Phase19-B 第 3 步取消）

### 6.1 理由

1. **云内无硬件、验证闭环假**：本机/CI 无 137 MHz SDR 硬件，唯一验证手段是合成 IQ 往返（编码→QPSK→解调→解码等于输入）。合成往返只能证明"自洽"，**不能证明对真实 Meteor-M2 下行参数正确**；而当前仓库参数口径未收敛（§4.1 三套同步字/三套多项式），在未对齐参数上写 C++ 等于双倍重写、双倍可能错。
2. **python 侧已有大半条链且差距是协议知识缺口，不是性能缺口**：`meteor_sat.py` 已覆盖 QPSK 解调入口、Forney 去交织、Viterbi、帧字搜索；`fec.py` 已有完整 RS/解扰/差分；缺的是 `compose_visible_image`（:750-762 TODO）——这是 VCDU 行格式知识缺口。把缺口从 Python 搬到 C++ 不消除知识缺口，只增加一倍维护。
3. **C++ 侧无调用方**：ui/ 是 A 组并行域（红线禁碰），ControlHub 生产链路（参照 Phase18 pocsag/m17/vor 落地模式）目前没有任何 Meteor 数字解调接入计划；落地即死代码。
4. **image_enhance 管线不可从 C++ 干净复用**：`mbdsdr_ai/image_enhance.py` 是纯 numpy Python（auto_stretch/brightness_contrast/hist_eq/jet-iron-gray LUT），cpp/src 下**无 C++ 等价物**；"复用 image_enhance"在 C++ 语境下意味着跨语言 IPC，收益不成立。
5. **SDR++ 参照系只有半条链**（§0）：真正难做的 Viterbi/RS/重组部分 SDR++ 源码里没有，移植工作量被低估。
6. **已有 APT 产品闭环**：`cpp/src/dsp/apt_decoder.{h,cpp}` 已完整覆盖 NOAA APT（见 sdrpp-weathersat.md 对照），同属"气象卫星出图"故事，137 MHz 平台真实可收。

### 6.2 回补条件（满足任一组合后值得重启）

1. **拿到真实录制样本**：至少 1 份 Meteor-M2 LRPT `.s` 软符号文件或带基带 IQ wav 的公开样本，且能与一个权威参考译码器（satdump/meteor_decode 输出图）逐帧对齐——先在 Python 侧把参数口径（ASM、多项式、加扰）收敛到"真实样本端到端出图成功"。
2. **C++ 侧出现明确调用方**：如 ControlHub 要把 LRPT 接入生产只读命令（仿照 Phase18 pocsag/m17/vor 模式），或嵌入式低延迟约束出现，Python 路径不够用。
3. **Python 侧 `compose_visible_image` 补齐并经真实样本验证**（C++ 应搬运已验证算法，不同时开荒协议未知部分）。
4. 若做，落地形态建议：干净室独立命名（如 `dsp/lrpt_*`，不复制 GPL）、合成 QPSK 已知帧 ctest（帧同步→Viterbi→去交织→重组断言 + 纯噪声空态）、复用 QImage 行缓冲（仿 `apt_decoder.cpp:311-333 finalizeRow` 模式）。

---

## 7. 未解决项（如实）

- SDR++ 本模块不含数字后端，LRPT 帧格式（CADU 1024B / VCDU / 行重组）未在本次源码范围内，结论基于自有 python 侧与既有审计文档。
- "0x7A7A" 出处未找到，按仓库证据（0x1ACFFC1D / 0x1DFCDC）处理。
