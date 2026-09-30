# UHD（USRP Hardware Driver，Ettus）核心接口学习笔记

> 干净室研究：只读 `repos/uhd`（UHD **v4.11.0.0**，`git describe` = `v4.11.0.0`），不写代码、不逐字复制。
> 所有 `file:line` 相对 `repos/uhd/`。MBDSDR 侧引用相对仓库根。
> 标注约定：
> - **【确认】**＝直接读到源码声明/实现，可逐行核对；
> - **【推断】**＝基于源码结构与 MBDSDR 现状的合理推断，未实测硬件；
> - **【未验证】**＝上游手册/常识说法，本仓库未直接核对。

---

## 0. 一句话定位与两层结构

UHD 是 Ettus/NI USRP 的**用户态设备驱动 + 框架**：上层 C++ API 一套，下层各型号
（B200/B210/X300/X310/N200/N210/B3xx/N3xx/E3xx…）用插件方式注册自己的 find/make。
和 SoapySDR 不同，UHD **本身就是设备厂商驱动**，不是中间总线——它只通吃 USRP 一家硬件。

关键认知：**真正的调谐/增益 API 不在基类 `uhd::device` 上，而在它的子类
`uhd::usrp::multi_usrp` 上。**【确认】

```
应用 (C++ / Python SWIG)
   │
   ├── uhd::device::find(hint)  ──► 枚举（只拿身份：type/addr/serial/name）
   ├── uhd::device::make(hint)  ──► 工厂，按 hash 去重重入 (device.cpp:224)
   ▼
uhd::usrp::multi_usrp  ←── 用户主入口（multi_usrp.hpp:121 make()）
   │   set_rx_rate / set_rx_freq(tune_request) / set_rx_gain / set_rx_bandwidth
   │   get_rx_freq_range / get_rx_rates / get_rx_gain_range   ← 能力读回
   ├── get_rx_stream(stream_args) ──► rx_streamer
   │        issue_stream_cmd(stream_cmd)  /  recv(buffs, md, timeout)
   └── get_tree() ──► property_tree（/mboards/... 路径式内部骨干）
```

源码里实际有两条平行的控制路径：**经典 `multi_usrp`**（本笔记主线，老型号全覆盖）
与 **RFNoC `rfnoc_graph`**（新型号 N3xx/X4xx 的图式编程，`host/include/uhd/rfnoc_graph.hpp`，
`examples/python/rx_to_file.py:140` 起的 `rfnoc_dram_rx` 用的就是它）。
**【推断】**MBDSDR 第一版应只用经典 `multi_usrp` 路径，RFNoC 留作后续。

---

## 1. 架构概览

### 1.1 基类 `uhd::device`：发现/工厂/流入口，不做射频控制

`host/include/uhd/device.hpp`（仅 122 行）是抽象基类：

- `:34` 枚举 `device_filter_t { ANY, USRP, CLOCK }`——`find/make` 的第二参数，
  用来把时钟设备（如 USRP 时钟子卡）和普通 USRP 过滤开。
- `:57` `static device_addrs_t find(const device_addr_t& hint, filter=ANY)` —— 枚举。
- `:73-74` `static sptr make(const device_addr_t& hint, filter=ANY, size_t which=0)` —— 打开，
  `which` 是多台设备时的下标。
- `:85 / :96` `get_rx_stream(stream_args_t)` / `get_tx_stream(stream_args_t)` —— 流工厂。
  `:78-84` 注释明确：**非 RFNoC 设备同一时刻只能有一个 RX streamer**，要换必须先销毁旧的。
- `:112` `get_tree()` 拿属性树。
- `:44-45` `register_device(find_t, make_t, filter)` —— 各型号就是这样把自己挂进发现/工厂体系的。

`make` 的实现语义（`host/lib/device.cpp:179-236`）【确认】：
- `:195-198` 一台都没找到 → 抛 `uhd::key_error("No devices found ...")`；
- `:201-204` `which` 越界 → 抛 `uhd::index_error`；
- `:210` 对 device addr 算 hash，`:224-228` **同一台设备重复 make 会返回已存在的实例**（weak_ptr 缓存）。

### 1.2 `multi_usrp`：多板/多通道统一抽象

