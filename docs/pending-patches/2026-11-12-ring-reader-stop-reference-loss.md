# 待应用补丁：`_ThreadedRingReader.stop()` join 超时后丢线程引用

- 状态：**未应用**（产品代码本轮零改动）
- 补丁：`docs/pending-patches/2026-11-12-ring-reader-stop-reference-loss.patch`
- 基准提交：`79e1d297`（2026-11-12）
- 影响文件：`mbdsdr_ai/sdr_backend.py`（1 处，4 行）
- 立案测试：`tests/test_network_resource_leaks.py::test_ring_reader_stop_timeout_keeps_thread_reference`
- 归属：`mbdsdr_ai/sdr_backend.py` 原 claim owner = 书签应用顺序线；
  按 `docs/COORDINATION.md` §6.1.4，过期 < 30 分钟不接管，本轮只出补丁。

## 1. 一句话现象

`stop()` 的 `join(timeout=2.0)` 超时后无条件把 `self._thread = None`：
生产者线程还活着却失去引用，之后无法再 join 收尸；同一 reader 再 `start()`
时因为 `_thread is None` 会再起一个生产者（两个线程并发写同一 ring）。

## 2. 应用步骤（照着做，不需要读背景）

### 第 0 步：确认前提

```powershell
cd D:\mbdsdr\MBDSDR
git status --porcelain -- mbdsdr_ai/sdr_backend.py     # 必须为空
git log --oneline -1                                   # 记下当前 HEAD
```

- 成功：第一条**没有任何输出**。
- 失败（有 ` M`/`??`）：说明有人正在改这个文件，**停止**，按
  `docs/COORDINATION.md` §6.1.1 先查在途改动。

### 第 1 步：试应用（不改盘）

```powershell
git apply --check docs/pending-patches/2026-11-12-ring-reader-stop-reference-loss.patch
"RC=$LASTEXITCODE"
```

- 成功：`RC=0`。
- 失败（非 0）：上下文已漂移（`sdr_backend.py` 在 `79e1d297` 之后被改过）。
  **不要** `--3way` 或手工硬贴，按新的行号重取上下文重做补丁。

### 第 2 步：应用

```powershell
git apply docs/pending-patches/2026-11-12-ring-reader-stop-reference-loss.patch
git diff --stat -- mbdsdr_ai/sdr_backend.py
```

- 成功：`1 file changed, 4 insertions(+), 1 deletion(-)`。
- 失败：报 `patch does not apply` → 回到第 1 步。

### 第 3 步：先看「补丁确实生效」的唯一判据

```powershell
$env:PYTHONPATH='d:\mbdsdr\MBDSDR'; $env:MBDSDR_TEST_LOCK='0'
python -m pytest -q tests/test_network_resource_leaks.py -k ring_reader
```

- **期望：`1 failed`，且失败原因是 `XPASS(strict)`**。
  这**不是**补丁坏了，而是立案时写的 `xfail(strict=True)` 在验收到位后
  按约定翻成失败——说明修复真的生效了。
- 若显示 `1 xfailed`：补丁没生效（或应用到了别的解释器/工作区），回第 2 步。
- 若显示 `1 passed`：说明有人已经把 `xfail` 标记删了，直接跳第 5 步。

### 第 4 步：删掉 xfail 标记（把「立案」转成「守门」）

编辑 `tests/test_network_resource_leaks.py`，删除：

```python
@pytest.mark.xfail(
    strict=True,
    reason="sdr_backend.py 的 _ThreadedRingReader.stop() 在 join 超时后丢弃"
           "_thread 引用；该文件被书签应用顺序线占用，待释放后修复",
)
```

（只删这 4 行装饰器，测试体与注释保留。）

### 第 5 步：验收

```powershell
python -m pytest -q tests/test_network_resource_leaks.py -k ring_reader   # 期望 1 passed
python -m pytest -q tests/test_network_layer_matrix.py                    # 期望 61 passed
python -m pytest -q tests/test_network_resource_leaks.py                  # 期望 7 passed
```

- 成功：三条命令的 summary 分别为 `1 passed` / `61 passed` / `7 passed`，
  且没有 `failed`/`error`。
- 失败：把 `git diff` 贴进提交说明，按失败用例定位；不要带着红提交。

### 第 6 步：回退（可选）

```powershell
git checkout -- mbdsdr_ai/sdr_backend.py
```

只回退产品文件；`tests/` 里删掉的 xfail 标记需要自己恢复（或 `git checkout -- tests/...`）。

## 3. 预期结果一览

| 时点 | 命令 | 期望 |
| --- | --- | --- |
| 应用前 | `pytest --runxfail -k ring_reader` | `1 failed`（`assert None is not None`） |
| 应用后、删标记前 | `pytest -k ring_reader` | `1 failed`（XPASS strict） |
| 删标记后 | `pytest -k ring_reader` | `1 passed` |
| 删标记后 | `pytest tests/test_network_layer_matrix.py` | `61 passed` |
| 删标记后 | `pytest tests/test_network_resource_leaks.py` | `7 passed` |

## 4. 补丁做了什么（评审要点）

只改 `stop()` 的收尾一行：`join(2.0)` 返回后**先看线程是否真的退出**，
只有 `not is_alive()` 才 `_thread = None`；仍活着就保留引用，让下一次
`stop()` 或调用方还能 join，且 `start()` 的 `_thread is None` 判断不会再
误起第二个生产者。

不改 join 上限（仍 2.0s），不改 `start()`，不改任何调用方——所以矩阵用例
行为不变。真正的「disconnect 白等 2s」在三条链路上已由
`79e1d297` 的「先关 socket 再 join」消化；本补丁只补 `_ThreadedRingReader`
自身的引用泄漏口，供其他直连 `read_fn`（如 USB bulk）的后端兜底。
