# 测试配置隔离系统修复：真实配置污染清单（REALCFG）

> 日期：2026-11-11
> 范围：`tests/` 下所有构造 `MainWindow()` 的 fixture + 用例体内直接构造
> 结论口径：全部数据来自实测（pytest 输出 / 审计钩子 / reg export），静态推断单独标注

## 0. 一句话结论

`MainWindow()` 构造会 `DesktopSettings.load()` 真实 `%APPDATA%/mbdsdr/desktop_settings.json`，
随后一次 `set('touch_mode', 同值)` 把配置标脏，500ms 防抖把**整份内容重写一遍**——
`size +0`、`sha256` 完全相同、只有 `mtime_ns` 变。这不是内容污染，但是越界写用户文件；
真正的风险在于它只差一个「值不同」就会变成真污染。修复分三层：fixture 显式请求
`isolated_profile`（26 个）、会话级 autouse 兜底（防以后再漏）、产品侧同内容不落盘（治根）。

## 1. 清单：构造 MainWindow 但未请求 isolated_profile 的 fixture

扫描方法：AST 遍历 `tests/*.py`，收集所有 `@pytest.fixture` 函数体（含经同文件 helper
间接）调用 `MainWindow(...)` 的，再沿 fixture 参数依赖链递归判断是否请求
`isolated_profile`。修前：**27 个 MISSING + 1 个 PARTIAL**。

### 1.1 已修（26 个，fixture 补 `isolated_profile`）

| # | 文件 | fixture |
|---|---|---|
| 1 | test_bookmark_builtin_label.py | window |
| 2 | test_close_recording_guard.py | window |
| 3 | test_desktop_ui_responsiveness.py | main_window |
| 4 | test_discoverability_help.py | main_window |
| 5 | test_gimbal_control_robustness.py | mw |
| 6 | test_gimbal_follow_wiring.py | mw |
| 7 | test_gimbal_rotctld_sim.py | mw |
| 8 | test_gimbal_track_chain.py | mw |
| 9 | test_interaction_wiring.py | main_window |
| 10 | test_listen_state_machine.py | win |
| 11 | test_main_window.py | main_window（原有 isolated_persisted_state 只盖 QSettings 三键） |
| 12 | test_no_device_onboarding.py | window |
| 13 | test_orphan_connection.py | mw |
| 14 | test_ptt_capability_rows.py | window |
| 15 | test_record_replay_config.py | window |
| 16 | test_replay_squelch_margin.py | window |
| 17 | test_shortcut_focus_priority.py | main_window |
| 18 | test_sky_action_visibility.py | window |
| 19 | test_sky_capability_negotiation.py | mw |
| 20 | test_sky_track_wiring.py | mw |
| 21 | test_sky_tune_honesty.py | window |
| 22 | test_three_column_layout.py | mw |
| 23 | test_touch_adaptation.py | main_window |
| 24 | test_ui_layout_actions.py | mw |
| 25 | test_ui_layout_locks.py | main_window |
| 26 | test_user_layouts.py | main_window |

### 1.2 未修但扫到（有等价隔离，实测 clean）

| 文件 | fixture | 原因 |
|---|---|---|
| test_ui_interaction_guard.py | main_window | 自带 `isolated_home`（重定向 HOME/USERPROFILE/APPDATA/LOCALAPPDATA + 清 QSettings 键），5 passed |
| test_command_palette.py | main_window | 扫描期间已被工单57-63线加上 `isolated_home`，19 passed |
| test_disconnect_state.py | window | 扫描后被设备线改为 `w.settings` 指向 tmp，2 passed |

### 1.3 残留缺口：用例体内直接构造（12 处，无 fixture 可注入）

`test_ai_kernel_deferred`、`test_error_remedy_links`、`test_floating_layout_memory`×3、
`test_gui_real_hardware`、`test_no_sim_regression`、`test_replay_squelch_margin`、
`test_settings_schema`、`test_shortcuts_guard`、`test_ui_layout_locks`×2。

这些不再逐个改 fixture，改用 §6 的会话级 autouse 兜底（不靠人记）。

## 2. 修前修后同命令对照

命令：`QT_QPA_PLATFORM=offscreen python -m pytest tests/<file> -q --no-header`，逐文件串行，
不并发。指标：GUARD-HIT 的文件数。

