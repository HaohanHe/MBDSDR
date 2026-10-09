# ControlHub 命令表 × Agent 工具表 — 入参契约一致性审计（只读）

- HEAD: `0acb59da8f4b50f28231efe9888ac2aaf5d4ac67`
- 审计面：`cpp/src/control/control_hub.cpp`（`ControlHub::table()` + `execute()` 分发）vs
  `cpp/src/ai/agent_tools.cpp`（`dispatchTable()` 执行体）+ `cpp/src/ai/tool_schema.cpp`（`registeredToolSpecs()` 声明式 JSON Schema）。
- 方法：逐命令/工具枚举 → 语义重叠对逐项比对入参名/类型/单位/默认值/可选性 → 差异判定 → 金集实跑。
- 全程只读，未改源码；未跑 GUI；未 mock。

---

## 1. 两侧清单枚举

### 1.1 ControlHub 命令表（`control_hub.cpp:82-156`，`table()`）

共 **65** 条（prompt 估 ~66，实际 65）。

写（gated，29）：
`tune, set_sample_rate, set_gain, set_mode, set_bandwidth, set_squelch_enabled, set_squelch_threshold, set_muted, start_recording, stop_recording, export_iq_segment, set_anr, set_gated_recording, set_watch, set_tuner_agc, set_rtl_agc, scan_band, vfo_add, vfo_remove, vfo_select, vfo_set_freq, vfo_set_offset, vfo_set_bandwidth, vfo_set_mode, set_vfo_armed, clear_digital_outputs, set_network_audio_sink, start_scan_link, stop_scan_link, set_squelch, set_noise_blanker, set_doppler_compensation, connect_network_source, add_bookmark, tune_to_bookmark, delete_bookmark, add_vfo, switch_vfo, rename_vfo, delete_recording, export_recording, set_fft_params, set_color_map`

读（36）：
`get_frequency, get_mode, get_bandwidth, get_status, get_telemetry, list_gains, get_capabilities, get_vfos, get_recording_state, get_pocsag_messages, get_m17_calls, get_vor_radial, get_acars_packets, get_navtex_messages, predict_passes, get_network_audio_status, get_scan_link_status, get_squelch_status, get_noise_blanker_status, list_bookmarks, list_vfos, list_recordings, get_spectrum_status`

### 1.2 Agent toolDefs（`tool_schema.cpp:63-956` `registeredToolSpecs()` + `agent_tools.cpp:1309-1360` `dispatchTable()`）

共 **47** 个工具，spec 与 dispatch 一一对应（`test_tool_registry::completeness_*` 金集 8 PASS 强制）。

### 1.3 语义重叠对总数

- 命令名完全相同：**40** 对（`comm -12` 精确集合）。
- 语义重叠但命名不同：
  - `tune_frequency` (Agent) ↔ `tune` (CH)
  - `set_vfo_frequency` (Agent) ↔ `vfo_set_freq` (CH)
  - `set_vfo_mode` (Agent) ↔ `vfo_set_mode` (CH)
  - `set_vfo_bandwidth` (Agent) ↔ `vfo_set_bandwidth` (CH)
- 合计 **44** 对语义重叠。

---

## 2. 语义重叠对入参对照表

图例：✅ 一致；⚠️ 行为/弹性差异（架构性，见 §3）；🔴 契约漂移（该修，见 §3）。

### 2.1 写类重叠对

