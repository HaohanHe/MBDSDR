# `safe_commit.py` 退出码

一份给人看的表：撞上哪个码，先判断**是谁的问题**，再照「正确处置」做。
工具的行为细节（私有 index、声明快照、排除 hunk）见脚本模块文档；这里只讲
「拿到码之后怎么办」。

判断「是不是提交成功了」只看一件事：**退出码 0 = 已提交（`--declare` 除外）**。
1/2/4/5/6/7 都**没有提交**，工作区与 index 保持原样；**3 是已提交但复核失败**，
按事故处理，不要重跑。

<!-- exit-codes:start -->
| 码 | 含义 | 是谁的问题 | 正确处置 | 不该怎么做 |
| --- | --- | --- | --- | --- |
| 0 | 成功。提交时为「已提交 + 逐文件 blob 复核一致」；带 `--declare` 时为「声明快照已记录」（**没有提交**） | 无 | 提交成功就继续；`--declare` 的 0 之后紧接着做写入/排除 | 不要把 `--declare` 的 0 当成「已提交」；声明之后别隔很久才提交（窗口越长漂移越多） |
| 1 | 前置条件不满足，**未提交**：参数或路径格式错、`read-tree` 失败、`git add` 的**永久**失败（路径不存在、权限不足、在仓库外——这类只试一次，不再重试）、声明快照读不出、`--exclude-hunk` 范围对不上任何改动、文件集不符（多/缺）、提交锁超时、撞 ref 锁、`git commit` 被拒、消息文件读不到或落在 `.git/` 里 | 多数是**我的清单错了**；撞 ref 锁、提交锁超时是**环境脏** | 读上一行打印的具体原因：文件集不符就改 `--files` 清单或补 `--exclude-hunk`；撞锁直接重跑同一条命令 | 不要 `--no-verify` 绕过；不要手工 `git add` 之后用普通 `git commit` 交差；看到「永久错误，不重试」就别再原样重跑 |
| 2 | 重试耗尽，**未提交**（内部已用新 HEAD 整盘重建 index 试了 `--retries` 次；`git add` 的**瞬时**失败——索引锁冲突、并发改名——也走这里）；**同时也是 argparse 参数用法错误的码**（如缺 `--message-file`），那种情况在参数解析阶段就退出，看不到任何 `[files]` 行 | 环境脏（别人在连续提交）或我的命令行写错 | 先看输出里有没有 `[files]` 行：没有就是参数写错；有就是世界一直在动，停下来问清谁在连续提交 | 不要把 2 当成「工具坏了」去改脚本；不要加大 `--retries` 硬顶——只是把撞锁窗口拉长 |
| 3 | **已提交**（未实测：构造它要写一笔作用域错误的提交，会污染历史，故未实测），但提交后复核失败：父提交不是本次基线、实际改动路径与 `--files` 不符、落库消息与私有副本不一致、HEAD blob 与暂存时不一致 | **工具失效 / 共享 index 被绕过**（有人手敲 `git commit`），不是我的清单 | 按事故卡处理：`git show --stat HEAD` 核对作用域，必要时 `git revert`，保留打印出的私有消息副本供复盘 | 不要把它当「没提交」重跑（会叠第二笔）；不要继续叠加提交把痕迹盖住 |
| 4 | 声明后内容漂移，**未提交**、不重试 | **别人的在途内容**（或我声明后又改了自己的文件） | 看报告里新增/消失的行属于谁：重新 `--declare` 再提交；不属于本笔的行用 `--exclude-hunk` 排掉 | **不要用 `--allow-foreign-drift` 一把梭**——它解决不了漂移，那是「已当面核对、明确要一起带走」的留痕手段，不是消警报的开关；也不要用它去开退出码 8 那道闸 |
| 5 | §6.1 缺登记行，**未提交**、不重试 | **我的清单错了**（漏登记） | 粘贴它打印的 `append_coordination_row.py` 命令，然后原样重跑同一条提交命令 | 不要手工往暂存区塞行；不要为了绕过而把整份 `docs/COORDINATION.md` 加进 `--files` |
| 6 | 消息源在提交前被改写，**未提交**、不重试（未实测：要抢「启动后、提交前」改写消息文件的窗口，无法稳定复现，按代码路径如实列出） | **环境脏**（两个进程共用同一个 `--message-file`） | 每条线各用一个私有消息路径，重写消息后重跑；本进程私有副本会保留下来 | 不要用 `.git/COMMIT_EDITMSG` 或任何全仓共享路径当消息源（会被直接拒绝）；不要无视这条硬重试 |
| 7 | 用了 `--exclude-hunk` 或 `--exclude-owner` 却没配 `--declare`，**未提交**、不重试 | **我的清单错了**（漏了声明这一步） | 先在同一文件集上跑 `--declare`，再执行原来的提交命令 | 不要重新算一个「能过」的行号范围——行号是坐标，坐标只在被计算的那一刻有效；`--exclude-owner` 只给线名，范围由工具在提交时现算 |
| 8 | 声明前就已经在工作区的他线改动会被带入本笔，且既没排除也没留痕，**未提交**、不重试。`--declare` 已按「N 个文件 / M 个块 / 共 K 行」列过清单 | **别人的在途内容**（或我声明前没看清单） | 按清单逐块处理：普通文件用 `--exclude-hunk <path>:<start-end>`，§6.1 登记面用 `--exclude-owner "<线名>"`；确认要一起带走就 `--allow-foreign-before-declare "<理由>"`，理由与块清单会写进说明的 `foreign-before-declare` 段 | 不要重新 `--declare` 想把清单「洗」掉——新快照只会把那些块记成新的基线，清单照样在；不要 `--no-verify` 跳过；不要拿 `--allow-foreign-drift` 来开这道闸（它只管声明后漂移） |
<!-- exit-codes:end -->

