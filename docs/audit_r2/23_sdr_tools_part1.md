# MBDSDR 第二轮深度代码审查 — sdr_tools.py 第 1–2000 行

> **审查范围**: `mbdsdr_ai/sdr_tools.py` 第 1–2000 行（工具注册层）
> **审查日期**: 2026-09-24
> **审查人**: 子 agent 23（MCP 工具注册层 part1）
> **文件总行数**: 6083 行（项目最大文件）
> **本范围注册工具数**: 约 114 个（第 1–2000 行内的 `agent.tool_registry.register(...)` 调用）

---

## 一、真 Bug（会导致错误行为/崩溃）

### B1. `sdr_set_frequency` 双重调用 set_frequency — 副作用执行两次
- **位置**: `sdr_tools.py:324-327`
- **代码**:
  ```python
  handler=lambda args: ToolResult(
      success=_get_backend(mgr).set_frequency(args["frequency_hz"]),
      content=f"频率已设置: ..." if _get_backend(mgr).set_frequency(args["frequency_hz"]) else "频率设置失败...",
  ),
  ```
- **问题**: `set_frequency()` 在 lambda 中被调用**两次**——一次取 `success`，一次取 `content`。真实后端每次调用都会触发 PLL 重新锁定、I²C 写入、延时等副作用。两次调用 = 双倍调谐延迟，且若后端在两次调用间状态变化（如另一个线程改了频率），结果不一致。
- **同类问题**:
  - `sdr_set_sample_rate` — `sdr_tools.py:349-352`，同样双重调用 `set_sample_rate()`。
  - `sdr_set_demod` — `sdr_tools.py:416`，同样双重调用 `set_demod()`。
- **修复建议**: 先 `ok = backend.set_frequency(freq)` 存变量，再用变量构造 ToolResult。

### B2. `gnss_direction_find` 错误路径 f-string 崩溃 — NameError
- **位置**: `sdr_tools.py:4728`
- **代码**:
  ```python
  return f"无法估算干扰源方向: {result.get('error', '样本不足') if isinstance(result, dict) else '无效样本'}。请至少提供一组有效的 {方位: RSSI} 样本。"
  ```
- **问题**: `{方位: RSSI}` 被 Python 解释为 f-string 替换字段——`方位` 是中文标识符，会被当作变量求值，但未定义，触发 `NameError: name '方位' is not defined`。开发者本意是输出字面量 `{方位: RSSI}`（字典表示法示例），应写为 `{{方位: RSSI}}`。
- **影响**: 当 `interference_direction_finding()` 返回错误或非 dict 时（即错误处理路径），工具直接崩溃，而非返回友好错误信息。
- **严重度**: 高（错误路径必崩）。

### B3. `_bookmark_goto` 忽略 set_frequency 返回值 — 越界静默成功
- **位置**: `sdr_tools.py:2392-2395`（在 helper 中，被本范围 line 510 的 `sdr_bookmark_goto` 调用）
- **代码**:
  ```python
  try:
      backend.set_frequency(int(freq))
  except Exception as e:
      return ToolResult(success=False, content=f"设置频率失败: {e}")
  ```
- **问题**: 已知问题。`set_frequency()` 越界时**返回 False 而非抛异常**，try/except 捕不到 False。函数继续执行到 line 2402 返回 `success=True, content="已跳转到..."`，但实际频率并未改变。
- **影响**: AI 以为跳到了目标频率，实际还在原频率，后续解调/解码全部错位。这是"实验室绿、真机红"的典型——模拟后端 set_frequency 总是返回 True，真机越界返回 False。

### B4. `_spectrum_analyze` 硬编码 threshold_db=-60 — 未标定尺度
- **位置**: `sdr_tools.py:2866`
- **代码**: `signals = spec.find_signals(spectrum, threshold_db=-60)`
- **问题**: 已知问题（相关）。`powers_db` 是 FFT 幅度的 dB（相对值，非 dBm），未经标定。-60 dB 在未标定尺度下可能把整带判成信号或把所有信号漏掉。`_spectrum_find_signals`（line 2936）默认也是 -60。
- **对比**: `_measure_signal`（line 3531）和 `_signal_detect`（line 5577）正确使用 `noise_floor_db + offset` 的相对门限，不受此 bug 影响。

