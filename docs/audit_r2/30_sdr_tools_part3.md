# sdr_tools.py 第二轮深度审查 — Part 3（行 4001–6083）

- 审查范围：`mbdsdr_ai/sdr_tools.py` 第 4001–6083 行（共 2083 行）
- 审查方式：只读，逐行阅读真实代码；交叉核对 `hal.py`、`radio_control.py`、`satdump_integration.py`
- 审查日期：2026-09-24
- 标签：`[真bug]` 功能性缺陷；`[空壳]` 无真实实现/永远返回固定结果；`[占位]` 演示/合成数据，不连真实硬件；`[建议]` 安全/健壮性改进

---

## 一、重点核实项（对题目的逐条回应）

### 1. HardwareManager 每次工具调用都 new 一个（非单例） — ✅ 属实，且比报告更严重

- `sdr_tools.py:4751`（`_sdr_list_hardware`）：`mgr = HardwareManager()`
- `sdr_tools.py:4797`（`_sdr_connect_hardware`）：`mgr = HardwareManager()`
- `sdr_tools.py:4823`（`_sdr_transmit_cw`）：`mgr = HardwareManager()`

核对 `hal.py:582-587`，`HardwareManager.__init__` 每次都把 `self._active_backend = None` 重置。**没有任何单例/缓存**。

后果链：
1. 用户调用 `sdr_connect_hardware(device="hackrf")` → 在 4797 那个临时 `mgr` 实例上 `_active_backend` 被设为 SoapySDR 后端，函数返回后该实例即被 GC。
2. 用户再调用 `sdr_transmit_cw` → 4823 又 `new HardwareManager()`，`_active_backend is None` → 直接命中 4826-4827 行 `"错误: 未连接任何SDR设备，请先调用 sdr_connect_hardware"`。
3. **也就是说：`sdr_connect_hardware` 成功之后，紧接着的 `sdr_transmit_cw` 必然报"未连接"**。[真bug]

附带问题：
- `hal.py:661-664`：SoapySDR 连接失败时**静默降级到 MockSDRBackend 并返回 `success: True, device: "模拟后端(降级)"`**。真机在硬件掉线/驱动失败时，工具仍然打印"状态: 成功"。这是典型的"实验室绿、真机红"。[真bug]
- `_sdr_list_hardware` 每次调用都会在 `hal.py:598/614/628` 重新构造 `SoapySDRBackend()`、`MockSDRBackend()`、`InstrumentBackend()`，代价高且无意义。[建议]

### 2. 未 radio_connect 即可用空端口幽灵单例 — ✅ 属实

`sdr_tools.py:4941-4946`：
```python
def _get_radio():
    global _radio_instance
    if _radio_instance is None:
        from mbdsdr_ai.radio_control import RadioCAT
        _radio_instance = RadioCAT()   # ← 默认 port="", baudrate=38400
    return _radio_instance
```

核对 `radio_control.py:251-259`，`RadioCAT()` 默认 `port=""`, `_serial=None`, `_connected=False`。

而 `radio_set_frequency`(4993)、`radio_set_mode`(5006)、`radio_ptt`(5019)、`radio_send_cw`(5035) 全部直接 `radio = _get_radio()` 就用，**没有任何 `if not radio._connected: return "请先 radio_connect"` 守卫**。

更糟的是 `radio_control.py:279-298` 的 `connect()`：
```python
try:
    self._serial = serial.Serial(self.port, self.baudrate, timeout=1)
    self._connected = True
    return True
except ImportError:
    self._connected = True; return True   # 无 pyserial 也"成功"
except Exception as e:
    self._connected = True; return True    # 端口不存在/被占用也"成功"
```

**`RadioCAT.connect()` 在任何情况下都返回 True**。于是：
- 用户从没调用 `radio_connect`，直接 `radio_set_frequency` → 在空端口幽灵实例上操作，工具照样打印"状态: 已设置"。
- 用户调用 `radio_connect(port="/dev/ttyUSB9")`（设备不存在）→ 同样打印"状态: 已连接"。
- 这是 [真bug]，且是最严重的一类"假成功"。

### 3. set_frequency 返回值被丢弃、硬打"已设置" — ✅ 属实，并蔓延到 set_mode / ptt

