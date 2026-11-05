# 网络接入层实测：rtl_tcp / SpyServer 中断矩阵

> 本篇结论未经 commit 取证，引用前须复核（判据见 `scripts/verify_claims.py`；补齐四元组 `<结论> — <hash> — <path>:<line> — <改动后字面量>` 后可摘除本行）。

> 口径：**本机 loopback 假服务端 + 真实 socket**，不连公网、不碰真硬件。
> 复跑命令（PowerShell）：
>
> ```powershell
> $env:MBDSDR_NO_HW='1'; $env:MBDSDR_OFFLINE='1'; $env:MBDSDR_NULL_AUDIO='1'
> $env:QT_QPA_PLATFORM='offscreen'; $env:PYTHONPATH='d:\mbdsdr\MBDSDR'
> python -m pytest -q -s tests/test_network_layer_matrix.py -p no:cacheprovider
> ```
>
> 实测时间为本机一次运行结果（假服务，Windows 11 / Python 3.14）。
> 测试里把 `idle_timeout` 压到 0.5s 以缩短用例；**产品默认是 5.0s**，
> 表中同时给出「实测值」和「按默认 5s 线性换算后的量级」。

## 0. 弱网矩阵结论：保持 5s / 10s（本轮实测，2026-11-05）

**建议值：保持 `idle_timeout=5s`、硬判 `2×idle=10s`，不改 3s / 6s，也不改 2s / 4s。**
理由见 0.3 与 0.5；本轮**没有改动任何常量**，测试结构也未动
（三种阈值只是 `RtlTcpClient(idle_timeout=...)` 的入参）。

### 0.1 取舍表（误判率 vs 发现延迟）

误判率口径：13 个弱网档，每档跑 `2×阈值 + 0.8s` 窗口（5s 档 10.8s、3s 档
6.8s、2s 档 4.8s），数客户端状态机「转 stale」与「转 disconnected」的次数。
窗口刻意覆盖「连续两次心跳超时」的时间尺度——否则 5s 一列的 0 次是结构性
的（窗口短于阈值，根本不可能超时），测了也没意义。

| 配置 | 误判率（13 档 × 窗口，rtl_tcp 客户端） | 真实发现延迟（半开连接，rtl / spy） |
|---|---|---|
| **5s / 10s（建议保持）** | **0/13 档，0 次 stale；0 次硬断开** | stale **5.004s / 5.010s**；硬判 **10.024s / 10.027s** |
| 3s / 6s | 2/13 档（**15.4%**），2 次 stale；0 次硬断开 | stale **3.010s / 3.003s**；硬判 **6.032s / 6.020s** |
| 2s / 4s（本轮补测，§0.5） | 2/13 档（**15.4%**），2 次 stale；0 次硬断开 | stale **2.010s / 2.018s**；硬判 **4.022s / 4.025s** |

3s/6s 的两档误判是：`delay-3s`（固定 3s 管道延迟，实测首字节 3.137s 到达）与
`bwjit-3.2s`（周期性 3.2s 完全停发，实测空档 3.230s）——都只越线 0.003～0.23s，
但足够让状态机闪一次 `stale` 并写错误文案。5s 档同样的空档只有阈值 64%，
仍有 1.7s 余量。

### 0.2 四类弱网 × 13 档明细（同一轮实测）

`max_gap` = 客户端相邻两次读成功的最大间隔（首帧从建连算起）。静默期内读不出
整帧，所以它是「无字节」的**上界**；窗口内 `max_gap < 2×阈值` 即证明硬断开
（需连续 2×阈值 无字节）不可能发生。`rate_ratio` 是观测吞吐/期望速率。

| 档位（实测参数） | 5s: stale / disc | 3s: stale / disc | max_gap 5s / 3s | rate_ratio 5s / 3s |
|---|---|---|---|---|
| loss-1%（按块丢 1%） | 0 / 0 | 0 / 0 | 0.017s / 0.017s | 0.188 / 0.188 |
| loss-5% | 0 / 0 | 0 / 0 | 0.022s / 0.024s | 0.179 / 0.180 |
| loss-10% | 0 / 0 | 0 / 0 | 0.033s / 0.027s | 0.171 / 0.170 |
| delay-200ms（+0.05~0.3s 抖动） | 0 / 0 | 0 / 0 | 0.337s / 0.318s | 0.184 / 0.181 |
| delay-1s | 0 / 0 | 0 / 0 | 1.137s / 1.137s | 0.170 / 0.159 |
| **delay-3s** | 0 / 0 | **1 / 0** | 3.138s / 3.137s | 0.137 / 0.103 |
| reorder-5%（每次 50ms 等重传） | 0 / 0 | 0 / 0 | 0.065s / 0.063s | 0.174 / 0.174 |
| reorder-10% | 0 / 0 | 0 / 0 | 0.062s / 0.062s | 0.173 / 0.173 |
| reorder-20% | 0 / 0 | 0 / 0 | 0.062s / 0.063s | 0.171 / 0.172 |
| bwjit-200ms（周期 0.8s，停 0.2s） | 0 / 0 | 0 / 0 | 0.220s / 0.244s | 0.139 / 0.134 |
| bwjit-1s（周期 4s，停 1s） | 0 / 0 | 0 / 0 | 1.032s / 1.024s | 0.134 / 0.134 |
| **bwjit-3.2s**（周期 12.8s，停 3.2s） | 0 / 0 | **1 / 0** | 3.212s / 3.230s | 0.133 / 0.099 |
| bwjit-trickle（1.3 kS/s = 标称 0.13%） | 0 / 0 | 0 / 0 | 1.641s¹ / 1.614s¹ | 0.0013 / 0.0013 |

