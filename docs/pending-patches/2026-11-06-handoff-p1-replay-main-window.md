# 对接说明：回放读盘移到后台线程（P1，交给 `操作逻辑线`）

> 写给**正在改 `desktop/main_window.py` 的那条线**，不是写给我自己看的补丁存档。
> 照着这份说明直接并进你手上的那份文件即可；我不在你持有期间动它。
> 生成时间：2026-11-06；基线 commit `d1616f9c`（HEAD 已前进到 `86fdbd0b`，该提交只改
> 测试，未动 `main_window.py`，下文行号不受影响）；`main_window.py` 当时由
> `操作逻辑线` 持有（pid 42524，TTL 60 min）。

## 0. 一句话

`_replay_recording` 现在把 `baseband_io.load_iq(path)` 同步跑在 GUI 线程，
1.1 GB cf32 实测 **4.90 s**，窗口假死。后端分块流式读已经落库（内存 5.8× → 1.1×），
**但"不冻 GUI"这一半必须由你在 `main_window.py` 里落地**：把读文件挪到后台线程，
进度框 + 取消 + 24M 样本上限接上，其余逻辑原样留在 GUI 线程。

## 1. 已落库的前置（`mbdsdr_ai/baseband_io.py`，ux-basis）

```python
rec = baseband_io.load_iq(
    path,
    max_samples=24_000_000,     # 本次载入上限
    chunk_samples=1 << 20,      # 单块 1,048,576 样本
    progress=lambda done, total: ...,   # 每块回调（在**后台线程**里被调用）
    should_cancel=event.is_set,         # 每块检查；True 即提前返回
)
```

返回字典新增字段（旧字段全部保留）：

| 字段 | 含义 |
|---|---|
| `file_samples` | 文件里真实样本总数（不受上限影响） |
| `file_duration_s` | 按 sidecar 采样率换算的总时长（未知时不存在） |
| `truncated` | 是否因 `max_samples` 截断 |
| `truncation_reason` | `"max_samples"`（当前唯一原因） |
| `cancelled` | 是否被 `should_cancel` 中断 |
| `cancel_reason` | `"user_cancel"`（当前唯一原因） |
| `samples` | 本次实际载入的样本数 |

实测（`.tmp/bench_load_iq.py`，本机可用内存 1.1 GB）：

| 文件 | 旧实现（整文件 `astype(float64)`） | 新分块实现 |
|---|---|---|
| 128 MB cf32 | 峰值 0.73 GB（5.8×），1.13 s，`complex128` | 峰值 0.14 GB（1.1×），0.77 s，`complex64` |
| 1.1 GB cf32（137.5 M 样本 / 57.3 s @2.4 MHz） | 需 ≥6 GB，本机跑不起来 | **峰值 1.04 GB，4.90 s，132 次进度回调** |

记住这个 4.90 s：它是**后台化前后 GUI 是否冻结**的唯一判据。

## 2. 线程边界（先定这条，再谈代码）

| 步骤 | 现在在哪个线程 | 改后 |
|---|---|---|
| 二次点击=停止 / `_stop_tx("回放开始", wait=True)` | GUI | **GUI，不变** |
| `QFileDialog.getOpenFileName` | GUI | **GUI，不变** |
| `from mbdsdr_ai import baseband_io` | GUI | **GUI，不变**（import 很便宜且失败要弹框） |
| `baseband_io.load_iq(...)` | GUI（4.90 s） | **worker 线程（唯一搬走的东西）** |
| `np.asarray` / 段说明 / 停录 / 停流 / 声卡 / `_replay_iq` / 50 ms QTimer / 状态栏 / doppler 置灰 | GUI | **GUI，不变** |
| 所有控件读写（`spectrum`、`statusBar`、`QMessageBox`、`QTimer`） | GUI | **GUI** |

铁律：worker 里**只允许**碰 `load_iq`、`threading.Event`、两个 Signal 的 `emit`。
不许 `statusBar()`、不许 `QMessageBox`、不许读 `self._replay_*` 之外的 UI 状态。

## 3. 改动清单（按顺序并）

### P1-1 模块级常量（文件顶部常量区，`HEALTH_POLL_MS` 附近）

```python
#: 回放单次载入上限：2.4 MHz 下约 10 s、192 MB complex64。
#: 超限即截断并在界面明确告知——不静默丢样本，也不让一次回放吃掉数 GB。
REPLAY_MAX_SAMPLES = 24_000_000
```

