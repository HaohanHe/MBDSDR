# 参考对标研究 03 — SatDump 卫星流水线 / 调度器 / Autotrack / L1b 定标

> 研究报告（只读，未修改 `/workspace/MBDSDR` 任何代码）
> 研究对象：`/workspace/reference/satdump/SatDump-master`（SatDump，C++17/Qt6，GPL-3.0）
> 参照物：`/workspace/MBDSDR`（C++17/Qt6 无线电接收机）
> 生成日期：2026-10-26
> 报告类型：**架构/机制对标**（pipeline / scheduler / autotrack / L1b calibration），带 `相对路径:行号` 源码引用，不抄代码清单。

---

## 结论 TL;DR

1. **SatDump 的流水线是「JSON 声明式步骤 + C++ 模块注册表」双层拼装**：`PipelineStep{level, module, parameters, input_override}`（`src-core/pipeline/pipeline.h:31-37`）以 level 字符串（`baseband`/`soft`/`frames`/`cadu`/`audio_wav`/`products`）作为阶段命名空间，模块由 `REGISTER_MODULE` 宏注册到 `modules_registry`（`src-core/pipeline/module.h:19-20,210`）。**新卫星/新仪器 = 新增一条 JSON 流水线 + 复用已有 C++ 模块**，只有新解调/新协议才需要写 C++ 模块。这是 MBDSDR 当前「硬编码链路」最值得抄的一块。
2. **调度器是实时决策器，不是离线排期表**：`AutoTrackScheduler` 后端 100ms 轮询（`src-core/common/tracking/scheduler/scheduler.cpp:252-257`），`getPassesForSatellite()` 用 libpredict 算 AOS/LOS（`passes.cpp:9-106`），`selectPassesForAutotrack()` 逐秒贪心选最高仰角的重叠 pass（`passes.cpp:119-195`），然后靠 `eng_callback`/`aos_callback`/`los_callback` 三个回调触发实际录制/停录（`scheduler.h:179-181`）。`getScheduleImage()`（`scheduler_ui.cpp:406`）只是预览，不参与决策。**与 MBDSDR `sat_pass_predict` 的「先出计划再手动执行」形态相反**。
3. **Autotrack = 天线 rotator 线程 + 多普勒 NCO 线程，两条独立闭环**：`ObjectTracker` 双线程（`object_tracker_backend.cpp:12` 50ms 更新 sat az/el；`object_tracker_rotator.cpp:15` 按 `rotator_update_period` 发 rotctl 指令），停车/提前对准策略 `rotator_park_while_idle` + `rotator_unpark_at_minus`（`object_tracker_rotator.cpp:46-58`），过顶翻转 `meridian_flip_correction`（`:43-44,95`）。多普勒补偿在 DSP 侧，`DopplerCorrectBlock` 逐样本 NCO 混频 + 一阶平滑（`src-core/common/dsp/utils/doppler_correct.cpp:53-65`），与 MBDSDR 的 `DopplerStepLimiter`（限步长收敛）思路互补而非替代。
4. **L1b 定标不是单一来源，是「地面系数 + 星上遥测 + 星上下传 LUT」三类混合**，通过 `ImageCalibrator` 抽象基类（`src-core/products/image/image_calibrator.h:11,37`）+ 事件总线 `RequestImageCalibratorEvent`（`image_calibrator.h:40-45`）按 `instrument_name` 分派到 AVHRR/MSU-MR/GVAR/xRIT 各自实现。定标单位用 ID 体系（`calibration_units.h:14-22`）+ `UnitConverter` 转换链（`calibration_converter.h:114,173`），物理公式是普朗克函数（`common/calibration.cpp:19`）。**MBDSDR 目前只有 raw IQ + 解调音频，做 L1b 的价值取决于是否要出「亮温/反射率」物理产品**，否则 `DummyCalibrator` 直通就够。
5. **产品层是三段式 `Product → ImageProduct → DataSet`**：`Product` 基类 JSON+CBOR 持久化 `product.cbor`（`src-core/products/product.cpp:16-25`），`ImageProduct` 多通道 `ImageHolder{wavenumber, polarization, calibration_type, …}`（`image_product.h:71-82`），`DataSet` 用 `dataset.json` 汇总 `products_list`（`dataset.h:25-41`），再由 `products_processor` 模块读 `instrument_cfgs/<instrument>.json` 的 `presets` 自动合成 RGB/条件图（`product_process.cpp:13-55`）。**这套「产品元数据 + preset 表达式」是 SatDump 比通用 SDR 强的第二块**。

---

## 1. 流水线：JSON 声明式步骤 + C++ 模块注册表

### 1.1 核心数据结构

`src-core/pipeline/pipeline.h:20-50` 定义了流水线的全部骨架：

```cpp
// pipeline.h:20-26
struct LivePipelineCfg {
    std::vector<int> normal_live;   // 实时模式用的模块索引序列
    std::vector<int> server_live;   // 网络 server 模式（分布式解码）
    std::vector<int> client_live;   // 网络 client 模式
    int pkt_size;                   // CADU 包大小（匹配解调输出）
};

// pipeline.h:31-37
struct PipelineStep {
    std::string level;              // 阶段命名："baseband" / "soft" / "frames" / "cadu" / "audio_wav" / "products"
    std::string module;             // 模块 ID，对应 REGISTER_MODULE 注册名
    nlohmann::json parameters;      // 模块参数
    std::string input_override;     // 可选：显式指定输入文件（而非上一级输出）
};

// pipeline.h:28-50
struct Pipeline {
    std::string id;
    std::vector<EditableParameter> editable_parameters;
    LivePipelineCfg live_cfg;
    std::vector<PipelineStep> steps;
    static void run(...);           // pipeline.h:53-54
    static void prepareParameters(...);
    static void loadPipelines(...);
    static void savePipelines(...);
};
```

