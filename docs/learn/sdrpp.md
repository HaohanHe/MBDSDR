# SDR++ (SDRPlusPlus) 源码学习笔记

> 仓库: https://github.com/AlexandreRouma/SDRPlusPlus
> 本地路径: `/home/user/Doubao/chats/38438160041798146/repos/sdrpp`
> 学习目标: 拆解模块化信号流 / 源插件 / 频谱瀑布 / 设备抽象四层, 并对照 MBDSDR 给出可迁移点.
> 所有 `file:line` 引用均相对仓库根目录.

---

## 0. 总览: 两层"模块"概念不要混淆

SDR++ 里 "Module" 这个词被用在两个完全不同的层次上, 读源码时必须先分清:

1. **插件模块 (Plugin Module)**: 动态库 `.so/.dll/.dylib`, 由 `ModuleManager` 通过 `dlopen` 加载.
   一个源 (RTL-SDR)、一个 sink (Audio)、一个解调器 (Radio) 都是这种插件. 接口定义在
   `core/src/module.h` 和 `core/src/module.cpp`.
2. **DSP 处理块 (DSP block)**: 信号流里逐采样处理的 C++ 对象, 基类是 `dsp::block` / `dsp::Processor<I,O>`.
   接口定义在 `core/src/dsp/block.h` 和 `core/src/dsp/processor.h`.

这两层之间通过 `signal_path/` 下的全局单例 (`SourceManager`, `IQFrontEnd`, `VFOManager`, `SinkManager`)
粘合. 下文 1~2 节讲 DSP block 信号流, 2 节讲插件机制, 3~5 节讲渲染与设备.

---

## 1. 模块化信号流架构 (DSP block / 端口 / 调度)

### 1.1 基类 `dsp::block`: 每块一个工作线程, run() 是唯一调度入口

`core/src/dsp/block.h:18` 定义 `class block : public generic_block`. 关键成员:

- 纯虚 `virtual int run() = 0;` — `block.h:64`. 这就是每个 DSP 块的"主循环体".
- `workerLoop()` 就是一个死循环: `while (run() >= 0) {}` — `block.h:67-69`.
- `doStart()` 里 `workerThread = std::thread(&block::workerLoop, this);` — `block.h:71-73`.
  **即: 每个 block 一旦 start, 就独占一个 OS 线程跑自己的 run()**.
- `doStop()` 先对所有输入流 `stopReader()`, 对所有输出流 `stopWriter()`, 再 `join()` worker 线程,
  最后 `clearReadStop/clearWriteStop` — `block.h:75-94`. 这是优雅停机的标准范式.
- `tempStart()/tempStop()` 带递归深度计数 `tempStopDepth`, 用于在线重配置时短暂停块而不破坏外层状态
  — `block.h:46-62`.
- 控制互斥: `std::recursive_mutex ctrlMtx;` — `block.h:122`.
- 端口注册表: `std::vector<untyped_stream*> inputs, outputs;` — `block.h:124-125`,
  由 `registerInput/unregisterInput/registerOutput/unregisterOutput` 维护 — `block.h:104-118`.

**断言**: block 的端口模型是"无类型流指针 vector", block 自己不关心流里跑的是什么类型,
类型安全由子类 `Processor<I,O>` 在编译期保证.

### 1.2 类型化处理块 `dsp::Processor<I,O>`: process 签名与 run 宏

`core/src/dsp/processor.h:41` 定义 `template <class I, class O> class Processor : public block`.

- 输出端口 `stream<O> out;` 是公开成员 — `processor.h:69`.
- 输入端口 `stream<I>* _in;` 是 protected — `processor.h:72`.
- `init(stream<I>* in)` 把 `_in` 注册成输入, 把 `&out` 注册成输出 — `processor.h:50-55`.
- `setInput(stream<I>* in)` 会 `tempStop()` → 换流 → `tempStart()`, 实现运行时换源
  — `processor.h:57-65`.

**process 方法签名约定** (重点): SDR++ 并不要求子类实现统一的 `process()` 虚函数, 而是用宏
把 `run()` 模板化. 见 `processor.h:7-19`:

```cpp
#define OVERRIDE_PROC_RUN(exp) \
    int run() { \
        int count = _in->read();        /* 阻塞等输入 */ \
        if (count < 0) { return -1; }   /* 流被关了 */ \
        exp;                              /* 用户写的处理表达式 */ \
        base_type::_in->flush();          /* 通知上游我读完了 */ \
        if (!base_type::out.swap(count)) { return -1; } /* 把输出切给下游 */ \
        return count; \
    }
#define DEFAULT_PROC_RUN  OVERRIDE_PROC_RUN(process(count, _in->readBuf, out.writeBuf))
```

