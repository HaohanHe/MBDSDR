# UI 并进基线：交接文档定位 / 参考项目清单 / 差距 / 分级工单

> 性质：**只读调研与工单**。本文件不声称任何缺陷已修复；「已修」条目均按四元组格式
> `<结论> — <hash> — <path:line> — <改动后字面量>` 给出证据，未给四元组的即视为「仍成立」。
> 调研方法：静态读码 + `git log -S` + 文档互证；**本轮未启动 GUI、未占用真机设备、未修改产品代码**。
> 调研时工作树存在另一条会话未提交的 UI 改动（`adsb_panel / bookmark_panel / control_panel /
> device_panel / rf_sky_view / scanner_panel / status_panel`），任何实施前必须先 claim。

---

## §1 交接文档定位

### §1.1 `docs/pending-patches/` 清单（22 份，UI 相关已标记）

| # | 文件 | 一行主题 | 落库状态（实测） | UI |
|---|---|---|---|---|
| 1 | `2026-11-06-handoff-main-window-audit-p0p1.md` | **主交接单**：`main_window.py` 六处真软件缺陷 | 部分（详见 §1.3） | 是 |
| 2 | `2026-11-06-ux-basis-main-window.md` | UX 待落库：回放后台读盘/假调谐回滚/TLE 后台 | 主体已落；P2-C 已补 | 是 |
| 3 | `2026-11-06-handoff-p1-replay-main-window.md` | 回放读盘移后台 | 已落（`f47927b0`） | 否 |
| 4 | `2026-11-06-handoff-p2c-control-panel-gain.md` | 未连接增益显示「—」 | 已落：`desktop/control_panel.py:1462` 存在 `def set_gain_applied` | 是 |
| 5 | `2026-11-06-desktop-ppm-offset-bandwidth-wiring.md` | PPM/Offset/带宽失败回填 | 待落 | 是 |
| 6 | `2026-11-06-desktop-satpanel-timer-after-delete.md` | 面板销毁后定时器仍回调 | 待落 | 是 |
| 7 | `2026-11-06-desktop-sweep-tuning-refusal.md` | 调谐被拒不得产「无电台」伪结论 | 待落 | 是 |
| 8 | `2026-11-06-desktop-adsb-1090-truthfulness.md` | 1090 请求失败说人话 | 待落 | 是 |
| 9 | `2026-11-06-preset-combo-min-width-stability.md` | 右栏 278↔356 两态宽度 | 已落（`b0a440e3`） | 是 |
| 10 | `2026-11-06-record-pre-message-alignment.md` | 录音前置文案 A+B | 已实施 | 是 |
| 11 | `2026-11-06-restore-capability-hints.md` | 配置恢复项「被替换必须可见」 | 已提交（`abc52bd8` / `90f76caa`） | 是 |
| 12 | `2026-11-06-limitation-adsb-1090-fc0012.md` | 本机 FC0012 收不到 1090 的限制登记 | 文档类，已登记 | 是 |
| 13 | `2026-11-06-baseline-red-owners.md` | `test_ui_wiring_audit` 4 条基线红台账 | 已结案（`86fdbd0b`） | 是 |
| 14 | `2026-11-06-audit-ft8-get-colorder-stale.md` | 审计文档过期记述 | 文档更正 | 否 |
| 15 | `2026-11-06-coordination-section61-claim-record.md` | §6.1 claim 记录草稿 | 未见落库 | 否 |
| 16 | `2026-11-06-deadcode-unused-symbols.md` | 10 个无用公开符号待删 | 待决 | 否 |
| 17 | `2026-11-06-handoff-astro-wiring.md` | 天文框架四处接线 | 未落库 | 否 |
| 18 | `2026-11-06-handoff-mechanism-blob-writeback.md` | 私有 index 事故形态编号 | 机制线 | 否 |
| 19 | `2026-11-06-handoff-protocol-claim-ledger.md` | 文档数值更正 + 改名 | 待落 | 否 |
| 20 | `2026-11-06-limitation-conftest-desktop-settings-guard.md` | conftest 守卫误红 | 已修（`f55ff6d4`） | 否 |
| 21 | `2026-11-06-lrpt-frame-convention-not-verified.md` | LRPT 帧约定未对齐 | not_verified 登记 | 否 |
| 22 | `2026-11-06-sdr-backend-replay-audit-handoff.md` | 后端/回放只读审计 | 交接 | 否 |

