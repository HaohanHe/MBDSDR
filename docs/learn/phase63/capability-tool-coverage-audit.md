# 能力域 × 工具覆盖矩阵审计（2026-10-13）

## 背景与口径

用户原话：「SDR 方面、卫星解码、多普勒、矢量网络，你都整了吗？」
本次审计逐域 grep/Read 核实「引擎/引擎侧访问器已存在、但 89 工具面无入口」的真空白，
**不凭印象**。审计后落地 1 个高价值只读工具，工具面 89 → 90。

工具真值基线（审计前）：89（49 写 / 40 读），10 个 MCP 域。
已落地勿重造（历轮）：get_ft8_history、get_adsb_aircraft、get_aprs_messages、
predict_passes、VNA 11 工具、get_color_maps、list_templates/run_template、
get_task_history、memory 族 7、AIGUI 5。

## 逐域覆盖状态

### 1. SDR 调谐 / VFO（20 工具）
- 功能面：tune/mode/bandwidth、多 VFO（list/add/switch/rename/armed/freq/mode/bw）、
  FFT 参数、色图、频谱状态、多普勒补偿开关。
- 工具面：全部有入口。get_spectrum_status 已返回频谱状态；list_vfos 已列 VFO。
- 覆盖：**全覆盖**。真空白：无。
- 真机待验：FFT/色图参数在真源上的数值回读（离线已覆盖）。

### 2. 亚音 / 数字解码（16 工具）
- 功能面：CTCSS/CDCSS 亚音、POCSAG/M17/VOR/ACARS/NAVTEX/ADS-B/FT8/LRPT/APRS 解码。
- 工具面：每个解码器族都有对应的 set_*_status / get_*_messages 只读快照。
- 覆盖：**用户点名的解码器族全覆盖**。
- 引擎侧存在但未上工具面的解码器（仅发信号、无环形缓冲快照，需加状态存储才能上工具）：
  - CW（cw_decoder_：feed/takeText，但引擎只 emit cwDecoded(text,wpm)，无查询缓冲）
  - RDS（rdsDecoder_：WFM 通道，只 emit rdsUpdated，无查询快照）
  - APT（aptDecoder_：NOAA 云图，只 emit aptImageReady，无查询快照）
  - SSTV/SSDV/FCCH：解码器存在但无工具面入口。
- 判定：这些是「引擎有解码器实例但无查询缓冲」，上工具需先在引擎加环形缓冲/快照访问器，
  属侵入式改动，非「纯只读薄封装」。本轮不强行落地（宁缺毋滥）。
- 真机待验：CW/RDS/APT 云图在真源上的实际解码率。

### 3. VNA 测量（4 工具）
- 功能面：set_vna_sweep、get_vna_data（S11/S21 复数 + VSWR/回损/阻抗/Smith 归一化阻抗/
  coax_loss）、get_vna_status、vna_device。
- 覆盖：**8 分析面的原始数据面全覆盖**。get_vna_data 已返回 return_loss_db（回损）、
  smith_z_real/imag（史密斯）、z_real/imag（阻抗）、min_vswr_at_hz。
- 真空白（vna_rf.h 有但未上工具）：groupDelayS21（S21 群时延）、permeabilityNRW（NRW 磁导率）。
- 判定：群时延/磁导率是滤波器/材料测量的进阶指标，非用户点名的 8 分析面，低优先，本轮不落地。

### 4. VNA 分析（6 工具）
- 功能面：analyze_vna_resonance（谐振）、vna_tdr_cable（TDR 测长）、
  analyze_vna_filter（滤波器）、analyze_vna_equivalent（RLC 等效）、
  analyze_vna_touchstone（Touchstone 解析）、analyze_vna_match（L 匹配/EFHW 匹配拓扑）。
- 覆盖：**用户点名的 8 分析面全部有工具**（谐振/匹配/TDR/LC/滤波器/回损/史密斯/Touchstone）。
- 真空白：无高价值项。

### 5. VNA 校准（1 工具）
- vna_calibration 状态机。覆盖：有。

### 6. 导出演制（11 工具）
- 功能面：start/stop_recording、export_iq_segment、4 类快照导出、list_recordings、
  delete_recording、export_recording、get_recording_state。
- 覆盖：**录制/导出全覆盖**。list_recordings 已列录制文件。
- 真空白：offlinePosition()（回放进度秒）/isOfflineFileActive() 引擎有访问器但无工具；
  但无 paused 状态查询，是半成品，低优先，本轮不落地。

### 7. 书签扫描卫星（9→10 工具，本轮 +1）
- 功能面：书签 CRUD、波段/链路扫描、卫星过境预报、任务模板。
- 工具面（审计前）：scan_band、predict_passes、start/stop/get_scan_link_status、
  书签 4、list_templates/run_template、get_task_history。
