# 同名键双值治理方案（2026-11-12 · 方案稿，不动代码）

**状态**：只出方案。`themes.py` / `tokens.py` / 产品代码一律没动，也不含任何补丁。
**数据源**：`scripts/report_token_dual_values.py`（只报不拦，exit 0）的 A 段原始输出；
行号为 2026-11-12 HEAD（`c296fff7`）实测，会随编辑漂移。
**范围**：主题字典与全局字典同名的 5 个键，一次问完：
`accent`、`success`、`danger`、`status_warning`、`status_danger`。

## 0. 先说一条会推翻「0 读取=可删」的前提

`tokens().status_color()` 在 [tokens.py](file:///d:/mbdsdr/MBDSDR/desktop/tokens.py#L440-L443)
里是：

```python
key = kind if kind.startswith("status_") else f"status_{kind}"
if key not in self.COLORS:
    raise KeyError(f"未知状态色 token: {key}")
fallback = self.COLORS[key]
```

两件事必须记住：

1. **全局那份是 `status_color` 的存在性前提**。`status_*` 三个键即使 0 处直读，
   也不能只删字典——删了之后现有 `status_color("warning")` / `("danger")` 调用会直接
   `KeyError`。「删全局」的成本里必须算上改这段守卫。
2. **传 `bg` 的调用不是「读当前主题那一份」**。同一函数在
   [tokens.py](file:///d:/mbdsdr/MBDSDR/desktop/tokens.py#L451-L458) 会把
   「当前主题值 + 全局值 + 其它主题同名值」放进候选集，取对该背景对比度最高的一个。
   所以下面写「主题读取 7 处」时，`bg` 模式的那几处**名义上也会看全局候选**，
   只是当前返回值不是它。

---

## 1. `accent`

### 现状

| 来源 | 值 | 读取点 |
|---|---|---|
| 主题（5 套） | default `#BF7A4F`、dark `#D4956A`、high_contrast `#FF8C00`、dark_car `#919CAC`、figma `#D9D9D9` | QSS 5（`themes.py:838/840/844/904/923`，全经 `palette(bright-text)`：录音按钮常态底/描边、录音悬停底、滑块横/纵悬停底）；运行时 1（`main_window.py:5525`，传给 `marker` 形参但函数体没用，是死参）；测试 3（`tests/test_ui_contrast.py:127`、`scripts/theme_contrast.py:46-47`） |
| 全局 | `#919CAC` | 运行时 32（字 2、底 4、边框 1、图元 25）；测试 1（`tests/test_theme_tokens.py:65` 钉死值） |

三通道：主题 = QSS 5 / 运行时 1 / 测试 3；全局 = QSS 0 / 运行时 32 / 测试 1。
全局那 32 处几乎全在固定深色岛屿（`rf_sky_view.py` 21 处、`module_panel.py`、
`adsb_map_panel.py` 等），其中 25 处是图元。

### 唯一真值源

**主题那份。** 理由不是「主题听起来更对」，而是三条硬事实：

- 5 处 QSS 是**界面 chrome 的强调面**，必须随主题：high_contrast 要靠 `#FF8C00` 拿 9.00:1，
  figma 要靠 `#D9D9D9` 拿 11.68:1；全局 `#919CAC` 只是恰好等于 dark_car 的一档。
- 反向（全局当唯一源）会让门禁直接红：default 的 `accent/bg` 从 3.10 掉到 2.51、
  `accent/card` 从 3.44 掉到 2.78（`theme_contrast.py:46-47` 要求 3.0），
  `test_ui_contrast.py:120-130` 的 3.0–4.5 登记也会失败。
- 全局那份**不是重复定义，是岛屿调色板**。它跟主题无关是设计（岛内底固定深色），
  正确名字是「岛屿强调色」，不是第二个 `accent`。

### 消掉另一份的代价

两种「消掉」含义不同，成本差一个数量级：

- **只消重名（推荐，视觉 0 变化）**：把全局 `accent` 改名为 `island_accent`。
  接线 32 处 + `test_theme_tokens.py:65` 断言 1 处；`tests/test_ui_contrast.py` 的
  `_BASELINE` 里读 `accent` 的 A2/FROZEN/ISLAND 行要同笔改 token 名（否则扫描不到、
  stale 断言变红），约 20 行；`_LEGACY_READS` 里只有 `light_accent`，不受影响。
  不改任何色值，对比度全部不变。
- **把全局并入主题（视觉会变，属于视觉决策）**：33 处接线全改走主题取值。
  default 下 25 个图元由蓝灰变橙，浅底上的 2 处字从 2.51 → 3.10（仍不到正文 4.5，
  A2 已禁止 accent 当正文）；深岛上的图元对比 default 5.99/5.29 → 6.58/5.81，
  dark/hc/figma 也会换色相。门禁要删约 20 条 `_BASELINE` 行并删
  `test_theme_tokens.py:65`。
- 门禁条目：`theme_contrast.py:46-47` 的 10 条（5 主题 × 2 配对）在两种做法下都保留；
  只有「并入主题」才需要清理 `_BASELINE` 的 accent 行。

### 不改的后果

同一个名字永远指两份值：改 QSS 的人以为顺手改了那 25 个图元，实际没有；
切主题时 25 个图元 + 4 个底 + 2 处字始终不跟着变，用户看到的是半套配色。

---

## 2. `success`

### 现状

| 来源 | 值 | 读取点 |
|---|---|---|
| 主题（5 套） | default `#599788`，其余四套 `#6BA89A` | QSS 0；运行时 0；测试 1（`scripts/theme_contrast.py:48` 门禁配对） |
| 全局 | `#6BA89A` | 运行时 20（字 7、图元 7、底 1、取值 5）；测试 0 |

这是本组里唯一「主题副本完全没人读」的键：三通道 0 / 0 / 1，唯一消费者是门禁。

### 唯一真值源

**全局那份（在当前 A2 结论下）。** 理由：20 个活消费者 vs 0 个；
主题副本连 QSS 的 `_THEME_ROLE_MAP` 都没有入口（`themes.py:660-677` 无 `success`），
删掉它界面一个像素都不变。反过来以主题为源，要改 20 处接线，且 default 下会从
`#6BA89A` 变暗成 `#599788`（字在浅底 2.46 → 3.05，仍不到 4.5；深岛图元
6.10/5.11 → 4.92/4.13，反而变差）——这是一次纯亏损的迁移。

**前提**：如果 A2 最终选 (a)「success 跟随主题当字」，本结论反转，主题那份变成真值源，
需要另写一份 20 处接线方案。所以本键的治理要跟 A2 的 (a)/(b)/(d) 一起拍。

### 消掉另一份的代价

删主题副本（当前推荐）：

- 接线：`tokens.py` 的 `THEME_COLORS` 5 处各删 1 行；`scripts/theme_contrast.py:48`
  删 `("success","bg",3.0,"成功色")` 一行。
- 视觉：**0 变化**（没有任何读取点）。
- 门禁：会少 5 条检查（5 套主题 × `success/bg`），当前全部达标（2026-11-13 复跑 `python scripts/theme_contrast.py` → 全部达标（60 项））。
  必须同笔删 `PAIRS` 那一行——因为 `audit()` 对缺失键是 `continue`
  （`theme_contrast.py:59-60`），只删字典不删配对的话不会红，会**静默少检**。
- `_BASELINE` 不动：那里读的是全局 `success`，与主题副本无关。

### 不改的后果

字典里永远留着一份「只为门禁存在」的成功色：它不会出现在任何界面，
但每次加主题都要抄一遍，而 A2 做「success 当字」时又会误以为主题侧已经准备好了。

---

## 3. `danger`

### 现状

| 来源 | 值 | 读取点 |
|---|---|---|
| 主题（5 套） | default `#B85C5C`、dark `#D47070`、high_contrast `#FF4444`、dark_car `#B85C5C`、figma `#B85C5C` | QSS 2（`themes.py:847` 录音按钮 `:checked` 底、`:848` 描边，经 `palette(shadow)`）；运行时 1（`main_window.py:4997` 提示横幅描边，`c.get("danger", ...)`）；测试 1（`theme_contrast.py:49`） |
| 全局 | `#B85C5C` | 运行时 23（底 3、字 9、图元 6、图例 2、灯 2、边框 1）；测试 0 |

全局 23 处里已有明确去向：A1 的 9 处（status 4 / sat 3 / vfo 1 / ntrip 1）在
草稿补丁里迁 `status_color("danger")`；3 处底（`control_panel.py:999/1007`、
`main_window.py:10240`）等解冻单第 2 条的新「危险底」令牌；剩下 11 处
（`main_window.py:2689/3444/11887` 3 处字、`rf_sky_view.py:4682/5585/5589/5686`
4 处图元、`rf_sky_view.py:2141/2152` 2 处图例、`new_spacetime_panel.py:117` 与
`spectrum_widget.py:142` 2 处图元）是岛内图元/图例/字。

### 唯一真值源

**主题那份，但只限定「危险墨 / 描边 / 图元」这一个角色。**
理由：QSS 的 2 处必须随主题——high_contrast 的录音选中底要靠 `#FF4444` 拿 6.16:1，
dark 靠 `#D47070` 拿 4.45:1；全局 `#B85C5C` 在 dark 上只有 3.31:1，直接掉出门禁。

但**红底不是它的职责**：同一份主题值当底时，深色主题上的白字反而更差
（dark `#D47070` 上白字 3.31、hc `#FF4444` 上 3.41，全局 `#B85C5C` 是 4.45）。
这正是解冻单第 2 条要单独造「危险底」令牌的原因。
所以「唯一真值源 = 主题」这句话只在墨/边/图元范围内成立；底色要等新令牌，
不能顺手用 `danger` 顶。

### 消掉另一份的代价

删全局（按计划分三步走）：

- 接线：23 处。A1 覆盖 9 处（status 4 / sat 3 / vfo 1 / ntrip 1；连同 2 行
  `control:invalid` 共 11 条基线行，视觉变化见草稿 README §3：
  default 4.45/4.01 → 5.02/4.53）；3 处底等新令牌；剩余 11 处要先决定归属
  （岛内专用 token 还是继续用主题 `danger`）。
- 视觉（如果把 23 处全并进主题）：default / dark_car / figma 不变（值相同）；
  dark `#B85C5C` → `#D47070`、hc → `#FF4444`。红字/图标对深底的对比升
  （dark 3.74 → 5.03，hc 4.72 → 6.16）；录音选中底的 card 字也升
  （dark 3.31 → 4.45）；但「红底 + 白字」（control 无效输入）降
  （dark 3.31、hc 3.41，全局是 4.45）——一个值不能同时当墨和当底。
- 门禁：`theme_contrast.py:49` 的 5 条保留（主题还在）；A1 那 11 条 `_BASELINE`
  danger 行按 A1 规则删并降上限；`test_theme_tokens.py:61` 的 `"danger" in COLORS`
  断言要随全局键一起删。

### 不改的后果

切到 dark / high_contrast 时同一屏出现两种红：录音按钮选中底用主题的 `#D47070` /
`#FF4444`，而 23 处红字、红底、红灯用全局 `#B85C5C`。

---

## 4. `status_warning`

### 现状

| 来源 | 值 | 读取点 |
|---|---|---|
| 主题（5 套） | default `#876B27`，其余四套 `#C8A040` | QSS 0；运行时 7（`ai_panel.py:183/581/604/832/1086` 传 `bg`；`sat_track_panel.py:451`、`settings_panel.py:1436` 不传 `bg`）；测试 4（`tests/test_settings_panel_note_contrast.py:69`、`tests/test_ui_contrast.py:172/175/176`） |
| 全局 | `#C8A040` | 运行时 1（`main_window.py:5273` 警告横幅的边框与字，`_BASELINE` 里登记为 FROZEN）；测试 0 |

传 `bg` 的 5 处按候选集取最大值，当前返回值都是 `#C8A040`（与全局相同）；
不传 `bg` 的 2 处才真正返回 default 主题的 `#876B27`。

### 唯一真值源

**主题那份。** 理由：7 处调用、20 条门禁断言（5 主题 × 2 组参数化）都按主题值工作；
全局 `#C8A040` 在 default 浅底上只有 2.21:1，本来就不是一套可用的浅色主题值。
它之所以看起来「还行」，是因为 4/5 套主题恰好也写 `#C8A040`。

### 消掉另一份的代价

删全局：

- 接线：**不是 1 处**。`status_color()` 会先查 `key not in self.COLORS` 抛 `KeyError`
  （`tokens.py:441-443`），所以要先改守卫（改成「任一主题有该键即可」），否则现有
  7 处 `status_color("warning")` 全部炸；然后改 `main_window.py:5273` 走
  `status_color("warning")`，删 `tokens.py:120`，删 `_BASELINE` 里 `main_window:5273`
  那条 FROZEN 行（否则「基线里已不存在的直读」断言红）。
- 视觉：只有 default 主题的这条横幅变化：`#C8A040` → `#876B27`。在固定琥珀底
  `rgba(200,160,64,0.18)` 合成出的 `#EDE4CF` 上，对比 1.94 → 3.99。
  **改善但仍不到 4.5**；要真正达标得连横幅底色一起改，那是视觉决策，不在本方案内。
  其余 4 套主题取值不变。
- 门禁：`test_ui_contrast.py:151-166`（5 条）、`186-194`（15 条）不看全局键，不受影响；
  `test_settings_panel_note_contrast.py:69` 期望的正是主题值，不受影响。

### 不改的后果

default 主题下那条警告横幅的字只有 1.94:1（基本看不清），而另外 7 处警告色都跟着主题；
切主题时它也不变。

---

## 5. `status_danger`

### 现状

| 来源 | 值 | 读取点 |
|---|---|---|
| 主题（5 套） | default `#BD4848`、dark `#CE7676`、high_contrast `#C45C5C`、dark_car `#CF7B7B`、figma `#C96A6A` | QSS 0；运行时 4（`ai_panel.py:609/831/861/1087`，全部传 `bg`）；测试 1（`tests/test_ui_contrast.py:173` 断言 dark_car 的值） |
| 全局 | `#C05050` | 运行时 **0**；测试 0；只在 `status_color` 的守卫与候选集里出现 |

这是 `success` 的镜像：`success` 是「主题死、全局活」，`status_danger` 是
「全局死、主题活」，所以两边「删谁」方向相反，不能套同一个模板。

### 唯一真值源

**主题那份。** 理由：4 处调用全部经 `status_color("danger", bg=...)`，
候选集里主题值在每个真实底色上都胜出（`#1E1E20` 上全局 3.57，主题 3.99–5.37；
`#2B2C2F` 上全局 3.00，主题 3.35–4.50），全局那份从未被选中、也无人直读。
它是旧时代留下的兜底值，不是第二个语义。

### 消掉另一份的代价

删全局：

- 接线：**不是删一行**。与 `status_warning` 共用同一处改造：把
  `tokens.py:441-443` 的存在性守卫改成「任一主题有该键」；`fallback`
  （`tokens.py:443`、`:450`、`:454`）改成「取其它主题的同名值」；
  再补一条反证「未知 kind 仍抛 KeyError」。4 处调用本身不用改。
- 视觉：**0 变化**（全局值当前既不直读、也从不在候选集里胜出）。
- 门禁：`theme_contrast.py` 的 `PAIRS` 没有 `status_danger`；
  `test_ui_contrast.py:151-166`（5 条）、`186-194`（15 条）读主题值，不受影响；
  `tokens.py:121` 删 1 行。

### 不改的后果

字典里永远躺着一个 `#C05050`：谁把它当「全局入口」读（就像 `main_window.py:5273`
对 warning 那样），拿到的颜色与 4 处实际渲染都不同，而且没有任何测试会发现。

---

## 6. 汇总：方向、是否视觉决策、依赖

| 键 | 唯一真值源 | 另一份怎么处理 | 改视觉？ | 依赖 |
|---|---|---|---|---|
| `accent` | 主题 | 全局改名为 `island_accent`（**不要并入主题**） | 否 | 无（机械改名） |
| `success` | 全局（当前 A2 结论） | 删主题 5 行 + 门禁 1 行 | 否 | A2 若选 (a)，方向反转 |
| `danger` | 主题（限墨/边/图元） | 全局 23 处分三步退役 | A1 10 处会变；其余待定 | A1 草稿 + 解冻单第 2 条 |
| `status_warning` | 主题 | 删全局 + 改 `status_color` 守卫 | default 横幅 1.94→3.99（仍不达标） | 需先改 `tokens.py:441-443` |
| `status_danger` | 主题 | 删全局 + 同一处守卫改造 | 否 | 需先改 `tokens.py:441-443` |

**能一次做的（视觉零变化，不需要 A2）**：`accent` 改名、`success` 删主题副本、
`status_danger` 删全局（含守卫改造）。`status_warning` 删全局也能做到视觉零变化，
但 `main_window.py:5273` 在冻结文件里，得等解冻；`danger` 的全局退役必须等 A1 与
解冻单第 2 条，不能提前。

## 7. 复跑与证据

```powershell
python scripts\report_token_dual_values.py
```

本方案所有「读取点数」都来自该脚本 A/B 段；对比度数字用
`sRGB 线性化 + (Lmax+0.05)/(Lmin+0.05)` 现算。行号漂移后以脚本输出为准。
