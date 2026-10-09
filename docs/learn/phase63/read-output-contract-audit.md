# get_* 工具输出侧契约一致性审计（Agent executor × CH handler）

- HEAD：`57ee5f7b02a5f1a2a6c3070576d8e8e8c6b20f38`
- 上轮（入参侧，`ch-agent-param-contract.md`）已修 D1–D5 双侧兼容别名；本轮转到**输出侧**：同一语义字段在 Agent（LLM）通道与 CH/HTTP/Flutter 通道的返回类型/单位/键名/格式是否一致。
- 审计面：`cpp/src/ai/agent_tools.cpp`（executor 输出 JSON）vs `cpp/src/control/control_hub.cpp`（handler 输出 JSON），并下探消费侧 `mobile/lib/models/control_hub.dart`、`control_http_server.cpp` 以判定漂移方向。
- 方法：逐工具枚举输出字段 → 逐字段比对 类型/单位/键名/格式 → 差异判定（可修即修：一侧转换 + 测试同步；架构性不修则记录理由）→ 金集 offscreen 实跑。
- 全程 CLI/offscreen，未跑 GUI，未 mock，未 `git add/commit/push`。

---

## 0. 输出信封约定（两侧共有差异，先备案，不逐行重复）

| 维度 | Agent executor | CH handler | 判定 |
|---|---|---|---|
| 成功信封 | `{ok:true, ...业务字段, connected, test_signal, source}`（`addSourceFields` 注入 `agent_tools.cpp:59`） | `okBase()`=`{ok:true}`，再逐命令加 `command` 字段 | ⚠️ 信封附加键不同，属分层，非漂移 |
| 错误信封 | `{ok:false, error}` | `errResult`=`{ok:false, error}` | ✅ 一致 |
| 单位基准 | 频率一律 `*_hz`(double)、增益/静噪 `*_db`(double) | 同左 | ✅ 全表无 Hz↔MHz、dB↔dBFS 漂移 |

> 关键前提：两侧都把频率写 `*_hz`、电平写 `*_db`，**没有发现 Hz/MHz 或 dB/dBFS 的单位漂移**。本轮真正的漂移集中在「类型/格式」与「键名」两类。

---

## 1. get_* 输出字段逐工具对照表

图例：✅ 一致；➕ 单侧附加字段（无害）；⚠️ 轻微/分层差异（记录不修）；🔴 契约漂移（本轮修）。

### 1.1 get_status
| 字段 | Agent (agent_tools.cpp:246-271) | CH (control_hub.cpp:850-899) | 判定 |
|---|---|---|---|
| frequency_hz / mode / bandwidth_hz | double Hz / str / double Hz | 同 | ✅ |
| carrier_locked / symbol_locked / evm_percent | bool/bool/double | 同 | ✅ |
| doppler_available / doppler_enabled | bool/bool | 同 | ✅ |
| connected | bool（`src.connected`，源信息） | bool（`snap.available && snap.connected`，遥测推导） | ⚠️ 派生口径不同但同型 bool |
| source | str | str | ✅ |
| test_signal | bool（➕） | — | ➕ Agent 专有 |
| summary | str（人类可读「频率=..MHz 模式=..」） | — | ➕ Agent 专有 |
| command | — | str `"get_status"` | ➕ CH 专有 |
| status / error_message / telemetry_available | — | str 枚举 / str / bool | ➕ CH 遥测层专有 |
| readback_center_hz / readback_sample_rate_hz / readback_gain_db | — | double（仅 telemetry 到达时） | ➕ CH 专有 |

### 1.2 get_capabilities
| 字段 | Agent (1291-1318) | CH (938-964) | 判定 |
|---|---|---|---|
| connected / device_name / tunable_min_hz / tunable_max_hz / sample_rate_min_hz / sample_rate_max_hz / gains_db / provenance | bool/str/double×4/arr(double)/str | 同 | ✅（键名、类型、单位全一致；CH 多 `command`，Agent 多 source 三键） |