### P1-2 类级信号（`MainWindow` 类体，现有 `_ai_tool_done` / `_tx_finished` 旁）

```python
    replay_load_progress = Signal(int)       # 0-100，worker emit
    replay_load_finished = Signal(object)    # 结果 dict（含 error/cancelled）
```

### P1-3 `__init__`（`self._replay_timer = None` 附近）

```python
        self._replay_load_thread: Optional[threading.Thread] = None
        self._replay_load_cancel = threading.Event()
        self._replay_load_path = ""
        self._replay_progress_dialog: Optional[QProgressDialog] = None
        # 显式 QueuedConnection：回调必须落在 GUI 线程
        self.replay_load_progress.connect(
            self._on_replay_load_progress, Qt.QueuedConnection)
        self.replay_load_finished.connect(
            self._on_replay_loaded, Qt.QueuedConnection)
```

已确认：`import threading`（14 行）、`QProgressDialog`（388 行 import 列表）、
`Signal`、`Qt` 均已存在，**不需要新增 import**。

### P1-4 `_replay_recording` 的切点

基线 `d1616f9c` 里 `_replay_recording` 起于 8077 行（你改完会漂移，按代码锚点搜，
别按行号）。保留它 8077-8105 的全部内容（二次点击停止、停发、文件框、import 守卫），
把下面这一段：

```python
        try:
            rec = baseband_io.load_iq(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "回放失败", f"读取文件失败：\n{e}")
            return
        ...  // 8111 `if rec.get("error")` 一直到 8178 `self._refresh_tx_button_state()`
```

整体替换成一行：

```python
        self._start_replay_load(path)
```

原 8111-8178 的正文（`error` 框 → `iq = np.asarray(...)` → 段/未知时长说明 →
停录 → `_replay_resume_poll` → `_stop_iq_streams()` → 声卡 → `spectrum.set_connected(True)`
→ `_replay_iq/_pos/_sr/_center_hz/_total/_block` → `QTimer(50 ms)` → 状态栏 →
`_refresh_tx_readout()` → doppler 置灰 → `_refresh_tx_button_state()`）**一字不改地**
搬进 `_begin_replay_from_rec(self, rec, path)`，只把 `path` 变成参数。

### P1-5 后台读盘（新方法）

```python
    def _start_replay_load(self, path: str) -> None:
        """后台读盘：worker 只读文件 + emit，控件一律留在 GUI 线程。"""
        if self._replay_load_thread is not None and self._replay_load_thread.is_alive():
            self._replay_load_cancel.set()
            self.statusBar().showMessage("正在取消读取录音…", MSG_NORMAL)
            return
        try:
            from mbdsdr_ai import baseband_io
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "回放失败", f"baseband_io 不可用：\n{e}")
            return

        self._replay_load_cancel.clear()
        self._replay_load_path = path
        dlg = QProgressDialog("正在读取录音…", "取消", 0, 100, self)
        # NonModal：主窗口保持可拖动，进度框只是提示，不夺输入焦点。
        # 不用 WindowModal/ApplicationModal——那会让人以为窗口又卡住了。
        dlg.setWindowModality(Qt.NonModal)
        dlg.setMinimumDuration(0)     # 立即显示，不等 4 秒后才冒出来
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        dlg.canceled.connect(self._replay_load_cancel.set)
        dlg.setValue(0)
        self._replay_progress_dialog = dlg

        def _work():
            try:
                rec = baseband_io.load_iq(
                    path,
                    max_samples=REPLAY_MAX_SAMPLES,
                    chunk_samples=1 << 20,
                    progress=lambda done, total: self._emit_replay_progress(
                        done, total),
                    should_cancel=self._replay_load_cancel.is_set,
                )
            except Exception as e:  # noqa: BLE001
                rec = {"error": f"{type(e).__name__}: {e}"}
            try:
                self.replay_load_finished.emit(rec)
            except RuntimeError:
                pass      # 窗口已销毁（关窗后 daemon 线程收尾）

        self._replay_load_thread = threading.Thread(
            target=_work, name="replay-load", daemon=True)
        self._replay_load_thread.start()

    def _emit_replay_progress(self, done: int, total: int) -> None:
        """worker 侧进度：只发整数百分比，失败（窗口已关）就放弃。"""
        pct = int(100 * done / max(1, total))
        try:
            self.replay_load_progress.emit(max(0, min(100, pct)))
        except RuntimeError:
            pass
```

### P1-6 回 GUI 的两个槽

