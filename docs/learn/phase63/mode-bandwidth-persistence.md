# 解调模式+带宽持久化 / 余晖 / 瀑布平移 三候选判定（clean-room 机制深化）

HEAD = `e9d4440`。本轮先对三候选逐一侦察核实（file:line），再落地真缺口。全程 CLI/offscreen
（`QT_QPA_PLATFORM=offscreen`，`LD_LIBRARY_PATH=/home/user/Qt/6.8.2/gcc_64/lib`），零 GUI 操控，
零 mock，真实数据源 + 诚实空态。

## 一、三候选判定表

| # | 候选 | 判定 | 证据（file:line） |
|---|------|------|-------------------|
| 1 | 解调模式 + 带宽持久化（重启回默认 AM/12.5k？） | **非缺口（功能已闭环）** | 保存：`demodCombo_`/`bwCombo_` 变更接 `scheduleSave()`（`cpp/src/ui/main_window.cpp:3110-3113`）→ 500 ms `saveTimer_`（`:3099-3102`）→ `saveSettings()`（`:4133`）→ `saveUiState()` 写 `rx/demodMode`（`:4045`）+ `rx/bandwidth`（`:4048`）。恢复：`restoreUiState()` 构造期调用（`:3169`），读 `rx/demodMode`（默认 NFM，`:4354`）→ `findText`，非法/缺项回 index 1=NFM（`:4355-4357`）；读 `rx/bandwidth`（默认 12500，`:4359`）→ `nearestBwPresetIndex` snap（`:4360`）；引擎下发 `setDemodMode`（`:4505`）+ `currentBwHz_=bwHz; setBandwidth`（`:4506-4507`）。析构再 flush（`:3303-3306`）。键名实为 `rx/demodMode`（**非** `rx/mode`）+ `rx/bandwidth`。 |
| 2 | 频谱余晖 decay（SDR++ persistence 衰减余晖） | **非缺口（衰减峰值保持已存在）** | `maxHoldOn_` 逐帧衰减：`held -= tokens::kMaxHoldDecayDb` 后再与新帧取 max（`cpp/src/ui/spectrum_display.cpp:447-453`），注释明言"a burst's peak lingers visibly and then fades instead of freezing forever"；`kMaxHoldDecayDb = 1.5 dB/frame`（`cpp/src/core/tokens.h:128-134`）。关断清栈（`:592-593`），holdLine 绘制（`:887-907`）。这正是 SDR++ 衰减余晖语义；再补 decay 开关属重复 UI。 |
| 3 | 瀑布本体拖拽平移缺口 | **非缺口（多手势平移已存在）** | 条带左拖 → `Grab::Pan`（`cpp/src/ui/spectrum_display.cpp:1280-1284`）；Shift+拖 trace/瀑布 → `Grab::Pan`（`:1287-1290`）；`mouseMoveEvent` Pan 分支 `viewCenterHz_ -= dx/plotW*span` 并在捕获带边缘 clamp（`:1385-1392`）。Ctrl+滚轮 zoom-to-cursor（`:1525-1536`）；条带滚轮平移（`:1537-1547`）。空白瀑布左拖=跟随调谐（`:1348-1352`），为 SDR++ 式刻意映射。 |

**结论**：三候选在 HEAD 均非功能缺口。任务前提"无 rx/mode、rx/bandwidth 键，重启回默认 AM/12.5kHz"
与代码实况不符——持久化早已落地，键名是 `rx/demodMode` + `rx/bandwidth`。诚实起见**不**新建
`rx/mode` 平行键（会造成双写、恢复只读一把的真 bug），也**不**重复实现衰减/平移。

## 二、真缺口：该持久化零测试覆盖

侦察发现功能虽在，但 `test_ui_integration` 对 `rx/demodMode`/`rx/bandwidth` 的往返、非法回退、
恢复下发引擎**无任何断言**（grep 仅命中书签里的字面量）。此为真缺口，本轮落地：回归测试锁定
既有真实行为，而非编造新功能。

### 测试（`cpp/tests/test_ui_integration.cpp`，未新增 CMake 目标）
- `demodBandwidthPersistsAndRestoresRoundTrip()`：窗口1 真实切 `demodCombo_`=USB、`bwCombo_`=9 kHz
  预设（走真实 `currentIndexChanged` handler），等引擎线程异步读回 `demodMode()=="USB"`、
  `bandwidth()==9000`；窗口1 析构 flush 后，新 QSettings 读到 `rx/demodMode=="USB"`、
  `rx/bandwidth==9000`；窗口2 全新构造，combo 显示 USB、带宽 snap 到 9 kHz 预设，引擎读回一致。
- `demodBandwidthIllegalPersistedFallsBack()`：手种 `rx/demodMode="NOT_A_REAL_MODE"` +
  `rx/bandwidth=5e9`。恢复时非法模式 `findText` 落空 → combo 落 NFM（index 1）；离谱带宽在无
  持久 VFO 行时由默认 12.5 kHz 权威覆盖、combo snap 回默认预设（index 4）。永不出现 bogus 项。
- 时序诚实：合成源开启时引擎线程在跑，`setDemodMode/setBandwidth` 经 `applyIfIdle()` 在 headless
  同步 drain、运行线程异步 drain（`spectrum_engine.cpp:1099-1108`），故读回用 `QTRY_VERIFY`。

### 快照门控（`cpp/tests/ui_screenshot_narrow.cpp`，先例 `MBD_PEAKSHOT`/`MBD_WFDEPTH`）
- 新增 env 门 `MBD_MODE=USB MBD_BW=9000`：在 MainWindow 恢复**之前**把 `rx/demodMode`+`rx/bandwidth`
  及一致的 `vfo/0/*` 行种入 throwaway QSettings，使左侧"接收参数"组 combo 渲染恢复值（真实往返，
  非事后戳）。默认关闭，不影响既有快照。

## 三、构建与测试真实计数

- 增量构建（持久目录 `cpp/build`，未用 /tmp）：`cmake --build . --target test_ui_integration ui_shot_narrow`。
- `test_ui_integration` offscreen 全量：**22 passed, 0 failed, 0 skipped**（本轮 +2 新测试）。
- 快照（`MBD_SCROLL=demodCombo` 把接收组滚入视口）：
  - `ci/phase63_shots/modebw_960.png`（960x700）、`modebw_1920.png`（1920x900）。
  - Read 核查：接收参数组 解调=**USB**、带宽=**9 kHz** 可见；采样率诚实空态"未连接"；
    状态栏 `USB 未连接 VFO A 98.500 MHz`；**0 裁切、0 叠字**。

## 四、诚实未完成项 / 备注

- 未改任何 `src/` 功能代码——三候选功能本就存在；强行加 `rx/mode` 键或重复衰减/平移属造假。
- `rx/demodMode`/`rx/bandwidth` 仍是 `main_window.cpp` 内字符串字面量，未迁移为 `tokens.h` 具名
  token（与 `kSettingsKeyTuneHistory` 风格略不一致）。此为 cosmetic，且改 4 处字面量易与在途改动
  冲突，本轮不动；如后续统一，应一把替换 `:4045/:4048/:4354/:4359`，**不得**新增 `rx/mode`。
- 未跟踪隔离文件 `cpp/tests/ui_diag_freeze.cpp`、`cpp/scratch/*` 未触碰、未入库；无 git add/commit。