也就是说, 真正的处理函数签名是 **`int process(int count, const I* in, O* out)`** —
由 `DEFAULT_PROC_RUN` 宏在 `processor.h:37` 展开. 多速率块用 `DEFAULT_MULTIRATE_PROC_RUN`
(`processor.h:38`), 它允许 process 返回"实际产出样本数"而不是消费数.

实例: `core/src/dsp/multirate/rational_resampler.h:82-96` 的 `RationalResampler::process(count, in, out)`
就是这个签名, 返回 `outCount`; 它自己的 `run()` 在 `rational_resampler.h:98-110`,
严格遵守 `read() → process → flush() → swap(outCount)` 四步.

### 1.3 端口连接原语: `dsp::stream<T>` 双缓冲环形交换

`core/src/dsp/stream.h:25` 定义 `stream<T>`. 这是 block 之间唯一的数据通道:

- 默认缓冲 1 MSample: `#define STREAM_BUFFER_SIZE 1000000` — `stream.h:9`.
- 构造时各分配一块 `writeBuf` / `readBuf` — `stream.h:27-30`.
- 关键握手 (生产者/消费者跨线程):
  - 生产者写满后调 `swap(size)` — `stream.h:43-68`: 等 `canSwap` 条件变量, 交换两个指针,
    置 `dataReady`, 通知消费者.
  - 消费者调 `read()` 阻塞等 `dataReady` — `stream.h:70-76`, 返回 `dataSize` (样本数).
  - 消费者处理完调 `flush()` — `stream.h:78-92`: 清 `dataReady`, 置 `canSwap`, 通知生产者可以写下一块.
  - `stopWriter/stopReader/clear*` 用于停机解除阻塞 — `stream.h:94-116`.

**这是一种"双缓冲 + 条件变量"的定长块交换, 不是字节流, 也不是环形队列**.
生产者一次写满 N 个 sample 才 swap, 消费者一次 read 拿到 N 个 sample. 块大小由上游决定.

### 1.4 Source / Sink 基类

- `core/src/dsp/source.h:5-20`: `Source<T>` 只 `registerOutput(&out)`, 没有输入, `run()=0`.
  纯生产者 (rtl-sdr 读 USB, 文件读盘).
- `core/src/dsp/sink.h:5-35`: `Sink<T>` 只 `registerInput(_in)`, 没有输出端口, `run()=0`.
  纯消费者 (扬声器, 写盘).
- `Processor<I,O>` 是中间处理块 (有进有出).

### 1.5 链式拓扑: `dsp::chain<T>` 与 fan-out `Splitter`

`core/src/dsp/chain.h:32` `addBlock(block, enabled)` 把处理块串成一条直线:
- `enableBlock` (`chain.h:61-90`): 找到前一个已启用块 `before` 和后一个 `after`,
  把 `after->setInput(&block->out)`, 把 `block->setInput(before ? &before->out : _in)`.
  **即: 链的"接线"本质就是反复调用 `setInput()` 改流指针**.
- `disableBlock` (`chain.h:92-118`): 把 `after->setInput(before ? &before->out : _in)`,
  旁路掉被禁用的块, 然后 `block->stop()`.
- `setBlockEnabled` (`chain.h:120-128`) 是上面两个的便捷包装.
- `start()/stop()` 只对 `states[ln]==true` 的块启动线程 — `chain.h:144-160`.

IQ 前端里实际用法见 `core/src/signal_path/iq_frontend.cpp:36-41`:
```cpp
preproc.init(&inBuf.out);
preproc.addBlock(&decim, _decimRatio > 1);
preproc.addBlock(&dcBlock, dcBlocking);
preproc.addBlock(&conjugate, false); // IQ 翻转
split.init(preproc.out);
```
三级预处理 (抽取→DC阻塞→共轭) 串成 chain, 然后接到 `Splitter`.

`core/src/dsp/routing/splitter.h:46-61` 的 `Splitter::run()` 是 fan-out:
从 `_in->readBuf` 读一块, 对每个绑定的下游流 `memcpy` 一份再 `swap(count)`.
所以 SDR++ 的"一进多出"是**拷贝复制**而非零共享读, 每个下游拿到自己的 writeBuf.

### 1.6 调度模型总结

- 拓扑: Source → chain<Processor> → Splitter → 多个 (VFO Processor链) + FFT分支.
- 并发: 每个 block 一个线程, 块间用 `stream<T>` 双缓冲 + 条件变量同步.
- 流控: 背压由 `swap()` 阻塞实现 — 消费者慢, 生产者就卡在 swap 上.
- 重配置: `tempStop()/tempStart()` 递归深度计数, 避免嵌套 setInput 把状态搞乱.

---

## 2. 源/Sink 插件机制: 注册与连接

### 2.1 动态库导出符号约定

