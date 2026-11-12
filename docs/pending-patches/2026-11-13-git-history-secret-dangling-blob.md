# 裁定记录：历史扫描发现的那个不可达 blob —— 走 A，保留不清理

> 日期：2026-11-13；状态：**已裁定**（用户裁定：路 A）
> 事实（blob sha、打码指纹、覆盖口径、解除条件）的真值源在
> `docs/code-guards.md`「凭据历史扫描 / 已知良性登记」一节。本文只记**裁定与代价**，
> 不再复述事实，避免两处各写一份后漂移。

## 裁定

**不清理、不改写历史。** 那个 blob 按「已知良性」登记
（`scripts/secret_scan.py::KNOWN_BENIGN_HISTORY`），只在三个条件同时成立时放行：
sha 完全一致 + 规则名一致 + 没有任何提交引用它（不可达）。同一规则命中可达 blob、
或命中另一个 sha，一律照旧报 `HISTORY`。

## 为什么不是路 B（`git gc --prune=now`）

- 它会把 **103 个 unreachable commit / 303 tree / 112 blob** 一起不可逆删掉，不只是
  这一个 blob；这些多是 rebase/amend 前的旧提交，万一有人要 rescue 就没了。
- 换来的是「少一条历史告警」，而那条告警本身是合成值（36 位 token 体字符全同），
  收益远小于不可逆损失。
- `git prune` 同样只按「不可达」整体清理，git 不支持按单个 blob 定点删除。

## 为什么也不是「改写历史」

该 blob 不在任何提交里，没有任何历史可改写。**若哪天判定它是真值，正确动作是
轮换**（它从未进入可达提交，历史不需要动），而不是 `filter-repo`。

## 这条登记怎么失效（写死，避免变成永久噪音）

1. 同一规则在**可达** blob 上命中；
2. 命中**另一个 sha**；
3. 该 blob 出现在任何路径（被某个 tree/提交引用）。

任何一条发生，`_split_known_benign` 就不再放行，`--history` 恢复报 `HISTORY`；
`tests/test_release_secrets.py::test_known_benign_requires_registered_sha_rule_and_unreachable`
钉住这三条。
