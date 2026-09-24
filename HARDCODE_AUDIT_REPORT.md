# MBDSDR 全仓硬编码/假数据审查报告

> **审查范围**：`mbdsdr_ai/`（71个 .py）、`desktop/`（9个 .py）、`tests/`（8个 .py）、`experiments/`（9个 .py）、`scripts/`（2个 .py）、根目录脚本（3个 .py）、`mobile/`（6个 .dart）
> **审查方式**：59 个子 agent 并行只读审查，逐行比对 10 类硬编码问题
> **审查日期**：2026-09-24
> **约束**：只读，未修改任何代码，未触碰 git
> **排除目录**：kilo-code/、mimo-code/、opencode/、hermes-self-evolution/、ai-sdr-mini-kicad/、stellarium-web-engine/、repos/、.sessions/、.git/、skills/

---

## 摘要统计

| 严重度 | 数量 | 定义 |
|--------|------|------|
| **P0** | **19** | 骗用户说有真数据（白噪声冒充IQ、Mock冒充真连接、假安全检测、假干扰告警） |
| **P1** | **57** | 占位假装可用（stub返回成功、假坐标、假设备状态、恒真测试） |
| **P2** | **68** | 硬编码但有兜底（合理默认值、已披露的模拟数据、启发式常量） |
| **合计** | **144** | |

**最危险的系统性缺陷**：整个项目存在一条"静默降级到模拟数据并向上报告成功"的设计模式——HAL 层、SDR 后端管理器、MCP Worker、MCP Server、Radio CAT 控制全部在硬件失败时偷偷切到 Mock/合成数据并返回 `success=True`，调用方和用户完全无法区分真数据与假数据。

---

## P0 — 骗用户说有真数据（19项）

### P0-1: hal.py — read_rx() 设备未连接时静默返回高斯白噪声冒充真 IQ

- **位置**：`mbdsdr_ai/hal.py:359-362`
- **类别**：假设备/假硬件状态
- **原文**：
```python
# 模拟数据
t = np.arange(num_samples) / self._sample_rate
noise = (np.random.randn(num_samples) + 1j * np.random.randn(num_samples)) / np.sqrt(2) * 0.01
return noise.astype(np.complex64)
```
- **为什么假**：这是 `SoapySDRBackend`（真实硬件后端），不是 Mock 类。以下三种情况都会静默落到白噪声 fallback：① `self._device` 为 None；② `self._rx_stream` 为 None；③ `stream.read()` 返回 n<=0 或抛异常。最危险的是情况 2：`connect()` 已返回 True，调用者以为在收真实 IQ，实际每帧都是高斯白噪声。
- **怎么接真**：fallback 路径应抛异常或返回 None + 日志 error；调用方应在 setupStream 未就绪时禁止 read_rx。
- **分类**：真硬编码

### P0-2: hal.py — connect_sdr() 真实连接失败后静默降级 Mock 仍报 success=True

- **位置**：`mbdsdr_ai/hal.py:746-749`
- **类别**：假成功
- **原文**：
```python
# 降级到模拟
self._active_backend = MockSDRBackend()
self._active_backend.connect()
return {"success": True, "device": "模拟后端(降级)", "tx": True}
```
- **为什么假**：用户传入真实 device_str（如 "hackrf"），SoapySDR 连接已返回 False，但管理器不报告失败，偷偷切到 MockSDRBackend，仍返回 success: True。`tx: True` 也是编的——Mock 后端根本不发射。上层 UI 会绿灯显示"已连接硬件"，实际跑的是假数据流水线。
- **怎么接真**：SoapySDR 连接失败时应返回 `{"success": False, "error": "...", "fallback_available": True}`，由调用方显式选择是否降级。
- **分类**：真硬编码

### P0-3 ~ P0-8: radio_control.py — 整个 CAT 控制类除 PTT 外从不与真实电台通信

| # | 位置 | 问题 |
|---|------|------|
| P0-3 | `radio_control.py:291-294` | connect() 在 ImportError 时 `self._connected=True; return True` |
| P0-4 | `radio_control.py:295-298` | connect() 在任何异常下仍 `self._connected=True; return True`，注释自承"失败但仍允许模拟操作" |
| P0-5 | `radio_control.py:313-316` | set_frequency() 即使有串口也只是 `pass`，不发任何 CI-V/Yaesu/Kenwood 命令，返回 True |
| P0-6 | `radio_control.py:318-320` | get_frequency() 只返回本地缓存 `self._freq`，从不向电台查询 |
| P0-7 | `radio_control.py:327-328` | get_mode() 只返回本地缓存 `self._mode`，从不向电台查询 |
| P0-8 | `radio_control.py:357-368` | get_status() 全是本地变量，没有一项来自电台实际回读 |

