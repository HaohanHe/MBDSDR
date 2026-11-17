# SDR 自诊断与「连得上但 0 样本」排查

> 适用日期：2026-11-18 · 脚本：`scripts/sdr_diag.py` · 测试：`tests/test_sdr_diag.py`
> 适用设备：走统一 `DeviceManager` 的 RTL-SDR（pyrtlsdr）、SoapySDR、rtl_tcp 源。

## 1. 什么时候用

典型症状：

- 设备能被枚举、能连接、能调谐，但一读样本就超时或返回空；
- 官方 `rtl_sdr.exe` / `rtl_test` 同样拿不到数据；
- 不知道问题在「本软件」「驱动」「USB」「设备本身」哪一层。

`scripts/sdr_diag.py` 不解调、不出频谱，只回答一个问题：**从枚举到拿到
第一个样本，五段链路里哪一段断了**。信号质量与频谱请用
`scripts/rtl_selfcheck.py`。

## 2. 用法

```powershell
# 默认：第一个非 debug 设备，100.1 MHz / 2.4 MS/s / 2 s 有界读
python scripts/sdr_diag.py

# 指定设备（device_key 口径）
python scripts/sdr_diag.py --device rtlsdr:77771111153705700

# 调参数
python scripts/sdr_diag.py --freq 100100000 --rate 2400000 --timeout 2.0

# 机器可读：stdout 只有 JSON（日志/告警走 stderr）
python scripts/sdr_diag.py --json
```

参数说明：

| 参数 | 默认 | 含义 |
|------|------|------|
| `--device` | 空（第一个非 debug 设备） | 支持 `device_key`（`rtlsdr:SN`）、设备名、序列号、枚举序号 |
| `--freq` | `100100000` | 中心频率 Hz |
| `--rate` | `2400000` | 采样率 Hz |
| `--timeout` | `2.0` | 单次有界读等待上限（秒）；子进程总上限 = 该值 + 5 s 启动宽限 |
| `--json` | 关 | 输出 `stages[]` + `verdict`，不附加建议文本 |
| `--force` | 关 | 忽略启动预检（确认本机没有卡死诊断实例时才用） |

## 3. 五段诊断的含义

顺序固定，任何一段失败即停止，后续段不会伪造 PASS。

| # | `name` | 检查什么 | PASS 说明 | 失败说明 |
|---|--------|----------|-----------|----------|
| 1 | `enumerate` | `DeviceManager.list_devices()` | 至少有一台真实设备，且能选中 `--device` | 看不到设备 / 找不到指定设备 |
| 2 | `claim` | 只读查询跨进程占用登记 `get_owner_state` | 登记可读且无人持有（`free`） | 被占用（`owned`）或登记不可读（`unknown`） |
| 3 | `open` | `DeviceManager.open(info)`（此步才登记占用） | 驱动成功打开设备 | 驱动/权限/占用导致打不开 |
| 4 | `tune` | `set_sample_rate` + `set_frequency` | 配置下发成功；能回读时附驱动回读值 | 频率/采样率不支持或驱动失败 |
| 5 | `read` | `BoundedReader` 包住 `read_samples(256)` | `--timeout` 内读到 256 个样本（512 字节 cu8） | 超时、空数组或读异常 |

关键边界：

- 第 2 段是**只读**占用查询，不抢占；第 3 段 `open()` 成功才写入占用，
  第 5 段结束（无论成败）在 `finally` 里 `close()`，同时归还占用登记。
- 第 5 段复用 `mbdsdr_ai.bounded_read.BoundedReader`：pyrtlsdr 的
  `read_bytes` 没有 timeout 形参，真机实测可十几秒不返回；本脚本最多等
  `--timeout` 秒，不会把终端挂死。
- **设备 I/O 全程在子进程里做**（`--_worker` 隐藏入口），父进程只等待、
  不打开设备。父进程最多等 `--timeout + 5 s`（启动宽限），超时只对
  **子进程句柄** `terminate()`，再等 2 s；子进程仍存活 → 结果标
  `residual=true` 并给出 `worker_pid` 与「重启系统或拔插设备释放 USB
  句柄」。父进程本身干净退出，不会把解释器挂死。
