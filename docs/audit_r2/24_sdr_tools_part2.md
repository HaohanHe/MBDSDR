# sdr_tools.py 第二轮深度审查 · Part 2（第 2001–4000 行）

- 审查对象：`mbdsdr_ai/sdr_tools.py` 行 2001–4000（共 6083 行文件）
- 审查方式：只读，逐行阅读真实代码；跨文件核实后端/解码器/频谱实现
- 跨文件核实点：
  - `sdr_backend.py:101-108` `set_frequency` 基类实现
  - `decoders.py:720-829` `decode_digital_mode`
  - `spectrum_processor.py:73-145` `compute_spectrum` / `find_signals`

---

## 一、四个已知问题核实结论（全部命中）

### [真bug] 1. `set_frequency` 越界返回 False 被吞掉 —— 已确认
**位置：`sdr_tools.py:2392-2395`（`_bookmark_goto`）**

```python
2392    try:
2393        backend.set_frequency(int(freq))
2394    except Exception as e:
2395        return ToolResult(success=False, content=f"设置频率失败: {e}")
```

核实后端基类 `sdr_backend.py:101-108`：

```python
101  def set_frequency(self, freq_hz: float) -> bool:
103      if not self.status.connected:
104          return False
105      if freq_hz < self.device.frequency_range[0] or freq_hz > self.device.frequency_range[1]:
106          return False          # ← 越界/未连接时返回 False，不抛异常
107      self.status.frequency_hz = freq_hz
108      return True
```

`set_frequency` 在**未连接或越界时返回 `False` 而不抛异常**。`_bookmark_goto` 只 `except Exception`，**完全忽略返回值**。后果：当书签频率落在设备范围外（或设备未连接），`set_frequency` 静默失败，代码继续走到 2402 行返回 `ToolResult(success=True, content=f"已跳转到 {b.name}…频率 {freq/1e6:.4f} MHz")` —— **向 Agent 谎报"已跳转成功"，实际频率根本没动**。这正是"实验室绿、真机红"：模拟后端频率范围宽、书签频率总在范围内，永远测不出来；真机（RTL-SDR/Pluto 等）有硬边界时即暴露。

同类问题（同样忽略 `set_frequency` 的 bool 返回值）还有：
- `sdr_tools.py:2445`（`_watch_capture_tool`）
- `sdr_tools.py:2555`（`_adsb_decode_tool` 实时采集分支）
- `sdr_tools.py:2650`（`_rds_decode_tool`）
- `sdr_tools.py:2804`（`_noaa_apt_decode_tool`）
- `sdr_tools.py:3057`（`_wfm_stereo_tool`）
- `sdr_tools.py:3395`（`_satellite_doppler_track`）
- `sdr_tools.py:3558 / 3572`（`_ai_sweep`）
- `sdr_tools.py:3596 / 3611`（`_ai_find_center`）
其中 2555/2650/2804/3057 包在 try/except 里，同样吞不掉"返回 False"这一路径。

### [真bug] 2. `find_signals(threshold_db=-60)` 叠加未标定尺度 —— 已确认
**位置：`sdr_tools.py:2866`（`_spectrum_analyze`）**

```python
2866    signals = spec.find_signals(spectrum, threshold_db=-60)
```

核实 `spectrum_processor.py:107-108`：

```python
107  powers = np.abs(spectrum) ** 2
108  powers_db = 10 * np.log10(powers + 1e-12)   # 未标定的 FFT bin 功率，非 dBm
```

`powers_db` 是**未标定的相对 FFT bin 功率**：量纲随 FFT 点数、窗函数、样本幅度（归一化 [-1,1] vs int16/32768）、采样率变化，**没有任何 dBm 校准常数**。在此尺度上写死 `-60` 没有物理意义：
- 归一化 [-1,1] 噪声样本，1024 点 FFT 的 bin 功率约 `10*log10(1024)` ≈ +30 dB 量级，噪声底约 -30~-40 dB → `-60` 门限会**把几乎所有 bin 都判成"信号"**；
- 若样本来自 cs16/int16 路径（幅度 ~32768），功率整体抬升约 +90 dB → 同样的 `-60` 又可能**什么都检不出**。

对比同文件正确做法：
- `sdr_tools.py:3531` `_measure_signal`：`threshold_db=spectrum.noise_floor_db + 10`（相对噪声底，正确）
- `sdr_tools.py:2305` `sweep_scan` / `2467` `watch_capture`：用自适应 `margin_db`（正确）