- **为什么假**：`RadioCAT` 类名义上是"电台 CAT 控制"，但除了 `set_ptt` 里有一行 `setRTS()` 之外，所有读频率、读模式、设频率、设模式、发 CW、获取状态都是本地缓存操作。connect 在任何失败情况下都返回 True，整个类的"已连接"状态是自欺欺人的。
- **怎么接真**：按 model 分支发送对应 CAT 协议命令并校验回包 ACK；connect 失败应返回 False；get_frequency/get_mode 应真实查询设备。
- **分类**：真硬编码

### P0-9: sdr_backend.py — Manager 静默默认 Mock 后端，无真实硬件时用户拿到合成信号却以为已连接

- **位置**：`mbdsdr_ai/sdr_backend.py:1221`
- **类别**：假设备/假硬件状态
- **原文**：`self.active_backend = mock`
- **为什么假**：`SDRBackendManager._discover()` 无论是否发现真实硬件，都把 active_backend 静默设为 MockSDRBackend。用户调用 manager.connect() 时，Mock 的 connect() 直接返回 True，read_samples() 返回合成载波+白噪声——用户看到"已连接"、有 RSSI/SNR 读数，但全程在跑仿真信号，无任何 warning。
- **怎么接真**：_discover() 末尾应优先选择真实硬件；若一个真实硬件都没注册，必须 logger.warning 并在 list_devices() 里把 mock 标记为 simulated: true。
- **分类**：真硬编码

### P0-10: llm_judge.py — safety 维度焊死 9.0 分，零检测却声称"未检测到危险内容"

- **位置**：`mbdsdr_ai/llm_judge.py:344-350`
- **类别**：假返回值/假安全检测
- **原文**：
```python
# 安全性：检查是否包含危险内容（简化）
safety = 9.0  # 默认安全
dimensions.append(DimensionScore(
    dimension="safety", score=safety,
    weight=self.dimension_weights.get("safety", 0.10),
    reasoning="未检测到危险内容",
))
```
- **为什么假**：注释写"检查是否包含危险内容"，但代码里没有任何检测逻辑——既没有关键词黑名单，也没有调用安全分类器，直接把 safety 焊死在 9.0。更严重的是 reasoning 字段输出"未检测到危险内容"，这是与事实不符的声明。下游如果依赖这个维度做安全闸门，危险输出会被无条件放行。
- **怎么接真**：接真实安全判定路径（LLM 安全分类调用或关键词正则扫描），规则模式下至少做危险指令扫描，reasoning 改成"规则模式未启用深度安全检测"。
- **分类**：真硬编码

### P0-11: orchestrator.py — 无 handler 无 registry 时空跑仍标记 COMPLETED 返回 True

- **位置**：`mbdsdr_ai/orchestrator.py:338-344`
- **类别**：假成功
- **原文**：
```python
else:
    task.result = None
task.status = TaskStatus.COMPLETED
task.completed_at = time.time()
task.duration_ms = (task.completed_at - task.started_at) * 1000
return True
```
- **为什么假**：当任务既没有 handler 回调，也没有可用的 tool_registry 时，代码直接走 else 分支——只把 result 设为 None，没有任何实际执行动作，随后却将 status 置为 COMPLETED 并 return True。`create_sdr_pipeline()` 创建的全部 9 个任务都只指定了 tool_name、没有 handler。如果调用方没传入 tool_registry，整条 SDR 流水线会全部被标记为"成功完成"，但一行 SDR 硬件代码都没跑过。
- **怎么接真**：else 分支应判定为执行失败——抛 RuntimeError，让异常被外层 except 捕获标记为 FAILED。
- **分类**：真硬编码

### P0-12: satdump_integration.py — 超时后进程已被 kill，却返回 success=True "后台运行中"

- **位置**：`mbdsdr_ai/satdump_integration.py:107-112`
- **类别**：假成功
- **原文**：
```python
except subprocess.TimeoutExpired:
    return {
        "success": True,
        "message": "接收已启动（后台运行中）",
        "command": ' '.join(cmd),
    }
```
- **为什么假**：`subprocess.run(..., timeout=30)` 触发 TimeoutExpired 时会先 process.kill() 再回收僵尸进程——子进程已经被强杀，根本没有"后台运行中"的进程。却返回 success: True。且 30 秒对 live 接收（数分钟到数十分钟）必然超时，等于必然走假成功分支。
- **怎么接真**：真要后台常驻应改用 subprocess.Popen + PID 注册表；当前应把该分支改成 success: False。
- **分类**：真硬编码

### P0-13: sdr_tools.py — GNSS 单频带干扰监测伪造数据+硬编码 CW，无模拟标注

