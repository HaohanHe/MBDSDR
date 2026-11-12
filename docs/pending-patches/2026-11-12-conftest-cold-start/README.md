# conftest 冷启动：分段实测 + 必需/可延/可去三栏（只量不改）

> 状态：**提案，未改 `tests/conftest.py`**（该文件由布局线持 claim，正在落
> 「每用例一份 `desktop_settings.json`」隔离补丁）。本文件只出测量与判定，
> 供那条线落地时一并吸收。
> 测量环境：Windows / CPython 3.14 / PySide6 6.12；同一台机器上另有并发会话，
> 单段数值两次采样的波动约 ±10%，`settings` 相关段在机器繁忙时会出现 ±1.5x。
> 命令与原始日志：`output/tmp/time_conftest_segments.py`、
> `output/tmp/importtime_pytest.log`、`output/tmp/importtime_conftest.log`。

## 一、结论先行

1. **这条命令的冷启动现在就是秒级**：`python -m pytest tests/test_s_meter.py`
   实测 **1.39 s**（8 passed in 0.38 s），`--collect-only` **1.30 s**。
   目标（冷启动进秒级）**现状已达成**，不需要靠改 conftest 去换。
2. **`tests/conftest.py` 自身导入只有 0.21–0.24 s**（其中 `import pytest`
   占 0.19–0.20 s，本文件顶层自己只花 10–45 ms）。「卡在 conftest 导入
   两分钟」不成立。
3. **分钟级等待的真身是 Qt 跨进程字节锁**：实测 `--collect-only` 总耗时
   **185.28 s，其中等待锁 184.2 s**。HEAD 版 conftest 在等锁期间**一行都不
   打印**，外部看就是「停在 conftest 导入」。
4. **导入链最重的一段不在 conftest，而在 `pytest_configure` 的配置快照**：
   `settings` 累计 0.65–0.70 s，其中 **0.30 s 是
   `constants → sdr_backend → audio_out → sounddevice`**（`sounddevice`
   自耗 0.28 s，加载 PortAudio）。`settings` 里其实只用到
   `default_config_path()`。
5. 每用例的隔离成本 **0.7–0.9 ms/用例**，可忽略；贵的都是一次性守卫安装。

## 二、分段计时（冷启动，每段独立子进程，`_t` 只包住该段）

`-X importtime` 结果（`importtime_pytest.log`，按 cumulative，单位 ms）与
分段采样互相印证：

| 排名 | 段 | 采样① | 采样② | importtime cumulative | 何时发生 |
|---|---|---|---|---|---|
| 1 | `import settings`（含 config_schema / constants / QtCore） | 645.6 | 697.2 | 393.6 | `pytest_configure` |
| 1a | └ `import constants` → `sdr_backend` → `audio_out` → `sounddevice` | 300.6 | 306.9 | 152.9（constants 累计） | 同上，被 settings 带进来 |
| 2 | `import pytest`（conftest 顶层唯一重导入） | 191.9 | 199.0 | 202.1 | conftest 模块导入 |
| 3 | `import PySide6.QtCore`（settings 顶部强依赖） | 149.6 | 130.5 | 64.5 | 同上 |
| 4 | conftest 模块本体（含 pytest） | 236.6 | 208.0 | — | 收集前 |
| 4a | └ conftest 自身增量（stdlib + sys.path + 常量 + 函数定义） | 17–45 | 9–17 | — | 同上 |
| 5 | `QApplication([])` 创建 | 146.9 | 164.0 | — | 首个 Qt 会话 |
| 6 | `import PySide6.QtWidgets`（QtCore 已载） | 11.0 | 11.2 | 7.6 | 同上 |
| 7 | `scripts/qsettings_guard` 安装 | 58.1 | 57.0 | — | `pytest_collection_finish` |
| 8 | `_install_qsettings_isolation()`（补丁装入，不含 Qt 导入） | 8.8 | 7.9 | — | 同上 |
| 9 | `_install_modal_guard()` | 2.0 | 2.1 | — | 同上 |
| 10 | `_capture_app_style_baseline()`（字体/QSS/palette 快照） | 1.8 | 1.4 | — | 首个 Qt 会话 |
| 11 | `_snapshot_real_config()`（3 个真实文件的 sha256 + 目录列举） | 1.8 | 1.5 | — | `pytest_configure` |
| 12 | 每用例 QSettings ini（建目录 + 2 次 setPath + rmtree） | 0.7 | 0.9 | — | 每用例 |

