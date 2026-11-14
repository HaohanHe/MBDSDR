# 参考实现「可借鉴 / 不该借鉴」清单（第一批）

- 日期：2026-11-13
- 范围：本地已克隆的全部参考项目 + QGIS 3.44.15 源码
- 性质：**只读学习，不改产品代码**。本清单经批准后才进入实现批。
- QGIS 细节另见 [2026-11-13-qgis-3.44.15-source-study.md](2026-11-13-qgis-3.44.15-source-study.md)；本文只做跨项目的取舍结论。

---

## 0. 判定口径与取证规则

| 判断 | 含义 |
| --- | --- |
| **该加** | 我们确实缺这项能力，且用户能感知到收益；成本可估。 |
| **待定** | 机制成立，但触发条件（需求/场景）或成本未定，先不做，记录触发条件。 |
| **不该加（已具备）** | 本仓库已有等价或更强实现。列出来是为了**防止后来人重做**。 |
| **不该照搬** | 机制本身是反模式、依赖不成立、许可不允许，或与我们的约束冲突。 |
| **未核实** | 本轮没有在该克隆里读到源码/符号，或符号不存在。禁止凭印象补结论。 |

取证规则：

1. 每条结论必须能指到具体文件；**符号名为主，行号只作辅助**（代码会漂移，符号相对稳定）。
2. 同一个能力在本仓库有多处入口时，以「唯一的底层入口」为准记录对应位置。
3. 对「已具备」的判断，必须给出我们这边的符号名，不能只写「我们有了」。
4. 参考项目里没读到的，一律标「未核实」，不猜。

---

## 1. 参考项目索引（实际路径）

| 项目 | 实际路径 | 是什么 | 本轮读到的关键文件 |
| --- | --- | --- | --- |
| QGIS 3.44.15 | 源码包 `d:\mbdsdr\QGIS-final-3_44_15.tar.gz`；解包树 `d:\mbdsdr\.tmp\qgis34415\QGIS-final-3_44_15` | 通用桌面 GIS（Qt/C++） | `src/gui/qgsmessagebar.h`、`src/core/qgstaskmanager.h`、`src/core/locator/qgslocatorfilter.h`、`src/core/qgsdistancearea.h`、`src/gui/qgsrubberband.h`、`src/gui/qgspanelwidgetstack.h`、`src/app/qgspluginregistry.h` |
| SDR++ | `d:\mbdsdr\reference\sdrpp` | C++ SDR 桌面应用 | `core/src/module.h`、`core/src/module.cpp`、`core/src/signal_path/source.h`、`core/src/signal_path/sink.cpp`、`core/src/dsp/chain.h`、`core/src/config.cpp`、`core/src/gui/widgets/waterfall.cpp` |
| gqrx | `d:\mbdsdr\reference\gqrx` | GNU Radio + Qt SDR 接收机 | `src/applications/gqrx/receiver.h`、`receiver.cpp`、`remote_control.h`、`remote_control.cpp` |
| onair | `d:\mbdsdr\reference\onair` | C++ Web SDR 服务 | `sdk/include/onair_plugin.h`、`server/web/web_hub.cpp`、`server/web/web_dsp.h` |
| SatDump | `d:\mbdsdr\reference\satdump` | C++ 卫星接收/处理管线 | `src-core/pipeline/pipeline.h`、`pipeline.cpp`、`module.cpp`、`pipeline_run.cpp`、`src-core/common/widgets/pipeline_selector.cpp` |
| goestools | `d:\mbdsdr\reference\goestools` | GOES LRIT/HRIT 接收解码 | `src/decoder/packetizer.h`、`src/goesrecv/demodulator.cc` |
| xrit-rx | `d:\mbdsdr\reference\xrit-rx` | Python LRIT/HRIT 接收 | `src/demuxer.py` |
| noaa-apt | `d:\mbdsdr\reference\noaa-apt` | Rust NOAA APT 解码 | `src/decode.rs` |
| meteor_demod | `d:\mbdsdr\reference\meteor_demod` | Meteor LRPT 解调器（C） | `dsp/agc.c`、`dsp/pll.c`、`dsp/filter.c` |
| meteor_decode | `d:\mbdsdr\reference\meteor_decode` | Meteor LRPT 解码（C） | `decode.c`、`channel.c`、`channel.h` |
| gpredict | `d:\mbdsdr\reference\gpredict` | GTK 卫星跟踪/地面站控制 | `src/gtk-azel-plot.c`、`src/gtk-polar-plot.c`、`src/gtk-rot-ctrl.c`、`src/first-time.c` |
| stellarium | `d:\mbdsdr\reference\stellarium` | Qt/OpenGL 天象馆 | `src/StelMainView.cpp` |
| stellarium-web-engine | `d:\mbdsdr\reference\stellarium-web-engine` | WebAssembly 天球引擎（C） | `src/projection.c`、`src/modules/labels.c`、`src/labels.h` |
| wsjtx | `d:\mbdsdr\reference\wsjtx\967c85a6119110788b0df3782cf796485b2cee14` | **只含官方 FT4 样本与解码证据，不是源码树** | `official_ft4_decode_evidence.json`、`scan_official_ft4.py`、`ft4_messages.txt` |
| wspr_audio | `d:\mbdsdr\reference\wspr_audio` | WSPR 参考编解码/测试工具 | `refsrc/fano.c`、`refsrc/wsprsim_utils.c` |
| rtl_433 | `d:\mbdsdr\reference\rtl_433` | 433MHz 设备协议解码器集 | `include/r_device.h`、`include/bitbuffer.h`、`src/r_api.c`、`src/devices/new_template.c` |
| sigmf | `d:\mbdsdr\reference\sigmf` | IQ 录制元数据规范 | `sigmf-schema.json` |
| openngc | `d:\mbdsdr\reference\openngc` | 深空天体目录 | `NGC.csv` |
| deepseek-harness | `d:\mbdsdr\reference\deepseek-harness` | TypeScript Agent 运行时 | `docs/architecture.md`、`packages/core/agent-loop`、`packages/core/tool-executor-native` |
| dvbs2_bch_external | `d:\mbdsdr\reference\dvbs2_bch_external` | DVB-S2 BCH 参考实现（GPL） | `upstream_rx/include/dvbs2rx/bch_decoder_bb.h` |
| dvbs2_ldpc_external | `d:\mbdsdr\reference\dvbs2_ldpc_external` | DVB-S2 LDPC 参考实现（GPL） | 同源工程 |
| glasswork | `d:\mbdsdr\reference\glasswork` | 纯 CSS/JS「液态玻璃」演示 | `glass.css`、`adapt.js`、`refract.js` |
| glasswork_old_0127 | `d:\mbdsdr\reference\glasswork_old_0127` | 旧版玻璃演示 | 已被 `glasswork` 取代，本批不单独评 |

---

## 2. QGIS 3.44.15

### 2.1 该加 / 待定

