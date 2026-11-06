# 基线红台账与 owner（`tests/test_ui_wiring_audit.py` 4 条 + 1 条连带，2026-11-05/06）

> 用途：基线红不再是"非本次引入"就免票。每条红必须有：断言内容、当前失败原因、
> 归类、owner、修复动作、验收判据、反证。
> 基线 commit：`d1616f9c`；取证命令见文末。
> 连带第 5 条（`"20 dB" != "20dB"`）已于 `abc52bd8` 由 `设置架构线` 按不变式重写销账
> （`test_ui_stability_resources.py` 全文件 24 passed），台账只保留 4 条。

## 台账

| # | 用例 | 断言什么 | 为什么现在过不了 | 归类 | owner | 修复动作 | 验收 / 反证 |
|---|---|---|---|---|---|---|---|
| 1 | `tests/test_ui_wiring_audit.py::TestBookmarkDeletePersisted::test_delete_writes_to_store` | 删书签后 `cp._bookmarks == []` 且 `store.removed == ["98.500MHz FM"]` | `control_panel._on_bookmark_del` 在 HEAD `1046-1051` 新增 `QMessageBox.question` 二次确认（默认 `No`）；`tests/conftest.py` 模态守卫把它拦成"取消"，于是 `return` 发生在 `del self._bookmarks[idx]` 之前 | **产品真变了**（交互加确认，测试没桩） | 引入方 `设置架构线`；测试桩执行 `ux-basis` | 用例内 `monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)`；**不放宽**两条断言（内存删 + 落盘删都要保留） | 桩在 → 绿；删桩 → 立刻回红；再把 `_remove_bookmark_persisted(name)` 删掉 → 仍红（证明断言还在咬东西） |
| 2 | `tests/test_ui_wiring_audit.py::TestBookmarkDeletePersisted::test_store_unavailable_does_not_raise` | 仓库 `remove` 抛 `OSError` 时删除不抛异常，且 `cp._bookmarks == []` | 同 #1：确认框先被拦成取消，`_bookmarks` 没被删 | **产品真变了**（同 #1） | 同 #1 | 同 #1 的桩 | 同 #1 |
| 3 | `tests/test_ui_wiring_audit.py::test_no_new_dead_interactive_widgets` | 桌面端不得存在"既无 `signal.connect` 又无人读取"的交互控件；当前报 `desktop/spectrum_widget.py:2298 self._more_btn` | `_more_btn` 用 `QToolButton` + `setMenu(more_menu)`（HEAD `2311`）接线，菜单项各自 `connect`；扫描器 `_scan_file` 只认 `.connect` 和 `READ_METHODS`，**不认 `setMenu`** | **扫描器盲区**（产品无缺陷） | `ux-basis`（测试侧） | `_scan_file` 把 `setMenu` 计入接线（与 `connect` 同级，附注释说明"弹出菜单即动作入口"） | 识别 `setMenu` → 绿；临时去掉识别 → 回红 |
| 4 | `tests/test_ui_wiring_audit.py::test_allowlist_entries_still_unconnected` | `ALLOWED_UNCONNECTED` 里的条目若已接线，必须从白名单删除；当前报 3 条 | HEAD 已存在 `device_panel.py:145 dev_combo.currentIndexChanged.connect`、`new_spacetime_panel.py:401-402 lat_spin/lon_spin.valueChanged.connect`，白名单没跟着删 | **白名单过期**（产品已接线） | `ux-basis`（测试侧） | 删掉 `device_panel.py:self.dev_combo`、`new_spacetime_panel.py:self.lat_spin`、`self.lon_spin` 三条 | 删后 → 绿；恢复任一条 → 回红 |


## owner 规则（本仓执行口径）

1. **产品真变了**（#1/#2）：测试桩由引入方负责或明确授权执行，assigned owner 写引入方；
   "不是本次改动引入"不是免票，必须给出修复日期或明确的接手人。
