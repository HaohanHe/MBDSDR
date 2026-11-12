# git 历史凭据扫描：1 个不可达 blob 命中 PAT 形状（合成值），待决定是否清理

> 日期：2026-11-13；形态：**待决策，未执行任何清理或历史重写**
> 影响面：本仓库对象库（26 个 worktree / 25 个 worktree HEAD 共享同一对象库）
> 结论先说：**可达历史（`--all` + reflog + 各 worktree HEAD）命中 0 提交、0 路径**；
> 对象库里另有 1 个**不可达 blob** 命中 `github-pat-classic` 形状，但内容是合成值
> （36 位全同字符），无路径、无提交。**没有改写历史。**

## 一、扫描口径（「干净」是怎么判出来的）

命令：`python scripts/secret_scan.py --history`（工作目录 `D:\mbdsdr\MBDSDR`）。

| 项 | 值 |
| --- | --- |
| 判据 | `SHAPE_RULES` 7 条：GitHub classic / fine-grained PAT、GitLab PAT、Slack token、AWS access key、JWT、PEM 私钥块 |
| 不跑的规则 | 熵（全量历史 700+ MiB，熵告警没有信号，只会淹掉形状命中）；AST 的 argv/日志规则也不跑 |
| 豁免 | 行内 `# pragma: allowlist secret` 同样生效（逐行） |
| 扫描对象 | `git cat-file --batch-all-objects` 列出的**对象库全部 blob**，13 622 个 / 757.2 MiB |
| 二进制跳过 | 679 个（首 8 KB 含 NUL，不做文本扫描） |
| 非 blob | 0（只把 blob 喂给扫描器） |
| 归属索引 | `rev-list --objects --all --reflog <25 个 worktree HEAD>` + `git log --raw` 反向索引（blob → 引入它的提交） |
| HEAD 可达性 | 逐个命中提交跑 `git merge-base --is-ancestor <commit> HEAD`，不靠 `git log` 印象 |
| 输出纪律 | 只打印路径、行号、规则名、提交号；任何分支都不打印命中的值 |

为什么把 worktree HEAD 也加进归属索引：链接工作树处于 detached HEAD 时，该提交
不在任何 ref 下（`.git/worktrees/<name>/HEAD` 不是 ref），`git rev-list --all`
看不到它。只用 `--all` 会让「历史干净」这句话少一个口径。

## 二、三个数

| 问题 | 答案 |
| --- | --- |
| 命中的提交数 | **0**（可达历史）；全对象库也是 0——那 1 个命中 blob 无任何提交引用它 |
| 命中的路径集合 | **空**（该 blob 不在 HEAD 的任何路径，不在 103 个 unreachable commit 的 tree，也不在 303 个 unreachable tree 里） |
| 是否仍可从 HEAD 到达 | 不适用：没有命中提交可判断。`git cat-file --batch-all-objects` 能看到它，`rev-list --all --reflog` + 25 个 worktree HEAD 都看不到它 |

## 三、那 1 个 blob

| 项 | 值 |
| --- | --- |
| 对象 | `c20bd082d638…`（blob，已在 pack 里，非 loose） |
| 大小 / 行数 | 69 字节 / 2 行 |
| 命中 | `github-pat-classic` 1 处，token 总长 40（前缀 4 + 体 36） |
| 判别（不回显值） | token 体的 **36 个字符全部相同**——真实 GitHub PAT 是随机串，不可能 36 位全同；因此这是**合成/占位值** |
| 其他特征 | 含非 ASCII 字节；非 token 字符 27 个 |
| 是否等于当前 4 个 BLOCK 文件 | 不是。`git hash-object` 对比：`api_push.py`=`3c98fd3d…`、`ci_watch.sh`=`8d482aa1…`、`ci_watch71.sh`=`8ced1a01…`、`ci_watch72.sh`=`c58613e8…`，均不等于该 blob |
| 最可能来源 | 某次 `git add` 已把文件写进对象库、随后 `git reset` / 换分支，blob 留在共享对象库里再也无人引用（`git hash-object -w` 也会留下同样形态） |

`git fsck --unreachable`：103 个 unreachable commit、303 个 unreachable tree、
112 个 unreachable blob。该 blob 是其中唯一命中形状规则的。

## 四、两条路（代价写清，等你决定）

### 路 A：保留，不动历史也不 gc

- 代价：任何能读 `.git` 的人都能 `git cat-file` 取出这 69 字节；它不含真值，风险是
  「形似凭据的样本」被后来的人误判成真泄露，或反过来被当成"反正也不是真的"而放松警惕。
- 代价：以后每次 `--history` 都会稳定报这 1 条，需要一直用「36 位全同 = 合成值」
  这条判据解释，容易演变成噪音，进而被整体忽略。
- 好处：零风险、零不可逆操作；unreachable 的 103 个旧提交仍可 rescue。

### 路 B：`git gc --prune=now`（只清不可达对象，不改任何提交）

- 会删掉**全部** 103 个 unreachable commit / 303 tree / 112 blob，不只是这一个 blob。
  这些通常是 rebase/amend 前的旧提交——如果有人还想用它们 rescue，就没了。**不可逆**。
- 影响所有 worktree（共享对象库），但**不需要任何人重新克隆**，也不改提交号。
- 前提：确认没有线正在用 unreachable commit 做 bisect / rescue；`git stash` 是 ref，
  不受影响。
- 备选（更窄）：`git prune --expire=now` 同样只动不可达对象；无法只删这一个 blob
  （git 不支持按对象删除，除非 `repack -d` + 手工 `verify-pack` 过滤，代价远大于收益）。

### 路 C：万一这枚其实是真 PAT

当前证据不支持（36 位全同）。若你判断它是真值，那就不是清理问题，而是**轮换**：
该值从未进入任何可达提交，轮换后保留历史即可，不需要 `filter-repo`。

## 五、需要你决定

1. 选 **A（保留）** 还是 **B（gc 清理）**；选 B 的话我会先 `git fsck --unreachable`
   存档完整清单到 `docs/`，再执行 `git gc --prune=now`，并复跑 `--history` 验证归零。
2. 若你判断这枚是真值：按路 C 只做轮换，历史不动。

在得到决定前：不改对象库、不 gc、不重写历史；`--history` 的这 1 条命中保持可见。
