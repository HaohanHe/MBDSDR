# Phase51 块3：experiments/ 实验可复现性复核报告

> 复核时间：2026-10-06（UTC+8）。基线 HEAD = `31d7b34`（manifest 实测 `code_version=31d7b34`）。
> 环境：Python 3.12.11 / numpy 1.26.4 / scipy 1.13.1；`mbdsdr_ai` 可导入。云内**无** `MBDSDR_LLM_API_KEY`。
> 方法：按 `paper_submission_package/MANIFEST.txt` 与 `SELFCHECK.txt` 记录的口径（seed/trials）逐脚本重跑，
> 输出用 `--out` 重定向到 `experiments/_phase51_verify/`（隔离暂存，不覆盖既成产物），再与打包
> `paper_submission_package/data/*.csv` 逐字节 diff；manifest 的 seed/n_samples/data_origin 与 CSV 行计数交叉核对。

结论先行：**10 张论文图对应的 common/ 管线脚本全部确定性复现，CSV 与打包产物逐字节一致**；
Wilson 95% CI 独立复算吻合；口径标注（synthetic/recorded/online）与实际输出一致；
LLM 列与 OTA/录制空态全部诚实 PENDING/空态，无伪造数字。修正了 2 处一致性/文档缺口（见末节）。

---

## 1. 「实验 ↔ 论文结论 ↔ CSV 文件」可回溯清单（数字均可复算）

打包 CSV = `paper_submission_package/data/`；论文源 = `paper_submission_package/sources/main.md`（v0.5）。
所有 N 均为 `manifest.n_samples`，seed 为 `manifest.seed`，口径为 `manifest.data_origin`。

| 图号 | 脚本 | 产物 CSV（data/） | seed | N | 口径 | 支撑的论文结论（main.md） | 复核状态 |
|---|---|---|---|---|---|---|---|
| Fig.1 | `exp_ebno_decode.py` | `ebno_decode_success.csv` | 20261001 | 2400 | synthetic | BPSK 在 Eb/N0≈6 dB 跨过 0.5、≥10 dB 达 1.0；ADS-B 需≈12–14 dB；AX.25 因 B/Rb 惩罚最大需≈26 dB（A 节/Tab.II） | ✅ CSV 逐字节一致；独立复算 BPSK@6dB k=30/50 → 0.600，Wilson[0.462,0.724] 与 CSV 一致 |
| Fig.2 | `exp_rate_sweep.py` | `rate_sweep_success.csv` | 20261001 | 1400 | synthetic | 固定 Eb/N0=6 dB、整包成功率对 fs 近似不变：0.595–0.705（7 档 fs，Wilson 带重叠）（B 节/Tab.III） | ✅ CSV 逐字节一致；实测 min/max=0.595/0.705，与结论句完全一致 |
| Fig.3 | `exp_rate_bandwidth.py` | `rate_bandwidth_success.csv` | 20261001 | 900 | synthetic | 解码成功率 vs 接收低通带宽，≥20 kHz 饱和到 0.78–0.88，定前端带宽下界（C 节） | ✅ CSV 逐字节一致 |
| Fig.4 | `exp_doppler_comp.py` | `doppler_comp_success.csv` | 20261001 | 3600 | synthetic | 固定 Eb/N0=8 dB：无补偿 fd=5Hz→0.065、fd≥15Hz→0；理想已知频偏补偿后全网格 0.94–0.98（D 节/Tab.IV） | ✅ CSV 逐字节一致（含 2026-10-02 重跑修正注记，Tab.IV 已对齐 canonical run） |
| Fig.5 | `exp_baseline_compare.py` | `baseline_compare.csv` | 20261001 | 600 | synthetic | 同数据集：经典规则弱（0.333@0dB→1.0@15dB），KNN-AMR 0.542@0dB→0.958@10dB→1.0（E 节/Tab.V） | ✅ CSV 逐字节一致；Tab.V 五格 classic/KNN 数值与重跑逐格相等；LLM 列=PENDING_ONLINE_RUN |
| Fig.6 | `exp_llm_baseline.py` | `llm_baseline.csv` | 20261001 | 300 | synthetic（LLM 列空态） | 经典/KNN/LLM 三族对比占位；LLM 槽 `Qwen/Qwen2.5-7B-Instruct` 列=PENDING_ONLINE_RUN（E 节） | ✅ CSV 逐字节一致；`llm_status=PENDING_ONLINE_RUN`，classic/KNN 本地真算，未联网/未伪造 |
| Fig.7 | `exp_amr.py` | `amr_confusion_matrix_snr10.csv`（+`amr_confusion_public.csv`） | 1234 (test_seed) | 640 | synthetic | SNR=10 dB 混淆矩阵对角准确率 0.9938 [0.984,0.998]；唯一残余混淆 FSK→QAM 4/80（E 节） | ✅ 混淆矩阵 CSV 逐字节一致；manifest diag_acc=0.9938、Wilson[0.984,0.9976] 与打包一致（需 `--trials 80`，非默认 30） |
| Fig.8 | `exp_doppler_orbit.py` | `doppler_convergence.csv` | 20261001 | 155 | synthetic | 单站多普勒定轨升高后收敛到≈1.1 km 地板，误差随 σ 增大（F 节） | ✅ CSV 逐字节一致；155 行=5 噪声档×31 历元 |
| Fig.9 | `exp_doppler_duration.py` | `doppler_duration_convergence.csv` | 20261001 | 6 | synthetic | 观测窗长扫描：60 s≈5.8 km，近全 pass 600 s≈4.0 km，单站观测性固有极限（F 节） | ✅ CSV 逐字节一致；6 行=6 窗长档 |
| Fig.10 | `exp_agent_toolcall.py` | `agent_toolcall_stage_summary.csv`（+detail/requests） | 20261001 | 400 | synthetic | 本地管线（注册/解析/校验/执行）对正确与错误请求成功率均=1.000；LLM 决策列=PENDING_ONLINE_RUN（G 节/Tab.VI） | ✅ detail 400 行=8 请求×50 次，n/success_rate/Wilson 逐格一致；**唯一非字节一致列=`mean_latency_ms`（墙钟）** |

