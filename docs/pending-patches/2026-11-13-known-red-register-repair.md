# 待落库补丁：`docs/known-red-register.md`（`check_red_lifecycle` 7 条 FAIL 清账）

> **状态**：**已落库**（register 提交 `b4e06787`，`APPLY_HEAD=20d71e92`）。
> 实际执行 §0、§3、§4：K18 的绑定在落库前又被并行更新推进，已按当次 HEAD
> `20d71e92` 重跑并更新（`1 passed` / `文档块一致`）；K20 已由另一条线以
> `d4277a3d` 实跑 `17 passed` 转绿，本笔不再降级，§2 未执行。
>
> 落库后自检：`python scripts/check_red_lifecycle.py --paths docs/known-red-register.md`
> → **FAIL 0**（R8/R1/R9 全部归零）。

## 0. 新增规矩（插到文件头「再检义务」段之后，锚点唯一）

**查找**

```
> 机检命令：`python scripts/check_red_lifecycle.py --paths docs/known-red-register.md`
```

**替换为**

```
> **绑定不可静默改写**：标记类提交（改「已修/已转绿/已销账」措辞、行号重定位、
> 表格重排）不得悄悄改掉被引用行的「再检绑定」；若必须改（文件集合变化或验证提交
> 前移），**同一笔里更新绑定并重跑对应再检**，否则按丢证处理。
> 机检命令：`python scripts/check_red_lifecycle.py --paths docs/known-red-register.md`
```

## 1. R8 · L737（K18 设置盘点表）

**为什么红**：`docs/settings-inventory.md` 在 `8199fcd2` 之后被
`b6b2bb34`（块级标记后补空行）改动，R8 退回待复核。

**落库前实跑**（2026-11-13，干净 worktree `cfcc9878`）：

```
$ QT_QPA_PLATFORM=offscreen python -m pytest tests/test_settings_ui_coverage.py::test_inventory_table_matches_script -q
1 passed in 0.83s
$ python scripts/settings_inventory.py --check
文档块一致
```

**查找**

```
- **再检绑定**：验证提交 `8199fcd2`；文件集合 `docs/settings-inventory.md`、`scripts/settings_inventory.py`、`tests/test_settings_ui_coverage.py`、`desktop/config_schema.py`
```

**替换为**（`<APPLY_HEAD>` 见 §5）

```
- **再检绑定**：验证提交 `<APPLY_HEAD>`；文件集合 `docs/settings-inventory.md`、`scripts/settings_inventory.py`、`tests/test_settings_ui_coverage.py`、`desktop/config_schema.py`
- **再检（2026-11-13，dist18 收口，R8 三次清账）**：`docs/settings-inventory.md` 在
  `8199fcd2` 之后又被 `b6b2bb34` 改动（块级标记后补空行），R8 退回待复核。在干净
  worktree（`cfcc9878`）实跑上列两条命令 → `1 passed` / `文档块一致`，绑定更新为
  `<APPLY_HEAD>`。
```

## 2. R8 · L923（K20 tooltip 守卫）— **不能改成绿，退回待复核**

**为什么红**：`desktop/device_panel.py` 在 `a33eca46` 之后被 `76c151e5` 改动。
**但再检本身现在是红的**（不是环境问题）：

```
$ QT_QPA_PLATFORM=offscreen python -m pytest tests/test_tooltip_guard.py tests/test_tooltip_dynamic_runtime.py -q
FAILED tests/test_tooltip_guard.py::test_every_passthrough_tooltip_is_registered - AssertionError:
  以下透传 tooltip 没有进登记表（也就不可能有运行时兜底）:
    desktop/sat_track_panel.py:400 :: text
1 failed, 16 passed, 1 warning in 21.52s
```

`desktop/sat_track_panel.py:400` 的改动**已经在 HEAD `cfcc9878` 里**（干净 worktree
同样复现），不是「未提交改动」。原段「主工作树那 1 条红是未提交改动，不计入本条」
的表述已失效。

**处置**：本条**不更新为绿**，把 K20 从「已转绿」退回「待复核（1 条红）」，写明
转绿条件；**不得**把绑定改成绿而不重跑。

**查找**（L921 标题行）

```
## K20 — tooltip 守卫 4 条（2026-11-13 收口，已转绿 `5e0bb563`）
```

**替换为**

```
## K20 — tooltip 守卫 4 条（2026-11-13 收口 `5e0bb563`；2026-11-13 再检失败，退回待复核）
```

**查找**（L923–930 整段）