### 1.3 list_vfos / get_vfos
| 字段 | Agent list_vfos (905-926) | CH get_vfos (967-986) | 判定 |
|---|---|---|---|
| vfos[].id | int | int | ✅ |
| vfos[].freq_hz / bandwidth_hz | double Hz / double Hz | 同 | ✅ |
| vfos[].mode / name | str / str | 同 | ✅ |
| vfos[].selected / armed | bool / bool | 同 | ✅ |
| vfos[].center_offset_hz | — | double Hz（➕） | ➕ CH 专有 |
| selected_vfo_id | int | int | ✅ |
| count（顶层） | int（➕） | — | ➕ Agent 专有（CH 走 vfos 数组长度自推） |

### 1.4 get_recording_state
| 字段 | Agent (1324-1335) | CH (989-1000) | 判定 |
|---|---|---|---|
| recording / recording_path / watch_enabled / recording_dir | bool/str/bool/str | 同 | ✅ |

### 1.5 get_squelch_status
| 字段 | Agent (759-769) | CH (1306-1313) | 判定 |
|---|---|---|---|
| enabled / threshold_db / auto / open | bool / double dB / bool / bool | 同 | ✅ |

### 1.6 get_noise_blanker_status
| 字段 | Agent (792-799) | CH (1326-1330) | 判定 |
|---|---|---|---|
| enabled | bool | bool | ✅ |

### 1.7 get_pocsag_messages
| 字段 | Agent (480-507) | CH (1003-1030) | 判定 |
|---|---|---|---|
| address | **int (qint64)**，RIC 0..2097151 | **double** | ⚠️ 整数 vs double；线两端均为 JSON number，Dart `is num` 通吃，无害（见 §3-NA1） |
| function | int | int | ✅ |
| type | str 枚举 numeric/alpha/unknown | 同 | ✅ |
| text | str | str | ✅ |
| channel（回显已解析信道） | 键 `channel_id`（int） | 键 `channel`（int） | ⚠️ 键名差异，值正确（见 §3-NA2） |
| count / messages[] | int / arr | 同 | ✅ |

### 1.8 get_m17_calls
| 字段 | Agent (510-536) | CH (1033-1061) | 判定 |
|---|---|---|---|
| src / dst | str / str | 同 | ✅ |
| **type（LSF TYPE 字）** | **int**（`static_cast<int>(c.type)`） | **🔴 原为 hex 串 `"0x%04X"`** | 🔴 **OF1，本轮修为 int** |
| is_stream / payload_class / frame_kind / crc_ok / voice_undecoded | bool/int/int/bool/bool | 同（➕ `viterbi_cost` int） | ✅ |
| 元数据载荷 | `meta_size` / `payload_size`（int 计数） | `meta[]` / `payload[]`（byte 数组）➕ viterbi_cost | ⚠️ 字段形状不同（分层，见 §3-NA3） |
| channel 回显 | `channel_id`（int） | `channel`（int） | ⚠️ 同 1.7 |
| count / calls[] | int / arr | 同 | ✅ |

### 1.9 get_vor_radial
| 字段 | Agent (597-618) | CH (1064-1077) | 判定 |
|---|---|---|---|
| locked | bool | bool | ✅ |
| radial_deg / quality | locked 时 double；未锁时 **null + note 说明** | 始终 double（未锁也发数值） | ⚠️ 诚实空态策略不同（见 §3-NA4） |
| morse_id | str | str | ✅ |
| channel 回显 | `channel_id` | `channel` | ⚠️ 同上 |

### 1.10 get_acars_packets
| 字段 | Agent (539-568) | CH (1080-1108) | 判定 |
|---|---|---|---|
| direction / mode / label / block_id / ack / text / crc_ok | str枚举/str/str/str/str/str/bool | 同 | ✅（channel 回显键差异同上） |

### 1.11 get_navtex_messages
| 字段 | Agent (571-594) | CH (1111-1133) | 判定 |
|---|---|---|---|
| station / type / number / text / diversity_ok / phasing_ok / diversity_errors | str/str/str/str/bool/bool/int | 同 | ✅ |

