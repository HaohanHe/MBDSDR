# 事故速查卡（16 种形态）

> 出事 30 秒内照着做。全文与取证细节见 [COORDINATION.md](./COORDINATION.md) 第 6 节。
> ①–⑫ 是提交事故形态；⑬ 是真机测量窗口误判（**占用表为空 ≠ 无人占**）；
> ⑭ 是私有 index 回写覆盖工作区；⑮ 是 claim 交接误判（**pid 已死 ≠ 可接管**）；
> ⑯ 是分步编辑的中间态窗口（**有活跃 claim ≠ 文件可运行**）。
> 三条底线：**不 reset**（移动 main 一律禁止）；**私有 index 显式传参**（不靠 shell 变量）；
> **共享 index 必须干净**（`git diff --cached --name-only` 为空且 `ls-files -s` == `ls-tree HEAD`）。

| # | 形态 | 30 秒认出（看到什么） | 处置（≤3 步） | 章节 |
|---|---|---|---|---|
| ① | 多带别人的 | pre-commit 报 `MULTI` / `EXTRA <path>`；或提交文件集超出自己的 claim 清单 | 1) 停，别 push；2) 用私有 index 只装自己的 hunks（`read-tree HEAD` 后逐条 add）；3) 重提，`--expect` 只写自己的清单 | [§6.9](./COORDINATION.md#L2531) |
| ② | 漏带自己的 | 提交后 `git show --stat` 比预期少文件/少 hunk | 1) 停；2) **补一笔新提交**（不 reset、不改写历史）；3) 在台账记一笔 | [§6.9](./COORDINATION.md#L2531) |
| ③ | 陈旧 index 回退已入库内容 | 提交后 `diff HEAD~1 HEAD -- <file>` 出现行数减少；post-commit 报 `STALE_INDEX_SUSPECT` | 1) 停；2) 逐文件确认丢了哪些 hunk，`git revert` 或补回；3) 重建私有 index 再提交 | [§6.9.3](./COORDINATION.md#L2663) |
| ④ | 回退事件（共享文档被改回旧版） | 共享文档出现整段删除，且不是本笔意图；blob 回环 | 1) `git branch rescue/<short> <hash>` 保命；2) `git revert` 恢复；3) 报主会话（时间/目标/被移出 hash） | [§6.10.2](./COORDINATION.md#L3329) |
| ⑤ | 私有 index 提交的副作用（反向暂存） | 提交成功后 `git status` 仍是本笔文件 staged（`MM`） | 1) 在仓库根目录 `python scripts/git_sync_index.py --paths <files>`；2) `--check` 复核 `staged=0`；3) 不一致才 `--all` | [§6.9.5](./COORDINATION.md#L2715) |
| ⑥ | 私有 index 文件名撞车 | 提交夹带别线文件；index 用了固定名/复用旧名；闸门报 `EXTRA` | 1) 坏提交已入库先 `git revert <hash>`（未入库跳过）；2) 换唯一名（会话标识 + 时间戳）+ `read-tree HEAD` 重建；3) 只 add 自己的文件，带 `--expect` 提交并复核 `EXTRA` 为空 | [§6.9.6](./COORDINATION.md#L2758) |
| ⑦ | PowerShell 环境变量没落到子进程 | 闸门打印 `INDEX 实际读取 .git/index`，而声明的是私有 index | 1) 停；2) 用 `GIT_INDEX_FILE=<path> git commit ...` 或 `subprocess.run(env=...)`；3) 复核 `INDEX 实际读取` 与 `INDEX 声明` 同路径 | [§6.9.7](./COORDINATION.md#L2785) |
| ⑧ | 陈旧私有 index 制造幻影删除 | `git diff --cached --name-status` 出现 `D`，但不是你要删的文件；闸门同时报 `DELETE` 与 `EXTRA` | 1) 停；2) 在**同一份私有 index** 上 `git read-tree HEAD` 重建；3) 重新 add 自己的文件，确认暂存里没有 `D` | [§6.9.8](./COORDINATION.md#L2811) |
| ⑨ | 改测试销红 | 同一用例 HEAD 稳定红、工作区绿；`git diff HEAD -- <test>` 非空，但产品代码没动 | 1) `python scripts/check_test_version.py <test>`；2) 报「被测版本不同，绿的结论无效」即拒收绿结论；3) 回 HEAD 版本，或单独提交测试修正（写清改断言理由），产品与断言不得混提 | [§6.9.9](./COORDINATION.md#L2834) |
| ⑩ | 私有 index + `git commit -- <paths>`（工作区内容覆盖 index） | `--post-commit` 报 `FAIL <path>`，`HEAD <blob>` 与 `INDEX <blob>` 不等；或 `git diff --cached` 只有自己的 hunk、提交却多带一行 | 1) 停，不 reset、不改写历史；2) 该行归属线补正/登记，机制线不重复改文案；3) 卡片登记 + `python scripts/git_sync_index.py --check`。**`git commit -- <path>` 会连工作区里别人的同文件改动一起提交，hunk 级选择必须用私有 index** | [§6.9.10](./COORDINATION.md#L2856) |
| ⑪ | 私有 index 的基线不是当时的 HEAD（复用陈旧 index / 他线毫秒级连提两笔） | 闸门报 `EXTRA`（他线文件被带入）或幻影 `DELETE`；`git rev-parse HEAD` 与建 index 时记下的基线不等 | 1) 停，不 push；2) 直接用 `python scripts/safe_commit.py --files <清单> --message-file <msg>`——一个命令做完「当时的 HEAD 建 index → 校验文件集 → 重校验基线 → 提交」，撞锁自动用新 HEAD 整盘重建（默认 3 次），任何一步不满足就退出且不提交；退出码 3 = 已提交但 blob 复核不一致，按形态⑩处理；3) 手工路径：同一份 index 上 `read-tree HEAD` 重建 + 重新 add + 提交前再比一次 HEAD | [§6.9.11](./COORDINATION.md#L2903) |
| ⑫ | 私有 index 的环境变量泄漏（`GIT_INDEX_FILE` 被会话级导出，别的会话写进同一 index） | `$env:GIT_INDEX_FILE` 指向共享/固定路径；`git status` 出现不属于自己的文件；钩子报 `EXTRA`+`MULTI` | 1) 停，清掉当前会话的 `GIT_INDEX_FILE`；2) 改一次性驱动，`GIT_INDEX_FILE` 只进子进程 `env`；3) 用唯一路径 `read-tree` 重建，提交前校验基线与自己的文件清单 | [§6.9.12](./COORDINATION.md#L2943) |
| ⑬ | 空占用表被当无人占（真机测量窗口误判，非提交类） | `§6.1` 只有模板行；`Get-Process python` 里有 `deadscan.py` 等单核满载；PnP 再正常也已被污染 | 1) 停，按 §6.1 三查（`file_claim.py status` + `Get-Process` + PnP）；2) 命中重负载 → 等其跑完或 `SKIP(NEEDS_OPERATOR)`，不 kill 他人进程；3) 三条件都满足才开跑，占用行写不进去就报主会话 | [§6.1](./COORDINATION.md#L1854) |
| ⑭ | 私有 index 提交前把 blob 写回工作区，覆盖他人未提交内容 | 提交前有没有 `Write`/重定向写过任何工作区文件；提交后 `git log -S` 对丢失内容零命中、`git status` 里别人的改动变少 | 1) 立即停写；2) 查 `git log -S` 与本地历史，记录丢失文件/行数与最后可见版本；3) 由原作者按会话记录重放，并在重放节首标「非原始文本」；不要假装恢复成功 | [§6.9.14](./COORDINATION.md#L2975) |
| ⑮ | 「pid 已死」被当成「可接管」（claim 交接误判） | `file_claim.py status` 里 `pid=…(已不存在)`，但 TTL 未归零且 `git status --porcelain -- <path>` 有改动 | 1) 停；2) 看 `held/剩余 TTL` 与 `git status --porcelain -- <path>`：两者都 FAIL 就等 TTL 或让持有方 `release`；3) 只有「pid 死 +（过期 或 无在途）」才 takeover，reason 写可复核证据；绝不 `checkout`/覆盖 | [§6.9.15](./COORDINATION.md#L3002) |
| ⑯ | 分步编辑的中间态窗口（先加调用点、后落方法体） | `git status` 只有 `M`，但测试在 **setup** 阶段报 `AttributeError`/`NameError`；`py_compile` 过、导入却缺符号 | 1) 先确认是不是别人的编辑窗口（`git status --porcelain -- <path>`）；2) `python -m py_compile <file>` + 一次最小导入确认；3) 不是自己的缺陷就别改代码——等持锁方落完、或在 `git archive HEAD` 干净树上重跑 | [§6.9.16](./COORDINATION.md#L3023) |

## 取证三件套（任何形态都先留证）

```
git status --porcelain
git diff --cached --name-status --no-renames
git rev-parse --git-path index        # 实际读到的 index 路径
```

- 提交前：`python scripts/check_commit_scope.py --staged --expect <自己的清单>`（多了就拒）。
- 提交后：`python scripts/check_commit_scope.py --post-commit HEAD --declared-index <私有 index>`（pathspec 覆盖 index 即拒，打印两侧 blob）；再 `python scripts/git_sync_index.py --check`（共享 index 必须与 HEAD 一致）。
  **前提（按 HEAD 计）**：需 HEAD 已含 `check_commit_scope.py --post-commit` 与
  `COORDINATION.md` §6.9.10–§6.9.12；否则该命令跑不通、锚点也对不上。本条命令与锚点均按
  HEAD 计，工作区跑出的 PASS 不算通过。
- 怀疑丢内容：**不要只查 reflog**——陈旧 index 回写旧 blob 不经 reset，reflog 一条也看不到；
  按 `blob[i] == blob[i+2] && blob[i+1] != blob[i]` 的 blob 回环判据扫。
- 卡片防漂移：`python scripts/check_card_links.py`（每个 `§x` 直链必须指到真实章节标题）；
  把锚点改错即红。破坏性操作走 `python scripts/git_safe.py <reset|push> ...`（无 reason 即拒并留痕）。
  **方法论：以工作区计算的 PASS 不算通过。** 锚点类检查必须写明是按哪个 HEAD 算的，
  并在该 HEAD 的干净快照上复跑（如 `git archive HEAD <docs/scripts> | tar -x -C <tmp>` 后执行）；
  工作区含他线在途改动时，行号会漂，PASS 只说明「对当前工作区成立」，不是完成态。

**自测记录（2026-11-06 03:05，形态⑥「私有 index 文件名撞车」，临时仓）**：认出 → 照卡 3 步完成，用时 **0.9s**。
卡点：第 3 步原写「重提」，但闸门拦下时并没有已入库的坏提交（应为「提交」），且已入库的坏提交要先 `git revert`
卡片没写——已据此改写第 6 行（改为「坏提交先 revert / 未入库跳过」+「带 `--expect` 提交」）。

**自测记录（2026-11-06 03:12，形态⑤「反向暂存」，临时仓）**：私有 index 提交后 `git status` 实测 `MM a.txt`；
照卡 `--paths` → `--check` 两步，**2.1s**，暂存清零。卡点：`git_sync_index.py` 必须在仓库根目录跑
（原文未写明），已补「在仓库根目录」。

**自测记录（2026-11-06 03:12，形态⑧「幻影删除」，临时仓）**：按旧 HEAD 建索引 + 别人新提交 3 文件 →
闸门实测 `DELETE c.txt/d.txt/e.txt` + `EXTRA`，**认出 1.5s**；照卡 `read-tree HEAD` 重建后暂存只剩 `M a.txt`，
gate 退出 0。卡点：原第 2 步没写「在同一份私有 index 上重建」（壳里变量没落地时会改错 index），已补。
