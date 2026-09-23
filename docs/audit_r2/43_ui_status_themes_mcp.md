# R2 审查：UI 状态面板 / 主题系统 / 入口 / MCP Worker

- 审查范围：`desktop/status_panel.py`、`desktop/themes.py`、`desktop/main.py`、`desktop/mcp_worker.py`
- 交叉参照：`desktop/main_window.py`、`desktop/control_panel.py`、`mbdsdr_mcp_client.py`
- 审查方式：只读，逐行阅读真实代码
- 图例：[真bug] 功能错误/崩溃/数据错误；[空壳] connect 了但函数体 pass 或永不触发；[占位] 代码存在但未真正接入；[建议] 健壮性/可维护性

---

## 一、结论速览

- 状态面板**不是**固定值：它通过 `status_updated / gps_updated / imu_updated / connection_changed / tool_result` 5 个 Qt 信号接收数据（`main_window.py:432-442` 已连接）。初始 "--" 只是占位。
- 主题系统**真实可用**：三套主题（日式浅色 / 深色 / 高对比）、QPalette + QSS 应用、菜单栏 + 工具栏下拉双向切换都接好了（`main_window.py:113-120, 187-192, 352-378`）。
- 但 `mcp_worker.py` 存在**严重的跨线程调用 bug**：UI 线程直接调用 worker 的槽函数 `call_tool`，导致网络 I/O 跑在 UI 线程、且 `self.client` 被两个线程并发访问。
- 另有若干"接了线但 worker 不认识的工具名"和"CLI `--theme` 被静默覆盖"等真 bug。

---

## 二、status_panel.py（272 行）

### [真bug] RSSI 阈值与模拟数据量纲不一致，颜色指示恒为绿色
- 文件：`desktop/status_panel.py:224-229`
  ```python
  if rssi > -50:   color: #6BA89A  # 强信号-绿
  elif rssi > -70: color: #C4B85C  # 中等-黄
  else:            color: #B85C5C  # 弱-红
  ```
  阈值按真实 dBm（负数）设计。但 `SimDataGenerator.get_status()`（`mcp_worker.py:55-60`）返回的 `rssi` 范围是 **+10 ~ +70**（`best_rssi` 初值 10，台表给出 30–70）。
- 后果：模拟模式下所有 rssi 都 > -50，**永远走绿色分支**，黄/红分支是死代码。真实硬件若返回负 dBm 则逻辑正确，但模拟演示完全看不出颜色变化。

### [真bug] 缺省值 0 被当成真实数据显示
- 文件：`desktop/status_panel.py:206-207`
  ```python
  rssi = status.get("rssi", 0)
  snr  = status.get("snr", 0)
  ```
- 后果：当后端字典缺键时，显示 "0 dBm" / "0 dB"，并被上一条规则判为"强信号绿色"。缺省应为 `None`/`"--"`，不应渲染成 0。

### [真bug] GPS / IMU 字典字段缺 None 防护
- 文件：`desktop/status_panel.py:239-242`
  ```python
  self.gps_lat.setText(f"{gps.get('lat', 0):.6f}")
  ```
  若服务器返回 `{"lat": None}`（key 存在但值为 None），`None:.6f` 直接 `TypeError`，整个槽崩溃。
- 文件：`desktop/status_panel.py:247-258`
  ```python
  acc = imu.get("acc", [0,0,0])
  self.acc_x.setText(f"{acc[0]:.2f}")
  ```
  若 `acc` 为 `None` 或长度 < 3，`acc[0]` / 索引访问抛 `IndexError/TypeError`。`gyr/mag` 同样问题。
- 后果：真实硬件偶发上报字段缺失时，UI 槽抛异常，Qt 仅打印到 stderr，面板定格在旧值。

