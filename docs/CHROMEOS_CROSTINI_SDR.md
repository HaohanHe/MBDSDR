# 在 Chromebook（ChromeOS / Crostini）上运行 MBDSDR（C++ Qt6 + ControlHub 时代）

适用：x86_64 Chromebook（如 Dell Latitude 5400 / C640 一类 Intel 机型）。
ChromeOS 不直接支持 RTL-SDR，需要在 **Crostini（Linux 开发环境，Debian 容器）** 里运行，
并把 USB 设备透传进 Linux。整个过程不需要进入开发者模式、不影响系统安全。

> 本文以产品当前主线为准：桌面引擎是 **C++ Qt6 应用 `mbdsdr`（`cpp/`）**，
> 另有一条 **ControlHub HTTP 控制通道**（默认 `127.0.0.1:50732`）供浏览器/移动端远程控制。
> 旧文档里的 Python 后端（PySide6 / `desktop/main.py` / `rtl_selfcheck.py` / `--sim`）已不存在，
> 不再适用。

ARM 架构 Chromebook 见文末 **第 9 节**——**截至本文，arm64 路径未在真机实测**，只给依赖清单与验证动作，结论以真机记录为准。

---

## 0. 两种部署拓扑（先选一种）

**拓扑 A：一体化（Chromebook 既是接收机又是控制端）**

```
[Chromebook / Crostini (Debian, x86_64)]
   ├─ USB 透传 ── RTL2832U 棒 (VID 0bda / PID 2838)
   ├─ mbdsdr (C++ Qt6 桌面引擎) ── 直接打开本机 librtlsdr
   └─ mbdsdr 内置控制 HTTP ── 127.0.0.1:50732（本机浏览器打开即可）
```

**拓扑 B：Chromebook 当远程控制端，接收机跑在另一台机器上**

```
[接收机（桌面/另一台 Linux）]                [Chromebook / 手机]
   ├─ RTL2832U 棒（本地 USB）                 └─ 浏览器 App
   ├─ mbdsdr 桌面引擎                              ↑
   └─ 控制 HTTP 绑定在「接收机的 127.0.0.1:50732」  │
        （仅回环、无鉴权，见第 6 节）           隧道转发
        ◄────── SSH -L 50732:127.0.0.1:50732 ────┘
```

> 重要事实（来自代码）：控制 HTTP 端点**只绑定本机回环 `127.0.0.1`，刻意不绑 `0.0.0.0`**，
> 且**无鉴权**。因此它**不能直接用接收机的局域网 IP 访问**；远程控制必须像拓扑 B 那样
> 自己建一条隧道（SSH 端口转发），再在控制端打开隧道映射出来的 `127.0.0.1:50732`。
> 不要为了省事把它暴露到局域网/公网。

---

## 1. 开启 Linux 开发环境

设置 → 高级 → 开发者 → Linux 开发环境 → 开启。首次会下载并创建 Debian 容器，
完成后得到“终端”App。

## 2. 安装构建与运行依赖

```bash
sudo apt update
sudo apt install -y git cmake build-essential pkg-config \
    qt6-base-dev librtlsdr-dev rtl-sdr
```

各包用途（对应 `cpp/CMakeLists.txt` 的真实依赖）：

| 包 | 用途 |
|---|---|
| `cmake` / `build-essential` | 配置与编译 `cpp/` |
| `qt6-base-dev` | `find_package(Qt6 REQUIRED COMPONENTS Core Gui Widgets Multimedia Network Concurrent Test)` 的全部组件来源 |
| `librtlsdr-dev` | 本机 USB 直收：`pkg_check_modules(RTLSDR librtlsdr)` / `find_path(rtl-sdr.h)` 命中后打开真实设备 |
| `rtl-sdr` | 提供 `rtl_test`（验棒）与 `rtl_tcp`（网络兜底服务端）命令 |
| `pkg-config` | CMake 用 PkgConfig 探测 `librtlsdr` |

> **没装 `librtlsdr-dev` 也能编过**：CMake 找不到库时不会报错退出，而是自动编译
> RTL-SDR 的 stub 实现并打印 `RTL-SDR support: DISABLED (librtlsdr not found; stub compiled)`。
> 此时 `mbdsdr` 能启动、能跑界面，但打不开本机 USB 棒（只能用 rtl_tcp 网络源或离线文件）。
> 要本机直收，必须装上 `librtlsdr-dev` 并看到 `RTL-SDR support: ENABLED`。

## 3. 把 RTL-SDR 棒透传给 Linux（关键步骤）

ChromeOS 出于安全默认不把 USB 共享给 Linux：