说明：

- **字体 / QSS / palette 的「初始化」不在导入期**。它们只在
  `_capture_app_style_baseline()` 里各取一次快照（1.4–1.8 ms），且按用例
  只在状态真变了才 `setFont/setStyleSheet/setPalette`。不是冷启动成本。
- conftest 顶层没有任何 Qt / 字体 / QSS / palette 初始化（AST 清单：
  仅 `ctypes/gc/hashlib/importlib.util/inspect/json/os/shutil/sys/tempfile/
  time/types` + `pytest`，加 `sys.path` 注入与 `MBDSDR_OFFLINE` 默认值）。
  「conftest 导入慢」在这个文件里没有对应物。
- 1.39 s 的账基本对得上：解释器 0.04 + conftest 0.24 + settings 0.65 +
  qsettings_guard 0.06 + 收集/跑用例 0.4。

## 三、三栏判定

### A. 必须（导入期 / 收集期，不能挪）

| 项 | 成本 | 为什么不能挪 |
|---|---|---|
| `import pytest` | 0.19–0.20 s | 任何 hook 都要它 |
| conftest 顶层（`sys.path` 注入、`MBDSDR_OFFLINE=1`、`_RESULT`、纯函数） | 10–45 ms | `_ROOT_DIR` 必须在各测试文件自己加 path 之前垫好；离线默认决定 `fetch_tle` 走缓存还是空态 |
| `pytest_configure` → `import settings` 快照 | 0.65–0.70 s | 快照必须早于**任何** fixture 建 MainWindow，否则守卫拍到的是已被污染的状态；`settings` 顶部 `from PySide6.QtCore import QTimer` 无法绕开 |
| 会话级 QSettings 隔离 + 写守卫 | 8 + 58 ms | module 级 fixture 会在 function 级之前建窗，安装必须更早；写守卫是「隔离被绕过」的唯一报警器 |
| `_isolate_backing_config`（`default_config_path` 重定向） | <1 ms | 防「用例体里直接 `MainWindow()`」把真实 `desktop_settings.json` 写脏 |
| Qt 跨进程互斥锁 | 0（无争用时） | 并发跑 Qt 用例会把全机 commit 打穿（51 个崩溃进程那次） |

### B. 可延到首次使用（保持「每用例一份独立 profile」不变）

| 项 | 成本 | 延法 / 前提 |
|---|---|---|
| `PySide6.QtWidgets` + `QApplication` + 样式基线 | 11 + 147 + 1.8 ms | **已懒**：`_live_qapp()` 无 app 时返回 `None`，纯逻辑会话一分不花。保持现状，别提前 `QApplication([])` |
| `_install_modal_guard()` | 2 ms | 已挂在「判定为 Qt 会话」之后；`QMessageBox.exec` 拦截只需在建窗前 |
| 每用例 QSettings ini | 0.7–0.9 ms | **不能**延到「首次用到 QSettings」——module fixture 先建窗。只能维持 collection_finish 装、function 级切目录 |
| `scripts/qsettings_guard` | 58 ms | 同上，安装点不能晚于 module fixture |
| `constants → sdr_backend → audio_out → sounddevice` | 0.30 s | **产品侧**可延：`desktop/constants.py` 顶层如今拉进 PortAudio；改成首次调用时才 import `sdr_backend`，全部 Qt 测试一次性省 0.3 s。属产品改动，本提案只登记 |

红线：以上任何延迟都**必须**保持「每个用例一份独立 profile / ini」。
上一轮已证明状态泄漏会让 8 条布局基线假红（`_probe` 点「专注」把
`focus_mode=False` 写进共享配置，后建窗口多出 95 px 抽屉 + 工具栏），
**不能为了快收敛成会话级共享**。

