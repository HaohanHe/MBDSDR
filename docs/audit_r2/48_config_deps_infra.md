# R2 深度审查 #48 — config / optional_deps / version_store / file_tracker / watcher

**审查范围**：`mbdsdr_ai/config.py` (132 行)、`mbdsdr_ai/optional_deps.py` (76 行)、`mbdsdr_ai/version_store.py` (266 行)、`mbdsdr_ai/file_tracker.py` (335 行)、`mbdsdr_ai/watcher.py` (177 行)。
**审查方式**：只读，逐行阅读真实代码 + 跨文件调用关系 grep + 磁盘实态核查（`~/.mbdsdr/`）。
**结论速览**：5 个文件中真正"名副其实"的只有 `watcher.py`（且它根本不是文件监视器，而是 SDR 信号活动值守——与审查任务书假设不符）。`config.py` 基本可用但有 3 个真 bug；`optional_deps.py` 是**未被任何模块 import 的死代码**且检测清单与实际硬依赖完全脱节；`version_store.py` 能跑通内存快照，但被 `self_evolution` 用于保存**虚拟 txt 内容**，并未真正追踪代码文件；`file_tracker.py` 存在**重启后静默回滚失效**和**双重回滚自取消**两个真 bug。

---

## 0. 任务书假设纠偏（先说清楚）

| 任务书假设 | 代码实态 |
|---|---|
| `watcher.py` 是文件监视器（watchdog/轮询） | **错**。`watcher.py` 是 SDR 单频值守/触发录制（`watch_capture`），与文件系统无关。全仓库 grep `watchdog|Observer|inotify` 无任何文件事件监视实现。 |
| `optional_deps.py` 检测 numpy/scipy/pyrtlsdr/ephem | **错**。实际检测 scipy/skyfield/pyserial/PySide6/SoapySDR，**不含 numpy、pyrtlsdr、ephem**。而 numpy 在 ~20 个模块顶层 `import numpy as np`，是事实上的硬依赖。 |
| version_store 被自进化用于版本回滚代码 | **半对**。self_evolution 确实调用 VersionStore，但 snapshot 的是 `{target_type}_{target_name}.txt` 这类**人造文件名**的虚拟内容（self_evolution.py:304-305），不是真实 .py 文件。 |
| file_tracker 与 watcher 配合追踪文件变化 | **错**。两者无任何调用关系。file_tracker 是"被动记录"——等 LLM 主动调 `file_change_track` 工具，code_editor.py 写文件时**不会**自动回调 file_tracker。 |

---

## 1. config.py

### [真bug] config 保存非原子，崩溃即损坏
- **位置**：`mbdsdr_ai/config.py:126-132`
- **现象**：`save_config` 直接 `open(path, "w")` 覆盖写。写一半断电/异常 → `~/.mbdsdr/config.json` 截断为 0 字节或半截 JSON。下次 `load_config` 走 `except (json.JSONDecodeError, IOError)` 分支打印警告并**静默回退到默认配置**（config.py:121-123），用户的 api_key 看似"丢失"。
- **对比**：同项目 `self_evolution.py:354-363` 的 `apply()` 已经用了 `tempfile.mkstemp + os.replace` 原子写，config.py 却没用。
- **建议**：改为 `tempfile.mkstemp(dir=dirname)` + `os.replace(tmp, path)`。

### [真bug] api_key 明文落盘且未强制 0600
- **位置**：`mbdsdr_ai/config.py:130-131`；磁盘实态 `~/.mbdsdr/config.json` 当前权限 `600`（因本机 umask=077）。
- **现象**：代码里 `open(path, "w")` 后无 `os.chmod(path, 0o600)`。在默认 umask=022 的服务器/真机上，文件会以 `0644` 创建，**同机其他用户可读 API key**。当前 600 是 umask 巧合，不是代码保证。
- **建议**：保存后 `os.chmod(path, 0o600)`；或 `os.open(path, os.O_WRONLY|os.O_CREAT|os.O_TRUNC, 0o600)`。

### [真bug] 环境变量覆盖逻辑反了——"显式等于默认值"时不覆盖
- **位置**：`mbdsdr_ai/config.py:74-80`
- **代码**：
  ```python
  env_url = os.environ.get("MBDSDR_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
  if env_url and self.base_url == DEFAULT_CONFIG["base_url"]:
      self.base_url = env_url
  ```
