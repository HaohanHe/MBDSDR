# libbladeRF 真实硬件参数移植笔记

> 本笔记记录从 `repos/bladeRF` **C 源码**（host/libraries/libbladeRF 驱动 +
> fpga_common 公共 RFIC 参数）逐条核对后，移植进 `mbdsdr_ai/bladerf_params.py`
> 的真实硬件参数。每条都标注 `文件:行号`。红线：增益分级是
> `bladerf_set_rxvga1/rxvga2/txvga1/txvga2()` 对应的**真实量程**，不是按物理
> 量程线性铺开的假数据；无设备时后端 `connect()` 直接返回 False，绝不造假。

## 1. 数据源（实际读过的源文件）

| 文件 | 作用 |
|---|---|
| `repos/bladeRF/host/libraries/libbladeRF/include/libbladeRF.h` | 主 API、枚举、通道/格式、返回码 |
| `repos/bladeRF/host/libraries/libbladeRF/include/bladeRF1.h` | bladeRF 1.0 (x40/x115, LMS6002D) legacy 常量与分级增益 API |
| `repos/bladeRF/host/libraries/libbladeRF/include/bladeRF2.h` | bladeRF 2.0 Micro (AD9361) 专用 API（bias-tee / RFIC 寄存器） |
| `repos/bladeRF/host/libraries/libbladeRF/src/bladerf.c` | 主 API 分发层 |
| `repos/bladeRF/host/libraries/libbladeRF/src/board/bladerf2/bladerf2.c` | bladeRF 2.0 板级 range 表挂载（sample_rate/bandwidth/frequency） |
| `repos/bladeRF/fpga_common/include/bladerf2_common.h` | **bladeRF 2.0 真实 range 表**（频率/采样率/带宽/增益分段） |
| `repos/bladeRF/fpga_common/src/lms.c` | LMS6002D VCO 分频表（bladeRF 1.0 频率合成） |
| `repos/bladeRF/host/libraries/libbladeRF/src/board/bladerf1/bladerf1.c` | bladeRF 1.0 板级 range 表挂载 |

## 2. 两种板型、两种 RFIC

bladeRF 有两代硬件，RFIC 完全不同，量程表也不同：

| 板型 | RFIC | 频率范围 | 采样率 | 带宽 | 增益 API 风格 |
|---|---|---|---|---|---|
| bladeRF 1.0 (x40/x115) | LMS6002D | 237.5 MHz – 3.8 GHz | 80 kSPS – 40 MSPS (rec) | 1.5 – 28 MHz | legacy VGA 分级 (RXVGA1/RXVGA2/TXVGA1/TXVGA2) |
| bladeRF 2.0 Micro | AD9361 | RX 70 MHz – 6 GHz / TX 47 MHz – 6 GHz | 520 834 – 61.44 MSPS | 200 kHz – 56 MHz | 总增益 `bladerf_set_gain()`，按频段分段 |

来源：
- bladeRF 1.0 常量：`host/libraries/libbladeRF/include/bladeRF1.h:47,54,60,66,84,90`
- bladeRF 2.0 range：`fpga_common/include/bladerf2_common.h:510-562`

## 3. 频率范围

### 3.1 bladeRF 1.0 (LMS6002D)

- 无 XB-200 扩展板：**237.5 MHz – 3.8 GHz**。
  - `bladeRF1.h:84` `#define BLADERF_FREQUENCY_MIN 237500000u`
  - `bladeRF1.h:90` `#define BLADERF_FREQUENCY_MAX 3800000000u`
- 加 XB-200 高频扩展：下限可到 0 Hz（`bladeRF1.h:78`），但器件只保证 50 MHz 以上。
- 板级挂载：`board/bladerf1/bladerf1.c:268-269,276` 把这两个常量填进
  `bladerf_range` 结构，`bladerf_get_frequency_range()` 直接返回。

### 3.2 bladeRF 2.0 Micro (AD9361)

- **RX: 70 MHz – 6 GHz**
  - `bladerf2_common.h:550-555` `bladerf2_rx_frequency_range = {70000000, 6000000000}`
- **TX: 47 MHz – 6 GHz**
  - `bladerf2_common.h:557-562` `bladerf2_tx_frequency_range = {47000000, 6000000000}`

> 任务说明里写的 "70 MHz-6 GHz" 与源码一致；"300 MHz-3.8 GHz" 是 bladeRF 1.0
> 加 XB-200 之前的旧值（实际源码是 237.5 MHz 起）。我们以源码为准。

## 4. 采样率

### 4.1 bladeRF 2.0 Micro

- 基础区间：**520 834 – 61 440 000 Hz**，步进 2 Hz。
  - `bladerf2_common.h:518-523`
    ```c
    static struct bladerf_range const bladerf2_sample_rate_range_base = {
        FIELD_INIT(.min,  520834),
        FIELD_INIT(.max,  61440000),
        FIELD_INIT(.step, 2),
    };
    ```