- 真机 0 样本时，librtlsdr 的同步读会卡在驱动里（`libusb_bulk_transfer`
  的 timeout=0）：进程可能显示 `HasExited=True` 却仍在烧 CPU，`taskkill`
  报「no running instance」——**这种进程只能靠重启系统回收**。所以脚本
  启动前会先做预检（见下一条），不再往上堆。
- 预检：启动时扫描本机是否已有卡住的诊断实例（按 Python 解释器 + 脚本
  参数精确匹配，避免误判测试文件）。命中即拒绝启动、打印 PID 与处置
  建议，退出码 `5`；确认可以继续时加 `--force`。
- 脚本不启动热插拔监控线程，读线程是 daemon。

## 4. 退出码

| 退出码 | 含义 | 结论 |
|--------|------|------|
| `0` | 五段全部通过 | 设备能正常出样本 |
| `1` | 无设备 | 枚举为空或 `--device` 不存在 |
| `2` | 设备被占用 / 占用状态无法判断 | 有其它进程持有，或占用登记不可读 |
| `3` | 连接/调谐失败 | 打开了但配置失败（打开本身失败也归此类） |
| `4` | 无样本 | 已连接并配置成功，`--timeout` 内没读到样本 |
| `5` | 预检拒绝 | 本机已有卡住的诊断实例；先按提示结束/重启，确认可继续加 `--force` |

`--json` 的 `verdict` 取值：`ok` / `no_device` / `device_busy` /
`owner_unknown` / `connect_failed` / `tune_failed` / `no_samples`；子进程
超时且无完整结果时为 `worker_timeout` / `runner_error`（退出码 4），
`residual=true` 表示子进程终止失败、需重启系统。

## 5. `--json` 输出结构

```json
{
  "device": "RTL-SDR #0 (FC0012) [rtlsdr:77771111153705700]",
  "stages": [
    {"name": "enumerate", "ok": true,  "detail": "发现 1 台，选中 …", "elapsed_ms": 677.7},
    {"name": "claim",     "ok": true,  "detail": "设备空闲（占用登记可读，无持有者）", "elapsed_ms": 0.6},
    {"name": "open",      "ok": true,  "detail": "已打开 …", "elapsed_ms": 528.0},
    {"name": "tune",      "ok": true,  "detail": "已下发 采样率 2.400 MS/s、中心频率 100.100 MHz；驱动回读 2.400 MS/s、100.100 MHz", "elapsed_ms": 79.7},
    {"name": "read",      "ok": false, "detail": "未拿到样本（上限 2.0 s）：2.0 s 内读 512 字节（256 样本）无返回", "elapsed_ms": 2012.7}
  ],
  "verdict": "no_samples",
  "exit_code": 4
}
```

- `stages` 按固定顺序排列；**只包含实际执行过的段**，上游失败时后续段不出现；
- `detail` 是给人看的证据文本，不保证跨版本逐字稳定；`name`/`verdict`/
  退出码才是机器可依赖的契约。

## 6. 本次真机证据（2026-11-18）

设备与配置：

- `rtlsdr:77771111153705700`（RTL-SDR #0 / FC0012）
- 100.1 MHz / 2.4 MS/s / 有界读上限 2.0 s

实跑结果（`--json` 一次）：

| 段 | 结果 | 证据 |
|----|------|------|
| 枚举 | PASS | 发现 1 台，选中 `rtlsdr:77771111153705700`（677.7 ms） |
| 占用登记 | PASS | 「设备空闲（占用登记可读，无持有者）」 |
| 连接/打开 | PASS | `open()` 成功（528.0 ms） |
| 调谐/采样率 | PASS | 驱动回读 2.400 MS/s、100.100 MHz（79.7 ms） |
| 有界读样本 | **FAIL** | 2.0 s 内读 512 字节（256 样本）无返回；耗时 2012.7 ms |

结论：`verdict=no_samples`，退出码 `4`。诊断结束后复查占用登记仍为
`free`（占用登记已归还），**但进程并未真正消失**——见下条。