### 1.12 predict_passes
| 字段 | Agent (274-312) | CH (1140-1176) | 判定 |
|---|---|---|---|
| passes[].name / catalog_number / rise_time / rise_az / set_time / set_az / max_el | str/int/ISODate str/double/ISODate str/double/double | 同 | ✅（两侧读同一 `SatPassEntry`，类型构造上必然一致；CH 多 `command`） |
| source（TLE 来源 provenance） | str | str | ✅ |

### 1.13 list_bookmarks
| 字段 | Agent (803-812) | CH (1333-1348) | 判定 |
|---|---|---|---|
| bookmarks[] | 硬编码空数组 + note（Agent 拿不到 BookmarkManager 实例，经 routed honest-empty） | 真实 `[{name,freq_hz,mode,bandwidth_hz,group}]` | ⚠️ routed-stub 分层（见 §3-A1，沿用上轮 A8） |

### 1.14 list_recordings
| 字段 | Agent (1081-1104) | CH (1446-1463) | 判定 |
|---|---|---|---|
| recordings[].name / bytes | str / int(qint64) | 同 | ✅ |
| recordings[].modified | ISODate str（➕） | — | ➕ Agent 专有附加时间戳 |
| dir / count / recordings[] | str/int/arr | 同 | ✅ |

### 1.15 get_spectrum_status
| 字段 | Agent (1224-1233) | CH (1555-1561) | 判定 |
|---|---|---|---|
| fft_size | int | int | ✅ |
| window | int，**键原为 `window_type`** | int，键 `window` | 🔴 **OF2：键名漂移（类型同为 int），本轮修为 `window`** |
| average | int，**键原为 `average_mode`** | int，键 `average` | 🔴 **OF2：键名漂移，本轮修为 `average`** |

### 1.16 get_network_audio_status
| 字段 | Agent (686-694) | CH (1225-1241) | 判定 |
|---|---|---|---|
| enabled | bool（硬编码 false）+ note | bool（真实 `netTapRaw_!=nullptr`）；激活时加 port/protocol/client_connected/bytes_sent/frames_dropped/last_error | ⚠️ routed-stub 分层（§3-A1） |

### 1.17 get_scan_link_status
| 字段 | Agent (716-726) | CH (1270-1281) | 判定 |
|---|---|---|---|
| 运行态 | `scanning`(bool) / `dwelling`(bool) / `hit`(null) + note | `status`(str 枚举 idle/scanning/dwell) / `dwell_count`(int) / `parked_freq_hz`(double Hz) / `retune_count`(int) | ⚠️ routed-stub 分层（§3-A1）：Agent 无 ScanActivityLink 实例，只发 honest idle |

---

## 2. 差异判定清单

### 2.1 🔴 本轮已修（最小改动：一侧转换 + 测试同步）

| ID | 位置 | 漂移点 | 修复 |
|---|---|---|---|
| **OF1** | `control_hub.cpp:1043`（原）vs `agent_tools.cpp:520` | `get_m17_calls[].type`：Agent 发**整数** LSF TYPE 字，CH 发**十六进制串** `"0x%04X"`。同字段不同类型/格式。下探消费侧 `mobile/lib/models/control_hub.dart:222` `M17Call.fromJson` 只认 `raw['type'] is num`——CH 的 hex 串在移动端被**静默吞成 0**，是潜在 bug。 | CH 一侧改为 `o["type"] = static_cast<int>(c.type);`（`control_hub.cpp:1043-1048`），与 Agent 及 Flutter 实际契约统一为数值 int。无旧测试断言 hex 值（空引擎走空数组路径，per-element 字段本就不出现），故无旧断言需翻转。 |
| **OF2** | `agent_tools.cpp:1230-1231`（原）vs `control_hub.cpp:1559-1560` | `get_spectrum_status`：类型两侧**同为 int**，但 Agent 键名 `window_type`/`average_mode`，CH 键名 `window`/`average`。更要命的是 Agent **自身的 schema 描述**（`tool_schema.cpp:904`）就写「返回 fft_size/window/average」，executor 实际发 `window_type/average_mode`——Agent 内部 schema↔executor 自相矛盾，且与写工具 `set_fft_params` 的回显键 `window/average`（`test_agent.cpp:1130`）也不一致。 | Agent 一侧改发 `o["window"]=...` / `o["average"]=...`（`agent_tools.cpp:1230-1235`），对齐 schema 声明 + CH 读回 + 写回显三处。新增断言块锁键名（见下）。 |