### §1.2 「所谓的交接文档」= `2026-11-06-handoff-main-window-audit-p0p1.md`

它交代的是 `desktop/main_window.py` 六条可复现缺陷，每条带锚点与修法，收件方是持有该文件的线：

1. ADS-B 面板永久「等待第一帧」（`_poll_sdr_iq` 从未启动）。
2. `audio_tap` 在 SDR 线程里直接改 Qt 控件（跨线程）。
3. 调谐失败回滚只拉回标签/控制面板，不拉回频谱。
4. 云台死区分支记账传错层（`ctx` vs `{"ctx": ctx}`），误差统计恒为 0，且裸 `except`。
5. `_update_time_info()` 在 GUI 线程做 NTP 网络同步。
6. 每帧额外 `update()`（原 33ms 旁路定时器）使性能档失效。

`docs/COORDINATION.md` §八「UI 多线对接」只有 3 行交接表（约 L3212-3222），不含布局结论；
真正在维护的对照账本是 `docs/design-basis.md`（§2 现状 / §4 十大差距 / §5 留痕硬规则）。

### §1.3 六条交接项实测状态（哪三条未修）

| 条 | 结论 | 证据 |
|---|---|---|
| #1 ADS-B 空态 | **已实质修复**（路径改由 pipeline 发布） | `desktop/main_window.py:8705` 起 IQ 总线发布；定时器 `1153-1155` 仍创建但从不 `start()`，为死代码 |
| #2 跨线程改控件 | **已修复**（面板内部改走 Signal） | `desktop/aprs_panel.py:80`（`packet_ready = Signal(object)`）、`desktop/anr_panel.py:56`（`snr_ready = Signal(float)`）；`audio_tap` 注入点 `main_window.py:8360` 保留 |
| #3 回滚漏频谱 | **未修** | `desktop/main_window.py:7037-7053` `_revert_tune_ui` 只写 `freq_label/status_freq/control_panel`，无 `spectrum.set_center_freq` |
| #4 云台记账 | **未修** | `desktop/main_window.py:12177` 传 `ctx`；`desktop/main_window.py:10569` 读取 `task.get("ctx")` → 恒空；`12182` 裸 `except: pass` |
| #5 NTP 卡 GUI | **未修** | `desktop/main_window.py:12307` GUI 线程同步 `get_time_info(prefer_ntp=True)`；`mbdsdr_ai/new_spacetime.py:380-391` 内含 socket；`12317` 裸 `except: pass` |
| #6 重绘过载 | **部分修复，且出现回归** | 33ms 旁路定时器已撤（`main_window.py:8691-8695` 注释），但每帧仍调 `spectrum._refresh_status_labels()`（`:8697`），而该方法 `desktop/spectrum_widget.py:3126-3127` 无条件 `sync_span_slider()` + `_redraw()`（`_redraw` = `update()`），性能档仍被逐帧 `update()` 旁路 |

**未修三条 = #3 / #4 / #5**（对应 §5 的 P0-1 / P0-2 / P0-3）。

---

## §2 参考项目清单（均打开确认有 UI 代码）

