# LimeSuite / LMS7002M 真实硬件参数移植笔记

> 本笔记记录从 `repos/LimeSuite` **C 源码**逐条核对后，移植进
> `mbdsdr_ai/limesuite_params.py` 的真实硬件参数。每条都标注 `文件:行号`。
> 红线：增益表是 `LMS7002M::SetRFELNA_dB / SetRFETIA_dB / SetRBBPGA_dB` 里的
> **真实离散档**，不是按物理量程线性铺开的假数据；无设备时后端 `connect()`
> 直接返回 False，绝不造假。

## 1. 数据源（实际读过的源文件）

| 文件 | 作用 |
|---|---|
| `repos/LimeSuite/src/lime/LimeSuite.h` | C API 定义、枚举、常量、数据格式 |
| `repos/LimeSuite/src/API/lms7_device.cpp` | LMS7 通用设备：频率范围、采样率范围、组合增益表 |
| `repos/LimeSuite/src/API/LimeSDR_mini.cpp` | LimeSDR Mini 板级覆写：频率/采样率上限 |
| `repos/LimeSuite/src/lms7002m/LMS7002M.cpp` | LMS7002M 射频芯片：LNA/TIA/PGA 增益寄存器映射 |
| `repos/LimeSuite/src/API/lms7_api.cpp` | C API 封装：`LMS_SetSampleRateRange` / `LMS_GetLOFrequencyRange` |
| `repos/LimeSuite/src/protocols/Streamer.cpp` | FPGA 数据流收发 |
| `repos/LimeSuite/src/ADF4002/ADF4002.cpp` | 外部时钟合成器（LimeSDR USB 用） |

## 2. 频率范围

| 板型 | 范围 | 来源 |
|---|---|---|
| LimeSDR USB（通用 LMS7_Device） | **100 kHz – 3.8 GHz** | `lms7_device.cpp:1384` |
| LimeSDR Mini（LMS7_LimeSDR_mini 覆写） | **10 MHz – 3.5 GHz** | `LimeSDR_mini.cpp:312` |

`lms7_device.cpp:1382-1385` 原文：
```cpp
LMS7_Device::Range LMS7_Device::GetFrequencyRange(bool tx) const
{
    return Range(100e3, 3.8e9);
}
```

`LimeSDR_mini.cpp:310-313` 覆写：
```cpp
LMS7_Device::Range LMS7_LimeSDR_mini::GetFrequencyRange(bool tx) const
{
    return Range(10e6, 3.5e9);
}
```

注意：`LMS_SetLOFrequency()`（`LimeSuite.h:251`）本身不做范围钳幅，量程约束由
各板子类 `GetFrequencyRange()` 返回的 `Range` 决定。上层必须自己校验。

## 3. 采样率

| 板型 | 范围 | 来源 |
|---|---|---|
| LimeSDR USB（通用 LMS7_Device） | **100 kHz – 61.44 MHz** | `lms7_device.cpp:690` |
| LimeSDR Mini（覆写） | **100 kHz – 30.72 MHz** | `LimeSDR_mini.cpp:307` |

`lms7_device.cpp:688-691` 原文：
```cpp
LMS7_Device::Range LMS7_Device::GetRateRange(bool /*dir*/, unsigned /*chan*/) const
{
    return Range(100e3, 61.44e6);
}
```

合法过采样比：**1, 2, 4, 8, 16, 32, 0（默认）**。来源 `LimeSuite.h:197,604`。
原文：*"Valid oversampling values are 1, 2, 4, 8, 16, 32 or 0 (use device default oversampling value)."*

推荐档（均落在 100k–61.44 MHz）：0.1 / 0.2 / 0.5 / 1 / 2 / 5 / 10 / 15.36 / 20 / 30.72 / 61.44 Msps。

## 4. 增益链（重点：真实离散档）

### 4.1 组合增益 —— 0–73 dB

- API：`LMS_SetGaindB(device, dir_tx, chan, gain)`（`LimeSuite.h:385`）。
- 合法范围：**[0, 73]** dB。来源 `LimeSuite.h:382` 原文：*"Desired gain, range [0, 73]"*。
- 驱动内部 `maxGain = 74`（`lms7_device.cpp:1032`），索引 0..73。
- 驱动查表（`lms7_device.cpp:1057-1071`）自动把组合增益拆成 LNA + TIA + PGA 三级。

### 4.2 LNA（RFE 级，G_LNA_RFE 寄存器）—— 15 档

- API：`LMS7002M::SetRFELNA_dB(value)`（`LMS7002M.cpp:789`）。
- `gmax = 30` dB（`LMS7002M.cpp:791`）。
- 寄存器码：`lna+1`（`lms7_device.cpp:1086`），即 1..15。
- 回读 switch 表（`LMS7002M.cpp:818-835`）：

| 寄存器码 | dB |
|---|---|
| 15 | 30 |
| 14 | 29 |
| 13 | 28 |
| 12 | 27 |
| 11 | 26 |
| 10 | 25 |
| 9 | 24 |
| 8 | 21 |
| 7 | 18 |
| 6 | 15 |
| 5 | 12 |
| 4 | 9 |
| 3 | 6 |
| 2 | 3 |
| 1 | 0 |