| # | Agent 工具 (spec/exec) | CH 命令 (handler) | 入参名 | 类型 | 单位 | 默认/可选 | 判定 |
|---|---|---|---|---|---|---|---|
| 1 | `tune_frequency` | `tune` | `freq_hz` / `freq_hz` | number / double (needDbl) | Hz | Agent 必填；CH 必填 | ✅名/类型/单位；⚠️行为：Agent `f<=0` 拒（`agent_tools.cpp:89`），CH clamp 到 `[kFreqMinHz,kFreqMaxHz]`（`control_hub.cpp:412`）|
| 2 | `set_mode` | `set_mode` | `mode` / `mode` | string enum / needMode | — | 双方必填，统一上白名单 `tokens::kControlHubModes`（`tokens.h:1115`，11 模式）| ✅；⚠️Agent schema 仅列 6 枚举 `AM/NFM/WFM/USB/LSB/CW`（`tool_schema.cpp:92`），运行时接受全 11 |
| 3 | `start_recording` | `start_recording` | — | — | — | 无参 | ✅ |
| 4 | `stop_recording` | `stop_recording` | — | — | — | 无参 | ✅ |
| 5 | `export_iq_segment` | `export_iq_segment` | `sample_count`, `tune_hz` | number/double / double | 样本数 / Hz | `sample_count` 默认 65536；`tune_hz` 可选缺省保持当前中心 | ✅（默认值/可选性/错误文案一致，`agent_tools.cpp:158-192` vs `control_hub.cpp:554-586`）|
| 6 | `scan_band` | `scan_band` | `low_hz`, `high_hz`, `step_hz` | number / double | Hz | `step_hz` 默认 200000 | ✅名/类型/单位/默认；⚠️CH 强制 `step>=kControlHubScanStepMinHz=1.0`（`control_hub.cpp:643`），Agent executor 不强制（仅 schema min=1.0，`tool_schema.cpp:18`）；⚠️输出 key 风格不同：Agent camelCase `lowHz/highHz/stepHz/peakDbfs/hits[].frequencyHz`（`agent_tools.cpp:209-213`）vs CH snake_case `low_hz/.../hits[].frequency_hz`（`control_hub.cpp:649-656`）|
| 7 | `set_bandwidth` | `set_bandwidth` | `bandwidth_hz` / `bandwidth_hz` | number / double | Hz | 必填 | ✅；⚠️Agent schema 列 7 个 preset enum（`tool_schema.cpp:158-161`），运行时接受任意数 |
| 8 | `set_network_audio_sink` | `set_network_audio_sink` | `enable`, `port`, `format` | bool/number/string / needBool+needInt+string | — | enable 必填；port 必填；format 可选 | 🔴 **CH 额外接受 `stereo`(bool, 默认 false) 与 `host`(string, 默认 127.0.0.1)**（`control_hub.cpp:1159-1161`），Agent schema 未声明、routed stub 静默丢弃（`agent_tools.cpp:625-639`）；⚠️CH 校验 port∈[1,65535]，Agent 仅 needNum |
| 9 | `start_scan_link` | `start_scan_link` | `target_freq_hz` / `target_freq_hz` | number / double | Hz | 必填 | ✅ |
| 10 | `stop_scan_link` | `stop_scan_link` | — | — | — | 无参 | ✅ |
| 11 | `set_squelch` | `set_squelch` | `enabled`(opt), `threshold_db`(opt), `auto`(opt) | bool/number/bool / bool/double/bool | dB | 三者均可选，至少一个（CH 强制 has=true，`control_hub.cpp:1257`）| ✅名/类型/单位/可选性；⚠️CH 把 `threshold_db` clamp 到 `[kSquelchMinDb,kSquelchMaxDb]`（`control_hub.cpp:1248-1251`），Agent 原样下发不 clamp（`agent_tools.cpp:697-700`）|
| 12 | `set_noise_blanker` | `set_noise_blanker` | `on` / `on` | bool / needBool | — | 必填 | ✅ |
| 13 | `add_bookmark` | `add_bookmark` | `freq_hz`(req), `name`(opt), `mode`(opt), `bandwidth_hz`(opt exec), `group`(opt exec) | number/string/string/number/string | Hz | freq_hz 必填 | 🔴 **Agent executor 实际接受 `bandwidth_hz` 与 `group`**（`agent_tools.cpp:786-789`），但 Agent **schema 未声明这两个键**（`tool_schema.cpp:575-593` 只列 freq_hz/name/mode）；CH 接受 `bandwidth_hz`（默认 0.0，`control_hub.cpp:1315`）但 **不接受 `group`**（静默忽略）；CH 缺省 `name=f/1e6 MHz 串`（`control_hub.cpp:1312-1313`），Agent 缺省空串 |
| 14 | `tune_to_bookmark` | `tune_to_bookmark` | `index` / `index` | number / int (needInt) | — | 必填，双方越界拒 | ✅名/类型；⚠️**retune 路径不同**：Agent 走 `engine->vfoSetOffset(selectedVfoId, freq)`（IF 偏移，`agent_tools.cpp:827`）；CH 走 `engine->onSetCenterFreq(freq)` + `setDemodMode(mode)`（中心重调，`control_hub.cpp:1332-1333`）|
| 15 | `delete_bookmark` | `delete_bookmark` | `index` / `index` | number / int | — | 必填 | ✅ |
| 16 | `add_vfo` | `vfo_add` + `add_vfo` | — | — | — | 无参 | ✅（CH 同一名命令同时存在 `vfo_add` 与 `add_vfo` 两行，`control_hub.cpp:101,121`，二者 handler 相同）|
| 17 | `switch_vfo` | `switch_vfo` | `index` / `index`(可回退 `id`) | number / double | — | 必填 | ✅；⚠️CH 接受 `index` 缺省时回退读 `id`（`control_hub.cpp:1369-1372`），Agent 只读 `index` |
| 18 | `rename_vfo` | `rename_vfo` | `index`, `name` | number/string / double/string | — | 双方必填 | ✅；⚠️CH 同样 `id` 回退（`control_hub.cpp:1381-1382`）；Agent 为 routed stub，CH 真执行 `vfoRename` |
| 19 | `set_vfo_armed` | `set_vfo_armed` | `index`, `enabled` | number/bool-or-number / int/bool 严格 | — | 必填 | ✅名/类型；⚠️Agent 接受 `enabled` 为 bool **或** double≠0（`agent_tools.cpp:943-944`），CH 严格 `isBool()`（`control_hub.cpp:757`）|
| 20 | `set_vfo_frequency` | `vfo_set_freq` | 🔴 **`index`** (Agent, marker 序号) ↔ **`id`** (CH, VFO id) | number / int | Hz | 双方必填 | 🔴 **键名漂移 + 编号空间漂移**：Agent 读 `index` 再 `vfoMarkers()[i].id` 解析（`agent_tools.cpp:971-983`）；CH 直接读 `id` 透传 `vfoSetFreq(id,hz)`（`control_hub.cpp:694-703`）。命令名本身也不同（`set_vfo_frequency` vs `vfo_set_freq`）。同一语义，两个入参契约 |
| 21 | `set_vfo_mode` | `vfo_set_mode` | 🔴 **`index`** ↔ **`id`** | number / int | — | 必填 | 🔴 同 #20（`agent_tools.cpp:1000-1018` vs `control_hub.cpp:738-747`）|
| 22 | `set_vfo_bandwidth` | `vfo_set_bandwidth` | 🔴 **`index`** ↔ **`id`**；`bandwidth_hz` 同名 | number / int | Hz | 必填，>0 双方拒 | 🔴 键名漂移同 #20；`bandwidth_hz` 名/类型/单位/正数校验一致（`agent_tools.cpp:1040-1041` vs `control_hub.cpp:728-729`）|
| 23 | `delete_recording` | `delete_recording` | `name` / `name` | string / string | — | 必填，纯文件名（双方拒 `/`、`..`）| ✅ |
| 24 | `export_recording` | `export_recording` | `name`, `out_path` | string / string | — | 双方必填 | ✅ |
| 25 | `set_fft_params` | `set_fft_params` | `fft_size`(req Agent / opt CH), `window`, `average` | number / double；**🔴 string enum "Hann"/"Flattop"/"Blackman"** (Agent, `tool_schema.cpp:838`) ↔ **raw int 0/1/2** (CH, `control_hub.cpp:1464`)；`average` 同理 string "Off"/"Slow"/"Fast" ↔ int 0/1/2 | — | Agent fft_size 必填；CH 三者皆可选（至少一个）| 🔴 **类型漂移**：同一命令名，Agent 通道收字符串枚举，HTTP/CH 通道收整数。下游 Flutter/HTTP 客户端按 string 发会被 CH `isDouble()` 静默丢弃（`sz.isDouble()` false → has 不置位 → 走 "至少一个" 错误）；按 int 发 Agent 会走默认分支（不命中枚举映射）。这是本轮最实的跨通道契约漂移 |
| 26 | `set_color_map` | `set_color_map` | `file_path` / `file_path` | string / string | — | 必填非空 | ✅ |
| 27 | `set_doppler_compensation` | `set_doppler_compensation` | `enable` / `enable` | bool / bool (默认 false) | — | 必填（schema）/ 可选默认 false | ✅ |
| 28 | `connect_network_source` | `connect_network_source` | `host`(req), `port`(opt 默认 1234) | string/number / string/double | — | host 必填；port 可选 | ✅ |

