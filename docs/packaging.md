# Windows 打包与运行时依赖（实测）

状态：2026-11-05，写作时仓库 HEAD `dcc71e9`（HEAD 在持续变动，本次验证的冻结包由
当时工作树构建），打包线所有改动已 claim。

## 0. 结论先行

1. **根因一句话**：PyInstaller 的 scipy hook 与 `collect_submodules('scipy')` 只按
   `pkgutil` 枚举模块，而 `scipy/_external` 是 **PEP 420 命名空间包（没有
   `__init__.py`）**，`pkgutil` 看不见它 → 整棵 `scipy/_external` 子树（含
   `array_api_compat/numpy/fft.py`）**没被打进包**；其中 `fft.py` 又由
   `numpy/__init__.py` 里的 `__import__(__package__ + ".fft")` 动态导入，静态分析
   同样看不见。**是 spec 的模块收集方式漏了文件，不是本机 scipy 残缺，也不是
   用户解压不全。**
2. 修法已落地：[MBDSDR.spec](../../desktop/MBDSDR.spec) 增加按文件系统遍历
   `scipy/_external` 的 `_collect_namespace_submodules()`；新构建
   `Analysis-00.toc` 里 `scipy._external.*` 共 **88 条**，`...numpy.fft` 在列。
3. 同时发现第二处同类缺陷：`mbdsdr_ai/astronomy/__init__.py` 用
   `importlib.spec_from_file_location` 按**文件路径**加载被同名包遮住的
   `../astronomy.py`，静态分析看不见 → 该文件没进包，冻结版启动报
   `cannot import name 'Observer' from 'mbdsdr_ai.astronomy'`。已作为 data 文件带入。
4. 冻结版最新构建（`d:\mbdsdr\.tmp\pyi-dist9`，18:03 构建）实测：进程存活 40s、
   窗口标题 `MBDSDR - AI 定义无线电`、hwnd `921316`；启动日志
   `[smoke] 私有模块导入自检 13/13 通过`。
5. `desktop/main.py` 的 catch-all 棘轮：**ERROR 6 / WARN 4 → 2/2 → 0/0**（最终
   `INFO 1`，基线 2/2），未顶破。全仓唯二越线项 `scripts/ui_interaction_audit.py`
   属 UX 线，不是本线改动。

## 1. 打包现状清点

| 项 | 位置 | 实测/说明 |
|---|---|---|
| PyInstaller spec | `desktop/MBDSDR.spec`（全仓唯一 `.spec`） | onedir、`console=False`、`noarchive=False`、UPX |
| 一键脚本 | `desktop/build_windows.bat` | `pip install pyinstaller` + `pyinstaller MBDSDR.spec --clean` |
| C++ 打包脚本 | `scripts/package_windows.ps1` | configure/build/install + `windeployqt` + `librtlsdr.dll` + 干净 PATH 冒烟 + zip/SHA256 |
| 构建目录（本次） | `d:\mbdsdr\.tmp\pyi-build9` / `pyi-dist9` | 全部在 D 盘；C 盘无产物 |
| C++ 构建树 | `cpp/build-local/`（`mbdsdr.exe`、`mbdsdr_mcp.exe`、Qt6*.dll、MinGW 运行库、FFmpeg、`librtlsdr.dll`） | 由 `package_windows.ps1` 用 `cmake --install` 收进 `dist/mbdsdr-<ver>-win64-portable` |
| C++ 内核产物（Python 侧） | 无独立 DLL/pyd 产出 | Python 桌面端不加载 C++ 内核；C++ 是独立 exe 线（`cpp/build-local/mbdsdr.exe`） |
| Qt 插件（Python 冻结版） | `_internal/PySide6/plugins/` | PyInstaller 的 PySide6 hook 自动带：`platforms`（qwindows/qdirect2d/qminimal/qoffscreen）、`imageformats` 10 个、`styles` 1 个。**不需要手工拷**，但 `qwindows.dll` 缺失会导致 Qt 直接 abort，启动自检已前置拦截 |
| Qt 插件（C++ 包） | `bin/platforms`、`imageformats` 等 | `windeployqt` 收集；脚本仅手工补 `platforms/qoffscreen.dll`（headless 冒烟用） |

