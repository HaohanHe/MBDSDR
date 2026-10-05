# Phase37 块3：三态验证截图报告

> 范围：只读验证 + offscreen 截图 + 本目录文档。未改任何 cpp/mobile 源码（Wave1 的 tokens.h/ui10 文件/mobile token 化均为 A、B 已落地成果，本块只在其之上做视觉收口）。
> 截图方式：`QT_QPA_PLATFORM=offscreen` 运行 `cpp/build` 下既有 `ui_shot_*` 目标（CMake 截图程序，`MainWindow::grab()` 出真 PNG，无伪造数据）。
> 环境基线：ctest 127/127；mbdsdr 已按 A 的 token 化重编成功。

## 0. 截图清单（docs/learn/phase37/screenshots/）

| 文件 | 逻辑尺寸 | 设备像素(dpr) | 对应态 |
|---|---|---|---|
| standard_main.png | 1280×800 | 1.00 | 标准 |
| standard_fftband.png | 1280×800 | 1.00 | 标准 |
| standard_devices.png | 1280×800 | 1.00 | 标准（rtl_tcp 回环，真测试信号） |
| standard_statusstrip.png | 1500×700 | 1.00 | 标准（左侧 rail 展开 + 欢迎条） |
| standard_ai.png | 460×820 | 1.00 | 标准（AI 助手页） |
| narrow_main.png | **960×640** | 1.00 | 窄窗（请求 820×640，见 §1.2） |
| narrow_native.png | **960×640** | 1.00 | 窄窗（ui_shot_narrow 内置 820×640 起窗） |
| narrow_devices.png | **960×640** | 1.00 | 窄窗（真测试信号，量游标 M1） |
| hidpi_main.png | 1280×800 逻辑 | 1.50（1920×1200） | 高DPI QT_SCALE_FACTOR=1.5 |
| hidpi_fftband.png | 1280×800 逻辑 | 1.50（1920×1200） | 高DPI |
| hidpi_devices.png | 1280×800 逻辑 | 1.50（1920×1200） | 高DPI（真测试信号） |
| hidpi_statusstrip.png | 1500×700 逻辑 | 1.50（2250×1050） | 高DPI |

---

## 1. 三态结论（逐图自查）

### 1.1 标准态（1280×800, dpr=1.0）—— 通过
- **standard_main**：顶栏/控制工具条/频谱栅格/底部频率表/状态栏各行对齐，无叠字、无裁切。频谱为空（无信号源，离线测试引擎未推峰）属诚实空态。
- **standard_fftband**：工具条、瀑布行、底部"带宽预设: BPSK -> WFM Hz"均完整，无叠字。
- **standard_devices**：rtl_tcp 回环真信号，频谱多峰、游标 M1 98.630、调谐 98.500 MHz 标注清晰；右上角长主机名"rtl_tcp 127.0.0…"按设计省略号截断（非缺陷）。
- **standard_statusstrip**：左 rail 为 QScrollArea，右侧边界长标签（"RTL-SDR"等）在视口边被切——**滚动区预期行为**；底部欢迎条"欢迎使用 MBDSDR·三步上手" + 去连接/不再提示按钮完整。
- **standard_ai**：未配置模型提示条、新会话/重命名/删除、压缩上下文、运行任务/停止、导出/清空日志、对话转录、输入框+发送，全部无叠字无裁切。

### 1.2 窄窗态（请求 820×640）—— 部分发现，如实列出
- **起窗宽度被钳制**：`MBD_W=820` 与 `ui_shot_narrow` 的 `win.resize(820,640)` 均被主窗口最小宽度约束钳到 **960×640**（r27 日志确认 `960x640, dpr=1.00`）。即当前主窗口最小逻辑宽 ≈960px，820 不可达。这是频谱最小宽度约束的预期结果，左 rail 已做成 QScrollArea 可滚动。
- **发现 1（工具条下拉值被截）**：narrow_devices 顶部工具条下拉框数值压缩——`FFT 2048`→显示"2"、`窗 Hann`→"H"、`平均 Off`→"0"。窄宽下列表项文字宽度不足以容纳，出现截断。
- **发现 2（工具条动作按钮挤靠）**：narrow_native / narrow_devices 中"清标记 游标A 游标B 清游标"一排按钮在 960px 下标签相互挤靠，字形间距明显小于标准态（未见完全重叠糊字，但已逼近）。
- **发现 3（底部 S-meter 刻度连排）**：narrow_devices 右下角 S-meter 刻度标签"S1S2S3S4S5S6S7S8S9"在窄宽下无间距连排（标准/高DPI态下为"S1 S2 S3…"正常分隔）。
- 其余：频谱栅格、游标、底部表格、状态栏在 960×640 下不裁切、不叠字。左 rail 内容右侧裁切为滚动区预期行为。

### 1.3 高DPI态（QT_SCALE_FACTOR=1.5）—— 通过
- **hidpi_main**（1920×1200, dpr=1.50）：工具条全部按钮（2048/Hann/Off/标记/清标记/游标A/游标B/清游标/门限 15 dB）间距舒展、文字锐利，无叠字无裁切。
- **hidpi_devices**：真测试信号多峰清晰，游标标注 M1 98.630；底部 S-meter"S1 S2 S3…S8 S…"间距正常。
- **hidpi_statusstrip**（2250×1050）：`tokens::scaled()` 正确放大左 rail 与欢迎条，无叠字。
- 高DPI 下 `tokens::scaled()` 全局生效，控件尺寸/字号随比例放大，未出现 1.5× 下的裁切或错位。

