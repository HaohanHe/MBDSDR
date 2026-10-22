# Python 长尾收敛（P0 #65）

> 产品内真实功能（非 demo）。本轮把 Python 侧独立实现、C++ 8 域尚未覆盖的
> 长尾协议解码器与硬件参数查表，按需挂入 Python MCP server。
> 宁缺毋滥：不追求全量搬迁，A 类只选有明确真机价值、且离线可钉扎者。
> 全仓 MIT；GPL 外部能力一律走独立进程编排（见 §GPL），不抄、不链、不内嵌。

基线 commit `dc01998`（C++ 工具 70、Python pytest 335 passed）。

---

## 1. 盘点统计

| 面 | 数量 | 说明 |
| --- | --- | --- |
| Python MCP server 工具（`core/mcp_server.py::_build_tool_table`） | 53 → **60** | 本轮 +7（A 类） |
| Python `tool_registry` 注册族（`register_*_tools`） | **74** 个注册函数族 | 这是「~280 预期」的真实规模基底；本轮不搬迁此面，仅挂 MCP |
| C++ MCP 冻结域 | 8（与 Python `FROZEN_DOMAIN_NAMES` 同名同序） | 跨语言钉扎，本轮不动 |

**cpp 侧缺口清单核对结论**（`grep -ril <kw> cpp/src`）：

| 关键词 | cpp/src 命中文件数 | 结论 |
| --- | --- | --- |
| baudot | 0 | 缺口（Python 侧亦无实现 → C 类） |
| ardop | 0 | 缺口（Python `ardop_adapter.py` 已有独立实现） |
| dab_plus / DAB+ | 0 | 缺口（Python `dab_plus_lite.py` 已有） |
| nrsc5 | 0 | 缺口（Python `nrsc5_lite.py` 已有） |
| freedv | 0 | 缺口（Python `freedv_modem.py` 已有） |
| dmr | 0 | 缺口（Python `dmr_demod.py` 已有） |
| fst4 | 0 | 缺口（Python `fst4_encode.py` 已有） |
| rtl433 | 0 | 缺口（Python `rtl433_decoder.py` 已有） |
| rtk_solver / RTK | 0 | 缺口（Python `rtklib_adapter.py` SPP 已有，PPP/RTK 留接口） |
| gk2a | 0 | 缺口（Python `gk2a_lrit.py` 已有） |
| bladerf | 0 | 缺口（Python `bladerf_params.py` 已有） |
| gimbal | 0 | 缺口（Python `gimbal.py` 已有） |

**C++ 已有、不计缺口**（grep 命中 `cpp/src/agentapi/mcp_server.cpp` / `ai/agent_tools.cpp`）：
M17（`get_m17_calls`）、LRPT/Meteor（`set_lrpt`/`get_lrpt_status`）、ACARS
（`get_acars_packets`）、FT8（`set_ft8`/`get_ft8_status`/`export_ft8_results`）、
NAVTEX（`get_navtex_messages`）、POCSAG（`get_pocsag_messages`）、VOR
（`get_vor_radial`）、GNSS NMEA（`cpp/src/gnss/nmea_parser.cpp`）。

---

## 2. 三分类对照表

计数：**A = 7（挂 MCP）／B = 6（不挂，列理由）／C = 5（登记待定）**。

| 工具 / 模块 | 域归属 | 分类 | 理由 | 真机验证状态 |
| --- | --- | --- | --- | --- |
| `decoder_dab_fic_decode` ← `dab_plus_lite.dab_fic_decode` | 解码器长尾 | **A** | ETSI FIC/FIB/FIG 独立解析，C++ 无；离线 hex→结构可钉扎 | 合成钉扎可过；真机 PENDING |
| `decoder_nrsc5_frame_parse` ← `nrsc5_lite.hdradio_frame_parse` | 解码器长尾 | **A** | HD Radio 成帧/PSD 提取独立实现，C++ 无 | 合成钉扎可过；真机 PENDING |
| `decoder_rtl433_list` / `decoder_rtl433_decode` ← `rtl433_decoder` | 解码器长尾 | **A** | 433.92MHz ISM 7 类设备协议独立解码，C++ 无 | 合成钉扎可过；真机 PENDING |
| `decoder_gk2a_parse_headers` ← `gk2a_lrit.parse_gk2a_headers` | 解码器长尾 | **A** | GK-2A LRIT 文件头解析（C++ LRPT 是 Meteor，不含 GK-2A） | 合成钉扎可过；真机 PENDING |
| `decoder_fst4_encode_text` ← `fst4_encode.encode_fst4_text` | 解码器长尾 | **A** | FST4 文本→160 音调编码器，C++ FT8 族无 FST4 | 合成钉扎可过（同步块固定）；真机 PENDING |
| `device_bladerf_params_query` ← `bladerf_params.BladeRFParams` | 解码器长尾 | **A** | bladeRF 1.0/2.0 真实量程查表 + 合法性校验，C++ 无；纯查表无设备 | 纯查表（libbladeRF 量程事实）；设备连接由 `BladeRFBackend` 另判 |
| `freedv_modem.freedv_demodulate` | 解码器长尾 | B | 真实 OFDM/DBPSK 实现，但入参是整段音频向量，MCP JSON 边界不自然；留 `tool_registry` 研究面，不挂 | 不挂 |
| `m17_adapter`（`m17_verify_lsf` 等） | 亚音数字解码(C++已有) | B | **与 C++ `get_m17_calls` 重复**，不重复挂 | 不挂 |
| `ardop_adapter`（`register_ardop_tools`） | 研究夹具 | B | 已在 `tool_registry` 注册连接状态机；MCP 再挂=重复入口 | 不挂 |
| `dmr_demod` | 研究夹具 | B | 完整 ETSI TS 102 361 实现，但需整段 4FSK IQ；挂 MCP 边界不自然，留 DSP 研究面 | 不挂 |
| `gimbal.az_el_to_rotator_angles` | 书签扫描卫星 | B | 纯 1:1 角度换算，与 `tune_satellite` 指向重复；电机后端需硬件 | 不挂 |
| `rtklib_adapter` SPP | 研究夹具 | B | RINEX/SPP 已实现，但 NTRIP/PPP/RTK 仅接口；挂 MCP 前需真实基准站数据闭环 | 不挂 |
| baudot / RTTY 电报 | — | C | Python 侧无实现，思路未定 | 未实现 |
| Wi-Fi / LoRaWAN | — | C | 无实现、无设备场景 | 未实现 |
| RTK 固定解 / PPP | — | C | 仅留接口，需 RTKLIB/外部观测 | 未实现 |
| SatDump / wsjtx / fldigi / multimon 编排 | — | C | GPL 外部能力，独立进程协议待定（见 §GPL） | 未实现 |
| FreeDV HDC/AAC、DAB+ AAC 音频解码 | — | C | 物理层成帧已做，音频解码需外部库（独立进程） | 未实现 |