| # | 来源（项目 / 路径 / 符号） | 它解决什么问题 | 我们项目里的对应位置（文件 / 符号） | 判断 | 理由 |
| --- | --- | --- | --- | --- | --- |
| Q1 | QGIS `src/gui/qgsmessagebar.h` `QgsMessageBar`；`qgisinterface.h` 暴露的消息接口 | 非模态通知条：错误/进度/可带动作，不打断操作 | `desktop/main_window.py`（无消息条，错误仍散落为 `QMessageBox`）；`mbdsdr_ai/error_catalog.py` `ErrorSpec`、`suggestion()` | **该加** | `error_catalog` 已经能给出可执行建议，但呈现层只有模态框。采集/扫频时弹模态框会打断接收；有 remedy 的错误应带按钮，无 remedy 的只进日志。P1。 |
| Q2 | QGIS `src/core/qgstaskmanager.h` `QgsTaskManager`、`QgsTask`（依赖/取消/进度聚合） | 长任务的注册、依赖、取消与进度统一管理 | `desktop/` 无 `JobRegistry` 等价物；当前各面板自行起线程 | **待定** | 收益真实（状态栏能看到「正在跑什么」、设备断开统一取消），但涉及多面板改造，成本中。先记触发条件：当出现「断设备后残留任务」或「并发任务互相抢设备」的实测缺陷时再做。P3。 |
| Q3 | QGIS `src/gui/qgspanelwidgetstack.h` `QgsPanelWidgetStack`；`src/app/qgisapp.cpp` 的 dock 创建路径 | 面板由注册表/工厂构造，而不是主窗口逐个 `new` | `desktop/panel_registry.py` `PanelSpec`、`PanelRegistry`（`PanelSpec` 目前只有元数据，无 factory） | **该加** | 我们已是声明式登记，但构造仍硬编码在 `MainWindow`。给 `PanelSpec` 加 `factory`，新增面板只改一处，为可插拔铺路。P2。 |
| Q4 | QGIS `src/core/qgsdistancearea.h` `QgsDistanceArea` / `QgsGeodesic`；`src/gui/qgsrubberband.h` `QgsRubberBand`（量算交互） | 椭球面距离/方位与地图量算 | `mbdsdr_ai/adsb_map.py` `haversine_km`；`desktop/osm_map.py` `OsmMapView`（无量算工具） | **待定** | 球面 haversine 在千公里级有可见误差；量算工具是地图的自然交互。但当前没有「量距离」的明确需求，先不做。P2，触发条件：用户提出测距/覆盖范围核对。 |
| Q5 | QGIS `src/app/qgspluginregistry.h` `QgsPluginRegistry`；插件 `initGui` 能力声明 | 插件声明自己需要什么能力，宿主据此挂载界面 | `mbdsdr_ai/plugin_system.py`、`mbdsdr_ai/plugin_manager.py`；`desktop/panel_registry.py` `PanelRegistry` | **待定** | 我们的插件是内部静态注册，没有第三方生态。借「能力声明」概念即可，不需要 QGIS 的插件注册表/下载器。触发条件：出现需要按能力动态装配面板的插件。 |

### 2.2 不该加（已具备，防止重做）

| # | 来源（项目 / 路径 / 符号） | 它解决什么问题 | 我们项目里的对应位置（文件 / 符号） | 判断 | 理由 |
| --- | --- | --- | --- | --- | --- |
| Q6 | QGIS `src/core/locator/qgslocator.h`、`qgslocatorfilter.h` `QgsLocator`/`QgsLocatorFilter` | 统一搜索（命令/图层/地点） | `desktop/search_providers.py` `SearchProvider`、`PROVIDERS`；`desktop/command_palette.py` | **不该加（已具备）** | 我们已有声明式 provider 注册表，新增搜索源只加一行；不要引入 QGIS 的 model/view/异步线程实现。 |
| Q7 | QGIS `src/core/settings/qgssettings.h` `QgsSettings` 的 `defaults_override` / 用户显式设置语义 | 改默认值不误伤老用户；管理员可统一默认 | `desktop/settings.py` `EXPLICIT_KEYS_FIELD`；`desktop/config_schema.py` | **不该加（已具备）** | 「用户显式设置过」标记已落地，且我们的 JSON+schema 比 INI 更可测。只借概念，不借存储层。 |
| Q8 | QGIS 资源/翻译分发：`qgis_*.qm` + `qtbase_*.qm` | 标准控件文案跟随界面语言 | `desktop/l10n.py` `install_qt_translator`、`qt_translations_dirs` | **不该加（已具备）** | qtbase 翻译已加载；继续用我们的 `desktop_*.qm`。 |
| Q9 | QGIS 10.5 节「星下点、地面轨迹与覆盖圈」概念（`QgsDistanceArea` + 画布叠加） | 卫星在地球上的位置与覆盖范围 | `mbdsdr_ai/ground_track.py` `satellite_subpoint`、`ground_track`、`split_at_dateline`、`satellite_footprint`；`desktop/sat_ground_map.py` `sat_ground_track` | **不该加（已具备）** | 星下点/轨迹/覆盖圈已实现并接入地图；跨 180° 已按段绘制。不要再按 QGIS 的 GIS 模型重做。 |
| Q10 | QGIS `src/core/settings` + `QgsApplication::setStyleSheet` | 主题/样式集中管理 | `desktop/tokens.py`（令牌 → QSS 生成） | **不该加（已具备）** | 令牌化 QSS 比 QGIS 的主题目录更可控，保留我们的。 |

### 2.3 不该照搬

| # | 来源（项目 / 路径 / 符号） | 它解决什么问题 | 我们项目里的对应位置（文件 / 符号） | 判断 | 理由 |
| --- | --- | --- | --- | --- | --- |
| Q11 | QGIS `src/core/expression`（`QgsExpression`、`QgsExpressionContext`）、`src/core/proj` + PROJ + `srs6.db`、`src/core/symbology`（`QgsSymbolLayerRegistry`、`QgsFeatureRenderer`）、`src/3d`（`Qgs3DMapScene`）、`src/core/layout`、`src/analysis` / `processing` | 通用 GIS 平台能力：表达式、投影、符号、3D、排版、批处理 | 我们对应的是 Python 计算、WGS84 固定输入、`QPainter` 自绘、2D 地图 | **不该照搬** | 这些都是「任意数据、任意来源、任意用户」的通用性设计。我们的输入是确定的（SDR 设备、WGS84、自家格式），抽象成本没有对应收益。详见 QGIS 研究第 11 章。 |
| Q12 | QGIS `python/pyplugin_installer`、`src/core/auth`（`QgsAuthManager`、OAuth2、证书库） | 插件下载安装、企业认证 | 内部静态注册；远端依赖是 TLE/地图瓦片 | **不该照搬** | 没有第三方生态，开放下载 = 供应链与崩溃隔离风险；认证框架远超匿名/API key 需求。 |

---

## 3. SDR 基座：SDR++ / gqrx / onair

### 3.1 该加 / 待定

| # | 来源（项目 / 路径 / 符号） | 它解决什么问题 | 我们项目里的对应位置（文件 / 符号） | 判断 | 理由 |
| --- | --- | --- | --- | --- | --- |
| S1 | SDR++ `core/src/dsp/chain.h` `dsp::chain`（运行时启用/禁用 block 并重连前后节点） | 不重启数据流即可增删 DSP 环节 | `mbdsdr_ai/module_system.py` `SignalGraph`、`ModuleRegistry`；`desktop/receive_pipeline.py` | **待定** | 我们的 `SignalGraph` 已有图模型，但运行时重连的动态性不如 SDR++。先记触发条件：出现「切解调器必须重建整条链」的性能/状态缺陷时再做。 |
| S2 | onair `server/web/web_hub.cpp` `WebHub::setLimits`、`HubLimits`（`queueBytes` 有界队列、每监听者配额、丢弃计数） | 多客户端共享采集时防止慢客户端拖垮主机 | `mbdsdr_ai/web_bridge.py` `WebBridge`；`mbdsdr_ai/mobile_server.py` | **待定** | 机制正确，但我们当前是局域网单/少客户端。触发条件：开放公网或多用户 Web SDR 时，必须有有界队列与每用户配额，不能无界缓冲。 |
| S3 | onair `server/web/web_dsp.h` `SharedFft`、`SliceIq`、`VirtualRx`（一份 FFT 服务多个切片） | 多用户共享频谱计算，避免重复 FFT | `desktop/spectrum_widget.py`；`mbdsdr_ai/web_bridge.py` | **待定** | 与 S2 同一场景（公网多用户）。单机单窗口下收益为负（多一层间接）。 |