¹ 涓流档的 `max_gap` 是**整帧读取耗时**（一次读 2048 样点，1.3 kS/s 需要 1.6s），
不是无字节时长；字节到达间隔实为 0.2s。它证明的是「吞吐塌到 0.13% 也不会触发
心跳」——心跳只认字节有无，不认速率；速率坍塌走 `rate_ratio` 指标。

### 0.3 为什么保持 5s/10s 而不是 3s/6s

1. **收益 4s，代价 15.4% 档位误判。** 3s/6s 把硬判从 10.0s 提到 6.0s，快 4s；
   但 3s 延迟与 3.2s 拥塞塌陷两档各误报 1 次，而 5s 档 13/13 全零。
2. **误判对用户可见。** `stale` 会写「心跳缺失」错误文案并降级链路显示；
   在 3s 级 RTT 或持续拥塞的链路上，3s 阈值会周期性闪红，而 4s 的提前量
   相对 91s 的自动重连预算（§1）不可感知。
3. **5s 仍有 1.7s 余量，3s 已经越线。** 实测最坏空档 3.212s：对 5s 是 64%
   占用量，对 3s 是 107%。
4. **没有牺牲快速发现。** FIN/RST 仍是 0.1s / 0.0s 级（§2）；5s 只约束
   「完全静默」这一种情况，且硬判仍在严格的 2×心跳内（实测 10.02s）。
5. **真机余量只多不少。** 本机 loopback 已是理想链路，真实 Wi-Fi / NAT /
   拥塞的重传退避通常比本机恶劣；在理想链路上就误报 2/13 的阈值，
   没有理由外推到真机。

### 0.5 阈值再压一档：2s / 4s（2026-11-06 补测）

同口径 13 档，窗口 `2×2s + 0.8s = 4.8s`；半开连接也补测 2s/4s：

| 档位 | 2s: stale / disc | max_gap 2s |
|---|---|---|
| loss-1% / 5% / 10% | 0 / 0 | 0.025s / 0.030s / 0.041s |
| delay-200ms | 0 / 0 | 0.359s |
| delay-1s | 0 / 0 | 1.137s |
| **delay-3s** | **1 / 0** | 3.139s |
| reorder-5% / 10% / 20% | 0 / 0 | 0.082s / 0.064s / 0.062s |
| bwjit-200ms / 1s | 0 / 0 | 0.232s / 1.020s |
| **bwjit-3.2s** | **1 / 0** | 3.217s |
| bwjit-trickle | 0 / 0 | 1.604s¹ |

半开：rtl stale **2.010s** / 硬判 **4.022s**；spy stale **2.018s** / 硬判 **4.025s**
（`category=idle`；误差 ≤0.025s，确实压到 4s 级）。

**定量结论：阈值越低，误判余量单调收窄；2s/4s 不建议替代 5s/10s。**

| 指标 | 5s | 3s | 2s |
|---|---|---|---|
| 误判档位数（13 档） | 0/13 | 2/13（15.4%） | 2/13（15.4%） |
| 最坏空档 3.22s 占阈值 | 64% | 107% | 161% |
| delay-1s（1.14s）占阈值 | 22.7% | 37.9% | 56.9% |
| bwjit-1s（1.02s）占阈值 | 20.4% | 34.0% | 51.0% |
| 最坏空档的绝对余量 | +1.78s | -0.22s | -1.22s |

- **2s 档没有新增误报**（仍 2/13），但这不能推出「2s 安全」：13 档在 1.14s 与
  3.14s 之间有空洞，没有 1.5~2.5s 级停发档位；2s 只是把同样的两档判死，
  而 1s 级停发已占到阈值的 57%（3s 档 38%、5s 档 23%）。
- **「越低越易误判」在本矩阵体现为余量收窄**：最坏空档占阈值 64% → 107% →
  161%，到 2s 已是阈值的 1.6 倍，任何 1.5~2.5s 的真实停发都会被闪一次 stale。
- 半开 2s/4s 确实把硬判从 10.0s 压到 4.0s（实测 4.022s/4.025s），代价是上述
  余量；若要 4s 级硬判，先补 `delay-1.5s` / `bwjit-2s` 档位重跑，而不是拿
  「2/13 与 3s 相同」当安全证据。

### 0.6 方法与口径（这些结论只对本机成立）

- **测试形态**：本机 loopback 假 TCP 服务，真实 socket 与真实
  `RtlTcpClient` 状态机；无公网、无真硬件。
- **丢包**：应用层按块丢（等价于丢包重传后的晚到），**不是**内核级丢包，
  因此不覆盖 TCP 拥塞控制/重传的真实时序。
- **乱序**：TCP 保证字节序，客户端观测不到真正乱序；本档按「乱序导致等重传」
  的**可观测后果**建模（每次 50ms 停顿，同一 0.5s 内最多一次）。
- **带宽抖动**：按「拥塞窗口塌陷」建模为周期性完全停发（停发占空比 25%），
  另有涓流档验证「速率坍塌但字节不断」不会误判。