- **位置**：`mbdsdr_ai/sdr_tools.py:4905-4917`
- **类别**：假信号/假谱峰+假硬件状态
- **原文**：
```python
n_samples = int(sample_rate * duration)
iq = (np.random.randn(n_samples) + 1j * np.random.randn(n_samples)) / np.sqrt(2) * 0.01
if band_name in GNSS_BANDS:
    t = np.arange(n_samples) / sample_rate
    interference = 0.05 * np.exp(2j * np.pi * 5000 * t)   # 硬编码注入 5kHz 假 CW
    iq = iq + interference
result = monitor_gnss_band(iq, center_freq, sample_rate, band_name)
```
- **为什么假**：工具描述是"监测单个 GNSS 频带干扰、分析功率谱/噪声底/INR、AI 自动分类干扰类型"，但函数从不调用 SDR 后端，直接用 np.random.randn 造噪声、再硬编码叠加一个 5kHz 偏移 CW，输出"平均功率/噪声基底/INR/峰值频率/干扰类型=continuous_wave/置信度"。输出字符串里没有任何"模拟/合成"字样。
- **怎么接真**：先获取 backend，校验 connected，真调谐真采集，采满 n_samples 再喂 monitor_gnss_band；未连接时返回 success=False。
- **分类**：真硬编码

### P0-14: sdr_tools.py — GNSS 全频带监测硬编码只给 L1 注入假 CW，必然产出假告警

- **位置**：`mbdsdr_ai/sdr_tools.py:4952-4964`
- **类别**：假信号/假告警
- **原文**：
```python
for band in GNSS_BANDS:
    n = int(sample_rate * duration)
    iq = (np.random.randn(n) + 1j * np.random.randn(n)) / np.sqrt(2) * 0.01
    if band == "L1":
        t = np.arange(n) / sample_rate
        iq += 0.08 * np.exp(2j * np.pi * 3000 * t)   # 硬编码给 L1 注入 3kHz 假 CW
    iq_by_band[band] = iq
```
- **为什么假**：对每个频带造随机噪声，再硬编码只给 L1 注入一个 3kHz CW，必然产出一条 `[WARNING] L1: continuous_wave INR=…` 告警。这条告警是写死的剧本，不是测出来的。输出面无"模拟"标注。
- **怎么接真**：循环对每个 band 的 center_freq_hz 真调谐真采集，采完一组再交给 monitor_all_gnss_bands。
- **分类**：真硬编码

### P0-15: mcp_worker.py — 连接失败静默切模拟模式仍设置 connected=True

- **位置**：`desktop/mcp_worker.py:192-197`
- **类别**：假成功/假设备状态
- **原文**：
```python
except Exception as e:
    self.error_occurred.emit(f"连接失败: {e}")
    self.log_message.emit(f"连接失败: {e}，回退模拟模式")
    self.use_simulation = True
    self.connected = True
    self.connection_changed.emit(True, "连接失败，回退模拟模式")
```
- **为什么假**：真实硬件连接失败后，不保持断开状态，而是静默切到模拟模式，并设置 connected=True。UI 收到 connection_changed(True) 后会把设备状态灯显示为"已连接"，用户很可能没注意到括号里的"回退模拟模式"。
- **怎么接真**：连接失败应保持 connected=False；模拟模式应作为独立状态由用户显式开启。
- **分类**：真硬编码

### P0-16: mbdsdr_ai_mcp_server.py — 启动时自动连接 Mock 后端，对 AI 客户端冒充真实 SDR

- **位置**：`mbdsdr_ai_mcp_server.py:105`
- **类别**：假设备/假成功
- **原文**：
```python
result = self.tool_registry.call("sdr_connect", {"backend": "mock"})
if result.success:
    self._log("已自动连接 Mock SDR 后端（无真实硬件时的默认行为，可随时切换）")
```
- **为什么假**：服务器启动时自动连接 Mock 后端，随后所有 195 个 SDR 工具都返回模拟数据并标注 success=True。MCP 客户端（Cursor/Claude/AI IDE）通过 tools/list 看到全部工具"可用"，调用后收到带数据的成功响应——完全无法区分自己在跟真实 SDR 还是模拟器对话。唯一提示在 stderr verbose 日志里，默认不输出。
- **怎么接真**：启动时检测硬件是否存在；无硬件时应在 tools/list 的每个工具描述里加 [SIMULATED] 前缀，或在每次工具返回里注入模拟标记。
- **分类**：测试合成数据冒充生产

### P0-17: test_infra_units.py — 恒真测试，跑了等于没跑

- **位置**：`tests/test_infra_units.py:93-98`
- **类别**：测试恒真断言
- **原文**：
```python
def test_hal_base_not_instantiable():
    from mbdsdr_ai.hal import SDRBackendBase
    try:
        SDRBackendBase()  # 抽象类不应可实例化
    except TypeError:
        return True
    # 某些实现允许实例化但抽象方法未实现；只要 list_devices 是抽象即可
    return True
```
- **为什么假**：测试名为 not_instantiable，但无论 SDRBackendBase() 是否抛 TypeError，都会走到 return True。第二个 return True 是兜底恒真分支——即使类根本不是抽象类，测试照样通过。注释里写"只要 list_devices 是抽象即可"，但代码里没有任何一行去检查。
- **怎么接真**：删掉第二个 return True，或用 inspect.isabstract() 真正验证抽象约束。
- **分类**：真硬编码

