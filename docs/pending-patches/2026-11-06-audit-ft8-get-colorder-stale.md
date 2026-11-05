# 交接单：`docs/audit_r2/08_ft8_full.md:36` 关于 `get_colorder` 的过期记述

> 归属：审计线（`docs/audit_r2/**` 的 owner）。
> 本文件只是交接单，**未改动** `docs/audit_r2/08_ft8_full.md`；请审计线自行修订并决定是否销账。
> 提出方：冗余线。提出时间：2026-11-06。

## 1. 定位

- `path:line`：`docs/audit_r2/08_ft8_full.md:36`
- 所属小节：`## 2. [真bug] 对 (174,91) 错误施加了 colorder 置换`

## 2. 原文（逐字）

> **`ft8_decode.py:52-56`** `reorder_to_ldpc()` 调用 `ft8_ldpc.get_colorder()` 对 LLR 做反置换。

## 3. 正确事实（HEAD `e645b781` 实测）

1. `reorder_to_ldpc` 现位于 `mbdsdr_ai/ft8_decode.py:74-81`（不是 `:52-56`），实现为**恒等映射**：`return list(llr_cw)`，**不再调用** `ft8_ldpc.get_colorder()`。
2. 变更点是提交 `285a9863`（2026-09-24，`git show 285a9863 -- mbdsdr_ai/ft8_decode.py` 可见）：删掉 `colorder = ft8_ldpc.get_colorder()` 与按下标重排的列表推导，改为恒等返回，并在 docstring 写明「标准 FT8 (174,91) LDPC 码没有 colorder 置换；colorder 只属于旧的 (174,87) 码」。
3. `get_colorder` 现仅在 `mbdsdr_ai/ft8_ldpc.py:27` 定义，**全仓零调用方**（`git grep -w get_colorder HEAD` 代码命中只有该定义行）。
4. 因此「对 (174,91) 错误施加 colorder 置换」这一缺陷在当前 HEAD **已不存在**；该行记述应改为「历史缺陷，`285a9863` 已修」，或整节标注已销账。

## 4. 依据（可复现命令，HEAD `e645b781`）

```powershell
git grep -n -w get_colorder HEAD
#   HEAD:mbdsdr_ai/ft8_ldpc.py:27:def get_colorder() -> list[int]:
#   （另有 docs/learn/wsjtx.md、docs/redundancy-register.md 的文档引用）

git grep -n "get_colorder" HEAD -- mbdsdr_ai/ft8_decode.py
#   （空：reorder_to_ldpc 已不调用）

git show 285a9863 -- mbdsdr_ai/ft8_decode.py
#   -  colorder = ft8_ldpc.get_colorder()
#   -  return [llr_cw[colorder[j]] for j in range(ft8_ldpc._N)]
#   +  return list(llr_cw)

git grep -n "def reorder_to_ldpc" HEAD -- mbdsdr_ai/ft8_decode.py
#   HEAD:mbdsdr_ai/ft8_decode.py:74:def reorder_to_ldpc(...)
```

## 5. 建议的最小改写（供审计线参考，非强制）

> ~~`ft8_decode.py:52-56` `reorder_to_ldpc()` 调用 `ft8_ldpc.get_colorder()` 对 LLR 做反置换。~~
>
> **【已过期，`285a9863` 已修】** 原缺陷：`reorder_to_ldpc()` 曾调用 `ft8_ldpc.get_colorder()`
> 对 (174,91) 码字做反置换。当前 HEAD `mbdsdr_ai/ft8_decode.py:74` 已改为恒等映射
> （`return list(llr_cw)`），`get_colorder` 全仓零调用（仅 `ft8_ldpc.py:27` 定义）。

## 6. 与冗余登记表的关系

`docs/redundancy-register.md` §5.8.2 已按「`get_colorder` 确认无用」登记，并把本条审计记述
标为过期依据。审计线修订后，该登记行的「依据」一栏可去掉对 `08_ft8_full.md:36` 的引用。
