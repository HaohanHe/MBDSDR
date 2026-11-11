# 交接单：W37 回放终态判据改为文件样本数（D-9 裁定 B）

> 提出：2026-11-11；D-9 已裁定「先落 B（最小可回滚），C 单列后续条目」。
> 落点：`desktop/main_window.py`（HEAD `b9d10690` 快照行号）
> **状态：等待执行**。该文件当前由他线持有未提交改动（`git status` 非空，
> 含录音文案 hunk 与 `# MUTATION:` 标记），本线按纪律**不抢、不 stash**；
> 等 `git status --short -- desktop/main_window.py` 为空并 claim 后再动。
> 本单只描述改动，未改任何产品代码。

## 一、缺陷（实测）

当前 `_replay_total = int(iq.size)`（`:11491`）记的是**本次载入样本数**，
不是**文件样本数**。`load_iq` 的 `REPLAY_MAX_SAMPLES=24,000,000`（`:43`、
`:11371`）把 2.4 MS/s 的 20 s 文件读成 10.0 s 后，播到载入末尾即打印
`回放完成（10.0s）`（`:11540-11545`）。

实测（`scripts/repro/repro_playback_10s.py`，提交 `b9d10690`/`67cec71a`）：

- 20 s / 2.4 MS/s 文件：`file_samples=48,000,000`，载入 `24,000,000`，
  `truncated=True`，起播 `_replay_total=24,000,000`；
- 截断点随采样率变化，证明是样本数不是时长：

  | 采样率 | 文件（60 s） | 载入 | 截断秒数 | truncated |
  |---|---|---|---|---|
  | 0.480 MS/s | 28,800,000 | 24,000,000 | 50.00 s | True |
  | 1.024 MS/s | 61,440,000 | 24,000,000 | 23.44 s | True |
  | 2.400 MS/s | 144,000,000 | 24,000,000 | 10.00 s | True |
  | 3.200 MS/s | 192,000,000 | 24,000,000 | 7.50 s | True |

违反 D-9 判据「播放位置真的到达文件末尾」：截断态下「完成」是假读数。

## 二、方案（B：最小可回滚）

**核心约束**：不能直接把 `_replay_total` 换成文件样本数——`_replay_tick` 的
停止条件 `_replay_pos >= _replay_total`（`:11540`）会永远不成立，回放永不结束。
因此拆成两个量：

1. `_replay_total`（保持）：**载入**样本数，只用于停止推进；
2. 新增 `_replay_file_total`：**文件**样本数，作为终态判据与文案口径；
3. 新增 `_replay_truncated = bool(rec.get("truncated"))`；
4. `_begin_replay_from_rec` 里同时落盘：
   - `self._replay_file_total = int(rec.get("file_samples") or iq.size)`
   - `self._replay_file_duration_s = rec.get("file_duration_s")`（可能为 None）
5. 完成文案分支（`:11540-11545`）：
   - `_replay_truncated` 为真 → `已播完载入部分（文件共 {file_s:.1f} s，本次载入 {tot_s:.1f} s）`，
     **不得出现「回放完成」**；`file_s` 优先用 `file_duration_s`，缺失时用
     `file_total / sr`；
   - 未截断且时长未知 → 维持现状「回放完成（时长未知：录制中改过采样率）」；
   - 未截断且时长已知 → 维持现状 `回放完成（{tot_s:.1f}s）`。
6. 进度行（`:11538-11539`，建议一并）：截断态追加后缀
   `回放中: {cur:.1f}/{tot_s:.1f}s（仅载入部分）`，避免同屏两个口径打架。
7. **不动** `REPLAY_MAX_SAMPLES` 的数值（改上限属另一条，不在 B 范围）。

### 落点速查（HEAD 快照，行号会随他线改动漂移，以符号为准）

| 位置 | 现状 | 改动 |
|---|---|---|
| `:11421` `_begin_replay_from_rec` | 只在 `truncated` 时弹说明，不记状态 | 落盘 `_replay_file_total` / `_replay_file_duration_s` / `_replay_truncated` |
| `:11491` | `_replay_total = int(iq.size)` | 保留；新增文件口径变量 |
| `:11538-11539` | 进度行只显示载入总长 | 截断态加「（仅载入部分）」 |
| `:11540-11545` | 一律「回放完成」 | 三分支文案 |
| `:1063` 附近 `__init__` | 仅 `_replay_total: int = 0` | 补两个新属性默认值，防 `getattr` 兜底 |

