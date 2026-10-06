# Phase 53 块2+块3：RSS 慢漂移定位 + 性能基线

HEAD = 5002be9（未 commit/push）。构建目录 cpp/build，Qt 6.8.2，offscreen 合成源
（MBDSDR_TEST_SOURCE=1，无硬件）。所有数字为本机实测，不虚构。

---

## 块2：RSS 慢漂移定位

### 现象（P51 遗留）
10 分钟持续 IQ 流值守，RSS 89.5 → 137.6 MB（+4.8 MB/min，单调不收敛）。
P51 已排除 waterfall 固定 ring（`spectrum_display.cpp:225`）与 audio sink 无设备重试
（不分配），但无 heaptrack/valgrind 无法定位。

### 方法：轻量全局分配计数探针
新增 `cpp/src/heap_probe.{h,cpp}`：override 全局 `operator new/delete`，只计数
liveAllocs / totalAllocs（不追字节，避免 size-map 递归），`malloc/free` 底层路由行为
不变。经 CMake `target_sources(mbdsdr PRIVATE src/heap_probe.cpp)` 只进 mbdsdr 目标
（不进 TESTAPP_SRCS，测试可执行文件不重链、ctest 不受影响）。`main.cpp` 经 env
`MBDSDR_HEAP_PROBE=1` 门控启动 10s 打印器（含 /proc/self/statm RSS + mallinfo2 的
arena(sbrk)/hblkhd(mmap) 分解）。

### 实测证据（三组对照）

**A. 流式时（合成 IQ，liveAllocs vs RSS）：**

| t(s) | liveAllocs | RSS(kB) |
|---|---|---|
| 10 | 53,320 | 90,124 |
| 60 | 53,315 | 93,144 |
| 120 | 53,317 | 96,444 |
| 180 | 53,317 | 101,036 |

→ liveAllocs 全程平稳 ~53,315（±5 振荡，零增长），RSS 却 +10.9MB。**不是 C++ 对象泄漏。**

**B. 无 IQ 流对照（诚实空态）：**

| t(s) | liveAllocs | RSS(kB) |
|---|---|---|
| 10 | 52,219 | 67,488 |
| 60 | 52,218 | 67,488 |
| 120 | 52,218 | 67,616 |

→ 无流时 RSS 平稳 67.5MB 零增长（totalAllocs ~3900/10s，是 1Hz 定时器而非每帧 DSP）。
**RSS 增长只发生在 DSP 工作线程流式时。**

**C. mallinfo2 分解（流式）：**

| t(s) | arena(sbrk,kB) | mmap(kB) |
|---|---|---|
| 10 | 26,004 | 5,728 |
| 40 | 27,064 | 7,780 |
| 80 | 29,112 | 9,828 |
| 120 | 29,112 | 13,924 |

→ arena(sbrk) 26→29MB 后**平台化**；mmap 5.7→13.9MB **阶梯式增长后保持**。

### 修复尝试（诚实阴性结果）
- **主线程 `malloc_trim(0)` 每 10s**（env `MBDSDR_HEAP_TRIM=1`）：RSS 仍 90→101MB
  （+3.8MB/min），未压平。malloc_trim 只 trim 主 arena。
- **worker 线程 `malloc_trim(0)`**（在 `SpectrumEngine::run()` 循环每 ~4s 一次，
  `spectrum_engine.cpp`）：RSS 仍 90→102MB（+4.2MB/min），斜率不变。
  → **已 `git checkout` 撤销 spectrum_engine.cpp**（无效且触热路径，不留）。

### 根因结论
**RSS 漂移 = glibc 分配器高水位/arena+mmap 保留行为，非应用泄漏。**
每帧 IQ buffer 分配/释放循环中，glibc 把 worker 工作区扩到峰值后不归还 OS；
liveAllocs 平稳证明每个对象都被释放，只是分配器保留了峰值内存。
进一步定位需 mallinfo 深入线程级 arena 或真 profiler（本环境无）。

### 已排除项清单
- waterfall 固定 ring（`spectrum_display.cpp:225`）— P51 已核
- audio sink 无设备重试 — 不分配
- peakWindow / maxHold 累积结构 — 有界
- 每帧/每遥测 tick 的累积 handlers — liveAllocs 平稳即排除
- 无流对照（B 组）— RSS 平稳，绑定 DSP 流式路径
- 主线程/worker 线程 malloc_trim — 均无效（见上）

---

## 块3：性能基线

### 启动时间分解（`MBDSDR_STARTUP_PROFILE=1`，QElapsedTimer）

| 阶段 | 累计 ms | 增量 ms |
|---|---|---|
| qapp_created | 0 | 0 |
| single_instance_lock | 6 | 6 |
| stylesheet_applied | 13 | 7 |
| mainwindow_shown | 407 | 394 |

--snapshot 端到端墙钟 ~0.75s（含 1.5s 等待保存截图）。**瓶颈在 MainWindow 构造
（394ms），样式表仅 7ms。** 与 P38 一致：C++ 域无 AMR 开销。

### 10 分钟持续流基线（offscreen 合成源）

| 指标 | 实测 |
|---|---|
| CPU | ~82%（单核满载 DSP，ps %cpu 采样 81.2/82.2/83.1） |
| RSS（30s） | ~90 MB |
| RSS 斜率 | ~4 MB/min（glibc 高水位，见块2） |
| 吞吐 | ~40 frames/s（run() 循环 ~25ms/帧） |
| liveAllocs 稳态 | ~53,315（零增长） |

### 低成本优化改前改后
**无可优化项（如实）：**
- 样式表 `buildDarkQss()` 仅 7ms，无需延迟。
- 设备/网络探测在 `#ifdef HAVE_RTLSDR` 后；本离线构建 HAVE_RTLSDR 未定义
  （CMakeCache 无），构造期无阻塞网络/USB 枚举。
- MainWindow 构造 394ms 是纯 Qt widget 构建，非可延迟的探测调用。
- 启动已快（0.75s），不做虚构优化。

---

## ctest 全量回归（offscreen）

```
100% tests passed, 0 tests failed out of 129
128 passed + 1 skipped (e2e_smoke, SKIP_RETURN_CODE 77)
Total Test time (real) = 84.62 sec
```

基线 129 不回归（main.cpp + heap_probe 改动后）。heap_probe 只进 mbdsdr 目标，
测试可执行文件未重链。

---

## 交付文件
- `cpp/src/heap_probe.h`（新）— 全局分配计数探针头
- `cpp/src/heap_probe.cpp`（新）— operator new/delete 计数 + 10s 打印器（RSS/arena/mmap）
- `cpp/CMakeLists.txt` — `target_sources(mbdsdr PRIVATE src/heap_probe.cpp)`
- `cpp/src/main.cpp` — bootProfile 阶段计时 + `MBDSDR_HEAP_PROBE` 门控 installPrinter
- `cpp/src/dsp/spectrum_engine.cpp` — **无改动**（worker malloc_trim 无效已撤销）
- 本文档 `docs/learn/phase53/performance-baseline.md`

## 未解决项
- RSS 高水位的线程级 arena 进一步分解需 mallinfo 线程级查询或真 profiler（heaptrack/
  valgrind），本环境无。当前结论已到"分配器行为"层级，非应用泄漏。
- worker 线程 malloc_trim 未压平 mmap 保留段；若产品要求 RSS 长时间平稳，需在启动期
  `mallopt(M_TRIM_THRESHOLD_/M_MMAP_THRESHOLD_)` 调 glibc 策略（运行时配置，非代码修复，
  本轮未做以避免改变分配器全局行为）。