- **现象**：env 只在"当前值恰好等于默认值"时才覆盖。若用户在 config.json 里**显式**写了 `"base_url": "https://api.siliconflow.cn/v1"`（与默认字符串相同），env 就不再生效。同样的问题在 `model`（line 79）。
- **根因**：`from_dict` 无法区分"用户没写这个字段"和"用户写的值恰好等于默认"。
- **建议**：env 应始终优先于文件值（12-factor 惯例）；或在 `from_dict` 里记录 `_explicit_fields`，env 只覆盖未显式设置的字段。
- **附带问题**：仅 api_key/base_url/model 三个字段有 env 覆盖，`max_context_tokens`/`timeout`/`temperature` 等完全无 env 通道。

### [建议] validate() 覆盖不全
- **位置**：`mbdsdr_ai/config.py:95-108`
- 校验了 api_key/context_tokens/compaction_threshold/temperature/tool_output_max_chars，但漏了：
  - `top_p` 应在 [0,1]（line 50 字段无校验）；
  - `timeout <= 0` 或过大；
  - `max_output_tokens <= 0`；
  - `compaction_target_ratio` 应在 (0, compaction_threshold) 之间，且 < 1.0（line 46 无校验）；
  - `base_url`  scheme 是否为 http/https。

### [建议] load_config 异常分支只 print 不抛
- **位置**：`mbdsdr_ai/config.py:121-123`
- JSON 损坏时静默回退默认配置，用户不知道配置文件已坏。建议至少 log.warning 或返回 (config, warnings)。

---

## 2. optional_deps.py

### [空壳/死代码] 全仓库无任何 import
- **位置**：`mbdsdr_ai/optional_deps.py:1-76`
- grep `check_optional_deps|optional_deps|print_deps_status` 排除自身后**0 命中**。`__init__.py:27-66` 的 import 清单里也没有它。
- 也就是说：这个"依赖检测与降级"模块既没被启动流程调用，也没被任何功能模块用来做降级分支判断。它只能 `python -m mbdsdr_ai.optional_deps` 手动跑一下打印状态。

### [真bug] 检测清单与实际硬依赖完全脱节
- **位置**：`mbdsdr_ai/optional_deps.py:16-49`
- 实际检测：scipy / skyfield / pyserial / PySide6 / SoapySDR。
- 实际硬依赖（grep 结果）：`numpy` 在 `adsb.py:21`、`adsb_lite.py:24`、`amr.py:31`、`analog_demod.py:8`、`ax25.py:18`、`baseband_io.py:11`、`cfo.py:25`、`constellation.py:8`、`decoders.py:20`、`demod.py:14`、`dsp.py:14`、`gnss_monitor.py:22`、`hal.py:26`、`meteor_sat.py:15`、`watcher.py:23` 等 ~20 处**顶层硬 import**。
- 后果：`optional_deps.py:71` 打印"核心功能不需要任何重依赖即可运行"——**这是误导**。没有 numpy，`import mbdsdr_ai` 直接 ImportError。检测脚本却把 numpy 列为"核心"不检查，把 scipy 列为"可选"。
- 任务书提到的 pyrtlsdr / ephem 也完全不在检测清单里。

### [建议] "已安装"检测用 `import` 而非 `importlib.util.find_spec`
- **位置**：`mbdsdr_ai/optional_deps.py:17-50`
- 直接 `import scipy` 会触发包副作用（编译、读数据文件），且同名模块在不同环境可能 shadow。建议 `importlib.util.find_spec("scipy") is not None`。

---

## 3. version_store.py

### [真bug] _save() 非原子，versions.json 损坏即全库历史丢失
- **位置**：`mbdsdr_ai/version_store.py:83-90`
- `with open(self.versions_file, "w")` 直接覆盖。崩溃在 `json.dump` 中途 → versions.json 截断。下次 `_load()`（line 73-81）走 `except Exception` 打印警告后 `self._versions = {}`，**所有版本元数据丢失**（objects/ 里的 blob 还在，但没人知道哪个 blob 属于哪个版本）。
- objects/ 下的 blob 倒是按 hash 命名（line 101-105），写前判断 exists，相对安全；但 versions.json 是单点。

