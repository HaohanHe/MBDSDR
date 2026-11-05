# python.exe 原生崩溃取证（2026-11-05）

**结论一句话**：不是单条用例的确定性崩溃，也不是显卡/平台插件问题；直接崩溃点是 PySide6 绑定层空指针解引用（`pyside6.abi3.dll+0x1c91e`，落在导出符号 `PySide::SignalManager::retrieveMetaObject(PyObject*)` 的 +0xAA 处）；同一时段反复伴随**系统级提交内存耗尽**（WER 2004：python.exe 单进程 17–28 GB、同一时刻两个 python.exe 合计 28.8 GB，本机 commit 上限 38.1 GB）——因果链"OOM→空指针"是机制推断，未证实。隔离条件下复现率 **0/44**（未复现）。防线（Qt 测试跨进程互斥锁 + 提交内存看门狗）已落地并**逐项验证生效**。

---

## 1. 判定与依据

| 候选 | 结论 | 依据 |
|---|---|---|
| (a) Qt 平台插件/插件加载期空指针 | **排除"加载期"形式**，但确属 PySide6 绑定层内部空指针 | 转储模块表里 `qoffscreen.dll` 已成功加载（221 个模块之一），崩溃点不在插件 init；故障偏移落在 PySide6 导出符号 `SignalManager::retrieveMetaObject` 内 |
| (b) 显卡驱动 / Qt RHI（D3D） | **排除** | 崩溃进程模块表无任何厂商驱动 DLL（无 `nvoglv64/igd*/atio*`）；`d3d11.dll / dxgi.dll / D3D12.dll` 只是系统组件随 Qt 载入；故障模块是 `pyside6.abi3.dll`，不是 GL/D3D 模块 |
| (c) 多进程并发跑 Qt 测试的资源冲突 | **部分成立（作为放大器，不是直接成因）** | 51 个崩溃进程中有 14 个 5 分钟窗口内出现 2–4 个不同 PID 同时崩溃；WER 2004 记录 20:00:50 时 `python.exe(2936) 17.2 GB` + `python.exe(192) 11.6 GB` 同时存在。但 5 轮"双进程并发跑 Qt 用例"隔离实验 0 崩溃 → 并发本身不足以复现 |
| (d) C++ 扩展（librtlsdr/DLL） | **排除** | 崩溃进程确实载入 `rtlsdr.dll`，但故障模块、异常地址、栈帧都在 PySide6/Qt 侧，rtlsdr 未出现在栈上 |
| (e) 内存耗尽 | **成立（现场的关键条件）；巨无霸已在 §9 定位到 fuzz 长字符串用例** | 5 条 WER 2004「虚拟内存不足」：python.exe 单进程 24.3 / 17.2+11.6 / 28.4 / 28.7 / 24.7 GB；本机 commit 上限 38.1 GB、物理内存 15.7 GB。全量套件单进程峰值仅 1.9 GB（§6.1）→ 17–28 GB 的 python 进程不是 pytest 负载，是 `scripts/tool_param_fuzz.py` 的 `long_string` 用例（§9） |

**为什么把 (e) 与 PySide 空指针绑定**：崩溃点读到的是"接近 NULL 的小偏移/0"（`0x0`、`0x6B8`），不是野指针大地址——提交耗尽时分配失败、容器/表结构处于半初始化状态，正好产生这种形态；且崩溃在隔离环境（≤1.9 GB 峰值、44 次运行）**一次都复现不出来**，只在真实高压力窗口出现。**但这是机制推断，不是直接证据**（无 cdb 符号、无当时命令行），故标注"强相关、机理未证实"。

**必须诚实标注的边界**：没有证据证明每一次崩溃都由 OOM 直接触发（23:01–23:55 的 13 次崩溃窗口内没有 2004 事件）。因此准确表述是：**直接缺陷是 PySide6 绑定层的空指针路径（观测事实）；内存耗尽是现场同时段反复出现的关键条件（观测事实）；"OOM 导致该空指针"是机制推断（未证实）；"每一次崩溃都因 OOM"未验证。**

---

## 2. 时间线（本地时间 Asia/Shanghai）

