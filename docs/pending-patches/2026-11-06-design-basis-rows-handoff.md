# 交接单：`docs/design-basis.md` §4 #6/#9/#10 三行判定替换

> 收件方：`docs/design-basis.md` 的 owner（UI 账本线）
> 提出方：UI 并进调研线
> 性质：**交接单，不改账本文件**。本单只给出「原文 → 建议替换文本」与依据；由 owner 落库。
> 落库前请复核本单引用的 `path:line` 仍指向 HEAD 同一内容；若已漂移，以 HEAD 为准并更新本单。
> 参考基线：`docs/pending-patches/2026-11-06-ui-integration-baseline.md` §4、§4.1。
> 状态：**已处理（2026-11-11，布局线/59ca04d1）**——#6/#9/#10 三行已按本单落库；
> 同轮按用户指令对 §4 其余七条做了逐条定性（#1/#2/#3/#4/#5/#7/#8 标「仍成立」并补
> `path:line` 证据），见 `design-basis.md` §4。

---

## 一、#6 参数分组 / 恢复默认反馈

**原文（design-basis §4 第 6 行）**

```
| 6 | 参数面板分组 + 恢复默认有反馈（satdump `usrp_sdr.cpp:L55`） | `ServiceSettingsPanel` 两个 groupbox + 无反馈的 reset | 分组粒度粗，reset 看不到变化 | 分组按子系统，reset 后给「已恢复 N 项」 |
```

**问题**：判定已过期。设置面板已按 schema 渲染 7 组，且有行为断言。

**建议替换**

```
| 6 | 参数面板分组 + 恢复默认有反馈（satdump `usrp_sdr.cpp:L55`） | **已修**：按 schema 渲染 7 组（`desktop/settings_panel.py:635` `RightSection`）；reset 有可见回执，行为断言见 `tests/test_settings_panel_observability.py:112`（`"已恢复本组默认值"`） | —（已销项） | 已修 — `a96c010f` |
```

**依据**

- `desktop/settings_panel.py:635`：`section = RightSection(group, body, ...)`
- `tests/test_settings_panel_observability.py:112`：`assert panel.error_label.text() == "已恢复本组默认值"`
- 提交 `a96c010f`（feat(settings): 全量设置面板 + schema 单一事实源…）

---

## 二、#9 设计 token

**原文（design-basis §4 第 9 行）**

```
| 9 | 深色 + 密集 + 少留白，且视觉数值可复现 | `tokens().SIZE` 只有 5 个尺寸键；浮层 152 / `0.55` / 10 / 12 全手写 | 间距/圆角/浮层宽没有系统 token，评审无处可指，下次必再手写 | 把间距、圆角、浮层宽/透明度收进 `desktop/tokens.py:DesignTokens`；本次令牌补丁（像素不变）是第一步 |
```

**问题**：`RADIUS/SPACING/SIZE/RATIO/MOTION` 已全部建立，原「只有 5 个尺寸键」不再成立；
仅剩 `0.55 / 10 / 12` 三个值未收 token。

**建议替换**

```
| 9 | 深色 + 密集 + 少留白，且视觉数值可复现 | **已修（余 3 个手写值）**：`desktop/tokens.py:271-343` 已含 `RADIUS` / `SPACING` / `SIZE` / `RATIO` / `MOTION`；浮层宽 152 已收 `SIZE["planetarium_strip_w"]`；`0.55 / 10 / 12` 仍手写 | 剩余 3 个值无基准，评审仍无处指 | 已修 — `5ba61b70`；剩余 3 值待用户裁定「自拟但冻结」（见 §待确认 4）。**注：该提交未附测试，「像素不变」未独立取证** |
```

**依据**

- `desktop/tokens.py:271-343`：`RADIUS` / `SPACING` / `SIZE` / `RATIO` / `MOTION`
- `desktop/tokens.py:326`：`"planetarium_strip_w": 152`
- 提交 `5ba61b70`（只改 `main_window.py` + `tokens.py` 共 6 行，无测试）
- `design-basis.md:194` 已登记 `0.55 / 10 / 12` 仍无基准

---

## 三、#10 1366×768 显示完整性（**重点：原「已修」不成立**）

**原文（design-basis §4 第 10 行，节选）**

```
| 10 | **1366×768 笔记本必须能完整显示** | 实测（`scripts/ui_narrow_breakdown.py`，双指纹）：**offscreen 1625×729**；**真屏 `windows`（DPR 1.5、行高 18）1237×799** … | **结论：1366×768 真机当前无法完整显示，瓶颈是高度（溢出 71px）…** | 改法见 [narrow-screen-plan.md](narrow-screen-plan.md)。**本行只记录问题，不改代码。** |
```

