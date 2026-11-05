# 工具域词条 · 工作台编排 `workbench`

> 冻结域（与 C++ 同名同序钉扎） · 图标 `▦` · 事实源 `mbdsdr_ai/core/mcp_server.py: FROZEN/PYTHON_EXTENSION_DOMAIN_SUMMARIES + _TOOL_DOMAIN`

| 语言 | 名称 |
|---|---|
| 简体中文 | 工作台编排 |
| 日本語 | ワークベンチ編成 |
| English | Workbench Orchestration |

## 这个域解决什么

AIGUI 三栏工作台布局编排：按编译期白名单调整 splitter 尺寸/右侧 tab 可见性，未知 id 诚实占位，不碰 DSP/音频/硬件。

## 域内的真实工具

`aigui_component_catalog`、`aigui_render_preview`、`aigui_validate_message`

代表工具：`aigui_component_catalog`、`aigui_render_preview`、`aigui_validate_message`

工具名来自 `mbdsdr_ai/core/mcp_server.py` 的 `_TOOL_DOMAIN`（工具 → 域映射），
由 `build/sync_from_repo.py` 只读抽取。**不要手工往这里加工具**——工具面以运行时
`tools/list` 为准，文档清单可能滞后。

## 能力而非流程

这个域描述模型可调用的能力集合，**不规定调用顺序**。模型按现场设备、站址可信度、
用户回答动态装配任务；没有一键完成任务的黑盒工具（原仓库 `capability-map.md` 的设计红线）。

## 调用通道

- ControlHub：`POST http://127.0.0.1:50732/command`，body `{"tool": "...", "args": {...}}`
- MCP：`tools/call`（`mbdsdr-core`）
- Headless：`SDRController` 方法面

写工具一律过 `arm_manual` 人工门；`gated:true` 表示**人工门未确认**，不是执行失败，
也不是设备故障。离线时返回 `{"ok":false,"error":"not_connected","suggestion":"..."}`，
不伪装成功。

## 现状与 not_verified

- 工具名与域归属：已由代码抽取并钉扎（本页即证据）。
- 本 UI 是否接线：默认离线（未连接 ControlHub），需 `?hub=1` 或宿主注入桥才启用；
  接线后的逐工具实测口径以 `docs/tool-availability.md` 的审计为准，本页不宣称已验证。
- 空域保持空态，不塞假工具。

## 相关

- [能力百科索引](../README.md)
- [原仓库对齐说明](../../09-repo-alignment.md)
- [分层架构与内核桥](../../03-architecture.md)
- [术语表](../glossary.md)
