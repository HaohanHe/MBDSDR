# R2 深度审查：desktop/control_panel.py（372 行）

审查人：UI 子 agent
审查对象：`desktop/control_panel.py`（83–372 行 `ControlPanel` 类）
联动审查：`desktop/main_window.py`、`desktop/mcp_worker.py`、`desktop/themes.py`
结论速览：**外壳可用，但 SDR 类控件（航空/业余/气象/ISM/短波预设）全部是死路；静噪、LNA/VGA 增益在 UI 层完全缺失；AM 预设走 FM 通道是真 bug；模式切换槽在 main_window 是空 pass。**

---

## 一、UI 控件 → 后端接线状态对照表

| 控件 | 位置 | 信号 | 槽 | main_window 接线 | worker 工具 | 实际生效？ |
|---|---|---|---|---|---|---|
| 频率大字体显示 `freq_display` | :116 | — | 只读 QLabel | — | — | ✅ 仅显示 |
| 模式下拉 `mode_combo`（仅 FM/AM） | :125–128 | `currentTextChanged` | `_on_mode_changed` :262 | → `_on_mode_changed` :550 | 无 | ⚠️ **半残**（见 B2） |
| 频率输入框 `freq_input` | :131–133 | `returnPressed` | `_on_freq_input` :269 | → `_on_tune_fm`/`_on_tune_am` | `tune_fm`/`tune_am` ✅ | ✅ FM/AM 输入生效 |
| "调谐"按钮 `tune_button` | :136–138 | `clicked` | `_on_freq_input` :269 | 同上 | 同上 | ✅ |
| 步进按钮 −1.0/−0.1/+0.1/+1.0 | :145–149 | `clicked` | `_step_freq` :287 | → tune_fm/am | tune_fm/am | ⚠️ FM OK，AM 步长语义错（见 B6） |
| 频段分类下拉 `band_combo` | :160–163 | `currentIndexChanged` | `_on_band_changed` :243 | 仅刷新 preset_combo | — | ✅ 仅 UI 内 |
| 预设下拉 `preset_combo` | :168–171 | `currentIndexChanged` | `_on_preset_selected` :300 | → `_on_tune_fm` + `_on_tune_sdr` | tune_fm ✅ / **tune_sdr ❌** | ❌ **SDR 频段全死**（见 B1） |
| 音量滑块 `volume_slider` (0–63) | :186–190 | `valueChanged` | `_on_volume_changed` :328 | → `_on_volume_changed` :529 | `set_volume` ✅ | ✅（注意：这是音频音量，**不是 RF 增益**） |
| 录音按钮 `record_button` | :199–203 | `toggled` | `_on_record_toggled` :334 | → `_on_record_toggled` :534 | `start_record`/`stop_record` ✅ | ✅ |
| 录音状态 `record_status` | :206 | — | `update_record_time` :363 | main_window 定时器驱动 | — | ✅ |

### 完全缺失的控件（对照审查重点）

| 审查项 | 现状 |
|---|---|
| **静噪滑块/门控** | ❌ **整个文件没有任何 squelch 控件**（grep 全 desktop/ 目录无 `squelch` 命中）。连空壳都没有。 |
| **LNA / VGA / 基带分段增益** | ❌ 无。唯一"滑块"是 `volume_slider`（0–63 音频增益，:187）。无 `lna_gain`/`vga_gain`/`baseband_gain`/`attenuation` 任何控件。 |
| **VFO A/B 切换** | ❌ 无。只有 `_current_freq_fm` / `_current_freq_am` 两个标量，无 VFO 概念。 |
| **AM/SSB/CW/Digital 模式下拉** | ❌ `mode_combo` 只有 `["FM","AM"]`（:126）。预设表里出现的 USB/LSB/CW/AFSK/FSK/PSK/QAM/ASK/WFM 等模式（:42–80）**在模式下拉里选不到**。 |
| **带宽 / 滤波器选择** | ❌ 无。 |
| **AGC 开关** | ❌ 无。 |

---

## 二、发现清单

### B1 [真bug] SDR 预设（航空/业余/气象/ISM/短波）调谐请求打到不存在的 worker 工具

- `control_panel.py:314` `self.tune_sdr_requested.emit(freq_mhz * 1e6, mode)`
- `main_window.py:298` 连接到 `_on_tune_sdr`
- `main_window.py:514–518` `self._worker.call_tool("tune_sdr", {...})`
- `mcp_worker.py:256–265` `method_map` 里**没有 `tune_sdr`**；`_sim_tool` (:272–323) 也没有。
- 结果：真实模式下返回 `{"error": "未知工具: tune_sdr"}`（:270），模拟模式返回 `{"error": "未知工具: tune_sdr"}`（:323）。**航空 118 MHz、业余 144/430 MHz、NOAA 137 MHz、ISM 433/868/915 MHz、短波 3.5–28 MHz 共 28 个预设全部调谐失败**。
- 附带：`main_window.py:748` 天空图点击卫星也调 `sdr_set_frequency`，该工具同样不在 `method_map`，同样静默失败（外层 try/except 吞掉）。

### B2 [真bug] 模式切换后预设列表被清空

