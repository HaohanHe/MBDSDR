# SDR++ 插件架构子系统 — 源码精读笔记（Phase12 Wave1-F）

> 上游：`repos/sdrpp/`（GPLv3，见根目录 `license`）。本笔记**只学机制/契约/生命周期范式**，
> 不逐字复制代码；落地到 MBDSDR 时一律用自有干净室实现。
> 所有上游引用均为 `repos/sdrpp/<path>:<line>`；MBDSDR 引用均为 `cpp/<path>:<line>` 或
> `mbdsdr_ai/<path>:<line>`。
>
> 范围：动态模块加载（`core/src/module.{h,cpp}`）、模块实例工厂与生命周期、
> 信号路径回调表（`core/src/signal_path/source.{h,cpp}`）、菜单注入点
> （`core/src/gui/widgets/menu.{h,cpp}`、`core/src/gui/menus/{source,module_manager}.cpp`）、
> 跨模块通信（`core/src/module_com.{h,cpp}`）、以及两个真实模块样例
> （`misc_modules/demo_module/src/main.cpp`、`source_modules/rtl_sdr_source/src/main.cpp`）。
>
> 对照 MBDSDR：C++ 工具层 `cpp/src/ai/tool_schema.{h,cpp}`、`cpp/src/ai/agent_tools.cpp`、
> 唯一的 dlopen 点 `cpp/src/tx/soapy_tx_backend.cpp:82`；Python 插件层
> `mbdsdr_ai/tool_registry.py`、`docs/modularization_status.md`。

---

## 1. 上游真实做法（GPLv3，仅机制）

### 1.1 模块即 `.so/.dll`：五个 C 符号 + 一段元数据

SDR++ 的"插件"不是 C++ 抽象类，而是**一个独立编译的动态库**，导出五个固定名字的 C 符号，
core 用 `dlopen`/`LoadLibraryA` 拉进来再按名取符号：

- `core/src/module.h:17-29` — 跨平台宏：Windows `__declspec(dllexport)`，
  Linux/macOS `extern "C"`；模块后缀 `.dll/.so/.dylib`。
- `core/src/module.h:33-41` — 元数据结构 `ModuleInfo_t { name, description, author,
  versionMajor/Minor/Build, maxInstances }`。`maxInstances=1` 表示硬件类源只能开一个实例；
  `-1` 表示不限。
- `core/src/module.h:104` — 一行宏 `#define SDRPP_MOD_INFO MOD_EXPORT const
  ModuleManager::ModuleInfo_t _INFO_`，把插件作者写的花括号初始化变成导出符号 `_INFO_`。
- `core/src/module.cpp:34-44` — `dlopen(path, RTLD_LAZY | RTLD_LOCAL)` 之后依次
  `dlsym` 取 `_INFO_ / _INIT_ / _CREATE_INSTANCE_ / _DELETE_INSTANCE_ / _END_`；
  任何一个缺失就报错并放弃（:46-70）。
- `core/src/module.cpp:81-82` — 全部符号齐备后立刻调 `mod.init()`（即 `_INIT_`），
  再把模块按 `info->name` 存入 `modules` map。

真实插件长什么样（`misc_modules/demo_module/src/main.cpp:5-11`，GPLv3，仅结构示意）：
```cpp
SDRPP_MOD_INFO{
    "demo", "My fancy new module", "author1;author2",
    0, 1, 0,   // version
    -1         // max instances
};
```
四个导出函数（`demo_module/src/main.cpp:48-62`）分别是 `_INIT_`（模块级一次性初始化，如
打开配置文件）、`_CREATE_INSTANCE_(name)`（`new DemoModule(name)`）、
`_DELETE_INSTANCE_(inst)`（`delete`）、`_END_`（模块级一次性清理，如存配置）。

### 1.2 模块实例基类只有四个虚函数

- `core/src/module.h:43-50` — `class ModuleManager::Instance` 纯虚基类：
  ```cpp
  virtual ~Instance() {}
  virtual void postInit() = 0;
  virtual void enable() = 0;
  virtual void disable() = 0;
  virtual bool isEnabled() = 0;
  ```
  **没有 `open()/read()/start()`**——那些能力走 §1.4 的 `SourceHandler` 回调表。
  基类刻意保持极小：模块作者继承它，构造函数里去做自己的注册（菜单 / 源 / 界面），
  析构函数里反注册。

