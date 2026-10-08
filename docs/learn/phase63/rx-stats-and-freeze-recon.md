# Phase63 · RX 统计与冻结机制三候选侦察（只读）

- 仓库 HEAD：`6a7ad7b`（与题面一致；侦察全程未改动任何跟踪文件）。
- 范围：三候选逐项侦察判定，**本轮不落地改码**；产出供主 agent 冻结下一轮规格。
- 方法：源码 `Read`/`grep` 取证 + 既有 harness `cpp/tests/ui_screenshot_narrow.cpp`（offscreen，`LD_LIBRARY_PATH=/home/user/Qt/6.8.2/gcc_64/lib`，`QT_QPA_PLATFORM=offscreen`，增量构建 `ui_shot_narrow`）拍快照并逐图 `Read` 核查。未跟踪文件 `cpp/tests/ui_diag_freeze.cpp` 未触碰。

## 0. 判定总表

| # | 候选 | 判定 | 关键证据 (file:line) | 收益评估 | 最小方案 | 测试/快照计划 |
|---|---|---|---|---|---|---|
| 1 | 左栏窄视口三孤儿组渲染 | **非缺口**（三组自身在 960 生产地板下无裁切/叠字） | 控件几何实测 + 代码见 §1 | 低：现状已可用 | 无需修；仅 netAudio 长状态行留档观察 | 已拍 9 张快照（见 §1.3）；下一轮回归用同 harness 复跑 |
| 2 | RX 统计汇总行 / 多 VFO 聚合 | **非缺口** | main_window.cpp:2145-2174, :3870, :3885-3908；spectrum_engine.cpp:1407-1415；vfo_manager.h:11-13, :48-68 | 低：每 VFO 配置聚合已存在；每 VFO 实时电平是引擎新测量，非补一行 UI | 不落地；若未来要每 VFO 电平，属新引擎测量（每通道功率），另立规格 | 可选：`ui_screenshot_multi_vfo.cpp` 既有 harness 复验列表渲染 |
| 3 | 频谱 y 轴 auto-range freeze | **真缺口（小）** | spectrum_widget.cpp:277-299；spectrum_display.cpp:364-405, :404, :492-502 | 中：对比测量时量程应稳定；现状关"自动"跳变到 spinbox 旧值而非当前量程 | spectrum_display.cpp:492-502 `setAutoRangeOn(false)` 时把当前 `dbCeilDb_` 冻入 `manualCeilDb_`，并回同步 `dbMaxSpin_`（约 5 行，零新按钮） | harness 灌 6 帧合成载波（MBD_PEAKSHOT 门控已存在），关自动后连续拍帧断言 ceiling 不变 |

## 1. 候选 1：左栏窄视口三孤儿组复核

### 1.1 既知约束（实测裸数）

- 生产地板 `kMainMinW = 960`（tokens.h:219）。960 窗口下 splitter 三栏 = **198 / 556 / 198**（实测日志）。
- 左轨 QScrollArea：视口 192px，内容 widget 294px（`widgetResizable=1` 但被"源与连接"组 min=268 + 边距撑大）——左轨内容右缘约 100px 落入水平可滚区，此为注释自述的既知条件（main_window.cpp:469-472）。
- 右页（扫描/书签、录制库 tab）视口 ≈ **158px**（实测 GBOX w=158）。

### 1.2 三组孤儿控件几何实测（960 窗口，MBD_DUMP=1）

| 孤儿组 | 位置 | 控件几何（x / w，内容坐标系） | 视口 | 判定 |
|---|---|---|---|---|
| 网络音频外送组 | main_window.cpp:460-518 | hostEdit x=37 w=128（右缘 165；:473-474 钳制 64..128）；portSpin x=38 w=82；开始/停止 x=10/62 w=48；状态标签 x=10 w=248 | 192px | 主控件全部落在 165px 内，**无裁切/叠字** |
| 活动扫描链组 | main_window.cpp:1486-1498 | scanLinkChk x=10 w=138（右缘 148）；scanLinkStateLabel x=10 w=138 | 158px | 干净，右缘 148 < 158 |
| 导出原始 IQ 段按钮 | main_window.cpp:1780-1782（独立行） | recLibIqBtn x=10 w=138 | 158px | 干净（phase62 特意独立成行，注释 :1776-1779） |

