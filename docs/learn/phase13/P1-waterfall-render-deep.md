# P1 渲染深度落地：SDR++ waterfall 对标（doZoom 峰保持 / 历史重染 / JSON 色板 / 时间轴联动）

> 批次：Phase13-Wave1 P1。上游机制来源 `docs/learn/phase12/sdrpp-waterfall.md`（GPLv3 只学机制，
> 干净室自有重写）。差距项 L6 doZoom 峰保持 / L7 重染历史 / L8 JSON 色板 + 时间轴刻度联动。
> 红线：只写 `cpp/src/ui/`、`cpp/tests/`、`cpp/CMakeLists.txt`（追加）、`docs/learn/`；禁 mock；未 commit/push。

## 1. 架构改动一句话
把瀑布环形从"**入环即烤成 QRgb 像素行**"改成"**环里存原始 dB 行**（source of truth），
显示时才按当前 LUT/量程上色"。由此：换色板/拖量程/缩放都能从 raw dB 整段重染，不丢帧。
纯算术全部抽到 header-only、widget-free 的 `cpp/src/ui/spectrum_render.h`（对标 `spectrum_tune.h`）。

## 2. 四项交付与落点

| 项 | 纯函数（可测） | widget 接线 |
|---|---|---|
| L6 doZoom 峰保持 | `decimateBlockMaxRange/()` `spectrum_render.h:82` | 瀑布降采样分支 `spectrum_display.cpp:733`（`srcW>falls.width()` 时逐行块内 max→LUT） |
| L7 历史重染 | `buildLut256()` `:121`、`lutIndexForDb()` `:144` | raw 环 `ringDb_`；`materialiseHistory()` `:195`；`setPalette:443`/`setDbRange:339`/`setAutoRangeOn:352` 触发重染 |
| L8 JSON 色板 | `parseColormapJson()` `:171`、`parseHexColor()` `:56` | `loadColormapFromJson/File` `:451/:462`，失败诚实回退，内置三档保留 |
| 时间轴联动 | `niceStepForSpan()/freqTicksNice()` `:234/:244` | 频率条 `paintEvent` `:823` 改用纯函数（数学等价） |

## 3. 关键设计取舍
- **放大 vs 降采样分流**：zoom-in（可视源宽 ≤ 画布宽）保持 Qt `drawImage` 双线性；仅当多 bin
  压到一个像素（zoom-out）才走块内 max，避免无意义重算。小 bin 测试（bins=128/512 < 画布宽）
  走放大路径，故旧像素契约 `history().width()==bins`、row0=最新 完全不变。
- **`history_` 仍是 bin 宽彩色快照**（公开测试契约），新增 `fallsPeak_` 仅作降采样显示缓存。
- **JSON 用 Qt6 QJsonDocument**（项目既有依赖，非 nlohmann）；接受 `stops:["#rrggbb"]` 均匀分布
  或 `[{t,c}]` 显式位置；任一畸形返回 false 且不动 out（诚实回退默认三档）。

## 4. 确定性单测（ctest 追加，禁 mock）
- `tests/test_spectrum_render.cpp`（新，header-only 链 Core/Gui）：11 用例——
  块内 max 逐点、窄峰 survive、LUT 端点、量程重映射、JSON 合法/显式/非法回退、刻度随缩放/平移。
- `tests/test_spectrum_display.cpp` 新增 `reRenderHistoryOnPaletteSwitch`：换灰阶色板同 raw 列
  0.9→229、floor→0、行数不丢、畸形 JSON 拒绝保持、收窄量程 0.8→204。
- `CMakeLists.txt` 追加 `test_spectrum_render`。

## 5. 验证结果（offscreen, Qt 6.8.2, GCC 10.3）
- `ctest -R spectrum`：**5/5 passed**（interaction 8 / display 15 / autorange 9 / tune 12 / render 11）。
- 98 基线相关 UI 测试全绿；改动仅自包含于 spectrum_display，未触碰 dsp/engine。

## 6. 未完成 / backlog
- **全量 ctest 0 Not Run 收尾归 P4**：本轮全量 `cmake --build` 在共享机上编译 ~140 个测试目标过慢，
  已停；只增量构建并验证了直接受影响的 5 个 spectrum 测试（它们是唯一编译 `spectrum_display.cpp`
  的测试目标）。UI 截图 target（fft_band/file_analysis 等）本轮未增量构建，其瀑布降采样路径仅在
  bins>画布宽 时触发，小帧 offscreen 不触发，需 P4 全量构建时复跑确认。
- 瀑布独立增益（G5，waterfallMin/Max 与折线解耦）本轮未做（spec 未列入 P1）。
- 外部色板文件的 UI 选择器/扫描目录未接（`loadColormapFromFile` API 已备，接 settings_dialog 留待后续）。
