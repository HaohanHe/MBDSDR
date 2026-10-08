# Phase63 · mbdsdr_core 重构后 Windows (MinGW-w64) 构建路径只读复核

- 仓库 HEAD：`1068d5d`；全程只读审计（grep / Read / git show），**零代码改动**，未 git add/commit/push。
- 触发背景：commit `97c60b7`（build: compile app once into mbdsdr_core static lib; kill per-target autogen）
  把整棵应用源码收敛为单一静态库 `mbdsdr_core`，AUTOMOC/AUTOUIC/AUTORCC 只跑一次；
  Linux 侧已验证（clean configure + -j4，758s 全量构建 rc=0，ctest 133/133）。
  本文档在**不实际构建**的前提下，从 CMake/源码事实推演 Windows MinGW-w64 13.1 + Qt 6.8.2
  侧（用户环境 `D:\mbdsdr\MBDSDR`）需要复核/验证的差异点。
- 结论先行：**重构本身的 CMake 逻辑是平台无关的，未发现"重构后 Windows 必须改码"的硬性差异点**；
  下文列出 4 个仅需在 Windows  configure/构建时复核确认的观察项 + 一份验证清单。

---

## 1. AUTOMOC 集中可行性复核

### 1.1 仓库现状（cpp/CMakeLists.txt 事实）

- 第 17–19 行：全局 `set(CMAKE_AUTOMOC ON / AUTOUIC ON / AUTORCC ON)`。
- 第 244 行：`add_library(mbdsdr_core STATIC ${CORE_SRCS} ${HEADERS})`，
  `CORE_SRCS = ${SOURCES} - src/main.cpp`（第 242–243 行），`HEADERS` 全量列在
  第 121–232 行 —— autogen 扫描范围 = 一个目标、一次完成，产物为单个 `mbdsdr_autogen/`。
- 第 246–247 行：core 以 **PUBLIC** 链接 `Qt6::Core Gui Widgets Multimedia Network Concurrent`，
  include 目录与 Qt 依赖自动传播给 `mbdsdr` 和全部 ~130 个测试目标。
- `mbdsdr`（第 251 行）只剩 `src/main.cpp + src/heap_probe.cpp`，PRIVATE 链接 core。
- 无 Q_OBJECT 目标：全仓 **59 处** `set_target_properties(... PROPERTIES AUTOMOC OFF AUTOUIC OFF AUTORCC OFF)`
  （commit 信息称 58 个，当前 HEAD 微增 1，以 grep 为准）。
- 全仓 **0 个 .ui 文件**、**0 个 .qrc 文件** —— AUTOUIC/AUTORCC 在当前代码树上实际无产物，
  仅 AUTOMOC 有工作量（moc 生成 cpp 编译进 core）。

### 1.2 MinGW 下可能差异点（推演，供验证）

| # | 差异点 | 仓库事实 | MinGW 复核动作 |
|---|---|---|---|
| M1 | **moc 可执行文件定位** | `find_package(Qt6 REQUIRED COMPONENTS ...)`（第 20 行）统一拉入 moc/uic/rcc | 无差异：Qt6 CMake 包在 MinGW 上同样通过 `Qt6::moc_executable` 提供 moc.exe。确认 `-DCMAKE_PREFIX_PATH` 指向 mingw_64 版 Qt 即可，无需手动指定 moc 路径。 |
| M2 | **autogen 目录命名/路径** | 第 265/272… 行（`if(NOT WIN32)` 内）与第 709/760/983/1090/1101/1115/1122/1129/1136/1143/1405/1643 行（Windows 上仍存活的 ctest 目标）**硬编码** `build/<target>_autogen/include`、`build/mbdsdr_autogen/include` 相对 include 路径 | 相对路径在 CMake 中相对 `CMAKE_CURRENT_SOURCE_DIR`（即 `cpp/`）解析。**建议二进制目录就用 `cpp/build`**（与 Linux 基线一致）；若改用别的 build 目录名，这些 `-I` 指向不存在的目录——GCC/MinGW 对不存在的 `-I` 静默忽略，且全局 AUTOMOC ON 会自动把 `<BINARY_DIR>/<target>_autogen/include` 加回目标，故**预期无害**，但需在 configure 输出里确认无 "Cannot find" 类报错。 |
| M3 | **`-D_USE_MATH_DEFINES`** | 第 10 行 `add_compile_definitions(_USE_MATH_DEFINES)` 在 project 之后立即生效，作用于**全部目标**（含 core 与所有测试） | 无需动作。MSVC/MinGW 下 `M_PI` 由该宏放开（注释第 8–9 行已写明）；Linux 下无害。重构未移动此行，MinGW 行为与此前一致。 |
| M4 | **静态库 `.a` 形态** | core 是 STATIC 库（`mbdsdr_core.a`），最终由 `mbdsdr.exe` 链接 | MinGW 静态库链接语义与 Linux 相同。唯一注意：core 编译单元数量大（~150 个 .cpp），链接期单 exe 链接 .a 较慢但无正确性问题。 |
| M5 | **控制台子系统** | `add_executable(mbdsdr ...)` 未加 `WIN32`/`WIN32_EXECUTABLE` | 观察项（非构建路径问题）：MinGW 产出控制台子系统 exe，运行时带一个控制台窗口。这是重构前就有的形态，未变化。 |