| 路径 | 项目 | UI 技术栈 | 可借鉴 | 许可 |
|---|---|---|---|---|
| `reference/sdrpp` | SDR++ | C++ / Dear ImGui + OpenGL | 瀑布/VFO/band plan；`core/src/gui/widgets/snr_meter.cpp`、`volume_meter.cpp`、`stepped_slider.cpp`、`constellation_diagram.cpp`、`symbol_diagram.cpp`；菜单 `core/src/gui/main_window.cpp:73-79` | GPLv3（项目口径；clone 内未见 LICENSE 文件） |
| `reference/gqrx` | GQrx | C++ / Qt5 Widgets + `.ui` | `src/qtgui/dockfft.ui`、`bookmarkstaglist.cpp`、`bookmarkstablemodel.cpp`、`iq_tool.cpp`、`dxc_spots.cpp`、`dockaudio.ui` | GPLv3 |
| `reference/satdump` | SatDump | C++ / ImGui | `src-ui/imgui/imgui_image_adv.cpp`、`src-interface/tracking_widget.cpp`、`recorder_vfo.cpp` | GPLv3 |
| `reference/gpredict` | Gpredict | C / GTK3 | `src/gtk-sky-glance.c`、`gtk-polar-plot.c`、`gtk-sat-map.c`、`gtk-event-list.c`、`gtk-single-sat.c`、`sat-pref-layout.c` | GPL |
| `reference/stellarium` | Stellarium | C++ / Qt5 + OpenGL | `src/gui/AstroCalcDialog.cpp`、`ObsListDialog.cpp`、`SearchDialog.cpp`、`ShortcutsDialog.cpp`、`LocationDialog.cpp`、`MapWidget.cpp`、`DateTimeDialog.cpp` | GPLv2 |
| `reference/stellarium-web-engine` | 星空 Web 引擎 | C++ → WebAssembly / WebGL | 仅思想 | **AGPL-3.0** |
| `reference/onair` | 全能接收/解码 | C++ Qt Widgets + Rust core | 每解码器独立 UI 的组织法：`app/adsb/adsb_map_ui.cpp`、`adsb_table_ui.cpp`、`app/airband/airport_ui.cpp`、`app/bandplan/bandplan_ui.cpp`、`app/autodet/autodet_ui.cpp` | GPLv3 |
| `reference/glasswork` | 玻璃材质 Web Playground | HTML/JS/CSS | 毛玻璃卡片、边缘折射、i18n（`glass.css`/`lens.js`/`panel.js`） | **MIT（唯一可搬素材）** |
| `reference/noaa-apt` | NOAA APT 解码 | Rust / GTK3 | APT 图像与状态呈现 | GPLv3 |
| `reference/goestools`、`meteor_demod`、`rtl_433`、`xrit-rx` | 解码链 | CLI / TUI | 无 GUI 可借 | — |
| `reference/wsjtx/<commit>` | FT4 官方证据样本 | — | 无 UI 源码 | — |
| `reference/openngc`、`sigmf`、`dvbs2_*` | 数据/协议/DSP | — | 无 UI | — |
| `D:/mbdsdr/hanabi-more-love` | three.js/Vite Web 工程 | Web 3D | 非 SDR | — |
| `D:/mbdsdr/定轨demo` | Python 定轨 + skyplot | matplotlib | 仅轨道可视化思路 | — |
| `MBDSDR/mbdsdr-ui-mockup/index.html`、`output/ui-prototype/index.html`、`output/mbdsdr-product/dist/index.html` | 自有原型 | HTML | 设计基准（design-basis 引用） | 自有 |

### §2.1 硬约束：许可

参考项目除 `glasswork`（MIT）外全部为 GPL 系，`stellarium-web-engine` 为 AGPL-3.0；本仓为 MIT。
**只可学交互、信息架构与视觉思路，不可复制代码/CSS/资源**；唯一可直接取用素材的项目是 `glasswork`。
实施时如需参考具体实现，只能读其行为并用 Qt 重写，不得逐行移植。

---

## §3 差距清单

### §3.1 参考有、我们没有

| 项 | 参考证据 | 我们现状 |
|---|---|---|
| 多星「天空一览」网格 | `gpredict/src/gtk-sky-glance.c` | 只有单星极坐标与过境表（`desktop/rf_sky_view.py`） |
| 过境事件列表/日历 | `gpredict/src/gtk-event-list.c` | `desktop/new_spacetime_panel.py:471-483` 仅两列过境表 |
| 天文计算器（升落/中天/坐标/图表） | `stellarium/src/gui/AstroCalcDialog.cpp` | 只有过境时间线（`desktop/time_machine.py`） |
| 观测计划清单 | `stellarium/src/gui/ObsListDialog.cpp` | 未找到 |
| IQ 校正可视化 | `gqrx/src/qtgui/iq_tool.cpp` | 仅两个开关（`desktop/control_panel.py:693-699`） |
| DX Cluster 消息 | `gqrx/src/qtgui/dxc_spots.cpp` | 未找到 |
| 稳态 SNR / VU 表 | `sdrpp/.../snr_meter.cpp`、`volume_meter.cpp` | 数字读数已按裁决撤掉（`desktop/spectrum_widget.py:3121-3124`） |
| 顶栏常驻模式切换 + 搜索 | 原型 `index.html:404-424`；design-basis §4 #1 | 埋在菜单/快捷键（`desktop/command_palette.py`） |
| 天空侧栏导航 + 缩放按钮 | 原型 `index.html:427-507`；design-basis §4 #2 | 已有「找星」搜索框与信息卡（`desktop/rf_sky_view.py:2139`、`:1887`），无侧栏/按钮 |
| 过境表排序/过滤 | design-basis §4 #7 | `QTableWidget` 无排序、无过滤 |
| 桌面 FT8 面板 | 后端已有 FT8 模块 | `desktop/` 无 UI 入口 |