而同函数族里仍写死绝对值的还有：
- `sdr_tools.py:2936` `_spectrum_find_signals`：`threshold = args.get("threshold_db", -60)`（默认 -60，同样未标定）
- `sdr_tools.py:3483` `_detect_fhss`：`threshold_db = args.get("threshold_db", -60)`
- `sdr_tools.py:3842` `_analyze_recording`：`spec.detect_signals(spectrum, threshold_db=-40)`

这些 `-60/-40` 都是"看起来像 dBm、实际是任意尺度"的伪标定值，是典型实验室自洽、真机漂移的问题。

### [真bug] 3. APRS 工具读取从不赋值的 `decoded_frames` —— 已确认
**位置：`sdr_tools.py:3224`（`_decode_aprs`）**

```python
3222    if result.get("external_tools_available"):
3223        output += f"状态: 已检测到 direwolf，真解码路径已就绪\n"
3224        output += f"结果: {result.get('decoded_frames', [])}\n"
```

核实 `decoders.py:803-807`（`decode_digital_mode` 的 aprs 分支）：

```python
803  elif mode == "aprs":
804      result["note"] = (
805          "APRS 为 1200 波特 AFSK(Bell202) over AX.25；内置 ax25.py 已具备 "
806          "FM 鉴频+数字 PLL+CRC-16 能力（见 exp_ax25_performance），direwolf 为可选增强。"
807      )
```

全仓库 grep `decoded_frames` 仅命中 `sdr_tools.py:3224` 一处（读取方），**没有任何赋值方**。APRS 分支只写了一条 note，既没有真调内置 AFSK 解码器，也没有 subprocess 调 direwolf。后果：
- `result.get('decoded_frames', [])` **恒为 `[]`**；
- 更糟的是 3222 行：一旦检测到 direwolf，就打印"真解码路径已就绪"，紧接着打印"结果: []" —— **对外谎称解码就绪，实际一个帧都没解**。这是"假装成功"型空壳，比直接报错更危险（Agent 会以为 APRS 正常工作）。

### [占位] 4. `_get_gps` 直接返回硬编码坐标 —— 已确认
**位置：`sdr_tools.py:3457-3458`**

```python
3457  def _get_gps(mgr):
3458      return "GPS 定位（需自研 ai-sdr Mini 设备连接）\n当前为模拟后端，实际数据需连接 ATGM336H 模块\n模拟数据: 43.82°N, 125.32°E, 海拔 250m, 12 星, HDOP 0.8"
```

- 函数**完全忽略 `mgr` 参数**，不读任何后端/串口/硬件状态，无条件返回同一段硬编码字符串（长春坐标 43.82N/125.32E）。
- 注册处 `sdr_tools.py:975` 用 `ToolResult(success=True, ...)` 包裹 —— **永远 success=True**，真机接上 ATGM336H 也不会读真实数据。
- 紧邻的 `_get_imu`（3460-3461）同样是硬编码模拟字符串（加速度(0,0,1)g…）。
- 注：函数文本里写了"当前为模拟后端"，算半诚实标注，但 `success=True` 包装使其在工具列表里表现为"可用真工具"，对 Agent 有误导。归为 [占位]。

---

## 二、范围内工具"真实实现 / 空壳"状态表

### A. 本范围内**定义实现**的工具（行 2195–4000）