**小结：AUTOMOC 集中化在 MinGW 下无已知功能性差异；M2 是唯一需要按事实确认的复核点（保持 build 目录命名 = `cpp/build` 即可规避）。**

---

## 2. librtlsdr DLL 链接复核

### 2.1 仓库事实（第 24–41 行、第 423–431 行）

```cmake
find_package(PkgConfig QUIET)
if(PkgConfig_FOUND)
    pkg_check_modules(RTLSDR librtlsdr)     # Linux/msys2 主路径
endif()
# 回退（MinGW 无 pkg-config 时走这条）：
if(NOT RTLSDR_FOUND)
    find_path(RTLSDR_INCLUDE_DIR NAMES rtl-sdr.h)
    find_library(RTLSDR_LIBRARY NAMES librtlsdr rtlsdr librtlsdr.dll.a)
    ...
endif()
...
if(RTLSDR_FOUND)
    target_compile_definitions(mbdsdr_core PUBLIC HAVE_RTLSDR=1)
    target_include_directories(mbdsdr_core PUBLIC ${RTLSDR_INCLUDE_DIRS})
    target_link_directories(mbdsdr_core PUBLIC ${RTLSDR_LIBRARY_DIRS})
    target_link_libraries(mbdsdr_core PUBLIC ${RTLSDR_LIBRARIES})
```

- 链接挂在 **`mbdsdr_core` PUBLIC** 上 → 自动传播给 `mbdsdr.exe`，exe 侧无需重复链接。
- `HAVE_RTLSDR=1` 也是 PUBLIC 编译定义 → core 内所有 TU 统一看到 RTL-SDR 后端开关。
- 找不到时**软失败**（第 430 行 "DISABLED ... stub compiled"），应用仍可构建。

### 2.2 MinGW 下 DLL 链接验证清单

| # | 检查项 | 期望（从 CMake 事实推导） |
|---|---|---|
| R1 | **导入库形态** | 官方 rtl-sdr Windows 发布包（osmocom 提供）MinGW 侧导入库文件名即 `librtlsdr.dll.a` —— 第 33 行 `find_library` 的候选名单 `librtlsdr / rtlsdr / librtlsdr.dll.a` 已覆盖。**不需要**直接链接 DLL 本体（MinGW 链接 `librtlsdr.dll.a` 即可）。 |
| R2 | **查找路径变量** | 把 rtl-sdr 发布包根目录（含 `include/rtl-sdr.h` 与 `lib/librtlsdr.dll.a`）追加进 `-DCMAKE_PREFIX_PATH=...`；或显式 `-DRTLSDR_INCLUDE_DIR=... -DRTLSDR_LIBRARY=...\librtlsdr.dll.a`。configure 后必须看到第 428 行 STATUS：`RTL-SDR support: ENABLED (librtlsdr <version>)`。若显示 DISABLED，说明走了 stub（无真实设备支持，仅验证构建）。 |
| R3 | **运行时 DLL 拷贝** | `librtlsdr.dll`（即 rtlsdr 官方包的 bin/ 下 DLL）必须与 `mbdsdr.exe` **同目录**，否则运行时缺 DLL 直接起不来（导入库只解决链接期）。winddeployqt 不会拷贝它，需手动拷。 |
| R4 | **USB 后端链** | rtl-sdr Windows DLL 自身依赖 libusb（发布包内已随附），与 rtl-sdr DLL 同目录即可；仓库 CMake 不直接链接 libusb，无需处理。 |
| R5 | **SoapySDR（TX 可选）** | `cpp/src/tx/soapy_tx_backend.cpp:86` 在 Windows 走 `LoadLibraryA("SoapySDR.dll")` **运行时动态加载**：构建期不链接、不强制存在；缺失时 TX 后端优雅不可用。不列入必需部署项。 |