- `sdr_tools.py:4997`：`radio.set_frequency(freq)` — 返回值（`bool`）被丢弃。
- `sdr_tools.py:5001`：`lines.append(f"状态: 已设置")` — 硬编码成功。
- 同类模式：
  - `sdr_tools.py:5010` `radio.set_mode(mode)` 返回值丢弃；`5014` 硬打"状态: 已设置"。
  - `sdr_tools.py:5024` `radio.set_ptt(on)` 返回值丢弃；没有任何失败分支。

当前 `RadioCAT.set_frequency/set_mode/set_ptt`（`radio_control.py:310-344`）**永远 return True**，所以现在丢弃返回值暂时不会出错；但一旦底层真的接入 CI-V 协议并可能失败，这一层会继续对用户撒谎。[真bug]（模式性缺陷）

对比：`_radio_send_cw`(5045) 是正确写法 — `ok = radio.send_cw_text(...)` 并在 5051 根据 ok 显示"已发送/失败"。

### 4. satdump_integration 命令注入风险

`sdr_tools.py:5326-5443` 四个工具全部委托给 `satdump_integration.py`。核对该文件：

- `satdump_integration.py:95-100, 138-143`：`subprocess.run(cmd, ...)`，`cmd` 是 **list 形式、未传 `shell=True`**。✅ 不存在 shell 元字符注入（`;`、`&&`、反引号都不会被解析）。
- 但仍有以下残留风险：
  - **参数注入（argument injection）**：`satellite`、`output_dir`、`input_file` 直接作为命令行位置参数传入。若 `satellite` 以 `-` 开头（如 `--help`、`--verbose`），会被 satdump 自身解析为开关而非位置参数。`satdump_integration.py:85-92, 130-135` 没有用 `--` 分隔位置参数，也没有用 `SATDUMP_SATELLITES` 白名单校验 satellite key。[建议]
  - **路径穿越 / 任意目录写**：`output_dir` 由 `os.makedirs(output_dir, exist_ok=True)`（`satdump_integration.py:83, 128`）任意创建；`input_file` 任意读取；`compose_cloud_image` 的 `output_file` 任意写入 PNG。`sdr_tools.py:5371, 5402, 5426` 全部来自用户入参，无校验。[建议]
  - **超时语义错误**：`satdump_integration.py:99` `timeout=30`，捕获 `TimeoutExpired` 后在 107-112 行返回 `{"success": True, "message": "接收已启动（后台运行中）"}`。但 `subprocess.run` 超时会**直接 kill 掉子进程**，并不是"后台运行"。也就是说 `satdump_live` 30 秒后必返回这个"成功已启动"，实际进程已死。[真bug]
  - `satdump_integration.py:34-38`：`find_satdump()` 用 `[path, "--help"]` 探测，path 列表硬编码；如果用户 PATH 里 `satdump` 是恶意脚本，会被无条件执行。但这是部署期问题，非运行期注入。[建议]

结论：**无 shell 命令注入**（list 形式 + 无 shell=True），但存在参数注入、路径穿越、超时假成功三类问题。

### 5. 13 个"信号分析/OpenAPI/云台/数字模式"工具真实实现状态

见下方第二节状态表。

### 6. "实验室绿、真机红"汇总

- `hal.py:661-664`：SoapySDR 失败静默降级 mock 并报 success。
- `radio_control.py:286-298`：串口打开失败静默置 `_connected=True`。
- `sdr_tools.py:4630-4640` `_gnss_monitor_band`：**永远合成假 CW 干扰**（偏移 5 kHz、幅度 0.05），与真实硬件无关。
- `sdr_tools.py:4677-4686` `_gnss_monitor_all`：硬编码 L1 频段假 CW 干扰。
- `sdr_tools.py:5222` `_lro_doppler_predict`：硬编码 `LROOrbit(altitude_km=50)`，**忽略用户传入的 altitude_km**；`station_pos` 硬编码月心 (MOON_RADIUS,0,0)。纯演示。
- `sdr_tools.py:5260-5319` `_lro_od_demo`：合成观测 + EKF 演示，名字里就有 demo。
- `sdr_tools.py:5606-5609` `_signal_identify_modulation`：无硬件时合成 FM 信号，并**诚实地**在输出里加了 `（注：无 SDR 硬件，用合成 FM 信号演示识别逻辑）`。✅ 这是正确做法。
- `sdr_tools.py:5647-5651` `_signal_extract_features`：无硬件时合成频谱，也诚实标注。✅

### 7. 空壳 / 占位识别

本范围内没有发现"直接 return '未实现'"式空壳。但以下是**演示/合成/只读静态文本**型占位：

