# Gqrx 源码学习笔记（对照 MBDSDR 迁移）

> 仓库：https://github.com/gqrx-sdr/gqrx （depth=1 clone）
> 阅读范围：src/applications/gqrx/{receiver.*,mainwindow.cpp}、src/receivers/nbrx.*、src/dsp/{agc_impl.cpp,rx_meter.cpp,rx_fft.cpp,rx_filter.cpp}、src/qtgui/{plotter.cpp,demod_options.cpp,dockrxopt.cpp}
> 阅读日期：2026-09-24

---

## 0. 总体架构

Gqrx = Qt GUI + GNU Radio flowgraph。核心分层：

- **GUI 层**（`src/qtgui/`、`src/applications/gqrx/mainwindow.cpp`）：
  频谱图 `CPlotter`、S 表 `CMeter`、解调选项 `CDemodOptions`、接收参数 dock
  `DockRxOpt`、输入控制 dock `DockInputCtl`。
- **顶层 flowgraph 封装**（`src/applications/gqrx/receiver.*`）：
  类 `receiver` 持有 `gr::top_block_sptr tb`、`osmosdr::source::sptr src`、
  DDC、IQ FFT、解调子图 `receiver_base_cf_sptr rx`、音频 sink。GUI 只通过
  `receiver::` 的 setter 操作 DSP，不直接碰 GR block。
- **解调子图**（`src/receivers/nbrx.cpp` / `wfmrx.cpp`）：
  窄带 `nbrx` = resampler → noise blanker → channel filter → meter →
  squelch → AGC → demod（AM/FM/SSB/AM-Sync）→ audio resampler。
- **DSP 块**（`src/dsp/`）：`rx_fft_c`、`rx_meter_c`、`rx_filter`、
  `rx_agc_cc`（包 `CAgc`）、`downconverter_cc`、`correct_iq_cc`。

关键设计：**所有重配置都先 `tb->stop()` / `tb->wait()`，改完再 `tb->start()`**
（见 `src/applications/gqrx/receiver.cpp:873-877`）。这是因为 GR 的
hier_block 重连在运行中容易崩，gqrx 选择"停一下重连"的稳妥路径。

---

## 1. 设备连接流程

### 1.1 构造时的默认源

`receiver` 构造函数里，如果没给 `input_device`，会立刻挂一个零文件伪源，
避免空指针：

```
src = osmosdr::source::make("file=...,freq=428e6,rate=96000,repeat=true,throttle=true");
```
见 `src/applications/gqrx/receiver.cpp:80-88`。

### 1.2 启动时从配置恢复设备

MainWindow 启动时读 `input/device`，调 `rx->set_input_device(...)`：

- `src/applications/gqrx/mainwindow.cpp:545-561`：
  ```
  QString indev = m_settings->value("input/device", "").toString();
  if (!indev.isEmpty()) {
      try { rx->set_input_device(indev.toStdString()); conf_ok = true; }
      catch (std::runtime_error &x) { QMessageBox::warning(...); }
  }
  ```
- 紧接着读采样率 `input/sample_rate` → `rx->set_input_rate(int_val)`
  （`mainwindow.cpp:597-621`），失败弹 "Device Error" 对话框。
- 再读 `input/decimation` → `rx->set_input_decim(int_val)`
  （`mainwindow.cpp:627-640`）。
- 然后读 `input/frequency` → `rx->set_rf_freq(...)`
  （`mainwindow.cpp:914`）。

### 1.3 `set_input_device` 内部（关键）

`src/applications/gqrx/receiver.cpp:182-255`：

1. 如果 flowgraph 在跑，先 `tb->stop(); tb->wait();`（line 196-200）。
2. 把旧 `src` 从 `iq_swap` 上断开（line 202-210）。
3. 对老版 GR（<3.8.2）有 workaround：先 connect 一个 dummy zero-file source
   再 stop，强制关掉旧 USB 设备句柄（line 212-223）。新版直接 `src.reset()`。
4. `try { src = osmosdr::source::make(device); }`
   `catch (std::exception &x) { error=x.what(); src = 伪零文件源; }`
   （line 225-233）—— **失败时回退到零文件源，不让 UI 崩**。
5. 读回真实采样率：`if(src->get_sample_rate() != 0) set_input_rate(...)`
   （line 235-236）。
6. 重新 `tb->connect(src, 0, input_decim/iq_swap, 0)`（line 238-246）。
7. 如果之前在跑，`tb->start()`（line 248-249）。
8. 如果 catch 到异常，**重新 throw `std::runtime_error`** 给 GUI 弹框
   （line 251-254）。