### [真bug] 文档声明的 HEAD 文件从未写入
- **位置**：`mbdsdr_ai/version_store.py:55, 62`
- docstring（line 55）说目录结构含 `HEAD` 文件；`__init__` line 62 `self.head_file = os.path.join(self.store_path, "HEAD")` 定义了变量。
- grep 全文件：`head_file` **只在 line 62 出现一次**，从未读、从未写。磁盘实态 `~/.mbdsdr/evolution/` 下只有 `objects/` 和 `versions.json`，无 HEAD 文件。
- 当前 `_head` 只是 versions.json 里的一个字段，HEAD 文件是死代码/文档谎言。

### [真bug] 无垃圾回收 / 无保留策略
- **位置**：`mbdsdr_ai/version_store.py:98-105`
- `objects/` 目录只增不减。每次 `snapshot` 都把当前全部文件内容（self_evolution.py:301 `get_all_files()` 取全量再 snapshot）写入新 blob。长期自进化后 objects/ 无限膨胀。
- `versions.json` 同样把所有版本 dict 序列化一遍（line 87），无 limit、无 prune 接口。
- 对比：同项目 `guardian.py` 有 snapshot 机制（审查 #33），但 VersionStore 本身没接 GC。

### [建议] objects/ 平铺无分片
- **位置**：`mbdsdr_ai/version_store.py:101`
- `os.path.join(self.objects_path, h)` 把所有 blob 放在同一层目录。磁盘实态目前只有 5 个对象，但版本多了之后单目录上万文件会拖慢 `os.listdir`（`get_stats` line 265 每次都 listdir）。建议 `objects/h[:2]/h[2:]` 两层分片。

### [建议] version_id 用 md5 截断
- **位置**：`mbdsdr_ai/version_store.py:131`
- `hashlib.md5(message.encode()).hexdigest()[:8]`。不做安全用途，但 md5 已弃用，改 sha256 截断即可。

### [与 self_evolution 的关系] 名义上被用，实际快照的是虚拟内容
- **位置**：`mbdsdr_ai/self_evolution.py:111-116, 301-319`
- 初始快照：`files={"system_prompt.txt": "", "tool_descriptions.json": "{}"}` —— 空字符串和 `"{}"`，**不是真实文件**。
- commit 时：`filename = f"{proposal.target_type}_{proposal.target_name}.txt"`（self_evolution.py:304），把 `proposed_change` 写进这个人造 txt。也就是说 VersionStore 追踪的是 `code_adsb.py.txt`、`prompt_system_prompt.txt` 这类虚拟 blob，**不是磁盘上真实的 .py 文件**。
- 真正落盘的是 `self_evolution.apply()`（self_evolution.py:344-364）用 tempfile + os.replace 写真实文件，但备份只存在内存 dict `self._disk_backups`（line 108, 353）——**进程重启后备份丢失**，rollback 无法恢复真实文件。
- 结论：VersionStore 本身的快照/回滚/diff 逻辑能跑通，但它被接入自进化时追踪的是虚拟内容；真实文件的回滚依赖内存 dict，重启即失效。这是"实验室绿、真机红"的典型——单元测试在内存里跑通 apply→rollback，真机长跑重启后 rollback 静默失败。

---

## 4. file_tracker.py

### [真bug] 重启后 revert_to 静默失效（content 不落盘）
- **位置**：`mbdsdr_ai/file_tracker.py:58-72`（to_dict 不含 content）、`301-335`（_load_changes 不恢复 content）、`191-225`（revert_to 依赖 content_before）
- **证据链**：
  1. `to_dict()` line 71 注释明说"不保存完整内容（太大），只保存哈希和元数据"——`content_before`/`content_after` 不写盘。
  2. `_load_changes()` line 316-328 重建 FileChange 时**不传** content_before/content_after，两者保持 dataclass 默认值 `""`。
  3. `revert_to()` line 204 `target_content = change.content_before` → 重启后是 `""`。
  4. line 222 `if file_writer and target_content:` —— `""` 为 falsy，**file_writer 不被调用**。
  5. 但 line 210-218 仍然创建一条 REVERT 记录，line 219 仍更新 `reverted_to`，返回非 None。
- **后果**：进程重启后调用 `file_change_revert` 工具，工具报告"成功"并生成回滚记录，但**文件根本没被写回**。静默失败，且 history 里看到的 reverted=true 与磁盘状态不一致。