---

## 3. windeployqt 依赖清单核对

### 3.1 当前程序实际链接的 Qt 模块（grep 全仓 CMakeLists）

```
find_package(Qt6 REQUIRED COMPONENTS Core Gui Widgets Multimedia Network Concurrent Test)
```

按产物区分：

| 模块 | 谁链接 | 是否随 `mbdsdr.exe` 部署 |
|---|---|---|
| Qt6::Core | core PUBLIC / exe | **是** Qt6Core.dll |
| Qt6::Gui | core PUBLIC / exe | **是** Qt6Gui.dll |
| Qt6::Widgets | core PUBLIC / exe | **是** Qt6Widgets.dll |
| Qt6::Multimedia | core PUBLIC / exe | **是** Qt6Multimedia.dll + multimedia 插件目录 |
| Qt6::Network | core PUBLIC / exe | **是** Qt6Network.dll + tls 插件目录（Windows 走 Schannel，通常不依赖外部 OpenSSL DLL） |
| Qt6::Concurrent | core PUBLIC / exe | **是** Qt6Concurrent.dll |
| Qt6::Test | 仅 ctest 测试目标 | **否**（测试不随发布） |

### 3.2 与"旧已知部署清单"的差异核对

| 旧清单项 | 当前仓库事实 | 处置 |
|---|---|---|
| Qt6Core/Gui/Widgets | 实际链接 | 保留 |
| Qt6::Multimedia / Network | 实际链接 | 保留 |
| **Qt6::Concurrent** | 实际链接（core PUBLIC，第 247/415 行），**旧清单漏列** | **补入手动核对项**：windeployqt 按链接关系会自动带上 Qt6Concurrent.dll，但若人工拷贝需注意它也是独立 DLL。 |
| Qt6::Svg | 全仓 CMakeLists **无 Qt6::Svg**；源码 grep **无 QtSvg/QSvg/QIcon-from-file/QMovie**；0 个 .qrc | **旧清单此项已过时**：无需部署 Svg 插件（windeployqt 反正只拷有依赖的 DLL，留着无害但可从人工清单删除）。 |
| platforms / styles / tls / multimedia 插件 | windeployqt 自动按依赖拷贝（platforms/qwindows、styles、tls、multimedia） | 保留，由 windeployqt 处理 |
| imageformats 插件 | 源码无磁盘图片加载（无 QPixmap(QString)/QIcon(file) 命中），无 svg/png 资源 | 非必需；windeployqt 拷贝了也无害 |
| FFmpeg (avcodec/avutil…) | 源码无任何 libav* include；Qt 6.8 Multimedia 在 Windows 为原生后端 | 仅当 Qt 自带 ffmpeg 多媒体插件被加载时才需 av*.dll；冒烟测试音频播放若起不来再补。**非重构新增**。 |
| MinGW runtime (libgcc_s_seh / libstdc++-6 / libwinpthread-1) | 工具链运行时，非 Qt 产物 | windeployqt 部分场景可自动带上；保险起见手动拷齐或确认在 PATH。 |
| librtlsdr.dll | 见第 2 节 R3 | windeployqt 不覆盖，手动拷。 |

**重构后 core 库聚合的 Qt 模块 = 上述 6 个（Core/Gui/Widgets/Multimedia/Network/Concurrent），与重构前 exe 直接链接的模块集合完全相同 —— 没有因静态库聚合而新增需要部署的 Qt 模块。** 本轮 UI 改动（commit `1068d5d` 网络音频外送/活动扫描链/IQ 导出）引入的是 `dsp/network_audio_sink`、`dsp/scan_link` 两个后端，全部落在既有的 Network/Multimedia/Core 模块内，**无新 Qt 模块**。

