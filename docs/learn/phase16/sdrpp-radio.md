# Phase16 Wave1-C：SDR++ `decoder_modules/radio` 统一解码框架精读

> 范围：`repos/sdrpp/decoder_modules/radio`（整目录 `.cpp/.h`，共 14 个源文件 / 2989 行，无 README 浏览）。
> 许可：上游为 **GPLv3**（`repos/sdrpp/license` 头部 "GNU GENERAL PUBLIC LICENSE Version 3"）。本笔记只摘录机制性短片段并注明出处，**源码不入库、不复制到 MBDSDR**；下文所有 "file:line" 均指上游仓库相对路径。
> 基线：HEAD=6c43861，ctest 104（本任务只写笔记，不改生产代码）。

---

## 0. 精读文件清单（全部真读，逐行）

| 文件 | 行数 | 角色 |
|---|---|---|
| `radio/src/main.cpp` | 28 | 模块导出 `_INIT_/_CREATE_INSTANCE_/_DELETE_INSTANCE_/_END_` |
| `radio/src/radio_interface.h` | 26 | 跨模块远程命令枚举（mode/bw/squelch/CTCSS/HP） |
| `radio/src/radio_module.h` | 914 | **核心**：VFO/IF链/AF链/模式工厂/菜单/modCom 接口 |
| `radio/src/demod.h` | 70 | `demod::Demodulator` 纯虚基类 + 模式枚举 + 聚合 include |
| `radio/src/demodulators/wfm.h` | 378 | WFM（含 RDS 子链、立体声、菜单） |
| `radio/src/demodulators/nfm.h` | 81 | NFM |
| `radio/src/demodulators/am.h` | 103 | AM（载频/音频 AGC） |
| `radio/src/demodulators/usb.h` | 96 | USB |
| `radio/src/demodulators/lsb.h` | 95 | LSB |
| `radio/src/demodulators/dsb.h` | 95 | DSB |
| `radio/src/demodulators/cw.h` | 110 | CW（=SSB+音调，独立类） |
| `radio/src/demodulators/raw.h` | 71 | RAW（IQ→stereo 直通，解调旁路） |
| `radio/src/rds_demod.h` | 101 | RDS 副载波解调（AGC→Costas→BPF→Costas2→MM时钟→差分译码） |
| `radio/src/rds.h` | 307 | RDS 块/组状态机 + PTY/PS/RT 枚举与字符串表 |
| `radio/src/rds.cpp` | 514 |  syndrome 计算、纠错、BlockA/B、Group0/2/10、呼号反解 |
| `radio/CMakeLists.txt` | 8 | `file(GLOB_RECURSE SRC "src/*.cpp")` —— 仅 `.cpp` 入库，头文件全 header-only |

> 观察：除 `main.cpp`/`rds.cpp` 两个翻译单元外，其余全是 header-only 模板/策略类；一个 `RadioModule` 实例 = 一条接收链。

---

## 1. 真读 file:line + 注释性短片段

### 1.1 模块导出与实例化边界 — `main.cpp`

```cpp
// main.cpp:3-9  模块元信息（Max instances = -1 = 不限实例数）
SDRPP_MOD_INFO{ "radio", "Analog radio decoder", "Ryzerth", 2,0,0, -1 };

// main.cpp:18-20  工厂入口：core 只认这个 new 出来的 Instance*
MOD_EXPORT ModuleManager::Instance* _CREATE_INSTANCE_(std::string name) {
    return new RadioModule(name);
}
```
要点：模块与"一条 VFO 接收链"1:1；core 通过 C ABI 四函数管理生命周期，内部自由 C++。

### 1.2 统一解调抽象基类 — `demod.h:33-61`

```cpp
namespace demod {
class Demodulator {
public:
    virtual ~Demodulator() {}
    // 生命周期
    virtual void init(std::string name, ConfigManager* config,
                      dsp::stream<dsp::complex_t>* input,
                      double bandwidth, double audioSR) = 0;          // demod.h:37
    virtual void start() = 0;  virtual void stop() = 0;              // :38-39
    virtual void showMenu() = 0;                                     // :40
    // 流式重接线
    virtual void setBandwidth(double bandwidth) = 0;                // :41
    virtual void setInput(dsp::stream<dsp::complex_t>* input) = 0;   // :42
    virtual void AFSampRateChanged(double newSR) = 0;               // :43
    // 能力查询（≈20 个 getter，驱动共享链/菜单/配置）
    virtual const char* getName() = 0;                              // :44
    virtual double getIFSampleRate() = 0;                           // :45
    virtual double getAFSampleRate() = 0;                          // :46
    virtual double getDefaultBandwidth() = 0;                       // :47
    virtual double getMinBandwidth() = 0;  virtual double getMaxBandwidth() = 0; // :48-49
    virtual bool   getBandwidthLocked() = 0;                        // :50
    virtual double getDefaultSnapInterval() = 0;                    // :51
    virtual int    getVFOReference() = 0;                            // :52  REF_CENTER/LOWER/UPPER
    virtual bool   getDeempAllowed() = 0;                           // :53
    virtual bool   getPostProcEnabled() = 0;                        // :54  AF链后处理总开关
    virtual int    getDefaultDeemphasisMode() = 0;                 // :55
    virtual bool   getFMIFNRAllowed() = 0;                         // :56
    virtual bool   getNBAllowed() = 0;                              // :57
    virtual bool   getHighPassAllowed() = 0;                        // :58
    virtual bool   getSquelchAllowed() = 0;                         // :59
    virtual dsp::stream<dsp::stereo_t>* getOutput() = 0;           // :60
};
}
```
要点：**所有模式实现同一个 27 方法虚接口**；一半是"能力自述"（capability descriptors），一半是"流式重接线钩子"。共享链的启停/使能完全由这些 getter 驱动。

