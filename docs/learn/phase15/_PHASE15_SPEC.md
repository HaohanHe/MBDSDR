# 第十五阶段实现规格：C++ 桌面端「实时解调→出声」端到端正确性验证与修复

> 基线：HEAD = 997ff38（Phase14 已验收+SSTV/SSDV 通用化+fec RS 根步进修复）。ctest 基线 **103**。
>
> 痛点：真机反复"沙啦沙啦噪声、听不到正确声音"。云端无硬件——用**确定性合成 IQ 夹具 + 走真实 spectrum_engine 完整链路**验证；合成信号仅作测试夹具，禁入生产 UI 默认路径/冒充真实接收。
>
> 勘察事实：真实链路在 `dsp/spectrum_engine.cpp` 处理循环（source IQ → 每 VFO channelizer/demod/resampler 得 48k raw → ANR(:973) → squelch.decide(:1006) → AGC processWithGain(:1011) → gate 关清零(:1013) → audioSink_->write(out)(:1048) 或 WFM writeStereo(:1046)；数字模式写静音 :955）。引擎测试缝：`setTestAudioSink(unique_ptr<IAudioSink>)`（spectrum_engine.h:71）；FileSource：`openRaw(path,sr)`/openSigmf/openWav（file_source.h:32-38）。现有 test_demod_e2e.cpp 是**测试自拼链路**（未走真实引擎、未用 MemoryAudioSink、无底噪对照/SNR/WFM 导频/引擎静噪）。traceShare_：tokens 已有 `kDefaultSpecFraction=0.5`、`kSettingsKeySpecFraction="view/specFraction"`（tokens.h:466-467），但 spectrum_display 仅构造设默认（:51）、splitZone 更新（:992），**未 QSettings 持久化**。

## 1. 批次

| 批次 | 范围（文件域互斥） | 交付 |
|---|---|---|
| **P1 解调→出声端到端验证与修复（核心闭环）** | `dsp/spectrum_engine.*`、`dsp/` 链路修复、`cpp/tests/`（新 e2e + 夹具） | 见下 |
| **P3 traceShare 持久化（并行、独立）** | `ui/spectrum_display.*`、对应 UI 测试 | 见下 |

### P1 任务
1. **确定性 IQ 夹具**（明确注释「synthetic test fixture，仅验证链路，非真实接收」）：NFM（1kHz 音、典型 RTL-SDR 率 2.048M/250k，经信道化到音频带宽）、WFM（单声调制音 + 可选 19kHz 导频）、AM（已知调制音）、**对照纯底噪**。
2. **走真实完整链路**：合成 IQ 写 raw/SigMF → FileSource.openRaw → 真实 spectrum_engine（`setTestAudioSink(MemoryAudioSink)`）启动处理 → MemoryAudioSink 捕获。
3. **断言（核心）**：
   - NFM/AM 音频主频 = 1kHz（容差内）、电平合理；与纯底噪对照——**有信号出音、无信号是安静底噪**（底噪无显著主频/低能量）；
   - WFM 解出调制音、19kHz 导频/立体声判定正确；
   - 信道化有理重采样后音高不漂移（呼应 L1）；
   - 量化客观指标：主频误差 Hz、SNR（或 THD），写进测试输出。
4. **静噪**：默认关闭；启用时有 1kHz→OPEN、纯底噪→CLOSED，门限/翻转正确。
5. **排查并修复真机「沙啦噪声」根因**（不能只留测试）：默认增益合理性（无增益时过噪/过弱）、解调模式与带宽匹配、音频正确路由到 sink、重复/错误接线、AGC 是否误放大噪声。发现真问题就修。
6. 新增阈值/采样率/增益走 `core/tokens.h` 具名常量 + tokens::scaled 弹性派生；通用 NFM/WFM/AM 能力（非专用）。

### P3 任务
- traceShare_ 持久化：spectrum_display 构造从 QSettings（`tokens::kSettingsKeySpecFraction`）回读（钳合法区间、缺失用默认），splitZone 拖拽结束保存；测试 QSettings 保存/回读往返、重启保持。

## 2. 质量门
- ctest **103 基线不破** + 新增全绿（offscreen：`LD_LIBRARY_PATH=/home/user/Qt/6.8.2/gcc_64/lib:/home/user/.local/lib QT_QPA_PLATFORM=offscreen`）。
- 干净室 MIT（只学 SDR++/GNU Radio 机制、不复制 GPL）；合成仅测试、生产无信号诚实空态（禁假峰值/假设备/假出声）；无密钥；无「比赛/competition」。
- git：只暂存相关文件（禁 add -A）；**不自行 push**（MainAgent 验收统一推）。

## 3. 红线
先学后做、真读源码；诚实披露未完成项与环境限制；报告须真实可复核（ctest 实跑通过数、客观指标数值、file:line）。
