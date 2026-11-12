# 幻影删除事故记录：陈旧私有 index 卷回别线文件

- 记录日期：2026-11-12
- 结论：**不是单次事故**。全历史可证实 4 起，2026-11-06 至 11-12，机制完全相同：
  提交树基于旧 HEAD 建好，新提交的树 = 旧树 + 本人改动，别人在旧 HEAD 之后入库的
  文件全部变成 `D`。
- 现有闸门已覆盖此形态（`25fde219`），但**未声明清单时只 WARN**，所以不用
  `safe_commit`、也不带 `--expect` 的提交仍会漏过去。`35db7a13` 就是这么漏的。

---

## 1. 本次事故 `35db7a13`

| 项 | 值 |
|---|---|
| 提交 | `35db7a13` `docs(roadmap): W104 转完成，统计 47+13+2+21+22=105` |
| 作者 / 时间 | HaohanHe / 2026-11-12 21:46:28 +0800 |
| 父提交 | `b4c2e308`（比它早 33 秒，正是新增守卫测试的那一笔） |
| 全量改动（`git diff --name-status 35db7a13^ 35db7a13`） | `M docs/research/2026-11-11-roadmap.md`、`D tests/test_theme_dual_value_guard.py` |
| 被删文件的新增提交 | `b4c2e308`（距离 1 条） |
| 恢复提交 | `ad7408dd`（21:46:56，+148 行，内容与原文件一致） |
| 该提交的树 | 与 `f0b8b7f9` 的树 + roadmap 一处改动**逐字节相同**（见 §3 复现，树哈希都是 `a479d8ab...`） |

父提交是 `b4c2e308`，说明工具把「当前 HEAD」正确写进了 parent，只是**树的来源**
还停在 `f0b8b7f9`。所以 diff 看起来像一次有意删除，实际是索引过期。

---

## 2. 全历史扫描：另外 3 起

扫描方法（临时脚本，未入库）：

1. `git log --diff-filter=D --name-status --no-renames` 拉出全历史删除条目，共
   1328 条、28 条提交；
2. 对每条删除路径检查 `git cat-file -e HEAD:<path>`，找出「删了又回来」的路径：
   8 个，恰好落在 4 起提交上；
3. 再按「距该文件被新增 ≤5 条提交」扫一遍共 26 条，逐条人工分类（revert、
   rename、清理、误删）。

| 事故提交 | 日期 | 被删文件（新增它的提交，距离） | 恢复提交 | 同机制证据 |
|---|---|---|---|---|
| `16f075f3` | 2026-11-06 | `tests/test_pose_fusion.py`、`web/product/build/sat-tracks.json`、`web/product/build/sync_sat_tracks.py`、`web/product/src/13-sat-tracks.js`、`web/product/tests/perf_bench.js`（`0055dddb`，1 条） | `a5d062e8`「撤回 16f075f3 误夹带的 26 个别线文件」 | `git diff --name-status 0055dddb^ 16f075f3` 只剩 10 个 DSO 相关文件、**零 D**，即树 = `0055dddb^` + 本人改动 |
| `b28ddadf` | 2026-11-11 | `tests/test_config_store_single_writer.py`（`8d8e3e6b`，2 条） | `b4ea081d`「恢复被旧私有索引卷回的配置写入收敛…」 | `git diff --name-status 8d8e3e6b^ b28ddadf` 只剩 10 个设置/错误目录文件、**零 D** |
| `685e8f50` | 2026-11-11 | `tests/test_sample_rate_wording.py`（`d77d9b78`，1 条） | `78003d87`「恢复被旧私有 index 误回退的 5 个文件」 | `git diff --name-status d77d9b78^ 685e8f50` 只剩 4 个设置/频谱文件、**零 D** |
| `35db7a13` | 2026-11-12 | `tests/test_theme_dual_value_guard.py`（`b4c2e308`，1 条） | `ad7408dd` | 见 §3，复现树哈希与肇事树完全相同 |

**排除项（看着像、实际不是）**：

- `b650a0d3`：提交语明确是「revert 40b2d27b」，删 `tests/test_sky_lazy_build.py`
  是被撤回提交带来的文件，属正常 revert。
- `4035f239`（本线）：13 个 `docs/pending-patches/2026-11-12-a1/*` 是 `git mv`
  重命名，`-M` 下全是 `R100`；`--no-renames` 才会显示成 D+A。
- `9b48607b`（strategic-positioning）、`e9ea90c2`（二进制清理）、
  `8cc91b93`（`cpp/-` 垃圾文件）、`7a332e25`（`scripts/fix_*` 清理）：提交语
  自述删除意图，且路径从未回到 HEAD。
- `35100e1c`：删了 `mbdsdr_ai/scratch_render.py`，但它的树包含父提交新增的 25
  个文件（`git diff 8d6bec62^ 35100e1c` 仍是 A），说明树基于真实父提交，不是
  同一症状。

**判据局限**：删除后从未被恢复的幻影无法用历史反推（没有「又回来了」这个信号）。
上表是「能证实」的全部；从今以后只能靠 §5 的守卫挡。

---

## 3. 机制复现（不移动任何 ref）

用 `GIT_INDEX_FILE` + 旧树建私有 index，再用 `git commit-tree` 造悬空提交；
全程不写分支、不改 HEAD。

```powershell
$idx = Join-Path $env:TEMP "w92_repro_idx"
$blob = git rev-parse "35db7a13:docs/research/2026-11-11-roadmap.md"
$env:GIT_INDEX_FILE = $idx
git read-tree f0b8b7f9                      # 基树 = 肇事者的旧 HEAD
git update-index --add --cacheinfo "100644,$blob,docs/research/2026-11-11-roadmap.md"
$tree = git write-tree
Remove-Item Env:\GIT_INDEX_FILE
git diff --name-status b4c2e308 $tree       # 真实父 vs 复现树
$c = git commit-tree $tree -p b4c2e308 -m "repro"
git diff --name-status b4c2e308 $c
```