### B5. `_ai_sweep` / `_ai_find_center` 循环中忽略 set_frequency 返回值
- **位置**: `sdr_tools.py:3558`（_ai_sweep）、`sdr_tools.py:3596`（_ai_find_center）
- **问题**: 在步进扫频循环中直接 `backend.set_frequency(current)`，不检查返回值。若某个步点频率越界被后端拒绝，该步的采样实际仍是上一个有效频率，功率测量错误，但工具继续运行并报告错误的扫描结果。
- **影响**: 扫频结果可能包含"假峰"（实际是上一个有效频率的信号被重复报告）。

---

## 二、空壳/占位工具（不执行真实硬件操作）

### S1. `sdr_get_gps` — 永远返回硬编码模拟数据
- **位置**: `sdr_tools.py:971`（注册），helper `_get_gps` 在 `sdr_tools.py:3457-3458`
- **代码**:
  ```python
  def _get_gps(mgr):
      return "GPS 定位（需自研 ai-sdr Mini 设备连接）\n当前为模拟后端，实际数据需连接 ATGM336H 模块\n模拟数据: 43.82°N, 125.32°E, 海拔 250m, 12 星, HDOP 0.8"
  ```
- **判定**: **[空壳]**。不读取 mgr，不检查设备连接状态，不访问串口/I²C。无论连接什么设备，永远返回长春坐标。描述里写"需要设备带 GPS 模块"但实际不做任何硬件交互。

### S2. `sdr_get_imu` — 永远返回硬编码模拟数据
- **位置**: `sdr_tools.py:979`（注册），helper `_get_imu` 在 `sdr_tools.py:3460-3461`
- **代码**:
  ```python
  def _get_imu(mgr):
      return "IMU 姿态...\n当前为模拟后端...\n模拟数据: 加速度(0,0,1)g, 角速度(0,0,0)°/s, 航向 0°"
  ```
- **判定**: **[空壳]**。同 S1，永远返回固定值，不读 BMI260/TMAG5273。

### S3. `gnss_monitor_band` — 永远生成合成随机 IQ
- **位置**: `sdr_tools.py:1639`（注册），helper `_gnss_monitor_band` 在 `sdr_tools.py:4618-4659`
- **代码**（line 4630-4640）:
  ```python
  # 生成模拟IQ数据（模拟模式下）
  n_samples = int(sample_rate * duration)
  iq = (np.random.randn(n_samples) + 1j * np.random.randn(n_samples)) / np.sqrt(2) * 0.01
  if band_name in GNSS_BANDS:
      t = np.arange(n_samples) / sample_rate
      interference = 0.05 * np.exp(2j * np.pi * 5000 * t)  # 强制加模拟CW干扰
      iq = iq + interference
  ```
- **判定**: **[空壳]**。不连接 SDR 设备，不采集真实数据。每次调用都生成高斯白噪 + 人为注入的 5kHz CW 干扰，然后跑分析算法。结果恒定（总有一个 CW 干扰在 5kHz 偏移处）。描述写"监测 GNSS 频带干扰"但实际是合成数据演示。
- **"实验室绿、真机红"**: 在模拟后端通过测试，但真机上永远不会调用真实采集。

### S4. `gnss_monitor_all` — 同上，合成随机 IQ
- **位置**: `sdr_tools.py:1656`（注册），helper `_gnss_monitor_all` 在 `sdr_tools.py:4662-4709`
- **判定**: **[空壳]**。为每个 GNSS 频带生成随机 IQ，并在 L1 频带强制注入 3kHz CW 干扰（line 4683-4685）。结果永远报告 L1 有干扰。

### S5. `sdr_decode_aprs` — 依赖外部 direwolf，无则仅给安装提示
- **位置**: `sdr_tools.py:847`（注册），helper `_decode_aprs` 在 `sdr_tools.py:3214-3230`
- **判定**: **[占位]**。若 direwolf 未安装，输出"direwolf 未安装，无法真解码"和安装命令。有 direwolf 时走外部进程。本身不做 AFSK 解调。

### S6. `sdr_decode_ft8` — 仅 lite 音峰检测，不做 LDPC 译码
- **位置**: `sdr_tools.py:832`（注册），helper `_decode_ft8` 在 `sdr_tools.py:3188-3212`
- **判定**: **[部分实现]**。用 `ft8_lite.analyze_ft8_audio` 做音峰检测和 SNR 估计，但呼号还原明确标注"需 jt9/wsjtx（LDPC 译码）"。能回答"有没有 FT8 信号"，但不能给出呼号报文。

---

## 三、安全问题

### SEC1. 任意文件读写（无路径限制）
以下工具接受用户/LLM 提供的文件路径，无白名单、无路径规范化、无 `..` 过滤：

