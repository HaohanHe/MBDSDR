# rtl_433 真实解码：从 C 源码到 `mbdsdr_ai/rtl433_decoder.py` 的落地笔记

> 本地参考克隆：`repos/rtl_433/`（merbanan/rtl_433）
> 配套实现：`mbdsdr_ai/rtl433_decoder.py`
> 往返验证：`tests/rtl433_roundtrip.py`（15 项全过）
>
> 本文件只记录**这次真读 `.c/.h` 源码后逐行对齐到 Python 的常量与位域**，
> 每条都给 `file:line`。不抄 README，只对齐真实解码链。

---

## 0. 系统级常量

| 项 | 值 | 来源 |
|---|---|---|
| 默认中心频率 | 433.92 MHz（433920000 Hz） | `repos/rtl_433/include/rtl_433.h:14` |
| 默认采样率 | 250 kS/s | `repos/rtl_433/include/rtl_433.h:13` |
| 可选高采样率 | 1 MS/s | `repos/rtl_433/src/rtl_433.c:560` |

---

## 1. 设备注册框架（r_device）

rtl_433 每个协议是一个 `r_device` 静态结构体，字段含义直接搬过来：

来源 `repos/rtl_433/include/r_device.h:59-92`：

```c
typedef struct r_device {
    char const *name;
    unsigned modulation;     // 调制/线编码类型
    float short_width;       // 短符号标称宽度 us
    float long_width;        // 长符号标称宽度 us
    float reset_limit;       // 结束一次发射的最大 gap
    float gap_limit;         // 结束一个包的最大 gap
    float sync_width;        // 同步符号宽度
    float tolerance;         // 允许偏差 us
    int (*decode_fn)(...);   // 解包回调
} r_device;
```

`modulation` 枚举（`r_device.h:24-40`）里本模块用到的：
- `OOK_PULSE_MANCHESTER_ZEROBIT = 3`（Ambient / Oregon）
- `OOK_PULSE_PPM = 5`（Nexus）
- `OOK_PULSE_PWM = 6`（Acurite / LaCrosse）
- `FSK_PULSE_PCM = 16`（Citroen / Elantra TPMS）

Python 里对应 `DeviceConfig` 数据类 + `DeviceDecoder` 基类 + `_REGISTRY` 字典，
等价于 `r_api.c` 里的协议表。

---

## 2. OOK 包络脉冲检测（pulse_detect.c）

来源 `repos/rtl_433/src/pulse_detect.c:293-430`。状态机 `IDLE→PULSE→GAP_START→GAP`。

关键阈值逻辑（`pulse_detect.c:300-304`）：

```c
threshold   = (low_est + min(high_est, MAX)) / 2;
hysteresis  = threshold / 8;          // ±12% 迟滞
```

- 上升沿：`am_n > threshold + hysteresis` 开始一个脉冲（`:309`）
- 下降沿：`am_n < threshold - hysteresis` 结束脉冲（`:339`）
- 低(噪声)电平很慢地滑动估计，`OOK_EST_LOW_RATIO = 1024`（`:27`）
- 高电平较快，`OOK_EST_HIGH_RATIO = 64`（`:26`）
- 最短脉冲 `PD_MIN_PULSE_SAMPLES = 10` 采样点（`include/pulse_data.h:23`），否则当毛刺

Python：`PulseDemodulator.detect()` 逐样本复刻该状态机，输出 `Pulse(pulse_us, gap_us)`。
测试里用合成包络验证提取出的 pulse/gap 宽度在 ±15% 容差内。

---

## 3. 宽度切片成比特（pulse_slicer.c）

三种切片器，全部按 `short_width/long_width/tolerance` 做宽度归类：

| 切片器 | 规则 | 来源 |
|---|---|---|
| PPM | 短 gap→0，长 gap→1 | `pulse_slicer.c:310-318` |
| PWM | 短 pulse→1，长 pulse→0 | `pulse_slicer.c:369+` |
| Manchester-zerobit | 边沿触发：首沿恒 0；距上次边界 > short×1.5 时当前沿=数据跳变，下降沿=1、上升沿=0 | `pulse_slicer.c:477-524` |

默认容差：`tolerance<=0` 时取 `long_width/4`（±25%），来源 `pulse_slicer.c:98-99`。

---

## 4. 位级曼彻斯特解码（bitbuffer.c）

来源 `repos/rtl_433/src/bitbuffer.c:255-280`：

```c
while (ipos < len) {
    bit1 = bit_at(...); bit2 = bit_at(...);
    if (bit1 == bit2) break;   // 非法
    bitbuffer_add_bit(outbuf, bit2);
}
```