**level 是字符串而非枚举**——约定俗成（`baseband`→`soft`→`frames`/`cadu`→`products`），框架不做强校验，只在 `pipeline.cpp:137` `newStep.level = pipelineStep.key()` 把 JSON 的 key 直接当 level。空 level `{}` 表示输入即 baseband（`pipeline.cpp:82-165` 解析 `work` 字段各层级）。

### 1.2 模块注册表与生命周期

`src-core/pipeline/module.h:19-228` 定义模块抽象：

```cpp
// module.h:19-20
#define REGISTER_MODULE(Class, id) ...
#define REGISTER_MODULE_EXTERNAL(Class, id) ...   // 插件注册

// module.h:26-32
enum ModuleDataType { DATA_FILE, DATA_STREAM, DATA_DSP_STREAM, DATA_NDSP_STREAM };

// module.h:58-190
class ProcessingModule {
    virtual void init() = 0;              // :139
    virtual void process() = 0;           // :156
    virtual void stop() = 0;              // :147
    virtual nlohmann::json getOutput() = 0;  // :130
    virtual std::vector<ModuleDataType> getInputTypes() = 0;   // :87
    virtual std::vector<ModuleDataType> getOutputTypes() = 0;  // :94
    void setInputType(...); void setOutputType(...);           // :101/108
    std::shared_ptr<RingBuffer<uint8_t>> input_fifo, output_fifo;  // :168-169
};
```

注册表 `ModuleEntry{id, params, inst}` + `modules_registry`（`module.h:200-210`），`getModuleInstance(id)`（`module.h:228`）按 id 反射实例化，`moduleExists(id)`（`module.h:235`）用于 `pipeline.cpp:152-154` 解析期校验。内置模块注册在 `module.cpp:86` `registerModules()`（demod/ccsds/products/generic/network/xrit 等），插件通过 `RegisterModulesEvent` 事件（`module.h:213-215`）追加（`module.cpp:121`）。

### 1.3 离线执行：`Pipeline::run()`

`src-core/pipeline/pipeline_run.cpp:14-228`：

- **前两模块流式并行优化**（`:44-117`）：若 `m1` 输出含 `DATA_STREAM` 且 `m2` 输入含 `DATA_STREAM`，用 `RingBuffer<uint8_t>(1000000)` FIFO 连接双线程 `process()`（`:65-93`）。
- **逐 level 顺序执行**（`:119-170`）：`getModuleInstance → init → process → getOutput`，上一级 `getOutput()` 的 JSON 作为下一级输入参数。
- **`products_processor` 自动后处理**（`:174-207`）：末级若非 products，框架自动补一级。
- **`prepareParameters()` 参数合并**（`:215-228`）：流水线 `editable_parameters` 与用户传入参数合并。

### 1.4 实时执行：`LivePipeline`

`src-core/pipeline/live_pipeline.cpp:45-108`：

- `start(stream, tp, server)`：首模块接 `DATA_DSP_STREAM`（SDR 硬件流），中间模块 `DATA_STREAM` FIFO 串联，末模块输出文件。
- 三套模块索引列表 `normal_live`/`server_live`/`client_live`（`live_pipeline.cpp:14-32` `prepare_modules(mods)` 按索引实例化）。
- `start_client()`（`:110-156`）：网络 client 模式，从 TCP 收 CADU 流续跑解码。
- `stop()`（`:158-178`）逐模块停；`getOutputFile()`（`:182-185`）取录制文件名。
- **`pkt_size`** 用于对齐 CADU 包边界（`live_pipeline.cpp` 中 `pkt_size` 与 `ccsds_conv_concat_decoder` 的 `cadu_size` 配合，见 `resources/pipelines/GOES.json:50` `pkt_size:10240`）。

### 1.5 代表性流水线（JSON）

`resources/pipelines/NOAA.json`：

| 流水线 | level 链 | 关键模块 | 行号 |
|---|---|---|---|
| `noaa_hrpt` | baseband(44) → soft(`pm_demod` 665400) → frames(`noaa_hrpt_decoder`) → products(`noaa_instruments`) | PM 解调 + 帧同步 + AVHRR/TIP | `NOAA.json:2,44-62` |
| `noaa_apt` | baseband(252) → audio_wav(`noaa_apt_demod` 50e3) → products(`noaa_apt_decoder`) | 模拟 APT 解调出 WAV → 楔形定标成图 | `NOAA.json:198,252-265` |

`resources/pipelines/Meteor-M.json`：

| 流水线 | level 链 | 关键模块 | 行号 |
|---|---|---|---|
| `meteor_m2_lrpt` | baseband(85) → soft(`psk_demod` QPSK 72e3) → cadu(`meteor_lrpt_decoder`) → products(`meteor_msumr_lrpt`) | QPSK + LRPT CADU + MSU-MR | `Meteor-M.json:45,85-104` |
| `meteor_m2-x_lrpt` | baseband → soft(`psk_demod` OQPSK) → cadu(`ccsds_conv_concat_decoder` 8192) → products | Viterbi+RS(223/239) | `Meteor-M.json:112,172-185` |

`resources/pipelines/GOES.json`：

| 流水线 | live_cfg | level 链 | 行号 |
|---|---|---|---|
| `goes_gvar` | `pkt_size:32786`, default/server/client | baseband → soft → frames → products | `GOES.json:3-18` |
| `goes_hrit` | `pkt_size:10240`, default:51 / server:56 / client:60 | baseband(112) → soft(`psk_demod` BPSK 927e3) → cadu(`ccsds_conv_concat_decoder`) → products(`goes_lrit_data_decoder`) | `GOES.json:48,112-139` |

