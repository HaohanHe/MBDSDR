# 原仓库对齐说明（事实源与自动同步）

> 本页由 `build/sync_from_repo.py` 生成，勿手改。产品工程在仓库外，
> 但**能力面、通道、设计令牌、站点口径全部来自原仓库代码**，不是手抄的设计稿。
> 生成时间：2026-11-05T22:01:44+08:00 · 仓库基线：`0592b519`（只读抽取；工作树可能含其它会话在途改动，HEAD 仅标识基线）。

## 为什么要有这一层

早期版本的产品原型把能力位、工具名、桥接通道、站点、时间倍率都写成了自说自话的演示值，
与 MBDSDR 原仓库的真实实现脱节。现在的规则是：**凡仓库里有事实的东西，一律由脚本
只读抽取，产品从 JSON 长出来**；产品侧只保留译名、图标、场景组合这类本地化层。

一键复跑：

```bash
python build/sync_from_repo.py     # 只读原仓库 → repo-facts.json / 05-repo-facts.js / 本页
python build/build.py              # 顺带自动跑同步 + wiki 生成 + 单文件构建
python tests/verify_product.py     # 校验事实源与 UI/文档一致
```

## 1. 工具域（事实源：`mbdsdr_ai/core/mcp_server.py`）

- 冻结域与 Python 扩展域同名同序常量在 C++ `cpp/src/agentapi/mcp_server.cpp` 有跨语言钉扎；
  `FROZEN_DOMAIN_NAMES` / `PYTHON_EXTENSION_DOMAIN_NAMES` 由 summary 字典的键顺序生成。
- 每个域的工具清单来自 `_TOOL_DOMAIN`（工具 → 域），不是文档里的精选子集。
- **空域必须保留**：VNA 测量、记忆编排在 Python 面本就没有工具，仓库注释明确写「诚实空态」；
  产品里照样显示为「该面无工具」，不塞假工具凑数。

| id | 域名（三语） | 组 | 代表工具（代码抽取） |
|---|---|---|---|
| `sdr-vfo` | SDR 调谐/VFO / SDR 同調 / VFO / SDR Tuning / VFO | 冻结 | `add_vfo`、`get_demod`、`get_frequency`、`learn_noise_floor`、`list_vfos`、`read_spectrum` |
| `subaudio` | 亚音数字解码 / サブトーン・デジタル復号 / Subtone & Digital Decode | 冻结 | `get_decoder_messages`、`start_decoder`、`stop_decoder` |
| `vna-measure` | VNA 测量 / VNA 測定 / VNA Measurement | 冻结 | **（诚实空态）该面无工具** |
| `vna-analyze` | VNA 分析 / VNA 解析 / VNA Analysis | 冻结 | `vna_analyze_sweep` |
| `vna-calib` | VNA 校准 / VNA 較正 / VNA Calibration | 冻结 | `vna_apply_calibration`、`vna_solve_error_box` |
| `export` | 导出演制 / 録音・エクスポート / Recording & Export | 冻结 | `export_peaks_csv`、`export_spectrum_snapshot`、`export_vna_report`、`get_recording_status`、`read_audio`、`read_iq` |
| `sat-scan` | 书签扫描卫星 / ブックマーク / 走査 / 衛星 / Bookmarks, Scan & Satellites | 冻结 | `add_bookmark`、`delete_bookmark`、`find_nearest_bookmark`、`get_satellite_pass`、`get_scan_results`、`list_bookmarks` |
| `system` | 系统设备 / システム / デバイス / System & Devices | 冻结 | `connect`、`disconnect`、`get_service_status`、`get_status`、`is_connected`、`list_audio_outputs` |
| `memory` | 记忆编排 / メモリ編成 / Memory Orchestration | 冻结 | **（诚实空态）该面无工具** |
| `workbench` | 工作台编排 / ワークベンチ編成 / Workbench Orchestration | 冻结 | `aigui_component_catalog`、`aigui_render_preview`、`aigui_validate_message` |
| `decoder-tail` | 解码器长尾 / デコーダ長尾 / Decoder Long Tail | Python 扩展 | `decoder_dab_fic_decode`、`decoder_fst4_encode_text`、`decoder_gk2a_parse_headers`、`decoder_nrsc5_frame_parse`、`decoder_rtl433_decode`、`decoder_rtl433_list` |
| `qa` | 人机问答 / 対話質問 / Human Q&A | Python 扩展 | `answer_question`、`ask_question`、`question_status` |
| `external-tools` | 外部工具 / 外部ツール / External Tools | Python 扩展 | `mcp_client_call`、`mcp_client_connect`、`mcp_client_disconnect`、`mcp_client_list` |
| `sat-link` | 卫星链路 / 衛星リンク / Satellite Links | Python 扩展 | `sat_link_get`、`sat_link_identify`、`sat_link_list`、`sat_link_tuning` |

