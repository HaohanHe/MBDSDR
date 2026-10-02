# P2 — IEEE WCL 投稿前 checklist 逐项核对（pre-submission reviewer pass）

- 日期：2026-10-02（Asia/Shanghai, UTC+8）
- 对象：`docs/learn/phase7/paper/main.md`（**DRAFT v0.4**，唯一事实源）+ `main.tex`（IEEEtran 镜像骨架）
- 核对基准：`paper/experiments/*.csv`、`manifest_*.json`、`paper/experiments/figures/*.png`（该目录 gitignored）
- 红线：未虚构文献/数据；未改任何 CSV；未 commit/push；本轮仅新增本文件与 `tools/paper_package.sh`。
- 证据格式：`文件:行`（相对仓库根）。

> 说明：本文件对应 `_PHASE10_SPEC.md` P2 批次①的交付。仓库原无
> `P2-submission-check.md`，本轮按规格新建；WCL checklist 维度取自
> `_PHASE10_SPEC.md:12`（标题/摘要指标/图表自洽/基线对比/related work/篇幅）。

---

## 0. 结论速览

| # | WCL checklist 项 | 结论 | 关键阻塞 |
|---|---|---|---|
| 1 | 标题传达贡献 | ✅ 通过 | — |
| 2 | 摘要含指标 | ✅ 通过（且诚实标注 PENDING） | — |
| 3 | 图表编号/引用自洽 | ✅ Markdown 层通过；⚠️ .tex 层 8 个 `\ref` 待迁移 | 图 2–10 仍是注释迁移点 |
| 4 | 基线对比 | ✅ 通过（三类 detector 同样本） | LLM 臂 `PENDING_ONLINE_RUN` |
| 5 | Related Work / 文献 | ⚠️ 叙述充分但**无正式 bibliography** | `refs.bib` 未建，全部待核实 |
| 6 | 篇幅 4–5 页两栏 | 🟡 量级对标，**未实测** | .tex 为骨架，未插图/未排两栏 |
| — | LaTeX 可编译性 | ❌ 云内默认不可编译 | 缺 `IEEEtran.cls`；另 2 处下划线硬错 |
| — | 占位符/声明 | ⚠️ 作者/收稿日期/版权位均 PENDING | 见 §4 |

---

## 1. 逐项核对（结论 + file:line 证据）

### ① 标题传达核心贡献 —— ✅ 通过

- 标题（front matter）`docs/learn/phase7/paper/main.md:2` 与正文 H1 同文 `:8`：
  > "MBDSDR: An AI-Defined Software-Defined Radio Stack with a **Function-Calling, Reproducible Measurement Backbone**"
- 直接点名两个新颖点：函数调用工具层 + 可复现测量骨干。
- 该标题为 v0.4 刻意收紧：原泛化措辞 "…Deterministic, Reproducible Experimentation" 已替换，见
  `docs/learn/phase7/paper/CHANGES.md:101`（"改 T1 — 标题"）。
- 对应贡献条目落地在 `main.md:68-73`（Contributions i–iv）。

### ② 摘要含关键指标 —— ✅ 通过（指标具体 + LLM 诚实空态）

摘要 `main.md:21-44` 含全部头条数字：
- 试验规模 "≈10 400 trials"（`:31`；与附录 N 求和 10 401 一致，见 CHANGES `:128`）。
- 采样率不变区间 "0.595–0.705 across 10 kS/s–1 MS/s"（`:36-37`）。
- 多普勒 "residual …≈10 Hz collapses uncompensated decoding to 6.5% while known-frequency restoration holds ≈0.94"（`:37-39`）。
- AMR "KNN-AMR reaches 0.96–1.00 … versus 0.67–1.00 for classical rule thresholds"（`:39-41`）。
- 工具管线 "400 mixed valid and malformed calls at a 1.000 graceful-handling rate"（`:41-43`）。
- LLM 臂 "a clearly-marked pending run, not a fabricated number"（`:40-41`、`:43-44`），未编造。
- 指标补入动作见 CHANGES `:102`（"改 S1 — Abstract 指标句"）。

### ③ 图表编号 / 正文引用自洽 —— ✅ Markdown 层通过；⚠️ .tex 层待迁移

**Fig.1–10 全部有正文引用，无孤儿图**（grep `main.md`）：
- Fig.1 `:125,:135`；Fig.2 `:146,:156`；Fig.3 `:168`；Fig.4 `:176,:186,:286`；
  Fig.5 `:206,:221,:222,:224,:228`；Fig.6 `:206,:213,:222,:225`；Fig.7 `:206,:218`；
  Fig.8 `:238,:283`；Fig.9 `:238,:244,:283`；Fig.10 `:248,:261`。
