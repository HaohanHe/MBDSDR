# MBDSDR Web 产品工程

一套系统、两种形态、**两个主导**：

- **星象仪模式 = 时空主导**：全天球 / 模拟时刻 / 坐标 / FOV。相机在球心，
  天空是球面数据，屏幕是全屏平面投影；地景绕 360° 方位铺在地平坐标系里。
- **专业模式 = SDR · 频谱主导**：能力矩阵来自原仓库 14 个工具域，
  无 IQ 输入时一律诚实空态，不画假谱线。

界面上的一切都不是写死的流程，而是**模型可调用的能力**；
「定轨」在 UI 里零入口，只有用户在对话框里主动说才会触发。

## 跑起来

```bash
# 开发态（多文件分层，直接打开）
index.html

# 发布态（单文件，可离线分发，无运行时网络请求）
dist/index.html

# 一键复现验收：事实源同步 → 星表同步 → 骨架 → 文档 → 单文件
# → 静态守卫 → 投影自检 → 无头运行时 → Edge 截图 → VERIFY-REPORT.md
python run_all.py
```

`run_all.py` 只读原仓库、不修改其中任何文件；仓库位置由 `paths.py` 自动探测
（本目录在仓库 `web/product/` 时取 `../..`），也可用环境变量 `MBDSDR_REPO` 指定。
没有 Edge 就跳过截图，不算失败。

## 目录

| 路径 | 内容 |
|---|---|
| `src/` | 分层前端模块（00-core → 70-docs → 60-app），`build.py` 拼成单文件 |
| `build/` | 事实源同步器、生成器、单文件构建（全部只读原仓库） |
| `tests/` | 静态守卫 / 无头运行时 / 投影自检 / 视觉夹具 |
| `docs/` | 36 篇产品文档：规格、架构、机制笔记、星表口径、wiki、场景 |
| `data/` | HYG v4.1 源 CSV（33 MB）+ SHA256 + 许可凭据 |
| `prototype/` | v1 单文件原型（CSS / HTML 骨架的唯一来源） |
| `dist/index.html` | 自包含发布产物 |
| `NOTICE.md` | **数据与代码许可分列**（HYG / OpenNGC CC BY-SA 4.0） |

## 事实源纪律

能力域、工具名、ControlHub 端口、时间倍率、卫星目录、设计令牌全部由
`build/sync_from_repo.py` 从仓库代码解析；星表由 `build/sync_sky_catalog.py`
从 HYG v4.1 + 仓库命名星/连线/DSO 目录生成。**不手抄、不造数据**，
仓库事实变了重跑 `run_all.py` 就能发现（2026-11-05 的 DSO 换源就是这么抓到的）。

## 许可与未验证项

`NOTICE.md` 逐条登记：代码 MIT；HYG v4.1 与 OpenNGC 数据及其派生
**CC BY-SA 4.0**（不得标公共领域）；Stellarium Web Engine 只学数学机制，
未引入任何 AGPL 代码。`VERIFY-REPORT.md` / `docs/10-sky-catalog.md` 的
`not_verified` 列明真实 SDR 链路、TLE 解算、完整岁差章动链等**尚未接通**的部分。
