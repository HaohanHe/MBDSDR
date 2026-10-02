# Q2 新时空融合：GNSS 授时 / TLE 新鲜度 / 多普勒补偿闭环

> 阶段 7 Wave1 · Q2。环境事实：**云 VM 无硬件、无 GNSS 模块**。所有"真实授时/过境"路径
> 均以**确定性离线测试**（注入固定 NMEA + 固定时钟 + 固定 TLE）验证；无硬件时保持诚实
> 空态并显式标注时间来源，绝不伪造授时/过境/坐标。

## 0. 一句话

把北斗/GNSS 时空信息真正接进 MBDSDR 链路：GNSS UTC → SigMF `captures[0].core:datetime`
（写读两端对齐 + 来源标注）；过境预测时间源显式化 + TLE 历元年龄三态报告；新建通用
NCO 多普勒补偿模块（逐样本/时变频移），并与 Q1 的多普勒补偿收益实验衔接。

---

## 1. 接入点一览（只动允许目录）

| 侧 | 文件 | 改动 |
|---|---|---|
| 时间对齐核心（新） | `mbdsdr_ai/gnss_timestamps.py` | NMEA(RMC/ZDA)→UTC datetime；`resolve_capture_datetime()` 产 `core:datetime` + `mbdsdr:time_source` |
| 读端 | `mbdsdr_ai/playback.py` | `parse_sigmf_meta()` 增读 `captures[0].mbdsdr:time_source`；`IQPlayback.time_source`；`PlaybackSource.get_status()` 增 `recording_time_source` |
| 读端 | `experiments/common/datasource.py` | `RecordingHandle` 增 `time_source` 字段，`SigMFReplay.open()` 透传 |
| 写端 | `tools/onboarding/onboard.py` | `step_record(..., gnss_file=None, clock=None)`：`--gnss` NMEA 日志可选；GNSS UTC 写入 `captures[0].core:datetime`，否则系统 UTC |
| TLE 新鲜度 | `mbdsdr_ai/new_spacetime_tle.py` | 新增统一阈值 `TLE_FRESHNESS_MAX_DAYS=7.0`、`parse_tle_epoch()`/`tle_age_days()`/`tle_freshness()` 三态 |
| 预测时间源（新） | `mbdsdr_ai/spacetime_predict.py` | `predict_passes_report()`：GNSS 时间可用则用之，否则系统时间并标注；报告 TLE 年龄 |
| 多普勒补偿（新） | `mbdsdr_ai/doppler_compensation.py` | `apply/remove_doppler_shift()`：标量/逐样本数组/callable 频移的 NCO 混频（确定性） |

> C++ 录制端 `cpp/src/dsp/recorder.cpp:62` 已写系统 UTC `core:datetime`（Qt ISODate，秒级 Z）。
> 本 Q2 不改 C++（红线：只动 `mbdsdr_ai/ experiments/ tools/onboarding/ docs/`）；Python 写端
> `onboard.step_record` 与之同格式（`YYYY-MM-DDTHH:MM:SSZ`），回放两端读到一致。

---

## 2. GNSS 授时 → SigMF captures 对齐

### 2.1 写入端（onboard record 步）
- `--gnss <nmea.log>`（可选）：逐行喂 `NMEAParser`，**优先 ZDA**（自带年月日），其次
  RMC（`hhmmss.ss` 时刻 + `ddmmyy` 日期组合）。
- 得到合法 GNSS UTC → `captures[0].core:datetime=<GNSS UTC ISO>`，并写
  `mbdsdr:time_source="gnss"`、`mbdsdr:time_note=...`。
- 无 `--gnss` / 解不出合法日期 → `core:datetime=<本机 UTC>`，
  `mbdsdr:time_source="system"`。**绝不冒充 GNSS 授时**。

### 2.2 诚实空态（重要）
- NMEA 的 `utc_time`（GGA/RMC）只是**一天内时刻**，本身不含日期。
  **仅 GGA（无 RMC/ZDA）时无法锚定日期 → 诚实返回 None，退回系统时间**，
  绝不"猜一个日期"。测试 `test_gga_only_no_date_is_honest_none` /
  `test_onboard_record_gga_only_falls_back_system` 锁住此行为。

### 2.3 读取端对齐
- `playback.parse_sigmf_meta()` 现返回 `datetime` + `time_source`；
  `IQPlayback` / `RecordingHandle` / `PlaybackSource.get_status()` 均透传。
- 旧 SigMF 文件无 `mbdsdr:time_source` 字段 → 读端返回 `""`（未知），不臆断。

### 2.4 确定性写读一致
注入固定 NMEA（RMC=`072545.00`+date=`021026`）+ 固定时钟，经 `step_record` 写出后用
`parse_sigmf_meta` 读回：`core:datetime=="2026-10-02T07:25:45Z"`、`time_source=="gnss"`。
无 GNSS 时 `core:datetime=="2026-10-02T08:00:00Z"`、`time_source=="system"`。

---

## 3. TLE 新鲜度与过境预测联动

- **统一阈值**：`new_spacetime_tle.TLE_FRESHNESS_MAX_DAYS = 7.0`（LEO SGP4 外推经验：
  <~3 天可靠，>~7 天方位/多普勒开始漂移）。全仓唯一阈值。