- 过采样模式区间：6.25 – 122.88 MSPS（`bladerf2_common.h:526-531`）。
- 4x 插值/抽取区间：520 834 – 2 083 334 Hz（`bladerf2_common.h:534-539`）。
- 板级 `bladerf2.c:985,988` 按是否开启 over-sample 选 base / oversample range。

### 4.2 bladeRF 1.0

- 最小 **80 kSPS**（`bladeRF1.h:47` `BLADERF_SAMPLERATE_MIN 80000u`）。
- 推荐最大 **40 MSPS**（`bladeRF1.h:54` `BLADERF_SAMPLERATE_REC_MAX 40000000u`）。

> 任务说明里写 "200 kSPS" 是近似；源码实际下限是 520 834 Hz（bladeRF 2.0）
> 或 80 kSPS（bladeRF 1.0）。我们以源码为准。

## 5. 带宽（LPF）

### 5.1 bladeRF 2.0 Micro

- **200 kHz – 56 MHz**，1 Hz 步进（设备会选最近的离散档）。
  - `bladerf2_common.h:542-547`
    ```c
    static struct bladerf_range const bladerf2_bandwidth_range = {
        FIELD_INIT(.min,  200000),
        FIELD_INIT(.max,  56000000),
    };
    ```
- `bladerf2.c:1195` `*range = &bladerf2_bandwidth_range;`

### 5.2 bladeRF 1.0

- **1.5 MHz – 28 MHz**。
  - `bladeRF1.h:60` `BLADERF_BANDWIDTH_MIN 1500000u`
  - `bladeRF1.h:66` `BLADERF_BANDWIDTH_MAX 28000000u`
- 板级钳幅在 `board/bladerf1/bladerf1.c:1997-2002`：
  ```c
  if (bandwidth < BLADERF_BANDWIDTH_MIN) bandwidth = BLADERF_BANDWIDTH_MIN;
  else if (bandwidth > BLADERF_BANDWIDTH_MAX) bandwidth = BLADERF_BANDWIDTH_MAX;
  ```

> 任务说明里写 "1.5 MHz-56 MHz" 是两代的拼接（1.5 MHz 来自 bladeRF 1.0，
> 56 MHz 来自 bladeRF 2.0）。源码里 bladeRF 2.0 实际下限是 200 kHz。我们以
> 源码为准，两套量程都保留在 `BladeRFParams` 里。

## 6. 增益链（重点：真实分级）

### 6.1 bladeRF 1.0 legacy VGA 分级（LMS6002D）

这些常量在 `bladeRF1.h:150-196`，对应 deprecated API
`bladerf_set_rxvga1/rxvga2/txvga1/txvga2()`（仍在 libbladeRF.so 里导出）：

| 级 | API | 范围 | 档数 | 来源 |
|---|---|---|---|---|
| RXVGA1 (pre-LPF) | `bladerf_set_rxvga1` | **5 – 30 dB** | 26 | `bladeRF1.h:154,160` |
| RXVGA2 (post-LPF) | `bladerf_set_rxvga2` | **0 – 30 dB** | 31 | `bladeRF1.h:166,172` |
| LNA 开关 | `bladerf_set_lna_gain` | 0 / 3 / 6 dB | 3 | `bladeRF1.h:203-222` |
| TXVGA1 (post-LPF) | `bladerf_set_txvga1` | **-35 – -4 dB** | 32 | `bladeRF1.h:178,184` |
| TXVGA2 (PA) | `bladerf_set_txvga2` | **0 – 25 dB** | 26 | `bladeRF1.h:190,196` |

RXVGA1 + RXVGA2 合计 **5 – 60 dB**，与任务说明一致。
`bladeRF1.h:314-316,344-346,258-260,229-231` 注释明确："Values outside the
range will be clamped"，即超出范围驱动自动钳幅，不报错。

LNA 三档枚举（`bladeRF1.h:203-208`）：
```c
BLADERF_LNA_GAIN_UNKNOWN,
BLADERF_LNA_GAIN_BYPASS,   // 0 dB
BLADERF_LNA_GAIN_MID,       // 3 dB  (:215)
BLADERF_LNA_GAIN_MAX        // 6 dB  (:222)
```

### 6.2 bladeRF 2.0 Micro 总增益（AD9361）

`bladerf_set_gain(dev, ch, gain_db)` 走总增益 API，范围按频段分段
（`bladerf2_common.h:344-441`）：