### 2.2 读类重叠对

| # | Agent 工具 | CH 命令 | 入参名 | 类型 | 判定 |
|---|---|---|---|---|---|
| 29 | `get_status` | `get_status` | — | — | ✅ 无参 |
| 30 | `predict_passes` | `predict_passes` | `satellite_name`(req), `hours_ahead`(opt 默认 24), `station_lat_deg`(opt), `station_lon_deg`(opt) | string/number/number/number | ✅ 完全一致（`agent_tools.cpp:279-285` vs `control_hub.cpp:1097-1105`）|
| 31 | `get_pocsag_messages` | `get_pocsag_messages` | 🔴 **`channel_id`** (Agent, `tool_schema.cpp:300`, `agent_tools.cpp:436`) ↔ **`channel`** (CH, `control_hub.cpp:327` `resolveChannel`) | number | 🔴 **键名漂移**：缺省均为 selectedVfoId，但 Agent 发 `channel_id` 而 CH 读 `channel`。HTTP 客户端用 `channel` 调 Agent 工具会落到缺省；LLM 用 `channel_id` 调 CH 命令会落到缺省——两侧都"沉默成功"但信道选择被吞 |
| 32 | `get_m17_calls` | `get_m17_calls` | 🔴 `channel_id` ↔ `channel` | number | 🔴 同 #31 |
| 33 | `get_vor_radial` | `get_vor_radial` | 🔴 `channel_id` ↔ `channel` | number | 🔴 同 #31 |
| 34 | `get_acars_packets` | `get_acars_packets` | 🔴 `channel_id` ↔ `channel` | number | 🔴 同 #31 |
| 35 | `get_navtex_messages` | `get_navtex_messages` | 🔴 `channel_id` ↔ `channel` | number | 🔴 同 #31 |
| 36 | `get_network_audio_status` | `get_network_audio_status` | — | — | ✅ 无参（Agent 返回硬编码 enabled=false + note，CH 返回真实端口/字节数——架构性差异，见 §3-A8）|
| 37 | `get_scan_link_status` | `get_scan_link_status` | — | — | ✅ 无参 |
| 38 | `get_squelch_status` | `get_squelch_status` | — | — | ✅ 无参，同读回字段 `enabled/threshold_db/auto/open` |
| 39 | `get_noise_blanker_status` | `get_noise_blanker_status` | — | — | ✅ 无参，同 `enabled` |
| 40 | `list_bookmarks` | `list_bookmarks` | — | — | ✅ 无参（Agent 当前返回空数组+note，CH 返回真实列表——架构性 routed-stub 差异）|
| 41 | `list_vfos` | `list_vfos`（== `get_vfos` 别名，`control_hub.cpp:1354`）| — | — | ✅ 无参 |
| 42 | `list_recordings` | `list_recordings` | — | — | ✅ 无参 |
| 43 | `get_spectrum_status` | `get_spectrum_status` | — | — | ✅ 无参；⚠️输出 key 名不同：Agent `window_type`/`average_mode`（`agent_tools.cpp:1187-1188`）vs CH `window`/`average`（`control_hub.cpp:1491-1492`）|
| 44 | `get_capabilities` | `get_capabilities` | — | — | ✅ 无参，同字段集 |
| 45 | `get_recording_state` | `get_recording_state` | — | — | ✅ 无参，同字段集 |