**参数 schema**：`{type, value, name, description}`，支持 `bool/int/options/timestamp` 等（`NOAA.json:26-42,217-250`）；运行时映射到 `EditableParameter`（`src-core/core/params.h:18-34`，支持 `PARAM_STRING/PASSWORD/INT/FLOAT/BOOL/OPTIONS/PATH/TIMESTAMP/NOTATED_INT/COLOR/BASEBAND_TYPE/LABELED_OPTIONS`）。

### 1.6 多普勒参数注入

`resources/pipelines/Analog.json:8-30` 声明了多普勒相关参数（**部分硬编码在流水线 JSON，不是框架自动注入**）：

```json
"start_timestamp":     { "type": "timestamp", "value": 0, ... },   // Analog.json:8-12
"enable_doppler":      { "type": "bool",      "value": false },    // :14-18
"satellite_norad":     { "type": "int",       "value": 0 },        // :20-24
"satellite_frequency": { "type": "float",     "value": 0 },        // :26-30
```

解调基类 `BaseDemodModule` 的 `getParams()`（`src-core/pipeline/modules/demod/module_demod_base.h:134-153`）声明 `symbolrate/enable_doppler/doppler_alpha/baseband_format/satellite_frequency/satellite_norad/qth_lon/qth_lat/qth_alt/start_timestamp`；`process()` 中 `doppler_shift = make_shared<DopplerCorrectBlock>(...)`（`module_demod_base.cpp:161-162`），文件模式必需 `start_timestamp` 否则禁用多普勒（`module_demod_base.cpp:165-166`）。

---

## 2. 调度器（AutoTrackScheduler）

### 2.1 数据结构

`src-core/common/tracking/scheduler/scheduler.h:12-56`：

```cpp
// scheduler.h:12-18
struct AutoTrackCfg {
    double autotrack_min_elevation;  // 最低仰角门槛
    bool   stop_sdr_when_idle;       // 无 pass 时停 SDR
    bool   multi_mode;               // 多目标并发（VFO 模式）
    bool   use_localtime;            // 时间线显示用本地时
};

// scheduler.h:40-56
struct TrackedObject {
    int norad;
    double min_elevation;
    struct Downlink {
        double frequency;
        bool record, live;
        std::string pipeline_selector;   // 选哪条流水线
        std::string baseband_format;
        int baseband_decimation;
    };
    std::vector<Downlink> downlinks;     // 多下行链路
};
```

`SatellitePass{norad, aos_time, los_time, max_elevation}`（`scheduler/passes.h:8-14`）。`TrackedObject` 支持 JSON 序列化含 `work_params` diff 保存（`scheduler.h:58-83`）。

### 2.2 过境计算

`src-core/common/tracking/scheduler/passes.cpp:9-106` `getPassesForSatellite()`：

- libpredict `predict_next_aos`/`predict_next_los` 循环枚举 AOS/LOS 对（`:62-68`）。
- 每个 pass 内 **50 步采样**求 `max_elevation`（`:62-68`）。
- `filterPassesByElevation(passes, min_el)`（`:108`）过滤低仰角。
- **GEO 卫星特殊处理**（`:23-35`）：恒定可见则整段 timespan 视为一个 pass。

`selectPassesForAutotrack()`（`passes.cpp:119-195`）：**逐秒扫描时间轴、重叠 pass 取最高仰角的贪心选择**——这是「多卫星抢同一时间窗」的仲裁核心。

### 2.3 实时决策循环

`src-core/common/tracking/scheduler/scheduler.cpp`：

- 后端线程 100ms 轮询（`:252-257`）。
- `updateAutotrackPasses()`（`:152-192`）：12h 前瞻 + 高度过滤 + 排序 + 选择。
- `processAutotrack()`（`:47-148`）：
  - **单目标模式**：当前 pass 结束或进入新 pass 时触发 `eng_callback` 切换跟踪目标。
  - **多目标模式**（`multi_mode`）：`vfo_mode_norads_vis` map 跟踪并发可见卫星（`:53-98`）。
  - AOS/LOS 判定双模式（`:108-143`）。
- 三回调：`eng_callback` / `aos_callback` / `los_callback`（`scheduler.h:179-181`）+ 事件总线 `TrackingSchedulerAOSEvent/LOSEvent`。

### 2.4 回调联动（录制器侧）

`src-interface/recorder/recorder_proc.cpp:295-408`：

- **AOS 回调**：多 VFO 模式 `add_vfo_live`/`add_vfo_reco`（`:295-336`）；单目标模式 `stop_processing → set_frequency → start_processing → start_recording`（`:337-373`）。
- **LOS 回调**（`:376-408`）：停 VFO/SDR。
- `stop_processing()` 后可选 `finish_processing_after_live`：从 `normal_live` 最后一个 level 续跑离线（`:222`），**即「实时解调 + 录制落盘 + 离线补完 products」**。

### 2.5 时间线预览

`scheduler_ui.cpp:406` `getScheduleImage()`：把 `SatellitePass` 列表渲染成时间线图（`scheduler.h:215`），仅作 UI 预览，不参与决策。

### 2.6 TLE 管理

`src-core/common/tracking/tle.h/.cpp` + `src-core/db/kepler/kepler_handler.h/.cpp`：

