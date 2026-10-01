# MBDSDR 论文实验管线（B2 可复现管线）

> 脚本与公共库都在本目录（`experiments/`）。
> **数据落点 `paper/experiments/` 被 .gitignore 整体忽略（生成物不入库的既定策略），
> 由下列脚本一键生成，不手改数字。** 本文档随仓库分发，说明口径与复现方法。
> 所有随机性固定种子；每张图/CSV/manifest 都标 **口径**（synthetic / recorded / ota）。

## 口径红线

| 口径 | 含义 | 本批是否产出 |
|---|---|---|
| `synthetic`（仿真） | 脚本内合成信号 + 固定种子加噪 | ✅ 全部新图均为此口径 |
| `recorded` | 真实 RTL-SDR 录制（SigMF）回放 | ⬜ 云内无录制，空态 |
| `ota` | 实时空中信号 | ⬜ 云内无硬件，空态 |

**绝不用合成数据冒充 OTA/录制。** 云内无录制时，OTA/录制段明确输出空态（见 `exp_ota_handoff.py`）。

---

## 公共库 `experiments/common/`

| 模块 | 职责 |
|---|---|
| `ebno.py` | SNR↔Eb/N0 换算（`(Eb/N0)_dB = SNR_dB + 10log10(B/Rb)`）+ 各模式 (Rb,B) 常量表 |
| `datasource.py` | 统一数据源：`SyntheticChannel` / `SigMFReplay`(包 `playback.IQPlayback`) / `WavReplay`；无录制诚实空态 |
| `runner.py` | 固定种子派生子流、逐格多次试验、Wilson/正态二项置信区间、记录样本数 |
| `manifest.py` | 每次 run 写 JSON manifest（脚本/种子/口径/参数/样本数/UTC 时间/git sha） |
| `plot.py` | 统一无头出图（英文防乱码）；图题与文件名含口径+样本数+日期，误差棒=Wilson CI |

---

## 产物清单（本批 B2 新增；落点在忽略目录 `paper/experiments/`）

| 产物（paper/experiments/ 下） | 脚本（experiments/ 下） | 口径 | 样本数 N | 说明 |
|---|---|---|---|---|
| `ebno_decode_success.csv` + `figures/ebno_decode_success__synthetic__N2400__*.png` | `exp_ebno_decode.py` | synthetic | 2400 | AX.25/ADS-B/BPSK 解码成功率 vs Eb/N0，Wilson 95% CI 误差棒 |
| `doppler_convergence.csv` / `doppler_floor.csv` + `figures/doppler_convergence__synthetic__N155__*.png` | `exp_doppler_orbit.py` | synthetic | 155 | 固定 TLE(ISS) 多普勒定轨，EKF/RLS 位置误差收敛曲线 |
| `baseline_compare.csv` + `figures/baseline_compare_amr__synthetic__N600__*.png` | `exp_baseline_compare.py` | synthetic | 600 | 经典规则 vs KNN-AMR 同数据集识别准确率；LLM 列=待在线 |
| `manifest_ota_handoff.json` | `exp_ota_handoff.py` | ota(空态) | 0 | 录制摄取空态演示 |
| `manifest_*.json`（每 run 一份） | 各脚本 | 随 run | — | 脚本/种子/口径/参数/样本数/UTC 时间/git sha |

### Eb/N0 换算口径
(Eb/N0)_dB = SNR_inband_dB + 10·log10(B/Rb)，B=噪声/判决带宽（本管线取 fs，保守含带外噪声）：
- AX.25/AFSK 1200：Rb=1200，B=44100（音频 fs）→ Δ=+15.6 dB
- ADS-B Mode-S 1M：Rb=1e6，B=4e6 → Δ=+6.0 dB
- BPSK 10k：Rb=1e4，B=1e5 → Δ=+10.0 dB

故三模式拐点在 Eb/N0 轴上右移量不同（AX.25 最右），这是**真实功率-比特率换算**的结果，非曲线位置错误。

---

## 复现命令（仓库根目录，全部确定性）

```bash
# 1. Eb/N0 解码成功率（仿真）
python3 experiments/exp_ebno_decode.py --trials 50 --seed 20261001

# 2. 多普勒定轨收敛（仿真，固定 ISS TLE）
python3 experiments/exp_doppler_orbit.py --seed 20261001 --n-obs 31

# 3. 基线对比（经典 vs KNN；LLM 列占位）
python3 experiments/exp_baseline_compare.py --trials-per-class 40 --seed 20261001

# 4. OTA/录制空态（云内跑应输出空态，退出码 0）
python3 experiments/exp_ota_handoff.py

# 5. 离线确定性测试
python3 -m pytest experiments/tests/ -q
```

> 产物全部落到被忽略的 `paper/experiments/`；重新跑上述命令即可复现全部 CSV/PNG/manifest。

---

## 真机用户如何接入 RTL-SDR 录制

1. **录制**：用 C++ recorder（`cpp/src/dsp/recorder.cpp`）录出标准 SigMF：
   `xxx.sigmf-data`（cf32_le）+ `xxx.sigmf-meta`（含 `core:sample_rate`/`core:frequency`/`core:datetime`）。
2. **回放**：把录制目录交给管线：
   ```bash
   python3 experiments/exp_ota_handoff.py --recordings-dir ~/mbdsdr/recordings/
   # 或单文件：
   python3 experiments/exp_ota_handoff.py --recording ~/mbdsdr/recordings/pass1.sigmf-data
   ```
3. 管线经 `mbdsdr_ai.playback.IQPlayback` 读取（mmap、从 meta 读 fs/freq/datetime，**不硬编码**），
   再用 `orbit_determination.extract_doppler_observations` 跑真实多普勒前端；观测不足时诚实返回空、不补点。
4. 该 run 的 manifest 会写 `data_origin="recorded"`。

## LLM/Agent 路径（待在线运行）

`exp_baseline_compare.py` 的 `llm_agent` 列恒为 `PENDING_ONLINE_RUN`：该路径需在线 LLM API，
云 VM 无 key、不可确定性复现（见 B2 审计 §5）。真机/有 key 环境运行
`experiments/exp_weak_model_toolcall.py` 后再把数值回填，**不伪造**。

## 已知诚实局限

- 多普勒定轨为单站多普勒-only：早期仰角低时几何弱、误差大，卫星升高后收敛到 ~1–3 km 地板；
  顺序 EKF 对初值/调参敏感，批处理 RLS 更稳。这是单站多普勒观测性的固有特性，非 bug。
- 基线对比的经典分类器是刻意简单的规则基线，低 SNR 下明显弱于 KNN。