`host/include/uhd/usrp/multi_usrp.hpp`（2025 行）是用户真正面对的接口。
它把**多块主板（mboard）+ 多通道（channel）**抽象成一个对象：

- `:103-109` 三个通配符常量：`ALL_MBOARDS` / `ALL_CHANS` / `ALL_GAINS`。
  几乎每个方法都带 `size_t chan = 0`，传 `ALL_CHANS` 即一次配所有通道。
- `:121` `static sptr make(const device_addr_t&)` —— 这是 Python 端
  `uhd.usrp.MultiUSRP(args)` 的 C++ 对应物（Python 包装见 `host/python/uhd/usrp/multi_usrp.py:40,45`）。
- `:704-705` `set_rx_subdev_spec(subdev_spec, mboard=ALL_MBOARDS)` —— 选子板/前端
  （如 `"A:A"`、`"A:B"`，`:73-89` 头部示例）。多板时每块板可映射不同前端。
- `:719` `get_rx_num_channels()`、`:733` `get_rx_subdev_name(chan)`。
- `:156` `get_usrp_rx_info(chan)` 返回 `dict<string,string>`，含主板 ID/name/serial、
  子板 RX ID/subdev name/spec/serial/antenna（docstring `:149-155`；填充实现见
  `host/lib/usrp/multi_usrp.cpp:300-346`，键如 `mboard_serial`）。

### 1.3 流式（rx_streamer / sample stream）

见第 3 节。要点：控制面（multi_usrp）与数据面（rx_streamer）**分离**——
先在 multi_usrp 上配频率/速率/增益，再 `get_rx_stream()` 拿流对象，
然后 `issue_stream_cmd()` 启动、循环 `recv()` 取 IQ。

### 1.4 时间戳 / 突发控制

- 时间戳用 `uhd::time_spec_t` 表示（`host/include/uhd/types/time_spec.hpp`）：
  `:40` `(double secs)`、`:47` `(int64_t full_secs, double frac_secs)`、
  `:64` `from_ticks()` / `:80` `to_ticks(rate)`。即"整秒 + 小数秒"，可换算成设备 tick。
- 突发由 `stream_cmd_t` 的 mode 控制（见 3.3）。`recv` 回来的 `rx_metadata_t` 里带
  `has_time_spec/time_spec`（`types/metadata.hpp:47,50`）和 `start_of_burst/end_of_burst`
  （`:69,72`），可做样本级时间对齐。
- 多板同步时，`issue_stream_cmd` 应在"近未来"打时间戳且 `stream_now=false`
  （`stream.hpp:338-340` 注释），靠时间戳对齐多板数据包。

### 1.5 属性树（property tree）

`host/include/uhd/property_tree.hpp`：
- `:206` `struct fs_path : std::string` —— 路径如 `/mboards/0/rx_rate`。
- `:221` `class property_tree`，`:234 subtree(path)`、`:240 exists(path)`、
  `:243 list(path)`、`:247 create<T>(path)`、`:251 access<T>(path)`。
- 每个叶子是 `property<T>`（`property.hpp:79`），带 coercer（`:98` 强制变换）
  和 desired/coerced subscriber（`:121/:132`）——是个观察者 + 强制钳位的键值树。

**【推断】**属性树是 UHD 内部"专家框架"（experts）的骨干，`multi_usrp` 的每个 set/get
最终都落到树上某个节点。普通应用**不需要直接碰属性树**，用 `multi_usrp` 封装即可；
只有 driver 私有旋钮（AGC 参数、bias-tee 等）才需要 `get_tree()->access<T>(path)`。
Python 包装里能看到它的痕迹：`multi_usrp.py:50-52` 用 `get_tree().exists("/mboards/0/token")`
判断是不是 MPM 设备。

---

## 2. 设备发现与枚举

### 2.1 入口与过滤参数

`device::find(hint, filter)`（`device.hpp:57`）的 `hint` 是一个**部分填充的 device_addr**，
用来收窄搜索。`device_addr_t` 本身就是 `dict<string,string>`（`types/device_addr.hpp:38`），
可直接从 `"key=val,key=val"` 字符串构造（`:45`）。

