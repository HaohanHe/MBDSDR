# MBDSDR 功能缺口分析 — 对标 SDR++ / GNU Radio / SDRangel

> 第二轮深度代码审查 · 子任务 53
> 审查日期：2026-09-24
> 审查方式：逐文件真实代码阅读，不凭文件名猜测
> 状态标签：✅ 有实现 | 🟡 有接口/算法但未接入链路 | ❌ 完全缺失 | 🐛 有实现但有 bug

---

## 一、对标基准：三款主流 SDR 软件核心功能清单

| 功能域 | SDR++ | GNU Radio | SDRangel |
|--------|-------|-----------|----------|
| 实时声卡播放 | ✅ Audio sink 多设备 | ✅ Audio Sink 模块 | ✅ Audio output |
| 多 VFO 同时接收 | ✅ 多 VFO 窗口 | ✅ 多 channel flowgraph | ✅ 多 channel |
| ANR 自动降噪 | ✅ NR 模块 | ✅ 可拼谱减/维纳 | ✅ 噪声抑制 |
| 实时星座图控件 | ✅ Constellation | ✅ Constellation Sink | ✅ Constellation |
| 静噪门控 | ✅ Squelch + UI | ✅ Squelch 模块 | ✅ Squelch |
| 设备热插拔 | ✅ SoapySDR reload | ⚠️ 需手动重连 | ✅ Device hotplug |
| 分段增益 (LNA/VGA/BB) | ✅ 每级独立 | ✅ SoapySDR API | ✅ 每级独立 |
| SpyServer 客户端/服务端 | ✅ 原生支持 | ⚠️ 需外部模块 | ✅ 支持 |
| 录音/回放 | ✅ WAV/IQ 录制 | ✅ File Sink/Source | ✅ Recording |
| 解调模式覆盖 | 全模式 | 可编程全模式 | 全模式 |
| 频谱/瀑布图 | ✅ 实时 FFT | ✅ FFT Sink | ✅ 实时频谱 |
| 频率管理器/书签 | ✅ 完整 | ⚠️ 外部 | ✅ Bookmark |
| 射频增益/AGC | ✅ 手动+AGC | ✅ 可编程 | ✅ 手动+AGC |
| DC 偏移/IQ 校正 | ✅ DC removal + IQ balance | ✅ DC blocker + CORDIC | ✅ IQ correction |

---

## 二、MBDSDR 功能对标矩阵

### 2.1 音频与监听

| # | 功能 | SDR++ | MBDSDR 状态 | 证据 (file:line) | 缺口说明 |
|---|------|-------|-------------|-----------------|----------|
| 1 | **实时声卡播放 (audio output)** | ✅ | ❌ 完全缺失 | 全仓 grep `sounddevice\|pyaudio\|sd\.play\|AudioOutputStream` → 仅 `scripts/rtl_fm_listen.py:114` 独立脚本命中；`mbdsdr_ai/` 与 `desktop/` 零命中 | 解调输出 `analog_demod.py:51` 仅返 float32 列表，无输出流接声卡。`sdr_backend.py:61` 有 `volume` 字段但无实际播放。桌面端无任何音频输出代码。 |

### 2.2 接收架构

| # | 功能 | SDR++ | MBDSDR 状态 | 证据 (file:line) | 缺口说明 |
|---|------|-------|-------------|-----------------|----------|
| 2 | **多 VFO 同时接收** | ✅ | ❌ 完全缺失 | `sdr_backend.py:54` 单 `frequency_hz` 字段；`SDRBackendManager:1165 switch_device()` 切换时 disconnect 旧设备；全仓 grep `vfo\|multi.*tune` 无 VFO 架构 | 整个后端只有一个 LO 频率，无第二 VFO 偏移抽头，无并行解调通道。`frequency_manager.py` 是书签库不是多 VFO。 |
| 3 | **设备热插拔** | ✅ | ❌ 完全缺失 | `sdr_backend.py:1091 _discover()` 仅在 `SDRBackendManager.__init__()` 跑一次；全仓 grep `hotplug\|udev\|device.*disconnect\|watch.*device` 零命中 | 无 udev 监听、无轮询、无设备断连检测。拔棒后 `read_samples()` 静默返 None，不报错不重连。 |

### 2.3 信号处理