| 时间 | 事件 | 来源 |
|---|---|---|
| 11-04 03:58:47 | 提交耗尽：`python.exe(41316)` 24,320,053,248 B | System 日志 2004 |
| 11-04 17:20:26 / 17:38:36 | 崩溃：`ucrtbase.dll+0xa527e`（0xC0000409 fail-fast） | Application 1000 |
| 11-04 17:40:24 / 17:41:27 | 崩溃：`pyside6.abi3.dll+0x1c91e`（0xC0000005） | Application 1000 |
| 11-04 19:25:21 – 19:59:42 | 9 次崩溃（`Qt6Core+0x1ce68` ×3、`pyside6+0x1c91e` ×5、`python314+0x1761c8` ×1） | Application 1000 |
| 11-04 20:00:50 | 提交耗尽：`python.exe(2936)` 17.2 GB **+ `python.exe(192)` 11.6 GB**（并发双进程） | System 2004 |
| 11-04 20:05:00 | 提交耗尽：`python.exe(2936)` 28,398,059,520 B | System 2004 |
| 11-04 21:01:39 – 21:52:16 | 5 次崩溃（`Qt6Core+0x1ce68` ×2、`ucrtbase+0xa527e` ×1、`pyside6+0x1c91e` ×2） | Application 1000 |
| 11-04 22:13:01 – 22:59:55 | 9 次崩溃；22:35:05 同时刻提交耗尽 `python.exe(34888)` 28.7 GB、22:35:06 `VCRUNTIME140+0x11a89` | Application 1000 / System 2004 |
| 11-04 23:01:17 – 23:55:06 | 13 次崩溃（其中 12 次 `pyside6+0x1c91e`），该窗口内**无** 2004 事件 | Application 1000 |
| 11-05 00:08:35 | 崩溃：`ucrtbase+0xa527e` | Application 1000 |
| 11-05 00:08:56 | 崩溃：`pyside6+0x1c91e`（PIDs 0xA280 段） | Application 1000 |
| 11-05 00:09:01 | 崩溃：`pyside6+0x1c91e`（dump `python.exe.3304.dmp`） | Application 1000 |
| **11-05 00:11:19** | **用户看到的弹窗**：`pyside6+0x1c91e`，PID 0xA280=41600，Report ID `1f063153-…` | Application 1000/1001 |
| 11-05 00:13:10 | 提交耗尽（**崩溃之后 2 分钟**）：`python.exe(37796)` 24,715,304,960 B（24.7 GB） | System 2004 |
| 11-05 00:24 – 01:18 | 本次取证的全部隔离实验：**0 次新崩溃**（事件 1000 最后一条仍是 00:11:19） | Application 1000 + 自测日志 |

统计口径：3 天内 `python.exe` 事件 1000 共 **51 条**（= 51 个崩溃进程，PID 各不相同）；1001 里同一 signature 上报 **80 条**（含重复上报）。按故障偏移分布：`0x1c91e` 37、`0x1ce68` 8、`0xa527e` 4、`0x11a89` 1、`0x1761c8` 1。

---

## 3. 原始事件日志摘录

### 3.1 Application 事件 1000（00:11:19，用户看到的那次）

```
出错应用程序名称： python.exe，版本： 3.14.150.1013，时间戳： 0x68e4e95f
出错模块名称： pyside6.abi3.dll， 版本： 0.0.0.0，时间戳： 0x6ac3a614
异常代码： 0xc0000005
错误偏移： 0x000000000001c91e
出错进程 ID： 0xA280
出错应用程序开始时间： 0x1DD6C7707F748AF
Faulting 应用程序路径： C:\Users\xxhzb\AppData\Local\Programs\Python\Python314\python.exe
Faulting 模块路径： C:\Users\xxhzb\AppData\Local\Programs\Python\Python314\Lib\site-packages\PySide6\pyside6.abi3.dll
Report ID： 1f063153-b524-4659-b9c4-5818562216c3
```

### 3.2 Application 事件 1001（同一崩溃的 WER 签名）

```
故障存储段 1191246180315457694，类型 4
事件名称: APPCRASH
问题签名:
P1: python.exe
P2: 3.14.150.1013
P3: 68e4e95f
P4: pyside6.abi3.dll
P5: 0.0.0.0
P6: 6ac3a614
P7: c0000005
P8: 000000000001c91e
报告 ID: 1f063153-b524-4659-b9c4-5818562216c3
```

### 3.3 System 事件 2004（资源耗尽诊断）

