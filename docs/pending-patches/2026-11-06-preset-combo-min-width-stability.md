# 对接说明：`preset_combo` 清空后最小宽缓存 → 右栏 278↔356 两态（交给 `参数治理线`）

> 基线 commit：`b0a440e3`（本次改动已在该笔落库）；生成时间：2026-11-06。
> 占用口径：`desktop/control_panel.py` 在取证时由 **`参数治理线`** 持有——就是本文件
> 生成方自己的 line（`file_claim.py` 记录 owner=`参数治理线`，claim 已在本文件生成前
> 释放）。**不存在跨线抢文件**；`b0a440e3` 是本线持锁期间落的库。
> 若要重做，按 §2 逐行替换并在本线释放后执行；若保留，按 §4/§5 复核即可。

## 0. 一句话

打包线 overlap 步在 windows 1000×600 的挤压计数在 **13 ↔ 15** 之间 5:4 跳变：
同 commit、同 DPI/字体指纹、同命令，两种结果各约一半。根因不在阈值、不在新控件，
而在 `preset_combo` 被清空后 **QComboBox 的最小宽缓存（292↔101）** 让
`ControlPanel` 的最小宽在 **356↔278** 两态切换；右栏内容宽随之 356↔288，
1000×600 下有 2 个控件跨过「`sizeHint > width+2`」的挤压线。

## 1. 证据（复跑可复现）

### 1.1 逐次计数（`scripts/ui_narrow_breakdown.py --json`，脚本原样未改）

修前（干净 worktree `385d0e15`，windows 1000×600 / 1344×700，挤压/越界）：

| 次数 | 1000×600 | 1344×700 |
|---|---|---|
| 1、3、4 | 13/1 | 7/1 |
| 2、5、6 | **15/1** | **9/1** |
| 前一轮另 3 次 | 13/7、15/9、13/7 | — |

合计 9 次 = **13/7 五次、15/9 四次**（5:4）。offscreen（DPR 1.0）4 次 =
**30/9 两次、36/15 两次**。

同 base 对照（`b0a440e3^` + `git checkout b0a440e3^ -- desktop/control_panel.py`）：
windows 4 次全 13/7（未命中坏态）；offscreen 2 次 = 30/9、36/15（仍双态）。

修后（`b0a440e3`，同样命令）：windows **9 次全 15/9**；offscreen **4 次全 37/16**。

### 1.2 两态的几何差（探针实测，不是推断）

| 量 | 13 态 | 15 态 |
|---|---|---|
| `ControlPanel.minimumSizeHint().width()` | **356** | **278** |
| `ControlPanel.width()`（viewport=288） | 356（内容比视图宽，横向滚动条被 `AlwaysOff` 关掉） | 288（贴住视图） |
| `preset_combo.minimumSizeHint().width()` | **292** | **101** |
| `preset_combo.count()`（两种都是 0） | 0 | 0 |
| 1000×600 挤压 | 13（面板内 2 项各 +6） | **15**（面板内 4 项，+6/+23/+57/+15） |

15 态比 13 态多出的正好是下面 2 项（其余 13 项逐字相同）：