`core/src/module.h:17-29` 定义跨平台宏: Windows 用 `__declspec(dllexport)`,
Linux 用 `extern "C"`, macOS 用 `.dylib`. 扩展名 `.so/.dll/.dylib` — `module.h:20-28`.

插件必须导出 5 个 C 符号 (见 `core/src/module.cpp:40-44` 的 dlsym):
- `_INFO_`   → `ModuleInfo_t*` (name/desc/author/version/maxInstances) — `module.h:33-41`.
- `_INIT_`   → `void()` 库级初始化 (加载配置).
- `_CREATE_INSTANCE_(name)` → `Instance*` 工厂.
- `_DELETE_INSTANCE_(Instance*)` → 工厂析构.
- `_END_`    → 库级收尾 (存配置).

`SDRPP_MOD_INFO` 宏 (`module.h:104`) 就是 `_INFO_` 的语法糖. RTL-SDR 插件实例见
`source_modules/rtl_sdr_source/src/main.cpp:17-23`:
```cpp
SDRPP_MOD_INFO{ "rtl_sdr_source", "RTL-SDR source module for SDR++",
                "Ryzerth", 0, 1, 0, /*maxInstances*/ 1 };
```
末尾四个导出符号在 `main.cpp:587-607`.

### 2.2 `ModuleManager`: dlopen + 工厂表

`core/src/module.cpp:34` `dlopen(path, RTLD_LAZY | RTLD_LOCAL)`, 然后依次 `dlsym` 五个符号
(`module.cpp:40-44`), 缺任何一个都报错并返回空 handle (`module.cpp:46-70`).
`loadModule` 末尾调 `mod.init()` 并把模块存进 `std::map<std::string, Module_t> modules` — `module.cpp:81-82`.

`createInstance(name, module)` (`module.cpp:86-106`) 检查 `maxInstances` (`module.cpp:95-99`),
调 `inst.module.createInstance(name)` 构造, 存进 `instances` map, 发 `onInstanceCreated` 事件.

加载顺序在 `core/src/gui/main_window.cpp:100-143`: 先扫 `modulesDir` 目录里所有 `.so` 调
`loadModule` (只 dlopen + `_INIT_`), 再按配置 `moduleInstances` 逐个 `createInstance`,
未启用的立即 `disableInstance` — `main_window.cpp:136-143`. 最后 `doPostInitAll()` (`main_window.cpp:227`).

### 2.3 `Instance` 接口: 插件必须实现的 4 个虚函数

`core/src/module.h:43-50`:
```cpp
class Instance {
public:
    virtual ~Instance() {}
    virtual void postInit() = 0;
    virtual void enable() = 0;
    virtual void disable() = 0;
    virtual bool isEnabled() = 0;
};
```
RTL-SDR 实现见 `main.cpp:103-115` (空 postInit, 三行 enable/disable/isEnabled).
**注意: Instance 接口本身没有任何"信号流"方法** — 插件怎么进信号流? 答案是下一节的 `SourceHandler`.

### 2.4 Source 插件如何接入信号流: `SourceHandler` 函数指针表

`core/src/signal_path/source.h:13-22` 定义:
```cpp
struct SourceHandler {
    dsp::stream<dsp::complex_t>* stream;   // 插件自己产生的 IQ 流
    void (*menuHandler)(void* ctx);
    void (*selectHandler)(void* ctx);
    void (*deselectHandler)(void* ctx);
    void (*startHandler)(void* ctx);
    void (*stopHandler)(void* ctx);
    void (*tuneHandler)(double freq, void* ctx);
    void* ctx;
};
```
这是一组 C 风格回调 + 上下文指针. RTL-SDR 在构造函数里填这张表 (`main.cpp:66-73`),
然后一行注册: `sigpath::sourceManager.registerSource("RTL-SDR", &handler);` — `main.cpp:95`.

`SourceManager::registerSource` (`core/src/signal_path/source.cpp:10-17`) 把 handler 存进
`std::map<std::string, SourceHandler*> sources` 并 emit `onSourceRegistered`.

`selectSource(name)` (`source.cpp:42-60`): 先对旧源 `deselectHandler`, 再对新源 `selectHandler`,
**关键接线动作**在 `source.cpp:57`: `sigpath::iqFrontEnd.setInput(selectedHandler->stream);`
—— 即把 IQ 前端的输入直接指向插件的 `dsp::stream<complex_t>`. 这就是"源插件连接"的全部.

`start()/stop()/tune(freq)` 只是转发到对应 handler (`source.cpp:69-91`).
`unregisterSource` (`source.cpp:19-34`) 若卸载的是当前选中源, 会把输入切回 `nullSource` (`source.cpp:29`).

### 2.5 跨插件通信: `ModuleComManager`