### 3.2 不该加（已具备，防止重做）

| # | 来源（项目 / 路径 / 符号） | 它解决什么问题 | 我们项目里的对应位置（文件 / 符号） | 判断 | 理由 |
| --- | --- | --- | --- | --- | --- |
| S4 | SDR++ `core/src/signal_path/source.h` `SourceManager`（select/start/stop/tune） | 输入源生命周期管理 | `mbdsdr_ai/device_manager.py` `OpenDevice`、`DeviceInfo` | **不该加（已具备）** | 设备打开/关闭/独占/断连已有统一入口与错误类型，不要再造一层 SourceManager。 |
| S5 | SDR++ `core/src/signal_path/sink.cpp` `SinkManager`（切换输出、音量/静音持久化） | 音频输出生命周期与配置 | `mbdsdr_ai/audio_out.py` `volume_pct_to_gain`、`soft_limit`；`desktop/settings.py` | **不该加（已具备）** | 音量曲线与软限幅已按实测校准，持久化在设置层。 |
| S6 | SDR++ `core/src/gui/widgets/waterfall.cpp` `WaterFall::autoRange` | 频谱/瀑布自动量程 | `desktop/spectrum_widget.py` `_auto_db_range`（`auto_db_range` 开关） | **不该加（已具备）** | 自动量程已落地并带开关。 |
| S7 | gqrx `src/applications/gqrx/receiver.h` `receiver::set_input_device` / `start` / `stop` | 切换设备时先停旧链、再建新源 | `mbdsdr_ai/device_manager.py`；`desktop/main_window.py` 收听状态机 | **不该加（已具备）** | 我们的设备切换同样走「先停后建」，并有独占 claim 与断连错误类型。 |
| S8 | gqrx `src/applications/gqrx/remote_control.cpp` `remote_control`（hamlib 风格文本命令） | 外部程序远程控制频率/模式/VFO | `mbdsdr_ai/remote_control.py` | **不该加（已具备）** | 已实现同类远程控制；继续用我们的协议与错误处理。 |

### 3.3 不该照搬

| # | 来源（项目 / 路径 / 符号） | 它解决什么问题 | 我们项目里的对应位置（文件 / 符号） | 判断 | 理由 |
| --- | --- | --- | --- | --- | --- |
| S9 | SDR++ `core/src/module.h` `ModuleInfo_t`/`Instance`/`Module_t`；`core/src/module.cpp` `loadModule` | 动态库插件 ABI 与生命周期 | `mbdsdr_ai/plugin_system.py`、`plugin_manager.py` | **不该照搬** | Python 侧用 entry point/静态注册即可；C++ 函数指针表 + 动态库校验只会增加崩溃面。借「能力自描述」概念，不借 ABI。 |
| S10 | SDR++ `core/src/config.cpp` `ConfigManager`（解析失败即重置为默认） | 配置损坏恢复 | `desktop/settings.py`；`mbdsdr_ai/version_store.py` | **不该照搬** | 直接重置会静默丢用户配置。我们已有「备份 + reason + 修复后读回」的更强路径，保留我们的。 |
| S11 | gqrx `src/applications/gqrx/receiver.cpp` 失败时回退到 `file=...zero...` 空源 | 设备打不开时界面仍能启动 | `mbdsdr_ai/error_catalog.py` `ErrorSpec`；`mbdsdr_ai/device_manager.py` `DeviceError` | **不该照搬** | 这是静默降级：用户以为在接收，实际是空源。我们要求明确报错并给可执行建议，不能伪装成正常。 |
| S12 | onair `sdk/include/onair_plugin.h` `OnAirPluginVTable` | C ABI 插件 vtable | `mbdsdr_ai/plugin_system.py` | **不该照搬** | 同上，Python 进程内插件不需要跨 ABI 边界。 |

---

## 4. 卫星接收与解码链

### 4.1 该加 / 待定

| # | 来源（项目 / 路径 / 符号） | 它解决什么问题 | 我们项目里的对应位置（文件 / 符号） | 判断 | 理由 |
| --- | --- | --- | --- | --- | --- |
| D1 | SatDump `src-core/pipeline/pipeline.h` `Pipeline`、`PipelineStep`、`PipelinePreset`、`LivePipelineCfg`；`pipeline.cpp` `loadPipelines`/`savePipelines`/`getPipelineFromID`（含 `.json.inc` 合并、缺模块告警后继续） | 用声明式配置描述「输入→模块→产物」，并按卫星/预设选择 | `mbdsdr_ai/sat_pipeline_params.py` `PipelineStage`、`SatellitePipeline`、`get_pipeline`、`list_pipelines` | **待定** | 我们已有 Python 版管线声明；SatDump 的增量价值是「配置外置 + 跨文件 include + 缺模块降级告警」。等出现「加一颗卫星要改代码」的痛点时再迁移。 |
| D2 | meteor_demod `dsp/pll.c` `_locked`、`_locked_once`；`dsp/agc.c` 增益状态 | 解调器锁状态/置信度可用于 UI 与日志 | `mbdsdr_ai/signal_quality.py` `signal_quality`、`squelch_gate` | **该加** | 我们的质量评估只有 IQ 层统计，缺少「载波/符号定时是否锁定」的显式状态。用户在弱信号下需要知道「没锁」还是「锁了但误码高」。小改动，优先做。 |
| D3 | goestools `src/decoder/packetizer.h` `Packetizer::Details`（`viterbiBits`、`reedSolomonBytes`、`ok`、`relativeTime`） | 每帧携带 FEC 纠正量与时间，用于质量统计 | `mbdsdr_ai/goes_lrit.py` `VCDU`、`TransportPDU`（该模块明确只吃 RS 译码后数据，不实现 Viterbi/RS） | **待定** | 机制值得借：把「纠正了多少字节」暴露到质量面板。但要先核实上游 RS/Viterbi 是否已记录纠正量；没核实前不动。 |
| D4 | xrit-rx `src/demuxer.py` `Demuxer`、`Channel`（按 VCID 分发、VCDU 连续性、M_PDU/CP_PDU、TP_File 组装、fill/blacklist、丢包统计） | CCSDS 数据流的分用与文件级重组 | `mbdsdr_ai/lrpt_vcdu.py` `VcduPacket`、`GapRange`、`ImageReassembly`、`fill_missing_lines`；`mbdsdr_ai/goes_lrit.py` `VCDU`、`TransportPDU`、`LRIHeader`、`LRITParser`、`HRITParser` | **待定** | 包级连续性/丢包/补黑条我们已有；文件级 CP_PDU/TP_File 重组是否覆盖需逐条对照（见「未核实」）。 |

### 4.2 不该加（已具备，防止重做）