### 1.3 实例工厂与生命周期事件

- `core/src/module.cpp:86-106` — `createInstance(name, moduleType)`：
  1. 模块类型必须已加载（:87-90）；
  2. 实例名必须唯一（:91-94）；
  3. **maxInstances 配额校验**（:95-99）：`countModuleInstances(module) >= maxCount`
     且 `maxCount > 0` 时拒绝；
  4. 调模块的 `createInstance(name)` 函数指针（:102），包成 `Instance_t{module, instance}`
     存入 `instances` map（:103）；
  5. 发射 `onInstanceCreated.emit(name)`（:104）。
- `core/src/module.cpp:108-119` — `deleteInstance(name)`：先发 `onInstanceDelete`，
  再调 `deleteInstance` 函数指针，再从 map 擦除，最后发 `onInstanceDeleted`。
  "Delete before / delete after" 分开，让订阅者能在对象析构前后分别做事。
- `core/src/module.cpp:126-158` — `enable/disable/isEnabled/postInit` 全部是对
  `instances[name].instance->` 的薄转发。
- `core/src/module.cpp:181-186` — `doPostInitAll()`：启动末尾对所有实例调一次
  `postInit()`。这是"两阶段初始化"的第二阶段——构造函数里不能依赖别的模块已就位，
  `postInit()` 里才可以。

启动流程（`core/src/gui/main_window.cpp:104-143,227`）：
1. 扫 `modulesDirectory` 下所有 `.so`（:104-111）逐个 `loadModule`；
2. 再按 config 里 `conf["modules"]` 数组加载额外路径（:123-133）；
3. 按 config 里 `moduleInstances` 列表逐个 `createInstance`，按持久化状态
   `disableInstance`（:136-143）；
4. GUI 全部就绪后 `doPostInitAll()`（:227）。
关闭流程（`core/src/core.cpp:408-410`）：对每个加载过的模块调 `mod.end()`。

### 1.4 信号路径：设备 = 一张按名注册的回调表

设备抽象不靠继承，靠**回调函数表**（`core/src/signal_path/source.h:13-22`，GPLv3）：
```cpp
struct SourceHandler {
    dsp::stream<dsp::complex_t>* stream;   // 输出 IQ 流
    void (*menuHandler)(void* ctx);        // 在"Source"菜单里画自己的控件
    void (*selectHandler)(void* ctx);      // 用户选中这台设备
    void (*deselectHandler)(void* ctx);    // 切走
    void (*startHandler)(void* ctx);       // 开始采集
    void (*stopHandler)(void* ctx);        // 停止
    void (*tuneHandler)(double freq, void* ctx);  // 改频率
    void* ctx;
};
```

- `core/src/signal_path/source.cpp:10-17` — `registerSource(name, handler)`：按名入 map，
  发 `onSourceRegistered` 事件，菜单层据此刷新下拉列表。
- `core/src/signal_path/source.cpp:19-34` — `unregisterSource`：先发 `onSourceUnregister`，
  若是当前选中源则调 `deselectHandler` 并把 IQ 前端切到 `nullSource`，再擦除。
- `core/src/signal_path/source.cpp:42-60` — `selectSource(name)`：旧源 `deselectHandler`
  → 新源 `selectHandler` → 把 `iqFrontEnd.setInput(newHandler->stream)`。**"切换设备"
  = 换一张回调表 + 换一条流指针**，无虚函数、无继承。
- `core/src/signal_path/source.cpp:62-67` — `showSelectedMenu()`：菜单绘制时只调
  `selectedHandler->menuHandler(ctx)`，让当前设备自己画菜单内容。

真实设备插件如何填表（`rtl_sdr_source/src/main.cpp:66-73`，GPLv3）：
```cpp
handler.ctx = this;
handler.selectHandler = menuSelected;
handler.deselectHandler = menuDeselected;
handler.menuHandler = menuHandler;
handler.startHandler = start;
handler.stopHandler = stop;
handler.tuneHandler = tune;
handler.stream = &stream;
```
然后在构造末尾 `sigpath::sourceManager.registerSource("RTL-SDR", &handler)`（:95），
析构开头 `unregisterSource("RTL-SDR")`（:100）。**构造即注册、析构即反注册**，RAII。