| 文件 | 修前 | 修后 |
|---|---|---|
| bookmark_builtin_label | 1 failed, 2 passed, **1 error** | 1 failed, 2 passed |
| close_recording_guard | 3 passed, **1 error** | 3 passed |
| desktop_ui_responsiveness | 21 passed, **1 error** | 21 passed |
| discoverability_help | 17 passed, **1 error** | 18 passed |
| gimbal_control_robustness | 12 passed, **1 error** | 12 passed |
| gimbal_follow_wiring | 12 passed, **1 error** | 12 passed |
| gimbal_rotctld_sim | 6 passed | 6 passed |
| gimbal_track_chain | 23 passed | 23 passed |
| interaction_wiring | 5 passed, **1 error** | 5 passed |
| listen_state_machine | 13 passed, **1 error** | 13 passed |
| main_window | 21 passed | 21 passed |
| no_device_onboarding | 3 passed, **1 error** | 3 passed |
| orphan_connection | 24 passed, **1 error** | 24 passed |
| ptt_capability_rows | 15 passed, **1 error** | 15 passed |
| record_replay_config | 6 passed, **1 error** | 6 passed |
| replay_squelch_margin | 3 passed, **1 error** | 3 passed |
| shortcut_focus_priority | 4 passed, **1 error** | 4 passed |
| sky_action_visibility | 2 passed, **1 error** | 2 passed |
| sky_capability_negotiation | 37 passed, **1 error** | 37 passed |
| sky_track_wiring | 14 passed, **1 error** | 14 passed |
| sky_tune_honesty | 3 passed, **1 error** | 3 passed |
| three_column_layout | 15 passed, **1 error** | 15 passed |
| touch_adaptation | 16 passed, **1 error** | 16 passed |
| ui_layout_actions | 16 passed, **1 error** | 16 passed |
| ui_layout_locks | 70 passed, 4 skipped | 70 passed, 4 skipped |
| user_layouts | 锁等待超时未跑成 | 10 passed |

**N → M：22 → 0。**

单会话合并跑（26 文件一条命令）：`374 passed, 4 skipped, 1 failed, 0 error in 309s`。
唯一 failed 是 `test_bookmark_builtin_label::test_builtin_bookmark_delete_is_refused_with_next_step`
（要「收藏当前频率」、实际「存为书签」），修前也在红，与隔离无关。

## 3. 审计钩子：证明「本进程没写真实配置」

守卫是**会话级**的，报出来的用例名是「本会话最后一个用例的 teardown」，不等于写盘者。
所以除了逐文件跑，另加进程级取证：

```python
# tests 之外的一次性插件，pytest -p probeplugin 载入
import os, sys
REAL = os.path.join(os.path.expandvars("%APPDATA%"), "mbdsdr", "desktop_settings.json").lower()
LOG = open(r"D:\mbdsdr\output\tmp\real_config_open.log", "a", buffering=1)
def _hook(event, args):
    if event == "open" and args:
        p = args[0]
        if isinstance(p, (str, bytes)) and str(p).lower() == REAL:
            LOG.write("pid=%d OPEN %r mode=%r\n" % (os.getpid(), p, args[1]))
    elif event in ("os.replace", "os.rename") and args:
        for a in args[:2]:
            if isinstance(a, (str, bytes)) and str(a).lower().startswith(REAL):
                LOG.write("pid=%d %s %r\n" % (os.getpid(), event, a))
sys.addaudithook(_hook)
```

结论：修后全部取证运行（单文件 + 合并会话）对真实配置**只有 `mode='r'` 打开，写事件 0 条**
（`mode='w'` / `os.replace` / `os.rename` 均为 0）。即「守护报的 mtime 变化不是本进程造成的」——
并发会话（另一条线在跑未隔离的测试或脚本）会跨进程改 mtime，这是「本进程全绿 ≠ 跨进程无假红」的根。

## 4. 注册表边界：逐字节相同只是「碰巧没写」

- `isolated_profile` 只重定向零参 `QSettings`；产品里窗口几何/分栏用的两参
  `QSettings("MBDSDR", "Desktop")` **不吃 setPath**，实测仍落
  `HKCU\Software\MBDSDR\Desktop`（另一线交接单 `0b82d0be` 已登记）。
- 实测 `test_three_column_layout` 跑前/跑后 `reg export HKCU\Software\MBDSDR` 逐字节相同，
  **但这只是「该用例没触发保存几何」，不是「隔离住了」**。