- **窗口**：每档 `2×阈值 + 0.8s`；停发周期取 `4×stall`（> 窗口），避免窗口
  末尾的读跨进下一轮停发、把窗口外的新事件误记成窗口内的误判。
- **半开**（§0.1 第二列）：假服务先真实吐数据，然后**保留 TCP、停止转发**；
  客户端必须在 `idle` 内 stale、`2×idle` 内硬判。rtl_tcp 与 SpyServer
  两种后端各测一遍，误差 ≤ 0.03s。
- **未验证**：真实 rtl_tcp / SpyServer 服务端；真实网卡的丢包/乱序/RTO/
  Wi-Fi 漫游/NAT 超时；>5s 的单向延迟；真机 USB。**本节的 0/13、15.4% 与
  2s 档的 161% 占用只在「本机假服务 + 本机回环」口径内成立，不能当作真实
  网络的误判率。** 若将来要改成 3s/6s 或 2s/4s，必须先有真实链路的同类实测，
  而不是拿本机结果外推。

复跑（这两个类约 6 分钟；整文件 61 passed / 347.79s，2026-11-06）：

```powershell
$env:MBDSDR_NO_HW='1'; $env:MBDSDR_OFFLINE='1'; $env:MBDSDR_NULL_AUDIO='1'
$env:QT_QPA_PLATFORM='offscreen'; $env:PYTHONPATH='d:\mbdsdr\MBDSDR'
python -m pytest -q -s tests/test_network_layer_matrix.py::TestWeakNetworkMatrix `
  tests/test_network_layer_matrix.py::TestRealThresholdLatency -p no:cacheprovider