### §3.2 我们做得比参考差

1. 频谱每帧 `update()` 绕过性能档（§1.3 #6，`main_window.py:8697` + `spectrum_widget.py:3126-3127`）。
2. 调谐失败后频谱仍停在新频点（§1.3 #3）。
3. 云台误差统计恒为 0（§1.3 #4）。
4. NTP 周期同步阻塞 GUI（§1.3 #5）。
5. 右栏 7 节 + 设置内多节长列滚动，仅靠「跳到分组」补（`main_window.py:2022-2038`）。
6. 无设备引导为横幅 + 状态栏双份（`main_window.py:1831`、`:4767`）。
7. 参数面板分组与 reset 反馈已补（`a96c010f`），但仅覆盖设置页，控制面板仍未分组。

### §3.3 明确「不该抄」六条及理由

| # | 不抄的东西 | 理由 |
|---|---|---|
| 1 | **GPL/AGPL 源码直接搬**（sdrpp/gqrx/satdump/gpredict/stellarium/onair；stellarium-web-engine 为 AGPL） | 本仓 MIT；移植即传染性许可冲突。只能读行为、用 Qt 重写。 |
| 2 | **ImGui 立即模式架构**（sdrpp/satdump） | ImGui 以「每帧重建整棵 UI」为前提，Qt Widgets 是保留模式、控件有生命周期与信号槽。照搬意味着把 MainWindow 拆成自绘 while 循环，重写量 ≥ 现有 UI，且丢失控件无障碍/输入法/高 DPI 适配。收益（更炫的实时曲线）已由现有 `QPainter/OpenGL` 双路径满足，**替换成本远大于收益**。 |
| 3 | **Stellarium 全量 OpenGL 星空引擎** | 其核心是大气折射/星等模型/银河纹理/行星历表的完整天文渲染栈。我们 `desktop/rf_sky_view.py`（约 429KB）已实现所需透视投影、地平圆、卫星/行星历表。替换 = 推倒重来 + 引入 GPLv2 代码 + 显卡兼容面扩大，**成本大于收益**。 |
| 4 | gpredict 的 GTK3 视觉 | 控件风格过时，与当前深色玻璃主题冲突；只借信息结构。 |
| 5 | satdump 全量图像产物管线 UI | 其面向多探测器产品链，UI 与后端产物模型强耦合；我们只需 `satellite_image_panel` 的只读查看。 |
| 6 | onair「每解码器一套 UI」 | 面向 100+ 解码器的全量布局，维护面与测试面巨大；我们按现有能力集增量加面板。 |

补充：`glasswork`（MIT）可借鉴毛玻璃材质与边缘折射的 CSS/JS 思路；它不是卫星追踪应用，只取材质层。

---

## §4 `design-basis.md` §4 十大差距现状复核（2026-11-11）

> 口径：只认代码/文档证据。「已修」按四元组给出；「部分」写明已落地与缺口；「仍成立」给现状证据。
> 本轮**未启动 GUI**，涉及像素的条目以 `narrow-screen-plan.md` 的已登记测量为准，未复跑。