```python
    def _on_replay_load_progress(self, pct: int) -> None:
        dlg = self._replay_progress_dialog
        if dlg is not None:
            dlg.setValue(max(0, min(100, int(pct))))

    def _on_replay_loaded(self, rec: dict) -> None:
        dlg = self._replay_progress_dialog
        self._replay_progress_dialog = None
        if dlg is not None:
            try:
                dlg.close()
            finally:
                dlg.deleteLater()
        self._replay_load_thread = None
        if rec.get("cancelled"):
            self.statusBar().showMessage(
                "已取消读取录音（录音与实时接收未受影响）", MSG_NORMAL)
            return
        if rec.get("error"):
            QMessageBox.warning(self, "回放失败", str(rec["error"]))
            return
        self._begin_replay_from_rec(rec, self._replay_load_path)
```

关键语义：**取消不打断录音**。原实现是"文件确认可播后才 `_apply_recording(False)`"，
这条顺序必须保留在 `_begin_replay_from_rec` 里；取消路径直接 return，录音照常。

### P1-7 截断必须说出来（`_begin_replay_from_rec` 内，`iq = np.asarray(...)` 之后）

```python
        if rec.get("truncated"):
            total = int(rec.get("file_samples") or iq.size)
            secs = rec.get("file_duration_s")
            tail = f"，约 {secs:.1f} s" if isinstance(secs, (int, float)) else "（时长未知）"
            QMessageBox.information(
                self, "回放说明",
                f"文件共 {total:,} 个样本{tail}；本次只载入前 {int(iq.size):,} 个"
                f"（上限 {REPLAY_MAX_SAMPLES:,}）。\n"
                "原因：整文件读入需要数 GB 内存。\n"
                "下一步：分段录音回放；完整回放的分块流式播放在后续版本提供。")
```

不许把截断写成"回放中 10.0/57.3 s"然后结束——那是把"只播了 17%"伪装成"播完了"。

### P1-8 二次点击 / `_stop_replay` / 关窗

`_replay_recording` **最顶部**（`if self._replay_timer is not None:` 之前）加：

```python
        if self._replay_load_thread is not None and self._replay_load_thread.is_alive():
            self._replay_load_cancel.set()
            self.statusBar().showMessage("正在取消读取录音…", MSG_NORMAL)
            return
```

`_stop_replay` 开头（`if self._replay_timer is not None:` 之前）加：

```python
        if self._replay_load_thread is not None and self._replay_load_thread.is_alive():
            self._replay_load_cancel.set()
```

关窗清理（`_cleanup...` 里遍历 `("_sky_update_timer", ..., "_replay_timer", "_repaint_timer")`
的那段，基线 9031-9040）前面加同样的两行，避免窗口销毁后 worker 还在读盘。

### P1-9 `_replay_recording` 的文档字符串

把"用 QFileDialog 选文件 → `baseband_io.load_iq()` 读回真实 IQ → QTimer …
**（读盘在后台线程，进度可见、可取消；GUI 线程只做播放器起停）**"补一句，
别让下一个人再从注释里读出"同步读盘"。

## 4. 验收

手测（用实测那个 1.1 GB cf32）：

1. 点「回放」→ 选文件 → **进度框出现时立刻拖主窗口**，窗口必须跟手；
2. 进度从 0 走到 100，中途点「取消」→ 1 秒内停在"已取消读取录音"，
   且**录音没被停掉**、实时频谱恢复；
3. 读完后回放正常起播，状态栏 0.0/xx.x s 递增；
4. 大于 24M 样本的文件 → 先弹"只载入前 24,000,000 个"的说明，再起播。

自动（落库时一并加 `tests/test_replay_load_async.py`，不要先加红用例）：

```python
def test_replay_load_does_not_block_gui(monkeypatch):
    # 1) 打桩 QFileDialog 返回固定路径（conftest 的模态守卫会直接给空路径）
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (FAKE_PATH, "")))
    # 2) 打桩 load_iq：sleep 0.6 s 再返回一个合法 rec
    def _slow(path, **kw):
        time.sleep(0.6)
        return {"iq": np.zeros(4096, np.complex64), "sample_rate": 2_400_000.0,
                "center_freq_hz": 100e6, "duration_s": 1.0,
                "file_samples": 4096, "samples": 4096}
    monkeypatch.setattr(baseband_io, "load_iq", _slow)
    mw = MainWindow(...)          # 按本仓既有 fixture 建窗
    t0 = time.monotonic()
    mw._replay_recording()
    assert time.monotonic() - t0 < 0.2, "读盘仍在 GUI 线程（>0.6 s 才返回）"
    assert mw._replay_load_thread is not None and mw._replay_load_thread.is_alive()
    # 3) 等 queued signal 落地：循环 processEvents + 短 sleep，别用 QTimer.singleShot(0)
    #    （本仓已知 offscreen 下 singleShot(0) 不触发），超时 3 s
    assert _wait_until(lambda: mw._replay_iq is not None, 3.0)
```