```cpp
// demod.h:64-71  聚合 include：基类只 forward，具体模式在此一次性 include
#include "demodulators/wfm.h"  // :64
#include "demodulators/nfm.h"   // :65
#include "demodulators/am.h"   // :66
#include "demodulators/usb.h"   // :67
#include "demodulators/lsb.h"   // :68
#include "demodulators/dsb.h"  // :69
#include "demodulators/cw.h"    // :70
#include "demodulators/raw.h"  // :71
```

### 1.3 模式表（枚举 + 工厂 switch） — `radio_module.h:176-186`, `:365-399`

```cpp
// radio_module.h:176-186  模式 ID（与 radio_interface.h 的对外 MODE 枚举对齐）
enum DemodID {
    RADIO_DEMOD_NFM, RADIO_DEMOD_WFM, RADIO_DEMOD_AM, RADIO_DEMOD_DSB,
    RADIO_DEMOD_USB, RADIO_DEMOD_CW, RADIO_DEMOD_LSB, RADIO_DEMOD_RAW,
    _RADIO_DEMOD_COUNT,
};

// radio_module.h:365-377  工厂：switch -> new demod::X()
demod::Demodulator* instantiateDemod(DemodID id) {
    demod::Demodulator* demod = NULL;
    switch (id) {
        case DemodID::RADIO_DEMOD_NFM:  demod = new demod::NFM(); break;  // :368
        case DemodID::RADIO_DEMOD_WFM:  demod = new demod::WFM(); break;  // :369
        case DemodID::RADIO_DEMOD_AM:   demod = new demod::AM();  break;  // :370
        ... (DSB/USB/CW/LSB/RAW)                                          // :371-375
    }
```
```cpp
// radio_module.h:380-396  每个模式首次实例化时按 getName() 建独立配置子树
double bw = demod->getDefaultBandwidth();
if (!config.conf[name].contains(demod->getName())) {                  // :383
    config.conf[name][demod->getName()]["bandwidth"] = bw;            // :384
    ... snapInterval / squelchLevel / squelchEnabled                  // :385-387
}
bw = std::clamp<double>(bw, demod->getMinBandwidth(), demod->getMaxBandwidth()); // :393
demod->init(name, &config, ifChain.out, bw, stream.getSampleRate());   // :396
```
要点：**配置按模式名（"WFM"/"NFM"/"AM"…）分键**，切模式不互相污染；带宽用该模式的 min/max 钳位。

### 1.4 运行时模式切换 = 销毁-新建-重接线 — `radio_module.h:401-562`

```cpp
// radio_module.h:401-417  入口：带耗时统计（微秒级切换）
void selectDemodByID(DemodID id) {
    auto startTime = std::chrono::high_resolution_clock::now();
    demod::Demodulator* demod = instantiateDemod(id);
    selectedDemodID = id;
    selectDemod(demod);
    config.conf[name]["selectedDemodId"] = id;                        // :413
    flog::warn("Demod switch took {0} us", ...);                      // :416
}
```
```cpp
// radio_module.h:419-435  先把 AF 链输入端切到 dummy，再停/删旧解调，装新解调
void selectDemod(demod::Demodulator* demod) {
    afChain.setInput(&dummyAudioStream, [...]);                       // :421
    if (selectedDemod) { selectedDemod->stop(); delete selectedDemod; } // :422-425
    selectedDemod = demod;
    selectedDemod->AFSampRateChanged(audioSampleRate);               // :429
    selectedDemod->setInput(ifChain.out);                             // :432
    afChain.setInput(selectedDemod->getOutput(), [...]);             // :435
```
```cpp
// radio_module.h:438-457  用新解调的能力 getter 刷新所有共享控件
bandwidth       = selectedDemod->getDefaultBandwidth();               // :438
minBandwidth    = selectedDemod->getMinBandwidth();                   // :439
maxBandwidth    = selectedDemod->getMaxBandwidth();                   // :440
bandwidthLocked = selectedDemod->getBandwidthLocked();               // :441
deempAllowed    = selectedDemod->getDeempAllowed();                  // :443
FMIFNRAllowed   = selectedDemod->getFMIFNRAllowed();                // :451
nbAllowed       = selectedDemod->getNBAllowed();                     // :454
squelchAllowed  = selectedDemod->getSquelchAllowed();                // :455
highPassAllowed = selectedDemod->getHighPassAllowed();               // :456
```
```cpp
// radio_module.h:514-520  按新解调重配 VFO（带宽限幅/参考边/采样率）
vfo->setBandwidthLimits(minBandwidth, maxBandwidth, selectedDemod->getBandwidthLocked()); // :516
vfo->setReference(selectedDemod->getVFOReference());                  // :517
vfo->setSnapInterval(snapInterval);                                  // :518
vfo->setSampleRate(ifSamplerate, bandwidth);                         // :519
```
```cpp
// radio_module.h:540-561  后处理总开关：postProcEnabled=true 才接 AF 链重采样/HPF/去加重
if (postProcEnabled) {
    afChain.stop();
    double afsr = selectedDemod->getAFSampleRate();                   // :543
    ctcss.setSamplerate(afsr);  resamp.setInSamplerate(afsr);        // :544-545
    afChain.enableBlock(&resamp, [...]);                              // :546
    setHighPass(highPass && highPassAllowed);                        // :550
    setDeemphasisMode(deempModes[deempId]);                          // :553
} else {
    afChain.disableAllBlocks([...]);                                 // :557
}
selectedDemod->start();                                               // :561
```