| 工具 | 位置 | 风险 |
|------|------|------|
| `sdr_open_iq_file` | line 288 | 读取任意文件作为 IQ 回放源 |
| `sdr_record_start` | line 710 (`save_path`) | 写入到任意路径 |
| `sdr_spectrum_screenshot` | line 647 (`save_path`) | 写入 PNG 到任意路径 |
| `sdr_decode_sstv` | line 802 (`input_path`/`output_path`) | 读任意 wav，写任意 PNG |
| `sdr_analyze_recording` | line 1209 (`file_path`) | 读取任意文件（np.fromfile） |
| `wsjtx_read_decodes` | line 1806 (`log_path`) | 读取任意文本文件 |

- **对比**: `_spill_save`（line 163）和 `_spill_read`（line 179）正确做了文件名白名单过滤（`isalnum() or "-_."`），是正面案例。
- **建议**: 对 `~/.mbdsdr/` 以外的写入路径加警告；或限制 `file_path` 参数必须在已知录制目录下。

### SEC2. `instrument_query` — 任意 SCPI 命令注入
- **位置**: `sdr_tools.py:1891`（注册），helper `_instrument_query` 在 `sdr_tools.py:4905-4931`
- **问题**: 直接把 LLM 提供的 `scpi_command` 字符串发送给连接的仪器，无命令白名单。恶意 prompt 可发送 `*RST`（复位）、`:OUTP OFF`（关输出）、甚至 `:SYST:ERR:ALL?` 之外的危险命令。
- **严重度**: 中（需要仪器已通过 VISA 连接；但 MCP 场景下 LLM 可被诱导发送任意命令）。

### SEC3. `radio_connect` — 连接任意串口
- **位置**: `sdr_tools.py:1921`
- **问题**: LLM 可指定 `port="/dev/ttyUSB0"` 或任意设备节点。若系统上其他串口设备（如 Arduino、GPS、USB 鼠标在 CDC 模式）存在，连接可能干扰其他设备。
- **严重度**: 低（需要物理串口存在）。

---

## 四、错误处理问题

### E1. 裸 except / 静默吞异常
| 位置 | 代码 | 问题 |
|------|------|------|
| line 2384-2385 | `except Exception: pass`（_bookmark_goto set_demod） | set_demod 失败静默忽略，不报告 |
| line 2390-2391 | `except Exception: pass`（_bookmark_goto set_bandwidth） | 同上 |
| line 2316-2317 | `except Exception: pass`（_sweep_scan_tool 恢复频率） | 恢复频率失败静默，可能留在错误频率 |
| line 3139-3140 | `except Exception: pass`（_decode_sstv fallback） | 新解码器异常被吞，fallback 到旧实现但不记录错误 |
| line 3007-3008 | `except Exception: pass`（_recordings_list 读 sidecar） | JSON 解析失败静默，可接受 |
| line 3800-3801 | `except Exception: pass`（_analyze_recording 读 sidecar） | 同上 |
| line 5604-5605 | `except Exception: iq = None`（_signal_identify_modulation） | 降级到合成数据，但不告知用户降级原因 |

### E2. 录制启动不检查返回值
- **位置**: `sdr_tools.py:2981`（_record_start）
- **代码**: `backend.start_recording(save_path, ...)` — 不检查返回值，line 2982 直接返回"开始录制"。
- **问题**: 若磁盘满、路径不可写、格式不支持，工具仍报告成功。

---

## 五、参数问题

### P1. `sdr_record_start` 有未文档化参数
- **位置**: `sdr_tools.py:2974-2975`
- **问题**: helper 读取 `gain = args.get("gain", 1.0)` 和 `decimation = args.get("decimation", 1)`，但工具参数 schema（line 712-719）只声明了 `duration`/`format`/`save_path`。LLM 看不到这两个参数，无法控制。

### P2. `sdr_get_frequency` 重复调用 get_frequency
- **位置**: `sdr_tools.py:335`
- **代码**: `f"当前频率: {_get_backend(mgr).get_frequency():.0f} Hz ({_get_backend(mgr).get_frequency()/1e6:.3f} MHz)"`
- **问题**: 调用 `get_frequency()` 两次。虽然后端 get_frequency 通常是读缓存无副作用，但模式不一致，且若 get_frequency 有 RPC 开销（如网络 SDR），双倍延迟。

