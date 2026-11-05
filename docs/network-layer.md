# 网络接入层实测：rtl_tcp / SpyServer 中断矩阵

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

**建议值：保持 `idle_timeout=5s`、硬判 `2×idle=10s`，不改 3s / 6s。**
理由见 0.3；本轮**没有改动任何常量**，测试结构也未动
（两种阈值只是 `RtlTcpClient(idle_timeout=...)` 的入参）。

### 0.1 取舍表（误判率 vs 发现延迟）

误判率口径：13 个弱网档，每档跑 `2×阈值 + 0.8s` 窗口（5s 档 10.8s、3s 档
6.8s），数客户端状态机「转 stale」与「转 disconnected」的次数。
窗口刻意覆盖「连续两次心跳超时」的时间尺度——否则 5s 一列的 0 次是结构性
的（窗口短于阈值，根本不可能超时），测了也没意义。

| 配置 | 误判率（13 档 × 窗口，rtl_tcp 客户端） | 真实发现延迟（半开连接，rtl / spy） |
|---|---|---|
| **5s / 10s（建议保持）** | **0/13 档，0 次 stale；0 次硬断开** | stale **5.004s / 5.010s**；硬判 **10.024s / 10.027s** |
| 3s / 6s | 2/13 档（**15.4%**），2 次 stale；0 次硬断开 | stale **3.010s / 3.003s**；硬判 **6.032s / 6.020s** |

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

### 0.4 方法与口径（这些结论只对本机成立）

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
  Wi-Fi 漫游/NAT 超时；>5s 的单向延迟；真机 USB。**本节的 0/13 与 15.4%
  只在「本机假服务 + 本机回环」口径内成立，不能当作真实网络的误判率。**
  若将来要改成 3s/6s，必须先有真实链路的同类实测，而不是拿本机结果外推。

复跑（约 4.5 分钟，只是新增两类的耗时；整文件含既有用例约 6 分钟）：

```powershell
$env:MBDSDR_NO_HW='1'; $env:MBDSDR_OFFLINE='1'; $env:MBDSDR_NULL_AUDIO='1'
$env:QT_QPA_PLATFORM='offscreen'; $env:PYTHONPATH='d:\mbdsdr\MBDSDR'
python -m pytest -q -s tests/test_network_layer_matrix.py::TestWeakNetworkMatrix `
  tests/test_network_layer_matrix.py::TestRealThresholdLatency -p no:cacheprovider
```

实测输出（2026-11-05，本机一次运行，30 passed / 0 failed，263.81s）：
每档打印一行 `[weak] case=... idle=... stale=... disc=... max_gap=... elapsed=...`，
半开打印 `[weak] half-open kind=... idle=... stale=... hard=... category=idle`；
上表逐字取自这次输出。

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

**已验证（假源、可复跑，41 个用例）**

- rtl_tcp：正常断开 / 进程被杀 / 源变慢+停发 / 半开（代理保 TCP）；恢复首帧、
  静噪链重建、TCP socket 回基线、生产者线程不累积。
- SpyServer：正常断开 / 进程被杀 / 半开；握手与读错误语义差异、采样率不匹配可见。
- 超时与重试：默认 5s / 5s / 10s；放弃预算 61s + 6×5s = 91s；
  无无限重试、无静默放弃；`retry_now()` 可人工恢复。
- 弱网矩阵（§0）：四类 13 档 × 5s/3s 两阈值，逐档数 stale/disconnected 次数；
  半开在 5s/10s 与 3s/6s 两种真实阈值下各测 rtl_tcp / SpyServer。
  结论「保持 5s/10s」只对**本机假服务 + 本机回环**成立。

**未验证（必须真机/真实网络，未放宽断言）**

- 真实 rtl_tcp / 真实 SpyServer 服务端进程；真实网卡的丢包、乱序、NAT/防火墙
  超时、Wi-Fi 漫游；高延迟（>5s RTT）链路下的心跳误判率。
- 真机 USB 热插拔的句柄计数已在既有回归中验证；本文件的 socket 计数只覆盖网络源。
- 进程句柄总数（`psutil.num_handles`）在假服务同进程运行时含服务端 worker 线程
  句柄，实测 371→382 有噪声；因此泄漏判据用的是**存活 TCP socket 数**
  （基线 1 → 3 轮重连后 1），不是进程句柄总数。真实环境请用外部句柄工具复核。

## 6. 已知无关失败

`tests/test_orphan_connection.py::test_constellation_cleared_resets_stats` 在本次
定向回归中失败：期望 `信号: --`，实际 `信号: —`。该文案改动来自桌面线
（`desktop/main_window.py`，本线未触碰），不计入本主题结论。