每型号的 find 用 `hint` 做**前缀过滤**——以 B200 为例（`host/lib/usrp/b200/b200_impl.cpp`）：

- `:186` `if (hint.has_key("type") and hint["type"] != "b200") return;` —— 要不是叫 b200 就跳过。
- 于是常用过滤键就是：`type`、`serial`、`name`、`addr`（网络设备）。

官方示例的命令行帮助把格式写明了：`examples/python/rx_to_file.py:54`
`(e.g., addr=192.168.40.2,type=x300)`。【确认】

### 2.2 find 返回的身份信息

各型号在 find 成功后往 `device_addr_t` 里填这些键（**只填身份，不填能力范围**）：

| 键 | 含义 | 证据 |
|---|---|---|
| `type` | 型号族名：`"b200"`/`"usrp2"`/`"x300"`/`"b3xx"`/`"mpmd"` | `b200_impl.cpp:254`、`usrp2_impl.cpp:133`、`x300_eth_mgr.cpp:137`、`b300_impl.cpp:65` |
| `name` | EEPROM 里的用户可编程名 | `b200_impl.cpp:255`、`x300_eth_mgr.cpp:160` |
| `serial` | 序列号（USB 设备） | `b200_impl.cpp:256`、`b100_impl.cpp:117` |
| `addr` | IP 地址（网口设备） | `usrp2_impl.cpp:138`、`x300_eth_mgr.cpp:138` |
| `product` | 具体产品串（如 B2XX 细分） | `b200_impl.cpp:258` 附近 |

`device_addrs_t` 就是 `vector<device_addr_t>`（`device_addr.hpp:94`）。

**重要结论**：【确认 + 推断】枚举阶段**拿不到**调谐范围/采样率/增益范围——
这些要 `make()` 打开设备后，在 `multi_usrp` 上查 `get_rx_freq_range()` 等。
这和 SoapySDR 笔记的结论一致（枚举只给身份，能力要 open 后 probe）。

### 2.3 能力如何读回（打开之后）

| 能力 | API（multi_usrp.hpp） | 返回类型 |
|---|---|---|
| 实际中心频率 | `get_rx_freq(chan=0)` `:789` | `double` Hz |
| 可调频率范围（含 DDC） | `get_rx_freq_range(chan=0)` `:800` | `freq_range_t` |
| 纯 RF 前端频率范围 | `get_fe_rx_freq_range(chan=0)` `:807` | `freq_range_t` |
| 实际采样率 | `get_rx_rate(chan=0)` `:762` | `double` Sps |
| 可设采样率范围 | `get_rx_rates(chan=0)` `:769` | `meta_range_t` |
| 实际增益 | `get_rx_gain(name, chan)` `:1176`（无参便捷版 `:1179`） | `double` dB |
| 增益范围 | `get_rx_gain_range(name, chan)` `:1203`（便捷版 `:1206`） | `gain_range_t` |
| 增益级名 | `get_rx_gain_names(chan)` `:1217` | `vector<string>` |
| 实际带宽 | `get_rx_bandwidth(chan)` `:1259` | `double` Hz |
| 带宽范围 | `get_rx_bandwidth_range(chan)` `:1266` | `meta_range_t` |

范围对象类型（`types/ranges.hpp`）：
- `:22 class range_t` = 单段 `[start, stop, step]`（`:42/:45/:48`）；
- `:66 struct meta_range_t : std::vector<range_t>` = **多段不连续区间**，
  提供 `:140/:142/:145` 聚合 `start()/stop()/step()` 和 `:154 clip(value, clip_step)`；
- `:172 typedef meta_range_t gain_range_t; :173 typedef meta_range_t freq_range_t;`

> 也就是说频率/采样率/带宽范围都可能是**多段**（像 RTL-SDR 那样有死区），
> 不能当成一个 `(min,max)` 二元组。`clip()` 是 UHD 自己的"吸附到合法档"工具。

---

## 3. 接收链路接口（API 名 + 参数语义 + file:line）

### 3.1 采样率

- `multi_usrp.hpp:744` `void set_rx_rate(double rate, size_t chan = ALL_CHANS)`。
  `:737-739` 注释：**会 coerce 到设备能接受的速率**，可能打 warning；
  要调 `get_rx_rate()` 才知道实际生效值。单位 Sps。
