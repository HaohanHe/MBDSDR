# 诊断页需要什么（2026-11-12，交路线图工单）

> 来由：`corrupt_evidence` 曾提示「见帮助 › 诊断」，而本仓帮助菜单只有
> 三步上手 / 能力目录 / 快捷键一览 / 命令面板——**没有任何面向用户的诊断出口**。
> 该误导文案已删（`4655458f`），缺口本身已按路线图要求立单。
> 本文只列**页面要能回答的三个问题**及每项现在的数据来源，不做界面设计。

## 一、损坏备份清单与理由

用户要能回答：**哪些文件坏过、什么时候坏的、为什么坏、原件还在不在。**

现在可从 `mbdsdr_ai/corrupt_evidence.py` 取到：

| 需要的信息 | 现有来源 | 说明 |
|---|---|---|
| 备份清单 | `scan_corruption_evidence(directories)` → `recent` / `stale` | 只读；只做**一级** `listdir` |
| 扫哪些目录 | `default_scan_directories(settings_path)` | `~/.mbdsdr` + `evolution` / `guardian_snapshots` / `workflows` + 设置目录 |
| 为什么坏 | 每条备份旁的 `<备份>.reason.txt`（`REASON_SUFFIX`，`write_reason` 写入） | 页面直接读该文件即可，一行文本 |
| 超期可清理 | `scan_corruption_evidence(...)["stale"]` | 只清点，**绝不自动删** |
| 读不了的目录 | `["unreadable"]`（dir + error） | 必须展示，否则会被误读成「没有损坏」 |

页面落地前必须先补齐的覆盖缺口（现在清点会漏）：

- `desktop/settings_panel.py::_report_bad_config`（AI 配置）调
  `backup_corrupt_config` 后**不写理由** → 用户当时在状态栏能看到，但事后进不了清单；
  对照样板：`desktop/settings.py::DesktopSettings.load` 用同一个备份函数，但补了
  `write_reason`（第 486 行），只差这一行；
- `desktop/main_window.py::_backup_corrupt_gui_config` 同样不写理由文件（卫星线锁）；
- `desktop/station_sync.py` 的 `.bak` 是站址冲突快照，**不是**损坏备份，别混进这一栏；
- 清点只覆盖一级目录，子目录新增时须同步 `default_scan_directories`。

## 二、日志文件路径

用户要能回答：**出了事去哪儿看日志、这次有没有持久日志。**

现在可从 `desktop/startup_paths.py` 取到：

| 需要的信息 | 现有来源 | 说明 |
|---|---|---|
| 本次实际落点 | `startup_log_path()` | 返回第一个**可创建**的路径；全部不可写返回 `""` |
| 候选顺序 | `log_candidates()` | `MBDSDR_STARTUP_LOG` → 非 C 盘 → 程序目录 `logs` → `%PROGRAMDATA%\MBDSDR\logs` |
| 本次写了哪 | `append_startup_log(text)` 的返回值 | `""` = 只进了内存 |
| 无持久落点时的副本 | `buffered_log()` / `persistent_log_note()` | 仅本进程有效，退出即失 |

注意：诊断页**不要**在纯展示时调 `startup_log_path()`——它会 `makedirs`，
展示不该有副作用；应展示「上次启动实际写入路径」（需要启动时把该值记下来）。
现在没有任何菜单/按钮能打开日志目录，这是缺口的一部分。

## 三、最近一次持久化错误

用户要能回答：**刚才那次失败，具体卡在哪一步、还能不能写盘。**

现在散落在各模块，**没有一处能看全**：

