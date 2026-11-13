# `desktop/main_window.py` 在途 hunk 归属（二）：键盘焦点链 / 右栏浮层

登记日期：2026-11-13
登记人：收尸线（父会话 `6ae85f56a16a76df1598024e`）
触发：`73b75378` 排除的在途行段里，有 **3 组**不属于设备会话写门批次；本文件单独归口。
口径：登记时（HEAD `c17f7011`）`desktop/main_window.py` 为 `-U3` 17 组 / `-U0` 31 段，
20:35 复测 20 组 / 34 段，新增 3 组见 `2026-11-13-main-window-tool-success-wiring.md`。
归属：键盘可达线（P1-37）。

## 一、为什么单独成文

`docs/COORDINATION.md` 已有一节「处理记录（2026-11-13）：键盘全操作——冻结文件内的两处
行为改动报备」（按标题搜「键盘全操作」），把这两处改动写成「本轮在该文件里只改…」，
并给了 waivers 依据与验证口径。**但代码不在 HEAD**：

- HEAD 无 `_window_focus_chain` / `_right_column_focus_widgets`；
- HEAD 的 `_setup_tab_order` 仍是旧版（`HEAD:1954`），调用点在 `HEAD:1952`（旧位置）；
- HEAD 的 `_open_right_overlay` 里没有 `self._right_col.show()`。

也就是说，那份处理记录描述的是**计划中的提交**，记录落了、代码没落。后续任何人读
COORDINATION 会以为已经改完——这是本文件存在的理由。

## 二、3 段逐段定性

| # | 符号 | 改动摘要 | HEAD 是否已有 | HEAD 证据 |
|---|---|---|---|---|
| 1 | `_setup_tab_order`（重写）+ `_window_focus_chain` + `_right_column_focus_widgets` | 显式串「工具栏 → 顶栏 → 左栏 → 右栏顶部『跳到分组』→ 右栏内部（按节顺序）→ 底坞」 | 否 | `HEAD:1954` 仍是 5 行旧实现（只串 `toolbar_mode_combo/theme_combo/lang_combo` 等） |
| 2 | `_setup_tab_order()` 调用点 | 从 `_build_tool_bar` 末尾移到 `__init__` 末尾（右栏/底坞都建完之后） | 否 | `HEAD:1952` 仍在旧位置；无移动后的新注释块 |
| 3 | `_open_right_overlay` | `lay.addWidget(self._right_col, 1)` 之后补 `self._right_col.show()` | 否 | HEAD 无该调用；窄窗打开浮层后右栏仍不可见 |

## 三、同批文件现状（盘上）

| 文件 | 状态 | 说明 |
|---|---|---|
| `desktop/command_palette.py` | **已在 HEAD**（`c4fb5405`） | 「跳到设备」「跳到增益」两条入口（`_jump_to_right` / `_panel_commands`）已随 D1 重构提交一并落库，但**不是在同一次提交里与焦点链一起落的** |
| `tests/test_keyboard_main_flow.py` | 未跟踪（`??`） | 7 条用例：`test_tab_ring_covers_right_column`、`test_explicit_tab_chain_is_visual_order`、`test_controls_outside_explicit_chain_stay_tab_reachable`、`test_tab_order_does_not_jump_from_right_column_to_bottom_dock`、`test_narrow_window_overlay_shows_right_column` 等 |
| `tests/test_command_palette.py`、`tests/test_discoverability_help.py` | 已改未暂存 | 对应两条入口的可发现性用例 |

注意：`command_palette.py` 先落库、焦点链后落库的窗口期内，命令面板会「跳到目标控件」
但焦点链仍是旧的——功能不假，只是 Tab 顺序仍按 P1-37 的缺陷走。

## 四、接取前提

1. `desktop/main_window.py` 现由**窄屏高度链 4px 线**在改（`topBarDetail` 56→52 等），
   必须先与那条线约定提交顺序；不要整文件 `git add`。
2. `python scripts/file_claim.py status desktop/main_window.py` 无活跃持有者，按 §6.1.4
   写接管记录（≤30 分钟宽限、进程、提交、在途四条必填）。
3. `desktop/main_window.py` 仍处 FZ-04 视觉冻结期；`docs/axioms-waivers.json` 里已有对应
   豁免条目（「视觉冻结期，改动需先上报排期」），接取前确认该条仍有效、且覆盖本次符号。
4. `git diff HEAD -- desktop/main_window.py` 逐段辨认，只取这 3 段；其余 14 段属
   `2026-11-13-main-window-session-write-gate.md`，不要混进同一笔。

## 五、验收判据

1. `tests/test_keyboard_main_flow.py` 7 条全绿；其中 `test_narrow_window_overlay_shows_right_column`
   必须真的走「收窄 → 打开浮层 → 右栏可见 → Esc 回到窄条」三段状态。
2. 反证：临时删掉 `_right_col.show()` → `test_narrow_window_overlay_shows_right_column` 必须红；
   临时把调用点移回 `_build_tool_bar` 末尾 → `test_explicit_tab_chain_is_visual_order` 必须红。
3. 不新增/删除控件、不改布局尺寸颜色；提交说明里附「零视觉改动」的核对口径（同 COORDINATION 记录）。

## 六、不接的合法出口

显式回退这 3 段（只回退焦点链，不碰他人的写门 hunk），并在 COORDINATION 的键盘记录下补
一行「代码未落库、已回退」，避免记录与树继续不一致。
