# R2 深度代码审查：desktop/main_window.py（831行）

**审查人**：UI 主窗口子 agent
**审查日期**：2026-09-24
**文件**：`desktop/main_window.py`

---

## 一、UI 控件 → 后端接线状态对照表

| UI 控件 | 位置 | 信号/槽连接 | 后端调用 | 状态 |
|---|---|---|---|---|
| 文件→连接设备... (Ctrl+C) | L86-89 | `triggered → _connect_dialog` | → `_connect_real` → `worker.start(host,port)` | ✅ 已接线 |
| 文件→断开连接 (Ctrl+D) | L91-94 | `triggered → _disconnect` | → `worker_manager.stop()` | ✅ 已接线 |
| 文件→模拟模式 (Ctrl+M) | L98-101 | `triggered → lambda: _connect_simulation()` | → `worker.start(use_simulation=True)` | ✅ 已接线 |
| 文件→退出 (Ctrl+Q) | L105-108 | `triggered → self.close` | → closeEvent → _save_gui_config + _disconnect | ✅ 已接线 |
| 视图→主题子菜单 | L113-120 | `triggered → _apply_theme(name)` | → theme.apply() + spectrum.set_theme_colors() | ✅ 已接线 |
| 视图→显示瀑布图 | L124-128 | `triggered → _toggle_waterfall` | → spectrum.toggle_waterfall() | ⚠️ 按钮不同步 |
| 视图→全屏 (F11) | L130-133 | `triggered → _toggle_fullscreen` | → showNormal/showFullScreen | ✅ 已接线 |
| 工具→FM 扫频找台 | L138-140 | `triggered → _start_sweep` | → 仅填入 AI 输入框文本，不提交 | ⚠️ 半成品 |
| 工具→开始/停止录音 (Ctrl+R) | L142-145 | `triggered → _toggle_record` | → 读取 record_btn 状态（未toggle），实际无效 | ❌ 死菜单 |
| 工具→关于 MBDSDR | L149-151 | `triggered → _show_about` | → QMessageBox.about | ✅ 已接线 |
| 工具栏-连接按钮 | L167-170 | `clicked → _connect_dialog` | 同菜单 | ✅ 已接线 |
| 工具栏-模拟模式按钮 | L172-175 | `clicked → lambda: _connect_simulation()` | 同菜单 | ✅ 已接线 |
| 工具栏-断开按钮 | L177-181 | `clicked → _disconnect` | 同菜单 | ✅ 已接线 |
| 工具栏-主题下拉框 | L187-192 | `currentIndexChanged → _on_theme_combo_changed` | → _apply_theme | ✅ 已接线 |
| 工具栏-瀑布图按钮 | L197-202 | `clicked → _toggle_waterfall` | → spectrum.toggle_waterfall() | ⚠️ 菜单不同步 |
| 工具栏-录音按钮 | L218-223 | `toggled → _toggle_record` | → 同步 control_panel.record_button | ✅ 已接线（唯一生效路径） |
| 频谱→freq_changed | L272 | `spectrum.freq_changed → _on_spectrum_freq_changed` | → worker.call_tool("tune_fm") | ✅ 已接线 |
| 控制→tune_fm_requested | L293 | → `_on_tune_fm` | → worker.call_tool("tune_fm") | ✅ 已接线 |
| 控制→tune_am_requested | L294 | → `_on_tune_am` | → worker.call_tool("tune_am") | ✅ 已接线 |
| 控制→volume_changed | L295 | → `_on_volume_changed` | → worker.call_tool("set_volume") | ✅ 已接线 |
| 控制→record_toggled | L296 | → `_on_record_toggled` | → worker.call_tool("start/stop_record") | ✅ 已接线 |
| 控制→mode_changed | L297 | → `_on_mode_changed` | → **pass（空槽）** | ❌ 空壳 |
| 控制→tune_sdr_requested | L298 | → `_on_tune_sdr` | → worker.call_tool("tune_sdr") | ❌ 工具不存在 |
| AI→tool_call_requested | L307 | → `_on_ai_tool_call` | → worker.call_tool + 部分UI同步 | ⚠️ 仅处理2种工具 |
| AI→command_submitted | L308 | → `_on_ai_command` | → ai_panel._call_ai()（后台线程） | ✅ 已接线 |
| 天空图→object_clicked | L279 | → `_on_sky_object_clicked` | → worker.call_tool("sdr_set_frequency") | ❌ 工具不存在 |