## 标准顺序

```text
# 1. 钉住声明快照（写入/排除之前做）
python scripts/safe_commit.py --files <清单> --declare
#    看 [foreign] 那行：非空就按清单决定「排除」还是「留痕」
# 2. 要排别人的行就先排除，再提交；消息用本线私有路径
python scripts/safe_commit.py --files <清单> \
    --exclude-hunk <path>:<start-end> --exclude-owner <对方线名> \
    --message-file <本线私有 msg>
```

## 声明前 / 声明后：两把锁不重叠

- **声明之前**已在工作区的他线内容：`--declare` 会列出「N 个文件 / M 个块 /
  共 K 行」，写进声明快照；提交时仍会被带入就拒绝（**退出码 8**）。归属按台账
  推断——登记面逐行读 §6.1 线名，普通文件按活跃 claim 的线名；没人占的文件没有
  「他线」证据，不算别人的；解析不出线名的行标「无归属」。清单只比 `--files`，
  不扫全仓。
- **声明之后**冒出来或消失的内容：由 ⑰ 的时间窗比对，不一致就是**退出码 4**。
- 两者**不重叠，也不互相替代**：重跑 `--declare` 只能把「声明后的漂移」清零，
  不会让退出码 8 的块消失（它们只是换了一份基线）；反过来，排掉声明前的块也
  挡不住声明后的新漂移。只做其中一个，另一半照样漏——`8982b4f9` /
  `052828d1` / `ed3c09e5` 就是同一形态反复出现。
- 处置入口只有两个：**排除**（普通文件 `--exclude-hunk`，登记面
  `--exclude-owner`）或**留痕**（`--allow-foreign-before-declare "<理由>"`，
  理由与块清单进提交说明的 `foreign-before-declare` 段）。既不排除也不声明 → 拒绝。
- **两个豁免各管一道闸，理由不许共用**（2026-11-14）：`--allow-foreign-drift`
  只开 ⑰（退出码 4，声明后漂移），`--allow-foreign-before-declare` 只开 ㉑
  （退出码 8，声明前已在工作区的他线块）。用错开关，另一半照样拒绝。
  `1a092cf8` 的教训：同一个开关两道闸全开，说明里写「空行块原文误报」，实际
  带走了 `mbdsdr_ai/bookmark_manager.py` 的一条登记行——理由与内容对不上。

## 自证不漂

`tests/test_safe_commit_exit_codes.py` 从 `scripts/safe_commit.py` 里抓出所有
`return <N>` / `sys.exit(<N>)` 的码，与上面表格里的码集合**双向比对**：代码里多出
一个码而表里没写要红，表里写了一个代码里没有的码也要红。改码必须同时改这张表。

