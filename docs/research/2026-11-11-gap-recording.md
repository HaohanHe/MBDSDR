# 录音、回放与文件管理：只读缺口调研（2026-11-11）

> 证据状态：E4——本文件数字未逐条绑定原始件/复跑命令，引用前须复核（判据见 docs/evidence-standard.md）。
> 性质：**只读调研**，未修改任何产品代码。
> 范围：`desktop` 的录音/回放链路（`_start_recording` / `_confirm_record_dir_safe` / `_record_write_chunk` /
> `_stop_recording` / `_replay_recording` / `audio_out`）+ `mbdsdr_ai` 的录段、sidecar、`recording_library`、`recorder`。
> 方法：静态逐行核对 + 离屏 Qt 探针驱动真 `MainWindow`（stub 后端 2.4 MS/s / 100 MHz）逐条走成功、
> 目录不可用、取消、注入 ENOSPC、回放、调谐六类路径；同时复跑现有测试。
> 探针脚本（仓库外，保留以便复跑）：`D:\mbdsdr\output\tmp\probe_rec\probe_ui_truth.py`。
> 行号口径：以下 `path:line` 均以 2026-11-11 当前工作树为准；工作树含其他工作线的未提交改动，
> 这些改动落库后行号可能整体偏移，引用时请以符号名二次核对。

## 0. 先报：「失败路径 UI 是否说真话」

实测口径：每一步同时记录**界面显示的文本/按钮选中态**与**内部真实状态**（`_recording`、文件是否落盘、
sidecar 内容），两者不一致记为「说假话」。

| 场景 | 用户看到 | 真实状态 | 一致？ |
|---|---|---|---|
| A 正常开始（`_toggle_record()`） | 工具栏选中、文案「停止中」；面板选中、「录音中... 00:00」；状态栏「开始录制 baseband: …」 | 文件已 `open(...,"wb")`，`_recording=True` | ✅ |
| A 写入 12288 样本后未停止 | 面板仍「录音中... 00:00」 | `os.path.getsize()` = **0**（96 KiB 全在 Python 缓冲，未 flush） | ⚠️ 文件系统层面看起来没在写 |
| A 正常停止 | 面板「已存 12288 样本 (96 KB)」；状态栏「录制完成: … (12288 样本, 96.0 KB, 0s)」 | 文件 96 KiB、sidecar `samples=12288/duration=0.005` | ✅（时长显示 0s，见 §9 附录 P3） |
| B 录音目录不可用（路径被文件占用） | 同一个弹窗**连续弹两次**；状态栏同文案；按钮已复位；**面板常驻「录音完成 (0 秒)」** | 未创建任何文件，`_recording=False` | ❌ 面板是假完成 + 双弹窗 |
| C 目录在程序目录内、用户取消确认 | 同一个确认框**弹两次**（要取消两次）；状态栏「已取消录音（录音目录未确认）」；按钮已复位；**面板常驻「录音完成 (0 秒)」** | 未创建任何文件 | ❌ 面板是假完成 + 双弹窗 |
| D 写入第 2 块时注入 ENOSPC | 状态栏「写入录音文件失败: [Errno 28] …（已写 65536 字节…）**；录制完成:** …（8192 样本…）」；**工具栏/面板仍选中**、面板文案「开始录音」、面板状态「已存 8192 样本 (64 KB)」 | `_recording=False`，流已收尾 | ❌ 三重假象：仍像在录 + 错误后面接「录制完成」 |
| D2 中止后用户再点「停止录音」 | 状态栏保留上一条错误；面板被改成「录音完成 (0 秒)」 | 已停止，无新动作 | ❌（因为 `_apply_recording` 幂等，第二次点击是空操作，面板却自行写了完成态） |
| E 录音中启动回放 | 状态栏只剩「回放中: … 0.0/0.1s」；**录音按钮仍选中**、面板状态「已存 240000 样本 (1875 KB)」 | 录音已被强制停止并落盘 | ❌ 「已停止当前录音（文件已保存）」在同函数内被回放进度覆盖，按钮仍显示录音态 |
| F 从 RecordTap 线程触发写失败 | 状态栏「…写入失败…；录制结束（无数据落盘）」；按钮仍选中；面板「未录音」 | 状态机走完；但控件/QTimer 操作发生在 RecordTap 线程 | ❌ 状态不统一 + 跨线程触碰 UI |
| G 录音中把 100 MHz 调到 105 MHz | 频谱/频率标签显示 105 MHz | sidecar `center_freq_hz` 仍是 **100 MHz**，且无调谐变更记录 | ❌ 回放时整份文件的绝对频率会被标错 |

**这一组结论**：

1. **内部发起的停止不会复位录音按钮**（`_stop_recording` 只改文案，不改 `setChecked`）——写盘失败、回放启动、
   设备掉线三条路径都会留下「按钮还按着、其实早停了」的状态。实测 D、E、F。
2. **控制面板把任何一次取消/失败都写成「录音完成 (N 秒)」**，因为它在按钮取消选中的瞬间无条件写完成态，
   而主窗口的失败分支只复位按钮、不回写面板。实测 B、C、D2。