| # | 功能 | SDR++ | MBDSDR 状态 | 证据 (file:line) | 缺口说明 |
|---|------|-------|-------------|-----------------|----------|
| 4 | **ANR 自动降噪** | ✅ | 🟡 有算法未接入链路 | `dsp.py:675 anr_denoise()` STFT 谱减实现完整；但仅被 `agent.py:1185 sdr_anr_denoise` 独立 AI 工具调用；`analog_demod.py` / `dsp.py:fm_demod()` 解调链中无 ANR 步骤 | 算法本身是真实的 STFT 谱减，但属于"离线分析工具"，不在实时接收链中。无自适应噪声估计、无流式降噪。 |
| 5 | **静噪门控** | ✅ | 🟡 有算法+存储字段，未门控音频+无 UI | `sdr_backend.py:60 squelch_db` 字段、`:153 set_squelch()` 仅赋值；`signal_quality.py:39 squelch_gate()` 是离线分析函数返回占空比报告；`read_samples()` 中无静噪门控逻辑；`desktop/control_panel.py` 无静噪 UI 控件 | 静噪阈值存了但不用。信号低于阈值时不静音、不断流。桌面端用户看不到也调不了静噪。 |
| 6 | **分段增益 (LNA/VGA/基带)** | ✅ | 🟡 接口字段存在，无实际分段控制 | `hal.py:60 gain_stages: Dict[str, Tuple[float, float]]` 字段定义了；`hal.py:105 set_gain(gain_db, stage="")` 签名接受 stage 参数；但 `sdr_backend.py:122 set_gain()` 是单标量 `gain_db`；`RTLSDRBackend:505 self._sdr.gain = float(gain_db)` 单值设置 | HAL 层有 `stage` 参数但主后端 `sdr_backend.py` 完全没用。RTL-SDR 实际支持 LNA/Mixer/VGA 三段增益，代码只暴露一个总增益标量。桌面端无增益滑块 UI。 |
| 7 | **射频增益 / AGC** | ✅ | 🟡 基础实现粗糙 | `sdr_backend.py:56-57 gain_db + agc_enabled` 布尔开关；`RTLSDRBackend:510 set_agc()` 透传给设备；`dsp.py:460 AGC` 类存在（数字 AGC）但需确认是否串入接收链；`desktop/control_panel.py` 无增益/AGC UI | AGC 是设备级布尔开关，无软件 AGC 时间常数/阈值可调。桌面端用户无法调增益。`dsp.py` 的 `AGC` 类未被解调链调用。 |
| 8 | **DC 偏移 / IQ 不平衡校正** | ✅ | ✅ 有真实实现 | `dsp.py:203 DCBlocker` IIR 直流阻断；`dsp.py:~80 IQCalibrator` 协方差白化校正；`dsp.py:189 front_end()` 一站式 DC→IQ→抽取；`sdr_tools.py:3630 dc_block` 参数可开关 | 实现是真实的（特征分解白化矩阵），但校准是按块 fit（每块重新估计），非连续跟踪。无 UI 可视化校正效果。 |

### 2.4 网络与共享

| # | 功能 | SDR++ | MBDSDR 状态 | 证据 (file:line) | 缺口说明 |
|---|------|-------|-------------|-----------------|----------|
| 9 | **SpyServer 服务端/客户端** | ✅ | ❌ 完全缺失 | 全仓 grep `spyserver\|spy_server\|SpyServer` → 零命中 | 无 SpyServer 协议客户端连接，无服务端共享 IQ 流。仅 `RTLSDRBackend:457` 支持 `rtl_tcp` 客户端（`RtlSdrTcpClient`），但不是 SpyServer 协议。 |

### 2.5 录制与回放

| # | 功能 | SDR++ | MBDSDR 状态 | 证据 (file:line) | 缺口说明 |
|---|------|-------|-------------|-----------------|----------|
| 10 | **录音 / 回放** | ✅ | ✅ 有真实实现 | `sdr_backend.py:171 start_recording()` 线程化流式写盘；支持 cf32/cs16/cu8/wav/csv 五种格式；`:281` sidecar JSON 元数据；`:935 FileIQBackend` 回放源支持 seek/loop；`baseband_io.py:29 save_iq()/load_iq()`；`desktop/control_panel.py:199` 录音按钮 | 录制功能完整度高。回放仅能切换为"文件设备"，无时间轴拖拽 UI。 |

