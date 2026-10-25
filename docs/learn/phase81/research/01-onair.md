# 参考架构研究 01 — OnAir（SDR 接收机）三层架构 / `/api/v1/*` 工具面 / 插件机制

> 研究报告（只读，未修改 `/workspace/MBDSDR` 任何代码）
> 研究对象：`/workspace/reference/OnAir-main`（40 MB）
> 生成日期：2026-10-26

---

## 摘要

**先纠正一个身份问题（重要，影响后面所有结论的可信度）。**

任务书假定 `/workspace/reference/OnAir-main` 是 **NASA 的 OnAir**（Python 遥测/AI 框架，含 `onair/` 与 `onair_test/` 目录）。**实测不是**：

| 断言 | 实测结论 | 证据 |
|---|---|---|
| NASA onair（Python） | **未找到**。全仓无 `onair/` 或 `onair_test/` Python 包；唯一的 `onair` 目录是 `rust/onair`（Rust crate，协议层） | `find . -maxdepth 2 -name onair` → 只有 `./rust/onair` |
| `/workspace/reference` 下另有 NASA 版 | **未找到**。`/workspace/reference/OnAir`（仅 `.git`）、`OnAir-src`（仅 5 个 md）、`onair.tar.gz`（解出就是 `OnAir-main/`）都是同一个项目 | `tar tzf onair.tar.gz \| head` |
| 实际是什么 | **`github.com/adswill/OnAir` v0.2.7**（GPL-3.0，C++20，跨平台 SDR 接收机，README 自称 “120 modes”）。官网 `on-air.dev` | `README.md:1-30`、`ARCHITECTURE.md:3` |

**结论：研究对象是 `adswill/OnAir`（C++20 桌面 SDR），不是 NASA OnAir（Python）。** 这对 MBDSDR 反而是好消息：它是**同赛道、同语言族（C++）、同形态（Qt/ImGui 桌面 + headless server）**的成熟工程，可比性远高于一个 Python 遥测框架。本报告按“实际存在的东西”作答，不按 NASA 前提编造。

三个点名的特性**确实都存在**，只是名字和数量跟任务书不一样：

1. **分层**：`ARCHITECTURE.md:3` 原文写的是 **two layers**（`core/` 库 + `app/` GUI），不是三层。但按 CMake 构建产物看，实际是 **1 个核心库 + 3 类宿主 + 1 个扩展 ABI**，可以整理成“三层”。
2. **`/api/v1/*`**：**真实存在**，集中在 `core/src/net/remote_api.cpp`（31 KB）。但**端点很少**（13 个 REST + 1 WebSocket + 3 个 server 专属），不是“工具面”的那种几十上百个 endpoint —— 它是一个**遥控面**（remote-control API），不是工具注册表。
3. **“158 模式插件”**：**数字对不上**。插件机制真名是 **OnAir Plugin ABI v1**（`sdk/include/onair_plugin.h`，纯 C ABI，CC0 公有领域）。内置模式表实测 **88 条**（去重 87）+ 32 条 SubMode；SDK 里只有 **3 个 C++ 示例 + 1 个 Rust 示例 + 1 个模板**，全仓 **20 个文件**。仓库里唯一出现的 `158` 是灰色 RGB `IM_COL(150,154,158)`。**详见第 4 节的实测口径表。**

**对 MBDSDR 的总判断**：MBDSDR 在“工具注册表”这件事上**已经比 OnAir 做得更细**（90 条声明式 spec + 109 条命令表 + 读/写门禁 + 线程编组），真正缺的是 **①/api/v1 版本化自描述面 ②统一的错误信封 ③插件式模块注册表（OnAir 的 `registerPluginTuning` + `manifest.json` 是最值得抄的一块）④二进制频谱帧**。

---

## 1. 三层架构

### 1.1 官方说法 vs 实测

`ARCHITECTURE.md:3` 原文：

> OnAir is a C++20 program in **two layers**: a **receiver library** (`core/`) that knows nothing about windows, and a **graphical app** (`app/`) that draws what the library reports. The command-line tools in `tools/` use the same library.

但 `CMakeLists.txt` 的实际目标不止两个：

```cmake
CMakeLists.txt:133  add_library(dect2core STATIC core/src/... )     # 核心库
CMakeLists.txt:238  add_library(dect2_plugin STATIC core/src/plugin/*.cpp)
CMakeLists.txt:230  add_library(dect2_automation STATIC core/src/automation/*.cpp)
CMakeLists.txt:199  add_library(dect2track  STATIC core/src/track/*.cpp)
CMakeLists.txt:245  add_executable(onair-server server/main.cpp server/server_config.cpp server/server_host.cpp)
CMakeLists.txt:242  add_executable(onair-plugin-host tools/onair_plugin_host.cpp)
CMakeLists.txt:518  add_executable(dect2 ${APP_SOURCES})            # GUI 主程序
```

### 1.2 整理成三层（本报告采用的口径）

```
┌───────────────────────────────────────────────────────────────────────────┐
│ 第 3 层  扩展层  Extension ABI                                             │
│   sdk/include/onair_plugin.h   —— 纯 C ABI v1（CC0 公有领域）              │
│   sdk/template/ sdk/examples/（3 个 C++ + 1 个 Rust）                      │
│   .so/.dylib/.dll + manifest.json；进程内 或 onair-plugin-host 辅助进程    │
│   ↑ 依赖方向：插件只依赖 ABI 头文件，不依赖 OnAir 任何其它头/代码           │
└───────────────────────────────────────────────────────────────────────────┘
                     ↑ dlopen + registerPluginTuning()
┌───────────────────────────────────────────────────────────────────────────┐
│ 第 2 层  宿主层  Hosts / Front-ends（每类宿主都实现同一组抽象接口）         │
│   app/      GUI 程序 dect2（ImGui）        → app/platform/remote_api_app.cpp│
│   server/   headless onair-server          → server/server_host.h          │
│   tools/    dect2cli / dect2scan（本压缩包中 tools/ 目录缺失，见开放问题）  │
│   外部      Android bridge（仓库外，只用 core/include + dect2core）         │
│                                                                            │
│   宿主必须实现的抽象接口：dect2::RemoteApiHost（纯虚基类，12 个方法）       │
└───────────────────────────────────────────────────────────────────────────┘
                     ↑ 直接函数调用 + 虚函数回调 + 每帧 Job 队列
┌───────────────────────────────────────────────────────────────────────────┐
│ 第 1 层  核心库  dect2core（不知道"窗口"的存在）                            │
│   core/include/dect2/<folder>/<name>.h   公开头（与 core/src 同目录名）     │
│   core/src/engine/     Engine 分析线程、模式表、扫描器、遥测                │
│   core/src/source/     收音机驱动（HackRF/RTL/Soapy/网络电台/IQ 文件…）     │
│   core/src/<mode>/     每种解码一个目录（124 个目录，92 个 *_rx.cpp）       │
│   core/src/net/        remote_api.cpp / nettuner / share_server / feeds    │
│   core/src/plugin/     插件宿主（8 个文件）                                │
└───────────────────────────────────────────────────────────────────────────┘
```

### 1.3 层间通信：不是消息队列，也不是事件总线

这是最值得注意的一点。**OnAir 层间没有消息中间件**，用了三种朴素机制：

| 机制 | 用在哪 | 代码位置 |
|---|---|---|
| **① 抽象接口（依赖倒置）** | core 定义接口，宿主实现。core 不认识 GUI | `core/include/dect2/net/remote_api.h:40-61` `class RemoteApiHost`（12 个纯虚方法） |
| **② 每帧 Job 队列 + condvar** | 网络线程 → GUI 主线程 | `app/platform/remote_api_app.cpp:31-100` |
| **③ mutex 保护的快照** | Engine → 所有消费者（遥测/频谱） | `core/include/dect2/engine/engine.h:754` `publishRx()` |