### 1.5 菜单注入：模块构造时往主菜单塞一项

- `core/src/gui/widgets/menu.h:18-22` — `MenuItem_t { drawHandler, ctx, inst }`。
  注意 `inst` 字段：如果非空，菜单右侧会自动画一个 enable/disable 复选框
  （`menu.cpp:105-116`），勾选即调 `inst->enable()/disable()`。
- `core/src/gui/widgets/menu.cpp:9-21` — `registerEntry(name, drawHandler, ctx, inst)`：
  入 `items` map，若不在 `order` 列表里就追加到末尾。
- `core/src/gui/widgets/menu.cpp:23-25` — `removeEntry(name)`：只擦 `items`，
  `order` 里的残留项在 `draw()` 时跳过（:40-42）。
- `core/src/gui/main_window.cpp:73-79` — core 自己注册内置菜单项
  （Source / Sinks / Band Plan / Display / Theme / VFO Color / Module Manager）。
- `core/src/gui/menus/source.cpp:83-92,283-302` — "Source" 菜单先从
  `sourceManager.getSourceNames()` 刷出设备下拉，然后调
  `sigpath::sourceManager.showSelectedMenu()`（:302）——把控制权交给当前选中设备的
  `menuHandler`。事件绑定在 `source.cpp:197-202`：`onSourceRegistered /
  onSourceUnregistered` 都会触发 `refreshSources()`。

模块侧注入范式（`demo_module/src/main.cpp:13-22`，GPLv3）：
```cpp
DemoModule(std::string name) {
    this->name = name;
    gui::menu.registerEntry(name, menuHandler, this, NULL);  // 构造即注册
}
~DemoModule() { gui::menu.removeEntry(name); }               // 析构即反注册
```
模块管理器 GUI（`core/src/gui/menus/module_manager.cpp:101-104`）提供运行时
"输入名字 + 选类型 → `createInstance` → `postInit`"的入口，并在 :114-124 把实例列表
持久化到 config 的 `moduleInstances` 字段。

### 1.6 跨模块通信：ModuleComManager

插件之间不能直接 `#include` 对方头文件（独立编译、符号隔离），SDR++ 用一张
"按名字注册的过程调用表"解耦：

- `core/src/module_com.h:6-10` — `ModuleComInterface { moduleName, ctx,
  void (*handler)(int code, void* in, void* out, void* ctx) }`。
  一个模块对外暴露一组能力，用 `code` 区分子命令，`in/out` 是不透明指针由双方约定。
- `core/src/module_com.cpp:4-16` — `registerInterface(moduleName, name, handler, ctx)`：
  重名拒绝，入 `interfaces` map，全程 `recursive_mutex`。
- `core/src/module_com.cpp:43-52` — `callInterface(name, code, in, out)`：查表 →
  `iface.handler(code, in, out, iface.ctx)`。
- `core/src/module_com.h:14-18` — 配套 `unregisterInterface / interfaceExists /
  getModuleName`。

这是一个**最小化的 RPC 总线**：没有序列化、没有 IDL、没有版本协商——
靠"双方都是 C++、约定好 `in/out` 的 struct 布局"工作。模块 B 想调模块 A 时，
先 `interfaceExists("A_freq_api")`，再 `callInterface("A_freq_api", SET_FREQ, &f, nullptr)`。

---

## 2. MBDSDR 现状（对照）

### 2.1 C++ 工具层：硬编码 schema + 硬编码 if-else 分发

- `cpp/src/ai/tool_schema.h:33-37` — `ToolSchemaSpec { name, description, params }`
  是一个纯数据结构，没有注册表。
- `cpp/src/ai/tool_schema.cpp:56-199` — `registeredToolSpecs()` 返回**写死的 8 个工具**：
  `tune_frequency / set_mode / start_recording / stop_recording / scan_band /
  set_bandwidth / get_status / predict_passes`。函数体里逐个 `ToolSchemaSpec s; ...
  out.append(s);`，没有任何外部注册点。注释（:48-50）自称"the single source of truth"。
