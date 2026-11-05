# MBDSDR 时空操作台 · 产品说明

> 一套系统、两种形态、**两个主导**：**星象仪模式 = 时空主导**（全天球 / 时刻 / 坐标 / FOV），**专业模式 = SDR·频谱主导**。星象仪打开即全天空展开，摄像机锁星后天动星静。
> 内核本来就是 SDR；界面上的一切都不是写死的流程，而是**模型可调用的能力**。

| 项 | 值 |
|---|---|
| 版本 | 0.3.0-product（原仓库事实源对齐版） |
| 形态 | 纯前端静态工程，无构建依赖、无运行时网络请求 |
| 语言 | 简体中文 / 日本語 / English（运行时切换，偏好持久化） |
| 设计语言 | `D:/mbdsdr/.figma` 导出稿（黑白灰阶 / MiSans / 大圆角 / 软阴影） |
| 参考形态 | Stellarium Web（交互与信息架构）；机制研究见 [引擎机制笔记](08-engine-mechanism-notes.md) |
| 许可证纪律 | 本工程 MIT 自研；**未引入任何 AGPL 代码**，只学数学与机制 → [06-compliance.md](06-compliance.md) |

## 快速开始

```bash
# 开发态（多文件分层，直接打开即可）
D:/mbdsdr/output/mbdsdr-product/index.html

# 发布态（单文件，可离线分发）
python build/build.py
D:/mbdsdr/output/mbdsdr-product/dist/index.html
```

无需 npm、无需服务器：`file://` 直接打开就能跑。文档中心（顶栏 `? 文档`）已把本目录全部内容编译进页面，离线可查。

## 两个形态，一套能力核心

| | 星象仪模式 | 专业模式 |
|---|---|---|
| 第一眼 | 全天空"唰"地展开 | 频谱 + 瀑布 + 工具树 + 协作栏 |
| 交互 | 拖拽看天、滚轮缩放、点星锁定、搜索 | 调谐参数、协议栈、拖拽分隔条 |
| 摄像机 | 锁定后卫星钉在画面中心，天球在背后动 | 不适用 |
| 触发动作 | 追踪 / 接收 / 指向 → **模型现场提问** | 直接跟模型说话 |
| 共用 | 同一套 `state.caps`、同一数据源适配层、同一模型协商引擎 | 同左 |

切换形态不会丢状态（形态、语言、能力开关、视图开关都持久化到 localStorage）。

## 目录

| 文档 | 内容 |
|---|---|
| [01-product-spec.md](01-product-spec.md) | 产品铁律、两形态定义、摄像机语义、能力而非流程、定轨规则 |
| [02-user-guide.md](02-user-guide.md) | 操作手册、三语切换、文档中心、诚实数据口径 |
| [03-architecture.md](03-architecture.md) | 分层架构、模块表、DataSource 契约、下层内核接缝、渲染管线 |
| [04-i18n.md](04-i18n.md) | 词条规范、覆盖校验、新增语言流程 |
| [05-design-language.md](05-design-language.md) | 设计 token 全表与组件口径 |
| [06-compliance.md](06-compliance.md) | AGPL/MIT 纪律、机制来源、数据出处 |
| [07-roadmap.md](07-roadmap.md) | 从原型到产品：真机、Qt、云台/GNSS/6DOF/VR 的验收门槛 |
| [08-engine-mechanism-notes.md](08-engine-mechanism-notes.md) | 天象引擎机制研究：地景坐标系 / FOV / 大气 / 渲染管线（AGPL 合规，只学机制） |
| [wiki/README.md](wiki/README.md) | 能力百科索引（真实工具域 + 规划未验证）+ 术语表 + FAQ |
| [scenarios/](scenarios/) | 科教教培 / 科学研究 / 通联 三个场景页 |

## 工程结构

```
mbdsdr-product/
├─ index.html              开发态入口（按层加载 src/*.js）
├─ dist/index.html         发布态单文件（build.py 生成）
├─ src/
│  ├─ style.css            设计语言（.figma token）
│  ├─ 00-core.js           下层：状态 / 事件总线 / 持久化 / 投影数学 / 契约
│  ├─ 10-data.js           下层：目录 / 由 repo-facts 生成的能力位 / ControlHub 适配器
│  ├─ 20-i18n.js           中层：三语词典 / 运行时替换 / 定轨关键词识别
│  ├─ 30-sky.js            上层：天球渲染 / 摄像机 / 信息卡
│  ├─ 40-workbench.js      上层：专业模式 / 频谱瀑布 / 状态栏
│  ├─ 50-dialog.js         上层：模型协商 / 对话触发 / 诚实失败回执
│  ├─ 70-docs-data.js      生成物：docs/ 编译后的文档数据
│  ├─ 70-docs.js           上层：文档中心 / 轻量 Markdown 渲染
│  └─ 60-app.js            引导层：形态 / 时钟 / 工具栏 / 能力矩阵 / 帧循环
├─ build/                  scaffold.py（骨架抽取）· build.py（单文件发布 + 文档编译）
├─ tests/                  语法 / DOM / i18n / 铁律 / 文档链 / 无头运行时校验
└─ docs/                   本文档体系
```