| # | 来源（项目 / 路径 / 符号） | 它解决什么问题 | 我们项目里的对应位置（文件 / 符号） | 判断 | 理由 |
| --- | --- | --- | --- | --- | --- |
| D5 | meteor_decode `decode.c` 状态机（`READ`/`PARSE_MPDU`/`VIT_SECOND`、`_rs`/`_vit`）、`channel.c` `channel`（MCU/MPDU 序列对齐、丢 strip 补黑条） | Meteor LRPT 从 CADU 到图像 | `mbdsdr_ai/lrpt_pipeline.py`；`mbdsdr_ai/lrpt_fec.py` `viterbi_decode`；`mbdsdr_ai/lrpt_image.py` `ChannelImage`；`mbdsdr_ai/lrpt_vcdu.py` `reassemble_image_stream`、`fill_missing_lines` | **不该加（已具备）** | 整条链已实现并有丢包/补块处理。不要再从 C 版复制一套并行实现。 |
| D6 | noaa-apt `src/decode.rs` `decode`、`find_sync`、`generate_sync_frame` | APT 同步帧检测与重同步 | `mbdsdr_ai/noaa_apt_lite.py` `decode_apt`、`find_line_starts` | **不该加（已具备）** | 同步检测已落地；Rust 实现只作为行为对照，不移植。 |
| D7 | goestools `src/goesrecv/demodulator.cc`（AGC→Costas→RRC→clock recovery→quantization 固定顺序） | 解调链的阶段编排 | `mbdsdr_ai/lrpt_modem.py`、`mbdsdr_ai/coarse_sync.py`、`mbdsdr_ai/cfo.py` | **不该加（已具备）** | 链路阶段与顺序一致；我们的实现已按本仓库的采样率/接口适配，照搬 C++ 只会引入重复。 |
| D8 | SatDump `src-core/module.cpp` `registerModules`、`getModuleInstance`、`RegisterModulesEvent`；`module_ccsds_simple_psk_decoder.h` | 模块注册表与按 id 取实例 | `mbdsdr_ai/decoder_registry.py` `DecoderDescriptor`、`register`、`get_decoder`；`mbdsdr_ai/module_system.py` `ModuleRegistry` | **不该加（已具备）** | 我们已有两级注册（解码器 + 信号模块）。只需要在新增解码器时复用注册入口，不要另建注册表。 |

### 4.3 不该照搬

| # | 来源（项目 / 路径 / 符号） | 它解决什么问题 | 我们项目里的对应位置（文件 / 符号） | 判断 | 理由 |
| --- | --- | --- | --- | --- | --- |
| D9 | SatDump 整套 C++ DSP block graph + 插件加载 | 通用卫星处理框架 | `mbdsdr_ai/module_system.py` | **不该照搬** | 我们是 Python 实时流 + 少量 C 加速；移植整套 block graph 会引入跨语言生命周期与线程模型，收益不抵成本。 |

---

## 5. 卫星追踪 / 天球 / 业余无线

### 5.1 该加 / 待定

| # | 来源（项目 / 路径 / 符号） | 它解决什么问题 | 我们项目里的对应位置（文件 / 符号） | 判断 | 理由 |
| --- | --- | --- | --- | --- | --- |
| T1 | gpredict `src/gtk-azel-plot.c` `gtk_azel_plot_new`、`calculate_graph_points`、`on_draw`；`src/gtk-polar-plot.c` `gtk_polar_plot_new`、`azel_to_xy` | 极坐标过顶图：AOS/LOS、方位/仰角随时间变化 | `desktop/sat_track_panel.py`（只有 AOS/LOS 倒计时与列表，无极坐标图） | **该加** | `sat_passes.py` 已能给出过境时刻与仰角，缺的只是把一次过境画成极坐标轨迹。用户能直接看出「什么时候朝哪个方向」。中低成本，P2。 |
| T2 | wsjtx `official_ft4_decode_evidence.json`、`scan_official_ft4.py`、`ft4_messages.txt` | 官方 FT4 样本 + 解码结果 + 来源 commit/SHA-256 金标 | `mbdsdr_ai/ft4_codec.py`、`mbdsdr_ai/ft4_modem.py`；测试 `test_ft4_codec.py`、`test_ft4_modem.py` | **该加（仅测试素材）** | 这是外部真值，不是实现。把官方证据登记为对拍用例，能防止「自编码自解码」的循环自证。只登记测试，不改产品逻辑。 |
| T3 | wspr_audio `refsrc/fano.c` `fano`、`refsrc/wsprsim_utils.c`（消息↔符号、callsign hash） | WSPR 参考解码与消息编码 | `mbdsdr_ai/wspr_codec.py` | **待定** | 可做对拍参考，但当前 WSPR 链已有测试；先记触发条件：出现解码结果与官方工具不一致时，用它对拍定位。 |

### 5.2 不该加（已具备，防止重做）

| # | 来源（项目 / 路径 / 符号） | 它解决什么问题 | 我们项目里的对应位置（文件 / 符号） | 判断 | 理由 |
| --- | --- | --- | --- | --- | --- |
| T4 | gpredict `src/gtk-rot-ctrl.c`、rotator/rig 配置抽象 | 旋转器/电台控制抽象 | `mbdsdr_ai/gimbal.py` `RotctldClient`、`GimbalMode`、`GimbalPose`、`GimbalStatus` | **不该加（已具备）** | 已有 rotctld 与板载两种后端与统一状态模型，不需要 GTK 的 rotator 抽象。 |
| T5 | gpredict `src/first-time.c`（首启解压卫星数据/配置模板） | 首次运行资源初始化 | `mbdsdr_ai/startup_sequence.py` `StartupSequence` | **不该加（已具备）** | 启动序列已覆盖数据与配置初始化。 |
| T6 | stellarium-web-engine `src/modules/labels.c` `labels_add`、`labels_add_3d`（`priority` 冲突消解）、`src/labels.h` | 标签在密集天区按优先级避让 | `desktop/rf_sky_view.py`（标签分层/优先级/避让） | **不该加（已具备）** | 我们已有标签优先级与避让；不要引入 WebGL 引擎的标签系统。 |
| T7 | stellarium `src/StelMainView.cpp` FPS timer / cursor timeout / 渲染节奏 | 天球视图的帧率与交互状态 | `desktop/rf_sky_view.py`、Qt 定时器 | **不该加（已具备）** | Qt 侧已有对应的刷新与交互处理；Stellarium 的 OpenGL 主循环不适用。 |

### 5.3 不该照搬

| # | 来源（项目 / 路径 / 符号） | 它解决什么问题 | 我们项目里的对应位置（文件 / 符号） | 判断 | 理由 |
| --- | --- | --- | --- | --- | --- |
| T8 | stellarium-web-engine `src/projection.c` `projection_klass_t`、`proj_register_`（投影注册表）；`src/hip.c` 星表加载 | 可插拔投影与星表 | `mbdsdr_ai/celestial_geometry.py`（固定四种天球投影） | **不该照搬** | 我们只需要固定几种投影；注册表与 WASM 星表加载是为通用天球引擎设计的，移植收益为零。 |
| T9 | stellarium 整套 OpenGL 场景图/材质/大气渲染 | 通用天象馆渲染 | `desktop/rf_sky_view.py` | **不该照搬** | 依赖重、与 Widgets 体系并行；我们的天球视图用 2D 绘制已满足指向需求。 |
| T10 | wsjtx 的 Qt UI / 解码列表交互 | 业余无线客户端界面 | `desktop/` 现有 FT8/FT4 面板 | **不该照搬** | 本克隆只含证据文件，没有源码；且 UI 形态与我们的面板体系不同。只借金标数据（T2）。 |

### 5.4 未核实

| # | 来源（项目 / 路径 / 符号） | 它解决什么问题 | 我们项目里的对应位置（文件 / 符号） | 判断 | 理由 |
| --- | --- | --- | --- | --- | --- |
| T11 | gpredict 卫星转发器/频率库（本轮在 `d:\mbdsdr\reference\gpredict` 中未找到 transponder 数据文件） | 每颗卫星的上下行频率/模式 | `mbdsdr_ai/data/sat_links.json`；`mbdsdr_ai/sat_links.py` | **未核实** | 仅核实到 `data/satdata/satellites.dat` 为 TLE 条目，未见到转发器数据。不能据此说「gpredict 有/没有」；如需借用，需另找其数据仓库或运行时生成文件。 |

---

## 6. 协议解码 / 元数据 / Agent 运行时

### 6.1 该加 / 待定