### [空壳] "设备信息"分组（固件/SDR/MCU）几乎永远是 "--"
- 文件：`desktop/status_panel.py:159-183, 266-272`
- `on_tool_result` 只在 `tool_name == "get_version"` 时填 `fw_version / sdr_chip / mcu_chip`。
- 但全工程**没有任何地方自动发起** `call_tool("get_version", {})`：
  - `mcp_worker._connect()`（`mcp_worker.py:184`）虽然调用了 `self.client.get_version()`，但**没有把结果通过 `tool_result` 信号 emit 出去**。
  - `main_window.py` 全部 `call_tool(...)` 调用点（505/511/516/531/537/544/573/585/748）都不含 `get_version`。
- 后果：固件/SDR/MCU 三个标签初始化后永远是 "--"。需要在连接成功后由 worker 主动 `self.tool_result.emit("get_version", version)`。

### [主题硬编码] 面板内联样式绕过主题系统
- 文件：`desktop/status_panel.py:32, 195, 199, 225, 227, 229, 236`
  - 硬编码色值：`#B85C5C`（红）、`#6BA89A`（绿）、`#C4B85C`（黄）。
  - 这些值与 `themes.py` 中 `JAPANESE_LIGHT.colors["danger"]="#B85C5C"` 恰好一致，但 **DARK 主题的 danger 是 `#D47070`、HIGH_CONTRAST 是 `#FF4444`**。
- 后果：切到深色 / 高对比主题后，连接指示灯、RSSI 颜色、GPS fix 颜色仍然是日式浅色那组低饱和色，与主题不匹配。`setStyleSheet()` 是内联覆盖，优先级高于 app QSS，主题切换不会刷新它们。
- 同类硬编码扩散到 `main_window.py:209, 394, 422, 464`（`#6B6B6B`、`#C4845C`、`#C4B85C`、`#6BA89A`）。

### [建议] 初始字号与连接后字号跳变
- 文件：`desktop/status_panel.py:32` 初始 `font-size: 16pt;`，而 `on_connection_changed`（195/199）切到 12pt。首次连接瞬间字号从 16pt 跳到 12pt，视觉抖动。

---

## 三、themes.py（380 行）

### [真实功能] 多主题系统确实存在并被使用
- 三套主题实例：`JAPANESE_LIGHT`（277-305）、`DARK`（308-336）、`HIGH_CONTRAST`（339-367）。
- `Theme.apply()`（64-92）同时设置 QPalette 和 QSS。
- `main_window._apply_theme()`（`main_window.py:352-366`）调用 `theme.apply(app)` 并把 `spectrum_colors` 传给频谱组件；菜单栏（113-120）和工具栏 QComboBox（187-192）双向切换都接好了。
- 这部分不是空壳。

### [空壳] `get_monospace_font()` 定义后从未被调用
- 文件：`desktop/themes.py:37-49`
- 全仓 grep 确认：仅定义处出现，无任何 `get_monospace_font()` 调用。QSS 里改用字符串 `font-family: "JetBrains Mono", "Fira Code", "Consolas", monospace`（115, 120, 216 行）。
- 死代码；要么删掉，要么在需要等宽字体的控件上 `label.setFont(get_monospace_font())`。

### [建议] QSS 字体族与 `get_app_font()` 重复维护
- 文件：`desktop/themes.py:101` vs `17-34`
- QSS 里硬写了一份 `"MiSans", "Noto Sans CJK SC", ...`，`get_app_font()` 又写了一份。新增字体需改两处。

### [建议] `QWidget { background-color: ... }` 一刀切
- 文件：`desktop/themes.py:98-102`
- 对所有 QWidget 设背景，常见 Qt 反模式：会让后续自定义绘制控件（频谱、天空图）的背景被意外覆盖。目前靠 `spectrum.set_theme_colors()` 手动补救。

### [建议] 缺语义化色板
- `colors` 里有 `danger` 但没有 `success / warning / info`。`status_panel` 自己发明 `#6BA89A`(绿)/`#C4B85C`(黄)，而这两个色值其实已经在 `JAPANESE_LIGHT.spectrum_colors` 里出现过。建议主题直接暴露 `success/warning` 语义色，面板统一取用。

---

## 四、main.py（99 行）