**② 的细节（这一段直接对标 MBDSDR 的 `ControlHub::execute()` 跨线程编组）**：

```cpp
// app/platform/remote_api_app.cpp:31
struct Host : RemoteApiHost {
    // ---- main-thread hand-over: the server's threads queue a job, tick() (every frame) runs it
    struct Job {
        std::function<bool(App&, std::string&)> fn;
        std::mutex m; std::condition_variable cv;
        bool running = false, done = false, cancelled = false, ok = false;
        std::string err;
    };
    std::mutex qMu;
    std::deque<std::shared_ptr<Job>> q;
    std::atomic<double> lastTick{-1e9};

    bool run(const std::function<bool(App&, std::string&)>& fn, std::string& err) {
        if (steadyNow() - lastTick > 1.5) { err = "the application is not responding (is its window minimised or busy?)"; return false; }
        auto j = std::make_shared<Job>(); j->fn = fn;
        { std::lock_guard<std::mutex> lk(qMu); if (q.size() > 64) { err = "too many requests waiting"; return false; } q.push_back(j); }
        std::unique_lock<std::mutex> lk(j->m);
        if (!j->cv.wait_for(lk, std::chrono::seconds(3), [&]{ return j->done || shutdown.load(); })) { ... }
        ...
    }
    void tick(App& a, bool on) {          // 每帧由主循环调用
        lastTick = steadyNow();
        std::deque<std::shared_ptr<Job>> todo;
        { std::lock_guard<std::mutex> lk(qMu); todo.swap(q); }
        for (auto& j : todo) { j->running = true; ... j->fn(a, err) ... j->cv.notify_all(); }
        ...
    }
};
```

要点：**远端线程永远不直接碰 Engine**；它投递一个 `std::function<bool(App&, std::string&)>`，在 GUI 帧循环里执行，用 condvar 同步拿结果。**有界**（队列 64）、**有活性检测**（`lastTick > 1.5s` 直接拒绝并给出可读原因）、**有超时**（3 s）。

**③ 的细节**：`Engine` 在 `rxMu_` 下发布遥测快照，带单调递增 `seq`（`engine.h:752-754`）：

```cpp
// engine.h:752
// took and latestRx() would hold them back. The published report therefore carries a number of the engine that only grows. Under rxMu_.
void publishRx(RxTelemetry&& t) { t.seq = ++rxPub_; rxTel_ = std::move(t); }
```

消费者靠 `seq` 变化判断“有没有新数据”，避免重复推送（`remote_api.cpp:385-392` 的 WebSocket 遥测节流就是靠 `sq != telSeq`）。

**总线/事件**：层间**没有**全局事件总线。唯一接近“事件”的是自动化事件（`plugin.<type>`，供规则引擎消费），那是**功能特性**，不是层间通信机制。

### 1.4 为什么这么分层：解决了什么

ARCHITECTURE.md 自己给了答案（`:90`）：

> The Android bridge (`onair-android/native`, outside this repo) includes headers as `dect2/<name>.h`. … The bridge only needs `core/include` and the `dect2core` target, so its CMake does not change.

拆开说：

| 分层带来的好处 | 具体证据 |
|---|---|
| **同一份 DSP 喂 4 种宿主** | GUI (`app/`)、headless server (`server/`)、CLI (`tools/`)、Android bridge（仓外）—— 全部只链接 `dect2core` + `core/include` |
| **无硬件也能测** | `ARCHITECTURE.md:86`："Most use synthetic signals produced by the generators in the library (`t2gen.cpp`, `dvbt_gen.cpp`, `atsc_gen.cpp`), so they need no radio." 实测 95 个 `*_gen.cpp` |
| **测试目录镜像源码目录** | `tests/<folder>/` 与 `core/src` 同名目录；`tests/<mode>/test_<mode>_*.cpp` 被 glob 自动收集（`ARCHITECTURE.md:43`） |
| **插件不能污染主体** | 插件只用 CC0 的 ABI 头 → 法律上不是衍生作品（`docs/plugins.md:181-189`），且崩溃可被 safe-mode 隔离 |
| **Rust 重写协议层不影响调用方** | `ARCHITECTURE.md:49`："The public C++ headers did not change, so callers do not know the difference"（18 个 Rust crate 替换了 C++ 实现） |

### 1.5 关键文件清单（三层）

| 层 | 目录 / 文件 | 职责 |
|---|---|---|
| 核心 | `core/include/dect2/**/*.h` | 公开头，目录名与 `core/src` 一致 |
| 核心 | `core/src/engine/engine.cpp` (24 KB)、`engine_{start,tune,loops,telemetry,watch,secondary}.cpp` | Engine：持有 source、跑分析线程、发布快照 |
| 核心 | `core/src/engine/modes.cpp` (28 KB) | **模式注册表**（`table()` 88 条 + `kSubModes[]` 32 条） |
| 核心 | `core/src/net/remote_api.cpp` (31 KB) | HTTP/1.1 + WebSocket 服务器，**全部 `/api/v1/*` 在这里** |
| 核心 | `core/src/plugin/*.cpp`（8 个文件） | 插件宿主：manifest 解析 / dlopen / 进程内 / 辅助进程 / zip 安装 |
| 宿主 | `app/platform/remote_api_app.cpp` | GUI 侧 `RemoteApiHost` 实现 + 主线程 Job 队列 |
| 宿主 | `server/server_host.{h,cpp}` | headless 侧 `RemoteApiHost` 实现 + 快照线程 + 音频 hub |
| 宿主 | `server/web_ui.h` (10 KB) | 浏览器页面（内置，无外部资源） |
| 扩展 | `sdk/include/onair_plugin.h` (182 行) | C ABI v1，CC0 |
| 扩展 | `sdk/cmake/OnAirPlugin.cmake` | `onair_add_plugin()` CMake helper |
| 扩展 | `docs/plugins.md` (206 行) | 完整插件文档 |

---

## 2. `/api/v1/*` 工具面

### 2.1 全部端点（实测，全仓 grep `/api/v1` 得到 23 个唯一路径串）

**基础面（13 个，README.md:452-462 有表格）**：

| 端点 | 方法 | 作用 | 代码位置 |
|---|---|---|---|
| `/api/v1` | GET | **自描述索引**：返回 name/version/endpoints 数组 | `remote_api.cpp:236-241` |
| `/api/v1/status` | GET | running, device, mode, freq, rate, gain, AGC | `:242` |
| `/api/v1/devices` | GET | 收音机列表（index/name/kind/board/serial/limits） | `:243` |
| `/api/v1/modes` | GET | 模式列表（`family` + 可选 `sub`） | `:244` |
| `/api/v1/telemetry` | GET | 当前模式遥测（state/locked/snrDb/cfoHz/FEC/quality） | `:245` |
| `/api/v1/spectrum?bins=N` | GET | 最新频谱线（16..4096 bins，默认 1024） | `:246-252` |
| `/api/v1/rotator` | GET | 天线旋转器状态 | `:253-256` |
| `/api/v1/ws` | GET+Upgrade | **WebSocket**：推遥测（≤10/s）、频谱（≤5/s）、音频 | `:257`, `:321-422` |
| `/api/v1/start` | POST | 启动接收机（`{"device":2}`） | `:281-284` |
| `/api/v1/stop` | POST | 停止 | `:285` |
| `/api/v1/mode` | POST | `{"family":25,"sub":"ft8"}` | `:287-291` |
| `/api/v1/frequency` | POST | `{"hz":...}` 或 `{"mhz":...}` | `:292-296` |
| `/api/v1/samplerate` | POST | `{"hz":...}` / `{"msps":...}`，0=自动 | `:297-301` |
| `/api/v1/gain` | POST | `{"db":33}`，会关掉 AGC | `:314-317` |
| `/api/v1/rotator/{goto,stop,park}` | POST | 旋转器控制（3 个） | `:302-313` |