```

实测输出（2026-11-06，本机一次运行，61 passed / 0 failed，347.79s）：
每档打印一行 `[weak] case=... idle=... stale=... disc=... max_gap=... elapsed=...`，
半开打印 `[weak] half-open kind=... idle=... stale=... hard=... category=idle`；
上表取自同口径的两次本机运行（5s/3s 列 2026-11-05，2s 表 2026-11-06），
不是跨版本拼接。

## 1. 常量与现状（代码位置）

| 项 | 值 | 位置 |
|---|---|---|
| 建连超时（rtl_tcp） | 5.0s | `mbdsdr_ai/rtltcp_client.py` → `RtlTcpClient.CONNECT_TIMEOUT` |
| 建连 + 握手超时（SpyServer） | 5.0s（默认 `timeout=5.0`） | `mbdsdr_ai/spyserver_client.py` → `SpyServerClient.__init__` |
| 无数据心跳超时 `idle_timeout` | 5.0s（下限 0.5s） | 两个 client / 两个 backend 的构造参数 |
| 半开硬判阈值 | 连续 2 次心跳超时 = **10.0s** | `RtlTcpClient._STALE_ESCALATE_AFTER` / `SpyServerClient._STALE_ESCALATE_AFTER` |
| 重连退避 | 1, 2, 4, 8, 16, 30（封顶 30） | `mbdsdr_ai/reconnection.py` → `compute_backoff` |
| 重试上限 | 默认 6 次（`None` 也归一到 6） | `mbdsdr_ai/reconnection.py` → `DEFAULT_MAX_ATTEMPTS` |
| 重连放弃后 | 不再自动重试；只能 `retry_now()` / `start()` | `ReconnectionManager._run` / `retry_now` |

**用户会等多久看到结论**

- 建连失败（refused / DNS / timeout）：**≤ 5s**（一次 connect 的硬超时）。
- 中途掉线（FIN / RST）：**≈ 0s～0.1s**（下次读立刻报错，实测见下表）。
- 中途静默（心跳缺失）：**5s 转 `stale` 并带错误文案**；**10s 硬判定断开**。
- 自动重连放弃：退避合计 **61s**，另加最多 6 次 × 5s 建连 = **最坏 91s**。
  之后状态停在 `gave_up`，`snapshot()` 里有 `suggestion`；不会无限刷屏。

结论：**没有无限重试路径**（默认 6 次；显式传 `None` 也被归一到 6），
**也没有静默放弃**（即使不传 `on_give_up`，`snapshot()` 仍能读到
`gave_up / attempts_total / last_error / suggestion`）。

## 2. rtl_tcp 中断矩阵（`TestRtlTcpMatrix`）

测试条件：`idle_timeout=0.5s`；后端为 `RtlTcpBackend`（带环形缓冲/生产者线程）。

| 场景 | 实测发现时间 | 发现方式 | 恢复后首帧 | 静噪链 | 句柄/线程 |
|---|---|---|---|---|---|
| 正常断开（服务端排空后 FIN） | **0.103s** | 读错误 EOF（`error_category=eof`） | 正常：4096 点 complex64、有限、幅度 ≤√2、非常量 | 断开即废弃旧链；重连后新建 `ReceiveChain` 与全新 `BaselineSquelch`（`squelch_db=-50` 生效） | 3 轮断开/重连后存活 TCP socket **1→1**；生产者线程始终 ≤1，断开后为 0 |
| 服务端进程被杀（`TerminateProcess`） | **0.000s**（下一次读立即可见） | 读错误 RST（Windows 杀进程时入站未读→RST，`error_category=reset`） | 同端口重启假服务后重连：首帧正常 | 同上（重连走 `connect()` 重建） | 断开后 `_client=None`、`_ring_reader=None` |
| 网络闪断（源变慢 + 停发） | **0.501s**（默认 5s） | 心跳超时（`idle_timeout`，`error_category=idle`） | 停发结束后下一次读恢复 `connected`，数据继续 | 同一条连接未断，不重建；`receive_chain` 保留 | 无新增 socket |
| 半开连接（上游进程已死、代理保持 TCP） | **0.511s** 转 stale；**1.028s** 硬判断开（默认 5s / 10s） | 心跳超时 → 连续 2 次超时升级 | —（硬判后必须重连） | 重连时由 `_ensure_receive_chain()` 新建 | 硬判时客户端主动 `close()`，socket 回基线 |

补充实测（同一用例）：

- 源半速（期望 1 MS/s，实际 0.5 MS/s）：`rate_ratio=0.30`、`gap_events=1`、
  `dropped_samples=650564`；数值来自真实数据流计数，不是推演。
- 首帧校验同时检查 `dtype=complex64`、长度、`isfinite`、幅度上界与非零方差，
  防止「状态已连接但拿到空/常量占位」。

### 2.1 半开连接反证（任务 4）

`test_half_open_after_kill_reports_within_n_seconds` 的构造：

1. 假 rtl_tcp 进程正常吐数据；
2. 客户端经一个本机代理连上；
3. `proc.kill()` 杀死上游，代理**冻结转发但不关闭客户端 socket**（模拟中间盒保持半开）；
4. 客户端必须在有限时间内报错。

实测：`stale=0.511s`、`hard-disconnect=1.028s`（`idle_timeout=0.5`），
即 **2×idle + 少量调度误差**。按产品默认 `idle_timeout=5s` 换算为
**5s 出现明确 stale 错误文案、10s 硬判定断开**。
断言同时校验 `status.connected is False`、`status.error` 非空、
`error_category=idle`、`data_health.last_data_age_s` 有值。

**这条兜底是本次补上的**：之前连续心跳超时只会一直停在 `stale`
（socket 层仍 `connected=True`，UI 若只读 `connected` 会永远显示「已连接」）。
现在连续 2 次超时会主动关闭 socket、翻成 `disconnected`，交给重连/用户。

## 3. SpyServer 中断矩阵（`TestSpyServerMatrix`）

测试条件：`idle_timeout=0.5s`；后端为 `SpyServerBackend`。

| 场景 | 实测发现时间 | 发现方式 | 恢复后首帧 | 静噪链 | 句柄/线程 |
|---|---|---|---|---|---|
| 正常断开（服务端排空后 FIN） | **0.098s** | 读错误 EOF | 正常（同 rtl_tcp 校验） | 断开废弃旧链，重连新建链与新静噪器 | 断开后 `_client/_ring_reader` 归 None |
| 服务端进程被杀 | **0.000s** | 读错误 RST | 用例覆盖恢复路径（同 rtl_tcp 语义） | 重连重建 | 同上 |
| 半开（停发但 TCP 保持） | **0.505s** 转 stale；**1.016s** 硬判断开（默认 5s / 10s） | 心跳超时 → 连续 2 次升级 | —（硬判后必须重连） | 重连重建 | 硬判时关闭 socket |

### 3.1 与 rtl_tcp 的差异点（均有用例钉住）

| 维度 | rtl_tcp | SpyServer |
|---|---|---|
| 握手 | 12 字节 `RTL0` + tuner/gain | 1024 字节 ASCII `key=value\0` 配置串 |
| 读侧超时返回 | `read_samples()` 返回 `None`，状态 `stale` | `read_samples()` 抛 `SpyServerError`，状态 `stale` |
| 采样率口径 | 客户端**下发**采样率并吸附到设备支持档 | 服务端**声明**采样率；`data_health` 暴露 `server_sample_rate_hz` / `requested_sample_rate_hz` / `sample_rate_mismatch` |
| 命令 | 5 字节 `>BI` | 同格式 `>BI`（本项目近似实现） |
| 断线恢复 | 两种都走 `disconnect()` 废弃接收链 → `connect()` 重建 | 同左 |

> 说明：SpyServer 客户端是项目内的近似实现（1024 字节配置串 + 5 字节命令），
> 未按上游全部协议细节实现；本表只描述**本项目行为**，不外推到真实 SpyServer 的所有边界。

## 4. 顺带修掉的真实缺陷

1. **读侧环形缓冲超时路径自死锁**（`mbdsdr_ai/sdr_backend.py`，
   `_FanOutSub.read`）：`Condition` 用的是非可重入 `Lock`，超时分支里再次
   `with r._lock` 会自锁。表现是「缓冲暂时空 → 读永远不返回」。矩阵用例
   首跑即卡死 70s+（faulthandler 栈证据），修复后超时按时返回、`underruns` 正常累加。
2. **`attempts_total` 多记 1 次**：放弃那轮不再计入累计尝试（先判上限再累加），
   快照与「最多试 6 次」一致。
3. **放弃后仍每 61s 重来一轮**（历史 `_run` 会反复进入 `_retry_loop`）：
   现在 `_gave_up` 后循环只等待停止信号；重来必须显式 `retry_now()`。
4. **半开连接无升级**：见 2.1，两个客户端都补了 stale→disconnected。

## 5. 已验证 / 未验证

**已验证（假源、可复跑）**

- rtl_tcp：正常断开 / 进程被杀 / 源变慢+停发 / 半开（代理保 TCP）；恢复首帧、
  静噪链重建、TCP socket 回基线、生产者线程不累积。
- SpyServer：正常断开 / 进程被杀 / 半开；握手与读错误语义差异、采样率不匹配可见。
- 超时与重试：默认 5s / 5s / 10s；放弃预算 61s + 6×5s = 91s；
  无无限重试、无静默放弃；`retry_now()` 可人工恢复。
- 弱网矩阵（§0）：四类 13 档 × 5s/3s 两阈值，逐档数 stale/disconnected 次数；
  半开在 5s/10s 与 3s/6s 两种真实阈值下各测 rtl_tcp / SpyServer。
  结论「保持 5s/10s」只对**本机假服务 + 本机回环**成立。
- 阈值决策锁（`TestWeakThresholdDecisionLock`）：默认 5.0s / 2×心跳=10.0s 与
  13 档实测证据钉成断言；改成 3s/6s 会直接报红（不是文档口头约定）。
- 读侧超时自死锁回归（§5.1）：超时路径重入会红；修复后 `underruns` 正常累加。

**未验证（必须真机/真实网络，未放宽断言）**

- 真实 rtl_tcp / 真实 SpyServer 服务端进程；真实网卡的丢包、乱序、NAT/防火墙
  超时、Wi-Fi 漫游；高延迟（>5s RTT）链路下的心跳误判率。
- 真机 USB 热插拔的句柄计数已在既有回归中验证；本文件的 socket 计数只覆盖网络源。
- 进程句柄总数（`psutil.num_handles`）在假服务同进程运行时含服务端 worker 线程
  句柄，实测 371→382 有噪声；因此泄漏判据用的是**存活 TCP socket 数**
  （基线 1 → 3 轮重连后 1），不是进程句柄总数。真实环境请用外部句柄工具复核。

### 5.1 读侧超时自死锁：faulthandler 定位过程

**症状**：矩阵用例首跑即卡死 **70s+ 不返回**，不是断言失败，而是纯挂起；
`Ctrl+C` 也只打印「KeyboardInterrupt」而没有栈——因为卡在 C 层 `Lock.acquire`。

**定位过程**（按顺序做，三步就能收敛）：

1. 先排除「假服务没起 / 端口占用」：同一假服务单独用裸 socket 能正常收数据，
   说明阻塞在**客户端读侧**。
2. 用 `faulthandler` 定时抓栈（等价于 `pytest -X faulthandler` 的效果）：

   ```python
   import faulthandler
   faulthandler.dump_traceback_later(2.0, repeat=True)   # 2s 后 dump 全部线程
   ```

   栈显示读线程停在 `mbdsdr_ai/sdr_backend.py` → `_FanOutSub.read` 的**超时分支**，
   且该帧的行正是 `with r._lock`；主线程停在 `join`/等待。也就是说：
   `read()` 外层已经持有 `r._lock`，超时分支又拿同一把锁。
3. 用「等价形态」最小复现（同一把 `Lock` + 同一个 `Condition`，已持有锁时再
   `with lock:`），确认自锁与业务无关：

   ```python
   lock = threading.Lock()
   cond = threading.Condition(lock)
   def read_old_timeout_branch():
       with lock:                     # ← _FanOutSub.read 已持有 r._lock
           deadline = time.time() + 0.2
           while True:
               now = time.time()
               if now >= deadline:
                   with lock:         # ← 旧代码：超时分支再嵌套 with r._lock
                       pass
                   return
               cond.wait(timeout=max(0.001, deadline - now))
   ```

   该复现的 faulthandler 实抓栈（本机，Python 3.14，2s 超时；线程名已改）：

   ```
   Thread 0x000072b4 (most recent call first):
     File "dl_probe.py", line 17 in read_old_timeout_branch
     File ".../threading.py", line 1023 in run
     File ".../threading.py", line 1081 in _bootstrap_inner
     File ".../threading.py", line 1043 in _bootstrap
   Thread 0x00006b30 (most recent call first):
     File ".../threading.py", line 1132 in join
     File "dl_probe.py", line 26 in <module>
   probe stuck=True after 3.0s join
   ```

   第 17 行就是内层 `with lock:`；主线程卡在 `join`。原首跑日志已随临时文件
   清理，这段栈是今天用同一形态重新抓取，行号对应上面的复现片段。

**根因**：`_ThreadedRingReader` 用 `threading.Lock()` 构造
`threading.Condition(self._lock)`。`Condition` 的默认锁是 `RLock`，但这里显式
传入**非可重入** `Lock`；`_FanOutSub.read` 在 `with r._lock` 内等待条件，超时
分支再 `with r._lock` 就是同线程二次加锁 → 永久阻塞。

**修复**：超时分支不再进锁，直接 `r._underruns += 1` 后返回 `None`
（`mbdsdr_ai/sdr_backend.py` `_FanOutSub.read`，注释在 1198-1199 行）。

**回归**：`TestRingReaderTimeoutDeadlock::test_timeout_branch_returns_instead_of_self_deadlocking`
让读线程走超时分支，`join(2.0)` 后必须已退出，并检查 `underruns=1`、锁已释放；
若把嵌套锁加回去，线程会永远停在 `with r._lock`，该用例直接报红（daemon 线程
+ 失败时不碰锁，保证红的时候不会把 pytest 挂死）。复跑：

```powershell
python -m pytest -q -s tests/test_network_layer_matrix.py::TestRingReaderTimeoutDeadlock `
  tests/test_network_layer_matrix.py::TestWeakThresholdDecisionLock -p no:cacheprovider
```