### [真bug] CLI `--theme` 参数被静默覆盖
- 文件：`desktop/main.py:37-39, 57-58`
  ```python
  theme = get_theme(args.theme); theme.apply(app)   # 57-58：先应用 CLI 主题
  ```
  然后 `MainWindow.__init__`（`main_window.py:59`）**立即** `self._apply_theme(DEFAULT_THEME)` 覆盖一次；100ms 后 `_load_gui_config`（`main_window.py:805-831`）再从 `~/​.mbdsdr/gui_config.json` 读上次保存的主题（827-829）覆盖。
- 后果：用户传 `--theme dark` 完全无效，最终主题 = gui_config.json 里保存的值。
- 修复方向：`MainWindow` 应接收初始主题参数，或 `_load_gui_config` 仅在 CLI 未显式指定时才覆盖。

### [真bug/竞态] `--host` 模式下模拟连接与真实连接同时排程
- 文件：`desktop/main.py:65-71` vs `desktop/main_window.py:62`
  ```python
  # main_window.py:62
  QTimer.singleShot(500, self._auto_connect_simulation)   # 无条件排程
  # main.py:71
  QTimer.singleShot(500, lambda: window._connect_real(...))
  ```
- 两个 singleShot 都在 500ms 触发，且 `_auto_connect_simulation` 不检查 `args.host`。结果：先起 sim worker，立刻被 `_connect_real → _disconnect()` 停掉，再起真实 worker。功能上最终正确，但白启动/白销毁一个 QThread，且日志里会先打"已进入模拟模式"再打"连接真实硬件"，误导用户。

### [空壳] `if args.sim: pass` 分支
- 文件：`desktop/main.py:65-67`
  ```python
  if args.sim:
      # 模拟模式由主窗口自动连接
      pass
  ```
  整个分支只有 pass。注释说明 sim 由主窗口自动连接，那这个 if 本身就是死代码。

### [建议] `--theme` choices 硬编码，与 THEMES 字典脱钩
- 文件：`desktop/main.py:38`
  ```python
  choices=["japanese_light", "dark", "high_contrast"]
  ```
  新增主题时要同时改 `themes.py:THEMES` 和这里，漏改就出 bug。

### [建议] 无异常兜底
- `main()` 未对 `MainWindow()` 构造或 `app.exec()` 包 try/except；未设置 `sys.excepthook`。Qt 槽里未捕获异常在 PySide6 默认只打到 stderr，开发期易被忽略。

### [建议] `window.app = app` 外挂属性
- 文件：`desktop/main.py:62`
- `MainWindow._apply_theme`（`main_window.py:356`）用 `self.app if hasattr(self,'app') else QApplication.instance()` 兜底。能工作，但属于约定式注入，建议显式构造参数。

---

## 五、mcp_worker.py（362 行）

### [真bug — 严重] UI 线程直接调用 worker 槽 `call_tool`，跨线程执行
- 文件：`desktop/mcp_worker.py:233-249`（`call_tool` 是 `@Slot(str, dict)`）；`desktop/mcp_worker.py:344`（`self.worker.moveToThread(self.thread)`）
- 但 `main_window.py` 在 UI 线程**直接**调用：
  - `505` `self._worker.call_tool("tune_fm", ...)`
  - `511` `self._worker.call_tool("tune_am", ...)`
  - `516` `self._worker.call_tool("tune_sdr", ...)`
  - `531` `self._worker.call_tool("set_volume", ...)`
  - `537/544` `start_record/stop_record`
  - `573` AI 工具调用
  - `585` 频谱拖频
  - `748` 天空图卫星点击 `sdr_set_frequency`
- 后果（Qt 直调 moved-to-thread 对象的方法 = 在调用方线程执行，不走队列）：
  1. `self.client.tune_fm(...)` 是 websocket 同步 I/O（`mbdsdr_mcp_client.py:83` `create_connection` / `call()`），**直接阻塞 UI 线程**，卡 UI。
  2. `self.client` 同时被 UI 线程（`call_tool`）和 worker 线程（`_poll` QTimer，`mcp_worker.py:207-224`）访问。虽然 client 内部有 `threading.Lock`（`mbdsdr_mcp_client.py:69`），但 `close()` vs `get_status()` 的交错、`self.ws` 状态机仍存在竞态窗口。
