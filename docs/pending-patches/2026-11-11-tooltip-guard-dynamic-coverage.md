# 交接单：tooltip 守卫的「动态文案」覆盖盲区（2026-11-11 实测）

> 来源：W71–W77 策论线。起因：`tests/test_tooltip_guard.py` 的 `scan_tooltips()`
> 只收 AST 字面量，f-string / `+` / `.format()` / 变量一律 `continue` 跳过。
> 本文只做定量与判别设计，**未改任何代码**。

## 0. 结论先行

1. 盲区是整类，不是一处：`desktop/` 227 个 `setToolTip(` 调用点里，
   **76 个（33.5%）静态守卫完全看不到**；另有 9 个是 `setToolTip("")`（清空
   提示），本就该跳过，**非空的字面量覆盖是 100%**——所以「漏」只在动态形态。
2. **不该一刀切全收**。76 条里 38 条是纯变量透传（`setToolTip(self._tip)`），
   静态层面没有可校验文本；剩下的 38 条能取出常量片段，但按现有「长度 ≥12」
   规则直接套到片段上会 **一次红 32 条**（全是「⚠」「收起」「升起」这类碎片）。
3. 只有 **11 条**属于「诚实性文案」（含量纲/绝对数值/来源与置信标注；另有
   1 条 `"%s"` 被当百分号的误报），值得静态守；按候选规则收紧后**实际只会
   红 2 条**，都是「补一个来源限定词」的一行改动，不是文案造假。
4. 建议顺序：**先加登记表（只建立边界，不产生红）→ 再扩扫描器且只查诚实性
   片段、只查新增/改动（棘轮）**。不要先扩扫描器。

## 1. 统计口径与实测数字

扫描范围：仓库内所有 `.py`（排除 `.git` / `build*` / `node_modules` 等）。
`.audit_tmp/` 下 42 处 `setToolTip` 属未入库的一次性审计产物（`git ls-files`
为空），不计入。

| 口径 | 数量 |
| --- | --- |
| 入库代码 `setToolTip(` 调用点总数 | **228**（`desktop/` 227 + `scripts/` 1） |
| `desktop/` 字面量（非空）调用点 | 142 |
| `scan_tooltips()` 实际返回的唯一条目 | 136（142 处里有 6 处文案重复） |
| `desktop/` 字面量但为空串（`setToolTip("")`） | 9（`constellation_panel` 1、`control_panel` 3、`device_panel` 2、`gain_panel` 2、`main_window` 1） |
| `desktop/` 动态形态（守卫看不到） | **76** |
| 　　其中纯变量/无常量片段（`Name`/`Attribute`/`Subscript`/非字符串常量） | **38** |
| 　　其中有常量片段的 f-string / `+` / `.format()` / 条件表达式等 | 38 |

### 76 条按形态

| 形态 | 条数 | 静态可取的信息 |
| --- | --- | --- |
| `variable`（`self._tip`、整段变量） | 34 | 无（只能登记或跑运行时断言） |
| `fstring` | 24 | 全部常量片段 + 插值表达式源码（`ast.unparse`） |
| `IfExp`（三元） | 9 | 每个分支的常量片段 |
| `Call`（`.format()` / `join` / 函数返回值） | 4 | 部分可解 |
| `concat`（`+` 拼接） | 2 | 各常量片段 |
| `BinOp`（`%` 格式化） | 1 | 格式串片段 |
| `BoolOp`（`a or "兜底文案"`） | 1 | 兜底文案片段 |
| 非字符串 `Constant`（`setToolTip(None)`） | 1 | 无 |

### 76 条按文件

| 文件 | 条数 | 其中零片段 |
| --- | --- | --- |
| `desktop/control_panel.py` | 32 | 17 |
| `desktop/main_window.py` | 13 | 5 |
| `desktop/device_panel.py` | 4 | 3 |
| `desktop/rf_sky_view.py` | 4 | 3 |
| `desktop/settings_panel.py` | 4 | 3 |
| `desktop/spectrum_widget.py` | 4 | 4 |
| `desktop/constellation_panel.py` | 2 | 1 |
| `desktop/gain_panel.py` | 2 | 0 |
| `desktop/module_panel.py` | 2 | 0 |
| `desktop/sat_track_panel.py` | 2 | 0 |
| `capability_drawer` / `dock_layout_manager` / `error_remedy` / `new_spacetime_panel` / `satellite_image_panel` / `shortcuts_panel` / `weather_panel` | 各 1 | 2（`shortcuts`、`weather`） |

## 2. 判别标准：只守「会让用户误判设备状态」的文案

tooltip 三条规矩里，只有一条能被机械校验，而它真正怕的是**用户把配置意图
当成设备读数**。所以入守卫的判据是「这句话是否在断言一个设备/信号的
客观量」，而不是「这段字是不是 template」。

**入守卫（三类，命中任一）**

- **A 量纲/绝对数值**：片段或插值表达式里出现 `MS/s`、`Msps`、`MHz`、`kHz`、
  `Hz`、`ksps`、`dB`/`dBm`、`ppm`、`°`，或 `format_sample_rate` / `:.1f` 这类
  数值格式化插值。
- **B 来源与置信标注**：出现「未确认 / 未验证 / 未知 / 声明 / 回读 / 回执 /
  以设备为准 / 不是设备读数 / 配置意图 / 未施加 / 未下发 / 夹到 / 默认值」。
- **C 能力/状态的否定式断言**：出现「不支持 / 不可用 / 禁用 / 置灰 /
  未检测到 / 未连接」且指向某个控件或能力。

**不入守卫**