- `:755 set_rx_spp(spp, chan)` —— 每包样本数（影响包大小/延迟）。
- 读回：`:762 get_rx_rate` / `:769 get_rx_rates`。

### 3.2 中心频率（tune）

- `multi_usrp.hpp:781-782`
  ```cpp
  virtual tune_result_t set_rx_freq(const tune_request_t& tune_request, size_t chan = 0) = 0;
  ```
  **注意：传的是 `tune_request_t`，不是裸 double**（裸 double 只是最常用构造）。
  `:773-775` 注释：超范围会 coerce 到最近合法频，必须看返回值或 `get_rx_freq()`。

`tune_request_t`（`types/tune_request.hpp`）：
- `:32` `tune_request_t(double target_freq=0)` —— 最常用：RF+DSP 都自动 policy。
- `:42` `tune_request_t(double target_freq, double lo_off)` —— 指定 LO 偏移（避开本振泄漏）。
- `:45-52` `enum policy_t { POLICY_NONE='N', POLICY_AUTO='A', POLICY_MANUAL='M' }`。
- `:58 target_freq`、`:64 rf_freq_policy`+`:70 rf_freq`、`dsp_freq_policy`+`dsp_freq`。
  即可以分别手动指定 RF 本振频率和 DDC 数字下变频频率（做"移频/避开 DC"）。

`tune_result_t`（`types/tune_result.hpp:18`）回读实际落点：
`:27 clipped_rf_freq`、`:39 target_rf_freq`、`:46 actual_rf_freq`、
`:59 target_dsp_freq`、`:67 actual_dsp_freq`。

### 3.3 增益 / AGC

- `multi_usrp.hpp:1091` `set_rx_gain(double gain, const std::string& name, size_t chan=0)`
  —— `name` 指定具体增益级（如 `"LNA"`/`"VGA"`）；`:1085` 空名 = 分配到所有级。
- `:1126-1129` 便捷版 `set_rx_gain(double gain, chan)` 内部转调 `ALL_GAINS`。
- `:1080-1083` 注释：超范围会 coerce，事后用 `get_rx_gain()` 回读真实值。单位 dB。
- `:1148 set_normalized_rx_gain(gain)` —— `[0,1]` 归一化增益（设备无关）。
- `:1167 set_rx_agc(bool enable, chan)` —— `:1152-1155` 注释：
  **只有 B200 系列、E310、E320 实现了片上 AGC**；不支持的设备调用会抛异常；
  开了 AGC 后手动增益被忽略。
- `:1203 get_rx_gain_range(name, chan)` / `:1217 get_rx_gain_names(chan)`。

### 3.4 前端带宽（模拟/滤波带宽，≠采样率）

- `:1252 set_rx_bandwidth(double bandwidth, chan=0)`，`:1245-1246` 会 coerce；
- `:1259 get_rx_bandwidth` / `:1266 get_rx_bandwidth_range`。

### 3.5 天线 / 子板

- `:1227 set_rx_antenna("RX2"/"TX/RX"/...)`，`:1241 get_rx_antennas()` 返回合法天线名列表。
- `:704 set_rx_subdev_spec("A:A", mboard)` —— 选前端。

### 3.6 流式启动 / 停止

流对象由 `get_rx_stream(stream_args_t)` 产出（`device.hpp:85`、`multi_usrp.hpp:144`）。

`stream_args_t`（`stream.hpp:50`）：
- `:75 cpu_format`：`fc64`/`fc32`/`sc16`/`sc8`（主机内存格式；MBDSDR 要 `complex64` → 用 `"fc32"`）；
- `:95 otw_format`：`sc16`/`sc8`/`sc12`（线上格式；降位宽换吞吐，`:88-93` 注释）；
- `:132 args`（device_addr_t，可塞 `spp`、`fullscale` 等）；`channels`（非 RFNoC）。

`rx_streamer`（`stream.hpp:169`）：
- `:177 get_num_channels()`、`:180 get_max_num_samps()`；
- `:327-331` **核心接收调用**
  ```cpp
  size_t recv(const buffs_type& buffs, size_t nsamps_per_buff,
              rx_metadata_t& metadata, double timeout=0.1, bool one_packet=false);
  ```
  返回实际收到的样本数。**注意 recv 非线程安全**（`:242-247`）。
