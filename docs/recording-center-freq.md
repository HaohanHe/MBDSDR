# 录音中心频率记账（P0-09）

## 契约

录音段的 sidecar 里：

- `center_freq_hz` = 该段**最后一次真正生效**的中心频率，取
  `backend.get_frequency()` 的读回值，不取 `set_frequency` 的请求入参。
- `center_freq_changes` = 有序事件表，**只在真的变过频时出现**，不留恒为
  `null` 的字段。每条事件：

| 字段 | 含义 |
|---|---|
| `segment_index` | 段号（从 1 起，与 `_partN` 文件后缀一致） |
| `start_sample` | 事件生效时的**段内**样本位（不是全局样本位） |
| `at_time` | 事件落账时间（ISO 8601，本地时区） |
| `from_hz` | 变更前的中心频率（读回值） |
| `to_hz` | 变更后的中心频率（读回值） |
| `target_hz` | 本次**请求值** |
| `actual_hz` | 本次**读回值**，恒等于 `to_hz` |

`target_hz` 与 `actual_hz` 相差 >1 Hz 时单独打一条 warning（「要到了但设备
没跟」），并按读回值记账；下发失败（`set_frequency` 返回 `False`/抛异常）
根本不会进记账，二者不合并成一句「调谐成功」。读回失败时留 warning 并跳过
本次记账，绝不猜一个值写进 sidecar。

## 一拍一记

判定口径：**按 `(segment_index, start_sample)` 去重**，不按墙钟时间窗。

- 两次调谐之间没有写入任何样本 → `start_sample` 相同 → 视为同一拍。
- 同一拍只保留一条事件，记净变化：`from_hz` 取该拍之前的值，`to_hz` /
  `target_hz` / `actual_hz` 取最后一次生效的值。
- 同一拍调出去又调回原值（净变化为 0）→ 整条事件删除，不留假变更。

不按时间窗的原因：墙钟受调度抖动影响，且无法对应回放时间轴；样本位才是
录音文件里的真值。

## 调谐出口登记

机器登记表唯一一份在 `tests/test_record_center_freq_changes.py` 的
`_AST_REGISTRY`；本文件只定义分类语义，不复制函数名单。

| 类别 | 语义 | 记账要求 |
|---|---|---|
| `tune` | 用户可见的调谐（含扫频完成调最强台、天空视图点目标、逐拍调星） | 必须记账。走 `_apply_hw_action("调谐", …, target_hz=…)` 收口，或在直调点调用同一个 `_note_record_center_change` |
| `initial` | 连接期初始频率与连接失败回退 | 此刻不存在已打开的录音段，不需要记账 |
| `rollback` | 面板扫频结束后恢复原频点（约 10503 行） | 扫频期间 IQ 流已停，录音没有样本；恢复的是 sidecar 已有值，不需要记账 |
| `scan` | 扫频内部逐点换频（`_SweepWorker.run.acquire`） | 同上，换频期间不产生可回放样本，不需要记账 |

新增直调出口（`backend.set_frequency` / `self._active_sdr_backend.set_frequency`）
而未登记时，AST 守卫立即变红。

## 守卫红榜文案

| 触发 | 文案 |
|---|---|
| 新增未登记直调 | `发现未登记的 set_frequency 直调出口：X、Y。新增调谐路径请走 _apply_hw_action("调谐", …, target_hz=…)；确实不能走的，在登记表里归类并写明补记理由。` |
| 登记项已消失/改名 | `登记表里的出口已不存在（改名/挪走），请同步登记表：X` |
| `tune` 出口不记账 | `X 被登记为调谐出口，却既不经过收口也不补记：录音中心频率会静默漏记` |
| 收口漏 `target_hz` | `第 N 行的调谐收口没传 target_hz：sidecar 将无法区分「请求值」与「读回值」` |
| 逐拍调星退回直调 | `逐拍调星没有走 _on_tune_sdr → _apply_hw_action 收口` |
| 同一拍重复记账 | `同一拍被两个出口重复记账` / 侧车出现重复拍 |

## 不入收口的两个入口

- `_on_sweep_done`（扫频完成调最强台）：它要把「未能调谐到最强台」与峰值
  列表合成**最后一句**摘要，收口的通用失败提示会被立刻覆盖；补记复用同一个
  `_note_record_center_change`，读回口径与收口一致。
- `_on_sky_object_clicked`（天空视图点卫星）：它要把「方位角 + 未调谐原因
  （未连接 / 无下行频率 / 下发失败）+ 下一步」拼成一句最终状态，三类原因
  自己分流，不适合套收口的通用失败文案；补记同样复用
  `_note_record_center_change`。

收口的异常路径**不会折叠原文**：状态栏按 `<类型>: <原文>` 显示
（`_report_hw_failure`），由
`test_apply_hw_action_passes_through_exception_text` 钉住。因此「收口丢异常
原文」不能作为绕开收口的理由。

逐拍调星（`_poll_satellite_track`）经 `_on_tune_sdr` →
`_apply_hw_action("调谐")` 收口，已在收口内记账，不再单独补记，避免同一拍
写两条事件。