### 1.5 共享 IF 链与 AF 链的组织 — `radio_module.h:79-116`

```cpp
// radio_module.h:80  单 VFO：REF_CENTER，初始带宽/采样率 200k
vfo = sigpath::vfoManager.createVFO(name, ImGui::WaterfallVFO::REF_CENTER,
        0, 200000, 200000, 50000, 200000, false);
// radio_module.h:88-96  IF 链：vfo 输出 -> [NB噪声抑制, PowerSquelch, FMIF噪声抑制]
ifChain.init(vfo->output);
nb.init(NULL, 500.0/24000.0, 10.0);                                  // :90
fmnr.init(NULL, 32);                                                 // :91
powerSquelch.init(NULL, MIN_SQUELCH);                                 // :92
ifChain.addBlock(&nb, false);  ifChain.addBlock(&powerSquelch, false); // :94-95
ifChain.addBlock(&fmnr, false);                                       // :96
```
```cpp
// radio_module.h:99-110  AF 链：解调输出 -> [CTCSS, RationalResampler->48k, HPF, Deemphasis]
afChain.init(&dummyAudioStream);
ctcss.init(NULL, 50000.0);                                            // :101
resamp.init(NULL, 250000.0, 48000.0);                                 // :102  IF->48k
hpTaps = dsp::taps::highPass(300.0, 100.0, 48000.0);                 // :103
hpf.init(NULL, hpTaps);                                              // :104
deemp.init(NULL, 50e-6, 48000.0);                                   // :105  50us 去加重
afChain.addBlock(&ctcss, false);  afChain.addBlock(&resamp, true);  // :107-108
afChain.addBlock(&hpf, false);    afChain.addBlock(&deemp, false);  // :109-110
```
```cpp
// radio_module.h:115-116  最终 sink：AF 链输出注册进全局 sinkManager（扬声器/录制）
stream.init(afChain.out, &srChangeHandler, audioSampleRate);
sigpath::sinkManager.registerStream(name, &stream);
```
**完整数据流**：`Source IQ → VFO(下变频+带宽窗) → IF链[NB→PowerSquelch→FMIF] → Demodulator(解调) → AF链[CTCSS→Resampler→48k→HPF→Deemphasis] → Sink`。解调本身只负责"复数基带→立体声音频"，**所有相邻处理（静噪/去加重/HPF/重采样）都在模块级共享链里按模式能力开关**。

### 1.6 共享链按模式能力条件使能 — `radio_module.h:657-704`, `:622-633`

```cpp
// radio_module.h:661-698  静噪：先全禁，再按模式只开一个块
ifChain.disableBlock(&powerSquelch, [...]);                          // :662
afChain.disableBlock(&ctcss, [...]);                                 // :663
switch (mode) {
  case SQUELCH_MODE_POWER:   ifChain.enableBlock(&powerSquelch, [...]); break; // :672
  case SQUELCH_MODE_CTCSS_MUTE: ctcss.setRequiredTone(...); afChain.enableBlock(&ctcss, [...]); break; // :681-682
  ...
}
```
```cpp
// radio_module.h:622-627  去加重：按 tau 表选系数，再把 deemp 块挂进 AF 链
bool deempEnabled = (mode != DEEMP_MODE_NONE);
if (deempEnabled) { deemp.setTau(deempTaus[mode]); }                 // :626
afChain.setBlockEnabled(&deemp, deempEnabled, [...]);              // :627
```

### 1.7 菜单/参数绑定 — `radio_module.h:189-363`, `:229-320`

```cpp
// radio_module.h:198-224  8 个 ImGui RadioButton，每个都调 selectDemodByID 切模式
if (ImGui::RadioButton(CONCAT("NFM##_", _this->name), ...) && ...) {
    _this->selectDemodByID(RADIO_DEMOD_NFM);                         // :199
} ... WFM/AM/DSB/USB/CW/LSB/RAW                                       // :201-223
```
```cpp
// radio_module.h:249-320  通用参数控件只在该模式"允许"时才渲染（能力 getter 驱动 UI）
if (_this->deempAllowed)   { /* De-emphasis Combo */ }              // :250
if (_this->squelchAllowed) { /* Squelch Mode/Level/CTCSS */ }       // :259
if (_this->nbAllowed)       { /* Noise blanker */ }                  // :286
if (_this->FMIFNRAllowed)  { /* IF Noise Reduction */ }              // :300
if (_this->highPassAllowed){ /* High Pass */ }                       // :316
// radio_module.h:323  末尾再调模式专属菜单（WFM 的立体声/RDS 表、AM 的 AGC 滑条…）
_this->selectedDemod->showMenu();
```
要点：**菜单分两层**——通用控件（带宽/静噪/去加重/NB/HPF）由模块统一渲染并按能力开关；模式专属控件（WFM 立体声/RDS、AM AGC、CW 音调）下沉到各 `Demodulator::showMenu()`。

### 1.8 跨模块远程命令接口（读写分离 + 写门） — `radio_interface.h:3-26`, `radio_module.h:780-842`

