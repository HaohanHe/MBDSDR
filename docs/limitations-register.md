# 限制总账（Limitations Register）

> 维护口径：**P11 集成体检（2026-11-04）汇总**。本表只回答一个问题：
> 这个能力是「真验过」，还是「只用合成信号/假后端自测过」，还是「根本没验」。
>
> 三档定义：
> - **真验过**：有真机、真实样本或第三方真值背书（可写出证据文件/命令）。
> - **仅自测**：只有本仓合成 fixture、假后端或单测自洽，不构成外部验证。
> - **未验证**：本轮环境物理上做不到（无硬件/无过境/无 key/无网络/待用户决策）。
>
> 纪律：未验证项不得在汇报或文档里写成「已验证」。数字均为本轮实测快照，
> 会随提交浮动；以各条「复跑条件」里的命令重新取数为准。

## 0. 一页判读

| 档位 | 条目 |
| --- | --- |
| 真验过 | RTL-SDR（FC0012）七环验收 R1–R5/R7；NanoVNA 真机 SOLT；FT8 外采 WSJT-X 官方样本；ADS-B 外采 dump1090 全量清单；LRPT 外采 TU Berlin 实录；GPS C/A IS-GPS-200E 矢量；DVB-S2 BCH/LDPC KAT |
| 仅自测 | 全量工具面与 schema 一致性；AI 上下文预算/工具裁剪/并发取消/错误恢复；录音库 P9 全系列；TLE 编号三方校验；发射门/状态机/半双工互斥（假后端）；天空/云台编排 |
| 未验证 | 真实发射（PlutoSDR）；1090 MHz ADS-B；云台/rotator 闭环；真过境 APRS/NOAA-APT/Meteor OTA；真实 LLM 联调（工具选择/流式取消/token 计数）；celestrak/NTP/open API 实时网络；arm64 Chromebook 真机；听感验收 |

## 1. 待真机（有代码、无硬件）

| 编号 | 条目 | 来源线 / 证据 | 现状 | 复跑条件 |
| --- | --- | --- | --- | --- |
| L1 | 发射链路真发（CW/IQ/回放发射/TC 上行） | 发射线 `2aa912c`/`6094b87`/`322aba4`/`899882c`；`tests/test_tx_no_hardware.py` 用假后端 | 仅自测 | PlutoSDR + 衰减器/假负载 + 一台接收机；设 `MBDSDR_TX_BANDS` 与 `MBDSDR_TX_TOKEN`，先失败路径（未 arm/错 token/超频段）再真发 |
| L2 | 云台/rotator 闭环（`gimbal_track_satellite`、`sky_gimbal_follow`、`gimbal_rssi_sweep`） | 天空/云台线；`sdr_tools._gimbal_rssi_sweep`（`agent.sdr_manager.get_active()` 先例） | 仅自测 | 真 rotator（串口 RPRT）+ 真实过境；先验 `gimbal_read_pose` 回读一致，再验跟踪 |
| L3 | ADS-B 1090 MHz 接收 | `docs/real_hw_acceptance_20261103.md` 环 6 = UNVERIFIABLE（FC0012 上限约 948.6 MHz） | 未验证 | 换 R820T/R820T2 棒，`python scripts/real_hw_acceptance.py --ring 6` |
| L4 | 听感验收（链内 AGC、长时音高漂移） | 同上文档「不可验声明」；本轮全程 `MBDSDR_NULL_AUDIO=1` | 未验证 | 用户放行声卡后人工听；数据面（导频/RMS/频谱含量）已另行验证 |
| L5 | NanoVNA 命令细节（`vbat`/`freqoffset`/`edelay`/`averages`） | `docs/learn/nanovna/protocol-study.md` 逐行标「待真机验证」；SOLT 已于 2026-10-23 真机验过 | 部分真验 | 真机逐条发命令并记录回显；单位/上限语义以实测为准 |
| L6 | 物理 arm64 Chromebook 启动/USB 直收/rtl_tcp | `docs/CHROMEOS_CROSTINI_SDR.md`（qemu 交叉编译已过，物理机未验） | 未验证 | 真机启动 + `QT_QPA_PLATFORM=offscreen` ctest + USB 直收 |