**server 专属扩展（`/api/v1/x/*`，3 个）** —— `docs/headless-server.md:78`：

| 端点 | 方法 | 作用 |
|---|---|---|
| `/api/v1/x/server` | GET | server 自身状态 |
| `/api/v1/x/demod` | POST | Listen 的解调方式 |
| `/api/v1/x/autostart` | POST | 开机自启 |

**数量级结论：REST 端点 13 个 + 旋转器 3 个 + server 扩展 3 个 + 1 个 WebSocket ≈ 20 个“入口”。这是个遥控面，不是工具注册表。**

### 2.2 组织方式：没有蓝图 / router / 资源类

**这是最反直觉的一点**：一个 31 KB、支持 TLS + WebSocket + 20 个端点的服务器，**全部路由就是一个 `if` 链**。

```cpp
// core/src/net/remote_api.cpp:215
void route(sock_t s, const Request& rq) {
    const std::string& p = rq.path;
    const bool isGet = rq.method == "GET", isPost = rq.method == "POST";
    if (!isGet && !isPost) { sendErr(s, 405, "method not allowed", "Allow: GET, POST\r\n"); return; }
    ...
    if (p == "/" || p == "/index.html") { ... }
    if (p == "/api/v1" || p == "/api/v1/") { getOnly([]{ return std::string("{\"name\":\"OnAir remote API\",\"version\":1,\"endpoints\":[...]}"); }); return; }
    if (p == "/api/v1/status")    { getOnly([&]{ return host.statusJson(); }); return; }
    if (p == "/api/v1/devices")   { getOnly([&]{ return host.devicesJson(); }); return; }
    ...
    if (p.compare(0, 10, "/api/v1/x/") == 0) { /* 交给宿主的 handleExtra() */ }
    const bool known = p == "/api/v1/start" || p == "/api/v1/stop" || ... ;
    if (!known) { sendErr(s, 404, "no such endpoint"); return; }
    ...
}
```

**没有 Flask Blueprint、没有 Express Router、没有资源类。** 只有：

- 一个 `route(sock_t, const Request&)` 自由函数
- 一组 lambda 辅助：`okJson` / `fail` / `getOnly`
- 一个 `parseFlatJson()` 扁平 JSON 解析（**只支持一层对象**，嵌套/数组直接返回 false）

**评价**：端点少（20 个）时，if 链完全够用且可读性极好。**但 MBDSDR 有 109 条 ControlHub 命令 + 90 条 AI 工具，绝不能抄这个**——MBDSDR 现有的静态表 + 内省（`commandTable()` / `executorToolNames()`）已经是对的做法。

### 2.3 版本化：字面量前缀 + 自描述索引

版本化做得非常轻，但**有一招很值得抄**：

```cpp
// remote_api.cpp:236-241
if (p == "/api/v1" || p == "/api/v1/") {
    getOnly([] { return std::string("{\"name\":\"OnAir remote API\",\"version\":1,\"endpoints\":["
        "\"GET /api/v1/status\",\"GET /api/v1/devices\",\"GET /api/v1/modes\",\"GET /api/v1/telemetry\","
        "\"GET /api/v1/spectrum?bins=N\",\"GET /api/v1/rotator\",\"GET /api/v1/ws\","
        "\"POST /api/v1/start\",\"POST /api/v1/stop\",\"POST /api/v1/mode\",\"POST /api/v1/frequency\","
        "\"POST /api/v1/samplerate\",\"POST /api/v1/gain\",\"POST /api/v1/rotator/goto\","
        "\"POST /api/v1/rotator/stop\",\"POST /api/v1/rotator/park\"]}"); });
    return;
}
```

特点：

- 版本是**路径字面量** `/api/v1`，没有常量、没有宏、没有路由版本协商机制。
- 但 `GET /api/v1` 返回**机器可读的端点清单**（`version:1` + `endpoints[]`），客户端可以自发现。
- **端点清单是手写的**（上面这段字符串常量），和 `route()` 的 if 链是**两份真相**，会漂移。这是它设计上的一个真缺陷（MBDSDR 的 `generateToolDocumentation()` 是自动生成的，反而更好）。
- 宿主扩展点靠 `handleExtra()` 返回 **0 = 未处理 → 统一 404**，让 server 能加自己的端点而不改 core：

```cpp
// remote_api.h:61
// GET / POST under /api/v1/x/ (the program's own extras). Returns the HTTP status
// (0: not handled, the server answers 404) and the JSON body in `json`.
virtual int handleExtra(const std::string& method, const std::string& path,
                        const std::map<std::string, JsonVal>& body, std::string& json) { return 0; }
```

### 2.4 认证

| 场景 | 规则 | 代码 |
|---|---|---|
| 回环（127.0.0.1） | **无需 token**，但 `Host` 头必须是回环地址（防 DNS rebinding） | `remote_api.h:5-7`、`remote_api.cpp:~190` |
| 非回环 | 必须 `Authorization: Bearer <token>` 或 `?token=`；token ≥ 16 字符 | `remote_api.cpp:202-206` |
| 校验失败 | **常数时间比较 `ctEqual`** + **故意 sleep 300 ms**（防爆破）+ 401 + `WWW-Authenticate: Bearer` | `remote_api.cpp:206-208` |
| `--lan` 无 token | **拒绝监听**（不是“监听但拒绝请求”） | `remote_api.h:47` |
| token 生成 | `remoteApiNewToken()` = 24 个十六进制字符 | `remote_api.h:69` |
| TLS | 可选 PEM 证书（`tlsCert`/`tlsKey`），需 `DECT2_WITH_TLS=ON` | `remote_api.cpp:451-455` |

```cpp
// remote_api.cpp:202-210
const std::string a = rq.hdr("authorization");
if (a.size() > 7 && lower(a.substr(0, 7)) == "bearer ") tok = trim(a.substr(7));
else tok = queryParam(rq.query, "token");
if (c.token.size() >= 16 && ctEqual(tok, c.token)) return true;
std::this_thread::sleep_for(std::chrono::milliseconds(300));   // guessing is slow
sendErr(s, 401, "token required", "WWW-Authenticate: Bearer\r\n");
return false;
```

### 2.5 硬化 / 限额（写在头文件注释里，很有参考价值）

`core/include/dect2/net/remote_api.h:5-7`：

> Hardening: header block <= 8 KB, body <= 4 KB, 5 s to send a request, 16 connections (8 WebSockets), POST needs `Content-Type: application/json` (so a web page cannot send one without a CORS preflight, **which is never granted: the server sends no CORS headers**), a request that carries an `Origin` header must come from the server's own origin, and a loopback request must name a loopback `Host` (DNS rebinding).

**“POST 必须 `application/json` + 永不发 CORS 头”这一招很漂亮**：浏览器表单无法直接 POST（会触发 preflight，而 preflight 永不通过），于是不需要 CSRF token 就天然防住了网页驱动。

HTML 页面还带严格 CSP（`remote_api.cpp:233`）：

```
Content-Security-Policy: default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline';
                         connect-src 'self' ws: wss:; img-src 'self' data:;
                         frame-ancestors 'none'; base-uri 'none'
Referrer-Policy: no-referrer
```

### 2.6 响应约定

| 情况 | 响应 |
|---|---|
| 成功 GET | `200` + 宿主返回的完整 JSON（宿主自己拼字符串，`*Json()` 返回完整值） |
| 成功 POST | `200` + `{"ok":true}`（`remote_api.cpp:221`） |
| 业务失败 | `400` + `{"error":"<宿主给的 err>"}`（`:222`） |
| 参数错 | `400` + 具体原因，如 `"give hz or mhz"`、`"family must be a whole number (see /api/v1/modes)"` |
| 方法错 | `405` + `Allow: GET` / `Allow: POST` |
| Content-Type 错 | `415` |
| 未知端点 | `404` + `{"error":"no such endpoint"}` |
| 未鉴权 | `401` |
| WS 未升级 | `426 Upgrade required` |
| WS 过多 | `503 {"error":"too many WebSocket clients"}` |
| JSON 不合法 | `400 body must be a flat JSON object` |

