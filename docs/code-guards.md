# 代码守卫：catch-all 静默吞噬（P13 补充）

- 结论：全仓 catch-all 处理器 **1884** 处，其中「无记录」静默吞噬 **1399** 处，白名单 **1** 处，存量 **1398 处**（`ERROR` 573 / `WARN` 825）。
- 策略：**棘轮 + 定点清除**。存量不逐处修；基线只许降不许升，新代码必须干净；只清用户能感知“点了没反应/功能凭空消失”的点。
- 基线：`docs/code-guards-baseline.json`（按文件记 `ERROR`/`WARN`，快照 2026-11-05）。任何文件超基线、新文件出现静默 catch-all、总量上升 → 红。
- 扫描快照：1061 个 Python 文件（提交树口径；工作区另含未跟踪/在途文件时以脚本实时输出为准）。
- 事故首例：`desktop/rf_sky_view.py::RFSkyView._find_body_at` 的 `float(None)` 抛 `TypeError` 被 `try/except Exception: pass` 吞掉，深空天体既不可见也不可点，无日志无报错。该函数快照内 6 处（2 ERROR + 4 WARN）。
- 本轮只报不修；`docs/limitations-register.md` 未触碰，`desktop/main_window.py` 未触碰。

## 判据

扫描器 `scripts/lint_swallow_all.py` 用 AST（不是正则）识别 catch-all：

- catch-all = `except:` / `except Exception:` / `except BaseException:`，或元组里含 Exception/BaseException；
- 静默 = 处理器体内没有「记录原因」的动作（日志、print、warning/error 状态字段、错误容器 append），也没有 `raise`：
  - `EMPTY`：只有 `pass`、`...` 或注释（AST 里注释不可见，等同 Pass）→ **ERROR**
  - `SILENT_CONTROL`：只 `return None` / `return <常量>` / `continue` / `break` → **WARN**
  - `SILENT_FALLBACK`：有语句但无记录（塞默认值后继续）→ **WARN**
- 有记录（RECORDED）或真正重新抛出（RERAISE）→ 只计数，不入缺陷；
- **禁令**：`except Exception: raise` 这种单条裸 `raise`（`FAKE_RERAISE`）判 ERROR；`contextlib.suppress(Exception)` / `suppress(Exception)`（`FORBIDDEN_SUPPRESS`）判 ERROR。两者都不可进白名单，防止绕过扫描器。
- 裸 `except:` 当前为 **0** 处。

每处白名单必须同时回答三问 + 声明触发方式 + 复核期限，缺一即不合格：

1. `reason`：为什么允许降级（必须是外部边界：第三方库/网络/硬件/平台）；
2. `user_visible`：降级后用户看到什么（不能是「什么都没有」）；
3. `test`：哪条测试覆盖这条降级路径（文件必须存在）；
4. `trigger`：边界**怎么失效**，单键对象，如 `{"import_fail": "sounddevice"}` /
   `{"env_absent": "AUDIO_DEVICE"}`。门禁做**源码级**校验：`test` 指定的测试函数里
   必须真的出现「模块名 + `raise ImportError`」（或「目标名 + 环境 monkeypatch」）。
   **只改 `test` 路径、写个理由过不了门禁**；反证见
   `test_whitelist_trigger_must_be_exercised`。
5. `review_by`：`YYYY-MM`，到期即判不合格，必须复核后再续期。

## 白名单（1 处）

| 位置 | ① 为什么允许降级 | ② 用户看到什么 | ③ 覆盖测试 | ④ trigger | ⑤ 复核 |
| --- | --- | --- | --- | --- | --- |
| `mbdsdr_ai/audio_out.py:37` | 外部边界：`sounddevice`/PortAudio 是可选依赖，导入失败（库缺失、平台无 PortAudio、后端初始化失败）必须整体降级，不能拖垮整个应用。 | `_SD_AVAILABLE=False`，`AudioPlayer.available=False`；`start()` 返回 False 并保持静音；`write()` 返回 0；UI 音频入口显示不可用，而不是崩溃或假装播放。 | `tests/test_audio_selection_e2e.py::test_sounddevice_import_fail_degrades`（`builtins.__import__` 对 `sounddevice` 抛 `ImportError` 后 reload，断言 `available/start/write/stop` 降级）；另有 `test_sounddevice_absent_degrades` 覆盖 `sd=None` 形态。 | `{"import_fail": "sounddevice"}` —— 门禁已确认测试里同时出现模块名与 `raise ImportError`。 | 2027-01 |

其余 1398 处**没有一处能同时回答三问**：绝大多数连日志都没有，更谈不上覆盖降级路径的测试。因此不把「可选依赖 = 自动豁免」当作批量白名单——豁免必须逐条给出三问，否则就是静默丢功能。

## 凭据扫描器豁免登记（2026-11-13）

`scripts/secret_scan.py` 的命中里有 11 行是「变量名撞上凭据词、实际打印的不是凭据值」的误伤。它们只走**逐行摘牌**：`# pragma: allowlist secret — <理由>` 贴在命中那一行，扫描器整行跳过。**没有**目录级/文件级豁免，也**没有**关掉某条规则的开关。

判据（三条同时满足才算合规豁免）：

1. **逐行**：pragma 只作用于它所在的那一行，不给相邻行、同一函数的其他 `print`、同文件其他命中放行（反证：`tests/test_secret_scan.py::test_inline_allowlist_silences_only_that_line`——同一文件另外 4 条命中仍然 BLOCK）；
2. **理由可核**：理由必须写成「打印的是 X，非凭据值」，且 X 能被测试静态或行为验证，不能只是一句自述；
3. **有测试先红**：理由一旦不成立（该行改成打印变量值），必须有一条测试先失败——宁可红在本地，也不要等泄露。

| 位置 | 规则 | 理由 | 谁守着它 |
| --- | --- | --- | --- |
| `scripts/file_claim.py` 9 处（633 / 639 / 673 / 699 / 712 / 746 / 759 / 1016 / 1036） | `log-credential-value` | 打印的是 sidecar 路径（`_token_path(key)`）或「凭据文件写入失败」的错误文本（`sidecar_err`），非凭据值 | ① 静态：`tests/test_file_claim.py::test_allowlisted_claim_hints_print_paths_not_values` 用 AST 取每个带理由的 `print` 调用，断言其中不出现 `token` / `fresh_token` / `upgraded_token` / `new_token` / `presented` / `stored`；② 行为：`tests/test_file_claim.py::_token` 的 `assert token not in output`，claim/takeover 的 stdout+stderr 不得含凭据 |
| `scripts/ui_baseline.py:630` | `log-credential-value` | 打印的是设计变更里的 UI 元素标识（`gdiff["text"]`），非凭据 | 无专门测试；值是设计变更清单里的界面文本，不含凭据形状。**本表最弱的一条**，见解除条件 |
| `scripts/ui_layout_audit.py:151` | `log-credential-value` | 打印的是 UI 元素标识（`token` 是布局审计里的元素名），非凭据 | 无专门测试，同上 |

另有 2 条 `argv-cli-credential-option`（`scripts/file_claim.py:1343/1363`，即 `--token` / `--token-file` 选项）**不在豁免内**：它只是 WARN、不拦提交，属于「CLI 上有凭据参数」的常态告警，留着提醒别把值写进命令行。

### 解除条件（任一触发即撤豁免，不许延期）

1. **该行开始打印凭据值**：撤 pragma，让扫描器 BLOCK。`file_claim.py` 那 9 处由上面的静态测试先红——本轮已反证：把第 1036 行改成 `+ new_token`，静态测试与 `_token()` 的行为断言同时失败（2 failed）。
2. **删掉/绕过行为断言**：`tests/test_file_claim.py::_token` 里的 `assert token not in output` 一旦被删或改成恒真，`file_claim.py` 的 9 条豁免立即作废，必须重新评审——这条断言就是豁免的对价。
3. **豁免行数变化**：`file_claim.py` 里带理由的 pragma 不再是 9 行时，本表与静态测试同步改；新增豁免要重新走上面三条判据，不许「反正在同一个文件里」顺手加。
4. **只豁免行，不豁免规则**：禁止在 `secret_scan.py` 里给 `log-credential-value` 加文件白名单、变量名前缀白名单，或让 pragma 支持「整段 / 整文件」形态。

复核：**2027-05**（与其他豁免同一节奏）。

## 凭据历史扫描（`--history`，2026-11-13）

工作树 0 BLOCK 只证明「现在没有」，不证明「从没提交过」。`scripts/secret_scan.py --history`
对**对象库里的全部 blob** 跑同一组形状规则，报「命中 blob / 路径 / 提交」三个数。

口径（复跑命令：`python scripts/secret_scan.py --history`，cwd 为仓库根）：

| 项 | 值 |
| --- | --- |
| 判据 | 7 条形状规则（GitHub classic/fine-grained PAT、GitLab PAT、Slack、AWS、JWT、PEM 私钥），**不跑熵**，也不跑 argv/日志 AST 规则 |
| 豁免 | 行内 `# pragma: allowlist secret` 同样生效（逐行） |
| 扫描对象 | `git cat-file --batch-all-objects` 的 13 731 个 blob / 762.6 MiB（含 `--all`/reflog 之外的对象；数字随提交增长，判据不变） |
| 二进制跳过 | 679 个（首 8 KB 含 NUL，未做文本扫描） |
| 归属索引 | `rev-list --objects --all --reflog <各 worktree HEAD>` + `git log --raw` 反向索引；detached 的 worktree HEAD 不在 refs 下，必须单独补 |
| HEAD 可达性 | 逐个命中提交 `git merge-base --is-ancestor <commit> HEAD` |

结果（2026-11-13，HEAD `c483222d`，27 个 worktree HEAD）：

- **命中提交 0 个、命中路径 0 个**——可达历史干净；
- 另有 **1 个不可达 blob**（`c20bd082d638…`，69 字节）命中 `github-pat-classic`，
  但 token 体 36 位字符全同（合成值），且没有任何提交/路径引用它；2026-11-13 裁定
  **保留、不清理**，按下面的「已知良性登记」处理（**不改写历史**）。

