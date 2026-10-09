# Peak-hold 衰减速率可配置（maxHold decay tier）

HEAD `aabc309`。本 phase 把峰值保持（maxHold）的每帧衰减步长从一个写死的具名常量
（`kMaxHoldDecayDb = 1.5 dB/帧`）升级为用户可选的「慢 / 中 / 快」三档，并走 QSettings
持久化往返。全程 CLI/offscreen（`QT_QPA_PLATFORM=offscreen`），无 GUI 操控；只接真实
数据源、诚实空态、零 mock。

## 1. 机制（改前）

每帧峰值保持的衰减逻辑原本是：

```
hold[i] = max(frame[i], hold[i] - kMaxHoldDecayDb)     // 原 spectrum_display.cpp:452
```

- `kMaxHoldDecayDb = 1.5f` 是 `tokens.h:134` 的具名常量（不再是裸数字），但**没有任何
  UI 入口、没有持久化键**——用户无法让峰值「久留」（慢）或「快落」（快）。
- 对照 SDR++：其 peak-hold 衰减速率是用户可调的。这是本仓库相对成熟实现的真实缺口。

## 2. 三候选判定表

| # | 候选 | 判定 | 证据 |
|---|------|------|------|
| 1 | 频谱显示参数可配置面（maxHold 衰减速率） | **真实缺口，已落地** | 改前 `kMaxHoldDecayDb` 仅 `tokens.h:134` 定义、`spectrum_display.cpp:452` 应用，全仓 grep 无 UI / 无 settings 键（`grep maxHoldDecay` 改前仅命中常量定义与测试） |
| 2 | 扫频命中自动锁定 / 停驻复核 | **非缺口（已完备）** | `HitHoldMode` 枚举 `frequency_scanner.h:35`（UntilSignalGone / FixedMs）；`holdMode/lingerMs/holdMs` `frequency_scanner.h:54-56`；状态机计时 `holdTimerMs_/goneTimerMs_` `frequency_scanner.h:104-105`，FixedMs 分支 `frequency_scanner.cpp:192-197`；UI 已有 `scanLingerSpin_` `main_window.cpp:1427`、`scanHoldMsSpin_` `:1433`、`scanBmOnlyChk_` `:1437`、一键「存入书签」`scanSaveBmBtn_` `:1462-1465`、`scanStateLabel_` `:1475`，命中频率 `hitFrequency()` `frequency_scanner.h:80`（存书签 `main_window.cpp:1646`） |
| 3 | FFT 窗口函数复核 | **非缺口（已完备）** | `winCombo` 三档 Hann / FlatTop / Blackman `spectrum_widget.cpp:144-145`，`currentIndexChanged` 下发 `windowTypeRequested(idx)` `spectrum_widget.cpp:150-152` |

结论：仅候选 1 是真缺口并落地；候选 2、3 给出证据，不做重复实现。

## 3. 落地 file:line

**tokens（具名 token，不写裸值）** — `cpp/src/core/tokens.h`
- `kMaxHoldDecaySlow = 0.5f`（慢）`:143`、`kMaxHoldDecayMedium = 1.5f`（中，默认）`:144`、
  `kMaxHoldDecayFast = 3.0f`（快）`:145`；
- 档位集合 `kMaxHoldDecayChoices[]` `:146-147`；
- 缺省回退 `kMaxHoldDecayDefault = kMaxHoldDecayDb` `:149`；
- 持久化键 `kSettingsKeyMaxHoldDecay = "view/maxHoldDecay"` `:150`。

**canvas（读取配置衰减，替换常量）** — `cpp/src/ui/spectrum_display.{h,cpp}`
- 匿名命名空间校验 `legalMaxHoldDecay(float)` `spectrum_display.cpp:99-103`：只认三档，
  其余回默认（不做连续区间 clamp——每帧衰减步长没有可连续夹取的物理意义）；
- 自读 `loadRequestedMaxHoldDecay()` `spectrum_display.cpp:109-116`：缺键 / 非数值 /
  非有限值 / 非档位 → 默认 1.5；
