# P1：真机结果回填 + OTA 实验操作说明

> 目标：真机拿到一次录制输出后，**贴回 JSON → 解析 → 一条命令出 recorded 口径图/CSV/manifest**，
> 直接进论文 Fig.1–10 体系。云内（无硬件）跑就是诚实空态 N=0，不合成、不补 OTA 格。
>
> 配套脚本：`experiments/exp_ota_run.py`（本批新写）。旧的 `experiments/exp_ota_handoff.py`
> 保留不动（仍只做空态演示），二者并存、互不破坏。

---

## 1. 真机三步（拿到输出即可一键出成果）

### 第①步：真机跑 selfcheck / onboard（都带 `--json`）

在**插好 RTL-SDR 的真机**上跑两条命令，把**完整输出**留着：

```bash
# a) 硬件自检（11 项：USB/udev、tuner、丢包、声卡、GNSS、依赖）
python3 tools/hw_selfcheck/selfcheck.py --json

# b) 一条命令跑通：detect → capture → record → decode → output
#    例：ADS-B 1090 MHz
python3 tools/onboarding/onboard.py --step all --freq 1090e6 --mode adsb --json
#    例：NOAA APT 云图
# python3 tools/onboarding/onboard.py --step all --freq 137.5e6 --mode apt --n 4.8e6 --json
```

`onboard --json` 的 `record` 步会在产物目录里写出**标准 SigMF**：

- `时间戳_频率Hz_模式.sigmf-data`（cf32_le 复数 IQ）
- `时间戳_频率Hz_模式.sigmf-meta`（含 `core:sample_rate` / `core:frequency` /
  `core:datetime` / `mbdsdr:mode` / `mbdsdr:gain_db` / `mbdsdr:time_source`）
- `时间戳_模式_messages.json`（decode 步已 CRC/FCS 校验的解码报文）
- `manifest.json`、频谱 PNG 等

产物目录默认在 `paper/experiments/onboarding_<mode>_<时间戳>/`，也可用 `--out-dir` 指定。

### 第②步：把输出贴回聊天 / 文件

把第①步两段 `--json` 的**完整 stdout**（从第一个 `{` 到最后一个 `}`）贴回聊天，
或存成一个文件（例如 `hw_report.txt`）。人话、命令回显、报错混在 JSON 之间都没关系——
解析器会自动抽出 JSON 对象。

### 第③步：跑回填脚本指向录制目录，出 recorded 口径图

```bash
# 云侧/本地解析贴回的 JSON（人读结论 + 结构化字段，确认设备与产物路径）
python3 tools/onboarding/parse_hw_report.py hw_report.txt

# 关键一步：把 onboard 产物（录制）目录交给回填脚本
python3 experiments/exp_ota_run.py --recordings-dir paper/experiments/onboarding_adsb_<时间戳>/
# 或单文件：
# python3 experiments/exp_ota_run.py --recording paper/experiments/onboarding_adsb_<时间戳>/xxx.sigmf-data
```

产出（落在 `--out`，默认 `paper/experiments/`）：

| 产物 | 内容 |
|---|---|
| `ota_recorded_metrics.csv` | 每段录制一行：fs/频率/时间/模式、SNR 估算、Eb/N0 估算、候选前导数、CRC 通过数、成功率+Wilson 区间、AMR 预测、多普勒观测数 |
| `figures/ota_recorded_success_vs_ebn0__recorded__N*__*.png` | 解码成功率 vs **估算** Eb/N0 散点（误差棒=Wilson 95% CI）；无可估点时诚实不出图 |
| `manifest_ota_run.json` | 口径/样本数/参数/UTC 时间/git sha/SNR 标定提示 |

---

## 2. parse_hw_report 的 JSON 与录制目录是什么关系

两者是**同一次 onboard 运行的两种投影**，不要混为一谈：

- **`onboard --json`（selfcheck/onboard JSON）**：是**机器可读的运行日志**。
  `parse_hw_report.py` 解析它，回答的是"设备好不好、哪步 PASS/FAIL、产物落在哪个目录、
  解码出了几帧"——它指向产物，但**本身不含 IQ 波形**。
- **录制目录（SigMF）**：是**真实 IQ 波形 + 元数据**。`exp_ota_run.py` 直接读这里的
  `.sigmf-data/.sigmf-meta`，从样本重算指标。

分工一句话：

> `parse_hw_report.py` 告诉你**录制目录在哪、设备是否正常**；
> `exp_ota_run.py` 才**真的去读那目录里的波形**算 recorded 指标。

典型闭环：`parse_hw_report` 输出里的 `out_dir`（或产物清单里的 SigMF 路径）→
把那个目录填给 `exp_ota_run.py --recordings-dir`。

---

## 3. 指标口径（全部数字来自录制，未标定项诚实标注）

| 指标 | 怎么算 | 诚实边界 |
|---|---|---|
| 解码成功率 vs Eb/N0 | ADS-B 贪心扫前导 → 候选数 n、CRC-24 通过数 k → k/n + Wilson 95% CI | SNR 用 PSD 峰/中位数粗估（**未标定**），再按 `(Eb/N0)=SNR+10log10(fs/Rb)`（B=录制 fs）折算。绝对值需真机已知信号源校准 |
| AMR 识别 | 对录制 IQ 跑开箱 KNN-AMR | **只报预测类+置信度，不报准确率**——真实录制无地面真值标签（`amr_accuracy=N/A_no_ground_truth_labels`） |
| 定轨收敛 | `extract_doppler_observations` 数有效多普勒观测 | 只报观测数；"收敛到 X km"需多过境+TLE 真值，单段录制不编造误差 |
| Eb/N0 可算模式 | 仅 `adsb`（Rb=1 Mbps） | CW/APT 等非比特流/无已知 Rb 模式不报 Eb/N0 |

口径恒为 `recorded`。CSV/manifest/图标题与文件名都标 `recorded`；
**任何路径下都不会写成 synthetic**（红线，有测试锁定）。

---

## 4. 空态行为（无录制时）

没传 `--recording/--recordings-dir`、目录里没有任何 `.sigmf-data`、或指定文件不存在时：

- 打印"未发现任何 .sigmf-data 录制 → recorded 口径空态"；
- 只写一份 `manifest_ota_run.json`（`n_samples=0`、`status=empty_state_no_recording`、
  `data_origin=recorded`）；
- **不写 CSV、不出图**；退出码 **0**（空态是合法结果，不是失败）。

云内无硬件跑就是这个行为——这是预期，不是 bug。

---

## 5. 红线与复现

- 数字只来自注入的录制文件；脚本内不合成信号、不用仿真格冒充 recorded。
- `paper/` 根目录被 .gitignore 忽略（图/CSV 是生成物，不入库）；本文档与脚本入库。
- 确定性测试：`python3 -m pytest experiments/tests/test_phase8_ota_backfill.py -v`
  （注入固定 SigMF 录制验证指标/图/CSV/manifest；空态 N=0；口径不得为 synthetic）。