**后续事故（同日晚些时候复核发现）**：这台机器上累积了 5 个残留 python
进程（3 个 `sdr_diag.py` / `system_diag.py`，2 个内联 `device_manager`
读样本脚本），`HasExited=True`、各自的 CPU 时间仍在每秒 +1 s，`Stop-Process
-Force` 报成功却杀不掉，`taskkill /F` 报「There is no running instance of
the task」。原因：旧版脚本在自身进程内 `TerminateProcess`，而 librtlsdr 的
同步读卡在驱动里，内核无法回收该线程。这些进程很可能一直占着 RTL-SDR 的
USB 句柄。**处置：重启系统**（拔插设备不一定够）。此后的版本已改为设备
I/O 子进程隔离 + 启动预检（退出码 5），不会再生新残留；已存在的残留仍需
重启清理。

同期旁证：

- 官方 `rtl_sdr.exe -f 100100000 -s 2400000 -n 256 out.bin` 在 8 s 内
  未退出，输出文件 **0 字节**（本次复测；此前复现同样 0 字节）；
- 任务前置复现记录：USB 枚举、WinUSB 绑定、控制传输均正常，但
  `read_bytes` 与官方工具都拿不到数据流。

## 7. 为什么不能据此断定「软件坏了」

1. **控制通路已证明是通的**：第 3、4 段成功，说明 libusb/WinUSB 枚举、
   打开设备、下发采样率与中心频率都生效，驱动回读也一致。0 样本发生在
   数据流端点之后，而不是配置阶段。
2. **两条独立调用路径得到同一结果**：本脚本最终只调用 librtlsdr 的
   `read_bytes`，官方 `rtl_sdr.exe` 走的是同一驱动层的同步读。两者都
   0 字节，故障点在两者共有的下游（USB 数据通路 / 驱动绑定 / 供电 /
   设备固件或射频前端），而不是 MBDSDR 的解调、解码或业务逻辑。
3. **但也不能反过来说「设备一定坏了」**：USB 口/线接触不良、HUB 供电
   不足、USB3 兼容性、驱动虽装上但数据端点异常、其它进程占用数据流，
   都会造成完全相同的症状。当前证据只能把范围收窄到「数据通路层」，
   不能定位到具体元件。
4. **脚本本身不会把失败伪装成成功**：无样本返回空数组或超时，脚本如实
   标 FAIL、退出码 4；`BoundedReader` 保证不会无限阻塞；失败路径也会
   `close()` 设备并归还占用。设备 I/O 在子进程里，父进程不碰设备；
   子进程终止失败会标 `residual=true`，不谎报「已清理」。
5. **2 秒超时是单次观测**：偶发丢包、设备冷启动、宿主 USB 调度抖动都
   可能让单次读失败。应重复运行同一命令 2–3 次，并与 `rtl_test` 的
   丢包统计对照，再下结论。

## 8. 建议的排查顺序

1. **换口/换线**：优先主板直连的 USB2 口，绕开 HUB 与延长线；再跑
   `python scripts/sdr_diag.py`。
2. **排除占用**：关闭 SDR#、GQRX、CubicSDR、`rtl_fm`/`rtl_tcp`、另一个
   MBDSDR 实例；确认第 2 段为 `free`。
3. **确认驱动**：Windows 用 Zadig 将 RTL2832U 装成 WinUSB；Linux 黑名单
   `dvb_usb_rtl28xxu` 后重新插拔。
4. **官方工具交叉验证**：
   ```powershell
   rtl_test.exe -s 2400000
   rtl_sdr.exe -f 100100000 -s 2400000 -n 256 out.bin
   ```
   官方工具同样无数据 → 问题在驱动/USB/设备，不在本软件；官方工具能出
   数据 → 回到本软件的打开/读取参数继续查。
5. **换主机/系统验证**：同一根棒在另一台机器上复测，可区分「棒/线」与
   「本机 USB 控制器/驱动」。
6. **仍无数据**：保留 `--json` 输出、`rtl_test` 输出与 USB 拓扑信息，
   按硬件故障流程处理，不要继续改软件参数。

## 9. 边界与不做什么