- `TLE{norad, name, line1, line2, time}`（`tle.h:12`）；`parseTLEStream` 校验和验证（`tle.cpp:22`）。
- `tryFetchTLEsFromFileURL` / `tryFetchSingleTLEwithNorad`（`tle.cpp:90/121`），`tle_settings.url_template` 支持 `%NORAD%` 替换（`tle.cpp:126`）。
- Space-Track API 备用：`get_from_spacetrack_time`/`get_from_spacetrack_latest_list`（`tle.cpp:156/279`）。
- `KeplerDBHandler : DBHandlerBase`（SQLite，`kepler_handler.h:39`），`get_from_norad_time(norad, timestamp)` 查**历史 TLE**（`kepler_handler.h:68-79`）——定标/投影需要「过境时刻」的 TLE，不是最新 TLE。
- 自动更新任务 `AutoUpdateKeplersEvent` 间隔可配（4h/1d/3d/7d/14d，`kepler_handler.cpp:44-84`），来源 `kepler_settings.urls_to_fetch`/`norads_to_fetch`（`kepler_handler.cpp:88-105`）。
- `TLEsUpdatedEvent` 事件（`tle.h:37`）通知订阅方刷新。

---

## 3. Autotrack：天线 rotator 跟踪 + 多普勒跟踪

### 3.1 ObjectTracker 双线程

`src-core/common/tracking/obj_tracker/object_tracker.h:14` + `object_tracker_backend.cpp` + `object_tracker_rotator.cpp`：

- **backend 线程**（`object_tracker_backend.cpp:12`）：50ms 周期，用 libpredict 更新当前目标 az/el。
- **rotator 线程**（`object_tracker_rotator.cpp:15`）：按 `rotator_update_period`（`object_tracker.h:136`）发 rotator 指令。
- **停车/解算策略**：
  - `rotator_park_while_idle` + `rotator_unpark_at_minus`（提前 N 秒对准下一 AOS 方位，`object_tracker_rotator.cpp:46-58`）。
  - `meridian_flip_correction`（过顶方位角翻转修正，`object_tracker_rotator.cpp:43-44`，实现 `correctRotatorAzimuth` `:95`）。
  - `rotator_rounding`、`rotator_az_min/max`（`object_tracker.h:137-145`）限位。
- `setObject(TrackingMode, objid)`（`object_tracker.h:160`）切换跟踪目标（卫星/地面点/JPL Horizons）。
- `object_tracker_horizons.cpp`：JPL Horizons API 备用数据源。

### 3.2 RotatorHandler 抽象

`src-core/common/tracking/rotator/rotator_handler.h:16`：

```cpp
// rotator_handler.h:22-27
class RotatorHandler {
    virtual std::pair<double,double> get_pos() = 0;
    virtual void set_pos(double az, double el) = 0;
    virtual void render() = 0;
    virtual bool is_connected() = 0;
    virtual bool connect() = 0;
    virtual void disconnect() = 0;
};
// rotator_handler.h:36
struct RequestRotatorHandlerOptionsEvent { ... };  // 插件扩展 rotator 类型
```

内置 `RotatorHandler` 实现为 `RotctlHandler`（rotcl/hamlib 网络协议，`rotcl_handler.h:8`）。事件注册允许插件新增 rotator 驱动。

### 3.3 多普勒跟踪（DSP 侧）

`src-core/common/dsp/utils/doppler_correct.cpp:53-93` `DopplerCorrectBlock::work()`：

```cpp
// doppler_correct.cpp:53-56 逐样本 NCO 混频
out[i] = in[i] * std::complex<float>(cos(-curr_phase), sin(-curr_phase));
curr_phase += curr_freq;              // 相位积分

// doppler_correct.cpp:65 一阶低通平滑
curr_freq = curr_freq * (1 - alpha) + targ_freq * alpha;   // alpha = doppler_alpha (默认 0.01)

// doppler_correct.cpp:73-93 每块工作后重算目标频偏
if (file_mode) start_time += nsamples / samplerate;        // :73-76 文件模式递推
else           t = getTime();                              // :80 实时系统时钟
predict_orbit → range_rate;
doppler_shift = (range_rate * 1000 / C) * f0;              // :88
targ_freq = hz_to_rad(-doppler_shift, samplerate);         // :93 取负转相位增量
```

- 构造时 `predict_parse_tle` + `predict_create_observer`（`:17-21`），f0/observer 由 demod 模块参数注入（`module_demod_base.cpp:43-166`）。
- **`doppler_alpha` 平滑是关键**：避免 NCO 频率跳变引起相位不连续；默认 0.01 对应约 100 样本时间常数。
- **与 MBDSDR 对比**：MBDSDR `DopplerStepLimiter`（`cpp/src/core/sat_capture.h:135-231`）是「限步长收敛」（`reset/advance/requestReturnToZero/rearm/disarm/offset/frozen/state`），思路是**频率域限斜率**；SatDump 是**相位域一阶平滑**。两者都解决「多普勒估计跳变→解调失锁」，SatDump 的 NCO 相位积分对 PSK 更友好（相位连续）。

### 3.4 失锁/再捕获策略

SatDump **没有显式的「失锁→再捕获」状态机**，依赖：

1. **pass 边界自然重置**：AOS 回调重新 `start_processing`（`recorder_proc.cpp:337-373`），LOS 回调停（`:376-408`）。
2. **多普勒平滑**避免自身引起的失锁（`doppler_correct.cpp:65`）。
3. **解调模块自身同步头捕获**：`ccsds_conv_concat_decoder` 的 ASM `0x1ACFFC1D` 同步 + `viterbi_ber_thresold`/`viterbi_outsync_after`（`module_ccsds_conv_concat_decoder.h:46-118`）负责链路层失锁检测与重捕。
4. **`stop_sdr_when_idle`**（`scheduler.h:15`）在无 pass 时停 SDR，下次 AOS 冷启动。

