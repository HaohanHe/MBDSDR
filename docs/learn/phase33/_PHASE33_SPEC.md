# 第三十三阶段实现规格：论文投稿冲刺

> 基线：HEAD = eef56b3（已推送），ctest 124/124、flutter 342、analyze 0。
>
> 勘察校正（相对任务背景）：refs.bib **已建**（Phase11 P2 产物，12 条目：7 已核验/5 部分核验/0 编造，2026-10-02 公开检索核验，各条带 HOW/日期/STATUS 注释）但 main.tex **0 处 \cite 未接线**；P2-submission-check.md 目录内缺失（重建）；IEEEtran.cls 未装；**10 张图全部真实存在**于仓库根 paper/experiments/figures/（与 MIGRATE 注释点吻合）。

## 1. refs.bib 接线（防虚构铁律）
- \cite 全接线：正文 19 处 \cite、12 唯一 key 与 refs.bib 一一对应（0 孤儿/0 缺失）；CITATION-MAP.md 映射表；
- **机械语法修复**（B 移交）：注释散文裸 @ 字样（BibTeX 0.99d 误判条目起始，refs.bib:70/112/183/199）、note 字段中文 [部分核验]（:226/:238）、未转义 function_call 下划线（:191）——不增删条目、不改事实字段；
- 取消 \bibliographystyle{IEEEtran}/\bibliography{refs} 注释（main.tex 原 L321-322）。

## 2. LaTeX 全流程编译验证
- IEEEtran.cls V1.8b + .bst V1.14 下载落地（CTAN/aliyun，主镜像 504 换源）；
- 10 图真实 \includegraphics 接入（graphicspath 上溯 4 层）+ fig 标签补全；骨架作者块多余 } 机械修复（exists.}}}→}}，占位文字不动）；
- 全流程 pdflatex → bibtex → pdflatex ×2：**4 页 PDF、12 \bibitem、0 undefined citation、0 致命错误**（7 underfull hbox cosmetic）；build-log.txt 记录。

## 3. PENDING 收口
- P2-submission-check.md 重建（WCL 逐项：refs 完成/编译真实结果/LLM 列=有 key 跑 exp_llm_baseline.py+exp_agent_toolcall.py 回填（不 mock）/OTA 列=真机 SigMF→exp_ota_run.py 回填（当前 empty_state_no_recording n=0）/作者留【待补充：作者与单位】匿名占位）；
- README 补编译环境（需 IEEEtran）；CHANGES.md 追加 v0.5。

## 质量门 / 红线
- 防虚构：无核验不写 bib、无来源不写数字、作者不编造；无比赛字样；活动参数禁入；
- 不改 C++/Flutter；编译中间产物（aux/bbl/blg/log）不入库，pdf/cls/bst 入库（投稿包）；
- 只暂存相关文件（禁 add -A）；**不 commit/push**；如实报告条目数/核验方式/PDF 结果/剩余 PENDING。
