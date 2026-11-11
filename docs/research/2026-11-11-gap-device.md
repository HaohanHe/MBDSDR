# 设备连接 / 能力协商 / 空态诚实性 —— 只读缺口调研

- 日期：2026-11-11
- 范围：`desktop/device_panel.py`、`desktop/control_panel.py`、`desktop/module_panel.py`（面板落点）、`desktop/main_window.py`（连接/回读路径）、`desktop/status_panel.py`（读数落点）、`mbdsdr_ai/sdr_backend.py`、`mbdsdr_ai/device_capabilities.py`、`mbdsdr_ai/rtlsdr_params.py`、`mbdsdr_ai/rtltcp_client.py`、`mbdsdr_ai/spyserver_client.py`、`mbdsdr_ai/bladerf_params.py`、`mbdsdr_ai/reconnection.py`、`mbdsdr_ai/module_graph.py`
- 性质：**只读**。未修改任何产品代码。
- 产物：本文件。

## 0. 方法与证据等级

本机没有连接真机，全部结论来自静态调用链与离线 Python 计算，**不含真机实测**。文中逐条标注：

| 标记 | 含义 |
|---|---|
| 〔读码〕 | 已沿调用链读通，路径确定，但未运行 |
| 〔离线〕 | 在无硬件环境用 Python 直接调用该函数，输出已复核 |
| 〔推演〕 | 依赖第三方库/远端服务器的实际行为，未验证 |

能力矩阵里的“可用/禁用”指**代码会不会挡**，不等于“设备真的支持”。

已确认的既有覆盖（本轮不重复立项）：硬件被拒后增益/带宽/PPM/Offset/AGC/采样率/PTT/录音/天线真值回填已做过一轮；`_humanize_error` 已存在；PTT 走 `tx_capability` 三态（`main_window.py:374-400` 一带）；状态栏无后端时统一 `—`（`main_window.py:4861-4891`）。

---

## 1. 能力 × 控件矩阵

图例：● 可用　◐ 可用但显示/状态与真值不符　○ 已禁用　⊘ 无控件或隐藏　— 不适用

列：
- A = RTL-SDR 本地 USB（`RTLSDRBackend`）
- B = rtl_tcp（桌面入口 `RtlTcpBackend`；env 登记走 `RTLSDRBackend` + `RtlTcpDeviceAdapter`，差异见注）
- C = SpyServer（`SpyServerBackend`，仅 env / 非桌面入口）
- D = BladeRF（`BladeRFBackend`）
- E = SoapySDR 通用（`SoapySDRBackend`）

> 所有格子的“依据”是该行为的代码位置；链接统一写 `path:line`。

