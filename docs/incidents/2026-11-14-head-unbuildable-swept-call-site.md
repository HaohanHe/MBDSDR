# 事故记录：`12bcdb7a` 带走调用侧、定义没跟上，HEAD 有 272 秒不可构建

- 事故提交：`12bcdb7a`（2026-11-14 09:10:36 +0800，subject
  `fix(sat): 5° 可见门收敛为单一来源`）
- 补齐提交：`b129905f`（09:15:08 +0800，给 `desktop/main_window.py` 加 13 行）
- 现象：这 272 秒内，HEAD 上构造主窗口直接
  `AttributeError: 'MainWindow' object has no attribute '_create_optional_panel'`
- 处置：**不改历史**；补一道提交后可构建烟雾检查
  （`scripts/head_build_smoke.py` + `scripts/hooks/post-commit`），见 §3
- 与既有两篇的关系：跟
  [`2026-11-13-commit-message-collision.md`](file:///d:/mbdsdr/MBDSDR/docs/incidents/2026-11-13-commit-message-collision.md)
  （说明串车）、
  [`2026-11-13-exclude-hunk-swept-appended-rows.md`](file:///d:/mbdsdr/MBDSDR/docs/incidents/2026-11-13-exclude-hunk-swept-appended-rows.md)
  （排 hunk 后新追加行被带走）都是**不同类事实**，单独成篇，不合并

## 1. 事实

`12bcdb7a` 的 message 写的是「覆盖圈、地面轨迹地图、跟踪面板控件初值与主窗口跟踪环
不再各写一份 5.0」，但对 `desktop/main_window.py` 实际改了 100 行（`--stat`：100
个 `+/-`），其中包含**另一线当时未提交的调用侧重构**：

| 被带走的改动 | 证据（`git show 12bcdb7a -- desktop/main_window.py`） |
|---|---|
| 删掉 8 个面板类的模块级 import | `- from scanner_panel import ScannerPanel` … `- from adsb_panel import AdsbPanel`（共 8 条） |
| 8 处 `XxxPanel()` 构造改走 `_create_optional_panel(...)` | `scanner / modulation / bookmark / anr / constellation / device / gain / adsb_list` |
| 该方法的**定义**没有一起带进来 | `git show 12bcdb7a:desktop/main_window.py` 里 `_create_optional_panel` 出现 8 次，**全部是调用，没有 `def`** |

定义在下一笔相关提交才落地：`b129905f` 对同一文件 `+13` 行
（`refactor(panel): 右栏固定页签收编到 PanelSpec.factory`）。时间线（2026-11-14，+0800）：

| 时刻 | 事件 | 证据 |
|---|---|---|
| 09:10:36 | `12bcdb7a` 落库：调用侧被整份带走，定义缺失 | `git log -1 --format=%ai 12bcdb7a` |
| 09:14:24 | `3ee3c177` 落库（`verify_claims: …`），HEAD 仍不可构建 | `git log -1 3ee3c177` |
| 09:14:35 | `c5bbef7a` 落库（`test(sat): …`），HEAD 仍不可构建 | `git log -1 c5bbef7a` |
| 09:14:46 | `3901e806` 落库（`test(coordination): …`），HEAD 仍不可构建 | `git log -1 3901e806` |
| 09:15:08 | `b129905f` 落库，补上 `def _create_optional_panel`，HEAD 恢复可构建 | `git show b129905f --stat -- desktop/main_window.py` |

窗口长度 09:10:36 → 09:15:08 = **272 秒**；窗口内共有 **4 笔**提交
（`12bcdb7a`、`3ee3c177`、`c5bbef7a`、`3901e806`）落在同一个不可构建的
`desktop/main_window.py` 上。任何在这 272 秒里 `checkout` / `pull` / 跑构造类用例的
人拿到的都是这个 tree。

可复跑的判据（本次实测，不是推理）：

```
$ python scripts/head_build_smoke.py --commit 12bcdb7a
[head-build-smoke] ✗ HEAD 不可构建：12bcdb7a fix(sat): 5° 可见门收敛为单一来源
[head-build-smoke]   失败用例（1）：
[head-build-smoke]     tests/test_panel_registry_factory.py::test_main_window_builds_panels_through_registry
[head-build-smoke]       AttributeError: 'MainWindow' object has no attribute '_create_optional_panel'
退出码 3
```

`b129905f` 上同一条命令退出码 0。两条记录都落在
`D:\mbdsdr\.locks\head_build_smoke.log`（JSONL）。

## 2. 为什么现有守卫没拦住

三道现有闸门判的都不是这件事：

| 闸门 | 它判什么 | 为什么不覆盖本例 |
|---|---|---|
| claim 台账 + `claim_gate.py` | 「这个文件此刻归谁」：别人仍持有的文件不许提交 | `12bcdb7a` 碰 `desktop/main_window.py` 时该文件没有活跃 claim（调用侧是对方**未提交**的工作区内容），归属面看不出「带走了半套重构」 |
| `safe_commit` ⑰ 声明快照 | 「**声明之后**有没有新的改动」：声明时刻与提交时刻逐文件比 blob 指纹，一个字节不同就拒（`scripts/safe_commit.py` 的 ⑰ 段与 `_verify_declaration`） | 调用侧在声明**之前**就已经躺在工作区，原样进了快照，两个时刻一致 → 判据通过。⑰ 自己的边界写得很清楚：判的是**时间窗**，不是**归属**，也不是**完整性** |
| post-commit 残留 / `STALE_INDEX_SUSPECT` | 「提交新增的行有没有以删除形态回到工作区」 | 只比对提交内容与工作区，不执行代码；方法没定义也不会让 diff 形态发生变化 |

结论：**没有任何一道闸门判「提交出去的 tree 自身能不能构建」**。而能抓住它的用例当时
已经存在——`tests/test_panel_registry_factory.py` 由 `ec7bbaef`（05:31:53，早于事故
3 小时 39 分）引入，在 `12bcdb7a` 的 tree 上直接红（本次实跑：`1 error`，即上面那条
`AttributeError`）。缺的不是用例，是「提交后跑一次」这个动作。

## 3. 修法：提交后可构建烟雾检查

| 端 | 位置 | 行为 |
|---|---|---|
| 判据 | [`scripts/head_build_smoke.py`](file:///d:/mbdsdr/MBDSDR/scripts/head_build_smoke.py) | 在 `git worktree` 里检出被检查的提交（不复用工作区，别线未提交改动污染不了结论），只跑 4 个既有节点（24 项）：import 冒烟、单面板构造、主窗口整体构造、逐面板工厂构造 |
| 接入 | [`scripts/hooks/post-commit`](file:///d:/mbdsdr/MBDSDR/scripts/hooks/post-commit) | 放在「已审计 / 主动绕过 / 未审计」分支**之前**，带 `MBDSDR_BYPASS_REASON` 的提交也照样跑 |
| 台账 | `D:\mbdsdr\.locks\head_build_smoke.log`（JSONL） | `pass / fail / error / skipped` 四种结局都记；红时含 sha、失败 nodeid、首条错误、耗时 |
| 安装 | `scripts/install_git_hooks.py` | `head_build_smoke.py` 加入 `SUPPORT_FILES`，旧工作树没有 `scripts/head_build_smoke.py` 时用 hooks 目录副本兜住 |

**耗时（本次实测，同一台机器）**：

| 场景 | 检出 | pytest | 合计 |
|---|---|---|---|
| 首次（新建 worktree） | 3699 ms | 8007 ms | 12009 ms |
| 常规（worktree 已存在、无锁竞争） | 82 ms | 5044 ms | **5433 ms** |
| 常规（Qt 全局锁被别处占用，等到锁） | 1128 ms | 14202 ms | 15667 ms |

所以典型提交增加约 **5.4 s**；文档 / 数据类提交（不触及
`desktop/`、`mbdsdr_ai/`、`tools/`、`assets/`、`tests/` 与任何 `.py`）直接跳过，
只打印一行并在台账留 `skipped`。

**不静默跳过**：脚本缺失、worktree 检出失败、pytest 未收集到用例（rc=5）、被中断
（rc=2，常见为 Qt 全局锁等不到）、超时、脚本自身崩溃——全部记 `status=error`
（退出码 4）并醒目打印原因，不会当绿。`MBDSDR_HEAD_BUILD_SMOKE=0` 是唯一的显式
关闭开关，关闭也会打印并记一条 `skipped`。

**反证（真造，不是推理）**：

1. 历史树复现：`--commit 12bcdb7a` 红（上面的输出）、`--commit b129905f` 绿；
2. 现造一笔「调用了未定义的方法」的提交 `1c48364e`（独立 worktree + 独立分支
   `smoke-repro-20261114`，不动主分支 HEAD）：在 `MainWindow.__init__` 里插入
   `self._smoke_repro_undefined_method()`。post-commit 自动跑烟雾检查，台账记
   `fail`——失败用例 `test_main_window_builds_panels_through_registry`，首条错误
   `AttributeError: 'MainWindow' object has no attribute '_smoke_repro_undefined_method'`，
   同条记录还有 `_tx_session` 的 teardown 错误（构造中断留下的半成品对象，同一根因）。
   随后**另造一笔修复提交** `64dc5553`（不 amend、不 rebase，只删除那三行），同一
   路径复绿，台账记 `pass`（24 项，pytest 9273 ms）。
   说明：`1c48364e` 的第一轮自动运行记的是 `error`——当时 Qt 全局锁被别线的 pytest
   占着，等到 45 s 仍拿不到锁，判为 `error` 而非绿；锁释放后重跑同一条路径才得到
   `fail`。这正是「检查本身失败也要 fail-loud」的现场：**等不到锁不等于通过**。
3. 用例级反证：把 `_status_of` 的 `rc=1 → fail` 改回 `pass`，
   `tests/test_head_build_smoke.py::test_real_undefined_attribute_failure_is_red`
   变红；把 post-commit 里的调用挪到 bypass 分支之后，
   `test_post_commit_calls_smoke_before_bypass_branches` 变红。两次都实测过。

## 4. 不做什么

- **不改历史**：`12bcdb7a` 与 `b129905f` 原样保留，本篇只记录；
- **不阻断提交**：post-commit 只记录、不改退出码——提交已经落地，卡下一笔只会把账
  算到下一个人头上（与残留检查同一口径）；
- **不代替归属判断**：烟雾检查只说「这个 tree 构建不住」，不说「这行是谁写的」。
