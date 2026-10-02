# MBDSDR Paper — Phase 7 Draft

> 受控论文初稿目录。本目录被 `.gitignore` 显式例外纳入版本控制；
> 仓库根 `paper/`（含早期草稿与实验产物）**仍整体忽略**，不在此目录。

| 项 | 值 |
|---|---|
| 主稿 | [`main.md`](./main.md)（Markdown，IEEE WCL 篇幅对标，约 4–5 页量级） |
| 目标刊物 | IEEE Wireless Communications Letters (WCL)，4–5 页 |
| 状态 | **DRAFT v0.2（2026-10-02）**，可投稿初稿，未提交 |
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
# 方式 C：LaTeX（可选，后续移植 IEEEtran 模板）
```

如需 IEEE 正式投稿格式，后续把 `main.md` 内容迁入 `IEEEtran` 模板
（`\documentclass[journal]{IEEEtran}`），图表改用 `\includegraphics{}`。
当前 Markdown 先行，图表以相对路径引用 `paper/experiments/figures/`。

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
| OTA / 真实录制解码 | 空态 | 用 C++ recorder 录 SigMF 后 `--recordings-dir` 回放，manifest 标 `recorded` |
| 多普勒补偿的**实测** fd 估计 | 空态 | 当前 Fig.4 用已知 fd（理想上界）；待真实 fd 估计器接入 |
| 多站 / GNSS 融合定轨 | 未做 | 当前为单站多普勒-only（Fig.8/9 的观测性局限） |
| LaTeX/IEEEtran 正式排版 | 未做 | 当前 Markdown 初稿；投稿前迁移 |
| 作者信息 / 参考文献格式化 | 未做 | 待补 |

---

## 4. 红线遵守

- 论文每个数字均来自 `paper/experiments/*.csv`，无估算/编造。
- 口径全部标 `synthetic`；LLM 列 `PENDING_ONLINE_RUN` 诚实空态。
- 每张图/表标口径 + 样本数 N + 日期。
- 只写 `experiments/` 与 `docs/learn/phase7/` 内文件；根 `paper/` 继续忽略。
