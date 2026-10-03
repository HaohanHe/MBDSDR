# SDR++ `weather_sat_decoder` 精读笔记（Phase19-B）

> 范围：`repos/sdrpp/decoder_modules/weather_sat_decoder/src/`（真读源码原文）。
>  GPL 声明：同 SDR++ 整体 **GPLv3**（`repos/sdrpp/license`）。本笔记只学机制，不复制代码；MBDSDR 侧 MIT 干净室。

---

## 0. 一句话结论

SDR++ 的 `weather_sat_decoder` **只实现了 NOAA HRPT**（1700 MHz、数字 BPSK/Manchester、AVHRR 五通道），**不是 NOAA APT**。它与 MBDSDR 已有的 C++ `apt_decoder` 是两个不同产品：APT=137 MHz 模拟 FM 副载波；HRPT=1700 MHz 高速数字。结论：**不重复造，无需移植**；自有 `cpp/src/dsp/apt_decoder.{h,cpp}` 已完整覆盖 APT 产品路径。

---

## 1. 文件清单与规模

| 文件 | 行数 | 职责 |
|---|---|---|
| `src/main.cpp` | 146 | 模块外壳：VFO、decoder 注册表、菜单 |
| `src/sat_decoder.h` | 15 | `SatDecoder` 抽象接口（select/start/stop/setVFO/drawMenu…） |
| `src/noaa_hrpt_decoder.h` | 550 | **唯一具体实现：NOAA HRPT** |

模块注册的 decoder 表只有一条：`main.cpp:49` `decoders["NOAA HRPT"] = new NOAAHRPTDecoder(...)`。

---

## 2. GPL 片段注明

- `main.cpp:26-32` `SDRPP_MOD_INFO{... "weather_sat_decoder", "Ryzerth", 0.1.0 ...}`。
- `noaa_hrpt_decoder.h:1-10` 无版权头，仅 include；整体继承 GPLv3。
- **关键外部依赖**：`noaa_hrpt_decoder.h:5-6` `#include <dsp/noaa/hrpt.h>`、`<dsp/noaa/tip.h>` —— 这两个头在 SDR++ **核心 DSP 库**里，不在本 decoder 模块快照内；`HRPTSyncWord` 同步字表、`HRPTDemux`/`TIPDemux`/`HIRSDemux` 的真实实现均不可见。即：本模块只是"装配+显示"，核心 deframe/demux 逻辑在别处（且我们读不到）。

---

## 3. 机制总结（file:line 索引）

### 3.1 顶层装配（main.cpp）

- `main.cpp:47` VFO：1 MHz 参考、1 MHz 带宽。
- `main.cpp:49-60` decoder map 注册 + 默认选第一个；`main.cpp:92-99 selectDecoder()` 切换时 stop 旧→start 新。
- `main.cpp:115` **"Record" 按钮是空壳**（无 handler）——本模块不录制。
- `main.cpp:71-77 enable()`：对全部已注册 decoder `setVFO(vfo)` 后 start 当前选中者。

### 3.2 NOAA HRPT 解调链（noaa_hrpt_decoder.h）

- `noaa_hrpt_decoder.h:12-13` `NOAA_HRPT_VFO_SR=3000000.0f`（3 MHz）、`NOAA_HRPT_VFO_BW=2000000.0f`（2 MHz）—— HRPT 信号本身约 1.7 GHz、665.4 kbps，需宽带 VFO。
- `:17` 构造即建 6 张 `LinePushImage`（2048×256）：AVHRR RGB 合成图 + ch1..ch5 单通道图。
- `:22` `demod.init(vfo->output, 3000000.0 /*sr*/, 665400.0*2.0 /*符号率*/, 0.02e-3 /*pll bw*/, (0.06²)/2.0, 32, 0.6, (0.01²)/4.0, 0.01, 0.005)` —— **PMDemod（FM 鉴频）+ 内置位同步**，HRPT 是 BPSK 数字信号。
- `:24-29` splitter 分两路：visStream 抽 1024 块给符号图；dataStream 进 deframe。
- `:31` **帧同步**：`deframe.init(&dataStream, 11090*10*2 /*每帧 bit 数=221800*/, (uint8_t*)dsp::noaa::HRPTSyncWord, 60 /*容差*/)` —— ManchesterDeframer，按 HRPT 同步字定帧，665.4kbps×Manchester 双采样。
- `:32-36` 后级：`manDec`（Manchester 译码）→ `packer`（bit 打包字节）→ `demux = HRPTDemux` → `tipDemux = TIPDemux` → `hirsDemux`。
- `:39-44` AVHRR ch1..ch5 五路 `HandlerSink<uint16_t>`；`:45-47` TIP 下的 SBUV/DCS/SEM 用 `NullSink`（不接）；`:49` AIP 也是 NullSink；`:51-70` HIRS 20 路 sink。

