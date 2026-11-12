# 静默读取点分级：配置 / 用户状态（2026-11-12）

> 只做清单与判断，不改代码。
> 已修好的三处（`settings.py`、`bookmark_manager.py`、`fm_scan.py`）不在表内，仅作为第 1 条的修法参照。
> 范围：生产代码里读用户配置 / 用户状态 JSON 的点；打包数据、协议报文、录音元数据读取单列在末尾，不入 27 条。
> 修复状态：A1 → `d48fc550`，A2 → `afa77023`，B4 → 本笔；表内其余各条仍为清单判断。

## 结论摘要

- 真正会丢数据的只有第一档：**读失败当空容器，下一次保存整份写回，且没有备份**。共 11 条。
- 第二档（会覆盖但有备份）4 条，问题主要是「备份了但用户看不见」，不是丢数据。
- 第三档（只读不写）12 条，坏文件不会抹掉内容，但会静默失真，其中两条（tool_policy、hook_commands）属于安全策略静默失效。
- 排在第一位的是 `desktop/layout_store.py`：数据层可直接套书签模板，只差一个 UI 出口。

## 第一档：会覆盖且无备份（唯一真丢数据的一档）

| # | 读取点 | ① 读失败分支 | ② 是否写回同一文件 | ③ 备份 | ④ 用户可见 | 最坏结局 |
|---|--------|--------------|--------------------|--------|------------|----------|
| A1 | `desktop/layout_store.py:114` `layouts.json` | 任何异常 → 返回空骨架 `{"presets": {}}`，只 `logger.warning` | 是。`save_preset/delete/rename/set_active` 都先 `_load()` 再 `_save()` 整份写（L173-241） | 无 | 无。`DockLayoutManager.last_save_error` 只报**写**失败，不报读损坏 | 用户自定义布局全没了，只剩内置布局，用户以为是自己删的或升级弄丢的 |
| A2 | `mbdsdr_ai/frequency_manager.py:211` `bookmarks.json` | `except` → 空列表 + `load_error` 文本 | 是。`_save_user` 整份写回用户书签（L263-269） | 无（绕过了 BookmarkManager 的备份） | **已核实**：全仓无任何 UI/生产代码读取 `load_error`，仅 `tests/test_dsp_backend_vfo_frequency.py`、`tests/test_frequency_manager_single_source.py` 断言；用户看不到（`load_error` 字段本身见 `frequency_manager.py:210/229`） | 频率管理里一保存，整套用户书签被写成只剩内置项 |
| A3 | `mbdsdr_ai/frequency_manager_v2.py:108` `bookmarks_v2.json` | `except` → 空集合 + warning | 是。`save()` 整份写回（L160-176） | 无 | 无 | v2 书签库整体清空，下次保存把空库固化 |
| A4 | `mbdsdr_ai/scheduler.py:113` 定时任务 | `except` → `print` 警告 + 空列表（GUI 无控制台＝静默） | 是。任一增删触发 `_save` 整份写 | 无 | 无 | 定时任务全消失，用户以为程序没跑或自己没建过 |
| A5 | `mbdsdr_ai/memory.py:68` 长期记忆 | `except` → `print` + 空 dict | 是。`add` 后 `_save` 整份写 | 无 | 无 | 长期记忆被清空，AI 突然「不记得」用户 |
| A6 | `mbdsdr_ai/version_store.py:72` `versions.json` | `except` → `print` + 空结构 | 是。`save_version` 整份写 | 无 | 无 | 版本历史全丢，回滚列表变空；旧版本目录还占着磁盘却找不回来 |
| A7 | `mbdsdr_ai/guardian.py:98` `snapshots.json` | `except` → `print` + 空列表 | 是。`create_snapshot` 追加后整份写 | 无 | 无 | 快照文件还在，但守护者列表空了，一键恢复无项可选 |
| A8 | `mbdsdr_ai/workflow_engine.py:176` `workflows/*.json` | 单个文件坏 → `print` 跳过，其余照读 | 是（仅当保存**同名**工作流时覆盖该文件） | 无 | 无 | 同名工作流一保存，坏文件里原来的步骤被新内容顶掉 |
| A9 | `desktop/main_window.py:10266` 录音分段 sidecar | `except` → 用目录里的分段重建最小 meta | 是。紧接着写回 sidecar | 无 | 无 | 录音原始 sidecar 元数据被「重建版」覆盖，事后无法追溯采样率/增益变更 |
| A10 | `desktop/main_window.py:14385` `_read_ntrip_from_disk` | `except` → 返回 `None` | 是。保存 GUI 配置时用空 NTRIP 段整份写回 | 只在**启动加载**时备份；运行期被改坏这条路径不备份 | 无 | 运行中 gui_config 被改坏后任何一次保存，NTRIP 主机/用户名/密码被清空且无备份 |
| A11 | `desktop/command_palette.py:64` 最近命令 | `except` → `[]` | 是。执行后整份写最近列表 | 无 | 无 | 最近使用记录清空（价值低，但同样是坏文件被写没） |