| 工具 | 行号 | 性质 |
|---|---|---|
| `_gnss_monitor_band` | 4618-4659 | [占位] 永远合成假 IQ + 假 CW 干扰 |
| `_gnss_monitor_all` | 4662-4709 | [占位] 永远合成假 IQ，硬编码 L1 干扰 |
| `_lro_orbit_info` | 5182-5211 | [占位] 物理公式 + 硬编码建议文本，不调真实 SPICE |
| `_lro_doppler_predict` | 5214-5257 | [占位] 合成轨道推演；忽略 altitude 入参 |
| `_lro_od_demo` | 5260-5319 | [占位] 名字即 demo，合成观测跑 EKF |
| `_gimbal_modes` | 5838-5849 | [占位] 纯静态文本说明 |
| `_digital_mode_params` / `_digital_mode_frequencies` | 5995-6030 | 查表工具，真实但只读静态表 |

### 8. 安全：路径穿越 / 任意文件读写

- `sdr_tools.py:6037-6066` `_wsjtx_read_decodes`：`log_path` 由用户传入，直接 `open(log_path, 'r')`。可读取本机任意文本文件（`/etc/passwd`、`~/.ssh/id_rsa` 等）。返回内容会被截断展示，但仍构成**任意文件读**原语。[建议]（建议限制在 WSJT-X 已知目录或做扩展名/路径白名单）
- `sdr_tools.py:4180` `_aprs_send_position`：`tmp_path = os.path.join(tempfile.gettempdir(), f'mbdsdr_aprs_{src_call}.wav')`。`src_call` 来自用户 callsign，未做路径分隔符清洗；若 callsign 含 `../`，可写到 tempdir 之外。[建议]
- `satdump_integration.py:83, 128, 233`：`output_dir` / `output_file` 任意路径写。[建议]
- `_satdump_compose_image` (sdr_tools.py:5421-5443)：`input_dir` 任意 glob、`output_file` 任意保存。[建议]
- 未发现 `shell=True`、未发现 `os.system`、未发现 `eval/exec` 拼接用户输入。

### 9. 错误处理：异常静默吞掉

- `sdr_tools.py:4248-4249` `_kiss_decode`：`except Exception: pass` — AX.25 info 解码失败被静默吞掉。[建议]
- `sdr_tools.py:5604, 5637` `_signal_identify_modulation` / `_signal_extract_features`：`except Exception: iq = None` — 吞掉后端异常，然后回退合成。至少应该把异常类型记入 note。[建议]
- `sdr_tools.py:4728` `_gnss_direction_find`：对 `result.get('error', ...)` 做了防护，✅ 处理得当。
- `sdr_tools.py:6075-6076` `_wsjtx_read_decodes`：`except Exception as e: return f"读取日志失败: {e}"`，✅ 正确。
- `radio_control.py:295-298`：`except Exception: self._connected=True; return True` — 最严重的吞异常。[真bug]

---

## 二、审查范围内工具的"真实实现 / 空壳"状态表

> 范围：sdr_tools.py:4001–6083 内定义、并在文件前部（行 1086–2186）注册的工具。