### P0-18: astronomy.py — predict_satellite_pass 真调了 sgp4 却丢弃位置向量，用硬编码 45° 仰角

- **位置**：`mbdsdr_ai/astronomy.py:542, 550, 564`
- **类别**：假坐标/假返回值
- **原文**：`alt_approx = 45.0`（硬编码仰角），`rise_az = 0.0`，`set_az = 180.0`（硬编码方位角）
- **为什么假**：函数真调了 sgp4 计算卫星位置，但丢弃了计算出的位置向量，用硬编码 alt_approx=45.0° 当仰角，配合 rise_az=0.0/set_az=180.0 硬编码方位角。且因逻辑 bug 实际永远返回空列表。
- **怎么接真**：使用 sgp4 输出的真实位置向量计算仰角/方位角，修复返回空列表的逻辑 bug。
- **分类**：真硬编码

### P0-19: decoders.py — decode_sstv 只是把音频包络重采样成灰度图，不做 FM 解调却报告"解码成功"

- **位置**：`mbdsdr_ai/decoders.py:574-609`
- **类别**：假返回值/假成功
- **为什么假**：实际只是把 |audio| 包络线性重采样成 320×256 灰度图，不做 FM 解调/同步检测/VIS 识别，却保存 PNG 并报告"解码成功"。
- **怎么接真**：接入真实 SSTV 解码（参考 sstv_decoder.py 的过零鉴频+同步检测实现），或明确标注为"包络可视化"而非"解码"。
- **分类**：真硬编码

---

## P1 — 占位假装可用（57项，按文件分组）

### hal.py（4项）

| # | 位置 | 问题 |
|---|------|------|
| P1-1 | `hal.py:265-275` | set_frequency() 无设备时静默更新本地 _center_freq，调用者无法判断是否真下发 |
| P1-2 | `hal.py:277-287` | set_sample_rate() 无设备时静默更新本地 _sample_rate |
| P1-3 | `hal.py:289-302` | set_gain() 无设备时静默更新；即使有设备 setGain 抛异常也只 warning 不回滚 |
| P1-4 | `hal.py:205` | list_devices() 硬编码 sample_rates=[2M,4M,8M,10M,20M]，不读设备 getSampleRateRange() |

### sdr_backend.py（10项）

| # | 位置 | 问题 |
|---|------|------|
| P1-5 | `sdr_backend.py:558-559` | RTL-SDR _apply_frequency 中 `if self._sdr is None: return True`（fail-open） |
| P1-6 | `sdr_backend.py:564-565` | RTL-SDR _apply_sample_rate 同上 |
| P1-7 | `sdr_backend.py:570-571` | RTL-SDR _apply_gain 同上 |
| P1-8 | `sdr_backend.py:900-901` | HackRF _apply_frequency 中 `if self._hackrf is None: return True` |
| P1-9 | `sdr_backend.py:909-910` | HackRF _apply_sample_rate 同上 |
| P1-10 | `sdr_backend.py:918-919` | HackRF _apply_gain 同上 |
| P1-11 | `sdr_backend.py:984-985` | USRP _apply_frequency 中 `if self._usrp is None: return True` |
| P1-12 | `sdr_backend.py:994-995` | USRP _apply_sample_rate 同上 |
| P1-13 | `sdr_backend.py:1003-1004` | USRP _apply_gain 同上 |
| P1-14 | `sdr_backend.py:879-888, 962-971` | HackRF/USRP connect 后从不覆写 readback_hw_state()，软件频率/采样率/增益全是默认假值 |
| P1-15 | `sdr_backend.py:198-208` | 基类 set_agc/set_bandwidth 只改软件标志，HackRF/USRP/AISDRMini 未覆写，硬件寄存器完全没动 |

### radio_control.py（3项）

| # | 位置 | 问题 |
|---|------|------|
| P1-16 | `radio_control.py:268-276` | list_ports() 在 ImportError 时硬编码列出 /dev/ttyUSB0 等假端口 |
| P1-17 | `radio_control.py:322-325` | set_mode() 只更新本地字典映射，从不向串口发命令 |
| P1-18 | `radio_control.py:353-355` | send_cw_text() 注释自承模拟，只存字符串到属性就返回 True |

### agent.py + subagents.py（2项）

| # | 位置 | 问题 |
|---|------|------|
| P1-19 | `agent.py` | 超时机制定义了但从未实现（timeout_s 存入 task 后从不比较，SubagentStatus.TIMEOUT 从未赋值） |
| P1-20 | `subagents.py` | astro_pointing_guidance 用 `if False else None` 把 beamwidth_deg 参数硬编码作废 |

### amr.py（1项）

| # | 位置 | 问题 |
|---|------|------|
| P1-21 | `amr.py` | 内置训练模板中"QAM"类实际生成 QPSK（4星座点），docstring 却宣称支持 16QAM/64QAM |

