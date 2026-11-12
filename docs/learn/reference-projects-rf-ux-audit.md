# 参考项目逻辑核对：连接 / 播放 / 音量 / 性能（2026-11-10）

用户 11-10 原话：「你看好 sdr plus plus 这些无论是 set dump、sdr plus study
还有这些项目以及 deep sake 哈涅斯你都克隆下来了，你真正去看他们这个逻辑了吗」。
本轮**实读源码**（不是只有 README），逐条对照 MBDSDR 的差距与落地。

本地已克隆位置（均在 `D:/mbdsdr/reference/`，仓库外）：

| 项目 | 路径 | 本轮读过的文件 |
|---|---|---|
| SDR++ (v1.2.x 完整源码) | `sdrpp/` | `core/src/gui/main_window.cpp/.h`、`core/src/dsp/audio/volume.h`、RTL source 档表注释 |
| gqrx | `gqrx/` | `src/applications/gqrx/receiver.cpp:49,122-124` |
| SatDump | `satdump/` | `plugins/*/main.cpp`、`satdump_cfg.json` |
| DeepSeek Harness | `deepseek-harness/`（本轮真克隆，20665 文件） | `docs/architecture.zh.md`、`docs/capability-seams.zh.md`、`docs/subsystems/tools.zh.md`、`docs/config-catalog.zh.md` |

## 一、设备插上 ≠ 开始播放（用户点名的那条）

SDR++ `core/src/gui/main_window.h:51`：

```cpp
bool playing = false;
bool autostart = false;      // 仅命令行 --autostart 才置 true
```

`main_window.cpp:676-690` 的 `setPlayState(bool)` 是**唯一**入口：
点 PLAY 才 `sigpath::sourceManager.start()` + `tune()`；点 STOP 才
`sourceManager.stop()` + `iqFrontEnd.flushInputBuffer()`。
`main_window.cpp:365-368` 的 autostart 是显式命令行开关，不是"插上就播"。

**MBDSDR 原来的错**：启动 200 ms 自动枚举 + `connect()`，连接成功即
`_start_iq_streams()` → 打开声卡 + 起解调线程。用户听到的是"插上就出声"。

**本轮改法**（对齐 SDR++）：
- 新增 `MainWindow._listening = False`；工具栏加显式「▶ 收听 / ■ 停止」。
- `_start_iq_streams()` 不再自动 `AudioPlayer.start()`；只有 `_listening`
  为真才开声卡。断连会复位收听态并关声卡，重连不会偷偷出声。
- 未收听时 `DemodWorker` 连解调都不跑（每帧门控），CPU 让给频谱/GNSS。
- 收听态经 `_sync_listen_workers()` 推给流水线 worker；流水线重建
  （切采样率/切设备/回放结束）后重新推送，避免"重建后突然出声"。

## 二、音量：为什么我们一直过载

| 项目 | 做法 | 位置 |
|---|---|---|
| SDR++ | `_volume = powf(volume, 2)`，volume ∈ [0,1]，**最大 1.0** | `core/src/dsp/audio/volume.h:14,22,38` |
| gqrx | `DEFAULT_AUDIO_GAIN -6.0`（=0.5） | `receiver.cpp:49,124` |
| MBDSDR 旧 | `volume/100*2.0` → 72% 给 **1.44**、100% 给 **2.0** | `main_window._on_volume_changed` |

量化实测（`output/audio_overload_probe.py`，48 kHz 合成 FM 链路）：
72% 时 **20.8%** 样本被 `np.clip` 硬削成方波，100% 时 **58.3%**。
这就是"间歇有正常声音、大多过载"——只有信号弱的瞬间没削到。

**本轮改法**：
- `mbdsdr_ai/audio_out.volume_pct_to_gain()`：平方律，上限 1.0。
- `soft_limit()`：阈值 0.9 以下原样，之上有理函数 `t + span·d/(d+span)`
  （`d=|x|-t`）渐近压到 ±1、**永不到达 1**，一阶连续，绝不把不同电平切成
  同一个平顶（硬 clip 就是方波失真）。不用 tanh：float32 下 `d>10·span`
  时 tanh 已等于 1.0，多个电平仍压成同一平顶（测试抓到过）。注意区分：
  `WFMReceiver` 第一级 gentle waveshaper 用 `np.tanh`，最终输出限幅是这里的有理式。
- `WFMReceiver.output_gain` 1.6 → 1.0；AGC `target 0.5/max_gain 60` →
  `0.25/4.0`，弱噪声不再被顶到满幅。
- **逐块峰值归一化是第二个根因**：旧 `wfm_broadcast_demod` 对每块 IQ
  独立归一化到 0.95，纯噪声块也被拉满。实测（`audio_overload_probe.py`）
  噪声块 peak=0.950 与强信号块一模一样。内核里本来就有修好的有状态
  `WFMReceiver`（固定增益+tanh、跨块保留滤波器历史），但 **CLI/MCP 的
  `_FMListener` 和桌面回放路径没用它**，仍调旧函数。本轮两条都切过去，
  回放每段新建接收器（滤波器历史不跨文件）。

**改后端到端实测**（`output/audio_chain_e2e_probe.py`，按 `receive_pipeline`
真实顺序 WFMReceiver(1.0) → AGC(0.25/4.0) → **真实 `AudioPlayer.write`**
（假流绕过声卡、逐块写后抽干队列，量的就是产品代码实际产出样本），
合成强信号/正常/纯噪声三段 FM IQ，统计写声卡前 `|x|>=1` 的硬削平比例：