| # | 来源（项目 / 路径 / 符号） | 它解决什么问题 | 我们项目里的对应位置（文件 / 符号） | 判断 | 理由 |
| --- | --- | --- | --- | --- | --- |
| P1 | rtl_433 `include/r_device.h` `r_device` 的 `fields`、`disabled`、`decode_events`/`decode_ok`/`decode_messages`/`decode_fails[5]`；`src/r_api.c` `r_devices`、`register_protocol` | 每个协议声明输出字段、默认启停、运行统计 | `mbdsdr_ai/rtl433_decoder.py` `DeviceDecoder`、`register_device`、`PulseDemodulator` | **该加** | 我们已有解码器注册与脉冲解调，但缺「每协议字段表 + 默认禁用开关 + 成功/失败计数」。这三点直接改善 CSV/JSON 输出与现场排障。小改动。 |
| P2 | deepseek-harness `docs/architecture.md` 的工具事件链（`tools/pre-execute` → `tools/execute` → `tools/post-execute` → `tool/result`）；`packages/core/tool-executor-native` | 工具调用前后可插拔的策略/审计接缝 | `mbdsdr_ai/hooks.py` `EventType.AGENT_PRE_TOOL_USE`、`AGENT_TOOL_RESULT`、`SUBAGENT_*`；`mbdsdr_ai/tool_registry.py` | **待定** | 我们的钩子已覆盖前后事件；差距在「策略/审计是否强制走同一接缝」。先核对一次调用链，若存在绕行再收敛；不要照搬 Cordis 插件树。 |

### 6.2 不该加（已具备，防止重做）

| # | 来源（项目 / 路径 / 符号） | 它解决什么问题 | 我们项目里的对应位置（文件 / 符号） | 判断 | 理由 |
| --- | --- | --- | --- | --- | --- |
| P3 | rtl_433 `include/bitbuffer.h` `bitbuffer_t`（行/列、同步计数、原始位） | 脉冲解码的位缓冲 | `mbdsdr_ai/rtl433_decoder.py` `PulseDemodulator`（内部位缓冲） | **不该加（已具备）** | 位缓冲已内建；只需要补 P1 的元数据与统计。 |
| P4 | sigmf `sigmf-schema.json`（`global`/`captures`/`annotations`、`core:datatype`、`core:version` 等） | IQ 录制元数据规范 | `mbdsdr_ai/recording_meta.py` `build_sigmf_meta`、`write_sigmf_meta`、`read_sigmf_meta`、`SigMFRecordingSession` | **不该加（已具备）** | 已按 SigMF 字段落盘并可读回。除非要对外交换数据集，否则不必引入 JSON Schema 运行时校验。 |
| P5 | openngc `NGC.csv`（分号分隔列：名称/类型/坐标/星等/别名） | 深空天体目录 | `mbdsdr_ai/data/dso_catalog.json`（`meta.schema = mbdsdr.dso_catalog.v2`，来源 OpenNGC，带许可与 sha256） | **不该加（已用）** | 已用 OpenNGC 构建派生目录并登记来源/许可/哈希。不要再直接读 CSV。 |

### 6.3 不该照搬

| # | 来源（项目 / 路径 / 符号） | 它解决什么问题 | 我们项目里的对应位置（文件 / 符号） | 判断 | 理由 |
| --- | --- | --- | --- | --- | --- |
| P6 | glasswork `glass.css`（`backdrop-filter`、`contrast()`、`--glass-lens`）、`adapt.js`、`refract.js` | 液态玻璃视觉：模糊、折射、随背景自适应对比 | `desktop/tokens.py`（Qt 令牌 → QSS） | **不该照搬** | Qt Widgets 没有 `backdrop-filter`/折射；强行在桌面端模拟会带来持续重绘与可读性风险。Web 侧如要做，只借「随背景调对比」的思路，不搬 CSS 滤镜链。 |
| P7 | deepseek-harness Cordis 插件树 / 可替换组件注册 | 运行时组件装配 | `mbdsdr_ai/plugin_system.py`、`module_system.py` | **不该照搬** | 我们是进程内 Python 模块；Cordis 的插件树解决的是 TypeScript 生态的可替换性，移植会引入不必要的间接层。 |
| P8 | dvbs2_bch_external `upstream_rx/include/dvbs2rx/bch_decoder_bb.h`（GPL 参考实现） | DVB-S2 BCH 译码参考 | `mbdsdr_ai/dvbs2_bch.py`、`mbdsdr_ai/bch_codecs.py` | **不该照搬（只对拍）** | 参考树带 GPL 头，直接抄代码会把许可传染进本仓库。只允许用它生成/核对测试向量（KAT），实现保持自有。 |
| P9 | dvbs2_ldpc_external（同源 GPL 工程） | DVB-S2 LDPC 参考 | `mbdsdr_ai/dvbs2_ldpc.py` | **不该照搬（只对拍）** | 同上：只做交叉验证，不复制实现。 |

---

## 7. 建议落地顺序（只列「该加」）

按「用户能否多做成一件事 / 成本 / 是否阻塞其他项」排序。**本批不动手，等批准。**

| 顺序 | 事项 | 来源 | 成本 | 验收判据 |
| --- | --- | --- | --- | --- |
| 1 | 非模态通知条（错误/进度/可带动作） | QGIS `QgsMessageBar`（Q1） | 小 | 有 remedy 的错误出现按钮；无 remedy 的只进日志；批量导入只汇总一次 |
| 2 | `PanelSpec` 加 `factory`，面板由注册表构造 | QGIS `QgsPanelWidgetStack`（Q3） | 小 | 删除 `MainWindow` 硬编码构造后面板仍能出现/恢复布局 |
| 3 | 过境极坐标图（AOS/LOS + 方位/仰角轨迹） | gpredict `gtk_polar_plot_new`/`gtk_azel_plot_new`（T1） | 中 | 选一次过境，图上 AOS/LOS 与 `sat_passes.predict_passes` 一致；跨 0°/360° 不断线 |
| 4 | 每协议 `fields` / `disabled` / 成功失败计数 | rtl_433 `r_device`（P1） | 小 | 同一输入，开启/禁用协议输出可区分；CSV/JSON 字段与声明一致；计数可复跑 |
| 5 | 解调锁状态/置信（`locked`/`locked_once`） | meteor_demod `dsp/pll.c`（D2） | 小 | 弱信号下 UI 显示「未锁」；强信号显示「已锁」；状态与 SNR 同步变化 |
| 6 | 官方 FT4 金标对拍用例 | wsjtx 证据文件（T2） | 小 | 用例读官方 SHA-256 校验后解码，消息逐条一致；去掉解码器实现时用例必须失败 |

以下三项已记「待定」，不进入本批：JobRegistry（Q2）、椭球距离/量算（Q4）、SatDump 配置外置（D1）、goestools FEC 统计（D3）、xrit 文件级重组（D4）、onair 多用户限额（S2/S3）。

---

## 8. 未核实清单（禁止据此下结论）

> 2026-11-14 已全部核实，逐条结论与取证见 §11；本节保留原记录以便对照。

| # | 事项 | 状态 | 下一步 |
| --- | --- | --- | --- |
| U1 | gpredict 是否随包提供转发器/频率库 | 未核实（`data/satdata/satellites.dat` 仅见 TLE） | 找其数据生成流程或运行时文件后再评 |
| U2 | xrit-rx 的 CP_PDU/TP_File 文件级重组是否已被我们覆盖 | 未核实（我们已核实包级连续性/补块） | 逐函数对照 `lrpt_vcdu.ImageReassembly` 与 `Demuxer`/`Channel` |
| U3 | goestools `Packetizer::Details` 的纠正量是否已在我们的上游 RS/Viterbi 记录 | 未核实（`goes_lrit.py` 明确只吃 RS 译码后数据） | 检查上游 FEC 模块返回值 |
| U4 | deepseek-harness 的工具策略是否所有入口都走 `pre/post` 接缝 | 未核实（仅读文档与包结构） | 追一次真实工具调用链，确认无绕行 |

---

## 9. 取证索引（文件 + 符号）

### 9.1 参考侧

