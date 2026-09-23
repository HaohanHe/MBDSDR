# 19 · sdr_backend.py 设备管理 / 后端抽象 / RTL-SDR 审查（R2）

- 审查对象：`mbdsdr_ai/sdr_backend.py`（1196 行）
- 审查范围：1–600 行（抽象基类、Mock、RTL-SDR、ai-sdr Mini 开头），并为"设备管理/枚举/热插拔"补读 `SDRBackendManager`（1078–1196）做交叉确认。
- 结论概览：抽象骨架清晰、RTL-SDR 主链路可用；存在 **状态与硬件不一致**、**采样率不校验**、** setter 异常处理不一致** 三类真问题；HackRF/USRP 为半实现占位；**无任何热插拔机制**；已知问题 #5/#6 已逐条确认。

---

## 一、设备抽象基类 `SDRBackend`（72–321）

### [真bug] 基类 `set_sample_rate` 不做范围校验，与 `set_frequency` 不一致
- `sdr_backend.py:113-117`：`set_sample_rate` 只检查 `connected`，**完全不校验** `device.sample_rate_range`，直接写 `self.status.sample_rate_hz`。
- 对比 `set_frequency`（`sdr_backend.py:105-106`）有 `frequency_range` 越界拦截。两个对称接口行为不对称。
- 后果：`sample_rate_range` 这个字段（RTL=`(250000, 3200000)`，`sdr_backend.py:416`）从未被任何逻辑用于校验，形同虚设；上层可把任意 Hz 写进状态。

### [真bug] 抽象方法/钩子盘点
- `connect()`（`sdr_backend.py:93-95`）：`raise NotImplementedError` —— 预期抽象，子类 Mock/RTL/ai-sdr/HackRF/USRP 均已实现，**无遗漏**。
- `read_samples()`（`sdr_backend.py:161-163`）：`raise NotImplementedError` —— 预期抽象。`AISDRMiniBackend.read_samples`（`sdr_backend.py:755-757`）按 SI4732 不出 IQ 的事实 `return None`，属合理空实现而非未实现。
- `disconnect()`（`sdr_backend.py:97-99`）基类只置 `connected=False`，子类各自 close 硬件资源，模式正确。

### [建议] 统一接口文档与实际接口有缺口
- 文件头 docstring（`sdr_backend.py:13-20`）列的统一接口不含 `set_ppm` / `set_direct_sampling` / `set_bias_tee`，这些只存在于 `RTLSDRBackend`（533/544/561），是 RTL 专有扩展，基类未声明。上层无法用统一接口做多态设置，属可接受设计但文档未说明。

---

## 二、RTL-SDR 后端 `RTLSDRBackend`（392–576）

### [真bug] connect 后状态与硬件真实参数不一致
- `RTLSDRBackend.connect()`（`sdr_backend.py:454-475`）成功后只置 `connected=True`、记录 `_start_time`，**从不回读硬件实际值**。
- pyrtlsdr `RtlSdr` 打开后默认 `sample_rate = 2.0 MHz`、`center_freq` 未设置；而 `SDRStatus` 默认 `sample_rate_hz = 2400000.0`（`sdr_backend.py:55`）。
- 后果：连接刚成功、上层尚未调 `set_sample_rate` 前，`get_status()` 上报 2.4 MHz，硬件实际跑 2.0 MHz。录制 sidecar（`_write_recording_sidecar` 281-308）写的 `sample_rate` 可能与硬件实际采样率不符 → 回放速度错误。应在 connect 后回读 `self._sdr.sample_rate` / `center_freq` 回填 status。

### [真bug] `set_frequency` / `set_sample_rate` 写硬件无 try/except，与同类 setter 风格不一致
- `set_frequency`（`sdr_backend.py:489-490`）：`self._sdr.center_freq = freq_hz` —— 无保护。
- `set_sample_rate`（`sdr_backend.py:496-497`）：`self._sdr.sample_rate = rate_hz` —— 无保护。
- 对比 `set_gain`（504-507）、`set_agc`（514-520）、`set_bandwidth`（527-530）、`set_ppm`（537-541）、`set_direct_sampling`（550-558）、`set_bias_tee`（564-568）**全部** try/except 兜底并返回 False。
- 后果：当上层传入 pyrtlsdr 不接受的频率/采样率（叠加上面"采样率不校验"的 bug，极易触发），异常会直接穿透到调用方，而不是像其它 setter 一样优雅返回 False。行为不一致。