---

## 二、确认的已知问题

### [真bug] 问题1：双定时器冲突——decoders.py (5s) 与 orbit.py (10s) 互相覆盖

**位置**：`main_window.py:68-71` + `rf_sky_view.py:1112-1121`

**确认属实**。存在两条独立的卫星位置更新路径，互相覆盖：

| 定时器 | 间隔 | 数据源 | 调用方法 | 最终动作 |
|---|---|---|---|---|
| MainWindow._sky_update_timer | **5s** | `mbdsdr_ai.decoders` | `compute_satellite_position()` | `sky_view.set_objects()` |
| SatelliteTracker._timer | **10s** | `mbdsdr_ai.orbit` | `orbit.compute_satellite_state()` | `sky_view.set_objects()` + `set_trajectory()` |

**冲突表现**：
- t=0s: SatelliteTracker.refresh() 用 orbit 数据画卫星+轨迹
- t=5s: MainWindow._update_sky_satellites() 用 decoders 数据覆盖 set_objects()，**轨迹被丢弃**（decoders 路径不调用 set_trajectory）
- t=10s: SatelliteTracker.refresh() 重新画卫星+轨迹
- 循环往复，轨迹每 5 秒被擦除一次

**数据质量差异**：
- `decoders.py:152-153` 注释明确写道："简化：将 ECI 转换为观测点的仰角/方位角（完整实现需要考虑地球自转，这里用简化的球面几何）"——**不考虑地球自转，坐标变换是错的**
- `decoders.py:199` `radial_velocity = 0`——多普勒频移恒为 0
- `orbit.py` 使用 NORAD catalog number 做正规 sgp4 传播，是正确路径

**结论**：5s 定时器（错误路径）每 5 秒覆盖一次 10s 定时器（正确路径）的数据，导致：
1. 卫星位置显示的是 decoders.py 的简化错误计算结果
2. 卫星轨迹线每 5 秒消失一次
3. 两套 TLE 数据源（BUILTIN_TLE vs BUILTIN_SATS）卫星列表可能不一致

**建议**：删除 `main_window.py:67-73` 的 5s 定时器和 `_update_sky_satellites` 方法，完全依赖 SatelliteTracker。`_update_time_info()` 应拆为独立定时器。

---

### [真bug] 问题2：热力图仅 90 个 sin/cos 假点

**位置**：`main_window.py:657-663`

**确认属实**。计数验证：
- `range(0, 360, 20)` = 18 个方位角 (0,20,40,...,340)
- `[15, 30, 45, 60, 75]` = 5 个仰角
- 总计 = 18 × 5 = **90 个 HeatmapCell**

信号值完全由数学函数生成：
```python
signal = -70 + 20 * math.sin(math.radians(az * 2)) + 10 * math.cos(math.radians(el * 3))
```

无任何真实 SDR 扫频数据输入。热力图是纯装饰性占位，不反映任何真实信号环境。

---

### [真bug] 问题3：new_spacetime 路径——授时调用本身合理，但被错误地绑定在卫星更新定时器上

**位置**：`main_window.py:702-723`

**部分确认**。`_update_time_info()` 调用 `mbdsdr_ai.new_spacetime.get_time_info()` 本身是正确的——这个函数确实做 NTP 授时和 GPS 时间计算。

**但问题在于调用时机**：`_update_time_info()` 被 `_update_sky_satellites()`（5s 定时器）调用。这意味着：
1. 授时逻辑与错误的卫星路径（decoders.py）耦合在一起
2. 当 `do_ntp=True` 时（每 30 次调用 ≈ 150s），`get_time_info()` 会发起 **NTP 网络请求**，阻塞 UI 线程等待 NTP 响应
3. NTP 请求超时时间未设置（`new_spacetime.py` 中 `get_ntp_time` 可能默认阻塞较长时间）