- ctor 首帧生效 `maxHoldDecayDb_ = loadRequestedMaxHoldDecay();` `spectrum_display.cpp:132`；
- 应用处读成员而非常量 `held -= maxHoldDecayDb_;` `spectrum_display.cpp:482`；
- 新 setter `setMaxHoldDecayDb(float)` `spectrum_display.cpp:627-636`（校验后即下帧生效，
  无需重建包络）；公开接口 `spectrum_display.h:109-110`；成员 `maxHoldDecayDb_ = 1.5f`
  `spectrum_display.h:440`（类内字面量仅为 ctor 前安全值，避免把 tokens.h 拉进头文件）。

**widget（零新按钮，并入既有 Max/Rst 峰值行）** — `cpp/src/ui/spectrum_widget.cpp`
- 在 `Max` 复选框 + `Rst` 按钮之后插入「衰减」标签 + `QComboBox`
  `spectrum_widget.cpp:185-211`；`objectName = "maxHoldDecayCombo"` `:187`；
- 三项 `慢/中/快`，`itemData` 承载真实 dB/帧（`0.5/1.5/3.0`）`:189-191`；
- 镜像 `depthCombo` 先例：`setCurrentIndex` 在 `connect` 之前（`:193-201`），构造期不会
  对尚不存在的 canvas_ 发信号；
- 变更时 `QSettings` 写键 + `canvas_->setMaxHoldDecayDb()` `:205-210`；
- canvas ctor 自读持久值，故首帧即生效，combo 只是实时镜像 + 变更入口（与 wfDepth 同构）。

**快照门控** — `cpp/tests/ui_screenshot_narrow.cpp`
- 新增 `MBD_MHDECAY=0.5/1.5/3.0` 门控 `:68-79`：在 MainWindow 恢复前把档位写入一次性
  QSettings（throwaway 路径），真实持久化往返，不污染用户数据；先例同 `MBD_WFDEPTH`。

## 4. 诚实语义

- 档位标签用「慢 / 中 / 快」描述**衰减速度**，不暴露裸 dB；但 `itemData` 与 tooltip 给出
  真实 dB/帧（0.5 / 1.5 / 3.0），数值与机制一一对应，不撒谎。
- 非法 / 缺失持久值 → 回退默认 1.5（中），不静默夹取、不发明档位。
- 全程真实离线测试信号（`isTestSignal`）驱动，空态时 combo 仍在、空态标签不变。

## 5. 测试（offscreen 真实计数）

扩展既有 `cpp/tests/test_spectrum_maxhold.cpp`（直接驱动 `ui::SpectrumDisplay` 测试缝，
不经过 paint）：
- `decayTierChangesFalloff()`：切「快」每帧落 3.0 dB、切「慢」每帧落 0.5 dB，逐帧断言包络；
- `persistedTierHonouredOnCtor()`：写「慢」/「快」入 QSettings，新构造 canvas 自读档位
  （写后移除键，不残留用户配置）；
- `illegalPersistedValueFallsBack()`：写 `99.0`（非档位）与非数值 `"not-a-number"`，
  断言回退默认 1.5。

运行结果（`QT_QPA_PLATFORM=offscreen`）：

```
Totals: 8 passed, 0 failed, 0 skipped, 0 blacklisted   (改前 5 passed → 现 8 passed)
```

## 6. 快照核查结论

`MBD_MHDECAY=3.0 MBD_PEAKSHOT=1`，输出至 `ci/phase63_shots/mhdecay-{960,1920}.png`：
- **1920**：工具行「… Max Rst | 衰减 快 | Min 余晖 关 …」衰减下拉清晰可见，选中项为
  「快」（证明持久化往返：环境注入 3.0 → combo 落「快」），无裁切、无叠字；
- **960**：FlowLayout 优雅换行，「衰减 快」位于第一行 Rst 之后，无裁切、无叠字、文字可读。

两档宽度均 0 裁切 0 叠字，combo 在峰值行内，未新增按钮。

## 7. 未完成 / 诚实声明

- 未做「连续 dB/帧滑杆」——刻意只给三档具名档位，避免把衰减步长暴露成无物理意义的
  连续区间；与 waterfall depth 只认 `{128,256,512}` 的取舍一致。
- 候选 2（扫频停驻）、候选 3（FFT 窗）经核实已完备，未重复实现。