| 工具名（注册行） | 实现函数（行） | 状态 | 备注 |
|---|---|---|---|
| `sdr_open_iq_file` (288) | `_tool_open_iq_file` (2227) | ✅ 真实 | 真实调 `mgr.open_iq_file`，回放信息完整 |
| `sdr_sweep_scan` (468) | `_sweep_scan_tool` (2259) | ✅ 真实 | 步进调谐+`sweep_scan`，finally 恢复频率；真机注意 set_frequency 返回值忽略 |
| `sdr_bookmark_list` (493) | `_bookmark_list` (2353) | ✅ 真实 | 读书签库 |
| `sdr_bookmark_goto` (511) | `_bookmark_goto` (2365) | ⚠️ 有真bug | 实现真实，但 2392-2395 越界谎报成功（见上 §一.1） |
| `sdr_bookmark_add` (529) | `_bookmark_add` (2406) | ✅ 真实 | |
| `sdr_watch_capture` (547) | `_watch_capture_tool` (2421) | ✅ 真实 | 触发抓帧+存 cu8/cf32+sidecar；真机 set_frequency 返回值忽略 |
| `sdr_adsb_decode` (573) | `_adsb_decode_tool` (2518) | ✅ 真实 | 实时/离线双路径调 `decode_adsb`，CRC 统计真实 |
| `sdr_rds_decode` (597) | `_rds_decode_tool` (2615) | ✅ 真实 | 正交鉴频+`decode_rds`，诚实报告未同步 |
| `sdr_spectrum_analyze` (453) | `_spectrum_analyze` (2856) | ⚠️ 有真bug | 2866 写死 threshold=-60（见 §一.2）；其余真实 |
| `sdr_spectrum_zoom` (620) | `_spectrum_zoom` (2882) | ✅ 真实 | |
| `sdr_spectrum_pan` (634) | `_spectrum_pan` (2890) | ✅ 真实 | |
| `sdr_spectrum_screenshot` (648) | `_spectrum_screenshot` (2898) | ✅ 真实 | 真用 matplotlib Agg 出 PNG，失败有降级提示 |
| `sdr_spectrum_find_signals` (663) | `_spectrum_find_signals` (2928) | ⚠️ 尺度问题 | 默认 threshold=-60 未标定（见 §一.2） |
| `sdr_spectrum_center_offset` (678) | `_spectrum_center_offset` (2946) | ✅ 真实 | |
| `sdr_spectrum_text` (692) | `_spectrum_text` (2958) | ✅ 真实 | |
| `sdr_record_start` (710) | `_record_start` (2968) | ✅ 真实 | 委托 backend.start_recording |
| `sdr_record_stop` (726) | `_record_stop` (729→2984) | ✅ 真实 | |
| `sdr_recordings_list` (734) | `_recordings_list` (2992) | ✅ 真实 | 读 ~/.mbdsdr/recordings |
| `sdr_decode_noaa_apt` (752) | `_noaa_apt_decode_tool` (2730) | ✅ 真实 | 实时/离线IQ/WAV 三路径 + `lite_decode_apt`，出 A/B 云图 |
| `sdr_wfm_stereo` (779) | `_wfm_stereo_tool` (3020) | ✅ 真实 | 真出双声道 48k WAV，诚实报单声道 |
| `sdr_decode_noaa_apt`（旧/文件） | `_decode_noaa_apt` (3101) | ✅ 真实 | 委托 `decode_noaa_apt` |
| `sdr_decode_sstv` (803) | `_decode_sstv` (3118) | ✅ 真实 | 优先内置 sstv_decoder，异常回退旧实现；3139 裸 except 仅做 fallback |
| `sdr_decode_cw` (818) | `_decode_cw` (3156) | ✅ 真实 | wave 解析+cw_decoder |
| `sdr_decode_ft8` (833) | `_decode_ft8` (3188) | ⚠️ 半真实/诚实 | 只做音峰+8FSK 硬判决，**明确说明呼号还原需 jt9**（不伪造）；属诚实降级而非空壳 |
| `sdr_decode_aprs` (848) | `_decode_aprs` (3214) | ❌ 空壳/误导 | `decoded_frames` 恒空却报"真解码就绪"（§一.3） |
| `sdr_decode_adsb` (863) | `_decode_adsb` (3232) | ✅ 真实 | 内置 numpy Mode-S 解码 |
| `sdr_satellite_sky_view` (883) | `_satellite_sky_view` (3256) | ✅ 真实 | sgp4 `visible_satellites` |
| `sdr_satellite_doppler` (900) | `_satellite_doppler` (3288) | ✅ 真实 | sgp4 多普勒 |
| `sdr_satellite_passes` (917) | `_satellite_passes` (3314) | ✅ 真实 | sgp4 过境预测 |
| `sdr_satellite_doppler_track` (936) | `_satellite_doppler_track` (3337) | ✅ 真实 | 分段多普勒补偿+流式录 cf32+sidecar；真机 set_frequency 返回值忽略 |
| `sdr_get_gps` (972) | `_get_gps` (3457) | 🚫 占位 | 硬编码坐标，success=True（§一.4） |
| `sdr_get_imu` (980) | `_get_imu` (3460) | 🚫 占位 | 硬编码 IMU 模拟值 |
| `sdr_identify_modulation` (992) | `_identify_modulation` (3463) | ✅ 真实 | 委托 `spec.extract_modulation_features` |
| `sdr_detect_fhss` (1006) | `_detect_fhss` (3474) | ⚠️ 尺度问题 | 真调 `detect_fhss`，但默认 threshold=-60 未标定 |
| `sdr_measure_signal` (1022) | `_measure_signal` (3520) | ✅ 真实 | 正确用 noise_floor+10 相对门限 |
| `sdr_ai_sweep` (1041) | `_ai_sweep` (3544) | ⚠️ 真机风险 | 真实循环调谐，但 set_frequency 返回值忽略、read_samples(512) 极小；真机 PLL 稳定/采样对齐需验证 |
| `sdr_ai_find_center` (1059) | `_ai_find_center` (3583) | ⚠️ 真机风险 | 同上；且每步 sleep 20ms 可能不足以等 PLL |
| `sdr_iq_correct` (1150) | `_iq_correct` (3623) | ⚠️ 有真bug | 实现真实，但 3676 打印串变量（见 §三.b） |
| `sdr_demodulate` (1190) | `_demodulate` (3697) | ✅ 真实 | 真解调+出 wav，广播 FM 链完整 |
| `sdr_analyze_recording` (1210) | `_analyze_recording` (3784) | ⚠️ 尺度问题 | 读 cf32/cs16/wav 真实，但 3842 写死 threshold=-40 未标定 |
| `sdr_encode_ax25` (1312) | `_encode_ax25` (3887) | ✅ 真实 | AX25Frame 真编码 |
| `sdr_decode_ax25` (1331) | `_decode_ax25` (3945) | ✅ 真实 | hex/音频双路解码 |
| `sdr_aprs_encode` (1345) | `_aprs_encode` (3998) | ✅（起头在界内） | 函数体 3998 起，跨出 4000；委托 ax25 模块 |