---

## 4. Windows 侧逐步验证清单（全部从仓库事实推导）

前置假设：Qt 6.8.2 以 MinGW 套件安装（如 `.../Qt/6.8.2/mingw_64`），MinGW-w64 13.1 的 `mingw32-make`/`mingw64-make` 在 PATH；rtl-sdr 发布包已解压到某目录（含 include + lib + bin）。

1. **configure**（在 `D:\mbdsdr\MBDSDR\cpp` 下，二进制目录保持 `build` 命名以对齐 M2）：
   ```
   cmake -S . -B build -G "MinGW Makefiles" ^
     -DCMAKE_BUILD_TYPE=Release ^
     -DCMAKE_PREFIX_PATH="<Qt6.8.2 mingw_64 前缀>;<rtl-sdr 发布包根目录>"
   ```
   - 核对 configure 输出三条关键 STATUS：
     - `RTL-SDR support: ENABLED (librtlsdr …)`（R2；DISABLED 即 stub，真实设备不可用）；
     - `MBDSDR build platform: …`（信息行，不 fail）；
     - 无 moc/autogen 报错（M2）。
2. **构建**：
   ```
   cmake --build build -j
   ```
   - 预期产物：`build\mbdsdr.exe`。观察是否只有**一次** `mbdsdr_autogen` 参与（重构后语义）。
3. **冒烟测试（可选但推荐）**：
   ```
   cd build && ctest -R "startup_robustness|audio_sink|engine_integration" --output-on-failure
   ```
   - `startup_robustness`（CMakeLists 第 614–619 行 WIN32 分支）直接运行 `mbdsdr.exe --snapshot <png>`，
     是 Windows 上最划算的"链接 + 启动 + 退出"冒烟。
4. **部署**：
   - 在部署目录跑 `<Qt>\6.8.2\mingw_64\bin\windeployqt.exe mbdsdr.exe`（自动带 Qt6*.dll + platforms/styles/tls/multimedia 插件）；
   - 手动补：`librtlsdr.dll`（R3）、MinGW 运行时三 DLL（见 3.2）；
   - 核对部署目录内存在 Qt6Concurrent.dll（3.1 漏列补核）；**不出现** Qt6Svg.dll 属正常（3.2）。
5. **运行冒烟**：
   - 插好 RTL-SDR，启动 `mbdsdr.exe`，设备列表应枚举出真实 dongle（验证 R1–R3 端到端）；
   - 不插设备启动应进入诚实空态（"未连接"）而非崩溃 —— 与 startup_robustness 测试互为印证。

---

## 5. 差异点总评

| 维度 | 重构前（每目标独立 autogen） | 重构后（mbdsdr_core 集中） | Windows MinGW 是否需调整 |
|---|---|---|---|
| AUTOMOC | ~252 个 autogen 目标 | 1 个 core autogen + ~102 个单文件轻量 autogen | **否**（机制平台无关；仅 M2 保持 build 目录命名习惯） |
| 无 Q_OBJECT 目标 | 各自跑 autogen | 显式 OFF（59 处） | 否 |
| `-D_USE_MATH_DEFINES` | 全局 | 全局（未动） | 否 |
| librtlsdr 链接 | exe/目标各自 | core PUBLIC 聚合，自动传播 | 否（R1–R3 仅部署动作，非改码） |
| windeployqt 模块 | 6 个 Qt 模块 | 同 6 个（+补记 Concurrent；-删过时 Svg） | 否（清单认知更新，非构建改动） |

**总体：无已知"重构后 Windows 必须改 CMake/源码"的差异点；本文档即为"复核清单"性质，按第 4 节逐步验证即可。**

---

### 硬约束自查

- 零代码改动：仅新增本文件与 `ui-geometry-scan2.md`；未 git add/commit/push。
- 活动参数零硬编码；文档不预置 TLE/呼号；无竞赛/赛事类字样（已逐行自查）。
- 许可证措辞中立；未复制 `repos/` 下任何第三方源码。
