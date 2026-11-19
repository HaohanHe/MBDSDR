# Stellarium 星座/星官标签与连线机制

> **本文为当时状态快照**：记录当日工作状态与结论，未逐条复核；
> 不作现状依据。共 1 条未复核；需要现状请查 `docs/COORDINATION.md`、
> `docs/known-red-register.md` 与 checklist。

只读研究：参考实现位于 `reference/stellarium/`（GPL-2.0-or-later，仅学习机制，不复制代码/数据）。
目的：解释 150° 全天图里「中间全是标签」的成因，并给出比 Stellarium 更适配本仓的修正方向。

## 1. 机制摘要（file:line）

### 1.1 决定“画哪些星座名”

- 总开关是配置项，不是自动密度：`viewing/flag_constellation_name` 默认 **false**，
  `flag_constellation_drawing` 也默认 false（`src/core/modules/ConstellationMgr.cpp:103-105`）。
- 加载 sky culture 时，JSON 的 `constellations` / `asterisms` 数组分别读入
  （`StelSkyCultureMgr.cpp:271-281`），每个星座项建一个 `Constellation`，并把
  同一个 `namesDisplayed` 布尔值赋给所有对象的 `nameFader`
  （`ConstellationMgr.cpp:543-565`）。没有“按重要度只启用一部分”的逻辑。
- 绘制时遍历全部星座、全部 `XYZname` 标签点；过滤只有视口裁剪、淡入状态和
  季节可见性（`ConstellationMgr.cpp:756-774`；`Constellation.cpp:437-449`；
  `StelProjector.cpp:442-445`）。**没有优先级、没有数量上限、没有 FOV 限名**。
- 标签位置不是最亮星：默认取所有线段的单位向量均值（重心）再归一化
  （`Constellation.cpp:230-237`），并允许 sky culture 用 `label_offset`
  （`:242-255`）或 `label_positions`（`:258-276`）手工校正。Serpens 这类
  双段星座就是靠 `label_positions` 放两个名字。
- 额外的整座隐藏规则只有季节可见性：sky culture 提供 `visibility` 月份区间时，
  不在当月的整座线名都不画（`Constellation.cpp:553-576`）。`chinese` 数据里
  没有任何条目带 `visibility`（0 条），所以这条对中文星官不起作用。
- 文本以投影点为中心水平居中，`drawText(..., -width/2, 0, false)`；
  最后参数 `false` 让 gravity label 保持直立（`StelPainter.cpp:807-818`）。
- 星官（asterism）走另一套模块：`AsterismMgr::drawNames` 遍历所有
  `flagAsterism` 的对象（`AsterismMgr.cpp:351-374`）；`Asterism::drawName`
  是**唯一**带 FOV 条件的名字：`TelescopicAsterism` 在 `getFov()>60` 时直接返回
  （`Asterism.cpp:415-426`，判断在 :420）。普通星官名没有 FOV 限制。

### 1.2 连线怎么画、何时隐藏

- `ConstellationMgr::drawLines` 设置混合、线宽（`constellationLineThickness` ×
  屏幕缩放）、抗锯齿，并取 `getBoundingCap()` 作为视口半球
  （`ConstellationMgr.cpp:736-753`）。
- `Constellation::drawOptim` 逐段调用
  `drawGreatCircleArc(star1, star2, &viewportHalfspace)`；单星线段画成小圆
  （`Constellation.cpp:380-416`）。线色与名字色默认都是
  `Vec3f(0.4,0.4,0.8)`（`:49-50`），暗星座再乘 0.6667（`:387-389`）。
- 星官侧有两条独立的线组：`drawLines` 只画 `isAsterism()` 的对象，
  `drawRayHelpers` 画非 asterism 的射线辅助线；两者线宽、颜色、淡入时长都分开
  （`AsterismMgr.cpp:305-348`）。颜色见 `Asterism.cpp:43-45`。
- **宽 FOV 不会隐藏星座/星官连线**；`lineFader` 只由用户开关和淡入控制。
  唯一与 FOV 有关的常量是星座艺术图在 1°→2° 的淡入，与 150° 全天图无关
  （`ConstellationMgr.cpp:945-948`）。
- 画大圆弧时先按视口 cap 裁剪：`clippingCap->clipGreatCircle(pt1,pt2)`，然后
  `drawSmallCircleArc` 用 `fIter` 按投影畸变细分，且只要线段有一端仍在视口内
  就绘制（`StelPainter.cpp:969-983`、`:988-1028`）。因此跨视口边缘的线会保留
  可见部分，而不是整段消失。

### 1.3 屏幕空间避让：没有通用碰撞系统