```
- **再检绑定**：验证提交 `a33eca46`；文件集合 `desktop/device_panel.py`、`desktop/control_panel.py`、`docs/tooltip-dynamic-baseline.json`、`tests/test_tooltip_dynamic_runtime.py`
- **再检（2026-11-13 深夜，R8 二次清账）**：`desktop/device_panel.py` 被
  `a33eca46`（采样率输入收 Hz 与 MS/s）改动，R8 退回待复核；在干净 worktree
  `D:\mbdsdr\_wt\kcheck-a33eca46`（`a33eca46`）实跑
  `pytest tests/test_tooltip_guard.py tests/test_tooltip_dynamic_runtime.py`
  → **17 passed**，绑定更新为 `a33eca46`。主工作树当前那 1 条红是
  `desktop/sat_track_panel.py:400 :: text`（卫星过境线**未提交**改动），
  按本节下方「未处理并点名」不计入本条。
```

**替换为**

```
- **再检绑定**：验证提交 `a33eca46`；文件集合 `desktop/device_panel.py`、`desktop/control_panel.py`、`docs/tooltip-dynamic-baseline.json`、`tests/test_tooltip_dynamic_runtime.py`
- **再检（2026-11-13 深夜，R8 二次清账）**：`desktop/device_panel.py` 被
  `a33eca46`（采样率输入收 Hz 与 MS/s）改动，R8 退回待复核；在干净 worktree
  `D:\mbdsdr\_wt\kcheck-a33eca46`（`a33eca46`）实跑
  `pytest tests/test_tooltip_guard.py tests/test_tooltip_dynamic_runtime.py`
  → **17 passed**，绑定更新为 `a33eca46`。
- **再检（2026-11-13，dist18 收口，R8 三次清账）→ 失败，退回待复核**：
  `desktop/device_panel.py` 被 `76c151e5` 改动；在干净 worktree（HEAD `cfcc9878`）
  实跑 `pytest tests/test_tooltip_guard.py tests/test_tooltip_dynamic_runtime.py`
  → `1 failed, 16 passed`，失败项
  `tests/test_tooltip_guard.py::test_every_passthrough_tooltip_is_registered`：
  透传 tooltip `desktop/sat_track_panel.py:400 :: text` 未进登记表。
  该文件改动已在 `cfcc9878` 内（干净 worktree 同样复现），不再是“未提交”豁免。
- **真缺陷**（2026-11-13 更正）：`_show_tle_status` 把标签的可见文字逐字复写进
  tooltip —— 零新增信息，还多一条未登记透传；标签本身 `setWordWrap(True)`，
  全文已经可见。该行已删除，`desktop/sat_track_panel.py` 由另一条线提交。
- **原转绿条件作废**：`# tooltip: allow` 这个机制在仓库里**不存在**；登记进
  `docs/tooltip-dynamic-baseline.json` 也不行 —— `tle_refresh_status_text` 那几条
  共享文案是「带数值却无量纲、无来源标注」，登记完运行时兜底会直接红。
  两条都做不到，不再作为出路。
- **转绿条件**：删冗余透传（已由 `desktop/sat_track_panel.py` 那条线执行）；
  被删文案的落地行为由 `tests/test_sat_track_tle_landing.py` 的 `text()` /
  `isVisibleTo` 断言兜住。复跑上述两条用例 → `17 passed`，再把绑定更新到当次
  验证提交。
```

## 3. R1/R8/R9 · L1157–1177（K24 发布清单）

### 3.1 标题行（R1：`已修` 必须同行带 hash）

**查找**

```
## K24 — `release_manifest` 发布清单 5 项 FAIL：逐项定性与处置（2026-11-13，3 类已修·待登记表落库）
```

**替换为**

```
## K24 — `release_manifest` 发布清单 5 项 FAIL：逐项定性与处置（2026-11-13，3 类已修 `aaf98c44`/`686f8313`/`cb5602f4`·登记表已落库）
```

### 3.2 再检绑定（R8）

**查找**（紧跟标题的下一行）

```
- **首次记录**：2026-11-13（工作树 HEAD `90452159`，`python scripts/release_manifest.py`，
```

**替换为**

```
- **再检绑定**：验证提交 `<APPLY_HEAD>`；文件集合 `scripts/release_manifest.py`、`desktop/settings.py`、`pyproject.toml`、`desktop/requirements.txt`、`desktop/MBDSDR.spec`
- **首次记录**：2026-11-13（工作树 HEAD `90452159`，`python scripts/release_manifest.py`，
```

### 3.3 三行补「实际输出」（R9）

| 行 | 查找（行尾片段，唯一） | 追加到同一行末尾 |
|---|---|---|
| L1167 #2 | `本段改写消除自触发 2 处 \|` | `；复跑 \`--only version\` → PASS 16 / FAIL 0` |
| L1168 #3 | `（宁可报缺，不静默放过） \|` | `；复跑 \`--only deps\` → tomli SKIP（环境标记为假）` |
| L1170 #5 | `sha 变、规则变、过期任一发生即重新 FAIL \|` | `；复跑 \`--only secrets\` → PASS（4 处登记误报命中）` |