即 `'10'→0`，`'01'→1`，相邻两位相同就停。Python `manchester_decode_bits()`
逐对复刻，并提供逆变换 `manchester_encode_bits()` 做往返。

---

## 5. 校验工具（bit_util.c）

| 函数 | 算法 | 来源 |
|---|---|---|
| `crc8` | MSB-first，poly/init 作参 | `bit_util.c:278-294` |
| `lfsr_digest8` | 逐位 LFSR，key 右移、反馈 gen | `bit_util.c`（lfsr_digest8） |
| `reflect4/reflect_nibbles` | 每字节内 4 位 bit-reverse | `bit_util.c:41-53` |

---

## 6. 各设备解码器逐字段

### 6.1 Acurite 5n1（`devices/acurite.c`）
- PWM，short=220 / long=408 / sync=620 us（`acurite.c:2198-2205`）
- 解包前先 `bitbuffer_invert()`（`acurite.c:1349`）
- 8 字节：`sum(b0..b6)&0xFF == b7`（`acurite.c:1285`）；字节 2..6 偶校验（`:1293`）
- 温度帧 msgtype=0x38：`temp_raw=(b4&0x0F)<<7|(b5&0x7F)`，F=(raw-400)×0.1（`:649-650`）
- 雨量/风向帧 msgtype=0x31：`raincounter*0.01 in`，风向索引×22.5°（`:623,626`）

### 6.2 Ambient Weather F007TH（`devices/ambient_weather.c`）
- Manchester-zerobit，short=500 us（`ambient_weather.c:167-172`）
- 6 字节：`lfsr_digest8(b[0..4], gen=0x98, key=0x3e) ^ 0x64 == b[5]`（`:51-56`）
- `temp_f=(temp_raw-400)*0.1`，`temp_raw=((b2&0x0F)<<8)|b3`（`:62-63`）

### 6.3 LaCrosse TX（`devices/lacrosse.c`）
- PWM，short=550(=1) / long=1400(=0) us（`lacrosse.c:187-194`）
- 44 bit = 11 nibble，首字节 0x0A（`:62-71`）
- 校验：`sum(nibble0..9)&0xF == nibble10`（`:103-106`）
- 温度 type=0：`value-50`，value=nib5×10+nib6+nib7×0.1（`:123,138`）

### 6.4 Oregon Scientific THGR122N（`devices/oregon_scientific.c`）
- Manchester-zerobit，short=440 us（`oregon_scientific.c:1071-1076`）
- 解包后先 `reflect_nibbles`（`:233`）
- sensor_id=0x1D20（`:20`）；温度 BCD（`:52-62`），湿度（`:85`）
- sum-of-nibbles 校验，校验字节两 nibble 交换（`:151-178`）

### 6.5 Nexus（`devices/nexus.c`）
- PPM，short=1000(=0 gap) / long=2000(=1 gap) us（`nexus.c:220-226`）
- 5 字节：`b3` 高 nibble 必为 0xF（`:59`）；12bit 有符号温度 `((b1&0x0F)<<8|b2)*0.1`（`:94-95`）
- 湿度 `((b3&0x0F)<<4)|(b4>>4)`（`:96`）

### 6.6 TPMS Citroen（`devices/tpms_citroen.c`）
- FSK，PCM+曼彻斯特，short=52 us（`tpms_citroen.c:135-141`）
- 10 字节：`b[1]^...^b[9]==0`（`:58`）
- 压力 kPa = b6×1.364（`:84`），温度 = b7−50（`:85`）

### 6.7 TPMS Elantra2012（`devices/tpms_elantra2012.c`）
- FSK，PCM+曼彻斯特，short=49 us（`tpms_elantra2012.c:142-148`）
- 8 字节：`crc8(b[0..7], poly=0x07, init=0)==0`（`:63`）
- 压力 = b0+60 kPa（`:69`），温度 = b1−50 °C（`:70`）

---

## 7. 注册到 ToolRegistry

`register_rtl433_tools(registry)` 注册三个工具：
- `rtl433_list_devices` — 列出 7 个已移植设备
- `rtl433_decode_pulses` — 对 hex 载荷按设备解包
- `rtl433_decode_iq` — 基带 IQ → 包络 → 脉冲 → 解码

## 8. 验证

`tests/rtl433_roundtrip.py` 15 项：
- 曼彻斯特编/解码往返一致
- 合成 OOK 包络 → pulse/gap 时长在容差内
- 每个设备一条**能通过真实校验和**的已知帧 → 物理量正确
- 篡改字节 → 校验失败被拒
- ToolRegistry 三个工具可调用