### 2.3 仅单侧存在（不构成重叠对，仅备案）

- **仅 CH**：`set_sample_rate, set_gain, set_squelch_enabled, set_squelch_threshold, set_muted, set_anr, set_gated_recording, set_watch, set_tuner_agc, set_rtl_agc, vfo_remove, vfo_select, vfo_set_offset, clear_digital_outputs, get_frequency, get_mode, get_bandwidth, get_telemetry, list_gains, get_vfos`（`list_vfos` 别名外的 get_vfos）。
- **仅 Agent**：`tune_frequency`（CH 名 `tune`，已在 #1 比对）、`calibrate_frequency`、`apply_frequency_correction`。

---

## 3. 差异判定清单

### 3.1 🔴 契约漂移（该修，最小方案）

| ID | 位置 | 漂移点 | 最小修复方案 |
|---|---|---|---|
| **D1** | `agent_tools.cpp:971-983, 1000-1018, 1034-1048` vs `control_hub.cpp:694-703, 738-747, 719-735` | VFO 写族：Agent 命令名 `set_vfo_frequency/mode/bandwidth` + 键 `index`（marker 序号）；CH 命令名 `vfo_set_freq/mode/bandwidth` + 键 `id`（VFO id）。命令名与键双双不一致，且 `index`（0..N-1 列表序号）与 `id`（引擎分配 id，remove 后不连续）是两个编号空间。HTTP 客户端若按 Agent schema 的 `index` 发到 CH 的 `vfo_set_freq`，会因缺 `id` 报"缺少整数参数: id"；反之按 CH `id` 发到 Agent `set_vfo_frequency` 会因缺 `index` 报错。 | 二选一：(a) 把 Agent 三命令重命名为 `vfo_set_freq/mode/bandwidth` 并改键为 `id`（同步改 `tool_schema.cpp:707-769` + `dispatchTable` + 金集）；或 (b) 把 CH 三命令加 `index` 别名入口（像 `switch_vfo` 那样 `a.value("index").isUndefined()?a.value("id"):...`，`control_hub.cpp:1369-1372` 已有同款弹性）。推荐 (b)，改动小、向后兼容。|
| **D2** | `tool_schema.cpp:300,318,336,354,372` + `agent_tools.cpp:436` vs `control_hub.cpp:326-334` (`resolveChannel` 读 `channel`) | 5 个数字解码读工具入参键：Agent `channel_id` ↔ CH `channel`。同名命令、不同键名。两侧都缺省到 selectedVfoId，所以"不带参"时静默一致；一旦调用方显式指定信道，按 A 侧发的键在 B 侧被吞，落到当前选中信道（非报错，是最危险的那种漂移）。 | 统一为 `channel`（CH 侧已稳定、HTTP 已在用）或 `channel_id`（Agent 侧 LLM schema 已下发）。推荐把 CH `resolveChannel` 改为 `channel_id` 优先、`channel` 回退（与 `switch_vfo` 的 `index`/`id` 回退同款弹性），一行改动。|
| **D3** | `tool_schema.cpp:835-845` + `agent_tools.cpp:1143-1157` vs `control_hub.cpp:1461-1466` | `set_fft_params.window/average` 类型漂移：Agent 收字符串枚举 `"Hann"/"Flattop"/"Blackman"`、`"Off"/"Slow"/"Fast"`，运行时映射到 int；CH 收原始 int 0/1/2。同名命令、不同 wire 类型。跨通道客户端必然踩一个。 | 推荐 CH 侧对齐 Agent：接受字符串枚举再映射（与 Agent `execSetFftParams` 相同的 if-else 映射表），保留 int 向后兼容。或 Agent 改为发 int。前者对 LLM 友好，后者对 HTTP 友好——需产品决策，但必须二选一，不能两边都收不同类型。|
| **D4** | `agent_tools.cpp:786-789`（executor 实际读 `bandwidth_hz`/`group`）vs `tool_schema.cpp:575-593`（schema 只声明 freq_hz/name/mode）vs `control_hub.cpp:1307-1324`（CH 收 `bandwidth_hz` 默认 0，不收 `group`） | 三处不齐：Agent executor 比 schema 多吃两个键；CH 吃 bandwidth_hz 不吃 group。 | (a) Agent schema 补 `bandwidth_hz`(number, opt) 与 `group`(string, opt)；(b) CH `cmdAddBookmark` 加 `group` 透传到 `b.group`（一行）。否则 LLM 按 schema 生成的调用永远写不进 group，而 LLM 看不到 bandwidth_hz。|
| **D5** | `agent_tools.cpp:625-639` vs `control_hub.cpp:1138-1179` | `set_network_audio_sink`：CH 额外吃 `host`(默认 127.0.0.1) 与 `stereo`(默认 false)，Agent routed stub schema 未声明、静默丢弃。 | Agent schema 补 `host`/`stereo` 两个可选键（executor 已是 routed echo，直接透传到 echo 即可）。|

