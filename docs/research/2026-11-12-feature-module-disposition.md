# 51 个功能模块的处置判定（只判定，不动代码，2026-11-12）

> 证据状态：E4——本文件数字未逐条绑定原始件/复跑命令，引用前须复核（判据见 docs/evidence-standard.md）。
> 结论：上一份清单里「58 个模块无生产导入」的 51 个功能模块，按「该怎么办」分成
> **该接 UI 12 个 / 该保留 36 个 / 确实该删 3 个**。
>
> 最反直觉的一条：**主因不是「功能做完了没接界面」，而是「界面里已经有一份自己的实现」。**
> `device_panel.py` 自己做了 2 秒热插拔轮询 + 0.5 秒枚举超时看门狗
> （[device_panel.py:206-224](../../desktop/device_panel.py#L206-L224)）；
> `spectrum_widget.py` 自己做了 FFT 平均（`AVG_FRAMES=(1,4,8,16)`，
> [spectrum_widget.py:251](../../desktop/spectrum_widget.py#L251)）和 max/min hold
> （[:302](../../desktop/spectrum_widget.py#L302)）；`scanner.py` 自己做
> `classify_segment`（[scanner.py:143](../../mbdsdr_ai/scanner.py#L143)）；
> `weather_panel.py` 直接调 `lrpt_pipeline` 真录链
> （[weather_panel.py:319](../../desktop/weather_panel.py#L319)）；
> `location_panel.py` 和 `satellite_image_panel.py` **完全不 import `mbdsdr_ai`**。
> 所以对这些模块，「零引用」的意思是「库里有一份更完整的实现、面板没用它」，
> 不是「用户少一个功能」。
>
> 第二反直觉：**真正只差一个入口的只有 1 条**——`vfo_enhanced`。其余 A 类要么是
> 「替换面板里的内联换算」（`s_meter`），要么是「入口 + 参数/数据」两件事
> （`gain_calibration`、`sat_calibration`、`startup_sequence`、`reconnection`），
> 验收必须上真机或真实录制数据。
>
> 第三反直觉：**`FrequencyManagerV2` 不能按「V2 更新所以留 V2」判**。它缺 v1 的
> `find` / `categories` / `get_bookmarks_in_range`，而 v1 正被 `control_panel` 使用；
> 真正与它功能重合的是 `bookmark_manager`（16 个方法，`bookmark_panel` 在用）。
> 正确处置是「把 `classify_signal` 并入 `bookmark_manager` 后删 v2」，不是「删 v1 保 v2」。
>
> 第四：**`device_watchdog` / `recording_library` 不是「接后被摘」**——在 `desktop/`
> 的 git 历史里它们是 **0 次引用**，从没在 Python UI 接上过；`playback` 的 1 次删除是
> `audio_to_playback` 注释（同名不同物），`image_enhance` 的 4 次删除是工具名
> `sat_image_enhance`（属 `sat_image_processing`）。这三条都不能当摘线证据。
>
> 本文件只出判定，**不删任何代码**。删那 15 个私有符号另开一笔，等测试锁。

---

## 1. 判据

### 1.1 三堆定义与判定顺序

| 堆 | 定义 | 数量 |
| --- | --- | --- |
| A 该接 UI | 功能已完成，且**面板里没有等价实现**，只差界面入口 | 12 |
| B 该保留为库/内部能力 | 本来不面向用户，或被别的模块间接使用 | 36 |
| C 确实该删 | 功能被别的实现取代，或有更完整的新版，且零消费者 | 3 |

判定顺序（顺序不能倒）：

1. **看面板实际 import 了谁**——UI 用的常常是旧兄弟实现（`anr` 而非 `anr_iq`、
   `vfo_manager` 而非 `vfo_enhanced`、`bookmark_manager` 而非 `frequency_manager_v2`、
   `remote_control` 而非 `remote_control_v2`、`gain_staging` 而非 `gain_calibration`）。
2. **看面板是否已内联等价实现**——有，就不是「缺入口」，`spectrum_accumulator` /
   `device_watchdog` / `channel_analyzer` 都栽在这一步。
3. **查 git 历史**——`git log -S <模块名> -- desktop/` 的提交数与删除行，区分
   「接后被摘」和「从未接线」。
4. **最后才看零引用**——零引用只说明没有导入语句，不说明可以删：
   [cli.py:264-269](../../mbdsdr_ai/core/cli.py#L264-L269) 的 `pkgutil` 会全量 import
   `mbdsdr_ai` 顶层模块，[__init__.py:203](../../mbdsdr_ai/__init__.py#L203) 的 `_EXPORTS`
   还会按字符串懒加载。

### 1.2 两个标记不是第四堆

每条判定后面带一个标记，只表示**删除门槛**，不改变归属：

- **【硬件】**：必须真机才能验收，删了再想用就得重写。共 16 条（§6.1）。
- **【历史核查】**：看起来像基础设施、疑似「接后被摘」，需要用 git 历史单独否证。
  共 10 条（§6.2）。

### 1.3 证据等级与局限

- E1：面板源码的 import / 调用点、模块方法集、`git log -S` 输出，均可复跑。
- 未逐屏核：Flutter `mobile/`、Web 前端不在本次扫描范围；如果这些前端才是某些
  模块的真正消费方，A/B 的边界会变。本文的「接在哪一屏」一律指 **Python Qt 桌面**
  （`desktop/`），因为那是上一份清单的扫描范围。
- C++ 侧已有同名实现（`cpp/src/ui/s_meter.cpp`、`recording_library.cpp`、
  `vna_panel.cpp`、`vor_panel.cpp`、`weather_panel.cpp`），所以「Python 无 UI」
  不等于「产品无此功能」。

---

## 2. 三堆总表（51 条）

### 2.1 A 该接 UI（12）

| # | 模块 | 接在哪一屏 | 接完多什么能力 | 标记 |
| --- | --- | --- | --- | --- |
| A1 | `background_decoder` | `scanner_panel`（扫描结果区） | 扫到活动段自动解码并落结果/告警，不用手点「解码」 | 仅测试 |
| A2 | `channel_analyzer` | `scanner_panel`（活动段详情） | 点开一段就能看到 IQ 级调制/带宽/占空比，而不只是「疑似 FM」 | 仅测试 |
| A3 | `gain_calibration` | `gain_panel` | 按频段套用实测校准增益，而不是只有一档分级 | **硬件** |
| A4 | `optional_deps` | `diagnostics_panel` | 一眼看到缺哪个可选依赖、当前降级到什么程度 | — |
| A5 | `position_service` | `location_panel` | 位置聚合 + 轨迹 + GeoJSON 导出（面板现在完全自己算） | — |
| A6 | `reconnection` | `device_panel` | 拔线后自动退避重连，而不是只显示「设备已拔出」 | **硬件** |
| A7 | `remote_control_v2` | `settings_panel`（远程控制开关） | GQRX 客户端能切录音/AGC/列设备，不再只有 freq/mode/gain | — |
| A8 | `s_meter` | `status_panel`（替换 `_dbfs_to_smeter`） | 与 C++ `s_meter.cpp` 同口径的 S 值与校准状态 | — |
| A9 | `sat_calibration` | `satellite_image_panel` | counts→辐射率→反射率/亮温的物理定标，不再只做显示拉伸 | 真实录制数据 |
| A10 | `startup_sequence` | `device_panel`（连接向导） | 枚举→打开→试音→首帧分步诊断，失败时知道卡在哪一步 | **硬件** |
| A11 | `vfo_enhanced` | `vfo_panel` | 多 VFO 同时开时只有一个出声，主听优先不被打断 | — |
| A12 | `ysf_demod` | `mode_registry`（模式下拉） | YSF/C4FM 数字语音进模式列表 | 需录制 IQ |

### 2.2 B 该保留为库/内部能力（36）

| # | 模块 | 一句话理由 | 标记 |
| --- | --- | --- | --- |
| B1 | `anr_iq` | `anr_panel` 用的是音频域 `anr.SpectralSubtractionANR`，IQ 域降噪属解调前接收链，不是面板入口 | — |
| B2 | `block_stream` | 通用块流框架，`flowgraph_runtime` 是它的线程化执行器，没有 UI 语义 | — |
| B3 | `device_watchdog` | `device_panel` 已内联 2 秒轮询 + 超时看门狗，本模块是并行实现 | **硬件** / **历史核查** |
| B4 | `dvbs2_datafield` | DVB-S2 DATAFIELD→VCDU 重组是解码链内部环节，无独立界面 | — |
| B5 | `feature_manager` | `Feature` 继承 `Plugin`（[:32](../../mbdsdr_ai/feature_manager.py#L32)），是插件扩展点，且没有任何内置 Feature 子类 | — |
| B6 | `flowgraph_runtime` | 线程逐块运行时，`block_stream` 的执行器 | — |
| B7 | `ft8_modem` | FT8 **调制器**（TX），UI 无发射入口（只有 tests/experiments 在用）；删除需重写发射波形 | **硬件**（发射） |
| B8 | `gated_recorder` | 自带 `run_session`/`main`，是独立录音工具；Python 无录音面板，C++ 有 `gated_recorder.cpp` | **硬件** |
| B9 | `gnss_timestamps` | NMEA 时间→SigMF 对齐的时间基准函数，供录制链调用 | — |
| B10 | `gps_tracking` | 纯计算 GPS C/A 跟踪，参考码由外部注入，属 DSP 原语 | **硬件** |
| B11 | `hal_enhanced` | `device_panel` 已有设备健康/超时逻辑，本模块是并行实现且零 importer | **硬件** / **历史核查** |
| B12 | `image_enhance` | 与 `SatImageProcessor.enhance_pipeline` 部分重叠，但 `enhance_apt`/`save_enhanced_png` 无对应，不能按名字判死 | **历史核查** |
| B13 | `janvna2_proto` | VNA 二进制协议编解码；Python 无 VNA 面板，C++ 有 `vna_panel.cpp` | **硬件** |
| B14 | `lrpt_render` | `generate_cloud_simulation` 是仿真云图，产品真链已走 `lrpt_pipeline`；只剩测试用 | 测试夹具 |
| B15 | `marker_math` | Marker 吸附/delta 纯函数，C++ VNA 面板侧使用 | **硬件** |
| B16 | `mimo_v2` | 较完整的 MIMO 管理（`MultiDeviceManager` + `PhaseCoherence`），等第二台设备再接 | **硬件** |
| B17 | `mobile_server` | 电脑端手机 WebSocket 服务，自带 `main`，是独立进程不是面板 | — |
| B18 | `module_graph` | SDR++ 式模块图（源/解调模块类），无 UI | — |
| B19 | `module_system` | `ModuleRegistry` + `SignalGraph`，职责与 `module_graph` 不同，不是替代 | — |
| B20 | `multi_user` | 多用户共享 SDR 调度，服务端能力 | — |
| B21 | `nanovna_v2_proto` | NanoVNA V2 线协议编解码 | **硬件** |
| B22 | `playback` | IQ 回放无 Python 回放面板，C++ 侧已有实现 | **历史核查** |
| B23 | `polyphase_resampler` | 有理重采样 DSP 原语 | — |
| B24 | `protocol_analyzer` | 只有 2 个方法的分析骨架，未到「只差入口」 | — |
| B25 | `recorder_enhanced` | 包装 `AudioRecorder` 的命名模板/电平/时长，无录音面板 | — |
| B26 | `recording_library` | 37 方法的录音库索引，Python UI 从未接线；C++ 有 `recording_library.cpp` | **历史核查** |
| B27 | `sat_pipeline_runner` | 调度骨架 + ETA，面板自己报 `stage/stats`；可作统一进度层，当前无入口 | — |
| B28 | `sdrpp_decoders` | 统一解码器封装层，`register_sdrpp_decoders` 未被调用；各解码器已有独立实现 | 仅测试 |
| B29 | `spectrum_accumulator` | `spectrum_widget` 已内联平均 + max/min hold；库更完整（`auto_tune`/`peak`） | 仅测试 |
| B30 | `tc_cltu` | CCSDS TC BCH CLTU 编码纯函数 | — |
| B31 | `usb_watchdog` | 与 `reconnection` 配套的拔线回调；`device_panel` 用 QTimer 轮询替代 | **硬件** / **历史核查** |
| B32 | `vna_avg` | 扫间平均 + 历史回溯，C++ VNA 面板侧使用 | **硬件** |
| B33 | `vna_calio` | 误差盒/校准件 JSON 落盘 | **硬件** |
| B34 | `vor_decoder` | 纯编解码函数；Python 无 VOR 面板，C++ 有 `vor_panel.cpp` | — |
| B35 | `web_bridge_host` | 非 Qt 的 Web 桥接宿主，自带 `main` | — |
| B36 | `webspectra` | WebSpectra 瀑布流编解码，Web 服务端能力 | — |

### 2.3 C 确实该删（3）

| # | 模块 | 一句话理由 | 标记 |
| --- | --- | --- | --- |
| C1 | `frequency_manager_v2` | 与在用的 `bookmark_manager` 功能重合，且缺 v1 的 `find`/`categories`/`get_bookmarks_in_range`；先把 `classify_signal` 并入再删（§5.1） | — |
| C2 | `mimo` | 被 `mimo_v2` 覆盖（`set_*_all`/`start_all`/`read_synchronized`/`PhaseCoherence`），且 v1/v2 均零消费者（§5.2） | **硬件** |
| C3 | `spacetime_predict` | 与 `orbit.predict_passes_report` 同名两套实现，活的是 orbit 那套（`sdr_tools.py:4690`）；本模块只多一个 `time_source`（§5.3） | 合并后删 |

---

## 3. A 类逐条：接在哪一屏、接完多什么

> 统一前提：**不动视觉**。下面说的「接」是改数据来源与控件状态，不新增装饰性 UI；
> 若必须新增控件，走 FZ-04 解冻后的单独提交。

**A1 `background_decoder`**
- 屏：[scanner_panel.py](../../desktop/scanner_panel.py) 的扫描结果区。
- 能力：扫描到活动段后自动解码并写入结果/告警，用户不用手动点「解码」。
- 现状：`BackgroundScheduler` 有完整线程循环（`start`/`stop`/`_loop`，
  [background_decoder.py:124-145](../../mbdsdr_ai/background_decoder.py#L124-L145)）、
  持久化与 `alerts()`；只有 `tests/test_background_decoder.py` 在用。
- 风险：后台线程 + GUI 信号跨线程，需按现有 `QThread` 惯例接。

**A2 `channel_analyzer`**
- 屏：[scanner_panel.py](../../desktop/scanner_panel.py) 的活动段详情（右键/展开）。
- 能力：IQ 级分类（调制/带宽/占空比），替代现在只按带宽猜的
  `scanner.classify_segment`（[scanner.py:143](../../mbdsdr_ai/scanner.py#L143)）。
- 现状：`ChannelAnalyzer.analyze(iq, fs)` 完整
  （[channel_analyzer.py:76](../../mbdsdr_ai/channel_analyzer.py#L76)），仅测试引用。
- 风险：需要保留一段原始 IQ，扫描器当前只留功率谱。

**A3 `gain_calibration`**
- 屏：[gain_panel.py](../../desktop/gain_panel.py)。
- 能力：按频段套用实测校准增益；现在只有 `GainStager` 的分级增益
  （[gain_panel.py:32](../../desktop/gain_panel.py#L32)）。
- 现状：`GainCalibrationTable`（[gain_calibration.py:32](../../mbdsdr_ai/gain_calibration.py#L32)），零引用。
- 风险：**校准值必须实测**，无硬件只能接出空表；否则就是给用户一个假能力。

**A4 `optional_deps`**
- 屏：[diagnostics_panel.py](../../desktop/diagnostics_panel.py)。
- 能力：列出缺哪个可选依赖、降级到什么程度；现在只跑
  `diagnostics.collect_diagnostics`，没有依赖清单。
- 现状：`check_optional_deps`（[optional_deps.py:13](../../mbdsdr_ai/optional_deps.py#L13)）。
- 风险：无。

**A5 `position_service`**
- 屏：[location_panel.py](../../desktop/location_panel.py)。
- 能力：位置聚合、轨迹、GeoJSON 导出；该面板现在**完全不 import `mbdsdr_ai`**，
  自己算坐标。
- 风险：无。

**A6 `reconnection`**
- 屏：[device_panel.py](../../desktop/device_panel.py)。
- 能力：拔线后自动退避重连；现在拔线只显示「设备已拔出」
  （[device_panel.py:11](../../desktop/device_panel.py#L11)）。
- 现状：`ReconnectionManager` 21 个方法（[reconnection.py:99](../../mbdsdr_ai/reconnection.py#L99)），零 importer。
- 风险：**【硬件】**必须真拔线验收；`usb_watchdog` 文档写了「触发
  `on_device_removed` 交给 ReconnectionManager」，但代码没连。

**A7 `remote_control_v2`**
- 屏：[settings_panel.py](../../desktop/settings_panel.py) 的「启用远程控制」
  （[:382](../../desktop/settings_panel.py#L382)）。
- 能力：GQRX 客户端能切录音/AGC/列设备；现在 v1 只有 freq/mode/gain。
- 现状：`RemoteControlV2` 10 个方法，v1 是它的真子集。
- 风险：v2 要替换的是**服务端命令表**，不是控件；协议回归需用 GQRX 客户端验证。

**A8 `s_meter`**
- 屏：[status_panel.py](../../desktop/status_panel.py)。
- 能力：与 C++ `s_meter.cpp` 同口径的 S 值/校准状态。
- 现状：面板自带 `_dbfs_to_smeter`
  （[status_panel.py:148](../../desktop/status_panel.py#L148)），所以说「接」是**替换内联
  换算**，不是新增能力。若只想要新能力，这条可以不做。
- 风险：替换后读数会变，属于用户可见变化 → 等 FZ-04。

**A9 `sat_calibration`**
- 屏：[satellite_image_panel.py](../../desktop/satellite_image_panel.py)。
- 能力：counts→辐射率→反射率/亮温的物理定标；现在只做显示拉伸。
- 现状：`CalibrationTable` + 4 个换算函数，零引用。
- 风险：需要真实录制数据验证数值，不能用合成图验收。

**A10 `startup_sequence`**
- 屏：[device_panel.py](../../desktop/device_panel.py) 的连接流程。
- 能力：枚举→打开→试音→首帧分步诊断，失败时告诉用户卡在哪一步。
- 现状：`StartupSequence` 19 个方法，零引用。
- 风险：**【硬件】**必须接真 RTL-SDR；无设备时只能走到「未发现设备」。

**A11 `vfo_enhanced`**
- 屏：[vfo_panel.py](../../desktop/vfo_panel.py)。
- 能力：多 VFO 同时开时只有一个出声 + 主听优先不被打断。
- 现状：面板只用 `VfoManager`（[vfo_panel.py:36](../../desktop/vfo_panel.py#L36)）；
  `VfoAudioRouter` 是音频互斥策略，不是 `VfoManager` 的超集，所以是真缺口。
- 风险：这是唯一一条「功能完整、只差入口」的干净例子；但仍需真机听感验收。

**A12 `ysf_demod`**
- 屏：[mode_registry.py](../../desktop/mode_registry.py) 的模式下拉。
- 能力：YSF/C4FM 数字语音进模式列表。
- 现状：`mode_registry` 是声明式 + `"module:func"` 字符串懒加载
  （[mode_registry.py:12](../../desktop/mode_registry.py#L12)、
  [:56](../../desktop/mode_registry.py#L56)），加一条 `DemodMode` 即可；但
  `ysf_decode_iq` 的签名要适配 `demod_func` 约定，且输出是语音要接音频路由。
- 风险：不是「一行注册」那么小；需要录制 IQ 验收。

---

## 4. B 类分组读法

**4.1 协议/格式常量与编解码原语**：`dvbs2_datafield`、`tc_cltu`、`gnss_timestamps`、
`webspectra`、`janvna2_proto`、`nanovna_v2_proto`、`vna_calio`、`marker_math`。
理由：面向的是「另一种设备/另一层协议」，不是用户屏幕。

**4.2 DSP 原语**：`anr_iq`、`polyphase_resampler`、`gps_tracking`、`ft8_modem`、
`vna_avg`、`spectrum_accumulator`、`sdrpp_decoders`、`lrpt_render`。
理由：输入输出都是数组/比特，接 UI 只会把算法细节泄漏到界面。

**4.3 框架/运行时**：`block_stream`、`flowgraph_runtime`、`module_graph`、
`module_system`、`protocol_analyzer`、`feature_manager`、`sat_pipeline_runner`、`multi_user`。
理由：是「给别的模块用的骨架」，没有独立用户动作。注意 `module_graph` 与
`module_system` **不是替代关系**，方法集与职责都不同（前者是源/解调模块类，
后者是注册表 + 信号图）。

**4.4 设备/服务/录制**：`device_watchdog`、`usb_watchdog`、`hal_enhanced`、
`recording_library`、`recorder_enhanced`、`playback`、`gated_recorder`、`mimo_v2`。
理由：要么是服务端/独立进程，要么 C++ 侧已有实现，要么等真机。

**4.5 Python 侧无对应屏幕的 C++ 已实现能力**：`recording_library`、`vna_avg`、
`vna_calio`、`marker_math`、`vor_decoder`、`sat_calibration`（部分）。
理由：`cpp/src/ui/` 里已有 `recording_library.cpp`、`vna_panel.cpp`、`vor_panel.cpp`、
`s_meter.cpp`、`weather_panel.cpp`——不是产品缺功能，是 Python 桌面不是这些功能的
唯一前端。

**4.6 测试夹具**：`lrpt_render`、`sdrpp_decoders`、`spectrum_accumulator`、
`background_decoder`、`channel_analyzer`（后四者同时是 A 类，测试证明实现可用）。
理由：`tests/` 在锁行为；删之前得先把测试迁走。

---

## 5. C 类逐项比对

### 5.1 `frequency_manager_v2`：不是「V2 更新」，是「重复且更缺」

三套实现的方法集（实读源码）：

| 能力 | `frequency_manager`（v1，`control_panel` 在用） | `frequency_manager_v2`（零消费者） | `bookmark_manager`（`bookmark_panel` 在用） |
| --- | --- | --- | --- |
| 增删改查 | `add` / `remove` / `list` | `add` / `remove` / `update` / `list` | `add` / `remove` / `update` / `all` |
| 查找 | `find` / `categories` | **缺** | `search` / `suggest_mode` |
| 最近邻 | `nearest` | `nearest` | — |
| 范围 | `get_bookmarks_in_range` | **缺** | `in_range` |
| 分组 | — | `add_group` / `list_groups` | `groups` |
| 导入导出 | — | `import_csv` / `export_csv` | `import_csv_text` / `export_csv_text` |
| 持久化 | — | `load` / `save` + `UnsupportedBookmarkVersion` | `load` / `save` |
| 信号分类 | — | `classify_signal` | — |
| 标签 | — | — | `find_or_add_tag` / `set_tag_color` |

结论：

- v2 **不是** v1 的超集——它缺 `find`/`categories`/`get_bookmarks_in_range`，
  而 v1 正在被 [control_panel.py:1300](../../desktop/control_panel.py#L1300) 使用。
- v2 与 `bookmark_manager`（[bookmark_manager.py:109](../../mbdsdr_ai/bookmark_manager.py#L109)）
  功能高度重合，后者还是超集（多了标签、`suggest_mode`）。
- v2 唯一独有的是 `classify_signal` + 版本化文件格式。
- 处置：**把 `classify_signal` 与版本校验并入 `bookmark_manager`，然后删
  `frequency_manager_v2` 与 `tests/test_frequency_manager_v2.py`**。不做这一步，
  「删 v2」会丢掉信号分类能力；「删 v1」会丢掉 `control_panel` 在用的 `find`。

### 5.2 `mimo`：v2 覆盖，且两边都零消费者

| `mimo.MIMOController`（v1） | `mimo_v2.MultiDeviceManager` + `PhaseCoherence` |
| --- | --- |
| `add_device` / `remove_device` / `devices` | `add_device` / `remove_device` / `list_devices` |
| — | `set_frequency_all` / `set_sample_rate_all` |
| `all_synced` | `_alive_slots` / `_safe_call` |
| `read_stream(duration_s)` | `read_synchronized(n)` |
| `measure_phase_coherence` | `PhaseCoherence.measure_phase_diff` / `phase_calibrate` / `apply_calibration` |
| — | `start_all` / `stop_all` |

结论：v2 是功能超集；`mimo` 与 `mimo_v2` **都没有任何 importer（连测试都没有）**。
处置：**删 `mimo`**。但它是【硬件】类——删之前要确认 v2 在多机实测里能替代
`read_stream(duration_s)` 的阻塞语义；没有第二台设备时 v2 也没被验证过。

### 5.3 `spacetime_predict`：同名两套报告，活的是 orbit 那套

- [spacetime_predict.py:47](../../mbdsdr_ai/spacetime_predict.py#L47)：
  `predict_passes_report(tle_lines, ground_station, hours, min_alt, gnss_dt, clock)`
  —— 薄包装 `sat_passes.predict_passes`，额外报告 `time_source`（gnss/system）与
  TLE 新鲜度三态。
- [orbit.py:736](../../mbdsdr_ai/orbit.py#L736)：
  `predict_passes_report(observer_lat, observer_lon, ..., allow_stale, require_trusted_station)`
  —— 卫星目录口径的完整报告（`degraded` 清单、站址可信度），**正被
  [sdr_tools.py:4690](../../mbdsdr_ai/sdr_tools.py#L4690) 使用**。
- 消费者：`spacetime_predict` 只有
  `mbdsdr_ai/tests/test_tle_freshness_predict.py` 与 `docs/learn/`。

结论：两份报告同名但语义不同（一份是「时间基准 + TLE 新鲜度」，一份是「目录过境 +
降级清单」）。直接删会丢 `time_source` 的显式性；保留则是两套同名 API 迟早用错。
处置：**先把 `time_source` 并入 `orbit.predict_passes_report` 的返回体，再删本模块**；
在合并完成前不删。

---

## 6. 两类特别标注（不混进其他判定）

### 6.1 需要真实硬件才能验证（16 条）

| 模块 | 为什么必须真机 | 删了会怎样 |
| --- | --- | --- |
| `gain_calibration` | 校准值来自实测功率 | 表结构要重写，实测数据要重采 |
| `gated_recorder` | VOX 门限/包络取决于真实信号 | 守听录音逻辑要从头写 |
| `gps_tracking` | DLL/PLL 锁定行为取决于真实 C/A 信号 | 跟踪环要重写 |
| `janvna2_proto` / `nanovna_v2_proto` | 二进制线协议要对着 VNA 抓包 | 协议编解码要重写 |
| `vna_avg` / `vna_calio` / `marker_math` | 扫频/校准/读数要对着 VNA | 校准流程要重写 |
| `mimo` / `mimo_v2` | 相位一致性要两台以上 SDR | 多机同步要重写 |
| `reconnection` | 要真拔线/真断流 | 退避状态机要重写 |
| `startup_sequence` | 枚举/打开/首帧要真 RTL-SDR | 启动向导要重写 |
| `device_watchdog` / `usb_watchdog` / `hal_enhanced` | 拔插/健康检查要真设备 | 监控逻辑要重写 |
| `ft8_modem` | 发射波形要真发射链路验证 | 调制器要重写 |

这 16 条里，只有 `mimo` 进了 C 堆（因为 v2 覆盖且零消费者）；其余全在 B 或 A。
**它们不能因为「零引用」被批量删**，这是本文件最硬的约束。

### 6.2 「看起来像基础设施」的 git 历史核查（10 条）

判法：`git log -S <模块名> -- desktop/` 的提交数 + 被删行数。0 次引用 =
**从未在 Python UI 接线**，不是「接后被摘」。

| 模块 | desktop 提交数 | 被删行 | 结论 |
| --- | --- | --- | --- |
| `device_watchdog` | 0 | 0 | **从未接线**；`device_panel` 内联了轮询，不是摘线 |
| `recording_library` | 0 | 0 | **从未接线**；C++ 有 `recording_library.cpp` |
| `usb_watchdog` | 0 | 0 | **从未接线**；只在文档里写了与 `ReconnectionManager` 的配法 |
| `hal_enhanced` | 0 | 0 | **从未接线**；零 importer |
| `reconnection` | 0 | 0 | **从未接线**；零 importer |
| `startup_sequence` | 0 | 0 | **从未接线** |
| `playback` | 3 | 1（`12250c63`） | 删的是 `audio_to_playback` **注释**，同名不同物，**不是摘线** |
| `image_enhance` | 2 | 4（`982c4d99`） | 删的是工具名 `sat_image_enhance`（属 `sat_image_processing`），**不是本模块** |
| `vfo_enhanced` | 1 | 0（`d4ebd5bf`） | 提过但没接上；`vfo_panel` 至今只用 `VfoManager` |
| `sdrpp_decoders` | 0 | 0 | **从未接线**；只有 `test_sdrpp_decoders.py` |

反例（真的接后又被摘的）本次没有找到。所以「看起来像基础设施」的这批，
**没有一条可以用「以前接过后被摘掉」当删除理由**。

---

## 7. 下一步（不动代码）

1. 本文只作判定，不改任何产品代码。
2. C1 的两步走：先提「`classify_signal` 并入 `bookmark_manager`」的改造单，
   再提「删 `frequency_manager_v2`」；两步之间要有测试锁。
3. C3 的两步走：先提「`time_source` 并入 `orbit.predict_passes_report`」，
   再提删模块。
4. C2（`mimo`）等 v2 多机实测记录后再删；没有实测就不删。
5. 删那 15 个私有符号另开一笔，等测试锁。
6. A 类 12 条全部需要 FZ-04 解冻后才动；A3/A6/A9/A10/A12 还要真机或真实录制数据。