2b. **开始失败时同一个弹窗会弹两次**：`_toggle_record` 与 `control_panel.record_toggled` 各调一次
   `_apply_recording`，成功时靠幂等去重，失败时 `_recording` 仍为 False，两次都会真正执行
   `_start_recording`。实测 B/C 的弹窗日志各有两条完全相同的记录（用户要取消两次）。
3. **写盘中止的最终文案自相矛盾**：`error` 前缀后面仍拼「录制完成」，与该方法自己的 docstring
   「绝不把中止说成『录制完成』」直接冲突。实测 D。
4. **回放启动会静默停掉录音**：提示语句在同一个函数里立刻被回放进度覆盖，用户只看到「回放中」，
   且按钮仍是录音态。实测 E。

## 1. 逐项实测路径

复现方式（离屏、隔离 profile，不碰真实用户配置）：

```powershell
# 探针内部依次执行：
#   A 成功：settings.record_dir=临时目录 → _toggle_record() → _record_write_chunk() → _toggle_record()
#   B 目录不可用：record_dir 指向一个已存在的文件 → _toggle_record()
#   C 取消：record_dir 指到程序目录内、ack=False，确认框不点任何按钮 → _toggle_record()
#   D 空间不足：开始录音后把 _record_stream_fh 换成第 2 次写入抛 OSError(28) 的写手 → 写 2 块
#   E 回放：录 240000 样本 → baseband_io.load_iq() → _begin_replay_from_rec() → _replay_tick() → _stop_replay()
#   F 线程：把同一个写失败回调放进 threading.Thread 执行
#   G 调谐：开始录音 → _on_tune_sdr(105 MHz, WFM) → 停止 → 读 sidecar
python D:\mbdsdr\output\tmp\probe_rec\probe_ui_truth.py
```

| 步骤 | 实测结果 |
|---|---|
| 开始录音（成功） | 文件立即创建（0 字节）；状态栏给出完整路径；面板进入 00:00 计时 |
| 开始录音（目录不可用） | 弹窗 + 状态栏同一文案；按钮复位；**面板「录音完成 (0 秒)」** |
| 开始录音（取消确认） | 状态栏「已取消录音（录音目录未确认）」；按钮复位；**面板「录音完成 (0 秒)」** |
| 录音中写入 | 计数递增；磁盘文件大小在 flush 前一直是 0（实测 96 KiB 未落盘） |
| 空间不足（注入 ENOSPC） | 立即中止、保留已写部分、写 sidecar；文案「错误；录制完成」；按钮不复位 |
| 停止（正常） | 面板/状态栏样本数与字节数一致；sidecar 与样本数自洽 |
| 回放 | 从 0 开始、只能整段顺序播；进度只出现在状态栏；无暂停/跳转/倍速/循环按钮 |
| 回放（文件 > 24,000,000 样本） | 弹窗说明后**只播前 24,000,000 样本**（2.4 MS/s 下 = 10 s） |
| 删除 | **无 UI 入口**：全仓只有「打开录音目录」交给资源管理器；`recording_library` 无桌面调用方 |

> 现有测试复跑：`tests/test_record_streaming.py`、`test_record_dir_safety.py`、`test_replay_load_async.py`、
> `test_recorder_overwrite_guard.py` 合计 **27 passed**；`tests/test_close_recording_guard.py` **3 passed**。
> 这些用例覆盖「开始/停止/关闭前确认/目录安全」，**没有一条断言失败后的按钮与面板文案**。

## 2. 开始/停止与失败收口

### R-01（P1）内部停止后录音按钮仍选中，UI 声称「在录」而实际已停

- 位置：`desktop/main_window.py:9706-9784`（`_stop_recording` 只改 `setText`/`setStyleSheet`，UI 恢复段在
  `9755-9757`，不 `setChecked(False)`）；调用方 `_report_record_abort`（`9417-9428`）、
  回放启动（`11412-11415`）、断开落盘（`6804-6809`）都不经按钮路径。
- 现象：写失败、开始回放、设备掉线后 `_recording=False`，但 `record_btn.isChecked()` 与
  `control_panel.record_button.isChecked()` 仍为 True。实测 D、E、F。
- 影响：用户以为还在录音，实际没有；再点一次是空操作（`_apply_recording` 幂等，`9385-9392`），
  可能错过整场录制。
- 成本：低（统一收口到一处 `setChecked(False)` + 2 条行为用例）。
- 产品决定：否（属缺陷）。

### R-02（P1）取消/目录失败/写失败都会在控制面板留下「录音完成 (N 秒)」

- 位置：`desktop/control_panel.py:1248-1256`（按钮一取消选中就无条件写完成态，`1256`）；
  `desktop/main_window.py:9560-9621`（无后端/取消/目录错误三个失败分支只复位按钮，不回写面板）。
  同一文件 `9593-9594` 的注释把口径写死了：「完成态只在成功路径出现」——实现与注释相反。
- 现象：B、C、D2 三次实测面板常驻「录音完成 (0 秒)」，磁盘上没有任何文件。
- 影响：控制面板是常驻读数，用户离开电脑回来看到「完成」，会误以为录到了。
- 成本：低（完成态只应由真正收尾的 `_stop_recording` 写；面板不自行宣布结果）。
- 产品决定：否。