```cpp
// radio_interface.h:3-16  命令码：每个参数一对 GET/SET
enum { RADIO_IFACE_CMD_GET_MODE, RADIO_IFACE_CMD_SET_MODE,
       RADIO_IFACE_CMD_GET_BANDWIDTH, RADIO_IFACE_CMD_SET_BANDWIDTH,
       RADIO_IFACE_CMD_GET_SQUELCH_MODE, RADIO_IFACE_CMD_SET_SQUELCH_MODE,
       RADIO_IFACE_CMD_GET_SQUELCH_LEVEL, RADIO_IFACE_CMD_SET_SQUELCH_LEVEL,
       RADIO_IFACE_CMD_GET_CTCSS_TONE, RADIO_IFACE_CMD_SET_CTCSS_TONE,
       RADIO_IFACE_CMD_GET_HIGHPASS, RADIO_IFACE_CMD_SET_HIGHPASS };
```
```cpp
// radio_module.h:787-835  写命令一律带 `&& _this->enabled` 门（未启用模块拒绝写）
if (code == RADIO_IFACE_CMD_GET_MODE && out) { *_out = _this->selectedDemodID; } // :787-789
else if (code == RADIO_IFACE_CMD_SET_MODE && in && _this->enabled) {             // :791
    _this->selectDemodByID((DemodID)*_in);
}
else if (code == RADIO_IFACE_CMD_SET_BANDWIDTH && in && _this->enabled) {        // :799
    if (_this->bandwidthLocked) { return; }                                       // :801 带宽锁定拒绝
    _this->setBandwidth(*_in);
}
```
注册点：`core::modComManager.registerInterface("radio", name, moduleInterfaceHandler, this);`（`radio_module.h:134`）。**这是 Wave2 无头控制层的直接蓝本**：GET 回读状态、SET 走 `enabled` 门 + 锁定检查。

### 1.9 各解调器的能力自述（getter 对比表）

| 类 (file) | IFSR | 默认BW | BW范围 | VFO参考 | deemp | postProc | squelch | NB | HPF | FMIFNR | 备注 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `NFM` (nfm.h:55-71) | 50000 | 12500 | 1k–50k | CENTER | 允许(默认NONE) | 是 | 允许 | 否 | 是 | 允许 | 低通开关 |
| `WFM` (wfm.h:267-283) | 250000 | 150000 | 50k–250k | CENTER | 允许(默认50us) | 是 | 允许 | 否 | 是 | 允许 | 立体声/RDS 子链 |
| `AM` (am.h:75-91) | 15000 | 10000 | 1k–15k | CENTER | 否 | 是 | 允许 | 否 | 是 | 否 | 载频/音频AGC |
| `USB` (usb.h:69-85) | 24000 | 2800 | 500–12k | **LOWER** | 否 | 是 | 允许 | 是 | 是 | 否 | SSB Mode::USB |
| `LSB` (lsb.h:68-84) | 24000 | 2800 | 500–12k | **UPPER** | 否 | 是 | 允许 | 是 | 是 | 否 | SSB Mode::LSB |
| `DSB` (dsb.h:68-84) | 24000 | 4600 | 1k–12k | CENTER | 否 | 是 | 允许 | 是 | 是 | 否 | SSB Mode::DSB |
| `CW` (cw.h:81-97) | 3000 | 200 | 50–500 | CENTER | 否 | 是 | 否 | 否 | 否 | 否 | 音调 250–1250Hz |
| `RAW` (raw.h:48-64) | =audioSR | =audioSR | =audioSR | CENTER | 否 | **否** | 否 | 否 | 否 | 否 | ComplexToStereo 直通 |

要点：
- SSB 三件套（USB/LSB/DSB）**复用同一个 `dsp::demod::SSB` 模板**，只靠 `Mode` 枚举与 VFO 参考边区分（usb.h:34 / lsb.h:33 / dsb.h:33）——**同一 DSP 引擎多模式参数化**的范例。
- `RAW` 是"解调旁路"：`getPostProcEnabled()=false`（raw.h:58），模块级 AF 链全禁（radio_module.h:555-557），直接把复数 IQ 转成立体声送给 sink。
- CW 复用 `SSB` 思路但独立类（cw.h:101 `dsp::demod::CW`），因为要加音调振荡，且不允许静噪/HPF。

### 1.10 WFM 内部的 RDS 子链（不挂共享链） — `wfm.h:77-95`

```cpp
// wfm.h:78-82  WFM 解调内部另起一条 RDS 专用链：BroadcastFM.rdsOut -> RDSDemod -> Handler -> rds::Decoder
demod.init(input, bandwidth/2.0f, getIFSampleRate(), _stereo, _lowPass, _rds); // :78
rdsDemod.init(&demod.rdsOut, _rdsInfo);                            // :79  57kHz 副载波输出
hs.init(&rdsDemod.out, rdsHandler, this);                          // :80  硬符号 -> Decoder
reshape.init(&rdsDemod.soft, 4096, ...);                           // :81  软符号(高级RDS)
diagHandler.init(&reshape.out, _diagHandler, this);               // :82
```
```cpp
// wfm.h:255-261  WFM 带宽 = 频偏；输入重接线直接转发给内部 BroadcastFM
void setBandwidth(double bandwidth) { demod.setDeviation(bandwidth/2.0f); } // :256
void setInput(dsp::stream<dsp::complex_t>* input) { demod.setInput(input); } // :260
```
RDS 解调 `RDSDemod`（rds_demod.h:64-74）是一条固定 DSP 流水线：
```cpp
count = agc.process(...);        // :65 FastAGC
count = costas.process(...);     // :66 Costas<2> 载波恢复
count = fir.process(...);         // :67 BPF(0..2375Hz @5kHz)  rds_demod.h:26
count = costas2.process(...);     // :68 第二个 Costas 锁副载波
count = ComplexToReal::process(...); // :69
count = recov.process(...);      // :70 MM 时钟恢复  rds_demod.h:30
count = BinarySlicer::process(...); // :71
count = diff.process(...);       // :72 差分译码(NRZ-I)
```

