# SoapySDR 源码学习笔记

> 仓库: https://github.com/pothosware/SoapySDR （及驱动仓 pothosware/SoapyRTLSDR）
> 本地路径: `repos/SoapySDR`、`repos/SoapyRTLSDR`（已 gitignore，仅本地学习）
> 学习目标: 搞清楚 SoapySDR 的"设备总线"机制——枚举/打开/收发流/范围查询的 C++ API 与 Python SWIG 绑定，
> 并据此在 `mbdsdr_ai/sdr_backend.py` 里落地 `SoapySDRBackend`（三层降级：Python 绑定 → SoapySDRUtil CLI → 空列表）。
> 所有 `file:line` 引用相对 `repos/SoapySDR/` 或 `repos/SoapyRTLSDR/`。

---

## 0. 一句话定位

SoapySDR 是 SDR 生态的 **驱动总线/中间层**：上层只面对一套 C++ API（`SoapySDR::Device`），
下层由各个 `Soapy*` 支持模块（SoapyRTLSDR、SoapyHackRF、SoapyUHD、SoapyBladeRF...）
用 `dlopen` 注册自己的 find/make 函数。**应用代码一份，通吃几十种硬件**——这正是 MBDSDR
想要的"通用后端"。

```
应用 (Python/C++)
   │  SoapySDR::Device.enumerate() / Device(args)
   ▼
libSoapySDR.so  ←─ 注册表 Registry（Factory.cpp）
   │  dlopen 加载
   ├── libSoapySDRSupport_RTLSDR.so  (pothosware/SoapyRTLSDR)
   ├── libSoapySDRSupport_HackRF.so
   └── ...
```

---

## 1. 枚举（enumerate）：拿身份，不拿范围

### 1.1 入口与"驱动键自动注入"

`lib/Factory.cpp:41` — `SoapySDR::KwargsList SoapySDR::Device::enumerate(const Kwargs &args)`：

- `:43` 先 `automaticLoadModules()` 一次性把所有 `SoapySDR` 模块 `.so` 加载进来。
- `:69-91` 对每个已注册的 find 函数 `std::async` 并发枚举（还带 1 秒结果缓存，`:57` `CACHE_TIMEOUT`）。
- **`:101` 关键**：每个驱动返回的 handle 被强行补上 `handle["driver"] = it.first;`。
  也就是说枚举结果里必然有 `driver` 键——这是上层分发到具体后端的依据。
- `:117` 还有一个 `enumerate(const std::string &args)` 重载，内部 `KwargsFromString` 转 dict，
  等价于 Python 端 `enumerate("driver=rtlsdr")`。

枚举返回的 `KwargsList` 就是 `vector<map<string,string>>`。对 RTL-SDR，这些键由
`SoapyRTLSDR/Registration.cpp:69-73` 填充：

```cpp
devInfo["label"]      = std::string(rtlsdr_get_device_name(i)) + " :: " + serial;  // :69
devInfo["product"]    = product;      // :70
devInfo["serial"]     = serial;       // :71
devInfo["manufacturer"] = manufact;   // :72
devInfo["tuner"]      = get_tuner(serial, i);  // :73
```

**结论**：枚举阶段只能拿到身份信息（driver/label/serial/manufacturer/product/tuner），
**拿不到频率/增益/采样率范围**——那些要 open 之后查 `getFrequencyRange` 等。
MBDSDR 的 `SoapySDRBackend._enumerate_via_python()` 因此做了一次 best-effort probe：
临时 `Device(info)` 打开 → 查范围 → `del` 关闭。

### 1.2 打开设备

`lib/Factory.cpp:133` — `Device* SoapySDR::Device::make(const Kwargs &inputArgs)`：

- `:138` 先查"已打开设备表"，避免重复 open 同一台。
- `:145` 若 args 不带全，会重新 `enumerate(inputArgs)` 补全参数。
- Python 端等价物就是 `SoapySDR.Device(args)`（构造函数内部调 make），
  args 可以是 dict 或 `"key=val,key=val"` 字符串——我们两种都吃，统一存成字符串。

---

## 2. 控制 API：方向 / 通道 / 频率 / 增益 / 采样率

### 2.1 方向常量

`include/SoapySDR/Constants.h:22` — `#define SOAPY_SDR_RX 1`（`:17` `SOAPY_SDR_TX 0`）。
几乎所有控制 API 第一个参数都是 direction，第二个是 channel。

### 2.2 调谐与频率范围

- `include/SoapySDR/Device.hpp:801` — `setFrequency(direction, channel, frequency, args=Kwargs())`。
- `:855` — `RangeList getFrequencyRange(direction, channel)`。返回是**区间列表**（多段）。
- `:789` 附近 — `getFrequency(direction, channel)` 回读当前频率。

RTL-SDR 的具体频率区间由 tuner 型号决定，见 `SoapyRTLSDR/Settings.cpp:408-427`：

| tuner | 频率范围 |
|---|---|
| E4000 | 52 MHz – 2.2 GHz (`:411`) |
| FC0012 | 22 MHz – 1.1 GHz (`:413`) |
| FC0013 | 22 MHz – 948.6 MHz (`:415`) |
| R828D + RTLSDRBlog V4 | 0 – 1.764 GHz（内置上变频，`:418`） |
| 其他(R820T/T2) | 24 MHz – 1.764 GHz (`:420`) |