数字处理有专门工具：`jsonNum(double)` 把 NaN/Inf 输出成 `null`（`remote_api.h:78`）；`jsonEscape()` 转义字符串。**非有限数永不进 JSON**。

### 2.7 完整请求链路

```
TCP accept
  └─ TLS accept（可选）
     └─ readRequest()           有界：头 ≤ 8 KB，体 ≤ 4 KB，5 s
        └─ authorise()          回环免 token / 非回环 Bearer 或 ?token=，ctEqual，失败 sleep 300 ms
           └─ 分流
              ├─ GET /api/v1/ws 且带 Upgrade → serveWs()  → 101 Switching Protocols → 帧循环
              └─ route()
                 ├─ HTML 首页（带 CSP）
                 ├─ GET 只读端点 → lock(hostMu) → host.xxxJson() ──┐
                 ├─ /api/v1/x/* → host.handleExtra()（0 → 404）    │
                 └─ POST 写端点 → 校验 Content-Type → parseFlatJson│
                                  → 数值范围校验                    │
                                  → lock(hostMu) → host.setXxx()  ──┤
                                                                    ↓
        【GUI 宿主】app/platform/remote_api_app.cpp
           网络线程 → Host::run() 投递 Job 到 std::deque（上限 64）
                    → condvar 等待 ≤ 3 s
           主线程   → Host::tick()（每帧）swap 出队列，逐个执行 j->fn(App&, err)
                    → notify_all() 唤醒网络线程
           活性闸   → steadyNow() - lastTick > 1.5 s → 直接拒绝并给出可读原因
                                                                    ↓
        【headless 宿主】server/server_host.cpp
           网络线程 → lock(mu_) → 直接改 Engine（server 无 GUI 帧循环）
           快照线程 → snapLoop() 每 1 s 刷新 telJson_ / spec_
                                                                    ↓
   发送 → send() / sendErr() → sockShutdown()
```

**关键设计**：`hostMu` 串行化所有宿主调用（`remote_api.h:37-38`：“The server calls it from its connection threads, one call at a time (the server serialises them), so a host that owns a GUI must hand work to its own thread”）。**core 只保证串行，线程归属由宿主自己解决**——GUI 宿主用 Job 队列，headless 宿主直接跑。

---

## 3. 插件机制（真名：**OnAir Plugin ABI v1**）

### 3.1 “158” 从哪来：实测数字（口径全列出来，请自行核对）

**全仓 grep `\b158\b` 的结果：仓库里唯一的 158 是灰色 RGB 三元组 `IM_COL32(150, 154, 158, 255)`**，出现在约 20 个 UI 文件里（如 `app/adsb/adsb_map.h:61` `inline ImU32 acStale() { return IM_COL32(150, 154, 158, 255); }`）。**它跟插件/模式数量毫无关系。**

实测口径表：

| 口径 | 数字 | 怎么数出来的 |
|---|---|---|
| 内置模式表 `table()` 条目 | **88**（`ndbTuning()` 出现 2 次，去重后 **87**） | `core/src/engine/modes.cpp:311-313` 正则数 `xxxTuning()` 调用 |
| SubMode 表 `kSubModes[]` | **32** | `modes.cpp:345-378` 数 `{"` 条目 |
| README 宣称的模式数 | **120** | `README.md:5`、`README.md:197` “All 120 modes, by family” |
| `core/src/` 目录数 | **124** | `find core/src -maxdepth 1 -type d` |
| `app/` 目录数 | **112** | 同上 |
| `tests/` 目录数 | **29** | 同上 |
| `*_rx.cpp`（接收机实现） | **92** | `find core/src -name "*_rx.cpp"` |
| `*_rx.h`（公开头） | **89** | `find core/include -name "*_rx.h"` |
| `*_gen.cpp`（测试信号发生器） | **95** | `find core/src -name "*_gen.cpp"` |
| **插件 SDK 文件总数** | **20** | `find sdk -type f` |
| 插件示例（C++） | **3**（tone-detector / burst-counter / log-to-file） | `sdk/examples/` |
| 插件示例（Rust） | **1**（level-meter） | `sdk/rust/examples/` |
| 插件模板 | **1** | `sdk/template/` |
| 插件宿主源码 | **8 个文件** | `core/src/plugin/` |
| 模式 family 号上限 | 内置最大 76（CODAR），插件 1000+ | `mode_tuning.h:14`、`mode_tuning.h:35` |

**结论：“158” 在本仓库中不代表任何插件/模式数量。最接近的“模式相关数量”是 88（内置模式表）/ 120（README 宣称）。** 若任务书的 158 指 NASA 版 OnAir 的某个例子数，那与本项目无关（NASA 版未找到）。

### 3.2 插件放哪个目录

```
sdk/                              ← 对外 SDK（插件作者只碰这里）
├── include/onair_plugin.h        ← C ABI v1，182 行，CC0 公有领域
├── cmake/OnAirPlugin.cmake       ← onair_add_plugin() CMake helper
├── template/                     ← 最小 IQ 插件（3 个文件，可直接复制）
│   ├── CMakeLists.txt
│   ├── manifest.json
│   └── plugin.cpp                ← 43 行，能跑
├── examples/{tone-detector,burst-counter,log-to-file}/
└── rust/onair-plugin + rust/examples/level-meter/

core/src/plugin/                  ← 宿主实现（OnAir 内部）
├── manager.cpp   (27 KB) 扫描/启用/权限/安装/卸载/state.json
├── manifest.cpp  (12 KB) manifest 有界解析 + 校验 + 版本比较
├── loader.{h,cpp}               dlopen + vtable 校验 + C 回调 trampoline
├── inproc.cpp    (13 KB)        进程内运行器（worker 线程 + watchdog）
├── proc.cpp      (20 KB)        辅助进程运行器（管道协议）
├── results.cpp   (12 KB)        结果有界存储（表/状态/日志/图/像/点）
└── zip.cpp       (6 KB)         .onairplugin 包的有界 zip 读取

app/plugin/plugin_app.cpp         ← GUI 侧：把插件模式注册进模式表
tools/onair_plugin_host.cpp       ← 辅助进程可执行体（本压缩包 tools/ 缺失）
```

### 3.3 最小接口：一个导出函数 + 一张函数表

**插件只需要导出一个 C 函数**（`sdk/include/onair_plugin.h:177`）：

```c
ONAIR_EXPORT const OnAirPluginVTable* onair_plugin_entry(uint32_t host_abi_version);
```

函数表（`onair_plugin.h:156-173`）：

```c
typedef struct OnAirPluginVTable {
  uint32_t struct_size;      // 版本兼容：宿主只读 struct_size 范围内的字段
  uint32_t abi_version;      // 插件编译时针对的 ABI 版本
  uint32_t caps;             // ONAIR_CAP_* 能力位：声明实现了哪些
  const char* id;            // 必须等于 manifest.json 的 "id"

  void* (*create)(const OnAirHostApi* host, const char* mode_id);   // 返回 NULL = 拒绝
  void  (*destroy)(void* instance);
  void  (*configure)(void* instance, double centre_hz, double sample_rate);
  void  (*process_iq)(void* instance, const float* iq, uint32_t n_complex, const OnAirIqMeta* meta);
  void  (*on_event)(void* instance, const OnAirEvent* ev);
  void  (*tick)(void* instance, double now_unix);
  void  (*setting_changed)(void* instance, const char* key);
} OnAirPluginVTable;
```

**最小可运行插件**（`sdk/template/plugin.cpp`，全文 43 行，核心 15 行）：

