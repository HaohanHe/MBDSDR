# 上游再整合 + 三端全量复验 + 写工具人工门（phase64）

> 日期：2026-10-18  作者：MBDSDR 工程轮
> HEAD：MBDSDR @ 见 §7 清理后的提交链
> 上游基线：origin/main @ c6217d9
> 性质：交付/复验报告。上游二次推送后的整合、环境补齐、六个缺陷修复、
> 一次测试事故链的根因与修复、三端测试结果与提交历史清理。
> 不吹不贬；数字均为当日实测快照，非能力标语。

---

## 0. 本轮触发点

用户二次推送上游（`fix(test): vna_onramp 握手脚本——onramp 自己的刷新只发
cal 不发 tcxo`，c6217d9）。任务：重新拉取、与本地既有修复整合、全量复验
三端、如实交代未覆盖项。

---

## 1. 上游整合对照

浅拉取（`git fetch --depth=1 origin main`，直连 GitHub 7 分钟白下后改浅拉
5 分钟成功）后逐项比对：

| 问题 | 上游 c6217d9 | 本地既有修复 | 处置 |
|---|---|---|---|
| Qt 6.4 移植（QString::assign / QTimeZone::utc / QCOMPARE 同型化） | 已修 | 有冗余修复 | **弃用本地冗余**，取上游 |
| vna_onramp 握手脚本 tcxo 步 | 已修（即本提交主题） | 有冗余修复 | 弃用本地冗余 |
| TDR 夹具相位约定（因果时延=负相位） | 已修 | 独立诊断出同一修法 | 弃用本地冗余，**修法与上游一致**（互相验证） |
| rtl_sdr_source 测试缝三态化 | 未触及 | 独有 | **保留**（d030043） |
| spectrum_display traceRectForTest + 轮询等完成信号 | 未触及 | 独有 | **保留**（d030043） |
| test_checkpoint_writer 轮询条件 | 未触及 | 独有 | **保留**（d030043） |

本地完整原始修复集存证于分支 `local-fixes-2026-10-18` @ eae3c4c（不删，
可追溯"我们先诊断出来过"的轨迹）。上游未覆盖的三项缝修复提交为 d030043。

---

## 2. 环境补齐

| 项 | 状态 | 说明 |
|---|---|---|
| Python 3.12.8 | 自建 `/opt/python312` | CI 口径 `python-version: '3.12'`；`sdr_tools.py:5092` 用 PEP 701 f-string，3.11 下 86 个收集错误（基线树即有，非合并引入）。python.org 直连慢且易截断，`curl -C -` 续传补全 27MB 源码后编译 |
| 依赖 | 清华镜像 16 秒装完 | PyPI 直连 ~57KB/s 不可用；三份 requirements + extras 装入 3.12 |
| Qt6/CMake/g++ | 已有 | C++ 侧构建通过 |
| Flutter >=3.9 | **未安装** | mobile `pubspec.yaml` 要求 `>=3.9.0`；沙箱带宽下 3.47.5（1.58GB）下载 ~75-280KB/s 不可行。上游 mobile 改动仅 2 个测试文件各 +3 行 mkdir，无产品代码 |

---

## 3. C++ 侧复验

- 构建：通过（`build_upstream.log`）。
- **ctest 152/152 全绿**（`ctest_upstream.log`）；e2e_smoke 为无硬件诚实
  SKIP，不计失败。
- 本会话无新的 C++ 产品改动。

---

## 4. Python 侧复验

运行口径对齐 CI：五个 pytest 根目录 + `QT_QPA_PLATFORM=offscreen` +
`timeout -k 20 900` 每根防卡死。

| 根目录 | 结果（2026-10-18 实测） |
|---|---|
| experiments/tests | **34 passed / 11.33s** |
| mbdsdr_ai/tests | **901 passed / 5 skipped**（含 §5.1 门回归 11 例；此前 894/12，装 astropy 后 7 个 skip 解锁且全过） |
| tools/onboarding | **36 passed / 4 skipped** |
| tools/hw_selfcheck | 通过 |
| tests/ | 见 §4.1、§4.2、§4.3 |

### 4.3 tests/ 根 run 4：首次完整跑完 + 六个失败全定位

两个挂起根因修复后，tests/ 根**首次完整跑完**（run 4：
1800s 预算，**755s / 12:35 跑完 1490 项：1484 passed /
6 failed / 4 skipped / 6 subtests passed**）。六个失败分三组，
全部定位且有修复：

