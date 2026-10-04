# CITATION-MAP — MBDSDR (docs/learn/phase7/paper/)

> Phase 33 块1 交付：`refs.bib` → `main.tex` `\cite` 接线对照。
> 生成日期：2026-10-05。防虚构铁律：**未新增/未改动 refs.bib 任何条目字段**，仅做 `\cite` 接线。
> 自查方式：本机 `pdflatex`/`bibtex` 存在，但 `IEEEtran.cls` 未安装（`kpsewhich IEEEtran.cls` 退出 1），
> 完整 `pdflatex → bibtex` 链路在此环境跑不起来；故采用**等效文本一致性检查**（grep 提取 main.tex 全部 `\cite` key
> 与 refs.bib 全部 `@...{key,}` 做 `comm` diff）。结果：**0 孤儿 cite / 0 缺失条目**。

## 0. 汇总

| 指标 | 值 |
|---|---|
| `\cite{...}` 出现总次数 | **19** |
| 被引用的唯一 bib key 数 | **12** |
| refs.bib 条目总数 | **12** |
| 孤儿 cite（tex 引用、bib 无定义） | **0** |
| 缺失条目（bib 有定义、tex 从未引用） | **0** |

bib key 清单（12，按 refs.bib 出现顺序）：
`wilson1927`, `hoots1980spacetrack`, `vallado2006revisiting`, `sigmf_spec`,
`gnuradio_project`, `sdrpp_project`, `satdump_project`,
`openai_function_calling`, `mcp_spec`, `radiomaster2026`, `mxai2025`,
`polese2023understanding`。

---

## 1. 逐点映射（正文位置 file:line ↔ bib key ↔ 条目全名）

> file 均为 `docs/learn/phase7/paper/main.tex`（相对仓库根）。行号为本次接线后 `grep -n '\\cite{'` 的实测行号。

| # | main.tex 行号 | 所在节 | bib key | 条目全名（refs.bib） | refs.bib STATUS |
|---|---|---|---|---|---|
| 1 | `main.tex:84` | Introduction（引出段） | `gnuradio_project` | The GNU Radio Project, *GNU Radio: Free & Open Software Radio Ecosystem* | 部分核验 |
| 1 | `main.tex:84` | Introduction（引出段） | `sdrpp_project` | Rouma, A. & SDR++ Contributors, *SDR++: A Modular, Cross-Platform SDR Receiver* | 部分核验 |
| 1 | `main.tex:84` | Introduction（引出段） | `satdump_project` | SatDump Contributors, *SatDump: A Generic Satellite Data Processing Software* | 部分核验 |
| 2 | `main.tex:91` | Introduction · itemize (i) | `openai_function_calling` | OpenAI, *Function Calling and Other API Updates* (2023-06-13) | 已核验 |
| 3 | `main.tex:93` | Introduction · itemize (ii) | `wilson1927` | Wilson, E. B., *Probable Inference, the Law of Succession, and Statistical Inference*, JASA 22(158):209–212 (1927) | 已核验 |
| 4 | `main.tex:103` | System Design（引出段） | `sigmf_spec` | SigMF Contributors / GNU Radio Foundation, *SigMF: Signal Metadata Format Specification* | 已核验 |
| 5 | `main.tex:106` | System Design（引出段） | `hoots1980spacetrack` | Hoots, F. R. & Roehrich, R. L., *Models for Propagation of NORAD Element Sets*, Spacetrack Report No.3 (1980) | 已核验 |
| 5 | `main.tex:106` | System Design（引出段） | `vallado2006revisiting` | Vallado, D. A. et al., *Revisiting Spacetrack Report #3: Rev 2*, AIAA 2006-6753 (2006) | 已核验 |
| 6 | `main.tex:127` | Experiments（引出段） | `wilson1927` | （同 #3；Wilson 95% CI） | 已核验 |
| 7 | `main.tex:163` | Experiments §F Single-station Doppler orbit | `hoots1980spacetrack` | （同 #5；SGP4 传播 ISS TLE） | 已核验 |
| 7 | `main.tex:163` | Experiments §F Single-station Doppler orbit | `vallado2006revisiting` | （同 #5） | 已核验 |
| 8 | `main.tex:183` | Related Work · *SDR frameworks* | `gnuradio_project` | （同 #1） | 部分核验 |
| 9 | `main.tex:184` | Related Work · *SDR frameworks* | `sdrpp_project` | （同 #1） | 部分核验 |
| 9 | `main.tex:184` | Related Work · *SDR frameworks* | `satdump_project` | （同 #1） | 部分核验 |
| 10 | `main.tex:188` | Related Work · *LLM tool-calling / radio agents* | `openai_function_calling` | （同 #2） | 已核验 |
| 11 | `main.tex:189` | Related Work · *LLM tool-calling / radio agents* | `mcp_spec` | Anthropic, *Introducing the Model Context Protocol (MCP)*, spec rev 2024-11-05 | 已核验 |
| 12 | `main.tex:191` | Related Work · *LLM tool-calling / radio agents* | `radiomaster2026` | Anonymous arXiv authors, *RadioMaster: Multi-Agent System for Autonomous Radio Signal Generation*, arXiv:2606.01862 | 部分核验（预印本，未确认同行评审） |
| 13 | `main.tex:192` | Related Work · *LLM tool-calling / radio agents* | `mxai2025` | Anonymous arXiv authors, *MX-AI: Agentic Observability and Control Platform for Open and AI-RAN*, arXiv:2508.09197 | 部分核验（预印本，目标 Open-RAN） |
| 14 | `main.tex:197` | Related Work · *O-RAN / 6G RAN intelligence* | `polese2023understanding` | Polese, M. et al., *Understanding O-RAN: Architecture, Interfaces, Algorithms, Security, and Research Challenges*, IEEE COMST 25(2):1376–1411 (2023) | 已核验 |
| 15 | `main.tex:201` | Related Work · *Foundations reused here* | `sigmf_spec` | （同 #4） | 已核验 |
| 16 | `main.tex:202` | Related Work · *Foundations reused here* | `hoots1980spacetrack` | （同 #5） | 已核验 |
| 17 | `main.tex:203` | Related Work · *Foundations reused here* | `vallado2006revisiting` | （同 #5） | 已核验 |
| 18 | `main.tex:204` | Related Work · *Foundations reused here* | `wilson1927` | （同 #3） | 已核验 |