```cpp
#include "onair_plugin.h"
namespace {
struct State { const OnAirHostApi* host; unsigned long long samples = 0; };
void* create(const OnAirHostApi* host, const char* mode_id) { (void)mode_id; return new State{host}; }
void destroy(void* p) { delete (State*)p; }
void process(void* p, const float* iq, uint32_t n, const OnAirIqMeta* meta) {
    auto* s = (State*)p; s->samples += n;
    char text[64]; snprintf(text, sizeof text, "%llu samples", s->samples);
    s->host->status(s->host->ctx, "samples", text, ONAIR_LAMP_OK);
}
const OnAirPluginVTable kVt = {sizeof(OnAirPluginVTable), ONAIR_PLUGIN_ABI_VERSION,
                               ONAIR_CAP_IQ_SINK, "my-plugin",
                               create, destroy, nullptr, process, nullptr, nullptr, nullptr};
}
extern "C" ONAIR_EXPORT const OnAirPluginVTable* onair_plugin_entry(uint32_t host_abi) {
    return host_abi >= ONAIR_PLUGIN_ABI_VERSION ? &kVt : nullptr;
}
```

**接口约定（值得照抄的 5 条）**：

1. **可选方法 = NULL**，用 `caps` 位声明（`ONAIR_CAP_IQ_SINK` / `EVENTS` / `TICK` / `SETTINGS`），不是靠“实现空函数”。
2. **每个 struct 以 `struct_size` 开头**，新旧宿主/插件双向兼容（宿主只读落在 `struct_size` 内的字段）。
3. **纯 C 边界**：定宽整数、float、NUL 结尾 UTF-8、指针。**无 C++ 类型、无异常、无所有权转移**（传入内存只在调用期间有效，接收方自己拷贝）。
4. **返回值约定**：`int` 返回 0 = 成功，负数 = 被拒（坏参数 / 超限 / 缺权限），**插件应当继续运行而不是崩**。
5. **不许异常/恐慌跨越边界**（`docs/plugins.md:124`："Do not let C++ exceptions or Rust panics leave a callback."）。

**宿主给插件的 API**（`OnAirHostApi`，13 个函数，`onair_plugin.h:117-153`）：`log` / `get_setting_{text,number,flag}` / `request_iq` / `table_define|row|clear` / `status` / `result_log` / `plot` / `image` / `map_point` / `emit_event` / `audio_out` / `data_dir`。**插件写零 UI 代码**，宿主用一个通用 Plugin 面板渲染插件上报的结果（表/状态灯/日志/图/像/地图点）。

### 3.4 发现与注册：**动态**（目录扫描 + dlopen），不是静态注册，也不是 Python 式 import

```cpp
// core/src/plugin/manager.cpp:138
void Manager::scan() {
    ... // 读 plugins/state.json（启用状态、已答权限、设置值、模式号）
    auto scanDir = [&](const std::string& dir, bool bundled) {
        for (fs::directory_iterator it(dir, ec), end; !ec && it != end; it.increment(ec))
            if (it->is_directory(ec)) dirs.push_back(it->path());
        std::sort(dirs.begin(), dirs.end());
        for (const auto& d : dirs) {
            Entry e; e.dir = d.string(); e.bundled = bundled;
            const std::string folder = d.filename().string();
            const std::string text = readFile(d / "manifest.json", kMaxManifestBytes + 1);  // ≤ 64 KiB
            if (text.empty()) continue;                                  // 不是插件目录
            if (!parseManifest(text, e.m, err, cfg_.hostVersion)) { e.m.id = folder; e.m.name = folder; e.error = err; }
            else if (e.m.id != folder) { e.error = "the folder name must be the plugin id (" + e.m.id + ")"; }
            else { /* 检查 library 文件是否存在 */ }
            bool dup = false;
            for (const auto& o : p_->entries) if (o.m.id == e.m.id) dup = true;
            if (dup) continue;   // 用户安装的插件覆盖同名内置插件
            p_->entries.push_back(std::move(e));
        }
    };
    scanDir(cfg_.pluginsDir, false);   // 用户插件目录
    scanDir(cfg_.bundledDir, true);    // 内置示例目录
}
```

流程：**目录扫描 → 读 `manifest.json` → 有界解析校验 → 记录（含错误原因）→ 用户启用时 `dlopen` 并取 vtable**。

```cpp
// core/src/plugin/loader.h:11
struct Lib { void* handle = nullptr; const OnAirPluginVTable* vt = nullptr; };
// Loads (once; libraries are never unloaded, an unload can run code of a plugin that has gone wrong)
// and checks the vtable against the manifest.
bool loadLibrary(const std::string& path, const Manifest& m, Lib& out, std::string& err);
```

**库永不卸载**（卸载会执行已出错插件的代码）—— 一个很实在的安全决策。

**注册进模式表**（关键一招，让插件和内置模式走同一条路）：

```cpp
// core/include/dect2/engine/mode_tuning.h:36
void registerPluginTuning(int stdMode, const std::string& id, const std::string& name,
                          double minMhz, double maxMhz, double defMhz, double sampleRate, double bandwidthMhz);

// core/src/engine/modes.cpp:323 —— 插件模式进的是同一张 registry
void registerPluginTuning(int stdMode, ...) {
    if (!isPluginStd(stdMode)) return;
    std::lock_guard<std::mutex> lk(gPluginTuningMu);
    auto& e = pluginTunings()[stdMode];   // node-based: 指针保持有效
    ...
}

// core/src/engine/modes.cpp:334 —— modeTuning() 对内置和插件一视同仁
const ModeTuning* modeTuning(int stdMode) {
    if (isPluginStd(stdMode)) { ...pluginTunings()... }
    for (const auto& m : table()) if (m.stdMode == stdMode) return &m;
    return nullptr;
}
```

调用方（`app/plugin/plugin_app.cpp:89,96`）：

```cpp
dect2::registerPluginTuning(999 + 2, "plugins", "Plugins", 0.001, 6000, 100, 2e6, 1);
...
dect2::registerPluginTuning(r.number + 2, "plugin-" + r.pluginId + "-" + r.modeId,
                            r.name, 0.001, 6000, r.defaultHz / 1e6, rate, r.bandwidthHz / 1e6);
```

**模式号分配**（`docs/plugins.md:164-165`）：family 1000 起（`kPluginStdBase = 1001`），**按首次出现顺序分配，永不复用，插件卸载重装后仍保留**，持久化在 `plugins/state.json`。

### 3.5 元数据：`manifest.json`

```json
{
  "id": "burst-counter",                 // 必填：a-z 0-9 - _ .，≤64，必须等于文件夹名
  "name": "Burst counter",               // 必填
  "version": "1.0.0",                    // 必填：点分数字 + 可选后缀
  "author": "OnAir contributors",        // 必填
  "license": "CC0-1.0",                  // 必填：SPDX id 或短文本
  "homepage": "https://example.org",     // 可选，仅 http(s)
  "description": "What it does.",        // 可选，≤2000 字符
  "kind": "mode",                        // 必填：mode | action | source | panel-lite
  "abi_version": 1,                      // 必填：必须等于宿主 ABI
  "minimum_onair": "0.2.7",              // 可选
  "library": {"macos": "x.dylib", "linux": "x.so", "windows": "x.dll"},  // 仅文件名，不含路径
  "permissions": ["filesystem"],         // network | filesystem | audio-out | record | spawn-process
  "modes": [                             // mode/source 必填，≤16
    {"id": "bursts", "name": "Burst counter", "group": "Plugins",
     "default_frequency_hz": 433920000, "bandwidth_hz": 500000, "sample_rates": [1000000, 2000000]}
  ],
  "settings": [                          // 可选，≤64；宿主负责画 UI 和持久化
    {"key": "threshold_db", "label": "Threshold (dB)", "type": "slider", "min": 3, "max": 40, "default": 10},
    {"key": "on",   "label": "On",   "type": "checkbox", "default": true},
    {"key": "kind", "label": "Kind", "type": "combo", "options": ["a","b"], "default": 0},
    {"key": "prefix","label":"Prefix","type":"text","default": ""}
  ]
}
```

