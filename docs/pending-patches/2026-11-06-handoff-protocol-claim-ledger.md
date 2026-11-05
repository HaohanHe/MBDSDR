# 待落库：`parameter-unification.md` 引用口径数值更正 + 5 处 `_fill_sr_combo` 改名（协议线 78420b4f）

> 交接方：采样率真值线（`6ae85f56`）；收件方：协议线（`78420b4f`，`docs/parameter-unification.md` claim 持有方）。
> **本线不直接改该文件**：上一轮用工作区整文件提交，卷走过别线未提交的措辞 hunk（已还原为未提交状态）。以下清单请由你落库。

## 目标文件

| 文件 | 状态 | 处理 |
|---|---|---|
| `docs/parameter-unification.md` | 协议线持有 claim；工作区有 25/26 行在途改动（别线措辞） | 按 §A、§B 改；行号见两套坐标 |
| `docs/evidence-standard.md` | 本线已提交 `5894dfb1`（§7.8，24 行，仅本节 hunk） | 无需再动 |

## §A 引用口径：四条替换 + 复现式

把「引用口径」节里那两张表的第四列（闭合关系）与两行数值整体替换为：

```markdown
| `b0a440e3` | 156 / 58 / 12 / 86 | 工作区随未提交 hunk 实时变，不写死 | 旧值 164/58/17/89 **作废**：不可复现；同口径重跑为 156/58/12/86 |
| `d731bdeb` | 156 / 58 / 12 / 86 | 同上 | 旧值 140/58/12/70 **作废**：`git ls-tree` 的 quotepath 把 `docs/PRD-现状对照.md` 当带引号路径跳过，少 16 条；改 `git archive` 快照后为 156 |
| `e4810574` | 156 / 63 / 12 / 81 | 同上 | |
| `81cd345a` | 156 / 63 / 13 / 80 | 同上 | 与冗余线实测一致 |
```

表格下方补一行复现式（口径写死，详见 `docs/evidence-standard.md` §7.8）：

```text
git archive <HEAD> docs | tar -x -C <tmp>
git show <HEAD>:scripts/verify_claims.py > <tmp>/vc.py
python <tmp>/vc.py --root <repo> --docs <tmp>/docs
# 范围：docs/*.md 顶层非递归；EXCLUDE_FILES={claim-waivers.md}；CLAIM_RE 与 HEAD 逐字一致
```

变更记录那行里的数字替换为：

- 旧：`（164/58/17/89 vs 128/70/13/45）与（140/58/12/70 vs 128/70/12/46）`
- 新：`（156/58/12/86 @ b0a440e3 / 156/63/13/80 @ 81cd345a）`，并加「旧值 164/140 作废」。

## §B 五处 `_fill_sr_combo` 改名清单

依据：旧签名 `70429a02:3714` = `def _fill_sr_combo(device):`（module-level，无 self）；新签名 HEAD:53 = `def fill_sample_rate_combo(combo, device) -> None:`，调用侧 HEAD:4041 = `fill_sample_rate_combo(sr_combo, d)`。

**行号两套坐标**：HEAD 提交态 / 当前工作区（工作区因别线在途改动整体上移 1 行）。

| # | HEAD:行 | 工作区:行 | 现文（关键片段） | 改成 |
|---|---|---|---|---|
| 1 | 55 | 54 | `desktop/main_window.py:_connect_backend/_fill_sr_combo/_on_sample_rate_changed` | `desktop/main_window.py:_connect_backend/fill_sample_rate_combo/_on_sample_rate_changed` |
| 2 | 175 | 174 | `desktop/main_window.py:_fill_sr_combo` | `desktop/main_window.py:fill_sample_rate_combo` |
| 3 | 394 | 393 | ``改 `_fill_sr_combo(device)`：有量程走…`` | ``改 `fill_sample_rate_combo(sr_combo, device)`：有量程走…`` |
| 4 | 444 | 443 | 取证四元组 `70429a02 — desktop/main_window.py:3963 — _fill_sr_combo` | `70429a02 — desktop/main_window.py:3714 — _fill_sr_combo`（**字面量必须保留旧名**；`:3963` 实为 `return host, port`，`:3714` 才是 `def _fill_sr_combo`） |
| 5 | 444 | 443 | 同格后文 ``→ `_fill_sr_combo(device)` 按设备能力生成`` | ``→ `fill_sample_rate_combo(sr_combo, device)` 按设备能力生成`` |
| 6 | 514 | 513 | 变更记录：``改 `_fill_sr_combo(device)` 能力驱动`` | **整句保留原文**。当日 `70429a02:3714` 就是 `def _fill_sr_combo(device)`，记录准确；改成新名等于篡改历史 |

给上游的原话纠错（一并转达）：

- 我上轮说的 `HEAD:423` 是**旧 HEAD（`d731bdeb`）的行号**；`e4810574` 起实际是 `:444`（HEAD:423 是空行，已核）。
- 上游原话「改成 `fill_sample_rate_combo(device)`」**漏了 `combo` 参数**，正确形态是 `fill_sample_rate_combo(sr_combo, device)`。
- 第 1、2 项是「当前符号名」清单，可改名；第 4 项是「历史取证」，只改行号、不改字面量；第 6 项是历史记录，整句不动。

## §C 需要你裁定的一处口径冲突（`docs/evidence-standard.md` §7.7）

§7.7 写死「`path:N` 是当前 HEAD 坐标，第 4 步校验 `git show HEAD:<path>` 第 N 行必须含字面量」。但第 4 项的字面量 `_fill_sr_combo` 在 HEAD 已不存在（`7bea149a` 改名），第 4 步必然判「行号失效」。

本清单按上游指令写 `70429a02:3714`（提交坐标），**请协议线决定**：是否给该行加「历史字面量」标记、把第 4 步对历史行豁免，或把它从四元组校验范围移出。不要靠改字面量过关——改成新名会让 `log -S` 在 `70429a02` 上失配。

## §D 落库时怎么验

```text
git show HEAD:docs/parameter-unification.md 里 :444 的取证元组仍含 `_fill_sr_combo`
grep -n "fill_sample_rate_combo" docs/parameter-unification.md   # 应有 3 处新名（55/175/394 对应行）
grep -n "_fill_sr_combo" docs/parameter-unification.md           # 应只剩 444 的取证元组与 514 的历史记录
按 §7.8 复现式重跑 verify_claims，HEAD 提交态应为 156/58/12/86 @ b0a440e3 等四组已核值
```
