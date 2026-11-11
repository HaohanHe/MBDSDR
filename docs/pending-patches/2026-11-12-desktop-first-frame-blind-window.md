# 补丁计划：首帧之前健康轮询完全失明（幽灵「已连接」）

> owner：**UI 操作逻辑线**（`desktop/main_window.py` 在其手里；本线只出计划，未改产品代码）
> 发现：2026-11-12 幽灵「已连接」审计（HEAD `463a83be`）
> 关联：`docs/network-layer.md` §10

## 1. 结论

| 项 | 内容 |
|---|---|
| 缺陷 | 连接建立后**一帧都没收到**时，`_on_health_tick` 在宽限期判断之前就 `return`，此后每秒空转；后端即便已报 `disconnected` 也不会被接住 |
| 性质 | **产品缺陷**（UI 生命周期的兜底逻辑有死区），影响本地 RTL 的“句柄在、读不返回”形态 |
| 位置 | [desktop/main_window.py:8795-8799](file:///d:/mbdsdr/MBDSDR/desktop/main_window.py#L8795-L8799) |
| 影响面 | 本地 RTL（libusb 同步读可永久阻塞）；rtl_tcp / SpyServer 因 socket 有超时、`read_failed` 会在 1s 后触发，不受此盲窗影响 |
| 严重度 | P2（不算常见触发，但后果是无上限的假「已连接」；与 AC-06.7「断源 2s 内可见」矛盾） |

## 2. 代码事实

```python
# desktop/main_window.py:8784-8799（现状）
def _on_health_tick(self) -> None:
    backend = self._active_sdr_backend
    if backend is None or self._pipeline is None:
        return
    if not self._health_has_data:
        # 本段连接还没出过一帧：空窗由 read_failed + 宽限期负责
        return                      # ← 死区：宽限期永远检查不到
    if time.monotonic() < self._health_grace_until:
        return
    ...
```

- `_health_has_data` 只在首帧回调里置 True（`:8989`）。
- `HEALTH_GRACE_S = 3.0`（`:59`）的设计意图正是“首帧前只宽限 3 秒”，但上一行的
  早退让它成为**不可达代码**（该状态下）。
- 另一条兜底 `read_failed`（`desktop/receive_pipeline.py:152-167`）依赖
  `read_fn` **返回** None/抛异常；本地 RTL 的 `read_samples` 走
  `rtlsdr_read_sync`（libusb 同步传输，无超时），句柄在但不出数时可以永不返回。

## 3. 可复现步骤（真实产品方法，无需硬件）

最小 harness 只提供早退门之前会碰的属性；状态读取仍调用产品实现
`MainWindow._backend_status_facts`，不复制判据：

```python
import os, sys, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, r"d:\mbdsdr\MBDSDR")
sys.path.insert(0, r"d:\mbdsdr\MBDSDR\desktop")
from mbdsdr_ai.sdr_backend import SDRStatus

class Backend:
    def __init__(self, link, connected, age):
        self.status = SDRStatus(connected=connected)
        self.status.link_state = link
        self.status.last_data_age_s = age
    def get_status(self):
        return self.status

class Harness:
    def __init__(self, has_data, backend):
        self._active_sdr_backend = backend
        self._pipeline = object()
        self._health_has_data = has_data
        self._health_grace_until = time.monotonic() - 100.0  # 宽限早已过期
        self.calls = []
    def _backend_status_facts(self, backend):
        from main_window import MainWindow
        return MainWindow._backend_status_facts(self, backend)
    def _handle_backend_lost(self, msg, code=""):
        self.calls.append(("backend_lost", msg, code))
    def _show_link_degraded(self, age, error, code):
        self.calls.append(("degraded", age, code))
    def _restore_link_display(self):
        self.calls.append(("restore",))
    def _warn_buffer_overflow(self):
        self.calls.append(("buffer",))

from main_window import MainWindow

def tick(has_data, link, connected, age):
    h = Harness(has_data, Backend(link, connected, age))
    MainWindow._on_health_tick(h)
    print(has_data, link, connected, age, "->", h.calls or "（空：直接 return）")

tick(False, "connected",    True,  None)   # 场景 1
tick(True,  "connected",    True,  30.0)   # 场景 2
tick(True,  "stale",        True,  6.0)    # 场景 3
tick(False, "disconnected", False, None)   # 场景 4
```

实测输出（2026-11-12，HEAD `463a83be`）：

| 场景 | `_health_has_data` | status | 调用记录 |
|---|---|---|---|
| 1 | False | `connected` / `age=None` | **（空）直接 return** |
| 2 | True | `connected` / `age=30s` | `[('degraded', 30.0, '')]` |
| 3 | True | `stale` / `age=6s` | `[('degraded', 6.0, '')]` |
| 4 | False | **`disconnected`** | **（空）直接 return** |

场景 1 是其“什么都不做”，场景 4 是更硬的证据：后端已经说断开也不处理。

## 4. 修复方案（本线不改，交 UI 操作逻辑线）

把早退门改成“宽限期内早退，宽限期外照样读事实”：

```python
if not self._health_has_data:
    now = time.monotonic()
    if now < self._health_grace_until:
        return
    error, connected, age, link, code, _buf = self._backend_status_facts(backend)
    if link in ("failed", "disconnected") or not connected:
        self._handle_backend_lost(
            error or "后端报告未连接（首帧之前）", code)
        return
    waited = (now - self._health_grace_until) + HEALTH_GRACE_S  # 连接至今
    if waited >= DATA_LOST_S:
        self._handle_backend_lost(
            f"连接后 {waited:.1f}s 仍未收到首个 IQ 帧（数据流中断）", code)
        return
    self._show_link_degraded(waited, error or "尚未收到首帧", code)
    return
```

要点：

- 前 3s 仍不判（不把慢启动误报成掉线）；
- 3s 后先降级可见（`_show_link_degraded` 对 `age=None` 有兜底文案“超过阈值”，
  这里传 `waited` 保证有具体秒数）；
- 到 10s（与 `DATA_LOST_S` 对齐）硬判断开，保持与既有阶梯一致；
- 不改任何阈值，不新增定时器。

## 5. 验收与反证

1. **场景 1 转绿**：`has_data=False`、宽限过期、后端 `connected` → 调用记录必须是
   `degraded`，UI 主状态位不得再显示设备名。
2. **场景 4 转绿**：`has_data=False`、后端 `disconnected` → 必须 `backend_lost`。
3. **10s 硬判**：`waited>=10` → 必须 `backend_lost`，文案含“仍未收到首个 IQ 帧”。
4. **反证**：把修复撤掉（恢复早退）→ 上述三条必须回红；不能只跑 1 条。
5. **不回退**：真机首帧慢于 3s 但随后出数时，不得误报断开；`_restore_link_display`
   在首帧到达后仍要还原文案（`_on_pipeline_frame_ready` 已有 `_health_has_data=True`）。
6. **回归**：`pytest tests/test_network_layer_matrix.py::TestRtlTcpMatrix
   tests/test_network_layer_matrix.py::TestSpyServerMatrix -q` 仍为 `8 passed`；
   `pytest tests/test_reconnection.py tests/test_reconnection_e2e.py
   tests/test_reconnect_zero_freq.py -q` 仍为 `18 passed`。

## 6. 附带说明

- 网络来源不受影响：socket 超时会让 `read_fn` 返回 `None`，`read_failed` 在 1s
  后发出、3 次后上层断开，这条路径与本次补丁无关，但补丁后它们会更快进入
  “降级”可见态（同文案，不新增文案）。
- 本次审计不改 `desktop/main_window.py`；计划落地后请把 `docs/network-layer.md`
  §10.2 中“是（盲窗）”一行改为“已修复 + commit”，并保留本节作为历史证据。