### [确认已知问题 #6] 增益为单标量，无 LNA/VGA/基带分段
- 基类 `set_gain`（`sdr_backend.py:122-127`）：单标量，仅 `max(0, min(gain_db, max_gain))` 钳位。
- RTL `set_gain`（`sdr_backend.py:500-508`）：`self._sdr.gain = float(gain_db)`，单一总增益。
- 确认无 `set_lna_gain` / `set_vga_gain` / 混频增益分路。说明：librtlsdr/pyrtlsdr 本身不暴露分路增益，这是库能力上限而非纯代码缺陷；但与文档暗示的"完整调谐器增益控制"有差距，且 `max_gain=49.6`（`sdr_backend.py:417`）是 R820T 峰值，实际可用档位是离散列表，直接赋浮点 pyrtlsdr 会就近取档，文档未提示。

### [建议] 频率校正 ppm —— 实现正确
- `set_ppm`（`sdr_backend.py:533-542`）：`self._sdr.freq_correction = int(ppm)`，pyrtlsdr API 正确；未连接时仅缓存 `_ppm`，connect 后（468-469）补应用。符合预期。

### [缺失/建议] 无直流偏移(DC offset) / IQ 平衡校正
- 全文 grep `dc_offset` / `iq_balance` / `set_dc` **零命中**。
- pyrtlsdr 提供 `set_dc_offset(1)` / `set_iq_balance(1)`，RTL2832 + R820T 在近零频、直采 HF（`set_direct_sampling` 544）场景 DC 泄漏与 IQ 失衡明显，本后端完全未启用。属功能缺口（建议项），非崩溃 bug。

### [建议] rtl_tcp 分支
- `connect`（456-458）用 `RtlSdrTcpClient(hostname=, port=)`，API 正确；但 rtl_tcp 远端设备不支持本地 `set_bias_tee` / `set_direct_sampling` 语义，代码仍会尝试调用（561-569 / 544-559），失败被 try 吞掉返回 False，可接受但未区分提示。

---

## 三、设备枚举 `list_devices` / `SDRBackendManager`

### [真bug/建议] `RTLSDRBackend.list_devices()` 探测会独占设备
- `sdr_backend.py:430-452`：对每个 index `probe = RtlSdr(i)` 打开读 `tuner_type` 再 `close()`。逻辑正确、异常兜底完整、返回 `[{index,serial,tuner}]`，无库时返回 `[]`。
- 但 `SDRBackendManager._discover`（`sdr_backend.py:1098-1105`）探测方式不同：直接 `RtlSdr(0); sdr.close()`。**若该棒已被另一进程/本管理器后续实例占用，此处 open 失败 → 直接判定"无 RTL-SDR"并不注册**，即使物理设备存在。探测逻辑两处不一致（list_devices 用 get_device_count，_discover 直接 open index 0）。

### [占位] HackRF / USRP 为"import 即注册"的半实现
- `_discover`（`sdr_backend.py:1107-1122`）：HackRF 仅 `import hackrf` 成功就 `HackRFBackend(0)` 注册（不连接）；USRP 仅 `import uhd` 成功就 `USRPBackend()` 注册。
- `HackRFBackend`（`sdr_backend.py:760+`）：`import hackrf` 并 `hackrf.HackRF()`——PyPI 上并无广泛可用的 `hackrf` 绑定包（常见为 `pyhackrf`），且以 `self._hackrf.frequency = ...` 属性赋值驱动，属**猜测式 API，半实现占位**。
- `USRPBackend`（`sdr_backend.py:845+`）：`uhd.usrp.MultiUSRP(...)` + `uhd.libpyuhd.types.tune_request(...)`，API 看似更贴近 UHD，但同样无硬件验证路径。
- 文档头（`sdr_backend.py:8-9`）宣称支持 HackRF One / USRP(UHD)，实际为"库在就注册、连上靠运气"的占位。

### [空壳/未实现] 无 BladeRF / PlutoSDR / SoapySDR
- 全文 grep `bladerf` / `pluto` / `SoapySDR` 零命中。这些设备类型**完全不存在**，文档也未宣称。属未实现，非缺陷，但需在对外能力清单中如实剔除。

---

## 四、设备热插拔

### [占位/缺失] 全文无任何热插拔检测机制
- grep `hotplug` / `udev` 零命中。
- `SDRBackendManager._discover`（1091）**仅在 `__init__` 跑一次**，运行中新插入的棒不会被发现；无重扫/事件订阅入口。
- 拔出后：`RTLSDRBackend.read_samples`（`sdr_backend.py:571-576`）直接 `self._sdr.read_samples()`，**不捕获 USB 拔出导致的 OSError/IOError**，且不把 `status.connected` 置 False。
  - 唯一兜底是录制线程 `_recording_loop` 的通用 `except Exception`（`sdr_backend.py:267-268`）——录制不崩，但 `get_status()` 仍报 `connected=True`、`uptime` 持续累加，上层误以为设备在线。
