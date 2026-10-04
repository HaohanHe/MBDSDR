# MBDSDR Paper — Phase 7 Draft

> 受控论文初稿目录。本目录被 `.gitignore` 显式例外纳入版本控制；
> 仓库根 `paper/`（含早期草稿与实验产物）**仍整体忽略**，不在此目录。

| 项 | 值 |
|---|---|
| 主稿（**唯一事实源**） | [`main.md`](./main.md)（Markdown，IEEE WCL 篇幅对标，约 4–5 页量级） |
| LaTeX 镜像（骨架） | [`main.tex`](./main.tex)（IEEEtran journal letter 骨架，图表为注释式迁移点；**勿在此改数字/论断**） |
| 参考文献 | [`refs.bib`](./refs.bib)（phase 11 已建：12 条，7 已核验 / 5 部分核验 / 0 虚构，逐条带核实注释；核实日 2026-10-02） |
| 目标刊物 | IEEE Wireless Communications Letters (WCL)，4–5 页 |
| 状态 | **DRAFT v0.5（2026-10-02，refs.bib 核实 + 作者/致谢/基金诚实 PENDING 占位）**，未提交 / 未 commit |
| 数据口径 | 全部 `synthetic`（固定种子仿真）；无 OTA/录制；LLM 列 `PENDING_ONLINE_RUN` |
| 投稿包 | `bash tools/paper_package.sh`（打包 + zip + `--check` 引用链自检，当前 71 pass / 0 fail） |
| 许可证 | MIT（代码）；论文内容随仓库 |

---

## 1. 渲染 / 编译

主稿是纯 Markdown，推荐以下任一方式出 PDF：

```bash
# 方式 A：pandoc（推荐，带目录）
pandoc docs/learn/phase7/paper/main.md -o main.pdf --toc \
    -V geometry:margin=2cm -V fontsize=10pt

# 方式 B：VS Code Markdown PDF 插件 / Typora 直接导出
# 方式 C：IEEEtran 正式排版（见下方"main.md → main.tex 同步机制"）
```

### main.md → main.tex 同步机制（main.md 为唯一事实源）

- **改稿只动 `main.md`**。`main.tex` 是投稿排版用的镜像骨架，**不要**在
  `main.tex` 里直接改数字、结论或措辞——一切以 `main.md` 为准。

### pdflatex 真实编译（2026-10-05 已跑通正文+图）

- **环境要求**：TeX Live 的 `pdflatex` + `bibtex`（本环境 `/usr/bin/`，TeX Live
  2022）。**需自备 `IEEEtran.cls` 与 `IEEEtran.bst`**——系统默认未装（`kpsewhich
  IEEEtran.cls` 为空）。本轮已从 CTAN 取
  `IEEEtran.cls V1.8b (2015/08/26)` 与 `IEEEtran.bst V1.14 (2015/08/26)` 放进本目录
  （来源与版本见 `build-log.txt §1`）。TeX Live 完整版若已带 IEEEtran 则无需下载。
- **编译命令**（在本目录 `docs/learn/phase7/paper/` 下）：
  ```bash
  pdflatex main.tex
  bibtex main          # 见下方注意：refs.bib 当前暂不可被 bibtex 解析
  pdflatex main.tex
  pdflatex main.tex
  ```
- **图路径**：图不在本 docs 树内，而在仓库根 `paper/experiments/figures/*.png`。
  main.tex 已加 `\graphicspath{{../../../../paper/experiments/figures/}}`（从本目录上溯
  4 层到仓库根）。Fig.1–10 已全部真实 `\includegraphics` 接入（2026-10-05 核实图均存在）。
- **当前真实结果**：正文 + 10 图可干净编译（`main.pdf`，3 页，0 致命错误）。
  **bibliography 暂注释**——refs.bib 有三处 BibTeX/pdflatex 兼容性问题（注释里裸写
  `@misc`/`@techreport`、note 里含中文 `[部分核验]`、note 里未转义 `function_call`），
  由 refs.bib 的负责块修复后取消 main.tex 末尾 `\bibliography{refs}` 注释再重跑全流程。
  详见 `build-log.txt` 与 `P2-submission-check.md`。

- `main.tex` 里图表以注释式迁移点标出，例如：
  `% Fig.1 → paper/experiments/figures/ebno_decode_success__synthetic__N2400__20261001.png`。
- 投稿前同步二选一：
  1. **手抄**：把 `main.md` 各节文字填进 `main.tex` 对应 `\section`，把注释里的
     图换成 `\includegraphics[width=\linewidth]{...}`、表换成 `booktabs` 表格；
  2. **工具**：`pandoc main.md -o body.tex --standalone=false` 生成正文片段后
     贴入 `main.tex`，再手工校正图表/表格/引用（pandoc 不会自动插图）。
- 同步后跑 `pdflatex main && bibtex main && pdflatex main && pdflatex main`；
  `refs.bib` **已建并逐条公开核验（2026-10-02）**，投稿时确认 `\cite` 键与正文对应即可。