| # | 控件 / 显示项 | A RTL 本地 | B rtl_tcp | C SpyServer | D BladeRF | E Soapy |
|---|---|---|---|---|---|---|
| 1 | 中心频率输入/调谐 | ● `control_panel.py:278-301`、`sdr_backend.py:350` | ● `rtltcp_client.py:315` | ● `spyserver_client.py:568` | ● `bladerf_params.py:500` | ● `sdr_backend.py:350` |
| 2 | 采样率下拉 | ● 真实离散档 `control_panel.py:1541-1595` | ● 吸附后下发 `rtltcp_client.py:782-802` | ◐ 只有 225.001 kHz / 3.2 MS/s 两个端点，且量程是硬编码 RTL 值 `spyserver_client.py:467`〔读码〕 | ◐ 量程 52.0834 k–61.44 M；下拉刷新依赖 `backend.device`，见 G01 `bladerf_params.py:90-100`、`main_window.py:6105`〔读码/推演〕 | ◐ 能力未知时弹 1 kHz / 61.44 MHz 占位端点 `sdr_backend.py:3073`、`device_capabilities.py:250`〔离线〕 |
| 3 | 采样率读数（状态栏） | ● 吸附后真值 `sdr_backend.py:487`、`2327` | ● 吸附后真值 `rtltcp_client.py:795-802` | ◐ = 请求值，服务器未确认 `spyserver_client.py:579-588` | ◐ = 请求值 `bladerf_params.py:524` | ◐ = 请求值（忽略 `setSampleRate` 返回值）`sdr_backend.py:3480-3489` |
| 4 | 增益滑杆范围 | ◐ 固定 0–49.6，非 R820T 调谐器真实上限更低 `sdr_backend.py:1643` | ◐ 固定 0–49.6 `rtltcp_client.py:636` | ◐ 固定 0–49.6（Airspy 等并非 49.6）`spyserver_client.py:468` | ◐ 收放代码在 try 内，缺 `.device` 时保持 0–49.6 兜底 `main_window.py:6110-6119` | ◐ 未知时退回 0–49.6 兜底 `main_window.py:6111-6119` |
| 5 | 增益读数 | ◐ 显示请求值，非吸附后硬件值 `sdr_backend.py:533` + `2381-2388` | ◐ 显示请求值 `rtltcp_client.py:804-809` | ◐ 显示请求值 `spyserver_client.py:590-597` | ◐ 恒为 `—`（缺 `get_gain`）`main_window.py:6720-6732` | ◐ 显示请求值 `sdr_backend.py:3491-3500` |
| 6 | AGC 复选 | ● 数字 AGC + 增益模式 `sdr_backend.py:2453-2477` | ◐ 桌面入口数字 AGC 空操作却返回成功 `sdr_backend.py:575-581`；env 适配器同 | ◐ **完全空操作**（`SpyServerClient.set_agc` 存在但后端从不调用）`spyserver_client.py:303` vs `575-597` | ○ 无 `set_agc`：连接时报“AGC 未生效”，勾选时提示不支持 `main_window.py:6986` | ● `sdr_backend.py:3502-3510` |
| 7 | 硬件带宽（模块面板） | ◐ 下发后状态=请求值，驱动是否吸附未知 `sdr_backend.py:607`、`2341` | ◐ **空操作返回成功** `rtltcp_client.py:559-566`（env 适配器同） | ◐ **空操作返回成功**（无 `_apply_bandwidth`）`sdr_backend.py:575-581` | ● 真实回读 `bladerf_params.py:543-557` | ● `sdr_backend.py:3512-3526` |
| 8 | PPM | ● `sdr_backend.py:2479` | ● 桌面入口 `rtltcp_client.py:811-816`；env 适配器经 `freq_correction` 可用 | ◐ 无 `set_ppm` → 连接后报“PPM 未生效” `main_window.py:6979-6981` | ◐ 同 C | ◐ 同 C |
| 9 | 偏移调谐 | ◐ **控件常开、勾选后假开**（驱动无该方法仍写状态）`sdr_backend.py:2551-2558` | ◐ 桌面入口：方法缺失 → 提示“不支持”并回退（诚实）`main_window.py:7599-7604`；env 适配器：返回 True 空操作（假）`rtltcp_client.py:585-589` | ○ 无该方法 → 连接时报“Offset Tuning 未生效” `main_window.py:6982-6985` | ○ 同 C | ○ 同 C |
| 10 | 直接采样 / 偏置供电 | ⊘ 无 UI（能力代码存在）`sdr_backend.py:2510`、`2527` | ⊘ 无 UI；env 适配器返回 True 空操作 `rtltcp_client.py:576-584` | ⊘ | ⊘ | ⊘ |
| 11 | 天线选择 | ⊘（单天线） | ⊘ | ⊘ | ⊘ | ⊘ **后端有 `set_antenna` 但无 UI** `sdr_backend.py:3527` |
| 12 | TX / PTT | ⊘ `sdr_backend.py:1645` | ⊘ `rtltcp_client.py:638` | ⊘ `spyserver_client.py:470` | ⊘ | ⊘ **硬编码 False** `sdr_backend.py:3076`（Lime/USRP 经 Soapy 也看不到 TX） |
| 13 | 录音 | ● | ● | ● | ◐ 取 `backend.status.gain_db` 抛错被吞，录音增益静默记 0 dB `main_window.py:9554-9557` | ● |
| 14 | 设备名/连接状态 | ● `main_window.py:6088-6090` | ● | ● | ○ **连接成功路径 `backend.device.name` 未加保护，抛出后本槽后续代码全不执行** `main_window.py:6105` | ● |
| 15 | 空态：频率 | ● `—` `main_window.py:4870`、`control_panel.py:350-368` | ● | ● | ● | ● |
| 16 | 空态：采样率 | ◐ 组合框显示 **2.400 MS/s** 且不标默认/未确认 `control_panel.py:577-578`、`1573-1579`；状态栏为 `—` `main_window.py:4871`；模块面板为“未知（默认 2.400 MS/s）”`module_panel.py:584-587` | 同 A | 同 A | 同 A | 同 A |
| 17 | 空态：LNA | ● `—` `control_panel.py:627`、`1453` | ● | ● | ● | ● |
| 18 | 空态：PPM | ◐ 显示 `0` `control_panel.py:669-672` | ◐ | ◐ | ◐ | ◐ |
| 19 | 空态：静噪 | ◐ 显示 `6 dB` `control_panel.py:723` | ◐ | ◐ | ◐ | ◐ |
| 20 | 空态：硬件带宽 | ◐ 显示 `150000 Hz` 且**始终可编辑** `module_panel.py:369`、`419-420` | ◐ | ◐ | ◐ | ◐ |