对 MBDSDR 的启示：**失锁处理应下沉到解调模块（同步头 + BER 门限），调度器只管 pass 边界**，不要在调度层做信号级重捕。

---

## 4. L1b 定标框架

### 4.1 概念澄清

**L1b = 辐射定标后的物理量**（亮温 / 反射率 / 辐射率），区别于 L0（raw IQ/帧）和 L1a（未定标的 DN 数字量化值）。SatDump 的产品层直接产出 L1b 级 `ImageProduct`，定标在「products」level 由各仪器的 `ImageCalibrator` 完成。

### 4.2 抽象基类与事件分派

`src-core/products/image/image_calibrator.h:11-49`：

```cpp
// image_calibrator.h:11
class ImageCalibrator {
protected:
    std::shared_ptr<ImageProduct> d_pro;
    nlohmann::json d_cfg;                 // 定标系数（来自 resources/calibration/*.json + 星上遥测）
public:
    ImageCalibrator(std::shared_ptr<ImageProduct> p, nlohmann::json c);
    virtual double compute(int abs_idx, int x, int y, double px_val) = 0;  // :37 纯虚
};

// image_calibrator.h:40-45
struct RequestImageCalibratorEvent {
    std::string id;                                        // instrument_name
    std::vector<std::shared_ptr<ImageCalibrator>>& calibrators;
    std::shared_ptr<ImageProduct> product;
    nlohmann::json calib;
};
```

`get_calibrator_from_product()`（`image_calibrator.cpp:17-45`）：查事件注册表按 `instrument_name` 分派，**缺失时退化 `DummyCalibrator`**（直通 DN）。`generate_calibrated_product_channel()`（`image_calibrator.cpp:48-91`）逐像素 `calibrator->compute()` + `UnitConverter::convert()` + `ImgCalibHandler` 量化输出。

插件注册示例（`plugins/noaa_metop_support/main.cpp:29,44-55`）：

```cpp
eventBus->register_handler<RequestImageCalibratorEvent>(provideImageCalibratorHandler);
// :44-55 按 evt.id 分派
if (evt.id == "avhrr_3") calibrators.push_back(std::make_shared<NoaaAVHRR3Calibrator>(...));
```

同类注册：`meteor_support/main.cpp:38-41`（MSU-MR）、`goes_support/main.cpp:45-48`（GVAR）、`xrit_support/main.cpp:12-17`（xRIT）。

### 4.3 定标单位 ID 体系

`src-core/products/image/calibration_units.h:14-22`：

```cpp
CALIBRATION_ID_ALBEDO
CALIBRATION_ID_EMISSIVE_RADIANCE
CALIBRATION_ID_REFLECTIVE_RADIANCE
CALIBRATION_ID_BRIGHTNESS_TEMPERATURE
CALIBRATION_ID_SUN_ANGLE_COMPENSATED_REFLECTIVE_RADIANCE
CALIBRATION_ID_SUN_ANGLE_COMPENSATED_ALBEDO
...
```

`UnitInfo{name, unit}`（`calibration_units.h:34`）+ `unit_registry` 映射（`calibration_units.cpp:13`）。`UnitConverter`（`calibration_converter.h:114`）通过事件 `ConverterRequestEvent`/`ConversionRequestEvent`（`calibration_converter.h:25/61/90`）注册转换链，`convert()`（`calibration_converter.h:173`）按需转换单位（如 radiance ↔ brightness temperature ↔ albedo）。

### 4.4 物理公式

`src-core/common/calibration.cpp:6-25`：

```cpp
const double c1 = 1.1910427e-05, c2 = 1.4387752;          // :6-7 普朗克常数组合
temperature_to_radiance(t, v) = (c1 * v^3) / (exp(c2*v/t) - 1);   // :19
radiance_to_temperature(...)  = ...;                       // :21
freq_to_wavenumber / wavenumber_to_freq;                   // :23/25
calculate_sun_irradiance_interval(...);                    // :69 太阳辐照度积分
radiance_to_reflectance(...);                              // :169
```

`ImgCalibHandler{unit, scale, offset, min, max}`（`src-core/image/meta.h:73-79`）做线性映射 `getVal(img_val) = img_val * scale + offset`（`image/meta.h:114`），写入图像元数据供后续单位切换。

### 4.5 定标系数三类来源

#### (1) 地面预置系数 JSON

`resources/calibration/AVHRR.json`（NOAA-15 段 `:30-93`，NOAA-18 段 `:124-187`，MetOp-B/C 各自分段）：

- VIS 通道：`channels[]{wavnb, slope_lo, int_lo, slope_hi, int_hi, F}`（双段线性 + crossover）。
- IR 通道：`channels[]{wavnb, Vc, A, B, Ns, b[3]}`（Vc 中心波数、A/B 非线性、Ns 噪声、b 二阶）。
- `PRT[][]` 多项式（铂电阻温度计转温度）。

`resources/calibration/MSU-MR.json`：

- `wavenumbers[6]`（`:2-9`）+ `vis{METEOR-M2-3/M2-4}[4][2]`（含标定点，`:10-38`）。

#### (2) 星上下传遥测（逐行/逐帧校正）

