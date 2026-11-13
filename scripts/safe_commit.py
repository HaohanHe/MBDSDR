#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""安全提交：私有 index + 提交前重校验基线 + 撞锁整盘重建。

形态⑪（私有 index 的基线不是当时的 HEAD）反复出现：建完 index 到真正
``git commit`` 之间，别的线可能已经推进 HEAD。此时旧 index 相对新 HEAD 会多出
幻影 ``D`` 和别线的 ``M``，提交要么被 ref 锁拒，要么把别人的状态带进去。

本脚本把「建 index → 校验文件集 → 校验基线 → 提交 → 复核 blob」串成一次原子
操作，任何一步不满足就退出且**不提交**；撞上 ref 锁就用新 HEAD 整盘重建 index
再试（默认 3 次）。

会话私有 index（2026-11-13，形态⑬）
------------------------------------

本脚本从不读写共享的 ``.git/index``；index 固定落在
``<locks>/index/<session>.<pid>.index``（``MBDSDR_LOCKS_DIR`` 可覆盖，
默认仓库上一级的 ``.locks``，与 file_claim 同一处）。选它而不是
``git commit --only -- <paths>``：``--exclude-hunk`` 需要先把声明 hunk
从 index 反向撤掉，``--only`` 会按工作树重新取内容，排除声明会失效。
仍会串的场景写在 ``_warn_shared_index``：**别的会话**用 ``git add -A`` /
``git commit -a`` 往共享 index 暂存，再由它自己的普通 ``git commit`` 带走；
本脚本只能做到「自己这一笔绝对不含未声明路径」，管不了别人的笔。
因此提交后追加作用域机检：HEAD 的父必须是本次基线，且 HEAD 的改动路径
必须与 ``--files`` 清单逐项相等，否则按退出码 3（已提交、事故卡）报出。

私有 index 只隔离了「读谁的暂存区」，隔离不了「写进哪个父提交」：建完 index
到真提交之间 HEAD 若被推进，本笔 tree 按旧 HEAD 建，diff 出来会删掉对方刚加的
文件。所以「重读 HEAD → commit → 作用域机检」整段由 ``<locks>/commit.lock``
互斥（``_CommitLock``，崩溃残留 600s 后可摘）；锁内发现基线变了就返回可重试
错误，用新 HEAD 整盘重建 index。绕开本脚本手敲 ``git commit`` 仍能破坏它，
那种情况由作用域机检以退出码 3 抓出来。

排除他人 hunk（2026-11-12 裁定）
--------------------------------

共享文件里同时有自己和他人的在途改动时，可用 ``--exclude-hunk <path>:<start-end>``
（可重复）把指定 hunk 从**私有 index** 里反向撤掉，工作树保持原样。声明会：
① 作为 ``MBDSDR_EXCLUDE_HUNKS``（JSON）传给 pre-commit 闸门，闸门据此放行
``DIRTY(allowed)``（共享 index 不认这种声明）；
② 追加到提交说明的 ``excluded-hunks:`` 段，写明路径、行号范围与 owner（可空）。
声明范围必须完整覆盖 diff 的 hunk，跨 hunk 边界或对不上任何改动一律拒绝。

意图删除（2026-11-12）
----------------------

pre-commit 闸门对**任何未声明的删除条目**一律拒绝（幻影删除 fail-closed）。
私有 index 里「HEAD 有、新索引没有」的路径就是本次真删的文件，本脚本把它们
自动导出为 ``MBDSDR_INTENT_DELETIONS``，正常删文件不用手写声明；``--files``
清单之外的路径根本不会进索引，因此不会被误声明。

声明快照：只交「声明时的那份内容」（2026-11-13，形态⑰）
-------------------------------------------------------

``8982b4f9`` 实证：``--files`` 声明之后、``git add`` 之前，别的线往同一个文件
写了两行，于是这笔「只该加 1 行」的提交带了 3 行，内容没丢但归属记错。根因是
本脚本一直按**提交时刻的工作区**取文件，而不是按声明时的那份快照。

现在：

* ``--declare`` 只记录声明快照（每文件的存在性 + blob 号 + 行数），落在
  ``<locks>/declarations/<session>.<文件集摘要>.json``，不提交；
* 提交时若找到同一文件集的声明，就以它为准；没有声明则退化为「本次进程启动
  时刻的工作区」，并明确打出「无预先声明，窗口只覆盖本次运行」；
* ``git commit`` 之前重算一次，**一个字节不同就拒绝**（退出码 4，不重试）：
  逐行列出声明之后新增的行、消失的行，并从 claim 台账推断这些行可能属于谁；
* 声明时**已经存在**的他人改动不算变化（那正是 ``--exclude-hunk`` 处理的对象），
  这条边界不能混：混了以后共享文件就没人敢提交；
* 确认要连别人的行一起带走时，用 ``--allow-foreign-changes "<理由>"`` 放行，
  理由与逐文件增删行数会写进提交说明的 ``foreign-changes:`` 段——豁免必须留痕，
  不能默默通过。

⑰ 的边界（`052828d1` 形态）：判据是**时间窗**，不是**归属**
------------------------------------------------------------

``052828d1`` 现场：声明把两个**未被任何 claim 占着**的新测试文件列进了
``--files``，同时工作区里另有他线写在同文件里的内容。这两类都**不在** ⑰ 的
判定范围内：

* 声明**之前**就已经躺在工作区里的他线内容会原样进快照，⑰ 不会提示——
  本脚本**没有**任何机制判断「这几行是谁写的」；
* 未被 ``--files`` 列到的未跟踪文件，本脚本**碰不到**：index 只从 HEAD 建，
  只 add 声明清单，提交后再逐路径比对作用域。``052828d1`` 能把它们带走，
  只可能是它们**被写进了 ``--files``**——那是声明内容错了，不是工具漏了。

能提供的只有弱提示：进入 ``--files`` 的路径若台账里没有当前 owner 的 claim
记录，打印 ``[claim] 该路径无 claim 记录，请确认归属：<path>``。**只提示，
不拦**——没签字不等于偷东西，归属由人确认，工具不替人签字，也不把「没签字」
当成拦下的理由。声明时已有他人 hunk 与「声明后漂移」的分界，见 ⑰ 的判定本身。

提交说明逐字校验（2026-11-13，形态⑱）
--------------------------------------

``7a76b097`` 实证：主题写「hardcoded-parameters 的文案」，内容却是
``desktop/command_palette.py``。多进程并发提交时 ``.git/COMMIT_EDITMSG`` 是
共享可变状态（``git commit -F`` 也会把消息写进去，commit-msg 钩子跑完再读回），
别人的消息可能在读回之前覆盖它。

本脚本的处理：消息只走 stdin（``git commit --cleanup=verbatim -F -``，不经过
任何外部临时文件），提交锁内串行化，提交后从 commit 对象里把消息取回来逐字
比对——对不上就是退出码 3 的事故报告，不会因为「已经提交了」而沉默。

``b898f4a3`` 证明这还不够（2026-11-13，形态⑱补）。那笔的 tree 是工具成败线的
5 个文件，message 却是本会话另一笔 ``5c6f10f8`` 的原文（逐字相同，1266 字节）
＋它自己的 ``excluded-hunks:`` 尾段；尾段只可能由 ``_exclusion_trailer`` 生成，
所以它确实走了本脚本。串的是**读入之前**：``--message-file`` 指向了一个全仓
共享的可变路径，别的进程写进去的消息被它原样读走，随后「读到的字节 vs 落库
字节」当然一致——这条比对管的是 git 有没有原样保存，管不了读进来的是谁的消息。

因此现在：

* ``--message-file`` 落在 ``.git/`` 里一律拒绝（退出码 1）——那是 git 自己会
  覆写的全仓共享路径，不是本进程的载体；
* 启动时打出消息来源、sha256 与字节数；**提交前重读来源文件**，与启动时读到
  的字节不同就拒绝（退出码 6，不重试）：两个进程共用一个消息路径本身就是
  事故前兆；
* 提交前把最终消息（含各类尾段）落成本进程私有副本
  ``<locks>/messages/<session>.<pid>.msg``，``git commit -F -`` 用副本字节，
  提交后也只跟这份副本比对——比对端不再回头读任何可能被覆盖的共享文件。

这条路径上的共享可变状态清单：

* ``.git/index`` / ``.git/index.lock``：不进，本脚本全程用会话私有 index；
* ``.git/COMMIT_EDITMSG``：git 自己会写，本脚本无法改路径；它不再是本脚本的
  输入或比对端（消息走 stdin、复查用私有副本），只剩 git 内部这一跳；