| # | 原差距 | 判定 | 证据 |
|---|---|---|---|
| 1 | 顶栏无「专业/星象仪」切换 + 搜索胶囊 | **仍成立** | `desktop/main_window.py:3889-3990` 无模式段与搜索框；命令面板为菜单项（`ac87f7b9` 只加 `desktop/command_palette.py` 与快捷键） |
| 2 | 星象仪缺侧栏导航/信息卡/缩放按钮/HUD | **部分成立** | 已落地：信息卡 `desktop/rf_sky_view.py:1887`、自包含搜索「找星」`:2139`（`51c01a57`，路径命中）；缺口：侧栏导航与显式缩放按钮仍无 |
| 3 | 右栏 7 个页签并列、无主次 | **部分成立（形态已改）** | `desktop/main_window.py:1905` 已是 `RightColumn()` 长列、7 个 `_add_right_tab`（`:1960-2011`）+「跳到分组」（`:2022-2038`，`a2be554a`）；缺口：观测/解码/工具主次分组仍未做 |
| 4 | 无设备引导横幅 + 状态栏双份 | **仍成立** | 横幅 `desktop/main_window.py:1831`、`_NO_SDR_HINT` `:264`、状态栏文案 `:4767`/`:3272` 仍在两处 |
| 5 | 顶栏/底栏状态全压到 `QStatusBar` | **部分成立** | 顶栏已常驻连接态与授时芯片（`desktop/main_window.py:3906`、`:12293`；`a2be554a` 收听入口常驻）；录音/过境仍在状态栏 |
| 6 | 参数面板无分组语义、reset 无反馈 | **已修（行为测试支撑）** | 已修 — `a96c010f` — `desktop/settings_panel.py:635` — `RightSection` 分组；反馈有行为断言：`tests/test_settings_panel_observability.py:112` 断言 `"已恢复本组默认值"` |
| 7 | 大列表不可筛选 | **仍成立** | `desktop/new_spacetime_panel.py:471-483` 建表后无 `setSortingEnabled`、无 `QSortFilterProxyModel`、无过滤输入 |
| 8 | 能力矩阵/命令面板埋菜单 | **部分成立** | 命令面板已存在（`ac87f7b9` → `desktop/command_palette.py`，`Ctrl+K`）；仍无顶栏可见入口，未与搜索合并 |
| 9 | 无间距/圆角/浮层 token | **已修（纯结构改动，但「像素不变」无测试）** | 已修 — `5ba61b70` — `desktop/tokens.py:271-343` — `self.RADIUS` / `SPACING` / `SIZE` / `RATIO` / `MOTION` 均存在；该提交只改 `main_window.py`+`tokens.py` 共 6 行，**未附测试**，「像素不变」仅提交说明，未独立取证 |
| 10 | 1366×768 高度溢出 71px | **待复验（原「已修」判定作废）** | 改动在位（`a2be554a`：`desktop/main_window.py:842-852` 改为按屏幕取 `1000×680` 兜底、`_apply_responsive_chrome`），但**旧校验方式无效**：① `scripts/ui_narrow_breakdown.py:162` 固定 `win.resize(1000, 600)`，输出的是 `minimumSizeHint` 求和，不是目标尺寸下的实际窗口；② `docs/narrow-screen-plan.md:22` 的 `960×633` 是「内容最小宽提示」，同文 `:295-308` 自认「960 不是声明最小宽 1000」；③ 实际最小尺寸被 `main_window.py:852` 抬到 `1000×680`，故 `960×633` **物理不可达**（`scripts/ui_baseline.py:125` 亦记录「1000x600 被最小高 680 夹成 1000x680」）；④ `a2be554a` 的测试只断言子控件几何（命令栏宽/谱面高），**无一条断言「实际窗口尺寸 == 请求尺寸」**；⑤ 提交说明中的 `900x620` 全仓无脚本/测试可复现（grep 0 命中）；⑥ 测量脚本本身从未请求过 `1344×700/1366×768`，且 `scripts/ui_narrow_breakdown.py:163-170` 已写明「families() 为 0 时拒绝出数」。 |

**结论：已修 2 条（#6、#9），待复验 1 条（#10），部分成立 4 条（#2、#3、#5、#8），仍成立 3 条（#1、#4、#7）。**
`design-basis.md` 的 #6/#9/#10 三行需要改判定，按跨线规则出交接单（见 `2026-11-06-design-basis-rows-handoff.md`），本文件不直接改账本。

### §4.1 尺寸类结论的有效性判据（本轮新增，必须遵守）

1. **`minimumSizeHint` ≠ 实际窗口尺寸**。只测 hint 不能证明「目标分辨率下不裁切」；必须断言
   `window.size() == 请求尺寸` 或直接读取 `window.minimumSize()`。
2. **请求尺寸会被 `setMinimumSize` 静默抬高**。断言必须比较「请求值」与「实际值」，出现
   实际 > 请求即说明该尺寸不可达，不得当作通过。
3. **`QT_QPA_PLATFORM=offscreen` 下字体常不可用**（本机 `QFontDatabase.families() == 0`，
   文字渲染为方框），文本驱动的宽高不可信；`scripts/ui_narrow_breakdown.py:163-170`
   已内置该守卫并拒绝出数。offscreen 数字只能用于同一环境的相对比较，不能用于真机验收。
4. **几何相交对数在多次进程间一致 ≠ 无重叠**。若扫描只比较控件矩形、把透明容器也算进去，
   结果一致只证明脚本稳定，不证明布局正确；必须排除透明/零尺寸容器并给出重叠样本坐标。