- `:344 issue_stream_cmd(const stream_cmd_t&)` —— 启停流。

启停命令 `stream_cmd_t`（`types/stream_cmd.hpp:43-48`）四种模式：

| 模式 | 值 | 语义 |
|---|---|---|
| `STREAM_MODE_START_CONTINUOUS` | `'a'` | 连续流开始 |
| `STREAM_MODE_STOP_CONTINUOUS` | `'o'` | 连续流停止 |
| `STREAM_MODE_NUM_SAMPS_AND_DONE` | `'d'` | 突发 N 个样本后停 |
| `STREAM_MODE_NUM_SAMPS_AND_MORE` | `'m'` | 收 N 个后继续 |

`:55 stream_now`（false 则按 `:57 time_spec` 在未来某刻启动——时间戳突发）。

典型生命周期【确认，见 Python 包装 `multi_usrp.py:88-148`】：
`get_rx_stream()` → `issue_stream_cmd(start_cont)` → 循环 `streamer.recv(buf, md, timeout)`
直到凑够数 → `issue_stream_cmd(stop_cont)` → 把队列里残余 recv 空（flush）。

### 3.7 overrun / underrun 处理

错误码在 `types/metadata.hpp:113-135` `rx_metadata_t::error_code_t`：

| 码 | 值 | 含义 |
|---|---|---|
| `ERROR_CODE_NONE` | `0x0` | 正常 |
| `ERROR_CODE_TIMEOUT` | `0x1` | 超时（不一定是错误，突发源常见） |
| `ERROR_CODE_LATE_COMMAND` | `0x2` | 命令时间戳已过 |
| `ERROR_CODE_BROKEN_CHAIN` | `0x4` | 多板时间链断 |
| `ERROR_CODE_OVERFLOW` | `0x8` | **overrun/溢出**：设备吐得比应用读得快 |
| `ERROR_CODE_ALIGNMENT` | `0xc` | 对齐错 |
| `ERROR_CODE_BAD_PACKET` | `0xf` | 坏包 |

overrun 关键语义（`stream.hpp:255-268`）【确认】：
- overrun **不会立刻**在 recv 里报出来——FIFO 清空前还有一串有效样本会先交给应用；
  只有所有有效样本都交完，下一次 recv 才置 `OVERFLOW`。
- 连续流式时设备会**自愈**（FIFO 清完即恢复），应用继续 recv 就能拿到新数据。
- 处理姿势：收到 `OVERFLOW` 记一笔统计/日志，**不要退出循环**，继续 recv 即可。
- TX 侧对应 underflow，由 `tx_streamer.recv_async_msg()`（`stream.hpp:425`）回报，
  以及 stream_args 里的 `underflow_policy`（`stream.hpp:112-116`：`next_burst`/`next_packet`）。

---

## 4. Python 绑定对应（MBDSDR 实际会用到的）

MBDSDR 用 `import uhd`。绑定结构【确认，`host/python/uhd/usrp/multi_usrp.py`】：

- `:40 class MultiUSRP(lib.usrp.multi_usrp)` —— 继承 SWIG 出来的 C++ multi_usrp，
  因此 `set_rx_rate/set_rx_freq/set_rx_gain/get_rx_freq_range/...` 全部直接可用。
- `:45 __init__(args="")` —— `MultiUSRP("addr=192.168.10.2,type=b200")`。
- `:55-151 recv_num_samps(num_samps, freq, rate=1e6, channels=(0,), gain=10,
  start_time=None, streamer=None)` —— **一次性取 N 点的便捷函数**，内部就是 3.6 的生命周期：
  `:83 stream_args("fc32","sc16")` → `:85 get_rx_stream` → `:92/101 issue_stream_cmd(start_cont)`
  → `:139 recv 循环`（`:141` 检查 `error_code`）→ `:108 stop_cont` + flush。
  返回 `np.complex64` 数组（`:123`）。重复调用时**传复用的 streamer**，别每次重建（docstring `:62-64`）。