**Tier B（诚实空态 / 非论文主图）**

| 脚本 | 口径 | 重跑真实结果 | 说明 |
|---|---|---|---|
| `exp_ota_handoff.py` | ota（空态） | `n_samples=0`，退出码 0，不产 recorded/ota 图 | 未提供录制→空态，不合成冒充 OTA |
| `exp_ota_run.py` | recorded（空态 N=0） | `n_samples=0`，`data_origin=recorded`，退出码 0 | 云内无 `.sigmf-data`→诚实空态 |
| `exp_weak_model_toolcall.py` | 本地确定性 | total_cases=10，call/pick/arg_rate=1.0（读 `~/.mbdsdr/config.json`） | 非在线 LLM，本地 tool-calling 成功率 |

**Tier C（早期纯软件合成脚本，README 已声明不在 common/ 口径内，仅历史基线）**

| 脚本 | seed | 重跑真实结果 | 状态 |
|---|---|---|---|
| `exp_ax25_performance.py` | 内置 | total_frames=180（3 sample_rate×6 SNR 点），写 `ax25_demod_performance.csv` | ✅ exit 0 |
| `exp_pnt_fusion.py` | 内置 | total=1500（5 场景×300），写 `pnt_fusion_montecarlo.csv` | ✅ exit 0 |
| `exp_freq_offset.py` | 20260919 | 500 trials×多档，写 cfo_ppm_scenario/vs_snr/vs_blocklength CSV | ✅ exit 0 |
| `exp_fhss_detection.py` | 20260919 | 300 trials，写 fhss_pd/pfa/param CSV | ✅ exit 0 |
| `exp_spectrum_sensing.py` | 20260919 | 4000 trials，写 sensing_pd/roc/noise_uncertainty CSV | ✅ exit 0 |
| `demo_spacetime.py` | 固定注入 | Doppler +150Hz 注入→NCO 移除，残余 +150→+0.0Hz ok=True，写 experiments/artifacts/spacetime_demo | ✅ exit 0 |
| `exp_digital_modes.py` | 20260919 | 280 s 内跑完 SSTV 段（clean/20/15/10 dB 行已出），未及 ADS-B 段 | ⚠️ 超时（见未解决项） |
| `exp_sstv_identification.py` | 20260919 | 产出 `sstv_mode_identification.csv`（21 行），real_wav 解码段超时（WavFileWarning: EOF 6431104/6431106） | ⚠️ 超时（见未解决项） |

---

## 2. 固定种子 / Wilson CI / 样本数 / 口径 一致性核对

- **固定种子**：common/ 管线全部经 `ExperimentRunner.make_rng(tag)`（BLAKE2b 派生子流），同一 (seed,tag) 跨机同结果。10 图重跑 CSV 与打包逐字节一致，**证明种子派生与加噪过程完全确定**。manifest.seed 与复现命令一致（20261001；amr 用独立 test_seed=1234 且 train_seed=20260919 隔离）。
- **Wilson 95% CI**：`common/runner.wilson_ci` 独立复算校验——取 ebno BPSK@Eb/N0=6dB（k=30/50），手写 Wilson=(0.461812, 0.723918)，CSV `wilson_lo/hi`=0.4618/0.7239，**吻合**。`z=1.96` 由 `scipy.stats.norm.ppf` 实算，非手填魔法数。
- **样本数 N**：manifest.n_samples 与 README/论文 Appendix/文件名 `Nxxxx` 三者一致（2400/1400/900/3600/600/300/640/155/6/400）；可由 (trials×格点数) 复算（如 ebno=48 格×50=2400；doppler_comp=9 fd×2 方向×200=3600；agent_detail=8 请求×50=400 行）。
- **口径标注**：`data_origin` 枚举（synthetic/recorded/ota/online）与实际一致；synthetic 绝未写成 ota；LLM 在线列在无 key 时整列 `PENDING_ONLINE_RUN`，`data_origin` 仍标 synthetic（信号为仿真），与 manifest 注释定义一致。