### 3.2 ⚠️ 架构性不修（文档化，非 bug）

| ID | 位置 | 差异 | 为什么不修 |
|---|---|---|---|
| A1 | `agent_tools.cpp:89-90` vs `control_hub.cpp:412` | tune：Agent 严格拒 `f<=0`/非有限；CH 弹性 clamp 到 token 范围。 | LLM 通道要"宁可报错也不假装成功"（护栏），HTTP/UI 通道要"用户拖个滑块到界外就 clamp"（弹性）。两侧哲学不同，是 feature。|
| A2 | `agent_tools.cpp:697-700` vs `control_hub.cpp:1248-1251` | set_squelch.threshold_db：CH clamp 到 `[kSquelchMinDb,kSquelchMaxDb]`，Agent 原样下发。 | 同 A1，Agent 护栏 vs CH 弹性。|
| A3 | `agent_tools.cpp:209-213` vs `control_hub.cpp:649-656` | scan_band 输出 key 风格：Agent camelCase (`lowHz/peakDbfs/frequencyHz`) vs CH snake_case (`low_hz/peakDbfs?` —— 实际 CH 用 `dbfs`/`frequency_hz`)。 | 本任务范围是**入参**一致性；输出 casing 是另一轮的事，且 Agent 输出已被 LLM 消费、CH 输出被 Flutter 消费，两拨消费方各自稳定。仅备案。|
| A4 | `agent_tools.cpp:827` vs `control_hub.cpp:1332-1333` | tune_to_bookmark：Agent 走 selected VFO 的 `vfoSetOffset`（IF 偏移），CH 走 `onSetCenterFreq`（中心重调）。 | Agent 上下文是"AI 在多 VFO 并行监听里调当前选中 VFO"，CH 上下文是"headless 单 VFO 中心调谐"。语义由调用场景决定，不是 bug。|
| A5 | `control_hub.cpp:1369-1372, 1381-1382` | switch_vfo/rename_vfo：CH 接受 `index` 缺省回退 `id`，Agent 只读 `index`。 | CH 对 HTTP 弹性向后兼容；Agent schema 是单一真相，不回退。|
| A6 | `agent_tools.cpp:943-944` vs `control_hub.cpp:757` | set_vfo_armed.enabled：Agent 接受 bool 或 double≠0，CH 严格 bool。 | Agent 对 LLM 更宽容（LLM 可能发 0/1），CH 严格。|
| A7 | `tool_schema.cpp:92` vs `tokens.h:1115-1118` | set_mode schema enum 列 6 项，运行时白名单 11 项。 | schema 是给 LLM 的提示，不是校验；运行时白名单才是契约。LLM 看到 6 个常用项足够，数字模式 (POCSAG/m17/VOR/ACARS/NAVTEX) 走专门的 get_* 工具。|
| A8 | `agent_tools.cpp:642-651, 672-683, 759-769` vs CH 对应 handler | routed-stub 类（network_audio_status / scan_link_status / list_bookmarks）：Agent 返回硬编码空态 + note，CH 返回真实后端状态。 | Agent 层拿不到 ScanActivityLink / NetworkAudioSink 实例（它们在 control 层），只能 routed honest-empty。这是分层边界，不是漂移。|
| A9 | `agent_tools.cpp:1187-1188` vs `control_hub.cpp:1491-1492` | get_spectrum_status 输出 key：Agent `window_type`/`average_mode` vs CH `window`/`average`。 | 输出侧，备案。|