| # | 工具名（注册名） | 实现函数 | 行号 | 状态 | 说明 |
|---|---|---|---|---|---|
| 1 | `sdr_aprs_encode` | `_aprs_encode` | 3998-4055 | ✅ 真实 | 真实编码 AX.25/APRS 帧，可选写 WAV |
| 2 | `sdr_aprs_decode` | `_aprs_decode` | 4058-4121 | ✅ 真实 | 解析 hex 或音频，遍历 APRS 包 |
| 3 | `sdr_aprs_send_position` | `_aprs_send_position` | 4124-4186 | ⚠️ 半真实 | 编码+写 WAV 真实，但**不真正发射**，仅落盘；callsign 未做路径清洗 |
| 4 | `sdr_kiss_encode` | `_kiss_encode` | 4189-4215 | ✅ 真实 | KISS 帧编码 |
| 5 | `sdr_kiss_decode` | `_kiss_decode` | 4218-4252 | ✅ 真实 | 解码 KISS 流；info 解码异常被吞(4248) |
| 6 | `sdr_digipeater_process` | `_digipeater_process` | 4255-4298 | ✅ 真实 | 调 `ax25.Digipeater.process_frame` |
| 7 | `time_get_info` | `_time_get_info` | 4305-4330 | ✅ 真实 | 委托 new_spacetime |
| 8 | `time_ntp_sync` | `_time_ntp_sync` | 4333-4357 | ✅ 真实 | 真实 NTP 对时，捕获 NTPError |
| 9 | `gnss_system_info` | `_gnss_system_info` | 4360-4368 | ✅ 真实 | 透传 |
| 10 | `gnss_parse_rmc` | `_gnss_parse_rmc` | 4371-4394 | ✅ 真实 | NMEA 解析 |
| 11 | `gis_distance` | `_gis_distance` | 4397-4414 | ✅ 真实 | haversine |
| 12 | `gis_bearing` | `_gis_bearing` | 4417-4437 | ✅ 真实 | |
| 13 | `gis_destination` | `_gis_destination` | 4440-4455 | ✅ 真实 | |
| 14 | `pnt_get_state` | `_pnt_get_state` | 4473-4477 | ✅ 真实（单例） | `_pnt_engine` 懒加载单例 4459-4470 |
| 15 | `pnt_update_source` | `_pnt_update_source` | 4480-4527 | ✅ 真实 | 同上，单例 |
| 16 | `satellite_predict_pass` | `_satellite_predict_pass` | 4530-4557 | ✅ 真实 | 委托 new_spacetime |
| 17 | `satellite_predict_all` | `_satellite_predict_all` | 4560-4587 | ✅ 真实 | |
| 18 | `sky_view_visible` | `_sky_view_visible` | 4590-4611 | ✅ 真实 | |
| 19 | `gnss_monitor_band` | `_gnss_monitor_band` | 4618-4659 | 🟡 [占位] | **永远合成假 IQ+假 CW**，与真实硬件无关 |
| 20 | `gnss_monitor_all` | `_gnss_monitor_all` | 4662-4709 | 🟡 [占位] | 硬编码 L1 CW 干扰 |
| 21 | `gnss_direction_find` | `_gnss_direction_find` | 4712-4737 | ✅ 真实 | 对弱模型空参数有防御 |
| 22 | `sdr_list_hardware` | `_sdr_list_hardware` | 4744-4786 | ⚠️ [真bug] | 每次 new HardwareManager(4751)；逻辑本身真实但昂贵 |
| 23 | `sdr_connect_hardware` | `_sdr_connect_hardware` | 4789-4809 | ⚠️ [真bug] | 每次 new HardwareManager(4797)；hal.py:661 失败静默降级 mock 报成功 |
| 24 | `sdr_transmit_cw` | `_sdr_transmit_cw` | 4812-4842 | 🔴 [真bug] | 每次 new HardwareManager(4823)，**刚 connect 完就必报"未连接"** |
| 25 | `platform_info` | `_platform_info` | 4845-4875 | ✅ 真实 | 调 `detect_embedded_platform()` |
| 26 | `instrument_list` | `_instrument_list` | 4878-4902 | ⚠️ 半真实 | 每次 new InstrumentBackend(4885)；空时给提示 |
| 27 | `instrument_query` | `_instrument_query` | 4905-4931 | ⚠️ [建议] | 每次 new InstrumentBackend(4913)；直接访问私有 `_resource`(4916)；自动连第一个仪器；SCPI 命令未做白名单 |
| 28 | `radio_list_ports` | `_radio_list_ports` | 4949-4961 | ✅ 真实（含回退） | 无 pyserial 时模拟端口列表 |
| 29 | `radio_connect` | `_radio_connect` | 4964-4990 | 🔴 [真bug] | `RadioCAT.connect()` 永远返回 True（radio_control.py:286-298），端口不存在也报"已连接" |
| 30 | `radio_set_frequency` | `_radio_set_frequency` | 4993-5003 | 🔴 [真bug] | 4997 丢弃返回值；5001 硬打"已设置"；无 connected 守卫；可在幽灵空端口实例上调用 |
| 31 | `radio_set_mode` | `_radio_set_mode` | 5006-5016 | 🔴 [真bug] | 同上模式（5010/5014） |
| 32 | `radio_ptt` | `_radio_ptt` | 5019-5032 | 🔴 [真bug] | 5024 丢弃返回值；无 connected 守卫；可幽灵 PTT on |
| 33 | `radio_send_cw` | `_radio_send_cw` | 5035-5055 | ⚠️ 半真实 | 5045 正确检查 ok；但仍无 connected 守卫；morse_encode 真实 |
| 34 | `morse_encode` | `_morse_encode` | 5058-5069 | ✅ 真实 | 纯函数 |
| 35 | `morse_decode` | `_morse_decode` | 5072-5083 | ✅ 真实 | 纯函数 |
| 36 | `meteor_list_sats` | `_meteor_list_sats` | 5090-5111 | ✅ 真实 | 静态表 |
| 37 | `meteor_get_params` | `_meteor_get_params` | 5114-5138 | ✅ 真实 | |
| 38 | `meteor_demod_setup` | `_meteor_demod_setup` | 5141-5179 | 🟡 [占位] | 只打印配置参考文本，**不真的配置解调器**（名字误导） |
| 39 | `lro_orbit_info` | `_lro_orbit_info` | 5182-5211 | 🟡 [占位] | 物理公式+硬编码建议文本 |
| 40 | `lro_doppler_predict` | `_lro_doppler_predict` | 5214-5257 | 🟡 [占位] | 5222 硬编码 altitude=50，忽略用户入参；月面站位置硬编码 |
| 41 | `lro_od_demo` | `_lro_od_demo` | 5260-5319 | 🟡 [占位] | 名字即 demo，合成观测跑 EKF |
| 42 | `satdump_check` | `_satdump_check` | 5326-5346 | ✅ 真实 | `find_satdump()` 探测 |
| 43 | `satdump_list_sats` | `_satdump_list_sats` | 5349-5362 | ✅ 真实 | 静态表 |
| 44 | `satdump_live` | `_satdump_live` | 5365-5393 | 🔴 [真bug] | subprocess list 形式无 shell 注入；但 30s 超时 kill 后仍报"接收已启动（后台运行中）"（satdump_integration.py:107-112） |
| 45 | `satdump_process` | `_satdump_process` | 5396-5418 | ⚠️ 半真实 | 真实 subprocess 调用；input_file/output_dir 任意路径 |
| 46 | `satdump_compose_image` | `_satdump_compose_image` | 5421-5443 | ⚠️ 半真实 | PIL 真实合成；input_dir/output_file 任意路径 |
| 47 | `sdr_cfo_correct` | `_cfo_correct` | 5471-5511 | ✅ 真实 | 委托 `cfo.estimate_and_correct`；支持传 IQ 或读后端 |
| 48 | `sdr_energy_sense` | `_energy_sense` | 5514-5561 | ✅ 真实 | EnergyDetector；对未标定噪声有友好报错 |
| 49 | `sdr_signal_detect` | `_signal_detect` | 5564-5590 | ✅ 真实 | 从 backend.read_samples 实时采集 |
| 50 | `sdr_signal_identify_modulation` | `_signal_identify_modulation` | 5593-5624 | ✅ 真实 | 无硬件时合成并**诚实标注** |
| 51 | `sdr_signal_extract_features` | `_signal_extract_features` | 5627-5666 | ✅ 真实 | 同上，诚实标注 |
| 52 | `sdr_signal_detect_interference` | `_signal_detect_interference` | 5669-5699 | ✅ 真实 | |
| 53 | `openapi_list_apis` | `_openapi_list_apis` | 5706-5724 | ✅ 真实 | 静态表 |
| 54 | `openapi_iss_position` | `_openapi_iss_position` | 5727-5744 | ✅ 真实 | 真 HTTP 请求 |
| 55 | `openapi_people_in_space` | `_openapi_people_in_space` | 5747-5764 | ✅ 真实 | |
| 56 | `openapi_weather` | `_openapi_weather` | 5767-5790 | ✅ 真实 | |
| 57 | `openapi_aircraft_nearby` | `_openapi_aircraft_nearby` | 5793-5819 | ✅ 真实 | ADS-B 真实数据 |
| 58 | `gimbal_modes` | `_gimbal_modes` | 5838-5849 | 🟡 [占位] | 纯静态文本 |
| 59 | `gimbal_connect` | `_gimbal_connect` | 5852-5877 | ✅ 真实 | 单例 `_GIMBAL_CONTROLLER`(5826-5835)；支持 manual/rotctld/board_pwm |
| 60 | `gimbal_point` | `_gimbal_point` | 5880-5899 | ✅ 真实 | |
| 61 | `gimbal_track_satellite` | `_gimbal_track_satellite` | 5902-5919 | ✅ 真实 | |
| 62 | `gimbal_read_pose` | `_gimbal_read_pose` | 5922-5931 | ✅ 真实 | |
| 63 | `gimbal_stop` | `_gimbal_stop` | 5934-5937 | ✅ 真实 | |
| 64 | `gimbal_rssi_sweep` | `_gimbal_rssi_sweep` | 5940-5987 | ✅ 真实 | 支持注入 RSSI provider |
| 65 | `digital_mode_params` | `_digital_mode_params` | 5995-6018 | ✅ 真实 | 查表 |
| 66 | `digital_mode_frequencies` | `_digital_mode_frequencies` | 6021-6030 | ✅ 真实 | 查表 |
| 67 | `wsjtx_read_decodes` | `_wsjtx_read_decodes` | 6033-6083 | ⚠️ [建议] | 真实读文件，但 `log_path` 任意路径可读 |