### baseband_io.py（1项）

| # | 位置 | 问题 |
|---|------|------|
| P1-22 | `baseband_io.py` | load_iq 写死按 float32 交错读，完全不用 sidecar 里的 format 字段 |

### code_editor.py（1项）

| # | 位置 | 问题 |
|---|------|------|
| P1-23 | `code_editor.py` | run_tests 空测试集初始 success=True；不存在的测试文件被静默跳过不计 failed |

### context_manager.py（1项）

| # | 位置 | 问题 |
|---|------|------|
| P1-24 | `context_manager.py` | 系统提示词写死"当前设备：ai-sdr Mini（SI4732前端，144kHz-108MHz，GPS+IMU 9轴）"，无任何硬件探测 |

### dsp.py（1项）

| # | 位置 | 问题 |
|---|------|------|
| P1-25 | `dsp.py` | rds_decode_from_wfm 只做 55-59kHz 带通+能量门限，不做 RDS 协议解码，却返回 rds_present 布尔 |

### digital_modes.py（1项）

| # | 位置 | 问题 |
|---|------|------|
| P1-26 | `digital_modes.py` | WSJTInterface 声称 UDP 网络接口并接收 host/port，但全文件无 socket，实际只读日志文件 |

### file_tracker.py（1项）

| # | 位置 | 问题 |
|---|------|------|
| P1-27 | `file_tracker.py` | 重启后 content_before 丢失（to_dict 不持久化内容），revert_to 静默跳过 file_writer 却仍返回成功记录 |

### frequency_manager.py + sweep.py（2项）

| # | 位置 | 问题 |
|---|------|------|
| P1-28 | `frequency_manager.py` | 设备读数据全部失败时静默返回空活动列表，调用方无法区分"无信号"和"设备没在工作" |
| P1-29 | `sweep.py` | 同上，扫频失败静默返回空结果 |

### gimbal.py + pose.py（2项）

| # | 位置 | 问题 |
|---|------|------|
| P1-30 | `pose.py` | PoseFusion confidence 每帧恒+0.01，100帧后必到1.0，与数据质量无关 |
| P1-31 | `pose.py` | Madgwick 滤波器归一化了磁力计却从未在修正中使用，号称9DOF实际是6DOF |

### gnss_monitor.py（1项）

| # | 位置 | 问题 |
|---|------|------|
| P1-32 | `gnss_monitor.py` | 功率字段标 dbm 但实际是未校准的相对 PSD dB 值 |

### guardian.py（2项）

| # | 位置 | 问题 |
|---|------|------|
| P1-33 | `guardian.py` | create_snapshot 单文件拷贝失败静默吞错仍返回完整快照；源路径不存在仍产0文件"成功"快照 |
| P1-34 | `guardian.py` | 无 evaluate_func 时安全检查默认放行 |

### llm_judge.py（1项）

| # | 位置 | 问题 |
|---|------|------|
| P1-35 | `llm_judge.py:192-194, 166` | LLM 调用异常回退到规则评分后，judge_model 仍被无条件覆盖成 LLM 模型名 |

### model_manager.py（2项）

| # | 位置 | 问题 |
|---|------|------|
| P1-36 | `model_manager.py:71-77` | API 不可用时返回 5 条硬编码假模型列表给 UI，用户点任何一个都会在 chat() 时失败 |
| P1-37 | `model_manager.py:186-187` | switch_model() 不做连通性探测、不校验 model id 是否存在，无条件返回 True |

### mobile_server.py（1项）

| # | 位置 | 问题 |
|---|------|------|
| P1-38 | `mobile_server.py:71-75` | chat 消息收到后回发假 ai_command 占位回声"已收到：xxx（AI 内核待接入）"，手机端无法区分真假 |

### new_spacetime.py（4项）

| # | 位置 | 问题 |
|---|------|------|
| P1-39 | `new_spacetime.py:641-646` | SDRDataSource.connect() 只置 self.connected=True，不接硬件 |
| P1-40 | `new_spacetime.py:648-656` | SDRDataSource.get_data() 声明 type=iq_samples 但无 samples 字段 |
| P1-41 | `new_spacetime.py:667-669` | GNSSDataSource.connect() 只置 self.connected=True，不接硬件 |
| P1-42 | `new_spacetime.py:664-678` | GNSS satellites=0/fix_type="none" 永远不更新，全文件无 NMEA 解析 |

### openapi_integration.py（1项）

| # | 位置 | 问题 |
|---|------|------|
| P1-43 | `openapi_integration.py:132-142` | get_satellite_passes() 是空壳函数，无任何计算/网络请求，直接返回 success=True |

### orchestrator.py（1项）

| # | 位置 | 问题 |
|---|------|------|
| P1-44 | `orchestrator.py:291` | 全部任务被跳过时 success 仍为 True（success=failed==0，不考虑 skipped） |

### plugin_system.py（3项）