```
11-05 00:13:10  Windows 成功诊断出虚拟内存不足的情况。以下程序使用了大部分虚拟内存:
python.exe (37796) 使用了 24,715,304,960 字节；WorkBuddy.exe (17628) 使用了 1,409,064,960 字节；
Trae CN.exe (3384) 使用了 1,316,556,800 字节。

11-04 22:35:05  python.exe (34888) 使用了 28,739,452,928 字节；WorkBuddy.exe (20968) 1,994,395,648 字节。

11-04 20:05:00  python.exe (2936) 使用了 28,398,059,520 字节；WorkBuddy.exe (30104) 1,900,826,624 字节；
python.exe (34872) 使用了 869,482,496 字节。

11-04 20:00:50  python.exe (2936) 使用了 17,208,483,840 字节（17.2 GB）；python.exe (192) 使用了 11,609,010,176 字节（11.6 GB）。

11-04 03:58:47  python.exe (41316) 使用了 24,320,053,248 字节；Trae CN.exe (15368) 1,247,334,400 字节。
```

---

## 4. 转储证据（只读分析，未在 C 盘落任何新文件）

**位置**：`%LOCALAPPDATA%\CrashDumps`（10 个 minidump，各 8.2 MB，23:26–00:11）；WER 归档在 `C:\ProgramData\Microsoft\Windows\WER\ReportArchive\AppCrash_python.exe_45fe2c9a…`（目录 ACL 拒绝读取，未强改权限）。本机没有 `cdb/windbg`，因此用自写 minidump 解析器直接读异常流（ExceptionStream）、模块表（ModuleListStream）与栈内存（MemoryListStream / Memory64ListStream）——解析器为一次性工具，已清理，方法可复现。

**取证产物脱敏规则**：红线门命中时**先判断"真 PII 还是普通长数字"**，再决定是否动文件（本次 `17,208,483,840` 实为 WER 2004 中 python.exe(2936) 的提交字节数，不是手机号）。日志/转储解析出的长数字写入文档前统一按千分位书写（如 `17,208,483,840`），既保留精确值，又避免连续的 11 位数字再次被误判；提交前对 `docs/**` 与解析输出跑一遍手机号/邮箱/证件号正则扫描。

**异常记录（`python.exe.41600.dmp`，即 00:11:19）**

```
ExceptionCode = 0xC0000005
ExceptionAddress = 0x7FFD6632C91E  (pyside6.abi3.dll + 0x1C91E)
ExceptionInformation[0] = 0 (READ)
ExceptionInformation[1] = 0x0        <-- 与弹窗"引用了 0x0 内存，不能为 read"一致
```

**异常记录（`python.exe.3304.dmp`，00:09:01，同一 signature）**

```
ExceptionCode = 0xC0000005
ExceptionAddress = 0x7FFD6632C91E  (pyside6.abi3.dll + 0x1C91E)
ExceptionInformation[0] = 0 (READ)
ExceptionInformation[1] = 0x6B8      <-- 同样是"NULL + 小偏移"形态
```

**偏移归符号**（无 PDB，用 PE 导出表取"最近导出 ≤ 偏移"，因此是**包含关系**而非精确函数边界）：

- `0x1C91E` → `PySide::SignalManager::retrieveMetaObject(PyObject*)`（导出 RVA `0x1C874`）+`0xAA`
- 栈上调用者 `0x168A3` → 落在 `PySidePropertyPrivate::getValue` 导出（RVA `0x16764`）区段
- `0x1CE68`（Qt6Core 8 次 fail-fast）→ 属 Qt6Core 内部 abort/fail-fast 路径，未做进一步符号化（无 PDB，**未验证**）

**故障线程栈（模块化扫描，非符号化回溯）**

```
exception ctx: RIP=pyside6.abi3.dll+0x1C91E  RSP=0x8665ADDC20
rsp+0x0018  Qt6Widgets.dll+0xED6EB      <-- 由 Qt 控件代码进入的调用链
rsp+0x0048  pyside6.abi3.dll+0x168A3
rsp+0x0068  python314.dll+0x619B0
rsp+0x00E8  shiboken6.abi3.dll+0x2CBC9
```

即：**Qt 控件发出/读取属性 → shiboken 绑定 → PySide 取元对象 → 解引用空指针**，属 Python 包装器 ↔ C++ 对象生命周期不一致的路径，不是插件加载期问题。

