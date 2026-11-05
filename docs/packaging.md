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

`python scripts/release_check.py` 按序执行 build → smoke → dll → scipy → qt →
narrow → log；每步打印 PASS/FAIL 与耗时，末行汇总，任一步 FAIL 退出码 1。
`--workpath/--distpath` 与 `PYINSTALLER_CONFIG_DIR` 全在 D 盘，脚本对 C 盘产物落点直接判失败。

实测（2026-11-05，产出 `pyi-dist12`）：build 225.9s（4429 文件 / 820.8 MiB，
对 dist11 `+0 -0 ~1`）、smoke 14.4s（13/13）、dll 17.0s、scipy 2.5s、qt 3.1s、
narrow 7.4s、log 61.3s；汇总 **PASS 6 / FAIL 1**。

- scipy 场景：发布包的纯模块在 PYZ 归档里，删不到目录；spec 增加
  `MBDSDR_NOARCHIVE=1` 开关（默认 False，发布行为不变）生成诊断包，删除整棵
  `_internal/scipy/_external` 后原文匹配到 `No module named 'scipy._external'`
  + `这是打包缺陷，不是你的操作问题`。
- narrow：`ui_narrow_breakdown.py --json` 实测 `window_minimumSizeHint=1625×729`，
  超过 1360×728 → **FAIL 属实**，产品代码未动。
- log：只把「日志类文件」计入 FAIL（路径含 mbdsdr 且 `*.log`/startup）；
  全盘扫 `C:\Users\<user>` 1,106,587 个文件，本次运行 0 条日志写入；
  `gui_config.json`/`desktop_settings.json` 是用户数据，不在本项范围。

### 5.4 1366×768 实测（含替代方案）

本机显示器支持 37 种模式，`EnumDisplaySettings` 枚举 **没有 1366×768**，无法真机切换，
如实标注为「替代方案」：

| 方式 | 结果 |
|---|---|
| 源码窗口 + `QT_SCALE_FACTOR=1` 强制 1366×768 | `minimum_size_hint=1670×989`；**40 个控件越界、8 个文本被压缩**；截图右侧面板、顶部 UTC、底部提示均被裁 |
| 冻结版 dist12 + `QT_SCALE_FACTOR=1`，Win32 压到 1366×768 | 窗口 1366×768、进程存活；客户端 1344×712，右侧星图缩放/顶部工具条被裁 |
| 启动日志噪声 | 新增 2 行，1 条 `[dll] 未发现 SoapySDR.dll`（可选依赖告警），**无 `[record]`** |

结论：1366×768 下窗口不能完整显示，控件被裁是产品现状。

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
9. **1366×768 红线未修**：发布自检的 narrow 步骤实测 `1625×729`（要求 ≤1360×728），
   且 1366×768 下实际裁切 40 个控件；这属于 UI 布局线的待办，本线只做测量与如实报红，
   没有为了脚本变绿去改 `main_window` 的最小尺寸/布局。

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
- `docs/packaging.md`：本文。