## 2. 待真样本（有链路、无真实信号）

| 编号 | 条目 | 来源线 / 证据 | 现状 | 复跑条件 |
| --- | --- | --- | --- | --- |
| L7 | APRS / NOAA-APT / Meteor LRPT OTA 解码 | `docs/real_hw_acceptance_20261103.md` 环 6 | 未验证（文件级真录另有验证） | 等在轨过境/本地台活动，`python scripts/real_hw_acceptance.py --ring 6 --aprs-seconds 120` |
| L8 | FT8 本机 OTA 自采 | `docs/learn/capability-map.md`（外采真值 5/14 命中，本机 OTA 记录为零） | 部分真验（外采真值） | 自架天线录一段 OTA wav 接 `tests/test_ft8_wsjtx_golden.py` 同链 |
| L9 | LRPT Gardner 环修复（默认关） | 协作台账「LRPT 队列#9」（实验已测未入库；`mbdsdr_ai/lrpt_modem.py` 属他线在途，未代改） | 仅自测 | 用真实 Meteor 过境样本跑开关对照；先等该文件不在途 |
| L10 | 录音库迁移/回收站/损坏索引 | 录音库线 `b55420b`/`2de82f3`/`f902ba7`/`613665e`/`c4b1b36`/`8a3940d` | 仅自测（合成库 + 临时目录） | 复制一份真实录音库副本（不碰原件）跑迁移预演与回收站路径 |
| L11 | JAMX01 活动真过境 SSTV/SSDV | `docs/learn/phase34/event-rx-runbook.md`（多处【待真机确认】） | 未验证（合成级联 IQ 已出图） | 活动窗口真过境；SSTV 制式/行数与 SSDV 包流现场核对 |

## 3. 待真模型（有 agent、无真实 LLM）

| 编号 | 条目 | 来源线 / 证据 | 现状 | 复跑条件 |
| --- | --- | --- | --- | --- |
| L12 | token 预算与上下文压缩 | AI 线 `9ba99c3`（P7.3）；`context_manager.estimate_tokens` 在 tiktoken 缺失时按启发式估算 | 仅自测 | 装 `tiktoken` + 真模型，对同会话同时记录估算值与 usage 实际值 |
| L13 | 工具选择准确性 40/40 语料 | AI 线 `4da1944`（P7.1）；语料为自建 | 仅自测 | 真模型跑 `scripts/ai_intent_regression.py`；语料是否够用待用户拍板 |
| L14 | 流式取消 / 错误恢复不谎报 | AI 线 `121b0b1`（P7.2）、`a7cfe3a`（P7.4）；测试用假模型 | 仅自测 | 真 provider 触发流中断/取消/超时，核对终态与计费留痕 |
| L15 | `tool_choice` 的 required/none/指定函数名 | `docs/learn/model-tool-calling/04-sf-chat-completions-api.md` 第 329 行（文档未覆盖） | 未验证 | 有 key 后逐个取值联调；默认只当 `auto` 用 |

## 4. 待网络（有代码、无外网）

| 编号 | 条目 | 来源线 / 证据 | 现状 | 复跑条件 |
| --- | --- | --- | --- | --- |
| L16 | TLE 编号-名称-来源三方校验 | P10-1 `f7f1d27`；离线只用内置/回退 TLE | 仅自测 | 联网拉 celestrak 实时 TLE，跑校验并记录 epoch/source |
| L17 | NTP / open API（ISS、天气、航班）/ web_fetch / 外部 MCP 客户端 | `docs/learn/capability-map.md` §外部依赖销账表（失败行为已核对，真实网络未验） | 仅自测（失败路径） | 联网逐项调用；失败路径由 `tests/test_external_api_honesty.py` 桩测已锁 |

## 5. 待用户拍板（行为与范围）