## 验收

```bash
python tests/verify_product.py     # 静态：语法、词条、铁律、文档链
node tests/verify_runtime.js      # 动态：mock DOM/Canvas 实跑两种绘制路径
python build/build.py             # 产物：dist/index.html（自包含）
```

校验脚本会**主动拦截**三类回归：UI 上出现定轨入口、词条缺语言、文档与能力矩阵 id 漂移。详见 [04-i18n.md](04-i18n.md) 与 [01-product-spec.md](01-product-spec.md)。

## 诚实边界（not_verified）

- **全天星表是真实数据**：HYG v4.1（Hipparcos+Yale+Gliese）25,792 颗，J2000 坐标与 V 星等，CC BY-SA 4.0；星座连线/中文星官/DSO 来自原仓库。见 [10-sky-catalog.md](10-sky-catalog.md)。
- 卫星位置与过境时刻为**演示数据**，不是实时 TLE/SGP4 结果；站址默认 `trusted=false`，界面如实提示。
- 银河图层**无数据·不提供**；宽/窄视场的显示门限是视觉适应近似（设计项，未验证）。
- 频谱/瀑布在无 IQ 时**只画网格与「未连接 SDR」空态**，绝不合成数据；真链路走 ControlHub 回环 HTTP（`?hub=1`）或宿主注入桥。
- 未接入时工具调用返回**离线失败回执**，不伪装成功（沿用仓库的诚实失败消息纪律）。
- 6DOF 视觉引导 / 视觉校准 / VR·XR 是**规划 · 未验证**（07 设计稿 §9 + gimbal/pose 审计），能力矩阵里禁用开关并标注 not_verified。


---

## 原仓库事实源（0.3.0 起）

本工程在仓库外，但**能力面、工具名、卫星目录、ControlHub 端口、时间倍率、设计令牌、
站点口径与红线全部只读抽取自 `D:/mbdsdr/MBDSDR` 的代码**，由
`build/sync_from_repo.py` 生成 `build/repo-facts.json`，前端与 wiki 再由它生成：

```bash
python build/fetch_hyg.py          # 首次：git 稀疏克隆 HYG v4.1 全天星表（34 MB，带 SHA256）
python build/sync_from_repo.py     # 只读原仓库 → repo-facts.json / 05-repo-facts.js / 09 文档
python build/sync_sky_catalog.py   # 仓库命名星 + HYG 全天星场 → 12-sky-catalog.js / 10 文档
python build/build.py              # 自动串接两次同步 → 骨架 → wiki → 单文件
python run_all.py                  # 一键：同步 → 构建 → 静态守卫 → 无头运行时 → 截图 → 报告
python tests/verify_product.py     # 核对事实源 ↔ UI ↔ wiki ↔ dist 一致
```

- **星表与投影**：星位固定 J2000 → LST → 地平 → 相机在球心 → **全屏平面投影**（≤88° 针孔透视，更大立体投影鱼眼，无圆形遮罩）；地平是球面大圆，透视下成直线、鱼眼下成弧，数据见 [10-sky-catalog.md](10-sky-catalog.md)。
- 工具域来自 `mcp_server.py` 的 `FROZEN/PYTHON_EXTENSION_DOMAIN_SUMMARIES` 与 `_TOOL_DOMAIN`；
  **空域保持诚实空态**，不塞假工具。
- 卫星目录来自 `sat_tracker.py` 的 `BUILTIN_DOWNLINK_HZ` / `_BUILTIN_TLES`；
  合成 TLE 仅离线演示（`builtin_tle_meta.py` 策略：禁止精确过境）。
- 设计令牌来自 `desktop/tokens.py` 的 `THEME_COLORS["figma"]` / `RADIUS` / `FONT`；
  仓库侧另有 `docs/design_tokens_mapping.md` 记录 .figma 导出稿到 Qt 令牌的映射。
- 默认站点 `0.0, 0.0` / `trusted=false`（`AGENTS.md §1` 身份红线），
  站址不可信时拒绝锁定与过境（`mbdsdr_ai/station.py` 守卫）。

完整对照表见 [09-repo-alignment.md](09-repo-alignment.md)。