| 失败 | 根因 | 处置 |
|---|---|---|
| `test_gk2a_jp2.py::test_jp2_roundtrip_uint16` | Pillow 13 typemap 收紧，`Image.fromarray(mode="I;16")` 抛 `Invalid mode for data type`；产品 `gk2a_lrit.py:1081` 与测试 helper 同模式中招 | 两处改 `Image.frombuffer("I;16", (w,h), arr.astype("<u2").tobytes())`，位深语义不变（提交 a752e11） |
| `test_solar_system_ephemeris.py` 4 例 | 环境缺 astropy：`_EphemerisBackend` 回退链断在 `'none'`（期望 `'vsop87_astropy'`） | 装 astropy 8.0.2；四例复跑转绿 |
| `test_phase4_gap_cleanup.py::test_d9_subagent_description_narrowed` | **上游自身债**：测试期望 description 窄化（3 类 + 通用 tool_name 路径 + `NotImplementedError`），c6217d9 干净树上同样 AssertionError；代码行为（未列出类型 `allowed_tools=None`）本就支持该契约，仅文案停留在"等 7 类"宣传 | 收窄 `subagent_create` description/参数文案，不改运行时（提交 9eb3527 附带） |

### 4.4 退出期 segfault：定位与规避（PySide6 6.12 + CPython 3.12）

run 4/5 全量跑完后解释器退出必崩（exit 139/134，稳定复现）：
`QObject: shared QObject was deleted directly` +
`malloc_consolidate(): unaligned fastbin chunk detected`。

**定位路径**（全部测试结论产出后才崩，不掩盖任何测试结果）：
文件序二分 92→46→35 个均崩、23 个不崩 → 36-41 区间崩 →
单文件 `tests/test_desktop_ui_responsiveness.py` 复现（3.5s）→
单测试方法复现 → **gdb C 栈**：

```
Py_FinalizeEx -> atexit_callfuncs -> PySide::runCleanupFunctions
  -> PySide::destroyQCoreApplication
  -> Shiboken::BindingManager::visitAllPyObjects
  -> ~QObject  -> SIGSEGV
```

**根因**：该测试文件模块级创建 `QApplication`（`app =
QApplication.instance() or QApplication(sys.argv)`），pytest 退出时
解释器 finalize，PySide6 的 atexit 清理强拆残留 QObject，撞上已被删
对象。**上游 c6217d9 干净树同样中招**；同操作裸脚本正常——pytest
模块级 app 模式触发。文件内 atexit 主动 gc/release 均无效（引用被
pytest 侧持有）。

**规避**（提交 0fb382d，非产品修复）：`tests/conftest.py`
`pytest_sessionfinish(trylast)` 在 terminal summary 打印后按
`session.testsfailed` 真实结果 `os._exit(0/1)`，跳过
Py_FinalizeEx 的 PySide 强拆。桌面端产品退出路径由 `main()`
控制，不受此影响；真正修复属 PySide6 上游（或桌面端退出期
显式销毁 QApplication 后 import 层清理重排）。

**验证**：元凶文件 20 passed / exit=0；失败场景 exit=1 正确
传播（不掩盖失败）；其他文件无副作用；全量复跑见 §8.1。

### 4.1 tests/ 根挂起 #1：TLE 失败重试风暴

**现象**：`-q` 全根跑在 64%+ 处静默挂起 30+ 分钟，0% CPU；
`-v` 重跑定格在 `test_full_integration.py::test_tool_callability`
（42%，633 例已过）。

**取证**：
- `/proc/<pid>`：94 线程、`do_poll`、utime 仅 61s——阻塞在 poll，非死循环；
- py-spy dump：栈定格
  `test_tool_callability -> tool_registry.call -> _satellite_predict_all
  -> predict_all_passes -> predict_satellite_pass ->
  compute_satellite_position -> _resolve_tle -> fetch_tle ->
  urllib urlopen -> socket.connect`；
- 受控实验（`/tmp/net_probe.py`）：本沙箱对 celestrak 的 connect 15.0s 后
  `TimeoutError`、urlopen 15.0s 后 `URLError`——**15s 超时本身生效**，
  排除"单连接死锁"。