### R-02B（P2）开始失败时同一个弹窗弹两次（取消也要取消两次）

- 位置：`desktop/main_window.py:1719`（`record_btn.toggled` → `_toggle_record`）、`1926`
  （`control_panel.record_toggled` → `_on_record_toggled`）、`8219-8245`（`_toggle_record` 对齐两个按钮后
  还「兜底再调一次」`_apply_recording`，见 `8243-8245`）；失败路径不置 `_recording`，幂等（`9385-9392`）去不掉第二次。
- 现象：实测 B/C 的弹窗日志各出现两条完全相同的记录：目录不可用弹两次、程序内目录的取消确认也要点两次。
- 影响：用户以为第一次点「取消」没生效；连续两个模态框会挡住状态栏，看不到「已取消录音…」的下一步提示。
- 成本：极低（`_toggle_record` 只负责按钮对齐，真正的录制动作只由 `_on_record_toggled` 触发；
  或用一次性标志/返回值判断）。
- 产品决定：否。

### R-03（P1）写盘失败的收口在 RecordTap 线程里操作 QWidget/QTimer

- 位置：`desktop/main_window.py:8810-8830`（录制写块）→ `8832-8844`（tap 回调）→ `9417-9428` →
  `9706-9784`（改文本、停 QTimer、改样式）；线程来源见 `desktop/receive_pipeline.py:551-565`
  （`RecordTap.run` 直接调用 `self._on_iq`，`main_window.py:8832` 是普通 Python 回调，不是跨线程信号）。
- 现象：ENOSPC/掉盘时，`_stop_recording` 在非 GUI 线程执行控件与定时器操作。实测 F 状态机走完，
  但线程归属由调用链确定，Qt 对此明确不安全。
- 影响：可能崩溃、定时器停不掉、面板停在中间态；这类问题只在真故障时出现，最难复现。
  同一路径也覆盖「录音目录在 U 盘/移动盘上被拔掉」的 EIO（`OSError` 同分支）。
- 成本：低（改为 `Signal` 投递到 GUI 线程再收口；写失败只置位+发信号）。
- 产品决定：否。

### R-04（P2）中止文案「写入失败…；录制完成」与自身注释矛盾

- 位置：`desktop/main_window.py:9782-9784`（`msg = f"{error}；{msg}"`）对比 `9707-9711`
  （docstring：「绝不把中止说成『录制完成』」）。
- 现象：实测 D 状态栏原文：「写入录音文件失败: [Errno 28] No space left on device（已写 65536 字节…）；
  录制完成: …（8192 样本, 64.0 KB, 0s）」。
- 影响：用户只读后半句会当成成功；与项目「失败必须显式中止」的口径冲突。
- 成本：极低（error 非空时走独立收尾文案；已有测试可扩一条断言）。
- 产品决定：否。

### R-05（P2）写盘无 flush/fsync，录制期间文件在资源管理器里可能一直是 0 字节

- 位置：`desktop/main_window.py:8822-8830`（每次 `fh.write(...)`，无 flush/fsync；只累加 `len(iq)`，
  不检查 `write()` 返回值是否短写）。
- 现象：实测 A2：写入 12288 样本（96 KiB）后 `os.path.getsize()` 仍为 **0**，`flush()` 后才变成 98304。
- 影响：用户中途看文件大小会以为没在录；断电/崩溃时缓冲里的数据全丢。
  `9418-9422` 的 docstring 声称「sidecar 的 samples 就是磁盘上真实的样本数」，但计数口径是
  `write()` 接受量；一旦 buffer 在 `flush/close` 时才因 ENOSPC 失败（**推断**，本探针用注入写手，
  未在真满盘上复现），sidecar 的样本数会大于磁盘真实内容，`9423` 的「已写字节数」也随之偏大。
- 成本：低（按时间或块数周期性 flush；收尾用真实文件大小回填 sidecar）。
- 产品决定：否。

### R-06（P2）无剩余空间预检、无实时大小/剩余可录时长、无丢样计数

- 位置：`desktop/settings.py:566-580`（`ensure_record_dir` 只 `makedirs`，不测可写/剩余空间）；
  `desktop/main_window.py:9763-9784`（停止后才报大小，且只有 1 s tick 的 `_record_seconds`）；全仓无
  `shutil.disk_usage` 调用。
- 现象：2.4 MS/s 的 cf32 约 19.2 MB/s（≈69 GB/h）；开录前不告知磁盘能撑多久，录制中不显示已写大小。
- 影响：常见结局是录到一半 ENOSPC，且用户事先毫无预期。
- 成本：低-中（预检 + 面板一行「已写 X MB / 剩余 Y 分钟」）。
- 产品决定：否（显示口径可后定）。

### R-07（P2）慢盘会把整条接收链拖停，且没有任何告警

- 位置：`mbdsdr_ai/dsp.py:1278-1300`（splitter 对每个输出串行 `swap`，任一路阻塞则全部停）、
  `mbdsdr_ai/dsp_stream.py:67-95`（`swap` 无限等待 `_can_swap`）、`desktop/receive_pipeline.py:47`
  （帧长 16384 样本 ≈ 6.8 ms @2.4 MS/s）。