| 编号 | 条目 | 来源线 / 证据 | 现状 | 复跑条件 |
| --- | --- | --- | --- | --- |
| L18 | `arm_manual` 人工门策略（是否允许会话 token 自动 arm） | 发射线 `322aba4`；注册表 94 个写工具走门 | 仅自测（策略层） | 用户给出部署策略后再调 `tx_guard` 默认值 |
| L19 | 发射频段/合规策略（`MBDSDR_TX_BANDS` 未设时一律拒发） | `tx_guard.policy_from_env` | 仅自测 | 用户确认目标频段与合规边界 |
| L20 | 工具面是否全量暴露给模型（预算裁剪上限） | AI 线 `4da1944`；`tool_budget` 裁剪后 ≤8192 token | 仅自测 | 用户决定保留/隐藏分类；真模型侧看误选率 |
| L21 | MCP 面范围（控制器面 82 个 vs AI 注册表 495 个） | `mbdsdr_ai/core/mcp_server.py`、`docs/mcp_tools.md`；注册表 meta `mcp` 分类 = 0 | 设计分裂，未拍板 | 用户决定是否把 AI 工具镜像进 MCP；当前只在 Agent 面可用 |
| L22 | 本轮集成验收红线（哪些红必须清零才合并） | P11 任务书 | 待确认 | 用户对「环境红/真红」的处置口径 |

## 附 A：工具面总账（本轮实测快照）

- 总数：**495**；分类：**75**；`available` 非 True：**0**；`handler` 缺失：**0**；
  `write=True`：**94**；注册表 meta `category="mcp"`：**0**。
- schema/handler 一致性：`scripts/ai_tool_consistency.py` 扫描 **495/495** 可读源码，
  结构性错误 **0**、漂移线索 **0**（P11 修复后）。
- sky 相关 8 个：`orbit_sky_plot`、`sdr_satellite_sky_view`、`sky_capability_report`、
  `sky_capability_state`、`sky_gimbal_follow`、`sky_track_satellite`、
  `sky_tune_satellite`、`sky_view_visible`。
- `tx` 前缀工具：**0**。发射能力不按 `tx_` 命名，实际入口是
  `sdr_transmit_cw`、`sdr_transmit_iq`、`tc_uplink_transmit`、`radio_ptt`。
- 不可用原因：注册表层面无（`available` 全 True）；**运行时**受硬件/样本/模型/网络限制，
  见第 1–4 节。
- MCP 面：`MCPToolSet` 暴露 **82** 个控制器方法面工具，不具备上述 495 个 AI 工具，
  也不含 sky/云台工具（见 L21）。
- 分类分布（快照，按数量降序）：satellite 44、decode 22、sdr_spectrum 16、sdr 15、
  orbit 15、sdr_decode 14、ham_modes 13、sdr_analysis 12、new_spacetime 12、
  digital_voice 12、gnss 11、digital_modes 10、sdr_device 10、code_editor 10、
  dsp 9、sdr_frequency 9、astronomy 9、acquisition 9、sdr_ui 9、signal_analysis 8、
  sdr_network 8、radio_control 8、spectrum 8，其余 52 类各 1–7 个。

复跑：

```bash
python scripts/ai_tool_consistency.py
python -c "from mbdsdr_ai.core.mcp_server import MCPToolSet; print(len(MCPToolSet().list_tools()))"
```

## 附 B：本轮环境特有测试事实

- `mbdsdr_ai/tests`：1 failed（`test_dtmf_handler_shape_guard`，**真红**，非环境：
  P7.2 注册层参数校验先于 handler，旧断言只认 handler 文案）。
- `tests/` A 组：1708 passed、1 skipped（约 10 分 50 秒）。
- `tests/` B 组：见 P11 汇报（含 2 个红，归因单独给）。
- `experiments/tests`：34 passed（17.22 秒）。
- `tools/onboarding` + `tools/hw_selfcheck`：54 passed、4 skipped（5.39 秒）。
- skip 口径：无硬件（RTL-TCP/设备）、可选依赖缺失（SatDump/reedsolo/zstandard）、
  无网络（真实 TLE），均为诚实 skip，不计失败。
- CI：`gh` 未安装；GitHub API 部分可用。最新 run `f7f1d27` 进行中；上一 run `b6c4cd9`
  结论：cpp/flutter/两个 tools/experiments 成功，tests A/B 被新推送取消，
  `mbdsdr_ai/tests` 失败（同一 `test_dtmf_handler_shape_guard`）。更早 run 均被并发取消除。