- 附录实验-产物映射表 `:344-355` 逐图列出 script/origin/N/date。

**Tab.I–VI 全部有正文交叉引用，无孤儿表**：
- Tab.I `:79`（表体 `:107`）；Tab.II `:133`（表体 `:135`，原孤儿表已补引用，见 CHANGES R1 `:111`）；
  Tab.III `:152`（表体 `:156`）；Tab.IV `:199,:202`（表体 `:186`）；
  Tab.V `:213`（表体 `:228`）；Tab.VI `:259`（表体 `:261`）。

**附录映射 ↔ 磁盘文件名一致**（10/10 规范图均在 `paper/experiments/figures/`）：
| Fig | 磁盘文件（origin/N/date） | 附录行 |
|---|---|---|
| 1 | `ebno_decode_success__synthetic__N2400__20261001.png` | `:346` |
| 2 | `rate_sweep_success__synthetic__N1400__20261002.png` | `:347` |
| 3 | `rate_bandwidth_success__synthetic__N900__20261001.png` | `:348` |
| 4 | `doppler_comp_success__synthetic__N3600__20261002.png` | `:349` |
| 5 | `baseline_compare_amr__synthetic__N600__20261001.png` | `:350` |
| 6 | `llm_baseline_amr__synthetic__N300__20261001.png` | `:351` |
| 7 | `amr_confusion_matrix__synthetic__N640__20261001.png` | `:352` |
| 8 | `doppler_convergence__synthetic__N155__20261001.png` | `:353` |
| 9 | `doppler_duration_convergence__synthetic__N6__20261001.png` | `:354` |
| 10 | `agent_toolcall_stages__synthetic__N400__20261002.png` | `:355` |

**数字 ↔ CSV 抽查一致**：
- Tab.IV ↔ `paper/experiments/doppler_comp_success.csv`：fd=10 Hz 无补偿 13/200=**0.065**、补偿 188/200=**0.940**；N=3600=9 fd×2 arm×200；`manifest_doppler_comp.json` `seed=20261001, n_samples=3600`（与 `main.md:196-204` resolved provenance note 一致；v0.3 冒烟覆盖问题已恢复）。
- Tab.VI ↔ `agent_toolcall_stage_summary.csv`：7 阶段全 1.000、注册 5 工具 0.016 ms（`main.md:263-272`）。
- 附录 N 求和 = 2400+1400+900+3600+600+300+640+155+6+400 = **10 401 ≈ 10.4k**（`main.md:31`、`:332`；CHANGES `:128`）。

> ⚠️ **.tex 层注意**：`main.tex` 中只有 Fig.1 有真实 `\begin{figure}`+`\label{fig:ebno}`（`main.tex:97-102`）；
> Fig.2–10 仍是注释式迁移点（`main.tex:106,110,113,119-121,127-128,131`），因此 `\ref{fig:rate/bw/doppler/base/cm/orb/dur/tool}`
> 暂无对应 `\label`（见 §2 编译日志）。这是骨架预期状态，**不是 main.md 的自相矛盾**；全量迁移后即解析。

### ④ 基线对比（baseline）—— ✅ 通过（三类 detector 同样本）

- `main.md:208-215` 明确 "three detector families on the same fixed-seed samples of FSK/PSK/NOISE:
  (i) classical-DSP rule classifier, (ii) real KNN-AMR (25 features, k=5), (iii) an LLM agent"。
- 对比表 Tab.V `main.md:230-236`：Classic rules vs KNN-AMR vs LLM column（LLM 列 `PENDING_ONLINE_RUN`）。
- 低信噪比量化对比 `main.md:212`：规则 0.33@0dB / 0.67@10dB vs KNN 0.54 / 0.96。
- reviewer note `main.md:221-226`：Fig.5（120/SNR）与 Fig.6（60/SNR，预留 LLM 曲线）待 LLM 回填后合并为一张三类对比图。
- 显式化动作见 CHANGES `:104`（"Baseline 三类对比"）。

### ⑤ Related Work / 文献 —— ⚠️ 叙述充分，但**无正式 bibliography**（投稿阻塞项）

- 叙述覆盖 `main.md:296-309`：GNU Radio/SDR++/SatDump（手工专家配置）；LLM-tool/MCP 原型（交互式控制而非测量骨干）；O-RAN/6G xApp（无线资源调度，非开放可复现 SDR 实验）。
- 已去虚构风险：删除无法确证的具体项目名 "GR-MCP"，改通用表述（CHANGES `:56-59`、`:105`）。
- **待补清单** `main.md:311-324`：仅列引用*类别*与公认锚点（GNU Radio/SDR++/SatDump、OpenAI function-calling、Wilson 1927、SGP4/Hoots&Roe、SigMF、O-RAN），全部标 `[待核实]`。
- **`refs.bib` 不存在**（已核实 `docs/learn/phase7/paper/refs.bib` No such file）；`main.tex:162-163` 的 `\bibliography` 仍注释。
- 结论：可投稿前必须逐条检索确认、按 IEEE 格式入 bib；**当前不得提交任何参考文献**（红线：禁虚构）。