---

## 3. 三处诚实标注（非 bug / 非论文管线，如实说明）

1. **`agent_toolcall_stage_summary.csv` 的 `mean_latency_ms` 列跨次漂移**（重跑 0.021 vs 打包 0.016 ms；name_resolution 0.0 vs 0.001）。该列为**墙钟实测时延**（`np.mean(r["latency_ms"])`），本就非种子确定性；所有计数 n、success_rate、wilson_lo/hi 全部逐格一致。**非缺陷**——时延不可种子复现是物理实测属性。已在 README 产物清单该行注明。
2. **`exp_digital_modes.py` / `exp_sstv_identification.py` 重跑超时**：二者为早期纯软件合成脚本（README 已声明不在 common/ 口径、非论文 10 图）。digital_modes 的 SSTV 图像解码与 sstv 的 real_wav 解码在云 VM 单段耗时 >150–280 s；SSTV 识别 CSV(21 行)、digital SSTV 行均已正常产出，仅后续重计算段未在时限内跑完。**非复现性失败**，见未解决项。
3. **LLM 列无 key → 整列 `PENDING_ONLINE_RUN`**：`exp_llm_baseline.py`（online 列）、`exp_baseline_compare.py` 的 llm_agent 列、`exp_agent_toolcall.py` 的 LLM 决策列、`exp_weak_model_toolcall.py`（在线质量部分）均在云内无 `MBDSDR_LLM_API_KEY` 时**不联网、不伪造**，classic/KNN 本地列真算。这是 open-items 已登记的 `PENDING_ONLINE_RUN` 已知未决项。

---

## 4. 本块修正了什么（均在 experiments/ 内，未动实验逻辑数值）

1. **`experiments/exp_amr.py`：补 `--out` 输出目录覆盖**。原脚本无 `--out`（兄弟 common/ 脚本均有），CSV/图/manifest 只能写死 `paper/experiments/`，无法隔离复现。新增 `--out`（默认 None→保持原行为不变），本块核验即用它把 N640 产物重定向到暂存区且数值不变（混淆矩阵仍逐字节一致）。仅改入口参数解析，未改任何信号/分类/CI 逻辑。
2. **`experiments/README.md`：产物清单 + 复现命令补齐 3 张论文图**。原清单/命令漏列 Fig.2 `rate_sweep`(N1400)、Fig.4 `doppler_comp`(N3600)、Fig.10 `agent_toolcall`(N400)——这三图在论文与打包中存在，但 README（可复现口径文档）未登记，属覆盖性文档缺口。已在产物表加 3 行（含 N/口径/结论）、在复现命令块加 `#6b/#6c/#7b` 三条（参数取自重跑 manifest：rate_sweep `--trials 200` 固定 Eb/N0=6dB；doppler_comp `--trials 200` 固定 Eb/N0=8dB；agent_toolcall `--trials 50`）。

回归：`python3 -m pytest experiments/tests/ -q` → **34 passed**（exp_amr 改动未破坏任何测试）。

---

## 5. 未解决项 / 已知局限（如实登记）

- **LLM 在线列（PENDING_ONLINE_RUN）**：需在有 `MBDSDR_LLM_API_KEY` 的真机/本地环境跑 `exp_llm_baseline.py`（回答缓存到 `paper/experiments/.llm_cache_<model>.json`）与 agent LLM 决策列回填。云内不具备，本块不 mock。沿用 open-items 的 `PENDING_ONLINE_RUN`。
- **recorded/OTA 口径空态**：`exp_ota_run.py` 需真机 SigMF 录制（`paper/experiments/onboarding_adsb_<时间戳>/`）方能出 recorded 图/CSV；云内无硬件录制，当前 N=0 空态。
- **早期脚本超时**：`exp_digital_modes.py`（ADS-B 段）、`exp_sstv_identification.py`（real_wav 解码段）在云 VM 时限内未跑完；非论文管线、不影响 10 图可复现性，建议后续在更大超时/真机环境补跑以确认其历史 CSV。另 sstv real_wav 报 WavFileWarning（EOF 差 2 字节，header 与实际长度不符），属录制文件本身小瑕疵，不影响模式识别 CSV。
- **次要一致性观察**：多数 common/ 脚本的图落点 `FIG_DIR` 由模块常量 `OUT_DIR` 派生，`--out` 仅重定向 CSV/manifest，**图仍写 `paper/experiments/figures/`**（PNG 已 gitignore，非交付数字）。属隔离复现时的路径小不一致，未改（避免动 plot 公共签名引入风险）；CSV/manifest 这两个真数字产物已可完整隔离复现。