**崩溃进程的模块画像（221 个模块）**：`Qt6Core/Qt6Gui/Qt6Widgets/Qt6OpenGL/Qt6OpenGLWidgets`、`Shiboken.pyd`、`shiboken6.abi3.dll`、`pyside6.abi3.dll`、`qoffscreen.dll`、`rtlsdr.dll`、`numpy/scipy`、`d3d11/dxgi/D3D12`、`_wmi/_socket/_ssl/brotli`。说明是"载入 SDR + GUI + 网络的全量级测试进程"，而非某个孤立小脚本。

**环境基线**：Qt 6.12.0 / PySide6 6.12.0 / Python 3.14.0（PySide6 元数据声明支持 3.14、3.15，不属"版本不受支持"）；物理内存 15.7 GB，commit 上限 38.1 GB。

---

## 5. 触发用例定位（未完成，标注未验证）

用转储线索能确定的只有"范围"：

- 进程同时载入 QtWidgets/OpenGLWidgets + numpy/scipy + rtlsdr + 网络库 → 对应大范围测试进程，不是单一 UI 模块；
- 崩溃栈有 `Qt6Widgets` 帧 → 触发发生在 Qt 控件属性/信号回调里；
- `qt` 用例模块（源码含 `QApplication` 的 102 个文件）**整批在单进程跑完 95%（约 3400 用例）也 0 崩溃**，峰值仅约 1.6 GB。

因此**未能把崩溃钉到某一条用例**（GPU/符号/命令行证据均不足：URL 会话日志拿不到当时的命令行，Security 4688 审计未开启）。这一项明确标注 **未验证 / 未定位**。（§9 追加：触发负载已定位到 fuzz `long_string`，不在 pytest 用例侧；具体解码器分配点仍未定位。）

---

## 6. 复现实验记录

统一环境变量：`QT_QPA_PLATFORM=offscreen`、`MBDSDR_NULL_AUDIO=1`、`MBDSDR_NO_HW=1`、`MBDSDR_OFFLINE=1`（均在每次运行前显式设置）。判定"崩溃"= Application 1000 出现新的 python.exe 记录，或进程退出码为 `0xC0000005/0xC0000409` 类原生码。

| # | 场景 | 次数 | 新崩溃 | 备注 |
|---|---|---|---|---|
| 1 | 最小 Qt 脚本（QApplication + QWidget + `grab()`，正常退出） | 10 | **0** | 每次都打印 `grab ok` / `exit normally`，退出码 0 |
| 2 | 桌面 MainWindow 构造 + processEvents + grab + close 后正常退出 | 1 | **0** | 退出码 0，耗时 8 s |
| 3 | 单模块 `tests/test_ui_layout_locks.py` 全量 | 1 | **0** | 72 passed |
| 4 | 嫌疑 GUI 用例 `-k panel_screenshot`（含 grab 基线比对） | 20 | **0** | 20/20 退出码 0，总耗时 722 s；含互斥锁，天然串行 |
| 5 | 双进程并发（`MBDSDR_TEST_LOCK=0`，两进程同时跑同一 GUI 用例集） | 5 轮 ×2 | **0** | 10/10 全部 `15 passed`，无 1000 事件 |
| 6 | 102 个 Qt 模块单进程整批跑 | 1 | **0** | 跑到 95% 人工终止（约 40 min，已执行约 6100 条、其中 130 条失败，失败来自其它会话在途改动）；峰值 1.6 GB |
| 7 | 全量 `tests` + `mbdsdr_ai/tests` 单进程（带 5 s 采样与 8 GB 兜底） | 1 | **0** | 见 §6.1 |

**隔离复现率：0/44（0%）；未复现，因此无法给出"真实故障条件下的复现率"。** 不采用"人为把 commit 打穿"的方式来测复现率——那会再次连累同一台机器上的其它会话，属于用生产事故换数据，明确不做（标注未验证）。

### 6.1 全量套件内存观测（已跑完）

命令：`python -m pytest tests mbdsdr_ai/tests -q`（单进程，`MBDSDR_TEST_COMMIT_LIMIT_MB=0` 关掉看门狗、`MBDSDR_TEST_LOCK=0`，每 5 s 采样一次 `PrivateMemorySize64`，外部 8 GB 兜底）。