- 正确做法：在 MCPWorker 上加一个 `request_tool = Signal(str, dict)`，UI 线程 `self._worker.request_tool.emit(...)`，worker 线程在自己的 `call_tool` 槽里执行。

### [真bug] `MCPWorkerManager.stop()` 跨线程调用 worker
- 文件：`desktop/mcp_worker.py:354-362`
  ```python
  def stop(self):
      if self.worker: self.worker.stop()      # 357：UI 线程直调
      if self.thread:
          self.thread.quit(); self.thread.wait(3000)   # 359-360
  ```
- `worker.stop()`（156-170）里访问 `self._timer.stop()` 和 `self.client.close()`。QTimer 属于 worker 线程，从 UI 线程 stop 不是线程安全的；`client.close()` 又与 worker 线程 `_poll` 中的 `client.get_status()` 竞态。

### [真bug] `thread.wait(3000)` 超时后线程泄漏
- 文件：`desktop/mcp_worker.py:360-362`
- 真实 client 超时 5 秒（`mcp_worker.py:181` `timeout=5`）。若 `_poll` 正卡在 `client.get_status()` 的 5s 等待中，`wait(3000)` 3 秒超时返回，随后 `self.worker=None; self.thread=None` 丢弃引用。
- 后果：线程仍在跑（QTimer 还在 event loop 里），反复连接/断开会累积僵尸线程和未关闭的 websocket。

### [空壳] `self.worker.finished = self.thread.quit` 不是信号连接
- 文件：`desktop/mcp_worker.py:347`
  ```python
  self.worker.finished = self.thread.quit  # 自定义信号
  ```
- `MCPWorker` 的信号列表在 `mcp_worker.py:115-121`：`status_updated / gps_updated / imu_updated / connection_changed / tool_result / error_occurred / log_message`，**没有 `finished`**。
- 这行只是给实例动态塞了一个 Python 属性，不是 `Signal`，也没 `connect()`。worker 永远不会 emit 它，这行完全无效。注释"自定义信号"具有误导性。线程退出实际靠 `MCPWorkerManager.stop()` 里的 `thread.quit()`。

### [真bug] UI 调用了 worker 不认识的工具名
- `main_window.py:516` 调 `"tune_sdr"`；`main_window.py:748` 调 `"sdr_set_frequency"`。
- 但 `MCPWorker._real_tool` 的 method_map（`mcp_worker.py:256-265`）只认：`tune_fm / tune_am / set_volume / start_record / stop_record / get_version / reboot / list_tools`。
- `_sim_tool`（272-323）同样不认这两个名字。
- 后果：
  - 控制面板选预设 → `control_panel.py:314 emit tune_sdr_requested` → `main_window.py:514-517` → worker 返回 `{"error": "未知工具: tune_sdr"}`。
  - 天空图点卫星 → `main_window.py:748` → `{"error": "未知工具: sdr_set_frequency"}`。
- 这两条 UI 链路是"接了线但 worker 不执行"的假功能。

### [真bug] `client.connect()` 返回值被忽略
- 文件：`desktop/mcp_worker.py:181-184`
  ```python
  self.client = MBDSDRClient(...)
  self.client.connect()                 # 返回 bool，被丢弃
  version = self.client.get_version()    # connect 失败时 ws=None
  ```
- `mbdsdr_mcp_client.py:76-94` 明确返回 `True/False`。失败时 `self.ws` 仍为 None，下一步 `get_version()` 抛异常，被外层 `except Exception`（189）吞掉后回退模拟。逻辑上兜住了，但应该先判 `if not self.client.connect(): raise ...`。