### ⑥ 篇幅（4–5 页两栏）—— 🟡 量级对标，未实测

- `main.md` 正文约 **2 907 词 / 355 行**；目标 WCL letter 4–5 页两栏（README `:10`、`:97`）。
- 因 `main.tex` 仍是骨架（图未 `\includegraphics`、表未 booktabs 化、摘要含 `[SYNC]` 注释），
  **当前无法给出真实两栏页数**。经验上 ~2.9k 词 + 10 图 + 6 表落在 4–5 页区间，但必须在全量迁移、插图、建 bib 后实测。
- 标注：**待投稿环境全量排版后实测页数**。

---

## 2. LaTeX 可编译性（云内实测）

**环境事实**：云内**有** `pdflatex`（pdfTeX 3.141592653-2.6-1.40.22, TeX Live 2022/dev）、`xelatex`、`bibtex`、`pandoc 3.1.8`；
`graphicx/booktabs/url` 均可用。**但 `IEEEtran.cls` 未安装**（`kpsewhich IEEEtran.cls` 空；磁盘仅有 `.bst` 与 tex4ht 辅助）。

**第一次编译（云内真实状态）—— 失败，无 PDF**：
```
! LaTeX Error: File `IEEEtran.cls' not found.
l.16 ^^M
!  ==> Fatal error occurred, no output PDF file produced!
```
失败点：`main.tex:15` 的 `\documentclass[journal]{IEEEtran}`。

**验证性第二次编译**（仅在临时目录补入从 CTAN 拉取的 `IEEEtran.cls` V1.8b，不入交付）—— 产出 1 页 PDF，但：
1. **2 处硬错误 `! Missing $ inserted.`**：`main.tex:55` 摘要 `[SYNC: …LLM-agent arm = PENDING_ONLINE_RUN.]`
   中下划线 `_` 未转义（LaTeX 文本模式下 `_` 是数学模式字符）。**投稿前必须**把 `PENDING_ONLINE_RUN` 写成
   `PENDING\_ONLINE\_RUN` 或包进 `\texttt{PENDING\_ONLINE\_RUN}`（该 `[SYNC]` 注释块本就是待替换的编辑批注）。
2. **8 个未解析前向引用** `fig:rate / fig:bw / fig:doppler / fig:base / fig:cm / fig:orb / fig:dur / fig:tool`
   （`main.tex:105,109,112,118,126,130`）——因图 2–10 尚无 `\begin{figure}\label{}`，属骨架预期；Fig.1 的
   `fig:ebno`（`main.tex:101`）在第二遍后已解析。
3. 参考文献注释关闭（`main.tex:162-163`），未跑 bibtex；全部 `\includegraphics` 注释（`main.tex:99`）。

> **结论：当前 main.tex 在云内默认环境不可干净编译。** 已标注
> **「待投稿环境编译验证」**：投稿机须具备 `IEEEtran.cls`（TeX Live `ieeetran` 包），
> 并先修 `main.tex:55` 下划线、完成图 2–10 迁移后跑
> `pdflatex main && bibtex main && pdflatex main && pdflatex main`（README `:41`）。

---

## 3. 占位符与声明核对

| 占位项 | 位置 | 现状 |
|---|---|---|
| 作者占位 | `main.tex:30-34`；`main.md:5` | `\author{Firstname~Lastname … and~Bi4MIB~open-source~contributors}`；正文作者行 "Bi4MIB (open-source, call for co-authors)" —— **真实作者/单位/联系方式待补** |
| `\thanks` 收稿日期 | `main.tex:33` | "Manuscript received …; revised … **(PENDING)**." |
| `\thanks` 作者单位邮箱 | `main.tex:34` | "The authors are with … (e-mail: …)." —— 占位 |
| IEEE 版权声明位 | `main.tex:30-34`（`\thanks`，首栏脚注位） | 位置约定正确（IEEEtran journal 类把版权脚注放在 `\thanks`、首栏底部）；**正式版权行尚未写入** —— 这是投稿后由 IEEE 生产期添加，预投稿阶段留空属正常 |
| 图引用占位 | `main.tex:96,106,110,113,119-121,127-128,131` | `% MIGRATE: Fig.X → …png` 注释迁移点；`\includegraphics` 全部注释（`:99`） |
| 参考文献占位 | `main.tex:159-163`；`main.md:311-324` | `refs.bib` 未建；`\bibliography{refs}` 注释；待核实清单 6 类 |
| 文内 PENDING 标记 | `main.md:15,40,214,232-236,259,272` 等 | `PENDING_ONLINE_RUN`（LLM 列 / Tab.VI 末行）—— 诚实空态，未编造 |

---

## 4. 投稿包（打包脚本 + 清单）

- 脚本：`tools/paper_package.sh`（本轮新建，已 `chmod +x` 并实测）。
- 用法：`bash tools/paper_package.sh [OUTDIR]`（默认 `./paper_submission_package`）。
- 收集内容（实测 **36 文件 / 0 缺失**）：
  - `sources/`：`main.md`、`main.tex`、`CHANGES.md`、`README.md`（受控稿）。
  - `figures/`：上述 **10 张规范图**（按 main.md 附录白名单显式收集，非 glob）。
  - `data/`：10 个实验对应 CSV + 10 个 `manifest_*.json` + Fig.10 的 `agent_toolcall_detail.csv`/`requests.csv`。
  - `MANIFEST.txt`：逐文件字节数 + sha256。
- **刻意排除**：① 冒烟/调试小 N 图（`…__N80__`、`N160__`、`N2__`、`N18__`、`N40__`，落盘于 2026-10-02 15:14 的临时运行）；
  ② 未被正文引用的 `ota_recorded_success_vs_ebn0__recorded__N5__20261002.png`（论文明确声明无 OTA/录制结果，见 `main.md:13,:279-281`）——
  该 recorded 图若误入会与论文诚实声明矛盾，脚本按白名单已规避。
- 路径说明：根 `paper/` 被 `.gitignore` 整体忽略，图/CSV 不随版本库走；脚本只**读取**这些产物并复制到 OUTDIR。
  OUTDIR 默认落在仓库内但为生成物，**不 commit**；正式投稿由本地执行后打包上传。

---

## 5. PENDING 项状态（诚实，未虚构进展）

| PENDING | 当前状态 | 解锁条件 | 本环境能否推进 |
|---|---|---|---|
| LLM 在线（Fig.6 AMR 列 / Fig.10·Tab.VI 末行） | `PENDING_ONLINE_RUN`（`main.md:15,214,232-236,259,272`） | 配 `MBDSDR_LLM_API_KEY`/`BASE_URL` 跑 `exp_llm_baseline.py`、`exp_weak_model_toolcall.py` | ❌ 无 API key，保持空态 |
| OTA / 真实录制解码 | 空态（`main.md:13,279-281`） | C++ recorder 录 SigMF 后 `--recordings-dir` 回放，manifest 标 `recorded` | ❌ 云内无硬件 |
| 参考文献 `refs.bib` | 未建（清单见 `main.md:311-324`） | 逐条检索确认 + IEEE 格式；**禁虚构** | ❌ 须人工核实文献 |
| 作者名单 / 单位 / 联系方式 | open-source call（`main.md:5`；`main.tex:30-34`） | 补真实作者、单位、通讯邮箱、致谢 | ❌ 需人工提供 |
| Fig.5+Fig.6 合并 | 待 LLM 回填后（`main.md:221-226`） | LLM 曲线到位后合并三类对比图 | ❌ 依赖 LSM 在线 |
| main.md → main.tex 全量迁移 | 骨架（`main.tex` 注释迁移点） | 填正文、插图、建 bib、跑 pdflatex+bibtex | 🟡 结构已验，须投稿机完成 |
| 实测 fd 估计 / 多站融合定轨 | 未来工作（`main.md:286-287`） | 替换 Fig.4 理想已知 fd 上界 | ❌ |

---

## 6. 本轮未完成项 / 投稿前必须人工处理

1. 在带 `IEEEtran.cls` 的 TeX 环境全量编译，并修 `main.tex:55` 下划线（`PENDING_ONLINE_RUN` → `\_` 或 `\texttt{}`）。
2. 把 main.md 正文/表格迁入 `main.tex` 注释迁移点，图 2–10 换 `\includegraphics{}`，消除 8 个未解析 `\ref`。
3. 逐条核实 `main.md:311-324` 的 6 类引用，建立 `refs.bib`（无虚构）。
4. 补真实作者/单位/通讯地址；删除 `\thanks` 的 PENDING 占位。
5. 有 API key 环境回填 LLM 两臂并合并 Fig.5+Fig.6；有硬件环境录一组 SigMF 并标 `recorded`（否则在投稿信中说明仅合成验证）。
6. 全量排版后实测两栏页数落在 4–5 页。
