# Windows 打包与运行时依赖（实测）

> 本篇结论未经 commit 取证，引用前须复核（判据见 `scripts/verify_claims.py`；补齐四元组 `<结论> — <hash> — <path>:<line> — <改动后字面量>` 后可摘除本行）。

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
4. 冻结版最终构建（`d:\mbdsdr\.tmp\pyi-dist11`）实测：进程存活 40s、窗口标题
   `MBDSDR - AI 定义无线电`、hwnd `25495906`；启动日志
   `[smoke] 私有模块导入自检 13/13 通过`；`C:\Users\xxhzb\.config\MBDSDR\mbdsdr_desktop.log`
   mtime 停在旧构建的 `17:50:11`（新构建不再写 C 盘用户目录）。
5. 启动期新增三类自检：运行库 DLL（缺 `librtlsdr.dll` 弹「缺少运行库：librtlsdr.dll」，
   不摊 `0xC0000135`）、录音目录后台可写探测（失败进状态栏 + 日志，不阻塞启动）、
   日志落点策略（`%PROGRAMDATA%` 兜底，全不可写进内存缓冲并如实说明）。
6. `desktop/main.py` 的 catch-all 棘轮：**ERROR 6 / WARN 4 → 2/2 → 0/0**（最终
   `INFO 1`，基线 2/2），未顶破。本轮全仓越线项 `scripts/ui_interaction_audit.py`
   与 `tests/test_ui_layout_locks.py` 均属其它线（UX / 测试锁），不是本线改动。

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
| ① 机器没有 D 盘 | 启动日志按裁定顺序：环境变量 → 非 C 盘盘符 → 安装根 `logs`（实际可创建）→ `%PROGRAMDATA%\MBDSDR\logs`；全不可写则进内存环形缓冲（200 行）。审计日志 `default_audit_path()` 落 `<程序根>\logs\tx_audit.jsonl` 并告警；录音默认目录：非 C 盘不可用 → `%LOCALAPPDATA%\MBDSDR\recordings` | 绝不写 C 盘用户目录的日志；能看到明确告警 | 日志已按裁定落地，`%LOCALAPPDATA%` / `~/.config` / `%TEMP%` 全部不在候选内。**录音**是用户数据不是日志，仍按原设计在只有 C 盘时落 `%LOCALAPPDATA%` |
| ② 目标目录只读/无写权限 | 设置写盘失败：`设置保存失败（PermissionError）：[WinError 5] 拒绝访问。: '…\readonly_appdata\mbdsdr'`；录音写盘失败：`录音目录不可用（…\readonly_root\recordings）：[WinError 5] 拒绝访问。…`。启动期改为后台线程试写 0 字节文件（不阻塞主线程），失败进状态栏 + 日志（见 §5.1） | 明确说明「哪不可写、下一步怎么办」，不裸崩 | 提示达预期；启动期不再用 `tempfile.mkstemp`（ACL deny-write 目录实测会卡 >6s） |
| ③ 安装路径含中文 | 冻结版整包移到 `d:\mbdsdr\.tmp\clean_test\中文 安装 目录\MBDSDR 打包版` 后启动：窗口标题 `MBDSDR - AI 定义无线电`，hwnd `9440640`，`[smoke] 私有模块导入自检 13/13 通过`；源码副本在中文目录下运行只打印 SoapySDR 缺失告警，无编码异常 | 中文路径可用 | 无差距 |
| ④ 用户目录被重定向 | `APPDATA` 指到 `D:\mbdsdr\.tmp\clean_test\appdata_redirected` 后启动冻结版：该目录里出现 `desktop_settings.json.corrupt-20261105-175724`（损坏配置被备份，证明读的是重定向目录）；同时 `C:\Users\xxhzb\AppData\Roaming\mbdsdr\desktop_settings.json` 的 mtime 保持 `17:53:52` 不变 | 跟随重定向，不写真实用户目录 | 无差距 |

日志落点策略改动（`desktop/startup_paths.py` 是唯一实现，`main.py` 只接线）：

```
顺序：MBDSDR_STARTUP_LOG → D:/E:/... 非 C 盘 → 安装根 logs → %PROGRAMDATA%\MBDSDR\logs
全部不可写：startup_log_path() 返回 ""，内容进内存 deque(maxlen=200)，
            错误出口打印「本次无持久日志（原因：无可用写入位置）。」+ 本次会话日志
```

断言（`tests/test_startup_log_policy.py`，7 项全过）：

```
$ python -m pytest tests/test_startup_log_policy.py -q
.......                                                                  [100%]
7 passed in 0.26s
```

覆盖：模拟无 D 盘 + 安装根不可写 → 落点字符串必须等于
`%PROGRAMDATA%\MBDSDR\logs\mbdsdr_startup.log`，且不含 `\users\` / `appdata`；
全部不可写 → 空串 + 内存缓冲 + 固定说明；`%APPDATA%` mtime 前后不变；
候选列表不出现用户目录。

### 5.1 异步可写探测（不阻塞启动）

`probe_dir_writable_async()` 起 daemon 线程试写 0 字节文件、写完即删，失败经 Qt
信号切回主线程显示在状态栏（20 秒），同时写启动日志。主线程不等结果，启动不受
ACL deny-write 目录影响。实测日志：

```
[record] 录音目录不可写（Z:\nope）：[WinError 3] 系统找不到指定的路径。: 'Z:\\'。
         请在「设置 → 存储与日志」改到可写目录。