> 注 1：B 列有两条实现路径。桌面“远程 rtl_tcp 源”走 `RtlTcpBackend`；env 登记的网络源走 `RTLSDRBackend(host=…)` + `RtlTcpDeviceAdapter`。两者对 Offset/直采/偏置的诚实度不同，矩阵中已分别标注。
> 注 2：C 列（SpyServer）当前**没有桌面入口**，只能经 env / 管理器进入；见 §6 G14。

### 1.1 读数真值来源（第二张表：谁在“冒充已生效”）

| 读数 | 值从哪来 | 是否等于硬件真值 | 依据 |
|---|---|---|---|
| 采样率（RTL 本地 / rtl_tcp） | `_resolve_applied_sample_rate` 的吸附值 | 是（吸附档） | `sdr_backend.py:487`、`2327`；`rtltcp_client.py:795-802` |
| 采样率（SpyServer / Soapy / BladeRF） | 基类默认 = 请求值 | **否** | `sdr_backend.py:502`、`3480-3489`；`spyserver_client.py:579-588` |
| 增益（全部后端） | 请求值 | **否**（离散调谐器 / Soapy 驱动会吸附） | `sdr_backend.py:533`；`3491-3500` |
| 带宽（RTL 本地） | 请求值 | **未验证** | `sdr_backend.py:607` |
| 带宽（rtl_tcp / SpyServer） | 请求值 | **否**（底层空操作） | `rtltcp_client.py:559-566`；`sdr_backend.py:575-581` |
| PPM（全部） | 最后一次被接受的请求值 | 协议无回读，**未验证** | `control_panel.py:1492-1496` 自述 |
| Offset Tuning（RTL 本地） | 请求值 | **否**（驱动无该方法） | `sdr_backend.py:2551-2558` |
| 设备名 | `backend.device.name` | 是（SDRBackend 子类） | `main_window.py:6088-6090` |

---

## 2. “冒充已生效”逐处清单

> 判据：UI / 状态栏把一个**未经硬件确认**的值显示成已生效，且用户没有可见提示。

