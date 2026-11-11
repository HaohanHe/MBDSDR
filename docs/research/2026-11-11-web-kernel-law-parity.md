# Web / 内核「不同律」审计与裁定（2026-11-11）

> 范围：`web/product/src`（浏览器呈现层）与 `mbdsdr_ai`（科学内核）之间的数值律差异。
> 本文是维护依据：**物理量必须同律；观感函数允许分层，但必须在此登记**。
> 关联：`docs/research/2026-11-11-gap-sky.md`（待补链接）。

## 1. 扫描口径与总量

扫描对象为 `web/product/src/*.js`（仓库内全部前端源文件，6 个文件命中）。

| 形态 | 实测处数 | 说明 |
|---|---:|---|
| `Math.trunc` | 2 | 均在 `14-astro-frame.js`（Bortle 向零取整，镜像内核 `int(bortle)`） |
| `parseInt` | 0 | 前端不使用 `parseInt` |
| `Math.max` / `Math.min` | 40（6 文件） | 全量匹配；若只计 `Math.max(0, …)` / `Math.min(…, x)` 这类**数值钳制**形态为 26 处 |

40 处的分布与性质：

- **参与数值律**：`14-astro-frame.js`（Bortle 取整、晨昏/背景亮度夹取、矩阵分量钳制）、`31-celestial.js`（`clamp(vz,-1,1)`、消光分母保护、星等门）。这些已逐条对内核。
- **不参与数值律**：`30-sky.js`（地景逐列像素、画布尺寸、缓存键、卫星线宽）、`40-workbench.js`（瀑布图归一化与布局）、`00-core.js`（`clamp` 工具本身）、`60-app.js`（DOM 尺寸）。它们只影响像素、尺寸或缓存键，不产生物理量。
- 单位口径：web 统一度（`D2R` 就地换算），内核注释内统一度、矩阵内部弧度；dBFS 与 MS/s、Hz 的换算只在显示层出现，无跨层复用。

## 2. 逐条表

| # | web 位置 | 内核位置 | 差异类型 | 最坏偏差（输入 → 两边值） | golden 覆盖 | 判定 |
|---|---|---|---|---|---|---|
| 1 | `30-sky.js:324`（旧 `clamp(sunAlt/12,0,1)`） | `atmosphere.py:200,210` `_SKY_BRIGHT_BREAKS` / `sky_brightness_factor` | 不同律（昼夜判错） | 太阳 alt=−1° → web 旧 0.0000 / 内核 0.8883 | 晨昏 golden 272 点 + 三点锚点（−1/−6/0，容差 1e-6）+ 变异反证 | **已改**（`5872c67c`） |
| 2 | `31-celestial.js:455`（旧 `Math.sin(-1.2*D2R)`） | `star_catalog.py:122` `_HORIZON_GATE_DEG=-0.6` | 不同律（地平门差 0.6°） | 同表同时刻：alt∈(−1.2°,−0.6°] 全表 103 颗；L=7.5 时旧门多画 4 颗（7327→7331） | `build/star-visibility-golden.json` + `tests/star_visibility_selftest.js` | **已改**（`33b8de5e`） |
| 3 | `31-celestial.js` 命名星/DSO（旧 `v.z > -0.02` / `< -0.02`，≈−1.15°） | `star_catalog.py:122` | 不同律（同 2 的环带） | 与 #2 同一环带；旧门在命名星与 DSO 各多画一条 0.6° 边 | 同上（守卫禁 `v.z ? -0.02`） | **已改**（`33b8de5e`） |
| 4 | `14-astro-frame.js` 极限星等 | `stars.py:165` `naked_eye_limit_mag` | 不同律（截断 vs 线性） | Bortle 1/3 → 旧 web 都是 8921 颗；去截断后 Bortle 1 = 19116 > Bortle 3 = 8921 | `build/limit-mag-golden.json` + 六组跨语言 + 画面级反证 | **已改**（web `805e59f7`，内核 `8b09b8fa`） |
| 5 | `31-celestial.js` `starStyle` | `stars.py:197` `star_draw` | **有意分层**（观感） | `star_draw(6.0,3)=(1.035,0.0401)`、`(5.0,3)=(1.643,0.1875)`；其 α≈0.04 在 30° fov 下几乎不可见 | 无逐像素 golden（设计如此）；改由 #2 的**集合相等**断言兜底 | **不镜像**，已登记（`33b8de5e`） |
| 6 | `40-workbench.js:24-31` min-max 归一化 | 无对应内核渲染律（内核只产绝对 dBFS） | 显示约定（非物理量） | 真实帧整帧搬到峰值 −50 dBFS → 显示逐点相同（max diff 1.7e-16）；恒定 −50 dBFS → 全线 0.08；恒定 +1e-3 dB 噪声 → 被放大 840× 铺满 0.08–0.92 | 无 | **待产品决定**：只显示相对形状，绝不代表绝对电平 |
| 7 | `31-celestial.js` `dateToHorizon`（太阳 of-date） | `atmosphere.py` `sun_altitude_deg`（Meeus of-date） | 框架处理（不是错） | 2026-11-11T22:00Z @(0,0)：内核 −58.934701° / web −58.933447°，差 **0.001253°**；若误把 of-date 坐标再过岁差 → −58.572654°，差 0.3608° | astro/twilight golden 已钉矩阵 | **无实质差异**；太阳必须留在 of-date 链 |
| 8 | `30-sky.js` `satVector`（地心视向） | `sat_passes`（站心） | 缺站址视差（已声明） | ISS 2026-11-09T16:45Z @(0,0)：地心 alt 84.288° vs 站心 29.273°，差 **55.014°**（range 774.9 km）；TCA 差 2.230°（413.5 km）。方位差 0（站址位移沿本地垂线，视差只改高度） | 无；HUD `not_verified` + 不给升落/过境 | **不判错，但不得据此指向**（已有诚实标注） |
| 9 | `05-repo-facts.js:634` `alt: 0.0` | `station.py` `alt_m`（米） | 单位未标注 | 当前值 0.0，数值无差异；若站高 500 m 被读成 0.5 km 才会显现 | 无 | **待标注**（不是不同律） |
| 10 | `31-celestial.js:396` `if (sinAlt >= 1) return 0` | 无对应门 | 防御性早退 | alt≥90° 走不到；仅防浮点把 sinAlt 抬到 1.0000000002 | 无 | **保留**，已写注释（`33b8de5e`） |

