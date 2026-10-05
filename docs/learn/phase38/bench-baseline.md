# Phase38 · 阶段1 性能基准（先测后改）

> 本文件是**优化前**的真实基线。所有数字均为本机实测，未改动任何 `cpp/` 产品源码。
> 测量日期：2026-10-05。测量方式：在仓库源码树**之外**编译只读计时壳，链接 `build_ui` 已编译对象。

## 0. 红线遵守

- 未修改任何 `cpp/src/**` 产品代码（优化属阶段2）。
- 无假数据：下列数字全部来自 `QElapsedTimer` 实测 + `resource.getrusage` 峰值 RSS。
- 只新增本文件（bench harness 位于 agent 工作区，不在仓库源码树内）；未 `git add`、未 commit/push。

## 1. 测量环境

| 项 | 值 |
|---|---|
| CPU | AMD EPYC 9Y24，**cgroup 限 4 vCPU** |
| 内存 | 7.9 GiB，**无 swap**（与 8GB cgroup OOM 约束一致） |
| 编译器 | `/usr/bin/g++` |
| 构建类型 | **Release `-O3 -DNDEBUG`**（产品对象已如此编译；bench 壳本身 `-O2 -std=c++17`） |
| Qt | 6.8.2（`/home/user/Qt/6.8.2/gcc_64`） |
| 离屏 | `QT_QPA_PLATFORM=offscreen` |
| 峰值内存 | 无 `/usr/bin/time`、无 perf/valgrind/gdb；改用 Python `resource.getrusage(RUSAGE_CHILDREN).ru_maxrss`（Linux 单位 KiB，报告 MiB） |
| 计时 | 进程内 `QElapsedTimer`（nsecs），段内自带 warmup |

注意：本机**没有** perf/gprof/valgrind，因此无法做函数级火焰图；分段靠"段间 QElapsedTimer + 源码走读"分解，已在下文如实标注哪段是实测、哪段是源码推断。

## 2. 可复跑命令

```bash
cd cpp
# 产品 Release 已构建于 build_ui（Qt6.8.2, offscreen）。
# bench 壳链接 build_ui/CMakeFiles/ui_shot_timing.dir 下全部 .o（替换其自带 main）：
#
#   g++ -O2 -std=c++17 -I cpp/src -I build_ui/ui_shot_timing_autogen/include \
#       -I build/mbdsdr_autogen/include <qt6 -isystem paths> -c bench.cpp -o bench.o
#   g++ bench.o <所有 ui_shot_timing.dir/*.o，除 ui_screenshot_timing.cpp.o> \
#       -o bench -Wl,-rpath,$QT/lib -lQt6Widgets -lQt6Multimedia -lQt6Network \
#       -lQt6Concurrent -lQt6Gui -lQt6Core -lGL
#
# 跑四场景：
export QT_QPA_PLATFORM=offscreen
./bench_dsp     # 场景2/3/4（FFT 吞吐 / 解调链 / agent 初始化）
./bench_ui      # 场景1（MainWindow 构造分段）
```

口径：每个场景跑 **7 次**，取中位数；报告同时给最小/最大。段内先 warmup 再计时。

---

## 3. 四场景真实基线

### 场景 1 — MainWindow 构造（offscreen）

`new MainWindow()` + `show()`，离屏。7 次 warm 运行（冷启动仅首次，QApp 首跑 115ms、后续 ~35ms）：

| 段 | 中位耗时 | 说明 |
|---|---:|---|
| `QApplication` 启动 | **~35 ms** | Qt GUI 初始化（冷首跑 ~115ms） |
| `tokens::buildDarkQss()` 生成样式表 | **~0.13 ms (128 µs)** | 样式表**生成**成本，可忽略 |
| `MainWindow` 构造函数本体 | **~210 ms** | 见下方分解 |
| `win->show()` | **~8 ms** | 首次离屏 show |
| **构造+show 合计** | **~218 ms** | 整进程墙钟中位 **0.34 s** |
| **进程峰值 RSS** | **~60 MiB** | ru_maxrss |

构造函数本体（~210ms）进一步分解（实测/推断）：
- **样式表**：构造内 `setStyleSheet(buildDarkQss())`（`main_window.cpp:167`）——样式表生成本身仅 128µs，QSS 应用到控件树的成本包含在 210ms 内，非热点。
- **网络探测**：C++ 构造期**没有任何阻塞网络调用**。源码走读确认：无 `QTcpSocket::connectToHost`/阻塞 `lookupHost`；尾部 `setupControlHttpServer()`（`main_window.cpp:2977`）仅 `bind` 本机回环 `127.0.0.1`（绑定瞬时、端口冲突只翻琥珀色 chip）。TLE（`main_window.cpp:2904 refetchTle()`）走本地缓存/`TleClient::builtinTle()` 的本地 SGP4 推算，网络抓取在 QTimer 里异步进行。**→ 网络探测 ≈ 0ms 阻塞**。
- **AMR / AI agent**：构造内 `new ai::Agent(this)`（`main_window.cpp:2488`），见场景4。
- 剩余 ~200ms 为：整棵控件树（~50 widget/panel）+ `new SpectrumEngine` + 编译期内嵌的大数据（`coastline_data.h` 109KB、world/sky 视图、builtin TLE）。受工具所限（无 perf），此块**未能**再细拆到具体 widget——如实记录为阶段2的待 profile 点。