| 模块 | 取法 | 覆盖对象 |
|---|---|---|
| `mbdsdr_ai/config.py` | `last_error()` | agent 配置 `~/.mbdsdr/config.json` |
| `desktop/station_sync.py` | `last_error()` | 站址镜像写入 `config.json` |
| `desktop/command_palette.py` | `recent_error()` | 最近命令列表 |
| `desktop/settings.py` | `DesktopSettings.last_error` | 桌面设置 |
| `desktop/layout_store.py` | `LayoutStore.last_error` / `.corrupt_backup` | 布局 |
| `mbdsdr_ai/bookmark_manager.py` | `BookmarkManager.last_error` / `.corrupt_backup` | 书签 |
| `mbdsdr_ai/frequency_manager.py` | `FrequencyManager.load_error` | FM 预设 |
| `mbdsdr_ai/memory.py` | `MemoryStore.last_error` / `get_status_text()` | 记忆 |
| `mbdsdr_ai/scheduler.py` | `Scheduler.last_error` / `get_status_text()` | 定时任务 |
| `mbdsdr_ai/guardian.py` | `Guardian.last_error` / `get_status_text()` | 守护快照 |
| `mbdsdr_ai/workflow_engine.py` | `WorkflowEngine.last_error` / `get_status_text()` | 工作流（含 `_blocked_names`） |
| `mbdsdr_ai/version_store.py` | `VersionStore.last_error` | 版本索引 |

两类口径要区分，页面上别混：

1. **文本口径**：memory / scheduler / guardian / workflow_engine 的
   `get_status_text()` 已含「持久化错误: …」一行，直接取文本即可；
2. **属性口径**：其余对象只有 `last_error`，页面按属性读。

页面最小验收（满足即可上线，不追求好看）：

- [ ] 打开即列出 `scan_corruption_evidence(default_scan_directories(cfg))` 的
      `recent` 条数、每条理由首行、`stale` 条数、`unreadable` 明细；
- [ ] 显示上次启动日志的**实际路径**；若为内存缓冲，显示
      `persistent_log_note()` 原文；
- [ ] 显示上表各对象的 `last_error`（为空的不显示，避免满屏「无」）；
- [ ] 一切只读：不删备份、不写配置、不创建目录。

非目标：不做日志查看器、不做自动修复、不在诊断页里改配置。

## 口径裁定（2026-11-12，数据层落地前）

**1. 「只认 `*.reason.txt`」是设计口径，不是漏。**

- 理由文件是唯一记录「为什么坏」的东西；裸 `*.corrupt-*` 说不出原因，
  可能是旧版本遗留、也可能是用户手抄的副本，无法证明是本次损坏；
- `station_sync` 的 `.bak` 是**冲突快照**，语义与「损坏备份」不同，并进来
  会让同一次故障在两个栏目各出现一次；
- 因此读取端**不放宽**。writer 缺口（`settings_panel._report_bad_config`、
  `main_window._backup_corrupt_gui_config` 尚无理由）单独修，修完自然进清单。
- 已知代价：加理由文件之前产生的裸备份**永远不显示**；这是有意的，
  不为了「看起来全」把无证据的文件算成损坏。

**2. 「只扫一级目录」实测不漏，但要守住不变量。**

2026-11-12 实查 `~/.mbdsdr/`：

| 目录 | 实际结构 | 备份位置 | 结论 |
|---|---|---|---|
| `evolution/` | `objects/`（内容寻址目录）+ `versions.json` | `versions.json.corrupt-*` 在**一级** | 不漏 |
| `guardian_snapshots/` | 空（快照与备份都在一级） | 一级 | 不漏 |
| `workflows/` | `recordings/`（音频）+ `*.json` | 备份在**一级** | 不漏 |

不变量：**备份必须与源文件同目录，且源目录要登记进
`default_scan_directories`**。以后若有 store 改成「按 id 建子目录放源文件」，
必须同步登记，否则一级扫描会漏——这是约束，不是当前缺陷。

## 数据层（已落地，`mbdsdr_ai/diagnostics.py`）

`collect_diagnostics()`：无参、只读，返回三块：

- `backups`：`directories` / `total` / `stale` / `items`（每条含 `backup`、
  `reason_file`、`reason` 全文）/ `unreadable`；
- `log`：`path`（来自 `startup_paths.last_log_path()`，**不调**会 `makedirs`
  的 `startup_log_path()`）/ `candidates` / `buffered`（末 20 行）/ `notice`；
- `errors`：`items`（模块级 `last_error()` + 实例弱引用注册表，空错误不列）/
  `unwired`（DesktopSettings、LayoutStore、BookmarkManager、
  FrequencyManager、VersionStore——需要窗口实例，等 W106 面板接线）。

实例注册：`Agent.__init__` 把 memory / guardian / workflow_engine / scheduler
弱引用登记，不改变生命周期；同一进程取最近存活实例。