### 1.4 采样率/增益/频率 setter

- 采样率：`receiver::set_input_rate` `src/applications/gqrx/receiver.cpp:324-369`
  —— `tb->lock()` → `src->set_sample_rate(rate)` → 失败时不崩，
  降级为请求值 → 重新计算 `d_decim_rate / d_ddc_decim / d_quad_rate` →
  同步更新 `dc_corr`、`ddc`、`rx->set_quad_rate`、`iq_fft->set_quad_rate`。
- 增益：`set_auto_gain(bool)` → `src->set_gain_mode(automatic)`
  （`receiver.cpp:634-639`）；手动 `set_gain(name, value)`
  （`receiver.cpp:616-621`）。增益档位枚举从 `src->get_gain_names()`
  拉，由 `MainWindow::updateGainStages()` 填到 dock。
- 频率：`set_rf_freq` → `src->set_center_freq(d_rf_freq)`
  （`receiver.cpp:537-545`）。频率范围通过 `get_rf_range()` 透传
  `src->get_freq_range()`（`receiver.cpp:566-586`）。
- 频率校正（PPM）：`set_freq_corr` → `src->set_freq_corr(ppm)`
  （`receiver.cpp:717-722`），GUI 调用点在 `mainwindow.cpp:1073`。

### 1.5 错误处理模式总结

- 设备打开失败 → 回退零文件源 + throw → GUI 弹 `QMessageBox`。
- 采样率设置失败（`set_sample_rate` 返回 0）→ 不抛异常，仅打 stderr，
  用请求值顶上（`receiver.cpp:344-357`）。
- I/Q 回放时用 `file=...,rate=...,freq=...,throttle=true` 字符串动态拼设备名
  （`mainwindow.cpp:1732-1737`），跟真实硬件走同一条 `set_input_device` 路径。

---

## 2. 频谱交互（FFT + 点击调 VFO + 缩放平移）

### 2.1 FFT 块参数

- 默认 FFT 大小：`receiver.h:108` `DEFAULT_FFT_SIZE = 8192`。
- 构造时：`iq_fft = make_rx_fft_c(DEFAULT_FFT_SIZE, d_decim_rate, gr::fft::window::WIN_HANN)`
  （`receiver.cpp:119`）。音频 FFT 同样 8192、Hann（`receiver.cpp:121`）。
- FFT 大小运行时改：`MainWindow::setIqFftSize`
  （`mainwindow.cpp:1824-1830`）→ `rx->set_iq_fft_size(size)`
  → `rx_fft_c::set_fft_size`（`src/dsp/rx_fft.cpp:182-198`）
  内部 **delete 旧 FFT plan 再 new**，避免复用错误 plan。
- 窗口函数：`set_iq_fft_window` → `rx_fft_c::set_window_type`
  （`rx_fft.cpp:207-220`）→ `update_window()` 用
  `gr::fft::window::build(...)` 重算窗系数并做幅度/能量归一
  （`rx_fft.cpp:222-238`）。
- **FFT 是拉模式**：`work()` 只把样本扔进环形 buffer（`rx_fft.cpp:95-120`），
  GUI 定时器到点才调 `get_fft_data()`（`rx_fft.cpp:126-156`）。
  `get_fft_data` 内部按时间差 `diff` 推进读指针，保证读的是"最新"数据
  而不是固定块。FFT 输出做 fftshift + `std::norm`（幅度平方）。

### 2.2 帧率（FPS）

`MainWindow::setIqFftRate(int fps)` `mainwindow.cpp:1833-1859`：
- fps=0 → interval=36e7（≈100 小时），`plotter->setRunningState(false)`。
- 否则 `interval = 1000/fps` ms，`plotter->setFftRate(fps)`，
  定时器 `iq_fft_timer->setInterval(interval)`。

### 2.3 点击频谱 → VFO

在 `CPlotter::mousePressEvent` `src/qtgui/plotter.cpp:625-792`：

- **先判断光标是不是抓在解调盒上**：靠近 `m_DemodFreqX`（中心）→
  `m_CursorCaptured = CENTER`（line 634-639）；靠近 low-cut → LEFT
  （line 640-645）；靠近 hi-cut → RIGHT（line 646-651）。
- **左键无修饰**（line 730-748）：
  - 如果开了 peak detect，先 `getNearestPeak(pt)` 找最近峰；
  - 否则 `m_DemodCenterFreq = roundFreq(freqFromX(px), m_ClickResolution)`；
  - **emit `newDemodFreq(m_DemodCenterFreq, m_DemodCenterFreq - m_CenterFreq)`**
    （line 741）—— 第二参数就是相对中心频点的 offset，即 VFO 偏移。