### 1.3 图核查结论（逐图 Read）

快照清单（均 gitignore 落盘 docs/learn/phase63/）：

- 960：`recon-netaudio-c-960.png`、`recon-scanlink-960.png`、`recon-iqbtn-960.png`（另 `recon-netaudio-960.png` = 左轨滚底，录/制组）
- 1920：`recon-netaudio-1920.png`、`recon-scanlink-1920.png`、`recon-iqbtn-1920.png`
- 640 逃生口（harness  lifted min，生产不可达）：`recon-*-640.png`

逐图结论：

1. **960 生产地板**：三孤儿组自身文字完整、无重叠。网络音频组 host "127.0.0.1"、端口 49100、UDP、开始/停止、"未开启"全部可读；活动扫描链"启用活动扫描链 / 空闲（未启用）"完整；"导出原始 IQ 段"按钮完整。
2. **1920**：三栏 420/1072/420，全部宽松，无问题。
3. **640 逃生口**：轨宽被压到 100px，三组标题与控件均被右缘裁切（"活动扫""启用…"）——**但 640 低于生产地板 960，生产路径不可达**，仅作最坏情况记录，不构成修码理由。
4. **旁证（既存兄弟组，非本轮范围）**：960 下右页 158px 视口里，同页既存的"频率扫描"QForm 标签列被挤压堆叠（recon-scanlink-960.png 右上）、"录制文件"6 按钮行文字挤成空块（recon-iqbtn-960.png）。这些是上一轮既存组的窄视口挤压，非三孤儿引入，留档观察。
5. **唯一残余观察项**：netAudio 运行态状态行可达 ~30 字符（"UDP → host:port · N B · 发送错误 N"，main_window.cpp:5064-5074），标签 w=248 且 wordWrap（:515）。几何推导其最长行尾部可能落入左轨水平可滚区（192 视口外），但 wordWrap 保证不叠字；本次未注入长串实测（不改码不 mock），留档。

**候选 1 判定：非缺口。** 三孤儿组在生产地板 960 下渲染合格；无需修。

## 2. 候选 2：RX 统计汇总行 / 多 VFO 聚合

### 2.1 现有覆盖（上一轮已核实项的复核）

- 常驻状态条 permanent widgets：sbMode_ / sbSr_ / sbVfo_ / sbRds_ / sbGain_ / sbSdr_ / sbRssi_ / sbSnr_ / sbSquelch_ / sMeter_（main_window.cpp:2145-2174）。
- sbVfo_ 只写**选中 VFO**：`"%1 %2 MHz"`（name + freq，:3870）；sbRssi_/sbSnr_ 由 engine rssiLevel/snrLevel 驱动（:4760-4784）。

### 2.2 多 VFO 聚合视角是否缺失

- **配置级聚合已存在**：左轨"多 VFO"组 vfoList_（:643-654）每行 = 选中点 / 名字(id) / 频率 / 模式 / 带宽 / `[出声]` 或 `[并行监听]` 标签（vfoRowText :3885-3908）；频谱画布同步绘制 band box（:3813 setVfoMarkers）。多个 VFO 时用户一屏可见全部 VFO 的频率/模式/带宽与出声/并行监听状态。
- **实时电平无每 VFO 维度，且引擎不计算**：rssiLevel 来自**宽带回采总能量**（对整个 IQ 块求 norm 均值，spectrum_engine.cpp:1407-1415），SNR = 该能量减跟踪噪声底（:1428-1443）。VfoMarker 结构无任何电平字段（vfo_manager.h:48-68）；VfoChannel 也无 per-channel 电平成员。架构注释明确：静噪/AGC/音频/录制等共享下游**只作用选中 VFO 的 48k 音频**（vfo_manager.h:11-13）。
- 含义：状态条里 RSSI/SNR/S-meter 本就是"宽带 + 选中路"语义，不是"选中 VFO 通道内"电平。若要"每 VFO 一行汇总（含各自电平）"，需要引擎新增每通道（channelizer 输出）功率测量——这是新数据源的新功能，不是补一个缺失的 UI 行。

