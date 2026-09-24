# gr-osmosdr 通用 SDR 源抽象（MBDSDR 移植笔记）

> 源码来源：`repos/gr-osmosdr`（GNU Radio OOT 模块）
> 移植产物：`mbdsdr_ai/osmosdr_source.py`
> 本文所有结论均来自直接阅读 `.cc/.h` 源码，标注 `file:line`。

## 1. gr-osmosdr 是什么

gr-osmosdr 是 GNU Radio 的"通用 SDR 硬件源块"。它把数十种 SDR 前端
（RTL-SDR / HackRF / bladeRF / USRP(UHD) / AirSpy / SoapySDR…）抽象成
**同一个 GNU Radio 块** `osmosdr::source`，上层流图不关心底下插的是谁。

公共 API 定义在 `include/osmosdr/source.h:38`：

```cpp
class OSMOSDR_API source : virtual public gr::hier_block2 {
  static sptr make(const std::string &args = "");   // source.h:53
  virtual double set_sample_rate(double rate);       // source.h:82
  virtual double set_center_freq(double freq, ...); // source.h:105
  virtual gain_range_t get_gain_range(...);         // source.h:142/150
  virtual double set_gain(double gain, ...);        // source.h:177/186
  ...
};
```

## 2. 设备字符串：格式与路由

### 2.1 解析过程（`lib/arg_helpers.h`）

1. 整个 args 串先按**空格**切成多组设备：`args_to_vector()`
   —— `arg_helpers.h:48-60`，分隔符 `("\\", " ", "'")`。
2. 每组再按**逗号**切成 `key=value`：`params_to_vector()` —— `arg_helpers.h:62-74`。
3. 每个 token 按**第一个 `=`** 切成键值：`param_to_pair()` —— `arg_helpers.h:76-93`。
4. 值外层的单引号剥掉 —— `arg_helpers.h:104-105`。

于是：

| 字符串 | 解析结果 |
|---|---|
| `rtl=0` | `{rtl: "0"}` |
| `hackrf=aa11bb22` | `{hackrf: "aa11bb22"}` |
| `bladerf=0` | `{bladerf: "0"}` |
| `uhd,type=b200,serial=1234` | `{uhd:"", type:"b200", serial:"1234"}` |
| `soapy=0,driver=rtlsdr` | `{soapy:"0", driver:"rtlsdr"}` |
| `rtl=0,label='My Stick'` | `{rtl:"0", label:"My Stick"}` |

### 2.2 后端路由（`lib/source_impl.cc:271-397`）

构造函数对每组 `dict` 做一串 `dict.count(...)` 判断，命中哪个 key 就 `make_*_source_c()`
出对应后端：

```cpp
if (dict.count("rtl"))      make_rtl_source_c(arg);       // source_impl.cc:297
if (dict.count("uhd"))      make_uhd_source_c(arg);       // source_impl.cc:311
if (dict.count("hackrf"))   make_hackrf_source_c(arg);    // source_impl.cc:332
if (dict.count("bladerf"))  make_bladerf_source_c(arg);   // source_impl.cc:339
if (dict.count("soapy"))    make_soapy_source_c(arg);     // source_impl.cc:372
```

内置后端顺序（`source_impl.cc:128-175`）：
`file, fcd, rtl, rtl_tcp, uhd, miri, sdrplay, hackrf, bladerf, rfspace,
airspy, airspyhf, soapy, redpitaya, freesrp, xtrx`。

**未指定设备时**（`source_impl.cc:202-268`）：遍历每个后端的 `get_devices()`
自动枚举，取第一个；一个都找不到就抛
`"No supported devices found (check the connection and/or udev rules)."`（`source_impl.cc:268`）。
这正是 MBDSDR `OsmoSDRSource.connect()` 复刻的行为。

## 3. 范围表示：range_t / meta_range_t（`lib/ranges.cc`）

- `range_t(start, stop, step)`：`stop < start` 直接抛异常（`ranges.cc:49-51`）。
- `meta_range_t`：`std::vector<range_t>`，支持不连续区间。
- `clip(value, clip_step)`（`ranges.cc:136-154`）：落在区间内按 step 量化
  （`boost::math::round`，**.5 远离零取整**），落在间隙里夹到最近端点。

MBDSDR 对应 `GainRange` / `MetaRange`，已复刻 clip 与取整语义。

## 4. 各后端关键常量（无设备也成立的标称值）

### 4.1 RTL-SDR（`lib/rtl/rtl_source_c.cc`）

- 设备串：`"rtl=<index|serial>"`（`rtl_source_c.cc:385`）。
- 默认采样率 **1 024 000 Hz**（`rtl_source_c.cc:198`）。
- 已知可用采样率档（`rtl_source_c.cc:421-429`）：
  250k / 1M / 1.024M / 1.8M / 1.92M / 2M / 2.048M / 2.4M / 2.56M。