- **中键**（line 750-757）：直接改 `m_CenterFreq`（中心频率平移），
  再 emit `newDemodFreq`。
- **右键**（line 758-762）：`resetHorizontalZoom()` 复位水平缩放。
- **Shift/Ctrl+左键**（line 657-727）：在曲线上做 AB marker 选带宽，
  遍历 FFT buf 找到阈值穿点，emit `markerSelectA/B`。

MainWindow 侧接收 `newDemodFreq`：
- `on_plotter_newDemodFreq(qint64 freq, qint64 delta)` →
  若 |delta| 大则 `rx->set_rf_freq(freq)` 移中心频点；否则
  `rx->set_filter_offset((double) freq_hz)` 调 DDC 偏移
  （`mainwindow.cpp:1028`、`mainwindow.cpp:2105`）。
- `set_filter_offset` 内部：`ddc->set_center_freq(d_filter_offset - d_cw_offset)`
  （`receiver.cpp:655-661`）。

### 2.4 滚轮缩放/平移

`CPlotter::wheelEvent` `plotter.cpp:922-1000`：

- 光标在 Y 轴上 → 垂直 dB 范围缩放（line 939-962），
  以光标处 dB 为锚点。
- 光标在 X 轴上 → `zoomStepX(...)` 水平缩放（line 963-966）。
- **Ctrl+滚轮**：调带宽（`m_DemodLowCutFreq -= ...; m_DemodHiCutFreq += ...`，
  line 967-974）→ emit `newFilterFreq`。
- **Shift+滚轮**：整体平移带宽（line 976-983）。
- **裸滚轮**：步进调 VFO 中心频率 `m_DemodCenterFreq += numSteps*m_ClickResolution`
  （line 986-996）→ emit `newDemodFreq`。
- zoomBase：裸滚 0.9，Ctrl 0.7（line 937）。

### 2.5 鼠标拖动

`mouseMoveEvent`（line 197 起）按 `m_CursorCaptured` 分支：
CENTER 拖解调盒中心、LEFT/RIGHT 拖带宽边、XAXIS/YAXIS 拖整体平移。

---

## 3. 静噪（Squelch）实现

### 3.1 是能量阈值，不是 CTCSS

全仓 grep `ctcss` 无业务命中（只在 remote_control 注释里出现）。
静噪用的是 GNU Radio 自带 `gr::analog::simple_squelch_cc`：

```
sql = gr::analog::simple_squelch_cc::make(-150.0, 0.001);
```
见 `src/receivers/nbrx.cpp:48`。

- 第一个参数：初始阈值 -150 dBFS（即"全开"，默认不静噪）。
- 第二个参数 alpha：0.001（包络平滑系数）。

### 3.2 在链路里的位置

`nbrx` 构造里：
```
connect(filter, 0, meter, 0);     // line 76
connect(filter, 0, sql, 0);      // line 77
connect(sql, 0, agc, 0);         // line 78
connect(agc, 0, demod, 0);       // line 79
```
即：信道滤波器输出 → 同时送 meter 和 squelch → **sql 后面才是 AGC 和解调**。
sql 一旦判定信号低于阈值，直接把输出静音（null sink 等价），AGC 不会被
噪声抽风。这是个很关键的拓扑细节。

### 3.3 阈值设置

- GUI：`DockRxOpt` 里 `sqlSpinBox`（dBFS），`dockrxopt.cpp:347-363`。
- MainWindow：`setSqlLevel(double level_db)` `mainwindow.cpp:1450-1454`
  → `rx->set_sql_level(level_db)` → `nbrx::set_sql_level`
  （`nbrx.cpp:152-155`）→ `sql->set_threshold(level_db)`。
