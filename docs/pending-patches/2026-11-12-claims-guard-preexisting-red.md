# 交接单：`test_claims_guard` 在他线文档上持续报红（2026-11-12 实测）

> 来源：测试诚实性轮次（档 1 收集器修复 / 档 2 弱断言加固）。**与那一轮改动无关**，
> 单独记账，等持有方各自处理。本文只记录，不改任何被点名的文档。

## 复现

```
python -m pytest tests/test_claims_guard.py -q
-> 2 failed, 3 passed (0:01:54)
```

两条红的用例：

1. `test_no_new_or_modified_unverified_claims`：20 处断言缺完整四元组，分布在 12 个文档；
2. `test_waivers_are_not_stale`：3 条白名单条目已失效（被点名的断言已补证或删除）。

## ① 缺四元组的断言（20 处 / 12 个文档）

| 文档 | 行 | 闸门给出的原因 |
| --- | --- | --- |
| `docs/COMMIT-INCIDENT-CARD.md` | 22 | 未写 commit hash |
| `docs/COORDINATION.md` | 1896 | 未写 commit hash |
| `docs/COORDINATION.md` | 3383 | hash 存在但未改动该文档（文档过期） |
| `docs/design-basis.md` | 164 / 167 / 168 | hash 存在但 `git log -S` 未命中候选字面量 |
| `docs/evidence-standard.md` | 212 | §4.3 表格行，未写 commit hash |
| `docs/narrow-screen-plan.md` | 739 / 771 | 739 缺 `<path>:<line>`；771 未写 hash |
| `docs/packaging.md` | 329 / 334 / 445 / 450 / 620 | 445 缺 `<path>:<line>`；其余未写 hash |
| `docs/parameter-unification.md` | 447 | 有 hash 但缺 `<path>:<line>` |
| `docs/performance-baseline.md` | 269 / 333 | 269 缺 `<path>:<line>`；333 未写 hash |
| `docs/real_hw_acceptance_20261103.md` | 31 | 未写 commit hash |
| `docs/redundancy-register.md` | 648 / 657 | 648 缺 `<path>:<line>`；657 未写 hash |

## ② 失效白名单（3 条）

```
docs/design-basis.md             指纹 23b9f2f6b319
docs/narrow-screen-plan.md       指纹 8db68d25d0bd
docs/real_hw_acceptance_20261103.md 指纹 e85843173c3b
```

闸门的提示是「断言已补证或已删除，请删行并调小 waived-total」——说明这三条
原白名单覆盖的断言，要么已被补上四元组，要么原文已不在了。

## 与本轮改动的关系（为什么不是新引入）

- 本轮对 `docs/evidence-standard.md` 只改了 2 处：§4.2 例子句尾补半句
  「往返只证可逆、不证正确」，§4.3 表格后新增一句「测试名声称的行为必须有
  对应断言……」。两处都不含数字，也都不在上面的红名单里（红的是 §4.3 表格第
  212 行，改动前就有）。
- 其余被点名的文档本轮未触碰（`git status` 里没有它们的改动）。
- 因此这 2 failed 是**继承状态**，不是本轮 59 条档 1 / 5 条档 2 的副作用。

## 处置建议（不由本轮执行）

按文档持有线拆开：`COORDINATION.md` / `design-basis.md` / `packaging.md` /
`real_hw_acceptance_20261103.md` 四条是主账，建议由主账线统一补四元组或
把过期断言改写成「历史结论 + 当前不可复跑」；白名单 3 条随对应断言修订时
一并删除并调小 `waived-total`。不要用「加白名单」的方式压红。
