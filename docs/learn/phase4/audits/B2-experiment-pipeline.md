# B2 实验管线审计（只读）

> 审计基线：远端 main `d331681`；审计日 2026-10-01（云 VM）。
> 方法：逐行读 `experiments/exp_*.py`、`mbdsdr_ai` 相关内核、`cpp/src/dsp|ai` 摄取与定轨代码，不只看 README。
> 红线遵守：纯只读，本报告是唯一写出物；推断处显式标「推断」。

---

## 1. 逐脚本现状表

口径列：诚实度 = 脚本是否如实标注"纯合成/无硬件"。样本/种子列给 file:line。

| 脚本 | 被测对象 | 数据源（证据） | 评估指标 | 输出 | 口径诚实 | 样本数 / 种子 | 可复现性 |
|---|---|---|---|---|---|---|---|
| `exp_amr.py` | `AMRClassifier`（25 维特征+KNN，`mbdsdr_ai/amr.py:359`） | **合成**：`synthesize_modulation_iq`（`exp_amr.py:61`；生成器 `amr.py:297`） | 8 类准确率 vs SNR、8×8 混淆矩阵 | `paper/experiments/amr_accuracy_vs_snr.csv`、`amr_confusion_matrix_snr10.csv` | 诚实（`exp_amr.py:4-11` 明说纯软件） | trials=30/类/SNR（`exp_amr.py:154`）；train seed=20260919（`amr.py:375`）、test seed=1234（`exp_amr.py:40`），二者显式隔离 | 高（种子派生确定 `exp_amr.py:59-60`） |
| `exp_ax25_performance.py` | `ax25.AFSKModem` Bell202（`ax25.py:361`） | **合成**：44.1k 合成后降采样（`exp_ax25_performance.py:32`） | FCS(CRC16) 通过率 | `paper/experiments/ax25_demod_performance.csv` | 诚实（模拟声卡采集） | 10 帧/格（`exp_ax25_performance.py:17`）；seed=`1000*sr+k`（`:34`） | 中（seed 派生可复现，但每格仅 10 帧，统计功效弱） |
| `exp_digital_modes.py` | `sstv_decoder`、`adsb.decode_baseband` | **合成**：pysstv 编码 MartinM1、随机 ADS-B 帧（`exp_digital_modes.py:80,154`） | SSTV 逐通道 Pearson/RMSE/行同步；ADS-B 前导检测率、CRC 通过率、呼号正确率、BER；纯噪声虚警 | `digital_sstv_quality.csv`、`digital_adsb_decode.csv`、`digital_adsb_false_alarm.csv` | 诚实（`:5-21` 明说闭环无 dump1090/RTL） | ADS-B trials=200（`:219`，但已提交 CSV 是 20，说明跑过早期版本）；SSTV 12；seed=20260919 | 高 |
| `exp_fhss_detection.py` | `decoders.detect_fhss`（`decoders.py:619`） | **合成**：分块置换跳频+复噪声（`exp_fhss_detection.py:52-72`） | 检出率/信道数正确率 vs SNR、虚警率 vs 门限、参数 MAE | `fhss_pd_vs_snr.csv`、`fhss_pfa_vs_threshold.csv`、`fhss_param_vs_snr.csv` | 诚实且功率约定写明（`:16-18`）；并自注 best-case、复杂工况留待后续（`:20-21`） | trials=300（`:193`）；seed=20260919 | 高 |
| `exp_freq_offset.py` | `cfo.estimate_and_correct`（Kay+FFT 两级） | **合成**：单音+复噪声（`exp_freq_offset.py:28-32`） | 残余频偏 RMS vs SNR、vs 块长、vs ppm | `cfo_accuracy_vs_snr.csv`、`cfo_vs_blocklength.csv`、`cfo_ppm_scenario.csv` | 诚实 | trials=500；seed=20260919 | 高 |
| `exp_pnt_fusion.py` | 逆方差加权融合（脚本内 `:33-41`） | **合成**：真值+高斯噪声观测（`:26-30`） | 5 场景 RMSE/均值/最大误差 | `pnt_fusion_montecarlo.csv` | 诚实（`:7-8` 明说观测=真值+噪声） | N=300/场景（`:23`）；seed=42（`:73`） | 高，但融合引擎是脚本内简化版，非 `position_service.py` 真实链 |
| `exp_spectrum_sensing.py` | `spectrum_sensing.EnergyDetector` | **合成**：H0/H1 能量统计（`:39-48`） | ROC、Pd vs SNR、噪声不确定度/SNR wall | `sensing_roc.csv`、`sensing_pd_vs_snr.csv`、`sensing_noise_uncertainty.csv` | 诚实（含与理论曲线对照） | trials=4000；seed=20260919 | 高 |
| `exp_sstv_identification.py` | 数据驱动 SSTV 制式识别（`sstv_decoder`） | **合成**为主（`:126-141`）+ **真实 OTA 钩子**：`run_real_ota`（`:253-264`）读 `real_sstv.wav`，文件不存在则打印跳过、返回 None | 识别率 vs SNR、Robot36 恢复质量、纯噪声虚警；OTA 单样本判定 | `sstv_mode_identification.csv`、`sstv_robot36_quality.csv`、`sstv_real_ota.json` | 诚实空态已就位（`:254-256`）；`sstv_real_ota.json` 记录真实 Robot36 解码（240 行，timing 法） | ID/解码 trials 默认 8（`:279-280`，偏少）；seed=20260919 | 合成高；OTA 仅 1 个文件、无 SNR 标注 |
| `exp_weak_model_toolcall.py` | `MBDSDRAgent`+`register_sdr_tools` 工具编排 | **真 LLM 调用**：读 `~/.mbdsdr/config.json` 打在线 API（`:35-37`） | call/pick/arg 三级成功率 | `weak_model_toolcall.csv` | 半诚实（非合成，但）**无种子、不可云内复现**（`:49` 每次实时推理） | 仅 10 个固定 case（`:20-31`），无重复/无 CI | **低**：云 VM 无 API key 跑不了；结果不可复现 |