**校验是有界的**（`docs/plugins.md:90-92`）：manifest ≤ 64 KiB、嵌套 ≤ 32 层；id 或 library 名**任何能指向插件目录之外的写法都被拒**（防路径穿越）；未知 permission / kind、ABI 版本不符 → **在管理器里显示原因并拒绝运行**，而不是崩溃。

**四种 kind**（`docs/plugins.md:21-26`）：`mode`（解码器，进模式列表）、`source`（偏分析的流消费者）、`action`（响应其它模式解码出的事件，类似自动化规则但用代码写）、`panel-lite`（只有 manifest + 设置，**无代码**）。

### 3.6 稳定性 / 安全模型（值得抄的三招）

| 机制 | 做法 | 出处 |
|---|---|---|
| **watchdog** | 单次回调 > 2 s（启动 3.5 s）→ 关闭插件、停止喂数据、停止等待。卡死的线程**不强行 kill**（不安全），进程内插件被“遗弃” | `docs/plugins.md:130-133` |
| **safe mode** | 插件代码运行期间在插件目录放 marker 文件；下次启动发现 marker → 只禁用出问题的那个插件启动安全模式；UI 可退出 | `docs/plugins.md:134-136` |
| **权限提示** | 首次启用时逐条列出 manifest 声明的权限，用户逐条允许/拒绝；未授权时 `data_dir` / `audio_out` 直接返回错误 | `docs/plugins.md:138-141` |
| **辅助进程** | 可选；`onair-plugin-host --plugin-host <lib>`，管道（fd 0 读 / fd 3 写），每条消息确认；超时 SIGKILL；崩溃只死 helper | `docs/plugins.md:150-157` |
| **安装包** | `.onairplugin` = zip：≤64 条目、单文件 ≤128 MiB、总计 256 MiB、CRC-32、无加密无 zip64；拒绝 `..`、绝对路径、反斜杠、盘符 | `docs/plugins.md:143-146` |
| **IQ 背压** | 每插件 8 MiB 队列，慢了就丢块，丢块数显示在插件界面上 | `docs/plugins.md:29-30` |

### 3.7 限额表（一处集中声明，很值得抄）

`docs/plugins.md:194-205`：manifest 64 KiB / 深度 32 / 16 modes / 64 settings / 64 combo 选项；IQ 队列 8 MiB；watchdog 2 s；表 8 个 / 列 24 / 行 2000 / 单元格 512 B；status 64 / log 500 行 / 行 1000 B；图 8 个 / 65536 floats；图 4 个 / 4 MiB / 边长 4096；地图点 2000；事件 32 字段 / 100 每秒；安装包 64 条目 / 128 MiB / 256 MiB。

**“所有限额集中在一张表里，代码里不带魔法数”** —— MBDSDR 的 `core/tokens.h` 已经是这个思路，可以对齐。

---

## 4. 与 MBDSDR 现状对照表

**MBDSDR 实测数字**（同样用脚本数出来，不是估的）：

| 项 | 数字 | 位置 |
|---|---|---|
| `registeredToolSpecs()` 声明式工具 | **90** | `cpp/src/ai/tool_schema.cpp:63`（`out.append(s)` × 90） |
| `ControlHub` 命令表 | **109**（63 write / 46 read） | `cpp/src/control/control_hub.cpp:166-…` |
| MCP JSON-RPC 方法 | 4（initialize / notifications / tools/list / tools/call） | `cpp/src/agentapi/mcp_server.h:12-18` |

| 维度 | OnAir | MBDSDR 现状 | 差距判断 |
|---|---|---|---|
| **核心/宿主分层** | `dect2core` 静态库 + app/server/tools/Android 4 类宿主 | `dsp::SpectrumEngine` + `ControlHub`（**已解耦 GUI**，`control_hub.h:5-8`）+ GUI/MCP/HTTP 3 类客户端 | **MBDSDR 已达标**，甚至更好（OnAir 自己只写 two layers） |
| **抽象接口倒置** | `RemoteApiHost` 纯虚基类，core 定义、宿主实现 | `ControlHub::execute(cmd, args)` 单一入口，GUI/AI/HTTP 共用 | **已达标** |
| **跨线程归属** | Job 队列 + 每帧 `tick()` + condvar + 活性闸（1.5 s）+ 超时 3 s + 队列上限 64 | `ControlHub::execute()` 跨线程用**阻塞队列调用**编组回引擎宿主线程（`control_hub.h:36-41`）；HTTP server 建在宿主线程上走零开销路径 | **已达标**，但 MBDSDR **没有活性闸和超时**——引擎卡住时 MCP/HTTP 会无限等 |
| **工具注册表** | 无（20 个 if 链端点 + 88 条模式表） | **90 条声明式 spec**（`write` 标记在 spec 上）+ 109 条命令表 + `commandTable()` / `executorToolNames()` 内省 | **MBDSDR 明显领先** |
| **读/写门禁** | HTTP 层区分 GET/POST；无“手动模式”概念 | `ToolSchemaSpec::write` + `arm_manual` + `setWriteEnabled()` + `gatedToolResult()`，三方一致 | **MBDSDR 领先** |
| **版本化 API** | `/api/v1` 路径字面量 + `GET /api/v1` 自描述端点清单（**手写**） | **无版本前缀**；`HttpControlServer` 直接暴露端点，无 `/api/v1`、无索引端点 | **MBDSDR 缺**（详见建议 1） |
| **错误信封** | 统一 `{"error":"..."}` + HTTP 状态码；POST 成功 `{"ok":true}` | **3 种形状**：ControlHub `{"ok":false,"error":…}` / `{"ok":false,"gated":true}`；MCP `content:[{type:"text"}]` | **MBDSDR 缺统一**（建议 3） |
| **认证** | 回环免 token + 非回环 Bearer/token（≥16，ctEqual，失败 sleep 300 ms）+ 无 CORS + Origin 校验 + DNS-rebinding 防护 | `HttpControlServer` **明确无认证、仅绑 127.0.0.1**，头文件里诚实声明（`control_http_server.h:16-24`） | **MBDSDR 选择更安全（仅回环）**，但缺 token 面 → 无法做“手机远控” |
| **限额集中声明** | `docs/plugins.md` 一张表 + 代码无魔法数 | `core/tokens.h` 集中常量（工具 schema 边界从 tokens 取） | **已达标** |
| **插件/模块注册表** | `manifest.json` + `registerPluginTuning()` + 稳定模式号（1000+，`state.json` 持久化不复用）+ dlopen 动态发现 | **无插件缝**。POCSAG/M17/VOR/ACARS/NAVTEX/FT8/LRPT 等是硬编码分支 | **MBDSDR 最大缺口**（建议 4） |
| **二进制频谱帧** | `fmt:"u8"` → `[0x01][u32 seq][f32 centre][f32 rate][f32 floor][f32 top][bins]`，约 JSON 的 1/10 带宽 | `export_spectrum_snapshot` 走 JSON | **MBDSDR 缺**（建议 6） |
| **自描述文档** | `GET /api/v1` 手写端点清单（会漂移） | `generateToolDocumentation()` **自动生成**（`tool_schema.cpp:2411`），但只喂给模型，不对外暴露 | **MBDSDR 更强，但没对外用**（建议 2） |
| **watchdog / safe mode** | 插件回调 2 s 看门狗 + marker 文件安全模式 + 可选辅助进程 | 无插件，故无此需求 | 引入插件后需要（建议 8） |
| **测试可无硬件** | 95 个 `*_gen.cpp` 合成信号发生器，`ctest` 全跑 | 有 Test Signal 源（`TelemetrySnapshot.sourceName == "Test Signal"`） | 已达标 |

---

## 5. 落地建议清单（按性价比排序）

