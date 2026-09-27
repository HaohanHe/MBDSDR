# MBDSDR 模块化现状报告

> 生成时间：2026-09-27
> 范围：解调模式注册表（mode_registry）迁移 + 四张既有注册表（decoder / tool / skill / panel）开放度审计
> 红线：不改解调数学、不碰触屏（touch_manager / touch_helpers）、不动 gated_recorder / squelch / iq_frontend

---

## 1. 四张注册表审计结论

| 注册表 | 状态 | 新增一个 X 的步骤 | 残留硬接线 |
|---|---|---|---|
| **tool_registry.py**（`mbdsdr_ai/`） | ✅ 真插件化 | 1) 在 `sdr_tools.py`（或独立 `register_*_tools`）调一次 `tool_registry.register(name, desc, params, handler, category)`；2) 无需改 agent / orchestrator 的调度。 | 无。调度是 `tool_registry.call(name, args)` 的 dict 查表（tool_registry.py:234），agent.py:1842 / orchestrator.py:334 / subagents.py:322 全部走 `.call()`。`register_builtin_tools` 下挂的各 `register_*_tools` 只是注册分组，不是调度 switch。 |
| **skill_registry.py**（`mbdsdr_ai/`） | ✅ 真插件化 | 1) 在 `skills/` 下新建 `<name>/SKILL.md`（带 `name`/`description` frontmatter）。下次启动自动被 `_scan()` 发现，无需改任何 .py。 | 无。目录扫描 + 懒加载正文；当前已发现 `find-interference` / `sat-track` / `sstv-decode` 三个技能。 |
| **decoder_registry.py**（`mbdsdr_ai/`） | ✅ 接口开放（但当前未接主调度） | 1) 写一个 `DecoderDescriptor(mode=..., module=..., decode_func=..., freq_bands_mhz=...)` 调 `register()`。注册表本身支持运行时追加。 | **无绕过 import**：全仓 grep 不到任何 `from pocsag_decoder/acars_protocol/vor_decoder import`（注册表之外）。**但**：`decoder_registry` 当前没有被任何运行时代码 import（孤儿模块）——真实解码走的是 tool_registry（sdr_tools 把解码能力注册成工具）+ background_decoder 自己的线程注册表。即：注册 API 干净，但还不是“唯一解码调度入口”。 |
| **panel_registry.py**（`desktop/`） | ⚠️ 部分开放 | 1) 在 `_register_defaults()` 加一条 `PanelSpec(id, name, icon, area, ...)`；2) **仍需**在 MainWindow 构造里手写 `self.x = XPanel()` + `addTab` + `attach_widget(id, self.x)`。 | (a) `scanner_panel`（扫频）与 `bookmark_panel`（书签）两个 dock 在 MainWindow 里懒创建、直接 `_add_right_tab(...)`，**没有 PanelSpec、不在面板菜单里**，绕过注册表；(b) 11 个内置面板的控件构造仍是 MainWindow 里逐行硬编码序列（注册表只存元数据/菜单/布局，不实例化 Qt——这是 docstring 明确的分工，故不算 bug，但“加面板”不是改一处）。 |

### 关键 grep 证据
- 解码绕过：`grep "import.*pocsag_decoder|acars_protocol|vor_decoder"` 在 decoder_registry.py 之外 = 0 命中。
- 工具调度：`agent.py:1842`、`orchestrator.py:334`、`subagents.py:322` 全部 `self.tool_registry.call(...)`。
- 面板菜单：`main_window.py:1593 for spec in registry().list()` —— 菜单由注册表驱动。
- 面板孤儿：`main_window.py:3947 / 3971` 的 scanner/bookmark dock 不在 `registry().ids()` 内。

---

## 2. 本轮拆除的硬接线清单