| # | 位置 | 问题 |
|---|------|------|
| P1-45 | `plugin_system.py:244-257` | enable_plugin 无 register 函数或 register 返回 falsy 时仍置 ENABLED 并 return True |
| P1-46 | `plugin_system.py:310-318` | load_all_plugins 的"成功列表"混入启用失败项，异常全吞 |
| P1-47 | `plugin_system.py:414-423` | entry_point 字段解析了但加载器从不读取，目录插件只认 __init__.py |

### sandbox.py（3项）

| # | 位置 | 问题 |
|---|------|------|
| P1-48 | `sandbox.py:271, 218-222` | execute_function 的函数真实返回值被模板末尾的 {"success":True} 覆盖，调用方永远拿不到函数结果 |
| P1-49 | `sandbox.py:131, 200-211` | max_memory_mb 声明了但 subprocess.run 无任何内存限制 |
| P1-50 | `sandbox.py:226` | 结果标记缺失时 output is None 短路为 True 默认放行 |

### scheduler.py（1项）

| # | 位置 | 问题 |
|---|------|------|
| P1-51 | `scheduler.py:282-284` | tool 类型任务丢弃 tool_executor 返回值，硬编码 return True，伪造成功率 |

### self_evolution.py + self_learning.py（2项）

| # | 位置 | 问题 |
|---|------|------|
| P1-52 | `self_evolution.py:540-545` | evolve() 不传 test_cases 时完全跳过评估仍返回 success=True |
| P1-53 | `self_learning.py:124` | judge 参数注入后全文件从未调用，"LLM-as-Judge 闭环"名存实亡 |

### signal_analysis.py（2项）

| # | 位置 | 问题 |
|---|------|------|
| P1-54 | `signal_analysis.py:126` | SSB 调制识别 confidence 写死 0.7，与信号离决策边界距离无关 |
| P1-55 | `signal_analysis.py:136` | QPSK 调制识别 confidence 写死 0.8，与 peaks 计数/直方图锐度无关 |

### sdr_tools.py（2项）

| # | 位置 | 问题 |
|---|------|------|
| P1-56 | `sdr_tools.py:3733` | _get_gps() 永远返回硬编码长春坐标 43.82°N, 125.32°E + 12星 + HDOP 0.8，不碰 mgr |
| P1-57 | `sdr_tools.py:3736` | _get_imu() 永远返回硬编码重力向量 (0,0,1)g + 零角速度 + 航向 0° |

---

## P2 — 硬编码但有兜底（68项，摘要列表）

### 合理默认值类（可配置/可覆盖，建议外部化）

| # | 位置 | 内容 |
|---|------|------|
| P2-1 | `hal.py:185, 215` | 硬编码 tx_capable 驱动列表，不查 device.hasTxChannel() |
| P2-2 | `hal.py:707` | Mock 设备硬编码 available=True |
| P2-3 | `hal.py:250` | 日志声称"使用模拟模式"但实际返回 False |
| P2-4 | `model_manager.py:91-92` | 默认 endpoint 锁死硅基流动 + 默认模型 Qwen3.6-35B |
| P2-5 | `model_manager.py:123-125` | api_key 为空时 _last_error 被吞，UI 只看到假模型列表 |
| P2-6 | `model_manager.py:157` | weak_keywords 启发式把 7B 一律当弱模型 |
| P2-7 | `openapi_integration.py:29,49,68,100` | 4个外部 API endpoint 硬编码（含明文 http://） |
| P2-8 | `openapi_integration.py:51-56` | get_people_in_space 不校验 message 字段即报成功 |
| P2-9 | `openapi_integration.py:168-173` | OpenSky requires_key=False 与实际认证要求不符 |
| P2-10 | `orchestrator.py:397-429` | SDR 流水线默认参数（采样率/增益/FFT/阈值/格式）写死 |
| P2-11 | `sdr_backend.py:423-455` | MockSDRBackend.read_samples 合成载波+白噪声（mock 本职，需配合 P0-9 修复） |
| P2-12 | `sdr_backend.py:416-421` | Mock connect 硬编码 RSSI=-60/SNR=20 |
| P2-13 | `sdr_backend.py:686` | AISDRMini 默认 host=192.168.4.1:81 |
| P2-14 | `sdr_backend.py:56-65` | SDRStatus 初始 100MHz/2.4MHz/0dB |
| P2-15 | `scheduler.py:257,264,274` | cron 解析失败静默回退到 1 小时间隔 |
| P2-16 | `time_sync.py:18` | 默认 NTP 服务器 ntp.aliyun.com 写死，缺多源 fallback |
| P2-17 | `workflow_engine.py:228-229` | 预设工作流硬编码长春坐标 43.8/125.3 |
| P2-18 | `workflow_engine.py:252,272,292,317` | 预设频段频率（14.230/144.640/87.5-108MHz）写死 |
| P2-19 | `desktop/main_window.py:405` | 连接对话框默认 IP 192.168.4.1 |
| P2-20 | `desktop/main.py:36` | 默认端口 81 |
| P2-21 | `desktop/control_panel.py:16-80` | 预设电台/频段表硬编码 |
| P2-22 | `desktop/control_panel.py:97-188` | 初始 UI 值 98.5MHz/音量30 |
| P2-23 | `desktop/ai_panel.py:307,358` | 默认 API endpoint 硅基流动 |
| P2-24 | `desktop/ai_panel.py:314,363` | 默认模型名 Qwen3.6-35B |
| P2-25 | `desktop/rf_sky_view.py:72-84` | 天线指向默认正北45° |
| P2-26 | `desktop/rf_sky_view.py:1107-1110` | 卫星标称频率表 |
| P2-27 | `mbdsdr_ai_mcp_server.py:73-74` | 默认 model/endpoint（有覆盖机制） |
| P2-28 | `mbdsdr_mcp_client.py:61,549` | 默认 ESP32 IP 192.168.4.1 |
| P2-29 | `mobile/connection.dart:92, main.dart:49` | WebSocket 默认 IP 192.168.1.10:8765 |
| P2-30 | `mobile/connection.dart:77-80` | 默认中心频率 98.5MHz/带宽 4.0MHz |

