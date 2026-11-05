# 参考项目机制目录（天空 / 频谱）

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