- `control_panel.py:128` `mode_combo.currentTextChanged.connect(self._on_mode_changed)`
- `control_panel.py:262–266`：
  ```python
  def _on_mode_changed(self, mode: str):   # mode = "FM" 或 "AM"
      self._current_mode = mode
      self.mode_changed.emit(mode)
      self._populate_presets(mode)         # ← 传 "FM"/"AM"
      self._update_freq_display()
  ```
- 但 `_populate_presets` (:219–239) 判定分支是 `if band == "FM 广播"` / `elif band == "AM 广播"` / else 按 SDR 关键词过滤。传 `"FM"` 不等于 `"FM 广播"`，落到 else 分支，`band_keywords.get("FM", []) = []`，于是 `preset_combo.clear()` 后**一项都不添加**。
- 现象：用户在模式下拉切 FM↔AM，预设框瞬间变空，必须再去频段下拉手动选一次才恢复。

### B3 [真bug] AM 广播预设被错误路由到 tune_fm 通道

- `control_panel.py:222–224`：
  ```python
  for freq, name in PRESET_AM_STATIONS:        # freq 单位 kHz，如 980
      self.preset_combo.addItem(f"{freq} kHz - {name}", (float(freq) / 1000.0, "AM"))
  ```
  即 data = `(0.98, "AM")`。
- `control_panel.py:307–314` 新 tuple 分支**无视 mode 字段**，统一执行：
  ```python
  freq_mhz, mode = data
  self._current_freq_fm = freq_mhz          # 0.98
  self.tune_fm_requested.emit(freq_mhz)     # ← 错！应该 tune_am_requested(980)
  self.tune_sdr_requested.emit(freq_mhz*1e6, mode)
  ```
- 结果：点"980 kHz 新闻"预设，实际发出 `tune_fm(0.98)`（SI4732 FM 段 64–108 MHz 之外，无效）+ `tune_sdr(980000, "AM")`（工具不存在，见 B1）。**AM 广播预设 8 个全部失效**。旧格式分支（:316–324）本来是对的，被新 tuple 分支抢了路。

### B4 [真bug] 航空/SDR 预设同时发了 tune_fm，硬件会被错误调到 FM 段外

- `control_panel.py:309–311`：选"航空塔台 118.000 MHz"时，无差别 `self.tune_fm_requested.emit(118.0)`。
- `main_window.py:503–506` `_on_tune_fm` 直接 `worker.call_tool("tune_fm", {"freq_mhz": 118.0})`，无范围钳制。
- SI4732 FM 接收段是 64–108 MHz；118 MHz 调过去要么固件报错要么钳回 108，频谱图 `set_center_freq(118)` 却显示到 118。**UI 显示与硬件实际频率不一致**。

### B5 [空壳] main_window 侧 `_on_mode_changed` 是空函数

- `main_window.py:549–551`：
  ```python
  @Slot(str)
  def _on_mode_changed(self, mode: str):
      pass  # 模式切换在控制面板内部处理
  ```
- `control_panel.mode_changed` 信号（:92）在这里被 connect 但完全不做事。切换 FM/AM 不会通知 worker 切换解调链、不会切换频谱量程、不会切 RDS/AM 包络检测。属于"接了线但线另一头是空插座"。

### B6 [建议/UX] AM 模式下步进按钮标签与实际步长不符

- `control_panel.py:145` 按钮标签固定 `−1.0 / −0.1 / +0.1 / +1.0`（按 MHz 设计）。
- `control_panel.py:293`：`step_khz = int(step*100) if abs(step)>=1 else int(step*10)`。
  - AM 下 `+1.0` → 100 kHz；`+0.1` → 1 kHz。
  - 标签写 `+0.1`，实际走 1 kHz，对中波用户而言步长语义混乱（中波步进通常是 9/10 kHz）。建议 AM 模式下动态改标签或换步进组。

### B7 [占位] 频率输入框无 validator，非法输入静默吞掉

- `control_panel.py:12` 导入了 `QIntValidator, QDoubleValidator`，但**全文从未实例化**（死 import）。
- `control_panel.py:131` `freq_input` 是裸 QLineEdit。
- `control_panel.py:283–284` `except ValueError: pass` —— 用户输入 `"abc"` 或越界频率（如 FM 填 200）时无任何提示，仅静默弹回旧值。
- 越界时（如 FM 200），内层 `if 64<=freq<=108` 不成立，但 `_update_freq_display()` 仍执行，把输入框刷回旧值 —— 用户得不到"为什么没动"的反馈。

### B8 [真bug] main_window 引用了 ControlPanel 不存在的属性 `freq_spin`

- `main_window.py:755`：`self.control_panel.freq_spin.setValue(freq_mhz)`
- ControlPanel 里只有 `freq_input`（QLineEdit，:131），**没有 `freq_spin`**。
- 外层包了 `try/except: pass`（:756–757），所以天空图点卫星后控制面板频率显示**不会更新**，用户看到频谱动了但控制面板大数字不动。

### B9 [建议] `_on_preset_selected` 未根据 mode 反切 `mode_combo`