---

## 3. A 类挂载说明

- 实现位置：`mbdsdr_ai/core/mcp_server.py`
  - handler 辅助函数：`_lt_dab_fic` / `_lt_nrsc5_parse` / `_lt_rtl433_list` /
    `_lt_rtl433_decode` / `_lt_gk2a_headers` / `_lt_fst4_encode` /
    `_lt_bladerf_params`（均懒加载对应 numpy 模块，输入非法返回诚实 dict 错误）。
  - 工具表登记：`_build_tool_table()` 末尾「解码器长尾」段。
  - 域映射：`_TOOL_DOMAIN` 新增 7 条 → `"解码器长尾"`。
- **域分组决定**：保留 C++ 冻结 8 域不动（`FROZEN_DOMAIN_NAMES` 仍是跨语言钉扎），
  Python 侧**新增独立扩展域** `"解码器长尾"`（`PYTHON_EXTENSION_DOMAIN_NAMES`），
  在 `tools/list` 视图末尾追加。理由：
  1. 不污染与 C++ 的 8 域冻结一致性测试；
  2. 长尾解码器与既有「亚音数字解码」（CTCSS/DCS 启停式会话）语义不同——前者是
     离线纯函数解析，后者是连接会话句柄；
  3. 客户端合并视图：C++ 8 域与本扩展域并列拼装即可，**两者 schema 各自独立、
     互不复制**；Python 工具表是其自身事实源。
- 域卡片 JSON 形状与 C++ 一致：`domain` / `summary` / `tools`（默认只列名，
  `detailed=true` 给完整 `name/description/inputSchema`）。

---

## 4. GPL 外部能力边界（只读核对，未改 LICENSING）

核对 `LICENSING.md` §2（GPL 组件使用边界）：已完整覆盖「独立进程 + stdin/stdout/
管道/数据文件 + 松耦合通用格式 + 不链接不内嵌」。§4b 是 WTFPL/宽松许可专用表，
**不**覆盖进程边界——进程边界由 §2 承担，无需修改 LICENSING。

本轮对 SatDump / wsjtx / fldigi / multimon / nrsc5 HDC 等 GPL 外部能力的处理：
- **未实现**，登记为 C 类。仅预留「独立进程编排」方向（stdin/stdout/临时文件协议），
  后续若接入须按 §2 走子进程 + 通用数据格式，不在本进程链接 GPL 库。
- 本仓所有长尾解码器均为依据公开标准独立编写的 MIT 实现（independent），
  未抄 GPL 源码。

---

## 5. PENDING 清单（诚实标注）

以下 A 类工具**合成钉扎可过，但真机未验证**，结果体均带
`real_hardware_verified: false`：

- `decoder_dab_fic_decode`（需真实 DAB 信号 + 已分帧 ETI）
- `decoder_nrsc5_frame_parse`（需真实 HD Radio 信号）
- `decoder_rtl433_decode`（需真实 433.92MHz 设备报文）
- `decoder_gk2a_parse_headers`（需真实 GK-2A LRIT 下行）
- `decoder_fst4_encode_text`（编码器，真机收发待 wsjtx 互操作）
- `device_bladerf_params_query`（纯查表；真实 `BladeRFBackend` 连接未在本轮覆盖）

---

## 6. pytest

`python3 -m pytest mbdsdr_ai/tests/ -q`：**335 → 342 passed**（新增 6 个长尾/域
钉扎用例，全绿）。钉扎覆盖：7 个长尾工具在表内且归属「解码器长尾」、扩展域独立
于冻结 8 域、默认/domain/detailed 三分支不丢工具、坏 hex/空输入/未知 device_type
返回诚实错误。