## 2. 运行时依赖清单

“缺了会怎样”均为实测：Python 包用 `sys.meta_path` 屏蔽后导入对应模块测（探针见
`d:\mbdsdr\.tmp\clean_test\probe_optional.py`），冻结版用真删文件测（见 §4）。

### 2.1 Python 三方包（PyInstaller 包内）

| 依赖 | 来源 | 必需/可选 | 缺了会怎样（实测） |
|---|---|---|---|
| PySide6 | `desktop/requirements.txt` | **必需** | 启动即 `ImportError: No module named 'PySide6'`；`desktop.main_window` 导入失败 → 弹窗「无法加载界面依赖：…」并 `SystemExit(3)`，GUI 完全不可用 |
| numpy | 同上 | **必需** | `mbdsdr_ai.decoders` 导入 `ImportError: blocked:numpy`；DSP/频谱/解调全链路不可用，启动弹窗同上 |
| scipy | 同上 | **必需** | `mbdsdr_ai.agent` 导入 `ImportError: blocked:scipy`；启动弹窗；实时音频路径同时打印「ReceiveChain/AudioPlayer 导入失败，实时音频路径将降级」 |
| requests | 根 `requirements.txt` | **必需**（未做降级） | `mbdsdr_ai.model_manager` 顶层 `import requests`，缺了直接 ImportError → 启动失败；AI/LLM、TLE 下载、模型管理全不可用 |
| sgp4 | 根/desktop requirements | 可选（已降级） | `mbdsdr_ai.decoders` 仍可导入（`HAS_SGP4=False`）；卫星轨道/过境计算不可用 |
| skyfield | 同上 | 可选（已降级） | `mbdsdr_ai.new_spacetime` 仍可导入；高精度星历/过境功能降级 |
| pymap3d | 同上 | 可选（已降级） | 坐标转换类功能降级（懒导入） |
| pyserial | 同上 | 可选（已降级） | `mbdsdr_ai.gnss_monitor` 仍可导入；GNSS 串口不可用 |
| Pillow | 同上 | 可选（已降级） | `mbdsdr_ai.lrpt_render` 仍可导入；LRPT/APT/SSTV 出图不可用 |
| sounddevice | 同上 | 可选（已降级） | `mbdsdr_ai.audio_out` 仍可导入；无声（`AudioPlayer.available=False`），不影响 IQ 接收 |
| websockets | 同上 | 可选（已降级） | `mbdsdr_ai.mobile_server` 仍可导入；模拟服务器不可用 |
| websocket-client | 同上 | 可选（已降级） | ai-sdr Mini 硬件连接不可用（启动打印「无法导入 mbdsdr_mcp_client，硬件将处于未连接状态」） |
| zstandard / reedsolo / pysstv / astropy | 未入 requirements | 可选 | 对应解码/压缩路径降级或跳过；测试里表现为 skip |

`scipy` 及其私有子树另见 §3。

### 2.2 Qt 与插件

| 依赖 | 来源 | 必需/可选 | 缺了会怎样（实测） |
|---|---|---|---|
| `PySide6/plugins/platforms/qwindows.dll` | PyInstaller PySide6 hook | **必需** | Qt 在 Python 异常之前 `abort`，只能看到英文裸弹窗。已加启动前置检查：中文提示「缺少 Qt 平台插件 platforms/qwindows.dll（当前平台 windows）…」（见 §4.3） |
| Qt 其它插件（imageformats/styles/iconengines…） | 同上 | 可选 | 缺 imageformats → JPEG/PNG 图标/截图加载失败；styles → Windows 原生风格退化，不影响启动 |
| `msvcp140.dll` | PySide6 目录内随包 | **必需**（Qt 依赖） | 缺 → `DLL load failed while importing QtWidgets: 找不到指定的模块`（§4.3 实测原文） |
| `vcruntime140.dll` / `vcruntime140_1.dll` | PyInstaller 收集到 `_internal` | **必需**（bootloader/扩展模块依赖） | 缺 → 进程起不来（0xC0000135 类） |

