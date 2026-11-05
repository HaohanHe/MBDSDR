# 对接说明：`preset_combo` 清空后最小宽缓存 → 右栏 278↔356 两态（结案：①+②）

> 基线 commit：`b0a440e3`（① 已落库）；② 静噪行拆行的实测在本文件 §2.3/§4 记录。
> 占用口径：`desktop/control_panel.py` 由本线持有，claim owner 现按
> `线名/会话前 8 位` 记为 **`参数治理线/59ca04d1`**（改名原因见
> `docs/COORDINATION.md` §6.1 备忘：旧名 `参数治理线` 无会话后缀，别线无法反查）。

## 0. 一句话（已结案，2026-11-06 06:45 更新）

坏态根因：`set_mode` 把解调模式名当频段名 → `preset_combo` 被 `clear()` →
`QComboBox` 对占位长文本的最小宽缓存（292↔101）让右栏在 **356↔278** 两态跳。
`b0a440e3` 修掉根因（预设跟频段 + 稳定宽策略）后收敛成单一态，但落在坏的那一支
（windows 15/9 ×9、offscreen 37/16 ×4，超现行阈值 +2/+1），**不能结案**。

最终处置 = ① 预设跟频段 + 稳定宽策略 **＋** ② 静噪行把「自动」拆成独立行。
实测 **windows 13/7 九次、offscreen 35/14 四次**（单一态，均 ≤ `TEMPORARY_WIDENED`
的 13/7、36/15），右栏内容完整可见。`setMinimumContentsLength` 保持 16。

> 实测否掉的中间方案：把 `setMinimumContentsLength` 16→8（A 路）。windows 仍
> 15/9 ×9，offscreen 变 36/15 ×4 —— 减的那 1 项只是把 `sizeHint`（280→176）缩小到
> 低于可分宽度，最长预设文本实测 416 px 仍 > 右栏内容 288，文字照样省略。
> 这是改「声明宽」不是改「可见宽」，不采用（详见 §5）。

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

### 改动 C（本笔新增，B 路）：静噪行拆行

锚点 `self.squelch_val.setMaximumWidth(80)` 之后原为 `sq_row.addWidget(self.auto_squelch_check)`
+ `rx_layout.addLayout(sq_row)`；改成：

```python
        sq_row.addWidget(self.squelch_val)
        rx_layout.addLayout(sq_row)

        # 「自动」独立成行：静噪行三项最小宽 60+84+43 + 两段 spacing 已占满
        # 右栏内容宽 242，再把「自动」塞进来会把「静噪门限」压到 45、
        # 「-50 dB」压到 36（sizeHint > width+2 即计为挤压）。拆行后两行各自
        # 最小宽都 < 242，文字不再被压缩；控件本体/信号/语义均未变。
        sq_auto_row = QHBoxLayout()
        self.auto_squelch_check = QCheckBox("自动")
        ...（原有 tooltip / setChecked / toggled 接线逐字保留）
        sq_auto_row.addWidget(self.auto_squelch_check)
        sq_auto_row.addStretch(1)
        rx_layout.addLayout(sq_auto_row)
```

为什么必须拆而不是「只把标签设 Fixed」：行首选宽 253 > 内容宽 242，标签固定后
缺口只会转移到滑杆/复选框（滑杆不在挤压口径里，但复选框会越界，`clipped` +1），
拆行才是真正把首选宽降到 199（windows）/ 282（offscreen）。

## 3. 影响面

| 面 | 影响 |
|---|---|
| 用户可见 | 修复「切模式后频率预设下拉变空」；1000×600 下不再出现 68 px 内容被裁；静噪「自动」独占一行（勾选语义不变） |
| 行为 | 预设来源从「当前模式」改为「当前频段」；静噪行控件顺序调整，信号接线未改 |
| 布局 | `ControlPanel` 最小宽 278 ≤ 内容宽 288；右栏内容完整可见；右栏高度 +1 行（在 `QScrollArea` 内，窗口最小高不变） |
| 测试 | 新增 6 条；`test_ui_layout_locks` + `test_three_column_layout` 83 passed；`test_mode_switch_preset` 17 passed；`test_settings_semantics` / `test_replay_squelch_margin` / `test_squelch_baseline` 55 passed |
| 尺寸计数 | windows 由 13/7↔15/9 双态收敛为 **13/7**；offscreen 由 30/9↔36/15 收敛为 **35/14** |

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
| ①`b0a440e3`（9+4 次） | **15/9 九次** | **37/16 四次** |
| ②A：N 16→8（9+4 次） | 15/9 九次（**未变**） | **36/15 四次** |
| **③定案 ①+B（9+4 次）** | **13/7 九次（9:0）** | **35/14 四次（4:0）** |