```

（此条曾由冻结版实测：本机配置残留测试写的 `Z:\nope`，探测在后台拿到错误并回报。
该残留已从 `%APPDATA%\mbdsdr\desktop_settings.json` 清回默认 `record_dir=""`，
重启冻结版后新增日志里不再出现 `[record]` 行；录音目录指向拔掉的盘/非法盘符时
仍按同一文案每次启动报一次，这是**如实提示**，不做静默回落。）

### 5.2 运行期 DLL 存在性检查

`runtime_dll_report()` 在程序根、`_internal`、`bin`、PATH 里查
`librtlsdr.dll`、`libusb-1.0.dll`（打包必备）与 `SoapySDR.dll`（环境可选）。
缺失打包必备项时弹中文提示（应用继续运行，不阻断）：

```
标题：MBDSDR 运行库缺失
缺少运行库：librtlsdr.dll

这些 DLL 应随安装包放在 _internal 目录（或 exe 同目录）。
请重新安装完整包，不要手工删 _internal 里的文件；若使用外部 SDR 驱动，
也可安装对应驱动后重试；若杀毒软件隔离了它们，请恢复后重试。
```

实测方法：冻结版改名 `_internal/librtlsdr.dll`，并用干净 PATH
（`C:\Windows\system32;C:\Windows`，避开本机 `C:\rtl-sdr\bin`）启动，
读到上面对话框原文；日志同时出现 `[dll] 缺失：librtlsdr.dll`。
SoapySDR.dll 缺失只告警不弹窗：`[dll] 未发现 SoapySDR.dll：SoapySDR 设备不可用；
RTL-SDR 直连不受影响`。

**限制**（两条，均为诚实口径）：
1. 该检查在 Python 桌面端启动时执行；直接双击 C++ 线 `mbdsdr.exe` 仍会
   先被 Windows 加载器拦成 `0xC0000135`（见 §7.5）。
2. 检查按「程序根 / `_internal` / `bin` / PATH」全机存在性判定：本机另装了
   rtl-sdr 且 `C:\rtl-sdr\bin` 在 PATH 时，删包内 `librtlsdr.dll` 也不会报缺——
   判据是「能否加载」，不是「包内是否自带」。要证明包内缺失必须用干净 PATH
   （实测方法即如此）。

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

### 5.3 发布前自检脚本（`scripts/release_check.py`）

`python scripts/release_check.py` 按序执行 build → smoke → subtree → single →
optional → qt → narrow → overlap → log；每步打印 `[PASS]/[FAIL]/[SKIP]` 与耗时，
**任一步 FAIL 立即停止**并打印该步「下一步该查什么」（`--keep-going` 仅用于出
完整报告）。末行汇总 `PASS n / FAIL n / SKIP n`，FAIL 退出码 1、只测了一半
（SKIP）退出码 2；**环境预检不通过退出码 3**（见下）。三态结果逐条写
`.tmp/release_check/result.json`（含每步 `status / reason_code / seconds / detail`）
——SKIP 不再只是汇总里的一个数字：

**环境预检（跑任何步骤之前）**：先执行 `python -c "import mbdsdr_ai"`。失败
（实测原文 `DSOIntegrityError: M1 缺 magnitude_source`）直接打印「环境不一致
（DSO 数据/dso_catalog.json）」并退出 3，**不启动任何 UI 步骤**——避免把
"并行会话改了 `mbdsdr_ai/data/dso_catalog.json`" 记成 narrow/overlap 红灯。

- `no_gui`：真屏 windows 指纹拿不到（无桌面会话 / 加载不到平台插件）；
- `not_run`：失败即停或 `--steps` 未选中，`detail` 注明「前序失败：<步骤>」；
- `no_device`：留给需要真硬件的步骤（当前闸门无此步骤）。

`--workpath/--distpath` 与 `PYINSTALLER_CONFIG_DIR` 全在 D 盘，脚本对 C 盘产物
落点直接判失败。build 步**自动**与「上一次 dist」比对（取 `.tmp` 下编号最大且
不是本次的 `pyi-distN`），不需要人肉写基准；本轮 dist11→dist12 文件 `+5 -0 ~3`、
`MBDSDR.exe` 30,933,722 → 31,108,193 B（**+174,471 B**），明细写
`.tmp/release_check/dist_diff.txt`。**exe 增长单独打印一行**，并尝试归因：
模块/依赖文件同时变化 →「未归因（另有 N 个模块/依赖文件同时变化）」；模块文件
无变化 →「打包器/引导层变动（模块文件无变化）」。dist11→dist12 属前者——同时
有 `constellations_iau.json`（−19,351 B）与 `dso_catalog.json`（+10,086 B）两个
数据文件变化，故如实打印「未归因（另有 2 个模块/依赖文件同时变化）」。

三类缺件（整棵子树 / 只缺单文件 / 可选件）都要求**弹窗标题 + 整段正文逐字相等**
（正文里唯一动态值是本次启动日志路径 `{log}`），不是「包含关键字」：

- subtree：noarchive 诊断包删整棵 `_internal/scipy/_external` →「这是打包缺陷…」；
- single：同一诊断包删 `fft.pyc` →「安装包解压不完整：只缺 …」；
- optional：发布包改名 `_internal/librtlsdr.dll`（干净 PATH）→「缺少运行库：librtlsdr.dll」。

实测（2026-11-06，`pyi-dist12`，一条命令跑全 9 步）：预检 `import mbdsdr_ai`
通过；build **PASS 243.7s**（4434 文件 / 821.8 MiB）、smoke **PASS 12.0s**（13/13）、
subtree **FAIL 398.2s**（40s 内没出现标题含「MBDSDR 启动失败」的窗口）；失败即停，
single/optional/qt/narrow/overlap/log 记 `SKIP(not_run)`；汇总
**PASS 2 / FAIL 1 / SKIP 6**，总耗时 **653.9s**，退出码 1。

subtree 红**不是闸门误报**，是产品侧回归：诊断包删掉整棵 `_internal/scipy/_external`
后，日志里 `[smoke] 私有模块导入自检 1/13 通过` 却仍正常建出主窗口
`MBDSDR - AI 定义无线电`；手动复测（rename `_external` → 启动 noarchive exe →
每 5s 取窗口标题共 100s）全程只有主窗口，**从未弹出「MBDSDR 启动失败」**。
`desktop/main.py` 的 `_report_startup_failure` 只在 `from main_window import
MainWindow` 抛 `ImportError` 时触发（main.py:273-280），当前工作区
`desktop/main_window.py`、`desktop/control_panel.py` 等有另一条线的未提交改动，
缺 scipy 已不再阻断 `main_window` 导入 → 缺件文案合同失效。归 UI 线修，闸门不改预期。

被 subtree 阻断的步骤单独补跑（`--skip-build --reuse-diag`）：

- `single` **FAIL 40.2s**（同一产品侧原因：缺 `fft.pyc` 也不弹窗，`[smoke] 4/13`）；
- `narrow` **PASS 37.5s**：offscreen 实际 `1265×618`（门槛 ≤1344×700）、
  windows 实际 `960×633`（门槛 ≤1360×728），物理 `1440×950` 仅信息行；
- `overlap` **PASS 0.0s**（复用同一次 breakdown）：offscreen 30/1、9/1；
  windows 13/1、7/1（四组都在 `TEMPORARY_WIDENED` 上界内）；
- `log` **PASS 232.1s**：`C:\Users` 全盘 879,884 个文件里无本次写入的产品日志，
  实际落点 `D:\mbdsdr\logs\mbdsdr_startup.log`；汇总 **PASS 3 / FAIL 0 / SKIP 0**，
  总耗时 **269.6s**，退出码 0。

（上一轮 2026-11-05 `--keep-going` 的 `PASS 7 / FAIL 1 / SKIP 0` 已作废：当时
narrow 只有一套指纹口径、`overlap` 仍叫 `size1366`，数字不再适用。）

- **narrow（两套指纹逐套判）**：offscreen（DPR 1.0 / 行高 13，门槛 **≤1344×700**）
  与真屏 windows（本机 DPR 1.5 / 行高 18，门槛 **≤1360×728**）各跑一次
  `ui_narrow_breakdown.py --json`，**各自按自己那套口径**判 PASS/FAIL，
  不换算、不互相代表；**任一 FAIL 整步 FAIL**。真屏逻辑尺寸换算出的物理尺寸
  只作信息行（暴露「逻辑过关但物理塞不进 1366×768」）。真屏拿不到（无桌面会话）
  时输出 `SKIP(no_gui) + 原因`，汇总逐条列出并把退出码置 2——「只测了一半」
  不允许静默当绿。本次 offscreen 最小 `1265×618` PASS；windows 逻辑 `960×633`
  PASS，物理 `1440×950` 仅作信息行（信息：>1366×768）。
- **overlap（口径与窄屏线共用，不另造一套）**：直接读
  `ui_narrow_breakdown.py` §4f 的 `summary.caliber`——扫描根
  `topBar/bottomDock/mainToolBar/centralWidget`，越界 = 子控件 `bottomRight`
  映射到父控件后 `x>父宽+1` 或 `y>父高+1`，挤压 = `QLabel/QAbstractButton/
  QComboBox/QLineEdit` 且 `sizeHint().width() > width()+2`。输出四个数字
  （尺寸 × 指纹 × 挤压/越界），每个都有自己的上限（`SIZE_LIMITS`）。
  **`TEMPORARY_WIDENED`**：下表是 2026-11-06 的**暂定上界，不是设计目标**，
  待 `desktop/control_panel.py`、`desktop/main_window.py` 未提交改动收口后重测收紧：

  | 指纹 | 1000×600 | 1344×700 | 相对旧基线 |
  |---|---|---|---|
  | offscreen（DPR 1.0） | 挤压 ≤36 / 越界 ≤1 | 挤压 ≤15 / 越界 ≤1 | 29→36（+7）、9→15（+6） |
  | windows（DPR 1.5） | 挤压 ≤13 / 越界 ≤1 | 挤压 ≤7 / 越界 ≤1 | 14→13（-1）、9→7（-2，反收紧） |

  旧基线（2026-11-05 窄屏线实测）`29/1、9/1、14/1、9/1` 在当前工作区**不可复现**：
  桌面端有未提交改动（topBar 新增 多普勒/发射/GNSS/UTC 等 statusValue），且
  `ControlPanel` 分栏最小宽在 **310 ↔ 365** 之间随启动时序变化，同一命令连跑
  offscreen 落在 **30/1 ~ 36/1（1000×600）、9/1 ~ 15/1（1344×700）**，windows 稳定
  **13/1、7/1**。按「暂定上界不是基线」的裁定，这里只锁当日实测最差值并标注
  `TEMPORARY_WIDENED`，禁止把它当设计目标引用。1000×600 是窗口声明的最小尺寸，
  产品允许该尺寸下溢出；1344×700 是 1366×768 屏客户区。两套指纹逐套报，任一 FAIL
  整步 FAIL，不许为了绿抬上限。
- log：只把「日志类文件」计入 FAIL（路径含 mbdsdr 且 `*.log`/startup），并排除
  IDE/Agent 会话日志目录（`.workbuddy` / `.trae*` / `.vscode` / `.cursor`）——
  它们的文件名带工作区名 `mbdsdr`，但不是产品日志；全盘扫 `C:\Users\<user>`，
  本次运行 0 条产品日志写入；`gui_config.json`/`desktop_settings.json` 是用户
  数据，不在本项范围。
- **失败即停的反证**（保留在用例里）：`--dist-dir 指向不存在的目录 --steps smoke,log`
  → `smoke` FAIL 后 `log` 记 `SKIP(not_run)`（detail「未执行（前序失败：smoke）」），
  打印 `下一步：…`，退出码 1；`result.json` 里能看到同一条 SKIP 记录。

### 5.4 1366×768 实测（含替代方案）

本机显示器支持 37 种模式，`EnumDisplaySettings` 枚举 **没有 1366×768**，无法真机切换，
如实标注为「替代方案」。**旧数字「40 控件越界 / 6 文本压缩」口径不可复现，已作废**，
现统一按 `ui_narrow_breakdown.py` §4f：

| 方式 | 结果（挤压 / 越界，上限即该格数字，见 §5.3 的 `TEMPORARY_WIDENED`） |
|---|---|
| offscreen（DPR 1.0 / 行高 13） | 1000×600：**30 / 1**（连跑区间 30~36，上界 ≤36 / ≤1）；1344×700：**9 / 1**（区间 9~15，上界 ≤15 / ≤1） |
| 真屏 windows（DPR 1.5 / 行高 18） | 1000×600：**13 / 1**（6/6 稳定，上界 ≤13 / ≤1）；1344×700：**7 / 1**（6/6 稳定，上界 ≤7 / ≤1） |
| 冻结版 dist12 + `QT_SCALE_FACTOR=1`，Win32 压到 1366×768 | 窗口 1366×768、进程存活；截图 1344×712，右侧控制面板与「AI 助手」页签右缘被裁 |
| 启动日志噪声 | 新增 2 行，1 条 `[dll] 未发现 SoapySDR.dll`（可选依赖告警），**无 `[record]`** |

恒为 1 的那个越界是 `ControlPanel` 在 `qt_scrollarea_viewport` 内的滚动内容，
不是文字被裁。结论：1344×700 下 9~15 处挤压属产品现状（已被暂定上界锁住，不是
设计目标），窗口完整显示仍属 UI 布局线待办。

**读数隔离（脚本侧适配，不是产品改动）**：产品用两参构造
`QSettings("MBDSDR","Desktop")`，Qt 在 Windows 下这种写法**恒为 NativeFormat**
（`setDefaultFormat(IniFormat)` 对它无效），窗口几何/分栏尺寸会落
`HKCU\Software\MBDSDR\Desktop`。`ui_narrow_breakdown.py` 原先会在 `close()` 时
把本次 1400×900 的分栏写回该键，下一次复跑就带着上一次状态，造成 29↔35 的漂移
（实测同一命令连跑数字跳动）。现在脚本在 `import main_window` **之前**把
`PySide6.QtCore.QSettings` 换成适配器：两参构造改走 `IniFormat` + `UserScope` +
每次新建的临时目录。**只改脚本进程内的类名，产品代码一行未动。**
反证（2026-11-06 02:56-02:59 实测）：先 `reg export HKCU\Software\MBDSDR\Desktop` 取
SHA256，再**真跑一次** `python scripts/ui_narrow_breakdown.py --json`（退出码 0），
跑完后再次导出，两次哈希**逐字节相同**
（`6DA7FC4D79D2323CC9E9AAFD3B1AFF28260969E7773424154C969280F806F0A3`）；
另有一对「不跑脚本、间隔 45s」的对照导出也相同（`EFCE39CF…C2FB`），
证明该键在观测窗口内没有第三方写入。脚本进程内只替换了 `QSettings` 类名，
产品代码一行未动——这是**脚本侧读数适配，不是产品改动**。


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

### 6.1 可复用判据（以后新增 scipy / numpy 子依赖先查这两条）

1. **PEP 420 命名空间包**：目录里有没有 `__init__.py`？
   没有 → `pkgutil.iter_modules` / `pkgutil.walk_packages` / `collect_submodules`
   一律枚举不到，PyInstaller 的 scipy hook 也看不见，必须按文件系统遍历收集。
   一条命令判定：

   ```
   python -c "import os,scipy; p=os.path.join(os.path.dirname(scipy.__file__),'_external'); print(os.path.exists(os.path.join(p,'__init__.py'))"
   ```

   `False` 就说明 `scipy/_external` 需要显式遍历（本次 P0 根因）。
2. **动态 `__import__` / 动态模块名**：源码里出现任一形态，静态分析就看不见：
   - `__import__(__package__ + ".fft")`（本次 numpy 的写法）
   - `importlib.import_module(拼接字符串)`（例如 `scipy._lib._array_api`）
   - `importlib.util.spec_from_file_location(name, path)`（本次 `astronomy.py` 的写法）

   判定命令：

   ```
   rg -n "__import__\(|import_module\(|spec_from_file_location" --type py <包目录>
   ```

   命中后必须在 spec 里显式 `hiddenimports`（模块名）或 `datas`（按路径加载的文件），
   并在冻结版里跑一次「导入即验证」冒烟，不能只看源码能导入。
3. 版本升级复核：每次升级 scipy/numpy，按上面两条重新扫一遍；`_external` 里如果
   某天出现 `__init__.py`（不再是命名空间包），遍历收集仍然正确，可保留。

这两个坑与具体包无关：任何「无 `__init__.py` 的 vendored 子树 + 运行时按需导入」
都会重演同一条故障链（漏收文件 → 用户看到 `No module named ...`）。

同类风险仍存在但**未逐个**纳入启动自检的：`scipy.io._*`、`scipy.sparse._*`、
`scipy.ndimage._*` 这类“公开包的私有实现模块”，它们由 scipy hook 正常收集；
只有 PEP 420 命名空间子树（`_external`）需要文件系统遍历。新增 scipy 版本时，
以 `<scipy>/_external` 是否存在 `__init__.py` 作为「是否需要遍历收集」的判据。

## 7. 待办与已知限制

1. **测试已补跑**：`tests/test_startup_log_policy.py` 7/7；锁释放后按裁定只跑
   `tests/test_settings_schema.py -k "record_dir or corrupt or write_failure"`
   子集，6/6 通过（全量 51/51 由频谱线另行实测）。注意：单独 `-k` 选到
   `test_record_dir_missing_target_reports_actionable_error` 时会因没有
   QApplication 而 Qt abort（`0xC0000409`）——已给该用例补 `qapp` fixture，
   子集命令现可独立复现全绿，不依赖全量顺序。
2. 4 个扩展项（cobyqa/pyprima、deprecation/_ccallback/_uarray、numpy._core.multiarray、
   numpy.fft/linalg）只在源码与 TOC 层核验，未纳入冻结版启动自检；要覆盖需把它们
   加进 `_RISKY_IMPORTS` 再重建。
3. 日志落点按裁定为「安装根可写则用安装根」：若用户把程序装在
   `C:\Users\<user>\...` 下，日志会写在该安装根内。这是裁定顺序本身的结果，
   不再回落到 `%APPDATA%`/`%LOCALAPPDATA%`；如要连安装根在用户目录也拒绝，
   需另行裁定（会导致部分用户完全没有持久日志）。
4. ACL 级只读（非 Program Files）不再靠启动期同步判断：已改为后台线程试写 +
   状态栏/日志回报（§5.1）。主线程不等待，因此不会出现「启动不报、写盘才炸」，
   但状态栏提示可能晚于窗口出现几秒。
5. C++ 线直接双击 `mbdsdr.exe` 时，缺 `librtlsdr.dll` 仍由 Windows 加载器先拦成
   `0xC0000135`：Python 侧启动检查只能在 Python 桌面端生效。C++ 启动期自检属于
   另一条线，未做；打包脚本的前置校验仍在。
6. PyInstaller 自身缓存默认落在 `%LOCALAPPDATA%\pyinstaller`（工具缓存，不是产物）。
   要严格不碰 C 盘，构建时设 `PYINSTALLER_CONFIG_DIR=<D 盘目录>`；本次 `--distpath`
   / `--workpath` 已全部在 D 盘。
7. `scripts/ui_interaction_audit.py` 棘轮越线（WARN 7 > 基线 3）属 UX 线，未处置。
8. **测试污染真实用户配置**：`tests/test_record_dir_consumption.py` 的 `window`
   fixture 直接 `mw.MainWindow()`，用的是默认 `%APPDATA%\mbdsdr\desktop_settings.json`；
   用例 `record_dir` 写入 tmp 路径后落盘，把真实配置改成 `…\pytest-…\rec`。
   本机残留已清理，但该 fixture 未隔离配置路径（属测试隔离线），下次整跑仍会再脏。
9. **1366×768 红线**：发布自检 `narrow` 两套指纹逐套判——offscreen `1265×618`
   （≤1344×700）PASS；真屏 windows 逻辑 `960×633`（≤1360×728）PASS，其物理
   `1440×950` 仅作信息行（>1366×768，未参与判红）。`overlap` 步按 §4f 口径
   输出四个数字（上限见 `SIZE_LIMITS`，标注 `TEMPORARY_WIDENED`）：本次实测
   offscreen 30/1、9/1（连跑区间 30~36 / 9~15），windows 13/1、7/1。
   旧值「1344×700 两套均 9/1」「1000×600 两套 29/1、14/1」在当前工作区不可复现
   （桌面端未提交改动 + `ControlPanel` 分栏最小宽漂移，见 §5.3）；窗口在
   1366×768 屏上完整显示仍属 UI 布局线待办（§5.4）。

## 8. 本轮改动的文件

- `desktop/MBDSDR.spec`：scipy 命名空间子树收集 + `astronomy.py` 数据文件 +
  `MBDSDR_NOARCHIVE=1` 诊断构建开关（默认关闭）。
- `scripts/release_check.py`（新）：构建/冒烟/三类缺件原文匹配/窄屏/日志落点的
  可重复发布自检。
- `desktop/main.py`：启动自检、缺件分类文案、Qt 插件前置检查、运行库 DLL 检查、
  异步录音目录探测接线；catch-all 收窄到 `ImportError`/`OSError` 等。
- `desktop/startup_paths.py`（新）：日志落点策略 + 内存缓冲 + 异步可写探测 +
  DLL 存在性检查（无 Qt 依赖，可单测）。
- `desktop/settings.py`：录音默认目录回退（受保护安装根/只读）+ 写盘错误可见。
- `tests/test_startup_log_policy.py`（新）：日志落点/内存缓冲/用户目录禁令断言。
- `tests/test_settings_schema.py`：给录音目录错误文案用例补 `qapp` fixture，
  使 `-k record_dir` 子集可独立运行（原顺序依赖缺 QApplication 会 abort）。
- `scripts/release_manifest.py`（新，2026-11-06）：发布清单四件机检
  （版本号 / 依赖存在性 / 许可证 / 安装器），只读、失败非零退出；见 §9。
- `docs/packaging.md`：本文。

## 9. 发布清单四件机检（2026-11-06 实测）

> **产物快照口径**：修复前 `d:\mbdsdr\.tmp\pyi-dist12`（目录时间
> `2026-11-06 02:33:45`）；修复后 `d:\mbdsdr\.tmp\pyi-dist13`（`2026-11-06 03:39:28`
> 起构建，Python 3.14.0 / PyInstaller 6.16.0）。**结论只对这份产物成立，重建后必须
> 重跑**。`TEMPORARY_WIDENED` 的收紧按约定等 UI 收口后单独重测，本轮不涉及
> （`release_check.py` 的窄屏/overlap 阈值未动）。

一次跑完四项（任一项 FAIL → 退出码 1；`--json` 输出纯 JSON）：

```
python scripts/release_manifest.py --dist d:\mbdsdr\.tmp\pyi-dist13
```

总判前后：

| 快照 | FAIL | WARN | PASS | SKIP | 结论 |
|---|---|---|---|---|---|
| 修复前 `pyi-dist12` | 19 | 7 | 23 | 6 | FAIL（退出码 1） |
| 首轮修复后 `pyi-dist13` | **0** | **30** | **52** | 6 | WARN（退出码 0） |
| 本轮回填 `pyi-dist14`（WARN 分桶 + 运行时证据） | **0** | **0** | **82** | 6 | PASS（退出码 0） |

首轮 30 条 WARN 里 29 条是「产物有但没声明」的逐包归因、1 条是汇总行。它们**全部
属于“已归因可接受”桶**（8 个指到 `Requires-Dist`、21 个指到 xref import 链，见 9.2）。
本轮把这桶改成 PASS、把“未归因”改成 FAIL，并给 21 个只靠静态 xref 的包补了
**产物解释器里的 import 证据**；WARN 归零不是删位置，是每条都拿到了可复核证据。

### 9.1 版本号一致性 — PASS（`mbdsdr_ai/VERSION` 单一真值源）

canonical 拍定为 `0.2.0`；唯一手抄点是 `mbdsdr_ai/VERSION`，其余全部派生：

| 出现处 | 派生方式 | 判定 |
|---|---|---|
| `mbdsdr_ai/VERSION` | 唯一手抄（`0.2.0`） | canonical |
| `cpp/CMakeLists.txt` | `file(READ .../mbdsdr_ai/VERSION)` → `project(... VERSION ${MBDSDR_VERSION})` | PASS |
| `pyproject.toml` | `dynamic = ["version"]` + `version = {file = "mbdsdr_ai/VERSION"}` | PASS |
| `mbdsdr_ai/version.py` | 读同目录 `VERSION`，导出 `__version__` | PASS |
| `mbdsdr_ai/__init__.py`、`core/__init__.py` | `from (..).version import __version__` | PASS |
| `desktop/main.py` | 导入共享版本 → `setApplicationVersion(_app_version)` | PASS |
| `desktop/MBDSDR.spec` | 读 `VERSION` → `VSVersionInfo` | PASS |
| 产物 `MBDSDR.exe` | `FileVersion = 0.2.0.0`、`ProductVersion = 0.2.0` | PASS |
| `README.md` 首行 | `# MBDSDR ... v0.2.0` | PASS |
| `desktop/main_window.py:_show_about` | `QApplication.applicationVersion()` | PASS |