- 现象：录制写一块需 >6.8 ms（持续 <19 MB/s，常见于 U 盘/网络盘/接近写满的盘）时，splitter 被录制流卡住，
  reader 阻塞在 `swap`，频谱与解调一起停；该阻塞不触发 `read_failed`（生产者卡在 swap，不再进入读循环）。
- 影响：界面整体冻结、硬件 FIFO 溢出导致样本静默丢失；录音文件里没有任何 gap 标记。
- 成本：中（录制写盘移出热路径/加有界队列 + 丢样计数与状态栏告警）。
- 产品决定：否（但「慢于实时时是丢样还是停录」需定口径）。

### R-08（P2）磁盘满的中止路径没有接入已有的故障帮助

- 位置：`desktop/error_remedy.py:179-183`（`classify_record_dir_error` 已能把 ENOSPC 归为 `disk_full`）、
  `desktop/troubleshooting.py:136`（已有「磁盘满」帮助条目）；`desktop/main_window.py:9417-9428` 未调用分类器。
- 现象：开录前的目录错误会带帮助链接；录到一半的磁盘满只显示英文 `[Errno 28] No space left on device`。
- 影响：同一故障两种体验，最需要帮助的时刻反而没有帮助。
- 成本：极低（abort 时按分类码走 `_show_error_dialog`）。
- 产品决定：否。

### R-09（P2）录音回调的异常在 RecordTap 里被静默吞掉

- 位置：`desktop/receive_pipeline.py:561-564`（`try: self._on_iq(iq) except Exception: pass`）。
- 现象：若收口逻辑本身抛异常（含跨线程 UI 异常），录制会「继续开着但不再写任何样本」，没有任何提示。
- 影响：与 S7 要消灭的「假成功」同类，只是触发条件更隐蔽。
- 成本：极低（失败即停录并发信号上报）。
- 产品决定：否。

### R-10（P3）掉设备时录音完成提示被断开提示覆盖

- 位置：`desktop/main_window.py:6804-6809`（断开前落盘）→ `8508-8523`（随后写「设备已断开…」）。
- 现象：录音文件已保存且 sidecar 已写，但用户只看到「设备已断开」，不知道录音保存到了哪里。
- 影响：用户可能以为录音丢了，去重复录制。
- 成本：极低（断开提示里拼上录音落盘路径）。
- 产品决定：否。

## 3. 元数据、命名与格式

### R-11（P1）录音中调谐后 sidecar 的中心频率不更新，也没有变更点

- 位置：`desktop/main_window.py:9407`（`_record_center_hz` 只在 `_open_record_segment` 开段时赋值）、
  `9430-9448`（`_record_segment_meta` 直接读它）；`7123-7150`（`_on_tune_sdr` 只更新
  `_backend_center_hz`，不碰录制态）。
- 现象：实测 G：后端已到 105 MHz，sidecar 仍写 `center_freq_hz=100000000.0`，且无
  `frequency_changes`/`tuning` 类字段。
- 影响：**整份文件**（包括调谐前录的部分）在回放时都会按旧中心频率标注；调谐后的内容绝对频率错一个 Δ。
  对射电/卫星留档是数据错误，不只是显示问题。
- 成本：中（调谐即切段，或写 `center_freq_changes` 时间线；回放按段取中心频率）。
- 产品决定：是（切段 vs 变更点二选一，取决于是否要求单文件连续）。

### R-12（P2）sidecar 只在停止时写，崩溃/断电后只剩裸 `.iq`

- 位置：`desktop/main_window.py:9450-9499`（`_finalize_record_segment` 关闭时才 `write_sidecar`，见 `9473-9482`）。
- 现象：录制中进程被杀 → `.iq` 在、`.json` 不在；再次回放时 `baseband_io` 按 2.4 MS/s 假定速率
  （`mbdsdr_ai/baseband_io.py:439-448`）计算时长与频率轴。
- 影响：崩溃后的录音无法确定真实速率/中心频率，只能人工猜；时长显示不可信。
- 成本：中（开录即写一份最小 sidecar，录制中周期性更新 samples）。
- 产品决定：否。

### R-13（P2）桌面录音是自定义 `.iq+.json`，不是 SigMF，跨工具/跨功能不认

- 位置：`desktop/main_window.py:9430-9448`（自定义字段）；`mbdsdr_ai/recorder.py:27-32` 明确「两套并存」；
  `desktop/satellite_image_panel.py:247-264`（LRPT 解码只认 `.sigmf-meta`，否则要求手填速率）。
- 现象：桌面录的 `.iq` 拖进卫星 LRPT 解码，采样率不会被自动读取；SigDigger/Inspectrum 也不能直接打开。
- 影响：同一产品内两套格式，用户要手工转换或手填参数；科研/复盘场景摩擦大。
- 成本：中（收尾时额外写一份 SigMF meta，或改用 `IQRecorder`）。
- 产品决定：是（是否保留现有 `.iq+.json` 兼容旧文件）。

### R-14（P2）没有格式/位深选择，固定 cf32