结论：定案两指纹都是单一态，且 windows 13/7、offscreen 35/14 均 ≤ 现行
`TEMPORARY_WIDENED`；`clipped` 恒 1（既有口径，在限内）。**不需要重定基线。**

A 路为何不采用：offscreen 那 1 项是 `preset_combo` 的 `sizeHint`（280）略大于
可分宽（270）而被计入挤压；把 N 降到 8 只是把 `sizeHint` 缩到 176，**控件实际宽度
与省略行为一字未变**（最长预设文本实测 416 px，仍远大于右栏内容 288）。
这属于「改声明宽让计数好看」，与本仓「不靠裁字换绿」的口径冲突。

## 5. 反证设计（改回旧行为必须仍能复现 278↔356）

不做「跑一次绿就收工」，按下面四组变异各做一次，每组都要求看到**两个值都出现**：

| 变异 | 必须出现 | 判定 |
|---|---|---|
| 回退 ①-a（`_populate_presets(mode)` 恢复） | 启动后 `preset_combo.count()==0`，`test_presets_never_empty_after_set_mode` 红 | 证明这一处修的是"清空"而不是尺寸 |
| 回退 ①-b（去掉 `AdjustToMinimumContentsLengthWithIcon`/`Ignored`） | `preset_combo` 宽提示在多次进程间 **292 与 101 都出现**；`ControlPanel.minimumSizeHint()` **356 与 278 都出现** | 证明这里修的是最小宽缓存 |
| ①-a+①-b 全回退 | `ui_narrow_breakdown.py` 连跑 9 次，windows 计数集合必须是 `{13,15}`（越界恒 1） | 证明两态跳变原样回来 |
| 回退 B（把「自动」放回静噪行） | windows **15/9**、offscreen **37/16**（实测已复现，与 ① 的 9+4 次逐字一致） | 证明 13/7、35/14 是拆行的结果，不是阈值/口径变了 |

因为坏态命中率约 50%（9 次实测 5:4），单次复跑可能只命中好态；**反证至少要
9 次 windows + 4 次 offscreen**，并在探针里同时打印

```
ControlPanel.minimumSizeHint().width()   # 回退后期望 356 与 278 都出现
preset_combo.sizeHint().width()          # 回退后期望 292 与 101 都出现
preset_combo.count()                     # 回退后期望为 0
```

修后同口径：两个值都必须是单值（278 / 216），出现第二个值即判定回退未生效。

## 6. 后果与后续

- 定案后 windows 13/7、offscreen 35/14，**没有再超 `TEMPORARY_WIDENED`**，
  `release_check.py` 的 `overlap` 步口径恢复为「单态且在上限内」。因此
  **不需要走「重定基线」**，也没有要还的阈值债。
- 旧口径的代价已清：修前 13/7 那一支靠右栏内容被裁 68 px（`AlwaysOff` 滚动条
  不可达）换来的；现在 13/7 是在内容完整可见（`ControlPanel.minimumSizeHint()`
  = 278 ≤ 288）下测得。
- 仍保留的已知偏差只有 `clipped` 恒 1（offscreen 面板 334 > 视口 288 的那 1 项，
  修前修后都在，且在限内），登记在 `docs/narrow-screen-plan.md` §2.3.1。
- 若后续有人把「自动」放回静噪行，或改回无会话后缀的 claim 名，前者会立刻回到
  15/9、37/16（§5 第四行已实测），后者见 `docs/COORDINATION.md` §6.1 备忘。

## 7. 执行归属

**已由本线执行**：① `b0a440e3`（持锁期间落库），② 静噪行拆行（本笔，claim owner
`参数治理线/59ca04d1`，`desktop/control_panel.py` 与 `docs/COORDINATION.md`）。
无跨线抢文件；claim 名已按 §6.1 新规改为「线名/会话前 8 位」。