## 6. 已知无关失败

`tests/test_orphan_connection.py::test_constellation_cleared_resets_stats` 在本次
定向回归中失败：期望 `信号: --`，实际 `信号: —`。该文案改动来自桌面线
（`desktop/main_window.py`，本线未触碰），不计入本主题结论。

## 7. 热插拔/重连回归脚本（`tests/network_hotplug_regression.py`）

一条命令跑四条路径，非 `test_` 前缀，不进 pytest 收集；直接执行：

```powershell
$env:MBDSDR_NO_HW='1'; $env:MBDSDR_OFFLINE='1'; $env:MBDSDR_NULL_AUDIO='1'
$env:PYTHONPATH='d:\mbdsdr\MBDSDR'
python tests/network_hotplug_regression.py
```

每行输出 `case=... verdict=PASS|FAIL|SKIP elapsed=...s detail=...`，
退出码 0 = 无 FAIL（SKIP 不算通过），1 = 有 FAIL。

| 路径 | 实测口径 | 本轮结果 |
|---|---|---|
| `usb-unplug`（真机拔线） | 需物理拔插；`MBDSDR_NO_HW=1` 或无设备时 SKIP 并写原因 | **SKIP**（本进程禁止碰真硬件；不伪造 PASS） |
| `port-switch`（换端口） | 服务端 A 口 → B 口，新后端连 B 取首帧 | PASS，connect→首帧 0.022s / 0.020s |
| `backend-crash`（进程被杀） | `TerminateProcess`，默认 `idle_timeout=5s` | PASS，detect=0.000s、`error_category=reset` |
| `half-open`（保 TCP 不转发） | rtl 与 spy 各一遍，默认 5s | PASS，rtl stale 5.018s / hard 10.031s；spy 5.009s / 10.023s |

