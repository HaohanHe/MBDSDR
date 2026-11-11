# MBDSDR Web 产品 · 一键复现验收报告

- 生成时间：2026-11-11T21:52:20
- 事实源：D:\mbdsdr\MBDSDR（只读，未修改）
- 单文件产物：`dist/index.html` 1251781 bytes · sha256 `21b5e63ff83dba6feb4ad56a09f85993dffa134c1f5d4504c7bc76980d31a2f8`
- 工具域 14 个 · 工具→域映射 83 条 · 写工具模式 82 条
- ControlHub 端口 `50732` · 时间倍率 5 档 · 中性站点 0.0, 0.0（trusted=False）
- **全天星表 HYG v4.1**：25,792 颗（V ≤ 7.5），J2000，CC BY-SA 4.0 · sha256 `eb086d463456ba8c…`
- 天球：球心相机 → 视图空间 → 全屏平面投影（≤88° 针孔透视 / 以上立体投影鱼眼） / 窗口矩形裁剪 / 地景绕 360° 方位 / 星位 J2000 + HYG 实测自行推进到当前历元 / 缩放改 FOV / LST 刚性旋转
- 文档 37 篇 · i18n 三语 · 视觉截图 12 张

## 步骤结果

| 步骤 | 结果 | 末行输出 |
|---|---|---|
| 仓库事实源同步 | PASS | `synced: 14 domains, 83 tool->domain entries, 82 write patterns, 5 time rates, ControlHub :50732, themes from desktop/tokens.py · THEME_COLORS['figma'] / RADIUS / FONT / TEXT_ALPHA ` |
| 全天星表同步 | PASS | `synced: HYG 25,792 stars (mag<=7.5), named 141, 27 constellations, 117 DSO` |
| 卫星轨迹同步 | PASS | `[sat-tracks] 6 sats x 481 samples @ 90s, TLE 2026-09-25, sgp4 2.27, frame J2000 ICRS geocentric RA/Dec (degrees, x100 integer encoded), js 33258 bytes` |
| 天文框架金标准 | PASS | `[astro-golden] 24 cases -> D:\mbdsdr\MBDSDR\web\product\build\astro-golden.json (127780 bytes), from repo MBDSDR` |
| 恒星自行金标准 | PASS | `[pm-golden] 53 stars × 4 epochs = 212 cases -> proper-motion-golden.json (73819 bytes) / [pm-golden] worst residual 0.3465" (Lalande 21185) vs tolerance 2.0"; skyfield 1.54` |
| 极限星等金标准 | PASS | `[limit-mag-golden] 19 fov × 15 bortle = 285 cases -> limit-mag-golden.json (43696 bytes) / [limit-mag-golden] kernel depth 9.0 / web depth 7.5; naked_eye(3)=6.5, naked_eye(9)=4.4, ` |
| 晨昏亮度金标准 | PASS | `[twilight-golden] 272 sun-alt components + 147 compositions = 419 cases -> twilight-golden.json (61215 bytes) / [twilight-golden] night 21.6; brightness(0)=8.0, brightness(-6)=15.0` |
| 骨架与样式 | PASS | `style.css bytes: 24517 / index.html bytes: 9547 / scripts: 00-core.js, 05-repo-facts.js, 10-data.js, 12-sky-catalog.js, 13-sat-tracks.js, 14-astro-frame.js, 20-i18n.js, 31-celestia` |
| 文档/wiki 生成 | PASS | `generated: 14 domain pages + 3 planned pages, 3 scenarios, glossary, faq, wiki index, README.en/ja` |
| 单文件构建 | PASS | `scripts: 00-core.js, 05-repo-facts.js, 10-data.js, 12-sky-catalog.js, 13-sat-tracks.js, 14-astro-frame.js, 20-i18n.js, 31-celestial.js, 30-sky.js, 40-workbench.js, 50-dialog.js, 70` |
| 静态守卫 | PASS | `WARN [i18n] zh 反查冲突：lang.current / lang.zhLabel（后者胜出，需人工确认译文可接受） / WARN [i18n] zh 反查冲突：cap.stack.name / pro.stack（后者胜出，需人工确认译文可接受） / WARN [i18n] zh 反查冲突：sidebar.docs / docs.openTit` |
| 性能基准 | PASS | `{"stars":25792,"starProjection":[{"fov":22,"ms":0.798,"stars":87},{"fov":60,"ms":1.88,"stars":416},{"fov":100,"ms":2.773,"stars":1100},{"fov":158,"ms":3.9,"stars":1625}],"landscape` |
| 天文框架跨语言自检 | PASS | `astro-frame cross-language OK · 24 cases / 144 vectors / worst 3.89e-16 (horizon 2461000/40.7 ra=200[2])` |
| 恒星自行跨语言自检 | PASS | `proper-motion cross-language OK · 53 stars / 212 samples / linear pin worst 0.00e+0° () / skyfield worst 0.3466" (Lalande 21185 jd=2470000) ≤ 2"` |
| 极限星等跨语言自检 | PASS | `  Bortle 9: JS nakedEye=4.40 == Python 内核 4.40 /   变异反证：把 max(0,…) 加回 → 对拍变红（bortle=0: 6.5 vs 内核 7.55 err=1.0499999999999998） /   画面级：HYG 25792 颗 / fov=60 / cap=7.5 · 改前 Bortle1=89` |
| 晨昏亮度跨语言自检 | PASS | `twilight cross-language OK · 272 components + 147 compositions / component worst 0.00e+0 mag () / composition worst 0.00e+0 mag () ≤ 1e-12` |
| 天球投影自检 | PASS | `horizon straight under perspective OK (spread 0.00 px over ±30° az) / horizon curved under stereographic OK (spread 28.9 px) / corners unprojectable at 158°: 4/4 / ALL PROJECTION C` |
| 无头运行时 | PASS | `RUNTIME {"modules":14,"docs":35,"locale":"zh-CN","caps":17,"i18nKeys":272,"textNodes":84,"framesRendered":37,"edgeUnproject":8558} / RESULT: RUNTIME-OK ✅` |
| 视觉夹具 | PASS | `fixture: D:\mbdsdr\MBDSDR\web\product\tests\visual_check.html 1144193 bytes / standalone fixtures: visual_{sky,pro,ja,en,docs,ai,caps}.html / fixture inject script: node --check OK` |