```
结果：144 failed, 6080 passed, 7 skipped, 244 warnings, 208 subtests passed in 1790.73s (0:29:50)
采样：起始 ~0.7 GB → 峰值 1933 MB（约在第 7–24 分钟）→ 收尾 1.25 GB
新崩溃：0（Application 1000 最后一条仍是 00:11:19）
```

**这条数据很关键，也必须写清楚**：单进程跑完 6100+ 条用例，峰值只有 **1.9 GB**，**无法**解释 WER 2004 里那 17–28 GB 的 `python.exe`。也就是说：

- 2004 记录的那个巨无霸 python.exe **不是**本次可复现的 pytest 负载；
- 它更可能是"被长期挂着跑的 python 进程"（桌面端 GUI 进程 / MCP 服务 / 长时采集脚本），这些进程在崩溃转储里也能对上（`qoffscreen.dll` + `Qt6OpenGLWidgets` + `rtlsdr.dll` 都载入了）；
- 具体是哪个进程、哪段代码在涨，**未定位/未验证**（缺当时的命令行：Security 4688 审计未开，WER 只留进程被测名与 PID，且 PID 已回收）。

---

## 7. 防线（已接入并验证）

改动只涉及 `tests/conftest.py`（测试基建层），未碰任何业务代码。

### 7.1 防线 1：Qt GUI 测试跨进程互斥

- 接入点：`pytest_collection_finish`（收集定稿、用例尚未执行时）判定本次会话是否包含直接 `import PySide6`/`QApplication` 的用例文件；是则取锁。
- 锁文件：`D:\mbdsdr\.locks\qt_test.lock`（默认 `<repo 父目录>\.locks\qt_test.lock`，可用 `MBDSDR_TEST_LOCK_PATH` 覆盖）。
- 机制：Windows `msvcrt.locking`（等价 LockFile 字节锁）/ POSIX `flock`。**进程崩溃/被杀时内核自动释放**，因此不需要"过期判定"，也不会留下死锁文件。
- 超时：默认 300 s（`MBDSDR_TEST_LOCK_TIMEOUT`）；超时以 `pytest.exit(..., returncode=2)` 明确报错，不会静默通过。
- 关闭开关：`MBDSDR_TEST_LOCK=0`（只在明确做并发压测时用）。
- 释放：`pytest_unconfigure` 里在 `TerminateProcess` 硬退出**之前**显式解锁。

**验证（实测）**

```
A 进程先持锁跑 test_ui_layout_locks.py；B 进程后启动（默认 300 s 超时）：
  A: 72 passed in 39.13s     stderr: [mbdsdr-lock] 已获得 Qt 测试互斥锁（等待 0.0s）
  B: 72 passed in 74.79s     ← 多出的 ~35s 即等锁，串行化生效

把 MBDSDR_TEST_LOCK_TIMEOUT=5 再测：
  B 退出码 = 2，耗时 5.8 s，输出:
  ! _pytest.outcomes.Exit: [mbdsdr-lock] 等待 5s 仍未拿到 Qt 测试互斥锁：D:\mbdsdr\.locks\qt_test.lock
```

### 7.2 防线 2：单进程提交内存看门狗

- 接入点：`pytest_runtest_setup`（每条用例边界），Windows 用 `GetProcessMemoryInfo.PrivateUsage`（= 提交量），POSIX 用 `/proc/self/status` 的 `VmRSS`。
- 阈值：默认 4096 MB（`MBDSDR_TEST_COMMIT_LIMIT_MB`，`0` 关闭）。越限立即 `pytest.exit(returncode=3)`，**带上越限用例 nodeid**。
- 目的：把"全机 commit 被单进程吃穿 → 同窗口所有 python.exe 随机 native 崩溃"降级成"一条明确可定位的用例失败"。

**验证（实测，把阈值压到 300 MB 强制触发）**

```
[mbdsdr-lock] 已获得 Qt 测试互斥锁（等待 0.0s）：D:\mbdsdr\.locks\qt_test.lock
! _pytest.outcomes.Exit: [mbdsdr-guard] 本进程提交内存 938 MB > 上限 300 MB，
  越限用例：tests/test_ui_layout_locks.py::test_panel_screenshot_matches_baseline[800x600-adsb_empty]
EXITCODE=3
```