- `cpp/src/ai/agent_tools.cpp:90-210` — `executeTool(name, args, engine)` 是一长串
  `if (name == "tune_frequency") {...} if (name == "set_mode") {...} ...`
  （:96 / :106 / :116 / :133 / :141 / :170 / :180 / :201）。**加一个新工具 = 同时改
  tool_schema.cpp 的列表 + agent_tools.cpp 的 if 分支 + 通常还要改 engine 接口**。
- `cpp/src/ai/agent_tools.cpp:18-29` — `writeTools()` 手写一个 `QSet<QString>` 列出
  6 个写工具，与上面 if 分支平行维护，注释自己承认"the gate list never drifts from the
  actual registered tools"——靠纪律，不靠机制。

### 2.2 C++ 唯一的 dlopen 点：SoapySDR 后端，不是插件架构

- `cpp/src/tx/soapy_tx_backend.cpp:80-90` — `loadLib()` 在 Linux 下
  `dlopen("libSoapySDR.so", RTLD_NOW | RTLD_LOCAL)`，找不到再试 `libSoapySDR.so.0.8`；
  Windows 走 `LoadLibraryA`。这是**为了在没有 SoapySDR 开发头文件的机器上也能编译**
  的条件加载（见文件头注释 `:2`），不是"用户丢一个 .so 进来就能用"的插件系统。
  没有 `_INFO_`、没有实例工厂、没有菜单注入——纯粹是延迟绑定一个库。
- 全仓 grep `QLibrary|dlopen|registerHandler|class.*Registry` 在 `cpp/src/` 下
  只命中这一个文件。**C++ 侧没有任何模块注册表**。

### 2.3 C++ 设备层：已近似 SourceHandler，但只支持单源

- `cpp/src/dsp/spectrum_engine.h`（推断，见 `agent_tools.cpp:5` 引入）——
  `SpectrumEngine*` 是所有 AI 工具的唯一入口，`onSetCenterFreq / setDemodMode /
  setBandwidth / scanBand / startRecording / centerFreq()` 等都是它的成员函数。
  工具直接持有 `SpectrumEngine*` 指针调用，**没有"按名注册多个 SourceHandler"的机制**，
  也没有 `selectSource(name)` 这种运行时换源的能力。

### 2.4 Python 层：已经是真插件注册表（与 C++ 形成反差）

- `mbdsdr_ai/tool_registry.py:72` — `self.tools: Dict[str, Dict] = {}`，
  名字 → `{definition, handler, available, category}`。
- `mbdsdr_ai/tool_registry.py:80-103` — `register(name, ..., handler, ...)`：
  纯 dict 写入，外部模块调一次即可注入新工具。
- `mbdsdr_ai/tool_registry.py:235-267` — `call(tool_name, args)`：
  `tool = self.tools[tool_name]` → `tool["handler"](args)`，**dict 查表，无 switch**。
- `docs/modularization_status.md:13` — 明确记录："调度是 `tool_registry.call(name, args)`
  的 dict 查表 … 全部走 `.call()`。`register_builtin_tools` 下挂的各 `register_*_tools`
  只是注册分组，不是调度 switch。"
- 同文 :14 — `skill_registry.py` 连目录扫描 + 懒加载都有（`skills/<name>/SKILL.md`
  自动发现）；:15 — `decoder_registry.py` 注册表 API 干净（但当前未接主调度，属"孤儿"）。

**结论**：MBDSDR 的"插件架构"在 Python 层已经走通（tool/skill/decoder/mode 四个注册表），
但在 C++ 桌面内核层完全没有对应物——C++ 的 8 个 AI 工具是编译期硬编码的。

---

## 3. 差距判定（四要素逐项）