| 编号 | 位置 | 现象 | 用户可见后果 |
|---|---|---|---|
| A1 | `sdr_backend.py:2551-2558` + `status_panel.py:715-719` | `set_offset_tuning()` 在检查驱动能力**之前**就写 `self._offset_tuning = enabled`；本机驱动无 `set_offset_tuning` 时只记 debug 并 `return True` | 勾选“偏移调谐”→ 勾保持、状态面板显示“开”，硬件完全没动〔读码〕 |
| A2 | `sdr_backend.py:533` + `2381-2388` + `control_panel.py:1488` + `main_window.py:7182` | `_apply_gain` 把请求值吸附到离散档，但 `status.gain_db` 仍写请求值；`get_gain()` 返回它；`set_gain_applied` 判定“相等”后提示“读数来自设备回执” | 非 R820T 调谐器（如 FC0012 最高 19.2 dB）请求 30 dB，UI 显示 30 dB，硬件实际 19.2 dB，还声称是回执〔读码〕 |
| A3 | `sdr_backend.py:3480-3500` | Soapy 的 `setSampleRate()` 返回值（实际速率）被丢弃；`setGain()` 后不回读 `getGain()`；基类 `_resolve_applied_sample_rate` 返回请求值（`sdr_backend.py:502`） | 经 Soapy 接 RTL/其他设备时，UI 采样率/增益可能长期偏离硬件，且状态栏“配置请求 X 未生效”判据也失效〔读码〕 |
| A4 | `rtltcp_client.py:559-566` | 适配器的 `bandwidth` setter 空实现；`getter` 恒返回 0 | 模块面板“硬件带宽 150 kHz”下发“成功”，设备实际带宽未变〔读码〕 |
| A5 | `rtltcp_client.py:576-589` | 适配器 `set_agc_mode` / `set_direct_sampling` / `set_bias_tee` / `set_offset_tuning` 全部 `return True` 空操作 | env 登记的 rtl_tcp 源上，AGC/直采/偏置/偏移都“设置成功”但无效；仅 Offset 有 UI 入口，其余为工具/脚本面〔读码〕 |
| A6 | `spyserver_client.py:579-588`、`575-597` | 采样率/增益只“发出命令”即成功；服务器是否接受、实际跑多少，不回读；`connect()` 还无条件下发 98 MHz + 20 dB（`519-521`） | 服务器拒绝或改了档位时，UI 仍显示请求值；首次连接短暂出现用户没设过的 98 MHz/20 dB〔读码〕 |
| A7 | `spyserver_client.py:303` vs `575-597` | 客户端有 `set_agc`，后端没有 `_apply_agc`，基类空操作返回 True | AGC 勾选显示“已开启”，服务器从未收到增益模式命令〔读码〕 |
| A8 | `device_capabilities.py:126-128`、`250` + `sdr_backend.py:3073` | `capability_status="unknown"` 但带占位量程时，被**强制降级为 `declared`**；`sample_rate_options_for_backend` 更是无条件写 `declared` | 未知能力被显示成“1.00 kHz–61.4 MHz（声明）”，采样率下拉给出 1 kHz / 61.44 MHz 两个假档〔离线〕 |
| A9 | `main_window.py:6979-6985` | 连接后无条件调用 `backend.set_ppm()` / `set_offset_tuning()`；BladeRF/Soapy/SpyServer/Pluto 等没有这两个方法 → `AttributeError` → 记为“未生效” | 每次连这类设备都弹“连接后参数未全部生效：PPM、Offset Tuning”，而这不是失败，是设备没这个能力〔读码〕 |
| A10 | `module_panel.py:366-374`、`419-420`、`639-641` | `bw_spin` 从不禁用，但只在连接时读取一次；连上后改它没有任何下发 | 用户连上后调“硬件带宽”，界面接受输入但设备不变〔读码〕 |

---

## 3. 空态诚实性：目前有三套标准

| 显示项 | 未连接时显示 | 是否符合“未知就 `—`” | 依据 |
|---|---|---|---|
| 状态栏 频率/采样率/带宽/增益/设备/信号 | `—` | 是（金标准） | `main_window.py:4861-4891` |
| 控制面板 LNA | `—`（tooltip 写“上次配置 X dB（未施加）”） | 是 | `control_panel.py:627`、`1453` |
| 控制面板 频率 | `--` / 清空 | 是 | `control_panel.py:350-368` |
| 控制面板 采样率 | `2.400 MS/s` 正常文本 | **否**：具体值、无“默认/未确认” | `control_panel.py:577-578`、`1573-1579` |
| 控制面板 PPM | `0` | **否**：像“当前校正=0” | `control_panel.py:669-672` |
| 控制面板 静噪 | `6 dB` | 部分（是配置意图，但未标“默认”） | `control_panel.py:723` |
| 模块面板 采样率 | “未知（默认 2.400 MS/s）” | 是（唯一把“默认”写进文本的） | `module_panel.py:584-587` |
| 模块面板 硬件带宽 | `150000 Hz`，且可编辑 | **否** | `module_panel.py:369`、`419-420` |
| 模块面板 频率 | `100.0000 MHz`，禁用 + 提示“不是设备读数” | 部分 | `module_panel.py:321-331` |
| 设备面板 采样率/增益/PPM 输入框 | `1.024 MS/s` / `30` / `0` | 输入框（配置意图），可接受；但与模块面板的 2.400 冲突 | `device_panel.py:185`、`199`、`204`；`perf_profile.py` 默认 eco |

**结论**：同一块窗口里，采样率有 `2.400 MS/s`（控制面板）、`2.400 MS/s`（模块面板提示）、`1.024 MS/s`（设备面板）三个“默认”，只有模块面板明确写了“默认”。这是本轮最容易被用户截图质疑的点。