机检口径：每个派生点必须有「读取 VERSION / 引用共享常量」的**正则命中**，
失配即 FAIL（防止删掉取值点后静默变绿）；同时扫描这些文件里的 `x.y.z` 字面量，
发现即 FAIL（`desktop/main.py` 的 `192.168.4.1` 这类 IP 被正则排除）。README
首行作为面向读者的版本声明，与 canonical 比对后放行。

复跑：

```
python scripts/release_manifest.py --only version --dist d:\mbdsdr\.tmp\pyi-dist13
```

### 9.2 依赖 pin 存在性 — PASS（差集①清空，差集② 29 个全部归因）

**WARN 可发布的条件（判定口径，写死在本节）**：产物里每个未在
`requirements*.txt` 声明的第三方包，必须同时满足两条才算“已归因可接受”——

1. 能指到 **`Requires-Dist` 闭包**（某个已声明包直接或间接依赖它），或能指到
   **一条从项目模块/已声明包出发的 import 链**（PyInstaller xref 的正向边反转）；
2. 在**产物解释器里 `import` 成功**（静态 xref 只解释“PyInstaller 为什么收集它”，
   不证明产物里真的能用）。

两条缺一（指不到上游，或链上的包 import 不起来）即为“未归因”，机检直接 **FAIL**；
不再以 WARN 形式留待人工判断。两个状态在 `scripts/release_manifest.py` 的
`check_deps` 里分别是 `PASS`（附链/附 Requires-Dist 来源）和 `FAIL`（附缺哪条证据）。