**三态小结**：标准态、高DPI态干净通过；窄窗态（实际 960×640）有 3 处密度问题（下拉值截断 / 游标按钮挤靠 / S-meter 刻度连排），均集中在"固定不换行的顶部工具条 + 底部仪表刻度"在收窄时的弹性不足，不影响信号链路主视图。820 目标宽因最小宽度钳制不可达（实得 960）。

---

## 2. 触控目标抽查（按钮/滑块/列表行 ≥44px 逻辑高）

依据：`src/core/tokens.h` `kTouchMin = 44`、别名 `kTouchMinDim = kTouchMin`（tokens.h:201/211）。
设计说明（tokens.h:618-620）：QPushButton 视觉密度 `kControlH=26px`，但纯文字小按钮通过 `setMinimumHeight(scaled(kTouchMin))` 把命中区抬到 44px。

| 控件类 | 代表控件 | 尺寸断言（file:line） | 逻辑高 |
|---|---|---|---|
| VFO 行内按钮 | vfoAdd/Copy/DelBtn | main_window.cpp:564-565 `setMinimumHeight(scaled(kTouchMin))` | 44 |
| 频谱缩放按钮 | zoomIn/zoomOut/zoomReset | main_window.cpp:1030-1040 | 44（宽+高） |
| 频谱历史按钮 | histBtn | main_window.cpp:1050 | 44 |
| 天空时间滑块 | skyTimeSlider_ | main_window.cpp:1094 `setMinimumHeight(scaled(kTouchMinDim))` | 44 |
| AI 会话下拉 | aiSessionCombo_ | main_window.cpp:1719 | 44 |
| AI 运行/停止 | aiRunTaskBtn_/aiStopTaskBtn_ | main_window.cpp:1815/1818 | 44 |
| AI 导出/清空日志 | exportActBtn/clearActBtn | main_window.cpp:1850/1853 | 44 |
| 离线分析按钮 | offAnaOpen/PauseBtn_ | main_window.cpp:1606-1607 | 44 |
| M17 清除按钮 | clearBtn_ | m17_panel.cpp:71 | 44 |
| POCSAG 清除按钮 | clearBtn_ | pocsag_panel.cpp:47 | 44 |
| VOR 读数行 | kReadoutH | vor_panel.cpp:22 `= tokens::kTouchMinDim` | 44 |
| VFO 列表行 | rowH | main_window.cpp:3312 `scaled(kVfoRowH)`，`kVfoRowH=kTouchMinDim`(tokens.h:218) | 44 |
| 标签页 | QTabBar::tab | tokens.h:751 QSS `min-height:%touch%`（%touch%=44） | 44 |
| 滚动条滑块 | QScrollBar::handle:vertical | tokens.h:859 QSS `min-height:%touch%` | 44 |

- **截图佐证**：standard_main（dpr=1.0）顶部工具条控制行实测纵向占带约 36px 视觉高（文字/图标带），命中区由 `setMinimumHeight(44)` 撑到 ≥44；hidpi_main（dpr=1.5）下按钮视觉高度相应放大至 ~66 设备像素，与 44 逻辑×1.5 一致。
- **未达 44 的已知项**：基线 QPushButton / QComboBox / QSpinBox 的 QSS `min-height:%ctlH%`=26（tokens.h:766/788），这些是行内密集型只读/输入控件，靠各自业务处的 `setMinimumHeight(44)` 补命中区；凡未补的行内控件视觉高 26px——与本块"不写功能代码"红线一致，列为观察项而非新增改动。

---

## 3. ctest 全量确认

命令：`QT_QPA_PLATFORM=offscreen ctest --output-on-failure`（在 `cpp/build`）。
完整输出见同目录 `ctest_full.log`。

```
127/127 Test #127: ai_sessions_prod .................   Passed    1.51 sec

100% tests passed, 0 tests failed out of 127

Total Test time (real) = 500.18 sec
```

- **结果：127/127 全过，0 failed，0 Not Run。**
- 无 Not Run 项：所有测试二进制均已在 `cpp/build` 编出，无增量目录未编导致的跳过。
- 复跑环境 offscreen，与基线一致（基线 127/127 维持，Wave1 token 化未引入回归）。

---

## 4. 未解决项 / 观察项（本块不改代码，仅登记）

1. **窄窗 960px 工具条弹性不足**（§1.2 发现1-3）：下拉值截断、游标按钮挤靠、S-meter 刻度连排。根因是顶部工具条为单行不换行布局，收窄时无折行/省略策略。需后续 Wave 决定是否给工具条加换行或省略号策略（本块红线禁改 cpp）。
2. **820×640 目标宽不可达**：主窗口最小逻辑宽钳到 960px；若确需 820，需下调频谱最小宽度约束（产品取舍，不在本块）。
3. **行内 26px 控件命中区**：部分 QComboBox/QSpinBox 未逐个补 `setMinimumHeight(44)`，靠视觉密度优先；如目标是全部可点区 ≥44，需后续审计补齐（本块未改）。

## 5. 交付边界自查
- 仅新增 `docs/learn/phase37/screenshots/*.png`、本报告、`ctest_full.log`；未改 cpp/mobile 源码。
- 截图为 `MainWindow::grab()` 真图，无假数据；窄窗/高DPI 发现如实列出。
- 未执行 `git add -A`，未 commit/push。