---

## 4. 热插拔与重连

| 事件 | 现状 | 评价 | 依据 |
|---|---|---|---|
| USB 拔出 | 2 秒轮询发现 → `device_unplugged` → `_disconnect()` → `_teardown_connection()`，停 ring/录音/扫频，面板置未连接，状态栏全 `—` | 主链路回退完整 | `device_panel.py:321-366`、`main_window.py:6739-6760` |
| 断开后控件残留 | 频率/模式/录音已清；**采样率文本、PPM 数值、静噪 `6 dB`、AGC/Offset 勾选、硬件带宽 150 kHz 仍保留** | 两套标准 | `control_panel.py:350-368`、`311`；`module_panel.py:419-420` |
| 重新插入 | 枚举恢复、可再次连接；**不自动重连** | 已在 UI 明说，属产品选择 | `main_window.py:8492-8496` |
| 数据流中断（STALE） | 10 秒按断开处理；提示“不会自动重连” | 诚实 | `main_window.py:3285-3286` |
| `ReconnectionManager` | 实现了退避/重试，但**桌面端没有任何引用**，只有测试/脚本用 | 能力闲置 | `mbdsdr_ai/reconnection.py`（全仓无 desktop 引用） |
| 重连后的“最后生效值” | `_last_ppm_applied` / `_last_offset_applied` 断开时不清，重连由 `_apply_connect_params` 重写 | 基本正确 | `main_window.py:6979-6985` |

---

## 5. 错误提示与连接耗时

| 项 | 现状 | 评价 | 依据 |
|---|---|---|---|
| 连接失败文案 | `_humanize_error` 把常见错误翻成人话（含 rtl_tcp 的 host/port/防火墙建议） | 好 | `main_window.py:6486-6520` |
| 能力缺失被报成失败 | 非 RTL 后端每次连接都提示“PPM、Offset Tuning 未生效”；BladeRF/Lime 还多一条“AGC 未生效” | **误导**：应显示“该设备不支持”，或干脆不调用 | `main_window.py:6979-6992` |
| 连接阻塞 | `connect()` 在 GUI 线程同步执行；注释自承本机原生 RTL 约 2.0 s，仅 `processEvents()` 刷一次 | 中等：连接 + 网络探测期间窗口无响应 | `main_window.py:6025` 附近 |
| 网络源入口 | 远程 rtl_tcp 用 `QInputDialog.getText` 弹模态框输入 host:port | 体验差、无法预配置/记住多源 | `main_window.py:6387-6389` |
| SpyServer 入口 | 桌面完全无入口 | 后端能力不可达 | `desktop/` 无 `spyserver` 引用（仅帮助文本） |
| 蓝牙/USB 驱动缺失 | 有任务指引（Zadig 等） | 好 | `main_window.py:267` |

---

## 6. 缺口明细

> 严重度：P0 = 崩溃/核心功能不可用；P1 = 严重误导或核心能力缺失；P2 = 一般；P3 = 打磨。
> 成本：低（<0.5 人日）/ 中（0.5–2 人日）/ 高（>2 人日）。

### G01（P0）BladeRF / LimeSDR 连接成功路径按 SDRBackend 使用，`.device` 不存在
- 现象：两者是独立类（`bladerf_params.py:346`、`limesuite_params.py:367`），没有 `.device` / `.status`。`main_window.py:6105` **在 try 之外**直接 `backend.device.name`；`6183`（回滚）、`3292`、`9555` 是同类访问但包了 try，只是静默降级（设备名“原设备”、录音增益记 0）。
- 影响：BladeRF/Lime 连接成功后 `6105` 抛 `AttributeError`，该槽函数从“连接成功处理”中途退出——后续增益范围收放、默认频率下发、`_apply_connect_params` 全部不执行。用户侧表现为“显示已连接但所有配置都没生效”，且没有错误提示。不是进程崩溃，但核心链路中断。
- 依据：`main_window.py:6105`（未保护）、`6102-6104`（前一个 try 已结束）、`6183`、`3292`、`9554-9557`；`bladerf_params.py:383-387`（只有 `device_identifier`）。
- 成本：低（改为 `getattr(getattr(backend, "device", None), "name", backend.__class__.__name__)`；`.status` 同理）。

