# pending patch：K23 分层收敛 + K24 逐项定性（2026-11-13）

**为什么是 pending**：`docs/known-red-register.md` 现由「残留收口线/6ae85f56」持有
（claim 至 06:11 左右），本线 `claim` 被拒。按协调规矩不抢、不在工作树改共享文件，
两段替换文本先落在这里；谁拿到 claim 谁整段替换。

**落库方式**：分别替换 `## K23 — …` 段与 `## K24 — …` 段（从标题到下一个 `---`）。
K24 段替换后，本文件里引用 canonical 的两行（1127/1144）一并消失，
`release_manifest --only version` 的自触发 FAIL 随之清零。

**来源**：两段均由「守卫账本线」本轮撰写；不含其他线的段落。

---

## 替换段 1：K23

## K23 — `audit_evidence` 2701 条数字断言无证据：分层、收敛机制与残量（2026-11-13 实测，存量·分层已定）

- **首次记录**：2026-11-13（工作树 HEAD `90452159`，`python scripts/audit_evidence.py`）。
- **分类**：存量。扫描 643 个文件 / 3470 条数字断言
  （`有证据 636 / 无证据 2701 / 已显式标未验证 131`）。
  复算（HEAD `116b4b11`）为 2703，漂移 +2 来自他线新增断言；分层以 2701 为基线。
- **分层 A：缺什么**
  | 层 | 条数 | 说明 |
  |---|---|---|
  | 三无 | 2320 | 无 commit、无原始件、无可复跑命令、无未验证标记 |
  | 仅 commit | 381 | 只有 E3 时点线索（单笔最高 33 条，`29ce257c`） |
- **分层 B：文档类型**：`docs/other` 1029、`docs/learn` 815、`docs/research` 459、
  `docs/audit_r2` 261、`docs/pending-patches` 71、`experiments/` 66。
- **分层 C：形态与块**：表格行 1611 / 正文 1090；991 个连续块，
  块内 ≥10 行覆盖 766 条、≥5 行覆盖 1217 条、≥3 行覆盖 1789 条。
- **分层 D：是否同一模板复制**：归一化后最高重复 23 / 19 / 19 / 16 / 15 / 13 条，
  集中在 `docs/research/2026-11-11-roadmap.md`、`docs/performance-baseline.md`、
  `docs/learn/phase7/paper/CHANGES.md` 的成段模板（同结论表/变更记录复制 N 份）。
- **大头文件（前 8）**：`performance-baseline.md` 242、
  `research/2026-11-11-roadmap.md` 200、`parameter-unification.md` 132、
  `limitations-register.md` 100、`hw-acceptance.md` 79、
  `learn/decoder-capability-matrix.md` 66、`experiments/nanovna_rh770/README.md` 56、
  `hardcoded-parameters.md` 55。
- **收敛机制**（1 条已落地，其余为一次性规则）：
  1. **R1 文件级 E4 横幅（已实现）**：`scripts/audit_evidence.py` 新增
     `FILE_BANNER_RE`，只认文件前 80 行的显式写法（「本文件…未验证」
     「证据状态：E4」），命中后整份文件的无证据行记「已显式标未验证」；
     **不按目录名猜**。反证见 `tests/test_audit_evidence.py`
     （有横幅→整份标记；无横幅→照旧无证据；本行自带工件→仍算有证据）。
     对历史快照类 187 个文件各加一行横幅，覆盖 1291 条。
  2. **R2/R5 块级标记（用现有窗口分类，不改代码）**：表格块的证据窗口是
     「表标题 + 表头 + 本行」，所以表头写一行「未验证（E4）」即可覆盖整表；
     正文窗口是同一段 ±2 行，段首标记只覆盖到第 3 行，长段落要按段切分。
     按真实窗口模拟：块内 ≥5 行覆盖 356 条，再对 ≥3 行覆盖 153 条
     （一次性脚本按块插入，不逐条贴）。
  3. **R0/R3/R4 按分诊桶一次性处理**：桶①「定义/规格/判据无需证据」573 条不计红；
     桶③「有工件缺路径」14 条批量补 `path:line`；桶⑤「删除/改写」6 条。
  - 合计收掉 573 + 1291 + 356 + 14 + 6 + 153 = 2393 条，
    **2701 → 约 308（−89%）**。