### 已披露的模拟/测试数据类（标注了"模拟/测试"，但建议加强标识）

| # | 位置 | 内容 |
|---|------|------|
| P2-31 | `sdr_tools.py:5881-5884` | 无硬件时合成 FM 信号演示识别（已带 note 标注） |
| P2-32 | `sdr_tools.py:5922-5926` | 无硬件时合成频谱+假峰演示特征提取（已带 note） |
| P2-33 | `sdr_tools.py:5543-5563` | LRO 定轨 demo 用模拟多普勒（docstring 明说模拟） |
| P2-34 | `desktop/spectrum_widget.py:83-93,40` | 合成 -90dBm 噪声底（5个FM假台已删除，但噪声无仿真水印） |
| P2-35 | `mbdsdr_sim_server.py:61-63,197-216` | 假 GPS 高度/卫星数/HDOP + 合成 IMU（sim 本职，但响应缺 simulated 字段） |

### 启发式/弱断言/逻辑缺陷类

| # | 位置 | 内容 |
|---|------|------|
| P2-36 | `hooks.py:408` | min_duration_s 参数接受但从未使用 |
| P2-37 | `hooks.py:372-376` | 日志写文件失败静默吞掉仍 return log_entry |
| P2-38 | `hooks.py:397,401,423` | 事件数据缺字段时 .get(...,0) 显示为 0.000 MHz / 0.0 dB |
| P2-39 | `sandbox.py:67-69` | SafeModule 类定义后从未使用（死代码制造"已拦截"假象） |
| P2-40 | `llm_judge.py:306-312` | correctness 维度用 len(answer)>50 冒充正确性 |
| P2-41 | `llm_judge.py:384-386` | 规则模式 strengths/weaknesses/suggestions 写死模板 |
| P2-42 | `signal_analysis.py:139` | BPSK confidence 写死 0.7 |
| P2-43 | `signal_analysis.py:251` | 窄带干扰 confidence 写死 0.8 |
| P2-44 | `signal_analysis.py:261` | 宽带干扰 confidence 写死 0.6 |
| P2-45 | `signal_analysis.py:174` | BER 估计门限 np.mean(dists)*0.5 几何上不严谨 |
| P2-46 | `tool_registry.py:339-360` | 返回值缺 success 键时一律视为成功；任何字符串视为成功 |
| P2-47 | `tool_registry.py:552-555` | switch_model 工具 handler success 恒 True，只取 [1] 拼 content |
| P2-48 | `skill_registry.py:95-101` | 技能读取失败把错误文本当正文返回 |
| P2-49 | `workflow_engine.py:390-391,481-491` | 变量解析不到时保留 "{{xxx}}" 字面量发给硬件 |
| P2-50 | `workflow_engine.py:421-424` | 条件求值异常时 fail-open 默认执行 |
| P2-51 | `workflow_recorder.py:270` | 参数化用描述字符串做匹配值，几乎永不命中 |
| P2-52 | `sstv_decoder.py:311` | VIS 奇偶校验失败仍照常返回 code，注释说"标记不可信"但没标 |
| P2-53 | `sstv_decoder.py:214,231` | 无过零点时整段填充 1500Hz（黑电平），掩盖"无信号" |
| P2-54 | `sstv_decoder.py:88` | Scottie DX 注释自承"彩色映射待精调"仍 success=True |
| P2-55 | `meteor_sat.py:470-482` | compose_visible_image TODO 桩诚实返回 None（但上层若不检查会崩） |
| P2-56 | `meteor_sat.py:503-506` | LROOrbit 用理想圆轨道+固定倾角，非真实星历 |
| P2-57 | `meteor_sat.py:238,253` | DVB-S 占位频率 4GHz/12GHz |
| P2-58 | `noaa_apt_lite.py:36-39` | _SYNC_WORD 40位 vs APT_SYNC_LEN=39 不一致（抄录bug） |
| P2-59 | `self_evolution.py:182-185` | 非代码修改默认 safe=True，dangerous_patterns=[] 凭空造 |
| P2-60 | `self_evolution.py:103,355` | auto_confirm_low_risk 默认 True |
| P2-61 | `self_evolution.py:342-346` | pytest 不可用时仅语法通过就算 passed=True |
| P2-62 | `self_learning.py:237,248,260` | confidence=score/10 线性换算，frequency 恒为 1 不聚合 |
| P2-63 | `self_learning.py:88,306` | applied 字段永不置 True，applied_patterns 统计永远 0 |
| P2-64 | `tests/smoke_live_tools.py:80,82,87` | bool() 弱断言 + "99.7"无关 OR 条件 |
| P2-65 | `tests/test_infra_units.py:39-41` | runs_safe_math 只断言沙箱没拦，不断言算对了 |
| P2-66 | `tests/test_infra_units.py:86-87` | 枚举值"是 str 或 int"近乎恒真 |
| P2-67 | `tests/tool_selftest.py:23-29` | FRIENDLY_HINTS 混入"需要""没有"等通用词，误判 bug 为友好降级 |
| P2-68 | `scripts/rtl_selfcheck.py:128-131,195-225` | 采样率回退不校验；无信号仍返回 PASS |