| 文件 | 原行号 | 原内容摘要 | 改造方式 |
|---|---|---|---|
| `desktop/control_panel.py` | 76–84 | 字面量 `MODE_VFO_BANDWIDTH = {"WFM":180000,"NFM":12500,...}` dict | 删 dict，改 `from mode_registry import ...`；保留 `MODE_VFO_BANDWIDTH = to_bandwidth_dict()` 向后兼容别名 |
| `desktop/control_panel.py` | 224 | `mode_combo.addItems(["FM","WFM","NFM","AM","USB","LSB","CW"])` | `[m for m in _PANEL_MODE_ORDER if get_mode(m)]`（顺序保持 UI 原序，注册表过滤存在性） |
| `desktop/control_panel.py` | 398 | `MODE_VFO_BANDWIDTH["FM"]` | `default_bandwidth("FM")` |
| `desktop/control_panel.py` | 630, 914 | `MODE_VFO_BANDWIDTH.get(mode, MODE_VFO_BANDWIDTH["FM"])` | `default_bandwidth(mode)` |
| `desktop/main_window.py` | 40 | `from control_panel import ControlPanel, MODE_VFO_BANDWIDTH` | 拆成 `from control_panel import ControlPanel` + `from mode_registry import default_bandwidth, audio_cutoff, demodulate, get as get_mode` |
| `desktop/main_window.py` | 577 | `toolbar_mode_combo.addItems([...7 个模式字面量])` | `[m for m in ["FM","WFM","NFM","AM","USB","LSB","CW"] if get_mode(m)]` |
| `desktop/main_window.py` | 2517 | `float(MODE_VFO_BANDWIDTH.get(m, MODE_VFO_BANDWIDTH["FM"]))` | `float(default_bandwidth(m))` |
| `desktop/main_window.py` | 3600–3625 `_demod_at_48k` | 6 分支 if-elif 链逐模式调 `dsp.fm_demod/am_demod/ssb_demod/cw_demod` | 注册表分发：`desc=get_mode(mode); if desc.outputs_audio: demodulate(mode, vfo, sample_rate=48000)`，兜底 FM 75k 不变 |
| `desktop/main_window.py` | 3627–3657 `_demod_at_native_sr` | cutoff 字面量 dict + 6 分支 if-elif 链 | `cutoff=audio_cutoff(mode)` + `demodulate(mode, iq, sample_rate=sr)`，None 兜底 FM 75k，`audio_to_playback` 不变 |
| `desktop/receive_pipeline.py` | 231–245 `_demod_48k` | 6 分支 if-elif 链 | 同 main_window：`get_mode` + `demodulate` 注册表分发，WFM 专用路径（274 行起）保持不动 |
| `desktop/module_panel.py` | 229 | `addItems(["WFM 广播","NFM 窄带","AM","USB","LSB"])` | `[get_mode(m).display_name for m in ["WFM","NFM","AM","USB","LSB"] if get_mode(m)]`（显示名取自注册表） |
| `desktop/bookmark_panel.py` | 235 | `["NFM","WFM","AM","USB","LSB","CW","DIG"]` 字面量列表 | `[m for m in [...] if get_mode(m)]`（保持 UX 顺序，注册表过滤） |
| `desktop/scanner_panel.py` | 47–54 | `_KIND_TO_MODE` / `_KIND_ICON` | **未改**——这是“扫描估计信号类型 → 建议模式”的业务分类映射（不是可用模式列表），模式名已与注册表一致，按任务要求保留业务逻辑 |

> 数学正确性：对随机 IQ 逐模式对比旧 if-else 与注册表分发的输出，48k 路径与 native-sr 路径全部 `np.allclose=True`（NFM/FM/AM/USB/LSB/CW，6/6）。`am_demod` 不接受 `sample_rate`，由 `mode_registry.demodulate()` 的 TypeError 回退自动剥参。

---

## 3. 新增解码器接入步骤

**走 tool_registry（当前真实生效路径）：**
1. 写解码器模块（如 `mbdsdr_ai/my_decoder.py`，导出 `decode(...)`）。
2. 在 `sdr_tools.py`（或新 `register_my_tools.py`）里调一次
   `agent.tool_registry.register(name="my_decode", description=..., parameters={...}, handler=lambda args: ...)`。
3. 完成。agent / orchestrator 经 `tool_registry.call("my_decode", args)` 自动可发现、可调用，无需改调度。