### G02（P1）偏移调谐假生效
- 现象：见 A1。驱动不支持时返回成功并把状态写成“开”。
- 影响：用户以为已启用 DC 抵消，实际没有；状态面板与复选框双重确认了假状态。
- 依据：`sdr_backend.py:2551-2558`、`status_panel.py:715-719`、`main_window.py:7591-7616`。
- 成本：低（只在真正下发成功后写 `_offset_tuning`；或增加 `offset_tuning_supported` 供 UI 禁用）。

### G03（P1）增益读数 = 请求值，却提示“来自设备回执”
- 现象：见 A2。吸附结果只用于 `self._sdr.gain`，不回写 `status.gain_db`。
- 影响：所有非 R820T 调谐器（FC0012/FC2580 等）与 Soapy 设备都可能差数 dB 以上；UI 明确声称是回执。
- 依据：`sdr_backend.py:533`、`2381-2388`、`3491-3500`；`control_panel.py:1482-1488`；`main_window.py:7179-7198`。
- 成本：低（`_apply_gain` 内 `self.status.gain_db = snapped`；Soapy 在 `setGain` 后 `getGain()` 回读）。

### G04（P1）Soapy 采样率/增益忽略驱动实际值
- 现象：见 A3。`setSampleRate` 的返回值被丢弃；`setGain` 后不回读。
- 影响：状态栏“采样率”与 DSP 的 `fs_in` 可能和硬件不一致；“配置请求未生效”判据失效。
- 依据：`sdr_backend.py:3480-3500`、`502`。
- 成本：低（使用返回值；或覆写 `_resolve_applied_sample_rate`）。

### G05（P1）连接非 RTL 后端每次都报“PPM、Offset Tuning（BladeRF 还有 AGC）未生效”
- 现象：见 A9。用 `_run` 包 `AttributeError`，再统一汇总成失败。
- 影响：BladeRF/Lime/Soapy/SpyServer/Pluto 用户每次连接都看到红字，误以为配置损坏；BladeRF 额外多一条“AGC 未生效”。
- 依据：`main_window.py:6979-6992`（AGC 在 6986）。
- 成本：低（先 `getattr(..., None)` 判能力，缺能力直接跳过并不计入 failures）。

### G06（P1）空态采样率 `2.400 MS/s` 未标“默认/未确认”，且与设备面板 1.024 MS/s 冲突
- 现象：见 §3。控制面板在未连接时就选中 `DEFAULT_SAMPLE_RATE_HZ`；状态栏却是 `—`。
- 影响：用户把默认值当成设备已生效值；跨面板默认值不一致，质疑数据可信度。
- 依据：`control_panel.py:577-578`、`1573-1579`；`device_panel.py:185`；`module_panel.py:584-587`。
- 成本：低（未连接时文本追加“（默认，未连接）”并置灰；或统一 `—`）。

### G07（P1）SpyServer 能力量程硬编码为 RTL 假设
- 现象：`frequency_range=(500k,1766M)`、`sample_rate_range=(225001,3.2M)`、`max_gain=49.6` 与具体服务器无关。
- 影响：Airspy Mini / HF+ 等服务器会选不到合法频率/速率，或滑杆超出真实范围；采样率下拉只有两个端点档。
- 依据：`spyserver_client.py:462-473`；`device_capabilities.py:242-252`。
- 成本：中（解析服务器 `device_info` 帧；解析不到则显示“未知”，不给端点档）。

### G08（P2）`capability_status="unknown"` 被强制显示为“声明”
- 现象：见 A8，已离线复现：输入 `(1000, 61440000)+unknown` → 输出 `declared`、文本“1.00 kHz–61.4 MHz（声明）”。
- 影响：直接违反 `device_capabilities.py:12-13` 自己写的红线；未知能力被当作已知。
- 依据：`device_capabilities.py:126-128`、`250`；离线输出见 §7。
- 成本：低（保留 `unknown` 优先；`sample_rate_options_for_backend` 读取真实 `capability_status`）。

### G09（P2）rtl_tcp 带宽/AGC/直采/偏置（env 适配器）空操作返回成功
- 现象：见 A4/A5。
- 影响：模块面板“硬件带宽”假生效；工具/脚本面调用直采/偏置会得到假成功。
- 依据：`rtltcp_client.py:559-566`、`576-589`。
- 成本：低（返回 False 或 `NotImplemented`，让上层显示“不支持”）。