**候选 2 判定：非缺口。** 多 VFO 下配置聚合（列表 + 画布 band box + 出声/并行标签）已存在且为真实数据；状态条只显示选中 VFO 是架构语义（共享下游只走选中路），不算聚合缺失。每 VFO 实时电平若未来要做，应另立"引擎每通道功率"规格，不属于本轮汇总行范畴。

## 3. 候选 3：频谱 y 轴 auto-range freeze

### 3.1 现状机制（取证）

- UI 入口："自动" checkable QToolButton（默认 ON，spectrum_widget.cpp:277-299）→ `setAutoRangeOn(on)`；另有 dB 上下限 spinbox（:246-271）→ `setDbRange(lo, hi)`。
- canvas 实现：auto 开时 ceiling 向 24 帧滑窗峰值缓动（每帧 ≤1.5 dB，spectrum_display.cpp:364-405）；**floor 恒为手动 floor**（:404 `dbFloorDb_ = manualFloorDb_`）。
- **关自动时的行为**（:492-502）：`dbCeilDb_ = manualCeilDb_` —— 即跳变到 spinbox 设定值（默认 0 dB），**而不是冻结当前屏幕上正在缓动的量程**。
- 拖拽面：canvas 全部拖拽逻辑在 x 轴（游标/标记/VFO/带宽边，:1315-1374），无 y 轴拖拽锁定；全 ui grep "冻结/锁定量程/rangeLock/freezeRange" 零命中。

### 3.2 判定与最小方案

**真缺口（小）**：用户想"定格当前量程做对比测量"时，现状是关"自动"瞬间跳回 spinbox 旧量程（如 0 dB），而非当前量程；没有任何"冻结当前 y 轴"的语义。

最小方案（file:line 级，约 5 行，零新按钮）：

1. `spectrum_display.cpp:492-502` `setAutoRangeOn(false)` 分支：把当前屏显量程冻入手动边界——`manualFloorDb_ = dbFloorDb_; manualCeilDb_ = dbCeilDb_;`（取代当前直接赋 manual 值），再 `materialiseHistory()`（已有调用）。
2. `spectrum_display.h` 增加一个只读 getter `currentCeilDb() const`（或复用 publishVisibleRange 已有出口），由 `SpectrumWidget` 在关自动时把 `dbMaxSpin_->setValue(round(dbCeilDb_))` 回同步（blockSignals），使 spinbox 与冻定格一致。
3. 语义变为：**"自动"= 随峰缓动；关掉 = 定格此刻量程**，天然覆盖 freeze 需求，无需新增按钮。

测试计划：offscreen harness 复用 MBD_PEAKSHOT 门控（ui_screenshot_narrow.cpp:118-152 已能灌 6 帧成熟载波），先让 auto 缓动到某 ceiling → 关自动 → 再灌 6 帧更强载波，断言屏显 ceiling 不变（可扩展 MBD_DUMP 式 qInfo 打印 `dbCeilDb_` 或快照比对 grid 线位置）。

## 4. 红线与诚实未完成项

- **红线结果**：`git status` 无任何跟踪文件修改；HEAD 仍 `6a7ad7b`；未 git add/commit/push；未触碰 `cpp/tests/ui_diag_freeze.cpp` 及其它未跟踪文件；快照 PNG 全部落盘 docs/learn/phase63/ 且被全局 `*.png` gitignore（不进版本库）。
- **未完成/未实测项**（诚实记录）：
  1. netAudio 运行态"长状态行"尾部是否真的在 192 视口外被裁——只做了几何推导（w=248 vs 视口 192），未注入长串实测（不改码、不 mock）。影响小：wordWrap 防叠字，且左轨水平滚动是既知条件。
  2. 未跑 `ui_screenshot_multi_vfo.cpp` 实测多 VFO 列表渲染；候选 2 判定基于代码取证（vfoRowText/setVfoMarkers/引擎能量语义），逻辑链完整。
  3. 候选 3 最小方案仅为规格建议，未写代码验证编译；行号以当前 HEAD 为准。