- **自动静噪**：`MainWindow::setSqlLevelAuto()` `mainwindow.cpp:1460-1468`：
  ```
  double level = rx->get_signal_pwr() + 3.0;   // 当前电平 + 3 dB 裕量
  if (level > -10.0) level = uiDockRxOpt->getSqlLevel();  // 防 0 dBFS
  ```
  快捷键 `` ` `` 触发（`dockrxopt.cpp:149-153`）。
- **复位**：`on_resetSquelchButton_clicked` → 设回 -150（`dockrxopt.cpp:695-697`），
  即永久打开。
- alpha（衰减包络）：`set_sql_alpha` → `sql->set_alpha(alpha)`
  （`nbrx.cpp:157-160`），控制开/关切换的平滑度。

### 3.4 S 表与静噪联动

`rx_meter_c` 在滤波器后测功率（`src/dsp/rx_meter.cpp:80-96`）：
- 平均窗口 = `quad_rate * 0.100`（100 ms，`rx_meter.cpp:40`）。
- `get_level_db()` 按时间差推进读指针，用 `volk_32f_x2_dot_prod_32f`
  算最近 100 ms 的平方和平均 → `10*log10(power)`。
- MainWindow 定时器 `meterTimeout()`（`mainwindow.cpp:1471-1478`）
  把 `rx->get_signal_pwr()` 推给 `ui->sMeter->setLevel(level)`，
  同时 `ui->sMeter->setSqlLevel(level_db)` 在 S 表上画静噪门限线
  （`mainwindow.cpp:1453`）。

**没有 CTCSS / DCS 解码**——只看能量。要做亚音静噪得自己加子模块。

---

## 4. VFO / 解调模式切换

### 4.1 解调枚举

`receiver.h:82-92`：
```
RX_DEMOD_OFF / NONE / AM / NFM / WFM_M / WFM_S / WFM_S_OIRT / SSB / AMSYNC
```

`nbrx.h` 内部再分：`NBRX_DEMOD_NONE/SSB/AM/AMSYNC/FM`。

### 4.2 切换流程

`MainWindow::setDemod(int mode_idx)` `mainwindow.cpp:1209-1322`：

- `MODE_OFF` → `rx->set_demod(RX_DEMOD_OFF)`（line 1209）。
- `MODE_RAW` → `RX_DEMOD_NONE`，plotter 解调范围 ±40 kHz（line 1213-1219）。
- `MODE_AM` → `RX_DEMOD_AM` + `rx->set_am_dcr(...)`（line 1221-1227）。
- `MODE_AM_SYNC` → `RX_DEMOD_AMSYNC` + DCR + PLL 带宽
  （line 1229-1236）。PLL 带宽档位见 `demod_options.cpp:94-127`：
  Fast 0.01 / Medium 0.001 / Slow 0.0001。
- `MODE_NFM` → `RX_DEMOD_NFM` + `set_fm_maxdev` + `set_fm_deemph`
  （line 1238-1245）。maxdev 档位 2.5k/5k/17k/25k（`demod_options.cpp:54-91`）；
  deemph tau 档位 0/25/50/75/100/250/530 µs/1ms（`demod_options.cpp:29-40`）。
- `MODE_WFM_*` → `RX_DEMOD_WFM_M/S/S_OIRT`，解调范围 ±120 kHz
  （line 1247-1264）。
- `MODE_LSB` → `RX_DEMOD_SSB`，plotter 范围 -40k..-100..-5000..0
  （line 1266-1272）—— 注意 SSB 没有"中心"，低频边带。
- `MODE_USB` → `RX_DEMOD_SSB`，范围 0..5000..100..40k（line 1274-1280）。
- `MODE_CWL/CWU` → 仍 `RX_DEMOD_SSB`，但叠加 `cwofs = ±getCwOffset()`
  （line 1282-1298），带宽 ±100 Hz。

### 4.3 切换时做的三件事

每个 case 结束后统一执行（`mainwindow.cpp:1308-1314`）：
```
ui->plotter->setHiLowCutFrequencies(flo, fhi);
ui->plotter->setClickResolution(click_res);
ui->plotter->setFilterClickResolution(click_res);
rx->set_filter((double)flo, (double)fhi, d_filter_shape);
rx->set_cw_offset(cwofs);
rx->set_sql_level(uiDockRxOpt->currentSquelchLevel());
```

即：**切模式 = 改解调块 + 重设带通滤波器 + 重设 CW BFO + 重设静噪门限**。

### 4.4 滤波器怎么重抽

`receiver::set_filter` `receiver.cpp:688-715`：
- 先做合法性检查（`low<high`、带宽 ≥ `RX_FILTER_MIN_WIDTH`）。
- 按 shape 算过渡带：SOFT=0.5×BW、NORMAL=0.2×BW、SHARP=0.1×BW
  （`receiver.cpp:695-710`）。
- 调 `rx->set_filter(low, high, trans_width)`。

`nbrx::set_filter` → `rx_filter::set_param` `src/dsp/rx_filter.cpp:76-98`：
```
d_taps = gr::filter::firdes::complex_band_pass(1.0, d_sample_rate,
                                               d_low + d_cw_offset,
                                               d_high + d_cw_offset,
                                               d_trans_width);
