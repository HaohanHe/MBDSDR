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

**阶段 A 已入库；阶段 B 的 Python 侧已入库，宿主注入未做**（`1d74cbac`）：
`mbdsdr_ai/web_bridge.py` 把 `ToolRegistry` 映射成 `window.mbdsdrBridge` 的可注入
形状，8 个契约方法与产品 `MB.CONTRACT` 逐字对拍，写工具门对齐内核 S13 红线——
ControlHub 是 C++ 宿主写开关 `writeEnabled_`，Python 内核是外部签发的一次性
`confirm_token`，**模型自报 `arm_manual` 无效**，前端只透传、不签发、不自判放行。

**仍未接通**：宿主注入（QWebChannel / JS 全局）、`confirm_channel` 接
`mcp_server.call_tool`、真实硬件逐工具实测。写工具在此之前恒返回 `gated`，
产品默认仍是离线诚实空态，不伪装成已连接内核。

> 台账 `docs/COORDINATION.md` 队列 #12 的闭环登记待该文件占用锁
> （诚实工程线 2026-11-06 00:39 claim，TTL 120min）释放后补写；
> 本页是锁定期的状态镜像，避免其它会话重复开工。