**真正的"错误路径"问题**：授时应该有自己独立的定时器（如每 60s 或 300s），而不是寄生在卫星更新定时器上。一旦卫星更新定时器被删除（见问题1建议），授时也会跟着消失。

---

## 三、新发现的问题

### [真bug] 问题4：菜单项"开始/停止录音"(Ctrl+R) 是死的

**位置**：`main_window.py:142-145` + `main_window.py:595-600`

**分析**：
- 菜单项 `record_action.triggered` → `_toggle_record()`
- `_toggle_record()` 读取 `self.record_btn.isChecked()` 的当前状态
- 但菜单项触发时 **record_btn 状态并未改变**（action 和 button 是两个独立控件）
- 因此 `record_btn.isChecked()` 返回旧状态，control_panel.record_button 已是该状态
- 同步判断 `if control_panel.record_button.isChecked() != checked` 为 False，什么都不做

**结果**：用户按 Ctrl+R 或点击菜单"开始/停止录音"完全无效。唯一能录音的方式是点击工具栏上的"录音"按钮。

**修复方向**：菜单项应调用 `self.record_btn.toggle()` 或 `self.record_btn.setChecked(not self.record_btn.isChecked())`，让 toggled 信号自然触发。

---

### [真bug] 问题5：tune_sdr 工具名不存在于后端 method_map

**位置**：`main_window.py:513-518` → `mcp_worker.py:256-265`

`_on_tune_sdr` 调用 `self._worker.call_tool("tune_sdr", {...})`，但 `MCPWorker._real_tool()` 的 method_map 中**没有 "tune_sdr"** 这个键。

method_map 仅包含：`tune_fm`, `tune_am`, `set_volume`, `start_record`, `stop_record`, `get_version`, `reboot`, `list_tools`。

调用 `tune_sdr` 会走到 `mcp_worker.py:270` 返回 `{"error": "未知工具: tune_sdr"}`。

**影响**：控制面板的 SDR 调谐功能（非 FM/AM 模式）在真实硬件上完全无效。

---

### [真bug] 问题6：sdr_set_frequency 工具名不存在于后端 method_map

**位置**：`main_window.py:748` → `mcp_worker.py:256-265`

`_on_sky_object_clicked` 中点击卫星后调用 `self._worker.call_tool("sdr_set_frequency", {"frequency_hz": ...})`，但 method_map 中同样没有 `sdr_set_frequency`。

**影响**：天空图点击卫星后，UI 频率显示和控制面板频率会更新，但**实际硬件频率不会改变**。卫星跟踪的"自动调谐"是假的。

---

### [真bug] 问题7：SpectrumDataGenerator 完全是模拟数据，不接后端

**位置**：`spectrum_widget.py:30-83`

`SpectrumDataGenerator.generate()` 生成的是硬编码的 5 个高斯峰 + 高斯噪声：
```python
self._stations = [
    (98.5, -30, 0.05), (97.4, -45, 0.08), (100.0, -50, 0.06),
    (95.5, -40, 0.07), (101.8, -35, 0.05),
]
```

MCPWorker 的信号列表（`mcp_worker.py:115-121`）中**没有 spectrum_data 信号**。后端只推送 status/gps/imu/connection/tool_result/error/log。

**结论**：频谱显示和瀑布图从头到尾都是本地模拟数据，与真实 SDR 硬件接收的信号无关。这是一个**重大的空壳组件**——UI 上画了完整的频谱图，但数据是假的。

---

### [真bug] 问题8：call_tool 在 UI 线程执行网络请求

**位置**：`main_window.py` 各处 `self._worker.call_tool(...)` 调用 → `mcp_worker.py:233-249`

虽然 MCPWorker 被 `moveToThread` 到了后台线程，但 `call_tool` 是从 UI 线程**直接调用**的普通方法（不是通过信号-队列连接）。

在 Qt 中，直接方法调用始终在调用者的线程上执行。因此 `call_tool` → `_real_tool` → `self.client.tune_fm()` 等网络请求**在 UI 线程上执行**，会阻塞界面。

模拟模式下无网络请求所以无感，但连接真实硬件时，每次调谐/调音量/录音都会冻结 UI。

---

### [真bug] 问题9：SatelliteTracker 使用错误的属性名读取观测站坐标