子目录产出（**无脚本、不可复跑**）：
- `experiments/e2e_check/fm_chain_proof.png`、`experiments/fm_stereo/*.wav`、`experiments/noaa_apt/*.png`——均为临时产物，`find` 未发现对应 `.py`，属手工/历史运行，无 manifest、无种子，不能入论文数据图。

---

## 2. 缺口分析

### 2.1 解码成功率 vs Eb/N0：现在全是 SNR，无一处 Eb/N0

**现状**：全仓 grep `eb/n0|ebn0|energy_per_bit` 为空。所有脚本统一用"信号功率/带内噪声功率"的 SNR：
- 复基带：信号归一化单位功率，噪声功率 `sig_p/10^(snr/10)`（`amr.py:350-355`、`adsb.py:640-643`、`exp_freq_offset.py:29-32`、`exp_fhss_detection.py:59`）。
- 实音频：`y += rms·10^(-snr/20)·n`（`exp_digital_modes.py:92`、`exp_sstv_identification.py:141`、`exp_ax25_performance.py:36`）。

**换算公式（推导）**：观测带宽 B 内噪声功率 Pn = N0·B；信号功率 Ps；比特率 Rb；每比特能量 Eb=Ps/Rb。
```
Eb/N0 = (Ps/Rb)/N0 = Ps/(N0·Rb) = (Ps/(N0·B))·(B/Rb) = SNR_meas · (B/Rb)
(dB)  (Eb/N0)_dB = SNR_meas_dB + 10·log10(B / Rb)
```
适用条件：噪声为白噪声、Ps 与 N0 同口径；**B 必须是"注入噪声、且判决器实际看到"的带宽**。本批脚本噪声铺满整个 fs 且判决器不额外带限，故对复基带脚本取 **B=fs**，对实音频脚本取 **B=音频 fs**（保守、含带外噪声）；若要与教科书曲线对齐，应另起一组"噪声仅注入信号带宽 B_sig"的对照。

**各模式应取的 B/Rb 与修正量 Δ=10log10(B/Rb)**：

