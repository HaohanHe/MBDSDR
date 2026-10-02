# CHANGES.md — MBDSDR 论文系统级自查与修订记录

- 日期：2026-10-02（Asia/Shanghai）
- 对象：`docs/learn/phase7/paper/main.md`（DRAFT v0.2 → v0.3）
- 数据口径核对基准：`paper/experiments/*.csv` 与 `paper/experiments/manifest_*.json`、`paper/experiments/figures/*.png`
- 红线遵守：未虚构任何文献/数据；未改动任何 CSV；只写本目录（`docs/learn/phase7/paper/`）内文件；未 commit / push。

---

## 一、自查结论摘要（按维度）

| 维度 | 结论 | 说明 |
|---|---|---|
| 结构完整性 | **通过** | Abstract→I Intro→II System Design→III Experiments(A–G)→IV Discussion→V Related Work→VI Conclusion→Appendix，顺序合理。篇幅对标 IEEE WCL 4–5 页量级（约 326 行 Markdown，二栏排版后落在 letter 篇幅内）。 |
| 图表正文引用 | **通过** | Fig.1–10、Tab.I–VI 全部在正文有引用；无孤儿图/表。附录实验-产物映射表与 figures 文件名（origin/N/date）逐一吻合。 |
| 数字与 CSV 一致性 | **基本通过 / 1 处待补** | Tab.II/Tab.III/Tab.V/Tab.VI、Fig.3/5/7/8/9 的抽查数字与 CSV 完全一致（含 Wilson CI 复核）。**唯一例外：Fig.4 / Tab.IV（多普勒补偿）**，见下文"待补项 #1"——其 CSV 已被一个非标准冒烟运行覆盖。 |
| Discussion 局限覆盖 | **通过** | 要求的 5 项（合成口径、单站观测性、理想多普勒上界、LLM PENDING、OTA 空态）均已在 IV 节明确覆盖，另含"理想载波/位定时同步"最佳情形说明。 |
| Related Work 真实性 | **通过（已去风险）** | 删除了无法确证的具体项目名 "GR-MCP"，改为通用表述并显式标注"未附正式参考文献、投稿前需核实引用"。保留的 GNU Radio / SDR++ / SatDump / O-RAN·6G xApp 均为真实存在的公开系统/方向。 |

---

## 二、逐项改动（增 / 删 / 改，含位置与理由）

### 改 1 — 版本号
- **位置**：front matter `status:` 行。
- **改动**：`DRAFT v0.2 (2026-10-02)` → `DRAFT v0.3 (2026-10-02, post system self-review; see CHANGES.md)`。
- **理由**：本轮系统自查后版本递进，便于回溯。

### 改 2 — 试验总数（Abstract）
- **位置**：Abstract 段。
- **改动**：`ten synthetic experiments (≈11 800 trials)` → `(≈10 400 trials; sum of per-experiment *N* in the Appendix)`。
- **理由**：附录 N 列求和 = 2400+1400+900+3600+600+300+640+155+6+400 = **10 401**≈10.4k，原文 11.8k 与附录自相矛盾。改为与附录一致的实数。

### 改 3 — Tab.III 补漏行 + Wilson CI 对齐 CSV
- **位置**：III.B 节 Tab.III（采样率扫描表）。
- **改动**：
  1. 增补缺失行 `| 20 | 2 | 2.99 | 0.605 | [0.536, 0.670] |`（CSV `rate_sweep_success.csv` 中 fs=20 kS/s、sps=2 这一档原被遗漏；N=1400=7 档×200 也佐证应有 7 行）。
  2. 全表 Wilson CI 按 CSV 三位小数重写（如 200 kHz 上限 0.6939→0.694、500 kHz 0.7639→0.764、1000 kHz 下限 0.5919→0.592），消除原文末位舍入偏差。
- **理由**：表格须与 CSV 逐档一致；正文"0.595–0.705"范围结论不变。

### 增 4 — Fig.4 多普勒：数据出处警示（不擅改数值）
- **位置**：III.D 节 Tab.IV 之后插入引用块。
- **改动**：新增 "Provenance note (self-review)"：说明规范图为 `doppler_comp_success__synthetic__N3600__20261002.png`（200 trials/cell），Tab.IV 数值来自该 N=3600 运行；但落盘的 `doppler_comp_success.csv`/`manifest_doppler_comp.json` 已被**非标准冒烟运行（seed=7、N=360/cell）**覆盖，数值与表不符；在重跑 `exp_doppler_comp.py` 恢复 N=3600 CSV/manifest 之前不得引用 Tab.IV。定性结论（≈10 Hz 残差使无补偿解码崩溃、已知 fd 恢复）两次运行一致。
- **理由（关键）**：经核对，落盘 CSV（seed=7，N=360/cell：fd=10 无补偿 1/20=0.05、有补偿 20/20=1.00）与论文 Tab.IV（N=3600：fd=10 无补偿 0.065、有补偿 0.940）属**两次不同运行**。该 CSV 不在 git 中（`paper/` 被 gitignore），N=3600 的原始 CSV 已无法找回。为遵守"不虚构、可回溯"红线：**既不把 seed=7 冒烟数据冒充为正式头条结果，也不臆造 N=3600 数值**，而是保留真实存在的 N=3600 图与表数值并显式标注待重跑。未将冒烟数据写入正文。