> 每条都给到“改哪个文件、加什么结构”。前 4 条建议优先做。

### ★1 加 `/api/v1` 版本化 + 自描述索引端点（**性价比最高**）

- **改**：`cpp/src/control/control_http_server.{h,cpp}`
- **加**：所有端点前缀改为 `/api/v1/<cmd>`；新增 `GET /api/v1` 返回
  `{"name":"MBDSDR control API","version":1,"commands":[{name, write, arity}]}` —— **从 `ControlHub::commandTable()` 生成，不要手写**（避免 OnAir 那样两份真相漂移）。
- **加**：`GET /api/v1/tools` 直接复用现成的 `ai::generateToolDocumentation()`（`tool_schema.cpp:2411`）输出 90 条工具的 Markdown/JSON，让 HTTP 客户端和模型看到同一份能力文档。
- **抄的点**：`remote_api.cpp:236-241` 的自描述思路 + `handleExtra()` 返回 0 → 404 的扩展缝（让未来 server 端加自己的 `/api/v1/x/*` 不用改 core）。
- **成本**：低（路由前缀 + 一个生成函数）。**收益**：版本化、可发现、为后续插件端点留缝。

### ★2 把 `registeredToolSpecs()`（90）与 `ControlHub::table()`（109）收成一张真相

- **改**：`cpp/src/ai/tool_schema.cpp`、`cpp/src/control/control_hub.cpp`、测试文件
- **加**：一张共享的**能力表**（建议在 `cpp/src/control/` 新建 `capability_registry.{h,cpp}`），每条含 `{name, kind(tool|command), write, schema, handler}`；`registeredToolSpecs()` 和 `ControlHub::table()` 都从它派生（或至少加一条**编译期/测试期一致性断言**：命令名集合 == 工具名集合 ∪ 允许的差异白名单）。
- **抄的点**：OnAir 的 `modeTuning()` **一张 registry 同时服务 app / CLI / server / 插件**（`modes.cpp:334`）——内置和插件走同一条查找路径。
- **依据**：MBDSDR 自己已经在 `agent_tools.h:53-57` 写了 “introspected, not hardcoded … adding a tool without wiring its executor now fails the build's tests”，把这个模式扩到 ControlHub 即可。

### ★3 统一错误信封 `ApiResult`

- **改**：`cpp/src/control/control_hub.h`（新增结构）、`mcp_server.cpp`、`control_http_server.cpp`
- **加**：
  ```cpp
  struct ApiResult { bool ok; int code; QString error; bool gated; QJsonObject data; };
  QJsonObject ApiResult::toJson() const;          // 唯一序列化点
  static ApiResult ApiResult::ok(QJsonObject d);
  static ApiResult ApiResult::err(int code, const QString& msg);
  static ApiResult ApiResult::gated(const QString& cmd);
  ```
  MCP 侧 `content:[{type:"text",text:<toJson()>}]`；HTTP 侧 `{"ok":false,"error":...}` + 状态码。
- **抄的点**：OnAir 全场只有两种响应形状（成功 JSON / `{"error":"..."}` + 状态码），且 `jsonNum()` 保证 NaN/Inf 输出 `null`（`remote_api.h:78`）。
- **成本**：中（要改 3 个出口）。**收益**：MCP/HTTP/AI 三方错误语义一致，前端不用写 3 套解析。

### ★4 引入插件式**模块注册表**（最值得抄 OnAir 的一块）

- **新建**：`cpp/src/control/module_registry.{h,cpp}`（或 `cpp/src/plugin/`）
- **加**：
  ```cpp
  struct ModuleManifest {
      QString id;            // 稳定；≤64；a-z0-9-_.
      QString name, version, author, license;
      QString kind;          // "decoder" | "analyzer" | "exporter" | "panel-lite"
      int abiVersion = 1;
      QStringList permissions;   // "filesystem" | "network" | "audio-out" | "record"
      QJsonObject modes;         // 该模块贡献的模式/能力条目
  };
  class ModuleRegistry {
      int  registerModule(const ModuleManifest& m, Factory fn);   // 返回稳定 id（1000+，永不复用）
      bool unregister(int id);
      QList<ModuleManifest> modules() const;
      QJsonObject toJson() const;                                  // 直接喂 /api/v1/modules
  };
  ```
- **首批落地候选**（现在硬编码在 `agent_tools.cpp` / `control_hub.cpp` 分支里的解码器）：`pocsag` / `m17` / `vor` / `acars` / `navtex` / `aprs` / `ft8` / `lrpt` / `adsb` / `vna`。
- **抄的点**：
  - `registerPluginTuning()` 让**插件和内置走同一张表**（`mode_tuning.h:36` + `modes.cpp:334`）
  - 稳定号 **1000+，按首次出现分配，永不复用，持久化**（`docs/plugins.md:164`），MBDSDR 可存 `plugins/state.json` 或 QSettings
  - manifest **有界解析** + **目录名必须等于 id** + **路径穿越拒绝**（`manager.cpp:165-176`）
  - 限额集中在 `docs/plugins.md` 一张表 → 对齐到 `core/tokens.h`
- **成本**：中高。**收益**：新增解码器不再需要改 3 个巨型 cpp（`agent_tools.cpp` 156 KB、`tool_schema.cpp` 140 KB、`control_hub.cpp` 118 KB），是这三个文件停止膨胀的唯一结构性出路。

### ★5 给 `ControlHub::execute()` 加活性闸 + 超时（对标 OnAir 的 `lastTick` 检查）

- **改**：`cpp/src/control/control_hub.cpp`
- **加**：
  - 一个 `std::atomic<double> lastHomeTick_`，由 GUI 帧循环（或引擎心跳）每帧刷新；
  - `execute()` 入口先判 `steadyNow() - lastHomeTick_ > 1.5s` → 直接返回 `{"ok":false,"error":"应用无响应（窗口最小化或繁忙？）"}`，**不阻塞调用方**；
  - 跨线程阻塞编组加**上限 3 s**（现在是无限等待——见 `control_hub.h:36-41`，OnAir 是 `wait_for(3s)` + “已开始就等到底”的两段式，见 `remote_api_app.cpp:54-58`）。
- **抄的点**：`app/platform/remote_api_app.cpp:47-62` 全文。
- **成本**：低。**收益**：MCP Agent / HTTP 客户端不会因为 GUI 卡住而永久挂起。

### ★6 频谱二进制帧（`fmt:"u8"`）

- **改**：`cpp/src/control/control_hub.cpp`（`cmdGetSpectrumStatus` / `export_spectrum_snapshot`）、`control_http_server.cpp`
- **加**：一个可选 `fmt` 参数，返回/推送
  `[0x01][u32 seq][f32 centreHz][f32 rateHz][f32 floorDb][f32 topDb][bins × u8]`（小端，0=floorDb，255=topDb）。
- **抄的点**：`remote_api.h:53-55` 的注释精确定义了字节布局；`docs/headless-server.md:44` 说 “about a tenth of the size”。另外抄它的**带宽自适应**：一次发送耗时超过间隔一半就把间隔翻倍（最多 16 倍），恢复后再降回来（`remote_api.cpp:404-408`）。
- **成本**：低。**收益**：远控/手机端流量降一个数量级。

### ★7 暴露 `/api/v1/capabilities` 与遥测快照的 `seq`

- **改**：`control_http_server.cpp`、`control_hub.cpp`
- **加**：遥测 JSON 带**单调递增 `seq`**；客户端用 `seq` 判断有无新数据，避免轮询重复处理。
- **抄的点**：`engine.h:754` `publishRx()` 给每份遥测打 `seq`，WebSocket 端靠 `sq != telSeq` 去重（`remote_api.cpp:385-392`）。MBDSDR 的 `TelemetrySnapshot`（`control_hub.h:47-70`）已经有 `available` 但没有序号。
- **成本**：极低。

### ★8 若采纳建议 4 的插件化，配套 watchdog + safe mode