| 模式 | Rb 证据 | B（本管线实测口径） | Δ(dB) | 是否报 Eb/N0 |
|---|---|---|---|---|
| AMR-FSK | sps=20, fs=100k→5k bps（`amr.py:321-323`） | fs=100k | +13.0 | 是 |
| AMR-BPSK | sps=16→6250 bps（`amr.py:326-328`） | fs=100k | +12.0 | 是 |
| AMR-QPSK | Rs=6250, Rb=12500（`amr.py:330-334`） | fs=100k | +9.0 | 是 |
| ADS-B Mode-S | 1 Mbps（`adsb.py:144` fs=4e6，112bit/1μs） | fs=4e6 | +6.0 | 是 |
| AX.25/AFSK | 1200 bps（`ax25.py:45`） | 音频 fs=44.1/22.05/11.025k | +15.6/+12.6/+9.6 | 是（须注明 B；若按占用带宽 ~2.4k 则仅 +3.0，二选一并写死） |
| CW/OOK | 无固定 Rb | — | 用"每点划能量/N0"，非 Eb/N0 | 否（报 E_symbol/N0） |
| AM/FM、SSTV | 模拟、无比特 | — | 无 Eb/N0 | 否（报带内 SNR / 恢复图 PSNR） |
| FHSS、CFO、频谱感知 | 非比特流能量/参数估计 | — | 无 Eb/N0 | 否（SNR-in-bandwidth 即正确口径） |

**最小实现**：新增 `experiments/common/ebno.py`，内置上表 `(mode, Rb, B, B_source_fileline)` 常量表；`SyntheticChannel` 同时记录 `snr_db` 并导出 `ebn0_db`；CSV 同时存两列，图横轴用 Eb/N0、图题注明换算用 B。

### 2.2 录制/OTA 摄取：C++ 能录能放，但 Python 实验管线没接进来

**现状资产**：
- 录制：`cpp/src/dsp/recorder.cpp:33-99` 写 **SigMF**（`.sigmf-data` cf32_le + `.sigmf-meta` 记 sample_rate/frequency/datetime/gain/hardware，`:49-70`）——这是真实 OTA IQ。
- 回放：`cpp/src/dsp/file_source.h:24-67` 流式读 SigMF/16bit WAV/raw cf32，`isConnected()=false` 诚实标注离线。
- Python 已有 SigMF 原生回放器 `mbdsdr_ai/playback.py:93` `IQPlayback`（mmap、`read_samples(n)`、从 meta 读 sample_rate/center_freq，`:150-151`）。
- 但 `orbit_determination.load_iq_file`（`:197-254`）**只读 .wav/.cf32/.raw/.iq，不认 SigMF**；9 个 exp 脚本无一消费录制文件。唯一 OTA 钩子是 `exp_sstv_identification.py:253` 读单个 `real_sstv.wav`。

**最小实现**：`experiments/common/datasource.py` 提供统一 `DataSource` 抽象：`SyntheticChannel`（合成）/`SigMFReplay`（包 `playback.IQPlayback`，从 meta 读 fs/freq/datetime）/`WavReplay`（包 `orbit_determination.load_iq_file` 的 wav 分支）。**空态**：扫描约定目录（如 `~/mbdsdr/recordings/`）无 `.sigmf-data` 时，OTA runner 打印"未检测到录制，跳过 OTA 段"并只产出仿真图，**绝不合成数据补 OTA 格**（与 `orbit_determination.py:207,271,870-876` 已有的诚实拒绝风格一致）。

### 2.3 基线对比：经典算法 vs AI/Agent 还没在同一数据集上比

**现状**：经典路径散落在产品内核（`ax25.AFSKModem`、`adsb.decode_baseband`、`cw_decoder.decode_cw`（`cw_decoder.py:67`）、`digital_modes.demodulate_qpsk`+viterbi（`digital_modes.py:224,231`）、C++ `DigitalDemod` BPSK/QPSK 带 EVM%/Costas 锁（`cpp/src/dsp/digital_demod.h:46-72`））；AI 路径是 `AMRClassifier`(KNN) 与 LLM 工具编排（`exp_weak_model_toolcall.py`）。但**没有任何脚本让两条路径吃同一份合成样本、同一 SNR/Eb/N0 网格**。

**最小实现**：`exp_baseline_compare.py`，同一 seed 生成的样本，`method ∈ {classic, knn_amr, llm_agent}` 三列写进同一 CSV；PSK 用 Python QPSK+viterbi 对 `AMRClassifier`；AX.25 用 `AFSKModem` FCS 对"LLM 调 `sdr_aprs_decode`"。**注意**：LLM 列需 API、不可云内确定性跑——要么录一条 fixture 转写离线重放，要么云内只比 `classic vs knn_amr`，LLM 列标"待 API/真机"。

