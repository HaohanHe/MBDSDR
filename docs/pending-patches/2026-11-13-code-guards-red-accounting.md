# 待落库：docs/code-guards.md 的两行守卫登记（2026-11-13）

**为什么不直接改**：`docs/code-guards.md` 当前被 `合规入口线/6ae85f56` 持有
（claim 2026-11-13 05:02:31 起，TTL 60min；pid 36100）。按本线约定，
被他人持有的文件不抢、不并发改；等持有人释放后由 owner 按下面的原文落库。

**落库位置**：`docs/code-guards.md` 第 389–399 行的 C 档登记表。

## 1. 替换 `check_red_lifecycle.py` 那一行（第 393 行）

原文：

```markdown
| `scripts/check_red_lifecycle.py` | `tests/test_red_lifecycle.py` | 仓库现状（27.3s，实测 rc=1：有到期/无证据红项） | 批次末手动；红项状态变更后 | 红项 owner | 2026-12 |
```

替换为：

```markdown
| `scripts/check_red_lifecycle.py` | `tests/test_red_lifecycle.py` | 仓库现状（27.3s，**不进 pre-commit**；2026-11-13 深夜实测 rc=0：`FAIL 0 / WARN 0 / SKIP 0`） | **触发条件**：① 每个批次收口前至少 1 次；② 任何 K 条目新增/改绑定/改状态后立即；③ 发版前必跑。**谁跑**：红项 owner（当前 `守卫账本线`）；谁改 K 账谁负责跑。 | 2026-12 |
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
已于 2026-11-13 深夜转绿（rc=0），触发条件见上表。
```

## 附录：2026-11-13 非 0 守卫扫描表（落库时可留在本文件或迁到 code-guards.md）

| 守卫 | 实测 | 判定 | 账/期限 | 依据 |
| --- | --- | --- | --- | --- |
| `verify_claims.py --check-lines --strict-lines` | 04:47 rc=1（1 条行号漂移）→ 05:01 rc=0 | 该修（已被他线 `80a1b873` 修掉） | 无需账 | 复跑 rc=0；唯一未豁免项是 `docs/reference-mechanisms.md:426` |
| `install_git_hooks.py --check` | rc=1（`.git/hooks/secret_scan.py` 与工作区不一致）→ 跑安装器后 rc=0 | 该修（本机跑一次安装） | 无需账 | 安装器已追平；`scripts/secret_scan.py` 仍被他线编辑，可能再次漂移 |
| `lint_swallow_all.py` | rc=1（越基线） | 已入账 | **K06** | 存量静默吞噬棘轮，113+ 处未修 |
| `check_destructive_ops.py` | rc=1（27 笔未审计） | 做 K 条目 | **K22**（复核 2026-12） | reflog 不能自证 no-op，需操作人逐笔补审计 |
| `audit_evidence.py` | rc=1（2701 条无证据 / 131 条已标未验证） | 做 K 条目 | **K23**（复核 2026-12） | 历史文档数字断言缺证据，总数只降不升 |
| `release_manifest.py` | rc=1（FAIL=5） | 做 K 条目 | **K24**（复核 2026-12） | 版本字面量 6 处 / tomli 未随包 / 4 个第三方二进制命中凭据形状 |
| `secret_scan.py`（全树） | rc=1（BLOCK=4） | 带期限豁免 | **豁免至 2026-12**（安全 owner） | 4 个 BLOCK 全在仓库外 `D:\mbdsdr\output\`，钩子/产物都碰不到，只能人工删或轮换 |
| `check_dist_head_parity.py --dist` | rc=1（dist16 缺 34 个当前文件） | 带期限豁免 | **豁免至 2026-12**（发版人） | 现有 `.tmp/pyi-dist16` 是 `6b19aa89` 旧包，非当前 HEAD；出下一个包后重跑 |
| `make_decode_fixtures.py --check` | 04:47 rc=1 → 04:5x 连跑两次 rc=0 | 该修（他线同日修绿，未复现） | 无需账 | 两次复跑均 `PASS`，不下结论为可复现红 |
| `release_check.py --steps guards` | rc=1（仅因 `install_git_hooks`） | 同 `install_git_hooks` | 不重复计 | 汇总器，所辖 13 条已逐条登记 |

**合计**：非 0 守卫 9 条（汇总器同因不重复计）；已入账 1 条（K06）；新建 K 3 条
（K22/K23/K24）；带期限豁免 2 条（均 2026-12）；已修/自愈 3 条（verify_claims、
install_git_hooks、make_decode_fixtures）。另有 13 条守卫无法从当前仓库独立执行
（缺参数/硬件/GUI/钩子上下文），在 `docs/guard-accounting.json` 里逐条写明理由与
复核期限（2026-12 或 2027-01）。
