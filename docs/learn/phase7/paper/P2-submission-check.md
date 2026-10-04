# P2-submission-check.md — IEEE WCL 投稿自检清单（Phase33 块2+3 重建）

- 重建日期：2026-10-05（Asia/Shanghai）
- 对象：`docs/learn/phase7/paper/main.tex`（IEEEtran journal letter）
- 说明：本文件在 paper/ 目录下**原缺失/未找到**，按任务要求据真实编译结果重建。
- 红线遵守：未改 refs.bib（块 A 独占）；未改 C++/Flutter；页数/警告如实；无比赛字样；未 commit/push。

---

## WCL 投稿 checklist（逐项真实状态）

| # | 检查项 | 状态 | 证据 / 下一步 |
|---|---|---|---|
| 1 | refs.bib 参考文献库 | **已建（12 条）但暂不可编译** | refs.bib 12 条（7 已核验 / 5 部分核验 / 0 虚构，2026-10-02 核实）。但当前不兼容经典 BibTeX/pdflatex，见 #2 的 blocker。 |
| 2 | 正文 `\cite` 接线 | **块 A 已接 12 键；bibtex 未通** | 正文已 `\cite` 全部 12 键（wilson1927 / hoots1980spacetrack / vallado2006revisiting / sigmf_spec / gnuradio_project / sdrpp_project / satdump_project / openai_function_calling / mcp_spec / radiomaster2026 / mxai2025 / polese2023understanding），键名在 refs.bib 中均存在。**但** `bibtex main` 报 4 错，原因全在 refs.bib（块 A 文件，本块未改）：(a) `%%` 注释散文里裸写 `@techreport`(L~70)/`@misc`(L~112/183/198) 被 BibTeX 误判为条目；(b) note 字段含原始中文 `[部分核验]`；(c) note 字段未转义下划线 `function_call`。修复后取消 main.tex 末尾两行注释并重跑全流程。已在 /tmp 副本验证：仅中和 (a) 后 bibtex 即可生成全部 12 条 \bibitem、零错误。 |
| 3 | LaTeX 编译（真实结果） | **✅ 通过（pdflatex）；bibtex 待 #2** | `pdflatex -interaction=nonstopmode main.tex` ×2 = **exit 0，0 致命错误**。真实产出 **main.pdf，3 页，US letter**，pdfTeX 1.40.22。已下载本地 `IEEEtran.cls V1.8b` + `IEEEtran.bst V1.14`（来源见 build-log.txt §1）。详见 build-log.txt。 |
| 4 | 图接入 | **✅ 10/10 真实 \includegraphics 接入** | 图实际存在于仓库根 `paper/experiments/figures/`（agent 旧注"图不存在"有误，已核实）。main.tex 加 `\graphicspath{../../../../paper/experiments/figures/}`，Fig.1–Fig.10 全部 `\includegraphics` 真实嵌入（pdfimages 证实），全部 `fig:*` 交叉引用解析，0 个 undefined figure ref。无伪造图。 |
| 5 | LLM 列（Fig.6 / Tab.VI 末行） | **PENDING_ONLINE_RUN（诚实空态，未 mock）** | 解锁条件：在有 key 环境设置 `MBDSDR_LLM_API_KEY` 与 `MBDSDR_LLM_BASE_URL`（仅读环境变量，无 key 字面量）后运行：<br>• `python3 experiments/exp_llm_baseline.py --trials-per-class 20` → 回填 Fig.6（`llm_baseline.csv` 的 `llm_acc`/`llm_wilson_lo/hi`），回答缓存 `.llm_cache_*.json` 可复现；<br>• `python3 experiments/exp_agent_toolcall.py --trials 50` → 回填 Tab.VI 的 call_rate/pick_rate/arg_ok_rate（`agent_toolcall_stage_summary.csv`）。<br>现状：`manifest_llm_baseline.json` 记 `llm_calls_this_run=0`；`llm_baseline.csv` 的 `llm_agent=PENDING_ONLINE_RUN`、`llm_acc` 全空；Fig.6 PNG 只画 classic/KNN。**不 mock**。 |
| 6 | OTA / 真机录制列 | **空态（manifest 标 empty_state_no_recording）** | 解锁条件：用 C++ recorder 录 **SigMF**（`.sigmf-data`+`.sigmf-meta`），再 `exp_ota_run.py --recordings-dir <dir>` 回放，manifest 改标 `recorded`。现状：`manifest_ota_handoff.json` = `data_origin: ota` 但 `recording: null`、`recordings_dir: /tmp/nope_recordings`、`n_samples: 0`、`status: empty_state_no_recording`。根目录另有 `ota_recorded_success_vs_ebn0__recorded__N5__20261002.png`（N=5），但无对应 CSV/manifest 落盘，未接入正文（不冒充 OTA 结果）。 |
| 7 | 作者名单 / 单位 / 邮箱 / 致谢 / 基金 | **【待补充：作者与单位】匿名占位** | main.tex `\author`/`\thanks` 全部保留 PENDING 占位：`PENDING_AUTHOR Name`、`PENDING_AFFIL [affiliation TBA]`、`PENDING_EMAIL`、`PENDING_ACK`、`PENDING_FUND`（无确认基金）。现为 "Bi4MIB open-source contributors" 社区署名。**未编造任何个人身份/单位/邮箱/基金号**，投稿前由真人补齐。 |
| 8 | 同步 main.md → main.tex | **部分** | 块 A 已把 Intro / System Design / Experiments 引言 / Doppler / Related Work 的正文与 `\cite` 迁入 main.tex。表 Tab.I–VI 仍为注释式迁移点（booktabs 行待填）；Appendix 实验-产物映射表仍注释。 |
| 9 | 编译环境要求 | **已补入 README.md** | 本环境需本地 `IEEEtran.cls` + `IEEEtran.bst`（已放入 paper/）；TeX Live 2022 / pdflatex + bibtex。 |
| 10 | 数据口径 | **全部 synthetic，固定种子 20261001** | 无 OTA 冒充；LLM 列诚实空态；每图带 origin/N/date。 |

---

## 本轮编译产物（真实）

- PDF：`docs/learn/phase7/paper/main.pdf`，**3 页**，566,858 字节，US letter。
- 致命错误：**0**。pdflatex 末两遍 exit 0。
- 警告（如实，非错误）：23 条 undefined `\cite`（= #2 refs.bib blocker，bibliography 暂关）；4 条 Underfull \hbox（双栏排版松紧，cosmetic）；0 Overfull；0 缺图；0 字体告警。
- 图：10/10 嵌入并解析。

## 未解决项（移交）

1. **【块 A】refs.bib 三处兼容性修复**（#2）：中和注释里的裸 `@misc`/`@techreport`、LaTeX 转义或删除 note 里的中文 `[部分核验]`、转义 `function\_call`。修好后取消 main.tex 末尾 `\bibliographystyle`/`\bibliography` 注释并重跑 `pdflatex → bibtex → pdflatex ×2`。
2. **【真人】LLM 在线运行**（#5）：有 key 环境跑 `exp_llm_baseline.py` + `exp_agent_toolcall.py` 回填 Fig.6 / Tab.VI，随后合并 Fig.5+Fig.6。
3. **【真人/硬件】OTA 真机 SigMF**（#6）：录制后 `exp_ota_run.py --recordings-dir` 回放。
4. **【投稿前】表 Tab.I–VI 行填入 main.tex；Appendix 映射表；作者/单位/邮箱/致谢/基金实名补齐。**