产品侧稳定 id 与三语译名在 `sync_from_repo.py` 的 `DOMAIN_I18N` 里显式维护（属于本地化层，
不是仓库事实）；图标同理。

## 2. 规划 / 未验证能力（诚实标注）

以下三项来自历史设计稿与审计结论，**代码里没有落地实现**，产品里标 `not_verified`、
开关禁用，不画成已上线功能（AGENTS.md §2 诚实文化）：

| id | 能力 | 为什么未验证 | 依据 |
|---|---|---|---|
| `vis6dof` | 6DOF 视觉引导 | 位姿融合 L0–L3 为规划；pose.py 的 IMU 修正算出来即丢弃，无串口/IMU 驱动 | 07-卫星指向与AR系统.md §9 · docs/audit_r2/27_gimbal_pose.md |
| `calib` | 视觉校准 | 相机-天线轴系标定为规划；gimbal.py 的 read_pose() 在 manual/board 模式恒返回 0° | docs/audit_r2/27_gimbal_pose.md |
| `vrxr` | VR / XR | OpenXR / 头显 6DOF 为 feature:xr-sky 规划，代码里不存在 | 07-卫星指向与AR系统.md §9 |

## 3. 内核通道（事实源：`cpp/src/core/tokens.h` / `control_http_server.cpp`）

- **ControlHub**：`http://127.0.0.1:50732/`，只绑回环、无鉴权、
  跨机走 SSH 隧道；端口环境变量 `MBDSDR_CONTROL_HTTP_PORT`，
  设置键 `control/httpPort`。
- 发现文档 `GET /`、状态 `GET /status`、命令 `POST /command`（body `{"tool":...,"args":{...}}`）。
  代码里抓到的路径：`/`、`/status`、`/command`、`/pocsag_messages`、`/m17_calls`、`/vor_radial`、`/acars_packets`、`/navtex_messages`。
- **写工具一律过 `arm_manual` 人工门**：`gated:true` 是「人工门未确认」，不是执行失败、
  更不是设备故障。UI 必须区分这两种回执。写工具名模式来自
  `mbdsdr_ai/tool_registry.py: WRITE_TOOL_NAME_PATTERNS`（82 条）。
- 另有 MCP `tools/list` / `tools/call`（`mbdsdr-core`，协议 `2024-11-05`）
  与 Headless `SDRController`（`docs/headless_api.md`）。三通道行为一致。
- **不要沿用旧端口**：历史设计稿里的 `7878`、`81` 等 Python 后端已不存在；`7356` 是 rigctl、
  `8000` 是 Python `start_web_server`、`4533` 是 rotctld，都不是 ControlHub。

## 4. 设计令牌（事实源：`desktop/tokens.py`）

产品 CSS 变量由 `THEME_COLORS["figma"]` 生成：`bg #000000`、
`card #1f1f1f`、`button #2C2C2C`、`border #404040`、
`primary #D9D9D9`、`text #FFFFFF`；圆角与字号沿用
`RADIUS` / `FONT`（`card_lg 24px`、`dock_icon 13px` 等）。
映射表见 `docs/design_tokens_mapping.md`。**外观令牌只描述外观，不塞业务数值**。

## 5. 站点与红线

- 默认站点 = 中性示例 `0.0, 0.0`、`trusted=false`；`mbdsdr_ai/station.py` 的守卫在站址不可信时
  拒绝过境计算，产品复刻同一行为（AGENTS.md §1 身份红线：禁止真实地理位置默认值）。
- 不硬编码能力数字（工具数/测试数/N 域）；能力用可复现命令证明，不用数字标语。
- 无硬件 → 显式空态或 `{"data":null,"error":"not_connected","suggestion":...}`，
  **绝不合成 IQ 冒充测量**；调试源若存在必须显式标「非真实设备」。
- 定轨不是功能：UI 上零入口，只有用户在对话里说出口才触发（用户 2026-11-04 铁律）。
- 全仓 MIT；Stellarium Web Engine 是 AGPL-3.0，**只学机制、不引代码 / 贴图 / 星表**。

## 6. 已知文档漂移（不要拿旧文档当事实源）

- docs/mcp_tools.md 末尾标记区为自动生成且已滞后（无 sat_link_* 工具），不可手抄
- README.md 的域数描述与代码 ALL_DOMAIN_NAMES 不一致，以 mcp_server.py 为准
- 07-卫星指向与AR系统.md 是历史设计稿：北京默认坐标、GPL-3.0 声明、/api/pointing/* 均未落地

## 7. 一致性保证

`tests/verify_product.py` 会核对：`repo-facts.json` ↔ `src/05-repo-facts.js` ↔
wiki 域词条文件集合 ↔ `dist/index.html` 内联数据；并扫描真实地名、能力数字标语、
规划项标注、诚实空态与定轨零入口。任何一处漂移即失败。