- 结论：**无热插拔检测、无拔出优雅降级**，需靠上层自己捕获 read_samples 异常。

---

## 五、已知问题逐条确认

### [确认 #5] `set_frequency` 越界静默返回 False（`sdr_backend.py:105-106`）
- 行为核实：`freq_hz` 越界 → `return False`，**第 107 行 `self.status.frequency_hz = freq_hz` 被跳过**，status 保留旧频率。
- 完整影响范围：
  - 状态一致性其实是**正确**的（不更新 = 不脏状态），并非"频率被错误改写"。
  - 所有子类均 gate 在 `super().set_frequency()`：RTL（487）、ai-sdr（702）、HackRF（805）——**越界请求绝不会触达硬件**。
  - 真正缺陷是**反馈缺失**：纯 `bool` 返回、无日志、无异常、无 error 文案；上层（AI 调谐 / UI）若不检查返回值，会把"调谐失败"当成"调谐成功"，继续按目标频率做后续解调/记录。
- 严重度：低。建议至少 `logging.warning` 越界值与合法范围。

### [确认 #6] 增益单标量（`sdr_backend.py:122`、`500`）
- 见上文第二节。确认无 LNA/VGA/基带分段；受 librtlsdr 能力上限约束。

---

## 六、采样率 / 缓冲区 / 溢出

- 默认采样率：`SDRStatus.sample_rate_hz = 2400000.0`（`sdr_backend.py:55`）；但见第二节真 bug——RTL 硬件默认实为 2.0 MHz，connect 后未回填，存在 2.4M vs 2.0M 漂移。
- 录制块大小：`chunk_size = 16384`（`sdr_backend.py:212`），在 2.4Msps 下约 6.8 ms/块，合理。
- 溢出处理：**无**。`RTLSDRBackend.read_samples`（571-576）直接透传 pyrtlsdr 读结果，无 USB 缓冲区 underflow/overflow 计数，无丢样本统计；pyrtlsdr 自身缓冲在高负载下会阻塞而非报错。
- `effective_rate = sample_rate_hz / decimation`（`sdr_backend.py:217`）用的是 status 中的（可能错误的）采样率，放大了第二节状态不一致的影响。

---

## 七、越界观察（>600 行，顺带记录，不计入本报告主结论）

- `AISDRMiniBackend._refresh_status`（`sdr_backend.py:693,695`）写 `self.status.rssi = ...` / `self.status.snr = ...`，但 `SDRStatus` 真实字段名是 `rssi_db` / `snr_db`（`sdr_backend.py:62-63`）。dataclass 不阻止动态新属性，结果是在实例上凭空建了 `rssi`/`snr` 属性，**真实 `rssi_db`/`snr_db` 永不更新**。疑似真 bug，归 ai-sdr 子 agent 复核。

---

## 优先级汇总

| # | 级别 | 位置 | 问题 |
|---|------|------|------|
| 1 | 真bug | `sdr_backend.py:470-472` | connect 后不回读硬件 sample_rate/freq，status 与硬件不一致（2.4M vs 2.0M），污染录制 sidecar |
| 2 | 真bug | `sdr_backend.py:113-117` | 基类 set_sample_rate 不校验 sample_rate_range，字段形同虚设 |
| 3 | 真bug | `sdr_backend.py:489-490,496-497` | set_frequency/set_sample_rate 写硬件无 try/except，与其余 setter 不一致，异常穿透 |
| 4 | 缺失 | `sdr_backend.py:571-576`（全局） | 无热插拔检测；拔出后 read_samples 异常不捕获、connected 仍为 True |
| 5 | 占位 | `sdr_backend.py:760+,845+` | HackRF/USRP 为 import 即注册的半实现；无 BladeRF/Pluto/SoapySDR |
| 6 | 缺失 | 全文 | RTL 未启用 DC offset / IQ balance 校正 |
| 7 | 建议 | `sdr_backend.py:105-106` | 越界静默返回 False 无日志（已知#5 已确认，状态本身一致） |
| 8 | 建议 | `sdr_backend.py:122,500` | 增益单标量无分路（已知#6 已确认，受库能力限制） |
| 9 | 建议 | `sdr_backend.py:1098-1105` | _discover 探测 RTL 直接 open index0，与 list_devices 逻辑不一致，易误判无设备 |