- 类型在 `libpyuhd.types` 下：`tune_request`、`stream_cmd`、`stream_mode`、`time_spec`、
  `rx_metadata`、`rx_metadata_error_code`（见 `:92/:98/:107/:141`）。

---

## 5. 与本项目（MBDSDR）的相关性

### 5.1 现有抽象长什么样

基类 `SDRBackend`（`mbdsdr_ai/sdr_backend.py:106`）已经定义好的源接口契约：

| MBDSDR 接口 | 行号 | 对应 UHD API |
|---|---|---|
| `connect()` | `:143` | `uhd.usrp.MultiUSRP(args)`（抛异常=失败） |
| `_apply_frequency(freq_hz)` | `:180` | `set_rx_freq(types.tune_request(freq), 0)` |
| `_apply_sample_rate(rate_hz)` | `:209` | `set_rx_rate(rate, 0)`，事后 `get_rx_rate()` 回读 |
| `_apply_gain(gain_db)` | `:238` | `set_rx_gain(db)`；或 `set_normalized_rx_gain()` |
| `set_bandwidth(bw_hz)` | `:260` | `set_rx_bandwidth(bw, 0)` |
| `set_agc(on)` | `:254` | `set_rx_agc(on, 0)`（注意老型号会抛异常，要 try） |
| `readback_hw_state()` | `:245` | `get_rx_freq/get_rx_rate/get_rx_gain/get_rx_bandwidth` |
| `read_samples(n)->complex64` | `:311` | `rx_streamer.recv()` 循环 / `recv_num_samps` |
| `list_devices()`（classmethod） | 见各后端 | `uhd.device.find("")` 遍历 type/serial/addr |

基类已做好的通用保护【确认】：换频前按 `device.frequency_range` 钳位（`:159`）、
增益钳到 `[0, device.max_gain]`（`:220`）、失败回滚 status（`:168-173`）、绝不造假连接。

### 5.2 现有 `USRPBackend` 现状与差距

`sdr_backend.py:2363` 已有一个 `USRPBackend` 占位：

**已做【确认】**：`MultiUSRP(args)` 连接（`:2394`）、`set_rx_freq(tune_request)`（`:2419`）、
`set_rx_rate`（`:2428`）、`set_rx_gain`（`:2437`）、`read_samples` 用 `recv_num_samps`（`:2446`）。

**差距 / 需修【确认 + 推断】**：
1. **能力是硬编码占位，没从硬件读回**：`:2377-2379` 写死
   `frequency_range=(10e3, 6e9)`、`sample_rate_range=(1e5,5.6e7)`、`max_gain=76.0`。
   【推断】这些是泛化估值，真实值必须 `make()` 后用 `get_rx_freq_range()/get_rx_rates()/
   get_rx_gain_range()` probe 填进 `self.device`，否则换频钳位（`:159`）和增益钳位（`:220`）
   会用错量程。
2. **`recv_num_samps` 位置参数疑似传错**：签名是
   `(num_samps, freq, rate, channels, gain, start_time, streamer)`（`multi_usrp.py:55-56`），
   而 `:2446` 调用 `recv_num_samps(n, freq, rate, [0], 0.1)` —— 第 5 个位置参数是
   **`gain=0.1` dB**，不是 start_time。【确认：从源码签名可直接判定】这会把增益压到 0.1 dB。
3. **没有连续流式生命周期**：每次 `read_samples` 都让 `recv_num_samps` 自建/自毁 streamer
   （`:2446` 没传 `streamer=`），高吞吐/瀑布刷新下会反复重建流，延迟大。
4. **没有 overrun 处理**：`recv_num_samps` 内部 `:141` 只 `print` 错误码，
   不透传给 MBDSDR 的 `status.error`，溢出不可观测。
5. 没接 `set_rx_bandwidth`、`set_rx_antenna`、`set_rx_agc`、`readback_hw_state`、`list_devices`。

### 5.3 最小接入点（让 USRP 后端"真能用"）【推断，基于上表映射】

按优先级：

- **P0 连接 + 读回能力**：`connect()` 成功后调 `get_rx_freq_range()/get_rx_rates()/
  get_rx_gain_range()` 填 `self.device`（替换硬编码），实现 `readback_hw_state()`。