### 1.11 RDS 块/组状态机 — `rds.cpp:133-188`

```cpp
// rds.cpp:136-145  逐位移入 26 位移位寄存器，算 syndrome 做同步强度计
shiftReg = ((shiftReg << 1) & 0x3FFFFFF) | (symbols[i] & 1);      // :136
uint16_t syn = calcSyndrome(shiftReg);                            // :142
bool knownSyndrome = synIt != SYNDROMES.end();                   // :144
sync = std::clamp<int>(knownSyndrome ? ++sync : --sync, 0, 4);   // :145  滞回同步
```
```cpp
// rds.cpp:161-182  块纠错后按 A/B/C/D 顺序攒满一组(contGroup>=3)再 decodeGroup()
blocks[type] = correctErrors(shiftReg, type, blockAvail[type]); // :161
if (type == BLOCK_TYPE_A) { decodeBlockA(); }                    // :164-166
else if (type == BLOCK_TYPE_B) { contGroup = 1; }               // :167
... if (contGroup >= 3) { contGroup = 0; decodeGroup(); }        // :179-182
```
```cpp
// rds.cpp:408-420  按组类型分发：0=PS/PTY，2=RadioText，10=ProgramTypeName
switch (groupType) {
  case 0:  decodeGroup0(); break;   // :409-410
  case 2:  decodeGroup2(); break;   // :412-413
  case 10: decodeGroup10(); break;  // :415-416
}
```
要点：RDS 是**独立于音频的副数据通道**，有自己的块同步/纠错/超时（`RDS_BLOCK_A_TIMEOUT_MS=5000`，rds.h:7），与音频流解耦；WFM 立体声则在 `BroadcastFM` 内核里（不在本目录，属 core DSP）。

---

## 2. 机制总结：一个流式模块如何统一多模式并运行时切换

### 2.1 统一注册三件套
1. **接口统一**：所有模式实现 `demod::Demodulator`（demod.h:34-61）——生命周期 + 流式重接线 + 能力 getter + `getOutput()`。
2. **模式表**：`enum DemodID`（radio_module.h:176-186）对应 `radio_interface.h` 对外 MODE 枚举；工厂用 `switch(id)` `new` 具体类（radio_module.h:365-377）。
3. **配置分区**：每个模式用 `getName()`（"WFM"/"NFM"/…）作 config 子键（radio_module.h:383），参数互不干扰。

### 2.2 运行时切换语义（selectDemod, radio_module.h:419-562）
- **不是"改参数"，而是"换对象"**：停旧解调 → delete → new 新解调 → 重接 IF/AF 两条 stream 的输入口。
- 切换后用新对象的能力 getter 重配**共享资源**：VFO 带宽限幅/参考边/采样率（:514-520）、AF 链重采样比/HPF/去加重（:540-554）、各后处理块使能位。
- 切换耗时被 flog 计时（:416），目标是微秒级无爆音。

### 2.3 共用音频链组织（关键设计）
```
VFO → IF链[NB, PowerSquelch, FMIF] → Demodulator → AF链[CTCSS, Resampler→48k, HPF, Deemphasis] → Sink
        ↑ 共享、按模式开关            ↑ 模式专属       ↑ 共享、按模式能力开关
```
- **解调只做"复数基带 → 立体声音频"**；相邻的静噪/去加重/HPF/重采样全部在模块级共享链，由各模式的 `*Allowed` getter 决定是否把对应块 `enableBlock`/`disableBlock` 进链路。
- `RAW` 模式 `getPostProcEnabled()=false` 演示了"整块后处理旁路"——同一套机制支持"直通"。
- 重采样是 AF 链固定块（resamp → 48k，radio_module.h:102），不管模式 IF 率多少都统一到 sink 的 48k。

### 2.4 参数/菜单绑定
- **通用参数**（带宽/静噪/去加重/NB/HPF）：模块统一渲染，`if (xxxAllowed)` 决定是否显示（radio_module.h:249-320）。
- **模式专属参数**（WFM 立体声/RDS、AM AGC、CW 音调）：下沉到各 `Demodulator::showMenu()`（radio_module.h:323）。
- **远程绑定**：modCom `moduleInterfaceHandler` 把同一批 setter 暴露成 GET/SET 命令，写命令带 `enabled` 门与 `bandwidthLocked` 检查（radio_module.h:791-835）。

### 2.5 数字/RDS 如何"挂"进来
- **RDS**：不是共享链块，而是 WFM 解调**内部的一条并行副链**（wfm.h:78-82 `BroadcastFM.rdsOut → RDSDemod → rds::Decoder`），音频与 RDS 符号同块到达但各走各的状态机。
- 真正的数字模式（M17/POCSAG/DAB）在 SDR++ 里是**独立 decoder 模块**（decoder_modules/ 下的兄弟目录），不塞进 radio 模块——radio 只负责模拟 + RDS。这是"通用框架 vs 专用解码模块"的边界。