**真实离散档 = [0, 3, 6, 9, 12, 15, 18, 21, 24, 25, 26, 27, 28, 29, 30] dB，共 15 档。**
注意步进非线性：0–21 步进 3 dB，24–30 步进 1 dB。

### 4.3 TIA（RFE 级，G_TIA_RFE 寄存器）—— 3 档

- API：`LMS7002M::SetRFETIA_dB(value)`（`LMS7002M.cpp:890`）。
- `gmax = 12` dB（`LMS7002M.cpp:892`）。
- 寄存器码：`tia+1`（`lms7_device.cpp:1087`），即 1..3。
- 回读 switch 表（`LMS7002M.cpp:907-912`）：

| 寄存器码 | dB |
|---|---|
| 3 | 12 |
| 2 | 9 |
| 1 | 0 |

**真实离散档 = [0, 9, 12] dB，共 3 档。**
组合分配逻辑（`lms7_device.cpp:1080-1081`）：组合增益 >51 → tia=2(9dB)；>42 → tia=1(0dB)。

### 4.4 PGA（RBB 级，G_PGA_RBB 寄存器）—— 5-bit / 32 码

- API：`LMS7002M::SetRBBPGA_dB(value)`（`LMS7002M.cpp:763`）。
- 写入映射：`g_pga_rbb = (int)(value + 12.5)`（`LMS7002M.cpp:765`）。
- 钳位：`> 0x1f` 钳到 31，`< 0` 钳到 0（`LMS7002M.cpp:766-767`）。
- 回读：`return g_pga_rbb - 12`（`LMS7002M.cpp:786`）。
- 组合分配表 `pgaTbl`（`lms7_device.cpp:1065-1071`）取值 0..31，共 32 个寄存器码。

**G_PGA_RBB 是 5-bit 寄存器域，合法码 = [0, 1, 2, ..., 31]，共 32 码。**

## 5. 天线端口

来源 `LimeSuite.h:280-289`：

```c
LMS_PATH_NONE = 0;
LMS_PATH_LNAH = 1;  // RX LNA_H port
LMS_PATH_LNAL = 2;  // RX LNA_L port
LMS_PATH_LNAW = 3;  // RX LNA_W port
LMS_PATH_TX1  = 1;  // TX port 1
LMS_PATH_TX2  = 2;  // TX port 2
LMS_PATH_AUTO = 255;
```

路径名表（`lms7_device.cpp:693-699`）：
- TX: `{"NONE", "BAND1", "BAND2"}`
- RX: `{"NONE", "LNAH", "LNAL", "LNAW", "LB1", "LB2"}`

## 6. 数据格式

来源 `LimeSuite.h:1099-1104`：

```c
enum {
    LMS_FMT_F32 = 0,   // 32-bit floating point
    LMS_FMT_I16,       // 16-bit integers
    LMS_FMT_I12        // 12-bit integers stored in 16-bit variables
} dataFmt;
```

LimeSDR 原生 ADC/DAC 为 **12-bit**，但在主机总线上以 **16-bit 容器**传输
（`LMS_FMT_I12`）。`LMS_RecvStream()` 读出的是交织 int16 I/Q，每复采样 4 字节。

## 7. C API 速查（ctypes 后端对应）

| 操作 | C 函数 | 来源 |
|---|---|---|
| 打开设备 | `LMS_Open(dev, info, args)` | `LimeSuite.h:103` |
| 初始化 | `LMS_Init(dev)` | `LimeSuite.h:164` |
| 关通道 | `LMS_EnableChannel(dev, dir_tx, chan, on)` | `LimeSuite.h:189` |
| 设采样率 | `LMS_SetSampleRate(dev, rate, oversample)` | `LimeSuite.h:205` |
| 设频率 | `LMS_SetLOFrequency(dev, dir_tx, chan, freq)` | `LimeSuite.h:251` |
| 设天线 | `LMS_SetAntenna(dev, dir_tx, chan, index)` | `LimeSuite.h:315` |
| 设组合增益 | `LMS_SetGaindB(dev, dir_tx, chan, gain)` | `LimeSuite.h:385` |
| 建流 | `LMS_SetupStream(dev, stream)` | `LimeSuite.h:1149` |
| 启流 | `LMS_StartStream(stream)` | `LimeSuite.h:1168` |
| 收样本 | `LMS_RecvStream(stream, buf, count, meta, timeout)` | `LimeSuite.h:1191` |
| 停流 | `LMS_StopStream(stream)` | `LimeSuite.h:1177` |
| 毁流 | `LMS_DestroyStream(dev, stream)` | `LimeSuite.h:1159` |
| 关设备 | `LMS_Close(dev)` | `LimeSuite.h:115` |

## 8. 诚实降级策略

`LimeSDRBackend`（`mbdsdr_ai/limesuite_params.py`）通过 ctypes 加载
`libLimeSuite.so`。找不到库或无设备时：

- `connect()` 返回 `False`，`connected = False`
- 所有 setter（`set_frequency` / `set_sample_rate` / `set_gain` / `start_rx`）返回 `False`
- `read_samples()` 返回 `None`

**绝不伪造"已连接"或假 IQ 样本。**