### 2.4 多普勒定轨收敛：零件齐全，缺一个可跑的合成实验

**现状资产**（`mbdsdr_ai/orbit_determination.py`）：`detect_doppler` FFT 找峰（`:147`）、`extract_doppler_observations`（`:257`，观测不足返回空、不补点 `:271`）、7 维 ECEF 状态 EKF（`:450`）、参考历元 RLS（`:483`）、`leosat_state_from_tle`（`:535`，sgp4 传播）、端到端 `doppler_orbit_determine`（`:857`，<2 点直接拒绝 `:873-876`）、`plot_convergence_curve`（`:804`，已用 matplotlib Agg）。C++ 侧 `sat_task_planner.h:48-77` 用同套 SGP4 算过境与峰值多普勒、无 TLE 时诚实 `ok=false`。

**缺口**：模块**没有 main/自测试**（grep 无 `__main__/demo/simulate/truth`）——即没有"真值轨道→加噪多普勒观测→解算→位置误差收敛曲线"的可跑闭环；`plot_convergence_curve` 需要的 `pos_err_rls/pos_err_ekf` 序列目前无人产出。

**最小实现（云内可确定性仿真）**：固定一条缓存 TLE（如 NOAA-19/ISS），用 `leosat_state_from_tle` 生成一次 10 min 过境真值，在长春站（43.8868,125.3245）逐历元用 `pseudorange_rate_and_jacobian`+`rangerate_to_fd`（`:297`）算真 fd(t)，加高斯噪声 σ_fd∈{1,5,10,20,50} Hz，给 `doppler_orbit_determine` 一个加了 ~5 km/50 m/s 扰动的初值，逐时刻用 `position_error_curve`（`:700`）算三维位置误差，调 `plot_convergence_curve` 出图，扫 σ_fd 得"收敛误差地板 vs 观测噪声"。**这部分完全可云内跑、必须标"仿真"**。
**必须 OTA 的部分**：真实录制过境 IQ 经 `extract_doppler_observations` 验证 FFT 前端在多径/AGC/真实噪声下的可用性——无录制时走 2.2 的诚实空态。

### 2.5 数据图自动生成：matplotlib 可用，但目前零图

**现状**：仓库只有临时 PNG（`fm_chain_proof.png`、`real_*_decoded.png`），**没有任何脚本自动出论文图**；CSV 也无误差棒/置信区间/生成日期列。已提交 `digital_adsb_decode.csv` 显示 n_trials=20（与脚本默认 200 不符），说明产物与脚本漂移，更需 manifest 锁定。

**绘图能力**：matplotlib 3.11、scipy 1.17、numpy 2.5 均在云 VM 可用；`orbit_determination.py:810-811` 已示范 `matplotlib.use("Agg")` 无头出图。

**图清单**（统一入 `paper/experiments/figures/`）：
1. 各模式"成功率 vs Eb/N0"曲线（FSK/PSK/QPSK/AX.25/ADS-B），带 Wilson 95% 置信区间误差棒；
2. AMR 在固定 Eb/N0 的 8×8 混淆矩阵热力图；
3. 多普勒收敛曲线（RLS vs EKF，纵轴 log 位置误差 km，含 1 km 阈值线）；
4. 基线对比分组柱状图（classic vs AI，同 Eb/N0 档）；
5. 频谱感知 ROC / Pd-vs-SNR（已有 CSV 可直接补画）；
6. EKF 残差直方图（`plot_residual_histogram` 已有，`:836`）。

**强制图注规范**：图题与文件名必须含 口径(仿真/录制/OTA) + 样本数 N + 生成日期(UTC)；坐标轴带物理单位；图例写明 method；误差棒=二项 Wilson 95% CI。

---

## 3. 管线设计

### 3.1 目录职责
- `experiments/`：可运行入口 `exp_*.py`（薄封装），新增 `common/` 公共库。
- `experiments/common/`（**新建**）：
  - `ebno.py`：SNR↔Eb/N0 换算 + 各模式 `(Rb, B)` 常量表（带 file:line）；
  - `datasource.py`：`DataSource` 抽象 = `SyntheticChannel`/`SigMFReplay`(包 `playback.IQPlayback`)/`WavReplay`，空态诚实；
  - `runner.py`：固定 seed 循环、Wilson CI、trials 统计；
  - `manifest.py`：每次运行写 `manifest.json`（git sha、seed、fs、Rb、B、method、`data_origin∈{synthetic,recorded,ota}`、UTC 时间）；
  - `plot.py`：统一风格出图（Agg、强制图注规范）。