`core/src/module_com.h:6-10`:
```cpp
struct ModuleComInterface {
    std::string moduleName;
    void* ctx;
    void (*handler)(int code, void* in, void* out, void* ctx);
};
```
`ModuleComManager` (`module_com.h:12-23`) 维护 `map<string, ModuleComInterface>`,
插件之间通过名字查接口, 再调 `callInterface(name, code, in, out)` 做 RPC.
这是为了避免插件直接链接彼此的头文件 — 解耦插件间依赖.

---

## 3. 频谱 + 瀑布渲染: FFT 窗口 / overlap / 平均 / 颜色映射

### 3.1 FFT 在 DSP 侧: `IQFrontEnd`

`core/src/signal_path/iq_frontend.h:17-21` 三种窗口枚举:
```cpp
enum FFTWindow { RECTANGULAR, BLACKMAN, NUTTALL };
```
默认配置在 `core/src/core.cpp:128`: `defConfig["fftWindow"] = 2;` (NUTTALL),
`fftSize = 65536` (`core.cpp:127`), `fftRate = 20` (`core.cpp:126`).

**FFT 帧率与重叠 (overlap/skip)**: `iq_frontend.h:59-63`
```cpp
static inline void genReshapeParams(double sr, int size, double rate, int& skip, int& nz) {
    int fftInterval = round(sr / rate);   // 每多少个样本出一帧 FFT
    nzSampCount = std::min<int>(fftInterval, size);  // 实际用多少样本喂 FFT
    skip = fftInterval - nzSampCount;               // 中间丢掉多少样本
}
```
**注意**: SDR++ 的"overlap"不是标准 OLA 那种滑动重叠, 而是 **按帧率抽帧**:
每 `fftInterval` 个样本取连续 `nz` 个做一次 FFT, 中间跳过 `skip` 个.
当 `fftInterval >= fftSize` 时 `skip=0, nz=fftSize`, 即每 `fftSize/rate` 个新样本做一帧 (无重叠);
当采样率低导致 `fftInterval < fftSize` 时, 复用最近的样本并在 `iq_frontend.cpp:65` 用
`dsp::buffer::clear` 把 FFT 输入 buffer 的尾段清零补零到 fftSize.
**它不做滑动平均重叠, 而是靠下游 GUI 侧的指数平滑来降噪** (见 3.4).

`dsp::buffer::Reshaper` 负责按 `keep/nz` 和 `skip` 抽样本 — `iq_frontend.cpp:46`, `iq_frontend.cpp:277-278`.

### 3.2 窗函数生成

`iq_frontend.cpp:49-58` 初始化时按所选窗口填 `fftWindowBuf`:
- RECTANGULAR: 填 0 (实际在 updateFFTPath 里改为 `±1` 交替, 见下)
- BLACKMAN: `dsp::window::blackman(i, nz)`
- NUTTALL:  `dsp::window::nuttall(i, nz)`

`core/src/dsp/window/nuttall.h:5-8` 就是标准 4 项 Nuttall:
```cpp
const double coefs[] = { 0.355768, 0.487396, 0.144232, 0.012604 };
return cosine(n, N, coefs, 4);
```

**关键细节 — 频移调制**: `iq_frontend.cpp:283-291` 在 `updateFFTPath` 里重新生成窗口时,
乘以 `((i % 2) ? -1.0f : 1.0f)` — 即 `(-1)^n` 复调制, 等价于把频谱搬移 ±Fs/2,
使 FFT 输出的 0 频点对应基带中心而不是 Nyquist 边缘. 这是 fftshift 的手写实现.

### 3.3 FFT 执行 + 功率谱

`iq_frontend.cpp:248-267` 的 `handler()` (由 `dsp::sink::Handler` 回调驱动):
1. `volk_32fc_32f_multiply_32fc` 把复数 IQ 点乘实窗 (`iq_frontend.cpp:252`).
2. `fftwf_execute(fftwPlan)` 执行 1D FFT, plan 在 `iq_frontend.cpp:62` 用
   `fftwf_plan_dft_1d(..., FFTW_FORWARD, FFTW_ESTIMATE)` 创建.
3. `volk_32fc_s32f_power_spectrum_32f(fftBuf, fftOutBuf, fftSize, fftSize)`
   直接把复数 FFT 输出算成 dB 功率谱写入 GUI 提供的 buffer (`iq_frontend.cpp:262`).

