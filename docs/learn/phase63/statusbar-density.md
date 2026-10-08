# Phase63 — 接收状态栏窄视口信息密度复核（候选 1 主复核）

- **HEAD**: `9950cf8`（落地前后未变，未做任何 git add/commit/push）
- **仓库根**: `/home/user/Doubao/chats/38438160041798146/MBDSDR`
- **运行方式**: 全程 CLI/offscreen（`QT_QPA_PLATFORM=offscreen`，`LD_LIBRARY_PATH=/home/user/Qt/6.8.2/gcc_64/lib`），未操控 GUI；截图走既有 `ui_shot_narrow`（`cpp/tests/ui_screenshot_narrow.cpp`）通道，环境变量 `MBD_W/MBD_H/MBD_RSSITREND`。真实数据源 + 诚实空态，未注入任何 mock。

## 1. 现状（侦察）

接收状态栏 permanent widget 清单（`cpp/src/ui/main_window.cpp:2146-2186`）：

| widget | 变量 | 来源 |
|---|---|---|
| 模式 | `sbMode_` | 真实解调模式 |
| 采样率/VFO/RDS/Gain | `sbSr_/sbVfo_/sbRds_/sbGain_` | 引擎 ~1Hz `sourceTelemetry` 真实回读 |
| 信号源 | `sbSdr_` | 真实源名，无设备时诚实「无信号源」 |
| 扫描/录制/监听 | `sbWatch_/sbScan_/sbRec_` | 真实活动，未活动为空 |
| RSSI/SNR/静噪/GNSS/声卡 | `sbRssi_/sbSnr_/sbSquelch_/sbGnss_/sbAudio_` | 真实引擎读数，空态 `--` |
| S-meter | `sMeter_` | 同一真实 RSSI（`onRssiLevel`） |
| RSSI 趋势 | `rssiTrend_` | 同一真实 RSSI 序列（本轮新增 widget） |

关键尺寸 token（`cpp/src/core/tokens.h`）：

- `kMainMinW = 960`（生产窗口最小宽度，`:239`）
- `kSMeterW = 220`（S-meter 固定宽，`:690`）
- `kRssiTrendW = 140`（趋势条固定宽，`:691`）
- `kSMeterMaxUnits = 9`（S0..S9 共 10 个刻度，`:451`）

## 2. 窄视口实测（先拍现状）

用 `ui_shot_narrow` 在 `MBD_RSSITREND=1`（真实 `pushDbfs()` 路径填充 S-meter/趋势条）下拍三档：

- 960×640（= 生产下限）
- 1920×900（宽屏对照）
- 640 请求（harness 抬了窗口最小，但子控件最小尺寸把窗口顶到 **764×997**，证明窗口有 ~764px 的硬地板，无法真正压到 640）

**逐像素结论**：

1. **14 个 QLabel 在 960 下全部完整可读、无重叠、无裁切**。Permanent 组右对齐，标签 sizeHint 自然占位；960 下总 permanent 宽度 < 状态条可用宽，有富余。
2. **S-meter（220px）与趋势条（140px）是固定宽**：960 与 1920 下蓝色填充实测均为 70px（= shownUnits≈3 个单位 / 满宽 220px），物理尺寸完全一致——**S-meter 没有被窄视口挤压**。
3. **真正可见的缺陷（与窄视口无关，1920 下同样存在）**：S-meter 两端的 **S0 / S9 刻度标签被 widget 边线半裁切**——
   - S0 居中于左轨道边线 `r.left()`，左半字挂到 widget 外被裁，渲染成「0」（缺 S）；
   - S9 居中于右轨道边线 `r.right()`，右半字挂到 widget 外被裁，渲染成「S!」（9 缺右半）。
   - 根因在 `cpp/src/ui/s_meter.cpp` 原标签循环：端帽与内标签**共用同一 `AlignCenter` 居中矩形**，端帽正好落在边线上。

## 3. 三候选判定表