这个「干净」的边界（不许当成无漏洞宣传）：二进制 blob 679 个没做文本扫描；
不可达对象（`git fsck --unreachable`：103 commit / 303 tree / 112 blob）里除上面
那 1 个 blob 外也扫了，但归属只能给出「未知」；`D:\mbdsdr` 根目录**不是** git 仓库，
所以 `output/`、`.tmp` 下工作树里命中的 4 处 BLOCK 没有历史可查（它们从未入库）。

### 提交信息通道（commit message / annotated tag / notes，2026-11-13）

blob 扫描有个结构性盲区：`git commit -m "ghp_…"` **不产生 blob**，消息写在 commit
对象里；annotated tag 的 message、`git notes` 的正文同理。`--history` 现在同时扫
这三类（同一组 7 条形状规则，只跑形状不跑熵）：

| 对象 | 取法 | 计数单位 | 命中格式 |
| --- | --- | --- | --- |
| commit message | `git log --all --reflog <各 worktree HEAD> --format=%x1e%H%x00%B` | 提交条数 / 消息行数 | `HISTORY-MSG <提交号12>:<行> <规则>` |
| annotated tag | `refs/tags` 里 `objecttype=tag`，`git cat-file tag <sha>` 取头部之后正文 | tag 个数 / 消息行数 | `HISTORY-MSG tag:<名称>:<行> <规则>` |
| notes | `refs/notes/*` 逐个 `git notes list` + `cat-file -p` | note 条数 | `HISTORY-MSG note:<对象号12>:<行> <规则>` |

轻量 tag 指向 commit、没有独立 message（正文就是被指向提交的消息，已在 commit
message 里扫过），只计数不重复扫；行内 `pragma: allowlist secret` 同样生效。

结果（同上 HEAD）：**commit message 3 460 条 / 43 597 行，annotated tag 0 个，
notes 0 条，命中 0 条**。

反证（临时仓库 `D:\mbdsdr\.tmp\msg-probe`，跑完已删除，未进主线）：

| 步 | 动作 | 结果 |
| --- | --- | --- |
| 1 | 2 条干净提交 + 1 个干净 annotated tag | `提交信息命中：0 条` |
| 2 | `git commit --allow-empty --no-verify -m "leak ghp_…"` | `HISTORY-MSG 7ae87ed07ecd:1 github-pat-classic` |
| 3 | `git tag -a v9.9 -m "tag leak ghp_…"` | `HISTORY-MSG tag:v9.9:1 github-pat-classic` |
| 4 | `git notes add -m "note leak ghp_…" HEAD` | `HISTORY-MSG note:7ae87ed07ecd:1 github-pat-classic` |
| 5 | 删分支/tag/note + `reflog expire --expire=now --all` + `gc --prune=now` | 回到 `提交信息命中：0 条` |

第 2 步用 `--no-verify` 是故意的：临时仓库要先证明「消息扫描看得见一条**真存在**的
提交」，再单独证明写入闸能拦住新提交（见下）。探针提交只存在于临时仓库，主线
`git log --oneline -3` 未受影响。

边界：消息写进 commit/tag/note 对象后，删字面量**没用**——只能重写历史。正因如此，
这条通道配了独立的写入闸。

### 提交信息写入闸（`commit-msg`，2026-11-13）

| 项 | 值 |
| --- | --- |
| 入口 | `scripts/hooks/commit-msg`（由 `scripts/install_git_hooks.py` 与 `secret_scan.py` 一起装到 common-dir 的 hooks 目录，旧工作树也能跑） |
| 判据 | `python scripts/secret_scan.py --commit-msg <消息文件>`：同一组 7 条形状规则，只跑形状不跑熵，BLOCK>0 退出 1 |
| 豁免 | 命中行内 `pragma: allowlist secret`（逐行；不做消息级/仓库级豁免） |
| 绕过 | `git commit --no-verify` 会同时跳过 pre-commit 与 commit-msg，post-commit 记 bypass 日志 |
| 反证 | `tests/test_secret_scan.py::test_commit_msg_hook_blocks_real_commit`：临时仓库装钩子后 `git commit -m "leak ghp_…"` 被拒且**不产生提交**，改干净消息后成功；`::test_commit_msg_mode_blocks_value_and_no_false_positive_on_prose` 钉住「只写『不要用 ghp_ 开头』不误报」 |

误报评估（决定「接」的依据）：

1. 规则要完整 token 形状（`gh[pousr]_` + 36 位、`AKIA` + 16 位等），正文里写「不要
   用 ghp_ 开头」**不命中**——已由上面的用例钉住；
2. 只有贴整串示例值才命中，出口是那一行的 pragma；
3. 已知误报面：`git revert` 自动带回被回滚提交的原文、`--amend` 与 squash 会拼接
   旧消息，旧消息里本就有凭据时会被拦——正确动作是改消息，不是 `--no-verify`；
4. 成本：每次提交多一个本地 python 进程（无网络、不写仓库），与 pre-commit 的扫描
   同量级；钩子缺失时按失败关闭（与 pre-commit 同一策略）。

解除条件：`commit-msg` 因**说明性文字**拦了正常提交且 pragma 出口不适用时，先补
反证用例再放宽判据；禁止改成「消息里出现 ghp_ 就放行」这类降级，也禁止把它做成
默认关闭 / 可选安装的软闸。

### 已知良性登记（1 个 blob；2026-11-13 裁定 A：保留，不清理）

| 项 | 值 |
| --- | --- |
| 对象 | `c20bd082d6382e0d498f029151e10e305a39ef12`（不可达 blob） |
| 打码指纹 | `ghp_` + 36 个**同一字符**（`distinct=1`）；69 字节 / 2 行；内容 sha256 `c9e65a6ebb1860f75f6aab662f941ed5d0c1fc695ecc3f450373e9e462a4e459` |
| 判据 | ① 不可达（没有任何提交引用它）；② 36 位 token 体字符全同 → 占位值；③ 命中规则与登记一致 |
| 落点 | `scripts/secret_scan.py::KNOWN_BENIGN_HISTORY`（只放 sha→规则名，不放正文） |
| 解除条件 | 同一规则命中**可达** blob、或命中**另一个 sha**、或该 blob 落入任何路径 → 照旧报 `HISTORY`（`_split_known_benign` 的三个条件缺一即不生效） |
| 裁定记录 | `docs/pending-patches/2026-11-13-git-history-secret-dangling-blob.md`（只记裁定与代价，事实以本节为准） |

不清理的理由：`git gc --prune=now` 会连 103 个 unreachable commit 一起不可逆删掉，
代价大于收益。若以后判成真值，正确动作是**轮换**——该值从未进入任何可达提交，
不需要改写历史。

### 发布产物门禁（2026-11-13）

发布产物不得包含任何 BLOCK 命中的文件。两个打包入口共用同一判据（
`scripts/secret_scan.py` 的 7 条形状规则，不另抄一份正则）：

| 入口 | 位置 | 时机 |
| --- | --- | --- |
| Python onedir | `scripts/release_manifest.py` 第 5 项 `secrets`（`--only` 默认含） | 发版前跑清单机检时 |
| C++ 便携包 | `scripts/package_windows.ps1` → `python scripts/secret_scan.py --artifact $stage` | 压 zip 之前，红就 `throw`，不出包 |

产物扫描按**字节**跑（exe/dll/pyc 里内嵌的字符串同样算），不跑熵、不认 pragma；
命中即 FAIL 并点名相对路径，输出不含值。反证：`tests/test_release_secrets.py`
往临时「待打包目录」塞含假 PAT 的文本文件与二进制壳，检查必须红并点名文件，
删掉即绿。

### `D:\mbdsdr\output\` 那 4 个未入库文件会不会进发布物

结论：**不会**。依据是三条互相独立的包含面，不是「应该不会」：