---

## 3. MBDSDR 现状对照（真读，file:line）

### 3.1 解调接口 — `cpp/src/dsp/demod.h:17-25`
```cpp
class IDemod {
public:
    virtual std::vector<float> process(const std::vector<std::complex<float>>& iq) = 0; // :20
    virtual void reset() = 0;                                  // :21
    virtual QString name() const = 0;                         // :22
    virtual double outputSampleRate() const = 0;               // :23
    virtual void setBandwidth(double /*hz*/) {}               // :24
};
```
对照 SDR++ `Demodulator`（27 方法）：MBDSDR 的 `IDemod` **只有 5 个方法**——缺能力 getter（IFSR/默认带宽/min/max/带宽锁定/VFO参考/去加重允许/静噪允许/后处理总开关），缺生命周期 `init/start/stop/showMenu`，缺流式重接线 `setInput/AFSampRateChanged`。**接口形态是"块式 vector in/out"，不是"stream<> 重接线"**。

### 3.2 解调实现与模式覆盖 — `demod.h:58-149`, `demod.cpp`
| MBDSDR 类 | file:line | 对应 SDR++ | 差异 |
|---|---|---|---|
| `DemodAM` | demod.h:58-74 / demod.cpp:86-111 | `demod::AM` | 有载频 AGC（demod.h:67 `setCarrierAgcEnabled`），DC 阻塞器 |
| `DemodNFM` | demod.h:77-96 / demod.cpp:114-161 | `demod::NFM` | **去加重内置在解调里**（demod.cpp:117-118,157），不在共享链 |
| `DemodWFM` | demod.h:99-130 / demod.cpp:164-199 | `demod::WFM` | 暴露 `mpxOut()`(:112) 与 `rawMpxOut()`(:120) 两个 tap 给下游 stereo/RDS |
| `DemodSSB` | demod.h:133-149 / demod.cpp:202-232 | `USB/LSB/DSB` | **USB/LSB 用一个类 + Sideband 枚举**（demod.h:135），无 DSB、无 CW 独立类 |

缺：DSB、RAW、CW 独立类（CW 当前 `vfo_manager.cpp:96` 直接复用 `DemodSSB(Lsb)`，无音调）。

### 3.3 每 VFO 解调如何构建 — `cpp/src/dsp/vfo_manager.cpp:20-109`
```cpp
// vfo_manager.cpp:85-97  if/else 按模式字符串 new 对应解调（无工厂表）
if (mode == "AM")          demod = std::make_unique<DemodAM>(ifRate, bandwidthHz);     // :85
else if (mode == "WFM") {
    demod = std::make_unique<DemodWFM>(ifRate, chBw);                                   // :87
    rds = std::make_unique<RdsDecoder>(ifRate);                                        // :89
    stereo = std::make_unique<WfmStereoDecoder>(ifRate);                              // :92
}
else if (mode == "USB")    demod = std::make_unique<DemodSSB>(Sideband::USB, ...);     // :94
else if (mode == "LSB")    demod = std::make_unique<DemodSSB>(Sideband::LSB, ...);     // :95
else if (mode == "CW")     demod = std::make_unique<DemodSSB>(Sideband::LSB, ...);     // :96
else                       demod = std::make_unique<DemodNFM>(ifRate, bandwidthHz);   // :97
```
对照 SDR++：
- MBDSDR 是**每 VFO 一条独立 channelizer+demod+resampler**（vfo_manager.h:64-130），天然多 VFO 并行；SDR++ radio 模块是**单 VFO**（radio_module.h:80）。架构方向不同但模式切换逻辑同构：MBDSDR 用 `needsRebuild` 标记 + `rebuild()`（vfo_manager.cpp:232），等价于 SDR++ 的 `selectDemod`。
- **工厂是 if/else 字符串比较**，SDR++ 是 enum+switch；MBDSDR 缺"模式注册/分发表"，新模式要改 `rebuild()` 多处。
- MBDSDR 把 RDS/stereo **在 WFM 分支里随解调一起 new**（:89,:92），对照 SDR++ 把 RDS 嵌在 WFM 解调对象内部（wfm.h:79）——MBDSDR 拆成了外部 tap 消费者，生命周期同等（随 rebuild 重建）。

### 3.4 音频链组织 — `vfo_manager.cpp:228-297`
```cpp
// vfo_manager.cpp:244-292  每通道：channelizer -> demod.process -> (rds/stereo tap) -> resampler->48k
auto baseband = ch.channelizer.process(iq);                          // :244
aif = ch.demod->process(baseband);                                  // :260
if (ch.rds) { if (auto* wfm=dynamic_cast<DemodWFM*>(...)) ch.rds->feed(wfm->mpxOut()); } // :264-267
if (ch.stereo) { ... ch.stereo->feed(wfm->rawMpxOut()); ... }       // :274-283
ch.audio48k = ch.resampler.process(aif);                            // :292
```
对照 SDR++ 共享链：
- MBDSDR 音频链是 **channelizer → demod（内含去加重）→ resampler→48k**，**没有模块级"按模式能力开关"的静噪/HPF/NB/FMIF 链**。vfo_manager.h:10-13 明确注释：共享下游块（squelch/AGC/录音/writer）**故意不属 VfoManager**，住在 engine 且只作用于 selected VFO 的 48k 音频。
- 去加重：MBDSDR 内置在 `DemodNFM`/`DemodWFM` 解调里（demod.cpp:117,167）；SDR++ 是 AF 链共享 `Deemphasis` 块（radio_module.h:105）按模式 tau 开关。
- **无 `getPostProcEnabled()` 旁路机制**；无 `RAW` 直通模式。

