# 主题字典死键清单（2026-11-12 · 只读）

**用途**：回答「哪些键没有任何运行时读取点、能不能删」，并给每个结论附证据。
生成器是 `scripts/report_token_dual_values.py`（只报不拦，退出码恒 0，不进钩子）。
**边界**：本轮只读，未改 `themes.py` / `tokens.py` / 任何产品代码。

## 0. 口径

- 三通道：「QSS」= `themes.py` 的 `_build_palette()` + `palette(role)`（159 处声明）；
  「运行时」= `desktop/` 下 Python 直接取色；「测试」= `tests/` 与
  `scripts/theme_contrast.py` 的 `PAIRS` 门禁表。
- 判定用 ast 静态推断：`theme.colors[...]` / `theme_colors(...)[...]` /
  `c.get(...)`（`c` 由 `theme_colors` / `get_theme(...).colors` 赋值）都算主题读取；
  `status_color("danger")` 按调用点反推为读 `status_danger`（函数体里是动态 `c[key]`）。
- 行号会随编辑漂移；本清单只对 2026-11-12 的 HEAD 负责。

## 1. 全表（20 个键 × 三通道）

| 键 | QSS | 运行时 | 测试 | 状态 |
|---|---:|---:|---:|---|
| `bg` | 5 | 3 | 15 | |
| `bg_alt` | 7 | 1 | 2 | |
| `card` | 17 | 2 | 6 | |
| `text` | 31 | 7 | 5 | |
| `text_secondary` | 5 | 1 | 10 | |
| `text_disabled` | 6 | 1 | 2 | |
| `border` | 24 | 1 | 3 | |
| `grid` | 13 | 0 | 0 | 仅 QSS |
| `primary` | 18 | 4 | 4 | |
| `accent` | 5 | 1 | 3 | |
| `accent_hover` | 0 | 1 | 0 | 只被 `main_window.py:5017` 读 |
| **`success`** | **0** | **0** | **1** | **仅门禁（删键需同步改门禁）** |
| `danger` | 2 | 1 | 1 | |
| `button` | 3 | 0 | 0 | 仅 QSS |
| `button_hover` | 7 | 0 | 0 | 仅 QSS |
| `button_pressed` | 5 | 0 | 0 | 仅 QSS |
| `button_disabled` | 6 | 0 | 2 | 运行时零读（QSS+门禁） |
| `primary_text` | 5 | 0 | 5 | 运行时零读（QSS+门禁） |
| `status_warning` | 0 | 7 | 4 | |
| `status_danger` | 0 | 4 | 1 | |

## 2. 结论

1. **三通道全零、可直接删的键：无。**
2. **运行时零读、且 QSS 也不引用的键：只有 `success` 一个。**
   它还有 1 处门禁配对，所以「删键」要同步改门禁，不能只删字典。
3. **运行时零读但 QSS 在用的键（不能删）**：`grid`、`button`、`button_hover`、
   `button_pressed`、`button_disabled`、`primary_text`。这些键的值仍会经
   `_build_palette()` 进入 QPalette，被 QSS 的 `palette(role)` 消费；删了就掉样式。

换句话说：**20 个键里只有 `success` 是死的**，其余 19 个至少有一个活消费者。
这条结论同时否掉了「顺手清一批没用到的键」的想法——没有一批，只有一个。

## 3. `success` 的证据链

**定义**：5 套主题各写了一份；default `#599788`，其余四套 `#6BA89A`。

**QSS = 0**：`themes.py:660-677` 的 `_THEME_ROLE_MAP` 没有 `success`，Qt 也没有
对应角色，所以 `_build_palette()`（`themes.py:680-696`）不会读它；QSS 全文没有
`palette(success)` 这种东西。

**运行时 = 0**：全仓没有 `theme.colors["success"]` / `c["success"]`。
全局 `COLORS["success"]`（`#6BA89A`）另有 20 处读取（见生成器 A 段），其中 6 处是
界面上当字用：`status_panel.py:863, 959, 1013, 1049, 1093`、`sat_track_panel.py:528`；
这 20 处都与主题键无关。

**测试 = 1**：`scripts/theme_contrast.py:48`
`("success", "bg", 3.0, "成功色")`，由
`tests/test_theme_contrast_gate.py:47-53` 的 `test_all_theme_contrast_pairs_pass`
消费。

**删键的真实后果（重要）**：`audit()` 在 `scripts/theme_contrast.py:59-60` 是

```python
if fg not in theme.colors or bgk not in theme.colors:
    continue
```

也就是说：**删掉主题 `success` 不会让任何测试变红，门禁会静默少检一条**。
所以「能删」的正确做法是「删字典 + 删/改 `PAIRS` 那一行」同笔做，
否则就是拿覆盖率换干净。

**对决策单的影响**：FZ-04 决策单第 3 条的选项 (c)「只改 default 的 `success` 键」
改的是一个运行时无人读的键——不仅视觉空操作，连删掉它界面都不会变。
有效选项只剩 (a)/(b)，或新增 (d) 改那 8 处接线。

## 4. 「半死」清单（运行时 0，但 QSS 仍消费）

| 键 | QSS 处数 | 说明 |
|---|---:|---|
| `grid` | 13 | 网格线 / 滑块轨道 / 进度条底；Python 侧 `ai_panel.py:173` 取的是全局 `COLORS.get("grid")`（恒走兜底），不读主题 |
| `button` | 3 | 按钮常态底 |
| `button_hover` | 7 | 按钮悬停底 |
| `button_pressed` | 5 | 按钮按下底 |
| `button_disabled` | 6 | 禁用底（Qt Accent 角色） |
| `primary_text` | 5 | 焦点环 / 单选实心圆 / tab 选中字 |

这些键"没人直接读"是正常的：它们的设计通道就是 QPalette。要改它们只能改 QSS 的
`palette(role)` 引用，不能删键。

## 5. 复跑

```powershell
python scripts\report_token_dual_values.py
```

期望在 B 段看到：

```
success               0      0    1  仅门禁（删键需同步改门禁）
[死键/仅门禁] success
    证据：scripts/theme_contrast.py:48  [门禁配对]
```

判据局限：ast 静态扫描，动态键只能靠调用点反推；`scripts/` 只把
`theme_contrast.py` 记为测试辅助，其余 `scripts/` 仍算运行时。
若新增了「按表取色」的辅助文件，需要把它加进生成器的 `parse_gate_pairs()`，
否则会重演把 `success` 误判成「可直接删」的错。
