# 校准图形化演示（手台掐频 → SDR 收到 → 图形化校准）

> 教学相长场景：用一部公众对讲机手台，在已知精确频率上发射，SDR 收到后**图形化测量**出本机晶振的 ppm 偏差。
> 本轮结论：**测量能力与图形化向导此前已落地，本轮做复用核查 + 小偏移精度回归 + 三档快照 + 文档**，未新建重复模块/工具。

## 1. 场景（409.750 / 438.500 公众对讲机）

公众对讲机（免执照 UHF 段）信道频率是**公开且精确**的标称值，例如：
- **409.750 MHz**（中国公众对讲机信道之一）
- **438.500 MHz**（典型 UHF 业余/公众通联频点）

手台晶振通常也有 ±几 ppm 偏差，但**对校准而言，我们把"手台当前所在信道的标称频率"当作已知参考**——真正要测的是 SDR 接收头的晶振误差。手台按住 PTT 发射一段未调载波（或一串话音/亚音），SDR  Tuned 到该标称频率上。如果 SDR 晶振偏了，这个载波在基带里就不会落在 0 Hz，而是偏移一个残余 `offsetHz`。

## 2. 复用的测量链（file:line）——本轮未新建模块

侦察后确认整条链已存在，**不重复造轮子**：

| 环节 | 位置 | 作用 |
|---|---|---|
| FFT 找峰 + 抛物线亚插值 | `dsp/frequency_calibrator.cpp` `measureCarrier()` | 在期望基带位置附近找参考载波峰，亚 bin 细化到 Hz 级 |
| 残余→ppm | `frequency_calibrator.h:18-26` 干净室符号约定 | `ppm = -offsetHz / Fc * 1e6`（kalibrate 同约定） |
| 多段聚合 + 一致性剔除 | `calibrateFromCapture()` | 分 4 段测，剔除离群、给置信度/散布 |
| 诚实空态 | `CalibrationMeasurement.detected` / `CalibrationResult.detected` | 无载波过门限 → `detected=false`，**绝不编读数** |
| 引擎喂数 | `SpectrumEngine::captureForCalibration`（`agent_tools.cpp:366` 调用） | 抓一段复数 IQ 给校准器 |
| 图形化向导 | `ui/calibration_dialog.{h,cpp}` | 分步向导：选参考→填已知频率→跟操作指引（手台 PTT）→实时频谱+锁定峰+残余 Hz+ppm→应用保存 |
| 三通道（读/写） | `tool_schema.cpp:221 calibrate_frequency`（读测量）、`:262 apply_freq_correction`（写 ppm） | 已暴露给 AI/HTTP/控制面 |

**结论**：现有 `measureCarrier` 的"FM 零差/载波偏差估计"语义已经满足校准演示所需——它就是"解调前基带复数 IQ → 载波相对 VFO 中心的偏差（Hz）"，比从解调音频估计更直接（不依赖 NFM 鉴频线性）。故**跳过新模块**。

## 3. ppm 语义

- `offsetHz = measuredBasebandPos - expectedBasebandPos`（Hz）。
- `ppm = -offsetHz / Fc * 1e6`（Fc = 标称调谐 RF 中心 Hz）。
- 物理含义：SDR 晶振跑快（正误差）会把外部参考拉到**更低**的基带位置（offset<0），校正为正；把这个 ppm 交给 `rtl_set_freq_correction` / `setPpm` 即把残余拉回 0。
- 例：截图里注入 +32 ppm、Fc≈446 MHz → 残余 ≈ −14266 Hz，−(−14266)/446e6·1e6 ≈ +31.99 ppm，与注入一致。

## 4. 本轮新增的确定性回归

`test_frequency_calibrator.cpp::smallHandheldOffsets_recoveredToWithinFewHz()`：
在两个演示中心（409.75 MHz / 438.5 MHz）上，把载波分别放在 **+500 Hz / −250 Hz / 0** 基带偏移，断言 `measureCarrier().offsetHz` 与注入值误差 **< 5 Hz**；纯噪声在既有 `noiseOnly_notDetected` 用例里诚实返回 `detected=false`。

## 5. 测试计数（offscreen 真实）

| 二进制 | 结果 |
|---|---|
| test_frequency_calibrator | **11 passed, 0 failed**（+1 小偏移精度用例） |
| test_calibration_dialog | **3 passed, 0 failed**（含三档快照渲染） |

## 6. UI 快照（MBD_CAL 三档）

复用 `test_calibration_dialog.cpp::renderScreenshot_offscreen`，加 `MBD_W` 宽度参数（640/960/1920）+ `MBD_CAL_TAG` 标签。三档均出图，核查：
- 实时频谱窗 + 锁定峰竖线清晰；
- 大字读数 **31.987 ppm**、残余频偏 −14266.3 Hz、置信度 100%、散布 ±0.000 ppm、最差 SNR 69.9 dB；
- **0 裁切、0 叠字**（640 窄轨下控件仍完整）；
- 空态由既有 `noiseOnly_honestEmpty` / `noProvider_honestEmpty` 用例覆盖（无信号→不编读数）。

## 7. 三通道工具 —— 诚实说明

- 桌面金集当前为 **53 个**（任务给的"92→94"为过期数字）。
- 校准的读/写**已由既有工具覆盖**：`calibrate_frequency`（读：ref/measured_offset/ppm/valid）、`apply_freq_correction`（写 ppm）。
- 故**不新增** `set_cal_ref_freq`/`get_cal_status`（会与既有两个工具语义重复，且 `agent_tools.cpp` 是并行 FT8 run-loop 会话的活跃文件，避免冲突）。金集保持 **53 / mobile 53**。

## 8. 图文操作指引（手台校准）

1. SDR 端：打开"频率校准"向导，参考类型选**手台引导**。
2. 手台端：调到一个你**知道精确标称频率**的信道（如 409.750 MHz 或 438.500 MHz），写进向导的已知频率框（**仅作参考值，不预置为默认/电台预设**）。
3. SDR 端 VFO 也调到该标称频率。
4. 手台**按住 PTT** 发射（最好掐一段静噪载波或话音，别松太快）。
5. SDR 向导点"实时刷新"：实时频谱里应冒出一根尖峰；锁住后读数显示**残余频偏(Hz)** 与估计 **ppm**。
6. 点"查看结果 → 应用"：把 ppm 写进 `rtl/ppm` 并交给引擎 `setPpm`；再测一次残余应≈0。
7. 无信号/手台没叫 → 向导诚实显示"未检测到参考载波"，不编读数。

## 9. 诚实未完成项

- 未做端到端真实手台/真实 RF 录制回归（本环境 null-audio/offscreen）；精度结论基于确定性合成 IQ，Hz 级误差 <5 Hz 为合成域结论。
- 未新增 `set_cal_ref_freq`/`get_cal_status` 工具（既有 `calibrate_frequency`/`apply_freq_correction` 已覆盖语义）；如需更窄的"只改参考频率不动 ppm"写接口，可下轮再加。
- 未改主窗口（`main_window.*` 为并行 FT8 会话活跃文件）；校准入口仍走既有 `CalibrationDialog` 向导，未在频谱页叠加新面板。