### 3.5 WFM 立体声 — `cpp/src/dsp/wfm_stereo.{h,cpp}`
- MBDSDR 是**独立类 `WfmStereoDecoder`**（wfm_stereo.h:35），feed `rawMpxOut()`（pre-去加重）tap，内部二阶 19kHz PLL（wfm_stereo.cpp:129-147）+ 38kHz 再相干解调（:158）+ M/S 各 15kHz FIR + 50us 去加重（:166-169），输出 sample-aligned 的 M/S，由 engine 矩阵成 L/R。
- 对照 SDR++：立体声在 `dsp::demod::BroadcastFM` **内核**（core DSP，不在本目录），由 `setStereo(bool)`（wfm.h:289）开关。**两边都靠 pilot PLL 再生 38kHz，但 MBDSDR 是 clean-room 独立类、可单测**，架构上等价于"tap 下游解码"，无需照抄内核。

### 3.6 RDS — `cpp/src/dsp/rds_decoder.{h,cpp}`
- MBDSDR `RdsDecoder`（rds_decoder.h:46）feed `mpxOut()`（去加重后 MPX，57kHz 副载波仍在）。
- 解调路径：**自由运行 57kHz NCO 复数下变频**（rds_decoder.cpp:124-132）→ I/Q 双臂 FIR 低通（:136-137）→ **相位不变差分积分解 biphase**（:154 `dot = x*prevX + y*prevY`）→ 26 位块同步 + CRC（:180-264）。
- 对照 SDR++ `RDSDemod`：用 **两个 Costas 环 + MM 时钟恢复**（rds_demod.h:65-72）锁副载波与符号时钟。**MBDSDR 不用 Costas**，靠自由 NCO + 双臂差分（相位不变）免载波恢复——这是更简单、确定性更强的 clean-room 路线，已能单测。
- 状态机对照：MBDSDR `processBit/handleGroup/parseGroup`（rds_decoder.cpp:180,266,281）对应 SDR++ `Decoder::process/decodeGroup/decodeGroup0/2/10`（rds.cpp:133,400,275,311,360）。两边都按组类型 0(PS)/2(RadioText) 分发；MBDSDR 明确留 TODO：type4 时钟、type8 TMC、0B/2B 版本（rds_decoder.cpp:305-306）。

---

## 4. 差距判定表（向统一接口收敛：通用价值 + 云内可验证方案）

> 原则：只提**通用平台价值**，不硬抄 GPL；每条给"现状 → 收敛方向 → 云内（offscreen/合成 IQ）可验证方案"。ctest 104 基线不破。