### [真bug] file_change_revert 工具双重调用 revert_to，自取消
- **位置**：`mbdsdr_ai/agent.py:1942`（file_tracker.py 本身的设计被 agent 层用错）
- **代码**：
  ```python
  handler=lambda args: ToolResult(success=True, content=json.dumps(
      ft.revert_to(args["change_id"], file_writer=...).to_dict()
      if ft.revert_to(args["change_id"], file_writer=...)
      else {"error": "变更不存在"},
      ...))
  ```
- **执行流**：
  1. 条件判断里先调一次 `ft.revert_to(X)` → 把文件写回 `content_before[X]`，并新建 REVERT 记录 R1。
  2. `.to_dict()` 分支里又调一次 `ft.revert_to(X)` → 这次 `self.changes.get(X)` 仍能拿到 X（X 没被删），再次把 X 的 content_before 写回，又新建 R2。
  - 等等，更糟的是：第二次调用时 `change = self.changes.get(change_id)` 仍是 X（不是 R1），所以又写一次 content_before[X]。实际上两次都写同一个内容，文件最终是对的。
  - 但副作用：产生了**两条重复 REVERT 记录**，且第二次调用里 `change.reverted = True`（line 207）重复设置，`reverted_to` 被覆盖成 R2 的 id，R1 的 id 丢失。
  - 更关键：若第一次 revert_to 因为 `target_content == ""`（重启后场景）没写文件但返回了 revert_change，第二次调用同样不写文件——问题不放大，但重复记录污染审计日志。
- **建议**：handler 应先 `r = ft.revert_to(...)` 一次，再根据 r 是否为 None 分支。当前写法是典型的 lambda 副作用堆叠 bug。

### [真bug] file_change_revert 的 file_writer 泄漏文件句柄且无路径校验
- **位置**：`mbdsdr_ai/agent.py:1942`
- `file_writer=lambda p,c: open(p,"w",encoding="utf-8").write(c)`
  - 无 `with`，文件句柄泄漏；
  - 无异常捕获，写失败直接抛 ToolResult 之外；
  - **无路径白名单/沙箱**：LLM 可通过 `file_path` 参数传 `/etc/passwd`、`~/.bashrc`、任意项目外路径，直接覆盖。这是 LLM 工具的路径穿越风险。
- 对比：`self_evolution.apply()` line 347 还做了 `os.path.abspath(os.path.expanduser(rp))` 和"目标文件不存在则拒绝创建"，file_change_revert 工具完全没有这层防护。

### [建议] 内存无限增长
- **位置**：`mbdsdr_ai/file_tracker.py:89-91, 131-134`
- `self.changes` 和 `self.file_changes` 是纯内存 dict，只增不删。`_load_changes()` 启动时把 `~/.mbdsdr/file_changes/` 下所有日期目录的所有 JSON 全读进内存（line 305-335）。长跑 Agent + 多轮自进化后，启动时间和内存线性膨胀。无 LRU、无按天数清理、无 max_changes 参数。

### [建议] code_editor.py 不接入 file_tracker
- grep 确认 `code_editor.py` 内**0 处**调用 `track_change/track_modify/track_create/track_delete`。它有自己的 `backup_dir=~/.mbdsdr/code_editor/backups`（code_editor.py:98）和 git 集成。
- 后果：FileChangeTracker 只在 LLM **主动**调 `file_change_track` 工具时才记录；CodeEditor 真正写代码文件时不经过 tracker，审计链断。这是"两个备份系统平行不通信"的典型架构债。

---

## 5. watcher.py

### [说明] 这不是文件监视器
- **位置**：`mbdsdr_ai/watcher.py:1-177`
- 全部内容是 SDR 信号活动检测：`band_power_db`、`activity_windows_from_power`、`watch_capture`（噪声底估计 → 门限触发 → pre-roll 缓冲 → hang 拖尾合并）。
- 唯一调用方：`mbdsdr_ai/sdr_tools.py:39 from .watcher import watch_capture`。
- 代码质量本身不错：纯函数 `activity_windows_from_power` 可离线测、pre-roll 用 `deque(maxlen=preroll_blocks)`（line 102）避免内存泄漏、状态机清晰。**这部分不是本次审查重点**，也没有发现真 bug。