- 守卫也不监控注册表（只比 desktop_settings.json 的 size/sha256/mtime_ns + 目录清单 +
  bookmarks 库 + gui_config 观测站三项）。

结论写死：**注册表那半逐字节相同 ≠ 隔离生效；这半不在本修复内，由锁线统一清点全仓两参 QSettings。**

## 5. 「同内容重写」的定性与根治

取证方式：把真实配置**复制**到临时 APPDATA，重定向 HOME/APPDATA/QSettings 后构造并销毁
MainWindow，记录每次 `set()` 与副本的 size/sha256/mtime_ns。

- 生命周期内只有一次 `set()`：`touch_mode`，写入值等于盘上原值；
- `DesktopSettings.set()` 无条件置脏 → 500ms 防抖 → `_write_to_disk()`：读盘 payload 做底、
  合并内存值、整份 `os.replace`；
- 副本结果：`size` 不变、`sha256` 相同、`mtime_ns` 变——**重写同内容，不是改了别的文件**。

定性：**当前不算数据污染（零字段变化），但属于越界写用户数据**，且只差一个「值不同」就是
真污染；mtime 空转还会误导备份/同步/热重载。

根治（产品侧，`desktop/settings.py`）：
1. `_write_to_disk()` 合并出文本后先与盘上文本比较，相同则**跳过 `os.replace`**（不碰
   mtime、不换 inode），不同才原子替换；
2. `default_config_path()` 支持 `MBDSDR_SETTINGS_DIR` 覆盖点（测试/CI 用，生产不设不改行为）。

用例：`tests/test_settings_write_isolation.py`
- 同值 `set()` + `flush()` → `os.replace` 调用 **0 次**，`size/sha256/mtime_ns` 逐位不变；
- 改值仍照常落盘（防止「不写」被做成「永不写」）；
- `MBDSDR_SETTINGS_DIR` 覆盖生效；
- 不请求 `isolated_profile` 也拿不到真实路径（兜底生效）。

> 命名更正：指令里说的 `mbdsdr_ai/config.py::ConfigStore.set()` 实际不存在——`ConfigStore`
> 只有 `update(mutator)`，且已有「`mutator` 返回 False 表示放弃写入」的契约，返回值被调用方
> 当「是否真的写盘成功」用。本次实测的写盘者是 `desktop/settings.py::DesktopSettings`，
> 故只改那一处，不动 `ConfigStore`（改返回值语义有破坏调用方风险）。

## 6. 防复发装置（本次新增）

| 装置 | 位置 | 作用 |
|---|---|---|
| 会话级 autouse `_isolate_backing_config` | `tests/conftest.py` | 把 `default_config_path()` 指向会话 tmp；用例体内直接 `MainWindow()` 也写不到真实文件 |
| `MBDSDR_SETTINGS_DIR` 覆盖点 | `desktop/settings.py::default_config_path` | 测试/CI/无人值守场景可整体改配置目录 |
| 同内容不落盘 | `desktop/settings.py::_write_to_disk` | 同值 `set()` 不再制造 mtime 空转 |
| 删冗余 autouse `_isolated` | 3 个 gimbal 测试 | 见 §7；module 级 `mw` 直接请求 `isolated_profile` 后它纯冗余 |

autouse 兜底的边界（必须知道）：
- 只盖 `desktop_settings.json`；`~/.mbdsdr/layouts.json`、`gui_config.json`、
  `mbdsdr_ai` 的 `config.json` 仍走真实路径（守卫至少覆盖 gui_config）；
- 不盖 `QSettings`（含注册表），理由同 §4；
- 守卫快照在 `pytest_configure`（兜底 fixture 之前）取，所以守卫盯的仍是真实文件，
  兜底只保证"写不落到那里"。

## 7. 更正：「死 fixture」判断有误

先前回报把 4 个 `_isolated(isolated_profile)` 说成「声明但无人请求」——**错**。
它们是 `@pytest.fixture(autouse=True)`，每个用例都请求，问题在**作用域**：
module 级 `mw` 先于 function 级 autouse 实例化，`MainWindow()` 在隔离生效前就构造完了。
这正是「看着写了隔离却仍报 mtime 变化」的机制。

处置：
- `test_gimbal_control_robustness` / `test_gimbal_rotctld_sim` / `test_gimbal_track_chain`：
  `mw` 已直接请求 `isolated_profile`，autouse 纯冗余，**删**；