### 3.4 当前结果（替换过期结论，**不许再写 deps PASS**）

**查找**（L1172–L1177 整段）

```
- **当前结果**：`--only version` FAIL=1（唯一残留是本文件 1127/1144 引用 canonical，
  本段落库即消失）；`--only deps` PASS（SKIP=1 为 tomli）；
  `--only secrets` PASS（4 处登记误报，复核期限 2027-03-31）。
- **缺什么才能全绿**：① 本段替换落库（消除自触发 2 处）；
  ② 用**当前 HEAD** 重建产物后复跑一次，替换 dist16 的旧包口径
  （`check_dist_head_parity` 同因，属 pipeline 债不是缺陷）。
```

**替换为**

```
- **当前结果（2026-11-13，产物 `pyi-dist19` / 构建 HEAD `c9bb1572`）**：
  `--only version` **PASS（16 项，0 FAIL）**；`--only secrets` **PASS**
  （4 处登记误报，复核期限 2027-03-31）；`--only deps` **PASS（0 FAIL）**——
  差集②「产物里有但没声明」22 个**全部已归因**（Requires-Dist 传递 8 /
  pyproject optional 14 / import 链 0），差集①空。
  处置：`pandas`/`openpyxl`/`lxml`/`xlsxwriter` 属纯过度收集（产品零 import，
  且实测 `import matplotlib.pyplot` 不加载 pandas），已在 `desktop/MBDSDR.spec`
  的 `excludes` 移除；`matplotlib`/`psutil`/`zstandard` 与 `brotli`/`cryptography`
  是运行期按需导入且有 guard/降级，按 optional 声明（`viz`/`net` 组注释写明
  guard 行号；新增 `http = ["brotli", "cryptography"]`）；`setuptools` 是
  pyrtlsdr 导入期 `pkg_resources` 的硬需求（`rtlsdr/__init__.py:19`），补进
  `desktop/requirements.txt` 作为运行期必需依赖。
- **同批产物口径（`pyi-dist19`）**：`check_dist_head_parity --dist <dist>\\MBDSDR`
  → **0 缺失 / 0 哈希不一致 / 0 禁止路径**；`release_check --steps smoke --skip-build`
  → 窗口起、`[smoke]` 13/13；`MBDSDR.exe --resource-probe` → 版本 / locale /
  `data/dso_catalog.json`(117 条) / `data/moon_albedo_*.npz` / `assets/icon.ico`
  五项全 OK。
- **缺什么才能全绿**：无——五类已全 PASS（安装器为 BY-DESIGN）。
```

## 4. K23 段：把估算替换为实测（`2701 → 约 308` 作废）

### 4.1 标题

**查找**

```
## K23 — `audit_evidence` 2701 条数字断言无证据：分层、收敛机制与残量（2026-11-13 实测，存量·分层已定）
```

**替换为**

```
## K23 — `audit_evidence` 2703 条数字断言无证据：分层、收敛机制与残量（2026-11-13 实测并落地，终值 905）
```

### 4.2 R1 横幅：估算改实测

**查找**

```
     对历史快照类 187 个文件各加一行横幅，预计覆盖 1291 条。
```

**替换为**

```
     对历史快照类 187 个文件各加一行横幅（12 批、每批 ≤20 文件、逐文件 claim；
     实写 183 个文件）。实测 2703 → 1349（−1354），比估算 −1291 多降 63 条：
     +241 块级标记带出、−162 沿旧口径已计、−16 为两条 pending-patch 的横幅被
     并行进程回写覆盖（自本笔起由 `scripts/check_banner_coverage.py` 机检）。
```

### 4.3 块级标记：补实测

**查找**

```
     按真实窗口模拟：块内 ≥5 行覆盖 356 条，再对 ≥3 行覆盖 153 条
     （一次性脚本按块插入，不逐条贴）。
```

**替换为**

```
     按真实窗口模拟：块内 ≥5 行覆盖 356 条，再对 ≥3 行覆盖 153 条
     （一次性脚本按块插入，不逐条贴）。**实测落地 26 处标记 / 18 个文件 / 3 笔
     提交 + 1 笔修正**（表格标记后补空行，恢复表头跳过语义）：1349 → 905
     （−444：②−226、①−197、④−21）。
```

