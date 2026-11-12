# 历史重写 SHA 映射（旧 SHA → 本仓等价提交）

> 用途：本仓主分支历史经过 reset 改写（见 `docs/COORDINATION.md` §6.10），
> 部分文档里的旧提交 SHA 在本仓不存在、但仍能在上游对象库查到。遇到这类
> SHA 不要直接删引用，也不要改成一个"看起来合理"的日期；先在本表查等价提交。
>
> 判定标准只有一条：**旧 SHA 的 tree 与本地提交的 tree 逐字符相同**。
> 仅 message/日期相同不算等价。

## 1. 已取证（tree 相同）

| 旧 SHA | 本仓是否存在 | 本地等价提交 | 证据（tree 相同） |
|---|---|---|---|
| `469e48e` | 否（`git cat-file -t` 报 not a valid object） | `462ac87c` | 上游 tree `880223011ea0aadbd464961c6d1d9b0d1c211baf` = 本地 `git rev-parse 462ac87c^{tree}`，逐字符一致 |
| `ae0effb` | 否 | `a752f1bc` | 上游 tree `91d85c25874d7dea40b8be1eee7fc5464db1666f` = 本地 `git rev-parse a752f1bc^{tree}`，逐字符一致 |
| `bda5e48` | 否 | `ae7a5b75` | 上游 tree `024d95ab909bfa856e50fe59c5b9c40f71703250` = 本地 `git rev-parse ae7a5b75^{tree}`，逐字符一致 |
| `6a7ad7b` | 否 | `6b6287a8` | 上游 tree `077dfdf9a996afc01c283b0e0ead1f92f584f9c9` = 本地 `git rev-parse 6b6287a8^{tree}`，逐字符一致 |

复核命令（本表 2026-11-13 实测）：

```powershell
git cat-file -t <旧SHA>                 # 本仓：不存在即报 not a valid object
git rev-parse <本地等价提交>^{tree}      # 本地 tree
# 上游：GET https://api.github.com/repos/HaohanHe/MBDSDR/commits/<旧SHA>
#       -> .commit.tree.sha
```

## 2. 待证（上游存在、本地不存在；等价 tree 尚未比对）

| 旧 SHA | 本仓是否存在 | 本地等价提交 | 证据 |
|---|---|---|---|
| `d82247f` | 否 | 待证 | 上游存在：feat(phase8) 真机回填一键闭环…，2026-10-02，tree `56ef9dab843019ad9de2511bd02dfa3505e5e08c`；本地等价 tree 未比对 |
| `aabc309` | 否 | 待证 | 上游存在：docs: readback-loop audit…，2026-10-09，tree `a36d57b770a16086c9d92c51bc65aadf74842865`；本地等价 tree 未比对 |
| `9efbdcb` | 否 | 待证 | 上游存在：ui: right-rail tab visibility…，2026-10-08，tree `dca3a8208411b96f73c35af62c075d822f6bef1a`；本地等价 tree 未比对 |

待证条目的当前所在位置（只登记，不改动）：

- `d82247f`：`docs/learn/phase9/_PHASE9_SPEC.md:3`（写作「远端 main = d82247f」）
- `aabc309`：`docs/learn/phase63/no-engine-readonly-audit.md:5`、`:192`（写作「HEAD」）
- `9efbdcb`：`docs/learn/phase63/tool-golden-47-check.md:7`（写作「上轮基线 HEAD」）

## 3. 待改引用点（不在本次 A11 范围内，留给对应线）

共 6 个文件、8 行（`readonly-tools-audit.md` 与 `rx-stats-and-freeze-recon.md` 各 2 行）。
逐文件 claim 后按行改，不要整文件批量替换；改前先用 `git cat-file -t` 复核旧 SHA。

| 位置 | 旧 SHA | 建议改为 | 备注 |
|---|---|---|---|
| `docs/learn/phase11/_PHASE11_SPEC.md:4` | `469e48e` | `462ac87c` | tree 已证相同 |
| `docs/learn/phase63/waterfall-scroll-pause.md:5` | `ae0effb` | `a752f1bc` | tree 已证相同 |
| `docs/learn/phase63/readonly-tools-audit.md:3` | `bda5e48` | `ae7a5b75` | tree 已证相同 |
| `docs/learn/phase63/readonly-tools-audit.md:185` | `bda5e48` | `ae7a5b75` | tree 已证相同 |
| `docs/learn/phase63/step-depth-marker-recon.md:3` | `bda5e48` | `ae7a5b75` | tree 已证相同 |
| `docs/learn/phase63/gate-semantics-audit.md:6` | `6a7ad7b` | `6b6287a8` | tree 已证相同 |
| `docs/learn/phase63/rx-stats-and-freeze-recon.md:4` | `6a7ad7b` | `6b6287a8` | tree 已证相同 |
| `docs/learn/phase63/rx-stats-and-freeze-recon.md:88` | `6a7ad7b` | `6b6287a8` | tree 已证相同 |

## 4. 已处理记录

2026-11-13 的 A11 文档修复已改完下列位置（本表只做索引，判据与处置见对应提交信息）：

- `docs/learn/phase11/P3-ux-walkthrough.md:6`：`469e48e` → `462ac87c`
- `docs/learn/phase63/http-endpoint-contract.md:4`（关联 `:6`）：`ae0effb` → `a752f1bc`
- `docs/learn/phase63/no-engine-readonly-audit.md:180`（关联 `:179`）：`bda5e48` → `ae7a5b75`
- `docs/learn/phase63/tool-golden-47-check.md:3`、`:81`：`6a7ad7b` → `6b6287a8`
