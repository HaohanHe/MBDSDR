# 备份推进规矩（2026-11-11 立）：`wip/2026-11-11` 收工必同步

> 性质：**备份推进，不是发布**。它不碰 `main`、不打 tag、不触发发布，
> 唯一目的是「机器出事时提交不只剩 reflog」。
> 归属：本文件由 COORDINATION 持有线稍后并入正文；本文只给条文，不改 COORDINATION。

## 0. 为什么要有这条

本仓主工作区长期处于 **detached HEAD**。2026-11-11 实测：其上领先 `main`
一百多笔（`git rev-list --left-right --count main...HEAD` = `1 171`），而
`git branch -a --contains HEAD` 当时**没有任何分支**包含它——游离指针一旦被
checkout 或 `git gc` 扫过，提交就只剩 reflog。

已建并已推：`wip/2026-11-11` → `origin/wip/2026-11-11`（本地与远端同哈希，
2026-11-11 23:5x 实测 `146b8033`）。

## 1. 收工前的固定动作（每条线都适用）

```
git merge-base --is-ancestor wip/2026-11-11 HEAD   # 先验只前进
git branch -f wip/2026-11-11 HEAD
git push origin wip/2026-11-11:wip/2026-11-11
git rev-list --count <上次推送的 tip>..HEAD         # 报「本次前进 N 笔」
```

## 2. 四条硬约束

1. `git branch -f` 前必须 `git merge-base --is-ancestor` 通过；不是祖先说明
   有人回退过或换过线，**停手报人**，不许硬推。
2. 只推 `wip/2026-11-11`。**禁止**顺手推 `main`：`main` 本地仍有一百多笔
   未推送，一次推上去等于把整条线一次性发布会。
3. 推完必须报「本地哈希 == `origin/wip/2026-11-11` 哈希」；不等就是没推成功。
4. 备份分支**不承载评审**：它是滚动快照，历史会被 `-f` 前移；不要基于它
   开 PR 迭代、不要把 CI 判据挂到它上面。

## 3. 哪些工作区要建自己的分支（2026-11-12 起生效）

今天晚上先按最小面改了 5 个还在提交的工作区（单独报备，不在本文范围）。
**从明天起**：任何新开的 worktree / 新起的线**一律先建自己的分支再干活**，
不许在 detached HEAD 上提交。命名：

```
wip/<线名>-<MMDD>        # 例：wip/settings-1112
```

理由：同一个分支**不能同时被两个 worktree 检出**，所有线共用
`wip/2026-11-11` 在 git 层面做不到（硬来要 `--ignore-other-worktrees`，
两条线同时提交即互相覆盖）。所以：**各线各分支，`wip/2026-11-11` 只做
主线的滚动备份快照。**

## 4. 清理旧 worktree（单独立项，本文不执行）

只读取证快照（2026-11-05 ~ 11-07 那批）维持 detached 即可，不产生新提交
就没有丢失风险。清理（`git worktree remove`）**单独排期**，动手前逐个人工
确认三件事：无未提交内容、无别线在用、其 HEAD 已在某条已推分支的可达历史里。
今晚不做。

## 5. 本机已知坑（2026-11-11 实测）

环境变量把 https 代理指向 `127.0.0.1:7897`，该端口未监听，直接 `git push`
报 `Failed to connect to github.com port 443 via 127.0.0.1`。一次性绕法
（不改 git 配置、token 不进命令与文件）：

```powershell
$env:HTTPS_PROXY=''; $env:HTTP_PROXY=''; $env:ALL_PROXY=''
git -c http.proxy= -c https.proxy= push origin wip/2026-11-11:wip/2026-11-11
```

直连 `github.com:443` 本机可达（`Test-NetConnection` = True）。凭据走已配置的
credential manager，命令里不出现 token。