- 不做信号质量、频谱、解调判断（用 `scripts/rtl_selfcheck.py`）；
- 不设置增益/ppm，不判断弱信号或天线问题；
- 不自动抢占被其它进程占用的设备，也不清理别人的占用登记；
- 不替代 `rtl_test` 的丢包/过载统计。

## 10. 自动化测试

```powershell
python -m pytest tests/test_sdr_diag.py tests/test_diag_residual_guard.py -q
```

使用假设备与假占用登记，不触碰硬件；覆盖：正常五段退出 0、无设备退出 1、
被占用退出 2、无样本退出 4、`--json` 字段与段顺序；子进程超时按时终止、
终止失败标 `residual`、预检识别残留并以退出码 5 拒绝启动。

## 11. 一条命令同时跑 SDR + GNSS（`scripts/system_diag.py`）

`scripts/system_diag.py` 把两侧聚合成一条命令：SDR 侧复用
`scripts/sdr_diag.py::run_in_subprocess()`（设备 I/O 仍在 `sdr_diag` 的
隐藏子进程里，聚合层父进程不碰设备），GNSS 侧复用
`scripts/gnss_diag.py::run_diagnostics()`，不重新实现任何一段，也不
触碰产品代码。设备与串口仍由各自脚本在 `finally` 里关闭/归还；聚合层
不持有任何句柄。SDR 侧同样受启动预检约束（有残留实例即以退出码 5 拒绝）。

```powershell
# 自动选第一个非 debug SDR 设备 + 按 9600 自动探测 GNSS 串口
python scripts/system_diag.py

# 显式指定两侧设备
python scripts/system_diag.py --sdr-device rtlsdr:77771111153705700 --gnss-port COM4

# 机器可读：stdout 只有 JSON
python scripts/system_diag.py --json

# 两侧共用等待上限（不指定则各用自身默认：SDR 2.0 s / GNSS 10.0 s）
python scripts/system_diag.py --timeout 5
```

| 参数 | 默认 | 含义 |
|------|------|------|
| `--sdr-device` | 空（第一个非 debug 设备） | 同 `sdr_diag.py --device` |
| `--gnss-port` | 空（按 9600 自动探测） | 同 `gnss_diag.py --port` |
| `--timeout` | 空 | 两侧共用的单段等待上限；空表示各用自身默认 |
| `--json` | 关 | 输出结构化 JSON，不附加建议文本 |

总体判定**只看两侧退出码**，GNSS 天线 `OPEN`/`SHORT` 只是 warning，
不会把 GNSS 侧或总体判成失败：

| 总体 `verdict` | 条件 | 总体退出码 |
|----------------|------|-----------|
| `ok` | 两侧退出码都是 0 | `0` |
| `degraded` | 一侧 0、另一侧非 0 | `2` |
| `failed` | 两侧都非 0 | `1` |

`--json` 顶层字段：

```json
{
  "sdr":  {"verdict": "no_samples", "exit_code": 4, "stages": []},
  "gnss": {"verdict": "ok", "exit_code": 0, "stages": [], "antenna": {}},
  "verdict": "degraded",
  "exit_code": 2,
  "summary": "SDR：无样本（硬件数据通路）；GNSS：定位正常（天线开路）"
}
```

- `sdr` / `gnss` 是两侧原始结果的完整投影，各自的 `verdict`、`exit_code`、
  `stages` 语义与单脚本一致（见本文第 3、4 节与 `docs/gnss-diagnosis.md`）；
- `summary` 是给人看的一句话；机器判定请用 `verdict` / `exit_code`；
- 单侧崩溃（脚本自身异常）会记成该侧 `verdict=runner_error`、退出码 1，
  另一侧照常执行，不会被吞掉。

GNSS 侧的段含义、退出码与天线告警口径见 `docs/gnss-diagnosis.md`。

自动化测试（假结果，不碰硬件）：

```powershell
python -m pytest tests/test_system_diag.py -q
```

覆盖：两侧 ok → 0、一侧失败 → 2、两侧失败 → 1、天线 warning 不计失败、
`--json` 顶层字段、单侧崩溃不吞另一侧。
