# Windows 弹窗根因分类与修法（直接照着查）

> 2026-11-03 四轮弹窗（Qt6Widgets / Qt6Test / platforms 插件 / libgcc）收口后
> 固化。每类给判定方法与修法，不重复踩。

## 失败模式速查表

| 弹窗文案 | 层 | 根因 | 判定 | 修法 |
|---|---|---|---|---|
| 「由于找不到 Qt6Xxx.dll / libgcc_s_seh-1.dll / libstdc++-6.dll …」系统错误 | **loader 层** | exe 旁 + PATH 均无该 DLL | 弹窗标题即 DLL 名；`objdump -p exe` 导入表核对 | 把 DLL 落到 exe 旁（loader 第一搜索位） |
| 「This application failed to start because no Qt platform plugin could be initialized」（Qt 对话框，非系统框） | **插件层** | DLL 全在、但 exe 旁无 `platforms\qwindows.dll` 或 QT_PLUGIN_PATH 未指插件目录 | stderr 有 `Could not find the Qt platform plugin "windows" in ""`；退出码 0xC0000142 | `platforms\qwindows.dll`（+ `qoffscreen.dll` 无头）落 exe 旁 `platforms\` |
| 无弹窗但测试断言漂移（空态类失败） | **硬件争用层** | 真 RTL-SDR 在位时每个 MainWindow 抢开 device 0，并发窗口 `usb_open error -3` | stderr 有 `[RtlSdrSource] opened device 0` + `Found Fitipower FC0012 tuner` | 设 `MBDSDR_NO_HW=1`（CTest 已统一注入；直跑需手动设或拔设备） |

## 每层详细判定与修法

### 1. loader 层（缺 DLL，系统错误框）

**判定**：弹窗标题栏是完整 DLL 名（`Qt6Test.dll`、`libgcc_s_seh-1.dll`…）。
进程退出码 0xC0000135（-1073741515）。

**根因**：Windows loader 按「exe 目录 → System32 → PATH」搜索导入 DLL。
构建树里的 exe 链接 Qt6/MinGW 运行库，这些 DLL 不在系统 PATH，开发者
shell 手动 export 只对当前会话有效。

**修法**：DLL 落到 exe 所在目录（第一搜索位，最快最稳）。
`cpp/cmake/deploy_runtime.cmake` + `cpp/CMakeLists.txt` 的 POST_BUILD
全目标自动部署已把 Qt6*.dll + MinGW 三件套 + 辅助库（avcodec 等）
[+ librtlsdr.dll] 落到 `CMAKE_RUNTIME_OUTPUT_DIRECTORY`。

**禁忌**：不要靠「开发者 shell 加 PATH」或「只跑 deploy_win_runtime 目标」
——前者对新 shell 无效，后者只构建单个测试时不触发（第四次弹窗根因）。

### 2. 插件层（platforms 插件找不到，Qt 对话框）

**判定**：DLL 全在（loader 不弹），但 Qt 弹「no Qt platform plugin could
be initialized」。stderr 有 `Could not find the Qt platform plugin
"windows" in ""`（空路径 = QT_PLUGIN_PATH 未设且 exe 旁无 platforms/）。
退出码 0xC0000142（STATUS_DLL_INIT_FAILED）。

**根因**：Qt 按插件搜索路径找 `platforms\qwindows.dll`：exe 旁 `platforms\`
→ QT_PLUGIN_PATH → Qt 安装目录。ctest 注入 QT_PLUGIN_PATH 只保 ctest
子进程；直跑/双击时 exe 旁无 platforms/ 即弹。

**修法**：`platforms\qwindows.dll`（GUI）+ `qoffscreen.dll`（无头）落
exe 旁 `platforms\`（部署脚本已含）。

### 3. 依赖库不在搜索路径（同 loader 层，但源不在 Qt/MinGW bin）

**判定**：`objdump -p exe` 导入表有某 DLL，但 exe 旁 + Qt bin + MinGW bin
都没有（如 librtlsdr.dll 在 RTLSDR=FOUND 树位于 `C:/Users/.../librtlsdr/
build/src`，不在任何默认搜索路径）。

**修法**：从 CMakeCache 的 `RTLSDR_LIBRARY` 推导目录拷 `librtlsdr.dll`
到 exe 旁（package_windows.ps1 便携包链路）或部署脚本 RTLSDR_DIRS。

### 4. 启动期抢硬件设备（空态测试漂移，无弹窗）

**判定**：无弹窗，但 phase21_empty_state / collapsible_group / agent 等
空态类测试在真 RTL-SDR 在位的机器上失败，stderr 有 `[RtlSdrSource]
opened device 0` / `Found Fitipower FC0012 tuner` / `usb_open error -3`。

**根因**：SpectrumEngine 构造函数 `RtlSdrSource::start()` 真机在位时成功
打开 device 0；多个 MainWindow 并发抢同一设备，后到者 usb_open error -3；
空态类测试假设「无硬件 → NullSource」被打破。

**修法**：`MBDSDR_NO_HW=1` 接缝（与 MBDSDR_NULL_AUDIO 同族）——
`rtlLibOps()` 返回 nullptr（stub 行为）+ `RtlSdrDeviceEnumerator::
enumerate()` 返回空表。CTest 已对全部 162 目标统一注入；直跑需手动设
`$env:MBDSDR_NO_HW="1"` 或拔设备（产品行为：直跑不设就真用硬件）。

## 硬门禁（防再犯）

- **构建期自检**：`deploy_runtime.cmake` 逐一 Test-Path 源文件（Qt6Core/
  MinGW 三件套/platforms 插件），缺失 FATAL_ERROR 构建期红。
- **运行期冒烟**：`scripts/smoke_exe_no_popup.ps1 -BuildDir <树>` 干净
  PATH（仅 System32，零 QT_/MBDSDR_ env）批量启动全部 exe，任一非零
  退出/超时即失败，输出缺失 DLL 名清单（objdump 导入表解析区分
  loader/插件层）。提交前/CI 固定环节。
- **CI Windows 面**：见 `.github/workflows/ci_windows.yml`（部署面硬门禁
  job 草案）。
