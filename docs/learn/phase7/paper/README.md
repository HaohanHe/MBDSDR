# MBDSDR Paper — Phase 7 Draft

> 受控论文初稿目录。本目录被 `.gitignore` 显式例外纳入版本控制；
> 仓库根 `paper/`（含早期草稿与实验产物）**仍整体忽略**，不在此目录。

| 项 | 值 |
|---|---|
| 主稿（**唯一事实源**） | [`main.md`](./main.md)（Markdown，IEEE WCL 篇幅对标，约 4–5 页量级） |
| LaTeX 镜像（骨架） | [`main.tex`](./main.tex)（IEEEtran journal letter 骨架，图表为注释式迁移点；**勿在此改数字/论断**） |
| 目标刊物 | IEEE Wireless Communications Letters (WCL)，4–5 页 |
| 状态 | **DRAFT v0.4（2026-10-02，投稿前审稿视角终稿）**，未提交 / 未 commit |
| 数据口径 | 全部 `synthetic`（固定种子仿真）；无 OTA/录制；LLM 列 `PENDING_ONLINE_RUN` |
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
- `main.tex` 里图表以注释式迁移点标出，例如：
  `% Fig.1 → paper/experiments/figures/ebno_decode_success__synthetic__N2400__20261001.png`。
- 投稿前同步二选一：
  1. **手抄**：把 `main.md` 各节文字填进 `main.tex` 对应 `\section`，把注释里的
     图换成 `\includegraphics[width=\linewidth]{...}`、表换成 `booktabs` 表格；
  2. **工具**：`pandoc main.md -o body.tex --standalone=false` 生成正文片段后
     贴入 `main.tex`，再手工校正图表/表格/引用（pandoc 不会自动插图）。
- 同步后跑 `pdflatex main && bibtex main && pdflatex main && pdflatex main`；
  `refs.bib` 须先按 `main.md` §V "References to verify（待补）" 逐条核实后建立。
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
| LaTeX/IEEEtran 排版 | **骨架已建（`main.tex`）** | 投稿前按 §同步机制把 main.md 终稿填入、插图、建 `refs.bib`、跑 pdflatex+bibtex |
| 参考文献（`refs.bib`） | 未做（已列待核实清单） | 按 main.md §V "References to verify" 逐条搜索确认，IEEE 格式入 bib；**禁虚构** |
| 作者名单 / 单位 / 联系方式 | 未做 | 现为 Bi4MIB open-source call；投稿前补真实作者与致谢 |

### 投稿准备清单（IEEE WCL）

- [ ] LLM 在线运行回填 Fig.6 / Tab.VI，并合并 Fig.5+Fig.6。
- [ ] 至少一组 OTA / 录制 SigMF 回放结果（manifest 标 `recorded`），否则在信中说明仅合成验证。
- [ ] `refs.bib` 建成并逐条核实；作者/单位/基金/通讯地址补齐。
- [ ] `main.md` 终稿同步进 `main.tex`，图表换 `\includegraphics{}`，两栏排版落在 4–5 页。
- [ ] pdflatex+bibtex 干净编译，无孤儿图/表，所有 `\ref`/`\cite` 解析。
- [ ] 按 WCL 投稿系统提交（版权表、Highlights、图文摘要等按当届要求）。

---

## 4. 红线遵守

- 论文每个数字均来自 `paper/experiments/*.csv`，无估算/编造。
- 口径全部标 `synthetic`；LLM 列 `PENDING_ONLINE_RUN` 诚实空态。
- 每张图/表标口径 + 样本数 N + 日期。
- 只写 `experiments/` 与 `docs/learn/phase7/` 内文件；根 `paper/` 继续忽略。