`usb-unplug` 只有「人工拔线后观察 connected→False、link_state→disconnected、
重连首帧正常」这一条路；脚本把人工步骤如实标 SKIP，不拿网络源的断开代替真机
拔线结论。

## 8. 界面状态阶梯：用户到底看到什么（2026-11-06）

回归文件：`tests/test_link_state_visibility.py`（六状态表本身写在文件头注释里）。
判据一律取后端 `get_status()` 的真实字段，UI 不另算：

| 状态 | 后端事实 | 界面文案（修复后） | 修复前 |
|---|---|---|---|
| 连接中 | `connect()` 尚未返回（远端最长 5s） | `连接中 <设备>`（`QApplication.processEvents()` 后可见） | 远端/本地路径**没有**连接中，标签停在上一状态；只有 ai-sdr worker 路径有 |
| 已连接 | `link_state=connected` 且数据年龄 ≤2s | `<设备名>` | 诚实 |
| 静默超时 5s | `link_state=stale`，`connected=True` | `数据流中断 5.2s（连接仍在）`；状态栏只报一次，附 `link_half_open` | **看不到**：health tick 先看 `error`（stale 会写 error）或 2s 年龄，直接按掉线收尾 |
| 升级断开 10s | `link_state=disconnected/failed` 或 `connected=False` | `未连接` + `设备已断开：…（错误码 link_reset）（不会自动重连，请点工具栏「连接」重试）` | 已经在 2s 被提前判死，10s 这一刻没有独立文案 |
| 自动重连中 | 桌面端**未接线** `ReconnectionManager` | 不显示（不伪造） | 同样不存在 |
| 重连成功 / 放弃 | 只能用户手动点「连接」 | 手动连接成功后回到「已连接」 | 不存在自动成功/放弃 |

错误码上浮：顶栏在 stale / 断开时显示 `link_half_open` / `link_reset` 等码；
环形缓冲积压到 `high`（数据还在流、但旧样本会被覆盖）单独提示
`buffer_overflow`，限流 30s，且**不拆连接**（`test_buffer_overflow_code_is_visible_without_disconnect`）。
`link_timeout` / `link_refused` / `link_half_open` / `link_reset` / `buffer_overflow`
均登记在 `error_catalog` 与 troubleshooting 的「接入链路」族。

用户最初假设「中间 5 秒显示还连着」与实测代码不符：`_on_health_tick` 的旧判据是
`status.error` 优先、其次 `last_data_age_s > 2s`，两处都会直接 `_handle_backend_lost`。
所以真实假状态是**跳过 stale、提前说「已断开」**，而不是 stale 期间冒充已连接。
修复后 2s 起就显式降级（满足 AC-06.7 可见性），5s/10s 由后端 `link_state` 驱动。

反证：删除 `_on_health_tick` 里的 `if link == "stale"` 分支后，
`test_stale_is_visible_not_disconnected` 立刻红，实际标签是 `FakeSDR |`（仍在冒充
已连接）；恢复分支后 6 条全绿。

### 重连策略实测（桌面 vs 库层）

桌面端没有把 `ReconnectionManager` 接到 `MainWindow`（`grep ReconnectionManager
desktop/` 无命中），所以真实策略是**断了就放弃**：自动重试 0 次、间隔 N/A、不会
自动恢复、也不会自动误连。`tests/test_link_state_visibility.py::test_no_auto_reconnect_policy`
把这条现状钉死。

