# SDR++ 模块系统 / 频率管理器 / 扫描器 / 录制器 / 主题 — 源码精读笔记

> 本文件是 MBDSDR 移植前的上游机制笔记，所有结论都带 `file:line` 回指
> （相对 `repos/sdrpp/`）。移植产物：`mbdsdr_ai/module_system.py`、
> `mbdsdr_ai/frequency_manager_v2.py`、`mbdsdr_ai/scanner.py`。

---

## 1. 模块系统（Module System）

### 1.1 全局单例（core.h）
- `core/src/core.h:7-14` — `namespace core` 导出四个全局对象：
  `configManager`、`moduleManager`、`modComManager`、`args`。
  即"模块管理器/配置管理器/模块间通信/命令行参数"是进程级单例。
- `core/src/core.h:13` — `setInputSampleRate(double)` 是全局采样率变更入口。

### 1.2 模块基类与注册表（module.h）
- `core/src/module.h:31-102` — `class ModuleManager`：
  - `ModuleInfo_t`（:33-41）：name / description / author / version(M,m,b) / **maxInstances**。
    `maxInstances<=0` 表示不限实例数。
  - `Instance` 抽象基类（:43-50）：`postInit()`、`enable()`、`disable()`、`isEnabled()`。
    所有模块实例都要继承它。
  - `Module_t`（:52-73）：动态库句柄 + 五个 C 符号 `_INFO_/_INIT_/_CREATE_INSTANCE_/_DELETE_INSTANCE_/_END_`。
  - `Instance_t`（:75-78）：`{Module_t module, Instance* instance}`。
  - `modules`（:100）：`map<name, Module_t>` 已加载类；`instances`（:101）：`map<实例名, Instance_t>`。
- `core/src/module.h:104` — 宏 `SDRPP_MOD_INFO` 就是模块导出的元数据符号。

### 1.3 动态加载与实例生命周期（module.cpp）
- `core/src/module.cpp:5-84` — `loadModule(path)`：
  - `dlopen`/`LoadLibrary`（:34/:22）打开 `.so/.dll/.dylib`。
  - `dlsym` 五个必需符号（:40-44），缺一即报错返回（:46-70）。
  - 重名检查（:71-75）、同句柄去重（:76-80）、`mod.init()` 后入表（:81-82）。
- `core/src/module.cpp:86-106` — `createInstance(name, module)`：
  类存在性 → 实例名唯一 → `maxInstances` 上限（:95-99）→ 调 `createInstance` 工厂 → emit `onInstanceCreated`。
- `core/src/module.cpp:108-119` — `deleteInstance(name)`：emit `onInstanceDelete` → 调 `deleteInstance` 析构 → 从表移除 → emit `onInstanceDeleted`。
- `core/src/module.cpp:126-142` — `enableInstance/disableInstance` 直接转发到 `Instance::enable/disable`。
- `core/src/module.cpp:181-186` — `doPostInitAll()`：所有实例构造完后统一 `postInit()`（解决模块间相互依赖）。
- **关键设计**：模块"类"与"实例"分离——一个 .so 是一个类，可 `createInstance` 出多个实例。

### 1.4 模块间通信（module_com.h）
- `core/src/module_com.h:6-10` — `ModuleComInterface{ moduleName, ctx, handler(code,in,out,ctx) }`。
- `core/src/module_com.h:12-22` — `ModuleComManager`：按名字注册接口、`callInterface(name, code, in, out)` 跨模块 RPC。
  这是 SDR++ 里"radio 模块的 VFO 怎么被频率管理器调谐"的通道（见 frequency_manager main.cpp:114-122）。

### 1.5 数据流：双缓冲 stream（dsp/stream.h）
- `core/src/dsp/stream.h:9` — `STREAM_BUFFER_SIZE = 1e6`（1 MSample）。
- `core/src/dsp/stream.h:24-141` — 模板 `stream<T>`：
  - 双缓冲 `writeBuf/readBuf`（:125-126）。
  - `swap(size)`（:43-68）：生产者写完一块后交换缓冲、通知读者。
  - `read()`（:70-76）：消费者阻塞等数据就绪。
  - `flush()`（:78-92）：消费者读完通知生产者可以再写。
  - `stopWriter/stopReader`（:94-116）：优雅停流。
- **移植要点**：Python 里用 `numpy.ndarray` 按块流式传递；端口即 `(module, port_name)` 端点；
  多输入多输出 = 模块持有 `input_ports: list[Port]` / `output_ports: list[Port]`。