## 视觉态

- `verify_sky.png`
- `verify_skywide.png`
- `verify_skyzoom.png`
- `verify_skyback.png`
- `verify_skytop.png`
- `verify_skyground.png`
- `verify_pro.png`
- `verify_ja.png`
- `verify_docs.png`
- `verify_ai.png`
- `verify_caps.png`
- `verify_caps2.png`

## not_verified（明确未验证，勿当作已上线）

- **真实硬件链路未接通**：本产品不直接读写 SDR；`ControlHub`（C++ 宿主写开关 `writeEnabled_`）与 Python 内核的一次性 `confirm_token` 门只按原仓库代码常量建模为契约，未连接真实 C++ headless / Python 内核，也未接入可签发 token 的外部确认通道。
- **阶段 B 宿主注入未接通**：`mbdsdr_ai/web_bridge.py` 已把 `ToolRegistry` 映射成 `window.mbdsdrBridge` 形状并做 16 条单测对拍，但 QWebChannel / JS 全局注入、 `confirm_channel` 接到 `mcp_server.call_tool` 均未实现；写工具恒返回 `gated`。
- **频谱与瀑布无 IQ 输入**：专业模式显示诚实空态，未展示任何实时或合成谱线。
- **卫星轨迹是真实 SGP4 传播**：TLE 取自 `sat_tracker.py::_BUILTIN_TLES`（静态快照），经仓库 IAU2006 逆岁差转 J2000；地心方向未做站址视差，站址未校准时不给可信过境结论。
- **规划能力位**：6DOF 视觉引导 / 视觉校准 / VR·XR 在原仓库为设计项，UI 中标注「规划 · 未验证」且禁用。
- **星表数据是真的**（HYG v4.1 下载 + SHA256）；坐标链已接仓库 `celestial_geometry.py` 的 IAU2006 岁差 + IAU2000B 简化章动 + Capitaine GMST，由 `build/gen_astro_golden.py` 与 `tests/astro_selftest.js` 跨语言对拍（3.3e-16）。**恒星自行已接 HYG 实测 pmra/pmdec**（线性 RA/Dec 近似推进到当前历元），由 `build/gen_proper_motion_golden.py`（skyfield 严格空间运动，独立无星历基准）与 `tests/proper_motion_selftest.js` 对拍：JS↔Python 线性紧钉扎 1e-9°，对 skyfield 残差实测 worst 记录于金标准 meta（显示级 ≤2″，亚像素）；线性近似忽略透视加速度/视差长期项，章动非完整 77 项，忽略 ΔT/大气折射/站址视差。
- **地景是示意轮廓**：仓库与产品都没有真实 360° 全景数据，地平高度场是确定性示意曲线（非实测地形、非照片），UI 已明写「示意轮廓」，不冒充实拍。
- **太阳位置是低精度解析式**（Meeus 简化，约 ±0.5°）：只用于昼夜/晨昏蒙影判定，不用于日出时刻表或日食计算。
- **极限星等已接内核事实源**：Bortle 裸眼基线（`skyengine/stars.py::naked_eye_limit_mag`，6.5-0.35·(b-3)，不夹 Bortle<3，Stellarium StelSkyDrawer 同语义）+ FOV 渲染 LOD 增益（`star_catalog.py::detail_gain_mag`，clamp(0.5·log2(30/fov),0,1.5)，广角恒 0、永不为负），由 `build/gen_limit_mag_golden.py`（直接 import 内核真值）与 `tests/limit_mag_selftest.js` 跨语言紧钉扎（分量/合成 1e-12 等）；前端以随仓 HYG 截断 MAG_CAP=7.5 作 depthCap。**仍未做**：完整天空亮度→极限星等光度学模型（月相/实际标定天光表面亮度，内核 limitations L24；晨昏连续压暗已接内核 twilight 事实源，见下条）；入场暗适应淡入是 Web 呈现层。LOD 增益非望远镜口径增益，也不代表该时刻肉眼可见下限。
- **晨昏连续压暗已接内核事实源**：可见极限 = 内核 limit_magnitude（Bortle 基线 + FOV LOD）减晨昏惩罚（`skyengine/twilight.py::twilight_limit_penalty_mag` = max(0, 21.6 − 天顶亮度)，天顶亮度是分段线性太阳高度表），与桌面 `rf_sky_view.py:4246` 同式 limit = base − penalty(sun_alt)。由 `build/gen_twilight_golden.py`（直接 import 内核真值）与 `tests/twilight_selftest.js` 跨语言紧钉扎（分量/合成 1e-12 等）+ 镜像内核 `TestTwilightPenalty` 不变量（天文夜 ≤−18° 惩罚恒 0、None/NaN→夜、单调、民用 −6°→裸眼极限 ∈[−2,1]、白天→裸眼极限 <−6）。布尔白天门（日落 −0.833° 整层不画）保留，低于该阈值后不再一档到底，由惩罚连续压暗（晨昏各约 1 小时的真实过程；旧版只有布尔门，太阳 −1° 时画出「白蓝天空 + 满屏 6.5 等星」的物理不可能画面）。**仍未做 / 未标定**：惩罚是量级近似文献曲线（Krisciunas & Schaefer 1991 / Patat et al. 2006），非辐射标定（气溶胶/臭氧/纬度/方位约 ±1 等）、天顶等效、无方位依赖、无月光；只用于显示层，不参与指向/定轨/过境结论。入场暗适应淡入（intro 0→1 基线由 2 等放开）是 Web 呈现层，非内核。
- **桌面端未联调**：未与 Qt6 天空模式 `rf_sky_view.py` 做端到端对拍；设计令牌与时间倍率为代码事实源同步，非运行时连线。
- **截图为 headless 静态态**：用于版式验收，不代表真机交互或信号接收结果。
- 许可证：UI 与文档 MIT；机制学习自 Stellarium Web Engine（AGPL-3.0），**未引入其任何代码**。

## 复跑

```
python run_all.py
```
（在本目录执行，需 Python 3.14；Node.js 用于运行时烟测，Edge 仅用于截图）
前置：Python 3.14（含标准库）、Node.js（运行时烟测）、原仓库 `D:\mbdsdr\MBDSDR` 可读；Edge 仅用于截图，缺失时跳过。

任何一步失败即停；报告只记录真实执行结果，不伪造通过。