### C. 可去（删除 / 停止做的）

- **代码段：0 项。** 逐段核过：没有任何一段是「为了快而重复导入」或
  「已失效兼容」。`_QT_BASELINE`、`_code_imports_qt`、`_MODULE_QT_CACHE`
  是锁门控的判定基线，删了会把非 Qt 会话误判成 Qt 会话去抢锁。
- **可去的是行为，不是代码**：`--collect-only` 仍然抢锁。收集阶段不建
  `QApplication`、不执行用例，抢锁只会把「看一眼有多少用例」变成「等另一
  个会话跑完」（实测 184 s）。见附录补丁 P1。

## 四、改前 / 候选补丁后实测对比

候选补丁 = 附录 `01-lock-visibility.patch`（只动锁的三处，不动任何隔离逻辑）：

| 场景 | HEAD（现在的 conftest） | 候选补丁 | 判据 |
|---|---|---|---|
| `pytest tests/test_s_meter.py -q`（无争用） | **1.39 s**（8 passed in 0.38 s） | 1.43 s（8 passed in 0.42 s） | 无争用时不改变成本 |
| `--collect-only -q`（无争用） | 1.30 s | — | 同上 |
| `pytest tests/test_s_meter.py -q`（有争用） | **185.28 s**（等锁 184.2 s，**全程无提示**） | **10.35 s**（先打「等锁中：持有者 PID 33092（23:01:09 启动）」，9.0 s 后获锁，8 passed in 9.49 s） | 症状从「静默卡住」变「知道在等谁」 |
| `--collect-only -q`（有争用） | 被挡满等待 | 1.49 s（不抢锁） | 收集阶段不需要锁 |
| 等锁超时 | RC=2，消息只有锁路径 | RC=2，消息含持有者 PID 与启动时刻 | 可一眼区分「别人在跑」与「僵尸持锁」 |
| 释放后残留 | — | `.owner` 随锁一起删除，无残留 | 授权失败不影响互斥正确性（字节锁仍是权威） |

补丁的定位：**它不加速导入**（导入本来就是秒级），它把「分钟级等待」
从不可见变成可见，并去掉收集阶段的无效等待。

## 五、风险（必须记一句）

**「测试能跑通」和「测试被 runner 收集」是两件事，后者才是回归防线。**

现有形态（本轮实测 + 一次纠正）：

- `tests/` 下文件里含 `test_` 函数、但**文件名不匹配 pytest 默认
  `python_files`（`test_*.py` / `*_test.py`）**的共 **18 个文件 / 151 条
  断言**：13 个模块级 `def test_`（74 条）+ 5 个类内 `test_` 方法（77 条）。
- 这 18 个文件**在 CI 里并非完全没人跑**：`scripts/ci_pytest_groups.py`
  的收件口径自 phase71 起包含 `*roundtrip*.py`，并且是以**显式路径**传给
  pytest（显式路径不受 python_files 限制）。所以「CI 从没跑过」是错的。
- 真正的缺口有两个：① 裸 `pytest tests/`、IDE、以及各线自己拼的
  `pytest tests/test_xxx.py` 看不见这些文件，本地和 CI 跑的不是同一集合；
  ② 一旦某条线按「conftest 导入要两分钟」的假结论改用临时脚本直接调
  `test_*()`（如 `tests/ax25_aprs_roundtrip.py:135-143`、
  `tests/fldigi_roundtrip.py:186-195` 的 `__main__`），那条线就只剩手调。
- 处置：18 个全部改名 `test_*.py` 纳管（本轮已完成 18/18，逐个实跑：
  151 条断言 148 passed / 1 skipped（`syn_robot36.wav` 缺失守卫）），
  裸 pytest 与 CI 从此是同一集合。