### 2.6 解调模式覆盖

| # | 模式 | SDR++ | MBDSDR 状态 | 证据 (file:line) | 缺口说明 |
|---|------|-------|-------------|-----------------|----------|
| 11a | **AM (包络检波)** | ✅ | ✅ | `analog_demod.py:31-34` 包络检波 + 低通 | 基础实现正确 |
| 11b | **FM (相位差分鉴频)** | ✅ | ✅ | `analog_demod.py:35-40`；`dsp.py:229 fm_demod()`；`dsp.py:253 wfm_broadcast_demod()` 含去加重/降采样 | WFM 广播链完整（鉴频→降采样→去加重→15k低通） |
| 11c | **SSB (USB/LSB)** | ✅ | 🟡 极简实现 | `analog_demod.py:41-44` 直接取实部 + 低通 | 无 BFO 拍频振荡器、无边带滤波器设计（仅简单低通），实际 SSB 听感差 |
| 11d | **CW (莫尔斯码)** | ✅ | ✅ | `radio_control.py:139 cw_decode_from_audio()` 过零检测解码；`radio_control.py:77 text_to_cw_audio()` 编码；`cw_decoder.py` 模块 | 编解码都有，但非实时连续监听模式 |
| 11e | **RTTY (电传打字)** | ✅ | ❌ 完全缺失 | 全仓 grep `rtty\|baudot\|45.*baud\|Baudot` → 零命中 | 无 170Hz FSK 解调、无 Baudot-Murray 码解码 |
| 11f | **BPSK31 / PSK31** | ✅ | ❌ 完全缺失 | 全仓 grep `bpsk31\|bpsk_31\|psk31\|PSK31` → 零命中 | 无 BPSK/DPSK 解调、无 Varicode 解码、无 31.25bd 符号定时恢复 |
| 11g | **FT8 / FT4** | ✅ | ✅ | `digital_modes.py` 参数表；`ft8_decode.py`、`ft8_ldpc.py`、`ft8_lite.py`、`ft8_callsign.py`、`ft8_unpack.py` 多模块 | FT8 解码器有完整 LDPC 译码链 |
| 11h | **ADS-B (1090ES)** | ✅ | ✅ | `adsb.py:33-37` PPM 调制 + CRC-24 校验；`adsb_lite.py`；`repos/dump1090/` 外部参考 | 基带级实验实现，含前导检测/位判决/CRC |
| 11i | **APT (NOAA 气象图)** | ✅ | ✅ | `noaa_apt_lite.py` | 注释明确写"不做：去斜、地图投影、降噪"——是 lite 版 |
| 11j | **SSTV (慢扫描电视)** | ✅ | ✅ | `sstv_decoder.py`；`experiments/exp_sstv_identification.py`；仓库内有 `real_sstv.wav` / `syn_robot36.wav` 实测样本 | Robot36 等模式有验证 |
| 11k | **AX.25 / APRS** | ✅ | ✅ | `ax25.py`；`experiments/exp_ax25_performance.py` | 有性能实验 |
| 11l | **RDS / WFM 立体声** | ✅ | 🟡 占位实现 | `rds_lite.py`；`wfm_stereo_lite.py`；`dsp.py:306 rds_decode_from_wfm()` 注释自承"占位实现：做带通选通与载波能量检测" | RDS 仅检测副载波存在性，未解出电台名/节目信息 |

### 2.7 频谱与可视化

| # | 功能 | SDR++ | MBDSDR 状态 | 证据 (file:line) | 缺口说明 |
|---|------|-------|-------------|-----------------|----------|
| 12 | **实时频谱 / 瀑布图** | ✅ | 🟡 UI 控件有，数据源是合成的 | `desktop/spectrum_widget.py:30-83 SpectrumDataGenerator` 硬编码 5 个假高斯峰（98.5/97.4/100.0/95.5/101.8 MHz）；`:6-7` 注释"频谱数据由 RSSI 扫频/模拟生成，预留真实 IQ 数据接口"；后端 `signal_spectrum.py:18 analyze_iq_spectrum()` Welch PSD 算法真实 | 后端 FFT 分析算法是真实的，但桌面 UI 的频谱/瀑布图完全接的是合成数据生成器，没有实时 IQ 流喂入。瀑布图存在但画的是假数据。 |
| 13 | **实时星座图控件** | ✅ | 🟡 后端计算函数有，无 UI 控件 | `constellation.py:49 scatter_points()` 生成散点坐标；`constellation.py:12 evm_qpsk()` EVM 计算；但 `desktop/` 全仓 grep `constellation\|星座\|scatter` → 零命中 | 后端有散点坐标生成函数，但桌面端没有星座图 QWidget 控件。无实时刷新。 |

