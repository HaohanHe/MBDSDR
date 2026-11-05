# MBDSDR Web 产品 · 一键复现验收报告

- 生成时间：2026-11-06T01:52:38
- 事实源：D:\mbdsdr\MBDSDR（只读，未修改）
- 单文件产物：`dist/index.html` 1033265 bytes · sha256 `b2339fc9585bd71c828b4b5d62819ec15e13691d5f711db46f3bb13ffcb68939`
- 工具域 14 个 · 工具→域映射 82 条 · 写工具模式 82 条
- ControlHub 端口 `50732` · 时间倍率 5 档 · 中性站点 0.0, 0.0（trusted=False）
- **全天星表 HYG v4.1**：25,792 颗（V ≤ 7.5），J2000，CC BY-SA 4.0 · sha256 `eb086d463456ba8c…`
- 天球：球心相机 → 视图空间 → 全屏平面投影（≤88° 针孔透视 / 以上立体投影鱼眼） / 窗口矩形裁剪 / 地景绕 360° 方位 / 星位固定 J2000 / 缩放改 FOV / LST 刚性旋转
- 文档 37 篇 · i18n 三语 · 视觉截图 12 张

## 步骤结果

| 步骤 | 结果 | 末行输出 |
|---|---|---|
| 仓库事实源同步 | PASS | `synced: 14 domains, 82 tool->domain entries, 82 write patterns, 5 time rates, ControlHub :50732, themes from desktop/tokens.py · THEME_COLORS['figma'] / RADIUS / FONT / TEXT_ALPHA ` |
| 全天星表同步 | PASS | `synced: HYG 25,792 stars (mag<=7.5), named 141, 27 constellations, 117 DSO` |
| 卫星轨迹同步 | PASS | `[sat-tracks] 6 sats × 481 samples @ 90s, TLE 2026-09-25, sgp4 2.27, js 32953 bytes` |
| 骨架与样式 | PASS | `style.css bytes: 24168 / index.html bytes: 9501 / scripts: 00-core.js, 05-repo-facts.js, 10-data.js, 12-sky-catalog.js, 13-sat-tracks.js, 20-i18n.js, 31-celestial.js, 30-sky.js, 40` |
| 文档/wiki 生成 | PASS | `generated: 14 domain pages + 3 planned pages, 3 scenarios, glossary, faq, wiki index, README.en/ja` |
| 单文件构建 | PASS | `scripts: 00-core.js, 05-repo-facts.js, 10-data.js, 12-sky-catalog.js, 13-sat-tracks.js, 20-i18n.js, 31-celestial.js, 30-sky.js, 40-workbench.js, 50-dialog.js, 70-docs-data.js, 70-d` |
| 静态守卫 | PASS | `WARN [i18n] zh 反查冲突：lang.current / lang.zhLabel（后者胜出，需人工确认译文可接受） / WARN [i18n] zh 反查冲突：cap.stack.name / pro.stack（后者胜出，需人工确认译文可接受） / WARN [i18n] zh 反查冲突：sidebar.docs / docs.openTit` |
| 性能基准 | PASS | `{"stars":25792,"starProjection":[{"fov":22,"ms":1.12,"stars":195},{"fov":60,"ms":2.228,"stars":461},{"fov":100,"ms":2.055,"stars":704},{"fov":158,"ms":2.012,"stars":678}],"landscap` |
| 天球投影自检 | PASS | `horizon straight under perspective OK (spread 0.00 px over ±30° az) / horizon curved under stereographic OK (spread 28.9 px) / corners unprojectable at 158°: 4/4 / ALL PROJECTION C` |
| 无头运行时 | PASS | `RUNTIME {"modules":13,"docs":35,"locale":"zh-CN","caps":17,"i18nKeys":270,"textNodes":84,"framesRendered":37,"edgeUnproject":8558} / RESULT: RUNTIME-OK ✅` |
| 视觉夹具 | PASS | `fixture: D:\mbdsdr\MBDSDR\web\product\tests\visual_check.html 937510 bytes / standalone fixtures: visual_{sky,pro,ja,en,docs,ai,caps}.html / fixture inject script: node --check OK` |

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
- **卫星目录为离线演示条目**：TLE 与下行频率取自 `sat_tracker.py`；合成/凑整 TLE 不用于精确过境，站址未校准时不给可信过境结论。
- **规划能力位**：6DOF 视觉引导 / 视觉校准 / VR·XR 在原仓库为设计项，UI 中标注「规划 · 未验证」且禁用。
- **星表数据是真的**（HYG v4.1 下载 + SHA256），但坐标链为 J2000 直算 LST，未接 `celestial_geometry.py` 的完整岁差/章动/自行链。
- **地景是示意轮廓**：仓库与产品都没有真实 360° 全景数据，地平高度场是确定性示意曲线（非实测地形、非照片），UI 已明写「示意轮廓」，不冒充实拍。
- **太阳位置是低精度解析式**（Meeus 简化，约 ±0.5°）：只用于昼夜/晨昏蒙影判定，不用于日出时刻表或日食计算。
- **FOV→极限星等**是自研视觉适应显示门限（设计项，未验证），非望远镜口径增益，也不代表该时刻肉眼可见下限。
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