### [真bug] 模拟数据与真实数据字段契约不对齐
- 模拟 `get_status()`（`mcp_worker.py:61-72`）返回键：`mode / mode_name / freq / freq_display / rssi / snr / volume / muted / recording / uptime`。
- 真实 `client.get_status()`（`mbdsdr_mcp_client.py:201-204`）原样返回服务器 result，服务器端键名未知（很可能是 `mode:int` / `freq:Hz`）。
- `status_panel.on_status_updated`（`status_panel.py:206-210`）读 `mode_name / freq_display`，真实服务器若不提供这两个合成键，模式和频率永远显示 "--"。
- worker 层没有做"原始字段 → UI 字段"的适配层。

### [建议] 轮询频率与 UI 更新
- `poll_interval_ms = 1000`（`mcp_worker.py:133`），每秒 emit 3 个信号（status/gps/imu），每个信号在 `main_window._connect_worker_signals`（432-436）里同时连了 `status_panel` 槽和 `main_window` 自己的 `_on_*_for_ui` 槽——每秒更新约 20+ 个 QLabel。1Hz 下不会阻塞 UI，合理。
- 但 GPS/IMU 1Hz 偏慢（IMU 9 轴通常需要 10–50Hz 才有意义），而 RSSI 1Hz 又偏粗。建议分开频率：status 1Hz、gps 2s、imu 独立定时器。

### [建议] 静默回退模拟模式
- 文件：`desktop/mcp_worker.py:189-194`
- 真实连接失败后 `self.use_simulation=True; self.connected=True; connection_changed.emit(True, "连接失败，回退模拟模式")`。UI 显示"已连接"，但数据是假的。建议状态文字用黄色/橙色区分，或单独 emit 一个 degraded 信号。

### [建议] 无重连逻辑
- `_poll`（207-224）捕获异常只打日志，`self.connected` 永远 True。设备中途掉线后，UI 不会自动重连，也不会把状态切回"未连接"。

### [建议] 风格不一致
- 文件：`desktop/mcp_worker.py:60, 67` 用 `__import__('random').randint(...)`；75/86 行又在方法内 `import random`。应顶部 `import random`。

---

## 六、跨模块 / main_window 集成问题

### [空壳] `_on_mode_changed` 槽函数体只有 pass
- 文件：`desktop/main_window.py:549-551`
  ```python
  @Slot(str)
  def _on_mode_changed(self, mode: str):
      pass  # 模式切换在控制面板内部处理
  ```
- 它由 `control_panel.mode_changed`（`main_window.py:297`）连接。接了线但什么都不做。要么删掉连接，要么在这里同步频谱/状态面板的模式显示。

### [建议] 频谱拖频洪水式调谐
- 文件：`desktop/main_window.py:581-585`
  ```python
  @Slot(float)
  def _on_spectrum_freq_changed(self, freq: float):
      self.control_panel.set_freq_fm(freq)
      if self._worker:
          self._worker.call_tool("tune_fm", {"freq_mhz": freq})
  ```
- 拖动频谱过程中 `freq_changed` 会高频触发，每次都直调 `call_tool`（叠加上面的 UI 线程阻塞 bug）。应做 debounce（如 150–300ms 节流）或只在 `freq_changed` 的"释放"事件下发。

### [建议] 录音按钮双向同步缺失
- 工具栏 `record_btn.toggled → _toggle_record`（`main_window.py:222, 595-600`）会反写 `control_panel.record_button.setChecked()`。
- 但反向：用户点控制面板自己的录音按钮 → `control_panel.py:343 emit record_toggled` → `main_window._on_record_toggled`（534-547）**不会**回写 `self.record_btn.setChecked()`。
- 后果：从控制面板启动录音后，工具栏录音按钮仍显示未按下。

### [已确认] 未发现无限递归
- 录音链路：`record_btn.toggled → _toggle_record → set control_panel.record_button.setChecked → control_panel.record_toggled → _on_record_toggled`。`_on_record_toggled` 不碰 `record_btn`，且 `main_window.py:596` 注释明确警示过这个递归。✅
- 主题切换：`_on_theme_combo_changed → _apply_theme → theme_combo.setCurrentIndex` 期间 `blockSignals(True/False)`（`main_window.py:371-373`）。✅
- 频谱→控制面板：`control_panel.set_freq_fm()`（`control_panel.py:349-354`）只改内部状态和显示，**不** emit `tune_fm_requested`，不会回流到频谱。✅