**结论：Python 冻结版的 Qt 插件不需要手工带**；只有 C++ 包那条线依赖 `windeployqt`。

### 2.3 C++ 内核产物与系统 DLL（C++ 便携包线）

| 依赖 | 来源 | 必需/可选 | 缺了会怎样（实测） |
|---|---|---|---|
| `mbdsdr.exe` / `mbdsdr_mcp.exe` | `cpp/build-local` → `cmake --install` | 必需 | 不存在即无程序 |
| Qt6Core/Gui/Widgets/Network/Multimedia/Svg/… | `windeployqt` | 必需 | 缺任一 → Windows 加载器报「找不到指定的模块」，进程 0xC0000135，无自绘提示（C++ 线不在本次启动自检范围） |
| MinGW 运行库 `libgcc_s_seh-1.dll`、`libstdc++-6.dll`、`libwinpthread-1.dll` | `windeployqt` 同目录 | 必需 | 同上，0xC0000135 |
| FFmpeg（`avcodec-61` / `avformat-61` / `avutil-59` / `swresample-5` / `swscale-8`） | 构建树 | 必需（音频/录制） | 启动可过，音频相关功能加载失败 |
| `librtlsdr.dll` | 从 CMakeCache 的 `RTLSDR_LIBRARY` 同目录拷入 | **必需（链接期）** | 实测 `mbdsdr.exe --snapshot` 退出码 `0xC0000135`，无优雅降级；打包脚本前置校验，缺则 `throw` 拒绝出包 |
| `D3Dcompiler_47.dll` / `opengl32sw.dll` | `windeployqt` | 可选（渲染后端） | 缺 → 软件/硬件渲染后端缺失，可能黑屏 |

### 2.4 外部工具（非随包）

| 工具 | 必需/可选 | 缺了会怎样 |
|---|---|---|
| SoapySDR / SoapySDRUtil | 可选 | 启动不崩，但打印「未发现 SoapySDR：既无 Python 绑定…」。本机 RTL-SDR USB 需它或 pyrtlsdr；否则硬件枚举为空 |
| rtl_tcp 服务端 | 可选 | 远端共享棒不可用 |
| SatDump 目录 | 可选 | 相关解码测试/功能 skip |

## 3. 两类打包缺陷（证据）

### 3.1 scipy 私有命名空间包被漏收（P0 根因）

证伪/证实命令与结果：

```
> python -c "import scipy._external.array_api_compat.numpy.fft"
（无报错 → 本机 scipy 1.18.0 完整）

> python -c "import os,scipy; p=os.path.dirname(scipy.__file__); print(os.path.exists(os.path.join(p,'_external','__init__.py')))"
False                      ← 没有 __init__.py，pkgutil 枚举不到

旧构建 Analysis-00.toc：含 'scipy._external.array_api_compat.numpy'，
                        不含 'scipy._external.array_api_compat.numpy.fft'
warn-MBDSDR.txt：无任何 scipy._external 相关条目（不是被 excludes 排除，是根本没被发现）
```

修后（`pyi-build9`）：

```
scipy._external.* TOC 条目：88
scipy._external.array_api_compat.numpy.fft   YES
scipy._external.array_api_extra              YES
scipy._external.packaging_version            YES
scipy._external.cobyqa / pyprima             YES
scipy._lib._array_api / _util / deprecation  YES
```

### 3.2 `astronomy.py` 按路径动态加载