- 频率范围随调谐器（`rtl_source_c.cc:473-489`）：

  | 调谐器 | 范围 |
  |---|---|
  | E4000 | 52 MHz – 2.2 GHz（1100–1250MHz 有间隙）|
  | FC0012 | 22 MHz – 948 MHz |
  | FC0013 | 22 MHz – 1.1 GHz |
  | FC2580 | 146–308 MHz + 438–924 MHz |
  | R820T / R828D | 24 MHz – 1766 MHz |

- 增益级：总有 `LNA`；仅 E4000 额外有 `IF`（`rtl_source_c.cc:531-536`）。
  E4000 的 IF 增益 3–56 dB step1（`rtl_source_c.cc:565`）。

### 4.2 HackRF（`lib/hackrf/hackrf_source_c.cc` + `hackrf_common.cc`）

- 设备串：`"hackrf=<serial 后6位>"` 或 `"hackrf"`（`hackrf_common.cc:216,220`）。
- 增益级：`RF` / `IF` / `BB`（`hackrf_source_c.cc:307`）。

  | 级 | 范围 | 来源 |
  |---|---|---|
  | RF（前后端 AMP） | 0–14 step14（即 0 或 14） | hackrf_source_c.cc:318 |
  | IF（LNA） | 0–40 step8 | hackrf_source_c.cc:322 |
  | BB（VGA） | 0–62 step2 | hackrf_source_c.cc:326 |

- 默认增益：RF=0（关 AMP 保护前端）、IF=16、BB=20（`hackrf_source_c.cc:104/106/108`）。
- 采样率档（`hackrf_common.cc:250-254`）：8M / 10M / 12.5M / 16M / 20M，
  默认取最小 8M（`hackrf_source_c.cc:101`）。
- 频率范围：`sample_rate/2 .. 7250e6 - sample_rate/2`（`hackrf_common.cc:283`）。

### 4.3 bladeRF（`lib/bladerf/bladerf_common.cc`）

- 设备串：`"bladerf=<instance>,label='Nuand bladeRF SN ...'"`（`bladerf_common.cc:428`）。
- 增益级：`LNA` / `VGA1` / `VGA2`（`bladerf_common.cc:756`）。

  | 级 | 范围 | 来源 |
  |---|---|---|
  | LNA | 0–6 step3 | bladerf_common.cc:792 |
  | VGA1 | 5–30 step1 | bladerf_common.cc:794 |
  | VGA2 | 0–30 step3 | bladerf_common.cc:796 |

- 采样率三段（`bladerf_common.cc:576-578`）：160k–200k step40k、
  300k–900k step100k、1M–40M step1M。
- 频率范围：280 MHz – 3.8 GHz（bladeRF1，`bladerf_common.cc:638-639`）。

### 4.4 USRP / UHD（`lib/uhd/uhd_source_c.cc`）

- 设备串：`"uhd,<uhd device addr>"`，如 `uhd,type=b200,serial=...`（`uhd_source_c.cc:140`）。
- **所有范围查询都透传给 UHD runtime**（`uhd_source_c.cc:189-216`），
  没有写死的常量——MBDSDR 里对它标注"需运行时探测"，不编造数值。

### 4.5 SoapySDR（`lib/soapy/soapy_source_c.cc`）

- 设备串：枚举出来是 `"soapy=<index>,<kwargs...>"`（`soapy_source_c.cc:118`）。
- 范围同样透传给 SoapySDR driver（`soapy_source_c.cc:129-162`），运行时探测。

## 5. MBDSDR 移植对照

| gr-osmosdr (C++) | MBDSDR (Python) |
|---|---|
| `osmosdr::range_t` | `osmosdr_source.GainRange` |
| `osmosdr::meta_range_t` | `osmosdr_source.MetaRange` |
| `args_to_vector`/`params_to_dict` | `args_to_vector`/`params_to_dict` |
| `source_impl` 路由 | `OsmoSDRSource.connect()` / `_driver_of()` |
| 各后端 `get_devices()` | `DeviceEnumerator.enumerate()` |
| 各后端静态范围表 | `BACKEND_STATIC` + 模块级常量 |

注册到 ToolRegistry 的三个工具：
- `osmosdr_list_devices`：枚举设备（无设备返回 `[]`）。
- `osmosdr_create_source`：按设备字符串建源对象并路由。
- `osmosdr_get_gain_ranges`：查某后端各增益级范围。

## 6. 红线遵守

- **无设备不造假**：`read_samples()` 在未绑定真实硬件时抛 `RuntimeError`，
  绝不返回伪造 IQ。`DeviceEnumerator.enumerate()` 在缺库时返回 `[]`。
- **设备字符串格式与 gr-osmosdr 一致**：`rtl=/hackrf=/bladerf=/uhd,/soapy=`。
- **常量可溯源**：每个硬件数值在代码注释里都标了 `file:line`。