- 位置：`desktop/main_window.py:9444`（`"format": "cf32"`）。
- 现象：2.4 MS/s 下约 19.2 MB/s；RTL 原生 cu8 只需 1/4 空间，桌面没有选项。
- 影响：移动硬盘/U 盘用户很快写满；长时守听不可行。
- 成本：中（写盘层支持 cu8/cs16 + sidecar 标注）。
- 产品决定：是（默认格式与是否保留 cf32）。

### R-15（P2）没有「录制解调后的音频（WAV）」入口

- 位置：`mbdsdr_ai/recorder.py:398`（`AudioRecorder` 已存在，48 kHz/16 bit WAV）；`desktop` 全仓无引用。
- 现象：工具栏「录音」录的是 baseband IQ，不是用户听到的声音；按钮文案也没有区分。
- 影响：想留一段通联/广播的用户拿到的是几十 GB 的 IQ，且不知道该怎么听。
- 成本：中（接线 AudioRecorder + 两个入口：录音频 / 录 IQ）。
- 产品决定：是（是否需要音频录音，以及默认录哪一种）。

### R-16（P3）文件名/目录规则：平铺、无频点、无标签、无会话目录

- 位置：`desktop/main_window.py:9622-9624`（`baseband_%Y%m%d_%H%M%S.iq`，本地时间）、
  `205-210`（`_part<N>`）、`213-231`（同秒冲突静默 `_2`）。
- 现象：文件名不含频率/带宽/设备；一次会话的多个 `_partN` 与历史会话全部平铺在同一目录；
  同秒重名静默改名，只在状态栏提一句。
- 影响：录音多了以后无法按频点/卫星/事件检索，只能逐个打开听。
- 成本：低-中（文件名加频点/模式；可选按会话建子目录）。
- 产品决定：是（命名规范一旦上线就难改）。

## 4. 回放

### R-17（P1）回放上限 24,000,000 样本：2.4 MS/s 下只能播前 10 秒

- 位置：`desktop/main_window.py:43`（`REPLAY_MAX_SAMPLES = 24_000_000`）、`11317-11319`
  （`load_iq(max_samples=REPLAY_MAX_SAMPLES)`）、`11375-11387`（超出弹窗「本次只载入前 …」）。
- 现象：2.4 MS/s 的 1 分钟录音 = 1.44 亿样本，只能播前 10 s；1.2 MS/s 也只有 20 s。
  弹窗明说「完整回放的分块流式播放在后续版本提供」。
- 影响：录得越长越播不了；录音功能的价值被回放上限直接削掉，属于核心可用性缺口。
- 成本：中-高（分块流式播放 + 环形缓冲；需处理解调实时性）。
- 产品决定：是（是否把流式回放列为下一版必做）。

### R-18（P1）回放没有进度条/时间轴/跳转/暂停/倍速/循环

- 位置：`desktop/main_window.py:11455-11493`（`_replay_tick` 只累加 `_replay_pos`）；工具栏只有一个
  「回放」按钮（`1727-1732`），播放中不换文案、不换 tooltip，二次点击才是停止。
- 现象：进度只写在状态栏（`回放中: 0.1/0.1s`），任何其他状态栏消息都会盖掉；不能定位到某个时刻，
  不能暂停，不能变速，不能循环。
- 影响：无法复盘一次通联/一次卫星过境的关键几秒；只能从头再播。
- 成本：中（进度条 + 拖动 seek + 暂停/倍速；需要按样本定位与重建解调状态）。
- 产品决定：是（是否需要 A/B 循环、频谱-时间联动等增强）。

### R-19（P2）多段录音只能播当前文件的一段，没有「整场播放/选段」

- 位置：`desktop/main_window.py:11394-11404`（只提示「本次回放第 N 段」）；
  `mbdsdr_ai/baseband_io.py:506-520`（sidecar 已带 `segments` 清单与总时长）。
- 现象：录制中改过采样率会切成 `_part2/_part3`；回放选哪个文件就只播哪个，段与段之间不连续。
- 影响：一次会话被拆成多个文件后，用户要自己记住顺序逐个打开；跨段的频谱/时间不连续。
- 成本：中（按 segments 清单串播；速率变化处重建流水线）。
- 产品决定：是（是否把多段视为一次「会话」）。

### R-20（P2）回放忽略「速率是假定的」这一事实

- 位置：`mbdsdr_ai/baseband_io.py:439-448`（sidecar 缺失时 `sample_rate_assumed=True`）、
  `476-477`（结果里带回 `metadata_missing`）；`desktop` 全仓无这两个字段的消费点。
- 现象：没有 sidecar 的 `.iq`（含崩溃残留、外部工具生成）按 2.4 MS/s 播放，时长/频率轴都按假定值显示，
  界面没有任何「假定」标记。
- 影响：与项目「不拿假定值冒充实测值」的既定口径冲突；用户会对时长/带宽得出错误结论。
- 成本：低（状态栏/播放头标注「速率未知，按 2.4 MS/s 假定」）。
- 产品决定：否。

### R-21（P2）回放解调/频谱异常被静默吞掉

- 位置：`desktop/main_window.py:11470-11478`（`except Exception: pass` 包住 `spectrum.update_iq` 与
  `_demod_and_play`）。