---

## 4. 金集实跑计数

环境：`env -u MBDSDR_TEST_SOURCE LD_LIBRARY_PATH=/home/user/Qt/6.8.2/gcc_64/lib QT_QPA_PLATFORM=offscreen ./<bin>`（clean env，未设 `MBDSDR_TEST_SOURCE`）。

| 二进制 | PASS | FAIL | SKIP | 备注 |
|---|---|---|---|---|
| `cpp/build/test_tool_registry` | 8 | 0 | 0 | `completeness_everySchemaHasExecutor` + `noOrphanExecutor` + `writeReadSplit` + `readOnlySet_parityWithFlutter` 全绿，强制 spec↔dispatch 47 对齐 |
| `cpp/build/test_agent` | 33 | 0 | 0 | 含 calibrate/apply/vfoEdit/bookmark/squelch/noise_blanker/readback_loop 全链路 |
| `cpp/build/test_control_hub` | 28 | 0 | 0 | 含 fft_params/squelch/noise_blanker/vfoList/bookmarks/recordings/scanLink/networkAudio/predict_passes/capabilities |
| `cpp/build/test_control_http` | 14 | 0 | 0 | 含 loopback 绑定、gate、decoder snapshots、channel passthrough、set_vfo_armed 路由 |
| **合计** | **83** | **0** | **0** | 全绿 |