`mbdsdr_ai/astronomy/__init__.py` 用 `spec_from_file_location` 加载被同名
package 遮住的 `../astronomy.py`。冻结版里该文件不存在 →
`cannot import name 'Observer' from 'mbdsdr_ai.astronomy'`。已在 spec
`datas` 中把 `mbdsdr_ai/astronomy.py` 带进 `_internal/mbdsdr_ai/`。

## 4. 启动自检与三类缺件实测

自检实现（`desktop/main.py`）：
- `_RISKY_IMPORTS` 17 项私有/动态模块逐个 import（结果写日志，不阻塞启动）；
- GUI 导入失败 → `_import_error_text()` 判定「打包缺陷 / 解压不完整 / 未知」；
- Qt 平台插件前置检查（按 `QApplication` 实际请求的平台精确匹配）；
- 弹窗用 `ctypes.MessageBoxW`，**不依赖 Qt**；日志落点见 §5。

### 4.1 缺 scipy 单文件（解压不完整分支）

人为删掉 `_internal/scipy/_external/array_api_compat/numpy/fft.pyc`（noarchive 诊断构建）：

```
标题：MBDSDR 启动失败
无法加载界面依赖：No module named 'scipy._external.array_api_compat.numpy.fft'

No module named 'scipy._external.array_api_compat.numpy.fft'
安装包解压不完整：
  只缺 scipy._external.array_api_compat.numpy.fft，同包其它模块可以正常导入。
请整包重新解压/重新安装（不要只拷贝 exe，也不要删 _internal 里的文件）。

日志：D:\mbdsdr\logs\mbdsdr_startup.log
```

### 4.2 删整棵 `scipy/_external`（打包缺陷分支）

```
标题：MBDSDR 启动失败
无法加载界面依赖：No module named 'scipy._external'

No module named 'scipy._external'
这是打包缺陷，不是你的操作问题：
  安装包里整棵 scipy._external 子树都没打进去（同一子包里的兄弟模块也导入失败）。
请获取最新构建版本；如果这就是最新版，请把日志发给维护者。

日志：D:\mbdsdr\logs\mbdsdr_startup.log
```

判定逻辑不靠猜：`_packaging_verdict()` 用 `_PACKAGING_SIBLINGS` 里的兄弟模块
逐个 `import` 探针；有任一成功 → `incomplete`，全失败 → `defect`。

### 4.3 缺 Qt / DLL

删掉 `_internal/PySide6/QtWidgets.pyd` 依赖的 Qt DLL：

```
无法加载界面依赖：DLL load failed while importing QtWidgets: 找不到指定的模块。

DLL load failed while importing QtWidgets: 找不到指定的模块。
安装包不完整或缺少 Microsoft VC++ 运行库。请整包重新解压；仍失败则安装 VC++ 2015-2022 x64 运行库。
```

删掉 `_internal/PySide6/plugins/platforms/qwindows.dll`（保留 qoffscreen/qminimal）：

```
缺少 Qt 平台插件 platforms/qwindows.dll（当前平台 windows）。
常见原因：安装包没解压完整，或只拷贝了 MBDSDR.exe。
修复：重新解压完整安装包，_internal 目录必须与 exe 放在一起。
```

（早期版本只检查「platforms 目录里有任意一个平台插件」，qoffscreen 还在时会误放行，
Qt 随后自己 abort 成英文弹窗；已改为按实际请求平台精确匹配后复测通过。）

## 5. 四条现实约束（实际 vs 期望）