**（可选）同步登记 decoder_registry：**
1. 构造 `DecoderDescriptor(mode="MY", module="mbdsdr_ai.my_decoder", decode_func="decode", freq_bands_mhz=[...])` 调 `decoder_registry.register(...)`。
2. 注：当前 decoder_registry 尚未被主调度 import，登记后若希望按频率自动选台，需在调度侧接 `select_by_frequency()`（见待办）。

---

## 4. 新增工具接入步骤

1. 在某个 `register_*_tools(agent)` 函数里调 `agent.tool_registry.register(name, description, parameters, handler, category="...", available=True)`。
2. 若工具依赖外部命令/硬件，把 `available` 设为可动态切换（`set_available`），未连接时灰掉。
3. 完成。`list_tools()` 自动列出，`.call(name, args)` 自动分发。

---

## 5. 新增面板接入步骤

1. 在 `panel_registry.py::_register_defaults()` 加一条
   `PanelSpec("my_panel", "我的面板", "图标", AREA_RIGHT, collapsible=True, min_w=...)`。
2. 在 `desktop/` 写 `my_panel.py`（QWidget 子类）。
3. 在 `main_window.py` 构造区手写：`self.my_panel = MyPanel()` + 挂到对应 tab/dock + `reg.attach_widget("my_panel", self.my_panel)`。
4. 面板菜单自动出现第 1 步的名字（菜单由 `registry().list()` 驱动）。

> 说明：第 3 步的 Qt 控件构造无法纯数据化（注册表 docstring 明确不实例化 Qt），所以是“注册表声明元数据 + MainWindow 构造控件”两段式。

---

## 6. 新增解调模式接入步骤

1. 在 `desktop/mode_registry.py::_BUILTIN_MODES` 加一条 `DemodMode(mode="DAB", display_name="DAB", default_bandwidth_hz=..., audio_cutoff_hz=..., demod_func="mbdsdr_ai.dsp:dab_demod", demod_kwargs={...}, outputs_audio=True)`。
2. 完成。以下全部自动出现，**不改任何 if-else**：
   - 控制面板 / 工具栏模式下拉（经 `get_mode` 过滤的 UX 顺序列表）；
   - 切模式时的默认 VFO 带宽（`default_bandwidth()`）；
   - 48k 与 native-sr 两条解调路径的分发（`demodulate()`）；
   - 回退路径音频低通截止（`audio_cutoff()`）；
   - 旧 `MODE_VFO_BANDWIDTH` 字典经 `to_bandwidth_dict()` 自动包含新模式。
3. 若新模式**不输出音频**（如新增数字直通类），把 `outputs_audio=False`，48k 分发会自动落到兜底/跳过。

---

## 7. 待办：尚未完全插件化的部分

1. **panel_registry 残留两个孤儿 dock**：`scanner_panel`（扫频）、`bookmark_panel`（书签）在 MainWindow 懒创建、未注册 PanelSpec，因此不出现在“面板”菜单里。应补两条 `PanelSpec` 并改走统一挂载。
2. **decoder_registry 未接主调度**：注册表干净但孤儿，真实解码走 tool_registry。若希望“按频率自动选解码器”，需在调度侧接 `select_by_frequency()`，并让 background_decoder 的线程注册与 decoder_registry 对齐。
3. **background_decoder 自带第二套注册表**：`BackgroundDecoder`（线程级 decode_fn 注册）与 `decoder_registry`（解码器描述符）是两套平行机制，长期可合并为“decoder_registry 描述符 + background_decoder 线程包装”。
4. **scanner_panel `_KIND_TO_MODE` 是业务分类映射**：保留，但未来若模式激增可考虑把 kind→mode 映射也声明化（当前仅 6 项，不急）。
5. **module_panel 显示名 → 模式名**：该 combo 当前发射显示文本，下游 `_on_mode_changed` 按模式名查表（WFM/NFM 显示名历史上回退到 FM 带宽是既有行为）。后续可改用 `itemData` 存模式名、发射真模式名，彻底消除显示名/模式名不一致。