声明面 = 根 `requirements.txt` + `desktop/requirements.txt` 实际依赖行（12 个发行包）
+ spec 点名模块/数据项。产物定位 = `_internal` 顶层 **+ exe 内嵌 `PYZ.pyz` 模块表**；
纯 Python 包（requests、pyserial、sgp4…）本来就不在 `_internal` 目录里，
这是 PyInstaller 6 onedir 的正常布局，只扫 `_internal` 会假报“全缺”。

修复项：

- `pymap3d` 两条死声明已删（源码零 `import pymap3d`），**差集①为空**。
- `pyrtlsdr==0.2.93` 显式声明：产物确实随包 `rtlsdr` 模块 +
  `pyrtlsdr-0.2.93.dist-info`（GPL-3.0），不能再写成“用户自装”。
- 测试框架闭包：spec 过滤 `mbdsdr_ai.tests` 并
  `excludes=['pytest','_pytest','pluggy','iniconfig']`；产物三处
  （顶层模块 / `PYZ.pyz` / `dist-info`）均无命中，机检**任一命中即 FAIL**。

差集② 29 个，分两类（脚本逐包输出）：

| 归因 | 包 |
|---|---|
| Requires-Dist 传递（8） | `certifi` ← requests, skyfield；`cffi` ← sounddevice；`charset-normalizer`、`idna`、`urllib3` ← requests；`jplephem` ← skyfield；`pycparser` ← cffi；`shiboken6` ← pyside6 |
| xref import 链（21） | 见下 |