### B. 本范围内**注册、实现在界外（4001–6083，归 Part3）**的工具

| 工具名（注册行，在界内） | 实现位置 | 初步状态 |
|---|---|---|
| `morse_encode` (1995) | `_morse_encode` 5058 | 实现界外，待 Part3 |
| `morse_decode` (2009) | `_morse_decode` 5072 | 实现界外，待 Part3 |
| `meteor_list_satellites` (2027) | `_meteor_list_sats` 5090 | 实现界外，待 Part3 |
| `meteor_get_params` (2039) | `_meteor_get_params` 5114 | 实现界外，待 Part3 |
| `meteor_demod_setup` (2053) | `_meteor_demod_setup` 5141 | 实现界外，待 Part3 |
| `lro_orbit_info` (2068) | `_lro_orbit_info` 5182 | 实现界外，待 Part3 |
| `lro_doppler_predict` (2083) | `_lro_doppler_predict` 5214 | 实现界外，待 Part3 |
| `lro_od_demo` (2098) | `_lro_od_demo` 5260 | 实现界外，待 Part3 |
| `satdump_check` (2117) | `_satdump_check` 5326 | 委托 `satdump_integration.check_satdump_installed`，非空壳 |
| `satdump_list_sats` (2129) | `_satdump_list_sats` 5349 | 委托 `list_satdump_satellites` |
| `satdump_live` (2141) | `_satdump_live` 5365 | 委托 `satdump_live(...)`，传 satellite/output_dir |
| `satdump_process` (2159) | `_satdump_process` 5396 | 委托 `satdump_process(input_file,...)` |
| `satdump_compose_image` (2175) | `_satdump_compose_image` 5421 | 委托 `compose_cloud_image` |

> satdump_* 五个工具自身只是薄包装，真正风险（是否 subprocess、是否 shell=True、output_dir/input_file 是否拼进命令行）在 `satdump_integration.py`，超出本文件范围，建议在 Part3/集成审查里追命令注入。

---

## 三、其他发现

### [真bug] b. `_iq_correct` "校正前"打印串了校正后变量
**位置：`sdr_tools.py:3676`**

```python
3674      result += f"--- 校正前 ---\n"
3675      result += f"DC 偏移: I={dc_i_before:.6f}, Q={dc_q_before:.6f}\n"
3676      result += f"功率: I={var_i_before:.6f}, Q={var_q_after:.6f}\n"   # ← Q 用了 var_q_after
3677      result += f"I/Q 协方差: {cov_iq_before:.6f}\n"
```