### [真bug/设计缺口] 全仓库无文件系统事件监视
- grep `watchdog|Observer|FileSystemEventHandler|inotify|watchdog.observers` 全仓库 0 命中（`Observer` 命中都是 astronomy.py 的天文观测者类）。
- 也就是说：项目宣称"文件追踪/一键恢复/防幻觉变砖"，但**没有任何机制在文件被外部进程修改时自动感知**。file_tracker 是 pull-based（等业务代码主动上报），不是 push-based（事件驱动）。真机上若用户/另一个进程改了 .py 文件，系统完全不知道。

---

## 6. 安全专项

| 项 | 位置 | 结论 |
|---|---|---|
| 明文 API key 落盘 | config.py:130 | 明文，但未 chmod 0600（见 §1） |
| file_change_revert 路径穿越 | agent.py:1942 | **真风险**，无白名单，LLM 可覆写任意路径 |
| file_change_track 任意路径写入 | agent.py:1913 | track_change 本身只记录不写文件，风险低；但 revert_to 会写 |
| version_store blob 路径穿越 | version_store.py:101 | blob 文件名是 sha256 hex，不可注入；安全 |
| config.json 加载 | config.py:117 | json.load 不执行代码；安全 |
| file_tracker._load_changes | file_tracker.py:314 | json.load + 手动构造 dataclass，不执行代码；安全 |

---

## 7. 汇总表

| # | 文件:行 | 等级 | 问题 |
|---|---|---|---|
| 1 | config.py:126-132 | 真bug | save_config 非原子，崩溃损坏配置 |
| 2 | config.py:130-131 | 真bug | api_key 未 chmod 0600，靠 umask 巧合 |
| 3 | config.py:74-80 | 真bug | env 覆盖条件反了，"显式=默认值"时不生效 |
| 4 | config.py:95-108 | 建议 | validate 漏 top_p/timeout/target_ratio/base_url |
| 5 | optional_deps.py:全文 | 空壳/死代码 | 无任何模块 import |
| 6 | optional_deps.py:16-49 | 真bug | 不检测 numpy（实际硬依赖），误导性宣称"核心无重依赖" |
| 7 | version_store.py:83-90 | 真bug | _save 非原子，versions.json 损坏全库丢历史 |
| 8 | version_store.py:55,62 | 真bug | HEAD 文件声明了但从未写（死变量） |
| 9 | version_store.py:98-105 | 真bug | 无 GC/保留策略，objects/ 无限膨胀 |
| 10 | version_store.py:101 | 建议 | objects/ 平铺无分片 |
| 11 | self_evolution.py:108,353 | 真bug | 真实文件备份在内存 dict，重启丢失 |
| 12 | file_tracker.py:58-72,301-335,204 | 真bug | 重启后 revert_to 静默不写文件 |
| 13 | agent.py:1942 | 真bug | file_change_revert 双重调用 revert_to，重复记录 |
| 14 | agent.py:1942 | 真bug | file_writer 泄漏句柄 + 无路径白名单（路径穿越） |
| 15 | file_tracker.py:89,301 | 建议 | 内存无界增长，启动全量加载 |
| 16 | code_editor.py（全文） | 建议 | 不接 file_tracker，审计链断 |
| 17 | watcher.py（全文） | 说明 | 是 SDR 信号值守，不是文件监视；质量 OK |
| 18 | 全仓库 | 真bug/缺口 | 无 watchdog/inotify，无文件事件 push 感知 |

---

## 8. 修复优先级建议

**P0（数据丢失/安全）**：
- #11 self_evolution 磁盘备份持久化（重启后回滚失效是变砖风险）
- #12 file_tracker 重启后 revert 静默失效
- #14 file_change_revert 路径白名单 + with 语句
- #1 config 原子写

**P1（正确性）**：
- #13 revert_to 双重调用
- #7 versions.json 原子写
- #2 api_key 0600
- #3 env 覆盖逻辑

**P2（可维护性）**：
- #6 optional_deps 补 numpy/scipy/rtlsdr/ephem 检测或删除该死代码
- #9/#15 加 GC/保留策略
- #16 code_editor 接入 file_tracker
- #18 引入 watchdog 或明确文档说明"本系统不监听外部文件改动"