1. 插好 RTL2832U 棒。
2. 设置 → 高级 → 开发者 → Linux 开发环境 → **USB 设备 / Manage USB devices**。
3. 在列表里勾选 Realtek 半导体设备（VID `0bda`，PID `2838`）。
4. 设备随即出现在 Linux 容器的 `/dev/bus/usb` 下。

注意：

- 每次重新插拔、或重启后，可能需要重新勾选一次。
- 透传后在终端执行 `lsusb`，应能看到 `Realtek Semiconductor Corp. RTL2838 DVB-T`。

## 4. 验证棒能被打开

```bash
rtl_test -t
```

正常会打印：

- `Found 1 device(s): 0: Realtek, RTL2838UHIDIR ...`
- 调谐器型号（廉价棒常见 `FC0012` / `FC0013` / `R820T`）
- 一组增益值

看到这些就说明 USB 透传和驱动都正常。`PLL not locked` 之类的个别告警可忽略。

### 如果提示设备被占用 / Permission denied

Crostini 是隔离 VM，通常不需要像普通 Linux 那样 blacklist `dvb_usb_rtl28xxu`。按下面顺序处理：

1. 拔掉棒，在 USB 设备列表里取消勾选，再重新勾选、重插。
2. 确认没有别的程序占用（关掉容器里其它 SDR 软件，包括还在跑的 `rtl_tcp`）。
3. 仍报权限问题，把当前用户加入对应组并重开终端：
   ```bash
   sudo usermod -aG plugdev,audio,video $USER
   ```
   然后重启 Crostini（设置里关闭 Linux 再打开）。
4. 实在透传不稳定，用第 7 节的 `rtl_tcp` 网络方式兜底。

## 5. 拉取项目并构建 C++ 桌面引擎

```bash
git clone https://github.com/HaohanHe/MBDSDR.git
cd MBDSDR/cpp

cmake -B build -S .
cmake --build build -j"$(nproc)"
```

构建时盯一眼 CMake 输出里这两行（决定本机 USB 能不能用）：

- `RTL-SDR support: ENABLED (librtlsdr ...)` —— 本机直收可用。
- `RTL-SDR support: DISABLED (... stub compiled)` —— 回到第 2 节补 `librtlsdr-dev` 再重跑 `cmake -B build`。

产物是可执行文件 **`mbdsdr`**（`add_executable(mbdsdr ...)`，输出目录即构建目录）：

```bash
./build/mbdsdr
```

Crostini 默认支持 Wayland/X11 转发，Linux GUI 窗口会自动出现在 ChromeOS 启动器里。

## 6. ControlHub 远程控制通道（一等公民）

`mbdsdr` 启动时会在主窗口构造完后自动拉起一个 HTTP 控制端点（`MainWindow::setupControlHttpServer`），
主窗口顶栏有一个“控制HTTP”状态徽章显示它当前绑在哪。

**端口解析顺序**（`HttpControlServer::startDefault`，从高到低）：

1. 环境变量 `MBDSDR_CONTROL_HTTP_PORT`；
2. QSettings 键 `control/httpPort`；
3. 默认常量 `50732`（`tokens::kControlHttpDefaultPort`）。

**绑定行为（务必理解）**：服务端用 `QHostAddress::LocalHost` 绑定，**只听回环**，
`never Any/AnyIPv4`。端点**无鉴权**，横幅明确提示“仅限本进程/本机访问，请勿暴露到局域网或公网”。

| 配置意图 | 做法 |
|---|---|
| 换默认端口 | `MBDSDR_CONTROL_HTTP_PORT=50780 ./build/mbdsdr`，或写 QSettings `control/httpPort` 后重启 |
| 本机浏览器查看 | 直接开 `http://127.0.0.1:50732/` |
| 端口被占 | 不会静默换端口（避免移动端连错）；徽章变黄并提示换端口重启 |

