# 待落库：docs/code-guards.md 的两行守卫登记（2026-11-13）

**为什么不直接改**：`docs/code-guards.md` 当前被 `合规入口线/6ae85f56` 持有
（claim 2026-11-13 05:02:31 起；05:58 复测时已续租为 TTL 120min，剩余 116.4min，
即到 **07:02 左右**）。05:58 本线 `claim` 复测被拒（CLAIM DENIED）。按本线约定，
被他人持有的文件不抢、不并发改；等持有人释放后由 owner 按下面的原文落库。

**落库位置**：`docs/code-guards.md` 第 389–399 行的 C 档登记表。

## 1. 替换 `check_red_lifecycle.py` 那一行（第 393 行）

原文：

```markdown
| `scripts/check_red_lifecycle.py` | `tests/test_red_lifecycle.py` | 仓库现状（27.3s，实测 rc=1：有到期/无证据红项） | 批次末手动；红项状态变更后 | 红项 owner | 2026-12 |
```

替换为：

```markdown
| `scripts/check_red_lifecycle.py` | `tests/test_red_lifecycle.py` | 仓库现状（27.3s，**不进 pre-commit**；2026-11-13 05:54 实测 **rc=1：FAIL 9 / WARN 0 / SKIP 0**＝R8 待复核 3 条＋「已修缺 hash」6 条；红项清单交各 owner，不在扫描时顺带改别人的段落） | **触发条件**：① 每个批次收口前至少 1 次；② 任何 K 条目新增/改绑定/改状态后立即；③ 发版前必跑。**谁跑**：红项 owner（当前 `守卫账本线`）；谁改 K 账谁负责跑。 | 2026-12 |
```

## 2. 在 `check_red_lifecycle.py` 行之后新增一行

```markdown
| `scripts/check_red_accounting.py` | `tests/test_red_accounting.py`（8 条反证，含「rc=1 且无条目必须报无账红」） | 守卫账本 `docs/guard-accounting.json`；`--fast` 约 60s，全量约 5min（含 audit_evidence / release_manifest） | 批次末与发版前；任一守卫改退出码语义或新增守卫后 | 红项 owner（守卫账本线） | 2026-12 |
```

## 3. 同段末尾「删除判定」段（第 418–422 行）的现状句

原文：

```markdown
`check_destructive_ops.py` / `audit_evidence.py` /
`lint_swallow_all.py` / `check_red_lifecycle.py` 当前仍是红的，属于待修项，不是死代码。
```

替换为：

```markdown
`check_destructive_ops.py` / `audit_evidence.py` / `lint_swallow_all.py` 当前仍是红的，
已分别入 K22 / K23 / K06，属于待修项，不是死代码；`check_red_lifecycle.py`
2026-11-13 05:54 实测 rc=1（FAIL 9：R8 待复核 3 条＋「已修缺 hash」6 条），
触发条件见上表，红项交各 owner，尚未转绿。
```

## 附录：2026-11-13 非 0 守卫扫描表（落库时可留在本文件或迁到 code-guards.md）