d_bpf->set_taps(d_taps);
```
即每次改带宽都重新设计 FIR 抽头并 `set_taps` 热替换，不用重建 block。

### 4.5 解调块本身怎么切

`nbrx::set_demod(int)` `nbrx.cpp:192-299`：
- 旧 demod 从 `agc` 和输出端 disconnect（line 205-236）。
- switch 选新 demod 指针：`demod_raw`(complex_to_float) / `demod_ssb`
  (complex_to_real) / `demod_am` / `demod_amsync` / `demod_fm`
  （line 238-265）。
- 重新 connect `agc→demod→audio_rr→self`（line 267-298）。

`receiver::set_demod`（`receiver.cpp:865-938`）更重：
`tb->disconnect_all()` → `connect_all(RX_CHAIN_NBRX or WFMRX)` →
`rx->set_demod(...)`。WFM 会整个换 `wfmrx` 子图，因为需要立体声解调 + RDS。

---

## 5. AGC

### 5.1 拓扑位置

`nbrx.cpp:47` 构造：
```
agc = make_rx_agc_cc((double)PREF_QUAD_RATE, true, -100, 0, 0, 500, false);
```
默认参数：on=true, threshold=-100, slope=0, decay=500 ms, hang=false。
位置：**sql 之后、demod 之前**（line 78-79）。

### 5.2 内部参数（`src/dsp/agc_impl.cpp`）

`CAgc::SetParameters` `agc_impl.cpp:124-190`：

| 参数 | 含义 | 源码常量 |
|---|---|---|
| `AgcOn` | AGC 开关，关了用 manual gain | line 135 |
| `UseHang` | hang 模式（信号掉了先保持增益一段） | line 136, 241-269 |
| `Threshold` | knee 点 dB，范围 -160..0 | line 137, 168 `m_Knee = Threshold/20` |
| `ManualGain` | AGC 关时的固定增益 dB | line 138, 165 |
| `SlopeFactor` | knee 以上输出衰减斜率 dB | line 139, 169 |
| `Decay` | 衰减时间 ms（20..5000） | line 140, 179-185 |

关键时间常数（`agc_impl.cpp:49-63`）：
```
DELAY_TIMECONST   = 0.015 s   // 信号延迟线，等滤波器群延迟
WINDOW_TIMECONST  = 0.018 s   // 峰值检测滑窗
ATTACK_RISE       = 0.002 s   // 上升时 attack 时间常数
ATTACK_FALL       = 0.005 s   // 下降时 attack
DECAY_RISEFALL_RATIO = 0.3    // decay rise = 0.3 × decay fall
RELEASE_TIMECONST = 0.05 s    // hang 释放时间
```

### 5.3 算法核心

`CAgc::ProcessData` `agc_impl.cpp:197-317`：

1. 每个样本把输入塞进 `m_SigDelayBuf` 环形延迟线（line 211-218），
   输出用的是"延迟后的样本乘增益"（line 306）—— 这样增益计算跟得上
   滤波器群延迟，不会过冲。
2. 幅度取 `max(|I|,|Q|)` 再 log10 转 dB（line 220-224）。
3. 用 deque 维护滑窗峰值（line 227-239），O(1) 更新。
4. **两个独立 IIR 平均器**：`m_AttackAve`（快）和 `m_DecayAve`（慢），
   上升用 rise alpha、下降用 fall alpha（line 244-290）。
5. hang 模式下，信号下降时先 `m_HangTimer` 计数，攒够 `m_HangTime` 才
   用 `RELEASE_TIMECONST` 慢释放（line 261-268）。
6. 增益 = max(attackAve, decayAve) 过 knee 后按 slope 压缩：
   `gain = AGC_OUTSCALE * 10^(mag*(slope-1))`（line 299-304）。
7. AGC 关：直接 `pOut = m_ManualAgcGain * pIn`（line 311-315）。

### 5.4 GUI 入口

`mainwindow.cpp:1396-1428` 把 dock 的 6 个信号都接到 `rx->set_agc_*`：
on / hang / threshold / slope / manual_gain / decay。

---

## 6. 其他值得注意的设计

### 6.1 IQ 交换 / DC 校正 / IQ 平衡

- IQ swap：`iq_swap = make_iq_swap_cc(false)`（`receiver.cpp:117`），
  `set_iq_swap` 直接 `iq_swap->set_enabled(bool)`（`receiver.cpp:462-469`）。
- DC 校正：`dc_corr = make_dc_corr_cc(d_decim_rate, 1.0)`
  （`receiver.cpp:118`），但开关时需要 `set_demod(..., force=true)`
  触发整条链路重连（`receiver.cpp:485-495`）——因为 dc_corr 串在
  `connect_all` 里是按条件连的。
- IQ balance：直接走 `src->set_iq_balance_mode(2/0)`（`receiver.cpp:511-519`），
  硬件级校正。

### 6.2 DDC（数字下变频）

`ddc = make_downconverter_cc(d_ddc_decim, 0.0, d_decim_rate)`
（`receiver.cpp:114`）。VFO 偏移通过 `ddc->set_center_freq(offset - cw_offset)`
实时调（`receiver.cpp:658`、`receiver.cpp:677`）。

`d_ddc_decim = max(1, (int)(d_decim_rate / TARGET_QUAD_RATE))`
（`receiver.cpp:112`），`TARGET_QUAD_RATE = 1e6`（`receiver.cpp:51`）——
目标是让后续解调链统一工作在 ~1 MSPS，再在 nbrx 内部用
`iq_resamp` 降到 `PREF_QUAD_RATE = 96000`（`nbrx.cpp:29, 43`）。

### 6.3 输入抽数器

`input_decim = make_fir_decim_cc(d_decim)`（`receiver.cpp:95`），
是硬件采样率 > 显示范围时的前置 CIC/FIR 抽数，try/catch 包住，
失败回退 decim=1（`receiver.cpp:97-103`）。

### 6.4 噪声抑制器（Noise Blanker）

`nb = make_rx_nb_cc(PREF_QUAD_RATE, 3.3, 2.5)`（`nbrx.cpp:45`），
在 channel filter 之前。`set_nb_on(nbid, on)` / `set_nb_threshold`
（`nbrx.cpp:136-150`），两个独立 NB 通道。

---

## 7. 对 MBDSDR 的可迁移改进点

对照 `desktop/control_panel.py`、`desktop/spectrum_widget.py`、
`desktop/main_window.py`、`mbdsdr_ai/analog_demod.py`。

### 7.1 设备连接

Gqrx 的做法比常见 Python SDR 前端健壮的地方：

1. **失败回退伪源**：`set_input_device` 里 catch 异常后立刻挂一个
   zero-file 源，UI 不崩（`receiver.cpp:225-233`）。
   → MBDSDR 的 `control_panel.py` 在 `rtlsdr.open()` 失败时应该
   **不要直接 raise 把主循环带死**，而是切到"dummy 源"模式继续画频谱。
2. **重连时先 stop/wait**：`tb->stop(); tb->wait();` 再改设备
   （`receiver.cpp:196-200`）。
   → MBDSDR 切设备/切采样率时，应该先停采集线程、join，再改
   `sample_rate` 属性，避免读线程读到半改的状态。
3. **采样率设了要读回真值**：`src->get_sample_rate()` 读回实际值
   （`receiver.cpp:235-236, 329`）。RTL-SDR 的采样率只能取离散档位，
   Python 端必须 `read_back = dev.get_sample_rate()` 再用真值重算
   FFT bin spacing 和 plotter span，否则频谱会拉伸。
4. **统一设备字符串**：回放 IQ 文件也走 `file=...,rate=...,freq=...`
   同一套 `set_input_device` 路径（`mainwindow.cpp:1732-1737`）。
   → MBDSDR 可以把"真实 RTL 源"和"录制 IQ 回放源"抽象成同一个
   `SdrSource` 接口，UI 层完全无感知。

### 7.2 频谱交互

1. **FFT 拉模式**：`work()` 只灌环形 buffer，GUI 定时器到点才取
   最新一帧 FFT（`rx_fft.cpp:95-156`）。按时间差 `diff` 推进读指针，
   不是固定块大小。
   → `spectrum_widget.py` 现在如果是固定 `read(N)` 再 FFT，会在
   高负载时丢帧/重复帧。改成环形 buffer + 按 `time_delta*samp_rate`
   推进读指针，帧率就跟采集解耦。
2. **点击调 VFO 的两参数**：emit 的是 `(绝对频率, 相对中心 offset)`
   （`plotter.cpp:741`）。MainWindow 判断 offset 是否超半采样率：
   超了就移中心频率（重新 tune 硬件），没超就只改 DDC 偏移。
   → MBDSDR 现在点击频谱大概率是直接 `set_center_freq(click_freq)`，
   每次都重新调硬件。应该引入"VFO offset"概念：小范围（±SR/2）
   内只在 Python 端数字混频移本振，不动硬件；跨边界才真正
   `set_center_freq`。这能把调谐延迟从几十 ms 降到亚 ms。
3. **滚轮语义分层**：裸滚=步进 VFO、Shift+滚=平移带宽、Ctrl+滚=
   调带宽宽度、光标在 Y 轴=dB 缩放、在 X 轴=水平缩放
   （`plotter.cpp:922-996`）。
   → `spectrum_widget.py` 可以照这个映射做，比现在只有"滚轮缩放"
   好用很多。
4. **Peak detect 点击**：开了 peak detect 后，点击频谱自动吸附到
   最近峰（`plotter.cpp:733-736`）。
   → MBDSDR 加个"一键点到信号"功能，先做一个简单的找峰
   （`np.argmax` 局部窗口）即可。
5. **窗口函数可换 + 能量归一**：`rx_fft.cpp:222-238`。
   → 给 `spectrum_widget.py` 加 Hann/Flattop/Blackman 下拉框，
   Flattop 用于幅度校准，Hann 用于看弱信号。

### 7.3 静噪

1. **拓扑：sql 在 AGC 之前**（`nbrx.cpp:76-79`）。
   → `analog_demod.py` 里如果现在是"解调后再静音"，AGC 会被
   噪声抽风。应该把能量检测放在解调之前，sql 触发时直接把
   解调输入接到 null sink。
2. **自动静噪 = 当前电平 + 3 dB**（`mainwindow.cpp:1462`）。
   → MBDSDR 加个"auto squelch"按钮，点一下读 S 表当前值加 3 dB
   写回 spinbox，非常简单但体验提升大。
3. **S 表上画门限线**：`ui->sMeter->setSqlLevel(level_db)`
   （`mainwindow.cpp:1453`）。
   → `meter_widget` 里画一条竖线标记当前 sql 门限，用户一眼能
   看出门限合不合适。
4. **没有 CTCSS**：gqrx 也没做，MBDSDR 如果要做亚音静噪是
   差异化点——可以在 FM 解调后加一个 67-250 Hz 音栓 PLL 检测。

### 7.4 VFO / 解调切换

1. **切模式 = 改解调块 + 重设带通 + 重设 CW BFO + 重设静噪**
   （`mainwindow.cpp:1308-1314` 四行）。
   → `analog_demod.py` 切 AM/FM/SSB 时不要只换解调函数，应该
   同时把 channel filter 的 low/high cut 一起预设好（AM: ±6k，
   NFM: ±5k，USB: 1.5k 高边带，LSB: -1.5k 低边带）。
2. **带宽滤波器热重抽**：`firdes::complex_band_pass` 每次重新设计
   抽头，`set_taps` 热替换，不重建 block（`rx_filter.cpp:88-97`）。
   → Python 里可以用 `scipy.signal.firwin` 预计算 8-10 档带宽的
   抽头缓存，切的时候直接 `fir_filter.taps = cached_taps`，
   避免实时重算卡顿。
3. **SSB 不用移频，直接 `complex_to_real`**（`nbrx.cpp:51, 245-248`）——
   靠 channel filter 选边带，解调块本身极简。
   → MBDSDR 的 SSB 实现可以非常薄：先做单边带带通，再
   `I + 0j*Q`（即取实部），不要去搞希尔伯特变换。
4. **CW 用 SSB + BFO offset**（`mainwindow.cpp:1282-1298`）：
   CW-L 等价 LSB + 700 Hz BFO，CW-U 等价 USB + 700 Hz。
   → 不用单独写 CW 解调，复用 SSB 路径 + 一个可配 BFO 频率。

### 7.5 AGC

1. **AGC 在 sql 之后**（`nbrx.cpp:78-79`）——sql 先把噪声砍掉，
   AGC 只在有信号时工作。
   → MBDSDR 的 AGC 如果现在跑在解调输出端，换到解调输入端。
2. **延迟线对齐**：`DELAY_TIMECONST=15ms` 让增益作用在"延迟后的
   样本"上（`agc_impl.cpp:49, 306`），补偿群延迟避免过冲。
   → Python 简单实现可以用 `np.roll` 或 `collections.deque`
   延迟 N 个样本，跟滤波器群延迟对齐。
3. **Hang 模式**：信号掉了先保持增益 `m_HangTime` 再慢释放
   （`agc_impl.cpp:261-268`）。
   → 对话音 SSB 体验非常关键，否则说话间隙增益会猛涨背景噪声。
   Python 端实现：记录最近 max 幅度的时间戳，信号下降时如果
   距上次峰值 < decay_ms，就不更新增益。
4. **双时间常数 IIR**：attack 2ms / decay 几百 ms（`agc_impl.cpp:56-57`）。
   → 不要用单一 alpha 的简单 AGC，按上升/下降分别用不同 alpha。

### 7.6 工程化小抄

- **配置持久化**：gqrx 把所有参数（设备/采样率/decim/频率/增益/
  sql 电平/AGC 参数/FFT 大小）都存 QSettings，启动时恢复
  （`mainwindow.cpp:545-640`）。MBDSDR 用 `toml`/`json` 做同样的事。
- **状态栏提示**：切设备、录 IQ、停录都 `statusBar->showMessage(...)`
  （`receiver.cpp:1052, 1093, 1702`）。Python 端加个状态栏字符串就行。
- **S 表定时器独立于 FFT 定时器**：`meterTimeout()` 单独 10 Hz
  （`mainwindow.cpp:1471-1478`），不要跟频谱 30 FPS 绑一起。

---

## 8. 关键 file:line 索引（速查）

- 设备构造伪源：`src/applications/gqrx/receiver.cpp:80-88`
- 切设备 stop/wait：`src/applications/gqrx/receiver.cpp:196-200`
- 切设备 catch + 回退：`src/applications/gqrx/receiver.cpp:225-233`
- 设备错误 throw：`src/applications/gqrx/receiver.cpp:251-254`
- 启动时恢复设备：`src/applications/gqrx/mainwindow.cpp:545-561`
- 采样率恢复：`src/applications/gqrx/mainwindow.cpp:597-621`
- 采样率设置+重算下游：`src/applications/gqrx/receiver.cpp:324-369`
- RF 频率设置：`src/applications/gqrx/receiver.cpp:537-545`
- PPM 校正：`src/applications/gqrx/receiver.cpp:717-722`
- VFO offset → DDC：`src/applications/gqrx/receiver.cpp:655-661`
- 默认 FFT 大小 8192：`src/applications/gqrx/receiver.h:108`
- IQ FFT 构造 Hann：`src/applications/gqrx/receiver.cpp:119`
- FFT 拉模式 work：`src/dsp/rx_fft.cpp:95-120`
- FFT get_fft_data：`src/dsp/rx_fft.cpp:126-156`
- FFT 窗口归一：`src/dsp/rx_fft.cpp:222-238`
- 帧率设置：`src/applications/gqrx/mainwindow.cpp:1833-1859`
- 左键点频谱：`src/qtgui/plotter.cpp:730-748`
- 中键移中心：`src/qtgui/plotter.cpp:750-757`
- 右键复位缩放：`src/qtgui/plotter.cpp:758-762`
- 滚轮分层：`src/qtgui/plotter.cpp:922-1000`
- squelch 块构造：`src/receivers/nbrx.cpp:48`
- squelch 在链路位置：`src/receivers/nbrx.cpp:76-79`
- 自动静噪 +3 dB：`src/applications/gqrx/mainwindow.cpp:1460-1468`
- S 表读数：`src/dsp/rx_meter.cpp:80-96`
- S 表定时器：`src/applications/gqrx/mainwindow.cpp:1471-1478`
- 模式切换大 switch：`src/applications/gqrx/mainwindow.cpp:1209-1322`
- 切模式后四件套：`src/applications/gqrx/mainwindow.cpp:1308-1314`
- FIR 热重抽：`src/dsp/rx_filter.cpp:88-97`
- demod 块切换：`src/receivers/nbrx.cpp:192-299`
- AGC 构造参数：`src/receivers/nbrx.cpp:47`
- AGC 时间常量：`src/dsp/agc_impl.cpp:49-63`
- AGC 参数计算：`src/dsp/agc_impl.cpp:124-190`
- AGC 处理主循环：`src/dsp/agc_impl.cpp:197-317`
- FM maxdev/deemph 档位：`src/qtgui/demod_options.cpp:29-91`
- AM-Sync PLL 档位：`src/qtgui/demod_options.cpp:94-127`
- connect_all 链路：`src/applications/gqrx/receiver.cpp:1336-1419`
- set_demod 重连：`src/applications/gqrx/receiver.cpp:865-938`

---

## 9. 一句话总结

Gqrx 的精髓不是算法（FIR/FFT/AGC 都是教科书），而是 **"GUI 只操作 receiver 门面 → receiver 统一 stop/重连/start → 每个 DSP 块都是 GR block 但被门面聚合"** 这套工程结构，加上 **"sql 在 AGC 前、AGC 在 demod 前、VFO offset 优先于硬件 tune"** 这几个拓扑决策。MBDSDR 迁移时最该抄的是这两个，而不是具体的数字。