5. 反例（已确认）：同一脚本 3 个进程的「几何相交对数」完全一致，但实际仍存在遮挡——
   该指标不得作为「无重叠」的证据。

---

## §5 分级依据

- **P0**：现有可复现缺陷或正确性问题（含用户明确要求必须先保的显示完整性）；单点改动、失败路径为主、可行为断言、可独立成一步、不改信息架构。
- **P1**：与原型/参考存在明确信息组织差距，中等成本，涉及布局或新增轻量控件；需按 `design-basis.md` §5 留痕并更新 UI 基线。
- **P2**：新增面板/新数据源/新渲染形态，跨模块且产生新测试面与基线，成本高；先立项再实施。

---

## §6 工单（P0，可直接执行；不写代码）

### P0-1 调谐失败回滚必须拉回频谱（对应交接 #3）

- **文件/行**：`desktop/main_window.py:7037-7053`（`_revert_tune_ui`）。
- **改动语义**：`last_hz > 0` 分支内，除现有标签与 `control_panel` 回写外，同步把频谱中心拉回 `last_hz`（调用 `self.spectrum.set_center_freq(last_hz / 1e6)`，单位以该接口实际签名为准，先读 `desktop/spectrum_widget.py` 的 setter 与内部 `_center_freq_hz`）；`last_hz <= 0` 分支不得把频谱设为 0。若存在 `_vfo_center_hz` 等内部缓存，同拍回写，避免下一帧又被旧值覆盖。
- **影响面**：`_on_tune_fm`（:7055）、SDR 调谐路径、`_on_spectrum_freq_changed` 的失败分支；只影响失败路径。
- **测试**：在 `tests/test_main_window_hw_truthfulness.py` 增用例：mock 后端 `set_frequency` 抛错/返回 False → 断言 `spectrum.set_center_freq` 以 `last_hz` 被调用，且频谱内部中心值 == `last_hz`。
- **变异反证**：删掉新增的那一行 → 新用例必须红；旧的标签断言仍绿（证明新用例确实捕获频谱缺口，而非重复断言标签）。
- **风险**：低。注意构造顺序：初始化早期 `spectrum` 可能尚未挂载，需 `getattr` 保护。

### P0-2 云台死区记账传对层 + 不再静默吞错（对应交接 #4）

- **文件/行**：`desktop/main_window.py:12175-12183`；消费端签名 `:10561-10577`。
- **改动语义**：把 `self._apply_gimbal_result(ctx, {}, 0.0)` 改为传 `{"ctx": ctx}`（消费端读 `task.get("ctx")`）；`except Exception: pass` 收窄为具体异常（至少 `KeyError/TypeError/ValueError`）并 `logger.warning` 携带异常类型与首行；死区分支（`cmd is None`）继续记真值 az/el，不得记 0。
- **影响面**：`_gimbal_error` 误差统计口径会从「恒 0」变为真实值，历史统计不可与新统计混比，须在提交说明与 `docs/COORDINATION.md` 登记口径变化。
- **测试**：`tests/test_gimbal_track_chain.py` 或 `tests/test_ring5_gimbal_sim_contract.py` 增：构造一拍 `cmd=None` 且 `limiter.last_command` 非空，断言记录 az/el == `truth_az/truth_el`；另一用例注入异常，断言写 warning 且不中断循环。
- **变异反证**：把 `{"ctx": ctx}` 改回 `ctx` → az/el 断言变 0 必须红；把 warning 改回 `pass` → 静默吞错用例红。
- **风险**：低-中（统计口径变化）。

### P0-3 NTP 同步移出 GUI 线程（对应交接 #5）

- **文件/行**：`desktop/main_window.py:12297-12318`；被调方 `mbdsdr_ai/new_spacetime.py:370-391`。
- **改动语义**：GUI 线程只做 `prefer_ntp=False` 的系统时间快照并立即刷 UI；`counter % 30 == 1` 时把 NTP 同步投递到后台（`QThreadPool`/`QRunnable` 或 `threading` + Qt Signal），完成后回主线程 `sky_view.set_time_info`；`_ntp_inflight` 防重入；后台失败 `logger.warning` 并保留系统时间；窗口销毁后回调必须安全（弱引用或存活标志）。
- **影响面**：授时链与天空图时间显示；线程生命周期。
- **测试**：新增 `tests/test_time_info_async.py`：monkeypatch `get_time_info` 阻塞 2s → 断言 `_update_time_info()` 返回 < 100ms 且 UI 已用系统时间；触发完成信号断言收到 ntp 值；异常路径断言 warning。
- **变异反证**：改回同步调用 → 耗时断言红；删除 `_ntp_inflight` → 并发触发用例红。
- **风险**：中（回调时序/对象销毁）。**不得**在 init 路径同步 NTP。