| 符号 | 文件 |
| --- | --- |
| `QgsMessageBar` | `d:\mbdsdr\.tmp\qgis34415\QGIS-final-3_44_15\src\gui\qgsmessagebar.h` |
| `QgsTaskManager` | `...\src\core\qgstaskmanager.h` |
| `QgsLocatorFilter` | `...\src\core\locator\qgslocatorfilter.h` |
| `QgsDistanceArea` | `...\src\core\qgsdistancearea.h` |
| `QgsRubberBand` | `...\src\gui\qgsrubberband.h` |
| `QgsPanelWidgetStack` | `...\src\gui\qgspanelwidgetstack.h` |
| `QgsPluginRegistry` | `...\src\app\qgspluginregistry.h` |
| `QgsSettings` | `...\src\core\settings\qgssettings.h` |
| `ModuleInfo_t` / `Instance` / `Module_t` / `loadModule` | `d:\mbdsdr\reference\sdrpp\core\src\module.h`、`module.cpp` |
| `SourceManager` | `d:\mbdsdr\reference\sdrpp\core\src\signal_path\source.h` |
| `SinkManager` | `d:\mbdsdr\reference\sdrpp\core\src\signal_path\sink.cpp` |
| `dsp::chain` | `d:\mbdsdr\reference\sdrpp\core\src\dsp\chain.h` |
| `ConfigManager` | `d:\mbdsdr\reference\sdrpp\core\src\config.cpp` |
| `WaterFall::autoRange` | `d:\mbdsdr\reference\sdrpp\core\src\gui\widgets\waterfall.cpp` |
| `receiver::set_input_device` / `start` / `stop` | `d:\mbdsdr\reference\gqrx\src\applications\gqrx\receiver.h` |
| `remote_control` | `d:\mbdsdr\reference\gqrx\src\applications\gqrx\remote_control.cpp` |
| `OnAirPluginVTable` | `d:\mbdsdr\reference\onair\sdk\include\onair_plugin.h` |
| `WebHub::setLimits` / `HubLimits` | `d:\mbdsdr\reference\onair\server\web\web_hub.cpp` |
| `SharedFft` / `SliceIq` / `VirtualRx` | `d:\mbdsdr\reference\onair\server\web\web_dsp.h` |
| `Pipeline` / `PipelineStep` / `PipelinePreset` | `d:\mbdsdr\reference\satdump\src-core\pipeline\pipeline.h` |
| `loadPipelines` / `savePipelines` / `getPipelineFromID` | `d:\mbdsdr\reference\satdump\src-core\pipeline\pipeline.cpp` |
| `registerModules` / `getModuleInstance` | `d:\mbdsdr\reference\satdump\src-core\module.cpp` |
| `PipelineSelector` | `d:\mbdsdr\reference\satdump\src-core\common\widgets\pipeline_selector.cpp` |
| `Packetizer` / `Packetizer::Details` | `d:\mbdsdr\reference\goestools\src\decoder\packetizer.h` |
| `Demuxer` / `Channel` | `d:\mbdsdr\reference\xrit-rx\src\demuxer.py` |
| `decode` / `find_sync` / `generate_sync_frame` | `d:\mbdsdr\reference\noaa-apt\src\decode.rs` |
| `pll` 状态（`_locked`/`_locked_once`） | `d:\mbdsdr\reference\meteor_demod\dsp\pll.c` |
| `channel` 序列对齐 | `d:\mbdsdr\reference\meteor_decode\channel.c` |
| `gtk_azel_plot_new` / `calculate_graph_points` | `d:\mbdsdr\reference\gpredict\src\gtk-azel-plot.c` |
| `gtk_polar_plot_new` / `azel_to_xy` | `d:\mbdsdr\reference\gpredict\src\gtk-polar-plot.c` |
| `gtk_rot_ctrl` 等 rotator 抽象 | `d:\mbdsdr\reference\gpredict\src\gtk-rot-ctrl.c` |
| `first-time` 资源初始化 | `d:\mbdsdr\reference\gpredict\src\first-time.c` |
| `labels_add` / `labels_add_3d` | `d:\mbdsdr\reference\stellarium-web-engine\src\modules\labels.c` |
| `projection_klass_t` / `proj_register_` | `d:\mbdsdr\reference\stellarium-web-engine\src\projection.c` |
| `StelMainView` | `d:\mbdsdr\reference\stellarium\src\StelMainView.cpp` |
| 官方 FT4 证据 | `d:\mbdsdr\reference\wsjtx\967c85a6119110788b0df3782cf796485b2cee14\official_ft4_decode_evidence.json` |
| `fano` | `d:\mbdsdr\reference\wspr_audio\refsrc\fano.c` |
| `r_device` | `d:\mbdsdr\reference\rtl_433\include\r_device.h` |
| `r_devices` / `register_protocol` | `d:\mbdsdr\reference\rtl_433\src\r_api.c` |
| `bitbuffer_t` | `d:\mbdsdr\reference\rtl_433\include\bitbuffer.h` |
| SigMF schema | `d:\mbdsdr\reference\sigmf\sigmf-schema.json` |
| OpenNGC 目录 | `d:\mbdsdr\reference\openngc\NGC.csv` |
| 工具事件链 | `d:\mbdsdr\reference\deepseek-harness\docs\architecture.md` |
| `glass.css` | `d:\mbdsdr\reference\glasswork\glass.css` |
| `bch_decoder_bb` | `d:\mbdsdr\reference\dvbs2_bch_external\upstream_rx\include\dvbs2rx\bch_decoder_bb.h` |

### 9.2 我们这边

| 符号 | 文件 |
| --- | --- |
| `PanelSpec` / `PanelRegistry` | `desktop/panel_registry.py` |
| `_auto_db_range` / `auto_db_range` | `desktop/spectrum_widget.py` |
| `SearchProvider` / `PROVIDERS` | `desktop/search_providers.py` |
| `command_palette` 统一索引 | `desktop/command_palette.py` |
| `install_qt_translator` | `desktop/l10n.py` |
| `EXPLICIT_KEYS_FIELD` | `desktop/settings.py` |
| `OsmMapView` | `desktop/osm_map.py` |
| `sat_ground_track` | `desktop/sat_ground_map.py` |
| `sat_track_panel`（AOS/LOS 列表） | `desktop/sat_track_panel.py` |
| `rf_sky_view`（标签优先级） | `desktop/rf_sky_view.py` |
| `tokens`（令牌 → QSS） | `desktop/tokens.py` |
| `satellite_subpoint` / `ground_track` / `split_at_dateline` / `satellite_footprint` | `mbdsdr_ai/ground_track.py` |
| `haversine_km` | `mbdsdr_ai/adsb_map.py` |
| `ErrorSpec` / `suggestion` | `mbdsdr_ai/error_catalog.py` |
| `volume_pct_to_gain` / `soft_limit` | `mbdsdr_ai/audio_out.py` |
| `OpenDevice` / `DeviceInfo` / `DeviceError` | `mbdsdr_ai/device_manager.py` |
| `remote_control` | `mbdsdr_ai/remote_control.py` |
| `plugin_system` / `plugin_manager` | `mbdsdr_ai/plugin_system.py`、`mbdsdr_ai/plugin_manager.py` |
| `ModuleRegistry` / `SignalGraph` | `mbdsdr_ai/module_system.py` |
| `DecoderDescriptor` / `get_decoder` | `mbdsdr_ai/decoder_registry.py` |
| `PipelineStage` / `SatellitePipeline` / `get_pipeline` | `mbdsdr_ai/sat_pipeline_params.py` |
| `VcduPacket` / `GapRange` / `ImageReassembly` / `fill_missing_lines` | `mbdsdr_ai/lrpt_vcdu.py` |
| `ChannelImage` | `mbdsdr_ai/lrpt_image.py` |
| `viterbi_decode` | `mbdsdr_ai/lrpt_fec.py` |
| `VCDU` / `TransportPDU` / `LRIHeader` / `LRITParser` / `HRITParser` | `mbdsdr_ai/goes_lrit.py` |
| `decode_apt` / `find_line_starts` | `mbdsdr_ai/noaa_apt_lite.py` |
| `signal_quality` / `squelch_gate` | `mbdsdr_ai/signal_quality.py` |
| `predict_passes` / `SatellitePass` / `GroundStation` | `mbdsdr_ai/sat_passes.py` |
| `RotctldClient` / `GimbalPose` / `GimbalStatus` | `mbdsdr_ai/gimbal.py` |
| `StartupSequence` | `mbdsdr_ai/startup_sequence.py` |
| `DeviceDecoder` / `PulseDemodulator` / `register_device` | `mbdsdr_ai/rtl433_decoder.py` |
| `build_sigmf_meta` / `write_sigmf_meta` / `read_sigmf_meta` | `mbdsdr_ai/recording_meta.py` |
| `EventType.AGENT_PRE_TOOL_USE` / `AGENT_TOOL_RESULT` | `mbdsdr_ai/hooks.py` |
| `dvbs2_bch` / `bch_codecs` | `mbdsdr_ai/dvbs2_bch.py`、`mbdsdr_ai/bch_codecs.py` |
| `dvbs2_ldpc` | `mbdsdr_ai/dvbs2_ldpc.py` |
| `dso_catalog`（OpenNGC 派生） | `mbdsdr_ai/data/dso_catalog.json` |
| `sat_links`（卫星链路表） | `mbdsdr_ai/data/sat_links.json`、`mbdsdr_ai/sat_links.py` |