- 现象：某块解调失败时既不出声也不报错，进度照走，用户以为「这段本来就没声音」。
- 影响：与静默吞噬治理规则冲突；回放的可信度下降。
- 成本：极低（失败计数 + 状态栏提示一次）。
- 产品决定：否。

### R-22（P2）录音与回放/实时接收互斥，且切换提示会被覆盖

- 位置：`desktop/main_window.py:11406-11419`（先 `_apply_recording(False)`，再 `_stop_iq_streams()`；
  「已停止当前录音」写在 `11414-11415`，随即被 `11446-11448` 的回放进度覆盖）。
- 现象：实测 E：开始回放后录音被停，但用户只看到「回放中」；录音按钮仍是选中态（同 R-01）。
- 影响：用户不知道录音已结束、文件在哪；也无法一边回放一边继续录制实时信号。
- 成本：中（若允许并行需独立缓冲；若保持互斥，至少要有持久提示 + 按钮复位）。
- 产品决定：是（是否需要「回放时仍可录音」）。

### R-22B（P3）回放不展示 sidecar 元数据（设备/增益/开始时间/是否缺 sidecar）

- 位置：`desktop/main_window.py:11391-11394`（只消费 `sample_rate`/`center_freq_hz`/`segments`）；
  `mbdsdr_ai/baseband_io.py:478-484` 已返回 `center_freq_known`、`gain_known`、`gain_db`、
  `device`、`driver`、`start_time`，桌面无消费点。
- 现象：回放时只能从频谱轴看出中心频率；录音用的设备、增益、开始时间、sidecar 是否齐全都看不到。
- 影响：用户无法判断一份录音的来源与可信度，也无法区分「实测元数据」与「默认值」。
- 成本：低（回放状态区加一行元数据 + 缺字段标「未知」）。
- 产品决定：否。

## 5. 文件管理

### R-23（P1）没有录音列表、选择、删除、改名；只有「打开系统文件夹」

- 位置：`desktop/settings_panel.py:1432-1448`（唯一入口是 `QDesktopServices` 打开目录）；
  `desktop/error_remedy.py:69` 明确「录音库…该模块无桌面调用方，code 不进入 UI」；
  `mbdsdr_ai/recording_library.py` 已实现 `list_entries`（672）、`update_entry`（802）、
  `reconcile`（1013）、`apply_retention`（1240）、`export_to`（1461）。
- 现象：应用内看不到录过什么；删除/改名/移动/清理全靠在资源管理器里手工操作。
- 影响：录音功能只有「产生文件」一半，没有「管理文件」；用户不敢多录，录了也找不到。
- 成本：中（接一个列表面板：列表/试听/删除到回收站/改名/按会话分组）。
- 产品决定：是（列表放左栏还是独立页签、删除是否进回收站）。

### R-24（P2）没有容量上限/保留策略，长期录制只能人工清理

- 位置：`mbdsdr_ai/recording_library.py:197`（`RetentionPolicy`）、`1240`（`apply_retention`）；桌面无调用。
- 现象：没有任何「最多占 X GB / 保留 N 天」的设置项，也没有启动时提醒。
- 影响：磁盘被录音悄悄占满，进而触发 R-06 的 ENOSPC；用户不知道是谁占的。
- 成本：中（接线保留策略 + 设置项 + 删除前的可见确认）。
- 产品决定：是（默认策略与是否自动删除）。

### R-25（P2）录音/回放的测试覆盖只覆盖「能跑通」，不覆盖「失败时是否说真话」

- 位置：`tests/test_record_streaming.py`、`test_record_dir_safety.py`、`test_replay_load_async.py`、
  `test_recorder_overwrite_guard.py`（27 passed）；`scripts/hw_acceptance/ring4_recording_playback.py:46-55`
  走的是 controller 的 SigMF 录制，不经过桌面 `_start_recording`/`.iq` 路径。
- 现象：没有一条用例断言「写失败后按钮复位/面板不得出现完成态/错误文案不得含『录制完成』」；
  也没有 ENOSPC、慢盘、掉设备、调谐中途的自动化用例。
- 影响：R-01/R-02/R-03/R-04/R-11 这类事故可以长期存在而 CI 全绿；R4 真机 PASS 也不能证明桌面录音可用。
- 成本：低-中（5-6 条行为用例，含反证）。
- 产品决定：否。

## 6. 已满足项（无需改，避免误报）

- **录音与解码并行**：`RecordTap`（`desktop/receive_pipeline.py:537-565`）与解调 worker 独立；
  「停止解调 / 不收听」（`desktop/main_window.py:4239-4310`）不会停录制。
- **取消/目录不可用路径本身是诚实的**：按钮复位 + 状态栏点名目录 + 给出「去设置改目录」的下一步
  （`desktop/main_window.py:9560-9621`）；问题只在面板的假完成（R-02）。
- **同名不覆盖**：`_unique_record_path`（`desktop/main_window.py:213-231`）避免同秒重名截断旧录音。
- **回放读盘在后台线程**、可取消、有进度框（`desktop/main_window.py:11287-11334`）。
- **改采样率会切段**，不会把两种速率混进同一个文件（`desktop/main_window.py:7392-7492`）。