* ``.git/ORIG_HEAD``：本脚本不产生，也不读；
* 钩子产物：pre/post-commit 的标记按 tree 号命名，不与并发提交撞名；
* ``refs/heads/*.lock``：git 自己的锁，撞上按可重试处理。

``052828d1`` 只把 ⑰⑱⑳ 写进说明、实际还带着 ⑲；``1004608f`` 说明只写「补
``--full-scan`` 全盘入口」、实际并进 log 扫描根收窄与 12 条测试。内容都对、
说明漏了——人按 message 检索会被指偏。因此另有一条弱提示：``--files`` 里某路径的
basename 在说明里一次都没出现，就打印 ``[message] 说明未提及：<path>``。只提示
不拦、不参与退出码，也不要求逐文件罗列：让漏写被看见，不是清单作业。

§6.1 登记行：闸看暂存区，这里连工作区一起看（2026-11-13，形态⑲）
------------------------------------------------------------------

``ac232bf5`` 实证：pre-commit 要求 §6.1 有登记行，人用
``append_coordination_row.py`` 把行追加到**工作区**，但这行没进暂存区——
闸看见的暂存区是干净的，行就这么挂在工作区等「下次顺手带走」。这正是
``8982b4f9`` 会重演的入口：闸的视野比本笔该带的内容窄。

现在，提交前按**同一个判定**（直接 import ``check_claim_coordination``，
不另写一份语义）算出本笔哪些暂存文件需要 §6.1 行：

* 工作区里没有该行 → 拒绝（退出码 5），并打印可直接粘贴执行的
  ``append_coordination_row.py`` 命令；
* 工作区里有、暂存区没有 → 只把覆盖本笔的**那几行**写进私有 index，
  工作区里别人写的行原样留在外面；拒绝信息与放行信息都会说清带了哪几行；
* 工具**不生成**登记行内容：行是谁占着文件谁写的（签名权在人手上），
  工具只负责别让这行掉队。

排除粒度：整 hunk 之外，允许按行（2026-11-13，形态⑳）
-------------------------------------------------------

现场：三行相邻的 §6.1 登记里只有一行是自己的，``--exclude-hunk`` 只认完整
hunk，于是有人把自己的行挪到别处让 diff 断开——提交干净了，台账时间序却被
「为了让工具能用」这个排版副作用打乱。**为了让工具能用而改数据，代价不能接受。**

现在 ``--exclude-hunk path:2051-2053`` 可以只排 hunk 里的那几行：

* 范围完整覆盖 hunk → 与原来的整块排除完全一样（老入口保留）；
* 范围收在单个纯新增 hunk 内部 → 只丢掉范围内的 ``+`` 行，其余行留在原位，
  不因为排除而挪动；
* 含删除行的 hunk 只能整块声明（新侧行号选不中 ``-`` 行）；跨 hunk 边界、
  行号落空、blob 逐字对不上，一律拒绝。

行号是坐标，坐标只在它被计算的那一刻有效（2026-11-13，``ed3c09e5``）
-------------------------------------------------------------------

``ed3c09e5`` 的 ``--exclude-hunk`` 行号是 20:58 按当时工作区算的（hunk
``+2100,6``）；21:02 另一条线在同一位置追加 4 行，hunk 变成 ``+2100,10``；
排掉 2100-2103 后，2104-2105 与新出现的 2106-2109 被一起带走——其中
2106-2109 是别人的。根因不是排除算法，是**没有 ``--declare``**：⑰ 的基线
退化成「本次启动时刻的工作区」，那 4 行被算进基线，程序一个字都没报。

现在：**``--exclude-hunk`` / 行范围必须与 ``--declare`` 一起用**。找不到
同一文件集的声明快照就直接拒绝（退出码 7，不重试），不再拿「本次启动时刻」
冒充声明——行号可以算得很准，但算完到提交之间世界会变。

用法::

    python scripts/safe_commit.py --files a.py b.md --message-file msg.txt
    python scripts/safe_commit.py --files a.md --declare
    python scripts/safe_commit.py --files a.md --message-file msg.txt \\
        --exclude-hunk a.md:120-138 --exclude-owner 接入层线
    python scripts/safe_commit.py --files a.md --message-file msg.txt \\
        --exclude-hunk a.md:2051-2052 --exclude-owner 文档引用复核线
    python scripts/safe_commit.py --files a.md --message-file msg.txt \\
        --allow-foreign-changes "B 线两行已当面核对，随本笔一起落库"

退出码：0 成功；1 前置校验失败（未提交）；2 重试耗尽（未提交）；3 提交后复核失败
（已提交，需按事故卡处理）；4 声明后内容变化被拒（未提交，不重试）；
5 §6.1 缺行被拒（未提交，不重试）；6 消息源在提交前被改写（未提交，不重试）；
7 使用了 ``--exclude-hunk`` 却没有声明快照（未提交，不重试）。
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(
    os.environ.get("MBDSDR_REPO_ROOT") or Path(__file__).resolve().parents[1]
)
#: 与 file_claim 同一处；index 不落在仓库里，避免被 .gitignore/备份扫到
LOCKS_DIR = Path(os.environ.get("MBDSDR_LOCKS_DIR") or (ROOT.parent / ".locks"))

EXCLUDE_ENV = "MBDSDR_EXCLUDE_HUNKS"
INTENT_ENV = "MBDSDR_INTENT_DELETIONS"
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def _parse_exclusions(specs: list[str], files: list[str]) -> list[dict]:
    """解析 `--exclude-hunk <path>:<start-end>`；格式错误或路径越界直接失败。"""
    items: list[dict] = []
    for spec in specs:
        path, sep, rng = spec.rpartition(":")
        match = re.fullmatch(r"\s*(\d+)\s*-\s*(\d+)\s*", rng or "")
        if not sep or not match:
            raise ValueError(f"--exclude-hunk 格式应为 <path>:<start-end>，收到 {spec!r}")
        norm = path.replace("\\", "/")
        if norm.startswith("./"):
            norm = norm[2:]
        if norm not in files:
            raise ValueError(f"--exclude-hunk 的路径不在 --files 清单内：{norm}")
        start, end = int(match.group(1)), int(match.group(2))
        if start <= 0 or end < start:
            raise ValueError(f"--exclude-hunk 行号范围不合法：{spec!r}")
        items.append({"path": norm, "start": start, "end": end})
    return items


def _revert_index_hunks(env: dict, path: str, ranges: list[dict]) -> None:
    """把声明的行从私有 index 里反向撤掉（工作树不动）。

    声明范围可以是**完整 hunk**（``a.md:120-138``），也可以是 hunk 内部的
    **行范围**（``a.md:2051-2053``）。后者是为 2026-11-13 的现场加的：三行
    相邻新增里只有一行是自己的，按整 hunk 排就只能把自己的行挪走让 diff 断开，
    结果「为了让工具能用而改数据」——台账时间序被排版副作用打乱。

    行级排除只在 hunk 不含删除行时才允许：新侧行号选不中 ``-`` 行，选中了
    也是猜。跨 hunk 边界、行号落空、blob 内容对不上，一律拒绝，绝不静默
    扩大或缩小排除范围。新增文件与二进制文件不支持排除。
    """
    if _run(["cat-file", "-e", f"HEAD:{path}"], env).returncode != 0:
        raise ValueError(f"{path} 是新增文件，不支持 --exclude-hunk")
    proc = _run(["diff", "--cached", "--no-renames", "-U0", "HEAD", "--", path], env)
    if proc.returncode != 0:
        raise ValueError(
            f"读取 {path} 的暂存 diff 失败：{proc.stderr.decode('utf-8', 'replace')[:200]}"
        )
    text = proc.stdout.decode("utf-8", "replace")
    if "GIT binary patch" in text or text.startswith("Binary files"):
        raise ValueError(f"{path} 是二进制文件，不支持 --exclude-hunk")
    header: list[str] = []
    hunks: list[dict] = []
    cur: dict | None = None
    for line in text.splitlines(True):
        if line.startswith("@@"):
            if cur is not None:
                hunks.append(cur)
            cur = {"head": line, "body": [], "start": 0, "end": -1}
        elif cur is None:
            header.append(line)
        else:
            cur["body"].append(line)
    if cur is not None:
        hunks.append(cur)
    if not hunks:
        raise ValueError(f"{path} 在私有 index 里没有改动，无法排除")

    for hunk in hunks:
        match = _HUNK_RE.match(hunk["head"].rstrip("\n"))
        if not match:
            raise ValueError(f"{path} 的 hunk 头无法解析：{hunk['head'].strip()}")
        hunk["start"] = int(match.group(1))
        count = int(match.group(2) or 1)
        # 纯删除 hunk（@@ … +b,0）在新版没有行，锚在删除点 b：按 [b, b]
        # 参与覆盖检查（与「声明范围按 new 行号」同一条规则）。反向撤销已验证：
        # --unidiff-zero 下能把删掉的行精确加回私有 index。
        hunk["end"] = hunk["start"] + count - 1 if count else hunk["start"]
        # 每个 + 行对应的新侧行号：行级排除要靠它定位，不能靠 body 下标。
        hunk["plus"] = []
        lineno = hunk["start"]
        for body_line in hunk["body"]:
            if body_line.startswith("+"):
                hunk["plus"].append((lineno, body_line))
                lineno += 1

    # plan: hunk → 要丢掉的新侧行号集合（None = 整块排除，保持老入口）
    plan: dict[int, dict] = {}
    for item in ranges:
        matched = [
            h for h in hunks if h["start"] <= item["end"] and h["end"] >= item["start"]
        ]
        if not matched:
            raise ValueError(
                f"{path}:{item['start']}-{item['end']} 没对上任何暂存 hunk"
            )
        for hunk in matched:
            entry = plan.get(id(hunk))
            if item["start"] <= hunk["start"] and hunk["end"] <= item["end"]:
                plan[id(hunk)] = {"hunk": hunk, "drop": None}
                continue
            if item["start"] < hunk["start"] or item["end"] > hunk["end"]:
                raise ValueError(
                    f"{path}:{item['start']}-{item['end']} 与 hunk "
                    f"{hunk['start']}-{hunk['end']} 只相交一部分：要么整块声明，"
                    "要么把范围收进这一个 hunk 里"
                )
            if any(line.startswith("-") for line in hunk["body"]):
                raise ValueError(
                    f"{path}:{item['start']}-{item['end']} 未完整覆盖 hunk "
                    f"{hunk['start']}-{hunk['end']}：该 hunk 含删除行，行级排除"
                    "定位不了删除行，这种 hunk 请按完整 hunk 范围声明"
                )
            drop = {ln for ln, _line in hunk["plus"]
                    if item["start"] <= ln <= item["end"]}
            if not drop:
                raise ValueError(
                    f"{path}:{item['start']}-{item['end']} 在 hunk "
                    f"{hunk['start']}-{hunk['end']} 里没有可排除的新增行"
                )
            if entry is None:
                plan[id(hunk)] = {"hunk": hunk, "drop": drop}
                item["partial"] = True
            elif entry["drop"] is None:
                continue                      # 已经被另一条声明整块排除
            else:
                entry["drop"] |= drop
                item["partial"] = True
    if not plan:
        raise ValueError(f"{path} 的排除声明没有选中任何 hunk")

    # 直接在私有 index 的 blob 上做行替换，不再让 git apply 去猜位置。
    # 原因（2026-11-13 实测）：排除 diff 是 -U0，没有上下文，git 只能按行号
    # 定位。一次塞多个 hunk 会报「patch does not apply」；即使自下而上逐个
    # apply，main_window.py 这种相邻 hunk 紧挨着（+8396 / +8398 / +8401）
    # 的文件仍会被放错位置——apply 返回 0，提交里却混进了他线的 3 行 tab 顺序
    # 搬移（9c58d767 实证）。这里按 hunk 自带的 +/- 行做「内容必须逐字相同」
    # 的精确撤销：对不上就报错，绝不静默写歪。
    blob = _run(["show", f":{path}"], env)
    if blob.returncode != 0:
        raise ValueError(
            f"读取私有 index 里 {path} 的内容失败："
            f"{blob.stderr.decode('utf-8', 'replace')[:200]}"
        )
    lines = blob.stdout.splitlines(keepends=True)
    for entry in sorted(plan.values(), key=lambda e: e["hunk"]["start"], reverse=True):
        hunk = entry["hunk"]
        removed: list[bytes] = []
        for body_line in hunk["body"]:
            if body_line.startswith("\\"):
                raise ValueError(
                    f"{path} 的 hunk {hunk['start']}-{hunk['end']} 含"
                    "「No newline at end of file」标记，暂不支持排除")
            if body_line.startswith("-"):
                removed.append(body_line[1:].encode("utf-8"))
        added = [(ln, line[1:].encode("utf-8")) for ln, line in hunk["plus"]]
        pos = hunk["start"] - 1 if added else hunk["start"]
        end = pos + len(added)
        # 整块逐字比对（包含被行级排除掉的那几行）：排除的每一行都必须在
        # blob 里原地、原样存在，找不到就拒绝——这条不能因为粒度变细而松。
        if lines[pos:end] != [raw for _ln, raw in added]:
            raise ValueError(
                f"{path} 的 hunk {hunk['start']}-{hunk['end']} 在私有 index 里"
                "对不上（行号/内容漂移），拒绝静默改写"
            )
        drop = entry["drop"]
        # drop is None 是整块排除的老入口：整个 hunk 的 + 行一行都不带。
        kept = [] if drop is None else [raw for ln, raw in added if ln not in drop]
        lines[pos:end] = kept + removed
    new_blob = b"".join(lines)
    sha = _run(["hash-object", "-w", "--stdin"], env, new_blob)
    if sha.returncode != 0:
        raise ValueError(f"写入 {path} 新 blob 失败：{sha.stderr.decode()[:200]}")
    mode = _run(["ls-files", "-s", "--", path], env).stdout.split(b" ")[0]
    info = f"{mode.decode()},{sha.stdout.strip().decode()},{path}"
    upd = _run(["update-index", "--cacheinfo", info], env)
    if upd.returncode != 0:
        raise ValueError(
            f"更新私有 index 里 {path} 失败：{upd.stderr.decode()[:200]}")


def _exclusion_trailer(message: bytes, exclusions: list[dict], owner: str) -> bytes:
    """把排除清单追加到提交说明（提交说明即持久日志）。"""
    if not exclusions:
        return message
    lines = ["", "", "excluded-hunks:" + (f" owner={owner}" if owner else "")]
    for item in exclusions:
        tail = "（行级）" if item.get("partial") else ""
        lines.append(f"- {item['path']}:{item['start']}-{item['end']}{tail}")
    return message.rstrip(b"\n") + ("\n" + "\n".join(lines) + "\n").encode("utf-8")

LOCK_HINTS = ("cannot lock ref", "expected ", "Unable to create", "index.lock")


def _run(args, env, stdin: bytes | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(ROOT), *args], env=env,
                          capture_output=True, input=stdin)


def _emit(proc: subprocess.CompletedProcess, label: str) -> None:
    """失败时把子进程输出原样打出来：只剩返回码等于看不见闸门为什么拒。"""
    out = proc.stdout.decode("utf-8", "replace").rstrip()
    err = proc.stderr.decode("utf-8", "replace").rstrip()
    if out:
        print(f"[{label}] stdout:\n{out}")
    if err:
        print(f"[{label}] stderr:\n{err}")


def _report_hooks(proc: subprocess.CompletedProcess, env: dict) -> None:
    """成功路径也把钩子交代清楚：跑没跑、scope 结果、标记去哪了。

    钩子的输出由 git 转发进 commit 的 stdout/stderr，而 ``_run`` 一直在捕获，
    所以这里主动回放 pre-commit / post-commit / 凭据扫描的关键行，再补一条结论——
    「通过了」本身必须可见，否则不可见的门禁等于没有。
    """
    text = (proc.stdout + proc.stderr).decode("utf-8", "replace")
    lines = [
        line.rstrip()
        for line in text.splitlines()
        if line.lstrip().startswith(("[pre-commit]", "[post-commit]", "[secret-scan]"))
    ]
    for line in lines:
        print(line)
    tree = _run(["rev-parse", "HEAD^{tree}"], env).stdout.decode().strip()
    marker = (
        Path(os.environ.get("MBDSDR_HOOK_MARKER_DIR") or r"D:\mbdsdr\.locks\hook_ok")
        / f"{tree}.json"
    )
    pre = "已执行" if any(l.lstrip().startswith("[pre-commit]") for l in lines) else "无输出"
    post = "已执行" if any(l.lstrip().startswith("[post-commit]") for l in lines) else "无输出"
    state = "仍在（post-commit 未消费）" if marker.exists() else "已被 post-commit 消费"
    print(
        f"[hooks] pre-commit={pre} · commit RC={proc.returncode} · "
        f"标记 {tree[:8]} {state} · post-commit={post}"
    )


def _index_env(path: Path) -> dict:
    env = dict(os.environ)
    env.pop("GIT_INDEX_FILE", None)          # 形态⑫：不继承会话级泄漏
    env["GIT_INDEX_FILE"] = str(path)        # 只进子进程
    return env


def _session_key() -> str:
    """会话名（环境变量优先），只保留文件名安全字符。"""
    raw = (os.environ.get("MBDSDR_SESSION_ID")
           or os.environ.get("MBDSDR_CLAIM_OWNER")
           or f"pid{os.getpid()}")
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).strip("._")
    return (safe or "session")[:64]


def _index_path() -> Path:
    """每个会话（进程）自己的 index；不碰共享 ``.git/index``。"""
    directory = LOCKS_DIR / "index"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{_session_key()}.{os.getpid()}.index"


def _shared_index_owners(paths: list[str]) -> dict[str, str]:
    """查每条暂存路径的**当前** owner；没有就写「无 claim 记录」。

    只认 ``current``：历史主人不是「现在找谁」，混在一起会指错人。
    有历史但无 current 时把历史放括号里，供人判断该问谁。
    """
    try:
        cc = _coord_module()
        registry = cc._load_registry(cc.fc.REGISTRY_PATH)
    except (ImportError, OSError, ValueError):
        return {path: "无 claim 记录（台账不可用）" for path in paths}
    by_path = {}
    for entry in (registry.get("claims") or {}).values():
        if isinstance(entry, dict) and entry.get("path"):
            by_path[cc._norm(str(entry["path"]))] = entry
    labels = {}
    for path in paths:
        entry = by_path.get(cc._norm(path)) or {}
        current = entry.get("current")
        if isinstance(current, dict) and current.get("owner"):
            labels[path] = str(current["owner"])
            continue
        seen = list(dict.fromkeys(
            str(item["owner"]) for item in (entry.get("history") or [])
            if isinstance(item, dict) and item.get("owner")
        ))
        labels[path] = ("无 claim 记录（历史：" + "、".join(seen) + "）"
                        if seen else "无 claim 记录")
    return labels


def _warn_shared_index() -> None:
    """共享 index 里有暂存项时告警：本脚本不读它，但别人会。

    这正是 ``ddcecf76`` 的成因——A 会话 ``git add -A`` 后只提交了声明路径，
    B 会话的普通 ``git commit`` 把剩下的暂存项按 B 的消息带走了。隔离只能
    保证本脚本自己的提交干净，挡不住别人用共享 index 提交。

    逐条带上 owner：知道找谁，才有处置的可能；查不到就写「无 claim 记录」，
    不拿空白冒充「没人认领」。
    """
    env = {k: v for k, v in os.environ.items() if k != "GIT_INDEX_FILE"}
    proc = _run(["diff", "--cached", "--name-only", "--no-renames"], env)
    staged = [p for p in proc.stdout.decode("utf-8", "replace").splitlines() if p]
    if staged:
        labels = _shared_index_owners(staged)
        print(
            f"[warn] 共享 index 有 {len(staged)} 个暂存项"
            "（本脚本不读它，但任何普通 git commit 都会把它们带走）："
        )
        for path in staged:
            print(f"[warn]   {path}（owner: {labels.get(path, '无 claim 记录')}）")


class _CommitLock:
    """跨进程串行化「读 HEAD → commit → 作用域机检」这一段。

    私有 index 只解决「读的是谁的暂存区」，解决不了「写进哪个父提交」：从
    ``read-tree`` 到 ``git commit`` 之间 HEAD 若被别的会话推进，本笔的 tree
    是按旧 HEAD 建的，diff 出来会**删掉**对方刚加的文件。所以提交这一段必须
    互斥：锁内重读 HEAD，和基线不一致就退回去整盘重建 index。
    """

    def __init__(self, timeout: float = 120.0, stale_after: float = 600.0) -> None:
        self.path = LOCKS_DIR / "commit.lock"
        self.timeout = timeout
        self.stale_after = stale_after
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def __enter__(self) -> "_CommitLock":
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                # 持有者崩溃留下的旧锁：超过 stale_after 直接摘掉，否则等
                try:
                    if time.time() - self.path.stat().st_mtime > self.stale_after:
                        self.path.unlink()
                        continue
                except OSError:
                    pass
                if time.monotonic() > deadline:
                    raise TimeoutError(
                        f"等提交锁超时（{self.timeout:.0f}s）：{self.path}"
                        "（若确认无提交在跑，删掉该文件即可）"
                    )
                time.sleep(0.05)
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(f"{os.getpid()}\n")
            return self

    def __exit__(self, *_exc) -> None:
        try:
            self.path.unlink()
        except OSError:
            pass


def _blob(env: dict, rev: str) -> str:
    """取 rev 的 blob 号；删除项/不存在返回空串。"""
    proc = _run(["rev-parse", "--verify", "--quiet", rev], env)
    return proc.stdout.decode().strip() if proc.returncode == 0 else ""


def _staged_set(env) -> list[str]:
    proc = _run(["diff", "--cached", "--name-status", "--no-renames"], env)
    out = []
    for line in proc.stdout.decode("utf-8", "replace").splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            out.append(f"{parts[0]}\t{parts[1]}")
    return out


# ---------------------------------------------------------------------------
# 声明快照：声明之后冒出来/消失的内容一律拒绝（形态⑰）
# ---------------------------------------------------------------------------

def _plain_env() -> dict:
    """不含 ``GIT_INDEX_FILE`` 的环境：只读工作区内容，不碰任何暂存区。"""
    return {k: v for k, v in os.environ.items() if k != "GIT_INDEX_FILE"}


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def _fingerprint(env: dict, path: str) -> dict:
    """工作区内容的声明指纹：存在性 + blob 号 + 行数。

    blob 用 ``hash-object -w --stdin`` 落进对象库（与 ``git add`` 同一种东西，
    未被引用，gc 会回收）：比对时能 ``cat-file`` 取回声明时的原文，用来逐行
    说出「哪几行是声明之后才冒出来的」，而不是只给一个不相等。
    """
    target = ROOT / path
    if not target.is_file():
        return {"path": path, "exists": False, "blob": "", "lines": 0}
    data = target.read_bytes()
    proc = _run(["hash-object", "-w", "--stdin"], env, data)
    if proc.returncode != 0:
        raise ValueError(
            f"计算 {path} 的 blob 号失败：{proc.stderr.decode('utf-8', 'replace')[:200]}")
    return {
        "path": path,
        "exists": True,
        "blob": proc.stdout.decode().strip(),
        "lines": len(data.decode("utf-8", "replace").splitlines()),
    }


def _declaration_dir() -> Path:
    directory = LOCKS_DIR / "declarations"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _declaration_digest(files: list[str]) -> str:
    """按文件集（不是按会话）索引：同一份声明清单在哪条线手里都指同一批内容。"""
    joined = "\n".join(sorted(files))
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()[:16]


def _write_declaration(files: list[str], env: dict) -> tuple[dict, float]:
    """把声明时刻的工作区内容记成快照；同一文件集只留最新一份。"""
    snapshot = {path: _fingerprint(env, path) for path in files}
    stamp = time.time()
    record = {
        "session": _session_key(),
        "owner": os.environ.get("MBDSDR_CLAIM_OWNER", ""),
        "root": str(ROOT),
        "declared_at": stamp,
        "declared_iso": _iso(stamp),
        "files": snapshot,
    }
    for old in _declaration_dir().glob(f"*.{_declaration_digest(files)}.json"):
        try:
            old.unlink()
        except OSError:
            pass
    target = _declaration_dir() / f"{_session_key()}.{_declaration_digest(files)}.json"
    tmp = target.with_name(f"{target.name}.tmp{os.getpid()}")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, target)
    return snapshot, stamp


def _load_declaration(files: list[str]) -> tuple[Path, dict, float] | None:
    """找同一文件集的声明快照；找不到返回 None（调用方退化为本次启动时刻）。"""
    candidates = sorted(
        _declaration_dir().glob(f"*.{_declaration_digest(files)}.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for path in candidates:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            print(f"[declare] 忽略读不出来的声明文件：{path}")
            continue
        if set(record.get("files") or {}) != set(files):
            print(f"[declare] 忽略文件集不符的声明文件：{path}")
            continue
        if str(record.get("root")) != str(ROOT):
            print(f"[declare] 忽略其它仓库的声明文件：{path}")
            continue
        print(
            f"[declare] 使用声明快照 {path}（session={record.get('session')}，"
            f"{record.get('declared_iso')}）"
        )
        return path, record["files"], float(record["declared_at"])
    return None


def _drop_declaration(path: Path | None) -> None:
    """提交成功才清声明：失败/被拒时快照必须留着，否则下次就查不出东西。"""
    if path is None:
        return
    try:
        path.unlink()
    except OSError:
        pass


def _declared_lines(env: dict, blob: str) -> list[str] | None:
    """取回声明时的原文；blob 已被 gc 清走时返回 None（仍然判不一致）。"""
    if not blob:
        return []
    proc = _run(["cat-file", "blob", blob], env)
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", "replace").splitlines()


def _line_diff(env: dict, declared: dict, limit: int = 40) -> tuple[list[str], int, int]:
    """逐行说出「声明之后多了什么 / 消失了什么」，返回 (明细, 新增数, 消失数)。"""
    old = _declared_lines(env, declared.get("blob") or "")
    if old is None:
        return ([f"    （声明 blob {(declared.get('blob') or '')[:8]} 已不在对象库，"
                 "无法逐行对比；指纹仍不一致）"], 0, 0)
    target = ROOT / declared["path"]
    new = (target.read_bytes().decode("utf-8", "replace").splitlines()
           if target.is_file() else [])
    items: list[tuple[str, int, str]] = []
    added = removed = 0
    matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        for k in range(i1, i2):          # 声明时在、现在没了：丢内容
            removed += 1
            items.append(("-", k + 1, old[k]))
        for k in range(j1, j2):          # 声明时没有、现在冒出来：归属会被记错
            added += 1
            items.append(("+", k + 1, new[k]))
    detail = [
        f"    {mark} {'声明时第' if mark == '-' else '现在第'} {lineno} 行：{text}"
        for mark, lineno, text in items[:limit]
    ]
    if len(items) > limit:
        detail.append(f"    …另有 {len(items) - limit} 行变化未显示")
    return detail, added, removed


def _claim_owners(path: str, since: float) -> list[str]:
    """从 claim 台账推断：声明之后仍持有过这个文件的线。"""
    try:
        data = json.loads(
            (LOCKS_DIR / "claims.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    record = (data.get("claims") or {}).get(path.casefold()) or {}
    entries = list(record.get("history") or [])
    if record.get("current"):
        entries.append(record["current"])
    now = time.time()
    me = os.environ.get("MBDSDR_CLAIM_OWNER", "")
    owners: list[str] = []
    for entry in entries:
        owner = str(entry.get("owner") or "")
        if me and owner == me:
            continue                     # 「可能属于」要指向别人，不指向自己
        start = float(entry.get("acquired_at") or 0)
        end_raw = entry.get("released_at")
        end = float(end_raw) if end_raw else None
        if start > now:
            continue
        if end is not None and end <= since:
            continue                     # 声明前就释放了，跟这次变化无关
        state = "持有中" if end is None else f"至 {_iso(end)}"
        line = f"{owner or '<未知>'}（{_iso(start)} 起，{state}）"
        if line not in owners:
            owners.append(line)
    return owners


def _claim_hint(files: list[str]) -> None:
    """台账里没有当前 owner 的 claim 记录时，逐路径提示一行（**只提示，不拦**）。

    声明快照（形态⑰）的判据是**时间窗**，不是**归属**：声明之前就已经躺在工作区
    里的他线内容会原样进快照，本脚本不会、也无法判断那几行是谁写的。``052828d1``
    就是这一类——``--files`` 把未 claim 的新测试文件一起列进了声明，工具照单全收，
    内容没错，错的是声明范围。

    归属是人的判断：没签字不等于偷东西，工具不替人签字，也不把「没签字」当成
    拦下的理由。所以这里只打印，不返回状态、不参与退出码。
    """
    cc = _coord_module()
    owner = cc.resolve_owner(ROOT)
    if not owner:
        return                       # 没有提交者身份可比，提示只会变成噪声
    try:
        registry = cc._load_registry(cc.fc.REGISTRY_PATH)
    except (OSError, ValueError):
        return
    known = cc.ledger_index(registry)
    for rel in files:
        owners = known.get(cc._norm(rel)) or set()
        if any(cc._owner_matches(item, owner) for item in owners):
            continue
        print(f"[claim] 该路径无 claim 记录，请确认归属：{rel}")


def _message_coverage_hint(message: bytes, files: list[str]) -> None:
    """说明里一个文件名都没提的路径，逐条提示（**只提示，不参与退出码**）。

    ``052828d1`` 的说明只列了 ⑰⑱⑳ 而实际还有 ⑲；``1004608f`` 只写「补
    ``--full-scan`` 全盘入口」，实际并进了 log 扫描根收窄与 12 条测试。两次的
    共同点是内容都对、说明漏了——而人对「说明」的信任没有上限，事后按 message
    检索会被指偏。

    判据是 basename 的子串出现，不做目录匹配、不要求逐文件罗列：提示只是让
    「漏写」被看见，不是把提交说明变成清单作业。不拦，也不参与退出码。
    """
    text = message.decode("utf-8", "replace")
    for rel in files:
        name = rel.rsplit("/", 1)[-1]
        if name and name not in text:
            print(f"[message] 说明未提及：{rel}")


def _verify_declaration(env: dict, declared: dict, declared_at: float,
                        waived: bool = False) -> tuple[list[dict], str]:
    """比对声明快照与当前工作区：返回 (变化清单, 报告文本)。

    ``waived`` 只改报告措辞（放行而不是拒绝），不改判定：豁免路径同样要把
    逐行明细打出来，否则「悄悄过去」和原来的 bug 没区别。

    只比内容，不比 mtime：``--exclude-hunk`` 声明时就已经在的他人改动已进快照，
    天然不会命中——这条边界混掉，共享文件就再没人敢提交。
    """
    changed: list[dict] = []
    for path, item in declared.items():
        current = _fingerprint(env, path)
        if (current["exists"] == bool(item.get("exists"))
                and current["blob"] == (item.get("blob") or "")):
            continue
        detail, added, removed = _line_diff(env, item)
        changed.append({
            "path": path,
            "detail": detail,
            "added": added,
            "removed": removed,
            "before": item.get("blob") or "",
            "after": current["blob"],
        })
    if not changed:
        return [], ""
    lines = [
        (
            f"豁免通过（--allow-foreign-changes）：声明之后有 {len(changed)} 个文件的内容"
            "变化，按给定理由一起落库。"
            if waived else
            f"拒绝提交：声明之后有内容变化（{len(changed)} 个文件）。"
        ),
        f"声明时刻 {_iso(declared_at)}。只比「声明之后新冒出来或消失」的内容；"
        "声明时就已经在的他人改动不算（那属于 --exclude-hunk）。",
    ]
    for item in changed:
        before = item["before"][:8] or "<无>"
        after = item["after"][:8] or "<无>"
        lines.append(
            f"  {item['path']}：声明 {before} → 现在 {after}"
            f"（+{item['added']} / -{item['removed']} 行）"
        )
        lines.extend(item["detail"])
        owners = _claim_owners(item["path"], declared_at)
        if owners:
            lines.append("    可能属于：" + "、".join(owners[:5]))
        else:
            lines.append("    可能属于：claim 台账里该路径在声明之后没有归属记录（无法推断）")
    if waived:
        lines.append(
            "豁免已留痕：理由与上面逐文件的增删行数会写进提交说明的 "
            "foreign-changes 段（事后可查是哪几行、按什么理由带走的）。"
        )
    else:
        lines.append(
            "处置：等对方落库或撤回后重新 --declare 再提交；确认要一起带走时用 "
            "--allow-foreign-changes \"<理由>\"（理由会写进提交说明）。"
        )
    return changed, "\n".join(lines)


def _foreign_trailer(message: bytes, changed: list[dict], reason: str) -> bytes:
    """豁免必须留痕：理由 + 逐文件增删行数写进提交说明。"""
    lines = ["", "", f"foreign-changes: {reason}"]
    for item in changed:
        before = item["before"][:8] or "<无>"
        after = item["after"][:8] or "<无>"
        lines.append(
            f"- {item['path']} 声明后 +{item['added']} / -{item['removed']} 行"
            f"（{before} → {after}）"
        )
    return message.rstrip(b"\n") + ("\n" + "\n".join(lines) + "\n").encode("utf-8")


def _git_dir() -> Path | None:
    """仓库的 git 目录（worktree 下 ``.git`` 可能是个文件，交给 git 解析）。"""
    proc = _run(["rev-parse", "--absolute-git-dir"], _plain_env())
    if proc.returncode != 0:
        return None
    raw = proc.stdout.decode("utf-8", "replace").strip()
    return Path(raw) if raw else None


def _shared_message_error(source: Path) -> str:
    """``--message-file`` 落在 ``.git/`` 里就拒绝：那是全仓共享的可变路径。

    ``b898f4a3`` 的入口就是共享消息路径：别的进程写进去的消息被这一笔原样
    读走。``.git/COMMIT_EDITMSG`` 尤其危险——任何 ``git commit`` 都会覆写它。
    """
    git_dir = _git_dir()
    if git_dir is None:
        return ""
    try:
        resolved = source.resolve()
    except OSError:
        resolved = source.absolute()
    try:
        rel = resolved.relative_to(git_dir.resolve())
    except ValueError:
        return ""
    return (
        f"消息文件位于 .git 目录内（{rel.as_posix()}）：这是全仓共享的可变路径，"
        "任何 git commit 都可能覆写它；请把消息写到仓库外、本进程独有的文件。"
    )


def _recheck_message_source(source: Path, expected: bytes) -> str:
    """提交前重读来源：与启动时读到的字节不同就拒绝（不重试）。

    比对端是**本进程启动时留存的那份字节**，不是磁盘现状；所以这里比的是
    「来源文件有没有在两个步骤之间被改写」，而不是「现在的文件长什么样」。
    """
    try:
        now = source.read_bytes()
    except OSError as exc:
        return f"消息源在提交前读不出来：{source}（{exc}）；本笔不提交"
    if now == expected:
        return ""
    return (
        f"消息源在提交前被改写：{source}（启动时 {len(expected)} 字节 / "
        f"现在 {len(now)} 字节）；两个进程共用同一个消息路径时，读到的可能"
        "不是本笔要提交的说明。本笔不提交，请改用本进程独有的消息文件后重跑。"
    )


def _message_copy_path() -> Path:
    """本进程私有消息副本的路径（落在 locks 下，不进仓库）。"""
    return LOCKS_DIR / "messages" / f"{_session_key()}.{os.getpid()}.msg"


def _write_message_copy(message: bytes) -> Path:
    """把最终消息（含各类尾段）落成本进程私有副本，作为提交与复核的唯一载体。"""
    path = _message_copy_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Windows 下 os.open 默认是文本模式，会把消息里的 \n 再翻成 \r\n；
    # 副本必须与传入字节逐字一致，所以显式带 O_BINARY。
    flags = os.O_CREAT | os.O_TRUNC | os.O_WRONLY | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags, 0o600)
    try:
        os.write(fd, message)
    finally:
        os.close(fd)
    return path


def _stored_message(env: dict) -> tuple[bytes | None, str]:
    """从 commit 对象里取回落库的消息原文（按显式 sha 取，不依赖 HEAD 时序）。"""
    sha = _run(["rev-parse", "HEAD"], env).stdout.decode("utf-8", "replace").strip()
    if not sha:
        return None, ""
    proc = _run(["cat-file", "commit", sha], env)
    if proc.returncode != 0:
        return None, sha
    _, sep, body = proc.stdout.partition(b"\n\n")
    return (body if sep else None), sha


def _verify_message(env: dict, copy: Path) -> str:
    """提交后逐字复核：``7a76b097`` 那种「主题与内容不符」必须可见。

    比对端是本进程私有副本（最终消息的落盘拷贝），不是 ``--message-file``
    指向的共享文件——后者在提交窗口里可能已被别人覆写（``b898f4a3``）。
    """
    try:
        message = copy.read_bytes()
    except OSError as exc:
        return (f"本进程消息副本读不出来：{copy}（{exc}）；这笔已落库，"
                "必须按事故卡处理")
    stored, sha = _stored_message(env)
    if stored == message:
        print(f"[message] 提交信息逐字校验一致（{sha[:12]}，{len(message)} 字节，"
              "比对端为本进程私有副本）")
        return ""
    got = (stored or b"").decode("utf-8", "replace")
    want = message.decode("utf-8", "replace")
    detail = [f"提交信息与传入内容不一致：传入 {len(message)} 字节 / 落库 "
              f"{len(stored) if stored is not None else '<读不出来>'} 字节"]
    detail.append(f"  落库提交：{sha[:12] or '<读不出 HEAD>'}")
    detail.append(f"  传入主题：{want.splitlines()[0] if want.splitlines() else '<空>'}")
    detail.append(f"  落库主题：{got.splitlines()[0] if got.splitlines() else '<空>'}")
    for i, (a, b) in enumerate(zip(want, got)):
        if a != b:
            detail.append(f"  首个差异在第 {i} 个字符：传入 {a!r} / 落库 {b!r}")
            break
    else:
        if len(want) != len(got):
            detail.append(f"  公共前缀一致，长度差 {abs(len(want) - len(got))} 个字符"
                          "（某一段被追加或截断）")
    detail.append(
        "  这笔已落库，必须按事故卡处理：`git log -1` 核对，必要时 "
        "`git commit --amend -F <原消息>` 改回来；别让错消息烂在历史里。"
    )
    return "\n".join(detail)


# ---------------------------------------------------------------------------
# §6.1 登记行：闸只看暂存区，这里连工作区一起看（形态⑲）
# ---------------------------------------------------------------------------

COORD_SECTION = "### 6.1 当前占用"
COORD_TABLE_HEAD = "| 文件 | 线名"


def _coord_doc_rel() -> str:
    """登记文档的仓库相对路径（与 pre-commit 同用 ``MBDSDR_COORD_DOC``）。"""
    raw = os.environ.get("MBDSDR_COORD_DOC")
    if not raw:
        return "docs/COORDINATION.md"
    try:
        return Path(raw).resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return Path(raw).as_posix()


def _coord_module():
    """按需导入闸门的判定实现：两边共用一套语义，不各写一份。

    放在函数里是因为 ``check_claim_coordination.py`` 是可选的登记一致性设施
    （不是凭据/删除那种安全闸）：缺了它本脚本仍要能提交，只在缺它时说明降级。
    """
    try:
        import check_claim_coordination as cc
        return cc
    except ImportError:
        scripts = str(Path(__file__).resolve().parent)
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        import check_claim_coordination as cc
        return cc


def _index_text(env: dict, rel: str) -> str | None:
    """私有 index 里该文件的内容；index/HEAD 都没有时返回 None。"""
    for spec in (f":{rel}", f"HEAD:{rel}"):
        proc = _run(["show", spec], env)
        if proc.returncode == 0:
            return proc.stdout.decode("utf-8", "replace")
    return None


def _coord_row_lines(text: str) -> list[str]:
    """§6.1 表格里的数据行原文（表头/分隔行除外），保持文档里的顺序。"""
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines)
                  if ln.strip().startswith(COORD_SECTION)), None)
    if start is None:
        return []
    out: list[str] = []
    started = False
    for ln in lines[start + 1:]:
        stripped = ln.strip()
        if stripped.startswith("|"):
            started = True
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
                continue
            if not cells or cells[0] in ("文件", ""):
                continue
            out.append(stripped)
            continue
        if started and stripped:
            break
    return out


def _insert_table_rows(text: str, rows: list[str]) -> str:
    """把行插到 §6.1 表格末尾；文档其余部分一个字节都不动。"""
    lines = text.splitlines(keepends=True)
    start = next((i for i, ln in enumerate(lines)
                  if ln.strip().startswith(COORD_SECTION)), None)
    if start is None:
        raise ValueError(f"找不到 {COORD_SECTION}")
    pos = start + 1
    # §6.1 标题与表格之间允许多段说明文字（「追加登记行不必占整文件」等），
    # 只要还没遇到下一个标题就继续往后找。原来只容忍空行，文档一加说明就报
    #「下面没有表格」，逼人手工插行——工具不该把正常文档结构当成错误。
    while pos < len(lines):
        stripped = lines[pos].lstrip()
        if stripped.startswith("|"):
            break
        if stripped.startswith("#"):
            raise ValueError(f"{COORD_SECTION} 下面没有表格")
        pos += 1
    if pos >= len(lines):
        raise ValueError(f"{COORD_SECTION} 下面没有表格")
    end = pos
    while end < len(lines) and lines[end].lstrip().startswith("|"):
        end += 1
    eol = "\r\n" if lines[end - 1].endswith("\r\n") else "\n"
    payload = "".join(row.rstrip("\r\n") + eol for row in rows)
    return "".join(lines[:end]) + payload + "".join(lines[end:])


def _covers(cc, line: str, pair: tuple[str, str]) -> bool:
    """这一行是否覆盖这个 (路径, owner) 对：解析与覆盖判定都走闸门那一套。"""
    synthetic = "\n".join([
        COORD_SECTION,
        COORD_TABLE_HEAD + " | 开始时间 | 预计提交 hash |",
        "| --- | --- | --- | --- |",
        line,
    ])
    try:
        rows = cc.parse_coordination_rows(synthetic)
    except ValueError:
        return False
    return bool(rows) and not cc.uncovered_pairs([pair], rows)[0]


def _coord_missing_report(cc, missing, doc_abs: Path) -> str:
    lines = [
        "拒绝提交：本笔的活跃 claim 在 §6.1 还没有登记行。",
        "pre-commit ④ 只看暂存区，看不到躺在工作区里的行；这里连工作区一起看，"
        "免得登记行掉队、下次被别人顺手带走。",
    ]
    for path, owner, pattern in missing:
        lines.append(f"  - {path} owner={owner}"
                     + ("（仅目录级登记；代码/共享文档必须逐条）" if pattern else ""))
    lines.append("补法（工具不代写登记行，签名权在占着文件的人手上）：")
    seen: set[str] = set()
    for path, owner, _pattern in missing:
        cmd = cc.append_command(path, owner, doc_abs, ROOT)
        if "--root" not in cmd:
            # 临时树/从别处调用时，默认 root 会指错仓库；补上才是可粘贴执行的。
            cmd += f' --root "{ROOT}"'
        if cmd in seen:
            continue
        seen.add(cmd)
        lines.append(f"  {cmd}")
    lines.append("补完重跑同一条 safe_commit 命令即可：这行会被「只带这一行」放进本笔。")
    return "\n".join(lines)


def _coordination_step(env: dict, files: list[str], doc_rel: str) -> tuple[int, str, list[str]]:
    """闸需要 §6.1 行时：工作区有就只带这一行，没有就拒绝并给命令。

    返回 (退出码, 说明, 额外进本笔的路径)。判定完全复用闸门实现，不另写一份：
    两条路各写一半，就会出现「闸说缺行、工具说不用」的死循环。
    """
    doc_abs = ROOT / doc_rel
    if not doc_abs.is_file():
        return 0, "", []
    try:
        cc = _coord_module()
    except ImportError:
        return 0, "[coord] 找不到 check_claim_coordination.py，跳过 §6.1 检查" \
                  "（pre-commit 侧仍会按暂存区拦）", []
    try:
        doc_text = doc_abs.read_text(encoding="utf-8")
        rows = cc.parse_coordination_rows(doc_text)
    except (OSError, ValueError) as exc:
        return 0, f"[coord] {doc_rel} 读/解析失败（{exc}），跳过 §6.1 检查" \
                  "（pre-commit 侧仍会按暂存区拦）", []

    staged = {cc._norm(f) for f in files}
    # 登记面自身的规范化键：与闸 main() 里 ``self_key`` 同一判据（同一个 _norm）。
    # 闸把它从 pairs 和 rest 里都排除；只排 rest 会让「文档被别人持有时，
    # 本笔连文档一起提交」变成给登记面自己要登记行——死循环。
    self_key = cc._norm(doc_rel)
    active = cc.active_claims_for_paths(sorted(staged), marker_dir=LOCKS_DIR / "claims")
    pairs = [(path, owner) for path, owner in active
             if path in staged and path != self_key]
    rest = staged - {path for path, _owner in pairs} - {self_key}
    owner = cc.resolve_owner(ROOT) if rest else ""
    recent = (cc.recently_released_claims(
        LOCKS_DIR / "claims.json", sorted(rest), owner) if rest else [])
    required = pairs + [(path, o) for path, o, _at in recent]
    if not required:
        return 0, "", []

    missing, _patterns = cc.uncovered_pairs(required, rows)
    if missing:
        return 5, _coord_missing_report(cc, missing, doc_abs), []

    # 工作区已有行：再看暂存区那份有没有。有就什么都不用做（本笔本来就带了）。
    index_text = _index_text(env, doc_rel)
    if index_text is None:
        return 5, (f"拒绝提交：{doc_rel} 既不在私有 index 也不在 HEAD 里，"
                   "没法只带登记行；请把整份文档声明进 --files。"), []
    index_rows: list = []
    try:
        index_rows = cc.parse_coordination_rows(index_text)
    except ValueError:
        index_rows = []
    missing_in_index, _ = cc.uncovered_pairs(required, index_rows)
    if not missing_in_index:
        return 0, "", []

    # 只挑「覆盖本笔」的那几行；同一行可能同时覆盖多条 claim，不重复带。
    candidates = _coord_row_lines(doc_text)
    picked: list[str] = []
    unresolved = []
    for path, owner, pattern in missing_in_index:
        line = next((row for row in candidates if _covers(cc, row, (path, owner))), None)
        if line is None:
            unresolved.append((path, owner, pattern))
        elif line not in picked:
            picked.append(line)
    if unresolved:
        return 5, _coord_missing_report(cc, unresolved, doc_abs), []

    try:
        new_text = _insert_table_rows(index_text, picked)
    except ValueError as exc:
        return 5, f"拒绝提交：无法把 §6.1 行放进暂存区（{exc}）。请整文件带上 {doc_rel}。", []

    blob = _run(["hash-object", "-w", "--stdin"], env, new_text.encode("utf-8"))
    if blob.returncode != 0:
        return 5, "拒绝提交：§6.1 行写入对象库失败（git hash-object 出错）", []
    mode = "100644"
    mode_proc = _run(["ls-files", "-s", "--", doc_rel], env)
    if mode_proc.returncode == 0 and mode_proc.stdout.strip():
        mode = mode_proc.stdout.decode().split()[0]
    upd = _run(
        ["update-index", "--add", "--cacheinfo",
         f"{mode},{blob.stdout.decode().strip()},{doc_rel}"],
        env,
    )
    if upd.returncode != 0:
        return 5, f"拒绝提交：§6.1 行 update-index 失败：{upd.stderr.decode('utf-8', 'replace')[:200]}", []

    report = (
        f"[coord] §6.1 行只在工作区、没进暂存区：只带这 {len(picked)} 行进本笔"
        f"（{doc_rel}），工作区其它改动原样留在外面"
    )
    for row in picked:
        report += f"\n    + {row}"
    return 0, report, [doc_rel]


def _attempt(
    files: list[str],
    message: bytes,
    index: Path,
    exclusions: list[dict] | None = None,
    exclude_owner: str = "",
    declared: dict | None = None,
    declared_at: float = 0.0,
    allow_reason: str = "",
    message_source: Path | None = None,
    source_bytes: bytes = b"",
) -> tuple[int, str]:
    """一次完整尝试。返回 (退出码, 说明)。"""
    exclusions = exclusions or []
    if index.exists():
        index.unlink()
    env = _index_env(index)

    base = _run(["rev-parse", "HEAD"], env).stdout.decode().strip()

    # 1) 用当时的 HEAD 建 index（形态⑧/⑪：绝不复用陈旧 index）
    read_tree = _run(["read-tree", "HEAD"], env)
    if read_tree.returncode != 0:
        _emit(read_tree, "read-tree")
        return 1, "read-tree 失败"

    # 2) 只 add 声明文件；工作区内容即 index 内容
    add = _run(["add", "--", *files], env)
    if add.returncode != 0:
        _emit(add, "git add")
        return 1, f"git add 失败：{add.stderr.decode('utf-8', 'replace')[:200]}"

    # 2b) 排除他人 hunk：只从私有 index 反向撤掉，工作树不动
    if exclusions:
        for path in sorted({e["path"] for e in exclusions}):
            ranges = [e for e in exclusions if e["path"] == path]
            try:
                _revert_index_hunks(env, path, ranges)
            except ValueError as exc:
                return 1, str(exc)
            print(
                f"[exclude] {path}: "
                + ", ".join(f"{e['start']}-{e['end']}" for e in ranges)
                + (f"（owner={exclude_owner}）" if exclude_owner else "")
            )
        env[EXCLUDE_ENV] = json.dumps(
            [
                {
                    "path": e["path"],
                    "start": e["start"],
                    "end": e["end"],
                    "owner": exclude_owner,
                }
                for e in exclusions
            ],
            ensure_ascii=False,
        )
        env["MBDSDR_DECLARED_INDEX"] = str(index)
        message = _exclusion_trailer(message, exclusions, exclude_owner)

    # 2c) §6.1 登记行：闸只看暂存区，行躺在工作区就等于「该带的一条没带」。
    #     工作区有 → 只把那几行写进私有 index；没有 → 拒绝并给补登记命令。
    coord_code, coord_note, coord_extras = _coordination_step(
        env, files, _coord_doc_rel())
    if coord_note:
        print(coord_note)
    if coord_code != 0:
        return coord_code, coord_note
    expect_files = files + [
        p for p in coord_extras
        if p not in files and p not in {f.replace("\\", "/") for f in files}
    ]

    staged = _staged_set(env)
    expect = []
    for f in expect_files:
        in_head = _run(["cat-file", "-e", f"HEAD:{f}"], env).returncode == 0
        # 删除文件同样要能提交：HEAD 有、工作区没有 → D（旧实现一律按 M 期望，
        # 于是任何「删掉某文件」的提交都被判成文件集不符，退不回去）。
        in_wt = (ROOT / f).exists()
        mark = "D" if (in_head and not in_wt) else ("M" if in_head else "A")
        expect.append(f"{mark}\t{f}")
    if sorted(staged) != sorted(expect):
        extra = [s for s in staged if s not in expect]
        missing = [e for e in expect if e not in staged]
        if exclusions and missing:
            names = [m.split("\t", 1)[-1] for m in missing]
            return 1, (
                f"排除 hunk 后文件无剩余改动：{names}"
                "（把不该提交的 hunk 全排除了，或声明范围过大）"
            )
        return 1, f"文件集不符：多 {extra} 缺 {missing}"

    # 2d) 意图删除集：私有 index 里「HEAD 有、新索引没有」的路径就是本次真删
    #     的文件，自动导出给 pre-commit 闸门（闸门对未声明的删除一律拒绝）。
    deletions = [line.split("\t", 1)[1] for line in staged if line.startswith("D\t")]
    if deletions:
        env[INTENT_ENV] = "\n".join(deletions)
        print(f"[intent-delete] 自动声明 {len(deletions)} 个删除：{', '.join(deletions)}")

    # 3) 记录待提交 blob，供提交后复核（形态⑩）
    #    用 --verify --quiet 取：删除项（index/HEAD 都没有该路径）返回空，
    #    否则 `rev-parse :path` 会把参数原样回显，删除项必被误判成 blob 不一致。
    wanted = {f: _blob(env, f":{f}") for f in expect_files}

    # 4) 提交窗口加锁：读 HEAD → commit → 作用域机检 必须串行，
    #    否则「建 index 时的父」和「真提交时的父」可以是两个（形态⑬）。
    try:
        lock = _CommitLock().__enter__()
    except TimeoutError as exc:
        return 1, str(exc)
    try:
        return _commit_locked(
            expect_files, message, env, base, wanted,
            declared or {}, declared_at, allow_reason,
            message_source or Path(), source_bytes,
        )
    finally:
        lock.__exit__(None, None, None)


def _commit_locked(
    files: list[str],
    message: bytes,
    env: dict,
    base: str,
    wanted: dict[str, str],
    declared: dict,
    declared_at: float,
    allow_reason: str,
    message_source: Path,
    source_bytes: bytes,
) -> tuple[int, str]:
    """锁内：复核声明快照 → 重校验基线 → commit → 作用域/message 机检 → blob 复核。"""
    # 4a) 声明快照：提交时刻的内容必须等于声明时刻的那份。不一致就拒绝（不重试），
    #     因为漂移的内容不会因为多试几次自己回去；豁免必须带理由并落进提交说明。
    if declared:
        changed, report = _verify_declaration(
            env, declared, declared_at, waived=bool(allow_reason))
        if changed:
            if not allow_reason:
                return 4, report
            print(report)
            message = _foreign_trailer(message, changed, allow_reason)
        else:
            print(f"[declare] 声明快照无漂移（{len(declared)} 个文件，"
                  f"声明于 {_iso(declared_at)}）")

    # 4) 提交前重校验基线：HEAD 变了就整盘重建
    now = _run(["rev-parse", "HEAD"], env).stdout.decode().strip()
    if now != base:
        return 1, f"基线在提交前变化：{base[:8]} → {now[:8]}（下次重试用新 HEAD 重建）"

    # 4b) 消息源复核：来源可能是共享路径，提交前确认它自本进程读入后没被改写；
    #     比对端是启动时留存的字节，不是磁盘现状（``b898f4a3``）。
    bad_source = _recheck_message_source(message_source, source_bytes)
    if bad_source:
        return 6, bad_source

    # 4c) 落成本进程私有副本（含各类尾段）：提交与复核都只认这份拷贝，
    #     不再回头读 ``--message-file``——它可能已经被第三方覆写。
    copy = _write_message_copy(message)
    message = copy.read_bytes()

    # 5) 提交（消息走 stdin bytes，避开编码坑；--cleanup=verbatim 保证落库逐字等于传入）
    commit = _run(["commit", "--cleanup=verbatim", "-F", "-"], env, stdin=message)
    if commit.returncode != 0:
        _emit(commit, "git commit")
        err = (commit.stderr + commit.stdout).decode("utf-8", "replace")
        if any(h in err for h in LOCK_HINTS):
            return 1, f"撞 ref 锁：{err.strip()[:160]}（下次重试用新 HEAD 重建）"
        return 1, f"git commit 失败：{err.strip()[:300]}"
    _report_hooks(commit, env)

    # 5a) 提交信息逐字复核：落库消息必须等于本进程留存的副本；不一致按事故
    #     报出（退出码 3），不能烂在历史里。
    bad_message = _verify_message(env, copy)
    if bad_message:
        return 3, bad_message

    # 5b) 作用域机检：本笔只能包含 --files 声明的路径，且父提交就是本次基线。
    #     共享 index 被别线暂存污染时，这里会在「已提交」之后红着脸报出来
    #     （退出码 3），而不是等下一个人对着 blame 猜「这行是谁的」。
    parent = _run(["rev-parse", "HEAD^"], env).stdout.decode().strip()
    changed = _run(
        ["diff-tree", "--no-commit-id", "--name-only", "-r", "--no-renames", "HEAD"],
        env,
    )
    actual = sorted(
        p for p in changed.stdout.decode("utf-8", "replace").splitlines() if p
    )
    declared = sorted(files)
    if parent != base:
        # 锁内仍被绕过（有人手敲 git commit）：这笔已落到别人的父上，按事故报
        return 3, (
            f"提交父不是本次基线：{parent[:8]} / {base[:8]}"
            f"（有进程绕过提交锁）；本笔改动 {len(actual)} 项 {actual[:8]}"
        )
    if actual != declared:
        extra = sorted(set(actual) - set(declared))
        missing = sorted(set(declared) - set(actual))
        return 3, (
            f"提交作用域不符：声明 {len(declared)} 项 / 实际 {len(actual)} 项"
            f" 多 {extra} 缺 {missing}"
        )

    # 6) 提交后复核：HEAD 里每个文件的 blob 必须等于暂存时的 blob
    #    （删除项两边都为空，相等即通过）
    bad = []
    for f, blob in wanted.items():
        head_blob = _blob(env, f"HEAD:{f}")
        if head_blob != blob:
            bad.append(f"{f}（暂存 {blob[:8] or '<删除>'} / "
                       f"HEAD {head_blob[:8] or '<删除>'}）")
    if bad:
        return 3, "提交后 blob 不一致：" + "；".join(bad)

    head = _run(["rev-parse", "--short", "HEAD"], env).stdout.decode().strip()
    return 0, f"已提交 {head}，blob 逐文件复核一致"


def _build_parser() -> argparse.ArgumentParser:
    """命令行。``--files`` 用 extend：重复传参合并，绝不「只取最后一个」。"""
    ap = argparse.ArgumentParser(description="私有 index 安全提交（形态⑪）")
    ap.add_argument(
        "--files",
        nargs="+",
        action="extend",
        required=True,
        help="本笔要提交的文件（精确清单）；可重复传，按出现顺序合并",
    )
    ap.add_argument("--message-file", help="提交信息文件（UTF-8）；--declare 时不需要")
    ap.add_argument(
        "--declare",
        action="store_true",
        help="只记录声明快照（不提交）：写入后立刻执行，把「声明时的内容」钉住",
    )
    ap.add_argument(
        "--allow-foreign-changes",
        default="",
        metavar="REASON",
        help="声明后内容有变化仍要提交时的豁免理由（会写进提交说明，不能留空）",
    )
    ap.add_argument("--retries", type=int, default=3, help="撞锁/基线变化时的重建次数")
    ap.add_argument(
        "--exclude-hunk",
        action="append",
        default=[],
        metavar="PATH:START-END",
        help="从私有 index 排除的 hunk 或 hunk 内的行范围（可重复）；工作树保持原样",
    )
    ap.add_argument("--exclude-owner", default="", help="排除 hunk 的归属线（可留空）")
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = _build_parser()
    args = ap.parse_args(argv)

    # 只剥掉真正的 "./" 前缀：lstrip("./") 会把点文件削成无点名字
    # （.gitattributes → gitattributes），这类文件根本提交不了。
    files = [f.replace("\\", "/") for f in args.files]
    files = [f[2:] if f.startswith("./") else f for f in files]
    # 重复声明按首次出现顺序去重；声明集合必须原样打出——这份清单就是作用域
    # 断言的全部依据，静默截短一次就会「内容没丢、分两笔」地骗过所有检查。
    uniq = list(dict.fromkeys(files))
    if len(uniq) != len(files):
        print(f"[files] 重复路径已合并：{len(files)} → {len(uniq)} 个")
    print(f"[files] 本次声明 {len(uniq)} 个文件：{', '.join(uniq)}")
    files = uniq
    _claim_hint(files)

    # 只声明不提交：把当前工作区内容钉成快照，供之后（可能跨进程）的提交比对。
    if args.declare:
        try:
            snapshot, stamp = _write_declaration(files, _plain_env())
        except ValueError as exc:
            print(f"拒绝：{exc}")
            return 1
        for path, item in snapshot.items():
            print(f"[declare] {path}：blob {item['blob'][:8] or '<缺失>'}，"
                  f"{item['lines']} 行")
        print(f"[declare] 已记录声明快照（{_iso(stamp)}，session={_session_key()}）；"
              f"提交时用同一文件集即可比对，提交成功后才清除")
        return 0

    if not args.message_file:
        ap.error("--message-file 必填（只有 --declare 可以不传）")
    message_source = Path(args.message_file)
    shared = _shared_message_error(message_source)
    if shared:
        print(f"拒绝：{shared}")
        return 1
    try:
        message = message_source.read_bytes()
    except OSError as exc:
        print(f"拒绝：读不到消息文件 {message_source}（{exc}）")
        return 1
    digest = hashlib.sha256(message).hexdigest()
    print(f"[message] 来源：{message_source}（sha256 {digest[:12]}…，"
          f"{len(message)} 字节；提交前会重读，变了就拒）")
    _message_coverage_hint(message, files)

    try:
        exclusions = _parse_exclusions(args.exclude_hunk, files)
    except ValueError as exc:
        print(f"拒绝：{exc}")
        return 1

    # 声明快照：优先用 --declare 钉住的那份；没有就退化为本次启动时刻，
    # 并明确说出窗口边界（只覆盖本次运行），避免让人误以为跨进程也受保护。
    found = _load_declaration(files)
    if found:
        declaration_file, declared, declared_at = found
    else:
        declaration_file = None
        declared = {path: _fingerprint(_plain_env(), path) for path in files}
        declared_at = time.time()
        print(f"[declare] 无预先声明快照：以本次启动 {_iso(declared_at)} 的工作区为基线"
              "（只覆盖本次运行；写入后先跑 --declare 才能拦住「释放 claim → 提交」这段漂移）")

    # 行号是坐标，坐标只在它被计算的那一刻有效：``--exclude-hunk`` 的行号是
    # 按某个时刻的 hunk 算的，没有声明快照就只能拿「本次启动」当基线，
    # 别人后来追加的行会被静默算进基线（``ed3c09e5``）。所以必须成对使用。
    if exclusions and declaration_file is None:
        print(
            "拒绝提交：--exclude-hunk 必须配 --declare。\n"
            "  行号是坐标，坐标只在它被计算的那一刻有效：20:58 算出的 hunk\n"
            "  +2100,6 到 21:02 已被别线追加的 4 行顶成 +2100,10（ed3c09e5），\n"
            "  排掉 2100-2103 后把 2106-2109 一起带走了。没有声明快照时，\n"
            "  ⑰ 的基线退化成「本次启动时刻的工作区」，新行被算进基线，程序一个字都不报。\n"
            "  先跑声明（在写入/排除之前）：\n"
            f"    python scripts/safe_commit.py --files {' '.join(files)} --declare"
        )
        return 7

    index = _index_path()
    keep_copy = False
    print(f"[index] 会话私有 index：{index}")
    _warn_shared_index()

    try:
        for n in range(1, args.retries + 1):
            code, note = _attempt(
                files, message, index, exclusions=exclusions,
                exclude_owner=args.exclude_owner,
                declared=declared, declared_at=declared_at,
                allow_reason=args.allow_foreign_changes.strip(),
                message_source=message_source, source_bytes=message,
            )
            print(f"[try {n}] {note}")
            if code == 0:
                _drop_declaration(declaration_file)
                return 0
            if code in (3, 4, 5, 6, 7):
                keep_copy = code in (3, 6)
                return code
        print(f"重试 {args.retries} 次仍未提交，已放弃（工作区与 index 未改动）")
        return 2
    finally:
        if index.exists():
            index.unlink()
        copy = _message_copy_path()
        if copy.exists():
            if keep_copy:
                print(f"[message] 本进程私有副本保留：{copy}")
            else:
                copy.unlink()


if __name__ == "__main__":
    sys.exit(main())