### 1.6 Source/Sink 注册（signal_path/）
- `core/src/signal_path/source.h:13-22` — `SourceHandler{ stream, menuHandler, selectHandler, deselectHandler, startHandler, stopHandler, tuneHandler, ctx }`。
- `core/src/signal_path/source.h:29-38` — `registerSource/unregisterSource/selectSource/start/stop/tune`。
- `core/src/signal_path/signal_path.h:8-13` — `sigpath` 命名空间导出 `iqFrontEnd/vfoManager/sourceManager/sinkManager` 四个单例。

---

## 2. 频率管理器（misc_modules/frequency_manager/src/main.cpp）

### 2.1 数据结构
- `:25-30` — `FrequencyBookmark{ double frequency; double bandwidth; int mode; bool selected; }`。
  注意上游书签只有 4 个字段；我们 v2 加 `name/notes/tags/group/color/icon`。
- `:32-36` — `WaterfallBookmark{ listName, bookmarkName, bookmark }` — 给瀑布图画线用的扁平视图。
- `:40-49` — 解调模式枚举字符串：`NFM/WFM/AM/DSB/USB/CW/LSB/RAW`。
- `:53-58` — 书签瀑布显示模式：`Off/Top/Bottom`。

### 2.2 分组（list）与持久化
- `:38` — `ConfigManager config;`（每模块一个配置文件）。
- `:827-836` — `_INIT_()` 默认配置：`selectedList="General"`、`lists["General"]["showOnWaterfall"]=true`、`lists["General"]["bookmarks"]={}`；
  路径 `<root>/frequency_manager_config.json`；`enableAutoSave()`。
- `:337-347` — `saveByName(listName)`：把内存 `bookmarks` map 整体写回 `config.conf["lists"][listName]["bookmarks"]`，
  然后 `refreshWaterfallBookmarks(false)` + `config.release(true)`（true=立即落盘）。
- `:315-335` — `loadByName(listName)`：从 config 把某 list 的 bookmarks 读进内存 map。
- `:287-304` — `refreshWaterfallBookmarks()`：遍历所有 list，挑 `showOnWaterfall==true` 的，拍平成 `waterfallBookmarks` 供 UI 画线。
- **移植要点**：我们 v2 用"分组=group"概念（航空/海事/业余/广播/卫星），每组带 color/icon；
  JSON 原子写（写临时文件再 `os.replace`）到 `~/.mbdsdr/bookmarks_v2.json`。

### 2.3 书签 CRUD
- `:126-193` — `bookmarkEditDialog()`：新建/编辑共用；Apply 时若编辑则先 `bookmarks.erase(oldName)` 再 `bookmarks[newName]=edited`（:178-182），然后 `saveByName`。
- `:462-485` — 删除选中书签：`bookmarks.erase(name)` 后 `saveByName`。
- `:446-457` — 自动命名：`New Bookmark`，冲突则 `New Bookmark (1)`…（最多 1000）。
- `:107-124` — `applyBookmark`：通过 `modComManager` 调 radio 接口 `SET_MODE/SET_BANDWIDTH`，再 `tuner::tune(...)`。

### 2.4 导入导出
- `:755-786` — `importBookmarks(path)`：读 JSON，要求顶层有 `"bookmarks"` 对象；重名跳过并 warn。
- `:788-792` — `exportBookmarks(path)`：`ofstream << exportedBookmarks`。
- **移植要点**：上游只支持 JSON；我们 v2 额外加 CSV import/export（任务要求）。
- **上游没有 `nearest()`**——任务要求的"nearest(freq, max_distance) 查找"是我们的增强。

---

## 3. 扫描器（misc_modules/scanner/src/main.cpp）

### 3.1 可调参数（默认值）
- `:275-282` —
  - `startFreq=88e6`、`stopFreq=108e6`（默认扫 FM 广播段）
  - `interval=1e5`（步进 100 kHz）
  - `passbandRatio=10.0`（检测带宽 = VFO 带宽 × 10%）
  - `tuningTime=250 ms`（换频后等待稳定时间）
  - `lingerTime=1000 ms`（信号消失后继续停留时间）
  - `level=-50.0 dB`（固定触发门限）

### 3.2 扫描主循环
- `:124-129` — `start()`：`current=startFreq`，起 worker 线程。
- `:139-230` — `worker()`：**10 Hz 循环**（:142 `sleep 100ms`）。
  - `:152` — `tuner::normalTuning(vfo, current)` 调谐到当前频点。
  - `:155-161` — 若正在 `tuning` 状态，等 `tuningTime` 后才开始测功率。
  - `:164-166` — `gui::waterfall.acquireLatestFFT(dataWidth)` 拿当前 FFT 帧。
  - `:169-175` — 算瀑布图可见频段 `wfStart/wfEnd/wfWidth` 和 VFO 带宽。
  - **接收态**（:177-187）：持续测 `current` 处功率，若 `>= level` 刷新 `lastSignalTime`；
    若低于门限超过 `lingerTime`，退出接收态继续扫。
  - **寻找态**（:188-224）：先按扫描方向 `findSignal`（:194），找不到再反方向找（:200-205），
    都找不到就 `current += interval`（:210-217），循环回绕。
  - `:220-223` — 若新 `current` 落在可见带宽外，置 `tuning=true` 等重调。