### 改 5 — Fig.7 类别集澄清
- **位置**：III.E 节。
- **改动**：`The 8-class confusion matrix ...` 前补一句，明确 Fig.7 是 `exp_amr.py` 的完整 8 类（AM/FM/CW/FSK/PSK/QAM/OFDM/NOISE，N=640）评估，与上文 Fig.5/Tab.V 的 3 类（FSK/PSK/NOISE，N=120/SNR）规则对比是两个不同实验。
- **理由**：manifest_baseline（3 类）与 exp_amr（8 类）类别集不同，原文易混；澄清后与 CSV 口径一致。

### 改 6 — Fig.9 观测噪声强度标注
- **位置**：III.F 节。
- **改动**：`Sweeping the observation window around peak elevation (Fig. 9)` → `... at σ=10 Hz (Fig. 9)`。
- **理由**：`doppler_duration_convergence.csv` 全表 `sigma_fd_hz=10.0`，而 Fig.8 头条用 σ=1 Hz（RLS 误差 ≈1.1 km）；补上 σ=10 Hz 使两图口径不混淆。

### 改 7 — Related Work 去虚构风险
- **位置**：V. Related Work 节。
- **改动**：删去具体项目断言 "**GR-MCP** exposes GNU Radio blocks to an LLM but does not measure the radio itself"，改为 "Several recent community prototypes expose GNU Radio blocks to LLMs via tool-calling / MCP interfaces ... specific projects in this space must be verified and cited before submission — **no formal bibliography is attached in this draft**, and no named prior system is asserted here that we cannot confirm." 其余（GNU Radio/SDR++/SatDump、O-RAN/6G xApp）保留。
- **理由**："GR-MCP" 作为一个可被引用的具体先前系统无法在当前环境确证；按"宁缺毋滥、禁虚构文献"红线，弱化为通用趋势表述并标注待核实。

### 改 8 — 试验总数（Conclusion）
- **位置**：VI. Conclusion 节。
- **改动**：`Ten fixed-seed experiments (≈11.8k trials)` → `(≈10.4k trials)`。
- **理由**：同改 2，与附录 N 求和一致。

---

## 三、本轮**未改动**但已核实无误的数字（抽查留痕）

- Tab.II（Fig.1）：BPSK 6 dB=0.60[0.462,0.724]、8 dB=0.92[0.812,0.969]；ADS-B 10 dB=0.48、12 dB=0.86；AX.25 24 dB=0.70、26 dB=0.98 —— 与 `ebno_decode_success.csv` 完全一致。Eb/N0 标定 Δ：AX.25 +15.6 dB、ADS-B +6.0 dB、BPSK +10.0 dB，与 `B=fs`、10·log10(B/Rb) 公式复核一致。
- Fig.3（`rate_bandwidth_success.csv`）：8 kHz=0.71、12 kHz=0.77、≥20 kHz 饱和 0.78–0.88 —— 一致。
- Tab.V（Fig.5，`baseline_compare.csv`）：规则/KNN 在 0/5/10/15/20 dB 全部一致；LLM 列 `PENDING_ONLINE_RUN` 与 CSV 一致。
- Fig.7 混淆矩阵：SNR=10 dB 总体精度 0.9938，Wilson 复核≈[0.984,0.998]；FSK→QAM 4/80 —— 一致。
- Fig.8/9（`doppler_floor.csv`、`doppler_duration_convergence.csv`）：σ=1 Hz RLS 误差≈1.1 km；60 s≈5.8 km、600 s≈3.96 km —— 一致。
- Tab.VI（Fig.10，`agent_toolcall_stage_summary.csv`）：各阶段 n 与 1.000 成功率、注册 5 工具≈0.016 ms、Wilson 下界 0.93–0.98 —— 一致；LLM 决策行 `PENDING_ONLINE_RUN` —— 一致。

---

## 四、仍待补项清单（投稿前必须处理）

1. **【高优先】Fig.4 / Tab.IV 多普勒 CSV 重跑**：落盘 `doppler_comp_success.csv` 现为 seed=7、N=360/cell 的冒烟运行，需在主种子（master seed 20261001）下重跑 `exp_doppler_comp.py`，恢复 N=3600 的 CSV+manifest，并据此回填/核对 Tab.IV 与 Abstract 中 "≈10 Hz→6.5% / 有补偿≈0.95" 的数值。当前正文已加醒目警示，未将冒烟数据写入。
2. **LLM 在线运行**：Fig.6（AMR 的 LLM 列）与 Fig.10/Tab.VI 末行（call/pick/arg 决策质量）仍为 `PENDING_ONLINE_RUN`，需有 API key 环境运行后回填。
3. **OTA / 真实录制解码**：当前全部 synthetic；用 C++ recorder 录 SigMF 后 `--recordings-dir` 回放，manifest 改标 `recorded`。
4. **参考文献与作者信息**：本稿未附正式 bibliography；GNU Radio/SDR++/SatDump/O-RAN 及 LLM-SDR/MCP 类先前工作的具体引用条目需核实并按 IEEE 格式补齐；作者名单待补（现为 Bi4MIB open-source call）。
5. **LaTeX/IEEEtran 排版**：当前为 Markdown 初稿，投稿前迁移 `IEEEtran` 模板、图表改用 `\includegraphics{}`。
6. **多站 / GNSS 融合定轨**：当前为单站多普勒-only（Fig.8/9 的观测性局限），属未来工作。