- **AVHRR TIP 帧**：`plugins/noaa_metop_support/instruments/avhrr/avhrr_reader.cpp:265-334` `AVHRRReader::calibrate(calib_coefs)`：PRT 多项式转温度（`:296`）→ `temperature_to_radiance(A+B*tbb, Vc)`（`:314-316`）→ 逐行 `perLine_perChannel` 写 `Spc`(space)/`Blb`(blackbody)/`Nbb`(blackbody radiance)/`Ns`/`b`（`:312-334`）。
- **MSU-MR analog_tlm**：`plugins/meteor_support/meteor/instruments/msumr/msumr_calibrator.h:75-112` `cold_view/hot_view` + `cold_temp/hot_temp` 双参考源线性定标；噪声过滤 `most_common`±5K（`:105-112`）。
- **APT 楔形参考**：`plugins/analog_support/noaa_apt/module_noaa_apt_decoder.cpp:323-365` `parse_wedge_full`/`get_calib_values_wedge` + PRT 温度平均（`:530-533`）。

#### (3) 星上下传 / LUT 校准表

- **GVAR**：`plugins/goes_support/goes/gvar/gvar_calibrator.h:18-49` 4 通道 1024 项 LUT 直接查表（`ch2_lut..ch5_lut`），LUT 加载自 `lut/goes/gvar/goes<N>_gvar_lut.txt`（`module_gvar_image_decoder.cpp:559`，`readLUTValues` `:423`）。
- **xRIT**：`plugins/xrit_support/xrit/processor/calib.h:29-98` `addCalibrationInfoFunc()` 解析 `ImageDataFunctionRecord`（HALFTONE 标定头）→ `set_channel_wavenumber`/LUT；`GenericxRITCalibrator`（`generic_xrit_calibrator.h:25-83`）支持整型 LUT 或样条插值 `VizGeorefSpline2D`。

### 4.6 代表性定标器实现

| 仪器 | 类 | VIS 策略 | IR 策略 | 关键行号 |
|---|---|---|---|---|
| AVHRR-3 | `NoaaAVHRR3Calibrator` | 双段线性 `slope_lo/int_lo/slope_hi/int_hi` + crossover | 逐行 `Ns/Nbb/Spc/Blb` 三线性 + 二次非线性 `b[3]` | `avhrr_calibrator.h:17-88` |
| MSU-MR | `MeteorMsuMrCalibrator` | 三点校准 `vis_min_point/vis_max_point` + `vis_coeffs[3][2]` | 逐行 `cold_view/hot_view` 双参考源线性 | `msumr_calibrator.h:25-151` |
| GVAR Imager | `GvarCalibrator` | — | 4 通道 1024 项 LUT 查表 | `gvar_calibrator.h:18-49` |
| xRIT (LRIT/HRIT) | `GenericxRITCalibrator` | LUT 或样条 | LUT 或样条 | `generic_xrit_calibrator.h:25-101` |
| NOAA APT | 内联于 `NOAAAPTDecoderModule` | 楔形参考 | 楔形 + PRT | `module_noaa_apt_decoder.cpp:323-533` |

`avhrr_calibrator.h:55` 还有 `radiance_factors` 硬编码 MetOp-C 修正（卫星批间差异）。

### 4.7 产品后处理：preset 表达式合成

`src-core/products/product_process.h/.cpp`：

- `process_product_with_handler()`（`product_process.cpp:13-55`）读 `resources/instrument_cfgs/<instrument>.json` 的 `presets`，逐个 apply + `saveResult`，支持 `preset_cache` 去重。
- `resources/instrument_cfgs/avhrr_3.json:6/24/40` 示例：
  - 通道别名合成：`"expression": "ch2, ch2, ch1"`（RGB）。
  - LUT 应用：`"lut=lut(lut/1d/stretchcon_avhrr.png)"`。
  - 条件表达式：`ch1 < 0.065 ? (ch4 - 0.7) * 2.66 : ch2 * 2.2 - 0.15`（夜间/白天分支）。
  - 单位转换：`(3a, sun_angle_compensated_reflective_radiance, 0, 4.3332)`。
  - 图像配置 `geocorrect`/`filters`（white_balance/equalize）。
- `products_processor` 模块（`src-core/pipeline/modules/products/module_products_processor.cpp:28-57`）：读 `dataset.json` → 逐 product `loadProduct` + `process_product_with_handler`，`getID()` 返回 `"products_processor"`（`:57`）。

---

## 5. 产品体系与地理定位

### 5.1 三段式产品

`src-core/products/product.h:33-40`：

```cpp
class Product {
    nlohmann::json contents;       // 元数据
    std::string instrument_name;
    std::string type;              // 用于 loadProduct 反查 loader
    void save(dir);                // product.cpp:16-25 → product.cbor (JSON+CBOR)
    void load(dir);                // product.cpp:28-58
};
// product.cpp:60-85
std::shared_ptr<Product> loadProduct(dir);   // 按 contents["type"] 查 product_loaders 注册表
```

`src-core/products/image_product.h:42-82`：

```cpp
class ImageProduct : Product { ... };        // :42
struct ImageHolder {
    int abs_index;
    std::string filename, channel_name;
    Image image;
    int bit_depth;
    double wavenumber, polarization, bandwidth;
    std::string calibration_type;
};                                          // :71-82
```

关键方法：`set_proj_cfg`/`set_proj_cfg_tle_timestamps`（`:94/110`）、`set_calibration(calibrator, cfg)`（`:144`）写 `contents["calibration"]`、`has_calibration/get_calibration/get_calibration_raw`（`:150/156/159`）、`set_channel_unit/set_channel_wavenumber/set_channel_frequency`、`get_channel_image_by_specifications`（`:197`）。

`src-core/products/dataset.h:25-41`：

```cpp
struct DataSet {
    std::string satellite_name;
    double timestamp;
    std::vector<std::string> products_list;   // 相对路径列表
    void save(dir);    // → dataset.json
    void load(dir);
};
```

### 5.2 地理定位（投影）