| 层 | 规则 | 为什么排除 |
| --- | --- | --- |
| git | 仓库根是 `D:\mbdsdr\MBDSDR`，`D:\mbdsdr\output\` 在根之外；无 `.gitmodules`（没有子模块指过去） | git 不跟踪根目录之外的文件，任何提交 / `git archive` 都不含它 |
| PyInstaller | `desktop/MBDSDR.spec`：入口 `desktop/main.py`；`datas` 是显式清单（`assets/`、`LICENSE`、`NOTICE.md`、`third_party_licenses/`、`mbdsdr_ai/astronomy.py`、`collect_data_files('mbdsdr_ai')`），`binaries=[]` | 显式清单里没有 `output/`，也没有对仓库根做通配；产物落在 `.tmp\pyi-distN` |
| C++ 便携包 | `scripts/package_windows.ps1`：`$stage` 只来自 `cmake --install $BuildDir`（默认 `cpp/build-local`）+ Qt DLL，`$zip` 只压 `$stage` | stage 的输入是构建树，不扫 `D:\mbdsdr` |

上传/分发面：全仓（`*.py/*.ps1/*.bat/*.md/*.spec/*.yml`）搜 `MBDSDR/output`、
`../output`、`..\output` **0 处命中**——没有任何脚本把该目录当输入。

但这 4 个文件仍是工作树 BLOCK（`output\api_push.py:18`、`ci_watch.sh:4`、
`ci_watch71.sh:4`、`ci_watch72.sh:3`，同一枚 PAT）：它们不在仓库里，所以既进不了
产物、也不受 pre-commit 管，只能人工处理（删文件或把 token 移出），不在本批次范围。

## 熵告警分级（2026-11-13，降噪）

告警洪水等于没有告警：降噪前全仓 WARN 8558 条，其中熵告警 8499 条（**口径：每文件
每规则一条**——`scan_text` 里 `entropy_reported` 让一个文件最多出一条熵告警）。

### 先量：8499/8502 条都在哪

`python scripts/secret_scan.py --entropy-stats`（只量不改判）：

| 维度 | 分布（前几项） |
| --- | --- |
| 后缀 | `.md` 4740（55.8%）、`.yaml` 1274（15.0%）、`.py` 1066、`.cpp` 544、`.json` 230、`.txt` 202、`.ts` 157、`.h` 66、其余 <50 |
| 目录（前两层） | `scratch/refs` 3751（44.1%）、`output/tmp` 3609（42.4%）、`output/_wt_head_attr` 223、`output/_wt_crashprv` 221、`MBDSDR/docs` 192、`output/remote_snapshot` 161、其余 <40 |
| 分数区间 | 4.2~4.5 **7633（89.8%）**、4.5~5.0 604、5.0~5.5 249、5.5~6.0 7、≥6.0 9 |

两个结论：**86.5% 来自两棵非产品树**（第三方参照克隆 `scratch/refs` + 旧 worktree
副本 `output/tmp`）；**89.8% 贴着下限**（4.2~4.5）。第二条正是不能靠调阈值的原因：
把下限抬到 4.5 能砍掉九成，但会把「刚好过线」的真凭据一起砍掉。所以按**结构**分级。

### 分级（判据只看位置/文件名/字段名，不动阈值）

**MUST（必须看）**——三选一，命中即必须人工过目：

1. 整名就是凭据文件：`.env` / `.env.*` / `*.pem|key|pfx|p12|keystore|jks`、
   `secrets.yaml`、`credentials.json`、`id_rsa`、`.netrc` 这类；
2. 高熵串**紧邻**凭据字段名：字段名与值之间只允许 `: = " '` 与空白（只看候选串
   前 48 字符窗口），如 `api_key = "…"`、`"token": "…"`、`APPLE_API_KEY: '…'`；
3. 其余一律 SAMPLE。

文件名判据只认**整名**、不做子串匹配：`test_secret_scan.py`、
`2026-07-29-...-credentials.i18n.yaml` 这类名字里带 secret/token 的测试与文档不是
凭据面——按子串判会把「必须看」重新灌满（第一版就是这么错的）。

**SAMPLE（抽样看）**——普通源码/文档/第三方树里的长串：计数全量保留，终端只打 20
条代表，全量分布用 `--entropy-stats`。**WARN** 档（argv 22 / log 37）维持原样。

### 反证（降噪之后仍拦得住，全在 `tests/test_secret_scan.py`）

| 用例 | 内容 | 期望 |
| --- | --- | --- |
| `test_entropy_still_hits_random_base64_in_python_source` | 普通 `.py` 里 32 字节随机 base64 | 仍命中，落 SAMPLE |
| `test_entropy_env_file_is_must` | `.env` 里 `API_KEY=<随机>` | 仍命中，落 MUST |
| `test_entropy_json_api_key_is_must` | `.json` 的 `"api_key": "<随机>"` | 仍命中，落 MUST |
| `test_long_blobs_in_assets_stay_sample` | minified JS 长串 + `data:image/png;base64,…` | 仍命中，落 SAMPLE |
| `test_entropy_stats_reports_distribution` | 4 个样本文件跑 `--entropy-stats` | `总=4 must=2 sample=2`，分层口径与扫描一致 |

随机值在测试里由 `sha256` 派生（`base64.b64encode(hashlib.sha256(...).digest())`），
源码里不留长字面量——否则测试自己就成了新的样本噪音。

### 降噪后实测（2026-11-13，`D:\mbdsdr`，同口径）

| 档 | 条数 |
| --- | --- |
| BLOCK | 4（不变：`output\` 4 个未入库文件，同一枚 PAT） |
| MUST | **2** |
| SAMPLE | 8500（终端打 20 条代表） |
| WARN | 59（argv 22 + log 37） |

MUST 只剩 2 条，都是第三方参照克隆里的测试夹具：
`scratch/refs/OnAir/tests/mesh/test_mesh_proto_rs.cpp:125`（`kSecretB64 = "…"`）、
`scratch/refs/deepseek-harness/apps/desktop/tests/macos-signature.spec.ts:29`
（`APPLE_API_KEY: '….p8'`）。两条都符合「字段名紧邻高熵值」，该看。

### 顺带修掉的真缺口

`Path(".env").suffix == ""`——`.env` 虽然写在 `_ENTROPY_SUFFIXES` 里，却**从未**
参与熵检查（suffix 永远匹配不上）。反证 (b) 先红后绿：先按「`.env` 必须命中」写
用例（失败，0 命中），才补 `_ENV_NAME_RE.match(name)` 让凭据面按**名字**入列。
当前仓库里没有任何 `.env` 文件（全仓 `-Filter ".env*"` 0 个），所以计数没变，但
通道现在是通的。

## 分布

按目录：`mbdsdr_ai` 692 / `desktop` 574 / `tests` 62 / `experiments` 32 / `scripts` 26 / `tools` 8 / 根脚本 3 / `docs` 1。

按类别：`ERROR/EMPTY` 574、`WARN/SILENT_CONTROL` 523、`WARN/SILENT_FALLBACK` 301。

高风险前 10（完整 256 个文件见附录 A）：

| 文件 | 合计 | ERROR | WARN |
| --- | --- | --- | --- |
| `desktop/main_window.py` | 341 | 239 | 102 |
| `mbdsdr_ai/sdr_backend.py` | 112 | 50 | 62 |
| `desktop/rf_sky_view.py` | 71 | 21 | 50 |
| `mbdsdr_ai/agent.py` | 51 | 11 | 40 |
| `mbdsdr_ai/sdr_tools.py` | 49 | 17 | 32 |
| `desktop/receive_pipeline.py` | 21 | 18 | 3 |
| `desktop/dock_layout_manager.py` | 20 | 11 | 9 |
| `mbdsdr_ai/device_manager.py` | 16 | 8 | 8 |
| `mbdsdr_ai/core/controller.py` | 15 | 1 | 14 |
| `mbdsdr_ai/orbit_tools.py` | 13 | 0 | 13 |

典型危险形态（都与「用户看不见的功能丢失」同构）：

- 设备打开/调谐失败后 `pass`：状态栏仍显示「已连接」，实际无数据（`sdr_backend.py` 多处）。
- 面板/控件构建异常后 `pass`：界面缺块且无提示（`main_window.py::_build_central_widget`、`desktop/*_panel.py`）。
- 天体/星表查找失败后 `continue` / 置 `None`：图层绘制与点击命中整段失效（`rf_sky_view.py::_find_body_at`）。
- `float(None)`、`KeyError`、`AttributeError` 等**编程错误**被 catch-all 吞掉：本类一律算缺陷，不能以「兜底」名义保留。

## 反证（棘轮，不再靠全量缺陷判红）

`tests/test_swallow_guard.py` 分五组：

- 扫描器行为组：DSO 事故同构（`float(None)` 被 `except Exception: pass` → `ERROR/EMPTY`）；静默 return → WARN；窄异常不误报；有记录/真重抛不误报；**假重抛**判 ERROR；**`suppress(Exception)`** 判 ERROR 且不可白名单；白名单三问缺项/缺 `review_by`/过期均判不合格；三问齐全才豁免。
- 白名单 trigger 组：`trigger` 缺失 / 未知类型 / 指向测试里不存在的屏蔽方式均判不合格（`test_whitelist_trigger_must_be_exercised` 是反证）；仓库里现有条目必须通过完整门禁。
- 降级路径组：`audio_out` 在 `import sounddevice` 抛 `ImportError` 时整链降级（`test_sounddevice_import_fail_degrades`），以及 `sd=None` 形态（`test_sounddevice_absent_degrades`）。
- 棘轮组：新文件出现静默 catch-all → 越线；存量下降 → 不越线；单文件 ERROR 增长 → 越线；仓库当前扫描 `check_ratchet` 必须为空。
- 现状组：白名单有效且未失效；禁令构造为 0；扫描确定性用固定夹具验证（仓库在途文件会被其他线并发改写，不能当夹具）。

修前必红的证据保留在扫描器本身：旧版 `test_repo_has_no_unwhitelisted_silent_catch_all` 曾以「须修 1398 处」失败；现在改为基线棘轮——存量不阻塞，新增立刻红。

复跑：

```
python scripts/lint_swallow_all.py                        # 棘轮判定（越线才退 1）
python scripts/lint_swallow_all.py --list                 # 打印每一处 catch-all
python scripts/lint_swallow_all.py --json out.json        # 完整明细
python scripts/lint_swallow_all.py --write-baseline docs/code-guards-baseline.json
python -m pytest tests/test_swallow_guard.py -q            # 24 passed + 1 xfailed（K06 已知红，xfail 不计为通过）
```

## 门禁脚本入口登记（D4）

`axioms.py --rule D4` 只认两种入口：git 钩子，或本文件的登记。**不放
pre-commit**：pre-commit 现在只跑 claim 范围 + claim 台账 + 暂存区凭据扫描
（每次提交约 0.2s×3），而机检脚本最便宜的 0.2s、最贵的 112s；逐个挂上去会让
每次提交慢几十倍，然后大家开始用 `--no-verify`，把 claim 闸一起废掉。

2026-11-13 全量测绘口径（44 个机检脚本，`scripts/` 下 `check_*`/`audit_*`/
`verify_*`/`lint_*`/`*_scan`/带 `--check` 的生成器，排除 bench/硬件/一次性工具）：
逐条实跑 1 次、`QT_QPA_PLATFORM=offscreen`、取墙钟秒；**没有一个脚本自己取
`qt_test.lock`**，所以下表的「Qt」一栏只有「是否 import/启动 Qt」的区别，不是锁。
按覆盖分为三档：**A 自动入口（26 条）**、**B 只有测试入口、仓库现状无人跑（13 条）**、
**C 无任何入口（5 条，漏网）**。

### A. 已接入自动入口（覆盖当前仓库）

`release_check.py` 的 `guards` 步（14 条 + 1 条只提示），加上原有
`docs`/`gen_reconcile`/`action_dup` 步与 3 个 git 钩子。`guards` 连跑 5 次墙钟
**35.2s / 39.2s / 40.1s / 46.9s / 82.2s，中位 40.1s**（最后两次他线在改
`scripts/secret_scan.py`，`install_git_hooks --check` 因工作树与钩子副本不一致
瞬时红）；单项超时 120s，任一非 0 即整步 FAIL，明细点名到脚本。

**2026-11-13 A 档复核**：原 27 条逐条 `git grep` 调用方/钩子配置，26 条有真实触发点
（`release_check.py` 的 `GUARD_CHECKS`/`do_docs`/`do_gen_reconcile`/`do_action_dup`、
`scripts/hooks/pre-commit`、`scripts/hooks/post-commit`、`.github/workflows/ci.yml`、
`scripts/run_all_tests.sh`）。**1 条是假入口**：`release_manifest.py` 只在测试里被
`import` 函数、`package_windows.ps1` 只在注释里提到，没有任何命令跑本仓或真 dist，
已移入 B 档。以后新增 A 档条目必须同时留下「可 grep 到的调用方」。

| 脚本 | 入口 | 什么时候跑 | 谁跑 | 复核 |
| --- | --- | --- | --- | --- |
| `scripts/verify_claims.py` | 自动：`release_check.py --steps docs` | 每次发版第一步；`--check-lines --strict-lines` 不通过即 FAIL（实测 33.7–59.8s，随并发提交数上涨；2026-11-13 已把 38 条坐标漂移逐条真修，豁免 0） | 发版人 | 2027-01 |
| `scripts/check_rollback_table.py` | 自动：`release_check.py --steps docs` | 发版前；§6.10.2 表格改动后本地跑（2.3s） | 发版人 / 表格改动者 | 2027-01 |
| `scripts/check_generated_artifacts.py` | 自动：`release_check.py --steps gen_reconcile` | 发版前（3.7s），同时覆盖 `gen_mcp_tools_doc` 的产物 | 发版人 | 2027-01 |
| `scripts/check_action_duplication.py` | 自动：`release_check.py --steps action_dup` | 发版前；菜单/QAction 增改后本地跑 `--gate`（5.9s，需 Qt/offscreen） | 发版人 / UI 改动者 | 2027-01 |
| `scripts/check_help_paths.py` | 自动：`release_check.py --steps docs`（**仅渲染点**；全量 CLI 无自动入口） | 发版前自动跑渲染点；帮助文案增删后手动跑全量（**112.4s**，原登记写 13s 已更正） | 发版人 / 帮助文案改动者 | 2027-01 |
| `scripts/check_card_links.py` | 自动：`guards` + `tests/test_card_links.py` | 发版前（0.2s）；§ 章节增删/改号后由文档 owner 跑 `--fix-anchors` | 发版人 / 文档 owner | 2027-01 |
| `scripts/check_data_licenses.py` | 自动：`guards` + `tests/test_data_licenses.py` | 发版前（12.6s）；新增/替换数据文件后本地先跑 | 发版人 / 数据改动者 | 2027-01 |
| `scripts/audit_source_text_pins.py` | 自动：`guards` + `tests/test_source_pin_ratchet.py` | 发版前（9.1s）；基线更新或批次末跑 `--report` | 测试线 / 发版人 | 2027-01 |
| `scripts/gen_shortcut_table.py` | 自动：`guards --check` + `tests/test_shortcut_table.py` | 发版前（0.2s）；快捷键注册表改动后本地 `--check` | 发版人 / UI 改动者 | 2027-01 |
| `scripts/settings_inventory.py` | 自动：`guards --check` + `tests/test_settings_ui_coverage.py` | 发版前（1.4s）；设置项增删后重建文档块 | 发版人 / 设置页 owner | 2027-01 |
| `scripts/theme_contrast.py` | 自动：`guards` + `tests/test_theme_contrast_gate.py` | 发版前（0.3s）；主题色改动后本地跑 | 发版人 / 主题维护者 | 2027-01 |
| `scripts/install_git_hooks.py` | 自动：`guards --check` | 发版前（0.3s）核对本机钩子是不是最新副本——**「我修了」≠「别人机器上是修复后的」**；不一致直接 FAIL，跑一次安装即可 | 发版人 | 2027-01 |
| `scripts/fetch_constellation_names_cc0.py` | 自动：`guards --check` | 发版前（0.2s，离线核对 payload 哈希，不联网） | 发版人 | 2027-01 |
| `scripts/fetch_dso_supplement.py` | 自动：`guards --check` | 发版前（0.2s，离线核对补字段哈希） | 发版人 | 2027-01 |
| `scripts/ai_intent_regression.py` | 自动：`guards --limit 40` + `tests/test_ai_tool_budget.py` | 发版前（5.5s）；语料/工具注册表改动后本地跑 | 发版人 / AI 工具线 | 2027-01 |
| `scripts/ai_tool_consistency.py` | 自动：`guards --limit 40` + `tests/test_ai_tool_consistency.py` | 发版前（2.6s）；工具名/描述改动后本地跑 | 发版人 / AI 工具线 | 2027-01 |
| `scripts/tool_honesty_scan.py` | 自动：`guards` | 发版前（4.9s）；工具描述声称与实际能力一致性 | 发版人 | 2027-01 |
| `scripts/access_layer_fault_injection.py` | 自动：`guards` | 发版前（5.0s）：无硬件/无声卡/无外网三条降级链 | 发版人 / 接入层 owner | 2027-01 |
| `scripts/check_roadmap_blocking.py` | 自动（只提示，恒退出 0）：`guards` | 发版前附结论行；路线图/阻塞理由变更后由维护者人读输出（0.2s） | 路线图维护者 | 2027-01 |
| `scripts/check_commit_scope.py` | 自动：`scripts/hooks/pre-commit` | 每次 `git commit` 前（暂存区，约 0.1s） | 全体提交者（钩子自动） | 2027-01 |
| `scripts/claim_gate.py` | 自动：`scripts/hooks/pre-commit` | 每次 `git commit` 前逐文件查 claim 台账（约 0.2s） | 全体提交者（钩子自动） | 2027-01 |
| `scripts/secret_scan.py` | 自动：`scripts/hooks/pre-commit`（**仅 `--staged`**） | 每次提交前扫暂存区；全量 `--history`/全树扫描**无自动入口**（实测 >300s，见 C 档） | 全体提交者（钩子自动） | 2027-01 |
| `scripts/git_sync_index.py` | 自动：`post-commit` 钩子 + `tests/test_git_sync_index.py` | 每次提交后同步共享 index（钩子自动） | 全体提交者（钩子自动） | 2027-01 |
| `scripts/make_decode_fixtures.py --check` | 自动：`release_check.py --steps guards` | 发版前（4.2s）：生成器与 8 个 cf32/wav + `manifest.json` 逐字对账；2026-11-13 生成器补 `provenance: self-generated` 后转绿 | 发版人 / 解码 fixture 改动者 | 2027-01 |
| `scripts/check_commit_residue.py` | 自动：`scripts/hooks/post-commit`（`--repo <root> --json HEAD`） | **每次提交后**（实测 0.68–0.85s）：本次提交新增的连续行块若以「删除」形态回到工作区未提交 diff → 打印 RESIDUE 并追加 `D:\mbdsdr\.locks\residue_commits.log`；只记录不阻断（提交已落地） | 全体提交者（钩子自动） | 2027-01 |
| `scripts/ci_pytest_groups.py` | 自动：`.github/workflows/ci.yml`（`--check` + `--group/--total`）+ `scripts/run_all_tests.sh` + `tests/test_collection_consistency.py` | 每次 CI 的 `tests` 矩阵 job（先 `--check` 断言并集=全集，再取本组文件跑 pytest）；本地全量测试走同一分组 | CI runner / 跑全量测试的人 | 2027-01 |

### B. 只有测试入口：扫描器被测，但「当前仓库」没人跑

这些脚本的 CLI 在 `tests/` 里对**临时夹具**跑过，逻辑有回归保护；但没有任何
入口对**本仓当前状态**执行，所以「仓库现在是红的」它抓不到。它们的红/绿
只在有人手动跑时才可见——这就是「一条命令输出合规状态」在这 13 条上**不成立**。

**B 档不是「没人跑」的抽屉**（2026-11-13 裁定）：每行必须写清「什么时候跑 / 谁跑」；
写不出责任人与触发时机的条目直接进 C 档并写明原因，不许用「反正有人会跑」占位。

| 脚本 | 现有入口 | 缺什么 | 什么时候跑 | 谁跑 | 复核 |
| --- | --- | --- | --- | --- | --- |
| `scripts/release_manifest.py` | `tests/test_release_secrets.py`、`tests/test_version_literal_guard.py` 只 `import` 函数并对**临时 dist** 断言 | 没有命令对**本仓/真 dist** 跑 CLI；`scripts/package_windows.ps1:149` 只是在注释里提到同判据，`docs/known-red-register.md:1136` 是人工复跑命令 | **每次 `python scripts/release_check.py --release` 构建成功后、压 zip 之前**：发版人对真 dist 跑 `python scripts/release_manifest.py --dist D:\mbdsdr\.tmp\pyi-dist12\MBDSDR`（`--only` 默认含 `secrets`）；`package_windows.ps1` 的 owner 应在落盘后调用同一命令，不能只留注释。当前对本仓是红的（`known-red-register.md:1136`），所以暂不接进日常闸 | 发版人（出包脚本 owner 共同承担） | 2027-01 |
| `scripts/lint_swallow_all.py` | `tests/test_swallow_guard.py`（棘轮） | 仓库现状（37.0s，实测 rc=1：越线中） | 批次末与发版前手动；越线必须当天归因 | 吞噬治理线 | 2026-12 |
| `scripts/check_red_lifecycle.py` | `tests/test_red_lifecycle.py` | 仓库现状（27.3s，**不进 pre-commit**；2026-11-13 05:54 实测 **rc=1：FAIL 9 / WARN 0 / SKIP 0**＝R8 待复核 3 条＋「已修缺 hash」6 条；红项清单交各 owner，不在扫描时顺带改别人的段落） | **触发条件**：① 每个批次收口前至少 1 次；② 任何 K 条目新增/改绑定/改状态后立即；③ 发版前必跑。**谁跑**：红项 owner（当前 `守卫账本线`）；谁改 K 账谁负责跑。 | 2026-12 |
| `scripts/check_red_accounting.py` | `tests/test_red_accounting.py`（8 条反证，含「rc=1 且无条目必须报无账红」） | 守卫账本 `docs/guard-accounting.json`；`--fast` 约 60s，全量约 5min（含 audit_evidence / release_manifest） | 批次末与发版前；任一守卫改退出码语义或新增守卫后 | 红项 owner（守卫账本线） | 2026-12 |
| `scripts/check_destructive_ops.py` | `tests/test_destructive_audit.py` | 仓库 reflog（0.5s，实测 rc=1：27 笔未审计） | 发生 reset/clean/branch -D 当天 | 操作执行人 | 2026-12 |
| `scripts/check_test_version.py` | `tests/test_check_test_version.py` | 只在事件调查时对具体路径跑（内部两次 pytest） | 怀疑「改测试销红」时 | 复核人 | 2027-01 |
| `scripts/find_blob_reverts.py` | `tests/test_find_blob_reverts.py` | 仓库历史 | 发现「刚修又被改回」时 | 事件调查人 | 2027-01 |
| `scripts/tool_param_fuzz.py` | `tests/test_tool_param_fuzz_guard.py` | 全量工具参数（测试只覆盖守卫逻辑） | 工具 schema 改动后 | AI 工具线 | 2027-01 |
| `scripts/tool_availability_audit.py` | 间接：`tool_param_fuzz.py` 调用 | 495 工具逐个可执行性（人工判读） | 工具批量增删后 | AI 工具线 | 2027-01 |
| `scripts/qsettings_guard.py` | `tests/conftest.py` | 只在 pytest 进程内生效；产品真机启动不覆盖 | 涉及 QSettings 写入的改动 + 发版前 | 桌面线 | 2027-01 |
| `scripts/gen_mcp_tools_doc.py` | 间接：产物由 `check_generated_artifacts.py` 对账 | 生成器本身无测试 | 工具注册表改动后重生成 | AI 工具线 | 2027-01 |
| `scripts/report_token_dual_values.py` | 手动（`docs/a11y/`） | 无自动对账；18.5s | 主题令牌/双值治理改动后 | 主题维护者 | 2027-01 |
| `scripts/release_check.py` | 本体入口 + `tests/test_narrow_gate_selfcheck.py` | —（其余步骤的汇总器） | 发版前 | 发版人 | 2027-01 |

### C. 无任何入口（漏网）

**这 5 条脚本（外加 1 个无入口的 `--history` 全量模式）谁也跑不到**。要么缺
必填参数（dist/commit 范围），要么要真机/GUI——「写不出谁跑」的，直接写原因，不编。

| 脚本 | 为什么没有入口 | 建议触发条件 | 谁跑 | 复核 |
| --- | --- | --- | --- | --- |
| `scripts/audit_evidence.py` | 51.5s 且**当前红**（2,702 条数字断言无证据）；接入会立刻把发版闸门变成常红 | 证据标准改版 / 批次末；先清库存再谈接入 | 证据标准 owner | 2026-12 |
| `scripts/config_write_guard.py` | 哨兵脚本，需挂在真实配置写点；目前只被 `scripts/repro/` 引用 | 配置写入路径改动时 + 发版前 | 桌面线 | 2027-01 |
| `scripts/check_dist_head_parity.py` | `--dist` 必填，无产物无法执行；实测未跑 | 每次出安装包后（与 release_manifest 同批） | 发版人 | 2027-01 |
| `scripts/ui_layout_audit.py` | 需要起 GUI 并对比几何基线（Qt），耗时与稳定性都不适合闸门 | UI 布局/尺寸改动后手动，基线更新必须给 `--reason` | UI 线 | 2027-01 |
| `scripts/sample_rate_readback_check.py` | 需要真实 SDR 硬件，CI/无硬件环境不可执行 | 有硬件的验收机，采样率路径改动后 | 硬件验收人 | 2027-01 |
| `scripts/secret_scan.py --history`（全量模式） | 单次 >300s（实测超时）；钩子只覆盖暂存区 | 发版前或季度一次，人工跑并留结论 | 安全 owner | 2027-01 |

删除判定：**0 个**。44 个都有活调用方、活文档流程或明确待接条件；C 档没有一个是
「结论已无人使用」的死代码，只是**入口缺失**。`check_card_links.py` 已于
2026-11-13 转绿（links=16 全 PASS），`make_decode_fixtures.py --check` 同日修绿并
接入 `guards`；`check_destructive_ops.py` / `audit_evidence.py` / `lint_swallow_all.py` 当前仍是红的，
已分别入 K22 / K23 / K06，属于待修项，不是死代码；`check_red_lifecycle.py`
2026-11-13 05:54 实测 rc=1（FAIL 9：R8 待复核 3 条＋「修完没留 hash」6 条），
触发条件见上表，红项交各 owner，尚未转绿。

### 零引用核查（2026-11-13）：两条「写不出责任人」的处置

上一版登记里 `check_commit_residue.py`、`ci_pytest_groups.py` 被标成「无人引用 /
无 CI、待认领」。按「先证零引用、证完再删」的口径逐条取证，**两条都不成立，不删**：

| 脚本 | 零引用证据（反证） | 结论 |
| --- | --- | --- |
| `scripts/check_commit_residue.py` | ① `scripts/hooks/post-commit:280-306` 就是调用方：`--repo <root> --json HEAD`，每次提交后跑，实测 0.68–0.85s；② 已安装副本 `.git/hooks/post-commit` 含 `_residue_records`；③ `docs/COORDINATION.md:1914` 记录了 `--range A..B` 的事后复核用法 | 入口存在（post-commit），改登记到 A 档；不接 pre-commit（虽只要 0.7s，但它是提交后检查，挂 pre-commit 等于用未提交内容判已提交事实） |
| `scripts/ci_pytest_groups.py` | ① `.github/workflows/ci.yml:150-166` 每 job 先 `--check` 再 `--group/--total`；② `scripts/run_all_tests.sh:160-163` 本地全量同分组；③ `tests/test_collection_consistency.py:7` 显式覆盖分组口径 | **本仓有 CI**（`.github/workflows/ci.yml`），「无 CI」是上版测绘错误；改登记到 A 档 |

可回滚点：本表改动前的版本为提交 `3de656f4`（`docs: guards 步接入与 43 条登记`），
如需还原两条「待认领」的旧表述，`git show 3de656f4:docs/code-guards.md` 即原文。

### 复核记录（2026-11-05，HEAD `d41591b`，工作区含他线在途改动）

- 扫描：1,069 文件 / catch-all 1,896 / 静默 1,392 / 须修 1,391（基线 1,398，总量仍**只降**）。
- 越线 1 项（**干净 HEAD 也红**）：`mbdsdr_ai/hackrf_params.py` `WARN 2 > 基线 1`。基线提交 `7332b95`（10:23）之后落地的 `9b803d0`（10:57，TX 流）新增了 `write_tx` 的 `except Exception: return False`（HEAD 第 625 行）。**不得靠调高该文件基线消化**：交后端线补日志/错误状态字段，或按标准修法登记白名单三问 + `review_by`。
- 越线 1 项（**仅工作区在途**）：`desktop/settings.py` `WARN 3 > 基线 1`（新增 2）；该文件 HEAD 版本扫描仍为 1/1，属未提交改动，提交前必须收掉，不能顺手抬基线。
- 其余文件未越线；`main_window.py` 239/102、`sdr_tools.py` 17/32 与基线逐项一致，`rf_sky_view.py` 仍在 21/51 以内。
- 复核口径固定：先跑扫描器比基线，再跑 `tests/test_swallow_guard.py`；有人声称「某文件修了 N 处」时，看须修总数与单文件计数**只降不升**，并确认基线文件本身的改动是随修复同步下降，而不是被调高。

## 标准修法（修一处就是这三步）

1. 能收窄异常类型的直接收窄（`except ValueError` / `except (OSError, RuntimeError)`），不吞编程错误；
2. 确属外部边界：补日志或错误状态字段 + 一条断言「降级后用户看到什么」的测试 + 登记白名单三问 + `review_by`；
3. 禁止 `contextlib.suppress(Exception)`、`except Exception: raise`、以及任何等价绕过写法；扫描器已把前两者判红。

## 定点清除清单（按用户会被伤到的顺序）

### A1. `desktop/main_window.py`：连接/录制/设备路径的 EMPTY（48 处）

`main_window.py` 本轮不碰，交桌面/操作逻辑线。行号已按当前提交树
（`5332140`）重新核对；该文件近期无提交改动：

| 方法（用户动作） | ERROR 行号 |
| --- | --- |
| `_connect_backend`（连接后端） | 3119, 3126, 3133, 3149, 3156, 3161, 3202, 3206, 3210, 3215 |
| `_connect_real`（连接真实设备） | 3600 |
| `_panels_set_sdr_connected`（连接后启用面板） | 3629, 3638, 3645, 3658, 3664, 3670, 3683, 3690, 3696, 3703, 3722, 3727, 3735 |
| `_teardown_connection`（断开/清理） | 3756, 3764, 3770, 3778, 3791, 3796, 3802 |
| `_on_connection_for_ui` / `_on_module_device_selected` | 3845, 3851, 5944 |
| `_on_device_plugged` / `_on_panel_device_connect` | 6004, 6101 |
| `_on_record_toggled` / `_record_write_chunk` / `_on_pipeline_record_iq` | 4035, 4951, 4961 |
| `_start_recording` / `_stop_recording`（录音） | 5557, 5567, 5577, 5631, 5640, 5646 |
| `_replay_recording`（回放） | 6733, 6737 |
| `_disconnect_gnss` | 7335 |

合计 48 处，与扫描计数一致。定位以方法名为锚，文件一旦有在途改动先按方法重扫再改行号。

判定：这些吞一次就是「点了没反应」；修的时候必须断言用户看到的状态（状态栏、按钮、提示），不能只补日志。

### A2. `desktop/rf_sky_view.py::_find_body_at`（6 处，含 DSO 事故）

| 行号（HEAD `5332140` 起） | 类别 | 现状 |
| --- | --- | --- |
| 5399 | ERROR/EMPTY | `float(None)` 类失败被 `pass`（事故点） |
| 5413 | ERROR/EMPTY | `pass` |
| 5459 | WARN/SILENT_CONTROL | `continue`（该天体不画） |
| 5519 | WARN/SILENT_FALLBACK | `px_arr = py_arr = alt_draw_arr = None` |
| 5537 | WARN/SILENT_CONTROL | `continue`（该天体不画） |
| 5554 | WARN/SILENT_FALLBACK | `hyg = 0` |

天空线在 `5332140` 已把在途改动提交，工作区与 HEAD 一致；命中数仍为 6
（2 ERROR + 4 WARN）。定位一律以 `RFSkyView._find_body_at` + 片段为锚，
不要只认行号（该文件近期每次提交都会整体位移）。

要求：每处修复都要有「天体出现在图层 / 点击命中返回该天体」的断言；`_find_body_at` 至少要区分「星表无此目标」与「解析失败」。

### A3. `mbdsdr_ai/sdr_tools.py`：AI 工具面（49 处，17 ERROR + 32 WARN）

ERROR（`pass`，模型会当成成功继续编解释）行号（已按 `5332140` 复核，17 处逐一命中）：661, 3117, 3185, 3191, 3375, 3471, 3797, 3986, 4055, 4118, 4527, 4930, 5451, 7873, 7891, 7925, 7943。

WARN 多数是 `return ToolResult(success=False, content=...)`（用户/模型能看到失败文本，但没有日志与错误码）。这 32 处至少要补错误码/日志，不能让模型只拿到一句自由文本。

### 其余

`desktop/` 其余 526 处、`mbdsdr_ai/` 其余 643 处按兵不动，靠棘轮只降不升。测试线 62 处（多为 teardown）与 `experiments/` 32 处排在最后。

### D. 三条长输入挂死：实测曲线与处置

**这不只是「慢」，还是内存炸弹。** 2026-11-05 崩溃取证：`scripts/tool_param_fuzz.py`
的 `long_string` 用例把 `feldhell_decode` / `psk31_decode` / `rtty_decode` 的进程吃到
**17.2 GB** commit，叠加打满系统 38.1 GB 上限后进程被拖死（WER 2004）。所以这三个
工具的挂死阈值和内存阈值必须一起定。

测量方法：每个长度独立子进程，父进程每 30ms 采样 RSS/Private，超过 2.5 GB 立即强杀
（保护测量机）；两张表都走完整 handler（注册表 → 调制 → 解调 → 字符匹配），口径一致。
输入为 `A` 重复 n 次。

**优化前（Python `list` 存波形；括注失败 = 往返不匹配返回 `handler_error`）**

| 字符数 | feldhell 耗时 / RSS | psk31 耗时 / RSS | rtty 耗时 / RSS |
| --- | --- | --- | --- |
| 500 | 1.10s / 365MB | 0.37s / 164MB | 0.38s / 255MB |
| 1,000 | 2.19s / 611MB | 0.75s / 209MB | 0.76s / 389MB（失败） |
| 2,000 | 4.52s / 1,102MB | 1.48s / 297MB | 1.49s / 659MB（失败） |
| 4,000 | 9.10s / 2,153MB | 2.95s / 504MB | 2.99s / 1,198MB（失败） |
| 8,000 | **被 2.5GB 闸强杀** | 5.91s / 915MB | 5.98s / 2,274MB（失败） |
| 100,000 | 事故现场 17.2GB | 超 60s | 超 60s |

**优化后（`array('d')` 存波形 + 包络/FSK 能量流式累加）**

| 字符数 | feldhell 耗时 / RSS | psk31 耗时 / RSS | rtty 耗时 / RSS |
| --- | --- | --- | --- |
| 500 | 1.32s / 146MB | 0.40s / 130MB | 0.32s / 125MB |
| 1,000 | 2.60s / 208MB | 0.81s / 138MB | 0.65s / 131MB |
| 2,000 | 5.26s / 250MB | 1.65s / 155MB | 1.29s / 141MB |
| 4,000 | 10.55s / 498MB | 3.24s / 228MB | 2.64s / 179MB |
| 8,000（超上限） | 0.00006s / 118MB（结构化拒绝） | 同左 | 同左 |

同长度对比（4,000 字符）：RSS feldhell **2,153 → 498MB**、psk31 **504 → 228MB**、
rtty **1,198 → 179MB**；耗时 feldhell +16%、psk31 +10%，rtty 反而 -12%

诊断（含优化前数据）：

- **耗时按音频样本数线性，不是 O(n²)**：feldhell 每字符 14 列 × 约 457 采样
  （8kHz / 17.5 列/秒）；psk31 每字符约 11 bit × 256 采样；rtty 每字符约 8.5 bit ×
  176 采样。48000 与 8000 采样率下斜率随样本数同比变化。
- **内存曾经也是线性但常数极大**：每个采样是一个独立 Python `float` 对象
  （约 24 字节对象 + 8 字节指针 ≈ 32 字节/采样），而 `array('d')` 是 8 字节/采样。
  feldhell 每字符 6,400 采样，10 万字符就是 6.4 亿个 float —— 这正是 17.2GB 的来源。
- 优化前「2 万以后每字符耗时变高」是内存/GC 抬升，不是算法复杂度变化。
- 优化后 feldhell 仍是三者最慢（每字符约 2.6ms）；因为它每字符要生成 6,400 个
  采样点，且逐样本 `math.cos`。

处置：

1. **先优化，再限流**。波形从 `list[float]` 改为 `array('d')`；FeldHell 的包络检波
   去掉整段 `[abs(s)]` 列表，改为同一次扫描求峰值、分列求和；RTTY 的 4 条
   正交基带能量数组改为按 bit 窗口流式累加（每个 bit 只需 4 个累加器）。
   RSS 降到原来的 1/2～1/7（feldhell 4.3×、psk31 2.2×、rtty 6.7×）；耗时
   feldhell/psk31 增加 10～16%（`array` 逐项 append 比 `list` 慢），
   rtty 因去掉 4 条与波形等长的数组反而快 12%。这是刻意的取舍：用一点
   线性常数换掉 GB 级内存峰值。
2. **上限 3,000 字符**，写死在 `MAX_ROUNDTRIP_TEXT`，并**写入三个工具的 schema
   描述**（模型和用户调用前就能看到「单次上限 3000 字符，超出直接结构化拒绝」）。
   依据：最慢的 feldhell 3,000 字符约 7.9s，仍在单次 10s 闸内；3,000 字符在
   RTTY 45.45 baud 下约 9 分钟连续发射、FeldHell 约 40 分钟，远超真实业余报文
   （通常几百字符）。没有为了迁就某个测试用例去卡阈值。
3. 超限在 handler 入口秒回 `param_out_of_range`（带 `param/length/limit`），
   不做调制解调、不截断。
4. **模糊测试自身加内存闸**：每个用例独立监控本进程 commit 增长，超过
   `DEFAULT_MEM_CAP_MB = 1536` 立即注入异步异常中止并判 `MEMORY`，证据里带
   case 名、输入规模、峰值与增量；`--mem-cap-mb` 可改。验证方式：
   `tests/test_tool_param_fuzz_guard.py::test_fuzz_memory_cap_aborts_case`
   用「分配 64MB 后睡 0.6s」的 handler 在 16MB 闸下必须判 `MEMORY`。
5. **长输入分档串行**：`long_string` 按 2,000 / 20,000 / 100,000 三档，
   `array_long` 按 5,000 / 50,000 两档，逐档跑、逐档打印
   `输入 / rss_peak / commit_peak / 增量 / 耗时 / 结论`，不再一次性把最大档喂进去。

回归锁：

- `tests/test_tool_param_fuzz_guard.py::test_long_text_roundtrip_is_bounded`
  （10 万字符必须 <5s 返回结构化错误）。
- `tests/test_tool_param_fuzz_guard.py::test_text_roundtrip_limit_is_declared_in_schema`
  （上限必须出现在工具 schema 描述里，不能偷偷截断）。
- `tests/test_tool_param_fuzz_guard.py::test_over_limit_boundary_is_rejected_instantly`
  （`limit + 1` 字符即拒绝，`data.limit` 等于常量）。
- `tests/test_fldigi_roundtrip.py`（优化前后往返结果仍然正确）。

#### D.1 `rtty_decode` 长帧往返失配：是编解码缺陷，不是测试期望错（已修）

证据（同一段 `A` 重复文本，分别取编码侧 bit 流与解调侧 bit 流）：

| 观测点 | 数值 |
| --- | --- |
| 编码侧每 bit 发射样本数 | 恒定 **176**（`int(round(8000/45.45))`） |
| 解调侧窗口边界 | `int(k * spb)`，`spb = 8000/45.45 = 176.0176` |
| 首次出现边界差 | k=57（边界 10033 vs 整数 10032，+1 样本） |
| 累计漂移 | k=1000 → +17 样本；k=4000 → +70；k=8000 → +140（≈0.8 bit） |
| 首个 bit 失配 | **第 4853 个 bit**（累计漂移约 85 样本 ≈ 半个 bit），与文本长度无关 |
| 失配总数 | 1000 字符：1485 / 8000 bit；2000 字符：5728 / 16000 bit |
| 100 / 500 字符 | 0 失配（漂移未跨半 bit） |

结论：调制器和解调器的时间基准不一致（整数 176 vs 浮点 176.0176），是**真缺陷**；
测试夹具没有写错。

修复：`RTTYModem.spb_samples = int(round(spb))`，调制与解调都用它；
`8000/176 = 45.4545 baud`，与标称 45.45 一致，量化误差远小于 RTTY 容差。
修复后 1,000 / 2,000 / 3,000 字符往返全部精确还原；小写输入按 ITA-2 规则大写比对。

失败语义：若将来再出现不匹配，`rtty_decode` 返回 `error=decode_error`，正文明确写
「该长度下的 RTTY 解码结果不可信，请缩短或改用 psk31_decode」，不再只回
`handler_error`。回归锁：`tests/test_fldigi_roundtrip.py::test_rtty_long_frame_no_clock_drift`。

## 附录 A：按文件分布（256 个文件）

| 文件 | 合计 | ERROR | WARN |
| --- | --- | --- | --- |
| `desktop/main_window.py` | 341 | 239 | 102 |
| `mbdsdr_ai/sdr_backend.py` | 112 | 50 | 62 |
| `desktop/rf_sky_view.py` | 71 | 21 | 50 |
| `mbdsdr_ai/agent.py` | 51 | 11 | 40 |
| `mbdsdr_ai/sdr_tools.py` | 49 | 17 | 32 |
| `desktop/receive_pipeline.py` | 21 | 18 | 3 |
| `desktop/dock_layout_manager.py` | 20 | 11 | 9 |
| `mbdsdr_ai/device_manager.py` | 16 | 8 | 8 |
| `mbdsdr_ai/core/controller.py` | 15 | 1 | 14 |
| `mbdsdr_ai/orbit_tools.py` | 13 | 0 | 13 |
| `mbdsdr_ai/serial_gnss.py` | 13 | 7 | 6 |
| `desktop/spectrum_widget.py` | 12 | 8 | 4 |
| `desktop/settings_panel.py` | 11 | 6 | 5 |
| `mbdsdr_ai/startup_sequence.py` | 11 | 4 | 7 |
| `mbdsdr_ai/decoders.py` | 10 | 0 | 10 |
| `mbdsdr_ai/solar_system.py` | 10 | 3 | 7 |
| `mbdsdr_ai/orbit_determination.py` | 9 | 3 | 6 |
| `mbdsdr_ai/subagents.py` | 9 | 2 | 7 |
| `experiments/satellite_rx/scripts/stack_up.py` | 8 | 4 | 4 |
| `mbdsdr_ai/acquisition.py` | 8 | 0 | 8 |
| `mbdsdr_ai/gnss_monitor.py` | 8 | 1 | 7 |
| `mbdsdr_ai/hal.py` | 8 | 2 | 6 |
| `mbdsdr_ai/sat_passes.py` | 8 | 0 | 8 |
| `desktop/ai_panel.py` | 7 | 6 | 1 |
| `mbdsdr_ai/code_editor.py` | 7 | 0 | 7 |
| `mbdsdr_ai/modulation.py` | 7 | 0 | 7 |
| `mbdsdr_ai/rtklib_adapter.py` | 7 | 5 | 2 |
| `desktop/ntrip_panel.py` | 6 | 3 | 3 |
| `desktop/touch_helpers.py` | 6 | 4 | 2 |
| `mbdsdr_ai/audio_out.py` | 6 | 2 | 4 |
| `mbdsdr_ai/context_manager.py` | 6 | 1 | 5 |
| `mbdsdr_ai/hal_enhanced.py` | 6 | 4 | 2 |
| `mbdsdr_ai/sat_tracker.py` | 6 | 0 | 6 |
| `mbdsdr_ai/sync_tools.py` | 6 | 0 | 6 |
| `mbdsdr_ai/tx_guard.py` | 6 | 1 | 5 |
| `scripts/ui_interaction_audit.py` | 6 | 3 | 3 |
| `tools/hw_selfcheck/selfcheck.py` | 6 | 1 | 5 |
| `desktop/adsb_map_panel.py` | 5 | 4 | 1 |
| `desktop/bookmark_panel.py` | 5 | 0 | 5 |
| `desktop/control_panel.py` | 5 | 2 | 3 |
| `desktop/device_panel.py` | 5 | 0 | 5 |
| `desktop/satellite_image_panel.py` | 5 | 1 | 4 |
| `desktop/scanner_panel.py` | 5 | 0 | 5 |
| `mbdsdr_ai/audio_device_manager.py` | 5 | 0 | 5 |
| `mbdsdr_ai/mcp_client.py` | 5 | 4 | 1 |
| `mbdsdr_ai/reconnection.py` | 5 | 3 | 2 |
| `mbdsdr_ai/spyserver_client.py` | 5 | 4 | 1 |
| `scripts/soak_receive_chain.py` | 5 | 1 | 4 |
| `tests/test_lrpt_meteor_real_golden.py` | 5 | 0 | 5 |
| `desktop/aprs_panel.py` | 4 | 0 | 4 |
| `desktop/gain_panel.py` | 4 | 2 | 2 |
| `desktop/main.py` | 4 | 2 | 2 |
| `desktop/status_panel.py` | 4 | 0 | 4 |
| `mbdsdr_ai/ccsds_tools.py` | 4 | 0 | 4 |
| `mbdsdr_ai/fengyun_sat.py` | 4 | 1 | 3 |
| `mbdsdr_ai/frame_layout_tool.py` | 4 | 0 | 4 |
| `mbdsdr_ai/gimbal.py` | 4 | 2 | 2 |
| `mbdsdr_ai/m17_adapter.py` | 4 | 0 | 4 |
| `mbdsdr_ai/op25_adapter.py` | 4 | 0 | 4 |
| `mbdsdr_ai/openapi_integration.py` | 4 | 0 | 4 |
| `mbdsdr_ai/openwebrx_adapter.py` | 4 | 0 | 4 |
| `mbdsdr_ai/orbit.py` | 4 | 0 | 4 |
| `mbdsdr_ai/pat_adapter.py` | 4 | 0 | 4 |
| `mbdsdr_ai/radio_control.py` | 4 | 3 | 1 |
| `mbdsdr_ai/rtltcp_client.py` | 4 | 3 | 1 |
| `mbdsdr_ai/sandbox.py` | 4 | 3 | 1 |
| `mbdsdr_ai/sat_consistency.py` | 4 | 0 | 4 |
| `mbdsdr_ai/sdrpp_decoders.py` | 4 | 0 | 4 |
| `mbdsdr_ai/sdrsharp_adapter.py` | 4 | 0 | 4 |
| `mbdsdr_ai/sdrtrunk_adapter.py` | 4 | 0 | 4 |
| `mbdsdr_ai/sstv_decoder.py` | 4 | 1 | 3 |
| `mbdsdr_ai/urh_adapter.py` | 4 | 0 | 4 |
| `tests/test_ui_hard_defects.py` | 4 | 4 | 0 |
| `desktop/adsb_panel.py` | 3 | 1 | 2 |
| `desktop/ai_steps.py` | 3 | 0 | 3 |
| `desktop/layout_store.py` | 3 | 0 | 3 |
| `desktop/mcp_worker.py` | 3 | 1 | 2 |
| `desktop/themes.py` | 3 | 1 | 2 |
| `desktop/vfo_panel.py` | 3 | 0 | 3 |
| `experiments/exp_ota_run.py` | 3 | 0 | 3 |
| `experiments/exp_sstv_identification.py` | 3 | 0 | 3 |
| `mbdsdr_ai/ardop_adapter.py` | 3 | 0 | 3 |
| `mbdsdr_ai/ax25.py` | 3 | 0 | 3 |
| `mbdsdr_ai/bch_codecs.py` | 3 | 0 | 3 |
| `mbdsdr_ai/blind_param_est.py` | 3 | 0 | 3 |
| `mbdsdr_ai/core/mcp_server.py` | 3 | 1 | 2 |
| `mbdsdr_ai/cubicsdr_adapter.py` | 3 | 0 | 3 |
| `mbdsdr_ai/gps_bit_sync.py` | 3 | 0 | 3 |
| `mbdsdr_ai/gps_nav.py` | 3 | 0 | 3 |
| `mbdsdr_ai/gqrx_receiver.py` | 3 | 0 | 3 |
| `mbdsdr_ai/hdsdr_adapter.py` | 3 | 0 | 3 |
| `mbdsdr_ai/inspectrum_adapter.py` | 3 | 0 | 3 |
| `mbdsdr_ai/js8call_adapter.py` | 3 | 0 | 3 |
| `mbdsdr_ai/lrpt_vcdu.py` | 3 | 0 | 3 |
| `mbdsdr_ai/model_manager.py` | 3 | 0 | 3 |
| `mbdsdr_ai/module_graph.py` | 3 | 2 | 1 |
| `mbdsdr_ai/new_spacetime.py` | 3 | 2 | 1 |
| `mbdsdr_ai/new_spacetime_timeline.py` | 3 | 0 | 3 |
| `mbdsdr_ai/protocol_stack.py` | 3 | 0 | 3 |
| `mbdsdr_ai/self_evolution.py` | 3 | 1 | 2 |
| `mbdsdr_ai/sigdigger_adapter.py` | 3 | 0 | 3 |
| `mbdsdr_ai/skill_registry.py` | 3 | 1 | 2 |
| `mbdsdr_ai/skyengine/satellites.py` | 3 | 0 | 3 |
| `mbdsdr_ai/tc_uplink.py` | 3 | 0 | 3 |
| `mbdsdr_ai/tool_registry.py` | 3 | 0 | 3 |
| `mbdsdr_ai/windows_setup.py` | 3 | 3 | 0 |
| `mbdsdr_ai/xastir_adapter.py` | 3 | 0 | 3 |
| `mbdsdr_mcp_client.py` | 3 | 1 | 2 |
| `scripts/tool_availability_audit.py` | 3 | 1 | 2 |
| `tests/test_gui_real_hardware.py` | 3 | 2 | 1 |
| `tests/test_ui_layout_actions.py` | 3 | 3 | 0 |
| `tests/test_ui_layout_locks.py` | 3 | 3 | 0 |
| `desktop/anr_panel.py` | 2 | 0 | 2 |
| `desktop/modulation_panel.py` | 2 | 0 | 2 |
| `desktop/module_panel.py` | 2 | 0 | 2 |
| `desktop/settings.py` | 2 | 1 | 1 |
| `desktop/touch_manager.py` | 2 | 0 | 2 |
| `desktop/weather_panel.py` | 2 | 0 | 2 |
| `experiments/exp_llm_baseline.py` | 2 | 0 | 2 |
| `experiments/nanovna_rh770/scripts/vna_capture2.py` | 2 | 2 | 0 |
| `experiments/satellite_rx/scripts/mbdsdr_ctl.py` | 2 | 0 | 2 |
| `experiments/satellite_rx/scripts/pass_recorder.py` | 2 | 1 | 1 |
| `experiments/satellite_rx/scripts/passes_leo.py` | 2 | 2 | 0 |
| `experiments/satellite_rx/scripts/sat_radar.py` | 2 | 2 | 0 |
| `mbdsdr_ai/aprs_parser.py` | 2 | 1 | 1 |
| `mbdsdr_ai/astronomy.py` | 2 | 0 | 2 |
| `mbdsdr_ai/builtin_tle_meta.py` | 2 | 2 | 0 |
| `mbdsdr_ai/conversation.py` | 2 | 1 | 1 |
| `mbdsdr_ai/df_position.py` | 2 | 0 | 2 |
| `mbdsdr_ai/digital_modes.py` | 2 | 0 | 2 |
| `mbdsdr_ai/doppler_link.py` | 2 | 0 | 2 |
| `mbdsdr_ai/fhss_dehop.py` | 2 | 0 | 2 |
| `mbdsdr_ai/fm_scan.py` | 2 | 0 | 2 |
| `mbdsdr_ai/gnss_timestamps.py` | 2 | 0 | 2 |
| `mbdsdr_ai/gnuradio_blocks.py` | 2 | 0 | 2 |
| `mbdsdr_ai/gpredict_adapter.py` | 2 | 0 | 2 |
| `mbdsdr_ai/gps_ca.py` | 2 | 0 | 2 |
| `mbdsdr_ai/guardian.py` | 2 | 1 | 1 |
| `mbdsdr_ai/hackrf_params.py` | 2 | 1 | 1 |
| `mbdsdr_ai/interference_eval.py` | 2 | 0 | 2 |
| `mbdsdr_ai/interferometry.py` | 2 | 0 | 2 |
| `mbdsdr_ai/limesuite_params.py` | 2 | 2 | 0 |
| `mbdsdr_ai/llm_judge.py` | 2 | 0 | 2 |
| `mbdsdr_ai/mobile_server.py` | 2 | 0 | 2 |
| `mbdsdr_ai/multi_user.py` | 2 | 2 | 0 |
| `mbdsdr_ai/nanovna_dump.py` | 2 | 1 | 1 |
| `mbdsdr_ai/orbit_perturbation.py` | 2 | 0 | 2 |
| `mbdsdr_ai/osmosdr_source.py` | 2 | 0 | 2 |
| `mbdsdr_ai/playback.py` | 2 | 2 | 0 |
| `mbdsdr_ai/r2cloud_adapter.py` | 2 | 0 | 2 |
| `mbdsdr_ai/sat_links.py` | 2 | 0 | 2 |
| `mbdsdr_ai/sat_pipeline_runner.py` | 2 | 1 | 1 |
| `mbdsdr_ai/sat_profile.py` | 2 | 0 | 2 |
| `mbdsdr_ai/satellite/tracker.py` | 2 | 1 | 1 |
| `mbdsdr_ai/self_learning.py` | 2 | 2 | 0 |
| `mbdsdr_ai/tle_fetch.py` | 2 | 0 | 2 |
| `mbdsdr_ai/ui_actions.py` | 2 | 0 | 2 |
| `mbdsdr_ai/web/streamer.py` | 2 | 1 | 1 |
| `scripts/ai_tool_consistency.py` | 2 | 1 | 1 |
| `scripts/bench_dsp_throughput.py` | 2 | 2 | 0 |
| `scripts/decode_sensitivity.py` | 2 | 0 | 2 |
| `scripts/real_hw_acceptance.py` | 2 | 0 | 2 |
| `scripts/rtl_selfcheck.py` | 2 | 0 | 2 |
| `tests/test_interaction_wiring.py` | 2 | 2 | 0 |
| `tests/test_no_sim_regression.py` | 2 | 0 | 2 |
| `tests/test_sdr_backend_audio.py` | 2 | 1 | 1 |
| `tests/test_ui_interaction_guard.py` | 2 | 2 | 0 |
| `tests/test_ui_vfo_constellation.py` | 2 | 2 | 0 |
| `tests/tool_param_smoke.py` | 2 | 1 | 1 |
| `tests/tool_selftest.py` | 2 | 0 | 2 |
| `tools/onboarding/onboard.py` | 2 | 0 | 2 |
| `desktop/constants.py` | 1 | 0 | 1 |
| `desktop/mode_registry.py` | 1 | 0 | 1 |
| `desktop/tokens.py` | 1 | 0 | 1 |
| `docs/learn/phase14/pass_predict.py` | 1 | 0 | 1 |
| `experiments/exp_digital_modes.py` | 1 | 0 | 1 |
| `experiments/exp_fhss_detection.py` | 1 | 0 | 1 |
| `experiments/exp_weak_model_toolcall.py` | 1 | 0 | 1 |
| `experiments/nanovna_rh770/scripts/vna_capture3.py` | 1 | 1 | 0 |
| `experiments/satellite_rx/data/check_tles.py` | 1 | 0 | 1 |
| `experiments/satellite_rx/scripts/sat_catch.py` | 1 | 0 | 1 |
| `mbdsdr_ai/__init__.py` | 1 | 1 | 0 |
| `mbdsdr_ai/atmosphere.py` | 1 | 0 | 1 |
| `mbdsdr_ai/audio_resampler.py` | 1 | 0 | 1 |
| `mbdsdr_ai/background_decoder.py` | 1 | 0 | 1 |
| `mbdsdr_ai/bladerf_params.py` | 1 | 1 | 0 |
| `mbdsdr_ai/channelizer.py` | 1 | 0 | 1 |
| `mbdsdr_ai/cma_equalizer.py` | 1 | 0 | 1 |
| `mbdsdr_ai/df_amplitude.py` | 1 | 0 | 1 |
| `mbdsdr_ai/doppler_compensation.py` | 1 | 0 | 1 |
| `mbdsdr_ai/dsp.py` | 1 | 0 | 1 |
| `mbdsdr_ai/feature_manager.py` | 1 | 0 | 1 |
| `mbdsdr_ai/fec.py` | 1 | 0 | 1 |
| `mbdsdr_ai/gk2a_lrit.py` | 1 | 0 | 1 |
| `mbdsdr_ai/golden_regression.py` | 1 | 0 | 1 |
| `mbdsdr_ai/hooks.py` | 1 | 1 | 0 |
| `mbdsdr_ai/kismet_adapter.py` | 1 | 0 | 1 |
| `mbdsdr_ai/lrpt_image.py` | 1 | 0 | 1 |
| `mbdsdr_ai/lrpt_pipeline.py` | 1 | 0 | 1 |
| `mbdsdr_ai/mimo_v2.py` | 1 | 0 | 1 |
| `mbdsdr_ai/orbit_dynamics.py` | 1 | 0 | 1 |
| `mbdsdr_ai/plugin_system.py` | 1 | 1 | 0 |
| `mbdsdr_ai/position_service.py` | 1 | 1 | 0 |
| `mbdsdr_ai/remote_control.py` | 1 | 0 | 1 |
| `mbdsdr_ai/rtcm3_decoder.py` | 1 | 0 | 1 |
| `mbdsdr_ai/rtk_solver.py` | 1 | 0 | 1 |
| `mbdsdr_ai/sat_image_processing.py` | 1 | 0 | 1 |
| `mbdsdr_ai/satnogs_adapter.py` | 1 | 0 | 1 |
| `mbdsdr_ai/scheduler.py` | 1 | 0 | 1 |
| `mbdsdr_ai/sdrangel_plugins.py` | 1 | 0 | 1 |
| `mbdsdr_ai/sky_actions.py` | 1 | 0 | 1 |
| `mbdsdr_ai/spacetime_predict.py` | 1 | 0 | 1 |
| `mbdsdr_ai/tests/exp_phase58_multisat.py` | 1 | 0 | 1 |
| `mbdsdr_ai/tests/test_mobile_server.py` | 1 | 0 | 1 |
| `mbdsdr_ai/tests/test_optional_deps.py` | 1 | 0 | 1 |
| `mbdsdr_ai/tests/test_phase58_multisat.py` | 1 | 0 | 1 |
| `mbdsdr_ai/tests/test_sdr_rtltcp_registration.py` | 1 | 0 | 1 |
| `mbdsdr_ai/time_sync.py` | 1 | 0 | 1 |
| `mbdsdr_ai/tool_budget.py` | 1 | 0 | 1 |
| `mbdsdr_ai/verify_chain.py` | 1 | 0 | 1 |
| `mbdsdr_ai/vna_pipeline.py` | 1 | 0 | 1 |
| `mbdsdr_ai/web/server.py` | 1 | 1 | 0 |
| `mbdsdr_ai/workflow_engine.py` | 1 | 0 | 1 |
| `mbdsdr_ai/workflow_recorder.py` | 1 | 1 | 0 |
| `scripts/access_layer_fault_injection.py` | 1 | 0 | 1 |
| `scripts/serial_gnss_probe.py` | 1 | 1 | 0 |
| `tests/conftest.py` | 1 | 0 | 1 |
| `tests/test_adsb_dump1090_golden.py` | 1 | 0 | 1 |
| `tests/test_agc_speed.py` | 1 | 1 | 0 |
| `tests/test_app_mode.py` | 1 | 1 | 0 |
| `tests/test_bookmark_tags.py` | 1 | 1 | 0 |
| `tests/test_clean_exc.py` | 1 | 0 | 1 |
| `tests/test_cli.py` | 1 | 1 | 0 |
| `tests/test_desktop_ui_responsiveness.py` | 1 | 1 | 0 |
| `tests/test_device_switch_flow.py` | 1 | 1 | 0 |
| `tests/test_ft8_wsjtx_golden.py` | 1 | 0 | 1 |
| `tests/test_full_integration.py` | 1 | 0 | 1 |
| `tests/test_gimbal_follow_wiring.py` | 1 | 1 | 0 |
| `tests/test_gk2a_jp2.py` | 1 | 0 | 1 |
| `tests/test_headless_smoke.py` | 1 | 1 | 0 |
| `tests/test_identity_redline.py` | 1 | 0 | 1 |
| `tests/test_main_window.py` | 1 | 1 | 0 |
| `tests/test_mcp_server.py` | 1 | 1 | 0 |
| `tests/test_mode_switch_preset.py` | 1 | 1 | 0 |
| `tests/test_orphan_connection.py` | 1 | 1 | 0 |
| `tests/test_record_replay_config.py` | 1 | 1 | 0 |
| `tests/test_satellite_tracker.py` | 1 | 0 | 1 |
| `tests/test_serial_gnss.py` | 1 | 0 | 1 |
| `tests/test_sky_capability_negotiation.py` | 1 | 1 | 0 |
| `tests/test_sky_daylight_stars.py` | 1 | 0 | 1 |
| `tests/test_sky_track_wiring.py` | 1 | 1 | 0 |
| `tests/test_three_column_layout.py` | 1 | 1 | 0 |
| `tests/test_touch_adaptation.py` | 1 | 1 | 0 |
| `tests/test_ui_flow_guards.py` | 1 | 1 | 0 |
| `tests/test_user_layouts.py` | 1 | 1 | 0 |
| `tests/tool_health_check.py` | 1 | 0 | 1 |
