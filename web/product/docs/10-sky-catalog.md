# 全天星表 · 来源、口径与 not_verified

> 由 `build/sync_sky_catalog.py` 自动生成。数据不进人工维护，重跑即与事实源对齐。
> **数据是真下载的**（`build/fetch_hyg.py` 用 git 稀疏克隆取回），不是本地造的。

## 事实源

| 项 | 值 |
|---|---|
| 全天星场 | HYG v4.1 **25,792 颗**（V ≤ 7.5 等） |
| 源文件 | `astronexus/HYG-Database @ main · hyg/CURRENT/hygdata_v41.csv` · 34,052,175 bytes |
| SHA256 | `eb086d463456ba8c565c6432d741e3de3f8191f0262bbddb6ce51513191e8c9e` |
| 许可 | CC BY-SA 4.0 (David Nash / Astronomy Nexus)（HYG v4.1 为 CC BY-SA 4.0（署名 + 相同方式共享），不是公共领域；产品数据层随其口径，已在 NOTICE 式登记中署名。） |
| 历元 | J2000 (equinox & epoch 2000.0)；文件 RA 单位=hours → 导出时 ×15 转度 |
| 命名亮星 | 141 颗（仓库 `stars.py`/`constellations.py`，含中文星官） |
| 星座 | 27 座 / 115 段连线（端点全部可查） |
| DSO | 117 条（类型符号，非形态还原） |
| DSO 来源 | OpenNGC · CC-BY-SA-4.0 · schema `mbdsdr.dso_catalog.v2` |
| DSO 未知量 | 星等缺失 7 条 / 角径缺失 2 条（**保持 null，不拿 0/99 冒充**） |
| 星等范围 | -26.7 ~ 7.5 |

**条数全部由数据文件实际读出**；`HYG v4.1` 与仓库 141 颗亮星逐值同源
（Sirius RA=6.752481h×15=101.2872°、B-V=0.009 与 `stars.py` 一致）。

### 星等分档（导出数据实测）

| 亮于 | 颗数 |
|---|---|
| 1 等 | 17 |
| 2 等 | 51 |
| 3 等 | 180 |
| 4 等 | 524 |
| 5 等 | 1,638 |
| 6 等 | 5,071 |
| 6.5 等 | 8,921 |
| 7 等 | 15,599 |
| 7.5 等 | 25,792 |

广角视场只放开到较亮的星等，放大（窄 FOV）逐步放开到全表最深等——
**星始终在那里，变的只是显示门限与投影尺度**。

## 渲染口径

- 恒星位置固定 J2000 赤道坐标；**星星不移动、不飞散、无独立角速度**。
- 每帧：模拟时刻 → JD → GMST → 地方恒星时 LST → 时角 → 地平 az/alt
  → 视图空间 (X,Y,Z) → **全屏平面投影**：≤88° 针孔透视 `x=X/Z·f`，更大 FOV 立体投影 `r=2·tan(θ/2)`。
- **天空是球、屏幕是平面**：裁剪只靠窗口矩形，没有圆形视场遮罩。透视下地平大圆成直线（针孔相机的正确像），立体投影下成圆弧。
- 周日视运动 = 整个天球随 LST 刚性旋转（所有星共用一个角）。
- 入场 = **FOV 收束**（160° → 100°）+ 暗适应，只改投影尺度与显示门限；星位一帧不动，星座形状与角距离守恒。
- 缩放改 `fov_deg`，不做相机推拉、不沿半径平移星点。
- 星等→半径/亮度按 Pogson 定律 gamma 近似；B-V→色温近似；地平附近大气消光。
- 可见判据（同一套门）：视锥/地平门 + 白天整层门 + 消光 + Bortle 基线 + FOV 视觉适应门限。
- 银河：**无数据·不提供**，不画装饰带（原仓库 limitations L25/L28）。

## DSO 换源（2026-11-05 事实源漂移）

- 仓库 `f0c8fa25` 把 DSO 从 Stellarium `catalog.txt`（GPL）换成 **OpenNGC**（CC-BY-SA-4.0），退役 GPL 数据；`dbf2a3e7` 补 `axis_ratio` 与来源标记。
- 产品同步链随之从 `astronomy/dso.py` 的 `_DSO_ROWS` AST 字面量，改读 `mbdsdr_ai/data/dso_catalog.json`（`meta` + `objects`），并校验 `schema` 与 `source_license`；RA 已是**度**，不再 ×15。
- 署名：Deep-sky object data from OpenNGC (https://github.com/mattiaverga/OpenNGC), by Mattia Verga and contributors, licensed CC BY-SA 4.0. Chinese names are Wikidata labels (CC0 1.0) matched by P528 catalog number; entries without a Wikidata label are marked 自译 or left empty. This derived catalog is distributed under CC BY-SA 4.0.
- 未知星等 / 角径保持 `null`，前端只画类型符号、不参与亮度门，与仓库「绝不用 0/99 冒充」同口径。

## not_verified（勿当作已上线）

- 岁差/章动/自行：Web 用 J2000 直算 LST，未接 celestial_geometry 完整坐标链
- 天空亮度→极限星等：原仓库 limitations L24 明确未做；Web 的 FOV 相关门限是自研显示门限（设计项，未验证），非望远镜口径增益
- 银河图层：原仓库 L25/L28 无数据未接入，产品保持诚实空态
- DSO 形态/表面亮度：原仓库 L26 明确不做还原，仅类型符号
- TimeEngine 接线：Web 离线用本地模拟时刻，未接 new_spacetime.TimeEngine
- 卫星位置：示意方位，未接 TLE/SGP4 解算；站址未校准不给可信过境结论

## 与桌面端的关系

原仓库 `desktop/rf_sky_view.py` / `mbdsdr_ai/celestial_geometry.py` 有完整坐标链、
球面投影、DSO 层与时间引擎（带测试）。Web 产品是独立自研的轻量近似，
未与桌面端联调，不宣称等价。差距与上面 not_verified 逐条对应。

生成时间：2026-11-06T00:42:04
