# Phase63 · routed 工具分层决策：书签三工具 Agent 层真实执行，网络音频/扫描链维持路由契约

- 仓库 HEAD：`8d8642c`（改动后未 commit）；全程 CLI/offscreen，未操控 GUI，未 git add/commit/push。
- 范围文件：`cpp/src/ai/agent_tools.{h,cpp}`、`cpp/src/ai/llm_worker.{h,cpp}`、`cpp/src/ai/task_orchestrator.{h,cpp}`、`cpp/tests/test_agent.cpp`、本文档。
- 判定口径：只接真实数据 / 诚实空态，禁 mock；活动参数零硬编码；工具名单 47 个不变，仅行为升级。

## 1. 现状事实（侦察结论）

- `ui::BookmarkManager`（`cpp/src/ui/bookmark_manager.h`）是纯数据类：`add()`（freq>0 否则返回 -1，自动按 (group, freq) 排序并落 QSettings）、`removeAt()`（越界静默）、`list()`、`count()`。
- `TaskRunner` 构造即持有 `bookmarks_`（`task_runner.cpp:42 orch_.setBookmarkManager(bookmarks_)`），并已转交 `TaskOrchestrator::bookmarks_`（`task_orchestrator.h:65/94`）——**书签存储在调度链里本来就有句柄**。
- 断点：`executeTool(name, args, engine)`（`agent_tools.h:13` 自由函数）没有 bm 参数，`LLMWorker::dispatchToolCall` 也没有，所以三个书签 executor 只能 `routedOk` 打桩。
- 网络音频 / 扫描链后端实例（`NetworkAudioSink`、`ScanActivityLink`）由 **control 层**（`ControlHub` / `MainWindow` 生命周期）持有，Agent executor 没有句柄。

## 2. 决策

| 工具组 | 决策 | 理由 |
|---|---|---|
| `add_bookmark` / `tune_to_bookmark` / `delete_bookmark` | **Agent 层真实执行**（注入 `BookmarkManager*`） | 存储句柄已在调度链里（TaskRunner→TaskOrchestrator），只差把参数透传进 `executeTool`；执行语义（排序插入 / 越界删除）纯数据、无 GUI 依赖，offscreen 可完整测试 |
| `set_network_audio_sink` / `get_network_audio_status` / `start_scan_link` / `stop_scan_link` / `get_scan_link_status` | **维持 routed 契约，仅统一 note 措辞** | 后端实例活在 control 层，executor 无句柄；跨层注入要把 `MainWindow` 生命周期对象塞进 AI 自由函数，破坏分层且无法 offscreen 验证。诚实路由 + 统一 note 是当前架构下唯一不撒谎的形态 |
| `list_bookmarks` | 不动（维持诚实空态 note） | 本次范围限定三写工具；读侧留待后续与注入窗口一起补 |

## 3. 实现要点

- `executeTool(name, args, engine, ui::BookmarkManager* bm = nullptr)`：默认 nullptr 向后兼容全部既有调用点；dispatch 表函数指针同步加第 4 参。
- 三 executor：`bm==nullptr` → `{ok:false, error:"书签管理器未注入…"}`；`freq_hz/index` 校验先于注入校验（既有金集要求缺参即 ok:false）；`add()` 返回 -1 → 诚实报频率非法；`removeAt` 之前自行做越界检查（`removeAt` 本身越界静默，不检查就是撒谎）；`tune_to_bookmark` 命中后走 `engine->vfoSetOffset(engine->selectedVfoId(), freq)`（与频段框拖拽同一条带内 IF 偏置路径）。
- `LLMWorker::dispatchToolCall(..., bm = nullptr)` 加第 5 参；实例加 `setBookmarkManager()` seam（桌面主窗口注入是后续接线点，null 路径契约不变）。
- `TaskOrchestrator::run()` 删掉 `add_bookmark` 特判分支，全部步骤统一走 `dispatchToolCall(..., bookmarks_)`——回到"单一执行点"注释承诺的形态。
- `routedOk` 单点 note 统一为："命令 X 已路由，实际效果由 ControlHub 通道落地，需在 UI 或 ControlHub 确认执行"。

## 4. 验证证据（offscreen 真实运行）

- `test_agent`：30 passed / 0 failed（新增 `bookmarkToolsRealExecutionWithInjectedStore`：真实 BookmarkManager 注入，add 后 count+1、list 字段断言、tune 落地选中 VFO 频率、delete 后 count-1、index 越界诚实报错、bm=nullptr 诚实报错、dispatchToolCall seam 透传）。
- 回归：`test_task_orchestrator` 9、`test_ai_real_link` 17、`test_tool_registry` 8、`test_tool_schema` 10、`test_control_hub` 27、`test_bookmark` 3、`test_ai_function_calling` 2（1 skipped 为离线既有跳过项）全绿。
- 工具名单：`registeredToolSpecs()` == dispatch 表 == 47，`testToolParse`/registry/schema 三个金集不受影响（纯行为变更，未增删工具）。

## 5. 不做的边界

- 不在 main_window/agent.cpp 注入 LLMWorker 的 bm（超范围文件清单；null 路径已诚实报错，接线是下一个 phase 的事）。
- 不 mock 书签管理器；测试用真实 `BookmarkManager`，QSettings 按既有惯例隔离到临时目录并事后 `remove("ui/bookmarks")`。
- 不把网络音频/扫描链后端句柄拉进 AI 层——架构性分层损失大于收益，维持路由契约。