- **新增**：`cpp/src/plugin/watchdog.{h,cpp}`
- **加**：回调超时 2 s → 停用该模块并记原因；运行期在模块目录留 marker 文件，下次启动发现 marker 则**只禁用该模块**进入安全模式（对标 `docs/plugins.md:130-136`）。
- **成本**：中（仅在插件化后才有必要）。

### ★9 `manifest.json` 声明式设置 + 宿主自动渲染（可选，长期）

- 抄 `docs/plugins.md:81-86`：模块在 manifest 里声明 `settings[]`（slider / checkbox / combo / text，含 min/max/default/options），**宿主统一画 UI 并持久化**（OnAir 存在 `plugins/state.json`，MBDSDR 可用 QSettings，按模块 id 分组）。
- **收益**：新增模块零 UI 代码。MBDSDR 的 Qt Widgets 栈比 ImGui 更容易做（有现成的 `QFormLayout` + 工厂控件）。

### ★10 权限模型（可选，做远控时再做）

- 抄 `docs/plugins.md:138-141`：模块声明 `permissions[]`，首次启用逐条提示，未授权时对应 API 直接返回错误而不是静默失败。
- MBDSDR 现在 `HttpControlServer` 仅回环、无认证（`control_http_server.h:16-24`，诚实声明），如果将来要支持手机远控，再抄 OnAir 的 token 面（`ctEqual` + 失败 sleep 300 ms + 无 CORS + Origin 校验）。

---

## 6. 未查明的开放问题

诚实列出，没有编造：

1. **NASA OnAir 未找到。** 任务书假定研究对象是 NASA 的 Python 项目（含 `onair/` `onair_test/`），`/workspace/reference/` 下 5 个 OnAir 相关副本全是 `adswill/OnAir`（C++20）。**若确实需要研究 NASA 版，需要另外提供源码**。本报告的所有结论基于实际存在的 `adswill/OnAir`。
2. **“158” 无来源。** 全仓唯一 `158` 是灰色 `IM_COL32(150,154,158,255)`。未找到任何与 158 相关的插件/模式/文件计数。**若 158 是 NASA 版的行数或例子数，本报告无法验证。**
3. **`tools/` 目录在压缩包中缺失。** `ARCHITECTURE.md:42` 和 `CMakeLists.txt:242`（`add_executable(onair-plugin-host tools/onair_plugin_host.cpp)`）都引用 `tools/`，但顶层目录列表里**没有 `tools/`**。因此 `dect2cli` / `dect2scan` / `onair-plugin-host` 的源码**未读到**，CLI 这一层宿主的实现细节**未查明**。
4. **`third_party/` 目录缺失**（`ARCHITECTURE.md:45` 说有 Dear ImGui 和 miniaudio），顶层目录列表里也没有。GUI 层如何初始化**未深查**。
5. **`app/` 层的 112 个子目录未逐个读。** 只深读了 `app/platform/remote_api_app.cpp` 和 `app/plugin/plugin_app.cpp`。UI 与 core 的完整耦合面（除 `RemoteApiHost` 外还有哪些）**未查明**。
6. **WebSocket 二进制音频帧格式**只从 `remote_api.h:56` 注释读到（`[0x02]` + 16 bit LE mono），未读 server 侧 `audioOpen/audioRead` 实现细节。
7. **`Rotator`（`/api/v1/rotator/*`）** 只在 `remote_api.cpp:302-313` 看到 3 个转发调用，`core/include/dect2/source/rotator.h` **未读**，其权限/安全模型**未查明**。
8. **Rust 侧（18 个 crate）未读。** `ARCHITECTURE.md:47-72` 说协议层已用 Rust 重写，但本报告未验证 `rust/` 目录内容与 C ABI 的实际对应关系。
9. **MBDSDR 侧**：`cpp/src/ai/agent_tools.cpp`（156 KB）与 `cpp/src/control/control_hub.cpp`（118 KB）**未通读**，只统计了注册表条目数与入口函数位置。建议 2/3/4 的改动面需要在动手前精读这两个文件的相关区段。
10. **OnAir 是否真有“三层”官方文档**：`ARCHITECTURE.md` 明确写的是 **two layers**，本报告的三层划分是**我按 CMake 构建目标整理的口径**，不是官方表述。

---

## 附录：本报告用到的关键文件索引

**OnAir（`/workspace/reference/OnAir-main`）**

| 文件 | 关键行 | 内容 |
|---|---|---|
| `ARCHITECTURE.md` | 3 / 22-45 / 47-72 / 86 / 90 | 两层定义、目录表、Rust 层、测试、Android bridge |
| `README.md` | 5 / 452-462 | 120 modes、API 端点表 |
| `CMakeLists.txt` | 133 / 238 / 242 / 245 / 518 | dect2core / dect2_plugin / plugin-host / server / GUI |
| `core/include/dect2/net/remote_api.h` | 5-7 / 37-61 / 69-78 | 硬化限额、RemoteApiHost 接口、工具函数 |
| `core/src/net/remote_api.cpp` | 200-318 / 321-422 / 236-241 | 鉴权、路由、WebSocket、自描述索引 |
| `core/include/dect2/engine/engine.h` | 1 / 752-754 | “UI talks only to this”、publishRx seq |
| `core/src/engine/modes.cpp` | 311-313 / 323 / 334 / 345-378 | 模式表 88 / registerPluginTuning / modeTuning / SubMode 32 |
| `core/include/dect2/engine/mode_tuning.h` | 14 / 35-36 / 47-64 | stdMode 编号、kPluginStdBase、ModeTuning、SubMode |
| `app/platform/remote_api_app.cpp` | 31-100 | Job 队列 + 每帧 tick + 活性闸 + 超时 |
| `server/server_host.h` | 23-80 | headless 侧 RemoteApiHost 实现（快照线程、音频 hub） |
| `docs/headless-server.md` | 18-25 / 44 / 78 | 访问控制、二进制频谱、server 扩展端点 |
| `sdk/include/onair_plugin.h` | 28 / 117-173 / 177 | ABI v1、HostApi、PluginVTable、入口函数 |
| `sdk/template/plugin.cpp` | 全文 43 行 | 最小插件 |
| `core/src/plugin/manager.cpp` | 138-195 | 目录扫描发现 |
| `core/src/plugin/loader.h` | 11-13 / 20-35 | loadLibrary（永不卸载）、ApiSink |
| `app/plugin/plugin_app.cpp` | 89 / 96 | registerPluginTuning 调用点 |
| `docs/plugins.md` | 21-26 / 61-96 / 119-124 / 130-157 / 194-205 | kind、manifest、接口约定、安全模型、限额表 |

**MBDSDR（`/workspace/MBDSDR`）**

| 文件 | 关键行 | 内容 |
|---|---|---|
| `cpp/src/agentapi/mcp_server.h` | 12-35 | MCP 方法、写工具门禁、零模型依赖 |
| `cpp/src/ai/tool_schema.h` | 20-46 / 63 / 79 | ToolParamSpec / ToolSchemaSpec（含 `write`） / registeredToolSpecs |
| `cpp/src/ai/tool_schema.cpp` | 63-… / 2411 | 90 条 spec / generateToolDocumentation |
| `cpp/src/ai/agent_tools.h` | 28 / 53-57 / 62 | executeTool / executorToolNames（内省） / gatedToolResult |
| `cpp/src/control/control_hub.h` | 5-8 / 36-41 / 47-70 / 118-124 / 140-142 | 解耦声明、跨线程编组、TelemetrySnapshot、CommandRow、commandTable |
| `cpp/src/control/control_hub.cpp` | 166-… | 109 条命令表（63 write / 46 read） |
| `cpp/src/control/control_http_server.h` | 5-8 / 16-24 / 36-40 | 薄 HTTP 前端 / 仅回环无认证（诚实声明） / 线程归属 |