---

## 三、关键发现按严重程度排序

### P0 — 真 bug（功能错误 / 假成功）

1. **`sdr_tools.py:4823` + `hal.py:582`**：`HardwareManager` 非单例，导致 `sdr_connect_hardware` 成功后 `sdr_transmit_cw` 必然报"未连接"。
2. **`sdr_tools.py:4941-4946` + `radio_control.py:286-298`**：`RadioCAT` 幽灵空端口单例 + `connect()` 永不失败 → 未连接即可 set_freq/set_mode/PTT，全部报成功。
3. **`sdr_tools.py:4997, 5001, 5010, 5014, 5024`**：丢弃返回值 + 硬编码"已设置"。模式性缺陷。
4. **`hal.py:661-664`**：SoapySDR 连接失败静默降级 mock 并返回 success。真机掉线时对外报成功。
5. **`satdump_integration.py:107-112`**：`satdump_live` 30 秒超时 kill 进程后，仍返回"接收已启动（后台运行中）"。

### P1 — 占位/演示冒充真实工具

6. **`sdr_tools.py:4618-4709`** `_gnss_monitor_band` / `_gnss_monitor_all`：永远合成假 IQ 和假干扰，与真实射频无关，但输出格式逼真，极易误导 LLM 以为真在监测 GNSS 频带。
7. **`sdr_tools.py:5141-5179`** `meteor_demod_setup`：名字叫"配置解调器"，实际只打印一段配置参考文本，不做任何配置。
8. **`sdr_tools.py:5214-5319`** `_lro_doppler_predict` / `_lro_od_demo`：纯数值仿真，`_lro_doppler_predict` 还忽略用户 altitude 入参（5222 硬编码 50km）。