- **历元解析**：TLE 第 1 行固定列第 19–32 列（`YYDOY.DDDDDDDD`）→ UTC datetime（不依赖空格分词）。
- **三态**：`fresh`（age≤7d）/ `stale`（age>7d，仍预报但告警）/ `none`（无/坏 TLE，不出假预报）。
- **预测时间源显式化**：`predict_passes_report(gnss_dt=...)` 给 GNSS UTC 则用之并标
  `time_source="gnss"`；否则 `clock=`（默认本机 UTC）并标 `"system"`。报告恒带
  `prediction_start_utc` + TLE `epoch_utc/age_days/threshold_days`。

实测（ISS TLE 历元=2024-10-01 12:00Z）：
- now=2024-10-02 → `fresh`，age=0.5 d；
- now=2024-12-01 → `stale`，age=60.5 d；
- 坏 TLE → `none`。

---

## 4. 多普勒补偿闭环

### 4.1 模块 `mbdsdr_ai/doppler_compensation.py`
- 接收模型 `r(t)=s(t)·exp(+j2π∫f_d)`，补偿 `s̃=r·exp(-j2π∫f_d)`（NCO 逐样本相位累积）。
- `doppler_hz` 支持：**标量**（恒定偏）/ **逐样本数组**（实测 AFC）/ **callable(t)->Hz**
  （SGP4 轨道多普勒曲线，如 `sat_passes.compute_doppler_curve` 输出）。
- 全程 float64、无全局随机态；`apply_doppler_shift`（合成用）与 `remove_doppler_shift`
  成对，逆运算精确回原（chirp 往返误差 ~6e-8）。
- 无硬件空态：不给 `f_d`（=0）即恒等，绝不"猜"多普勒。

### 4.2 与 Q1 实验的分工（避免双写）
`experiments/exp_doppler_comp.py` 由 **Q1 负责**（恒定 fd 的 BPSK 整包成功率，内联
`exp(-j2πfd t)`）。本 Q2 提供**通用时变 NCO 模块 + 基础测试**，覆盖 Q1 恒定模型未覆盖的
**逐样本/callable 时变频移**（chirp）情形，二者互补不冲突。Q1 实验真实数字（trials=60，
固定 Eb/N0=8 dB，BPSK 10k，包长 20 ms）：

| 残余 fd (Hz) | 相位漂移/包 | 不补偿成功率 | 补偿成功率 |
|---:|---:|---:|---:|
| 0   | 0.0 | 0.95 | 0.95 |
| 25  | 0.5 | **0.00** | **1.00** |
| 100 | 2.0 | **0.00** | **0.95** |
| 200 | 4.0 | **0.00** | **0.93** |
| 400 | 8.0 | **0.00** | **1.00** |

即：仅 25 Hz 残余频偏（包内 180° 相位漂移）即可让整包解码崩到 0%，已知多普勒补偿后
恢复到 0.93–1.00——量化了"轨道预测多普勒补偿"的上界收益。口径 `synthetic`（固定种子）。

---

## 5. 确定性离线测试（pytest）

新增（本 Q2，全绿）：
- `mbdsdr_ai/tests/test_gnss_timestamp_alignment.py` — 8 tests
- `mbdsdr_ai/tests/test_tle_freshness_predict.py` — 5 tests
- `mbdsdr_ai/tests/test_doppler_compensation.py` — 7 tests

合计 **20 passed**。回归（既有，不破坏）：
- `tests/test_playback.py` / `tools/onboarding/test_onboarding.py` /
  `tests/test_new_spacetime_tle.py` / `mbdsdr_ai/tests/test_sat_passes.py` → **30 passed**
- `experiments/tests/` → **28 passed**

运行：
```
python3 -m pytest mbdsdr_ai/tests/test_gnss_timestamp_alignment.py \
                  mbdsdr_ai/tests/test_tle_freshness_predict.py \
                  mbdsdr_ai/tests/test_doppler_compensation.py -q
```

---

## 6. 诚实声明与未完成项

- **无硬件空态**：云内无 GNSS 模块、无 SDR。GNSS 授时对齐用"注入固定 NMEA + 固定时钟"
  的确定性测试证明写读链路正确；真实 GNSS 模块（ATGM336H/u-blox）上线时把 `--gnss`
  指向实时 NMEA 流即可，时间来源会自动标 `gnss`。
- **TLE 预测**：`predict_passes_report` 用离线 ISS TLE + 固定起点验证；真实过境对准
  仍需用户在真机上录 IQ 复核。
- **多普勒补偿**：模块确定性正确；"已知 `f_d(t)`"在真链路上来自
  `sat_passes.compute_doppler_curve`（TLE 预报）或 AFC 实测——真实 OTA 补偿效果待录。
- **未改动 C++ recorder**（红线）；C++ 录制端目前写系统 UTC，`mbdsdr:time_source` 标签
  待后续在 C++ 侧补（Python onboarding 路径已具备）。
- **观察项（属 Q1，未改）**：`exp_doppler_comp.py` 在某 fd 网格下画图时
  `errorbar yerr` 遇到成功率=1.0/0.0 的 Wilson 边界会抛负 yerr（CSV/manifest 正常落盘），
  留 Q1 修。
- 未 commit/push（按红线）；产物 `paper/experiments/` 被 gitignore。