### 7.3 未做/未验证

- 显卡/RHI 禁用方案（`QT_OPENGL=software` / `QSG_RHI_BACKEND` 等）**未验证**——本报告已排除显卡驱动为故障模块，故未做该实验，也就没有可写的"验证有效"。
- 崩溃用例精确定位**未完成**（缺命令行与 PDB 符号）。
- WER `ReportArchive` 目录被 ACL 拒绝，未读其 `Report.wer` 正文（未提权、未改权限）。

---

## 8. 建议的后续动作

1. 先按 `MBDSDR_TEST_COMMIT_LIMIT_MB` 默认值跑一段时间：它在用例边界带 nodeid 报越限，能直接抓到"哪条用例在涨内存"（本报告尚未抓到，全量套件峰值仅 1.9 GB）。
2. **长期驻留进程的资源观测**：WER 2004 里 17–28 GB 的 python.exe 不是 pytest 负载。§9 已把它定位到 `scripts/tool_param_fuzz.py` 的 `long_string` 用例；本条保留为通用观测建议（给桌面端/长跑脚本加周期性 `PrivateUsage` 日志，下次再出现 2004 时能直接对上 PID 与代码路径）。
3. 若要能事后定位命令行，可临时开启进程创建审计（Security 4688，含命令行）——本次因为没有它，才只能到"python.exe"粒度。
4. PySide6 绑定层空指针属上游缺陷：把 `0x1C91E`（`SignalManager::retrieveMetaObject`）+ 两个 minidump 上报 PySide6 issue，附 Qt 6.12.0 / PySide6 6.12.0 / Python 3.14.0。
5. 保留 `tests/conftest.py` 的退出期 `TerminateProcess` 规避不动——它是 exit 期 segfault 的唯一有效规避（phase64 结论），与本次运行期崩溃是两件事。

---

## 9. 17–28 GB 元凶：`tool_param_fuzz.py` 的 `long_string`（2026-11-05 追加）

> 本节只追加事实与复跑口径，不改前文判定。前文"巨无霸未定位"以本节为准更新为"已定位到 fuzz 负载，具体分配点待审计线"。

### 9.1 实测链（直接证据）

```
2026-11-05 01:49:13  Trae CN 会话经 PowerShell 拉起（执行器 id 41c2e899…）：
  python -u scripts\tool_param_fuzz.py --only rtty_decode --json %TEMP%\ov_rtty.json --no-write
01:52:23  python.exe PID 37016（父进程 17504 = powershell.exe）
  PrivateUsage = 19,093 MB；WorkingSet = 3,354 MB；全机可用物理内存 219 MB
01:54:10  该命令被停止（执行器 status=stopped，terminalReason=user_stop，时长 296.9 s）
输出：calls=6 tools=1 OK=5 HANG=1
  [HANG] rtty_decode long_string: 超过单次上限 60s
```

- `long_string` 用例 = 把 **10 万字符 `"A"`** 灌进文本类工具的字符串参数（`LONG_STR = 100_000`，`stress=True`；stress 档预算 = `--timeout × 6`，默认 10 s → 60 s）。
- 同脚本的历史运行（执行器只存 cwd/pid/时间，不存命令行；下列命令由输出预算与明细**反推**）：

| 运行窗口（实测） | 输出（实测） | 同窗口 WER 2004（实测） |
|---|---|---|
| 22:29:20–22:40:32（671.8 s，执行器 id 7c5b8280，wrapper pid 32508） | `calls=33 tools=4 OK=30 HANDLED=1 SLOW=1 HANG=1`；`feldhell_decode long_string` → `exception:MemoryError`；`psk31_decode long_string` 102.7 s（长输入预算 360 s）；`rtty_decode long_string` 超 360 s | **22:35:05 `python.exe(34888)` 28,739,452,928 B（28.7 GB）**，落在该运行区间内 |
| 00:11:10–00:14:32（201.8 s，执行器 id af27ceae，wrapper pid 38432） | `calls=33 tools=4 HANDLED=2 SLOW=1 HANG=1`（含 feldhell/psk31 长字符串 MemoryError） | **00:13:10 `python.exe(37796)` 24,715,304,960 B（24.7 GB）**，落在该运行区间内；同批次 `ov_longtxt.json` 于 00:13:17 落盘 |
| 20:00:50 / 20:05:00（无命令记录） | —（该时段没有 Trae 执行器目录） | `python.exe(2936)` 17,208,483,840 B + `python.exe(192)` 11,609,010,176 B（合计 28.8 GB）；随后同 PID 2936 涨到 28,398,059,520 B |

