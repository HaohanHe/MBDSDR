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
| ⑤ | 私有 index 提交的副作用（反向暂存） | 提交成功后 `git status` 仍是本笔文件 staged（`MM`） | 1) `python scripts/git_sync_index.py --paths <files>`；2) `--check` 复核 `staged=0`；3) 不一致就 `--all` | [§6.9.5](./COORDINATION.md#L1082) |
| ⑥ | 私有 index 文件名撞车 | 提交夹带别线文件；index 用了固定名/复用旧名 | 1) 换唯一名（会话标识 + 时间戳）；2) `read-tree HEAD` 起底；3) 只 add 自己的文件后重提 | [§6.9.6](./COORDINATION.md#L1125) |
| ⑦ | PowerShell 环境变量没落到子进程 | 闸门打印 `INDEX 实际读取 .git/index`，而声明的是私有 index | 1) 停；2) 用 `GIT_INDEX_FILE=<path> git commit ...` 或 `subprocess.run(env=...)`；3) 复核 `INDEX 实际读取` 与 `INDEX 声明` 同路径 | [§6.9.7](./COORDINATION.md#L1152) |
| ⑧ | 陈旧私有 index 制造幻影删除 | `git diff --cached --name-status` 出现 `D`，但不是你要删的文件；闸门报 `DELETE` | 1) 停；2) `git read-tree HEAD` 重建私有 index；3) 重新 add 自己的文件，确认暂存里没有 `D` | [§6.9.8](./COORDINATION.md#L1178) |

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