### 补丁草案（不要直接套用，按落库时上下文对齐）

```python
# __init__（:1063 附近）
self._replay_file_total: int = 0
self._replay_file_duration_s = None
self._replay_truncated = False

# _begin_replay_from_rec（:11491 附近，紧随 _replay_total 赋值）
self._replay_file_total = int(rec.get("file_samples") or iq.size)
self._replay_file_duration_s = rec.get("file_duration_s")
self._replay_truncated = bool(rec.get("truncated"))

# _replay_tick 收尾（:11540-11545）
if self._replay_pos >= self._replay_total:
    if self._replay_truncated:
        file_s = (float(self._replay_file_duration_s)
                  if isinstance(self._replay_file_duration_s, (int, float))
                  else self._replay_file_total / self._replay_sr)
        done = (f"已播完载入部分（文件共 {file_s:.1f} s，"
                f"本次载入 {tot_s:.1f} s）")
    elif getattr(self, "_replay_unknown_duration", False):
        done = "回放完成（时长未知：录制中改过采样率）"
    else:
        done = f"回放完成（{tot_s:.1f}s）"
    self._stop_replay()
    self.statusBar().showMessage(done, MSG_NORMAL)
```

## 三、测试设计（落库时新建）

建议 `tests/test_replay_truncation_copy.py`，复用 `tests/conftest.py` 的
`window` 夹具与 `tests/test_replay_load_async.py` 的既有写法（monkeypatch
`_replay_tick`，避免 50 ms 定时器收尾）。用例用**小文件**构造 rec，不必真造
24M 样本：

1. `test_truncated_playback_never_says_complete`
   - rec：`iq` 3 s @ 48 kHz、`truncated=True`、`file_samples=288000`、
     `file_duration_s=6.0`、`duration_s=3.0`；
   - 调 `_begin_replay_from_rec`，随后循环调用真实 `_replay_tick()` 直到
     `_replay_timer is None`；
   - 断言最终状态栏文案含「已播完载入部分」「文件共 6.0 s」「本次载入 3.0 s」，
     且**不含**「回放完成」。
2. `test_untruncated_playback_says_complete_only_at_end`
   - rec：`truncated=False`、3 s；
   - 断言 `_replay_pos < _replay_total` 时文案仍是「回放中」；
   - 播到末尾后文案含「回放完成（3.0 s）」。
3. 回归：`tests/test_replay_load_async.py` 现有截断说明用例必须仍绿。

## 四、变异反证（各条必须能红）

- 移除 `_replay_truncated` 分支、恢复统一「回放完成」→ 用例 1 必红；
- 把 `file_s` 退回 `tot_s`（两个口径混用）→ 用例 1 必红（文案会出现
  「文件共 3.0 s」）；
- 把停止条件改成 `_replay_pos >= _replay_file_total` → 用例 1 会不结束（超时红），
  这正是第二节约束要防的坑；
- 让未截断分支也走「已播完载入部分」→ 用例 2 必红。

## 五、验收

- `python -m pytest tests/test_replay_truncation_copy.py tests/test_replay_load_async.py -q` 全绿；
- 实机（可选）：20 s / 2.4 MS/s 文件起播，约 10 s 后状态栏为
  「已播完载入部分（文件共 20.0 s，本次载入 10.0 s）」，全程无「回放完成」；
- `scripts/repro/repro_playback_10s.py` 仍返回 0（H0 判据不变）。

## 六、与其他条目的关系

- **C（SigMF + WAV 分离）**：单列后续条目，本单不引入新格式、不改文件布局。
- **D-3 / W36（sidecar `center_freq_changes[]`）**：另单，不得与本单合并提交。
- 本单不触碰采样率切换分段逻辑（`_on_sample_rate_changed`），也不改
  `REPLAY_MAX_SAMPLES` 数值。

## 七、证据复现

```powershell
python scripts/repro/repro_playback_10s.py        # 退出码 0，五条判定
```

脚本测到的时间类数字（`elapsed≈15.8 s`）是 offscreen 逐拍 FFT 成本，与截断无关。