- **P0 修 `read_samples`**：要么改对 `recv_num_samps` 的增益参数，要么自己建一个常驻
  `rx_streamer`（`stream_args("fc32","sc16")`），`start_cont` 启动后循环 `recv`，
  按 `metadata.error_code==OVERFLOW` 记日志继续，凑够 n 个 `complex64` 返回。
- **P0 流式启停与 disconnect 配对**：`disconnect()` 时 `issue_stream_cmd(stop_cont)` 再释放。
- **P1 `list_devices()`**：`uhd.device.find("")` 列出 type/serial/addr/name，
  与现有 RTL-SDR/SoapySDR 条目去重（同 serial 合并）。
- **P1 增益/AGC**：`set_rx_gain` 后 `get_rx_gain()` 回读真实值；`set_rx_agc` 包 try
  （不支持的型号抛异常，要降级为软件 AGC 或报错，别崩）。
- **P2 天线/带宽/子板**：`get_rx_antennas()` 枚举天线名，`set_rx_bandwidth` 默认设到
  `get_rx_bandwidth_range()` 中间值。

### 5.4 可落地功能清单

【推断，按 MBDSDR 现有频谱/接收链能力裁剪】

1. **USRP 真实 IQ 源**：替换 WebSocket/回放，把 `read_samples()` 的 `complex64` 接进
   现有 FFT 频谱/瀑布管线（基类已留 `subscribe_iq` ring fan-out 口子，`:299`）。
2. **频谱扫描/大带宽采集**：USRP 采样率可达数十 MSPS（`get_rx_rates()` 读真实上限），
   适合做宽带频谱感知，这是 RTL-SDR/HackRF 棒给不了的。
3. **时间戳对齐采集**：用 `recv` 回来的 `rx_metadata.time_spec` + `set_next_*`/命令时间戳，
   做定点/定时突发抓取（对卫星过顶、定时信标有用，对齐 MBDSDR 的 sat 指向场景）。
4. **多通道/同步接收**：`multi_usrp` 天然多通道，B210/X3x0 可做 MIMO / 双通道测向
   （对应白皮书里的阵列/测向方向）——二期。
5. **TX 回放**：`tx_streamer.send()`（`stream.hpp:414`）+ `send_waveform()` 便捷函数
   （`multi_usrp.py:153`），可做 SSTV/信号发射——二期，当前 `supports_tx=True` 先占位。

**不建议一期做**：RFNoC 图式编程（`rfnoc_graph`）、属性树直驱私有旋钮、
多板 MIMO 时间同步——这些等真有对应型号硬件再上。

---

## 6. 诚实标注小结

**【确认】（直接读源码，可逐行核对）**：
- 所有 API 签名与 `file:line`（device.hpp / multi_usrp.hpp / stream.hpp / types/*）；
- find/make 行为、异常、去重（device.cpp:169-236）；
- 发现枚举键 type/name/serial/addr 的填充位置（各 `*_impl.cpp`）；
- overrun 延迟上报与自愈语义（stream.hpp:255-268）、错误码表（metadata.hpp:113-135）；
- Python `recv_num_samps` 内部生命周期与签名（multi_usrp.py:55-151）；
- 现有 `USRPBackend` 硬编码量程与 `:2446` 位置参数错配（sdr_backend.py:2377-2446）。

**【推断】（基于源码结构 + MBDSDR 现状，未插真实硬件验证）**：
- "一期只用经典 multi_usrp、RFNoC 留后"的路线建议；
- 能力必须 open 后 probe、不能用枚举值；
- 5.3 的最小接入点优先级、5.4 功能清单的裁剪；
- 硬编码量程"偏小/偏差"的具体程度——**未实测，不知道真实型号的实际范围**。

**【未验证 / 本笔记未覆盖】**：
- 各具体型号（B210/X310/N320…）的**真实**可调频率/采样率/增益数值——本仓库头文件只
  暴露 `get_*_range()` 接口，具体数值在各 `*_impl.cpp` 与 FPGA 镜像里，本笔记未逐一枚举；
- RFNoC graph 的完整 API（仅确认其存在与入口 `rfnoc_graph.hpp`）；
- Python `uhd` pip 包是否在本环境可 import、能否真打开设备（干净室，未跑）。