**采集/释放回调解耦**: IQ 前端不直接认识 GUI, 它通过两个函数指针拿到写入目标
(`iq_frontend.h:23`, `iq_frontend.h:92-94`). 实际接线在
`core/src/gui/main_window.cpp:91`:
```cpp
sigpath::iqFrontEnd.init(&dummyStream, 8e6, true, 1, false, 1024, 20.0,
    IQFrontEnd::FFTWindow::NUTTALL, acquireFFTBuffer, releaseFFTBuffer, this);
```
`acquireFFTBuffer` → `gui::waterfall.getFFTBuffer()` (`main_window.cpp:230-232`),
`releaseFFTBuffer` → `gui::waterfall.pushFFT()` (`main_window.cpp:234-236`).
**这是 DSP 线程与 GUI 线程之间的零拷贝交接**: DSP 直接把 dB 值写进水瀑布的环形 buffer,
写完就 release, GUI 在 `pushFFT` 里做缩放/平滑/纹理上传.

### 3.4 GUI 侧: 环形历史 / 抽点缩放 / 指数平滑 / Peak Hold

`core/src/gui/widgets/waterfall.h:11` `#define WATERFALL_RESOLUTION 1000000` —
调色板被预插值成 100 万级查找表.

**环形历史 buffer**: `waterfall.h:286` `float* rawFFTs` 是一个
`waterfallHeight × rawFFTSize` 的二维环形数组. `getFFTBuffer()` (`waterfall.cpp:876-887`)
每次 `currentFFTLine--` 并对 `waterfallHeight` 取模, 返回下一行的写入指针.
这就是"每帧 FFT 作为瀑布新一行"的环形缓存.

**缩放抽点 (doZoom)**: `waterfall.cpp:65-90`. 当 raw FFT bin 数远大于屏幕像素宽时,
按 `factor = width/outSize` 取每段**最大值** (`maxVal`, `waterfall.cpp:81-87`) 而非平均,
这样窄脉冲信号在缩小时不会被平均抹平 — 这是频谱显示的关键技巧.

**指数平滑 (平均)**: `waterfall.cpp:914-920`:
```cpp
volk_32f_s32f_multiply_32f(latestFFT, latestFFT, fftSmoothingAlpha, dataWidth);
volk_32f_s32f_multiply_32f(smoothingBuf, smoothingBuf, fftSmoothingBeta, dataWidth);
volk_32f_x2_add_32f(smoothingBuf, latestFFT, smoothingBuf, dataWidth);
```
即 `smooth = alpha*new + beta*old`, 是一阶 IIR. `setFFTSmoothingSpeed` (`waterfall.cpp:1190-1194`)
里 `fftSmoothingBeta = 1.0f - speed`, alpha 就是用户给的 speed.
**这是 SDR++ 替代 Welch 平均的方式**: 流式指数平滑, 不是块内多帧平均.

**Peak Hold (峰值保持)**: `waterfall.cpp:934-939`:
```cpp
latestFFTHold[i] = std::max<float>(latestFFT[i], latestFFTHold[i] - fftHoldSpeed);
```
每帧取当前帧与历史保持值的较大者, 同时让旧峰值以 `fftHoldSpeed` 速率衰减,
形成"山峰慢慢回落"的效果.

**SNR 平滑**: `waterfall.cpp:922-932`, 选中 VFO 内 `calculateVFOSignalInfo`
(`waterfall.cpp:558-598`) 算 band 内 max 和 band 外左右侧平均, `snr = max - avg`,
再做一次 IIR.

### 3.5 颜色映射 (colormap)

默认 13 色段 `DEFAULT_COLOR_MAP` (`waterfall.cpp:11-25`):
深蓝→蓝→浅蓝→白→黄→橙→红→深红 (经典"火焰"色). 启动时实际加载 "Turbo" colormap
(`main_window.cpp:164`).

`updatePallette` (`waterfall.cpp:944-958`) 把 N 个控制点**线性插值**到 1M 级查找表
`waterfallPallet[WATERFALL_RESOLUTION]`, 打包成 RGBA8888 (`waterfall.cpp:955`).
渲染时 `pixel = clamp(dB, min, max)` 归一化到 `[0,1]`, 直接查 LUT:
`waterfallFb[pixel] = waterfallPallet[(int)(pixel*(WATERFALL_RESOLUTION-1))]` —
`waterfall.cpp:619`, `waterfall.cpp:901-905`.

**瀑布纹理上传**: `updateWaterfallTexture` (`waterfall.cpp:704-711`) 用 `glTexImage2D`
把 `waterfallFb` 上传为 GL 纹理, `GL_LINEAR` 缩放. 新一行到来时 `pushFFT` 里
`memmove(&waterfallFb[dataWidth], waterfallFb, dataWidth*(waterfallHeight-1)*4)`
把整屏往下滚一行, 再写顶行 (`waterfall.cpp:898`).

---

## 4. 设备抽象层: SourceModule 接口 / 采样率 / 增益 / 频率

### 4.1 没有基类, 只有回调表