## 3. 裁定（后续维护依据）

1. **物理量必须同律**。已改：昼夜背景因子（#1）、两处地平门（#2/#3）。判定标准是 golden 以内核为真值，web 不得多画或少画。
2. **观感函数允许分层，但必须登记**。星点半径/亮度/色偏（#5）不逐像素镜像内核 `star_draw`；为了让 web 在 30° fov 下可读，这是有意为之，不是漏改。
3. **天顶早退保留**（#10）：`alt≥90°` 在球面坐标里走不到，早退只为浮点防御，勿删。
4. **强断言**：同一星表、同一时刻、同一 fov/L，两边「画/不画」的集合必须**完全相等**。
   - 入口：`build/gen_star_visibility_golden.py` 直接 import 内核 `_HORIZON_GATE_DEG` / `extinction_delta_mag_array` / `limit_magnitude`，不自证；
   - 断言：`tests/star_visibility_selftest.js` 调用 `31-celestial.visibleSet`（与 `eachStar` 同一判据），逐元素比对；
   - 反证：把地平门改回 −1.2°，集合恰好多出 4 颗，自检必红。
   - 投影/屏幕矩形裁剪属于呈现层，允许 web 独有，不在该断言内。
5. **太阳留在 of-date 链**（#7）。`dateToHorizon` 只做 LST 旋转；把 of-date 坐标再过岁差会产生 0.36° 系统误差。

## 4. 待实测三条：当前状态（2026-11-11 实测，非代理报）

| 条目 | 实测输入 | 结果 | 结论 |
|---|---|---|---|
| 太阳 of-date vs J2000 | 2026-11-11T22:00Z，站 (0,0) | 内核 −58.934701°，web of-date −58.933447°，差 0.001253°；错误岁差路径 −58.572654°（差 0.3608°） | **无实质差异**（远小于 0.01°）；只有走错框架才会差 0.36° |
| ISS 地心 vs 站心视差 | 轨道窗口 2026-11-09T16:00–04:00Z，站 (0,0)，SGP4 | TCA 16:43:30Z：地心 89.855° / 站心 87.625°（Δ2.230°，413.5 km）；16:45Z：84.288° / 29.273°（**Δ55.014°**，774.9 km）；方位差 0 | 低仰角视差可达数十度，**地心视向绝不能用于指向/过境**；现有 `not_verified` 口径正确 |
| `40-workbench` 归一化 vs 绝对 dBFS | 真实采集帧 `aprs_144640.wav`（4096 点 FFT，Hann） | 原始 −33.44…+43.84 dB（mean 14.71）；整帧搬到峰值 −50 dBFS 后显示与原帧逐点相同（max diff 1.7e-16）；恒定 −50 dBFS → 全 0.08；+1e-3 dB 噪声 → 放大 840× | **整体 −50 dB 时 web 显示完全不变**；该图只表达相对形状，不能读绝对功率 |

补充两条（同批核实）：

- `05-repo-facts.js:634` 的 `alt` 来自内核 `alt_m`（米），当前值 0.0，数值无差异；字段名/单位需在 fact 层标注（待办）。
- DSO 的 `L + 1.5` 门与内核 `astronomy/dso.py:522 recommend_dso` **不是同一用途**：前者是渲染深度余量，后者是观测推荐（`max_magnitude=9.0`、`min_alt_deg=20`、太阳距角 90°）。此前把两者配对不成立；正确判定是「不同用途，不构成不同律」，DSO 的地平/消光部分已随 #2/#3 与内核同口径。

## 5. 复现命令

```bash
python web/product/build/gen_limit_mag_golden.py
python web/product/build/gen_twilight_golden.py
python web/product/build/gen_star_visibility_golden.py
node web/product/tests/limit_mag_selftest.js
node web/product/tests/twilight_selftest.js
node web/product/tests/star_visibility_selftest.js
python web/product/tests/verify_product.py
```

最近提交：`5872c67c`（昼夜背景因子）、`33b8de5e`（地平门 −0.6° + 星点集合相等断言）。