**位置**：`main_window.py:641-643`

```python
obs_lat = getattr(self, "obs_lat", 43.88)    # 属性名是 self._observer_lat
obs_lon = getattr(self, "obs_lon", 125.32)   # 属性名是 self._observer_lon
```

实际定义的属性是 `self._observer_lat`（L49）和 `self._observer_lon`（L50），带下划线前缀。`getattr(self, "obs_lat", ...)` 永远找不到，始终返回默认值 43.88/125.32。

**影响**：`_load_gui_config`（L824-825）从配置文件恢复的观察者坐标永远不会传递给 SatelliteTracker。即使配置文件中保存了不同的经纬度，卫星跟踪仍使用长春硬编码坐标。

---

### [空壳] 问题10：_on_mode_changed 是空槽（pass）

**位置**：`main_window.py:549-551`

```python
@Slot(str)
def _on_mode_changed(self, mode: str):
    pass  # 模式切换在控制面板内部处理
```

控制面板内部确实处理了 UI 状态更新（`control_panel.py:262-266`），但**后端从未收到模式切换通知**。用户从 FM 切到 AM 后，只有按下"调谐"按钮才会通过 `tune_am_requested` 间接通知后端。如果用户只切换模式不调谐，后端解调器模式不变。

---

### [空壳] 问题11：_start_sweep 只填文本不提交

**位置**：`main_window.py:611-618`

```python
self.ai_panel.input_field.setText("扫频 87-108 MHz 找所有电台")
```

只把指令填入输入框，不触发发送。用户必须再按一次回车或发送按钮。不是死按钮，但行为不完整——点击"FM 扫频找台"应该直接开始扫频。

---

### [空壳] 问题12：AI tool_call 仅同步 2 种工具的 UI

**位置**：`main_window.py:570-579`

```python
if tool_name == "tune_fm":
    ...
elif tool_name == "set_volume":
    ...
```

AI agent 可发出的工具调用包括 `tune_am`, `start_record`, `stop_record`, `get_gps`, `get_imu`, `get_status`, `scan_fm`（见 `ai_panel.py:575-605`），但 `_on_ai_tool_call` 只对 `tune_fm` 和 `set_volume` 做 UI 同步。其他工具调用后 UI 不更新。

---

### [建议] 问题13：连接状态颜色硬编码，不随主题适配

**位置**：
- L209: `render_label.setStyleSheet("color: #6B6B6B; font-size: 9pt;")`
- L394: `self.conn_label.setStyleSheet("color: #C4845C; font-weight: 600;")`（模拟模式橙）
- L422: `self.conn_label.setStyleSheet("color: #C4B85C; font-weight: 600;")`（连接中黄）
- L464: `self.conn_label.setStyleSheet("color: #6BA89A; font-weight: 600;")`（已连接绿）

这些颜色值直接写死在 setStyleSheet 中，不经过 themes 系统。在深色主题下，#6B6B6B 灰色可能看不清，橙色/黄色/绿色也可能与主题不协调。

**建议**：将状态色加入 themes 的 colors dict，通过 `theme.colors["status_sim"]` 等方式引用。

---

### [建议] 问题14：瀑布图菜单与工具栏按钮 checked 状态不同步

**位置**：L124-128（菜单）+ L197-202（工具栏按钮）

两者都连接到 `_toggle_waterfall`，但互不同步。点击工具栏按钮后，菜单项的 checked 状态不变；反之亦然。

---

### [建议] 问题15：_disconnect 不等待 worker 线程中的 in-flight 网络请求

**位置**：`main_window.py:444-454` → `mcp_worker.py:354-362`

`MCPWorkerManager.stop()` 调用 `thread.quit()` + `thread.wait(3000)`，但如果 worker 线程正在执行阻塞的网络请求（如 `client.get_status()`），`quit()` 只是告诉线程退出事件循环，不会中断正在执行的方法。3 秒等待可能不够。

---

## 四、无限递归检查

**结论：未发现信号-槽无限递归。**

逐一排查：

1. **录音按钮链路**：
   - `record_btn.toggled → _toggle_record → control_panel.record_button.setChecked() → control_panel.record_button.toggled → _on_record_toggled`
   - `_on_record_toggled` 不回调 `record_btn.setChecked()`，无回路 ✅

