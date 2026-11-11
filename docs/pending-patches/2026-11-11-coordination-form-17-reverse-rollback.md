# 交接单：COORDINATION.md 形态登记 §6.9.17（反向卷回）

- 日期：2026-11-11
- 目标文件：`docs/COORDINATION.md`
- 阻塞原因：该文件工作区有**其他线未提交改动**（`git diff --stat -- docs/COORDINATION.md` = 37 insertions / 12 deletions，含一条「未提交 hunk 不得追加」规则的删除与一段 2026-11-11 20:20 的线内补记）。按「共有文件只提交自己的 hunk」与「不得夹带别人改动」，不能用 `scripts/safe_commit.py` 整文件提交。
- 处置：本单给出可直接粘贴的段落。**待该文件无外来未提交改动后**，插入到 §6.9.16 之后、「补记（2026-11-11 20:20）」之前，再用 `scripts/safe_commit.py --files docs/COORDINATION.md` 提交。

> 编号说明：文档现有 6.9.1–6.9.12、6.9.14–6.9.16，缺 6.9.13（正文无对应小节），故本笔顺延为 **6.9.17**，不占用 13。

---

## 待粘贴段落

### 6.9.17 私有索引 + 提交间隙 HEAD 推进 = 反向卷回（`b28ddadf`）

> 与 6.9.11 同源不同果：6.9.11 是「撞锁/提交失败」，这一形态是**提交成功但把别人刚修好的文件卷回旧内容**。

**判据（任一命中，按本形态处理）**

1. `git diff <该笔父提交> <该笔> --stat` 里出现**自己根本没改的文件**，且方向是回退到旧内容（不是新增）。
2. 被卷回文件的 blob 等于更早的提交，而不是该笔父提交：
   ```powershell
   git rev-parse <该笔>:<file>
   git rev-parse <该笔>^:<file>
   git rev-parse <上一笔恢复/修复提交>:<file>
   ```
   第一条与第三条相同、与第二条不同 = 卷回成立。
3. 提交信息声明的文件集与实际 `--name-only` 不一致，且多出的文件属于别线正在维护的路径。

**成因**

私有 index 在 T0 用 `HEAD0` 建好；T1 另一条线推进到 `HEAD1`（含对 X 的修复）；T2 直接 `git commit`。旧 index 里 X 仍是 `HEAD0` 的 blob，提交树于是把 X 写回旧内容。提交瞬间 `git status` 看不出异常——幻影 `D` 被 commit 吞掉，事后只能从「父提交 vs 本笔」的 diff 里看见。**「`read-tree` 后立即 commit 并重试」不算修复**，它同样看不见幻影 `D`。

**本次实例**

- `b28ddadf`（设置页端口文案）把 `desktop/ai_panel.py`、`desktop/station_sync.py`、`mbdsdr_ai/config.py`、`tests/test_config_store_single_writer.py` 四个路径卷回旧内容。
- 另一条线用 `b4ea081d` 按 `68eb37db` 逐个恢复；恢复后四文件与 `68eb37db` 零差异。
- 这是同类事故第五次，前四次分别登记在 6.9.8 / 6.9.10 / 6.9.11 / 6.9.14。

**`scripts/safe_commit.py` 的四道闸**

1. 每次尝试都 `read-tree HEAD` 重建 index，并先清掉继承的 `GIT_INDEX_FILE`（不继承会话级泄漏）。
2. `--files` 精确清单 + `git diff --cached --name-status --no-renames` 集合校验；多一个、少一个都退出 1，**不提交**。
3. 建 index 前记 `base`，提交前再读一次 `HEAD`；变了就整盘重建（默认 3 次），退出 1。
4. 提交后逐文件复核 `HEAD:<file>` blob 与暂存 blob；不一致退出 3（已提交，按事故卡处理）。

**事后反证（两行）**

```powershell
git diff b28ddadf^ b28ddadf --stat
git rev-parse b28ddadf:desktop/ai_panel.py 68eb37db:desktop/ai_panel.py
```

**纪律**

- 禁止自建 `GIT_INDEX_FILE` / 手工 `read-tree` 提交流程；一律 `python scripts/safe_commit.py --files ... --message-file ...`。
- 提交前先 `git diff <file>` 确认暂存内容不含别线的 hunk 或变异探针标记（如 `# MUTATION:`）。
- 在别人 claim 的文件上提交是越权，即使内容无冲突：先请求释放，或写 `docs/pending-patches/` 交接单。