**测试同步**：
- `cpp/tests/test_agent.cpp` `phase63BilateralAliasContract()` 新增 OF2 块：先 `set_fft_params(window=2,average=1)`，再读 `get_spectrum_status`，断言 `window==2`、`average==1`、`fft_size==2048`，且**不再含** `window_type`/`average_mode`。
- OF1 无 per-element 可断言数据（金集空引擎），靠空数组路径不回归 + 代码注释锁定契约；populated 断言需注入解码快照夹具（见 §6 未完成项）。

### 2.2 ⚠️ 架构性 / 分层不修（记录理由，非 bug）

| ID | 位置 | 差异 | 为什么不修 |
|---|---|---|---|
| **A1** | network_audio_status / scan_link_status / list_bookmarks | Agent 发硬编码空态+note，CH 发真实后端状态。 | 沿用上轮 A8：Agent 层拿不到 `NetworkAudioSink`/`ScanActivityLink`/`BookmarkManager` 实例（在 control 层），只能 routed honest-empty。是分层边界，不是线协议漂移。 |
| **NA1** | pocsag `address`：Agent qint64 vs CH double | 同字段整数 vs 浮点。 | 线两端都是 JSON number；Dart `is num` 通吃（`control_hub.dart:141-143` `addr.toInt()`），RIC 域 0..2097151 精度无损。改它纯属字节洁癖，徒增 diff。 |
| **NA2** | 5 个解码读工具的信道回显键：Agent `channel_id` vs CH `channel` | 同值不同键名。 | 入参侧 D2 已双侧兼容（两侧 `resolveChannel*` 都接受两个键）。输出回显只是信息性镜像，**没有任何消费方回读这个回显键驱动逻辑**（Flutter `Pocsag/M17.fromJson` 只解析数组元素字段，不读顶层 channel）。跨传输（LLM↔HTTP）的客户端不会同一进程读两份。值正确，故不为此给 10 处输出加双键冗余。 |
| **NA3** | m17 元数据载荷：Agent `meta_size/payload_size`(int) vs CH `meta[]/payload[]`(byte 数组) | 字段形状不同。 | 面向不同消费方做了合理裁剪：LLM 通道给便宜的字节计数（不给大段原始字节），Flutter 通道给可渲染的字节数组（`control_hub.dart:198-211` `_bytesToDisplay` 同时兼容 string 与 number[]）。CH 另加的 `viterbi_cost` 是加字段，无害。 |
| **NA4** | vor_radial：Agent 未锁时 radial_deg/quality=null+note，CH 始终发数值。 | 诚实空态策略不同。 | Agent 哲学是「未锁绝不发可信方位」（null+明示 note），CH 哲学是「locked=false 时客户端自行忽略 radialDeg」。两者都不编造方位，只是表达方式；CH 注释（1072）已说明 unlocked 时应忽略读数。 |
| **NA5** | list_recordings[].modified（Agent 专有）、list_vfos.count（Agent 专有）、vfos[].center_offset_hz（CH 专有）、get_status 的 test_signal/summary vs status/error_message/readback_* | 单侧附加字段。 | 加字段向后兼容，不破坏对侧消费方；属各通道的信息增量。 |

---

## 3. 金集实跑计数

环境：`env -u MBDSDR_TEST_SOURCE LD_LIBRARY_PATH=/home/user/Qt/6.8.2/gcc_64/lib QT_QPA_PLATFORM=offscreen ./<bin>`（clean env，未设 `MBDSDR_TEST_SOURCE`；增量 `make test_agent test_control_hub test_control_http test_tool_registry` 于 `cpp/build`）。