> 行 84 / 91 / 93 / 103 / 106 / 127 / 163 为正文自然锚点（Introduction / System Design / Experiments）；
> 行 183–204 为 Related Work 段，按 main.md §V 的方向（SDR frameworks / LLM tool-calling & radio agents /
> O-RAN / Foundations reused here）逐方向 `\cite`。

---

## 2. 每个 bib key 的首引位置（供验收抽查）

| bib key | 首引 file:line | 出现次数 |
|---|---|---|
| `gnuradio_project` | `main.tex:84` | 2（Intro:84, RW:183） |
| `sdrpp_project` | `main.tex:84` | 2（Intro:84, RW:184） |
| `satdump_project` | `main.tex:84` | 2（Intro:84, RW:184） |
| `openai_function_calling` | `main.tex:91` | 2（Intro:91, RW:188） |
| `wilson1927` | `main.tex:93` | 3（Intro:93, Exp:127, RW:204） |
| `sigmf_spec` | `main.tex:103` | 2（Sys:103, RW:201） |
| `hoots1980spacetrack` | `main.tex:106` | 3（Sys:106, Exp:163, RW:202） |
| `vallado2006revisiting` | `main.tex:106` | 3（Sys:106, Exp:163, RW:203） |
| `mcp_spec` | `main.tex:189` | 1（RW:189） |
| `radiomaster2026` | `main.tex:191` | 1（RW:191） |
| `mxai2025` | `main.tex:192` | 1（RW:192） |
| `polese2023understanding` | `main.tex:197` | 1（RW:197） |

合计 19 次。

---

## 3. 无正文引用点的方向（如实标注，未硬塞）

- **`llm_sdr_measurement_backbone`（main.md §V 末段 / refs.bib:273–285 注释）**：
  这是论文声称的"缺口方向"——**未**在 2026-10-02 公开检索中定位到任何经同行评审的、开放可复现的
  LLM 驱动 SDR *测量骨干*先前工作。refs.bib 按防虚构铁律**故意不发 BibTeX 条目**（只保留注释占位）。
  因此 main.tex 正文**不对该 key 做 `\cite`**——无条目可引，亦不硬塞。Related Work 段已用文字诚实陈述
  这一缺口（"neither is confirmed peer-reviewed … rather than an open, reproducible SDR measurement backbone"）。

## 4. 备注

- `\bibliographystyle{IEEEtran}` / `\bibliography{refs}` 在 main.tex:230–231 仍按既有注释**保持注释态**，
  待人类作者在装有 `IEEEtran.cls` 的环境启用（本环境缺该类文件，无法实测 bibtex 报错）。
  一旦启用，上述 12 个 key 均已在 refs.bib 定义，预期不再产生 `undefined citation`。
- 本块**未触碰** refs.bib 任何字段、未触碰 C++/Flutter、未含比赛字样、未 commit/push。