排序理由：A1 第一，因为它是唯一由**日常界面操作**（切布局/存布局）高频触发整文件重写的用户资产；A2/A3 第二，因为它们和已修好的书签共用同一个文件，属于「隔壁修了、这条绕过去」；A4-A7 是 AI 侧状态，触发频率低但一丢就是整份。

## 第二档：会覆盖但有备份

| # | 读取点 | ① 读失败分支 | ② 写回 | ③ 备份 | ④ 用户可见 | 最坏结局 |
|---|--------|--------------|--------|--------|------------|----------|
| B1 | `mbdsdr_ai/config.py:334` / `:391` `config.json` | 解析失败/顶层非对象 → 备份后回默认 + `last_error` | 是，合并写（只提交变化的键） | 有，`*.corrupt-*` | 有 `last_error()`，AI 面板/预连接会展示 | 最多重填一次设置，证据还在 |
| B2 | `desktop/settings_panel.py:610` 通用 JSON 读取 | 解析失败/顶层非对象 → 备份 + `on_error` 提示 | 是（设置面板保存） | 有，`*.corrupt-*` | 有，面板错误行 | 设置回默认，但用户能看到「已备份」并找回 |
| B3 | `desktop/main_window.py:14502` `gui_config.json` | 解析失败/顶层非对象 → `_backup_corrupt_gui_config()` + 状态栏 + 置损坏标记 | 是；备份失败时 `_save_gui_config` 直接跳过本次保存 | 有，`*.corrupt-*` | 有，状态栏与 `last_error` | 备份失败时本次设置不落盘（会提示），原文件不删 |
| B4 | `desktop/station_sync.py:62` `config.json` 镜像 | 任何异常 → `None`（**读侧仍静默**） | 是，走 `ConfigStore.update` 合并写；值冲突时先建 `.bak` | 有（ConfigStore 的 `*.corrupt-*` + `.bak`） | **已修（`desktop/station_sync.py`）**：备份失败不再 `except OSError: pass`，而是 `last_error()` 留人话（含源路径、备份目标、原因）+ 一行 warning，并中止本次 `ConfigStore.update`，不覆盖旧站址这唯一证据；写入成功清空 `last_error()`。`.bak` 成功时仍静默生成，读侧 `last_error` 尚未接界面 | 备份失败时这次站址改不动（会留痕），但用户仍不知道成功生成的 `.bak` 在哪 |

## 第三档：只读不写（不丢数据，但静默失真）