- 纯动作说明（「收起」「送进 AI 输入栏」「恢复为默认值」）——没有设备量；
- 纯透传变量（`setToolTip(msg)`）——静态无文本，改由产出方函数的运行时
  断言覆盖；
- 纯占位符（`%s`、`{}`）与纯表情（`⚠`、`·`）。

按这套判据，76 条里命中 A/B/C 的 **11 条**，另有 1 条
（`capability_drawer.py:177` 的 `%s`）是判据误报；其余 64 条不需要静态守卫。

## 3. 一旦扩守卫，会红多少、各是谁改的

### 3.1 若照搬现有「长度 ≥12」规则到片段

**32 条会红**（76 条里有常量片段的 38 条中，32 条含 <12 字符片段）。
红的全是 `⚠`、`收起`、`升起`、`·`、`补偿已关闭：` 这类碎片——证明
**长度规则不能套在片段上**，只能套在「渲染后的整句」上（运行时才拿得到）。

### 3.2 若只对片段跑诚实性规则（A 类量纲 + B 类来源标注）

实测**只红 3 条**：

| 位置 | 形态 | 最近改动 | 为什么红 / 是否需要改 |
| --- | --- | --- | --- |
| `desktop/capability_drawer.py:177` | `"…%s…" % name` | `c47cdce9` 2026-11-08 | **误报**：`%s` 被当成百分号。规则需先剥离 `%s/%d/{}` 占位符 |
| `desktop/new_spacetime_panel.py:955` | f-string | `a2be554a` 2026-11-11 | 过境表列释义，含 `最大仰角 °`、`最大多普勒 Hz`、`持续 分钟`，但没有来源限定。补一句「按 TLE 推算」即合规——这是**该红的** |
| `desktop/satellite_image_panel.py:194` | f-string | `15add04c` 2026-11-06 | `手动 <值> MS/s；录制文件带 SigMF meta 时优先用 meta 里的值`——已经有来源说明，只是限定词不在词表。扩词表即绿 |

另外 9 条 A/B 命中的（已合规，不会红），供登记表参考：

| 位置 | 最近改动 | 合规依据 |
| --- | --- | --- |
| `control_panel.py:468` 频率输入 | `4c591730` | 「实际范围以设备为准」 |
| `control_panel.py:780` 静噪门限 | `4c591730` | 「不是绝对音频电平」 |
| `control_panel.py:1044` 频率真值 | `e47e8e6b` | 「还没拿到频率真值（未连接…）」 |
| `control_panel.py:1548` 增益读数 | `c4b17d39` | 「未连接：上次配置…（未施加）」 |
| `device_panel.py:214` 采样率 | `42f446e3` / `d77d9b78` | 「配置意图，不是设备读数」「默认 · 未确认」 |
| `gain_panel.py:176` 增益下发失败 | `bd2f8878` | 「未下发」「滑杆仍是配置意图」 |
| `gain_panel.py:195` 增益夹取 | `bd2f8878` | 「设备夹到」「配置意图」 |
| `module_panel.py:587` 采样率来源 | `7bea149a` | 「以设备回执为准」 |
| `module_panel.py:624` 增益范围 | `b8ad9e86` | 「实际档位以设备为准」 |

## 4. 实现思路（建议，不是待办）

**第一步：登记表，不扩扫描器。** 新增 `docs/tooltip-dynamic-baseline.json`，
键 = `文件::行所在的调用表达式`（用 `ast.unparse` 归一，避免行号漂移），
值 = `{reason, producer, runtime_test}`。规则：

- 允许登记的两类：**纯透传**（38 条，必须写清产出函数与覆盖它的运行时用例）
  和**非诚实性动态文案**（76 条减去上面 11 条与 1 条误报，即 64 条）；
- 每登记一条要写 `reason`；`reason` 只能是枚举值（`passthrough` / `action-only` /
  `placeholder`），不写自由文本，避免变成背书清单；
- 登记表**只减不增**：新增动态 tooltip 一律要先进 A/B/C 判据，不许直接登记。

**第二步：扩扫描器，但只做两件事。** 在 `scan_tooltips()` 之外加
`scan_dynamic_tooltips()`：

1. 对 `JoinedStr` / `BinOp(+)` / `.format()` / `IfExp` 取常量片段与
   `ast.unparse` 后的插值表达式，**只跑 A/B/C 判据**（不跑长度、不跑
   same-as-name，这两条对片段无意义）；
2. 未在登记表且不在 A/B/C 白名单的新调用 → 红；已登记的调用若被发现
   已变成 A/B/C 形态 → 红（提示从登记表移除，与现有 baseline「只降不升」
   同一形态）。

**为什么这个顺序**：先扩会把 32 条历史文案一次打红，且红的都不是真问题，
下一个人只有两个选择——改 32 处文案或往白名单塞 32 条，两种都会把守卫
变噪声。先建登记表可把「历史」与「新增」切开，扩扫描器时只需处理 2~3 条
真问题（`new_spacetime_panel.py:955` 补来源、`satellite_image_panel.py:194`
扩词表、`capability_drawer.py:177` 先剥占位符）。

**运行时兜底**：`setToolTip` 的最终形态是字符串，纯透传的 38 条建议在
各自面板的既有测试里加一句「渲染后必须含单位或来源标注」的断言——那才是
真正防「configuration 冒充 device reading」的一层，静态守卫只防新增。

## 5. 不建议做的事

- 不要给片段加「长度 ≥12」：会红 32 条且全是碎片；
- 不要把 76 条一次性全登记成白名单：等于把盲区固化成制度；
- 不要用行号做登记键：`control_panel.py` 单文件 32 条，行号漂移是常态。
