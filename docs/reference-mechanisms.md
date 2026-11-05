# 参考项目机制目录（天空 / 频谱）

> 本篇结论未经 commit 取证，引用前须复核（判据见 `scripts/verify_claims.py`；补齐四元组 `<结论> — <hash> — <path>:<line> — <改动后字面量>` 后可摘除本行）。

> 只读研究产出。范围：`D:\mbdsdr\reference\` 下 5 个 C/C++/JS 参考项目。
> 目的：把「别人怎么做的」拆成可派活的机制条目，并诚实标注我们已实现的部分。
> 行号核验：下表所有「参考位置」均由本人用 Read/Grep 直接核对到行；子 agent 仅用于初筛候选文件。少数条目明确标注「推断，未验证」。

## 0. 结论先行

1. 五个参考项目本次共核到 **37 个关键模块**（Stellarium 10、stellarium-web-engine 8、SDR++ 8、gqrx 7、gpredict 4），整理成 **34 条机制条目**（天空 14、频谱 15、交互/配置 5）。
2. **大部分常被点名的机制我们已经有了，而且部分做得更好**：光标锚定缩放、频段标签碰撞剔除、3 行书签堆放、VFO 带内 SNR、Max/Min hold、设置逐键迁移、快捷键冲突守卫、主题令牌、行星视大小+相位。这些不要再造一遍。
3. 本轮建议开工的 5 类（详见 §6）：**goto 的大圆向量插值**（现为 az/alt 独立线性）、**频谱峰值检测双通道**（现为单遍局部极大）、**频率刻度阶梯/单位**（现固定 5 等分、MHz 两位）、**卫星地影/受晒状态**（现完全缺失）、**dB 量程自动与 Y 轴拖动**（现只有两个滑杆）。
4. 最该警惕的包袱（详见 §5）：Stellarium 把消光公式同时写在 C++ 与 GLSL（双份数学源）、SDR++ 的频段标签“窄了就丢”与 ImGui/DSP 紧耦合、gqrx 的 QSettings 分散 + 硬编码快捷键、gpredict 的“只有实时时间”。
5. 未读：`satdump`、`decoders`、`blackmagic` 等目录存在但不在本次「天空+频谱」范围；Stellarium 全仓标签碰撞只抽查了 `LabelMgr.cpp` 与 `StelSkyDrawer.cpp`，属「未找到」而非严格证明。

## 1. 路径与规模核实

| 项目 | 绝对路径 | 关键模块数 | 本次核到的模块（简） |
|---|---|---|---|
| Stellarium（C++/Qt） | `D:\mbdsdr\reference\stellarium` | 10 | MovementMgr、Core 时间、ProjectorClasses、SkyDrawer、RefractionExtinction、Landscape、Planet、ActionMgr、ScriptMgr、LabelMgr（反例） |
| stellarium-web-engine（C/JS） | `D:\mbdsdr\reference\stellarium-web-engine` | 8 | core/projection、navigation（时间+方向）、movements、labels、landscape、skybrightness、planets |
| SDR++（C++/ImGui） | `D:\mbdsdr\reference\sdrpp` | 8 | waterfall（刻度/瀑布/调色板/autoRange/VFO 信息）、vfo_manager、config、style、flog、bandplan 菜单 |
| gqrx（C++/Qt） | `D:\mbdsdr\reference\gqrx` | 7 | plotter（缩放锚定/Y 轴/峰值/书签堆放）、dockfft（QSettings）、bandplan、bookmarks、agc、快捷键 |
| gpredict（C/GTK） | `D:\mbdsdr\reference\gpredict` | 4 | gtk-polar-view、gtk-azel-plot、sat-vis（地影）、配置持久化 |

以下路径均按 `项目/相对路径:行号` 书写。

## 2. 天空侧机制

| # | 问题 | 我们现状 | 参考实现位置 | 建议做法 | 优先级 | 风险 |
|---|---|---|---|---|---|---|
| S1 | 视角锁定（目标随时间移动） | `desktop/rf_sky_view.py:1351-1359` 锁定状态；`:1464-1467` 33ms tick；`:1605-1606` 重算目标+指数趋近；有 `test_sky_camera_lock.py` | `stellarium/src/core/StelMovementMgr.cpp:319-321`（flagLockEquPos）；`StelCore.cpp:537-551`（每帧 updateMotion）；`stellarium-web-engine/src/navigation.c:87-109`（四元数 slerp 锁定） | **不抄**。我们的「重算目标 + 指数趋近」与 SWE 的 slerp 语义等价，且已有速率钳制；无需引入四元数 | 低 | 无 |
| S2 | goto 飞行路径 | `mbdsdr_ai/sky_interaction.py:108-117`（az 最短弧 + alt 线性增量）；`:125-140`（三者在 t 上各自 OutCubic） | `stellarium/src/core/StelMovementMgr.cpp:1482-1527`（aim/aimUp 向量 + 统一 speed）；`stellarium-web-engine/src/navigation.c:44-82`（时间用 smoothstep） | 改为**起点/目标单位向量 slerp**（或向量 lerp 后归一化），az/alt 只在重投影时解算。保留 OutCubic 与 duration 语义 | 高 | 近天顶路径与截图会变；需新增“过天顶”用例，否则会误判为回归 |
| S3 | 天顶/天底奇异 | `mbdsdr_ai/sky_interaction.py:171-175` 只把 alt 夹到 ±(90−eps)，az 在极点仍无定义 | `stellarium/src/core/StelMovementMgr.cpp:1494-1521`（从 up 向量解出 az，并把起点 alt 退 1e-4°） | 与 S2 同批：起转前解一次 az，避免极点处 az 跳变；这是 Stellarium 的显式补丁，值得移植 | 中 | 与摄像机锁定叠加时需避免二次修正 |
| S4 | 大气消光模型 | `mbdsdr_ai/skyengine/stars.py:196-207` 用 `k/sin(alt)`；而 `mbdsdr_ai/atmosphere.py:170-190` 已有 Kasten-Young airmass，渲染链未使用 | `stellarium/src/core/RefractionExtinction.cpp:35-61`（apparent z 用 Rozenberg 1966，true z 用 Young 1994） | 渲染统一为 `Δm = airmass(alt) * k`，删除重复公式（与 `1/sin` 的差异随仰角降低增大：30° 约 0.3%、10° 约 3%、5° 约 10%，且低仰角本就被 3.5 等截断吸收） | 中 | 不是视觉升级而是**去重复**；地平线附近仍应保留 3.5 等截断，截图基线可能轻微变化 |
| S5 | 大气折射 | 天空渲染走矩阵链 `mbdsdr_ai/celestial_geometry.py:402-405`（**无折射**，与 `desktop/rf_sky_view.py:36-44` 的声明一致）；折射公式已在另一条链 `mbdsdr_ai/astronomy.py:303-309` 实现，但天空不用它 | `stellarium/src/core/RefractionExtinction.cpp:64-101`（折射/消光变换；具体绘制链调用点未逐行核对，**推断，未验证**） | 只给**绘制**加可选折射开关（复用 `astronomy.py:303-309`），跟踪/指向仍用真高度；默认关闭 | 低 | 低仰角目标位移约 0.5°，会改变“点亮星等”判定；`test_refraction.py` 已 pin 公式，必须保持其行为 |
| S6 | 标签避让 | `desktop/rf_sky_view.py:357-376`（`label_boxes_keep` 二维剔除）；`:3288-3318` 统一 flush | `stellarium-web-engine/src/modules/labels.c:128-145`（重叠 = 交叠矩形的 min(dx,dy)）；`:162-198`（`fader.target` + 掩星即隐） | 保留我们的剔除；补 **(a) 重叠面积阈值**（而非布尔相交）与 **(b) 150ms 淡入淡出** | 中 | 每帧需缓存 label 状态；无头截图测试会出现中间态 |
| S7 | 标签优先级/密度 | `desktop/rf_sky_view.py:3147-3150`（DSO 小而不亮不挂名）；其余调用以 `2.0+vmag` 为优先级 | `stellarium-web-engine/src/modules/labels.c:136`（priority 比较）、`:148-154`（远→近排序） | **不抄**。按星等/类型分级已足够；SWE 的优先级语义与我们等价 | 低 | 无 |
| S8 | 星等→半径/亮度 | `mbdsdr_ai/skyengine/stars.py:150-187`（含 sqrt 压缩、亮度补偿、<0.3px 丢弃） | `stellarium/src/core/StelSkyDrawer.cpp:403-417`（`radius<0.3` 丢弃、`1.2px` 防闪烁） | **不抄**（已同构，连防闪烁思路都有） | 低 | 无 |
| S9 | 天体符号按视大小 | `desktop/rf_sky_view.py:4093-4116`（真实半径圆面 + 相位亮区）；`:4177` 读 `angular_diameter_arcsec` | `stellarium/src/core/modules/Planet.cpp:3904`（`screenRd = getAngularRadius * pixelPerRadAtCenter`） | **不抄**（已实现；行星 LOD 细分对我们 2D 矢量绘制无意义） | 低 | 无 |
| S10 | 地平线/地景 | `desktop/rf_sky_view.py:3323-3327`（地景 alpha：紧 FOV 淡出、俯视地下渐隐）；`test_sky_landscape_layers.py` | `stellarium/src/core/modules/Landscape.cpp:1030-1046`（ground/decor/fog 的绘制顺序与混合） | **不抄**全景贴图/fog；只核对「地景在星之后、地平线下不画天体」——现状已满足 | 低 | 无 |
| S11 | 脚本化导览 | **无**。全仓无 tour/步骤调度（grep 无 tour/导览） | `stellarium/scripts/messier_marathon.ssc:27-51`（可选目标+moveTo+wait）；`stellarium/src/scripting/StelScriptMgr.cpp:927-936`（脚本改时间倍率并同步移动速度） | 加**数据驱动导览**：JSON 步骤 `select/goto/wait/set_rate/label`，不引入脚本语言。这是唯一「整块能力缺失」的天空侧机制 | 中 | 与 AI 工具调用命名需隔离；必须可中断/可回放 |
| S12 | 卫星地影/受晒 | **无**。全仓无 eclipse/受晒字段（grep 无） | `gpredict/src/sat-vis.c:55-105`（太阳矢量 + `Sat_Eclipsed` → ECLIPSED/DAYLIGHT/VISIBLE，含晨昏门限） | 轨道快照增加 `sunlit: Optional[bool]`；天空图与轨道页签区分受晒/地影；无太阳数据时显式 `None` | 中 | 依赖太阳位置与卫星位置；**不得用默认值造假** |
| S13 | 卫星 az/el 极图 | `desktop/rf_sky_view.py:2841` 保留固定等距极坐标兼容接口；主视图走投影 | `gpredict/src/gtk-polar-view.c:202-246`（`r = r*(1 - el/90)` 线性仰角→半径，含方向枚举） | **不抄**主视图；仅轨道小图可借它的“方向枚举”（N-up/E-up 切换） | 低 | 与主视图视觉口径不一致，只可作小部件 |
| S14 | 时间倍率/暂停/倒放 | `mbdsdr_ai/new_spacetime.py:142-190`（rate=0 暂停、负值倒流、JD_SECOND 换算） | `stellarium/src/core/StelCore.cpp:1386-1413`（setTimeRate/getTimeRate/toggle/revert） | **不抄**（我们已具备且语义更显式） | 低 | 无 |

## 3. 频谱侧机制

| # | 问题 | 我们现状 | 参考实现位置 | 建议做法 | 优先级 | 风险 |
|---|---|---|---|---|---|---|
| P1 | 频率轴刻度（步长与单位） | `desktop/spectrum_widget.py:1389-1417`：5→2 档自适应数量，但数值始终等分、固定 `MHz` 两位（10 kHz 粒度） | `sdrpp/core/src/gui/widgets/waterfall.cpp:40-47`（1/2/5×10^n 阶梯 `findBestRange`）；`:49-62`（Hz/K/M/G 自动单位）；`:145-160`（按 range 画刻度） | 抽纯函数：按 span 选 1/2/5×10^n 步长 → 标签数 ≤6；步长 <1 MHz 时用 kHz 与对应小数位；刻度落在整数频率上 | 中 | 截图基线更新；需新增纯函数测试，避免动到调谐逻辑 |
| P2 | 瀑布/频谱 dB 刻度 | `desktop/spectrum_widget.py:1420-1428` 固定 5 档等分 | `sdrpp/core/src/gui/widgets/waterfall.cpp:120-143`（`vRange` 由 `findBestRange` 生成） | 用同一阶梯函数生成 dB 刻度（10/20/50 dB），与 P1 共用 | 中 | 与 P1 同批，避免两套刻度逻辑 |
| P3 | dB 量程交互（拖动） | `desktop/spectrum_widget.py:1699-1711` 只有两个滑杆；`test_axis_zoom_fft_info.py` | `gqrx/src/qtgui/plotter.cpp:350-368`（Y 轴捕获模式：按像素位移换算 dB 平移，越界回滚） | 加「拖动 Y 轴平移量程」（Shift+左键或右键拖动），与滑杆双向同步 | 中 | 与左键调谐冲突，必须用修饰键；越界回滚逻辑照抄即可 |
| P4 | dB 自动量程 | **无**。默认写死 `-100/-20`（`desktop/spectrum_widget.py:758-759`） | `sdrpp/core/src/gui/widgets/waterfall.cpp:976-990`（`autoRange`：min−5 / max+5） | 加“自动”按钮：P5/P95 ±5 dB，带滞回（≥3 dB 才改），避免空谱抖动 | 中 | 强 CW 会把底噪压黑；必须用分位数而非 min/max，或照抄 SDR++ 但加滞回 |
| P5 | 峰值检测 | `desktop/spectrum_widget.py:437-470`：单遍局部极大 + median 噪声底 + ~2Hz 缓存 | `gqrx/src/qtgui/plotter.cpp:1699-1755`：窄峰（原谱）+ 宽峰（5×平滑），判据 `vi==maxV && vi>2*avg && vi>4*min`，宽峰若近旁已有窄峰则丢弃 | 移植双通道 + 去重；保留 2Hz 缓存语义与 `max_peaks` | 中 | 现有测试可能 pin 峰数；阈值需用真实样本重新标定 |
| P6 | Max/Min hold | `desktop/spectrum_widget.py:171-204` 纯 max/min 包络（`test_hold_snr.py`） | `sdrpp/core/src/gui/widgets/waterfall.cpp:889-939`（`pushFFT` + `latestFFTHold` 带衰减的 hold） | 加可选「衰减 hold」（每帧减 N dB，触底停止）；默认仍纯 max | 低 | 与现有 hold 语义重复，需 UI 上区分“永久 / 衰减” |
| P7 | 瀑布调色板 LUT | `desktop/spectrum_widget.py:1282-1293` 256 项 LUT，逐项 `value_to_color` | `sdrpp/core/src/gui/widgets/waterfall.cpp:944-974`（1024 项 + 线性插值到色表） | 可升到 1024（成本可忽略）或保持 256；**不优先** | 低 | 渐变带可能在高对比色表下可见 |
| P8 | 瀑布行缩放 | `desktop/spectrum_widget.py:1296-1305` numpy 索引重采样 + LUT | `sdrpp/core/src/gui/widgets/waterfall.cpp:601-630`（按 viewOffset/带宽重采样）（子 agent 标定，本人未逐行核对，**推断，未验证**） | **不抄**（我们的 numpy 实现等价且更快） | 低 | 无 |
| P9 | 缩放锚定光标 | `desktop/spectrum_widget.py:2125-2139`；`test_zoom_to_cursor.py` | `gqrx/src/qtgui/plotter.cpp:842-869`（`new_FftCenter = old + offset*(1-step)`，并做边界回夹、span 取偶） | **不抄**（已实现同款锚定公式） | 低 | 无 |
| P10 | 频段标签放置 | `desktop/spectrum_widget.py:709-739`：forced/high/low 三级 + 碰撞剔除 + 带太窄不画 | `sdrpp/core/src/gui/widgets/waterfall.cpp:697-700`（只判 `txtSz.x <= width`，**无碰撞**）；`gqrx/src/qtgui/plotter.cpp:2062-2079`（`elidedText` 截断，无碰撞） | **不抄**。我们的分级剔除优于两者；保留 | 低 | 无 |
| P11 | 多频段重叠提示 | `desktop/spectrum_widget.py:2040-2052` 只取“最窄覆盖带”；`:2054-2064` 单条气泡 | `gqrx/src/qtgui/plotter.cpp:299-313`（`getBandsEncompassing` 列出**全部**覆盖频段） | 气泡最多列 3 条重叠频段（窄→宽），其余折叠为“+N” | 低 | 文案变长，注意窄栏截断 |
| P12 | 书签多层堆放 | `desktop/spectrum_widget.py:1099-1147` 3 行 + 放不下只留虚线旗 | `gqrx/src/qtgui/plotter.cpp:2014-2031`（`tagEnd[level]` 逐层试放，找不到层则跳过） | **不抄**（我们已是同一 shelf-packing 思路） | 低 | 无 |
| P13 | VFO 带内 SNR | `desktop/spectrum_widget.py:1875` `compute_snr_db()`；`test_hold_snr.py` | `sdrpp/core/src/gui/widgets/waterfall.cpp:558-585`（带内 max、带外均值、SNR=max−avg） | **不抄**（已实现，且带外强峰反向用例已覆盖） | 低 | 无 |
| P14 | VFO 拖拽与 snap | `desktop/spectrum_widget.py:2066-2072`（中心对齐 snap 网格） | `sdrpp/core/src/gui/widgets/waterfall.cpp:465-476`（拖动后 `round(off/snap)*snap`） | **不抄**（语义一致） | 低 | 无 |
| P15 | 频段彩带边界线 | `desktop/spectrum_widget.py` 当前只填半透明色块 | `sdrpp/core/src/gui/widgets/waterfall.cpp:633-702`（仅当起点/终点可见时画竖边线，见 `:692-695`） | 加左右 1px 分隔线，可提升相邻同色频段的可读性 | 低 | 视觉噪音；需要在线宽 1px 下验证 |

## 4. 交互 / 配置 / 可观察性

| # | 问题 | 我们现状 | 参考实现位置 | 建议做法 | 优先级 | 风险 |
|---|---|---|---|---|---|---|
| I1 | 快捷键体系 | `desktop/main_window.py:641-654` 等全部经 `shortcuts.py` 的 `_key()`；`test_shortcuts_guard.py` 反向核对 | `stellarium/src/core/StelActionMgr.cpp:53-77`（conf `shortcuts/<id>` 覆盖、主/备两键、冲突告警）；`gqrx/src/applications/gqrx/mainwindow.cpp:159-174`（硬编码 `setShortcut`） | **不抄** Stellarium 的 QAction 双轨；我们的注册表+守卫已优于 gqrx | 低 | 无 |
| I2 | 设置持久化与迁移 | `desktop/settings.py:40-44` 逐键迁移标记（`volume_scale_v2`，幂等，不猜量纲）；`test_config_key_migration.py` | `sdrpp/core/src/config.cpp:18-52`（缺字段/坏文件重置默认）；`gqrx/src/qtgui/dockfft.cpp:223-260`（各 dock 分散 `QSettings`） | **不抄**。逐键迁移比 SDR++ 的整文件重置安全；继续保持 | 低 | 无 |
| I3 | 主题/样式令牌 | `desktop/tokens.py` + `themes.py`；`test_theme_tokens.py` | `sdrpp/core/src/gui/theme_manager.cpp:8-38`（按目录批量加载 `*.json` 主题）；`:40-70`（缺 `name`/类型错误/重名即拒绝，不半途应用） | **不抄**令牌结构（我们的 Python 令牌表更直接）；可借「目录装载 + 严格校验 + 重名拒绝」的加载器语义 | 低 | 校验失败要整体回退到当前主题，不能只应用一半 |
| I4 | 可观察性/日志 | `desktop/main_window.py:19` 等 4 处 `getLogger`；未见统一 handler/轮转（**推断，未验证**：可能在入口脚本配置） | `sdrpp/core/src/utils/flog.cpp:25-39`（DEBUG/INFO/WARN/ERROR + 终端着色） | 若确无统一 handler，补「分级 + 单文件轮转 + 崩溃前 flush」；否则不动 | 低 | 轮转参数需与既有日志路径兼容 |
| I5 | 导览脚本（交互侧） | 同 S11，缺失 | 同 S11 | 同 S11；建议作为「观测会话」而非脚本引擎 | 中 | 同 S11 |

## 5. 明确不抄：参考项目的历史包袱

1. **Stellarium 把同一套消光数学写两份**：C++ 一份、GLSL 一份（`stellarium/src/core/RefractionExtinction.cpp:64-101`）。我们是单语言渲染，绝不能把 `airmass`、`extinction` 再复制到第二个模块——S4 的目标就是消灭这种重复。
2. **SDR++ 的频段标签策略太粗**：只判“文字比带宽窄”，无碰撞（`sdrpp/core/src/gui/widgets/waterfall.cpp:697-700`）。我们已有三级优先级 + 碰撞剔除，不要倒退。
3. **SDR++ 把 GUI 与 DSP 绑在同一个 VFO 对象里**（`sdrpp/core/src/signal_path/vfo_manager.cpp:5-18`：构造时同时建 DSP VFO、ImGui VFO 并注册进全局 waterfall）。我们频谱与 DSP 通过信号解耦，保持现状。
4. **gqrx 的配置与快捷键是散的**：`QSettings` 键分散在各 dock（`gqrx/src/qtgui/dockfft.cpp:223-260`），快捷键硬编码在窗口构造（`mainwindow.cpp:159-174`）；本次抽查未见统一 schema/冲突检测（**未穷尽全仓，推断**）。我们的 `settings.py` + `shortcuts.py` 更好。
5. **gpredict 只有实时时间、无倍率/倒放**（`gpredict/src/gtk-polar-view.c:202-246` 仅跟踪绘制；时间源未逐行核对，**推断**）。不要为了“卫星页签”把天空时间引擎退回实时。
6. **Stellarium 的 JS 脚本引擎不适合当导览底座**（`StelScriptMgr.cpp:927-936` 把脚本 rate 双写进 core 与 movement）。Python 主程序里再嵌一门脚本语言，成本远大于 JSON 步骤表。

## 6. 下一批可立即开工的 5 条（含验收）

### ① goto 改为大圆向量插值 + 天顶补丁
- 改动点：`mbdsdr_ai/sky_interaction.py` 的 `GotoAnimation`（现 `:108-140`）。
- 验收：
  1. 新用例「az 0→180、alt 80→80」采样 20 个中间点，相邻点角距变化率 < 5%，总角距 = 起终点角距 ±0.5°；
  2. 过天顶用例单帧 az 跳变 < 90°；
  3. `tests/test_goto_anim_driver.py` 全绿，duration 语义不变；
  4. 截图基线仅路径形状变化，标注可接受。

### ② 峰值检测双通道（窄峰 + 宽峰）
- 改动点：`desktop/spectrum_widget.py:437` `find_peaks`。
- 验收：
  1. 合成谱「两个窄峰 + 一个宽平台」→ 恰好 3 个峰、无重复报告；
  2. 纯噪声（±1 dB 抖动）→ 0 峰；
  3. 带外强峰不抑制带内弱峰（反向用例）；
  4. 2 Hz 缓存语义与 `max_peaks` 上限不变；现有频谱测试全绿。

### ③ 频率/dB 刻度阶梯与单位
- 改动点：`desktop/spectrum_widget.py:1389-1428`，先抽成纯函数再接线。
- 验收：
  1. span ∈ {48k, 96k, 500k, 2.4M, 20M} 时步长均为 1/2/5×10^n，标签数 ≤6；
  2. 48k span 下标签精度 ≤1 kHz，且无重复文本；
  3. 相邻标签最小间距 ≥ 字宽 1.5 倍；
  4. 新增纯函数单测；截图基线更新。

### ④ 卫星地影/受晒标记
- 改动点：轨道快照增字段 + `desktop/rf_sky_view.py` 卫星绘制。
- 验收：
  1. 3 个已知时刻（受晒/地影/晨昏）判定与 gpredict 判据（`sat-vis.c:55-105`）一致，可用独立脚本对照；
  2. 无太阳矢量时 `sunlit=None`，UI 显示“未知”，不得渲染成受晒；
  3. 天空图两种状态颜色可辨（对比度按 `test_ui_contrast` 口径）；
  4. 新增纯几何单测，不依赖硬件。

### ⑤ dB 量程自动 + Y 轴拖动
- 改动点：`desktop/spectrum_widget.py:1699-1711` 与 `_draw_db_scale:1420-1428`。
- 验收：
  1. “自动”用 P5/P95 ±5 dB，滞回 ≥3 dB；
  2. 拖动 Y 轴 30px 后量程平移量与像素位移线性对应（误差 ≤5%），越界自动回夹；
  3. 滑杆与拖动双向同步，无循环触发；
  4. 强 CW 用例下底噪仍可见（不被压成全黑）。

> 次批候选（不进本轮）：S6 标签淡入淡出、S5 折射开关、S11 JSON 导览、P6 衰减 hold、P11 多频段气泡。

## 7. 未验证项与盲区

- `stellarium` 全仓是否还有别处的标签碰撞逻辑：仅抽查 `core/modules/LabelMgr.cpp` 与 `core/StelSkyDrawer.cpp`，**未找到**不等于不存在。
- `RefractionExtinction` 的渲染调用点（恒星/行星具体在何处加折射）未逐行核对，S5 中标为「推断，未验证」。
- `sdrpp/core/src/gui/widgets/waterfall.cpp:601-630` 的瀑布重采样细节来自子 agent，本人未逐行核对，故 P8 标为「推断，未验证」，且结论是“不抄”。
- 我们日志是否有统一 handler/轮转：仅查 `desktop/*.py` 的 `getLogger`，入口脚本未查，I4 标为「推断，未验证」。
- `satdump`、`decoders`、`blackmagic` 等目录未读（不在本次天空+频谱范围）。

> **2026-11-05 更正**：`reference/` 下不存在 `decoders`、`blackmagic` 目录；`satdump` 已在本轮补读。见 §8。

---

## 8. 参考仓库全量清点（2026-11-05）

本轮逐个核到 `D:\mbdsdr\reference\` 下 **18 个目录**，其中 7 个是完整应用/引擎源码，5 个是解码器源码，2 个是 FEC 上游源码，2 个是数据/规范仓，1 个是 WSJT-X 证据包，1 个是 WSJT-X 派生的 WSPR 源码。天空与频谱的既有条目（S1–S14、P1–P15、I1–I5）不重复展开；新增四类机制（H/D/T/X）与配置补充（C）在 §9–§13。

### 8.1 目录 → 项目 → 许可证

| 目录 | 项目 / 性质 | 许可证（证据文件） | 本文件覆盖 | 能否进 MIT 主库 |
|---|---|---|---|---|
| `stellarium` | 桌面星象仪（C++/Qt） | GPL-2.0（`stellarium/COPYING`） | S1–S10、S14、I1 | 否；只读思想 |
| `stellarium-web-engine` | Web 星图引擎（C/JS） | AGPL-3.0（`stellarium-web-engine/LICENSE-AGPL-3.0.txt`） | S1、S2、S6、S7 | 否；AGPL 更强 |
| `sdrpp` | SDR++ 桌面 SDR | GPL-3.0（`sdrpp/license`） | P1–P16、H1–H4、C1–C3 | 否；只读 |
| `gqrx` | Qt SDR 接收机 | GPL-3.0（`gqrx/COPYING`） | P3、P5、P9、P11、P12、H5、C4、C5 | 否；只读 |
| `gpredict` | 卫星追踪/过境/旋转器（C/GTK） | GPL-2.0（`gpredict/COPYING`） | T1、T3、T6、T7、X4、C6 | 否；只读 |
| `satdump` | 卫星数据处理（C++） | GPL-3.0（`satdump/LICENSE`） | H7、H8、D1、D2、T2、T4 | 否；只读 |
| `onair` | 多模式 SDR 接收机（C++/Kotlin） | GPL-3.0（`onair/LICENSE`） | H6、D7、T1、T5 | 否；只读 |
| `goestools` | GOES 接收/解调/解码（C++） | BSD-2-Clause（`goestools/LICENSE`） | D3 | 可入（保留声明），但未复制 |
| `noaa-apt` | NOAA APT 解码（Rust） | GPL-3.0（`noaa-apt/LICENSE`） | D5 | 否；只读 |
| `meteor_demod` | Meteor LRPT 解调（C） | MIT（`meteor_demod/LICENSE`） | D6 | 可入，未复制 |
| `xrit-rx` | LRIT/HRIT 处理（Python） | MIT（`xrit-rx/LICENSE`） | D4 | 可入，未复制 |
| `rtl_433` | ISM 设备解码（C） | GPL-2.0（`rtl_433/COPYING`） | D8 | 否；只读 |
| `openngc` | NGC/IC 星表数据（CSV） | CC-BY-SA-4.0（上游 README；本地目录无 LICENSE 文件） | 天空数据 | 数据可入；须署名 + 同许可 |
| `sigmf` | IQ 记录元数据规范 | CC-BY-SA-4.0（`sigmf/LICENSE.md`） | 记录格式 | 事实可用；规范文本不整段拷贝 |
| `dvbs2_bch_external` | gr-dvbs2rx 上游源码 | GPL-3.0（`.../upstream_rx/COPYING`） | D9 | 否；只做向量对拍 |
| `dvbs2_ldpc_external` | gr-dvbs2 上游源码 | GPL-3.0（`.../upstream/COPYING`） | D9 | 否；只做向量对拍 |
| `wsjtx` | 仅解码样本/证据（无源码） | 上游 WSJT-X 为 GPL-3.0；目录内无 LICENSE（**推断，未验证**） | 对拍证据 | 否 |
| `wspr_audio` | WSJT-X 的 wsprsim/wsprd 源码 | 上游 WSJT-X 为 GPL-3.0；目录内无 LICENSE（**推断，未验证**） | D10 | 否；只读 |

**约束结论**：GPL-2.0/3.0/AGPL-3.0 只允许「读思想、写自己的表达」，不得复制源码、注释、字符串表、数据文件进 `mbdsdr_ai/` 或 `desktop/`；CC-BY-SA-4.0 的数据（OpenNGC）可随仓，但必须保留署名并对其衍生数据保持同许可。执行细节见 `LICENSING.md` §7。

### 8.2 天空 / 星图渲染：既有条目映射

| 机制 | 参考位置（见 §2 对应行） | 我们现状 | 结论 |
|---|---|---|---|
| 摄像机锁定 | S1（`StelMovementMgr.cpp:319-321`；`navigation.c:87-109`） | `desktop/rf_sky_view.py:1351-1359` | 已对齐 |
| goto 飞行路径 | S2（`StelMovementMgr.cpp:1482-1527`；`navigation.c:44-82`） | `mbdsdr_ai/sky_interaction.py:108-140` | 未实现（建议开工） |
| 天顶奇异 | S3（`StelMovementMgr.cpp:1494-1521`） | `sky_interaction.py:171-175` | 未实现 |
| 消光 | S4（`RefractionExtinction.cpp:35-61`） | `skyengine/stars.py:196-207`（`k/sin`） | 部分对齐（双份公式，见 §14） |
| 折射 | S5（`RefractionExtinction.cpp:64-101`） | `celestial_geometry.py:402-405` 无折射 | 未实现（默认关） |
| 标签避让 | S6（`labels.c:128-198`） | `rf_sky_view.py:357-376` | 部分对齐（无淡入淡出/面积阈值） |
| 标签优先级 | S7（`labels.c:136-154`） | `rf_sky_view.py:3147-3150` | 有意不抄（现分级已够） |
| 星等→半径 | S8（`StelSkyDrawer.cpp:403-417`） | `skyengine/stars.py:150-187` | 已对齐 |
| 视大小符号 | S9（`Planet.cpp:3904`） | `rf_sky_view.py:4093-4116` | 已对齐 |
| 地景 | S10（`Landscape.cpp:1030-1046`） | `rf_sky_view.py:3323-3327` | 已对齐 |
| 脚本导览 | S11（`messier_marathon.ssc:27-51`；`StelScriptMgr.cpp:927-936`） | 无 | 未实现 |
| 卫星地影 | S12（`gpredict/src/sat-vis.c:82-105`） | **已补齐**：`mbdsdr_ai/sat_illumination.py:204-303`；`sat_passes.py:441-446` | 已对齐（本轮更正 S12） |
| 卫星极图 | S13（`gtk-polar-view.c:202-246`） | `rf_sky_view.py:2841` 兼容接口 | 有意不抄（仅小部件可用） |
| 时间倍率 | S14（`StelCore.cpp:1386-1413`） | `new_spacetime.py:142-190` | 已对齐 |

**S12 更正细节**：上一轮记为「无」，本轮核实已实现。`sat_illumination.py:78-80` 定义 `sunlit/umbra/penumbra` 三态，`:204` 起为圆锥影判定；`sat_passes.py:441-446` 明确注明「与 gpredict `Sat_Eclipsed` 同构」，并把半影按受晒处理以保持二值语义。与 gpredict 的差异只有一处：gpredict 还叠加「太阳高度 ≤ 晨昏门限」的肉眼可见判定（`sat-vis.c:92-105`），**有意不抄**——本产品不做肉眼观测预报，只回答卫星是否受晒。

### 8.3 频谱渲染与瀑布：既有条目 + P16

P1–P15 覆盖刻度、dB 量程、峰值、hold、调色板、缩放锚定、频段标签、书签堆放、VFO SNR/snap。新增一条本轮核到的机制：

| # | 机制 | 参考位置 | 我们对应 | 差异一句话 | 结论 |
|---|---|---|---|---|---|
| P16 | 瀑布历史环形缓冲（可调深度、回看重绘） | `sdrpp/core/src/gui/widgets/waterfall.cpp:611-616`（`((i+currentFFTLine)%waterfallHeight)`）；`waterfall.h:284-288`（`rawFFTs` 裸指针 + `latestFFT`/`latestFFTHold`） | `desktop/spectrum_widget.py:239-243`（`_wf_buf/_wf_head/_wf_count`），`:302-346`（按行回看 + `set_waterfall_depth` 后重建） | 同一环形缓冲语义；SDR++ 用手工裸指针与 `recursive_mutex`，我们用 numpy 定长数组 | 已对齐；**不抄**其手工缓冲/递归锁（见 §14） |

**频谱侧不再新增行**：P1–P15 的参考位置与差异已在本文件 §3 逐条钉过，且本轮复核代码未漂移（`desktop/spectrum_widget.py` 行号仍落在原区间）。

---

## 9. 设备抽象与热插拔（H）

| # | 机制 | 参考位置 | 我们对应实现 | 差异一句话 | 结论 |
|---|---|---|---|---|---|
| H1 | 统一信号源注册表（注册/选择/注销） | `sdrpp/core/src/signal_path/source.h:9-31`；`source.cpp:10-60` | `mbdsdr_ai/device_manager.py:367`（`DeviceManager`）、`:492-528`（`list_devices`）、`:530`（`open`）；`mbdsdr_ai/hal.py:49-59`（`DeviceCapability`） | SDR++ 用函数指针 handler + 全局 selected；我们 `open()` 返回可替换后端对象，失败抛异常 | 已对齐（抽象等价）；不抄全局单例选择 |
| H2 | 注销当前源时回落 `nullSource` | `sdrpp/core/src/signal_path/source.cpp:19-33` | `device_manager.py:530`（打开失败抛错）+ `device_watchdog.py:51-186`（自动重连/状态机） | SDR++ 设备消失后静默换成空源继续跑；我们把「设备消失」当错误上报并重连 | **有意不抄**：空源会让 UI 继续显示「在接收」而实际无数据，属于假阳性；本项目选择失败可见 |
| H3 | 设备枚举与刷新 | `sdrpp/source_modules/rtl_sdr_source/src/main.cpp:117-122`（`refresh` 只由菜单触发）、`:164-177`（`selectFirst`）、`:394-395`（Refresh 按钮） | `device_manager.py:608-640`（`start_monitor` 后台轮询，回调 `added/removed`） | SDR++ 手动刷新；我们 2s 轮询热插拔 | 已对齐并超出（具体差异：自动轮询 vs 手动按钮） |
| H4 | 单设备打开失败隔离 | `sdrpp/source_modules/rtl_sdr_source/src/main.cpp:185-195`（`rtlsdr_open` 失败仅 `flog::error`，不崩） | `device_manager.py:530-600`（逐设备 open/异常）；`device_watchdog.py` 重连 | 同等隔离；我们多一层自动重连 | 已对齐 |
| H5 | 设备错误要求重启应用 | `gqrx/src/applications/gqrx/mainwindow.cpp:602-611`（「reconnect … and restart gqrx」）；`:364`（仅启动时 `getDeviceList`） | `device_watchdog.py:106-160`（断线→重连循环） | 上游把恢复推给用户；我们自动重连 | **有意不抄**：要求重启是可用性缺陷；仅当设备被物理占用时保留「请重插」提示 |
| H6 | USB attach/detach 事件 + 补扫 | `onair/android/app/src/main/java/io/github/adswill/onair/usb/UsbRadioController.kt:68`（`rescan`）、`:104-107`（detach→`onLost`） | `device_manager.py:608-640` 轮询 | 事件驱动 vs 轮询 | **有意不抄**：Android USB/JNI 事件模型不适用于 Windows/SoapySDR；轮询已覆盖同一语义 |
| H7 | 设备表 + 模式枚举 + 歧义抛错 | `satdump/src-core/dsp/device/dev.h:11-21`（`DeviceInfo`）、`:30-36`（`MODE_SINGLE_RX/TX/RX_TX`）；`dev.cpp:9-31`（`getDeviceList`，>1 实例即 `throw "Got more than one device instance"`） | `device_manager.py:58-81`（`DeviceInfo.device_key` = `driver:serial` 去重）；`hal.py:49-59`（能力位） | 上游按静态 mode 枚举；我们按能力位 + 运行时 `tx_capability()["verified"]` | 已对齐（歧义同样失败）；不抄静态 mode 假设 |
| H8 | `DeviceInfo` 相等性比较 | `satdump/src-core/dsp/device/dev.h:18`（`(name == b.type)`，应为 `b.name`） | `device_manager.py:58-81` 用 `device_key` | 上游实现缺陷：名字与类型错位比较 | **有意不抄**（上游 bug，去重/判重会误判） |

---

## 10. 解码流水线（D）

| # | 机制 | 参考位置 | 我们对应实现 | 差异一句话 | 结论 |
|---|---|---|---|---|---|
| D1 | CCSDS 卷积+RS 解码链的质量门与统计 | `satdump/src-core/pipeline/modules/ccsds/module_ccsds_conv_concat_decoder.cpp:105-118`（`viterbi_ber_thresold`/`outsync` 参数）、`:180-196`（RS 每块 `errors[i]==-1` 即判帧无效）、`:202-213`（`viterbi_ber`/`lock`/`deframer_state` 统计） | `mbdsdr_ai/lrpt_fec.py:298-350`（跳 ASM→解扰→4 路解交织→RS→4 块全可纠才有效）；`lrpt_pipeline.py:37-45`（`sync_hamming`/`rs_errors`） | 帧有效性门已对齐；上游额外暴露 Viterbi BER/锁态/定界状态，我们只有同步汉明距离与每块纠错数 | **部分对齐 / 未实现**（建议补 stats，不改解码算法） |
| D2 | Viterbi 失锁与 BER 阈值作为参数 | `satdump/.../module_ccsds_conv_concat_decoder.cpp:105-118` | `mbdsdr_ai/lrpt_fec.py:151`（整帧一次性 Viterbi） | 我们离线整帧，没有「实时失锁」概念 | **有意不抄**：批处理链引入阈值只会产生不可解释的丢帧 |
| D3 | 预暖机 + 重编码统计纠正位数 | `goestools/src/decoder/packetizer.cc:11-20`（frame prelude 预暖 Viterbi）、`:129-136`（重编码后统计纠正比特） | `lrpt_fec.py:151-190`（从同步字起整帧解码） | 我们没有 prelude 暖机，也没有「纠正位数」质量指标 | **部分未实现**：暖机仅影响首帧边缘（低优先）；corrected-bit 指标可并入 D1 |
| D4 | 虚拟信道（VCID）路由/黑名单/填充包丢弃 | `xrit-rx/src/demuxer.py:48-111`（线程+队列、VCID 分流、填充包丢弃、未知航天器显式跳过） | `mbdsdr_ai/lrpt_pipeline.py:48-165`（单 VCID 端到端） | 我们没有多虚拟信道分流与「未知平台」显式拒绝 | **未实现**（仅多 VCID 场景有价值；当前只做 LRPT，暂不引入） |
| D5 | 全文件同步帧对齐（≥5 帧） | `noaa-apt/src/decode.rs:106-118`（同步帧不足 5 个即报错，不输出图） | `mbdsdr_ai/noaa_apt_lite.py:23-51`（逐行相关同步） | 上游两遍扫描全文件再统一定位；我们流式逐行对齐 | **有意不抄**：整文件两遍扫描不适合实时流；逐行相关已有单测 |
| D6 | AGC→Costas→Gardner 闭环解调 | `meteor_demod/demod.c:8-12`（PLL 初始化）、`:36-40`（AGC→混频→鉴相） | `lrpt_pipeline.py:119-127` + `lrpt_modem.py`（前馈 CFO + 符号恢复） | 功能等价，路线不同；闭环 Gardner 已实验证伪（COORDINATION LRPT 队列#9） | 已对齐；不抄闭环结构 |
| D7 | FEC API（RS(204,188)、交织、纠错计数） | `onair/core/include/dect2/fec/fec_proto.h:13-20` | `lrpt_fec.py:246-350`；`mbdsdr_ai/dvbs2_bch.py` | 同等 RS 语义与每块纠错数；我们额外做 CCSDS 4 路解交织 | 已对齐（独立实现） |
| D8 | 分级解码器 + 失败原因计数 | `rtl_433/include/r_device.h:73`（`priority` 字段）、`:85-87`（`decode_ok`/`decode_fails[5]`）；`rtl_433/src/r_api.c:443-454`（低优先级仅在无更优事件时运行）、`:867-874`（失败原因计数） | `mbdsdr_ai/decoder_registry.py:119`（`select_by_frequency` 单选解码器） | 上游允许同频多协议竞争；我们单车道 | **有意不抄**：盲试多协议会产生误报，违反「不伪造解码结果」红线；失败计数可借（低优先） |
| D9 | GPL 上游源码作 KAT 生成器 | `dvbs2_bch_external/upstream_rx/`；`dvbs2_ldpc_external/upstream/` | `mbdsdr_ai/dvbs2_bch.py`、`dvbs2_ldpc.py`（独立实现） | 只使用测试向量，不复制实现 | **有意不抄代码**；金样在 `tests/golden/` |
| D10 | WSPR 模拟器/证据 | `wspr_audio/refsrc/wsprsim.c`；`wsjtx/` 仅样本 | `mbdsdr_ai/wspr_*`/FT8 解码 | 证据链而非机制来源 | 不适用（许可证见 §8.1） |

**解码侧最重要的缺口**是 D1 的「锁态/BER 统计」：我们有 `sync_hamming`，但没有把 Viterbi 的软判决置信度与失锁状态暴露给 UI，导致「为什么这帧没出来」只能靠 RS 结果反推。

---

## 11. 卫星追踪与过境（T）

| # | 机制 | 参考位置 | 我们对应实现 | 差异一句话 | 结论 |
|---|---|---|---|---|---|
| T1 | 过境预测（AOS/LOS/TCA/maxEl） | `gpredict/src/predict-tools.c:413-421`（最小仰角取配置，0→1°）、`:129-160`（AOS 搜索）；`onair/core/src/geo/passes.cpp:248-292`（20s 扫描 + 0.5s 二分 + 黄金分割求 TCA）；`satdump/.../scheduler/passes.cpp:62-72`（AOS/LOS + 50 等分求最大仰角） | `mbdsdr_ai/orbit.py:420-448`（`_bisect_threshold`）、`:474-475`（60s 粗扫/2s 细扫）、`:559-607`；`sat_tracker.py:598` 起 `next_pass` | 我们 60s 粗扫 + 2s 细扫 + 二分；OnAir 20s + 黄金分割；SatDump 50 等分且不细化 | 已对齐（同解）；不抄 SatDump 的 50 等分（精度低于我们的 2s 细扫） |
| T2 | 多星自动调度与冲突仲裁 | `satdump/src-core/common/tracking/scheduler/scheduler.cpp:183`（按最小仰角过滤）、`:192`（选择过境）；`scheduler/passes.cpp:146-183`（逐秒取最大仰角，旧算法 `:129-145` 已注释） | 无调度器；单星、由用户/AI 显式触发（`sat_tracker.py`） | 上游多星自动切换；我们不做自动切换 | **有意不抄**：(a) 当前无旋转器硬件；(b) 自动切换=预制流程，违反「追踪只由用户显式触发」的既定红线（COORDINATION 11-04） |
| T3 | 旋转器机械限位与过境后归位 | `gpredict/src/gtk-rot-ctrl.c:787-788`（`SAFE_AZI`/`SAFE_ELE` 夹取）、`:796-817`（地平线下归位到 AOS/LOS 方位） | `mbdsdr_ai/gimbal.py:79-82`（`GimbalStatus.az_min/az_max/el_min/el_max` 声明）、`:162`/`:206`/`:293`（实际硬编码 `0-90`） | 上游用配置的机械限位并在地平线下归位；我们的限位字段存在但 `move()` 未使用 | **部分对齐 / 未实现**（本轮最实的缺口之一，建议开工） |
| T4 | 旋转器通信失败关闭 | `satdump/src-core/common/tracking/rotator/rotcl_handler.h:16`（`MAX_CORRUPTED_CMD=3`）、`rotcl_handler.cpp:105-136`（连续 3 条损坏指令即停止） | `gimbal.py` 无损坏计数 | 上游失败即停；我们无限重试 | **未实现**（建议补，纯计数，但会改变现有重试语义） |
| T5 | TLE 新鲜度与重试节流 | `onair/core/src/geo/passes.cpp:296-306`（`tleUpdateDue`：1 天成功间隔 / 6 小时失败重试） | `mbdsdr_ai/tle_integrity.py:178`（`freshness`）、`:244`（`accuracy`）；`orbit.py:459-465`（`allow_stale` + `degraded`） | 上游「到点才抓取」；我们「读取时评估并如实降级标注」，不自动抓 | 已对齐（我们选择不隐藏过期 TLE） |
| T6 | 多普勒频移 | `gpredict/src/gtk-sat-list.c:626-631`（`-f*v/c`）；`onair/core/include/dect2/geo/passes.h:56-57`（`dopplerHz`） | `sat_tracker.py` 的多普勒修正 + `sat_passes.py` 的 `range_rate` | 同一公式；我们仅在追踪开启时下发到电台 | 已对齐 |
| T7 | 受晒/地影判定 | `gpredict/src/sat-vis.c:82-105`（`Sat_Eclipsed` + 晨昏门限） | `sat_illumination.py:204-303`（三态）；`sat_passes.py:441-446` | 我们三态（本影/半影/受晒）；不叠加肉眼可见门限 | 内核已对齐；**有意不抄**可见过境判定 |
| T8 | Stellarium 卫星插件 | 本克隆无 `Satellites` 插件目录（全仓目录检索 0 命中） | — | 无过境/地影机制可对照 | 不适用（**未找到 ≠ 不存在**） |

---

## 12. 配置与持久化（C）

| # | 机制 | 参考位置 | 我们对应实现 | 差异一句话 | 结论 |
|---|---|---|---|---|---|
| C1 | 坏配置恢复 | `sdrpp/core/src/config.cpp:34-43`（解析异常→`conf=def`→立即覆盖写） | `desktop/settings.py:350-356`（先备份 `*.corrupt-时间戳` 再回默认） | 上游直接覆盖丢失原文件；我们先留证据 | 已对齐；**有意不抄**其破坏性恢复 |
| C2 | 原子写 | `sdrpp/core/src/config.cpp:47-52`（直接 `ofstream` 覆盖） | `desktop/settings.py:454-462`（写 `.tmp` + `os.replace`） | 我们不做非原子写 | **有意不抄**（上游是反例） |
| C3 | 定时自动保存线程 | `sdrpp/core/src/config.cpp:82-100`（1s `try_lock` + 写盘） | `desktop/settings.py` 按需 `flush` | 上游每秒尝试写；我们变更后写 | **有意不抄**：无收益且与原子写重复 |
| C4 | 崩溃自检与配置重载提示 | `gqrx/src/applications/gqrx/mainwindow.cpp:500-521`（`crashed` 标志 + 询问是否重载配置） | 无 | 上游能识别上次非正常退出；我们没有 | **未实现**（可选项，低优先；原子写+备份已降低丢配置概率） |
| C5 | 默认值不落盘 | `gqrx/src/qtgui/dockfft.cpp:238-324`（值等于默认则 `settings->remove(key)`） | `desktop/settings.py` 全量落盘 | 上游保持配置文件最小；我们全量写 | **有意不抄**：全量写更利于单一事实源、审计与逐键迁移 |
| C6 | 版本化迁移 | `gpredict/src/sat-cfg.c:302-330`（1.1→1.4 分支重写键） | `desktop/settings.py:250-258`（`_coerce`）、`:334-338`（`migration_notes`） | 文件级版本块 vs 逐键类型迁移 | 已对齐（语义等价，粒度更细） |
| C7 | 分段 JSON 配置 | `satdump/src-core/common/tracking/sat_finder/sat_finder_ui.cpp:107`（`db->get_user_json("satellite_finder")` 按模块存） | 单一 `settings.json` + schema | 上游每模块独立 blob；我们集中 | **有意不抄**：集中式更易做原子写/迁移/审计 |

---

## 13. TX 安全闸（X）

| # | 机制 | 参考位置 | 我们对应实现 | 差异一句话 | 结论 |
|---|---|---|---|---|---|
| X1 | 参考项目的 TX 能力 | `sdrpp/sink_modules/` 仅有 `audio_sink`/`network_sink` 等（无发射）；`gqrx`、`onair`、`satdump` 全仓 `transmit/ptt/interlock` 检索无命中 | — | 参考项目均为接收链，没有可对照的 TX 闸 | **不适用**（无实现可抄） |
| X2 | 人工确认 → 一次性 token → 有效期 → 审计 | 无（参考项目没有） | `mbdsdr_ai/tx_guard.py:543-597`（`arm`：TTL/动作绑定/单次）、`:599-620`（`revoke`/过期）、`:622` 起（`authorize`）、`:441-484`（审计记录）；`hal.py:721-741`（CW 路径）、`:765-779`（IQ/调制路径）均先 `authorize` 再 `write_tx` | 自研；无参考对照 | 已有，自研（本轮只登记「对照缺席」） |
| X3 | 能力声明 | `satdump/dev.h:30-36`（静态 `MODE_SINGLE_TX` 枚举） | `hal.py:139-141`（`supports_tx()` + `tx_capability()["verified"]`）；`docs/limitations-register.md:221-241`（未验证前不得显示「已验证」） | 上游静态声明；我们运行时验证后才置位 | 已对齐；**不抄**静态假设（避免把「声明」当「能力」） |
| X4 | 执行器安全（旋转器限位/失败关闭） | `gpredict/gtk-rot-ctrl.c:787-817`；`satdump/rotcl_handler.h:16` + `.cpp:105-136` | `gimbal.py:79-82` 声明、实际未接线；无损坏计数 | 上游两套执行器保护；我们只硬编码 0–90 | **未实现**（见 T3/T4；建议开工） |
| X5 | 发射波形模拟器 | `wspr_audio/refsrc/wsprsim.c`（离线生成波形，无硬件门） | — | 模拟器不是安全机制 | **有意不抄**：不能把「能生成波形」误当「可安全发射」 |

---

## 14. 明确别学的历史包袱

| 项目 | 包袱 | 证据 | 为什么别学 |
|---|---|---|---|
| Stellarium | 消光公式在 C++ 与 GLSL 各写一份 | S4；`RefractionExtinction.cpp:35-61` + `skyengine/stars.py` 同式 | 双份数学源必然漂移；我们只保留一处并复用 `atmosphere.py` |
| SDR++ | 设备消失静默回落 `nullSource` | `source.cpp:19-33` | UI 会继续显示「在接收」但无数据，制造假阳性 |
| SDR++ | 瀑布手工裸指针 + `recursive_mutex` + 注释掉的 `vector` 方案 | `waterfall.h:250-288` | 内存与并发风险；我们已用 numpy 定长环形数组 |
| SDR++ | 非原子配置写 + 坏文件直接覆盖 | `config.cpp:34-52` | 崩溃时丢配置；我们已用 `.tmp`+`replace`+备份 |
| SDR++ | 设备只在菜单点 Refresh 时重扫 | `rtl main.cpp:117-122,394-395` | 热插拔需要用户干预；我们已后台轮询 |
| gqrx | 设备错误要求重启应用 | `mainwindow.cpp:602-611` | 把恢复成本推给用户；我们自动重连 |
| gqrx | 各 dock 分散 `QSettings` + 默认值手写 `remove` | `dockfft.cpp:238-324` | 配置面难以审计；我们集中 schema |
| SatDump | `DeviceInfo` 相等性写成 `name == b.type` | `dev.h:18` | 上游 bug，设备比较会错位 |
| SatDump | 过境最大仰角只做 50 等分采样 | `passes.cpp:62-72` | 峰值时刻误差可达分钟级；我们 2s 细扫 + 二分 |
| SatDump | 多星调度逐秒 × 全过境表扫描（旧实现还留注释） | `passes.cpp:129-183` | O(秒数×过境数)，且业务上是预制流程，我们不需要 |
| gpredict | 只做实时时间（无时间倍率/暂停/倒放） | 本文件 S14 对照 | 我们需要复盘与时间快进；已自研 `new_spacetime.py` |
| OnAir | Android USB/JNI 事件式热插拔 | `UsbRadioController.kt:68,104-107` | 平台专属，Windows/SoapySDR 不适用 |
| noaa-apt | 全文件两遍扫描同步 | `decode.rs:106-118` | 不适合实时流；我们逐行同步 |

---

## 15. 本轮可立即开工的 5 条

1. **旋转器机械限位接线**（T3）：把 `GimbalStatus.az_min/az_max/el_min/el_max` 接进 `gimbal.py` 的 `move()/stop()`，并补过境前/后归位。验收：越界命令被夹取且记录原因；3 个边界用例（0°/90°/超范围）通过。
2. **Viterbi 质量统计**（D1）：`lrpt_fec.py` 增加可选 BER/失锁/定界状态，进 `E2EResult`；不改解码输出。验收：同一 IQ 样本解码结果逐字节不变，新增字段可读；无样本时显式 `None`。
3. **旋转器损坏指令失败关闭**（T4）：连续 3 条无效响应即 `stop()` 并置错误态。验收：注入 3 条损坏响应后不再下发指令；恢复需显式重连。
4. **goto 大圆插值**（S2）：起点/目标单位向量 slerp，az/alt 仅重投影。验收：过天顶用例无跳变；现有飞行时长/OutCubic 语义不变。
5. **纠正位质量指标**（D3）：对齐 goestools 的「重编码统计纠正位数」，作为 D1 的补充字段。验收：已知无误码样本纠正位=0；注入 N 位错误后计数与注入一致（±1）。

> 下一批（不进本轮）：D4 VCID 路由、C4 崩溃自检、P11 多频段气泡、S6 标签淡入淡出、X3 能力声明 UI 复核。

---

## 16. 本轮验证边界

- 许可证均以仓库内 `LICENSE/COPYING` 首段为准；`openngc` 本地无许可证文件，按其上游 README 记为 CC-BY-SA-4.0；`wsjtx`、`wspr_audio` 目录内无许可证文件，按上游 WSJT-X 推断为 GPL-3.0（**推断，未验证**）。
- `stellarium` 本克隆无卫星插件目录：结论是「本克隆未找到」，不代表上游没有。
- TX 的「无命中」是限定目录的文本检索结果（`sdrpp/`、`gqrx/`、`onair/`、`satdump/`），不代表这些项目在所有分支都没有发射能力。
- 本轮只读参考源码与自家代码，未运行任何测试；所有「已对齐」均指实现位置与语义可对照，不是行为等价证明。

---

## 17. 「照着做，还是不做」判定表（2026-11-06）

> 判定口径：**逐条裁决**，不写「整体已对齐」。
> - 「我们现状」的提交号是**行级 blame**（`git blame -L n,n -- 文件` 的首个提交），表示当前这一行由该提交引入或最后改写，不等于该机制的最初引入提交。
> - 「决定」三态：**照做**（机制已按参考思路落地，保持）／**改良后做**（路线不同或只落一半，已落地或已排给某条线）／**明确不做**（给理由与可追踪的挂账/验收门）。
> - 本表是裁决，**覆盖 §15 的候选清单**：S2、D1、D3 从「建议开工」改为「明确不做（本轮）」，理由写在对应行。

### 17.1 主表：§8–§16 共 53 条

#### 天空 / 星图（S1–S14）

| 机制 | 参考做法（文件:符号） | 我们现状（提交号 + 位置 / 缺什么） | 决定与动作 |
|---|---|---|---|
| S1 摄像机锁定 | `StelMovementMgr.cpp:319-321`（`flagLockEquPos`）；`navigation.c:87-109`（slerp） | 已落地：`587d55da` → `desktop/rf_sky_view.py:1351-1359` 锁定态、`:1464-1467` 33ms tick、`:1605-1606` 重算+指数趋近 | **照做**（保持） |
| S2 goto 大圆插值 | `StelMovementMgr.cpp:1482-1527`（aim 向量 + 统一 speed）；`navigation.c:44-82`（smoothstep） | 未做：`sky_interaction.py:108-140` 仍是 az 最短弧 + alt 线性 | **明确不做（本轮）**：相机锁定路径每 tick 重算目标，goto 多为短时过渡；改 slerp 会改 160° 全天投影截图基线（`944d8b66`），近天顶又无夹具 |
| S3 天顶/天底奇异 | `StelMovementMgr.cpp:1494-1521`（从 up 向量解 az、起点 alt 退 1e-4°） | 未做：`sky_interaction.py:171-175` 只夹 alt | **明确不做**：极点 az 无物理意义且目标在 FOV 外；与 S2 同批才会做 |
| S4 消光公式 | `RefractionExtinction.cpp:35-61`（Rozenberg / Young） | 已落地但双份：`179ce20d` → `skyengine/stars.py:196-207` 用 `k/sin(alt)`，`atmosphere.py:170-190` 另有 Kasten-Young，渲染未用 | **明确不做（本轮）**：10° 以上差异 <0.03 mag，5° 约 0.2 mag 且被 3.5 等截断吸收；统一要改截图基线，观测上不可见 |
| S5 大气折射 | `RefractionExtinction.cpp:64-101` | 未做：`celestial_geometry.py:402-405` 无折射 | **明确不做**：跟踪/指向必须真高度；绘制加折射会让点亮判定与跟踪口径分叉，`test_refraction.py` 已 pin 公式 |
| S6 标签避让 | `labels.c:128-145`（面积阈值）、`:162-198`（淡入+掩星即隐） | 部分落地：`591d49fe` → `rf_sky_view.py:357-376` 二维布尔剔除、`:3288-3318` 统一 flush；无面积阈值/淡入 | **明确不做**：布尔剔除已保证不重叠；淡入会让无头截图出现中间态，破坏像素回归 |
| S7 标签优先级 | `labels.c:136-154`（priority + 远近排序） | 已落地：`rf_sky_view.py:3147-3150` 按星等/类型分级 | **明确不做**：现有分级已覆盖遮挡优先级，引入 priority 字段要重标全部标签 |
| S8 星等→半径/亮度 | `StelSkyDrawer.cpp:403-417`（<0.3px 丢弃、1.2px 防闪） | 已落地：`e5ca57ce` → `skyengine/stars.py:150-187` | **照做**（保持） |
| S9 视大小符号 | `Planet.cpp:3904`（`screenRd = angularRadius * pixelPerRad`） | 已落地：`9645d406` → `rf_sky_view.py:4093-4116`、`:4177` | **照做**（保持） |
| S10 地平线/地景 | `Landscape.cpp:1030-1046`（ground/decor/fog 顺序） | 已落地：`a4611eae` → `rf_sky_view.py:3323-3327` 地景 alpha | **照做**（保持；不抄全景贴图/fog） |
| S11 脚本化导览 | `messier_marathon.ssc:27-51`；`StelScriptMgr.cpp:927-936` | 无 tour/步骤调度（全仓 0 命中） | **明确不做**：演示流程不是产品能力；产品已有「锁定目标+时间倍率」可手动复现；引入脚本引擎扩大攻击面与维护面 |
| S12 卫星地影 | `sat-vis.c:82-105`（`Sat_Eclipsed` + 晨昏门限） | 已落地：`3fc46b98` → `sat_illumination.py:204-303`；`sat_passes.py:441-446` | **照做**（保持；不叠加肉眼可见门限，见 T7） |
| S13 卫星 az/el 极图 | `gtk-polar-view.c:202-246`（`r*(1-el/90)` + 方向枚举） | 已落地兼容接口：`rf_sky_view.py:2841`；主视图走投影 | **明确不做主视图**：投影已够；只有「方向枚举」可给小部件，当前无该部件 |
| S14 时间倍率/暂停/倒放 | `StelCore.cpp:1386-1413` | 已落地：`a7c7e410` → `new_spacetime.py:142-190` | **照做**（保持） |

#### 频谱 / 瀑布（P16）

| 机制 | 参考做法 | 我们现状 | 决定与动作 |
|---|---|---|---|
| P16 瀑布环形缓冲 | `sdrpp/core/src/gui/widgets/waterfall.cpp:611-616`（`(i+current)%height`）；`waterfall.h:284-288`（裸指针+`recursive_mutex`） | 已落地：`82e32da2` → `spectrum_widget.py:239-243`（`_wf_buf/_wf_head/_wf_count`）、`:302-346` 回看与深度重建 | **照做**（保持；不抄裸指针/递归锁，见 §14） |

#### 设备抽象与热插拔（H1–H8）

| 机制 | 参考做法 | 我们现状 | 决定与动作 |
|---|---|---|---|
| H1 统一信号源注册表 | `sdrpp/core/src/signal_path/source.h:9-31`；`source.cpp:10-60` | 已落地：`d4ebd5bf` → `device_manager.py:367`、`:492-528`、`:530`；`hal.py:49-59` 能力位 | **照做**（保持；不抄全局单例选择） |
| H2 注销回落空源 | `source.cpp:19-33`（`nullSource`） | 已落地相反策略：`d4ebd5bf` → `device_manager.py:530` 抛错 + `device_watchdog.py:51-186` 重连 | **明确不做**：空源会让 UI 显示「在接收」而实际无数据，是假阳性 |
| H3 设备枚举/刷新 | `rtl_sdr_source/src/main.cpp:117-122` 手动 `refresh`、`:394-395` 按钮 | 库层已落地：`d4ebd5bf` → `device_manager.py:608-640` 2s 轮询；**`desktop/` 对 `start_monitor` 引用 0 处** | **改良后做**：库层保留轮询；桌面接线挂账 `docs/limitations-register.md:555-559`（L48），验收门 `tests/test_desktop_hotplug_autorecovery.py:68` 的 `xfail(strict=True)` |
| H4 单设备打开失败隔离 | `rtl main.cpp:185-195`（失败仅 `flog::error`） | 已落地：`d4ebd5bf` → `device_manager.py:530-600` 逐设备异常 | **照做**（保持） |
| H5 设备错误要求重启 | `gqrx/src/applications/gqrx/mainwindow.cpp:602-611`、`:364` | 已落地相反策略：`device_watchdog.py:106-160` 自动重连 | **明确不做**：把恢复推给用户是可用性缺陷 |
| H6 USB 事件式热插拔 | `UsbRadioController.kt:68`、`:104-107` | 已落地替代：`d4ebd5bf` → `device_manager.py:608-640` 轮询 | **明确不做**：Android USB/JNI 事件模型不适用 Windows/SoapySDR |
| H7 设备表 + 歧义抛错 | `satdump/.../dev.h:11-21`、`:30-36`；`dev.cpp:9-31`（>1 实例即 throw） | 已落地：`d4ebd5bf` → `device_manager.py:58-81`（`device_key=driver:serial`）；`hal.py:49-59` | **照做**（保持；不抄静态 mode） |
| H8 `DeviceInfo` 相等性 | `dev.h:18`（`name == b.type`） | 未做（也不做）：`device_manager.py:58-81` 用 `device_key` | **明确不做**：上游实现缺陷，去重会错位 |

#### 解码流水线（D1–D10）

| 机制 | 参考做法 | 我们现状 | 决定与动作 |
|---|---|---|---|
| D1 CCSDS 质量门 + BER/锁态 | `module_ccsds_conv_concat_decoder.cpp:105-118`、`:180-196`、`:202-213` | 门已落地：`7d04a3de` → `lrpt_fec.py:298-350`；`lrpt_pipeline.py:37-45` 只有 `sync_hamming/rs_errors`，无 BER/锁态 | **明确不做（本轮）**：批处理离线链没有实时失锁消费点；现有两字段已够解释失败；加字段要同时改 E2E 契约与 UI，收益只在离线诊断（推翻 §15 第 2 条） |
| D2 Viterbi 失锁阈值参数 | 同文件 `:105-118` | `lrpt_fec.py:151` 整帧一次性 Viterbi | **明确不做**：批处理引入阈值只会产生不可解释的丢帧 |
| D3 预暖机 + 纠正位数 | `goestools/src/decoder/packetizer.cc:11-20`、`:129-136` | `lrpt_fec.py:151-190` 从同步字起整帧；无纠正位统计 | **明确不做**：prelude 只影响首帧边缘；纠正位数可由 `rs_errors` 推导，无 UI 消费方（推翻 §15 第 5 条） |
| D4 VCID 路由/黑名单/填充包 | `xrit-rx/src/demuxer.py:48-111` | 未做：`lrpt_pipeline.py:48-165` 单 VCID 端到端 | **明确不做**：产品只做 LRPT 单虚拟信道，多 VCID 无需求 |
| D5 全文件同步对齐 | `noaa-apt/src/decode.rs:106-118`（≥5 帧） | 已落地替代：`5d44109c` → `noaa_apt_lite.py:23-51` 逐行相关 | **明确不做**（两遍扫描）：整文件两遍不适合实时流；逐行已有单测 |
| D6 AGC→Costas→Gardner 闭环 | `meteor_demod/demod.c:8-12`、`:36-40` | 已落地替代：`560f6564` → `lrpt_pipeline.py:119-127` 前馈 CFO + 符号恢复 | **改良后做**（已落地）：闭环 Gardner 已实验证伪（COORDINATION LRPT 队列 #9） |
| D7 FEC API（RS(204,188)） | `onair/.../fec_proto.h:13-20` | 已落地：`fd7359e2` → `lrpt_fec.py:246-350`；另做 CCSDS 4 路解交织 | **照做**（保持） |
| D8 分级解码器 + 失败计数 | `rtl_433/include/r_device.h:73`、`:85-87`；`r_api.c:443-454`、`:867-874` | 已落地替代：`390a0f39` → `decoder_registry.py:119` 单车道选择 | **明确不做**（多协议竞争）：盲试会误报，违反「不伪造解码结果」；失败计数可低优先再借 |
| D9 GPL 上游作 KAT 生成器 | `dvbs2_bch_external/upstream_rx/`、`dvbs2_ldpc_external/upstream/` | 独立实现 `mbdsdr_ai/dvbs2_bch.py`、`dvbs2_ldpc.py`；金样 `tests/golden/` | **明确不做**（复制代码）：只对拍向量，见 §17.3 |
| D10 WSPR 模拟器/证据 | `wspr_audio/refsrc/wsprsim.c`；`wsjtx/` 样本 | 证据链 | **不适用**（非机制来源） |

#### 卫星追踪与过境（T1–T8）

| 机制 | 参考做法 | 我们现状 | 决定与动作 |
|---|---|---|---|
| T1 过境预测 | `gpredict/predict-tools.c:413-421`；`onair/.../passes.cpp:248-292`；`satdump/.../passes.cpp:62-72`（50 等分） | 已落地：`69e2c1d1` → `orbit.py:420-448` 二分、`:474-475` 60s 粗扫/2s 细扫；`sat_tracker.py:598` | **照做**（保持；不抄 50 等分，精度低于 2s 细扫） |
| T2 多星自动调度 | `scheduler.cpp:183`、`:192`；`passes.cpp:146-183` | 未做（有意）：无调度器，单星显式触发 | **明确不做**：无旋转器硬件；自动切换违反「追踪只由用户显式触发」（COORDINATION 11-04） |
| T3 旋转器机械限位/归位 | `gtk-rot-ctrl.c:787-788`（`SAFE_AZI/ELE`）、`:796-817`（地平线下归位） | 未接线：`gimbal.py:79-82` 声明 `az_min/az_max/el_min/el_max`，`:181`/`:225`/`:328` 仍硬编码 0–90 | **改良后做（某条线在做）**：旋转器线；工作树已见 `mbdsdr_ai/gimbal.py` 修改、新增 `scripts/hw_acceptance/rotctld_sim.py`、`tests/test_gimbal_rotctld_sim.py` |
| T4 旋转器失败关闭 | `satdump/.../rotcl_handler.h:16`（`MAX_CORRUPTED_CMD=3`）、`.cpp:105-136` | 未做：`gimbal.py` 无损坏计数 | **改良后做（同旋转器线，sim 验收后接）**：连续 3 条无效响应即 `stop()`，恢复需显式重连 |
| T5 TLE 新鲜度/重试 | `onair/.../passes.cpp:296-306`（1 天/6 小时） | 已落地替代：`f7f1d278` → `tle_integrity.py:178`、`:244`；`orbit.py:459-465` 显式降级 | **改良后做**（已落地）：不自动抓，读取时评估并如实降级（不隐藏过期 TLE） |
| T6 多普勒频移 | `gtk-sat-list.c:626-631`；`onair/.../passes.h:56-57` | 已落地：`357076eb` → `sat_tracker.py:583-584`（`doppler_hz`） | **照做**（保持） |
| T7 受晒/地影 | `sat-vis.c:82-105` | 已落地：`3fc46b98` → `sat_illumination.py:204-303`；`sat_passes.py:441-446` | **改良后做**（已落地）：三态（本影/半影/受晒），不叠加肉眼可见门限 |
| T8 Stellarium 卫星插件 | 本克隆无 `Satellites` 目录（全仓 0 命中） | — | **不适用**（未找到 ≠ 不存在） |

#### 配置与持久化（C1–C7）

| 机制 | 参考做法 | 我们现状 | 决定与动作 |
|---|---|---|---|
| C1 坏配置恢复 | `sdrpp/core/src/config.cpp:34-43`（直接覆盖写默认） | 已落地改良：`a96c010f` → `settings.py:350-356` 先备份 `*.corrupt-时间戳` | **改良后做**（已落地）：先留证据再回默认 |
| C2 原子写 | `config.cpp:47-52`（`ofstream` 直接覆盖） | 已落地改良：`357076eb` → `settings.py:454-462`（`.tmp` + `os.replace`） | **改良后做**（已落地；上游是反例） |
| C3 定时自动保存线程 | `config.cpp:82-100`（1s `try_lock` + 写） | 按需 `flush` | **明确不做**：无收益且与原子写重复 |
| C4 崩溃自检/配置重载提示 | `gqrx/.../mainwindow.cpp:500-521`（`crashed` 标志） | 未做 | **明确不做**：原子写+备份已降低丢配置概率；崩溃标记无法区分断电/强杀，误报成本高于收益 |
| C5 默认值不落盘 | `gqrx/.../dockfft.cpp:238-324`（等于默认就 `remove(key)`） | 全量落盘 | **明确不做**：全量写是单一事实源，利于审计与逐键迁移 |
| C6 版本化迁移 | `gpredict/src/sat-cfg.c:302-330`（1.1→1.4 重写键） | 已落地改良：`605e89da` → `settings.py:250-258`（`_coerce`）、`:334-338`（`migration_notes`） | **改良后做**（已落地）：逐键迁移，粒度比文件级版本块更细 |
| C7 分段 JSON 配置 | `satdump/.../sat_finder_ui.cpp:107`（按模块 `get_user_json`） | 单一 `settings.json` | **明确不做**：集中式更易原子写/迁移/审计 |

#### TX 安全闸（X1–X5）

| 机制 | 参考做法 | 我们现状 | 决定与动作 |
|---|---|---|---|
| X1 参考项目 TX 能力 | `sdrpp/sink_modules/` 无发射；`gqrx`/`onair`/`satdump` 检索 `transmit/ptt/interlock` 无命中 | — | **不适用**（无对照可抄） |
| X2 人工确认→一次性 token→审计 | 无参考实现 | 已落地自研：`440424b9` → `tx_guard.py:543-597`（`arm`）、`:599-620`（`revoke`）、`:622`（`authorize`）、`:441-484`（审计）；`hal.py:721-741`、`:765-779` 先 `authorize` 再 `write_tx` | **照做**（自研，已落地；无许可涉入） |
| X3 能力声明 | `satdump/dev.h:30-36`（静态 `MODE_SINGLE_TX`） | 已落地改良：`1869648b` → `hal.py:139-141`（`supports_tx()` + `tx_capability()["verified"]`） | **改良后做**（已落地）：运行时验证后才置位，不把声明当能力 |
| X4 执行器安全 | `gtk-rot-ctrl.c:787-817`；`rotcl_handler.h:16` + `.cpp:105-136` | 未接线：`gimbal.py:79-82` 声明、`:181`/`:225`/`:328` 硬编码 0–90 | **改良后做（同 T3/T4 旋转器线）** |
| X5 发射波形模拟器 | `wspr_audio/refsrc/wsprsim.c`（离线生成，无硬件门） | — | **明确不做**：模拟器不是安全机制，不能把「能生成波形」当「可安全发射」 |

### 17.2 五项重点补充核验（不计入 53 条）

| 重点项 | 参考做法 | 我们现状 | 决定与动作 |
|---|---|---|---|
| 热插拔自动发现与恢复 | `rtl main.cpp:117-122,394-395` 手动刷新；`UsbRadioController.kt:68,104-107` 事件 | 库层 `device_manager.py:608-640` 有 2s 轮询；`desktop/` 引用 0 处；掉线只 `_handle_backend_lost` + 状态栏 | **明确不做（本轮）**：挂账 `docs/limitations-register.md:555-559`（L48）；接线后去 `tests/test_desktop_hotplug_autorecovery.py:68` 的 `xfail(strict=True)` 并用 `--runxfail` 复跑 |
| 频谱显示与检测的职责划分 | `cpp/src/dsp/peak_detector.cpp:30-32,40-41,49-61` 检测；`cpp/src/ui/spectrum_display.cpp:1121-1141` 成熟/保持/显示 | Python 把检测+成熟+显示集中在 `desktop/spectrum_widget.py:535,630-653,717-787`；两边 **8 条定义差异**见 `docs/parameter-unification.md:560-573` | **明确不做（本线）**：另一条线在改 `peak_detector.cpp` / `spectrum_widget.py`，工作树已见 `cpp/src/dsp/spectrum_engine.cpp` +26 行；本线只登记口径，等该线收敛后复跑 30 条判定（`parameter-unification.md:575-598`） |
| 卫星过境缓存与刷新 | gpredict 只实时时间；onair `passes.cpp:296-306` 到点抓取 | 已落地：`8d3ea22c` → `rf_sky_view.py:7646-7663`（1x 每 5s 墙钟；快转按模拟时间差 `PASS_REFRESH_SIM_S` + 墙钟下限）；`e6ff34bd` → `new_spacetime_panel.py:602-617` 手动刷新+重入锁 | **改良后做**（已落地）：不抄「每次重算」也不抄「只实时」，保留双时间基准防快转跳帧 |
| 错误呈现与日志分级 | satdump `flog`、gqrx `qDebug` 分级输出 | 呈现已落地：`90813ad0` → `desktop/error_remedy.py:38`（code→「怎么办」映射，`mapping_gaps()` 守卫）；日志已补基础设施：`33f3b1d7` → `desktop/logging_setup.py:configure_logging`、`desktop/main.py:main` 启动时按 `config_schema.py:log_level` 初始化 | **改良后做（已落地 `33f3b1d7`）**：默认 INFO、可切 DEBUG，重启生效；导入无副作用（不建 handler、不写文件） |
| 配置/书签存储格式与迁移 | gpredict `sat-cfg.c:302-330` 版本块；satdump 模块分段 JSON | 产品书签走 `frequency_manager.py:190,223`（`~/.mbdsdr/bookmarks.json`，无版本字段、原子写）；`frequency_manager_v2.py:load` 已真校验 `version`：缺失按 v1 语义读并补写 2，`!=2` 拒绝且不改写（`33f3b1d7`）；v2 仍仅测试引用 | **改良后做（已落地 `33f3b1d7`）**：`UnsupportedBookmarkVersion` + `logger.error`，测试 `tests/test_frequency_manager_v2.py:106,124`；v2 接线仍未启用，启用前它已是真机制 |

### 17.3 计数与许可复核

- **照做 14 条**：S1、S8、S9、S10、S12、S14、P16、H1、H4、H7、D7、T1、T6、X2。
- **改良后做 11 条**：H3、D6、T3、T4、T5、T7、C1、C2、C6、X3、X4。
- **明确不做 28 条**：S2、S3、S4、S5、S6、S7、S11、S13、H2、H5、H6、H8、D1、D2、D3、D4、D5、D8、D9、D10、T2、T8、C3、C4、C5、C7、X1、X5。
- 合计 **14 + 11 + 28 = 53**；另有 §17.2 五项重点补充核验，计数单列。
- **许可复核**：14 条「照做」与 11 条「改良」均**只借鉴思路，无代码复制**；`stellarium`(GPL-2.0)、`stellarium-web-engine`(AGPL-3.0)、`sdrpp`/`gqrx`/`satdump`/`onair`/`noaa-apt`(GPL-3.0)、`gpredict`/`rtl_433`(GPL-2.0) 未复制任何源码、注释、常量表、数据或着色器；`goestools`(BSD-2) 亦未复制。D9 仅用 GPL 上游生成 KAT 向量（`tests/golden/`），实现独立。本轮无新增复制，`LICENSING.md` §7 约束表不变；若将来由「照做」改为「复制代码」，先按 §7 判定并登记 `NOTICE.md`。