| # | 能力 | 上游做法（file:line） | MBDSDR 现状（file:line） | 判定 |
|---|---|---|---|---|
| G1 | 动态库加载 + 五符号契约 | `core/src/module.cpp:34-44` dlopen+dlsym 五符号 | `cpp/src/tx/soapy_tx_backend.cpp:82` 仅 SoapySDR 延迟绑定 | **未实现**（C++ 侧无插件加载器） |
| G2 | 模块实例工厂 + maxInstances 配额 | `core/src/module.cpp:86-106` | 无对应物；工具是函数集合，无"实例"概念 | **未实现**（但概念上 MBDSDR 工具本就无状态，可能不需要） |
| G3 | 两阶段初始化 postInit / doPostInitAll | `module.h:46`, `module.cpp:181-186` | 无；C++ 工具在 agent 构造时直接可用 | **未实现** |
| G4 | SourceHandler 回调表 + 按名注册多源 | `signal_path/source.h:13-22`, `source.cpp:10-60` | `SpectrumEngine*` 单指针直接调用（`agent_tools.cpp:90`） | **已实现但缺深度**：有"设备"概念，但没有多源注册表 / 运行时换源 |
| G5 | 菜单构造即注册 / 析构即反注册（RAII 注入） | `gui/widgets/menu.cpp:9-25`, `demo_module/main.cpp:17-21` | Qt UI 在 MainWindow 里硬编码构造（见 `modularization_status.md:16` panel_registry 残留） | **已实现但缺深度**（Python panel_registry 有雏形，C++ Qt 侧仍硬编码） |
| G6 | 跨模块通信 ModuleComManager | `module_com.h:6-18`, `module_com.cpp:4-52` | 无；模块间靠直接持有指针 | **未实现**（但 MBDSDR 模块耦合度低，可能不需要） |
| G7 | 生命周期事件（onInstanceCreated/Deleted） | `module.h:96-98` | Python `tool_registry` 无事件；C++ 无 | **未实现** |
| G8 | "加一个扩展 = 不改调度代码" | SDR++ 加模块 = 写一个 .so；Python `tool_registry.py:80` register | C++ 加工具 = 改 `tool_schema.cpp` + `agent_tools.cpp` if 分支 | **已实现但缺深度**（Python 侧真做到，C++ 侧没做到） |

---

## 4. 落地建议（按价值×可验证性排序）

### 4.1 高优先：C++ 工具注册表（对标 G8，不必上 dlopen）

**不要**照搬 SDR++ 的 `.so` 动态加载——MBDSDR 的 C++ 内核规模小、部署形态是单二进制，
上 dlopen 是过度工程。但应把 `tool_schema.cpp:56-199` 的硬编码列表 +
`agent_tools.cpp:96-201` 的 if-else 链，重构为**进程内注册表**：

```cpp
// 自有设计（干净室，非 GPL 复制）
struct ToolEntry {
    ToolSchemaSpec spec;
    std::function<QString(const QJsonObject&, dsp::SpectrumEngine*)> handler;
};
class ToolRegistry {
    std::map<QString, ToolEntry> tools_;
public:
    void registerTool(ToolEntry e);                       // 对应 SDR++ registerSource
    QList<ToolDef> listToolDefs() const;                  // 替代 registeredToolSpecs()
    QString call(const QString& name, const QJsonObject&, dsp::SpectrumEngine*);
};
```
- 收益：加第 9 个工具时，只在一个 `register_*.cpp` 里调 `registry.registerTool(...)`，
  不动 `agent_tools.cpp` 主调度；`writeTools()` 白名单也可以挪进 `ToolEntry`
  （`bool write;` 字段），消除 :18-29 与 if 分支的双写漂移。
- 云内可确定性验证：**是**——纯 C++ 逻辑，写两个假 handler 注册 / 查不到 /
  正常分发三条 ctest 即可，不需硬件、不需 GUI。

### 4.2 中优先：SourceHandler 风格的多源注册表（对标 G4）

如果未来要支持"离线文件源 / 网络 IQ 源 / 硬件源"并列切换（目前 `SpectrumEngine*`
是单态的），可以学 `signal_path/source.h:13-22` 的回调表设计：
- `struct SourceHandler { dsp::stream<complex_t>* stream; std::function<void()> start/stop/tune; }`
- `SourceManager::registerSource(name, handler)` + `selectSource(name)`
- 不一定要做 dlopen——源可以是进程内注册的 C++ 对象（对标 Python
  `tool_registry.py:80` 的 register，而不是对标 SDR++ 的 .so）。