- **真空白（本轮落地）**：predict_passes 要求传 satellite_name，但**没有工具列出 TLE 缓存里到底有哪些卫星**。
  模型要预测过境，得先知道缓存里有哪些卫星名；否则只能瞎猜「ISS」「NOAA 15」并依赖报错。
  这是 predict_passes 的前置只读目录，高价值、纯只读、复用现有 cachedTle()/catalogNumber()/
  parseTleEpoch()/daysSinceEpoch()/downlinkHzFor()。
- 落地：**list_satellites**（只读，无参数），返回名/NORAD 编号/历元/龄期/新鲜/下行频率。
- 卫星（TLE/多普勒/ISS）其余覆盖：predict_passes 已含 AOS/LOS/方位/最高仰角；
  set_doppler_compensation 已是过境实时多普勒自动补偿开关（写）；ISS 作为卫星名被 predict_passes 子串匹配覆盖。

### 8. 系统设备（7 工具）
- get_status、calibrate_frequency、apply_frequency_correction、network audio、
  connect_network_source、get_capabilities。覆盖：有。

### 9. 记忆编排（7 工具）
- memory_add/search/update/delete/flush/stats、checkpoint_status。覆盖：有。

### 10. 工作台编排（5 工具）
- arrange/get_workspace_layout/list_ui_components/reset/update_component_data。覆盖：有。

## 真空白清单与决策

| 真空白 | 引擎侧 | 价值判定 | 决策 |
|---|---|---|---|
| TLE 缓存卫星目录（list_satellites） | cachedTle() 已有，无工具面 | **高**（predict_passes 前置） | **本轮落地**（89→90） |
| CW 解码文本快照 | cw_decoder_ 只发信号无缓冲 | 中（需加环形缓冲，侵入式） | 不落地，待引擎加快照 |
| RDS 节目名/电台快照 | rdsDecoder_ 只发信号 | 中（同上） | 不落地 |
| APT 云图查询 | aptDecoder_ 只发信号 | 中（同上，且返回 QImage 不适合 JSON 工具） | 不落地 |
| VNA 群时延 groupDelayS21 | vna_rf.h 有 | 低（进阶滤波器指标） | 不落地 |
| VNA 材料磁导率 permeabilityNRW | vna_rf.h 有 | 低（材料测量，小众） | 不落地 |
| 离线回放进度 offlinePosition | 引擎有访问器 | 低（无 paused 查询，半成品） | 不落地 |

## 落地工具：list_satellites

- 三通道：tool_schema.cpp 末尾追加 spec（write=false）+ agent_tools.cpp execListSatellites +
  dispatchTable 末尾 + control_hub.cpp cmdListSatellites（命令表 read 行）+ mcp_server.cpp
  「书签扫描卫星」域组加入。
- 纯 seam：sat_task_planner 新增 listSatellitesFromEntries(entries, now) 注入缝 +
  listCachedSatellites(now) 读盘实现；复用 entriesAreFresh/kMaxTleAgeDays=30 天口径。
- 诚实空态：无缓存 -> ok=false、source=none，绝不列出 2006 内置演示 TLE。
- 单测：test_ai_real_link 新增 listSatellites_honestEmptyAndInjected（空态 + 注入缝字段映射）。

## 金集同步（89→90，49 写 / 41 读）

- test_agent：tools.size 89→90；写读 49/40→49/41；read 排除名单加 list_satellites（空态 ok=false）。
- test_tool_registry：schemas.size 89→90；defs.size 89→90；defs.last().name → list_satellites；
  kAllCxxTools / kFlutterUngatedReadTools 各加一项。
- test_tool_schema：specs.size 89→90；headerCount 89→90；expected 描述对加 list_satellites 条目。
- test_ai_real_link：supported 集合 + newTools dispatch 列表各加 list_satellites。
- mobile catalog+test：mobile 仅发 10 个共享工具，list_satellites 是 desktop-only 只读，不动 mobile（10 不变）。
- phase31 agent-tool-documentation.md：头部 89→90，末尾追加 list_satellites 手书段。
- README.md：工具数 89→90（3 处），卫星行加 list_satellites。
- MCP 域：「书签扫描卫星」组 9→10。

## 测试结果

- D135（Debug，ninja）：全量 ctest 152/152 通过，e2e_smoke Skipped（诚实），0 失败。
- R135（Release，make）：全量 ctest 152/152 通过，e2e_smoke Skipped（诚实），0 失败。
- 受影响套件（agent/tool_schema/ai_real_link/mcp_server/tool_registry）双构型全绿。

## 诚实未完成项

- CW/RDS/APT/SSTV/SSDV 解码器未上工具面：引擎只发信号无查询缓冲，需先加快照访问器，
  非本轮「纯只读薄封装」范围，留待后续。
- VNA 群时延/磁导率、离线回放进度：低价值或半成品，本轮不强行造。
- 真机待验（云测无硬件）：list_satellites 在真机 celestrak 拉取后返回真实卫星列表、
  predict_passes/list_satellites 与真源/真站坐标的一致性，需真机回归。