**根因（真实产品缺陷）**：`predict_satellite_pass`
（`new_spacetime.py:775-780`）以 30s 步长扫 24h 窗口 = **2880 步**，每步
都经 `_resolve_tle` 调 `fetch_tle`。`fetch_tle`（`orbit.py:78-100`）只缓存
**成功**拉取；失败被 `_resolve_tle` 静默吞掉回退内置 TLE，**失败不进任何
缓存**。外网不可达时每步重试一次网络、每次等满 15s 超时：单星
2880×15s≈12 小时，六星串联 ≈72 小时——工具调用实际不可用，测试表现为
"永久挂起"。

**修复**（`decoders.py:75-106`，提交 7c0f533）：`_resolve_tle` 增加 5 分钟
失败负缓存冷却（`_TLE_FAIL_UNTIL` / `_TLE_FAIL_COOLDOWN_S`）。冷却期内
直接回退内置 TLE，source 仍如实标 `"builtin-fallback"`——失败缓存只省
重试，不伪造联网来源；成功拉取即清冷却，到期自动恢复重试。

**回归测试**（`test_phase58_multisat.py` 新增 `TestTleFetchCooldown` 三例）：
失败后多次解析只真正尝试一次且如实回退（钉死 source 与 (line1, line2)）；
冷却到期恢复重试；成功后清冷却。未删断言、未放宽比较。

### 4.2 tests/ 根挂起 #2：Qt app 级样式表二次复杂度

**现象**：TLE 修复后重跑，越过原挂起点推进到 90%+ 后再次静默卡死
（15 分钟无输出、CPU 走低）。

**取证**：
- gdb 抓 C 栈：`setPalette -> propagate -> QApplication::notify` 事件
  风暴，应用级样式/调色板在向**全部**控件传播；
- 最小复现（`/tmp/qt_polish_probe.py`）：单窗口应用主题 0.4s（不卡）；
  循环构造 20 个 MainWindow 后构造耗时 1.65s → 12s → 33s → 卡死——
  **与窗口数超线性**，坐实累积效应。

**根因（真实产品缺陷）**：`QApplication.setStyleSheet/setPalette` 会重
抛光**全部**既有控件（O(全部控件)）。desktop 端每个新 MainWindow 构造
都重新应用一次主题，N 个窗口累计 O(N²)。长跑套件（开关触摸适配、
反复构造窗口）因此越跑越慢直至卡死。

**修复**（未提交修复，见 §7 清理后随 `desktop:` 提交落地）：
- `desktop/themes.py` `Theme.apply`：palette/font/stylesheet 三者与当前
  应用状态逐一相等即整体跳过（三者 `==` 比较均可靠；比较异常的平台
  照常应用，不改变行为）；
- `desktop/main_window.py` `_apply_touch_stylesheet`：目标串与
  `app.styleSheet()` 相等则跳过 setStyleSheet。

**验证**：构造 ≤1.4s、toggle 有界（~9s，20 窗口量级），
`test_touch_adaptation` **16 passed / 3.69s**。

---

## 5. 缺陷修复明细（本轮共六个 + 一次测试事故链）

| # | 缺陷 | 文件:行 | 性质 | 验证 |
|---|---|---|---|---|
| 1 | onboarding 参数范围校验被"未找到命令"掩盖 | `tools/onboarding/onboard.py:237-259` | 产品：无 rtl_sdr 机器上越界参数也必须报范围错（TestCaptureNoRTLSDR 契约） | onboarding 36/4 |
| 2 | coax_loss_s11 干净数据误报振铃 | `mbdsdr_ai/vna_dsp.py:216-227` | 产品：lstsq 去趋势残差是机器精度浮点残影（1.7e-16）但谱域相干集中，两道相对阈值全失守，跨 numpy/LAPACK 隐性平台依赖 | test_vna_dsp 全过 |
| 3 | skill registry 默认 user 根测试只 patch USERPROFILE | `mbdsdr_ai/tests/test_skill_registry.py:137-141,239-244` | 测试 hermetic 机制：POSIX expanduser 只认 HOME，Linux 下重定向静默失效、断言打真实家目录 | skill_registry 全过 |
| 4 | TLE 失败无负缓存，预测循环重试风暴 | `mbdsdr_ai/decoders.py:75-106` | 产品：外网不可达时单星预测 ≈12 小时（详见 §4.1） | 883 passed + 新测试 3 例 |
| 5 | Qt app 级样式表应用无幂等守卫，O(N²) 卡死 | `desktop/themes.py`、`desktop/main_window.py` | 产品：见 §4.2 | touch 16 passed |
| 6 | code_editor 四个写工具无 arm_manual 人工门 | `mbdsdr_ai/agent.py:2562-2730` | 产品：违反 AGENTS.md §3，详见 §5.1 | 门回归 11 例 |