---

## 10. 结论摘要

- **真正的新增点只有 6 条**（第 7 节）：非模态通知条、面板工厂、过境极坐标图、rtl_433 每协议元数据/统计、解调锁状态、FT4 官方金标对拍。
- **最大价值是「不该加（已具备）」那批**：搜索 provider、显式设置标记、qtbase 翻译、星下点/轨迹/覆盖圈、自动量程、远程控制、模块注册、SigMF、OpenNGC、工具前后钩子等，都已经有等价或更强实现。后来人不要再按参考项目重做一遍。
- **明确的反模式**：设备失败回退空源（gqrx）、配置损坏直接重置（SDR++）、C++ 插件 ABI（SDR++/onair）、GPL 代码直接复制（dvbs2 两个参考树）。
- **未核实 4 项**：2026-11-14 已全部核实，见 §11（不再沿用「不要当作结论使用」，但 §8 原记录保留）。

---

## 11. 未核实项核实结果（2026-11-14）

口径同 §0：只写读到的符号与文件；读不到就写「卡在哪」。

### U1 gpredict 转发器库

**结论：机制在源码里，数据不在克隆里；对我们不采纳（策略原因，不是能力缺口）。**

- 参考侧（已读到）：
  - `src/trsp-conf.c::read_transponders(catnum)` 从 `trsp_file_name()` 指向的 `USER_CONF_DIR/trsp/<catnum>.trsp` 读 GKeyFile，逐个 group 解析成 `trsp_t`（`src/trsp-conf.h` 声明；`check_trsp_freq` 规范化 `downlow/downhigh/uplow/uphigh`）。
  - `src/first-time.c::first_time_check_step_08` 只**创建** `USER_CONF_DIR/trsp` 目录（注释第 85–86 行），不投放任何数据。
  - 克隆内 `data/` 只有 TLE：`satdata/satellites.dat` + `satdata/*.cat` + `locations.dat` + `Amateur.mod`，**无 `.trsp` 文件**。
  - 消费端：`src/gtk-rig-ctrl.c`（`ctrl->trsplist = read_transponders(...)`、`Lock Transponder` 上下行联动）、`src/sat-info.c:69`。
- 我们这边：`mbdsdr_ai/data/sat_links.json` 的 `_note` 明确只收下行（「不含任何上行：上行涉及发射，一律走 tc_uplink 的外部确认 token 门」），消费方 `mbdsdr_ai/sat_links.py`。
- 判断：**不该加**。转发器库的核心增量是上下行配对与反相转发器换算，而上行被我们的发射策略禁止；下行频点 `sat_links.json` 已有。若将来要补「下行带宽/模式」字段，可参考 `trsp_t` 的字段划分，但不引入上行语义。

### U2 xrit-rx 的 CP_PDU/TP_File 文件级重组覆盖

**结论：包级与文件级重组已覆盖；三处差额（端到端 CRC、AES 解密、失败统计）中有两处值得补。**

- 参考侧（已读到）：
  - `src/demuxer.py::Channel.data_in`：M_PDU 首头指针（`mpdu.POINTER`）、跨 M_PDU 的 CP_PDU 续传、`CP_PDU` 长度/CRC 校验。
  - `Channel.check_CPPDU`：`LENGTH`/`CRC` 逐项报错；`Channel.handle_CPPDU`：`FIRST`/`CONTINUE`/`LAST` 组装 TP_File，长度不符打印「SKIPPING FILE DUE TO DROPPED PACKETS」并丢文件；`Channel.continuity`：VCDU 计数差报「DROPPED n PACKETS」。
  - `src/ccsds.py::CP_PDU.finish(data, crclut)`：CRC-16/CCITT-FALSE，取载荷末 2 字节比对；`CCSDS.TP_File`：10 字节头（16 bit 计数器 + 64 bit 文件长度/8），`finish` 只校验长度；`CCSDS.S_PDU(data, keys)`：AES 密钥解密。
- 我们这边（已读到）：
  - `mbdsdr_ai/goes_lrit.py::_VirtualChannel.process`：VCDU 计数差 → 丢弃未完成 TP_PDU；M_PDU 首头指针切包。
  - `_consume_one_tpdu` / `_continue_in_progress`：TP_PDU 跨 VCDU 续传；`TransportPDU.verify_crc` 每包 CRC 不过即丢该 APID 状态。
  - `_SessionPDU`：按 APID 归并，`seqFlag` 1/3 起、2 止；`expected_size()` 做总长度校验；`FILL_APID` 直接丢。
- 判断：**已具备**（包级 + 文件级 + 长度校验 + 每包 CRC）。差额：
  1. 没有 CP_PDU **端到端** CRC（只有每 TP_PDU 的 CRC）——xrit-rx 的 `CP_PDU.finish` 是整文件级；
  2. 没有 `S_PDU` AES 解密——当前接入的 GOES LRIT/GK-2A/FY 源没有加密产品，**暂不做**；
  3. 丢帧/丢文件/长度不符**只静默丢弃，没有计数与原因**——这是唯一对外可见的缺口。
- 后续：1+3 合并为第二批候选 2（§12）。

### U3 goestools 纠正量是否已在上游记录

**结论：RS 纠正量上游确实已算出并保留，只是没对外呈现；Viterbi 纠正位数没有；GOES/GK-2A 在本仓没有自有 FEC 级。**

- 参考侧：`src/decoder/packetizer.h::Packetizer::Details{viterbiBits, reedSolomonBytes, ok, relativeTime}`，`nextPacket(std::array<uint8_t,892>&, Details*)` 每包回填。
- 我们这边：
  - `mbdsdr_ai/fec.py::RSResult{nerrors, nerasures, corrected}`（`ReedSolomon.decode` / `decode_with_erasures` 返回，`nerrors = -1` 表示不可纠）；
  - `mbdsdr_ai/lrpt_fec.py::LrptCaduDecoder.decode_cadu` 返回每块 `rs_errors`；`lrpt_pipeline.py::LrptDecodeResult.rs_errors` 已在 `decode_cadu` 后赋值（`lrpt_pipeline.py:186`）；
  - 全仓 `rs_errors` 的消费方只有测试（`test_lrpt_pipeline.py`、`test_lrpt_cross_impl_fixture.py`），**没有任何 UI/stats 读取**。
  - `lrpt_fec.viterbi_decode` 返回 `(decoded, rel)`（软判决可靠性），**不返回**「纠正位数」；要等价 `viterbiBits` 需在回溯时累加判决翻转，属新增实现，不假装已有。
  - `gk2a_lrit.py` 中没有 `viterbi`/`ReedSolomon` 符号（该链路在本仓只做帧/文件解析），所以这条只能落在 LRPT（Meteor）链。