- `src/` 中没有通用的标签避让实现：`collision` 的命中只在快捷键对话框；
  `LabelMgr::draw` 把所有标签逐个画完（`LabelMgr.cpp:504-511`）；
  `StelPainter::drawText` 只做投影和直立处理，没有包围盒测试
  （`StelPainter.cpp:807-818`）。`StelObjectMgr` 也没有 `drawPointer`/标签避让，
  只有 `extraInfoStrings` 注释提醒插件自行避免屏幕碰撞
  （`StelObjectMgr.hpp:253-258`），不作用于星图标签。
- Stellarium 的实际减量手段是“层开关 + 星等阈值 + 单一名称风格”：
  - 恒星名：`maxMag = (limitMagnitude-6.5)*0.7 + labelsAmount*1.2 - 2`
    （`StarMgr.cpp:1420-1428`），逐星绘制时再比一次
    （`ZoneArray.cpp:562-567`）；`labelsAmount` 默认 3（`StarMgr.cpp:398`）。
  - 深空名：`maxMagLabels = limitMagnitude - 2 + labelsAmount*1.2 - 2`
    （`NebulaMgr.cpp:249`、`:870`），`Nebula::drawLabel` 只查
    `projectCheck` 和星等（`Nebula.cpp:1145-1149`）。
  - 行星名：`SolarSystem.cpp:2010-2020` 给整层一个星等预算；
    `Planet.cpp:3917-3927` 用 `ang_dist>0.25` 防止卫星名压住行星名，
    这是唯一的近邻规避，且不是通用屏幕碰撞。
  - 名称风格：`getScreenLabelStyle()` 一次只取 native / pronounce / translated /
    modern 中的一种（`Constellation.cpp:339-342`；
    `StelSkyCultureMgr.cpp:1317-1335`），从不把“中文名 + 拉丁名”拼在一起。
- 结论：Stellarium 在宽 FOV 下靠用户手动关层；本仓的
  `label_boxes_keep` 反而更进一步。

### 1.4 中国 sky culture 的实际组织

- `skycultures/chinese/index.json`：`"constellations"` 从 `:6` 开始，共 **312**
  项，全部都有 `common_name` 和 `lines`；没有 `"asterisms"` 键；
  `"edges_type":"none"` 在 `:1568`；`"common_names"` 在 `:1605`，共 3072 条
  星名。打开星座名后，`ConstellationMgr` 会尝试画 **312 个名字**，没有独立的
  “星官名”开关。
- `skycultures/modern_chinese/index.json` 才是分组的：`"asterisms"` 在 `:6`
  （327 项），`"constellations"` 在 `:1643`（88 项），`edges_type: iau`。
  分组带来 `AsterismMgr` 的独立线/名开关（`flag_asterism_name` 等，
  `AsterismMgr.cpp:77-79`）。
- 本仓是第三种组织：31 区域 / 287 星官。当前天空视图只为 31 个区域排标签
  （`desktop/rf_sky_view.py:5276-5279`、`:5381-5393`），287 个星官只贡献细线，
  不贡献名字。因此“287 个中文星官名同时压屏”在本仓渲染路径上并不存在；
  若看到类似现象，来源应是星名/DSO/行星标签，而不是星官名。

## 2. 与本仓实现的关键差异

| 维度 | Stellarium | 本仓 |
| --- | --- | --- |
| 标签选择 | 整层全画，只做视口裁剪 | `LABEL_TIER_CAPS=(5,20,24,12,20)` 每档预限流（`rf_sky_view.py:774`、`:791-810`） |
| 预限流排序 | 无 | 按到屏幕中心距离保留最近 N 条；在 150° 球极投影下中心角距压缩，导致名字向中心堆 |
| 碰撞剔除 | 无 | 贪心 AABB `label_boxes_keep`（`:813-832`），比 Stellarium 强 |
| 标签锚点 | 成员星重心 + 手工 offset | 最亮星（`xingguan.py:403-416`；IAU 的 `anchor()` `constellations.py:450-456`），再偏移 `+8,-8`（`rf_sky_view.py:5391-5393`） |
| 标签文本 | 单一风格，短 | IAU 恒为 `中文名 拉丁名`（`constellations.py:448`），双系统再加 `[IAU]` / `[中国星官]`（`:548-553`、`xingguan.py:361-364`），框更宽、更易撞 |
| FOV 分层 | 名字无 FOV 限制（只有望远镜星官 >60° 不画） | 座名 45°→65° 渐显；细节线 55°→30° 渐显；150° 时区域线全显、细节线为 0（`rf_sky_view.py:5161-5189`） |
| 连线几何 | 大圆细分 + 视口 cap 裁剪，保留部分段 | 直接 `lineTo`；任一端 `None`（`alt<-6°` 或非有限）整段丢弃（`:5191-5206`、`:5296-5307`） |

