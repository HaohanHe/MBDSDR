# D1 收口：`device_owner.py` 搬迁计划（只出计划，未动代码）

> 日期：2026-11-13
> 基线：`08e61bd5`（`mbdsdr_ai/sdr_backend.py` 最近一笔）
> 形态：**计划稿**。本文件不改任何代码；执行时一切以当时工作区为准。
> 取证：`git status --porcelain mbdsdr_ai/sdr_backend.py` 为空——AGC/增益能力探测
> 的 31 行已随 `08e61bd5` 落库；但该文件的 claim 仍被 `G1采样率线` 持有
> （约 04:41 到期），见 §六。

## 一、搬迁范围（按符号名，不按行号）

把 `mbdsdr_ai/sdr_backend.py` 里的**设备占用登记（跨进程）子系统**整体搬到新叶子
模块 `mbdsdr_ai/device_owner.py`。范围按顶层符号名界定：

### 1.1 常量与进程身份

`OWNER_REGISTRY_ENV`、`_OWNER_REGISTRY_DEFAULT`、`_ACTIVE_OWNER`、`_OWNER_LOCK`、
`_SELF_PID`、`_SELF_START`、`_OWNED_HERE`、`_OWNER_LABELS`、`AI_OWNER`、
`_PROCESS_OWNER`

### 1.2 冲突文案与渠道

`process_owner`、`set_process_owner`、`AmbiguousOwners`、`OWNER_CHANNEL_MCP`、
`OWNER_CHANNEL_CLI`、`OWNER_CHANNEL_DESKTOP`、`_CHANNEL_ALTERNATIVE_HINT`、
`_CHANNEL_AMBIGUOUS_HINT`、`owner_conflict_hints`、`owner_conflict_channel`、
`owner_conflict_untrusted`、`owner_conflict_text`、`owner_registry_path`、
`owner_label`

### 1.3 进程判活（Win32）

`_ERROR_INVALID_PARAMETER`、`_PROCESS_QUERY_LIMITED_INFORMATION`、`_STILL_ACTIVE`、
`PROC_ALIVE`、`PROC_DEAD`、`PROC_UNKNOWN`、`_open_process_failure_means_dead`、
`_kernel32`、`_open_process_for_query`、`_proc_probe`、`_proc_start_time`、
`_self_start`、`_pid_alive`

### 1.4 登记文件读写与损坏留证

`_registry_lock`、`_backup_corrupt_registry`、`RegistryUntrustedError`、
`_read_registry_state`、`_read_registry`、`_write_registry`

### 1.5 三态读侧

`OWNER_STATE_FREE`、`OWNER_STATE_OWNED`、`OWNER_STATE_UNKNOWN`、
`OWNER_STATE_AMBIGUOUS`、`OwnerState`、`owner_state_text`、`get_owner_state`

### 1.6 占用判定 / 释放 / 退出兜底

`release_stale_owners`、`_drop_stale`、`_log_stale_removed`、`_match_owner_record`、
`get_active_owner`、`claim_active_owner`、`release_active_owner`、
`acquire_hardware_owner`、`release_hardware_owner`、`_release_owned_here_at_exit`
（连同模块级的 `atexit.register(_release_owned_here_at_exit)`）

**不搬**：

- `logger`（全文件共用，搬走会让新模块反向 import `sdr_backend` 而成环）；
- `_WARNED_ONCE` / `_warn_once`（与占用登记无关；文件内除自身定义外无调用点，
  归另一条线处置）。

搬走后 `atexit` / `contextlib` 在 `sdr_backend.py` 不再被使用，删除这两个 import；
`threading` / `json` / `time` / `collections` / `dataclasses` 文件其余部分仍在用，
保留。

## 二、新模块 `mbdsdr_ai/device_owner.py` 的依赖

- 标准库：`os`、`json`、`time`、`atexit`、`contextlib`、`threading`、`typing`、
  `dataclasses`；
- 函数内延迟：`from .corrupt_evidence import write_reason`（唯一一条，保持延迟；
  `corrupt_evidence` 是纯叶子，不构成环）；
- `logger = logging.getLogger(__name__)` 自建：日志正文逐字不变；logger 名从
  `mbdsdr_ai.sdr_backend` 变为 `mbdsdr_ai.device_owner`（已全仓扫描，无按 logger
  名过滤的逻辑）；
- **不得** import `sdr_backend`。

`sdr_backend.py` 侧只做两件事：删除上述定义；顶部**只再导出公共名**
（`OWNER_REGISTRY_ENV`、`AI_OWNER`、`OWNER_CHANNEL_*`、`OWNER_STATE_*`、
`PROC_*`、`process_owner`、`set_process_owner`、`owner_*`、`get_owner_state`、
`get_active_owner`、`claim_active_owner`、`release_active_owner`、
`release_stale_owners`、`acquire_hardware_owner`、`release_hardware_owner`、
`AmbiguousOwners`、`OwnerState`、`RegistryUntrustedError`）。
**私有名一律不在 `sdr_backend` 再导出**——否则第一形态私有 import 存量会凭空 +N。