- 判断：**上游已记录（RS），缺口在呈现**；进第二批候选 1（§12）。

### U4 harness 工具策略是否存在绕行

**结论：MCP 面没有绕行；进程内 builtin 工具与一处 UI 直调确实绕过了 PRE 接缝。**

- 参考侧：`docs/architecture.md:101` 的事件链 `tools/pre-execute → tools/execute → tools/post-execute → tool/result`。
- 我们这边（已读到）：
  - `mbdsdr_ai/core/mcp_server.py::ToolCallGate._pre_tool_veto`（1953 起，发 `AGENT_PRE_TOOL_USE`，进程内 HookManager + 外部命令双闸）、`ToolCallGate._emit_result_event`（1947 起，发 `AGENT_TOOL_RESULT`）；`MCPToolSet` 继承该类，调用必经。
  - 旧壳 `mbdsdr_ai_mcp_server.py:126` 共用同一 `ToolCallGate`。
  - 模型 → MCP 硬件工具：`desktop/main_window.py::_mcp_call_handler`（写操作真人确认门）→ `desktop/mcp_worker.py::call_tool_sync` → MCP 服务端 gate ⇒ **无绕行**。
  - 模型 → 进程内 builtin 工具：`mbdsdr_ai/tool_registry.py::ToolRegistry.call_from_model` 自身不发 PRE；全仓 `AGENT_PRE_TOOL_USE` 只在 `mcp_server.py` 出现；`agent.py` 只发 `AGENT_TOOL_CALL`（4074/4400 附近）与 `AGENT_TOOL_RESULT`（4040/4420 附近）⇒ **没有 pre 否决点**。
  - UI 直调：`desktop/weather_panel.py:1036` `reg.call("sat_image_enhance", ...)`，不经 agent 事件也不经 gate ⇒ **完全绕行**。
- 判断：**存在绕行**，但影响面是「进程内工具」而非时空/信号链，未进第二批；触发条件：builtin 工具开始承担设备写或工作流编排时必须先收口（把 `ToolRegistry.call` 的上游统一到 `ToolCallGate`）。

---

## 12. 第二批候选（2026-11-14）

标准：**对我们的时空链或信号链有实质提升**，宁少勿滥；第一批 6 条已落地，不重复。两条都标「该加」，第三条留空（见 §12.3 为什么不凑）。

### 12.1 每帧 FEC 纠正量进质量面（信号链）

| 项 | 内容 |
| --- | --- |
| 参考侧机制 | goestools `Packetizer::Details`：每包回填 `viterbiBits` / `reedSolomonBytes` / `ok`，把「这帧纠了多少」变成可统计的一等量 |
| 我们已有的数据 | `mbdsdr_ai/fec.py::RSResult.nerrors`；`mbdsdr_ai/lrpt_fec.py::LrptCaduDecoder.decode_cadu` 的 `rs_errors`；`mbdsdr_ai/lrpt_pipeline.py::LrptDecodeResult.rs_errors`（已在 186 行赋值，但无人消费） |
| 要加什么 | ①在 `lrpt_pipeline` 的 `stats` 里聚合：`rs_corrected_frames`（`0 < nerrors`）、`rs_max_errors`、`rs_uncorrectable_frames`（`nerrors < 0`）、`rs_mean_errors`；②`desktop/status_panel.py` 质量行显示「纠错 n 帧 / 最大 m 符号 / 不可纠 k 帧」；③（可选子项）`lrpt_fec.viterbi_decode` 回溯时累计判决翻转数，作为 `viterbi_corrected_bits` |
| 加在哪 | `mbdsdr_ai/lrpt_pipeline.py`（`stats.update` 处，298 行附近）、`desktop/status_panel.py`、可选 `mbdsdr_ai/lrpt_fec.py::viterbi_decode` |
| 为什么现在做 | D3 的上游前提刚核实完：数据本来就算出来了，只是被丢掉；用户判断「弱信号还能不能救」目前只能看锁状态（第一批第 5 条），差的就是纠错强度这一维 |
| 验收判据 | 同一段真录 IQ：`rs_errors` 序列逐块固定 → 聚合值与逐块和一致；不可纠帧与成功帧互斥且总数等于块数；摘掉聚合计算后判据必须红；可选子项单独判据（已知输入下翻转数与对拍实现一致） |
| 成本 | 小（聚合 + 一行显示）；可选子项中 |

### 12.2 文件级重组失败统计 + 端到端 CRC（信号链）

| 项 | 内容 |
| --- | --- |
| 参考侧机制 | xrit-rx `Channel.continuity`（DROPPED n PACKETS）、`Channel.check_CPPDU`（LENGTH/CRC 逐项）、`Channel.handle_CPPDU`（长度不符即「SKIPPING FILE」）、`CCSDS.CP_PDU.finish`（整包 CRC-16/CCITT-FALSE） |
| 我们已有的 | `goes_lrit._VirtualChannel.process`（丢帧时清 in-progress）、`TransportPDU.verify_crc`（每包 CRC）、`_SessionPDU.expected_size`（长度校验）——判断都有，但失败**静默丢弃**，无计数无原因 |
| 要加什么 | ①`_VirtualChannel` 维护计数：`vcdus_missing`、`tpdu_crc_fail`、`session_len_mismatch`、`session_crc_fail`、`files_dropped`；②`_SessionPDU.finish` 补 CP_PDU 级 CRC-16/CCITT-FALSE（末 2 字节），失败计入 `session_crc_fail`；③`LRITParser`/`HRITParser` 暴露 `stats`，`ToolResult_goes` 返回里带上；④状态面板/日志一行汇总 |
| 加在哪 | `mbdsdr_ai/goes_lrit.py`（`_VirtualChannel.process` / `_finish_tpdu` / `_SessionPDU` / `_BaseParser`）、`desktop/status_panel.py` |
| 为什么现在做 | U2 刚核实出「我们静默、参考侧明确报因」的差额；用户遇到「没出图」时，目前无法区分「没信号」「丢包太多」「CRC 不过」 |
| 验收判据 | 构造已知丢帧/坏 CRC/长度不符的 VCDU 序列：每类计数精确等于注入次数，成功文件数不受影响；端到端 CRC 对已知好帧不误报；摘掉计数或 CRC 校验后判据必须红 |
| 成本 | 小到中（CRC 复用 `fec.py`/`crc16_ccitt` 已有实现） |

### 12.3 未列入的候选（如实说明）

- **TLE 龄期提示**：已有（`desktop/sat_track_panel.py` 的过期后缀/日期行 + `controller.tle_age_days`），按「防止重做」不列。
- **gpredict 转发器库**：见 §11 U1，上行被策略禁止。
- **S_PDU AES 解密**：见 §11 U2，当前接入源无加密产品，触发条件=接入加密 HRIT 产品。
- **工具 PRE 接缝收口**：见 §11 U4，是运行时治理面，不是时空/信号链，单列不凑数。
- 第三条留空：其余「待定」项（Q2 JobRegistry、Q4 量算、D1 管线外置、S1 运行时重连、S2/S3 多用户限额）都不满足「对时空链或信号链有实质提升且触发条件已到」。