- 增长率实测（20:00:50 → 20:05:00，同 PID 2936）：`28,398,059,520 − 17,208,483,840 = 11,189,575,680 B / 250 s ≈ 44.8 MB/s`。
- 崩溃侧对照：本次导出 56 条 `python.exe` 事件 1000（含 3 天窗口外的早期记录；3 天窗口内为 51 条），进程 uptime 中位数 **0.4 min**、最长 **15.7 min**；5 个 2004 PID（41316/2936/192/34888/37796）**都不在** 1000 里——巨无霸结束方式是外部停止/被杀或自行退出，不是弹"应用程序错误"的那个进程。
- 22:35:06 还伴随一个 `VCRUNTIME140+0x11a89` 崩溃（PID 35608，uptime 1.2 min），是另一个短命进程，不是巨无霸。

### 9.2 推断与未验证（不要当结论）

- **超时注杀不保证停住分配**：`_call_with_timeout` 把工具调用放进 daemon 线程，超时用 `PyThreadState_SetAsyncExc` 注入 `_TimeoutAbort`。该机制只在 Python 字节码边界生效；若工具正处在 C/numpy 分配循环里，线程不会立刻停止（脚本在 `_abort_thread` 注释里也写明"注入是尽力而为"）。→ "HANG 之后内存仍继续涨"是**机制推断**，未用插桩证实。
- **历史 PID 的父子对应**：执行器只记录 wrapper PID（32508 / 38432 / 17504），2004 记录的是 python 子进程 PID；只有 01:49 那次是**直接**观测到子进程 37016。历史两次是"区间包含 + 同批次输出含 MemoryError + 无其它长跑 python"的**强推断**，不是 PID 直证。
- **20:00/20:05 那轮**：没有命令行与执行器记录，只能确认"python.exe 长跑且持续增长"，不能确认就是本脚本（**未验证**）。
- **具体分配点**（哪个解码器、哪一行按字符数线性开数组）**未定位**，由工具审计线处理（内存上限 + 输入长度前置拒绝）。
- **"OOM → PySide6 空指针"仍是推断**：23:01–23:55 的 13 次崩溃窗口内没有 2004 事件，不能写成"每次崩溃都由 OOM 触发"。
- conftest 的互斥锁**只覆盖 pytest 进程**，管不到 fuzz/长跑脚本；本类事故的防线必须在工具注册层（输入长度/内存上限），不在测试基建层。

### 9.3 复跑口径（供别人验证）

```bat
:: 最小档：单工具、默认预算（stress = 60 s）
python -u scripts\tool_param_fuzz.py --only rtty_decode --json %TEMP%\ov_rtty.json --no-write

:: 历史 28.7 GB 档：4 个文本类工具、stress 预算 360 s（整轮约 11 分钟）
python -u scripts\tool_param_fuzz.py --only feldhell_decode psk31_decode rtty_decode bell103_modulate_text --timeout 60 --json %TEMP%\ov_text.json --no-write
```

- 期望输出：`calls=33 tools=4 …`，明细含 `feldhell_decode long_string … exception:MemoryError`、`rtty_decode long_string … 超过单次上限 360s`。
- 期望资源曲线：最小档**实测 3 分钟内 PrivateUsage 涨到 19 GB**。
- **安全阀**：不要无上限跑（本机 commit 上限 38.1 GB，跑满会连累同机其它 python 进程崩溃）。建议给子进程套 4 GB 的 Windows Job Object 内存上限，或外部 RSS 采样到 8 GB 即 kill；复跑时也不要同时跑 pytest Qt 用例。
- 环境：`QT_QPA_PLATFORM=offscreen`、`MBDSDR_NULL_AUDIO=1`、`MBDSDR_NO_HW=1`、`MBDSDR_OFFLINE=1`；`MBDSDR_FUZZ_VERBOSE=1` 可看每工具进度。
- 已知登记：`docs/limitations-register.md` 已记"10 万字符文本在纯 Python 文本调制/解调器上超过 60s 预算"。
