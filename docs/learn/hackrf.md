# libhackrf 真实硬件参数移植笔记

> 本笔记记录从 `repos/hackrf` **C 源码**（host/libhackrf 驱动 + firmware/common 固件）
> 逐条核对后，移植进 `mbdsdr_ai/hackrf_params.py` 的真实硬件参数。每条都标注
> `文件:行号`。红线：增益表是 `hackrf_set_lna/vga/txvga_gain()` 里的**真实离散档**，
> 不是按物理量程线性铺开的假数据；无设备时后端 `connect()` 直接返回 False，绝不造假。

## 1. 数据源（实际读过的源文件）

| 文件 | 作用 |
|---|---|
| `repos/hackrf/host/libhackrf/src/hackrf.h` | API、枚举、频率/采样率/增益/偏置文档、USB 块常量 |
| `repos/hackrf/host/libhackrf/src/hackrf.c` | 主驱动：`hackrf_set_freq/sample_rate/lna/vga/txvga_gain`、open/close/start_rx |
| `repos/hackrf/firmware/common/max2837.c` | MAX2837 射频前端：LNA/VGA/TXVGA 增益寄存器映射 |
| `repos/hackrf/firmware/common/max2837.h` / `max2837_regs.def` | MAX2837 寄存器位定义 |
| `repos/hackrf/firmware/common/max5864.c` | MAX5864 ADC/DAC 工作模式（8-bit I/Q 收发） |
| `repos/hackrf/firmware/common/transceiver_mode.h` | 收发模式枚举 OFF/RX/TX/SS/CPLD/RX_SWEEP |
| `repos/hackrf/host/hackrf-tools/src/hackrf_info.c` | 设备信息查询 |
| `repos/hackrf/host/hackrf-tools/src/hackrf_transfer.c` | 数据收发命令行参考 |

## 2. 频率范围

| 板型 | 范围 | 来源 |
|---|---|---|
| HackRF One（rev9 前 / 后） | **1 MHz – 6000 MHz** | `hackrf.h:662,670` |
| Jawbreaker（beta） | 10 – 6000 MHz | `hackrf.h:658` |
| RAD1O | 50 – 4000 MHz | `hackrf.h:666` |

文档原文（`hackrf.h:235`）：*"The HackRF One can tune to nearly any frequency
between 1-6000MHz"*。

注意：`hackrf_set_freq()`（`hackrf.c:1775-1807`）**本身不做范围钳幅**，它只是把
`freq_hz` 拆成 MHz 整数 + 余 Hz 两个 LE32 字段下发固件（`:1784-1787`）。量程约束在
固件/手册侧，宿主驱动只负责打包。因此上层必须自己校验 1 MHz–6 GHz。

## 3. 采样率

- 合法区间：**2 – 20 MHz**，默认 **10 MHz**。来源 `hackrf.h:247,1794,1813`。
  原文：*"set between 2-20MHz … with the default being 10MHz. Lower & higher
  values are technically possible, but the performance is not guaranteed."*
- `hackrf_set_sample_rate_manual()` 的时钟分频 `divider` 范围 **1–31**
  （`hackrf.h:1801`）。
- 每次设采样率后，固件自动把基带滤波器带宽设为 `≤ 0.75·Fs`
  （`hackrf.c:1907-1910`，`hackrf.h:243,1814`）。

推荐档（均落在 2–20 MHz）：2 / 4 / 8 / 10 / 12.5 / 16 / 20 Msps。

## 4. 增益链（重点：真实离散档）

HackRF One 有 5 个放大器（`hackrf.h:216-231`）。RX 链三级、TX 链两级：

### 4.1 LNA（RX IF 级，MAX2837 "IF"）—— 6 档

- API：`hackrf_set_lna_gain(dev, value)`（`hackrf.c:2022`）。
- 校验：`if (value > 40) return HACKRF_ERROR_INVALID_PARAM;`（`hackrf.c:2027`）。
- 步进：`value &= ~0x07;`（`hackrf.c:2031`）→ 向下对齐到 **8 dB** 的倍数。
- 固件侧 `max2837_set_lna_gain()` 的 switch 只接受 6 个 case
  （`max2837.c:344-371`）：`40 / 32 / 24 / 16 / 8 / 0`，其余 `return false`。

**真实离散档 = [0, 8, 16, 24, 32, 40] dB，共 6 档。**

### 4.2 VGA（RX 基带 BB 级，MAX2837 "VGA"）—— 32 档

- API：`hackrf_set_vga_gain(dev, value)`（`hackrf.c:2049`）。
- 校验：`if (value > 62) return HACKRF_ERROR_INVALID_PARAM;`（`hackrf.c:2054`）。
- 步进：`value &= ~0x01;`（`hackrf.c:2058`）→ 向下对齐到**偶数**。
- 固件侧（`max2837.c:373-376`）：`if ((gain_db & 0x1) || gain_db > 62) return false;`
  奇数直接拒绝；寄存器写入 `31 - (gain_db >> 1)`（`:378`）。

**真实离散档 = 0, 2, 4, …, 62 dB，共 32 档。**

### 4.3 TXVGA（TX IF 级）—— 48 档