| 候选 | 判定 | 证据 |
|---|---|---|
| **1. 状态栏窄视口信息密度** | **部分缺口 → 已落地** | 960 下 labels 无挤压（干净）；但侦察中发现 **S-meter 端帽 S0/S9 在所有宽度下被边线半裁**（`s_meter.cpp` 原标签循环居中于边线），属状态条域内真缺口，已修。 |
| **2. 瀑布/频谱 marker 防遮挡** | **非缺口** | bookmark 绿点线（`spectrum_display.cpp:930-937`，`kBookmarkColor=#5fd08a` alpha0.45 DotLine，`tokens.h:160-162`）在下层先绘；fixed marker（`:944-959`）后绘在上层：非选中 = accent 蓝实线 alpha0.95（`#7CC4FF`），选中 = 琥珀虚线（`#e0b35a`）。同频叠线时活动固定标记天然盖过被动书签（z-order 即去重），且三色（绿点/蓝实/琥珀虚）互不混淆。任务假设的「绿虚线 vs 琥珀实线」同 x 不成立——琥珀仅选中态，默认固定标记为蓝实。 |
| **3. 调谐步进快速预设复核** | **非缺口** | 7 档 items（`main_window.cpp:541-546`「1 Hz..1 MHz」）与值表 `kStepValuesHz={1,100,100,1000,10000,100000,1000000}`（`:132-133`）一一对应；`applyStep` 路径（`:2294-2299` 写 `currentStepHz_` + spinbox singleStep + `spectrum_->setStepHz`），`currentIndexChanged` 已接（`:2301-2305`）；PgUp/PgDn 循环绑定（`:3051-3057`，`ui::cycleStepIndex` 带 wrap）；持久化回环（`:4339-4343`）。完整。 |

## 4. 落地修复（候选 1 真缺口）

**问题**：`s_meter.cpp` 端帽标签 S0/S9 居中于轨道边线，半字悬挂被 widget 边缘裁切。

**修法**（最小显示层改动，零新 UI 元素、零新 token）：

1. 抽出纯函数 `SMeterWidget::labelPlacement(index, units, trackLeft, trackRight, rowTop, rowH)`（`s_meter.h` 声明、`s_meter.cpp` 实现），返回 `{QRectF rect, Qt::Alignment align}`：
   - **S0（index<=0）**：`AlignLeft`，字形从左轨 `trackLeft` 起，不越左缘；
   - **S9（index>=units）**：`AlignRight`，字形收在右轨 `trackRight` 内，不越右缘；
   - **内标签**：仍居中于各自 cell（行为不变）。
2. `paintEvent` 标签循环改用该 helper；端帽判定由 `isEnd=(i==units)` 扩为 `edgeCap=(i==0 || i==units)`（S0/S9 均恒绘）。

**测试**（扩既有 `test_s_meter` 目标，未新增 CMake 目标）：
`TestSMeter::endCapLabelsStayInsideTrack()` 断言 S0.rect.left()==trackLeft、S9.rect.right()==trackRight、内标签仍居中于 cell。

**快照核查**（`ui_shot_narrow` 门控，`MBD_RSSITREND=1`，见同目录 `smeter-endcap_960/1920/640.png`）：
- 修复前：端帽显示「0」「S!」；
- 修复后：端帽「S0」「S9」字形完整，**0 裁切**；端帽与相邻内标签保持小间隙（非叠字），内标签间距不变。

## 5. 测试真实计数（offscreen）

- `test_s_meter`：**12 passed, 0 failed**（含新增 `endCapLabelsStayInsideTrack`）
- `test_ui_integration`：**20 passed, 0 failed**（S-meter 被主窗口持有，回归通过）

## 6. 改动文件清单

- `cpp/src/ui/s_meter.h`（+11：helper 声明 + struct）
- `cpp/src/ui/s_meter.cpp`（+24/-5：helper 实现 + 标签循环重构）
- `cpp/tests/test_s_meter.cpp`（+30：新增回归断言）
- `docs/learn/phase63/statusbar-density.md`（本档）
- `docs/learn/phase63/smeter-endcap_{960,1920,640}.png`（修复后快照）

## 7. 诚实未完成项

- 640 逃生口：harness 抬窗口最小后，子控件最小尺寸仍把窗口顶到 ~764px，**无法真正压到 640 内容宽**；该档只证明窗口硬地板 ~764，640 下 labels 的极端溢出未直接观测（但生产下限 960 已确认干净，764 地板下也已确认容纳）。
- 端帽与相邻内标签（S0-S1 / S8-S9）间距比内-内间距略紧（~3-4px vs ~10px），字形不重叠、全部可读，但非完全等距；如需等距需在窄 meter 上 stride 掉相邻内标签（会减少刻度数），本轮未做，留作后续审美微调。
- 未触碰隔离文件 `cpp/tests/ui_diag_freeze.cpp`、`cpp/scratch/regen_tool_doc*`，以及他会话在途的 `docs/learn/phase63/param-boundary-audit.md`。