SDR++ 的"设备抽象"不是 C++ 虚基类, 而是 `SourceManager::SourceHandler` 这组函数指针
(`core/src/signal_path/source.h:13-22`). 任何设备插件只要:
1. 写一个 `dsp::stream<dsp::complex_t> stream;` 成员作为 IQ 输出;
2. 填好 6 个回调 + ctx;
3. `sourceManager.registerSource(name, &handler)`;
就接入了. 见 `main.cpp:548` (`dsp::stream<dsp::complex_t> stream;`) 和 `main.cpp:66-95`.

### 4.2 频率控制

`tuneHandler` 签名 `void (*)(double freq, void* ctx)` (`source.h:20`).
`SourceManager::tune` (`source.cpp:83-91`) 还会按 `tuneMode` 选择是调射频频率还是 panadapter IF:
```cpp
selectedHandler->tuneHandler(abs((tuneMode==NORMAL ? freq+tuneOffset : ifFreq)), ctx);
onRetune.emit(freq + tuneOffset);
```
RTL-SDR 实现 `RTLSDRSourceModule::tune` (`main.cpp:344-359`): 循环最多 10 次
`rtlsdr_set_center_freq` 再 `rtlsdr_get_center_freq` 读回校验, 失败重试.

### 4.3 采样率控制

RTL-SDR 不把采样率放进 `SourceHandler`, 而是在 `menuSelected` 回调里调
`core::setInputSampleRate(sampleRate)` (`main.cpp:277`, `main.cpp:379`).
`core::setInputSampleRate` (`core.cpp:41-57`) 做三件事:
1. `sigpath::iqFrontEnd.setSampleRate(samplerate)` 重新配置抽取/DC阻塞/VFO/FFT 路径;
2. 取 effectiveSr 后 `gui::waterfall.setBandwidth/setViewBandwidth` 重置横轴;
3. 日志.

`IQFrontEnd::setSampleRate` (`iq_frontend.cpp:76-99`) 会先 `tempStop()` DC 块和所有 VFO,
改 `effectiveSr`, 重算 DC blocker 速率 (`genDCBlockRate = 50/sr`, `iq_frontend.cpp:55-57`),
调每个 VFO 的 `setInSamplerate(effectiveSr)`, 再 `updateFFTPath()` 重建 FFT, 最后 `tempStart()`.

### 4.4 增益 / AGC / 其他硬件控制

RTL-SDR 的增益**不走 SourceHandler 回调**, 而是在 `menuHandler` 里直接调 `rtlsdr_set_tuner_gain`
(`main.cpp:448`, `main.cpp:461`), 并把选择写进 per-device 配置 (`main.cpp:452`).
开始采集时统一应用硬件参数 (`main.cpp:307-322`):
```cpp
rtlsdr_set_sample_rate(dev, sampleRate);
rtlsdr_set_center_freq(dev, freq);
rtlsdr_set_freq_correction(dev, ppm);
rtlsdr_set_direct_sampling(dev, directSamplingMode);
rtlsdr_set_bias_tee(dev, biasT);
rtlsdr_set_agc_mode(dev, rtlAgc);            // RTL 芯片 AGC
rtlsdr_set_tuner_gain_mode(dev, tunerAgc?0:1); // 0=AGC, 1=手动
rtlsdr_set_tuner_gain(dev, gainList[gainId]);
rtlsdr_set_offset_tuning(dev, offsetTuning);
```
增益列表来自 `rtlsdr_get_tuner_gains` (`main.cpp:194-195`), 排序后存 `gainList`,
UI 用 SliderInt 选 index, 显示成 `%.1f dB` (`main.cpp:541-543`).

### 4.5 数据流: 设备线程 → stream.swap

`start()` (`main.cpp:286-330`) 打开设备后起一个 `workerThread` 跑
`rtlsdr_read_async` (`main.cpp:526-529`). USB 回调 `asyncHandler` (`main.cpp:531-539`):
```cpp
for (i...) {
    stream.writeBuf[i].re = ((float)buf[i*2]   - 127.4) / 128.0f;  // uint8 偏移二进制转 float [-1,1]
    stream.writeBuf[i].im = ((float)buf[i*2+1] - 127.4) / 128.0f;
}
if (!stream.swap(sampCount)) return;
```
**这就是设备侧 producer 的标准范式**: 在自己的线程里把硬件采样转成 `complex_t`,
写进 `writeBuf`, 调 `swap()` 交给下游. stop 时 `stream.stopWriter()` 解除 `swap` 阻塞
(`main.cpp:336`).

---

## 5. 可迁移到 MBDSDR 的具体点

对照以下四个文件:
- `desktop/spectrum_widget.py` (PyQt 频谱+瀑布控件, 677 行)
- `desktop/control_panel.py` (控制面板, 361 行)
- `mbdsdr_ai/sdr_backend.py` (SDR 后端, 1374 行)
- `mbdsdr_ai/signal_spectrum.py` (离线 Welch PSD 分析, 114 行)