### 3.3 信号检测
- `:232-256` — `findSignal(scanDir, ...)`：从 `current±interval` 开始按 `interval` 步进，
  每个候选频点调 `getMaxLevel`，超过 `level` 就 `receiving=true; current=freq; break`。
- `:258-268` — `getMaxLevel(data, freq, width, dataWidth, wfStart, wfWidth)`：
  把 `[freq-width/2, freq+width/2]` 线性映射到 FFT bin 下标 `[lowId, highId]`，取最大值。
- **上游局限**：门限是**固定 dB 绝对值**（:282 `level=-50`），不适应不同噪声底；
  且一次只跳一个频点，不提取"连续活动段"。
- **我们增强**（scanner.py）：
  - 噪声底用**中位数**估计，门限 = 噪声底 + `threshold_db`（自适应）。
  - 连续超阈值频点**合并为活动段**，输出 `{start_freq, end_freq, peak_freq, peak_db, bandwidth}`。
  - 优先级队列：检测到活动段后可回调跳到该频点解调。

---

## 4. 录制器（misc_modules/recorder/src/main.cpp）

- `:28` — `SILENCE_LVL 1e-5` 静音检测阈值。
- `:30-36` — MOD_INFO：maxInstances=-1（不限）。
- `:40-43` — 时区枚举 Local/UTC。
- `:57-62` — 容器只开 WAV（RF64 注释掉）；采样类型 Uint8/Int16/Int32/Float32。
- `:50` — 文件名模板 `$t_$f_$h-$m-$s_$d-$M-$y`（时间/频率/时/分/秒/日/月/年）。
- `:108-117` — DSP 链：volume → splitter → meterStream；stereoStream → stereoSink；
  stereo→mono → monoSink；basebandSink 直接接 IQ。
- **移植要点**：录制器已有 `mbdsdr_ai/recorder.py`（不在本次新建范围）；本次只记录机制供对照。

---

## 5. 主题系统（core/src/gui/style.cpp）

- `:8-20` — `namespace style`：`baseFont/bigFont/hugeFont`、`uiScale`（桌面 1.0 / Android 3.0）。
- `:22-53` — `loadFonts(resDir)`：Roboto-Medium，16/45/128 px 三档；bigFont 只包含 `.` 和数字（给大数字频率显示），
  hugeFont 只含 `SDR+` 字符（给 splash）。
- `:55-68` — `beginDisabled()`：把 Button/FrameBg/Text 透明度压到 0.15/0.30/0.65（半透明禁用态）。
- `:70-73` — `endDisabled()` 弹回。
- **注意**：style.cpp 本身不做深/浅色切换——深浅色是调用 `ImGui::StyleColorsDark/Light`，
  style.cpp 只管字体缩放和禁用态透明度。我们 v2 模块里给分组颜色用一份命名色板即可，不引 ImGui。

---

## 6. 移植映射表

| SDR++ 上游 | MBDSDR 新文件 | 说明 |
|---|---|---|
| `ModuleManager` + `Module_t`/`Instance_t` (module.h:31-102) | `ModuleRegistry` + `Module` 基类 | 类/实例分离，register/create/list |
| `Instance::enable/disable/postInit` (module.h:43-50) | `Module.start/stop/post_init` | |
| `dsp::stream<T>` 双缓冲 (stream.h:24-141) | `SignalGraph` + numpy ndarray 块流 | 同步 process() 调用，不搞线程双缓冲（先简化） |
| `SourceHandler`/`SinkHandler` (source.h:13-22) | `ModuleType.source/sink/demod/tool` + input/output_ports | |
| `FrequencyBookmark` (fm main.cpp:25-30) | `BookmarkV2` dataclass | 加 name/notes/tags/group/color/icon |
| `lists[*].bookmarks` JSON (fm :827-851) | `~/.mbdsdr/bookmarks_v2.json` 原子写 | |
| `importBookmarks` JSON (fm :755-786) | import_json + **import_csv**（增强） | |
| （无） | `nearest(freq, max_distance)` | **增强** |
| `findSignal` 固定电平 (scanner :232-256) | `SweepScanner` 噪声底中位数+阈值 | **增强自适应门限** |
| （无，上游只跳单频） | 活动段合并 `{start,end,peak,peak_db,bw}` | **增强** |
| （无） | AI 推荐模块链 / 自动分类书签 / 信号类型识别钩子 | 留 hook，默认规则实现 |