库层（若将来接线）用假后端实测（**修复前**的原始行为；修复后见下面两小节）：

| 场景 | 实测数字 | 结论 |
|---|---|---|
| ① 对端彻底不回来 | 6 次，时刻 0.505/1.513/3.524/7.527/15.537/31.575s，间隔 1/2/4/8/16s，放弃总耗时 61.60s | 有上限，不会永远转圈 |
| ② 3s 后回来 | 第 3 次（3.52s）恢复；频率 100MHz、采样率 2.4MHz、增益 30dB 恢复；**解调模式 NFM→WFM 未恢复** | 自动恢复；模式不保持（`_restore_settings` 未下发 `demod_mode`） |
| ③ 回来但换了设备 | 首连即成功，`device_id=dev-B`，仍把 100MHz 设置下发到陌生设备 | **会误连**；无设备身份校验 |

场景 ②/③ 是库层行为，桌面端当前根本不会自动走到那里；报告里不得把它当成
桌面体验。真机/真实网络仍未验证。

#### 库层两处收口（2026-11-06）

- **解调模式恢复**：`_restore_settings` 之前只下发频率/采样率/增益，实测场景 ②
  重连后 NFM 掉回 WFM。现在补下发 `set_demod_mode`，断言在
  `tests/test_reconnection.py::test_disconnect_then_reconnect_restores_settings`。
- **设备身份校验**：断开前抓拍 `device_id / serial_number / sn`，重连成功后逐字段
  比对；同一字段前后都读到且不一致 → **不下发任何设置、不重启音频、不回调
  `on_reconnected`**，`last_error` 写明「断开前 A / 现在 B」，直接 `gave_up`
  （`test_reconnect_does_not_apply_settings_to_a_different_device`）。
  任一侧读不到身份就不判定，避免把「不知道」写成「换了设备」
  （`test_reconnect_without_identity_does_not_false_positive`）。
- 桌面端**未接线**自动重连，以上两条只是库层能力，不是桌面体验。

#### 91 秒不能干等：取消入口与界面文案（库层）

重试期间 `snapshot()` 新增 `hint` / `worst_case_s` 两个字段：

| 字段 | 含义 | 默认值 |
|---|---|---|
| `worst_case_s` | 退避合计 + 每次 connect 典型超时（5s） | **91.0s**（61 + 6×5） |
| `hint` | 只在 `retrying` 态非空：`正在重试（第 N/6 次，Xs 后重试；最坏约 91 秒放弃，可点 × 立即停止）` | 重试态 |

`cancel()`（界面「× 停止」的入口）**立刻停**：

- 退避等待被唤醒，**下一次 `connect` 不再发起**（用例断言取消后 `connect_calls` 冻结）；
- 状态直接转 `gave_up`、`last_error="用户取消重连"`，并回调一次 `on_give_up` 让按钮置灰；
- 若此刻正卡在某次 `connect` 的硬超时里，最坏要等这次 connect 返回（≤5s），之后不再有任何重试。

**保持不变**：音频仍是停止态（取消不会自动重启）；后端对象不被关闭（不替用户关别人的连接）；
设置快照保留到下次 `retry_now()` 为止（`retry_now()` 走 `start()`，会重置快照与计数）。

桌面端不适用这条等待：桌面没有接线自动重连，断线即刻显示
「设备已断开：…（不会自动重连，请点工具栏「连接」重试）」，用户不会遇到 91s 倒计时
（`desktop/main_window.py`，该文件由操作逻辑线持有，本线未改）。

#### 身份比对：半侧可读不判定

断开前只读到 `serial_number`、重连后只读到 `device_id`（互相缺一侧）时，
两个字段从未在同一侧同时出现 → **不判定**，照常恢复设置。
用例 `test_identity_half_side_readable_is_not_a_mismatch` 钉住；
反证：把「任一侧缺失就不判」改成「缺失当空串比较」，该用例立刻红，日志为
`重连后的设备身份不匹配（device_id：断开前 ，现在 dev-C）`。

#### 桌面「断了就放弃」：保留策略的理由与代价

策略保留：桌面不接 `ReconnectionManager`，断线后停在「未连接」，用户必须手点
顶栏「连接」。理由：

1. **不替用户做设备决策**：拔插 USB / 停远端服务通常需要用户到现场处理；
   自动重连会和用户操作抢时序（刚放弃又连上、刚连上又被拔）。
2. **不伪造进度**：没有自动重试就不显示「正在重连…」，六状态表第 5 行如实为空。
3. **身份校验刚落地、未真机验证**：先接线等于把新判据放进未经真机验证的路径。

代价（明说）：

- **库层最坏 91s 才拿到结论**：6 次退避合计 61s，另加最多 6×5s 建连超时。
  桌面不走这条路径，所以用户看到的是立即的「设备已断开」（见六状态表第 4 行）。
- **设备恢复后不会自己回来**：必须手动点「连接」，不点就一直停在「未连接」。
- **手动重连不等于自动恢复**：断开前的解调模式/频率是否在手动路径上保留，
  桌面未实测，本表**不写**「会自动恢复」。

## 9. 接入层四矩阵终局表（2026-11-06，HEAD `fc1f8076`）

同一 HEAD 串行跑完四条矩阵，跑前/跑后 `git rev-parse --short HEAD` 均为 `fc1f8076`
（无并发提交漂移）。全部断言由命令复跑得出（E2），原始输出落
`D:\mbdsdr\tmp\access_matrix_20261106\final_m*.txt`（未入库）。