### G10（P2）断开后采样率/PPM/静噪/带宽残留具体值
- 现象：`set_connected(False)` 只清频率/模式/录音；其余保持最后一次数值。
- 影响：与状态栏 `—` 形成两套标准；拔掉设备后仍显示“2.400 MS/s / 0 ppm / 6 dB / 150 kHz”。
- 依据：`control_panel.py:311`、`350-368`；`module_panel.py:419-420`。
- 成本：低（断开时统一 `—` 或加“未生效”后缀，保留 tooltip 里的“上次配置”）。

### G11（P2）模块面板“硬件带宽”连上后改了不生效
- 现象：`bw_spin` 只在连接 payload 读取一次（`module_panel.py:639-641`），无 `valueChanged` 下发。
- 影响：用户以为能实时改硬件滤波带宽。
- 依据：`module_panel.py:366-374`、`639-641`。
- 成本：低（连接态接 `valueChanged` → `backend.set_bandwidth`，失败回填）。

### G12（P2）连接在 GUI 线程同步阻塞
- 现象：`connect()` 本地约 2 s，网络源更久；只 `processEvents()` 一次。
- 影响：连接期间窗口“假死”，用户重复点击/误判崩溃。
- 依据：`main_window.py:6025` 附近。
- 成本：中（连接放 QThread；进度与取消）。

### G13（P2）PPM 不支持时回退到 0，而不是上次配置值
- 现象：`_on_ppm_changed` 对无 `set_ppm` 后端提示“不支持”后，把 spin 置 0。
- 影响：用户配置的 30 ppm 被界面抹成 0（配置未改，但显示与配置分叉）。
- 依据：`main_window.py:7559-7589`（`revert = self._last_ppm_applied`，为 None 时落到 0）。
- 成本：低（回填 `_last_ppm_applied` 或配置值，并标注“未施加”）。

### G14（P3）SpyServer 无桌面入口；rtl_tcp 仅模态输入框
- 现象：SpyServer 只能在 env/管理器里用；rtl_tcp 用 `QInputDialog` 输 host:port。
- 影响：桌面用户无法使用网络源；多源无法保存/切换。
- 依据：`desktop/` 无 `spyserver` 引用；`main_window.py:6387-6389`。
- 成本：中。

### G15（P3）偏置供电 / 直接采样 / 天线选择无 UI
- 现象：后端具备（`sdr_backend.py:2510/2527/3527`），桌面无控件。
- 影响：RTL Blog v3 偏置、HF 直采、Soapy 多天线口都不可用。
- 依据：`desktop/` grep 无 `bias_tee|direct_sampling|antenna` 控件。
- 成本：中（控件 + 能力禁用 + 状态回填）。

### G16（P3）Soapy 通用后端硬编码 `supports_tx=False`
- 现象：即使是 TX 能力设备经 Soapy 接入，PTT 也不显示。
- 依据：`sdr_backend.py:3076`。
- 成本：中（按 `getNumChannels(TX)`/`getAntenna` 探测）。

### G17（P3）BladeRF 无增益读数
- 现象：`BladeRFBackend` 无 `get_gain`，但 `status_dict()` 有 `rx_gain_db`；UI 恒显示 `—`。
- 依据：`main_window.py:6720-6732`；`bladerf_params.py:670-680`。
- 成本：低（补 `get_gain`）。

### G18（P3）`ReconnectionManager` 未接线
- 现象：退避重连能力存在但桌面不用；靠用户手点。
- 依据：`mbdsdr_ai/reconnection.py`；desktop 无引用。
- 成本：中（与“不自动重连”的产品口径需先对齐）。

### G19（P3）状态栏“带宽”是解调带宽，不是硬件带宽
- 现象：`status_bw` 取 `_vfo_bw`；硬件带宽在模块面板，二者不联动。
- 影响：用户把“带宽: 12.5 kHz”理解成 RF 滤波带宽。
- 依据：`main_window.py:4948-4952`；`7334`、`7663`。
- 成本：低（改标签为“解调带宽”，或在 tooltip 注明）。

---

## 7. 离线复现记录（无硬件）

命令（在 `D:\mbdsdr\MBDSDR` 下）：