| # | 维度 | SDR++ 做法 (file:line) | MBDSDR 现状 (file:line) | 判定 | 通用价值（收敛方向，非照抄） | 云内可验证方案 |
|---|---|---|---|---|---|---|
| G1 | 解调能力自述 | 27 方法虚接口，含 ~16 个能力 getter（demod.h:44-60） | `IDemod` 仅 5 方法，无能力 getter（demod.h:17-25） | **缺深度** | 给 `IDemod` 增一个轻量 `DemodCaps{ifSr, defaultBw, minBw, maxBw, bwLocked, vfoRef, deempAllowed, postProcEnabled, squelchAllowed, hpfAllowed, nbAllowed}` 纯虚 `caps()`。让 engine/vfo 统一读能力配链，消灭 if/else 特判 | 单测：每模式 `caps().defaultBw` 落在 min/max 内；WFM caps.postProcEnabled=true、RAW(若加)=false；合成 IQ 不触发 |
| G2 | 模式注册/工厂 | enum DemodID + switch new（radio_module.h:365-377）+ 按 getName() 配置分区 | 字符串 if/else（vfo_manager.cpp:85-97） | **缺深度** | 引入模式注册表：`QString mode -> {factory fn, defaultBw, caps}`。`rebuild()` 查表不再硬编码分支；新模式只加表项 | 单测：工厂 round-trip `factory("AM")->name()=="AM"`；未知 mode 返回 nullptr 且 engine 不崩（诚实报错） |
| G3 | 共享后处理链按能力开关 | IF链[NB/Squelch/FMIF]+AF链[CTCSS/Resamp/HPF/Deemp]，`enableBlock/disableBlock` 按模式能力（radio_module.h:94-110,657-704） | 去加重内置解调；静噪/NB/HPF 在 engine 作用于 selected 48k（vfo_manager.h:10-13） | **已实现但形态不同**（单VFO selected 模式） | 通用价值：把"后处理策略"声明为 caps 的一部分，engine 可对任意 VFO（不只 selected）按声明挂/摘块；**不强求**搬到每 VFO（保持单VFO 字节兼容红线） | 单测：caps 声明 `squelchAllowed=false` 的模式（CW）不挂静噪块；合成音下静噪开/关输出确定可差分 |
| G4 | 模式切换=换对象+重接线+计时 | stop/delete/new/重接 stream + flog 计时（radio_module.h:419-416） | `needsRebuild` 标记 + `rebuild()`（vfo_manager.cpp:232） | **已实现** | 已同构。可补：切换耗时记录与"切换前后块状态 reset"契约（demod.reset 已存在 demod.h:21） | 单测：切 WFM↔NFM 后 rds/stereo 指针随 rebuild 重建、旧状态清零（reset 被调） |
| G5 | 后处理旁路（RAW 直通） | `getPostProcEnabled()=false` → AF链全禁（raw.h:58, radio_module.h:555-557） | 无 RAW；数字/ADS-B 走独立路径（vfo_manager.cpp:36-74） | **未实现（模拟侧）** | 通用价值：caps.postProcEnabled=false 时 engine 跳过重采样/去加重，直通原始基带——对"数字解调旁路"与"频谱/录制 tap"有用 | 单测：caps.postProcEnabled=false 的模式，其 `audio48k` 直通 IF 率、不经过 48k resampler |
| G6 | 远程命令 GET/SET + 写门 | modCom handler，写命令带 `enabled` 门 + bwLocked（radio_module.h:787-835） | 无（属 Wave2 server 层） | **未实现（本阶段外）** | 通用价值：同一批 setter 暴露为读写分离命令，写动作 gate——正是 Wave2 无头控制接口蓝本 | Wave2 单测：未知命令诚实报错；写门关闭时 SET 不改状态、GET 回读一致 |
| G7 | RDS 副载波解调 | 双 Costas + MM 时钟（rds_demod.h:65-72） | 自由 NCO + 相位不变差分（rds_decoder.cpp:124-177） | **已实现（clean-room 替代）** | 无需照抄 Costas；MBDSDR 路线更确定。可补 type4 时钟/0B/2B（rds_decoder.cpp:305 TODO） | 单测：合成 57kHz biphase 序列 → 块同步收敛、PS/RT 字段正确 |
| G8 | WFM 立体声 | BroadcastFM 内核内 setStereo（wfm.h:289） | 独立 WfmStereoDecoder 消费 rawMpxOut tap（wfm_stereo.cpp:112） | **已实现（且更可测）** | 架构等价（tap 下游）。可补 pilot 丢失时平滑回落 mono（已实现 wfm_stereo.cpp:195-215） | 单测：合成 MPX（含 19k pilot + 38k DSB）→ M/S 对齐、blend 随 pilot 质量线性 |
| G9 | SSB 多模式参数化 | USB/LSB/DSB 复用同一 SSB 模板 + Mode 枚举（usb/lsb/dsb.h） | USB/LSB 复用 DemodSSB + Sideband（demod.h:135）；缺 DSB | **已实现（USB/LSB）** | 补 DSB：DemodSSB 加 Sideband::DSB 即可（同一 BFO/低通，双边带不滤边带） | 单测：DSB 模式不过边带、输出带宽对称；caps 表注册 |
| G10 | CW | 独立 CW 类 + 音调振荡（cw.h:101） | 复用 DemodSSB(Lsb)，无音调（vfo_manager.cpp:96） | **缺深度** | 通用价值：CW = SSB + 可设音调 BFO。给 DemodSSB 加可选 pitch 偏移即可，不必新类 | 单测：设 pitch=800Hz，合成 CW 单音 → 音频落在 800Hz |

**收敛总评**：MBDSDR 在"每 VFO 独立解调 + tap 下游 stereo/RDS"上**已经比 SDR++ 单 VFO 更现代、更可测**；真正的差距集中在 **G1（能力自述接口）、G2（模式注册表）、G6（远程命令层，Wave2）**。这三项是"统一接口收敛"的核心——G1/G2 可在本笔记指导下干净室落地（MIT、不抄 GPL），G6 是 Wave2 无头控制的直接目标。G3/G4/G5 是同一机制的不同形态，无需返工。

---

## 5. 未读透 / 留待后续清单（如实）

1. **`dsp::demod::BroadcastFM` 内核**（core/src/dsp/demod/broadcast_fm.*，含立体声解调、rdsOut 端口生成、RDS 副载波抽取）——不在本目录，未逐行读；WFM 立体声在 SDR++ 侧的内部实现细节未对照。属 Wave1-A（signal_path）范围。
2. **`dsp::chain` / `dsp::stream` / `dsp::sink::Handler`** 的线程模型与双缓冲语义（radio 模块大量依赖）——未读 core 实现；本笔记只从用法推断"stream 重接线即重连缓冲"。属 Wave1-A。
3. **`dsp::demod::FM/AM/SSB/CW` 模板内核**（core/src/dsp/demod/*）——radio 模块只是薄包装，具体解调算法（FM 鉴权器、AM AGC、SSB BFO）未逐行；MBDSDR 侧已有等价 clean-room 实现可对照。
4. **RDS 高级软判决路径**（rds_demod.h:84-86 `soft.swap`、wfm.h:81 reshape + SymbolDiagram）——读了结构，但软符号如何喂给纠错（WFM 高级 RDS）未深入；MBDSDR 暂只做硬判决。
5. **CTCSS/DCS 静噪的音調表与解码细节**（`dsp::noise_reduction::ctcss_squelch`）——只从 radio_module 用法（radio_module.h:61-67,681-688）了解接口，未读内核。
6. **modCom 注册/分发的底层**（`core::modComManager`）——只看 radio 侧 handler（radio_module.h:780-842），未看 core 如何跨模块路由；Wave1-B 范围。
7. **ctest 104 基线**：本任务只写笔记、未跑测试（无生产代码改动）；基线数字来自 SPEC 声明，未独立复核。