```powershell
$env:MBDSDR_NO_HW='1'; $env:MBDSDR_OFFLINE='1'; $env:MBDSDR_NULL_AUDIO='1'
$env:PYTHONPATH='d:\mbdsdr\MBDSDR'
python -m pytest tests\test_network_layer_matrix.py::TestRtlTcpMatrix -q -s
python -m pytest tests\test_network_layer_matrix.py::TestSpyServerMatrix -q -s
python tests\network_hotplug_regression.py
python -m pytest tests\test_backend_reconnect_stress.py -q
python -m pytest tests\test_reconnection.py tests\test_reconnection_e2e.py tests\test_reconnect_zero_freq.py -q
```

| 矩阵 | 子项 | 判定 | 关键数字 | 复跑命令 | 归因桶 |
|---|---|---|---|---|---|
| M1 rtl_tcp | 正常断开→重连（3 轮） | PASS | 发现 `0.000s`（EOF）；TCP socket 基线 `1`→3 轮后 `1`；生产者线程 `1`；进程句柄 `324→372`（+48 为首次建链触发 `scipy.signal` 惰性导入的一次性加载，**不是逐轮增长**）；`4 passed in 4.38s` | `pytest tests/test_network_layer_matrix.py::TestRtlTcpMatrix -q -s` | — |
| M1 rtl_tcp | 后端进程被杀→重启 | PASS | 发现 `0.000s`（reset）；重启后首帧正常（1024 点 complex64） | 同上 `TestRtlTcpMatrix` | — |
| M1 rtl_tcp | 源停顿 / 半速源 | PASS | stale `0.510s`（`idle=0.5s`）；`rate_ratio=0.30`；`gap_events=1`；`dropped=665,791` | 同上 `TestRtlTcpMatrix` | — |
| M1 rtl_tcp | 半开连接（服务死、TCP 活） | PASS | stale `0.509s`、硬判 `1.019s`（`idle=0.5s`） | 同上 `TestRtlTcpMatrix` | — |
| M2 SpyServer | 正常断开 / 进程被杀 / 半开 | PASS | 发现 `0.000s`（EOF / reset）；半开 stale `0.514s`、硬判 `1.027s`（`idle=0.5s`）；握手 `1024B` vs rtl_tcp `12B`；`4 passed in 3.43s` | `pytest tests/test_network_layer_matrix.py::TestSpyServerMatrix -q -s` | — |
| M3 热插拔 | `usb-unplug` 真机拔线 | **SKIP** | `MBDSDR_NO_HW=1`，拔线需人工；本机三路注入全不通（详见 L61） | `python tests/network_hotplug_regression.py` | **环境**（无管理员/无 VM/`usbipd` 未装），已登记 L61 |
| M3 热插拔 | `port-switch` 换端口 | PASS | `0.120s`；connect→首帧 `0.042s / 0.021s`，两条首帧均 1024 点 complex64 | `python tests/network_hotplug_regression.py` | — |
| M3 热插拔 | `backend-crash` 进程被杀 | PASS | `0.112s`；`detect=0.000s`（reset），终态 `disconnected` | `python tests/network_hotplug_regression.py` | — |
| M3 热插拔 | `half-open` 半开 | PASS | `20.088s`；rtl stale `5.016s`/硬判 `10.031s`，spy `5.006s`/`10.018s`（基准 2×idle=10.0s，容差 +0.9s）；summary `PASS=3 FAIL=0 SKIP=1` | `python tests/network_hotplug_regression.py` | — |
| M3 热插拔 | 假 USB 10 轮拔插/重连 | PASS | `2 passed in 4.90s`；修正基线后 10 轮句柄 `+0`、线程 `+0`、11 支假棒全部 closed | `pytest tests/test_backend_reconnect_stress.py -q` | — |
| M4 重连 | 库层重试/恢复/身份校验/取消/零频 | PASS | `18 passed in 3.63s`（含 `_restore_settings` 恢复 `demod_mode`、换设备不下发、半侧可读不判定、`cancel()` 冻结 `connect_calls`） | `pytest tests/test_reconnection.py tests/test_reconnection_e2e.py tests/test_reconnect_zero_freq.py -q` | — |

**失败归因**：终局跑 **FAIL=0**；唯一非 PASS 是 M3 `usb-unplug` 的 `SKIP`，归
**环境**桶（物理拔线在本机无法程序化注入，产品缺陷与测试取法均不成立），
复现步骤与升 E2 缺什么已在 `docs/limitations-register.md` L61。

**观察项（未计 FAIL）**：M4 首次运行（同 HEAD）在 teardown 打印过
`RuntimeError: libshiboken: Internal C++ object (SatTrackPanel) already deleted`
（`desktop/sat_track_panel.py::_apply_narrow_defaults` 由 QTimer 在对象销毁后回调）。
终局跑未复现、未致测试失败，归 **产品缺陷（潜在 UI 生命周期）**，待单独复现。

**与 5 号基线修正的关系**：性能线若把 `import scipy.signal` 提前到启动阶段，
只是把这次一次性加载从“首次建链”挪到“启动”；本测试的基线取法（热身建一次链后
采样）在预热前后**都成立**，逐轮“基线+2”的断言不变——**预热不影响该判定**。