补充：`label_tier_keep` 最终 `return sorted(keep)`（`:809-810`），而
`label_boxes_keep` 对同优先级按输入下标排序（`:821`），所以“近处优先”只作用于
预限流，不作用于碰撞阶段；同档内实际是目录顺序先占位。

## 3. 可执行修正建议（按收益/改动比排序）

1. **P0 宽 FOV 空间分桶**：在 `label_tier_keep` 内把屏幕划成 4×3（或 6×4）网格，
   每档按桶轮转填充 cap，桶内再按“到桶中心/视场中心距离”排序；仅当
   `fov >= CONSTEL_LABEL_FOV[1]`（65°）启用，窄 FOV 保持现有中心优先。
   同时让返回顺序为距离序，使同优先级碰撞时近者先占位。
   同步更新 `tests/test_sky_label_collision.py:226-238`。
   预期：cap 不变，名字从中心摊到全屏，边缘不再空。
2. **P1 宽 FOV 缩短文本**：拉远时 IAU 只画中文名，不拼拉丁名；双系统前缀
   `[IAU]` / `[中国星官]` 改为颜色/图例区分，或仅在 `fov < 45°` 时显示。
   直接减少 30–50% 包围盒宽度，等价于在同样 cap 下多放下若干名字。
   依据：Stellarium 的 `getScreenLabelStyle()` 一次只取一种风格。
3. **P2 锚点改重心**：中国区域与 IAU 星座都用成员星单位向量归一化均值作锚点，
   模长过小时回退最亮星；保留现有 `+8,-8` 屏幕偏移。这样名字落在图形内部，
   不再和“锚点亮星”的星名抢同一块像素。改动点：`xingguan.py:403`、
   `constellations.py:450` 及对应回归断言。
4. **P3 连线按大圆细分并裁剪**：对夹角 >2° 的段做 3–4 点 slerp 插值再投影；
   端点 `alt<-6°` 时插值求交点并在 -6° 截断；视口外端点不要整段丢弃，而是
   保留可见子段。当前最大段约 14°，150° 球极投影下弦高约 3 px，折线感有限；
   收益更大的是“跨地平/跨窗口整段消失”问题。
5. **P4 宽 FOV 收紧档位**：`LABEL_TIER_CAPS[2]` 在 `fov >= 65°` 时从 24 降到
   12–16；若将来把 287 个星官名也画出来，必须加 Stellarium 同款
   “>60° 不画细名”门槛（`Asterism.cpp:420`）。150° 下只保留 31 区域名。
6. **P5 用户密度档**：把 caps 乘一个 0–2 的“标签密度”设置（或直接调星名极限
   星等），持久化。对应 Stellarium 的 `stars/labels_amount` 与
   `astro/nebula_labels_amount`（默认 3）。这是最后的逃生口，优先级最低。

不要照搬 Stellarium “全画 + 不避让”的策略；本仓的 AABB 剔除更适合仪器界面。

## 3.1 已落地（2026-11-17，P0）

- `desktop/rf_sky_view.py`：新增 `LABEL_SPREAD_GRID=(4,3)`、
  `LABEL_SPREAD_MIN_FOV_DEG=65.0`、`_spread_pick()`；
  `label_tier_keep(..., bounds=, spread=, spread_tiers=(2,))`。
- `_flush_labels()` 按 `view_state.fov_deg >= 65°` 打开分桶，只作用于 P2
  （星座/星官名）——宽 FOV 下唯一会吃满 24 条名额的档。
- 反证用例：`tests/test_sky_label_collision.py::test_wide_fov_spreads_tier_cap_across_screen`
  （40 条挤在中央 + 4 条在四角：旧口径四角全丢，分桶后四角必留）。
- P1/P2/P3/P4/P5 未做，仍按收益排序保留在下面第 3 节。

## 4. 许可注意
- 参考目录只读。本文只记录机制和 file:line，不复制 GPL-2.0 代码、注释或
  sky culture 数据；实现时按本仓既有 clean-room 方式独立编写。
- Stellarium 代码为 GPL-2.0-or-later；`skycultures/*/index.json` 还可能带作者
  单独署名/许可，复制前须逐项核对。不要把 `chinese/index.json` 的 312 条
  星座记录或 3072 条星名导入本仓。
- 本仓 IAU 连线数据是 CC BY-SA 4.0（`NOTICE.md:76-86`）；中国星官数据另有
  来源，改动锚点/名称前先确认其许可与归属，不要用 Stellarium 数据“补全”。