2. **白名单/扫描器**（#3/#4）：测试基础设施属 `ux-basis`，因为白名单是这份审计的自己人维护的。
3. **断言过期**（#5）：谁改动该控件谁连带改断言；不允许"删断言换绿"。

## 取证命令（可复现）

```powershell
# 1. 复现 4 条红
$env:QT_QPA_PLATFORM="offscreen"
python -m pytest tests/test_ui_wiring_audit.py -q --tb=line
# → 4 failed, 7 passed

# 2. 证明四条失败条件都在 HEAD（不是工作树里新长出来的）
git show HEAD:desktop/control_panel.py | Select-String "QMessageBox.question"
git show HEAD:desktop/spectrum_widget.py | Select-String "_more_btn.setMenu"
git show HEAD:desktop/device_panel.py | Select-String "dev_combo.currentIndexChanged.connect"
git show HEAD:desktop/new_spacetime_panel.py | Select-String "lat_spin.valueChanged.connect"

# 3. 连带第 5 条
python -m pytest tests/test_ui_stability_resources.py -q -k nan_gain --tb=line
# → AssertionError: assert '20 dB' == '20dB'
```

## 当前状态（2026-11-06 实测）

- #1-#4：`ux-basis` 已在**测试侧**修复（只改 `tests/test_ui_wiring_audit.py`，没动任何产品行为）：
  - #1/#2：`monkeypatch` 桩 `QMessageBox.question → Yes`，两条落盘断言原样保留；
  - #3：扫描器把 `setMenu` 计入接线（注意 owner 取控件本体 `self._more_btn`，
    不是 `sig_node.value`，第一版就是在这里漏了）；
  - #4：删掉 3 条过期白名单（`device_panel.py:self.dev_combo` /
    `new_spacetime_panel.py:self.lat_spin` / `self.lon_spin`）。
  - 复跑：**11 passed**（原 4 failed / 7 passed）。
- #5（连带红）：**已销账**——`设置架构线` 于 `abc52bd8` 按不变式重写
  （不再比对字面量空格，改为 `before` 快照 + NaN 保持原值），
  `test_ui_stability_resources.py` 全文件 **24 passed**。本台账不再跟踪。
  遗留提醒：P2-C 落库后该用例的 `mw` 夹具若仍是未连接态，`before` 会变成「—」，
  需要先 `set_sdr_connected(True)` 再 `set_gain(20.0)`（断言不变）——见 handoff-p2c §4。
- **入库归属（已结案）**：上述 4 条修复被 `脆断言清理线` 的提交 `86fdbd0b`
  （2026-11-06 03:07:30，"test(脆断言): …"）一并带入库；该 commit message 未提及
  本单。已由收口方登记为形态「内容正确但提交说明不实」，**判定不回退**
  （双方复核 + 11 passed）。本线时序失误（编辑前未复核 claim）不再补交代，
  执行口径改为：**每次编辑前先跑 `python scripts/file_claim.py status`**。
- 纪律：如果 `control_panel._on_bookmark_del` 的确认方式从 `QMessageBox.question`
  换成自定义对话框，**#1/#2 的桩要同步换**，不许把它当成"测试又坏了"。

## 反证（真跑了，不是口头）

| 变异 | 实测结果 |
|---|---|
| 扫描器 `node.func.attr in ("connect", "setMenu")` → `in ("connect",)` | 1 failed, 10 passed（`self._more_btn` 重新被判死控件） |
| 白名单恢复 `device_panel.py:self.dev_combo` | 1 failed, 10 passed（报"该控件已有 connect"） |
| #1 的确认桩 `QMessageBox.Yes` → `No` | 1 failed, 10 passed（`_bookmarks` 非空，正是 #1 的原始症状） |
| 三处全部恢复 | **11 passed** |

## 验收记录

```powershell
$env:QT_QPA_PLATFORM="offscreen"
python -m pytest tests/test_ui_wiring_audit.py -q --tb=short
# → 11 passed
```
