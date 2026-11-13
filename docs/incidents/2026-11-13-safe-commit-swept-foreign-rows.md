# 事故记录：safe_commit 提交时卷走他线 §6.1 登记行（2026-11-13）

状态：已定位；**内容正确、无丢失、无重复，但归属记错**；已裁定不改写历史；根因修复已派给另一条线。

## 一、事实

- 提交：`8982b4f9 docs(coordination): 补 §6.1 登记行，登记 ae98d46e 的四个锚点机制文件`。
- `git show --numstat 8982b4f9` = `3  0  docs/COORDINATION.md`，但声明（`--files`）与意图只有 1 行。
- 多出的两行属于「文档引用复核线/6ae85f56」，原文见该提交：

  ```text
  | `docs/parameter-unification.md` | 文档引用复核线/6ae85f56（引用按符号重定位 main_window.py:8191（行号二次漂移）） | 2026-11-13 20:10 | 本笔 |
  | `docs/PRD.md` | 文档引用复核线/6ae85f56（引用按符号重定位 main_window.py:7069（行号二次漂移）） | 2026-11-13 20:10 | 本笔 |
  ```

- 落点：`docs/COORDINATION.md` §6.1 占用表。行号以 `git show 8982b4f9 -- docs/COORDINATION.md` 为准，不要按当前文件行号找（§6.1 表会继续增长）。

## 二、根因

`safe_commit.py --files <path>` 在**提交时刻**读取工作区该文件的整份内容写入私有 index，而不是使用声明 `--files` 那一刻的内容快照。本次时序：

1. 锚点机制线 release 掉 `docs/COORDINATION.md`；
2. 文档引用复核线在同一工作区写入自己的两行（20:10）；
3. 锚点机制线执行 `safe_commit --files docs/COORDINATION.md`，整份工作区内容被一起带入。

即「声明了什么」与「提交了什么」之间没有任何绑定：声明之后新冒出来的内容不会被拒绝。

## 三、后果与当前状态

- **这两行现在挂在 `8982b4f9` 上，归属记错但内容正确、无丢失、无重复。**
- 文档引用复核线后续的 `a22d2de4`、`5eb0804e` 只改各自的 `docs/parameter-unification.md`、`docs/PRD.md`，没有重复提交这两行；`docs/COORDINATION.md` 里每行各只出现一次。
- 按文件追溯时，**不要**把 `docs/parameter-unification.md` / `docs/PRD.md` 的这次改动理解为锚点机制笔内的改动。

## 四、处置

- 已裁定**不改写历史**：rebase 几个中间提交的代价与风险大于这个归属瑕疵。
- 根因修复已派给另一条线：给 `safe_commit.py` 增加「声明快照 → 提交前比对」，声明之后新冒出来的内容一律拒绝。本文件只记录事故，不承载修复。
- `8982b4f9` 的内容本身已验证：`check_card_links.py` 16 条 PASS、生成物对账 7/7（详见该提交说明）。

## 五、定位方式

- 目录：`docs/incidents/`（每事故一份，日期前缀）。
- 关键词：`8982b4f9`、`文档引用复核线`、`safe_commit`、`声明快照`。
- 原始证据：`git show --stat 8982b4f9`、`git show --numstat 8982b4f9`。
