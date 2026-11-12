# 交接单：tooltip 守卫的「动态文案」覆盖盲区（2026-11-11 实测）

> 证据状态：E4——本文件数字未逐条绑定原始件/复跑命令，引用前须复核（判据见 docs/evidence-standard.md）。
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

## 6. 落库结果（2026-11-11 23:07）

| 提交 | 内容 |
| --- | --- |
| `212d8b95` 23:07:10 | `desktop/new_spacetime_panel.py`：多普勒列补「（按 TLE 推算）」 |
| `1a8a34fb` 23:07:35 | `docs/tooltip-dynamic-baseline.json`（38 条）+ `tests/test_tooltip_guard.py`（动态扫描 4 用例）+ `tests/test_tooltip_dynamic_runtime.py`（9 用例） |

### 6.1 落地时与本单的差异

1. **`satellite_image_panel.py:194` 只扩词表，未改文案**。它的片段是
   「卫星下行信号的中心频率（MHz）；选好制式会自动填入…」与「录制文件带
   SigMF meta 时优先用 meta 里的值」——后者本身就是来源标注，所以把
   `SigMF|meta` 加入限定词表，而不是把文案改得更啰嗦。
2. **占位符先剥离再判**（`_strip_placeholders`），不是放宽词表。
   实测对照：同一套规则下，不剥离 → `capability_drawer.py:177`
   （`"把「%s」送进 AI 输入栏（不会自动执行）" % name`）被当成含百分号
   的数值文案误报；剥离后违规 0 条。词表未因此改动。
3. **运行时判据落成「非空 + 非裸数值 + 有量纲必带限定」，不是字面的
   「必含单位或来源标注」**。按字面执行会误伤 6 条合法文案：
   Offset 说明、GPS 分段说明、重启生效说明、云台无后端说明、轨道线
   「10 分钟」、显示跨度不改采样率——它们是动作/状态说明，不是设备量。
   这一层守的是诚实性而非文案字数，故不设长度断言（「卫星层」这类短语合法）。
4. **同一行循环复位多个控件时允许空串**。`set_sdr_connected` 里
   `w.setToolTip(base)` 一行会遍历 12 个控件，本来没有 tooltip 的控件
   渲染空串属合法清空。用例改为「该行至少一条真文案 + 非空文案全部
   自描述」，不因此豁免任何一条有文案的调用点。

### 6.2 词表新增及依据

| 词 | 依据 |
| --- | --- |
| `不是` | 频谱 `gate_tip`「不是 dBFS 绝对电平」：否定式本身即口径 |
| `典型` | PPM 说明「廉价棒典型偏差 20~50 ppm」：一般值，非设备读数 |
| `配置请求` | 采样率越界提示自称「配置请求 … 已按最近档 … 显示（配置文件未改）」 |
| `SigMF` / `meta` | 卫星面板注明频率来源为录制文件 meta |

### 6.3 反证实测

| 改回旧行为 | 结果 |
| --- | --- |
| 去掉多普勒「（按 TLE 推算）」 | `test_dynamic_tooltips_are_honest` 红（`new_spacetime_panel.py:955` unqualified_quantity） |
| 删登记表任一条 `runtime_test` 字段 | `test_dynamic_baseline_schema_is_complete` 红 |
| `_show_no_rtl_hint` 传「2.400」 | 运行时用例红：`main_window.py:6047 渲染成裸数值 '2.400'` |
| `_strip_placeholders` 改恒等 | 违规 0 → 1（`capability_drawer.py:177`） |

复跑：`python -m pytest tests/test_tooltip_dynamic_runtime.py tests/test_tooltip_guard.py -q`
→ **16 passed**。

## 7. 运行时覆盖率（2026-11-11 23:4x 实测）

口径：`desktop/*.py` 下所有 `setToolTip` 调用点（AST 计 227 个），逐个用
「拦截 `setToolTip` + 记调用栈行号」的 9 个运行时用例跑一遍，看是否真的渲染。