**端点清单**（`GET /` 会返回一份诚实的端点列表）：

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/` | 发现文档（列出全部端点） |
| GET | `/status` | 引擎当前状态（频率、采样率、信号等真实字段） |
| GET | `/pocsag_messages?channel=N` | POCSAG 解码消息（只读，无数据时为空态） |
| GET | `/m17_calls?channel=N` | M17 呼叫（只读） |
| GET | `/vor_radial?channel=N` | VOR 径向（只读） |
| POST | `/command` | 下发控制命令（如 set_frequency），走真实引擎 |

**远程（拓扑 B：Chromebook / 手机控制另一台接收机）**——因为端点只绑接收机回环，
在控制端先建 SSH 隧道，再访问本地映射口：

```bash
# 在 Chromebook 上执行（receiver 换成接收机地址）
ssh -N -L 50732:127.0.0.1:50732 user@receiver
# 保持连接，然后在 Chromebook 浏览器打开：
#   http://127.0.0.1:50732/
```

配套的移动端查看器（`mobile/` Flutter 端）在设置里填同一主机/端口；在回环-only 的安全模型下，
手机同样应经隧道或同一本机回环访问，不要把该端口直接暴露出去。

## 7. 兜底：rtl_tcp 网络模式（对接 C++ 后端）

当 Crostini 的 USB 透传不稳定，或你想让多台设备共用同一根棒时，在
**能直接访问棒的那台机器**上启动服务端：

```bash
rtl_tcp -a 0.0.0.0 -p 1234
```

然后在 `mbdsdr` 桌面界面的**源类型下拉框**里选「**rtl_tcp 远程**」，填入服务端的 host / port
（默认 `127.0.0.1:1234`）即可。背后对接的真实接口是：

- 引擎：`SpectrumEngine::connectRtlTcp(const QString& host, quint16 port)`；
- 源实现：`RtlTcpSource(QString host = "127.0.0.1", quint16 port = 1234)`（`cpp/src/dsp/rtl_tcp_source.h`）。

行为是诚实的：连接失败不会造数据——`isConnected()` 保持 false、`readIQ` 返回 0，
引擎回退到离线测试信号并通过 `sourceChanged` / `sourceError` 报出真实原因（如
“Connection refused”“timed out”）。rtl_tcp 模式不依赖本机 librtlsdr 直接打开 USB，
跨平台最稳，也是 ARM 上暂无 `librtlsdr` 时最现实的取流方式。界面还提供 rtl_tcp **自动重连**选项。

> 棒的身份信息来自 `rtl_tcp` 服务端 accept 后发送的 12 字节 `RTL0` 握手（调谐器型号/增益档数）；
> 收不到握手时，调谐器型号诚实显示为“未知”，不会猜。

---

## 8. 故障速查（C++ Qt6 时代）

| 现象 | 处理 |
|---|---|
| `lsusb` 看不到棒 | 没做 USB 透传，回第 3 步勾选设备 |
| `rtl_test` 找不到设备 | 重插并重新透传；确认 VID 0bda PID 2838 |
| 设备被占用 | 关闭其它 SDR 程序（含 `rtl_tcp`）；取消勾选再重勾；必要时重启 Linux |
| `cmake` 报 `Could not find Qt6` / `Qt6xxx not found` | 没装 `qt6-base-dev`（或缺 Multimedia/Network 组件），回第 2 步补装后重跑 `cmake -B build` |
| `cmake` 配置过但启动后打不开 USB 棒 | 看构建日志是不是 `RTL-SDR support: DISABLED ... stub compiled`；装 `librtlsdr-dev` 后**重新跑 cmake**（不是只重编译） |
| `librtlsdr.h: No such file` / 链接 `-lrtlsdr` 失败 | 同上行：`librtlsdr-dev` 缺失或 cmake 缓存了旧的探测结果，删 `build/` 重来 |
| `Permission denied` 打开 `/dev/bus/usb/*` | 按第 4 节加 `plugdev,audio,video` 组并重启 Crostini |
| FM 段全是平噪 | 接天线、换 USB 口、确认频点在当地广播频率 |
| 频率整体偏移 | 廉价棒晶振偏差，用界面里的 ppm 校正 |
| 想收短波 (<24MHz) | 需 direct sampling 或上变频器 |
| 顶栏“控制HTTP”变黄 / 端口被占 | 用 `MBDSDR_CONTROL_HTTP_PORT` 换端口重启；注意它**不会**自动改绑 |
| 手机/另一台机器打不开 `http://接收机IP:50732` | 正常——端点只绑回环；按第 6 节建 SSH 隧道，或改用同机回环 |
| rtl_tcp 源连不上 | 确认服务端 `rtl_tcp -a 0.0.0.0 -p 1234` 已起、host/port 填对、防火墙放行了 1234 |

---

## 9. ARM（arm64）Chromebook —— 未实测，先给验证路径

> **状态（2026-10-05 更新）：云端已用交叉工具链 + qemu-aarch64 真正交叉编译并实跑通过一批 arm64 测试目标（见文末「验证记录」）；物理 arm64 Chromebook 真机仍未验证。**
> 本节列出依赖清单、验证动作与已知边界，**不写"应该能跑"**；任何结论以真机记录为准。
> Phase30 B 块（arm64 构建能力探测）的真机/容器验证结果，将回填到本节下方「验证记录」。

**arm64 预期依赖（与 x86_64 同源，仅架构不同）**

```bash
sudo apt install -y git cmake build-essential pkg-config \
    qt6-base-dev librtlsdr-dev rtl-sdr
```

风险点：Debian arm64 仓库里 `qt6-base-dev` 与 `librtlsdr-dev` 是否齐全、版本是否够新，
取决于具体 Chromebook 容器的 Debian 版本，需现场确认。

**真机验证动作（按顺序）**

```bash
uname -m                      # 应输出 aarch64，确认容器真在 ARM 上
sudo apt update
sudo apt install -y git cmake build-essential pkg-config qt6-base-dev librtlsdr-dev rtl-sdr
cd MBDSDR/cpp
cmake -B build -S .           # 观察 Qt6 与 librtlsdr 是否被找到
cmake --build build -j"$(nproc)"
./build/mbdsdr
```

**已知边界（代码层面确定的事实，非推测）**

- **`librtlsdr` 缺失不会让构建失败**：CMake 找不到库就编 stub，产物能启动但打不开本机 USB 棒。
  arm64 上若拿不到 `librtlsdr-dev`，这是最可能落到的形态——届时走第 7 节 rtl_tcp 网络取流。
- **高采样率驱动限制**：RTL2832U 在 ARM 小核 / USB 带宽紧张时，高采样率（~2.4Msps 以上）
  容易丢包，需现场观察；这是硬件/带宽问题，不是 CMake 能解决的。
- 当前 `cpp/CMakeLists.txt` 尚无架构专属代码路径（保持通用）；arm64 与 x86_64 走同一套构建逻辑。

**验证记录**

- **B 块（arm64 能力探测）底稿**：`docs/learn/phase30/arm64-verification-notes.md`。
  摘要：云端为 x86_64，qemu-aarch64 / aarch64 交叉工具链 / docker 容器三条路径全断且 `/usr` 只读无法补装，
  **未实跑 arm64 构建**（不伪造通过）；已真实验证的仅是新增的 `CMAKE_SYSTEM_PROCESSOR` 探测块在 x86 host 上
  配置干净、对 x86_64 / aarch64 / arm64 / 未知输入分支正确、零报错。真机（arm64 机器出现后）照本节
  「真机验证动作」并加 `QT_QPA_PLATFORM=offscreen ctest --output-on-failure` 执行；6 项未验证清单见底稿 §6。

- **✅ 云端已交叉编译验证（Phase44，2026-10-05）**：在 x86_64 云主机上用
  Arm GNU 13.3.Rel1（`aarch64-none-linux-gnu`）交叉工具链 + `qemu-aarch64` 10.0.13 用户态模拟，
  配合手写解压的 Debian trixie arm64 sysroot（Qt **6.8.2**，与 x86 host Qt 同版本；
  191 个 .deb，`ar x`+`tar xf` 到 `~/.local/arm-cross/sysroot-qt`，469M；含 Qt6 Core/Gui/Widgets/Network/
  Test/Multimedia + librtlsdr0），把非 Qt / 纯算法 / QtTest / offscreen UI 目标真正编成 aarch64 ELF 并在
  qemu 下实跑通过。**这是云端模拟验证，不是物理 arm64 Chromebook 真机**——USB 直收 / 真机 rtl_tcp 表现仍待真机。
  - 入库的只有 `cpp/cmake/aarch64-linux-gnu.cmake`；工具链/qemu/sysroot 均在 `~/.local`，不入库。
  - 配置实测命中：`MBDSDR build platform: CMAKE_SYSTEM_PROCESSOR=aarch64 (arm64)`、
    `RTL-SDR support: ENABLED (librtlsdr )`、`Qt6 version: 6.8.2`、`Configuring done`（EXIT=0）。
  - **qemu 实跑通过的测试目标（全部 exit=0）**：`test_ssdv_packet`、`test_sstv_vis`、`test_pocsag_decoder`、
    `test_m17_decoder`、`test_adsb_cpr`（纯 C++/协议，all-green），`test_agc_dsp`(6)、`test_power_spectrum_window`(5)、
    `test_spectrum_maxhold`(5)、`test_fft`(6)、`test_peak_detector`(4)、`test_noise_blanker`(4)（QtTest，
    合计 30 个 case 全 PASS），`test_sgp4`（15 个轨道状态全 PASS）。
  - qemu 跑 Qt GUI 测试需 `QT_QPA_PLATFORM=offscreen QT_PLUGIN_PATH=<sysroot>/usr/lib/aarch64-linux-gnu/qt6/plugins`。
  - **剩余缺口（诚实）**：完整 `mbdsdr` 图形主程序未在 arm64 全量编译（仅编了测试/核心目标；全树编译的内存/时间预算未走）；
    无物理 arm64 硬件，USB/rtl_tcp 真机行为未验证；configure 有一条非致命 `Could NOT find XKB` 告警（offscreen 不受影响）。