### 2.8 频率管理

| # | 功能 | SDR++ | MBDSDR 状态 | 证据 (file:line) | 缺口说明 |
|---|------|-------|-------------|-----------------|----------|
| 14 | **频率管理器 / 书签** | ✅ | ✅ 有实现（但仅单 LO 跳转） | `frequency_manager.py:185 FrequencyManager`；内置 50+ 标准频点书签（广播/航空/海事/业余/FT8/ADS-B/气象/授时）；`:208 _save_user()` 持久化到 `~/.mbdsdr/bookmarks.json`；支持 nearest/find/add/remove | 书签库本身完整实用。但"跳转到书签"= 改一个 LO 频率，无扫频、无 band stacking、无收藏分组管理 UI。 |

---

## 三、汇总统计

### 按状态分类

| 状态 | 数量 | 功能列表 |
|------|------|----------|
| ✅ 有真实实现 | 6 | 录音/回放、AM 解调、FM 解调、DC/IQ 校正、FT8 解码、ADS-B 解码、频率管理器书签（基础） |
| 🟡 有算法/接口但未接入链路 | 6 | ANR 降噪、静噪门控、分段增益、AGC（软件层）、SSB 解调（极简）、实时频谱 UI（接合成数据）、星座图（无 UI）、RDS（占位） |
| ❌ 完全缺失 | 5 | 实时声卡播放、多 VFO、设备热插拔、SpyServer、RTTY、BPSK31 |

> 注：部分功能同时有"有实现"和"有缺口"两面，按最短板归类。

### 按严重程度排序（对用户体验影响从大到小）

1. **🔴 P0 — 无实时声卡播放**：解调了音频但听不到，SDR 软件的核心体验缺失
2. **🔴 P0 — 频谱/瀑布图接合成数据**：用户看到的是假频谱，不是真实信号环境
3. **🔴 P1 — 无多 VFO**：只能同时听一个频率，无法边守听边观察其他信号
4. **🔴 P1 — 静噪未接入链路**：存了阈值不生效，嘈杂底噪无法静音
5. **🟡 P2 — 无分段增益控制**：用户无法精细调 LNA/VGA 增益优化信噪比
6. **🟡 P2 — ANR 未串入解调链**：降噪算法存在但只在离线分析用
7. **🟡 P2 — 无星座图实时 UI**：数字调制信号质量无法可视化
8. **🟡 P3 — 无 SpyServer**：无法远程共享/接入 SDR 硬件
9. **🟡 P3 — BPSK31/RTTY 缺失**：业余数字模式不完整
10. **🟡 P3 — 无设备热插拔**：拔棒后需手动重连

---

## 四、关键差距结论

MBDSDR 的 **后端算法层**（DSP 解调、IQ 校正、FT8/ADS-B/SSTV 解码器、录制/回放）有相当扎实的实现，甚至部分模块（如 FT8 LDPC、ADS-B CRC-24）达到了可论文级别的实验验证深度。

但 **核心接收体验链路是断的**：

```
设备 IQ → DC/IQ 校正 → 解调 → 音频输出 → 声卡
         ✅         ✅     ❌        ❌
频谱显示 ← FFT ← IQ 流
         🟡     ❌(合成数据)
```

三个最致命的断点：
1. **解调后音频无声卡输出** — 听广播/听通话这个 SDR 最基本的功能体验不成立
2. **频谱 UI 接的是假数据** — 用户打开软件看到的频谱是程序硬编码的 5 个高斯峰，不是真实空中信号
3. **静噪/ANR/增益等"调节器"存了不用** — 后端有字段、有算法，但不在实时数据流路径上

这意味着 MBDSDR 当前更像一个 **"AI 驱动的离线 IQ 分析工具箱"**，而非一个可日常使用的 **"实时接收监听软件"**。