- `paper/experiments/`：**只做数据落点**（CSV/PNG/manifest），不手改；图入 `figures/` 子目录。
- `paper/EXPERIMENTS.md`：人读叙述，引用图与 CSV，不手算数字。

### 3.2 CLI 与复现
每个新脚本统一 `--trials --seed --out --data-origin{synthetic,recorded,ota} --recordings-dir`；退出码区分"跑通仿真"与"无录制而跳过 OTA"（后者非失败）。`experiments/README` 给一行复现命令。

### 3.3 文件所有权（交 W2-exp agent）
| 新文件 | 责任 |
|---|---|
| `experiments/common/{ebno,datasource,runner,manifest,plot}.py` | 公共底座 |
| `experiments/exp_ebno_decode.py` | FSK/PSK/QPSK/AX.25/ADS-B 成功率 vs Eb/N0（仿真） |
| `experiments/exp_doppler_convergence.py` | 固定 TLE 合成过境收敛曲线（复用 `orbit_determination`） |
| `experiments/exp_baseline_compare.py` | 同数据集 classic vs AI |
| `experiments/exp_ota_handoff.py` | 扫录制目录接 SigMF/WAV，无则诚实跳过 |
| 现有 9 个 exp | 仅**追加**写 manifest/补 Eb/N0 列，不改行为 |

---

## 4. 云内可跑（必须标"仿真"） vs 必须等真机录制

**云内纯合成可跑、图题必写"仿真"**：
- AMR 准确率/混淆矩阵 vs Eb/N0；AX.25/ADS-B/PSK 解码成功率 vs Eb/N0；CFO 残余；FHSS 检测；频谱感知 ROC/SNR wall；多普勒定轨**合成**收敛（固定 TLE+加噪）；classic vs KNN 基线。

**必须等用户真机（无录制时诚实空态、绝不冒充 OTA）**：
- 任何真实过境 IQ 的多普勒前端验证（SigMF 录制 → `extract_doppler_observations`）；
- 真实 SSTV/NOAA APT/ADS-B OTA 解码成功率与样本数（现 `experiments/noaa_apt`、`fm_stereo` 无脚本不可复跑，需先补录制+脚本）；
- 弱模型 toolcall 指标：需在线 LLM API，云 VM 无 key；建议录 fixture 离线重放，否则标"待 API"。

---

## 5. 五条最关键发现（回报摘要）

1. **全仓零 Eb/N0**：9 个脚本全是"带内 SNR"口径（`amr.py:353`/`adsb.py:640` 同式），论文要的 Eb/N0 需按 `Eb/N0=SNR·B/Rb` 换算；FSK 修正量高达 +13 dB、ADS-B +6 dB，不换算会严重误读曲线位置。
2. **录制摄取链在 C++ 已闭环、Python 没接**：`recorder.cpp:36-70` 写标准 SigMF，`file_source.h` 能回放，Python `playback.IQPlayback` 能读 SigMF，但 `orbit_determination.load_iq_file:197` 不认、9 个 exp 无一消费录制——这是 OTA 诚实空态的主缺口。
3. **多普勒定轨"零件全、闭环无"**：EKF/RLS/TLE 传播/收敛绘图函数齐全（`orbit_determination.py`），但没有可跑的"真值→加噪观测→收敛曲线"实验，需 W2-exp 补一个确定性合成实验（云内可跑，标仿真）。
4. **基线对比尚未同场竞技**：经典 DSP（含 C++ `DigitalDemod` EVM%）与 AI/KNN、LLM 路径各跑各的，无同一 seed 数据集、同一指标的对照实验。
5. **统计功效与可复现性有硬伤**：AX.25/SSTV 每格仅 8–10 次试验、无置信区间；已提交 ADS-B CSV(n=20) 与脚本默认(trials=200)漂移；`weak_model_toolcall` 无种子无 API 不可复现；图全部缺失、CSV 无日期/manifest——必须由 `common/runner+manifest+plot` 统一补齐。