| 约束 | 实测行为 | 期望 | 差距 / 已修 |
|---|---|---|---|
| ① 机器没有 D 盘 | 启动日志：非 C 盘都不存在时落程序目录 `logs`，再落 `%PROGRAMDATA%\MBDSDR\logs`；审计日志 `default_audit_path()` 落 `<程序根>\logs\tx_audit.jsonl` 并告警「未检测到非 C 盘…请用设置项 tx_audit_path 指定持久位置」；录音默认目录：非 C 盘不可用 → `%LOCALAPPDATA%\MBDSDR\recordings` | 不得写 C 盘**用户目录**的日志；能看到明确告警 | 日志路径符合（程序目录 / ProgramData 都不是 `C:\Users`）；**录音**在只有 C 盘且程序目录只读时会落到 `%LOCALAPPDATA%`（设计如此，因为录音是用户数据，不是日志）。若程序恰好装在 `C:\Users\...` 下，程序目录日志会落在用户目录——**未修**，见待办 |
| ② 目标目录只读/无写权限 | 设置写盘失败：`设置保存失败（PermissionError）：[WinError 5] 拒绝访问。: '…\readonly_appdata\mbdsdr'`；录音写盘失败：`录音目录不可用（…\readonly_root\recordings）：[WinError 5] 拒绝访问。…请检查该磁盘/网盘是否已连接，或在「设置 → 存储与日志」重新选择目录。` | 明确说明「哪不可写、下一步怎么办」，不裸崩 | 提示已达预期；`_dir_writable()` 只用 stat 写位 + 受保护安装根判断，**不试写**（试写 `tempfile.mkstemp` 在 ACL deny-write 目录会卡住 >6s，实测 faulthandler 停在 `tempfile._mkstemp_inner`）。因此 ACL 级只读但非 Program Files 的目录，启动期不会自动回退，只在真正写盘时给可行动错误——已知取舍 |
| ③ 安装路径含中文 | 冻结版整包移到 `d:\mbdsdr\.tmp\clean_test\中文 安装 目录\MBDSDR 打包版` 后启动：窗口标题 `MBDSDR - AI 定义无线电`，hwnd `9440640`，`[smoke] 私有模块导入自检 13/13 通过`；源码副本在中文目录下运行只打印 SoapySDR 缺失告警，无编码异常 | 中文路径可用 | 无差距 |
| ④ 用户目录被重定向 | `APPDATA` 指到 `D:\mbdsdr\.tmp\clean_test\appdata_redirected` 后启动冻结版：该目录里出现 `desktop_settings.json.corrupt-20261105-175724`（损坏配置被备份，证明读的是重定向目录）；同时 `C:\Users\xxhzb\AppData\Roaming\mbdsdr\desktop_settings.json` 的 mtime 保持 `17:53:52` 不变 | 跟随重定向，不写真实用户目录 | 无差距 |

此外修掉一条越线：打包版无控制台时 `sys.stdout/stderr` 为 `None`，旧实现把
stderr 兜底日志写到 `~/.config/MBDSDR/mbdsdr_desktop.log`（C 盘用户目录），
实测旧构建 17:50 仍在写该文件。现改为与启动自检共用 `_startup_log_path()`：

```
startup_log_normal   D:\mbdsdr\logs\mbdsdr_startup.log
startup_log_no_D     D:\mbdsdr\MBDSDR\logs\mbdsdr_startup.log   （无 D 盘视图）
stdio_sink           D:\mbdsdr\logs\mbdsdr_startup.log
```

新构建实测：`C:\Users\xxhzb\.config\MBDSDR\mbdsdr_desktop.log` mtime 停在
`17:50:11`（旧构建时间），新构建的 stderr 内容出现在 `D:\mbdsdr\logs\mbdsdr_startup.log`。

## 6. 私有子包同类风险与冒烟清单

启动自检 `_RISKY_IMPORTS` 当前 13 项（每次启动跑一遍，冻结版新构建日志 13/13 通过）；
下表另含 4 项源码/TOC 核验项（cobyqa、pyprima、deprecation、_multiarray_umath 中的
二进制形态），合计 17 项在源码环境逐个导入通过。