- `test_device_capability_probe`：**保留**。它的 autouse 是文件里唯一的隔离，且只覆盖
  function 级用例体，删掉会真的失去保护。

## 8. 四次他线在途破坏（本进程内全绿 ≠ 跨进程无假红）

| 时间（11-11） | 现象 | 影响 | 处置 |
|---|---|---|---|
| 约 22:20 | `settings_panel.SettingsPanel` 缺 `_restore_expanded_state`（AttributeError） | 两个 record 文件 8 error | 未干预，约 1 分钟后自愈 |
| 约 22:26 | `tests/conftest.py` `NameError: _collects_qt_tests`（Qt 锁门控改名中，roadmap-64-70 在途） | 合并会话直接 INTERNALERROR，no tests ran | 未碰该文件，等其收尾后重跑 |
| 约 22:31 | `desktop/main_window.py` `SyntaxError: expected 'except' or 'finally'`（卫星线在途） | 所有构造 MainWindow 的用例 2 error | 未干预，约 2 分钟后自愈 |
| 22:48 之后（29 文件合并会话内） | `settings_panel.py` 的「调用」已写、「定义」未写：`refresh_font_scale_note` 只存在于工作区 +23 行，`git show HEAD:desktop/settings_panel.py` 0 命中 | 守门用例的子进程导入半成品 → `AttributeError`；报错里只有方法名，`tests/` 下 `git grep` 与 `--collect-only` 都找不到对应 nodeid | 未干预；复跑 `tests/test_planetarium_entry_visibility.py::test_overlay_avoids_ai_bar_only_on_intersection` 已 `1 passed`。不是产品红，已从「需他人接手」撤掉 |

四条都不由隔离改动引起，但都说明：**守卫生效的前提是同一时刻只有一个进程在写真实配置**；
并发会话里出现的 `[mbdsdr-guard]` 可能来自别的进程，不能直接归给当前用例。

第 4 例值得单独记一条普遍形式，因为它的形态和前三条不同：

> **「调用」与「定义」分两次写盘 → 任何在此窗口内起子进程/导入模块的会话都会看到半成品。**
> 所以在途破坏不只是「文件被改」，还包括「模块处于语法完整但契约不完整的中间态」。

推论（判断这类红的形状）：报错落在**导入期或构造期**、且方法名在已提交代码里查无此物时，
先怀疑工作区未提交的半成品，而不是被测逻辑；用 `git show HEAD:<path> | grep <符号>` 一步即可定性。
反过来，若报错是断言失败而非 `AttributeError`/`SyntaxError`，才轮到怀疑尺子（`isVisible()` 在未 `show()` 的窗口上恒为 False 这类）。

## 9. 本次提交

- 26 个测试文件各一笔（`test(隔离): <file> ...`，每笔 1 insertion / 1 deletion）。
- `99391435` fix(settings): 同值 set 不再重写配置；新增 MBDSDR_SETTINGS_DIR 覆盖点
- `3e89e674` test(隔离): 会话级 autouse 兜底重定向 default_config_path
- `da196062` test(隔离): 同值 set 不写盘 / autouse 兜底 / env 覆盖 三条用例
- `e2d0b98e` / `0b6769bc` / `fa44d756` test(隔离): 删掉被 mw 取代的 autouse _isolated

## 10. 未做 / 需他人接手

- 注册表（两参 `QSettings`）清点与隔离：锁线。
- `test_gimbal_rotctld_sim::test_ui_never_shows_degrees`：**已恢复绿，不需要接手**。
  当时红因是 `d1fcba5b` 的 `_allow_for()` 读天空图侧 `sat_tracker`，而该用例注入的是
  执行侧 `_loop_sat_tracker` → 闸门判 `no_data` → 不下发调谐 → 走「未下发」分支，
  不再产出「不可报」。这是两处 tracker 不同源的真实不一致，不是「文案冲突」。
  卫星线已用 `94ab10cd`（23:16）改读 `_loop_sat_tracker` 并前移其创建，顺带补了
  6 个用例的 `tracking_policy_for` 桩。实测整文件 `6 passed in 5.74s`。
  断言本身不过宽（作用域是精度文本/状态栏片段/tooltip 片段），保留。
- `mbdsdr_ai/config.py` 的 `ConfigStore.update()`：不改（理由见 §5 命名更正）。
- 12 处用例体内直接构造：已由 autouse 兜底覆盖，未逐个改 fixture。