- `SatelliteRaytracer` 抽象（`src-core/projection/raytrace/satellite_raytracer.h:30`）`get_position(x,y,pos,otime)` 纯虚。
- `SatelliteRaytracerSatTrack{sat_tracker}`（`common/satellite_raytracer_sattrack.h:21`）用 `SatelliteTracker`。
- `NormalLineRaytracer{timestamps, sat_positions}`（`common/normal_line.h:26`）按行 timestamps + 线性插值。
- `SatelliteTracker`（`src-core/common/tracking/tracking.h:10`）支持 TLE 或星历（`predict_orbit`/`get_sat_position_at_raw` `tracking.h:29/39`）。
- `RequestSatelliteRaytracerEvent`（`satellite_raytracer.h:58`）插件注册自定义 raytracer。
- `ImageProduct::set_proj_cfg_tle_timestamps()`（`image_product.h:110`）把 TLE + 行时间戳写入产品，供后续 geocorrect 使用。

---

## 6. 与 MBDSDR 现状对标

### 6.1 MBDSDR 已有能力（实测）

| 能力 | 现状 | 位置 |
|---|---|---|
| 过境计算 | `TleClient` + `SatPass{aos, los, f0DownlinkHz, dopplerAtPeakHz}` + `Topocentric{rangeRateKmS}` | `cpp/src/dsp/tle_client.h:22-41` |
| 多普勒 | `dopplerHz(f0, rangeRateKmS)` + `DopplerStepLimiter` 限步长收敛 | `cpp/src/dsp/tle_client.h:52`、`cpp/src/core/sat_capture.h:135-231` |
| 捕获目标 | `SatChannelMode`/`recommendSatelliteMode`/`captureTargetHz(f0, dopplerAtPeak)` | `cpp/src/core/sat_capture.h:41-79` |
| TLE 源 | celestrak `gp.php` 抓取 + `builtinTle` + GNSS 星座过滤 | `cpp/src/dsp/tle_client.h:97-133` |
| sky tab UI | `skyView_`/`passTable_`/`tleClient_`/`onPassesReady`/`onCapturePassClicked`/`onDopplerCompToggled`/`updateLiveSatellite` | `cpp/src/ui/main_window.cpp:1562/1714/3776/6503/6754/6804/6900` |
| SatDump 移植笔记 | LRPT 解调参数、CADU 常量、Viterbi 多项式 `CCSDS_R2_K7_POLYS = {79, 109}` | `docs/learn/satdump.md` |
| L1b 定标 | **无** | — |
| 产品/preset | **无** | — |
| rotator 跟踪 | **无** | — |
| 调度器 | **无**（手动 `onCapturePassClicked`） | — |

### 6.2 多普勒实测数据（phase80）

MBDSDR 已有 NCO 多普勒实测（`doppler_verify.py`）：0.1Hz 精确 / 1Hz ZOH 33.9Hz / 不补偿仅 37.2% 在通带。**结论：多普勒补偿是必需的**，SatDump 的 `doppler_alpha=0.01` 一阶平滑 + 相位积分是比 ZOH 更稳的实现，值得抄。

### 6.3 差异本质

| 维度 | SatDump | MBDSDR 现状 |
|---|---|---|
| 流水线组织 | JSON 声明式 + 模块注册表 | C++ 硬编码链路 |
| 调度 | 100ms 轮询 + AOS/LOS 回调，自动录制 | 手动点选 pass，单次捕获 |
| 多普勒 | NCO 相位积分 + 一阶平滑（`doppler_alpha`） | `DopplerStepLimiter` 限步长收敛 |
| 天线 | rotator 双线程 + 停车/提前对准/过顶翻转 | 无 |
| L1b | 5 类仪器定标器 + 单位转换链 | 无（raw IQ + 音频） |
| 产品 | `Product`/`ImageProduct`/`DataSet` + preset 表达式 | 无 |
| TLE | SQLite 历史库 + 自动更新 + Space-Track | celestrak 实时抓取 |

---

## 7. 给 MBDSDR 的落地建议（≤10 条）