计数验证：
- CH `table()` 实际 **65** 条（prompt 估 ~66，差 1 是因为 `vfo_add` 与 `add_vfo` 是两行同 handler）。
- Agent `dispatchTable()` 实际 **47** 条（与 spec `s.name=` 47 个一一对应，金集强制）。
- 命令名精确交集 **40** 对；加 4 对异名语义重叠（tune_frequency↔tune、set_vfo_frequency↔vfo_set_freq、set_vfo_mode↔vfo_set_mode、set_vfo_bandwidth↔vfo_set_bandwidth）= **44** 对入参比对。

---

## 5. 红线扫描

- ✅ 未改任何源码（`git status` 仅见会话开始前已有的未跟踪 scratch 文件：`cpp/scratch/gated_render_snapshot.cpp`、`cpp/scratch/regen_tool_doc*`、`cpp/tests/ui_diag_freeze.cpp`，未触碰）。
- ✅ 未 `git add/commit/push`。
- ✅ 未跑 GUI（`QT_QPA_PLATFORM=offscreen`）。
- ✅ 未 mock（金集跑既有二进制，真实引擎/真实 Bookstore/真实 QDir）。
- ✅ 未用 `/tmp` 作为产物目录（测试二进制自身写 `/tmp/mbdsdr_*` 是它们的既有行为，不是本审计新建）。
- ✅ 无"比赛/competition"字样。
- ✅ 未跟踪隔离文件未动。

---

## 6. 诚实未完成项

1. **HTTP 层 (`control_http`) 的入参转发是否做了 channel↔channel_id 映射？** 本轮只读了 `control_hub.cpp` + `agent_tools.cpp` + `tool_schema.cpp`，未逐行读 `control_http.cpp` 的转发代码。`test_control_http::channelQueryPassthroughIsHonest` 绿说明 HTTP 层至少有一条 channel 透传路径通，但 D2 的修复方向需要再看一眼 HTTP 层是否已经做了别名映射才能最终定案。
2. **Flutter `ai_tools.dart` 消费侧**：任务背景提到 Flutter 是下游消费方，本轮未读 Dart 侧，只从 C++ 两侧对比。D3（fft window/average 类型）如果 Flutter 已经按某一种类型消费，修复方向就要跟着 Flutter 走，不能只看 C++。
3. **`get_m17_calls` 输出 `type` 字段**：Agent 输出整数 `c.type`（`agent_tools.cpp:482`），CH 输出十六进制串 `"0x%04X"`（`control_hub.cpp:999`）。这是输出差异不是入参差异，不在本轮范围，但顺便记下。
4. **`add_bookmark` 的 `group` 键**：Agent executor 接受但 schema 未声明、CH 不接受。修复时需要确认 UI 书签分组功能是否真的写 group，否则可能该键本来就是死代码。

---

## 7. 结论

两侧 47 vs 65 的能力面本身对齐良好（历轮已审过 gate/错误信封/参数边界/读写分类），**入参契约的真漂移集中在 5 处**：

- **D1 VFO 写族**：命令名 + 键名 + 编号空间三重漂移（最该修）。
- **D2 数字解码读族**：`channel_id` vs `channel` 键名漂移（沉默吞参，最危险）。
- **D3 `set_fft_params`**：window/average 字符串枚举 vs 整数类型漂移（最容易被下游踩）。
- **D4 `add_bookmark`**：三处不齐（schema/executor/CH）。
- **D5 `set_network_audio_sink`**：CH 多吃 host/stereo，Agent schema 缺声明。

其余 ~39 对入参名/类型/单位/默认值完全一致；剩余行为差异（clamp vs reject、弹性回退、routed-stub 空态）是 LLM 护栏 vs 直连弹性的分层设计，文档化即可。
