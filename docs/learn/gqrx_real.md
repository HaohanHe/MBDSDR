# GQRX 真实源码移植笔记（接收机架构 / 解调 / AGC / 音频）

> 本笔记对应代码：`mbdsdr_ai/gqrx_receiver.py`，校准 `mbdsdr_ai/analog_demod.py`。
> 所有常量都在代码里标注了「来源: gqrx src/...:行号」。本文是那张表的可读版。
> 源码位置：`repos/gqrx/`（注意该版本把 receiver.cpp 放在 `src/applications/gqrx/`，
> DSP 在 `src/dsp/`，接收机拓扑在 `src/receivers/`）。

## 1. 接收机信号流（最重要）

### 窄带 nbrx（FM/AM/SSB/CW）—— `src/receivers/nbrx.cpp:73-79`

```
源IQ → iq_resamp → nb(噪声消隐) → filter(信道带通) → meter/sql(静噪) → agc → demod
```

关键事实：
- **AGC 在信道滤波之后、解调之前**（nbrx.cpp:78-79）。
- **静噪在 AGC 之前**（nbrx.cpp:77-78）：无信号时 AGC 输入被静音，不会把噪声底增益拉满。
- 优选正交采样率 `PREF_QUAD_RATE = 96000 Hz`（nbrx.cpp:29）。AGC 挂在这个 96k 上。
- 默认 AGC 构造（nbrx.cpp:47）：`agc_on=true, use_hang=false, threshold=-100dB,
  manual_gain=0, slope=0, decay=500ms`。
- 静噪默认 `-150 dBFS`（全开），alpha=0.001（nbrx.cpp:48）。

### 宽带 wfmrx（WFM）—— `src/receivers/wfmrx.cpp:60-65`

```
源IQ → iq_resamp → filter(±80k) → sql → demod_fm(75k频偏) → stereo/mono
```
- **WFM 路径不接 AGC**（wfmrx.cpp:139-169 整段 AGC 被注释掉）。
- `PREF_QUAD_RATE = 240000 Hz`（wfmrx.cpp:29）。

### 顶层前端 —— `src/applications/gqrx/receiver.cpp:1340-1363`

```
源 → (输入抽取) → iq_swap → dc_corr(直流校正) → iq_fft → nbrx/wfmrx
```
- 直流校正在解调链最前面（receiver.cpp:1358-1363）。
- 音频输出采样率 **48000 Hz**（receiver.cpp:64 `d_audio_rate(48000)`）。

## 2. AGC（CAgc）—— `src/dsp/agc_impl.cpp`

逐样本算法（ProcessData, agc_impl.cpp:197-317）：

1. 幅度 `m = max(|I|,|Q|)`，转 log10 电平 `mag = log10(m+1e-8) - log10(1.0)`（:220-224）。
2. 在 **18ms 滑动窗**里取峰值（单调双端队列 deque）（:226-239）。
3. 两条 EMA 平均器：
   - `AttackAve`：上升 **2ms**、下降 **5ms**（:56-57, :244-251）。
   - `DecayAve`：上升 = decay×0.3ms；下降先 **hang 保持**，再 **50ms 释放**（:59,63, :253-268）。
4. 测量电平 = max(AttackAve, DecayAve)；
   - knee 以下：固定增益；以上：`gain = 0.7·10^(mag·(slope-1))`（:299-304）。
5. 输出 = **15ms 延迟线**里的样本 × gain（:49, :211, :306）——补偿信道滤波群延迟。

常量对照表：