### P3. `threshold_db=-60` 默认值语义错误
- **位置**: 
  - `sdr_spectrum_find_signals` 注册 line 668（默认 -60）
  - `_spectrum_find_signals` line 2936（默认 -60）
  - `_spectrum_analyze` line 2866（硬编码 -60）
  - `_detect_fhss` line 3483（默认 -60）
- **问题**: 这些 -60 是"绝对 dB"语义，但 `powers_db` 是未标定 FFT dB（范围通常 -80~0 或 -100~-20），不同设备/采样率下噪声底不同。正确做法是 `noise_floor_db + offset`（如 `signal_detect` line 5577 所做）。

---

## 六、工具真实实现/空壳状态总表（本范围 1–2000 行）

> 图例: ✅ 真实实现 | ⚠️ 真实实现但有 bug | 📦 空壳/占位 | 🔌 外部依赖

| # | 工具名 | 注册行 | helper 行 | 状态 | 备注 |
|---|--------|--------|-----------|------|------|
| 1 | sdr_plan | 73 | inline | ✅ | 纯 echo 计划文本 |
| 2 | sdr_todo | 125 | _todo_handler | ✅ | 结构化任务清单 |
| 3 | sdr_spill_save | 197 | 158 | ✅ | 文件名白名单过滤 |
| 4 | sdr_spill_read | 212 | 177 | ✅ | 分页读回 |
| 5 | sdr_connect | 229 | inline | ✅ | |
| 6 | sdr_disconnect | 246 | 2199 | ✅ | |
| 7 | sdr_list_devices | 254 | inline | ✅ | |
| 8 | sdr_switch_device | 262 | inline | ✅ | |
| 9 | sdr_status | 279 | 2207 | ✅ | |
| 10 | sdr_open_iq_file | 287 | 2227 | ✅ | 读任意路径 [SEC1] |
| 11 | sdr_set_frequency | 314 | inline | ⚠️ | **双重调用 B1** |
| 12 | sdr_get_frequency | 331 | inline | ⚠️ | 双重调用 [P2] |
| 13 | sdr_set_sample_rate | 339 | inline | ⚠️ | **双重调用 B1** |
| 14 | sdr_set_bandwidth | 356 | inline | ✅ | |
| 15 | sdr_set_gain | 374 | inline | ✅ | |
| 16 | sdr_set_agc | 388 | inline | ✅ | |
| 17 | sdr_set_demod | 406 | inline | ⚠️ | **双重调用 B1** |
| 18 | sdr_set_squelch | 420 | inline | ✅ | |
| 19 | sdr_set_volume | 434 | inline | ✅ | |
| 20 | sdr_spectrum_analyze | 452 | 2856 | ⚠️ | **硬编码 -60 B4** |
| 21 | sdr_sweep_scan | 467 | 2259 | ✅ | 恢复频率有裸 except [E1] |
| 22 | sdr_bookmark_list | 492 | 2353 | ✅ | |
| 23 | sdr_bookmark_goto | 510 | 2365 | ⚠️ | **set_freq 返回值忽略 B3** |
| 24 | sdr_bookmark_add | 528 | 2406 | ✅ | |
| 25 | sdr_watch_capture | 546 | 2421 | ✅ | 守听+触发前缓冲 |
| 26 | sdr_adsb_decode | 572 | 2518 | ✅ | 实时采集版 |
| 27 | sdr_rds_decode | 596 | 2615 | ✅ | 57kHz BPSK 解码 |
| 28 | sdr_spectrum_zoom | 619 | 2882 | ✅ | 仅视图状态 |
| 29 | sdr_spectrum_pan | 633 | 2890 | ✅ | 仅视图状态 |
| 30 | sdr_spectrum_screenshot | 647 | 2898 | ✅ | matplotlib Agg |
| 31 | sdr_spectrum_find_signals | 662 | 2928 | ⚠️ | **默认 -60 未标定 P3** |
| 32 | sdr_spectrum_center_offset | 677 | 2946 | ✅ | 抛物线插值 |
| 33 | sdr_spectrum_text | 691 | 2958 | ✅ | ASCII 频谱 |
| 34 | sdr_record_start | 709 | 2968 | ⚠️ | 未文档化参数 [P1]；不查返回值 [E2] |
| 35 | sdr_record_stop | 725 | 2984 | ✅ | |
| 36 | sdr_recordings_list | 733 | 2992 | ✅ | |
| 37 | sdr_decode_noaa_apt | 751 | 2730 | ✅ | |
| 38 | sdr_wfm_stereo | 778 | 3020 | ✅ | 立体声复合解码 |
| 39 | sdr_decode_sstv | 802 | 3118 | ✅ | 双解码器 fallback |
| 40 | sdr_decode_cw | 817 | 3156 | ✅ | 包络+cw_decoder |
| 41 | sdr_decode_ft8 | 832 | 3188 | 📦 | **lite 检测，无 LDPC [S6]** |
| 42 | sdr_decode_aprs | 847 | 3214 | 🔌 | **需外部 direwolf [S5]** |
| 43 | sdr_decode_adsb | 862 | 3232 | ✅ | numpy DF17 纯 Python |
| 44 | sdr_satellite_sky_view | 882 | 3256 | ✅ | sgp4 |
| 45 | sdr_satellite_doppler | 899 | 3288 | ✅ | |
| 46 | sdr_satellite_passes | 916 | 3314 | ✅ | |
| 47 | sdr_satellite_doppler_track | 935 | 3337 | ✅ | 跟踪录制 |
| 48 | sdr_get_gps | 971 | 3457 | 📦 | **永远返回长春坐标 [S1]** |
| 49 | sdr_get_imu | 979 | 3460 | 📦 | **永远返回固定值 [S2]** |
| 50 | sdr_identify_modulation | 991 | 3463 | ✅ | 特征提取 |
| 51 | sdr_detect_fhss | 1005 | 3474 | ⚠️ | threshold_db=-60 [P3] |
| 52 | sdr_measure_signal | 1021 | 3520 | ✅ | 正确用 noise_floor+10 |
| 53 | sdr_ai_sweep | 1040 | 3544 | ⚠️ | **忽略 set_freq 返回值 B5** |
| 54 | sdr_ai_find_center | 1058 | 3583 | ⚠️ | **忽略 set_freq 返回值 B5** |
| 55 | openapi_list_apis | 1078 | 5706 | ✅ | |
| 56 | openapi_iss_position | 1090 | 5727 | ✅ | |
| 57 | openapi_people_in_space | 1102 | 5747 | ✅ | |
| 58 | openapi_weather | 1114 | 5767 | ✅ | |
| 59 | openapi_aircraft_nearby | 1129 | 5793 | ✅ | |
| 60 | sdr_iq_correct | 1149 | 3623 | ✅ | DC+I/Q 校正 |
| 61 | sdr_cfo_correct | 1166 | 5471 | ✅ | FFT粗估+Kay精估 |
| 62 | sdr_demodulate | 1189 | 3697 | ✅ | |
| 63 | sdr_analyze_recording | 1209 | 3784 | ✅ | 读任意文件 [SEC1] |
| 64 | signal_detect | 1229 | 5564 | ✅ | 相对门限，正确 |
| 65 | energy_sense | 1244 | 5514 | ✅ | H0/H1 判决 |
| 66 | signal_identify_modulation | 1271 | 5593 | ⚠️ | 无硬件时静默降级合成数据 |
| 67 | signal_extract_features | 1283 | 5627 | ⚠️ | 同上 |
| 68 | signal_detect_interference | 1295 | 5669 | ✅ | 归一化到噪声底 |
| 69 | sdr_encode_ax25 | 1311 | 3887 | ✅ | |
| 70 | sdr_decode_ax25 | 1330 | 3945 | ✅ | |
| 71 | sdr_aprs_encode | 1344 | 3998 | ✅ | |
| 72 | sdr_aprs_decode | 1364 | 4058 | ✅ | |
| 73 | sdr_aprs_send_position | 1378 | 4124 | ✅ | |
| 74 | sdr_kiss_encode | 1398 | 4189 | ✅ | |
| 75 | sdr_kiss_decode | 1414 | 4218 | ✅ | |
| 76 | sdr_digipeater_process | 1428 | 4255 | ✅ | |
| 77 | time_get_info | 1449 | 4305 | ✅ | |
| 78 | time_ntp_sync | 1462 | 4333 | ✅ | |
| 79 | gnss_system_info | 1476 | 4360 | ✅ | |
| 80 | gnss_parse_rmc | 1489 | 4371 | ✅ | |
| 81 | gis_distance | 1503 | 4397 | ✅ | Haversine |
| 82 | gis_bearing | 1520 | 4417 | ✅ | |
| 83 | gis_destination | 1537 | 4440 | ✅ | |
| 84 | pnt_get_state | 1554 | 4473 | ✅ | |
| 85 | pnt_update_source | 1562 | 4480 | ✅ | |
| 86 | satellite_predict_pass | 1582 | 4530 | ✅ | |
| 87 | satellite_predict_all | 1601 | 4560 | ✅ | |
| 88 | sky_view_visible | 1619 | 4590 | ✅ | |
| 89 | gnss_monitor_band | 1639 | 4618 | 📦 | **合成随机 IQ [S3]** |
| 90 | gnss_monitor_all | 1656 | 4662 | 📦 | **合成随机 IQ [S4]** |
| 91 | gnss_direction_find | 1671 | 4712 | ⚠️ | **f-string 崩溃 B2** |
| 92 | gimbal_modes | 1690 | 5838 | ✅ | |
| 93 | gimbal_connect | 1698 | 5852 | ✅ | |
| 94 | gimbal_point | 1714 | 5880 | ✅ | |
| 95 | gimbal_track_satellite | 1730 | 5902 | ✅ | |
| 96 | gimbal_read_pose | 1746 | 5922 | ✅ | |
| 97 | gimbal_stop | 1754 | 5934 | ✅ | |
| 98 | gimbal_rssi_sweep | 1762 | 5940 | ✅ | |
| 99 | digital_mode_params | 1784 | 5995 | ✅ | 查表 |
| 100 | digital_mode_frequencies | 1798 | 6021 | ✅ | 查表 |
| 101 | wsjtx_read_decodes | 1806 | 6033 | ✅ | 读任意路径 [SEC1] |
| 102 | sdr_list_hardware | 1825 | 4744 | ✅ | |
| 103 | sdr_connect_hardware | 1837 | 4789 | ✅ | SoapySDR |
| 104 | sdr_transmit_cw | 1851 | 4812 | ✅ | TX 能力检查 |
| 105 | platform_info | 1867 | 4845 | ✅ | |
| 106 | instrument_list | 1879 | 4878 | ✅ | |
| 107 | instrument_query | 1891 | 4905 | ⚠️ | **任意 SCPI [SEC2]** |
| 108 | radio_list_ports | 1909 | 4949 | ✅ | |
| 109 | radio_connect | 1921 | 4964 | ⚠️ | 任意串口 [SEC3] |
| 110 | radio_set_frequency | 1937 | 4993 | ✅ | 不查返回值 |
| 111 | radio_set_mode | 1951 | 5006 | ✅ | 不查返回值 |
| 112 | radio_ptt | 1965 | 5019 | ✅ | |
| 113 | radio_send_cw | 1979 | 5035 | ✅ | |
| 114 | morse_encode | 1994 | 5058 | ✅ | |