| 音量 | 新链 gain | 新链削平 | 新链 peak | 旧链 gain | 旧链削平 |
|---|---|---|---|---|---|
| 48% | 0.230 | **0.0%** | 0.179 | 0.96 | 0.0% |
| 72% | 0.518 | **0.0%** | 0.402 | 1.44 | 14.4% |
| 100% | 1.000 | **0.0%** | 0.776 | 2.00 | 42.5% |

- 纯噪声无台段（vol 72）：新链 rms=0.103、削平 0.0%；旧链 rms=0.483、削平 3.8%
  （底噪被拉向满幅）。新链 peak 全程 < 0.8，留足余量，不再"大多过载"。
- 软限幅可分性：输入 `[0.95, 1.2, 2.0, 3.0]` → 写卡产出
  `[0.933, 0.975, 0.992, 0.996]`，严格递增、全部 `|x|<=1`——越界样本被
  渐近压缩而非切成同一平顶（方波失真根因已除）。
- 旧链 14.4%/42.5% 与上文 20.8%/58.3% 口径不同：本探针含 AGC 级、信号混合
  与种子不同，但"旧链硬削波、新链零削波"的结论一致。

## 三、采样率与性能（用户："电脑没有那么强大…得让路"）

- SDR++ `core/src/server.cpp:47` 默认 **1 MS/s**；rtl_sdr_source
  `main.cpp:27-39` 硬编码 11 个离散档，我们后端 `SAMPLE_RATES` 就是照抄这张表。
- 实测（`output/perf_probe.py`，20 ms IQ 块，FFT + WFM 解调，**还没算**
  AGC/瀑布/多 VFO/GNSS 轮询）：

  | 采样率 | 每块耗时 | 单核实时占用 |
  |---|---|---|
  | 2.40 MS/s | 3.60 ms | 18.0% |
  | 1.00 MS/s | 1.37 ms | 6.8% |
  | 0.48 MS/s | 0.71 ms | 3.6% |
  | 0.24 MS/s | 0.48 ms | 2.4% |

  2.4 → 1.024 MS/s 是 **2.6 倍** 差距，这正是 GNSS 面板/设置页"原地不动"的
  来源：IQ 流水线把主线程时间片吃满。

- **没有改内核 `DEFAULT_SAMPLE_RATE_HZ`**：它是真机验收 R4（2.4 MS/s
  SigMF 录制回放）、ADS-B 1090 与 31 个测试文件钉住的设备基准。改它会
  把"设备能开多快"和"桌面默认开多快"混为一谈。
- 新增 `desktop/perf_profile.py` 三档（省电 1.024 / 均衡 2.048 /
  精细 2.400 MS/s），**默认省电**，全部取后端真实离散档。设置页
  「通用 → 接收性能档」可调，切档即时改 GNSS 轮询间隔 / 频谱刷新 / FFT；
  采样率作为**下次连接默认值**下发（不越权覆盖用户在控制面板手动选的档）。
- 设备面板默认采样率也跟档位走，不再硬填 2.4 MS/s。

## 四、DeepSeek Harness 到底该学什么（本轮实读结论）

`docs/architecture.zh.md:140-146` 的 **seam 三角色**（Service Definition /
Provider / Consumer）与 `docs/subsystems/tools.zh.md:311` 的
`tools/pre-execute → execute → post-execute` 瀑布式事件，确实是我们的结构差：
我们的安全门硬编码在 `call_from_model` / `mcp_server.call_tool` 里，新增策略
要改代码，不能由插件挂载。已记在 `agent-harness-gap-audit.md` 第六节，仍欠。

但 Harness 与"SDR 插上就该播"**没有关系**：它是 agent 运行时（Cordis、
Session 事件流、工具准入），没有设备连接状态机。用户把两者并列提，是要求
"把这些项目都真读一遍、把能用的原则用到产品上"，不是要把 Harness 的
会话机制塞进射频链路。本轮按这个理解落地：SDR++/gqrx 管射频 UX 与音量，
Harness 管能力缝合与默认最小化（profile 默认不开重能力），各取所需。

`docs/architecture.zh.md:27` 还有一条直接对应性能档：
> YAML 控制 HMR：base 启用仅监视配置的 `dsh-hmr`；Headless、SDK 和 ACP 禁用它；
> `sdk-minimal` 不包含它。Profile patch 覆盖这些默认值。

即**默认 profile 只启用必需能力，重能力由显式 profile 打开**。我们的
"省电档默认 + 精细档显式切"是同一原则在射频侧的实现。

## 五、not_verified

- 本轮改动后**桌面全量 pytest 未重跑**（上一权威全量 `f9c3bb92`
  5573 passed 已过期）；已跑改动相关更大子集并全绿（2026-11-10 当时实跑）：身份红线 + 设置
  schema/ui_coverage/convergence/corruption + 主题切换 + audio/sdr_pipeline/
  touch + 新契约（233 + 127 + 24 passed），顺带诚实追平 6 个陈旧测试
  （theme 出厂默认 d509a4db 已改 dark_car、volume 平方律），不声称桌面全量绿。
- 真机七环未在本轮改动后重跑；音频"过载"修复已用**端到端探针**
  （`audio_chain_e2e_probe.py`，真实 `AudioPlayer.write` 路径）量化到
  新链各音量 0.0% 硬削波、旧链 14.4%/42.5%（见第二节表），但**没有做
  真机盲听 A/B**（合成链路证明文件级不再削波，最终听感仍需用户耳朵确认）。
- SDR++ / gqrx 的音频 sink 是 C++，本项目 Python/sounddevice 路径的
  PortAudio 回调行为只做了单元测试，未做高 DPI/真声卡长时间稳定性验证。
