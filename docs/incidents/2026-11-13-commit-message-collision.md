# 事故记录：`b898f4a3` 提交说明串车（⑱ 只证明了「落库 = 传入」）

- 事故提交：`b898f4a3`（2026-11-13 20:47:32 +0800，parent `d274697e`）
- 现象：tree 是工具成败线的 5 个文件，message 却是本会话另一笔 `5c6f10f8`
  （20:47:41）的原文
- 处置：**不改历史**（`b898f4a3` 的 message 保持原样），在工具侧封住入口
- 与 `docs/incidents/2026-11-13-safe-commit-swept-inflight-work.md` 的关系：
  那份记的是「卷走他线在途改动」，本篇是**另一类事实**（提交说明串车），
  单独成篇，不合并

## 1. 事实

`b898f4a3` 的 tree 是 `desktop/ai_steps.py`、`desktop/main_window.py`、
`mbdsdr_ai/tool_registry.py`、`tests/test_ai_steps_behavior.py`、
`tests/test_ai_tool_consistency.py`；message 是与这 5 个文件无关的
「无 claim 记录的声明路径给出归属提示」（1266 字节），后面追加了它自己的
`excluded-hunks:` 尾段（owner=他线在途，25 个 hunk，全部落在这 5 个文件里）。

逐字比对（都取自 `git cat-file commit` 的 commit 对象，不经过工作区）：

- `b898f4a3` 消息体 2242 字节；
- 去掉 `excluded-hunks:` 起的尾段后 1268 字节（含尾部空行），`rstrip()` 后与
  `5c6f10f8` 的 1266 字节**完全相同**，首个差异位：无；
- 尾段只可能由 `scripts/safe_commit.py::_exclusion_trailer` 生成 → 该笔确实
  走了 `safe_commit`，不是裸 `git commit`。

时间线（2026-11-13，+0800）：

| 时刻 | 事件 | 证据 |
|---|---|---|
| 20:47:03–04 | S1-S4 线分 5 个进程分别 claim 这 5 个文件 | `claims.json` history 的 `acquired_at` 1794574023–24 |
| 20:47:32 | `b898f4a3` 落库（author = committer = 1794574052） | `git cat-file commit b898f4a3`；`git reflog --date=iso` 同一秒 |
| 20:47:41 | `5c6f10f8` 落库（1794574061），message 原文首次出现 | 同上 |
| 20:47:44–45 | 这 5 个 claim 先后释放 | `claims.json` history 的 `released_at` |

能对上时间线的**一行证据**：

> `b898f4a3` 的 committer 时间戳 `1794574052 +0800` = **20:47:32**，其 message
> 体（去掉尾段后）与 **20:47:41** 落库的 `5c6f10f8` 逐字相同——说明这串文字在
> 我的提交之前就已经写进了对方读的那个消息文件，被对方原样带走；9 秒后我才
> 用它提交了自己的文件。

## 2. ⑱ 比对的两个来源（代码）

| 端 | 位置 | 符号 / 实现 |
|---|---|---|
| 传入 | `scripts/safe_commit.py` `main()` | `Path(args.message_file).read_bytes()` → 局部变量 `message` |
| 落库 | 同文件 | `_stored_message(env)`：`git cat-file commit HEAD`，按 `\n\n` 切出消息体 |
| 比对 | 同文件 | `_verify_message(env, message)`；提交用 `git commit --cleanup=verbatim -F -`（stdin） |

两端都**不是** `.git/COMMIT_EDITMSG`：左端是 `--message-file` 指向的任意路径，
右端是 commit 对象。所以「两边一致」能证明的只有一件事——git 原样保存了本进程
交给 stdin 的字节；它证明不了这串字节是本人写的。

关于「第三方在两步之间重写共享消息文件 → 两边一致」：**没有直接代码证据**。
代码能证明的是这条推断的前提——⑱ 在提交前不重读来源文件，提交后也不回头读它，
来源被污染时它没有任何手段发现（读入之后的重写也不会影响提交：stdin 已在内存）。
谁在 20:47:32 之前写了那个文件，git 对象里没有留痕，本篇不点名。

## 3. 修法：私有载体 + 与本进程留存拷贝比对

`scripts/safe_commit.py` 现在（形态⑱补）：

1. `--message-file` 落在 `.git/` 内一律拒绝（退出码 1）——那是任何 `git commit`
   都会覆写的全仓共享路径；
2. 启动时打印来源、sha256 与字节数；提交前 `_recheck_message_source()` 重读来源，
   与启动时读到的字节不同就拒绝（退出码 6，不重试）；
3. 提交前把最终消息（含 `excluded-hunks:` / `foreign-changes:` 尾段）落成本进程
   私有副本 `<locks>/messages/<session>.<pid>.msg`，`git commit -F -` 用副本字节；
4. 提交后 `_verify_message()` 只跟这份副本比对，`_stored_message()` 改为
   `rev-parse HEAD` 取显式 sha 再 `cat-file commit <sha>`，一致行附 sha 前 12 位。

## 4. 反证（都实际跑过）

| 反证 | 做法 | 结果 |
|---|---|---|
| 并发不串车 | 两个进程同时提交不同文件、不同 message（共用 `commit.lock`，落后的按基线变化重试） | 绿：两笔 message 与各自文件一一对应 |
| 共享消息文件在提交前被改写 | 锁内重读基线那一步改写信源文件（等价第三方插进窗口） | 绿：退出码 6、无提交、不打印「逐字校验一致」 |
| 把比对对象换回共享文件本身 | 把 `_verify_message(env, copy)` 改成读 `message_source` | 红：`test_verification_uses_private_copy_not_the_shared_file` 以退出码 3 失败 |
| 额外：去掉提交前重读 | 把 `_recheck_message_source(...)` 换成 `""` | 红：改写源文件那条用例返回 0 |

用例在 `tests/test_safe_commit_declaration.py`。

## 5. 不做的事

- 不改 `b898f4a3` 的历史，也不 amend：历史是事实，改了就再也对不上时间线；
- 不在本篇里追认「谁写的」：没有凭据就写没有凭据；
- 不把 `--message-file` 换成 `.git/COMMIT_EDITMSG` 的任何变体：共享路径本身就是
  入口，git 内部那一跳无法改路径，但可以不让它成为本脚本的输入或比对端。