提交：d030043（三项缝修复）、145c539（#1/#2/#3）、7c0f533（#4）；
#5/#6 及 §4.3 三个复验债在 §7 历史清理后分笔重落
（15684a1 desktop、a752e11 gk2a、9eb3527 门+文案）。

### 5.1 #6 详解：缺门如何被无人值守测试利用（README 事故链）

AGENTS.md §3 要求写工具一律走 arm_manual 人工门，但
`_register_code_editor_tools` 的四个写工具（modify/modify_section/
git_commit/rollback）**一个门都没有**——仓库自己的审计文档
`docs/audit_r2`（38:21、36:81）早已登记该缺陷，本轮坐实。

`test_tool_callability`（`tests/test_full_integration.py:1229`）对全部
required string 参数统一喂 `"README.md"`（仓库内真实 tracked 文件）做
dry-call。于是：

1. `code_modify_file` 收 `{file_path: "README.md"}`，`new_content` 缺省
   空串 → **真实清空仓库 README**（15166 → 0 字节）；
2. `code_git_commit` 收 `{message: "README.md"}`，`files=None` →
   `git add -A` + commit → **连未跟踪文件一起自动提交**（228e15b）；
3. 恢复 README 后套件再跑，同样路径再自动提交一次（a409a55）。

即：**无人值守测试套件在无任何人工的情况下真实修改并提交了仓库**。
审计文档的预言（缺门 → 幻觉参数/误触即真实写）完全兑现。

**修复**（三处，缺一不可）：
- `mbdsdr_ai/agent.py:2573-2580` `_arm_gate`：`arm_manual is not True`
  即返回 gated 信封（success=False, data={"gated": True}），不执行、
  不伪造成功；四个写工具 schema 把 `arm_manual` 加进 required 并在
  description 声明门语义；
- `tests/test_full_integration.py:1241` dry-call 通用字符串值改
  `/tmp/mbdsdr_drycall_probe.txt`（tmp 小文件，读写均不碰仓库）；
- 新增 `mbdsdr_ai/tests/test_code_editor_gate.py` 11 例：schema 声明、
  缺门被 registry 预检拒、显式 false 返回 gated 信封、armed 穿过门后
  仍受 editor containment 约束（越界路径被拒）、CodeEditor 直调机械
  正常（tmp 项目根内写+备份+回滚）。

### 5.2 插曲：机械喂值再次穿门（623299e）

门上线后 tests/ 根复跑，工作区仍被自动 `git add -A` 提交了一次
（623299e，message 恰为 `/tmp/mbdsdr_drycall_probe.txt`）。根因：
dry-call 的参数构造器对**所有 boolean 参数按类型喂 `True`**
（`tests/test_full_integration.py:1250`）——`arm_manual` 进 required 后
被机械喂真，等于"机器替人表达了手动确认"。

**修复**：dry-call 对 `arm_manual` 特判喂 `False`（无人值守场景不存在
人工，写工具被门拒绝才是正确预期），并在 `test_tool_callability`
增加门存在性断言（四个写工具缺 `arm_manual` required 即记失败）。
最小验证：四工具喂 `arm_manual=False` 全部返回 gated，未产生任何 git
操作。此局限（按类型喂值的自动调用方总能武装门）如实登记：门防的是
幻觉参数与误触，不防"自动武装"。

### 5.3 关于 #2 的数值档案

512 点精确线性数据（`0.5-0.01j - 1e-4·arange`）实测：
`max|resid|≈1.7e-16`；谱集中度（峰/中位）≈60；`spec[k] >= abs_peak` 同时
过关。即 prominence 与 peak_ratio 两道**相对**阈值都按"残差即信号"标定，
对浮点残影集体失守。修复为信号尺度绝对噪声底（`1e-9·max|x|`），真实振铃
（测试波纹幅 0.2）远高于此量级，检出行为不变。

---

## 6. 附带发现：测试产物注释与 git 跟踪状态不一致（且上游即如此）