### [建议] closeEvent 可能最多卡 3 秒
- `main_window.py:771-775` close → `_disconnect → worker_manager.stop → wait(3000)`。叠加"线程泄漏"bug，极端情况下关窗卡顿。

---

## 七、问题清单（按严重度排序）

| # | 级别 | 位置 | 摘要 |
|---|---|---|---|
| 1 | 真bug | mcp_worker.py:233 + main_window.py 多处 | UI 线程直调 worker.call_tool，websocket I/O 阻塞 UI 线程、与 _poll 竞态 |
| 2 | 真bug | mcp_worker.py:354-362 | stop() 跨线程操作 worker.timer/client，wait(3000) 超时后线程泄漏 |
| 3 | 真bug | main.py:57-58 + main_window.py:59,827 | CLI `--theme` 被 MainWindow 默认主题 + 保存配置覆盖，无效 |
| 4 | 真bug | mcp_worker.py:256-265 vs main_window.py:516,748 | `tune_sdr` / `sdr_set_frequency` 未实现，UI 两条链路假功能 |
| 5 | 真bug | status_panel.py:224-229 vs mcp_worker.py:55-60 | RSSI 量纲不一致，模拟下颜色恒绿 |
| 6 | 真bug | status_panel.py:239-242, 247-258 | GPS/IMU 字段 None 或缺长度时槽崩溃 |
| 7 | 真bug | mcp_worker.py:181-184 | client.connect() 返回值被忽略 |
| 8 | 真bug | mcp_worker.py:61-72 vs 真实 server | 模拟字段 mode_name/freq_display 在真实后端可能不存在 |
| 9 | 空壳 | status_panel.py:266-272 | 设备信息（固件/SDR/MCU）永远 "--"，无自动 get_version 上报 |
| 10 | 空壳 | mcp_worker.py:347 | `self.worker.finished = self.thread.quit` 不是信号连接，无效 |
| 11 | 空壳 | main_window.py:549-551 | `_on_mode_changed` 函数体 pass |
| 12 | 空壳 | main.py:65-67 | `if args.sim: pass` 死分支 |
| 13 | 空壳 | themes.py:37-49 | `get_monospace_font()` 从未被调用 |
| 14 | 主题硬编码 | status_panel.py:32,195,199,225,227,229,236；main_window.py:209,394,422,464 | 颜色 hex 硬编码，深色/高对比主题下不跟随 |
| 15 | 真bug/竞态 | main.py:71 + main_window.py:62 | --host 模式下 sim 与真实连接同时排程 |
| 16 | 建议 | main_window.py:581-585 | 频谱拖频洪水式 call_tool，需 debounce |
| 17 | 建议 | main_window.py:534-547 vs 222 | 录音按钮反向同步缺失 |
| 18 | 建议 | mcp_worker.py:189-194 | 连接失败静默回退模拟，UI 仍显示"已连接" |
| 19 | 建议 | mcp_worker.py:133 | GPS/IMU 1Hz 偏慢；建议分频率轮询 |
| 20 | 建议 | main.py | 无 sys.excepthook、--theme choices 与 THEMES 脱钩 |

---

## 八、修复优先级建议

1. **P0（必修）**：#1 call_tool 改信号队列调用；#2 stop() 流程改 queued call + 更长 wait 或强杀；#4 补 `tune_sdr` / `sdr_set_frequency` 实现或删 UI 入口。
2. **P1**：#3 CLI 主题不被覆盖；#5 RSSI 量纲统一；#6 槽函数 None 防护；#9 连接成功后 emit get_version 结果。
3. **P2**：#14 状态面板色值接入主题；#16 拖频 debounce；#17 录音按钮双向同步；#18 模拟回退状态可见化。