**问题**：该行还停在改前数字（1625×729 / 1237×799），而代码已改为按屏幕取 `1000×680` 兜底；
但**旧验收证据本身无效**，不能直接改写成「已修」。

**为什么原「已修 + 双指纹过线」不成立**

1. `scripts/ui_narrow_breakdown.py:162` 固定 `win.resize(1000, 600)`，输出的是
   `minimumSizeHint` 求和，不是目标尺寸下的实际窗口尺寸。
2. `docs/narrow-screen-plan.md:22` 的 `960×633` 自述为「内容最小宽提示」；
   同文 `:295-308` 明确「960 不是声明最小宽 1000」。
3. 实际最小尺寸由 `desktop/main_window.py:852`
   `setMinimumSize(max(860, _min_w), max(560, _min_h))` 决定，实测环境为 `1000×680`；
   `960×633` 低于该下限，**物理不可达**。
4. `scripts/ui_baseline.py:125` 已记录「1000x600 档窗口被最小高 680 夹成 1000x680」。
5. `a2be554a` 的测试只断言子控件几何（命令栏宽、谱面高），
   **无一条断言「实际窗口尺寸 == 请求尺寸」**。
6. 提交说明中的 `900x620` 在本仓库无脚本/测试可复现（全仓 grep 0 命中）。

**建议替换**

```
| 10 | **1366×768 笔记本必须能完整显示** | **待复验**：代码已改为按屏幕取 `1000×680` 兜底（`desktop/main_window.py:842-852`），但旧证据（`narrow-screen-plan.md` 的 `1265×618` / `960×633`）是 `minimumSizeHint`，且 `960×633` 低于实际最小 `1000×680`，不可达；`a2be554a` 无「实际尺寸 == 请求尺寸」断言 | **不能判定是否完整显示**；原「已修」判定作废 | 按 `docs/pending-patches/2026-11-06-ui-integration-baseline.md` §4.1 做真屏复验：读 `minimumSize()` 与 `availableGeometry()`，分别跑 DPR 1.0 / 1.5，记录请求 vs 实际；通过后再改「已修」 |
```

**校验代码/命令原文（供复核）**

```python
# scripts/ui_narrow_breakdown.py:163-170（现版本：无字体时直接拒绝出数）
fams = QFontDatabase.families()
if not fams:
    print("错误：QFontDatabase.families() 为 0（平台 %s）。"
          "字体会退化成方框，文字尺寸/控件高/挤压数全部不可信；"
          "本脚本拒绝出数。请改用 QT_QPA_PLATFORM=windows "
          "（窗口用 WA_DontShowOnScreen 不上屏）。" % app.platformName(),
          file=sys.stderr)
    return 2

# scripts/ui_narrow_breakdown.py:175-179（固定请求 1000×600，未请求 1344×700/1366×768）
win.setAttribute(Qt.WA_DontShowOnScreen, True)
win.resize(1000, 600)
win.show()
_pump(app)
```

```python
# scripts/ui_baseline.py:125（自述请求被最小尺寸抬高）
"measured": "1000x600 档窗口被最小高 680 夹成 1000x680；..."
```

运行命令（文档唯一口径，`narrow-screen-plan.md:28-31`）：

```powershell
python scripts/ui_narrow_breakdown.py            # 人读
python scripts/ui_narrow_breakdown.py --json     # 机器对账
```

**结论**：脚本从未以 `1344×700` 或 `1366×768` 为目标请求尺寸跑过，也未断言「实际 == 请求」；
`1265×618 / 960×633` 是脚本内部的「内容最小尺寸提示」，不能作为「1366×768 可完整显示」的证据。


**依据**

- `desktop/main_window.py:842-852`（`_min_w = min(1000, 0.94W)` / `_min_h = min(680, 0.94H)`）
- `docs/narrow-screen-plan.md:21-22`、`:295-308`
- `scripts/ui_baseline.py:125`
- 提交 `a2be554a`（测试无实际尺寸断言）
- `docs/pending-patches/2026-11-06-ui-integration-baseline.md` §4 #10、§4.1

---

## 四、落库要求（给 owner）

1. 只改上述三行；不得顺手改其它行或基线文件。
2. #10 在复验完成前**只能写「待复验」**，禁止写「已修」。
3. 若把 #6/#9 标为「已修」，按闸门要求保留四元组：
   `<结论> — <hash> — <path:line> — <改动后字面量>`。
4. 复验通过后，#10 需附「实际尺寸 == 请求尺寸」的断言位置，而不是 hint 数值。
5. 落库后请把本单标记为已处理（或删除），避免二次引用过期结论。