四个重点包（按 PyInstaller xref 指名链，来自 `pyi-build13/MBDSDR/xref-MBDSDR.html`）：

| 包 | import 链 |
|---|---|
| matplotlib | `mbdsdr_ai.orbit_determination → matplotlib`（`mbdsdr_ai.sdr_tools` 同） |
| pandas | `mbdsdr_ai.orbit_determination → matplotlib.pyplot → pandas` |
| lxml | `mbdsdr_ai.orbit_determination → matplotlib.pyplot → pandas → pandas.io.api → pandas.io.xml → lxml` |
| openpyxl | `mbdsdr_ai.orbit_determination → matplotlib.pyplot → pandas → pandas.io.api → pandas.io.excel → pandas.io.excel._openpyxl → openpyxl` |

其余 import 链（实测，均为 xref 直接边）：

- `brotli` ← `requests → urllib3.response`；`cryptography` ← `requests`
- `contourpy` ← `matplotlib.contour`；`cycler` ← `matplotlib.pyplot`
- `et-xmlfile` ← `... → openpyxl.descriptors.serialisable → openpyxl.xml.functions`
- `fonttools` ← `matplotlib.dviread → fontTools.agl`
- `kiwisolver` ← `matplotlib._constrained_layout → matplotlib._layoutgrid`
- `packaging` ← `rtlsdr → pkg_resources`
- `psutil` ← `mbdsdr_ai.core.controller`；`zstandard` ← `mbdsdr_ai.baseband_io`
- `pyparsing` ← `matplotlib._mathtext`
- `python-dateutil` ← `scipy.cluster.hierarchy._hierarchy_impl → matplotlib.pylab → matplotlib.dates`
- `six` ← 同上链 → `dateutil.relativedelta`
- `setuptools` ← `pyi_rth_setuptools.py → _distutils_hack`
- `typing-extensions` ← `PySide6.QtAsyncio.events`
- `tzdata` ← `pandas.core.dtypes.dtypes → zoneinfo`
- `xlsxwriter` ← `pandas.io.excel → pandas.io.excel._xlsxwriter`