| 二进制 | PASS | FAIL | SKIP | 备注 |
|---|---|---|---|---|
| `test_tool_registry` | 8 | 0 | 0 | spec↔dispatch 47 对齐强制断言全绿 |
| `test_agent` | 34 | 0 | 0 | 含新增 OF2 读回键断言块（上轮 33 → 34） |
| `test_control_hub` | 29 | 0 | 0 | 含数字快照诚实空态、D2/D3 回退 |
| `test_control_http` | 14 | 0 | 0 | loopback 绑定、gate、decoder snapshot、channel 透传 |
| **合计** | **85** | **0** | **0** | 全绿 |

---

## 4. 红线扫描

- ✅ 仅改 3 个文件：`agent_tools.cpp`（OF2）、`control_hub.cpp`（OF1）、`test_agent.cpp`（OF2 断言）。
- ✅ 工作树中并行会话在途改动（`tokens.h`、`spectrum_display.*`、`spectrum_widget.cpp`、`test_spectrum_display.cpp`、`ui_screenshot_narrow.cpp`）**未触碰、未回滚**；未跟踪 scratch 文件（`cpp/scratch/*`、`cpp/tests/ui_diag_freeze.cpp`）未动。
- ✅ 未 `git add/commit/push`（`git status` 仅工作区 modified，未 staged）。
- ✅ 未跑 GUI（`QT_QPA_PLATFORM=offscreen`）。
- ✅ 未 mock（金集跑既有二进制，真实引擎/真实 QDir/真实 QJson）。
- ✅ 未用 `/tmp` 作为本审计产物目录（测试二进制自写 `/tmp/mbdsdr_*` 是其既有行为）。
- ✅ 无「比赛/competition」字样。

---

## 5. 诚实未完成项

1. **OF1 的 populated 断言未建**：金集空引擎下 `calls` 为空数组，无法断言「一条真实 M17Call 的 type 是 int」。要锁这个需要经 `VfoManager::m17CallsChanged` / `mgr.m17Calls` 注入一条夹具（`test_digital_link_integration.cpp` 已有 VfoManager 夹具入口），本轮为控改动面未建。当前靠空数组不回归 + 注释约定兜底。
2. **HTTP 层只看了 `queryToArgs` 转发**（`control_http_server.cpp:41-57`，只转发 `channel`），未逐行读完整个 HTTP server 的响应序列化路径；但 HTTP 是把 CH handler 的 QJsonObject 原样 compact 序列化下发，故 CH 改 int 后 HTTP 自然跟随，无需额外改 HTTP。
3. **Dart 消费侧只核了 `control_hub.dart` 的 m17/pocsag 解析**；`remote_decoder_panel.dart` 等展示 widget 如何渲染 `type` 未逐行看（模型层已收敛为 int，展示层只吃模型）。
4. **gated/write 类工具的输出信封**（如 scan_band 的 camelCase vs snake_case `lowHz/frequencyHz`，上轮 A3 已备案）不在本轮 get_* 只读范围，维持备案。

---

## 6. 结论

get_* 只读工具的**单位面干净**（全表无 Hz/MHz、dB/dBFS 漂移），**核心读回字段（status/capabilities/vfos/recording_state/squelch/noise_blanker/acars/navtex/predict_passes）类型与键名双侧一致**。真漂移只有两处，且本轮都已最小修复：

- **OF1 `get_m17_calls[].type`**：CH 十六进制串 → int，顺带修掉 Flutter 把该字段静默吞成 0 的潜在 bug。
- **OF2 `get_spectrum_status` 键名**：Agent `window_type/average_mode` → `window/average`，对齐自身 schema 声明与 CH/写回显。

其余差异均为 routed-stub 分层（network_audio/scan_link/list_bookmarks）、无害的整数↔double（pocsag address）、信息性回显键名（channel_id/channel，入参已双侧兼容）、或单侧附加字段——记录为架构性不修。金集 85 全绿。