## 三、12 个 patch 目标（逐条）

用户口径是 10 个；2026-11-13 04:15 用第二形态扫描器实测为 **12 条 / 4 个文件**
（口径：AST 调用点，逐条计）。多出的 2 条是同属该子系统的
`_open_process_for_query`，不是扫描口径差异造成的误报。

| # | 位置 | 当前写法 | 迁移后 |
|---|---|---|---|
| 1 | `tests/test_device_owner_lock.py:173` | `monkeypatch.setattr(sb, "_open_process_for_query", …)` | `own._open_process_for_query` |
| 2 | `tests/test_device_owner_lock.py:187` | 同上 | 同上 |
| 3 | `tests/test_device_owner_lock.py:525` | `monkeypatch.setattr(sb, "_backup_corrupt_registry", …)` | `own._backup_corrupt_registry` |
| 4 | `tests/test_device_owner_lock.py:539` | 同上 | 同上 |
| 5 | `tests/test_device_owner_lock.py:569` | 同上 | 同上 |
| 6 | `tests/test_device_owner_lock.py:584` | 同上 | 同上 |
| 7 | `tests/test_device_owner_lock.py:598` | 同上 | 同上 |
| 8 | `tests/test_device_owner_lock.py:623` | 同上 | 同上 |
| 9 | `tests/test_device_owner_lock.py:632` | 同上 | 同上 |
| 10 | `tests/test_cli_open_owner_gate.py:145` | `monkeypatch.setattr(sb, "_backup_corrupt_registry", …)` | `own._backup_corrupt_registry` |
| 11 | `tests/test_device_panel_unplug_identity.py:244` | 同上 | 同上 |
| 12 | `tests/test_owner_conflict_before_open.py:157` | 同上 | 同上 |

统一规则：

- 迁移后首参一律指向 `mbdsdr_ai/device_owner.py`（测试里用
  `import mbdsdr_ai.device_owner as own`）；
- `sb`（`import mbdsdr_ai.sdr_backend as sb`）仍保留，但只用于后端类/工厂：
  `RTLSDRBackend`、`SDRBackend`、`SDRDevice`、`build_backend_for_device`、
  `HackRFBackend`、`USRPBackend`、`SoapySDRBackend`、`PlutoSDRBackend`；
- 同文件内**其他占用登记符号**（公共名也算）一并改用 `own`，别留一半 `sb` 一半
  `own`——否则公共再导出永远删不掉。

## 四、非 patch 的私有属性访问（必须同笔改指）

第二形态扫描器只统计 `setattr` / `patch.object`；下列**模块属性直读**在定义搬走后
会直接 `AttributeError`，必须与搬迁同一笔改指 `own`（口径：出现次数）：

| 文件 | 次数 | 访问的私有名 |
|---|---|---|
| `tests/test_device_owner_lock.py` | 24 | `_OWNED_HERE`、`_proc_start_time`、`_self_start`、`_release_owned_here_at_exit`、`_open_process_failure_means_dead`、`_proc_probe` |
| `tests/test_owner_conflict_before_open.py` | 15 | `_OWNED_HERE`、`_proc_start_time` |
| `tests/test_device_panel_unplug_identity.py` | 6 | `_OWNED_HERE` |
| `tests/test_device_identity_unified.py` | 3 | `_OWNED_HERE`、`_proc_start_time` |
| `tests/test_cli_open_owner_gate.py` | 0（只有 §三 的 patch） | — |

## 五、生产调用点（4 个文件）

| 文件 | 位置 | 现状 | 迁移后 |
|---|---|---|---|
| `mbdsdr_ai/bladerf_params.py` | 492、517 | `from .sdr_backend import acquire/release_hardware_owner` | `from .device_owner import …` |
| `mbdsdr_ai/limesuite_params.py` | 526、551 | 同上 | 同上 |
| `mbdsdr_ai/device_manager.py` | 66 | `from .sdr_backend import release_hardware_owner` | `from .device_owner import release_hardware_owner` |
| `mbdsdr_ai/device_manager.py` | 654 | 混合 import：`IDENTITY_STABLE, acquire_hardware_owner, device_identity_aliases, device_identity_key, rtlsdr_native_entry` | 拆三行：身份名 → `.device_identity`，`acquire_hardware_owner` → `.device_owner`，`rtlsdr_native_entry` → `.sdr_backend` |
| `desktop/device_panel.py` | 32–38 | 与 `enumerate_all_sdr_devices` 同一个 try import | 拆两行：4 个占用名 → `mbdsdr_ai.device_owner`，`enumerate_all_sdr_devices` 留 `sdr_backend`；`except` 分支的兜底赋值不变 |

另外两处只用公共名、可改可不改（建议顺手改指，减少再导出依赖）：
`tests/test_cli_owner_conflict_text.py`、`tests/test_diagnostics_e2e_five_corruptions.py`。