结论：29 个全部落在“已归因可接受”桶（Requires-Dist 8 / import 链 21），无一“来路不明”，
故差集②整条判 PASS。是否继续裁剪（把这些 import 路径改成可选延迟导入、或补声明）
属于依赖面决策，本轮不删不改。

运行时证据（`pyi-dist14`，`desktop/main.py` 新增的产物自检入口，不初始化 Qt）：

```
cd d:\mbdsdr\.tmp\pyi-dist14
.\MBDSDR\MBDSDR.exe --import-probe "brotli,certifi,cffi,charset_normalizer,contourpy,
cryptography,cycler,et_xmlfile,fontTools,idna,jplephem,kiwisolver,lxml,matplotlib,
mpl_toolkits,openpyxl,packaging,pandas,psutil,pycparser,pyparsing,dateutil,
_distutils_hack,pkg_resources,setuptools,shiboken6,six,typing_extensions,tzdata,
urllib3,xlsxwriter,zstandard"
```

实测 32 个 import 名**全部 `[probe] OK`**（退出码 0）。清单的 deps 检查会对产物跑同一
入口；任一包失败即把该包判“未归因 → FAIL”。旧产物没有该入口时也判 FAIL
（“产物不支持 --import-probe（旧产物，需重打）”），不会因为“跑不起来”而静默放行——
`pyi-dist13` 上实测就是 22 条 FAIL（21 包 + 汇总），正是这条闸在起作用。

