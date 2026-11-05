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
| 数据与代码许可分列 | [`NOTICE.md`](../../web/product/NOTICE.md) |

复现：`cd web/product && python run_all.py`。事实源（工具域、ControlHub 端口、
时间倍率、卫星目录、DSO 目录）变了，重跑即可发现漂移并重建全部产物。

**阶段 A 已入库，阶段 B 未做**：`window.mbdsdrBridge` 到
`mbdsdr_ai/tool_registry.py` 的运行时桥接尚未接通；产品离线时走诚实空态，
不伪装成已连接内核。详见 `docs/COORDINATION.md` 队列。