- API：`hackrf_set_txvga_gain(dev, value)`（`hackrf.c:2076`）。
- 校验：`if (value > 47) return HACKRF_ERROR_INVALID_PARAM;`（`hackrf.c:2081`）。
- **无掩码**（与 LNA/VGA 不同）→ **1 dB 步进**，直接下发。
- 固件侧 `max2837_set_txvga_gain()`（`max2837.c:383-395`）按 `gain<16` /
  `gain≥16` 两段线性映射寄存器。

**真实离散档 = 0, 1, 2, …, 47 dB，共 48 档。**

### 4.4 天线口 RF 放大器（RX/TX 共用）

`hackrf_set_amp_enable(dev, 0/1)`（`hackrf.c:1961`），开关式，约 **11 dB**
（`hackrf.h:227,1826`：*"~11dB RF RX/TX amplifiers U13/U25"*）。

## 5. 天线偏置（bias-tee）

- `hackrf_set_antenna_enable(dev, 0/1)`（`hackrf.c:2102`）。
- **3.3 V / 最大 50 mA**，默认关闭（`hackrf.h:255,1888`）。
- 关键注意（`hackrf.h:255,1890`）：固件回到 IDLE 模式会**自动关闭**偏置，
  每次进入 RX/TX 都要重新开——不能像 RTL-SDR 那样永久设置。

## 6. 数据格式（USB 流）

- **交织的有符号 8-bit I/Q**（`int8_t`，I/Q/I/Q/…）。
  - `hackrf.h:350`：`int8_t *signed_buffer = (int8_t*)transfer->buffer;`
  - `hackrf.h:965-966`：*"transfer data buffer (interleaved 8 bit I/Q samples)"*。
- 每个 USB 块：`BYTES_PER_BLOCK = 16384` 字节 = `SAMPLES_PER_BLOCK = 8192` 复采样
  （`hackrf.h:511,517`，即 8192×2 字节）。
- 默认传输缓冲 `buffer_size = 32768`（`hackrf.c:797`，固件未上报时）。

## 7. USB 标识与端点

| 项 | 值 | 来源 |
|---|---|---|
| USB VID | `0x1d50` | `hackrf.c:202` |
| HackRF One PID | `0x6089` | `hackrf.c:204`；`hackrf.h:826` |
| RX 端点 | `LIBUSB_ENDPOINT_IN \| 1` (0x81) | `hackrf.c:129` |
| TX 端点 | `LIBUSB_ENDPOINT_OUT \| 2` (0x02) | `hackrf.c:130` |

## 8. C API → Python 后端对应

| 操作 | libhackrf C 调用 | 位置 |
|---|---|---|
| 打开 | `hackrf_init` + `hackrf_open` | `hackrf.c:512,838` |
| 设频率 | `hackrf_set_freq(dev, freq_hz)` | `hackrf.c:1775` |
| 设采样率 | `hackrf_set_sample_rate(dev, freq)` | `hackrf.c:1920` |
| 设 LNA | `hackrf_set_lna_gain(dev, db)` | `hackrf.c:2022` |
| 设 VGA | `hackrf_set_vga_gain(dev, db)` | `hackrf.c:2049` |
| 设 TXVGA | `hackrf_set_txvga_gain(dev, db)` | `hackrf.c:2076` |
| 偏置 | `hackrf_set_antenna_enable(dev, 0/1)` | `hackrf.c:2102` |
| 放大器 | `hackrf_set_amp_enable(dev, 0/1)` | `hackrf.c:1961` |
| 启动 RX | `hackrf_start_rx(dev, cb, ctx)` | `hackrf.c:2339` |
| 停止 RX | `hackrf_stop_rx(dev)` | `hackrf.c:2369` |
| 关闭 | `hackrf_close(dev)` | `hackrf.c:2467` |

Python 侧见 `mbdsdr_ai/hackrf_params.py`：
- `HackRFParams` —— 纯参数表（量程/离散增益档/校验/吸附），无设备依赖；
- `HackRFBackend` —— ctypes 直连 `libhackrf.so`，缺库/缺设备时 `connect()` 返回
  `False`，所有 setter 返回 `False`、`read_samples` 返回 `None`，**不造假**。

`sdr_backend.HackRFBackend`（PyPI `hackrf` 绑定版）的量程与增益档也全部改为引用
`HackRFParams`，不再硬编码魔数；`enumerate_all_sdr_devices()` 固定列出一台
`hackrf_0`，真实打开成败由 `connect()` 决定。

## 9. 已注册工具（ToolRegistry）

| 工具 | 作用 |
|---|---|
| `hackrf_list_gains` | 列出 LNA/VGA/TXVGA 全部离散增益档与完整参数摘要 |
| `hackrf_get_freq_range` | 返回频率范围(Hz)与采样率合法区间 |
| `hackrf_set_params` | 把目标增益/采样率吸附到真实离散档（对齐 `&= ~0x07` / `&= ~0x01` 语义） |

## 10. 验证

```
python3 -m pytest tests/hackrf_params_test.py -v
# 15 passed：LNA 6 档 / VGA 32 档 / TXVGA 48 档 / 频率 1M-6G / 采样率 2M-20M /
#           无设备 connect 返回 False 不崩溃 / enumerate 含 hackrf 条目
```