### 2.3 采样率

- `Device.hpp:884` — `setSampleRate(direction, channel, rate)`。
- `:909` — `RangeList getSampleRateRange(...)`。
- `:892` — `getSampleRate(...)` 回读。

RTL-SDR 的采样率是**两段不连续区间**，`SoapyRTLSDR/Settings.cpp:489-497`：
`[225001, 300000]` 和 `[900001, 3200000]`——300k~900k 是死区，和我们 librtlsdr 后端
的 `_RTL_LOW_MAX/_RTL_HIGH_MIN` 钳位逻辑完全对上。

### 2.4 增益

- `Device.hpp:725` — `setGain(direction, channel, value)`（总增益）。
- `:734` — `setGain(direction, channel, name, value)`（分 LNA/VGA 级）。
- `:759` — `getGainRange(direction, channel)` 返回**单个 Range**（不是列表！）。
- `:708` — `setGainMode(direction, channel, automatic)` 开 AGC。

### 2.5 Range 对象

`include/SoapySDR/Types.hpp:64` — `class Range`，暴露 `minimum()` / `maximum()` / `step()`
方法（`:78/:85/:88`）。`:91` 注释明确：整个 RangeList 的最小 = `rl.front().minimum()`，
最大 = `rl.back().maximum()`。Python SWIG 后仍是方法调用 `r.minimum()`。

> **坑**：`getGainRange` 返回单个 Range，而 `getFrequencyRange/getSampleRateRange` 返回
> RangeList。MBDSDR 的 `_range_tuple()` 同时兼容这两种（`hasattr(rl,"minimum")` 判断单对象）。

---

## 3. 收发流（stream）：真正吐 IQ 的地方

### 3.1 setup / activate / read / deactivate / close

`include/SoapySDR/Device.hpp`：

- `:267` — `setupStream(direction, format, channels=[], args=Kwargs())`，返回不透明 `Stream*`。
- `:339` 附近 — `activateStream(stream, flags=0, timeNs=0, numElems=0)`，**必须先 activate 才能读**。
- `:352` — `readStream(stream, buffs, numElems, flags&, timeNs&, timeoutUs=100000)`，
  返回实际读到的元素数（负数=错误码，如 OVERFLOW/TIMEOUT）。
- 对应 `deactivateStream` / `closeStream`。

Python 真实用法见 `repos/SoapySDR/swig/python/apps/MeasureDelay.py`：

- `:81` — `rx_stream = sdr.setupStream(SOAPY_SDR_RX, SOAPY_SDR_CF32, [rx_chan])`
- `:88` — `sdr.activateStream(rx_stream, ...)`
- `:108` — `status = sdr.readStream(rx_stream, [rx_buff], len(rx_buff), timeoutUs=timeout_us)`
- `:113` — 有效样本数取 `status.ret`，`rx_buff[:status.ret]` 切片；`status.flags`/`status.timeNs` 是时间戳信息。

MBDSDR 的 `SoapySDRBackend.read_samples()` 照此：预分配 `np.zeros(n, complex64)` 数组传进
`[buff]`，按 `status.ret` 切片返回；`ret<=0` 记 error 并返回 None。

---

## 4. MBDSDR 落地：三层降级，绝不假成功

环境里 SoapySDR Python 绑定/CLI/共享库都没装，所以后端必须优雅降级：

| 优先级 | 接入方式 | 条件 | 行为 |
|---|---|---|---|
| 1 | `import SoapySDR` 原生 API | 装了 swig 绑定 | 全功能枚举/打开/收发 |
| 2 | `SoapySDRUtil --find ""` 子进程 | 有 CLI 无 Python | 解析 `:key=value` 文本做身份枚举 |
| 3 | 都没有 | — | `list_devices()` 返回 `[]` + log warning |

红线在代码里的体现：

- `connect()` 拿不到 `SoapySDR` 模块时，直接 `self.status.error = ...; return False`，
  **绝不 new 一个 mock 报 connected=True**。
- `read_samples()` 未连接/流未建时返回 None，硬件错误码写进 `status.error`。
- 桌面端 `main_window.py._connect_dialog()` 选了真实设备但 `backend.connect()` 返回 False 时，
  `QMessageBox.critical` 弹错并明确提示"未切换到模拟模式"——不静默回落。

去重合并见 `enumerate_all_sdr_devices()`：SoapySDR 条目信息更全（含真实范围），
同 `serial` 时优先保留它，原生 pyrtlsdr 条目只补 SoapySDR 没认出来的设备。

---

## 5. 后续可做（P1+，本次未做）

- 真插上 RTL-SDR 后实测 probe 范围、readStream 吞吐与溢出恢复。
- TX 流（`SOAPY_SDR_TX`）与 `activateStream` 突发模式（EndBurst）支持。
- 把 `_active_sdr_backend` 的 IQ 真正接进现有频谱/解调管线（目前桌面端只完成了
  真实设备枚举与 connect/disconnect 生命周期，IQ 主环路仍是 ai-sdr Mini WebSocket 路径）。
- `writeSetting(key,value)` 透传 driver 私有旋钮（如 RTL-SDR 的 bias-tee、AGC 阈值）。