> **关键反差**：阶段背景假设"MainWindow 构造 ~11s（AMR ~5s + 样式表 + 0.5s 网络探测）"来自 **Python/PyQt 旧栈**（`docs/learn/phase10/P4-stability.md`：`amr.py:369 _load_builtin_training_data` ×128 帧 ×25 维特征 ≈4.92s、`_probe_network_default` 0.5s）。**C++ 移植版实测构造仅 ~0.22s，那个 11s/AMR 5s 的热点在 C++ 路径上已不复存在。**

### 场景 2 — 频谱引擎 FFT 吞吐

生产路径 = `dsp::PowerSpectrum::process()`（`spectrum_engine.cpp:1130` 实际调用），内部 `fft.cpp` radix-2 + Hann 窗 + fftshift + 归一化 dBFS。生产 FFT 尺寸仅允许 1024/2048/4096，**默认 2048**（`spectrum_engine.h:494 fftSize_{2048}`）。每尺寸 4000 帧：

| FFT 尺寸 | µs/帧 | 帧/秒 | 吞吐 |
|---:|---:|---:|---:|
| 1024 | 28.6 | 34 957 | 35.8 Msps |
| **2048（默认）** | **60.9** | **16 418** | **33.6 Msps** |
| 4096 | 130.6 | 7 655 | 31.3 Msps |

整进程峰值 RSS（FFT+解调+agent 负载）：**~31.6 MiB**。
（附注：`fft.cpp` 为无外部依赖的朴素 radix-2，每级现场算旋转因子；吞吐随 N 增长近线性，N=4096 时 130µs/帧对实时频谱远够用。）

### 场景 3 — 解调链吞吐

合成带噪 IQ（cf32），按 8192 样本分块流式 `process()`，含 warmup + `reset()`：

| 解调 | IF 采样率 | 样本数 | 吞吐 | 每样本 |
|---|---:|---:|---:|---:|
| WFM（广播 FM） | 250 kS/s | 1 000 000 | **26.0 Msps** | 38.5 ns |
| NFM（窄带 FM） | 48 kS/s | 192 000 | **25.8 Msps** | 38.8 ns |
| AM（包络） | 48 kS/s | 192 000 | **12.0 Msps** | 83.3 ns |

（AM 较慢因其 `FirLowpass` + 载波 AGC；WFM/NFM 均 ~26 Msps，远超 250kS/s 实时需求（裕量 ~100×）。）

### 场景 4 — AI agent 初始化（AMR）

`mbdsdr::ai::Agent` 构造 30 次（含一次 warmup）：

| 指标 | 值 |
|---|---:|
| 单次 `new ai::Agent()` | **~4.4 ms** |

**AMR 热点结论（特征计算 or 模型加载？）——两者都不是，因为 C++ 里根本没有 AMR：**
- C++ `ai::Agent` 构造（`cpp/src/ai/agent.cpp`）只做：`AiConfig.load()` + 读 QSettings + `new LLMWorker()` + `moveToThread` + 起 `QThread`，**无任何内置训练数据、无 25 维特征提取、无 KNN、无模型文件加载**。
- 全树 grep（`modulation recognition / knn / extract_feature / builtin_train / .onnx/.pt/.pb / loadModel`）在 `cpp/src` 下**零命中**（唯一 "pty" 命中是 RDS 节目类型码，无关）。
- 结论：Python 时代 `amr.py:369 _load_builtin_training_data`（×128 帧 × `extract_features_from_iq` ≈4.5s CPU 绑定）这个 ~5s 热点，在 C++ 移植中**未被移植/已移除**。C++ agent 初始化的成本就是"起一个工作线程"≈4.4ms，既不是特征计算也不是模型加载——**没有那个负载**。

---

## 4. 汇总（前值落盘）

| 场景 | 时间（中位） | 峰值 RSS |
|---|---:|---:|
| 1 MainWindow 构造+show（offscreen） | **~218 ms**（构造 ~210 / QApp ~35 / show ~8 / 样式表生成 0.13 / 网络探测 ≈0） | ~60 MiB |
| 2 频谱 FFT（PowerSpectrum, N=2048 默认） | **60.9 µs/帧 · 16.4k 帧/s · 33.6 Msps** | ~31.6 MiB（整进程） |
| 3 解调链 WFM / NFM / AM | **26.0 / 25.8 / 12.0 Msps** | 同上 |
| 4 AI agent 初始化 | **4.4 ms/次**（无 AMR 特征提取/模型加载） | 同上 |

## 5. 未解决项 / 局限

1. **构造 ~210ms 的内部未细拆**：本机无 perf/gprof，无法定位是哪个 widget/数据加载最贵；阶段2 若需，建议加 `perf` 或临时在构造内插桩（须经批准、且属"先测后改"的改）。
2. **"样式表"段只测了 `buildDarkQss()` 生成本身（128µs）**；QSS 应用到控件树的成本并入构造块，未单列。
3. **峰值 RSS 为整进程 ru_maxrss**，非单场景分项；四场景中 GUI(MainWindow) ~60MiB、headless DSP ~32MiB。
4. **样本数/口径**：FFT 4000 帧/尺寸、解调各 ~1e6 样本、agent 30 次、UI 7 次；取中位。首跑冷启动（页缓存/QApp）偏高，已用 warm 后口径。
5. **环境依赖**：offscreen、Release -O3、Qt6.8.2、4 vCPU/7.9GiB cgroup；换机需重测。
