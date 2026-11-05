# 提交事故速查卡（8 种形态）

> 出事 30 秒内照着做。全文与取证细节见 [COORDINATION.md](./COORDINATION.md) 第 6 节。
> 三条底线：**不 reset**（移动 main 一律禁止）；**私有 index 显式传参**（不靠 shell 变量）；
> **共享 index 必须干净**（`git diff --cached --name-only` 为空且 `ls-files -s` == `ls-tree HEAD`）。

| # | 形态 | 30 秒认出（看到什么） | 处置（≤3 步） | 章节 |
|---|---|---|---|---|
| ① | 多带别人的 | pre-commit 报 `MULTI` / `EXTRA <path>`；或提交文件集超出自己的 claim 清单 | 1) 停，别 push；2) 用私有 index 只装自己的 hunks（`read-tree HEAD` 后逐条 add）；3) 重提，`--expect` 只写自己的清单 | [§6.9](./COORDINATION.md#L898) |
| ② | 漏带自己的 | 提交后 `git show --stat` 比预期少文件/少 hunk | 1) 停；2) **补一笔新提交**（不 reset、不改写历史）；3) 在台账记一笔 | [§6.9](./COORDINATION.md#L898) |
| ③ | 陈旧 index 回退已入库内容 | 提交后 `diff HEAD~1 HEAD -- <file>` 出现行数减少；post-commit 报 `STALE_INDEX_SUSPECT` | 1) 停；2) 逐文件确认丢了哪些 hunk，`git revert` 或补回；3) 重建私有 index 再提交 | [§6.9.3](./COORDINATION.md#L1030) |
| ④ | 回退事件（共享文档被改回旧版） | 共享文档出现整段删除，且不是本笔意图；blob 回环 | 1) `git branch rescue/<short> <hash>` 保命；2) `git revert` 恢复；3) 报主会话（时间/目标/被移出 hash） | [§6.10.2](./COORDINATION.md#L1370) |
| ⑤ | 私有 index 提交的副作用（反向暂存） | 提交成功后 `git status` 仍是本笔文件 staged（`MM`） | 1) 在仓库根目录 `python scripts/git_sync_index.py --paths <files>`；2) `--check` 复核 `staged=0`；3) 不一致才 `--all` | [§6.9.5](./COORDINATION.md#L1082) |
| ⑥ | 私有 index 文件名撞车 | 提交夹带别线文件；index 用了固定名/复用旧名；闸门报 `EXTRA` | 1) 坏提交已入库先 `git revert <hash>`（未入库跳过）；2) 换唯一名（会话标识 + 时间戳）+ `read-tree HEAD` 重建；3) 只 add 自己的文件，带 `--expect` 提交并复核 `EXTRA` 为空 | [§6.9.6](./COORDINATION.md#L1125) |
| ⑦ | PowerShell 环境变量没落到子进程 | 闸门打印 `INDEX 实际读取 .git/index`，而声明的是私有 index | 1) 停；2) 用 `GIT_INDEX_FILE=<path> git commit ...` 或 `subprocess.run(env=...)`；3) 复核 `INDEX 实际读取` 与 `INDEX 声明` 同路径 | [§6.9.7](./COORDINATION.md#L1152) |
| ⑧ | 陈旧私有 index 制造幻影删除 | `git diff --cached --name-status` 出现 `D`，但不是你要删的文件；闸门同时报 `DELETE` 与 `EXTRA` | 1) 停；2) 在**同一份私有 index** 上 `git read-tree HEAD` 重建；3) 重新 add 自己的文件，确认暂存里没有 `D` | [§6.9.8](./COORDINATION.md#L1178) |

## 取证三件套（任何形态都先留证）

```
git status --porcelain
git diff --cached --name-status --no-renames
git rev-parse --git-path index        # 实际读到的 index 路径
```

- 提交前：`python scripts/check_commit_scope.py --staged --expect <自己的清单>`（多了就拒）。
- 提交后：`python scripts/git_sync_index.py --check`（共享 index 必须与 HEAD 一致）。
- 怀疑丢内容：**不要只查 reflog**——陈旧 index 回写旧 blob 不经 reset，reflog 一条也看不到；
  按 `blob[i] == blob[i+2] && blob[i+1] != blob[i]` 的 blob 回环判据扫。
- 卡片防漂移：`python scripts/check_card_links.py`（每个 `§x` 直链必须指到真实章节标题）；
  把锚点改错即红。破坏性操作走 `python scripts/git_safe.py <reset|push> ...`（无 reason 即拒并留痕）。

**自测记录（2026-11-06 03:05，形态⑥「私有 index 文件名撞车」，临时仓）**：认出 → 照卡 3 步完成，用时 **0.9s**。
卡点：第 3 步原写「重提」，但闸门拦下时并没有已入库的坏提交（应为「提交」），且已入库的坏提交要先 `git revert`
卡片没写——已据此改写第 6 行（改为「坏提交先 revert / 未入库跳过」+「带 `--expect` 提交」）。

**自测记录（2026-11-06 03:12，形态⑤「反向暂存」，临时仓）**：私有 index 提交后 `git status` 实测 `MM a.txt`；
照卡 `--paths` → `--check` 两步，**2.1s**，暂存清零。卡点：`git_sync_index.py` 必须在仓库根目录跑
（原文未写明），已补「在仓库根目录」。

**自测记录（2026-11-06 03:12，形态⑧「幻影删除」，临时仓）**：按旧 HEAD 建索引 + 别人新提交 3 文件 →
闸门实测 `DELETE c.txt/d.txt/e.txt` + `EXTRA`，**认出 1.5s**；照卡 `read-tree HEAD` 重建后暂存只剩 `M a.txt`，
gate 退出 0。卡点：原第 2 步没写「在同一份私有 index 上重建」（壳里变量没落地时会改错 index），已补。