| 维度 | 覆盖 / 总数 | 比例 |
| --- | --- | --- |
| 全部调用点 | 168 / 227 | 74.0% |
| 动态调用点（f-string/变量/拼接等 76 条） | 52 / 76 | 68.4% |
| 纯透传登记项（38 条） | 38 / 38 | 100% |

未渲染 **59 条**，分两类：

- **35 条字面量**：静态守卫（长度 ≥12、不得与控件可见文字逐字相同）已覆盖；
  其中 16 条在 `main_window` 的菜单栏/对话框构造里，测试不建整窗故不触发。
- **24 条动态**：静态 `scan_dynamic_tooltips()` 的诚实性规则已覆盖，但**运行时
  兜底看不到**——这就是本次要量的盲区。

### 7.1 24 条动态未覆盖点（按触发条件）

| 触发条件 | 位置 |
| --- | --- |
| 设备连接 / 能力回读 | `control_panel` `set_gain_range`:1466,1470、`set_gain_applied`:1592,1595、`set_ppm_applied`:1626、`set_offset_tuning_applied`:1649、`set_vfo_bandwidth`:1858；`module_panel.set_gain_range`:624；`satellite_image_panel._on_sr_changed`:194 |
| 请求越界被钳位 | `control_panel.set_frequency_hz`:1504、`set_gain`:1561 |
| 有收藏项 | `control_panel._refresh_bookmarks`:1208 |
| 鼠标悬停 | `constellation_panel.mouseMoveEvent`:203 |
| 设备异步枚举 | `device_panel.refresh_devices_async`:316 |
| 过境预报出结果 | `new_spacetime_panel._populate_passes`:955 |
| 错误码出现 | `error_remedy.set_code`:213 |
| AI 面板刷新 | `capability_drawer.refresh`:177、`ai_panel._refresh_live_bar`:1160 |
| 增益分档完成/失败 | `gain_panel._mark_stage_failed`:176、`_mark_stage_applied`:195 |
| 主窗口状态同步 | `_refresh_tx_button_state`:5149、`_sync_fullscreen_btn`:10172、`_sync_tracking_ui`:12426、`_update_doppler_chip`:12723,12733 |
| 停靠折叠条 | `dock_layout_manager.build_collapse_strip`:132 |

按面板：`control_panel` 10、`main_window` 5、`gain_panel` 2、
`capability_drawer` / `constellation_panel` / `dock_layout_manager` /
`error_remedy` / `module_panel` / `new_spacetime_panel` /
`satellite_image_panel` 各 1。

### 7.2 9 个用例根本没构造的面板

`location_panel`(7 条)、`ntrip_panel`(3)、`time_machine`(2)、`ai_panel`(1)、
`capability_drawer`(1)、`error_remedy`(1)、`dock_layout_manager`(1)。
除 `location_panel` / `ntrip_panel` / `time_machine` 全是字面量外，其余 4 条
与 7.1 重合。

### 7.3 判断

- 纯透传（整段文案来自变量）这一层是满覆盖的，38/38；
- 诚实性运行时兜底的盲区是 **24 个动态点**，占动态点的 31.6%，**但它们不是
  无守卫**：静态扫描对 76 个动态点全覆盖，只是看不到「运行时才拼出来的整句」；
- 补齐顺序建议：先补 `control_panel` 的设备回读分支（`set_gain_applied` /
  `set_ppm_applied` / `set_offset_tuning_applied` / `set_vfo_bandwidth`，共 7 个点，
  都能用假能力值触发），再补 `main_window` 的 `_update_doppler_chip` 与
  `_sync_tracking_ui`（有真实信号流即可）。

## 8. 运行时豁免冻结（2026-11-11）

那 6 条「带量值/设备口径词、实为动作或状态说明」的文案，此前只是实现时
绕开，没有落纸。现冻结为 `docs/tooltip-runtime-exemptions.json`（字段：
`file` / `widget` / `match` / `why`），并由两条守卫看住：