| 常量 | 值 | 来源 |
|---|---|---|
| 延迟线时间 | 15 ms | agc_impl.cpp:49 |
| 峰值窗 | 18 ms | agc_impl.cpp:52 |
| 攻击上升 | 2 ms | agc_impl.cpp:56 |
| 攻击下降 | 5 ms | agc_impl.cpp:57 |
| decay 升/降比 | 0.3 | agc_impl.cpp:59 |
| 释放时间 | 50 ms | agc_impl.cpp:63 |
| 输出限幅 OUTSCALE | 0.7 (≈-3dB) | agc_impl.cpp:66 |
| 缓冲长度 | 2048 | agc_impl.h:17 |
| 默认 threshold | -100 dB | nbrx.cpp:47 / dockrxopt.cpp:434 |
| 默认 decay | 500 ms | nbrx.cpp:47 / dockrxopt.cpp:438 |
| 默认 slope | 0 | dockrxopt.cpp:452 |
| 手动增益范围 | 0–100 dB | agc_impl.cpp:119 |

> 移植注意：GQRX 把 AGC 跑在 96k，所以 18ms 窗=1728<2048。若在更高采样率跑，
> 必须像 agc_impl.cpp:188-189 那样把窗/延迟钳到缓冲长度内（已在 `GqrxAGC.set_parameters` 做）。

## 3. IQ 校正 —— `src/dsp/correct_iq_cc.cpp`

- **直流偏移**：单极点 IIR 估计均值再减掉。`alpha = 1/(1+tau·sr)`，
  `tau = 1.0s`（correct_iq_cc.cpp:47，receiver.cpp:118 `make_dc_corr_cc(rate, 1.0)`）。
- 该 GQRX 版本**没有**自动增益/相位正交校正块；我们补了一个 Gram-Schmidt 式在线
  I/Q 正交化（可关），直流部分是严格照搬。

## 4. 解调

| 模式 | 做法 | 来源 |
|---|---|---|
| FM/NFM | 正交鉴频 `gain=sr/(2π·max_dev)`，max_dev=5kHz，tau=75µs | rx_demod_fm.cpp, nbrx.cpp:52 |
| WFM | 正交鉴频 max_dev=75kHz，tau=0（去加重在立体声块） | wfmrx.cpp:48 |
| AM | 包络 `|z|` + 去直流 | rx_demod_am.cpp:48,60-63 |
| SSB/USB/LSB/CW | 复带通选边带后取同相分量（complex_to_real） | nbrx.cpp:51 |

### 信道带通预设（NORMAL 档）—— `dockrxopt.cpp:53-63`

| 模式 | low..high (Hz) |
|---|---|
| AM | -5000..5000 |
| NFM | -5000..5000 |
| LSB | -2800..-100 |
| USB | 100..2800 |
| CW | -250..250 |
| WFM | -80000..80000 |

### FM 去加重 —— `src/dsp/fm_deemph.cpp`

一阶 RC，双线性变换：`w_c=1/tau; w_ca=2·sr·tan(w_c/(2·sr)); k=-w_ca/(2·sr);
p1=(1+k)/(1-k); b0=-k/(1-k)`，分子 `[b0,b0]`，分母 `[1,-p1]`。
NFM tau=75µs（nbrx.cpp:52）；WFM tau=0（wfmrx.cpp:48）。
与 SDR++ 侧 50µs 欧洲 / 75µs 美国一致，`analog_demod.py` 已并存两套。

## 5. 音频重采样

- 音频链统一 48 kHz（receiver.cpp:64）。
- nbrx 用 PFB 任意重采样 `resampler_ff(audio_rate/PREF_QUAD_RATE)`（nbrx.cpp:68-69），
  cutoff=0.4、trans=0.2、32 相（resampler_xx.cpp）。
- numpy 移植用线性插值 + 抗混叠近似；`analog_demod.py` 末尾统一重采样到 48k。

## 6. 往返验证

`tests/gqrx_roundtrip.py`（10 用例全绿）：
- AGC 弱/强阶跃稳态都收敛到目标 ≈0.7；
- 攻击 ~ms 级收敛，hang 期压住、50ms 级慢释放；
- 带 DC 偏移的信号经 tau=1s IIR 后均值≈0；
- 合成 IQ → 七模式解调 → 48k 音频不崩溃，FM 1kHz 音调可解出。