| 模块 | 风险类型 | 构建 TOC | 源码 import | 冻结版自检 |
|---|---|---|---|---|
| scipy._external.array_api_compat.numpy.fft | 动态 `__import__` + PEP420 | YES | OK | OK |
| scipy._external.array_api_compat.numpy.linalg | 同上 | YES | OK | OK |
| scipy._external.array_api_compat.common | 同上 | YES | OK | OK |
| scipy._external.array_api_extra | PEP420 | YES | OK | OK |
| scipy._external.packaging_version | PEP420 | YES | OK | OK |
| scipy._external.cobyqa / pyprima | PEP420 子包 | YES | OK | 未入自检 |
| scipy._lib._array_api / _util | 私有子包，scipy hook 覆盖不全 | YES | OK | OK |
| scipy._lib.deprecation / _ccallback / _uarray | 同上 | YES | OK | 未入自检 |
| scipy.signal / special / fft / interpolate / optimize | 公开入口，内部依赖上表 | YES | OK | OK |
| numpy._core.multiarray | 私有 C 扩展 | YES | OK | 未入自检 |
| numpy._core._multiarray_umath | 私有 C 扩展（以 `.pyd` 收进 `_internal/numpy/_core/`） | TOC 名称匹配不到（二进制条目） | OK | OK（`_internal` 实测存在 3.7MB pyd） |
| numpy.fft / numpy.linalg | 公开入口 | YES | OK | 未入自检 |

冒烟结果：
- 源码环境 17 项全部导入成功（`OK 17 / 17`）；
- 冻结版新构建启动日志 `[smoke] 私有模块导入自检 13/13 通过`。

同类风险仍存在但**未逐个**纳入启动自检的：`scipy.io._*`、`scipy.sparse._*`、
`scipy.ndimage._*` 这类“公开包的私有实现模块”，它们由 scipy hook 正常收集；
只有 PEP 420 命名空间子树（`_external`）需要文件系统遍历。新增 scipy 版本时，
以 `<scipy>/_external` 是否存在 `__init__.py` 作为「是否需要遍历收集」的判据。

## 7. 待办与已知限制

1. **测试未跑**：`python -m pytest tests/test_settings_schema.py` 被其它会话持有的
   Qt 测试互斥锁挡住（`MBDSDR_TEST_LOCK_TIMEOUT=20` 实测 `no tests ran`，exit 2）。
   未用 `MBDSDR_TEST_LOCK=0` 绕过（会顶破内存预算纪律）。需等锁释放后补跑。
2. 4 个扩展项（cobyqa/pyprima、deprecation/_ccallback/_uarray、numpy._core.multiarray、
   numpy.fft/linalg）只在源码与 TOC 层核验，未纳入冻结版启动自检；要覆盖需把它们
   加进 `_RISKY_IMPORTS` 再重建。
3. 程序装在 `C:\Users\...` 下且只有 C 盘时，程序目录日志仍会落在用户目录：
   `_startup_log_path()` 未排除「程序目录位于用户主目录」的情况。可加判据
   （路径前缀 `%USERPROFILE%` 时跳过程序目录，只留 `%PROGRAMDATA%`），但会导致
   非管理员用户完全没有日志文件，需要先决定取舍。
4. `_dir_writable()` 探测不了 ACL 级只读（非 Program Files）；启动期不回退，
   写盘时才报错。要做真实探测就得接受写入超时的风险（已实测会卡 6s+）。
5. C++ 线缺 `librtlsdr.dll` 是链接期硬依赖，退出码 `0xC0000135`，**没有**自绘提示；
   目前只有打包脚本前置校验。若要做到「缺件说人话」，需要 C++ 启动期自检。
6. `scripts/ui_interaction_audit.py` 棘轮越线（WARN 7 > 基线 3）属 UX 线，未处置。

## 8. 本轮改动的文件

- `desktop/MBDSDR.spec`：scipy 命名空间子树收集 + `astronomy.py` 数据文件。
- `desktop/main.py`：启动自检、缺件分类文案、Qt 插件前置检查、日志落点策略、
  stdio 兜底改到非 C 盘用户目录；catch-all 收窄到 `ImportError`/`OSError` 等。
- `desktop/settings.py`：录音默认目录回退（受保护安装根/只读）+ 写盘错误可见。
- `docs/packaging.md`：本文。