## 7. 已存在但未接线 / 重复实现

| 能力 | 内核位置 | 桌面状态 |
|---|---|---|
| 录音库（列表/标签/对账/保留/导出/迁移） | `mbdsdr_ai/recording_library.py:351-1750` | 未接线（`error_remedy.py:69` 明示） |
| SigMF 流式录制 | `mbdsdr_ai/recorder.py:83`（`IQRecorder`） | 未使用；桌面自写 `.iq+.json` |
| WAV 音频录制（48k/16bit） | `mbdsdr_ai/recorder.py:398`（`AudioRecorder`） | 未使用；无 UI 入口 |
| 磁盘满帮助条目 | `desktop/troubleshooting.py:136` | 开录前可用，录中途不可用（R-08） |
| 回放分块流式播放 | 无 | `REPLAY_MAX_SAMPLES` 截断 + 弹窗承诺「后续版本」 |

## 8. 测试与真机验收口径

- 本次只读复跑：4 个录音/回放测试文件 **27 passed**；关闭前录音确认 **3 passed**。
- R4 真机验收结果（`.tmp-tool-results/hw_acceptance/20261110_051336/r4_recording_playback/result.json`）
  覆盖的是 `controller.start_recording`（SigMF、`.sigmf-data/.sigmf-meta`），**不覆盖桌面 UI 的
  `.iq + .json` 录制与回放**；因此不能用它的 PASS 推断桌面录音可用。
- 缺口：没有针对「失败路径文案/按钮态」的断言，也没有 ENOSPC/慢盘/掉设备/调谐中途的用例。

## 9. 严重度排序表

| # | 严重度 | 缺口 | 位置 | 用户可感知后果 | 成本 | 需产品决定 |
|---|---|---|---|---|---|---|
| 1 | P1（**已实测**） | 内部停止后按钮不复位，UI 仍显示录音态 | main_window.py:9706-9784、11412-11415 | 以为还在录，实际早停 | 低 | 否 |
| 2 | P1（**已实测**） | 取消/目录失败/写失败都留下「录音完成 (N 秒)」；开始失败弹窗还弹两次 | control_panel.py:1248-1256；main_window.py:9560-9621、8219-8245 | 面板常驻假完成；取消要点两次 | 低 | 否 |
| 3 | P1（**已实测**） | 录音中调谐后 sidecar 中心频率不更新 | main_window.py:9407、9436、7123-7150 | 回放绝对频率整份错位 | 中 | **是** |
| 4 | P1（**已实测**） | 回放上限 24M 样本 = 2.4 MS/s 下 10 s | main_window.py:43、11317-11319 | 长录音只能播开头 10 秒 | 中-高 | **是** |
| 5 | P1 | 回放无进度条/跳转/暂停/倍速/循环 | main_window.py:11455-11493、1727-1732 | 无法复盘关键几秒 | 中 | **是** |
| 6 | P1 | 无录音列表/删除/改名，只有打开文件夹 | settings_panel.py:1432-1448；recording_library 未接线 | 录了找不到、不敢多录 | 中 | **是** |
| 7 | P1 | 写失败收口在 RecordTap 线程操作控件/QTimer | main_window.py:8810-8844；receive_pipeline.py:551-565 | 真故障时可能崩溃/卡中间态 | 低 | 否 |
| 8 | P2（**已实测**） | 中止文案「错误；录制完成」自相矛盾 | main_window.py:9782-9784 | 用户当成成功 | 极低 | 否 |
| 9 | P2（**已实测**） | 无 flush/fsync，录制中文件显示 0 字节 | main_window.py:8822-8830 | 以为没在录；崩溃丢缓冲 | 低 | 否 |
| 10 | P2 | 无剩余空间预检/实时大小/剩余时长 | settings.py:566-580；main_window.py:9763-9784 | 录到一半才 ENOSPC | 低-中 | 否 |
| 11 | P2 | 慢盘阻塞 splitter，全链路停摆无告警 | dsp.py:1278-1300；dsp_stream.py:67-95 | 界面冻结、静默丢样 | 中 | 否 |
| 12 | P2 | 磁盘满中止未接入已有帮助分类 | main_window.py:9417-9428；error_remedy.py:179-183 | 最需要帮助时只有英文 errno | 极低 | 否 |
| 13 | P2 | sidecar 只在停止时写，崩溃后裸 `.iq` | main_window.py:9450-9499 | 速率/中心频率只能猜 | 中 | 否 |
| 14 | P2 | 非 SigMF，LRPT/第三方工具不识别 | main_window.py:9430-9448；satellite_image_panel.py:247-264 | 要手填速率/手工转换 | 中 | **是** |
| 15 | P2 | 固定 cf32，无位深选择（≈69 GB/h） | main_window.py:9444 | U 盘/移动盘很快写满 | 中 | **是** |
| 16 | P2 | 无 WAV 音频录音入口（内核已有） | recorder.py:398；desktop 无引用 | 想留声音却拿到几十 GB IQ | 中 | **是** |
| 17 | P2 | 多段录音只能播一段，无整场/选段 | main_window.py:11394-11404 | 会话被拆散后无法连续复盘 | 中 | **是** |
| 18 | P2 | 回放忽略「速率是假定值」，无标记 | baseband_io.py:439-448；desktop 无消费点 | 时长/带宽结论错误 | 低 | 否 |
| 19 | P2 | 回放解调异常 `except: pass` 静默吞 | main_window.py:11470-11478 | 以为「本来没声音」 | 极低 | 否 |
| 20 | P2 | 失败态/ENOSPC/慢盘/掉设备无自动化用例 | tests/；ring4_recording_playback.py:46-55 | 事故可长期存在而 CI 全绿 | 低-中 | 否 |