2. **主题切换链路**：
   - `theme_combo.currentIndexChanged → _on_theme_combo_changed → _apply_theme → theme_combo.blockSignals(True) + setCurrentIndex`
   - `_apply_theme` 中 blockSignals 防止了回环 ✅

3. **频率调谐链路**：
   - `spectrum.freq_changed → _on_spectrum_freq_changed → control_panel.set_freq_fm()`
   - `control_panel.set_freq_fm()` 不触发 `tune_fm_requested` 信号（它是设置方法，不是用户操作）✅
   - `_on_tune_fm → spectrum.set_center_freq()` → 不触发 `freq_changed` 信号（set_center_freq 是程序调用，非用户拖拽）✅

4. **AI 工具调用链路**：
   - `ai_panel.tool_call_requested → _on_ai_tool_call → worker.call_tool → worker.tool_result → ai_panel.on_tool_result`
   - `on_tool_result` 不发出 `tool_call_requested`，无回路 ✅

---

## 五、性能问题汇总

| 操作 | 位置 | 阻塞内容 | 严重度 |
|---|---|---|---|
| 5s 定时器卫星计算 | L666-697 | UI 线程中对每颗卫星做 sgp4 计算（decoders 路径，简化算法） | 中 |
| 10s 定时器卫星计算 | rf_sky_view.py:1123-1156 | UI 线程中对每颗卫星做 sgp4 + 11个轨迹点 sgp4 | 中高 |
| NTP 网络请求 | L702-723 | 每 ~150s 在 UI 线程同步发起 NTP 请求 | 高 |
| call_tool 网络请求 | 多处 | UI 线程直接调用 `client.tune_fm()` 等网络方法 | 高 |
| 频谱数据生成 | spectrum_widget.py:55-83 | numpy 运算 512 bin，每帧 < 1ms | 低 |

---

## 六、空壳/占位 UI 元素清单

| UI 元素 | 位置 | 实际状态 |
|---|---|---|
| 频谱图 + 瀑布图 | L271-273 | **完全模拟数据**，不接真实 SDR 采样流 |
| 热力图（方位-仰角） | L657-663 | 90 个 sin/cos 假点，无真实扫频数据 |
| 卫星轨迹线 | rf_sky_view.py:1145-1156 | 有数据（orbit.py），但每 5s 被 decoders 路径擦除 |
| "FM 扫频找台"菜单 | L138-140 | 只填 AI 输入框，不执行扫频 |
| 录音菜单 (Ctrl+R) | L142-145 | 死菜单，不触发任何录音操作 |
| SDR 调谐（非 FM/AM） | L513-518 | 调用不存在的后端工具 |
| 卫星点击自动调谐 | L740-757 | 调用不存在的后端工具，UI 更新但硬件不响应 |
| 模式切换通知后端 | L549-551 | 空槽 pass |
| 观察者坐标可配置 | L641-643 | 属性名错误，配置加载后不生效 |
| AI 工具调用 UI 同步 | L570-579 | 仅 2/9 种工具有 UI 同步 |

---

## 七、问题统计

| 类别 | 数量 |
|---|---|
| [真bug] | 9 |
| [空壳] | 3 |
| [占位] | 0（热力图/频谱数据归入空壳） |
| [建议] | 3 |
| **合计** | **15** |

---

## 八、修复优先级建议

1. **P0（功能性阻断）**：
   - 问题7：频谱数据不接后端（整个频谱显示是假的）
   - 问题4：录音菜单/Ctrl+R 死按钮
   - 问题5+6：tune_sdr 和 sdr_set_frequency 工具名不存在

2. **P1（数据正确性）**：
   - 问题1：双定时器冲突，删除 decoders 路径的 5s 定时器
   - 问题9：SatelliteTracker 属性名错误导致配置坐标不生效
   - 问题8：call_tool 在 UI 线程阻塞

3. **P2（完整性/体验）**：
   - 问题3：授时定时器独立化
   - 问题10-12：空槽和半成品功能补全
   - 问题13-15：主题适配和同步问题
