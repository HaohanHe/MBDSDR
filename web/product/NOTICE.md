# NOTICE — MBDSDR Web 产品工程

本目录是 MBDSDR 的 Web 产品工程（星象仪模式 + 专业模式）。**代码与数据许可不同，
不得整体当成公共领域，也不得整体当成 MIT。**

## 1. 代码（MIT）

`src/*.js`（除 `12-sky-catalog.js` 的数据部分）、`build/*.py`、`tests/*`、
`run_all.py`、`paths.py`、`index.html`、`dist/index.html` 由本仓库自研，
跟随 MBDSDR 仓库的 MIT 许可（见仓库根 `LICENSE` 与 SPDX 头）。

设计语言（黑白灰阶 / MiSans / 圆角 / 软阴影）取自项目自带的 `.figma` 导出稿，
属本项目自有资产，不引入第三方设计库。

## 2. 全天星场数据 — HYG v4.1（CC BY-SA 4.0）

- 来源：`astronexus/HYG-Database` @ main · `hyg/CURRENT/hygdata_v41.csv`
  （Hipparcos + Yale Bright Star Catalogue 5th ed. + Gliese 近星星表汇编）
- 作者 / 署名：David Nash / Astronomy Nexus
- 许可：**CC BY-SA 4.0**，https://creativecommons.org/licenses/by-sa/4.0/
  —— 必须署名、必须以相同方式共享；**不是公共领域**。
- 本地副本：`data/hygdata_v41.csv`（119,626 行，J2000 / ICRF）
- SHA256：`eb086d463456ba8c565c6432d741e3de3f8191f0262bbddb6ce51513191e8c9e`
  （另存于 `data/hygdata_v41.sha256`，由 `build/fetch_hyg.py` 用 git 稀疏克隆取回）
- 派生：取 V ≤ 7.5 的 25,792 颗，导出为定点数组写入 `src/12-sky-catalog.js`。
  **该文件的星表数据部分按 CC BY-SA 4.0 提供**，不随代码改为 MIT。

## 3. 深空天体目录 — OpenNGC（CC BY-SA 4.0）

- 事实源：仓库 `mbdsdr_ai/data/dso_catalog.json`（schema `mbdsdr.dso_catalog.v2`），
  Messier 1–110 全量 + 7 个亮 NGC，共 117 条。
- 原始出处：https://github.com/mattiaverga/OpenNGC （`NGC.csv` + `addendum.csv`）
- 署名原文：

  > Deep-sky object data from OpenNGC (https://github.com/mattiaverga/OpenNGC),
  > by Mattia Verga and contributors, licensed CC BY-SA 4.0. Chinese names are
  > Wikidata labels (CC0 1.0) matched by P528 catalog number; entries without a
  > Wikidata label are marked 自译 or left empty. This derived catalog is
  > distributed under CC BY-SA 4.0.

- 中文名：Wikidata 标签（CC0 1.0）；无标签者标「自译」或留空。
- 产品只画**类型符号**，不还原形态与表面亮度；源目录缺失的星等 / 角径保持
  `null`，绝不拿 0 / 99 冒充（与仓库 `dso.py` 同口径）。

## 4. 其余天文事实值

- 141 颗命名亮星、27 座连线、中文星官名：来自仓库
  `mbdsdr_ai/skyengine/{stars,constellations}.py` 与
  `mbdsdr_ai/data/*_culture.json`，与 HYG 逐值同源对拍（Sirius 等）。
- 卫星目录 / 下行频率 / 时间倍率 / 工具域 / ControlHub 端口：由
  `build/sync_from_repo.py` 只读解析仓库代码生成，不手抄、不另造事实。

## 5. 机制学习，不引代码

 sky 引擎的球面投影、视图空间与裁剪机制，研究过
**Stellarium Web Engine**（`reference/stellarium-web-engine`，AGPL-3.0）：
`projection.c` / `proj_perspective.c` / `proj_stereographic.c` /
`painter.c` / `modules/landscape.c`。

**本工程未复制、未改编、未链接其任何源码、星表、贴图或着色器**；
只把数学关系（视图空间 X/Y/Z、针孔透视 `x=X/Z·f`、立体投影 `r=2tan(θ/2)`、
窗口矩形裁剪、地景固定在地平坐标系）独立重写为 MIT 代码。
机制笔记见 `docs/08-engine-mechanism-notes.md`，其中不含 AGPL 源码原文。

## 6. 明确未做的事

`docs/10-sky-catalog.md`、`VERIFY-REPORT.md` 的 `not_verified` 逐条登记：
真实 SDR 链路、IQ/频谱、TLE 解算、完整岁差章动链、天空亮度→极限星等模型、
银河数据层、TimeEngine 接线、桌面端端到端对拍均未接通。
界面里对应位置一律**诚实空态**，不伪装成已实现。