复跑：

```
python scripts/release_manifest.py --only deps --dist d:\mbdsdr\.tmp\pyi-dist14
```

### 9.3 许可证与合规文件 — PASS（分发底线补齐）

- **PASS**：产物 `_internal/` 现在带项目 `LICENSE`、`NOTICE.md`，以及
  `third_party_licenses/{GPL-2.0,GPL-3.0,LGPL-3.0}.txt`（spec `datas` 落盘）。
- **PASS（陈述改成事实）**：`NOTICE.md`/`LICENSING.md` 已登记
  `pyrtlsdr 0.2.93`（GPL-3.0，产物内确有 `rtlsdr` 模块 +
  `pyrtlsdr-0.2.93.dist-info`）、`librtlsdr`/`rtlsdr.dll`（GPL-2.0）、
  `PySide6`/`shiboken6`（LGPL-3.0），并指向产物内许可证全文。
- **PASS（机检断言）**：对产物内每个 copyleft 包，`NOTICE.md` 必须有登记、
  产物内必须有对应许可证全文；`rtlsdr.dll`/`librtlsdr.dll` 若随包而 `NOTICE`
  未登记、或没有 GPL-2.0 全文，直接 FAIL；“pyrtlsdr 不随包/用户自装”的旧话术
  也会 FAIL。
