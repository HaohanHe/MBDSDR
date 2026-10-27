# Phase84 · 活动布局 A2「排」：右栏页签可拖动重排

> 日期：2026-10-27 ｜ 依据：phase83 蓝图 §0（布局是活的，五动词：拖/折/搬/排/存）
> 与 §4 阶段 A（A1 左栏折叠已落地 → A2 页签重排）。
> 前置动作：先用 `--offscreen --test-source --snapshot` 截图把当前版本看明白，
> 从截图里抓到一个真实缺陷（见 §1），本批连修带做。

---

## 1. 看明白当前版本：截图实证的一个 P0

无头截图 `cpp/build-local/mbdsdr.exe --offscreen --test-source --snapshot`
（测试信号、无硬件）显示右栏**整块空白**。根因不是渲染故障：

- 右栏用"分组标题页签"（`解码 / 观测 / 系统`）做分节，标题页签是
  `setTabEnabled(false)` 的**空 QWidget**，只当标签用；
- 恢复路径用位置索引 `ui/rightTabIndex` 落盘，而"分组标题"也是最靠前的
  页签（索引 0）。记住的位置一旦指向标题页签，右栏就渲染成一片空白；
- 兜底逻辑只判 `isTabVisible()`——标题页签是**可见**的（只是 disabled），
  于是兜底根本不触发。

**修复**：新增 `ensureRightTabSelectable()`，判定改为
"enabled && visible"；关闭页签 / 设置对话框批量显隐 / 启动恢复三条路径统一调用。
修复后截图：右栏落在 CW 面板，正常显示 `WPM: -- / 清空 / 切换到 CW 模式开始解码`。

顺带修掉两处仍在用**译文 `tabText()`** 匹配面板身份的遗留
（`applyPanelVisibility` / `currentHiddenRightPanels`），改走规范面板 id
`kPanelIdProp`——与 7d3fa3f「身份归真」同词汇，避免 en/ja 下失配。

## 2. A2 本体：拖得动、排得下、存得住

| 动词 | 实现 | 落盘键 |
|---|---|---|
| 排 | `rightTabs_->tabBar()->setMovable(true)` + `tabMoved → scheduleSave()` | — |
| 存（顺序） | `currentRightTabOrder()`：规范 id 序列；分组标题以 `#` + 标题同行 | `ui/rightTabOrder` |
| 存（当前） | 当前面板改记 **规范 id**（位置索引在可重排后必然失效） | `ui/rightTabCurrent` |

设计不变式：

1. **身份永远是规范面板 id**，不是译文、不是位置。
2. **未知/过期 id 跳过，绝不凭空造页签**：未来新增面板也不会毁掉用户排的顺序
   （缺失的面板保持内建相对位置，排到尾部）。
3. **位置索引降级为兜底**：`ui/rightTabIndex` 只在一个版本内兼容旧配置文件。
4. 分组标题随流而动——它是标签不是目的地；当前页永不落在它上面。

## 3. 测试（三个新用例，随批不欠账）

`cpp/tests/test_ui_integration.cpp`：

- `rightTabReorderPersistsRoundTrip`：页签 `isMovable`、`moveTab` 后顺序经真实
  `saveUiState()` 落盘、二次启动按同一顺序回来（身份是 id 序列）。
- `rightTabOrderStaleEntriesIgnored`：顺序里塞入"不存在的分组"和 `ghost-panel-9000`
  → 只按认识的排，页签数量与 canonical 全集一字不差（无幻影页签）。
- `rightTabCurrentNeverLandsOnGroupHeader`：两条路径（旧位置索引指向标题 /
  current-id 直接指到标题）都不得停在空白标题页。

`cpp/tests/test_spectrum_display.cpp`：把 `realFrameDrivesHistory` 的失败信息
改成带 rgb 实测值（原来的 "should be dark" 不告诉你到底多亮）。

**验证**：`ctest -R "ui_integration|settings_orphans|ui_audit|collapsible|spectrum"`
14 项中 13 绿，仅 `font_cjk_guard` 为已知 Windows offscreen 环境红；
`tests/test_cpp_no_bare_qsettings.py` 2 项绿。

## 4. 下一步（按蓝图顺序）

- **A3 页签浮出**（双击/拖出 → 独立工具窗，关闭归位）。
- **A4 命名布局快照**（复用 WorkspaceLayout JSON，专注/分析/全景）。
- **B1 右缘竖滑条**（Zoom/Max/Min 常驻，顶栏再减 3 件）——对应"信号高度/广度
  分别可调"的诉求。
- **B3 色图 JSON 资源化**（`res/colormaps/*.json`，14 张 SDR++ 色图）。