---

## 七、统计汇总

| 类别 | 数量 | 占比 |
|------|------|------|
| ✅ 真实实现（无 bug） | 89 | 78% |
| ⚠️ 真实实现但有 bug | 16 | 14% |
| 📦 空壳/占位 | 5 | 4% |
| 🔌 外部依赖占位 | 1 | 1% |
| 其他（纯 echo/查表） | 3 | 3% |
| **合计** | **114** | 100% |

**真 bug 计数**: B1（3处双重调用）、B2（f-string 崩溃）、B3（set_freq 返回值忽略）、B4（硬编码 -60）、B5（2处循环忽略返回值）= **8 处真 bug**

**空壳计数**: 5 个（gps/imu/gnss_monitor_band/gnss_monitor_all/ft8-lite）

**安全风险**: 3 类（任意文件读写、SCPI 注入、任意串口连接）

---

## 八、优先修复建议

1. **P0 — B2 f-string 崩溃**（line 4728）：`{方位: RSSI}` → `{{方位: RSSI}}`。一行修复，错误路径必崩。
2. **P0 — B1 双重调用**（lines 324, 349, 416）：把 set_frequency/set_sample_rate/set_demod 结果存局部变量。真实硬件上双倍调谐延迟。
3. **P1 — B3/B5 set_frequency 返回值检查**：所有调用 `backend.set_frequency()` 处检查 False 返回并报错，不要静默成功。
4. **P1 — B4/P3 threshold_db 尺度**：把 -60 绝对门限改为 `noise_floor_db + offset` 相对门限，与 `signal_detect`（line 5577）对齐。
5. **P2 — S1/S2 GPS/IMU 空壳**：至少在未连接真实设备时返回 `success=False` 或标注"模拟数据"，不要让 AI 误以为是真实定位。
6. **P2 — S3/S4 GNSS monitor 空壳**：在工具描述或返回结果中明确标注"合成演示数据"，避免 AI 报告"检测到干扰"误导用户。