坑（都踩过）：offscreen 下 `QTimer.singleShot(0)` 不触发，跨线程 Signal 要靠
`processEvents()` 轮询；`QMessageBox` 静态方法会被 `tests/conftest.py` 的模态守卫
拦成"取消"，测试里涉及截断/错误框要 monkeypatch。

## 5. 反证（必须真红）

| 变异 | 期望变红的用例 | 说明 |
|---|---|---|
| 把 `_start_replay_load(path)` 改回同步 `rec = baseband_io.load_iq(path)` | `test_replay_load_does_not_block_gui` | 调用耗时 ≥ 0.6 s（阈值 0.2 s） |
| 删 `dlg.canceled.connect(self._replay_load_cancel.set)` | 取消用例（`dlg.cancel()` 后仍起播） | `_replay_iq is not None` 即红 |
| `max_samples=REPLAY_MAX_SAMPLES` 改成不传 | 截断用例（大文件必须截断并在状态栏/弹框说明） | `rec["truncated"]` 断言红 |
| `Qt.QueuedConnection` 改 `DirectConnection` | `test_replay_load_does_not_block_gui` 偶发 Qt 断言/崩溃 | 回调落到 worker 线程即红 |

反证跑完恢复原样（仓库规矩：不留 `MUTATION` 标记）。

## 6. 不在本单范围内（别顺手改）

- `_stop_tx("回放开始", wait=True)` 的等待时长（另一个问题，见 P3 相邻讨论）；
- 回放发声链 `_demod_and_play` 的性能；
- `_replay_tick` 每 50 ms 的块大小。

## 7. 追加约束：`IQPlayback` use-after-close（实测已崩过两次）

> 本单已由 `操作逻辑线` 按上文落库（`f47927b0`，3 passed）。本节是给
> **后续把回放改成"分块流式播"** 的人看的，不是可选项。

`IQPlayback.read_samples` 在 dtype 相同时返回的是 **memmap 视图**；`close()`
之后该视图悬空，再 `np.concatenate` 就是 use-after-close，进程级
`0xC0000005`（退出码 `-1073741819`）直接退出，Python 层 try/except 拦不住。

铁律：

1. **`close()` 必须在所有视图用完（拼进 ndarray / 拷贝）之后**；或者干脆走
   副本路径（`np.array(view, copy=True)` 后再关闭）。不许"先关文件再拼数组"。
2. 后台读盘 worker 里同样适用：worker 的取消/异常收尾不得提前关闭尚被引用的
   memmap。

已落库的 `baseband_io.load_iq` **不踩这个坑**：它走 `_read_iq_stream`，用
`open()` + `np.fromfile` 分块读进独立分配的 `np.empty(complex64)`，没有
memmap、没有"关闭后仍持有视图"的所有权问题（见 `mbdsdr_ai/baseband_io.py`
`_read_iq_stream`）。**不要**为了"省一次拷贝"把它换成 `IQPlayback` 视图。
2026-11-06 更新（解码流水线 P5）：`IQPlayback` 所有权语义已定稿并落库
（`cdbf3578`）——`read_samples(n, copy=False)` 为零拷贝视图，`close()` 时若仍有
未释放视图会抛 `PlaybackViewsInUseError`；`read_samples(n, copy=True)` 为独立副本。
`PlaybackSource` 已改为返回副本，`baseband_io` 不受影响。

复现（离线、不碰真机）：

```powershell
python scripts/hw_acceptance/repro_replay_demod_crash.py --mode repro
# 录音件：D:\mbdsdr\tmp\rec_play\fm_10s.sigmf-data（155 MB，不入库）
# 预期（修复后）：退出码 3 +「还有 N 个视图未被使用完」，不再出现 0xC0000005/-1073741819
python scripts/hw_acceptance/repro_replay_demod_crash.py --mode safe-oneshot
python scripts/hw_acceptance/repro_replay_demod_crash.py --mode chunked
# 后两种是正确顺序（先拷贝/分块解调，再收尾），应正常结束
```