1. **先抄「JSON 流水线 + 模块注册表」骨架**：把当前硬编码的 IQ→解调→音频链路抽象成 `PipelineStep{level, module, params}`，模块用 `REGISTER_MODULE` 宏注册到 map，新增卫星/仪器只写 JSON 不写 C++。这是 SatDump 可扩展性的根，**优先级最高**。
2. **多普勒补偿从「限步长」升级为「NCO 相位积分 + 一阶平滑」**：抄 `DopplerCorrectBlock`（`doppler_correct.cpp:53-65`）的 `curr_phase += curr_freq` + `curr_freq = curr_freq*(1-α) + targ*α`，α 默认 0.01。对 PSK/QPSK 尤其关键（相位连续）。`DopplerStepLimiter` 可保留作 UI 手动调谐的限速器。
3. **调度器做成「实时决策器」而非「排期表」**：100ms 轮询线程 + AOS/LOS 回调，把 `onCapturePassClicked` 升级为自动 `start_recording`/`stop_recording`。`selectPassesForAutotrack()` 的「逐秒贪心选最高仰角」是多卫星冲突仲裁的正确算法，直接抄。
4. **TLE 升级为 SQLite 历史库**：当前 celestrak 实时抓取无法回放历史过境（定标/投影需要「过境时刻」的 TLE）。抄 `KeplerDBHandler`（`kepler_handler.h:39`）+ `get_from_norad_time(norad, timestamp)`，并加自动更新任务（4h/1d/3d/7d/14d）。
5. **L1b 定标先做「抽象基类 + 事件分派 + DummyCalibrator 直通」**，不必一次上 5 个仪器。`ImageCalibrator` 接口（`image_calibrator.h:11,37`）+ `RequestImageCalibratorEvent` 事件（`:40-45`）是干净的扩展点，新仪器只需注册一个 handler。
6. **定标单位用 ID 体系 + 转换链**：抄 `calibration_units.h:14-22` 的 `CALIBRATION_ID_*` + `UnitConverter`（`calibration_converter.h:114`），避免各仪器各写一套单位换算。物理公式 `temperature_to_radiance`/`radiance_to_temperature`（`calibration.cpp:19/21`）可直接搬。
7. **产品层做轻量版 `DataSet` + preset**：不必上完整 `Product`/`ImageProduct`，但**「产品元数据 JSON + preset 表达式合成」**（`product_process.cpp:13-55`、`instrument_cfgs/avhrr_3.json:40` 条件表达式）值得抄，能显著降低「合成图/伪彩」的开发成本。
8. **失锁处理下沉到解调模块**：调度器只管 pass 边界，链路层失锁/重捕交给解调模块的同步头 + BER 门限（参考 `ccsds_conv_concat_decoder` 的 `viterbi_ber_thresold`/`viterbi_outsync_after`）。不要在调度层做信号级重捕。
9. **rotator 跟踪做成可选插件**：`RotatorHandler` 抽象接口（`rotator_handler.h:22-27`）+ `RequestRotatorHandlerOptionsEvent` 事件（`:36`）允许后续接入 rotctl/hamlib。停车/提前对准策略（`rotator_park_while_idle` + `rotator_unpark_at_minus` `object_tracker_rotator.cpp:46-58`）和过顶翻转（`meridian_flip_correction`）是必备细节，别漏。
10. **录制落盘 + 离线补完 products**：抄 `recorder_proc.cpp:222` 的 `finish_processing_after_live`——实时解调同时录 IQ 到文件，LOS 后从 `normal_live` 最后一个 level 续跑离线出产品。这样「实时看」与「事后精处理」两不误，且不阻塞下一个 pass。

---

## 附录：关键文件索引

| 主题 | 文件 | 关键行号 |
|---|---|---|
| 流水线结构 | `src-core/pipeline/pipeline.h` | 20-50 |
| 流水线解析 | `src-core/pipeline/pipeline.cpp` | 82-165, 193-262 |
| 离线执行 | `src-core/pipeline/pipeline_run.cpp` | 14-228 |
| 实时执行 | `src-core/pipeline/live_pipeline.cpp` | 14-185 |
| 模块抽象 | `src-core/pipeline/module.h` | 19-235 |
| 模块注册 | `src-core/pipeline/module.cpp` | 86, 121 |
| 多普勒 NCO | `src-core/common/dsp/utils/doppler_correct.cpp` | 53-93 |
| 解调基类 | `src-core/pipeline/modules/demod/module_demod_base.h/.cpp` | .h:134-153, .cpp:161-166 |
| 调度器 | `src-core/common/tracking/scheduler/scheduler.h/.cpp` | .h:12-56, .cpp:47-257 |
| 过境计算 | `src-core/common/tracking/scheduler/passes.cpp` | 9-195 |
| Autotrack | `src-core/common/tracking/obj_tracker/object_tracker*.cpp` | rotator:15-95 |
| Rotator 抽象 | `src-core/common/tracking/rotator/rotator_handler.h` | 16-36 |
| TLE | `src-core/common/tracking/tle.h/.cpp`, `src-core/db/kepler/kepler_handler.h/.cpp` | tle.h:12-37, kepler.h:39-79 |
| 录制联动 | `src-interface/recorder/recorder_proc.cpp` | 222, 295-408 |
| L1b 抽象 | `src-core/products/image/image_calibrator.h/.cpp` | .h:11-49, .cpp:17-91 |
| 定标单位 | `src-core/products/image/calibration_units.h`, `calibration_converter.h` | units:14-34, converter:114-173 |
| 物理公式 | `src-core/common/calibration.cpp` | 6-25, 69, 169 |
| 产品体系 | `src-core/products/product.h/.cpp`, `image_product.h`, `dataset.h` | product:33-85, image:42-82, dataset:25-41 |
| preset 合成 | `src-core/products/product_process.h/.cpp`, `resources/instrument_cfgs/avhrr_3.json` | process:13-55, avhrr_3:6-40 |
| AVHRR 定标 | `plugins/noaa_metop_support/instruments/avhrr/avhrr_calibrator.h`, `avhrr_reader.cpp` | calib:17-88, reader:265-334 |
| MSU-MR 定标 | `plugins/meteor_support/meteor/instruments/msumr/msumr_calibrator.h` | 25-151 |
| GVAR 定标 | `plugins/goes_support/goes/gvar/gvar_calibrator.h` | 18-49 |
| xRIT 定标 | `plugins/xrit_support/xrit/generic_xrit_calibrator.h`, `processor/calib.h` | 25-101, 29-98 |
| APT 定标 | `plugins/analog_support/noaa_apt/module_noaa_apt_decoder.cpp` | 323-533 |
| 流水线 JSON | `resources/pipelines/{NOAA,Meteor-M,GOES,Analog}.json` | NOAA:2-265, Meteor:45-185, GOES:3-139, Analog:8-30 |
| 系数 JSON | `resources/calibration/{AVHRR,MSU-MR,POES_telemetry}.json` | AVHRR:30-187, MSU-MR:2-38 |
| MBDSDR 现状 | `cpp/src/ui/main_window.cpp`, `cpp/src/core/sat_capture.h`, `cpp/src/dsp/tle_client.h` | 6503-6900, 41-231, 15-161 |