`git add` 失败的分流另有判据用例 `tests/test_safe_commit_add_errors.py`：
永久错误 → 退出码 1 且只出现 `[try 1]`；瞬时错误 → 每轮重试、耗尽归 2。
反证：把 `_attempt` 里的分流去掉（一律按瞬时重试），前一条用例转红（退出码变 2）。

退出码 8 的判据用例在 `tests/test_safe_commit_foreign_at_declare.py`：声明前已有
他线块 → 拒绝并点名；显式 `--allow-foreign-before-declare` → 通过且
`foreign-before-declare` 段可查；排除后 → 该块不进本笔。反证：把拒绝分支改成
放行，第一条必须转红。

两豁免不共用另有用例：同一份「声明后漂移」现场，`--allow-foreign-before-declare`
必须仍判退出码 4；同一份「声明前他线块」现场，`--allow-foreign-drift` 必须仍判
退出码 8（各在自己的测试文件里）。

## 同因两码：先指出来，不合并

- **1 与 2 同源**：「世界在提交窗口里动了」（撞 ref 锁、基线变化、提交锁超时）。
  每次尝试返回 1，耗尽后才归成 2。但 1 还被大量**非重试**原因共用（清单错、
  范围对不上），所以看到 1 不能直接当成「再试一次就好」，要看具体那一行。
- **1 与 2 的新边界（`git add` 失败按错误类型分流，2026-11-14 裁定）**：
  - **永久错误 → 1，只试一次**：路径不存在（`pathspec ... did not match any files`）、
    权限不足（`Permission denied`）、在仓库外（`is outside repository`）。这类
    重试三次也不会变好，归到 2 会把人送去查环境，而真正该做的是改清单。
  - **瞬时错误 → 重试，耗尽归 2**：索引锁冲突（`Unable to create ... index.lock`、
    `File exists`）、并发改名（`cannot lock ref`、`another git process`、
    `No such file or directory`）。判据在 `safe_commit._transient_add_error`，
    白名单之外一律按永久处理。
  - 所以「1 可重试」这句话从此**只对部分原因成立**：看到「永久错误，不重试」
    就不要再原样重跑；看到「瞬时失败（可重试）」才是等一轮再试。
- **2 与 argparse 的用法错误撞码**：参数写错时 argparse 自己以 2 退出，与「重试
  耗尽」同码不同因。这是反向问题（两因一码），合并/拆分都会改掉各线现有的处置
  习惯，先记录在案，不在本次改动里动它。

## 自证不漂

`tests/test_safe_commit_exit_codes.py` 从 `scripts/safe_commit.py` 里抓出所有
`return <N>` / `sys.exit(<N>)` 的码，与上面表格里的码集合**双向比对**：代码里多出
一个码而表里没写要红，表里写了一个代码里没有的码也要红。改码必须同时改这张表。

`git add` 失败的分流另有判据用例 `tests/test_safe_commit_add_errors.py`：
永久错误 → 退出码 1 且只出现 `[try 1]`；瞬时错误 → 每轮重试、耗尽归 2。
反证：把 `_attempt` 里的分流去掉（一律按瞬时重试），前一条用例转红（退出码变 2）。

## 已知边界：`check_claim_coordination.py` 按 `<repo>/scripts` 找兄弟模块

`scripts/check_claim_coordination.py:82` 写的是
`sys.path.insert(0, str(ROOT / "scripts"))`，不是「脚本自身所在目录」。
**当前无实害**，因为两条前提都成立：

- 它不在 `tests/test_commit_scope_intent_delete.py` 的 `SUPPORT_FILES` 里，钩子
  夹具不会把它拷进临时仓库的 `<repo>/scripts/`；
- 它不从 `.git/hooks/` 运行：pre-commit ④ 调的是
  `<repo>/scripts/check_claim_coordination.py`（或 `MBDSDR_CLAIM_COORD` 覆盖），
  `__file__` 本来就在 `<repo>/scripts/`，`ROOT/"scripts"` 与自身目录重合。

哪天要把它装进 `.git/hooks/`，或让它进夹具的 `SUPPORT_FILES`，**必须先改成按
自身目录解析**（`Path(__file__).resolve().parent`）。否则兄弟模块
`file_claim` / `claim_window` / `check_coordination_append_only` 会导入失败，
或导到别处的旧副本——这正是 pre-commit 整条 `ModuleNotFoundError` 那类事故的入口。
在那之前，不要把这段 `sys.path` 写法当模板照抄。