### P0-4 恢复「性能档是唯一重绘驱动」（对应交接 #6 的回归）

- **文件/行**：`desktop/spectrum_widget.py:3118-3127`（`_refresh_status_labels`）；调用点 `desktop/main_window.py:8696-8699`。
- **改动语义**：每帧钩子只做廉价状态同步（`sync_span_slider`），**删除无条件 `_redraw()`**；重绘由 `_plot._timer` 按性能档驱动。若确有「首帧立即出图」需求，用工单内的 `_redraw_pending` 标记由定时器消费，禁止在数据回调里直接 `update()`。同时把调用点的裸 `except: pass` 改为 `logger.debug`。
- **影响面**：频谱/瀑布刷新节奏；eco 档 CPU 占用。属于可见节奏变化，提交需附基线截图与性能档实测。
- **测试**：新增/扩展 `tests/test_desktop_ui_responsiveness.py`：monkeypatch `_fft_plot.update`/`_wf_plot.update` 计数，50ms 内灌 200 帧 → 断言 `update` 次数 ≤ 性能档允许值（eco 100ms / quality 50ms）；首帧仍必须出图（`update` ≥ 1）。
- **变异反证**：恢复 `_redraw()` → 计数断言红；把首帧标记也删掉 → 首帧断言红。
- **风险**：低；但必须由持有 GUI 的会话复跑真实截图。

### P0-5 1366×768 显示完整性（**待复验**，不得写「已修」）

- **文件/行**：`desktop/main_window.py:842-852`（按屏幕取 `1000×680` 兜底）；`docs/narrow-screen-plan.md:18-22`、`:295-308`。
- **当前判定**：**待复验**。改动在位，但原「双指纹过线」证据无效（见 §4 #10 与 §4.1）：
  `960×633` 是 `minimumSizeHint` 且低于声明最小宽 1000、最小高 680，物理不可达；
  `a2be554a` 无「实际尺寸 == 请求尺寸」断言；`900x620` 无脚本可复现。
- **复验要求（只读，不改布局）**：
  1. 在真屏 `windows` 平台、`WA_DontShowOnScreen` 下，显式读取 `window.minimumSize()` 与
     `window.availableGeometry()`，断言 `minimumWidth <= 可用宽` 且 `minimumHeight <= 可用高`；
  2. 分别按 DPR 1.0（1344×700 验收线）与 DPR 1.5（1366×768 物理屏）各跑一次，记录
     `请求尺寸`、`实际尺寸`、`是否被夹`；
  3. 报告必须写「实际 == 请求」或「实际 > 请求（被最小尺寸抬高）」，禁止只报 hint。
- **通过后**：把 `design-basis.md` §4 #10 改为「已修 + 复验提交号 + `实际尺寸 == 请求尺寸` 断言位置」。
- **不通过**：另立 P1 工单，本步仍不动代码。
- **风险**：0（只读）；真屏复验须交给持 GUI 的会话，本线不抢设备。

---

## §7 交接事项（不在本文件直接改）

1. `docs/design-basis.md` §4 的 #6/#9/#10 判定行已过期，替换文本见交接单 `docs/pending-patches/2026-11-06-design-basis-rows-handoff.md`；本文件只出证据，不改账本。
2. `desktop/main_window.py:1153-1155` 的 `_iq_poll_timer` 为死代码（交接 #1 修复后遗留），可随 P0-1 同批清理，但须单独证明无调用方。
3. `docs/narrow-screen-plan.md:23` 仍写「声明最小宽（1000）地板仍是 `setMinimumSize(1000, 600)`（`main_window.py:591`）」，实际已是 `main_window.py:846-852` 的 `min(1000, 94%屏宽) × min(680, 94%屏高)`、兜底 `860×560`，属过期引用，交 owner 更正。
4. `docs/narrow-screen-plan.md:20-22` 的「offscreen 1265×618 / 真屏 960×633，两指纹都过」判定作废：两值均为 `minimumSizeHint`，且 `960×633` 低于其下一行自述的声明最小宽 1000，不可达。owner 应改为「待复验」并引用本文件 §4.1。