"校正前"小节里 Q 路功率误用 `var_q_after`（校正后值），导致前后对比报告里"前 Q 功率"实际等于"后 Q 功率"，误导读数。应为 `var_q_before`。

### [建议] c. 静默恢复型 `except Exception: pass` 分布
范围内出现多处恢复/降级型吞异常（均有正当语义，但建议至少加日志）：
- 2316-2317（扫频后恢复频率/采样率）
- 2384-2385、2390-2391（bookmark_goto 设 demod/bandwidth 失败）
- 2574-2575、2670-2671、2823-2824、3076-3077、3425-3426（采集后恢复状态）
- 3139-3140（sstv 新解码器异常回退旧实现，注释已说明）
- 3734-3735（RDS 副载波检测失败置空行）
- 3800-3801（读 sidecar JSON 失败）
- 3990-3991（AX.25 info 解码失败退化为 hex）

这些不是裸 `except:`（都带了 `Exception` 类型），多数是清理/降级逻辑，可接受；但 2384/2390 与 set_frequency 同类——**set_demod/set_bandwidth 也可能返回 bool 而非抛异常**，失败时 actions 列表的追加位置虽在 try 内，仍属"返回值被忽略"一族。

### [建议] d. 文件路径安全（无命令注入，但有任意路径读写）
- 全文件 grep：**无 `subprocess` / `os.system` / `shell=True` / `os.popen`**（本文件范围内），无命令注入面。
- 用户可控路径直接落盘/读取，未做越界校验：
  - `sdr_tools.py:2980` `_record_start`：`save_path = args.get("save_path") or ...`，用户可指定任意绝对路径写录制文件。
  - `sdr_tools.py:2905` `_spectrum_screenshot`：`save_path = args.get("save_path") or ...`。
  - `sdr_tools.py:2736/3023/3103/3120/3158/3190/3216/3234`：`input_path`/`file_path` 直接 `open`/`wavfile.read`/`np.fromfile`，任意文件读（作为"打开 IQ/音频文件"工具属预期能力，但会把任意文件按 IQ 解析后报错回显，轻微信息面）。
  - `sdr_tools.py:3370` `_satellite_doppler_track`：`out = args.get("output")` 直接 `open(out,"wb")`。
- 这些是 SDR 工具的预期能力，非高危；但若该 Agent 暴露给不可信调用方，建议对写出目录做白名单/前缀约束。归 [建议]。

### [建议] e. "实验室绿、真机红"集中点汇总
1. set_frequency 返回 False 不报错（§一.1）——真机频率边界触发。
2. 未标定 dB 门限 -60/-40（§一.2）——真机不同采样率/位深下检测率漂移。
3. `_ai_sweep`/`_ai_find_center`（3544/3583）：read_samples 仅 512/1024 点、sleep 20ms，模拟后端无 PLL 稳定概念；真机 RTL-SDR 调谐需 ~50-100ms 稳定 + 更大 FFT，可能扫到的是过渡瞬态。
4. `_satellite_doppler_track`（3395）每次循环 set_frequency 不查返回值，真机调谐失败不会中断。
5. `_get_gps`/`_get_imu`（3457/3460）：永远返回模拟值，真机接上硬件也不会变。

---

## 四、优先级结论

| 级别 | 条目 | 位置 |
|---|---|---|
| 高 | set_frequency 越界返回 False 被吞，bookmark_goto 谎报成功 | sdr_tools.py:2392-2395 |
| 高 | APRS `decoded_frames` 恒空却报"真解码就绪" | sdr_tools.py:3222-3224（根因 decoders.py:803-807） |
| 中 | 未标定 dB 尺度写死 -60/-40 门限 | sdr_tools.py:2866, 2936, 3483, 3842 |
| 中 | `_iq_correct` 校正前 Q 功率串用 after 变量 | sdr_tools.py:3676 |
| 中 | `_get_gps`/`_get_imu` 硬编码模拟值且 success=True | sdr_tools.py:3457-3461 |
| 低 | set_frequency/set_demod/set_bandwidth 返回值被普遍忽略（同族） | 2445/2555/2650/2804/3057/3395/3558/3596 等 |
| 低 | 用户可控 save_path/output 无路径白名单 | 2980/2905/3370 |

> 范围内无命令注入、无裸 `except:`（均带 Exception）；绝大多数工具为真实实现，真正"空壳/占位"集中在 `sdr_get_gps`、`sdr_get_imu`、`sdr_decode_aprs` 三处。