- 图表相对路径：仓库根 `paper/experiments/figures/*.png`（该目录被 gitignore，
  不随本目录版本控制）。

### 图表引用约定

- 图：`Fig.1`…`Fig.10`，对应 `paper/experiments/figures/*.png`。
- 表：`Tab.I`…`Tab.VI` + 附录实验-产物映射表。
- 每张图标题与文件名自带 `[origin, N=..., date]`；每个数字可回溯到
  `paper/experiments/*.csv` 与对应 `manifest_*.json`。

---

## 2. 复现实验

```bash
# 在仓库根目录；全部确定性、固定种子 20261001
python3 experiments/exp_ebno_decode.py      --trials 50            # Fig.1  N2400
python3 experiments/exp_rate_sweep.py       --trials 200           # Fig.2  N1400  (新)
python3 experiments/exp_rate_bandwidth.py   --trials 100           # Fig.3  N900
python3 experiments/exp_doppler_comp.py     --trials 200           # Fig.4  N3600  (新)
python3 experiments/exp_baseline_compare.py  --trials-per-class 40  # Fig.5  N600
python3 experiments/exp_llm_baseline.py     --trials-per-class 20  # Fig.6  N300
python3 experiments/exp_amr.py              --trials 80            # Fig.7  N640
python3 experiments/exp_doppler_orbit.py    --n-obs 31             # Fig.8  N155
python3 experiments/exp_doppler_duration.py                        # Fig.9  N6
python3 experiments/exp_agent_toolcall.py   --trials 50            # Fig.10 N400   (新)

# 离线确定性测试
pytest experiments/tests/ -q
```

产物落 `paper/experiments/`（CSV + figures/ + manifest_*.json）。

---

## 3. PENDING 项清单（诚实空态，未伪造）

| 项 | 状态 | 解锁条件 |
|---|---|---|
| LLM 调制识别列（Fig.6） | `PENDING_ONLINE_RUN` | 设 `MBDSDR_LLM_API_KEY`/`MBDSDR_LLM_BASE_URL` 后跑 `exp_llm_baseline.py`，结果缓存 `.llm_cache_*.json` |
| LLM 工具调用决策质量（Fig.10 / Tab.VI 末行） | `PENDING_ONLINE_RUN` | 有 key 环境跑 `exp_weak_model_toolcall.py`，回填 call_rate/pick_rate/arg_ok |
| Fig.5 与 Fig.6 合并 | 待 LLM 回填后 | LLM 曲线到位后，把两张三类（经典/KNN/LLM）图合并为一张（见 main.md III.E reviewer note） |
| OTA / 真实录制解码 | 空态 | 用 C++ recorder 录 SigMF 后 `--recordings-dir` 回放，manifest 标 `recorded` |
| 多普勒补偿的**实测** fd 估计 | 空态 | 当前 Fig.4 用已知 fd（理想上界）；待真实 fd 估计器接入 |
| 多站 / GNSS 融合定轨 | 未做 | 当前为单站多普勒-only（Fig.8/9 的观测性局限） |
| LaTeX/IEEEtran 排版 | **骨架已建（`main.tex`）** | 投稿前按 §同步机制把 main.md 终稿填入、插图、跑 pdflatex+bibtex |
| 参考文献（`refs.bib`） | **已建（12 条，7 已核验 / 5 部分核验）**，2026-10-02 | 投稿前确认正文 `\cite` 键齐全；部分核验条目（开源项目/预印本）按需取舍 |
| 作者名单 / 单位 / 邮箱 / 致谢 / 基金 | **PENDING 占位（main.tex \thanks + main.md 元信息）**，未编造 | 现为 Bi4MIB open-source call；投稿前由真人补真实作者/单位/邮箱/致谢/基金号 |

### 投稿准备清单（IEEE WCL）

- [ ] LLM 在线运行回填 Fig.6 / Tab.VI，并合并 Fig.5+Fig.6。
- [ ] 至少一组 OTA / 录制 SigMF 回放结果（manifest 标 `recorded`），否则在信中说明仅合成验证。
- [x] `refs.bib` 建成并逐条公开核实（2026-10-02）；作者/单位/基金/通讯地址**仍 PENDING**，投稿前由真人补齐（未编造）。
- [ ] `main.md` 终稿同步进 `main.tex`，图表换 `\includegraphics{}`，两栏排版落在 4–5 页。
- [ ] pdflatex+bibtex 干净编译，无孤儿图/表，所有 `\ref`/`\cite` 解析。
- [ ] 按 WCL 投稿系统提交（版权表、Highlights、图文摘要等按当届要求）。

---

## 4. 红线遵守

- 论文每个数字均来自 `paper/experiments/*.csv`，无估算/编造。
- 口径全部标 `synthetic`；LLM 列 `PENDING_ONLINE_RUN` 诚实空态。
- 每张图/表标口径 + 样本数 N + 日期。
- 只写 `experiments/` 与 `docs/learn/phase7/` 内文件；根 `paper/` 继续忽略。