1. **条数守卫**：`test_runtime_exemptions_are_frozen` 断言恰好 6 条，
   第 7 条出现即红，提示「新增豁免需评审」；
2. **数值守卫**：运行时渲染文本若含数字、既无量纲也无来源标注，且不在
   豁免表，直接红（快捷键名「已自定义：…」与文件路径按结构排除）。

| # | 控件 | 原文（节选） | 判为动作/状态说明的理由 |
| --- | --- | --- | --- |
| 1 | `control_panel` Offset 调谐复选框 | Offset Tuning：把本振偏移半个信道，避开零中频的 DC 尖峰… | 讲开关做什么，不含设备读数 |
| 2 | `constellation_panel` GNSS 状态行 | 可见 N 颗（GPS N）· 参与定位 N 颗 · 柱高=信噪比… | 星空状态与图例，颗数是本帧解算结果 |
| 3 | `settings_panel` 翻译器/日志级别 | 重启后生效：… | 生效条件说明，整句无量值 |
| 4 | `rf_sky_view` 云台跟随按钮 | 云台跟随：…（无可用云台后端：…） | 能力缺失说明 + 下一步，不含量值 |
| 5 | `main_window` 星象仪图层「轨道线」 | 轨道线：卫星未来 10 分钟轨迹 | 10 分钟是显示窗口参数，非设备读数 |
| 6 | `spectrum_widget` 显示跨度滑杆 | 显示跨度：右推收窄…左端回到全带宽。 | 交互说明，且已声明只改显示不改采样率 |

反证：把豁免表复制出第 7 条 → 条数守卫红；把
`_show_no_rtl_hint` 的文案改成「SDR 未连接，10 分钟后重试」→ 数值守卫红；
改回后均绿。复跑 **17 passed**。

## 9. 已知空洞 #1：频率

2026-11-11 由区块 12 发现，评估后决定不修（反证不通）。

**段原文**：`把频率改回设备标称范围：在「控制」面板的「频率」输入框直接输入，或点「-」「+」按钮按当前步进微调；书签里的超范围频率也要一并改掉。`
（`desktop/troubleshooting.py` 的 `NEXT_STEPS["frequency_out_of_range"]`）

- **为什么闸门抓不到**：控制面板的「频率」是 `QToolButton` 折叠组头，
  `_text_index` 收录其文字后命中**可操作分支**并短路；分句里的类型词
  `输入框` 只参与「非可操作候选」的双命中，对可操作候选不生效。因此这段话
  即使输入框被删，也会因为组头还在而被判通过。
- **要什么形态的破坏才露**：必须**组头文字还在、且它所在容器内所有
  「输入框」类控件（含 `QSpinBox` 等 `QAbstractSpinBox`）同时消失**才会变红
  ——例如把「频率」组整块内容删空、或把组头改名；只拆 `freq_input` 不会红。
- **现状事实**：文案本身是对的（该组里确有频率输入框），只是判据无法区分
  「组头」与「组内输入框」；已按裁定不登记进例外表（例外表保持 1 条）。
- **不要做的事**：不要为它加例外条目；也不要为此把「可操作分节头」一律降级为
  必须双命中。

修之前必须先知道的两个前提（实测）：

1. 该 `QToolButton` 的容器是 **`ControlPanel` 整个面板**，不是它折叠的那一小块
   内容，所以「组内同类型控件数」永远不会归零——拆掉 `freq_input` 后容器里
   仍有 2 个「输入框」类控件，双命中照样成立。
2. `QAbstractSpinBox` 被算进「输入框」类型词，`QSpinBox` / `QDoubleSpinBox`
   一律命中，导致「输入框」这个词在控制面板里几乎处处成立。

要修需先收窄容器到折叠块 + 把 `QAbstractSpinBox` 从「输入框」分离，属重新
设计判据；**不得**用「可操作分节头一律双命中」的粗暴做法（「控制」那 34 处
会大范围误红）。