- **残量（308 条，必须逐条补证）**：`COORDINATION.md` 31、
  `parameter-unification.md` 21、`hardcoded-parameters.md` 19、`PRD.md` 13、
  `performance-baseline.md` 12、`redundancy-register.md` 12、
  `learn/kalibrate.md` 11、`learn/wsjtx.md` 11…（全部桶②「必须补测/补原始件」）。
  这 308 条不再享有块/文件级批量豁免，一条一证。
- **转绿条件**：`无证据` 只降不升；机制落地后 ≤308，再按文件逐项清零；退出 0。
  禁止把脚本排除出守卫清单、禁止把阈值写死成当前值。
- **复跑命令**：
  ```bash
  python scripts/audit_evidence.py --json --classify > output/audit_evidence_classified.json
  # counts: 有证据 636 / 无证据 2701（复算 2703）/ 已显式标未验证 131
  # file_banners: 0 —— 机制已实现，横幅尚未批量写入文档（下一笔按文件分批写）
  ```
- **owner / 复核**：证据标准 owner；复核期限 2026-12。

---

## 替换段 2：K24

## K24 — `release_manifest` 发布清单 5 项 FAIL：逐项定性与处置（2026-11-13，3 类已修·待本段落库）

- **首次记录**：2026-11-13（工作树 HEAD `90452159`，`python scripts/release_manifest.py`，
  自动取到 `.tmp/pyi-dist16`）。
- **复跑口径**：产物 `pyi-dist16` 由 HEAD `6b19aa89` 构建，**比当前 HEAD 旧**；
  版本/依赖/凭据三类都按「产物 + 工作树」双口径判定。

| # | FAIL 原文 | 定性 | 处置 |
|---|---|---|---|
| 1 | `pyproject.toml` 写死版本字面量：第 70/75 行 | **误报**：两处都是 sdist 残留目录名 `mbdsdr_ai-<canonical>`，不是版本声明 | 已修：改成 `mbdsdr_ai-[0-9]*`（注释同步），排除任意版本残留目录，也不再写死版本 |
| 2 | 全仓手写 canonical 字面量（先 4 处、后 6 处） | **5 处误报 + 1 处文案**：`pyproject`/`.gitignore`/`COORDINATION` 4 处同为 `<name>-<x.y.z>` 目录名；`desktop/settings.py:143` 是「0.2 系列及更早」的历史文案；另 2 处是**本 K24 段落自己**引用 canonical | 已修：扫描器只跳过「紧跟在 `name-` 后的版本」（文件名片段），独立字面量照旧拦；文案改「0.2 系列」；本段改写消除自触发 2 处 |
| 3 | `tomli` ← 声明了但产物没有 | **口径缺陷，不是产品缺陷**：`tomli` 只在 `requirements.txt` 以 `python_version < "3.11"` 声明，产物解释器是 3.14；产品代码零导入，仅 3.10 测试用 | 已修：依赖检查按**产物解释器版本**评估环境标记，标记为假记 SKIP；取不到产物版本时仍按声明面校验（宁可报缺，不静默放过） |
| 4 | 差集①「声明了但产物没有」：tomli | 与 #3 同一根因的第二条计数 | 同 #3 |
| 5 | 产物凭据形状 4 个第三方二进制 | **误报**，逐条核定：3 个 Qt TLS 模块命中的是 PEM **头标记字符串**（`-----BEGIN … PRIVATE KEY-----`，无密钥正文）；scipy `.pyd` 的 `ghs_` 是 40 字符 base62 串嵌在更长标识符中（GitHub PAT 不会嵌在标识符内部） | 已修：新增产物二进制登记，**路径 + sha256 + 规则名 + review_by** 四元组；sha 变、规则变、过期任一发生即重新 FAIL |

- **当前结果**：`--only version` FAIL=1（唯一残留是本文件 1127/1144 引用 canonical，
  本段落库即消失）；`--only deps` PASS（SKIP=1 为 tomli）；
  `--only secrets` PASS（4 处登记误报，复核期限 2027-03-31）。
- **缺什么才能全绿**：① 本段替换落库（消除自触发 2 处）；
  ② 用**当前 HEAD** 重建产物后复跑一次，替换 dist16 的旧包口径
  （`check_dist_head_parity` 同因，属 pipeline 债不是缺陷）。
- **复跑命令**：
  ```bash
  python scripts/release_manifest.py --only version
  python scripts/release_manifest.py --only deps
  python scripts/release_manifest.py --only secrets
  ```
- **owner / 复核**：发版人（版本与打包）＋ 安全 owner（二进制登记）；复核期限 2026-12。