### 5.1 信号流: 把"每块一线程 + 双缓冲 stream"搬进 sdr_backend.py

现状: `sdr_backend.py` 用 `threading.Event` + 录制线程 (`sdr_backend.py:88-89`),
但 IQ 从设备线程到 GUI 之间没有显式块化双缓冲, 容易在 GUI 慢时 backpressure 丢数据或锁竞争.

迁移建议:
- 参考 `dsp/stream.h:43-92` 的 `swap/read/flush` 三件套, 在 Python 里用 `queue.Queue(maxsize=N)`
  或双 `np.ndarray` + `threading.Condition` 实现一个 `ComplexStream`:
  producer 写满一帧 `swap()`, consumer `read()` 阻塞取. 不要用无界 queue, 否则内存爆.
- 参考 `block.h:67-69` 的 `while run()>=0` 范式, 把 FFT 线程、VFO 解调线程、录制线程
  各自做成 `Thread(target=lambda: self._run_loop())`, 退出条件统一用 `stop_event`.
- 参考 `block.h:46-62` 的 `tempStopDepth`, 在改采样率/增益时不要直接杀线程,
  而是 `tempStop()` → 重配 → `tempStart()`, 避免 Python 线程 join 卡顿 UI.

### 5.2 设备抽象: 用"回调表"替代硬编码 rtl-sdr 分支

现状: `sdr_backend.py:39-46` 已经有 `SDRDevice` dataclass (`device_type/frequency_range/
sample_rate_range/max_gain`), `sdr_backend.py:104-182` 有 `set_frequency/set_sample_rate/set_gain`
带范围检查和异常回滚. 这比 SDR++ 的 `SourceHandler` 还更面向对象 (有状态 + 回滚).

可借鉴点:
- SDR++ 的 `SourceHandler` 把"菜单 UI / 选中 / 启停 / 调谐"做成 6 个独立回调
  (`source.h:13-22`), MBDSDR 可以给 `SDRDevice` 基类加这几个虚方法:
  `on_selected()/on_start()/on_stop()/on_tune(freq)/render_menu(panel)`.
  这样加 HackRF/Pluto/虚拟源时, `control_panel.py` 不用 if-else 堆 device_type.
- SDR++ 的 `selectSource` (`source.cpp:57`) 一行 `iqFrontEnd.setInput(handler->stream)` 完成换源.
  MBDSDR 对应做法: 后端持有 `current_stream`, 切换设备时把 FFT 消费者绑定到新 stream,
  不要重建整条 DSP 链.
- SDR++ 把"硬件采样率"和"DSP 有效采样率"分开 (`iq_frontend.cpp:27` `effectiveSr =
  sampleRate/decimRatio`), MBDSDR 现在 `sdr_backend.py:57` 只有一个 `sample_rate_hz`,
  建议加 `decimation` 字段, 让频谱显示的 bandwidth 用 effectiveSr, 硬件控制用原始 sr.

### 5.3 频谱渲染: 把 signal_spectrum.py 的离线 Welch 流式化

现状: `signal_spectrum.py:37-49` 是**一次性离线**分析: `hop = fft_size//2`, Hanning 窗
(`signal_spectrum.py:38`), 多帧累加 `acc += p` 做 Welch 平均 (`signal_spectrum.py:47`),
同时 `hold = np.maximum(hold, p)` 做 Peak Hold (`signal_spectrum.py:48`). 这是很好的"AI 找台"算法.

对照 SDR++:
- SDR++ 不在 DSP 侧做帧间平均, 而是在 GUI 侧做 IIR 指数平滑
  (`waterfall.cpp:914-920`). **MBDSDR 现在 `signal_spectrum.py` 是块平均 (Welch),
  `spectrum_widget.py:56/211` 是单帧 Hanning 无平滑**. 建议在 `spectrum_widget.py` 增加
  一个 `smoothing_buf` 数组, 每帧 `smooth = alpha*new + (1-alpha)*smooth`,
  alpha 默认 0.2~0.5, 暴露成控制条. 这比每 200ms 重算一次 Welch 更实时.
- SDR++ 的 Peak Hold 带衰减 (`waterfall.cpp:937` `hold[i] = max(new, hold[i]-speed)`),
  而 `signal_spectrum.py:48` 的 `hold = maximum(hold, p)` 永不衰减, 长时运行会"糊掉".
  建议在 widget 侧加带衰减的 hold, 离线分析侧保留无衰减 hold 用于报告.
