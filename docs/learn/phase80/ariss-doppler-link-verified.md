# phase80：ARISS 明早过境多普勒全链路实测保障

**日期**：2026-10-25（CST 凌晨执行，保障当日 06:27 过境）
**场景**：QTH 校园站点（43.8267N, 125.4189E, 220m；站点名按公开仓库身份红线中性化），接收 ARISS(ISS 25544) 145.800 MHz FM 中继
**TLE**：epoch 2026-10-23 16:37:59Z（wheretheiss.at；celestrak 本环境仍超时），AOS 时年龄 ~29.8h < 48h

## 过境预报（10-25 CST，min_alt=5°）

| AOS | 过顶 | max alt | LOS | 时长 |
|---|---|---|---|---|
| 01:35:20 | 01:39:34 | 63.1° | 01:43:50 | 8.5 min |
| 03:12:33 | 03:16:35 | 29.7° | 03:20:38 | 8.1 min |
| 04:50:19 | 04:54:07 | 21.4° | 04:57:55 | 7.6 min |
| **06:27:20** | **06:31:32** | **41.2°** | **06:35:44** | **8.4 min** |
| 08:04:14 | 08:08:18 | 33.5° | 08:12:22 | 8.1 min |

主过境（用户指定 6 点档）145.800 MHz 多普勒：fd +3218.2 Hz（AOS, az 306.3°）→ +16.4 Hz（过顶，az 22.8°）→ -3220.4 Hz（LOS, az 100.0°），全程单调下降，最大变化率 -39 Hz/s，频摆总量 6438.6 Hz。FM 语音 ±2.5 kHz 通带装不下 ±3.2 kHz 频摆 → 补偿是刚需。

## NCO 补偿实测（doppler_verify.py，合成 IQ + 真实 SGP4 曲线）

fs=48 kHz，503 s 全长，音调簇 0/±600/±1500/±2400 Hz + 噪声，先 apply 真实 fd(t) 再 remove 各方案补偿，每秒块 FFT 测残余：

| 方案 | 残余 max | p95 | 语音 ±2.5k 通带保持 |
|---|---|---|---|
| A 精确曲线（每样本） | 0.1 Hz | 0.1 Hz | 100% |
| B1 1 Hz 线性更新 | 0.1 Hz | 0.1 Hz | 100% |
| B2 1 Hz 零阶保持（C++ 循环同构） | 33.9 Hz | 31.0 Hz | 100% |
| C 2 Hz 线性更新 | 0.1 Hz | 0.1 Hz | 100% |
| D 不补偿 | 频摆 ±3.2 kHz | — | 仅 37.2% |

速率极值点特写（t=252.3s, rate=-39 Hz/s）：A/B1/C 残余 -0.0 Hz；B2 恒定 +30.9 Hz（零阶保持滞后 ~0.8s × 39 Hz/s，物理自洽）。

**结论**：仓内 `remove_doppler_shift` NCO 补偿对真实 ISS 频摆精确到数值极限（0.1 Hz）；按 C++ 1 Hz 循环真实行为（ZOH）最坏残余 33.9 Hz，FM 捕获效应无感。不补偿 62.8% 时间语音出通带。

## 单测复跑（全绿）

- `pytest mbdsdr_ai/tests/test_doppler_compensation.py`：7/7 passed
- `test_doppler_limiter`（C++）：ALL PASS，合成 LEO 稳态残余 0.000000 Hz，步幅限幅 2000 Hz/拍
- `test_pass_doppler`（C++）：ALL PASS

## C++ 补偿链路代码级核查（就绪）

- 1 Hz 循环 `MainWindow::updateLiveSatellite`（main_window.cpp:6584）：`propagateAt()` → `dopplerHz(f0, vr)`（tle_client.h:52，-f0·vr/c）→ `dopplerLimiter_.advance(liveFd)` → `vfoSetOffset(f0+off)`
- 限幅 2000 Hz/拍（tokens.h kDopplerMaxStepHz）vs 真实 39 Hz/s → 稳态每拍落 Target
- 非有限目标 → Frozen 保持，不拿 NaN 走 tuner；关闭 → Returning 平滑回零
- 前提诚实：未设站/未捕获 → 复选框禁用（onDopplerCompToggled:6488）
- 初始装订 `captureTargetHz = f0 + dopplerAtPeakHz`（sat_capture.h:79）；145.8 MHz 自动判 NFM 12.5 kHz
- 分工注释（sat_capture.h:85）：TLE 限幅管慢变大幅摆，解调器盲 CFO/Gardner（Python ssdv_phy）管残余快偏，带宽不重叠

## 操作要点（详见交付清单）

- 预置 145.803218 MHz（AOS 装订值），NFM 12.5 kHz
- 天线 AOS 306°NW 起步 → 过顶 41.2° → LOS 100°E
- 06:20 捕获过境 → 06:25 勾选「多普勒自动补偿」→ 状态行「补偿中·累计」确认
- 手跟频备份表 30 s 步进（每档 ≤1.2 kHz）

## 沿革备注

- 7418c14（phase79 收尾提交）本轮推送连续遭遇 TCP 134s 超时 ×2、TLS 非正常终止 ×1（phase77 已登记的间歇性网络抖动），最终与 phase80 一并重推成功
- 教训：重试循环里 `git push | tail -N` 的管道退出码取自 tail（恒 0），`&& break` 会被假成功骗过——git 的真实退出码必须单独捕获（本轮因此误判一次「已推送」）
