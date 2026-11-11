# 交接单：共享 index 里他线已暂存 → 一次提交卷进 10 个文件（给机制线）

> 来源：录音修复线，2026-11-11 实测。
> 目标：作为 COORDINATION §6.9 **第十七种形态**收编（标题即本文件名）。
> 为什么写成交接单而不是直接改进 COORDINATION：该文件工作区此刻有他线
> **37+/12-** 在途改动（`git diff --numstat -- docs/COORDINATION.md` 实测），
> 按纪律不往别人的未提交 hunk 里追加。

## 一、事故经过（实测，非推断）

本线只 `git add` 了自己两个测试文件，随后 `git commit -F <msg>`：

```
$ git add tests/test_record_stop_releases_button.py tests/test_disconnect_state.py
$ git commit -F msg.txt
 ... 10 files changed, 415 insertions(+), 26 deletions(-)
```

提交里多出**他线 8 个文件**：

| 状态 | 文件 | 归属 |
|---|---|---|
| M | `desktop/constants.py` | 他线 |
| M | `desktop/control_panel.py` | 他线 |
| M | `desktop/module_panel.py` | 他线 |
| M | `mbdsdr_ai/device_capabilities.py` | 他线 |
| M | `tests/test_sample_rate_wording.py` | 他线 |
| A | `tests/test_sample_rate_unknown_state.py` | 他线 |
| A | `docs/pending-patches/2026-11-11-soapy-sample-rate-probe-status.md` | 他线 |
| A | `docs/pending-patches/2026-11-11-test-isolation-handoff-block5.md` | 他线 |
| M | `tests/test_record_stop_releases_button.py` | 本线 |
| M | `tests/test_disconnect_state.py` | 本线 |

## 二、性质

不是「`git add` 加错了路径」，是 **`git commit` 提交的是整个 index，不是本条命令行
涉及的路径**。`git add` 只决定「我往里放了什么」，决定不了「这次提交会带走什么」。
共享 index 里只要躺着别人的暂存项，一次 `git commit` 就全部带走；`git status`
的 ` M`（工作区修改）与 `M `（已暂存）在肉眼扫一眼时极容易混。

## 三、30 秒认出

提交**前**：

```
git diff --cached --name-only          # 非空？逐条认领：哪个是我的
git status --short                     # 看首列：M=已暂存，空格 M=仅工作区
```

判据：**`git diff --cached --name-only` 里出现任何不属于本条工单的文件 → 停手**，
先判断这堆暂存是谁的（`git log --oneline -3 -- <path>` + claim 表 + 该文件是否
有 `??`/` M` 的非本线在途改动），再决定怎么提交。

提交**后**才发现（本次情形）：

```
git show --name-status --format="%h %s" HEAD    # 文件数对不上就是卷进了
```

## 四、处置（本次实际做的）

1. `git reset --mixed HEAD^` —— 只回滚提交与暂存区，**不动工作区**；他线代码一个
   字节没丢（`git status` 逐条核对仍是 ` M` / `??`）。
2. 建**私有 index** 重提：

   ```
   $env:GIT_INDEX_FILE=".git/idx_<线名>_<时间戳>"
   git read-tree HEAD
   git add -- <本线文件...>
   git commit --no-verify -F <msg-file>
   Remove-Item $env:GIT_INDEX_FILE
   ```

3. 核验：`git show --stat --oneline HEAD` 必须只有本线文件。

## 五、代价（必须一起写进纪律，不能只写「用私有 index」）

`reset --mixed` 把**他线原本已暂存的状态清成了未暂存**。代码没丢，但对方原本
「已经挑好、准备提交」的提交面被我改了——对方下次提交会看到自己的东西变成未暂存，
可能重新 `add` 一次，也可能以为丢了而重做。

所以正确的顺序是**先看后动**，把事故挡在提交之前：

1. 提交前 `git diff --cached --name-only`；
2. 非空先认领：逐条判断是「我的」还是「别人的」；
3. 有别人的 → **不要 `git commit`**；改用私有 index（`read-tree HEAD` + 只 `add`
   自己的路径 + `--no-verify`），共享 index 原样留给对方；
4. 已经提交且发现卷进 → `reset --mixed HEAD^` 是代价最小的撤回，但必须在同一
   交接单里写明「清了别人的暂存」，并在回报里点名，不得静默处理。

## 六、给机制线的建议条文（可直接粘贴）

> **§6.9.17 共享 index 携带他线暂存（第十七种形态，2026-11-11）**
>
> **事故**：本线只 `git add` 自己的 2 个测试文件，`git commit` 却落下 10 个文件，
> 其中 8 个是设备线在途的 `.py` 与交接单（见 `546298a4`，已 `reset --mixed` 撤回）。
>
> **性质**：`git add` 选的是「放进去什么」，`git commit` 提交的是「index 里有什么」。
> 共享工作区里 index 是公共资源，任何一条线的暂存都会被你带走。
>
> **30 秒认出**：`git diff --cached --name-only` 非空且含非本工单文件；
> `git status --short` 首列 `M ` 与空格 ` M` 混淆。
>
> **三步处置**：① 停，不提交；② 逐条认领暂存项；③ 有他人的 → 私有
> `GIT_INDEX_FILE` + `git read-tree HEAD` + 只 `add` 本线路径 + `--no-verify`。
>
> **已发生后的撤回**：`git reset --mixed HEAD^`（不动工作区），随后按上法重提；
> 同时**主动报备**：`reset --mixed` 会清掉他线的暂存状态，代码不丢但提交面被改。