- 云内可确定性验证：**是**——注册一个合成信号源、select、验证 stream 被
  iqFrontEnd 拿到。但当前没有多源需求时，**建议延后**。

### 4.3 低优先：ModuleComManager 风格的跨模块总线（对标 G6）

MBDSDR 的 C++ 模块目前通过 `SpectrumEngine*` 直接调用，耦合面已经收敛在 8 个工具上。
在模块数量没有爆炸前，`module_com.cpp:4` 那种"按名查表 + code 分发"的 RPC 总线
属于**杀鸡用牛刀**。建议等 C++ 侧工具数 > 20 或出现"解码器想反过来影响调谐"这种
双向耦合时再考虑。云内可验证（纯注册表），但落地价值低。

### 4.4 不建议落地：完整的 `.so` 插件加载器（对标 G1）

理由：
1. MBDSDR C++ 内核是单二进制部署，用户没有"丢一个 .so 进 plugins/ 就用"的需求；
2. Python 层 `tool_registry.py` 已经承担了"运行时扩展"的角色，C++ 侧再做一套
   动态加载会造成两套插件系统并存的复杂度；
3. SDR++ 做 .so 是因为它要支持几十个硬件厂商 SDK 各自独立编译、独立发版——
   MBDSDR 没有这个场景。
- 云内可确定性验证：**部分**（能写测试 .so 并 dlopen，但价值不支撑工作量）。

---

## 5. 差距判定片段（供 Wave2 整合使用）

| 条目 | 上游 file:line | MBDSDR file:line | 判定 | 落地价值 | 云内可确定性验证? |
|---|---|---|---|---|---|
| C++ 工具注册表（替代 if-else 分发） | `core/src/signal_path/source.cpp:10-17`（注册范式）；`core/src/module.cpp:86-106`（工厂范式） | `cpp/src/ai/tool_schema.cpp:56-199` 硬编码列表；`cpp/src/ai/agent_tools.cpp:96-201` if-else 链 | 已实现但缺深度（Python 侧 `mbdsdr_ai/tool_registry.py:80,235` 已真做到，C++ 侧没有） | **高** | **是**（纯 C++ map 查表 + 假 handler ctest） |
| 多 Source 回调表 + 运行时换源 | `core/src/signal_path/source.h:13-22`；`source.cpp:42-60` selectSource | `cpp/src/ai/agent_tools.cpp:90` 直接持有 `SpectrumEngine*`；无多源注册 | 已实现但缺深度 | 中（等多源真实需求出现） | 是（合成源 + select 测试） |
| 两阶段初始化 postInit | `core/src/module.h:46`；`module.cpp:181-186` doPostInitAll | 无对应物 | 未实现 | 低（当前无跨模块初始化依赖） | 是 |
| 菜单/面板 RAII 注入 | `core/src/gui/widgets/menu.cpp:9-25`；`demo_module/main.cpp:17-21` | `docs/modularization_status.md:16` panel_registry 残留硬编码 dock；C++ Qt MainWindow 硬构造 | 已实现但缺深度 | 中 | 部分（需 Qt GUI 环境，headless 难验证） |
| 跨模块 ModuleCom 总线 | `core/src/module_com.h:6-18`；`module_com.cpp:43-52` callInterface | 无；靠直接指针 | 未实现 | 低（模块数未爆炸前不需要） | 是 |
| 动态 .so 插件加载器 | `core/src/module.cpp:34-44` dlopen+dlsym 五符号 | `cpp/src/tx/soapy_tx_backend.cpp:82` 仅 SoapySDR 延迟绑定，非插件 | 未实现 | **低**（单二进制部署，Python 层已承担扩展） | 部分 |

**价值分布**：高 1 / 中 2 / 低 3。**可确定性验证**：是 5 / 部分 1。
**Wave2 推荐只落地第 1 条**（C++ 工具注册表重构）——它同时解决 G8 的硬编码漂移、
消除 `writeTools()` 双写维护、且云内 ctest 可完全确定性验证。其余条目等真实需求出现
再启动。