| 守卫 | 实测 | 判定 | 账/期限 | 依据 |
| --- | --- | --- | --- | --- |
| `verify_claims.py --check-lines --strict-lines` | 04:47 rc=1（1 条行号漂移）→ 05:01 rc=0 | 该修（已被他线 `80a1b873` 修掉） | 无需账 | 复跑 rc=0；唯一未豁免项是 `docs/reference-mechanisms.md:426` |
| `install_git_hooks.py --check` | rc=1（`.git/hooks/secret_scan.py` 与工作区不一致）→ 跑安装器后 rc=0 | 该修（本机跑一次安装） | 无需账 | 安装器已追平；`scripts/secret_scan.py` 仍被他线编辑，可能再次漂移 |
| `lint_swallow_all.py` | rc=1（越基线） | 已入账 | **K06** | 存量静默吞噬棘轮，113+ 处未修 |
| `check_destructive_ops.py` | rc=1（27 笔未审计） | 做 K 条目 | **K22**（复核 2026-12） | reflog 不能自证 no-op，需操作人逐笔补审计 |
| `audit_evidence.py` | rc=1（2703 条无证据 / 131 条已标未验证） | 做 K 条目 | **K23**（复核 2026-12） | 历史文档数字断言缺证据，总数只降不升 |
| `release_manifest.py` | **`--only version` 05:59 rc=0（PASS 16 / FAIL 0）**；全量仍有 secrets 的 4 条带期限豁免 | 部分转绿 | **K24**（复核 2026-12） | 版本字面量 / tomli / 二进制凭据三类已分笔修；全量 secrets 仍靠 2026-12 豁免 |
| `secret_scan.py`（全树） | rc=1（BLOCK=4） | 带期限豁免 | **豁免至 2026-12**（安全 owner） | 4 个 BLOCK 全在仓库外 `D:\mbdsdr\output\`，钩子/产物都碰不到，只能人工删或轮换 |
| `check_dist_head_parity.py --dist` | 05:58 用当前 HEAD `cc2f366d` 新建 `pyi-dist17`（156.1s，4447 文件）仍 **rc=1：HEAD 有而 dist 无 34 个 / dist 有而 HEAD 无 0 / 哈希不一致 0** | 不是过期，是 **spec/datas 打包缺口** | 待发版人补 spec（2026-12 前） | 34 个＝LICENSE、NOTICE.md、desktop/assets/*、mbdsdr_ai/{VERSION,README.md,astronomy.py}、mbdsdr_ai/data/*、locale、web/static、third_party_licenses/*；同 spec 重建不会消失 |
| `make_decode_fixtures.py --check` | 04:47 rc=1 → 04:5x 连跑两次 rc=0 | 该修（他线同日修绿，未复现） | 无需账 | 两次复跑均 `PASS`，不下结论为可复现红 |
| `release_check.py --steps guards` | rc=1（仅因 `install_git_hooks`） | 同 `install_git_hooks` | 不重复计 | 汇总器，所辖 13 条已逐条登记 |

## 4. K23 收敛实测（2026-11-13 05:30–05:55，落库时引用）

基线：HEAD `8862b803`，`audit_evidence --classify` = **2703 无证据**（①573 / ②1878 / ③145 / ④48 / ⑤59）。

- **R1 文件级横幅**：12 笔提交、183 个文件（每批 ≤20，逐文件 claim；被占的
  `docs/reference-mechanisms.md` 跳过并点名）。**实测 2703 → 1349（−1354）**。
  与估算 −1291 的差额 +63 可完全分解：① 估算把横幅文件里的 241 条桶①记在 R0，
  实际横幅同样把它们转成「已标未验证」（+241）；② 按文件性质排除 162 条不横幅
  （hw-acceptance 79／nanovna README 56／evidence-standard 6／reference-mechanisms 21）（−162）；
  ③ 两个 pending-patch 的横幅被并行进程回写覆盖 16 条（−16）。+241−162−16 = +63。
- **块级标记**：18 个文件、26 处标记、3 笔提交 + 1 笔修正（`b6b2bb34`：表格
  标记后补空行，恢复「表头照旧跳过」，避免表头里的 `文件:符号` 被误记 E1、
  表头数字被算成断言）。第 4 批 experiments 的 nanovna README 有真实 data/
  工件，属桶③，不标。**实测 1349 → 905（−444）**：桶② −226、桶① −197、桶④ −21。
- **终值 905**，桶分布：①164 / ②640 / ③70 / ④25 / ⑤6。
  桶②残量前几名：`docs/performance-baseline.md` 116、
  `docs/COORDINATION.md` 40、`docs/parameter-unification.md` 33、
  `docs/pending-patches/2026-11-11-p0-roadmap-backfill-handoff.md` 29、
  `docs/hardcoded-parameters.md` 25、`docs/PRD.md` 22、
  `docs/settings-inventory.md` 21；其余 73 个文件各 ≤20 条
  （逐条清单见 `d:\mbdsdr\output\audit_fixed.json` 的 `rows[].bucket=="②"`）。

## 5. 落库时的协调规矩（2026-11-13 派单方明确）

`docs/code-guards.md` 是共享工作树，工作区里现在同时躺着**两段**待落库改动：

1. 凭据线的「**熵告警分级**」章节（`## 熵告警分级（2026-11-13，降噪）` 及其 5 个子节，
   含 `release_manifest.py` 从 B 档移到 A 档的表格行）；
2. 本文件的第 1–3 节（`check_red_lifecycle` 行改写 + 新增 `check_red_accounting` 行 +
   第 418–422 行现状句）。

**谁先拿到 claim，就把两段一起提交**：先 `git diff docs/code-guards.md` 看全，
确认「熵告警分级」章节与本文三处替换都在，再一笔提交；提交信息里分别写明
「熵告警分级（凭据线）」与「无账红守卫登记（守卫账本线）」。**不许只提交自己那段、
把别人那段留在工作区**——那会变成一笔「消息与内容不符」。

> 附录表里 K24 那行是 05:0x 的快照。截至 05:4x，版本字面量 / tomli / 二进制凭据
> 三类已修并分笔提交，逐项定性见
> `docs/pending-patches/2026-11-13-k23-k24-layering-and-verdicts.md`。

**合计**：非 0 守卫 9 条（汇总器同因不重复计）；已入账 1 条（K06）；新建 K 3 条
（K22/K23/K24）；带期限豁免 2 条（均 2026-12）；已修/自愈 3 条（verify_claims、
install_git_hooks、make_decode_fixtures）。另有 13 条守卫无法从当前仓库独立执行
（缺参数/硬件/GUI/钩子上下文），在 `docs/guard-accounting.json` 里逐条写明理由与
复核期限（2026-12 或 2027-01）。