---

## 确认无问题的文件（干净清单）

以下文件经审查确认无硬编码/假数据问题：

- `mbdsdr_ai/`：adsb.py、adsb_lite.py、ax25.py、cfo.py、constellation.py、cw_decoder.py、fst4_ldpc.py、ft8_ldpc.py、ft8_callsign.py、ft8_unpack.py、ft8_decode.py、ft8_lite.py、rds_lite.py、signal_spectrum.py、spectrum_sensing.py、audio_out.py、analog_demod.py、wfm_stereo_lite.py、config.py、spectrum_processor.py、time_sync.py（仅P2）
- `desktop/`：themes.py（纯样式）
- `tests/`：tool_health_check.py、tool_selftest.py（仅P2）、ft8_roundtrip.py
- `experiments/`：全部 9 个文件（仅 2 个 P2，目录纪律很好）
- `scripts/`：rtl_fm_listen.py
- `mobile/`：theme.dart、spectrum_page.dart、chat_page.dart、sky_page.dart（数据均来自真实流）

---

## 修复优先级建议

### 第一优先级（立即修，涉及欺骗用户）
1. **hal.py P0-1/P0-2**：read_rx 白噪声 fallback + connect 静默降级 Mock——这是所有假数据的源头
2. **sdr_backend.py P0-9**：Manager 静默默认 Mock——无棒用户全程跑仿真却以为真连接
3. **radio_control.py P0-3~P0-8**：整个 CAT 类是假的——6个P0集中在一个文件
4. **mcp_worker.py P0-15 + mbdsdr_ai_mcp_server.py P0-16**：桌面端和 MCP 服务端都在硬件失败时静默切模拟
5. **sdr_tools.py P0-13/P0-14**：GNSS 干扰监测完全伪造数据，会误导 AI 给出"检测到干扰"的错误结论
6. **llm_judge.py P0-10**：safety 维度焊死 9.0，安全闸门形同虚设

### 第二优先级（修完后功能才真实可用）
7. **orchestrator.py P0-11**：无 registry 时空跑报成功——SDR 流水线可能全空跑
8. **satdump_integration.py P0-12**：live 接收必然超时假成功
9. **sdr_backend.py P1-5~P1-15**：所有硬件后端 fail-open + 不回读硬件状态
10. **sandbox.py P1-48~P1-50**：函数返回值丢失 + 内存限制未生效 + 默认放行
11. **plugin_system.py P1-45~P1-47**：插件启用假成功 + entry_point 死配置
12. **new_spacetime.py P1-39~P1-42**：SDR/GNSS 数据源全是 stub

### 第三优先级（健壮性/诚实性打磨）
13. 所有 P2 级别的合理默认值外部化、已披露模拟数据加强标识、弱测试断言补强

---

## 审查方法论说明

- **59 个子 agent** 按目录/模块分片并行审查，每片 1-5 个文件
- 每个发现均包含：file_path:line_number、类别(1-10)、严重度(P0/P1/P2)、原文代码片段、为什么假、怎么接真数据、分类[真硬编码/合理默认值/测试合成数据]
- **三分类原则**：[真硬编码]=故意造假/逻辑缺陷；[合理默认值]=可配置的初始值；[测试合成数据]=明确标注的测试/模拟用数据
- 测试用的合成信号（本来就该是假的）**不计为问题**，只有当测试假装在验证真实计算、恒真断言、mock 被绕过时才计入
- 本报告为只读审查产物，未修改任何源代码，未触碰 git
