# SDR++：停止解调 ≠ 断开设备（机制实读，2026-11-16）

> 用户要求："停止解调时保持连接但不再解调——去学 SDR++，别口头答应。"
> 本文是从 `d:\mbdsdr\reference\sdrpp` 源码读出来的机制，以及与本仓的逐条映射。
> GPL-3.0：**只学机制，不抄代码**。

## 1. SDR++ 的线程/对象拓扑

```
SourceManager (SDR 设备)          ← 采样源，唯一
   └── IQFrontEnd                 ← 中心频率搬移 + 分发给所有 VFO
          ├── VFO A stream ──► Demod 模块实例（可 enable/disable）
          ├── VFO B stream ──► Demod 模块实例
          └── (Waterfall 直接从 IQFrontEnd 取全带宽 FFT)
```

关键点：**频谱/瀑布属于 IQFrontEnd，不属于任何 VFO**。所以 VFO 停掉，瀑布照常滚。

## 2. 证据（文件:行）

| 机制 | 位置 | 说明 |
| --- | --- | --- |
| VFO 创建 = 建 DDC 子树 | `core/src/signal_path/iq_frontend.cpp:140-159` | `addVFO()` new 一个 `RxVFO`，`bindIQStream()` 后 `vfo->start()` |
| VFO 销毁 = 停 DSP 并摘流 | `iq_frontend.cpp:162-176` | `removeVFO()` → `vfo->stop()` 并解绑，**设备不动** |
| VFO 对象析构 | `core/src/signal_path/vfo_manager.cpp:20-28` | `dspVFO->stop(); iqFrontEnd.removeVFO(name)` |
| 瀑布标记只是 UI | `vfo_manager.cpp:9-17` | `ImGui::WaterfallVFO` 只画带宽/偏移，不参与 DSP |
| VFO 带宽决定是否滤波 | `core/src/dsp/channel/rx_vfo.h:24,60-70` | `filterNeeded = (bandwidth != outSamplerate)`；带宽=输出率时省掉 FIR |
| 改带宽/偏移不重建整链 | `rx_vfo.h:45-77` | `tempStop()/tempStart()` 只停本 VFO 的 processor |
| 解调模块可单独开关 | `core/src/module.cpp:126-150` | `enableInstance/disableInstance` → `instance->enable()/disable()` |
| 全局播放开关 | `core/src/gui/main_window.cpp:676-691` | `setPlayState(false)` 停的是 **sourceManager**（设备），不是单个解调 |

由此得到 SDR++ 的两种"停"：

- **停解调**：销毁/禁用该 VFO 的解调链（DDC 滤波器 + demod + sink），设备与 FFT 不动；
- **停设备**：`setPlayState(false)`，source 停止，频谱也停。

本仓的"⏸ 停止解调"对应前者；"断开"对应后者。两者不可混为一谈。

## 3. 本仓映射（desktop/receive_pipeline.py）

| SDR++ 概念 | 本仓实现 |
| --- | --- |
| IQFrontEnd 分发 | `IQSplitter` → `_spectrum_stream` / `_vfo1_stream` / `_vfo2_stream` / `_record_stream` |
| RxVFO（DDC+重采样+LPF） | `mbdsdr_ai.dsp.VFO.process()`，在 `DemodWorker.run()` 内 |
| Demod 模块 enable/disable | `DemodWorker.set_audio_enabled(on)` |
| 解调但不出声 | `DemodWorker.set_muted(True)`（对应"不收听"） |
| 瀑布独立 | `_fft` 线程只吃 `_spectrum_stream` |

门控点（`receive_pipeline.py:462-503`）：

```python
def _player_ok() -> bool:
    return bool(player is not None
                and getattr(player, "available", False)
                and self._audio_enabled)

while self._running:
    buf, n = self._in.read()
    if not _player_ok():
        self._in.flush()      # 连 asarray / DDC / 解调都不做
        continue
```

实测（`vfo.process` 调用计数，3 块 IQ）：

```
audio_enabled=False → VFO.process 调用 0 次
audio_enabled=True  → VFO.process 调用 3 次
```

即"停止解调"在 worker 里就已经停住，不是只把声卡静音。

## 4. 三档语义（与本仓 UI 对齐）

| 档位 | 设备 | 频谱/瀑布 | DDC+解调 | 声卡 | 用途 |
| --- | --- | --- | --- | --- | --- |
| ▶ 收听 | 连接 | 跑 | 跑 | 出声 | 正常收听 |
| ⏸ 停止解调 | 连接 | 跑 | **停** | 关 | 省 CPU，看频谱 |
| 🔇 不收听 | 连接 | 跑 | 跑 | 静音 | 解码/录制/星座图要数据 |

## 5. 判定"真停"的机械口径

1. `DemodWorker._audio_enabled == False`；
2. `VFO.process()` 每帧调用次数 = 0（不是"结果被丢弃"）；
3. `_fft` 线程仍在出帧（频谱必须活着，否则就是"停设备"而不是"停解调"）；
4. 声卡流已 stop，缓冲不再增长。

任何一条不满足，就不许在状态栏写"已停止解调"。

## 6. 不要照抄的部分

- 不引入 SDR++ 的模块动态库体系（本仓是单进程 Python）；
- 不复制 `RxVFO` 的 C++ 实现（GPL-3.0），本仓用 numpy/DSP 包；
- 不把 VFO 析构当作"停止解调"——本仓 VFO 是 UI 对象，DDC 在 worker 内，
  停的是 worker 的解调分支，保留 VFO 对象以便随时恢复。