`tests/test_fengyun.py:191-195` 每次运行把 `fy3_pass_curve.npy` 写进
`mbdsdr_ai/artifacts/`，注释称"不进 git"，但该文件**实际被 git 跟踪**——
`git ls-tree c6217d9 mbdsdr_ai/artifacts/` 显示上游 HEAD 即含
`fy3_pass_curve.npy`（blob 7de38a0）。即测试产物入库是**上游既有状态**，
非本地合并引入；本轮多次被测试写脏后 `git checkout --` 还原。
**未处置项**：要么 `.gitignore` + `git rm --cached`，要么把曲线改写真
位置；留待上游作者定夺（涉及上游 tracked 文件，不动）。

---

## 7. 提交历史清理

事故链产生三笔 junk 提交（内容均已找回，无实质丢失）：

| 提交 | 内容 | 处置 |
|---|---|---|
| 228e15b | README 被 dry-call 清空后的版本 | drop |
| a409a55 | README 恢复版 + 报告草稿 | drop |
| 623299e | 测试自动 `git add -A`：含本轮全部修复文件，message 为 `/tmp/...` 路径 | drop（内容随 §5 修复重提） |

存证：分支 `incident-2026-10-18` @ a409a55（junk 链）、
`local-fixes-2026-10-18` @ eae3c4c（原始修复集）保留不删。

处置方式：`git reset --mixed 7c0f533`（工作区全部保留）后按逻辑分笔
重提，实际落地链（master）：

```
0fb382d test: conftest 规避 PySide6 6.12 + CPython 3.12 退出期 segfault
4624cd8 docs: AGENTS.md 基线上档（901/5）
9eb3527 fix(ai): code_editor 写工具补 arm_manual 人工门 + dry-call hermetic 化
a752e11 fix(gk2a): Pillow 13 typemap 收紧后改 frombuffer 保 I;16 位深
15684a1 fix(desktop): 主题应用幂等化——app 级样式表不再重复重抛光全部控件
7c0f533 local: TLE 联网失败负缓存冷却——预测循环不再每步重试网络
145c539 local: 三个验证债修复
d030043 local: 上游未覆盖的三项测试缝修复
c6217d9 fix(test): vna_onramp 握手脚本……（上游 HEAD）
```

**教训归档**：写工具无人工门 + 无人值守 dry-call = 套件可真实改仓库。
两道修复（产品门 + 测试 hermetic 值）必须同时成立，只有其一时
机械喂值仍可穿门（§5.2）。

---

## 8. 结果与诚实边界

### 8.1 三端结果总表（2026-10-18）

| 端 | 结果 |
|---|---|
| C++ | ctest **152/152**（e2e_smoke 诚实 SKIP） |
| Python | mbdsdr_ai/tests **901 passed / 5 skipped**；onboarding **36/4**；experiments/tests **34 passed**；tools/hw_selfcheck **14 passed**；tests/ 根见下行 |
| tests/ 根 | 修复后完整复跑：**1490 passed / 0 failed / 4 skipped / 6 subtests passed，750.96s**（run 5，一次跑完；run 4 为 1484 passed/6 failed，六个失败的修复见 §4.3） |
| Flutter | **未验证**——沙箱无 >=3.9 SDK 且下载不可行（§2） |

### 8.2 诚实未完成项

- Flutter analyze/test 未跑（环境所限，非代码结论）；
- §4.4 的退出期 segfault 是**规避**（conftest 主动退出）不是修复，
  根因（PySide6 6.12 atexit 清理与 pytest 模块级 QApplication 的
  冲突）留给上游；桌面端产品路径不受影响；
- tests/ 根按 CI 注释本身"顺序相关、偶发墙钟超时"，单次通过不等于稳定；
- §6 的 artifacts 污染未处置（上游 tracked，不动）；
- 未跑真实硬件（无 rtl_sdr / 无 VNA），所有硬件相关结论均为诚实空态；
- 本机 `~/.mbdsdr/tle_cache` 有沙箱早前网络可用时写入的缓存（仓库外，
  不影响 git）；联网测试（如 `test_real_tle_fresh_or_skip`）在缓存新鲜时
  走磁盘、过期才触网，属既有设计；
- §5.2 的"自动武装"局限：按 schema 类型喂值的自动调用方可穿过
  arm_manual 门，门不防此类调用方。