- `statusValue「静噪门限」60→45`（[control_panel.py#L633-L637](../../desktop/control_panel.py#L633-L637)）
- `QLabel「-50 dB」40→36`（[control_panel.py#L652-L655](../../desktop/control_panel.py#L652-L655)）

同一批控件在 15 态被压得更狠：`mode_combo` 83→60、`freq_input` 149→92
（[control_panel.py#L343-L368](../../desktop/control_panel.py#L343-L368)）。

### 1.3 机制链

1. 构造时先给 `preset_combo` 塞长占位文本「（无预设：请用“自动扫台”或手动输入频率）」
   — [control_panel.py#L768](../../desktop/control_panel.py#L768)。
2. 启动恢复设置 `_apply_restored_settings()` → `set_mode("FM")`
   — [main_window.py#L3421](../../desktop/main_window.py#L3421)。
3. `set_mode()` / `_on_mode_changed()` 把**解调模式名**（`"FM"`）当**频段名**传给
   `_populate_presets()` — [control_panel.py#L1278](../../desktop/control_panel.py#L1278)、
   [#L820](../../desktop/control_panel.py#L820)。
4. `_populate_presets()` 只认 `"FM 广播"/"AM 广播"/"航空"…`；`"FM"` 落 else，
   `clear()` 后**一条 item 都没有** — [control_panel.py#L762](../../desktop/control_panel.py#L762)。
5. `QComboBox.clear()` 并不保证失效按占位长文本算出的最小宽缓存：同一控件同一时刻
   实测 `minimumSizeHint` 292（残留）↔ 101（已重算）。**哪一支命中取决于启动时序**
   （`clear()` 与首次样式/布局尺寸重算的先后）。
6. 78 px 差进入右栏滚动区：`setWidgetResizable(True)` + 横向滚动条 `AlwaysOff`
   — [main_window.py#L2329-L2332](../../desktop/main_window.py#L2329-L2332)；
   右栏最小宽 320 — [main_window.py#L1564-L1566](../../desktop/main_window.py#L1564-L1566)。
   面板最小宽 ≤ viewport 时内容被压到 288 → 多挤 2 项；> viewport 时内容保持 356，
   右端 68 px 被裁掉（无滚动条可达）。

## 2. 改动（已落库于 `b0a440e3`）

### 改动 A：预设跟随频段，不再跟随解调模式

锚点：`_on_mode_changed` 末尾。

```python
# 前
        self.mode_changed.emit(mode)
        self._populate_presets(mode)
# 后
        self.mode_changed.emit(mode)
        # 预设列表属于「频段」，不属于解调模式：切模式只换带宽，不重排电台。
        # 旧实现把模式名（"FM"）当频段名传进去，_populate_presets 无一支命中，
        # 结果 clear() 后列表为空，还留下一条内容相关的最小宽缓存。
        self._populate_presets(self.band_combo.currentText())
```

锚点：`set_mode` 末尾（`blockSignals(False)` 之后）。

```python
# 前
        self.mode_combo.blockSignals(False)
        self._populate_presets(mode)
# 后
        self.mode_combo.blockSignals(False)
        # 同 _on_mode_changed：预设按频段走，模式名不能当频段名（见该处注释）。
        self._populate_presets(self.band_combo.currentText())
```

副作用（正是要的）：启动后预设列表不再为空，UI 上「频率预设」保留该频段的
条目/占位项；切模式不再清空下拉。

### 改动 B：`preset_combo` 用与内容无关的最小宽策略

锚点：`self.preset_combo = QComboBox()` 之后。

```python
# 前
        self.preset_combo = QComboBox()
        self.preset_combo.setMinimumHeight(28)
        self._populate_presets("FM 广播")
# 后
        self.preset_combo = QComboBox()
        self.preset_combo.setMinimumHeight(28)
        # 尺寸策略：条目文本长短不得改变面板最小宽。占位文案
        # 「（无预设：请用“自动扫台”或手动输入频率）」曾经 AdjustToContentsOnFirstShow
        # 把「频率预设」组撑到 332，清空后该最小宽缓存又是 292↔101 两态，右栏随
        # 之在 356↔278 间跳（1000×600 下挤压 13↔15）。与 bookmark_combo 同一策略：
        # 最小宽只按最少字符数算，水平 Ignored 让布局用可用宽度。
        self.preset_combo.setSizeAdjustPolicy(
            QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.preset_combo.setMinimumContentsLength(16)
        self.preset_combo.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self._populate_presets("FM 广播")
```

实测效果：`preset_combo.minimumSizeHint()` 恒为 216，`ControlPanel.minimumSizeHint()`
恒为 278（不再随启动时序跳）。

### 新增守卫用例

`tests/test_preset_combo_stability.py`（4 条参数化 + 2 条）：切模式不清空/不重排预设、
`set_mode` 后任何频段列表都非空、`minimumSizeHint` 与条目文本无关。

## 3. 影响面

| 面 | 影响 |
|---|---|
| 用户可见 | 修复「切模式后频率预设下拉变空」；1000×600 下不再出现 68 px 内容被裁（改为完整可见） |
| 行为 | 预设来源从「当前模式」改为「当前频段」；频段切换逻辑未改 |
| 布局 | `ControlPanel` 最小宽由内容相关变为常量 278；右栏内容宽恒定 288 |
| 测试 | 新增 6 条；`tests/test_ui_layout_locks.py` + `test_three_column_layout.py` 83 passed；`test_mode_switch_preset.py` 17 passed |
| 尺寸计数 | windows 由 13/7↔15/9 双态收敛为 **15/9**；offscreen 由 30/9↔36/15 收敛为 **37/16** |

## 4. 复测口径与结果

```powershell
# windows（release_check._breakdown 同口径：系统 DPR、不设 QT_SCALE_FACTOR）
$env:QT_QPA_PLATFORM="windows"; Remove-Item Env:QT_SCALE_FACTOR -ErrorAction SilentlyContinue
python scripts/ui_narrow_breakdown.py --json

# offscreen
$env:QT_QPA_PLATFORM="offscreen"; $env:QT_SCALE_FACTOR="1"; $env:QT_ENABLE_HIGHDPI_SCALING="0"
python scripts/ui_narrow_breakdown.py --json
```

取 `summary.caliber.overflow_at_declared_minimum.squeezed_count/clipped_count` 与
`overflow_at_1344x700` 的同名字段；`env.device_pixel_ratio` / `font_line_height`
用来证明指纹一致（本次 windows=1.5/18、offscreen=1.0/13，全 13 次相同）。

| 阶段 | windows（1000×600 / 1344×700） | offscreen（1000×600 / 1344×700） |
|---|---|---|
| 修前（`385d0e15`，9 次） | 13/7 五次、**15/9 四次**（5:4） | 4 次：30/9 两次、36/15 两次 |
| 修前（同 base 4+2 次） | 13/7 四次（未命中坏态） | 30/9、36/15（仍双态） |
| **修后（`b0a440e3`，9+4 次）** | **15/9 九次（9:0 单一态）** | **37/16 四次（4:0 单一态）** |

收敛达成。但**收敛到的是「无缓存」那一支**：15/9 与 37/16 分别超过现行
`TEMPORARY_WIDENED`（windows 13/7、offscreen 36/15）**+2 / +1**。

## 5. 反证设计（改回旧行为必须仍能复现 278↔356）

不做「跑一次绿就收工」，按下面三组变异各做一次，每组都要求看到**两个值都出现**：

| 变异 | 必须出现 | 判定 |
|---|---|---|
| 只回退 A（`_populate_presets(mode)` 恢复） | 启动后 `preset_combo.count()==0`，`test_presets_never_empty_after_set_mode` 红 | 证明 A 修的是"清空"而不是尺寸 |
| 只回退 B（去掉 `AdjustToMinimumContentsLengthWithIcon`/`Ignored`） | `preset_combo.minimumSizeHint()` 在多次进程间 **292 与 101 都出现**；`ControlPanel.minimumSizeHint()` **356 与 278 都出现** | 证明 B 修的是最小宽缓存 |
| A+B 全回退 | `ui_narrow_breakdown.py` 连跑 9 次，windows 计数集合必须是 `{13,15}`（越界恒 1） | 证明两态跳变原样回来 |

因为坏态命中率约 50%（9 次实测 5:4），单次复跑可能只命中好态；**反证至少要
9 次 windows + 4 次 offscreen**，并在探针里同时打印

```
ControlPanel.minimumSizeHint().width()   # 期望 356 与 278 都出现
preset_combo.minimumSizeHint().width()   # 期望 292 与 101 都出现
preset_combo.count()                      # 期望旧行为下为 0
```

修后同口径：两个值都必须是单值（278 / 216），出现第二个值即判定回退未生效。

## 6. 后果与后续（必须一起决定，否则 overlap 会确定性 FAIL）

- 修后 windows 15/9 > 13/7、offscreen 37/16 > 36/15 → `release_check.py` 的
  `overlap` 步每次都会 FAIL。**这不是调阈值能解决的**：旧的 13/7、36/15 只有
  在面板撑到 356（右端 68 px 被裁、无滚动条可达）时才成立，是把裁字换成了
  少挤压项的假绿。
- 两条真实可选路径（都不属于本 patch）：
  1. **真窄排**：把静噪行拆成两行（滑杆一行、`自动`复选框一行），release 约 61 px；
     预计 288 宽下「静噪门限 / -50 dB」不再被压，回到 13/7，且内容完整可见。
     需 UX 线出账高度（右栏本就在 `QScrollArea` 内，窗口最小高不变）。
  2. **重新定基线**：由尺寸/打包 owner 依据「内容完整可见」的新口径重测后调整
     `SIZE_LIMITS`（本线不做，避免自己给自己放水）。
- 在二者落地前，本条按「已知偏差」登记（见 `docs/narrow-screen-plan.md`
  §2.3.1）：修前 13/7 约 4/9 概率兜不住；修后确定性 15/9、37/16。

## 7. 执行归属

**本线选择：保留 `b0a440e3`、交给 `参数治理线` 复核，不重做。**
理由：本改动已由 `参数治理线`（即本文件生成方自己的 line）在持锁期间落库，
claim 已释放；重做只会造成同文件重复叠加。本线此后不再写
`desktop/control_panel.py`，只做 §4/§5 的验证与 §6 的登记。
