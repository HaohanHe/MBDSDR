# 第十七阶段实现规格：C++ 补齐 POCSAG / m17 / VOR 三个通用数字 decoder

> 基线：HEAD = c7e4bd6（频率校准 + AM 前置载波 AGC 回归已修复）。ctest 基线 **107/107**。
>
> 目标：C++ 侧三个通用数字 decoder 完全没有（grep 空）。自有 Python 参考：mbdsdr_ai/{m17_adapter.py,pocsag_decoder.py,vor_decoder.py}（协议理解）；SDR++ decoder_modules 只学机制（GPL，不复制）；m17 官方规范宽松许可，POCSAG/VOR 公开标准。
>
> 勘察事实：fsk_demod（fsk_demod.h:37）`process(IQ)` → 内部 bits 队列（drain 取 0/1）；现有 decoder 范式 adsb_decoder/apt_decoder（feed→take 结构化输出）。

## 1. Wave1：三个并行 decoder（文件域互斥；**不改 CMakeLists.txt**，注册由主控收尾统一做）

| 批次 | 文件 | 交付 |
|---|---|---|
| **A POCSAG** | `dsp/pocsag_decoder.{h,cpp}` + `tests/test_pocsag_decoder.cpp` | 见下 |
| **B m17** | `dsp/m17_decoder.{h,cpp}` + `tests/test_m17_decoder.cpp` | 见下 |
| **C VOR** | `dsp/vor_receiver.{h,cpp}` + `tests/test_vor_receiver.cpp` | 见下 |

### A POCSAG
- 复用 fsk_demod（±4.5k FSK，512/1200/2400 baud）；前导 + 帧同步 SC=0x7CD215D8；
- 8 帧/批 × 2 码字（31bit）；**BCH(31,21) 纠错 + 偶校验**；码字分类（地址/数字/字母/空闲）；
- 消息：数字（BCD 4bit）与字母（7bit ASCII）；按地址归并；结构化输出（地址/文本/类型）。

### B m17
- 4FSK（4800 sym/s，复用 fsk_demod/gfsk）；preamble + sync（0x3243 等）帧同步；
- **LSF 帧解析**：SRC/DST 呼号字段、TYPE、META、CRC、Golay/RS（按官方规范）；
- m17 呼号特殊打包编码（对照 Python m17_adapter + 官方规范）；解扰；
- **先做数据帧与元数据**；语音 codec 若外部专有/受限则**不内置、诚实标注**；输出呼叫（源/目的呼号、类型、数据）。

### C VOR
- 解调音频后：30Hz 基准（9960Hz 副载波调频还原）与 30Hz 可变（空间调幅）；
- **两 30Hz 相位差 = radial（0–360°）**；1020Hz Morse 台站识别（键控）解码；可选 DME；
- 输出 radial（°）、识别码、信号质量；**无锁定诚实报未锁定**。

## 2. 各 decoder 测试要求（合成 fixture，注释「synthetic，仅验证链路」）
- 合成已知信号走完整解码：POCSAG 已知地址+文本、m17 已知呼号+帧、VOR 已知径向角（角度给容差），断言正确；
- 加噪/少量误码下纠错有效；纯噪声诚实空态（不造假消息/呼号/角度、不预存台站）。

## 3. Wave2（收尾，主控统一）
- CMakeLists 统一注册三个 test（源依赖以各 test 文件为准）。
- 链路接入：channelizer→fsk_demod/demod→各 decoder，注册为可选模式/解码器；解码结果经真实信号产出、ControlHub/Agent 只读可获取（不操作 GUI）。
- UI 简单显示（寻呼列表/m17 呼叫/VOR radial+识别），弹性布局、新常量走 core/tokens.h + tokens::scaled；无信号诚实空态。

## 4. 质量门
- ctest **107 基线不破** + 三个新 decoder 全绿（offscreen）；干净室 MIT（不复制 GPL、命名自有）；通用能力（非专用）；不硬编码/不预存；合成仅测试。
- git：只暂存相关文件（禁 add -A）；**不自行 push**。

## 5. 红线
先学后做（真读 Python 参考/公开标准/SDR++ 机制）；如实报告新增 file:line、ctest 实跑数、三 decoder 验证数值、未解决项（MainAgent 独立 find/跑测试/查 git 复核）。