## 六、前置条件（硬门，缺一不动手）

1. **`mbdsdr_ai/sdr_backend.py` 处于静默状态**：
   - `git status --porcelain mbdsdr_ai/sdr_backend.py` 为空；
   - 最近改动已落库（当前为 `08e61bd5`）；
   - 文件 claim 已释放、或已到期且原 PID 确认退出（当前 `G1采样率线` 持有，
     约 04:41 到期）；
   - 动手前再观察一个「无变化」窗口：至少一次完整测试跑期间该文件 diff 保持为空。
2. **claim**：执行时逐文件 claim；被占即跳过并点名。`mbdsdr_ai/device_manager.py`
   的 claim 已过期（原 PID `1860`），夺取前必须先确认该 PID 已退出。
3. `mbdsdr_ai/device_owner.py` 为新建文件，claim 路径本身。

## 七、提交策略

- **一笔原子提交（豁免「一文件一笔」）**：`mbdsdr_ai/device_owner.py`（新增）+
  `mbdsdr_ai/sdr_backend.py` + 5 个测试文件（§三 的 4 个 + §四 的
  `test_device_identity_unified.py`）。理由：私有名定义只在 `device_owner`，
  `sdr_backend` 又不能再导出私有名，拆开提交必然出现「模块已搬、测试仍打旧名」的
  中间红态。同 `baba826a` 先例。
- 之后**每文件一笔**：§五 的 4 个生产调用点（`device_manager.py` 一笔含两处），
  外加 §五 末尾两个可选测试。
- 公共再导出在 `sdr_backend` 保留一轮；等全仓 grep 确认零引用后，再单独一笔删除。

## 八、验收（三条）

### 8.1 SCC 不退化

口径：模块数。当前全图（模块级 + 函数体内 import）为 **`[18, 5]`**
（2026-11-13 04:15 复测）。执行后仍然 `[18, 5]` 或更小；新增的 `device_owner`
不得进入任何 SCC。判据：`python` 跑 AST import 图 + Tarjan。

### 8.2 私有名存量不增加

- 第一形态（口径：跨模块 import 语句条数）：**122 → 122**。
  特别检查 `sdr_backend.py` 顶部不得出现 `from .device_owner import _xxx`。
- 第二形态（口径：AST 调用点，逐条计）：**135 → 135**；涉及文件数 67 → 67。
  12 条只改首参模块，条数不变；若低于 135，说明顺手消掉了别的，要单列说明。

### 8.3 四份测试全绿且 patch 目标为新模块

```
python -m pytest tests/test_device_owner_lock.py tests/test_cli_open_owner_gate.py \
                 tests/test_device_panel_unplug_identity.py \
                 tests/test_owner_conflict_before_open.py -q
```

全绿；且静态检查：

- 这 4 个文件里 `setattr(sb, "_` 命中数为 **0**；
- `setattr(own, "_open_process_for_query"` 2 条、
  `setattr(own, "_backup_corrupt_registry"` 10 条；
- `grep -n "\bsb\._"` 在这 4 个文件 + `test_device_identity_unified.py` 命中为 0。

补充回归证据（不算第四条验收）：`owner_registry_path()` 仍读同一个
`MBDSDR_OWNER_REGISTRY`；`atexit` 兜底仍注册；冲突文案逐字不变
（`tests/test_cli_owner_conflict_text.py` 应保持通过）。

## 九、回滚与风险

| 风险 | 处置 |
|---|---|
| 其他线并发改 `sdr_backend.py`，与 900 行删除冲突 | §六 静默门 + claim；冲突即放弃本次、不动对方 hunk |
| 单例身份被拆成两份（`_OWNER_LOCK`、`_ACTIVE_OWNER`、`_OWNED_HERE`、`_SELF_*`） | 私有名只在新模块定义一次；不双向再导出；改完 grep 确认无 `sb._OWNER_*` 残留 |
| `sdr_backend` 内部仍有调用点（约 1134 / 1148 行等） | 通过顶部公共再导出继续解析，不改调用逻辑 |
| `atexit` 注册时机变化 | 注册语句随函数搬到 `device_owner` 模块级；任何 import 链（`sdr_backend → device_owner`）都会触发，行为等价 |
| logger 名变化影响日志过滤 | 已扫全仓无按名过滤；日志正文不变 |
| 回滚 | 原子提交整体 `git revert` 即回到 `sb.` 形态；生产调用点各自一笔，可单独 revert |

## 十、本文件不做的事

- 不改任何代码、不跑搬迁测试；
- 不给 `device_owner` 增设公共测试接缝。若下一轮要把第二形态再降 10–12 条，可仿
  orbit 缓存簇给 `_backup_corrupt_registry` / `_open_process_for_query` 做官方入口
  （如环境变量或可注入钩子），但那要单独立项，不在本次搬迁里夹带；
- 不动 `logger` / `_WARNED_ONCE` / `_warn_once`；
- 不动 `sat_track_panel.py` 与 `tools/axioms/*` 的待办。