实测输出：

```text
REPRO_TREE=a479d8ab82399f3aa943a742ef49f43a2cd86289
CULPRIT_TREE=a479d8ab82399f3aa943a742ef49f43a2cd86289
---diff HEAD(real parent) vs 复现树---
M	docs/research/2026-11-11-roadmap.md
D	tests/test_theme_dual_value_guard.py
DANGLING_COMMIT=3896c6ffdc9fe53b4d3007f3358958797d11c0c6
---diff 真实父 vs 悬空提交---
M	docs/research/2026-11-11-roadmap.md
D	tests/test_theme_dual_value_guard.py
```

复现树与 `35db7a13^{tree}` 哈希一致，且悬空提交在真实父下同样产生那条 `D`。
**机制定性成立**：树的来源是旧 HEAD，parent 却是新 HEAD。

---

## 4. 为什么现有门禁没拦住

- [pre-commit](file:///d:/mbdsdr/MBDSDR/scripts/hooks/pre-commit) 调
  `check_commit_scope.py --staged`，闸门确实会用 `git diff --cached` 算出删除集合
  （天然相对**真实父提交** HEAD，不需要另设基线）。
- `25fde219` 已经加了「删除必须出现在声明清单」的拒绝分支
  （[check_commit_scope.py](file:///d:/mbdsdr/MBDSDR/scripts/check_commit_scope.py#L656-L666)）。
- **缺口**在同文件 [L667-L672](file:///d:/mbdsdr/MBDSDR/scripts/check_commit_scope.py#L667-L672)：
  没有声明清单（`--expect` / `MBDSDR_COMMIT_PATHS` 都为空）时只打印 `WARN`，
  不拒绝。`35db7a13` 没带声明，于是 D 被放行。
- `safe_commit.py` 自己的 `staged == expect` 校验
  （[safe_commit.py](file:///d:/mbdsdr/MBDSDR/scripts/safe_commit.py#L280-L290)）能拦住，
  但它不是唯一提交路径。

---

## 5. 守卫方案（只写方案，未改任何工具）

### 5.1 断言

```text
D   = { git diff --cached --name-status --no-renames HEAD | status == D }   # 相对真实父提交
I   = 显式意图删除集合（逐条声明）
声明 = --expect / MBDSDR_COMMIT_PATHS

assert D ⊆ I ⊆ 声明
```

只要 `D` 里有一条不在 `I`，**拒绝提交**并逐条打印路径 + 「文件由哪个提交新增、
距离几条」的提示。重命名在 `--no-renames` 下是 D+A，需要声明源路径；或在计算时
用 `-M`，把 `R` 的旧路径自动计入 `I`，但新路径必须仍在声明清单里。

### 5.2 挂载点

1. **必做**：[check_commit_scope.py](file:///d:/mbdsdr/MBDSDR/scripts/check_commit_scope.py#L667-L672)
   的 `if not unexplained_deletions and deletions and declared is None:` 分支，
   从「只 WARN」改为「拒绝」。新增 `MBDSDR_INTENT_DELETIONS`（或
   `--intent-delete`），把「允许删」与「允许改」分开声明；校验
   `D ⊆ I ⊆ 声明`。钩子无需改：`check_commit_scope` 返回 1 即拒绝。
2. **配套**：[safe_commit.py](file:///d:/mbdsdr/MBDSDR/scripts/safe_commit.py#L280-L290)
   在 `staged == expect` 之外，把 `--files` 中「HEAD 存在、工作树/新索引不存在」
   推导为 `I`，导出 `MBDSDR_COMMIT_PATHS` / `MBDSDR_INTENT_DELETIONS` 给钩子。
   这样私有 index 提交能正常过，且钩子的子集断言仍生效。
3. **第二张网（可选）**：post-commit 审计——本次 `D` 的路径若在最近 20 条提交内
   被新增、且不在 `I` 里，写 `.audit` 告警（不 block，事后可见）。
4. **不做**：不引入「最近新增就自动放行」的启发式；不改钩子 rc2/3/4 分级语义。

### 5.3 代价

所有含删除的提交都必须显式声明删除路径。这是有意的 fail-closed：漏声明会被拒，
而不是静默卷回别线文件。

---

## 6. 归属线索

`scripts/safe_commit.py`、`scripts/check_commit_scope.py`、`scripts/hooks/pre-commit`
的近期改动全部来自提交卫生/机制线（作者 HaohanHe）：

- `505cb1e8` safe_commit 路径归一化
- `25fde219` 幻影删除纳入声明清单闸（就是 §4 那条）
- `404c5b44` 私有 index 必须落到子进程
- `5915277c` 提交前声明清单闸
- `e2af7192` DIRTY 只拦共享 index 旧快照
- `828a1853` / `9ac7ba9d` safe_commit 删除项支持
- `8e05c3db` 回放钩子输出

本 a11y 线未修改上述任何文件；§5 的守卫应派给该线落地。

---

## 7. 复跑

- 扫描：`git log --diff-filter=D --name-status --no-renames`，对每条 D 查
  `git cat-file -e HEAD:<path>`；「删了又回来」即高危。
- 单笔核查：`git diff --name-status <提交>^ <提交>` 全量列，不只看提交语里的路径。
- 机制复现：按 §3 命令，注意临时 index 用完删除，`git commit-tree` 只产生悬空
  提交，不移动 ref。
