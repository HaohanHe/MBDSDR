# 产品文档索引

MBDSDR Web 产品工程的完整文档随代码放在 [`web/product/docs/`](../../web/product/docs/README.md)，
由 `web/product/build/gen_docs.py` 与 `web/product/build/sync_sky_catalog.py`
从**本仓库代码事实源**自动生成；这里只保留入口，不复制一份会漂移的副本。

| 想看什么 | 位置 |
|---|---|
| 产品规格 / 快速开始 | [`web/product/docs/README.md`](../../web/product/docs/README.md) |
| 三语 README | `README.ja.md` / `README.en.md`（同目录） |
| 架构与天球投影链 | [`03-architecture.md`](../../web/product/docs/03-architecture.md) |
| 原仓库事实源对齐 | [`09-repo-alignment.md`](../../web/product/docs/09-repo-alignment.md) |
| 全天星表 / 许可 / not_verified | [`10-sky-catalog.md`](../../web/product/docs/10-sky-catalog.md) |
| Stellarium Web Engine 机制笔记 | [`08-engine-mechanism-notes.md`](../../web/product/docs/08-engine-mechanism-notes.md) |
| 能力百科（14 工具域 + 规划项） | [`wiki/`](../../web/product/docs/wiki/) |
| 合规与许可证纪律 | [`06-compliance.md`](../../web/product/docs/06-compliance.md) |
| 阶段 B 运行时桥接 / 写工具确认门 | [`mbdsdr_ai/web_bridge.py`](../../mbdsdr_ai/web_bridge.py) · [`03-architecture.md`](../../web/product/docs/03-architecture.md) §4 · [`09-repo-alignment.md`](../../web/product/docs/09-repo-alignment.md) §3.1 |
| 数据与代码许可分列 | [`NOTICE.md`](../../web/product/NOTICE.md) |

复现：`cd web/product && python run_all.py`。事实源（工具域、ControlHub 端口、
时间倍率、卫星目录、DSO 目录、**写工具确认门**）变了，重跑即可发现漂移并重建全部产物。
桥接层单测：`pytest tests/test_web_bridge.py`（16 条，含 Python / JS 契约对拍）。

**阶段 C 已闭环（`31b5d914`）**：`python -m mbdsdr_ai.web_bridge_host --port 8770 --open`
起一个只绑回环的宿主，把**真实内核**挂成 `window.mbdsdrBridge`：

- `mbdsdr_ai/web_bridge_mcp.py` 把 `MCPToolSet`（82 工具 / 43 写工具 / 14 域）
  接成 `WebBridge` 的 registry，`call()` 只走带门的 `call_tool()`，不碰 handler。
- 写工具要真人确认：前端 `POST /bridge/confirm` **只登记意图**，一次性 token 由
  `tx_guard.issue_confirmation()` 在**宿主进程内**签发并消费——前端拿不到、
  也伪造不了；前端传来的 `confirm_token` 一律剥离。gated 回执在 UI 上给
  「确认执行」按钮（三语文案）。
- `MBDSDR_BRIDGE_CONFIRM=deny` 整体切只读；`--no-mcp` 退回离线诚实空态。
- 「一套系统两形态」在 Web 侧成立：直接打开 `dist/index.html` 是离线诚实空态；
  走 host 才是已连内核。

**仍未接通**：Qt WebChannel 注入（需操作逻辑线放行 `main_window.py`）、
真实硬件逐工具实测、CSRF 频控与 token 轮换。确认来源如实标注为「同源本地
页面动作」，不是物理隔离通道。