- SDR++ 的窗口是 Nuttall (`nuttall.h:6` 四系数), MBDSDR 全用 Hanning (`signal_spectrum.py:38`,
  `spectrum_widget.py:56`). Hanning 主瓣宽、旁瓣 -31dB; Nuttall 旁瓣 -93dB,
  对"找强信号旁边的弱信号"更友好. 可在 `spectrum_widget.py` 加窗口下拉
  (hann/blackman/nuttall), 系数直接抄 `nuttall.h:6`.
- SDR++ 的缩放抽点用 **max 抽取** (`waterfall.cpp:81-87`) 而非平均, 避免窄脉冲被抹平.
  `spectrum_widget.py` 现在若做了 bin→pixel 平均, 应改成 max 抽取, 否则弱脉冲会丢.

### 5.4 瀑布: 环形 buffer + GL/Qt 纹理上传

现状: `spectrum_widget.py:38-39` 用 `List[np.ndarray]` 存瀑布, `max_waterfall_lines=200`,
超限 `pop(0)` (`spectrum_widget.py:68-69`). 这是 O(n) 搬移, 线数一多就卡.

迁移建议:
- 参考 `waterfall.cpp:880-884` 的环形索引: 预分配 `np.ndarray((H, W), np.uint8)`,
  用 `current_line = (current_line+1) % H` 写新行, 不做 memmove.
- SDR++ 颜色映射是 1M 级 LUT 预插值 (`waterfall.cpp:944-958`), 渲染时 O(W) 查表.
  MBDSDR `spectrum_widget.py:113 value_to_color` 现在大概是逐像素插值, 建议启动时预生成
  `LUT[256] = QColor(...)`, 渲染时 `img = LUT[clamp(db)]` 一次向量化查表, 用 `QImage` 包出来.
- SDR++ 把瀑布整屏 `memmove` 滚一行再写顶行 (`waterfall.cpp:898`), 这在 Python 里
  用 `np.roll` 或环形索引都行, 但要避免每帧 `new np.ndarray` (GC 压力).

### 5.5 配置持久化

SDR++ 每个插件自己维护一个 json 配置 (`main.cpp:587-594` `_INIT_` 里 `config.setPath` +
`load(def)` + `enableAutoSave()`), 按 per-device 存 sampleRate/gain/ppm/biasT 等
(`main.cpp:200-210`). MBDSDR 可直接借鉴这个 "per-device 子树" 模式,
在 `sdr_backend.py` 配置里建 `devices[serial] = {sample_rate, gain, ppm...}`,
切换设备时自动恢复上次参数.

### 5.6 立刻能做的 5 件小事 (按优先级)

1. `spectrum_widget.py` 加 IIR 平滑 (抄 `waterfall.cpp:914-920`), alpha 可滑.
2. `spectrum_widget.py` 瀑布改环形 buffer, 去掉 `pop(0)` (`spectrum_widget.py:69`).
3. 缩 bin 改 max 抽取 (抄 `waterfall.cpp:81-87`), 别平均.
4. `sdr_backend.py` 加 `effective_sr = sample_rate / decimation`, 频谱横轴用它.
5. 把 `SDRDevice` 抽象成基类 + `on_start/on_stop/on_tune/render_menu` 虚方法,
   参考 `SourceHandler` (`source.h:13-22`), 替换 `control_panel.py` 里的 device_type if-else.

---

## 附录: 本次实际阅读的文件清单 (≥8)

1. `core/src/module.h` (插件元信息 / Instance 接口)
2. `core/src/module.cpp` (dlopen 加载 / 工厂)
3. `core/src/module_com.h` (跨插件 RPC)
4. `core/src/core.h` / `core/src/core.cpp` (全局单例 / sdrpp_main / setInputSampleRate)
5. `core/src/dsp/block.h` (block 基类 / 线程模型)
6. `core/src/dsp/processor.h` (Processor<I,O> / run 宏)
7. `core/src/dsp/stream.h` (双缓冲流)
8. `core/src/dsp/source.h` / `core/src/dsp/sink.h` (Source/Sink 基类)
9. `core/src/dsp/chain.h` (可启用/旁路的处理链)
10. `core/src/dsp/routing/splitter.h` (fan-out 拷贝)
11. `core/src/dsp/multirate/rational_resampler.h` (多速率重采样实例)
12. `core/src/dsp/window/nuttall.h` (窗函数系数)
13. `core/src/signal_path/source.h` / `source.cpp` (SourceManager + SourceHandler)
14. `core/src/signal_path/iq_frontend.h` / `iq_frontend.cpp` (FFT 路径)
15. `core/src/signal_path/signal_path.h` (全局单例)
16. `core/src/gui/widgets/waterfall.h` / `waterfall.cpp` (频谱+瀑布渲染)
17. `core/src/gui/main_window.cpp` (FFT buffer 回调接线 / 插件加载顺序)
18. `source_modules/rtl_sdr_source/src/main.cpp` (完整源插件范例)