| # | 读取点 | ① 读失败分支 | ② 写回 | ③ 备份 | ④ 用户可见 | 最坏结局 |
|---|--------|--------------|--------|--------|------------|----------|
| C1 | `desktop/main_window.py:8285` 启动读 `config.json` | `except: pass` | 否（写由 ConfigStore 负责，有备份） | 不适用 | 无 | 用户明明配了 API key，AI 一直不可用且没有任何提示 |
| C2 | `desktop/main_window.py:12702` NTRIP 对话框回填 | `except: pass` | 否 | 不适用 | 无 | 对话框显示成「未配置」，用户重填一遍才发现原来有值 |
| C3 | `desktop/adsb_panel.py:96` 地面站坐标 | 异常 → `(None, None)` | 否 | 不适用 | 显示「未设置观测地点」——是误导，不是提示损坏 | 站址其实配了，界面说没配，用户重复填写 |
| C4 | `mbdsdr_ai/station.py:173` `station_from_config` | 异常 → 返回 `source="none"` | 否 | 不适用 | 无 | 跟踪/过境按「未设置站址」降级，用户看不出是读失败 |
| C5 | `desktop/settings_panel.py:1963` 导入预览 | 异常 → 错误字符串进对话框 | 否 | 不适用 | 有，导入结果框 | 最多是这次导入失败，用户看得到原因 |
| C6 | `mbdsdr_ai/recording_library.py:422` 录音库索引 | 解析失败 → `LibraryResult(False, "corrupt_library", …)`；写路径 `if doc is None: return` 拒绝写（L769-770） | 否（损坏时明确拒绝） | 无 | 有错误码与文本 | 录音库打不开，但录音文件都在，用户能看到「库文件损坏」而不是空库 |
| C7 | `mbdsdr_ai/preconnect.py:50` 预连接配置 | 异常 → 带原因的 issue 文本 | 否 | 不适用 | 有，预连接面板展示 issue | 预连接不会拿坏配置去连，最多这次不可用 |
| C8 | `mbdsdr_ai/tool_policy.py:134` 工具策略 | 解析失败 → stderr 警告 + 回默认策略 | 否 | 不适用 | 仅 stderr（GUI 不可见） | 用户写的 deny 规则静默失效，Agent 按默认策略放行/询问 |
| C9 | `mbdsdr_ai/hook_commands.py:205` 外部 hook | 解析失败 → stderr 警告 + 无 hook | 否 | 不适用 | 仅 stderr | 外部 hook 静默不执行，原本靠它做的拦截一起消失 |
| C10 | `mbdsdr_ai/file_tracker.py:326` 变更记录 | 单个文件坏 → 跳过 | 只写新文件，不覆盖坏的 | 不适用 | 无 | 历史变更记录缺一段，不影响新记录 |
| C11 | `mbdsdr_ai/workflow_recorder.py:443` 模板 | 单个文件坏 → 跳过 | 新模板用新 id，不覆盖 | 不适用 | 无 | 坏的那个模板读不出来，其余可用 |
| C12 | `mbdsdr_ai/self_learning.py:357` / `:383` 经验/模式 | 单个文件坏 → 跳过 | 新经验写新文件 | 不适用 | 无 | 少量经验丢失，不会扩散成整库清空 |

## 边界项（不计入 27 条）

- **读失败直接抛异常，不算静默**：`context_manager.py:663`、`gain_calibration.py:85`、`conversation.py:101`（load）。其中 `conversation.py:74` 是例外：`save()` 先读旧文件取 `created_at`，异常 `pass` 后用默认值整份写回——只丢创建时间/标题，可补进第一档末尾。
- **录音元数据只读**：`recording_meta.py:133`、`baseband_io.py:97/429`、`recorder.py`/`playback.py`、`satellite_image_panel.py:261`（SigMF meta）。坏了最多读不了这个录音，不会写回。
- **打包数据，不是用户状态**：`location/places.py:240`、`spectrum_widget.py:1142`（bandplan）、`local_agent_bridge.py:86`、`sat_links.py:41`、`sat_profile.py:35`、`i18n.py:59`。

## 第一名 `layout_store.py` 的修法同构评估