补充 P3（未入表）：掉设备完成提示被覆盖（`main_window.py:6804-6809`、`8508-8523`）；
录音回调异常被 `receive_pipeline.py:561-564` 静默吞掉；命名/平铺/无标签（`main_window.py:9622-9624`、`205-231`）；
时长用 1 s 计时器 tick 而非样本（`main_window.py:8047-8050`、`9763-9784`）；工具栏「停止中」文案
（`main_window.py:9648-9652`）；回放不展示 sidecar 元数据（R-22B）；
`settings.record_dir` docstring 仍写「程序目录同级」（`settings.py:561` vs `124-131`）。

## 10. 复跑

```powershell
# 1) 现有测试
python -m pytest tests/test_record_streaming.py tests/test_record_dir_safety.py tests/test_replay_load_async.py tests/test_recorder_overwrite_guard.py -q
python -m pytest tests/test_close_recording_guard.py -q

# 2) 失败路径 UI 探针（离屏、隔离 profile，不碰真实用户配置）
python D:\mbdsdr\output\tmp\probe_rec\probe_ui_truth.py
```

探针把 `HOME/USERPROFILE/APPDATA/LOCALAPPDATA` 指到 `D:\mbdsdr\output\tmp\probe_rec\profile`，
用 stub 后端（2.4 MS/s / 100 MHz）驱动真 `MainWindow`；ENOSPC 为**注入式**（第 2 次 `write` 抛
`OSError(28)`），真实磁盘满更可能在缓冲 flush/close 时暴露，对应 R-05 的分析。

> 复核记录：2026-11-11 20:52 在当前工作树（`desktop/main_window.py` 含其他工作线的未提交改动）
> 重跑本探针，A~G 全部结论与上表一致：B/C 弹窗仍各弹两次且面板「录音完成 (0 秒)」；D1 仍
> 「按钮选中 + 错误后接录制完成」；E1 回放启动后录音按钮仍选中；F 仍为「未录音」且按钮选中；
> G 仍为 `backend 105 MHz` / `sidecar 100 MHz`。行号已按本次工作树校正；若其他线继续改动
> 该文件，请以符号名（`_stop_recording`、`_start_recording` 等）二次核对。

## 11. W35 / A9 收尾核验（2026-11-11）

对象：`bf06e9a4`（按钮复位）、`f81f1301`（三态文案 + 失败回写面板）、`c2ce967a`（单入口防双弹窗）、
`bc199e01`（中止文案只说中止）。工作区 `desktop/main_window.py` 当时由其他线持有，故旧代码
一律用**只读 `git worktree` 检出**取证，未改被测文件；核验后 worktree 已全部删除。

### 11.1 三件套全绿（当前 HEAD）

```powershell
python -m pytest tests/test_record_failure_copy.py tests/test_record_stop_releases_button.py tests/test_record_single_entry.py -q
# 10 passed, 1 warning in 70.52s（Qt 锁等待 10.6s）
```

### 11.2 逐路径反证（旧代码必须红）

**worktree @ `bf06e9a4`（文案与单入口均未修）→ 5 failed, 5 passed**

| 用例 | 旧代码断言原文 |
|---|---|
| `test_dir_unavailable_is_failure_with_reason` | `AssertionError: assert '录音失败' in '未连接'` |
| `test_user_cancel_is_cancelled_not_completed` | `AssertionError: assert '已取消' in '未连接'` |
| `test_enospc_is_failure_with_reason` | `AssertionError: 状态栏仍是完成态: '写入录音文件失败: [Errno 28] No space left on device（已写 8192 字节…）；录制完成: … (1024 样本, 8.0 KB, 0s)'` |
| `test_write_failure_releases_button` | 同一条状态栏完成态断言 |
| `test_toolbar_click_failure_starts_and_dialogs_once` | `AssertionError: _start_recording 执行了 2 次  assert 2 == 1` |

**worktree @ `bf06e9a4^`（按钮复位未修）→ 3 failed**

写失败 / 回放启动 / 掉线三条内部停止路径全部命中
`AssertionError: 工具栏录音按钮仍处于按下态  assert True is False`。

### 11.3 结论

三条失败路径（目录不可用、用户取消、ENOSPC）、内部停止按钮复位（三条路径）、单入口防双弹窗，
均有「旧代码红 / 新代码绿」成对记录，且断言覆盖面板与状态栏两侧。W35 主体可判**完成**，
A9 核验项关闭；残留仅 R-10（掉设备提示覆盖录音落盘提示，P3）与 sidecar 中心频率（W36，待产品决定）。