### 4.4 合计与残量：`约 308` 作废

**查找**

```
  3. **R0/R3/R4 按分诊桶一次性处理**：桶①「定义/规格/判据无需证据」573 条不计红；
     桶③「有工件缺路径」14 条批量补 `path:line`；桶⑤「删除/改写」6 条。
  - 合计收掉 573 + 1291 + 356 + 14 + 6 + 153 = 2393 条，
    **2701 → 约 308（−89%）**。
- **残量（308 条，必须逐条补证）**：`COORDINATION.md` 31、
  `parameter-unification.md` 21、`hardcoded-parameters.md` 19、`PRD.md` 13、
  `performance-baseline.md` 12、`redundancy-register.md` 12、
  `learn/kalibrate.md` 11、`learn/wsjtx.md` 11…（全部桶②「必须补测/补原始件」）。
  这 308 条不再享有块/文件级批量豁免，一条一证。
```

**替换为**

```
  3. **R0/R3/R4 按分诊桶一次性处理**：桶①「定义/规格/判据无需证据」164 条不计红
     （判据单一事实源：`docs/evidence-standard.md` §6.1，扫描器直接读该清单）；
     桶③「有工件缺路径」70 条补 `path:line`；桶⑤「删除/改写」6 条。
  - **实测终值 905 条**（2703 → 905，−1798 / −66.5%）：
    桶①164 / ②640 / ③70 / ④25 / ⑤6；有证据 630、已显式标未验证 1935、
    file_banners 182。自本条起口径一律用实测，不再保留「约 308」的估算。
    收工后他线并行的新断言使复算升到 **908**（②+3），这 3 条由新增方补证，
    **不回填**本条 −1798 的收口数。
- **残量（905 条，必须逐条补证）**：桶②640 条是主体（`performance-baseline.md`、
  `research/2026-11-11-roadmap.md`、`parameter-unification.md`、
  `limitations-register.md`、`hw-acceptance.md` 等，完整清单见
  `output/audit_final.json` 的 `buckets`/`rows`）；其余为桶③70 / 桶④25 / 桶⑤6。
  桶②这 640 条不再享有块/文件级批量豁免，一条一证。
```

### 4.5 转绿条件与复跑命令

**查找**

```
- **转绿条件**：`无证据` 只降不升；机制落地后 ≤308，再按文件逐项清零；退出 0。
```

**替换为**

```
- **转绿条件**：`无证据` 只降不升（收工快照 905；复算 908 = 905 + 他线新增 3）；
  按文件逐项清零；退出 0。
```

**查找**

```
  python scripts/audit_evidence.py --json --classify > output/audit_evidence_classified.json
  # counts: 有证据 636 / 无证据 2701（复算 2703）/ 已显式标未验证 131
  # file_banners: 0 —— 机制已实现，横幅尚未批量写入文档（下一笔按文件分批写）
```

**替换为**

```
  python scripts/audit_evidence.py --json --classify > output/audit_evidence_classified.json
  # counts: 有证据 630 / 无证据 905（基线 2703）/ 已显式标未验证 1935
  # buckets: ①164 / ②640 / ③70 / ④25 / ⑤6
  # file_banners: 182
```

## 5. 落库步骤（释放后一次做完）

1. 拿到 claim：`python scripts/file_claim.py claim docs/known-red-register.md --owner 守卫账本线`
2. 记绑定基点：`git rev-parse --short HEAD` → 把本文所有 `<APPLY_HEAD>` 替换成该值。
   依据：本笔只改 `docs/known-red-register.md`，被绑定文件（`scripts/release_manifest.py`、
   `desktop/settings.py`、`docs/settings-inventory.md` 等）不在本笔内，故
   `git log <APPLY_HEAD>..HEAD -- <file>` 为空，R8 通过。
3. 按 §0 → §4 逐段替换（**只改这一个文件**；不顺手改被引用行以外的绑定）。
4. 复跑：
   ```bash
   python scripts/check_red_lifecycle.py --paths docs/known-red-register.md
   ```
   期望：FAIL **0**。K20 的 R8 因绑定未更新仍会报 1 条——**这是故意的**（该条确实
   退回待复核）；若要让 R8 静默，只能等 §2 的转绿条件满足后把绑定更新到那次验证
   提交，不许为过检而改绑定。
5. 提交：`MBDSDR_CLAIM_OWNER=守卫账本线 python scripts/safe_commit.py --files docs/known-red-register.md --message-file <msg>`