- 选"短波 14.000 MHz USB"预设后，`mode_combo` 仍显示 "FM"（因为 :126 只有 FM/AM 两项，根本没法显示 USB）。
- `_current_mode` 仍为 "FM"，用户再点 `+0.1` 步进会走 FM 分支（:288），把 14.0 MHz 加 0.1 MHz 后钳到 108 MHz 上限 —— 逻辑混乱。
- 根因：模式下拉只有两档，但预设表有 10+ 种模式，二者从未对齐。

### B10 [建议] 无递归风险（确认项）

- `set_freq_fm` (:349–354) 调 `mode_combo.setCurrentText("FM")` → 若当前已是 FM 则不发信号；若从 AM 切到 FM 会触发 `_on_mode_changed`，但后者不再回调 `set_freq_fm`，**无 valueChanged→setValue 循环**。
- `set_volume` (:370–372) 调 `volume_slider.setValue` → 触发 `_on_volume_changed` → 发 `volume_changed` → main_window 调 worker，worker 不回灌 `set_volume`（除 AI 工具回环，见 `main_window.py:578–579`，但那条路只由 AI 触发一次，不构成循环）。
- `preset_combo` 在 `_populate_presets` 里 `blockSignals(True/False)`（:217/:240），填项时不会误触发 `_on_preset_selected`。✅ 这点做对了。

### B11 [建议/主题] control_panel 本身无硬编码颜色

- 全文 grep 无 `setStyleSheet`、无 `#RRGGBB`、无 `QFont(...)` 实际调用（:12 导入 `QFont` 但未用）。
- 样式全靠 `objectName`：`freqDisplay` (:117)、`statusValue` (:182)、`recordButton` (:200)，分别在 `themes.py:119/114/149` 有 QSS 规则。✅ 主题隔离干净。
- 唯一硬编码样式在 `main_window.py:161,209,394,422,450,464`（conn_label 颜色 `#C4845C` 等），不在本文件范围内，由 main_window 审查项记录。

---

## 三、与 main_window 的集成关系

- 集成位置：`main_window.py:292–299`，作为右侧 TabWidget 的第一个 tab（"控制"）。
- 已连接信号：`tune_fm_requested` / `tune_am_requested` / `volume_changed` / `record_toggled` / `mode_changed` / `tune_sdr_requested` 全部 connect（:293–298）。
- 反向调用：
  - `main_window._on_spectrum_freq_changed` → `control_panel.set_freq_fm`（:583）✅
  - `main_window._on_ai_tool_call` → `control_panel.set_freq_fm` / `set_volume`（:577/:579）✅
  - `main_window._update_record_time` → `control_panel.update_record_time`（:593）✅
  - `main_window._toggle_record` → 反向 `control_panel.record_button.setChecked`（:600）✅（注释里明确避免了递归 :596）
- **未集成/错集成**：
  - `tune_sdr_requested` 连了但 worker 无此工具（B1）。
  - `mode_changed` 连了但 main_window 槽是 `pass`（B5）。
  - `main_window._on_sky_object_clicked` 引用不存在的 `control_panel.freq_spin`（B8）。
  - 控制面板没有暴露任何 set_squelch / set_gain / set_mode_sdr 接口，外部（AI、天空图）无法反向设置这些参数 —— 因为这些控件根本不存在。

---

## 四、总结

| 类别 | 数量 | 关键项 |
|---|---|---|
| 真 bug | 5 | B1 tune_sdr 工具缺失；B2 模式切换清空预设；B3 AM 预设走错通道；B4 航空预设误调 tune_fm；B8 freq_spin 不存在 |
| 空壳 | 1 | B5 mode_changed 在 main_window 是 pass |
| 缺失功能（非空壳，是根本没做） | 4 项 | 静噪门控、LNA/VGA/基带增益分段、VFO A/B、SSB/CW/Digital 模式下拉、带宽/AGC |
| 占位/建议 | 4 | B6 AM 步进标签；B7 validator 缺失；B9 模式下拉与预设表不对齐；B11 主题隔离良好（正面） |
| 递归风险 | 0 | 确认无 valueChanged↔setValue 循环 |

**优先级建议**：
1. P0：B1 —— 在 `mcp_worker._real_tool/_sim_tool` 补 `tune_sdr` 路由（或从 UI 层移除 SDR 预设）。
2. P0：B3 —— `_on_preset_selected` 按 data 里的 `mode` 字段分流到 tune_am/tune_fm/tune_sdr，不要无脑走 tune_fm。
3. P1：B2 —— `_on_mode_changed` 应把 `mode` 映射回 `"FM 广播"`/`"AM 广播"` 再调 `_populate_presets`，或直接根据 mode 切回对应 band_combo 索引。
4. P1：B5/B9 —— 要么把 mode_combo 扩成全模式（USB/LSB/CW/AM/FM/WFM/AFSK…），要么在 main_window `_on_mode_changed` 里真正通知 worker 切换解调链。
5. P2：静噪与 RF 增益 UI 完全缺失，若第二轮范围要求"可调"，需新增 QGroupBox（squelch 0–10 dB + LNA/VGA/BB 三滑块），并在 worker 侧补 `set_squelch`/`set_gain` 工具。
