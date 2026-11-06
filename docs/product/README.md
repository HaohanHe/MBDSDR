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

**阶段 A / B 已入库；阶段 C 的非 Qt 一半已可运行**（`1d74cbac` + `9dcb4ecc` 系列）：
`mbdsdr_ai/web_bridge.py` 把 `ToolRegistry` 映射成 `window.mbdsdrBridge` 的可注入
形状，8 个契约方法与产品 `MB.CONTRACT` 逐字对拍，写工具门对齐内核 S13 红线——
ControlHub 是 C++ 宿主写开关 `writeEnabled_`，Python 内核是外部签发的一次性
`confirm_token`，**模型自报 `arm_manual` 无效**，前端只透传、不签发、不自判放行。

`mbdsdr_ai/web_bridge_host.py` 把注入真正落地（**不碰 Qt**）：只绑 `127.0.0.1`，
服务 `dist/index.html` 时在 `</head>` 前注入 `/bridge.js`，同源 + `Sec-Fetch-Site`
+ 一次性 token 三重校验。用法：

```
python -m mbdsdr_ai.web_bridge_host --port 8770 --open
```

于是「一套系统两形态」在 Web 侧成立：直接打开 `dist/index.html` 走离线诚实空态；
走 host 的 `http://127.0.0.1:8770/` 才挂上桥。**它不注入 `confirm_channel`，
写工具仍恒 `gated`**——本地 host 不是打开写开关的后门，这条有测试钉死
（`pytest tests/test_web_bridge_host.py`，26 条）。

**仍未接通**：Qt WebChannel 注入（需操作逻辑线放行 `main_window.py`）、
`confirm_channel` 接 `mcp_server.call_tool`、真实硬件逐工具实测。注册表未注入
真实工具时，`isLive()` 如实返回 false，UI 保持离线形态，不伪装成已连接内核。
