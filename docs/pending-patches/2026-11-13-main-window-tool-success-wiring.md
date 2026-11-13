# `desktop/main_window.py` 在途 hunk 归属（三）：S1-S4 结果判定同源接线

登记日期：2026-11-13
登记人：收尸线（父会话 `6ae85f56a16a76df1598024e`）
触发：`73b75378` 之后复测该文件，`-U3` 由 17 组变 20 组（`-U0` 31 → 34 段），
新增 3 组不属于前两份归属文档。按「没覆盖且属于已知待办」归到本文件，
不并进设备会话写门批次。

所有者：S1-S4结果伪造线/6ae85f56。
线的状态：**部分在途**——`09712c6f`（S3）已落库「工具成败只认
`ToolResult.success`，步骤卡/状态条/Hooks 同源」，`mbdsdr_ai/tool_results.py`
的 `tool_success` 已在 HEAD；主窗口这三处调用点没跟着落。

## 一、3 组逐段定性（判据：HEAD 里有没有同样的逻辑）

| # | 触发符号 | 改动摘要 | HEAD 是否已有 | HEAD 证据 |
|---|---|---|---|---|
| 1 | 模块级 import | `from mbdsdr_ai.tool_results import tool_success` | 否 | `git show HEAD:desktop/main_window.py` 里 `tool_success` 出现 0 次 |
| 2 | `_mcp_call_handler` | `ok = "error" not in raw and not raw.get("skipped")` → 改用 `tool_success(raw)`，不再现算成败 | 否 | HEAD 同函数仍是旧表达式 |
| 3 | `_on_worker_tool_result` | `("error" in result or result.get("skipped"))` → `not tool_success(result)` | 否 | HEAD 同处仍是旧条件 |

三组同属一件事：**主窗口不再自己解释结果字典，成败只读 `ToolResult.success`**。
与 `09712c6f` 的口径一致；HEAD 的 `tool_results.py` 已提供该函数，缺的只是调用点。

## 二、接取前提

1. `python scripts/file_claim.py status desktop/main_window.py` 无活跃持有者；
   该文件现由窄屏高度链在改，先约定提交顺序，不要整文件 `git add`。
2. 只取这 3 组；其余 17 组属
   `2026-11-13-main-window-session-write-gate.md`（14 组）与
   `2026-11-13-keyboard-focus-chain-unlanded.md`（3 组），不要混进同一笔。
3. 按 §6.1.4 写接管记录（≤30 分钟宽限、进程、提交、在途四条必填）。

## 三、验收判据

1. `tests/test_tool_failure_consistency.py` 全绿（已落库的 S3 用例覆盖步骤卡/
   Hooks/编排；本文件只补主窗口这两条路径）。
2. 反证：临时把三处改回「结果字典现算」（`"error" not in raw`）→ 构造
   `{"success": False, "error": ...}` 且无 `skipped` 的工具结果时，主窗口
   必须把它当失败；用例必须红。
3. 不新增控件、不改布局；提交说明注明「只换成败判据，无视觉改动」。

## 四、不接的合法出口

显式回退这 3 组（只回退这三段，不碰另外 17 组），并在 COORDINATION 的
S1-S4 记录下补一行「主窗口接线未落库、已回退」，避免记录与树不一致。