### P2 — 安全/健壮性

9. **`sdr_tools.py:6037-6066`** `wsjtx_read_decodes`：任意 `log_path` 可读本机任意文本文件。
10. **`satdump_integration.py:83,128,233`** + **`sdr_tools.py:5371,5402,5426`**：`output_dir`/`input_file`/`output_file` 任意路径；无白名单校验 satellite key。
11. **`sdr_tools.py:4180`**：`mbdsdr_aprs_{src_call}.wav` 未清洗 callsign，存在 tempdir 外写风险。
12. **`sdr_tools.py:4916`**：`_instrument_query` 直接访问 `inst._resource` 私有属性；SCPI 命令无白名单。
13. **`sdr_tools.py:4248, 5604, 5637`**：多处 `except Exception: pass/=None` 静默吞异常。

### P3 — 架构性观察（非本范围引入，但影响本范围工具）

14. **双 SDR Manager 并存**：本范围的 `_cfo_correct`(5471)、`_energy_sense`(5514)、`_signal_detect`(5564)、`_signal_detect_interference`(5669) 使用的是注册时注入的 `mgr`（`sdr_tools.py:1181/1240/1267/1303`），通过 `_get_backend(mgr)`(2195) 走 `mgr.get_active()`；而 `_sdr_list_hardware/_sdr_connect_hardware/_sdr_transmit_cw` 用的是 `hal.HardwareManager`。**两套 manager 互不相通**：用户通过 `sdr_connect_hardware` 连的设备，`sdr_cfo_correct` 看不到；反之亦然。这是架构层面的一致性问题，建议下一轮单独审查。

---

## 四、与 Part1/Part2 的边界说明

- 本 Part 仅覆盖 4001–6083 行。`_get_backend`(2195)、`mgr` 注入点(1181 等)、`_sdr_connect` 系列真正的主连接函数（行号 < 4000）属于 Part1/Part2 范围，本报告只在交叉引用处提及。
- 注册表里 67 个工具在本范围内有实现函数；其余工具的注册行号（1086–2186）仅用于核对"每个工具是否有真实实现"，未逐行审阅。