- **PASS**：仓库既有数据 GPL 扫描（`scripts/check_data_licenses.py --check`）
  `scanned data files: 392 | 合规检查通过`——沿用该结论，未另写一套数据许可规则。
- **PASS**：产物里带 `attribution` 的 CC BY-SA 数据文件 3 个（署名随数据走）。

复跑：

```
python scripts/release_manifest.py --only license --dist d:\mbdsdr\.tmp\pyi-dist13
```

### 9.4 安装器冒烟 — SKIP（无安装器，本条不适用）

依据（脚本逐条打印，可复现）：

- 全仓 glob `*.iss`（Inno Setup）/ `*.nsi`（NSIS）/ `*.wxs`（WiX）/ `*.msix`/
  `*.appxmanifest`/`*.pkgproj` **0 命中**；
- `scripts/package_windows.ps1` 自述“本脚本只做构建+收集+打包，不做安装器
  （无注册表/快捷方式）”，产物是 zip 便携包 + SHA256；
- `desktop/build_windows.bat` 只调 `pyinstaller` 出 onedir 目录，无 install/uninstall 目标。

因此“装 → 启动到窗口出现 → 卸载后残留检查”没有安装器对象可跑；便携包启动这一半
已由 `scripts/release_check.py` 的 `smoke` 步覆盖（窗口标题 `MBDSDR - AI 定义无线电`
出现即通过），卸载残留没有注册表/快捷方式可查。若以后新增安装器描述文件，
本脚本会 FAIL 提示“必须补配套安装/启动/卸载冒烟”，不会静默跳过。

复跑：

```
python scripts/release_manifest.py --only installer
```

### 9.5 四项的机检契约

`scripts/release_manifest.py` 三态输出 `PASS/WARN/FAIL/SKIP`，逐项独立汇总，
任一项 FAIL → 进程退出码 1；`--json` 时 stdout 只有 JSON（无人类可读前缀）。
`WARN` 只用于“缺口存在但不构成取值冲突”的少数检查点；依赖差集②已改成
**PASS / FAIL 两态**（已归因 / 未归因），不再产生 WARN。
`SKIP` 只用于“对象不存在”（如无安装器），不算绿。