**数据层：可直接套书签模板**（`_backup_corrupt_bookmarks` → `_mark_corrupt` → `save()` 返回 bool 且 `_save_blocked` 时拒写）。需要的三件套完全一样：`os.replace` 改名成 `.corrupt-<stamp>`、`last_error` 人话、备份失败拒绝写。

三处差异，照抄时要改：

1. **不是一次性 load**。`LayoutStore` 每个操作都重新 `_load()`，所以「损坏且备份失败」的 blocked 状态必须挂在实例上（`self._save_blocked`），否则下一次 `save_preset` 又读到空骨架、又把空骨架写回去，等于没修。
2. **`_load()` 现在返回骨架，无法区分「文件不存在」和「文件坏了」**。要改成内部置 `self._corrupt_backup` / `self._last_error`，或返回 `(data, ok)`；`_save()` 先看 blocked 再写。
3. **没有现成 UI 出口**。布局侧没有状态行，最近的出口是 `DockLayoutManager.last_save_error`（现在只装写失败）或主窗口状态栏。建议复用这条通道，不新建界面；文案要写清「自定义布局已备份为 X，本次只显示内置布局」，因为 QSettings 里还留着 active 布局名，用户会以为「布局没坏、只是预设少了」——这两件事不一样。

工作量与测试口径可按书签那笔同构：初次损坏备份且字节一致、二次保存不吞备份也不重复造、备份失败拒绝写并保留可用状态。

## 附：A2 `load_error` 接线说明（只写方案，不动代码）

**现状**：`BookmarkManager` 已经把损坏处理成「备份 + 人话 `last_error`」，`FrequencyManager._reload_from_store()` 也把它转存到 `self.load_error`（`mbdsdr_ai/frequency_manager.py:210` 初始化、`:229` 赋值），但全仓只有两个单测读它，**用户永远看不到**。A2 的代码修复（单真值源）已在 `afa77023` 落地，缺的只是这条出口。

**落点**：`desktop/main_window.py:2098` 之后（`_build_control_panel` 里书签灌入 `try/except` 结束、`self._add_right_tab("控制", ...)` 之前），新增三行：

```python
        err = getattr(self._freq_mgr, "load_error", "")
        if err:
            logger.warning("书签文件读取失败：%s", err)
            self.statusBar().showMessage(f"书签文件读取失败：{err}", 15000)
```

**送什么**：直接送 `FrequencyManager.load_error` 原文。它已经包含路径、原因与备份去向（损坏时形如「书签文件损坏：原文件已备份为 `bookmarks.json.corrupt-…`」；只是跳过非法条目时形如「跳过 N 条缺少有效频率/字段非法的书签」），不需要在主窗口再拼一次，避免两处文案分叉。

**为什么选状态栏，而不是另外两条**：

- **状态栏**：`MainWindow` 已经在用（B3 的 `gui_config` 损坏就走这里），`statusBar()` 由 Qt 懒创建，不新建控件；这条路径正好在「书签已灌进面板之后」，用户看到空书签列表的同一时刻能拿到解释。
- **已有的错误提示组件**：书签面板没有错误行；设置面板里那套错误行属于 `settings_panel` 自己的 JSON，跨面板调用会把两个组件的生命周期绑在一起，不划算。
- **启动日志**：`main.py` 的 `corruption_startup_report` 在 `MainWindow` 构造之前跑，只能扫磁盘上的 `*.reason.txt`，看不到这个实例内存里的 `load_error`；GUI 没有控制台，单写日志等于没提示。

**归属**：这三行在 `main_window.py`，属于卫星线独占的那一笔（和 `10266` / `14385` 一起排），**等 Qt 锁放开再落**，本线不碰该文件。落的时候补一条断言：构造 `MainWindow` 前把临时 `bookmarks.json` 写坏，构造后 `load_error` 非空且 `statusBar().currentMessage()` 含「已备份为」。