- 另有 `tools/hw_selfcheck/test_selfcheck.py:257`、`tools/test_paste_block.py:318`、
  `tools/test_acceptance_run.py:680`、`tools/onboarding/test_parse_hw_report.py:364`
  用 `pytest.main([__file__])`；`mbdsdr_ai/tests/` 有 10 处同样写法。
  这些不在 CI 分组收件范围内，仍是「自己跑自己」。

一条线在真 runner 里没验证过时，用临时脚本调测试函数能拿到「通过」，
但那批断言从此不在任何自动回归里——改坏了没人知道。

## 六、复现命令

```powershell
cd D:\mbdsdr\MBDSDR
# 冷启动总耗时
Measure-Command { python -m pytest tests/test_s_meter.py -q }
Measure-Command { python -m pytest tests/test_s_meter.py --collect-only -q }
# importtime 排名（512 行）
python -X importtime -m pytest tests/test_s_meter.py --collect-only -q 2> importtime.log
# 分段（每段独立子进程）
python D:\mbdsdr\output\tmp\time_conftest_segments.py
# 谁在持锁（僵尸判据：owner PID 已不存在却仍拿不到锁）
python scripts/file_claim.py status            # 文件 claim
Get-Content D:\mbdsdr\.locks\qt_test.lock.owner   # Qt 锁持有者（补丁 P1 之后才有）
```

## 七、给布局线的合并顺序与位置（P1 补丁）

`01-lock-visibility.patch` **不要单独立一笔**，由持有 `tests/conftest.py`
claim 的布局线在落「每用例一份 `desktop_settings.json`」隔离补丁时一并吸收。

**顺序（不可颠倒）**

1. 先落隔离补丁（每用例独立 profile / ini）。它会改
   `pytest_collection_finish`、`_isolate_backing_config`、`isolated_profile`。
2. 再应用本补丁。两者在 `pytest_collection_finish` 同一函数里相邻，直接
   `git apply` 可能冲突；**按下面的锚点手工合并，不要 `-3` 强行覆盖隔离改动**。

**改动位置（6 处，全部在锁相关代码内，不碰任何隔离逻辑）**

| 位置 | 改动 |
|---|---|
| `_qt_lock_path()` 之后 | 新增 `_qt_owner_path()` / `_write_qt_owner()` / `_read_qt_owner()` |
| `_acquire_qt_lock()` 抢锁成功分支 | 成功时 `_write_qt_owner(path)` |
| `_acquire_qt_lock()` `except OSError` 分支 | 首次失败打一行 `[mbdsdr-lock] 等锁中：<持有者>；最多等 Ns（路径）` |
| `_release_qt_lock()` | 增 `path` 参数，释放后删 `.owner` |
| `_LOCK` | 增 `"path"` 键 |
| `pytest_collection_finish()` | ① `_install_qsettings_write_guard()` **之后**，`collectonly` 为真直接 return；② 超时消息里带上 `_read_qt_owner(path)` |

**合并顺序上的关键一句**：collect-only 早退必须放在隔离安装之后——收集阶段
仍要装 QSettings 隔离与写守卫，只是不抢执行锁；反过来会把隔离也一起跳过。

**语义边界**

- `--collect-only` 不建 `QApplication`、不执行用例，所以不需要执行锁；
  若将来收集期出现 module 级建窗，必须恢复抢锁。
- `.owner` 只是提示，字节锁才是权威；写失败/残留/过期都不影响互斥正确性。
- 若 `.owner` 里的 PID 已不存在却仍拿不到锁（`taskkill`/`Stop-Process` 报
  「无实例」），按**僵尸持锁**处理（另一线实测需 CIM `Terminate`），不要
  当成「别人正在跑」。

**验收（三条同时成立才算落地）**

1. 无争用：`python -m pytest tests/test_s_meter.py -q` ≈ 1.4 s；
2. 有争用：先打「等锁中：持有者 PID <pid>（<HH:MM:SS> 启动）」，
   获锁后打「已获得…（等待 Ns）」；
3. 等锁超时：RC=2，消息含持有者 PID 与启动时刻；进程退出后
   `D:\mbdsdr\.locks\qt_test.lock.owner` 不存在。