| RX 频段 | 增益范围 | 来源 |
|---|---|---|
| 0 – 1.3 GHz | -16 ~ +60 dB | `:354-355` (1-17 .. 77-17) |
| 1.3 – 4.0 GHz | -15 ~ +60 dB | `:370-371` (-4-11 .. 71-11) |
| 4.0 – 6.0 GHz | -12 ~ +60 dB | `:386-387` (-10-2 .. 62-2) |

TX 全频段（`bladerf2_common.h:444-461`）：
- 范围 **-23.75 ~ +66 dB**，步进 **0.25 dB**（step=250 milli-dB, scale=0.001）。

## 7. 数据格式

libbladeRF 原生同步流格式 `BLADERF_FORMAT_SC16_Q11`
（`libbladeRF.h:2141`）：

- **有符号复数 16-bit Q11**，I 在前 Q 在后，小端 `int16_t` 交织
  （`libbladeRF.h:2087-2097`）。
- 量程 `[-2048, 2048)` ↔ `[-1.0, 1.0)`（`libbladeRF.h:2089-2091`）。
- 每复采样 **4 字节**（I16 + Q16）。
- 物理 ADC/DAC 是 **12-bit**：`libbladeRF.h:2144` 明确写
  *"Signed, Complex 16-bit Q11 using a 12-bit Q11 intermediate format"*
  （`BLADERF_FORMAT_SC16_Q11_PACKED` 把 12-bit Q11 打包进 FPGA 流式接口）。
- 通道布局：`BLADERF_RX_X1 = 0`、`BLADERF_TX_X1 = 1`（`libbladeRF.h:715-716`）。
- 通道宏：`BLADERF_CHANNEL_RX(0) = 0`、`BLADERF_CHANNEL_TX(0) = 1`
  （`libbladeRF.h:664,679,694,695`）。

## 8. API 调用链（BladeRFBackend 真实映射）

| Python 方法 | C API | 来源 |
|---|---|---|
| `connect()` | `bladerf_open(&dev, "")` | `libbladeRF.h:211` |
| `disconnect()` | `bladerf_close(dev)` | `libbladeRF.h:225` |
| `set_frequency(f)` | `bladerf_set_frequency(dev, RX0, f)` | `libbladeRF.h:1287` |
| `get_frequency()` | `bladerf_get_frequency(dev, RX0, &f)` | `libbladeRF.h:1300` |
| `set_sample_rate(r)` | `bladerf_set_sample_rate(dev, RX0, r, &actual)` | `libbladeRF.h:1066` |
| `set_bandwidth(bw)` | `bladerf_set_bandwidth(dev, RX0, bw, &actual)` | `libbladeRF.h:1172` |
| `set_gain(db)` | `bladerf_set_gain(dev, RX0, db)` | `libbladeRF.h:838` |
| `set_rxvga1/db` | `bladerf_set_rxvga1(dev, db)` (deprecated, bladeRF 1.0) | `bladeRF1.h:324` |
| `start_rx()` | `bladerf_sync_config(...)` + `bladerf_enable_module(RX, true)` | `libbladeRF.h:2776,2655` |
| `read_samples(n)` | `bladerf_sync_rx(dev, buf, n, NULL, &n_ret)` | `libbladeRF.h:2858` |

返回码（`libbladeRF.h:4489-4513`）：`BLADERF_OK=0`、`BLADERF_ERR_RANGE=-2`、
`BLADERF_ERR_INVAL=-3`、`BLADERF_ERR_TIMEOUT=-6`、`BLADERF_ERR_NODEV=-7`。

## 9. 降级策略（红线）

`mbdsdr_ai/bladerf_params.py:BladeRFBackend` 的降级行为：

1. `ctypes.CDLL` 依次尝试 `libbladeRF.so.2 / .so / .dll / .dylib`，全部失败 →
   `_load_lib()` 返回 `None`，`connect()` 返回 `False`。
2. 有库但没插设备 → `bladerf_open()` 返回 `BLADERF_ERR_NODEV (-7)`，
   `connect()` 返回 `False`。
3. 未连接时所有 `set_*()` / `start_rx()` / `read_samples()` 直接返回 `False` / `None`，
   **绝不伪造"已连接"状态或假 IQ 样本**。
4. `set_rxvga1/rxvga2()` 在 bladeRF 2.0 上找不到符号（`getattr` 抛 `AttributeError`）
   → 优雅返回 `False`，提示"bladeRF 2.0 用总增益 API"。

## 10. 已注册工具

- `bladerf_list_gains`：列出 RXVGA1/RXVGA2/TXVGA1/TXVGA2/LNA 全部分散档表。
- `bladerf_get_freq_range`：返回 RX/TX 频率、采样率、带宽、ADC/DAC 位宽。
- `bladerf_set_params`：把请求的 RXVGA1/RXVGA2/采样率/带宽/频率吸附/校验到合法档。

测试：`tests/bladerf_test.py`（19 条用例，全部通过）。