### 3.3 图像合成

- `:315-330 avhrr1Handler` 等：每路 uint16 样本 `×255/1024` → 写 RGBA 行（`LinePushImage::acquireNextLine/releaseNextLine` 环形行缓冲）；同时把 ch1/ch2 原始 uint16 转写入 `compositeIn1/2` 双流。
- `:291-313 avhrrCompositeWorker` 独立线程：同步消费 compositeIn1（ch1→B 路）与 compositeIn2（ch2→RG 路），按 2048 列拼 221 伪彩色合成图（`:300-307` `buf[4i]=rg; buf[4i+1]=rg; buf[4i+2]=b`）。
- `:392-470` **hirs1Handler..hirs20Handler 全是空函数体**（TODO 占位）——HIRS 辐射计通道已 sink 但未出图。
- `:196-210` `canRecord()` 返回 false，录制接口整体注释掉。

---

## 4. MBDSDR 对照（自有 apt_decoder 已覆盖，不重复造）

### 4.1 先厘清产品差异：APT ≠ HRPT

| 维度 | NOAA APT（自有已实现） | NOAA HRPT（SDR++ 本模块） |
|---|---|---|
| 下行频率 | ~137 MHz（rtl-sdr 可收） | ~1700 MHz（需碟形天线+TEC） |
| 调制 | 模拟 FM，2400 Hz AM 副载波 | 数字 BPSK，Manchester，665.4 kbps |
| 行结构 | 2080 像素/行×2 通道（`apt_decoder.h:29-36`：sync39/space47/video909/telem45） | 221800 bit/帧 → AVHRR 5×10bit + TIP + HIRS |
| 同步 | 7 脉冲波形相关（`apt_decoder.cpp:96-124 buildTemplate`） | HRPTSyncWord 字同步（`noaa_hrpt_decoder.h:31`） |
| 输出 | 1818px 灰度 QImage（`apt_decoder.h:45`） | 2048px AVHRR 五通道 + 221 RGB 合成 |
| 流处理 | `feed(float baseband)→image()` 干净室（`apt_decoder.h:60-86`） | 同上思路：sink handler→行缓冲（:315-330） |

### 4.2 自有 C++ apt_decoder 完成度（file:line）

- `cpp/src/dsp/apt_decoder.h:29-46` 行布局常量（经 noaa-apt GPL 参考对齐，干净室）。
- `cpp/src/dsp/apt_decoder.cpp:48-78` 2400 Hz NCO 混频 + 127-tap Hann windowed-sinc LPF（自设计，不用共享 FirLowpass）。
- `apt_decoder.cpp:157-178 feed()` 逐样本 I/Q 包络检测。
- `apt_decoder.cpp:180-255` 归一化 Pearson 相关同步状态机（Search/Locked，升沿捕获/降沿提交/连续 3 次失锁重捕）。
- `apt_decoder.cpp:257-286 lockAt()`：含 LPF 群延时补偿（:266 `lineStart += (lpfN_-1)/2`）。
- `apt_decoder.cpp:288-333` 4160 px/s 分数累加器逐像素选通 A/B 视频带 → `QImage Format_Grayscale8`。
- 测试：`cpp/tests/test_apt.cpp`（合成 APT 音频端到端，CMakeLists.txt:988-989 `add_test(NAME apt)`）。

### 4.3 差距判定

- **APT 路径：零差距**——SDR++ weather_sat_decoder 根本不做 APT，自有实现已完整且有合成 ctest，spec §B.2"跳过不重复造"成立。
- **HRPT 路径：不移植**，理由：
  1. HRPT 需要 ~1.7 GHz 碟形/TEC 硬件，超出本产品 137 MHz rtl-sdr 信封；云内无硬件更无法验证。
  2. SDR++ 本模块核心 deframe/demux 依赖读不到的核心库头（§2），无干净室可学的完整机制。
  3. HIRS 20 通道 handler 在 SDR++ 自己代码里都是空壳（:392-470）——照它移植只会搬来半成品。
  4. ui/ 是 A 组并行域，落地即无调用方。
- 若未来要补 HRPT：等真实 1.7 GHz 硬件 + 公开 HRPT 录制样本到位，再参照 SDR++ `noaa_hrpt_decoder.h:31-36` 的 deframe→manchester→packer→demux 装配顺序干净室重写；当前不做。

---

## 5. 未解决项（如实）

- `<dsp/noaa/hrpt.h>` / `<dsp/noaa/tip.h>` 不在快照内，HRPTSyncWord 具体比特模式、HRPTDemux 通道拆分细节未核读；本笔记结论基于模块装配层源码原文。
- HIRS/TIP 的产品化价值未评估（当前仅记录存在空壳）。