```powershell
python -c "import sys; sys.path.insert(0,r'D:\mbdsdr\MBDSDR'); from mbdsdr_ai.device_capabilities import build_capability_report, sample_rate_options_for_backend; dev={'device_type':'soapy','label':'X','sample_rate_range':(1000.0,61440000.0),'capability_status':{'sample_rate_range':'unknown'}}; print(build_capability_report(dev)['sample_rate_range']); B=type('B',(),{'device':type('D',(),{'sample_rate_range':(1000.0,61440000.0),'device_type':'soapy'})()}); print(sample_rate_options_for_backend(B()))"
```

实测输出（2026-11-11）：

```text
CapabilityField(key='sample_rate_range', status='declared', value=(1000.0, 61440000.0),
                text='1.00 kHz–61.4 MHz（声明）', reason='')
options= [1000, 61440000]
```

→ 证明 A8/G08：输入明确 `unknown`，输出被改成 `declared`，并给出两个具体端点档。

其余条目为〔读码〕/〔推演〕，已在矩阵与明细中逐条标注，未做真机验证的格子不得当作“已实测”。

---

## 8. 按严重度排序（≤20 行）

| # | 严重度 | 缺口 | 证据 | 影响 | 成本 |
|---|---|---|---|---|---|
| 1 | P0 | BladeRF/Lime 连接成功路径 `.device` AttributeError | `main_window.py:6105`（另 6183/3292/9555） | 连接后半链路中断，无提示 | 低 |
| 2 | P1 | Offset Tuning 假生效 | `sdr_backend.py:2551-2558` | 勾选/状态栏双确认假状态 | 低 |
| 3 | P1 | 增益读数=请求值却称“设备回执” | `sdr_backend.py:533` + `2381-2388`；`control_panel.py:1488` | 非 R820T 可差数 dB | 低 |
| 4 | P1 | Soapy 采样率/增益不回读实际值 | `sdr_backend.py:3480-3500`、`502` | 读数与 DSP 分叉 | 低 |
| 5 | P1 | 非 RTL 连接每次报“PPM、Offset 未生效” | `main_window.py:6979-6992` | 把“没能力”说成“失败” | 低 |
| 6 | P1 | 空态 2.400 MS/s 未标默认，且与 1.024 冲突 | `control_panel.py:577-578`；`device_panel.py:185` | 默认值冒充生效值 | 低 |
| 7 | P1 | SpyServer 量程硬编码 RTL 假设 | `spyserver_client.py:462-473` | 选不到合法档/滑杆越界 | 中 |
| 8 | P2 | `unknown` 被强制显示为“声明” | `device_capabilities.py:126-128`、`250` | 违反自身红线，给出假端点 | 低 |
| 9 | P2 | rtl_tcp 带宽/AGC/直采/偏置空操作成功 | `rtltcp_client.py:559-566`、`576-589` | 假成功 | 低 |
| 10 | P2 | 断开后采样率/PPM/静噪/带宽残留 | `control_panel.py:311`、`350-368` | 两套标准 | 低 |
| 11 | P2 | 模块面板硬件带宽连上后改无效 | `module_panel.py:639-641` | 控件像实时其实不是 | 低 |
| 12 | P2 | 连接同步阻塞 GUI | `main_window.py:6025` 附近 | 窗口假死 | 中 |
| 13 | P2 | PPM 不支持时回退 0 而非配置值 | `main_window.py` PPM 守卫 | 显示与配置分叉 | 低 |
| 14 | P3 | SpyServer 无桌面入口 / rtl_tcp 仅模态框 | `main_window.py:6387-6389` | 网络源不可达 | 中 |
| 15 | P3 | 偏置/直采/天线无 UI | `sdr_backend.py:2510/2527/3527` | 能力不可用 | 中 |
| 16 | P3 | Soapy TX 硬编码 False | `sdr_backend.py:3076` | TX 设备被隐藏 | 中 |
| 17 | P3 | BladeRF 无增益读数 | `main_window.py:6720-6732` | 恒 `—` | 低 |
| 18 | P3 | `ReconnectionManager` 未接线 | `mbdsdr_ai/reconnection.py` | 能力闲置 | 中 |
| 19 | P3 | 状态栏“带宽”实为解调带宽 | `main_window.py:4948-4952` | 术语歧义 | 低 |
