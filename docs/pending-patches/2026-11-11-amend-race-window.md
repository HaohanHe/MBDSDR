# 待并入台账：§6.9.17 amend 只能修内容，不能修窗口风险（第十七种形态）

> 状态：**pending**（落笔时 `docs/COORDINATION.md` 有他线未提交 hunk，按避让纪律
> 不追加；该文件提交干净后由本条线的收口动作原样贴入 §6.9.17 并删除本文件）。
> 归属：WorkBuddy 线。日期：2026-11-11。

## 事故

同一小时里，我用私有 index 分步提交：

1. `git read-tree HEAD` 建 index（当时 HEAD = X）；
2. 在他线落 `8d8e3e6b`（5 文件，439+/107−）之后，我用**旧基线**的 index 提交了
   `88314e2d`；该提交的树相对新 HEAD 是「旧树 + 我的 8 文件」，
   于是 `8d8e3e6b` 的全部改动被**静默回退**——`git show --stat 88314e2d`
   显示 13 个文件、−447 行，其中 5 个文件的删除不属于我；
3. 我用 `git commit --amend` 重建 index（基于 `8d8e3e6b` 的树 + 我的 8 文件）
   得到 `68eb37db`，内容恢复了。

## 性质

**`amend` 只能修内容，不能修风险。** 事故根因不是「提交消息不对」或「多带了文件」，
而是 `read-tree HEAD` 与 `git commit` 之间存在一个**无锁窗口**：期间他线的提交
会让你的树变成「相对新 HEAD 的回退」。amend 只重写了那一条提交的内容，
**窗口本身还在**——只要还按「建 index → 稍后提交」的顺序干，下一次同样会卷。

## 30 秒认出

```
# 自己的提交里出现不属于自己的删除行
git show --stat <我的提交>            # 文件数/删行数明显大于自己的改动面
# 提交的父提交不是建 index 时的 HEAD
git rev-parse <我的提交>^             # 与建 index 前记录的 HEAD 比对，不等即中招
git log --oneline <旧HEAD>..<我的提交>^   # 中间落进来的他线提交
```

## 三步处置

1. 停，不 push；坏提交已入库用 `git revert`（或新提交修），**不改写历史**；
2. 内容确实丢了：以**当前** HEAD 重建 index 后，用一笔**新提交**把内容补回，
   提交说明写明「恢复被旧私有索引卷回的改动」；
3. 修完自证：`git diff <被卷提交> <修复提交> --stat` 逐文件贴出、
   对每个被卷文件的 blob 做 `8d8e3e6b` / 修复提交 / HEAD 三点比对、
   再跑该笔对应的测试。

## 规矩（写死）

1. **禁止 `git commit --amend`**（硬约束）。要修就新提交修或 revert；
2. **所有提交走 `scripts/safe_commit.py`**：它把「建 index → `--expect` 文件集校验 →
   基线复检 → 提交 → 提交后 blob 复核」串成一次原子操作，基线变了就整盘重建，
   撞 ref 锁重试，重试耗尽不提交；
3. 私有 index 的「提交前再取一次 HEAD」只是兜底，**不能替代** safe_commit 的整套闸门；
4. 反证：临时仓里「`read-tree` 后他线提交一笔 → 用 safe_commit 提交」，
   必须报「基线在提交前变化」并重建，而不是把旧树提交上去。

## 本次自证（实测）

- `git diff 8d8e3e6b 68eb37db --stat`：8 文件 46+/8−，全部是我的文件，
  无一个 `8d8e3e6b` 的文件；
- `8d8e3e6b` 的 5 个文件在 `68eb37db` 里 blob 逐一相同；当前 HEAD 中 4 个仍相同，
  `desktop/settings_panel.py` 只多了后续 `b28ddadf` 的服务启停文案（+20/−3）；
- `tests/test_config_store_single_writer.py`：`8d8e3e6b` 9 passed，当前 HEAD 9 passed；
- 相关 5 个测试文件在 HEAD 上 96 passed / 3 failed，同样 3 条在 `8d8e3e6b` 本身上
  同样失败、断言文案一致 → 存量红，不是回退。
