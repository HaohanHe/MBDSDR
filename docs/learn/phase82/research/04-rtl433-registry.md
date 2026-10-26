# 参考架构研究 04 — rtl_433 解码器注册表（registry）设计

> 研究报告（只读，未修改 `/workspace/MBDSDR` 任何源码）
> 研究对象：`/workspace/reference/rtl_433/`（C，merbanan/rtl_433，200+ 设备解码器）
> 对照对象：`/workspace/MBDSDR/cpp/src/dsp/`（C++17/Qt6，散落解码器，无注册表）
> 生成日期：2026-10-26

---

## 结论 TL;DR

1. **rtl_433 的"注册表"只是一张静态数组**：`src/r_api.c:133-137` 用 `DEVICES` 宏展开生成 `r_device r_devices[]`，启动时 `memcpy` 进 `cfg->devices`，`protocol_num` 按数组下标+1 分配（`src/r_api.c:139-142`）。没有构造函数自注册，没有 `dlopen`，**没有一处运行时插件发现**。
2. **一个解码器 = 一个 `.c` 文件 + 一个 `r_device` 常量 + 头文件里一行 `DECL(name)`**。`r_device` 结构体（`include/r_device.h:59-92`）是全部"元数据 + 生命周期钩子"的唯一载体：`name`/`modulation`/6 个时序参数/`decode_fn`/`create_fn`/`priority`/`disabled`/`fields`/输出与统计钩子。官方文档原话：「**register the decoder with one line, the rest is automatic**」（`docs/CONTRIBUTING.md:13`）。
3. **调度是"全表扫描 + 调制类型分发 + priority 分层短路"**：`run_ook_demods()`/`run_fsk_demods()`（`src/r_api.c:438-550`）遍历已注册列表，按 `r_dev->modulation` 选 `pulse_slicer_*`（`src/pulse_slicer.c`，10 种线编码），位流装进 `bitbuffer_t`，调 `decode_fn`。同一 priority 组内只要有解码器出货（`p_events > 0`）就**不再跑更低 priority**（`src/r_api.c:444`）。
4. **输出是统一 key-value `data_t` + 虚表式 `data_output_t`**：解码器只 `data_make()` 造 KV 树，`decoder_output_data()` 转 `output_fn`（默认 `data_acquired_handler`，`src/r_api.c:632`），统一加 `time`/`model`/`protocol`/`mod`/`rssi` 等元数据后广播给所有输出器（JSON/CSV/KV/MQTT/Influx/syslog/HTTP/trigger，`src/r_api.c:982-1057`）。CSV 列由各解码器声明的 `fields[]` 汇总生成（`src/r_api.c:414-436`）。
5. **失败被三态表达**：`DECODE_ABORT_*`（不是我的协议）vs `DECODE_FAIL_MIC`（是我的但 CRC/校验错）vs `n>0`（成功 n 条）。错误码不是日志，而是**直接进 `decode_fails[5]` 统计桶**（`src/pulse_slicer.c:26-52`），可导出报告。
6. **加一个新设备 = 1 个 `.c` + 1 行 `DECL` + 跑一次 `./maintainer_update.py`**（自动同步 `src/CMakeLists.txt`、README 协议表、`conf/rtl_433.example.conf`）。这是**手工表 + 脚本保同步**，不是 CMake `file(GLOB)`，也不是构造器自注册。
7. **测试不靠单元测试覆盖 200+ 解码器**，而是三层：`-y "{25}fb2dd58"` 比特串回放验证（`src/rtl_433.c:1693`）、`-r file.cu8` IQ 回放、`-S unknown` 抓样回归。`tests/` 里只有基础构件测试 + flex 解码器 smoke test。
8. **给 MBDSDR 的核心判断**：MBDSDR 的 `dsp/` 解码器**feed/take 接口已经比 rtl_433 的 `decode_fn` 更现代**（强类型、无副作用、可单测），真正缺的是 **① 一张 `DecoderRegistry` 真相表 ② 统一 KV 输出模型 ③ 失败三态协议 ④ UI 从注册表自动建页签**。不需要照抄 rtl_433 的 C 宏表，但必须抄它的"一表管全部"纪律。

---

## 1. 注册表（registry）机制

### 1.1 注册表长什么样：一张宏展开的静态数组

rtl_433 没有 `std::map`、没有 `dlopen`、没有构造器自注册。全部注册表就是**一个宏 `DEVICES` 展开成逗号分隔的 `r_device` 常量名列表**：

`include/rtl_433_devices.h:10-137`（表头，节选）：

```c
#define DEVICES \
    DECL(silvercrest) \
    DECL(rubicson) \
    DECL(prologue) \
    ...
    DECL(neptune_r900bcd) \
    DECL(olympia_9571) \
    /* Add new decoders here. */
```

表尾声明 + 数组生成（`include/rtl_433_devices.h:397-402` + `src/r_api.c:133-137`）：

```c
// include/rtl_433_devices.h:397-402
#define DECL(name) extern r_device const name;
DECL(flex_decoder)
DEVICES
#undef DECL

// src/r_api.c:133-137  —— 同一个 DEVICES 宏，换个 DECL 定义就变成数组初始化器
r_device r_devices[] = {
#define DECL(name) name,
        DEVICES
#undef DECL
};
```

启动时把它搬进配置（`src/r_api.c:139-147`）：

```c
cfg->num_r_devices = sizeof(r_devices) / sizeof(*r_devices);
for (unsigned i = 0; i < cfg->num_r_devices; i++) {
    r_devices[i].protocol_num = i + 1;   // 数组下标+1 = 协议号
}
cfg->devices = malloc(sizeof(r_devices));
memcpy(cfg->devices, r_devices, sizeof(r_devices));
```

**要点**：
- 同一份 `DEVICES` 宏被**两处复用**（`extern` 声明 / 数组初始化），这是 C 里典型的 X-macro 手法，避免"声明了没注册 / 注册了没声明"。
- `protocol_num` **是数组下标**，不是自增计数器。因此**删/插一个解码器会改变后续所有协议号**——这也是为什么 `include/rtl_433_devices.h` 里保留了多个 `DECL(new_template)` 占位（`include/rtl_433_devices.h:15,19,37,38,...`），**协议号是对外稳定契约**，不能随意压缩。
- `flex_decoder` 单独处理（`include/rtl_433_devices.h:398`），因为它不进 `DEVICES` 主表（它是通用解码器，靠 `create_fn` 动态构造实例）。

### 1.2 一个解码器的最小声明：`r_device` 结构体

`include/r_device.h:59-92`，全文 33 行字段（这是整个框架的"宪法"）：

```c
typedef struct r_device {
    unsigned protocol_num;   // 运行时赋值：数组下标+1（src/r_api.c:141）

    /* 信息：解码器自己提供的静态描述 */
    char const *name;
    unsigned modulation;     // OOK_PULSE_* / FSK_PULSE_* 枚举（r_device.h:24-40）
    float short_width;       // us，短符号标称宽度
    float long_width;        // us，长符号标称宽度
    float reset_limit;       // us，结束一次传输的最大 gap
    float gap_limit;         // us，结束一个 packet(row) 的最大 gap
    float sync_width;        // us，同步符号宽度
    float tolerance;         // us，允许偏差
    int (*decode_fn)(struct r_device *decoder, struct bitbuffer *bitbuffer);
    struct r_device *(*create_fn)(char const *args);  // 可选：参数化实例
    unsigned priority;       // 0=默认；>0 更靠后跑（r_device.h:73）
    unsigned disabled;       // 0启用/1默认关/2禁用/3禁用且隐藏（r_device.h:74）
    char const *const *fields;  // 输出字段名表，CSV 用（r_device.h:75）

    /* 公共运行时字段 */
    int verbose;
    int verbose_bits;
    void (*log_fn)(struct r_device *decoder, int level, struct data *data);
    void (*output_fn)(struct r_device *decoder, struct data *data);

    /* 结果统计 */
    unsigned decode_events, decode_ok, decode_messages;
    unsigned decode_fails[5];   // 对应 DECODE_* 负返回码（r_device.h:87）

    void *decode_ctx;  // flex 解码器私有
    void *output_ctx;  // 输出回调私有（指向 r_cfg_t）
} r_device;
```

**真实例子**（`src/devices/nexus.c:219-229`）：

```c
r_device const nexus = {
    .name        = "Nexus, FreeTec NC-7345, ... temperature/humidity sensor",
    .modulation  = OOK_PULSE_PPM,
    .short_width = 1000,
    .long_width  = 2000,
    .gap_limit   = 3000,
    .reset_limit = 5000,
    .decode_fn   = &nexus_decode,
    .priority    = 10, // Eliminate false positives by letting Rubicson go earlier
    .fields      = output_fields,
};
```

字段声明（`src/devices/nexus.c:208-217`）：

```c
static char const *const output_fields[] = {
    "model", "id", "channel", "battery_ok",
    "temperature_C", "humidity", "test",
    NULL,
};
```

`priority = 10` 的注释直白到可爱：**"让 Rubicson 先跑，消除假阳性"**——这就是 priority 字段存在的唯一理由：**同 family 的协议会撞帧，靠 priority 决定谁先认领**（详见 §1.5）。

### 1.3 注册表怎么被扫描/遍历：三层筛选

rtl_433 对 200+ 解码器不是"每个都盲跑"，而是三层：

| 层 | 筛什么 | 位置 |
|---|---|---|
| **L1 注册过滤** | `disabled` 级别：`-R n` 只注册指定协议，`-R 0` 清空，`-R -n` 反选 | `src/rtl_433.c:802-853`、`src/r_api.c:294-302` |
| **L2 调制分发** | `modulation` 决定走哪个 `pulse_slicer_*`，OOK 包不跑 FSK 解码器（反之亦然） | `src/r_api.c:438-550` |
| **L3 priority 短路** | 同 priority 组内出货即停，低 priority 不再跑 | `src/r_api.c:444` |

L1 注册（`src/r_api.c:294-302`）：

```c
void register_all_protocols(r_cfg_t *cfg, unsigned disabled) {
    for (int i = 0; i < cfg->num_r_devices; i++) {
        if (cfg->devices[i].disabled <= disabled) {
            register_protocol(cfg, &cfg->devices[i], NULL);
        }
    }
}
```

注意 `register_protocol()`（`src/r_api.c:235-275`）**不是把常量塞进表，而是复制一份实例**（`*p = *r_dev; // copy`，`src/r_api.c:260`）：

```c
if (r_dev->create_fn) {
    p = r_dev->create_fn(arg);   // flex_decoder 走这里，按 -X spec 造实例
} else {
    p = malloc(sizeof(*p));
    *p = *r_dev;                 // 常量模板 -> 可变实例副本
}
p->log_fn   = log_device_handler;    // 统一接上输出/日志（src/r_api.c:265-268）
p->output_fn = data_acquired_handler;
p->output_ctx = cfg;
list_push(&cfg->demod->r_devs, p);
```

**这是"模板 vs 实例"的关键设计**：`r_device const nexus` 是只读模板（可被多实例复用），运行时每个启用的协议各有一个 `r_device*` 实例挂在 `cfg->demod->r_devs` 列表上，实例里才有 `verbose`/`decode_ctx`/统计计数。`free_protocol()`（`src/r_api.c:277-281`）只 free 实例不碰模板。

L2 调制分发（`src/r_api.c:438-500`）：

```c
int run_ook_demods(list_t *r_devs, pulse_data_t *pulse_data) {
    int p_events = 0;
    unsigned next_priority = 0;
    for (unsigned priority = 0; !p_events && priority < UINT_MAX; priority = next_priority) {
        next_priority = UINT_MAX;
        for (void **iter = r_devs->elems; iter && *iter; ++iter) {
            r_device *r_dev = *iter;
            if (r_dev->priority > priority && r_dev->priority < next_priority)
                next_priority = r_dev->priority;
            if (r_dev->priority != priority) continue;

            switch (r_dev->modulation) {
            case OOK_PULSE_PCM:    p_events += pulse_slicer_pcm(pulse_data, r_dev); break;
            case OOK_PULSE_PPM:    p_events += pulse_slicer_ppm(pulse_data, r_dev); break;
            case OOK_PULSE_PWM:    p_events += pulse_slicer_pwm(pulse_data, r_dev); break;
            case OOK_PULSE_MANCHESTER_ZEROBIT: ... break;
            case OOK_PULSE_DMC:    p_events += pulse_slicer_dmc(pulse_data, r_dev); break;
            // ... 10 种 OOK 线编码
            case FSK_PULSE_PCM: case FSK_PULSE_PWM: case FSK_PULSE_MANCHESTER_ZEROBIT:
                break;   // FSK 解码器在 OOK 通道里直接跳过
            default:
                fprintf(stderr, "Unknown modulation %u in protocol!\n", r_dev->modulation);
            }
        }
    }
    return p_events;
}
```

`run_fsk_demods()`（`src/r_api.c:502-550`）是镜像版本，OOK 分支变 `break`，FSK 分支调 `pulse_slicer_*`。**两个函数结构完全对称**——调制类型是 OOK/FSK 二分，线编码是子类型。

**重要结论**：rtl_433 **不按协议族、不按位宽做索引**。它按**调制类型**分两路（OOK/FSK），每路内**线性扫**全表，靠 `modulation` switch 只选对的 slicer，靠 `priority` 做假阳性仲裁。200+ 解码器的性能完全靠"每包只跑一次"的 pulse_detect 分段 + slicer 内 `bitbuffer` 行检查快速拒绝。

### 1.4 线编码到 bitbuffer：`pulse_slicer_*`

`include/pulse_slicer.h` 声明 10 个 slicer（`pulse_slicer.h:38-184`）：

| 函数 | 线编码 | 声明行 |
|---|---|---|
| `pulse_slicer_pcm` | OOK/FSK NRZ（PCM/RZ） | `include/pulse_slicer.h:38` |
| `pulse_slicer_ppm` | Pulse Position | `:56` |
| `pulse_slicer_pwm` | Pulse Width | `:75` |
| `pulse_slicer_manchester_zerobit` | Manchester zero-bit | `:93` |
| `pulse_slicer_dmc` | Differential Manchester | `:116` |
| `pulse_slicer_piwm_raw` / `_dc` | PIWM | `:130` / `:144` |
| `pulse_slicer_nrzs` | NRZS | `:146` |
| `pulse_slicer_osv1` | Oregon Scientific v1 | `:148` |
| `pulse_slicer_rzi` | RZI | `:173` |
| `pulse_slicer_string` | 测试用：从 `"{25}fb2dd58"` 造 bitbuffer | `:184` |

所有 slicer 共用 `account_event()`（`src/pulse_slicer.c:26-66`）：

```c
static int account_event(r_device *device, bitbuffer_t *bits, char const *demod_name) {
    int ret = 0;
    if (device->decode_fn) {
        ret = device->decode_fn(device, bits);
    }
    device->decode_events += 1;
    if (ret > 0) { device->decode_ok += 1; device->decode_messages += ret; }
    else if (ret >= DECODE_FAIL_SANITY) { device->decode_fails[-ret] += 1; ret = 0; }
    else {
        print_logf(LOG_ERROR, demod_name, "Decoder \"%s\" gave invalid return value %d: notify maintainer", device->name, ret);
        exit(1);   // 解码器返回非法值 = 框架 bug，直接崩
    }
    // ... debug printout ...
    bitbuffer_clear(bits);  // 每次跑完清空，slicer 从干净状态开始下一条
    return ret;
}
```

**`exit(1)` 这个细节很重要**：框架把"解码器返回值越界"当成**程序员错误**，不是运行时可恢复错误。这就是 `decode_return_codes` 枚举被严格定义（`include/r_device.h:45-53`）的原因。

### 1.5 `priority` 与 `disabled`：两种"元数据治理"

**`priority`（`include/r_device.h:73`）**——只有一处用（`src/r_api.c:444` 的 `!p_events` 短路）：

```
priority=0 组先跑 → 有出货就停
priority=5 组（govee_h5112，src/devices/govee_h5112.c:449）
priority=10 组（nexus/acurite/prologue/baldr_rain 等 14+ 个）
```

实测 14 处 `priority = 10`，全部注释都是同一句话的不同变体：**"消除假阳性，让 X 先跑"**（`src/devices/nexus.c:227`、`src/devices/acurite.c:2139`、`src/devices/prologue.c:110`）。这说明**协议撞帧是 rtl_433 的常态**，`priority` 是唯一的仲裁手段。

**`disabled` 四档（`include/r_device.h:74`）**：

| 值 | 含义 | CLI 行为 |
|---|---|---|
| 0 | 默认启用 | 启动自动注册 |
| 1 | 默认禁用 | `-R n` 显式开启 |
| 2 | 禁用 | 只能 `-R n` |
| 3 | 禁用且**隐藏** | `-R n` 都不能开（`src/rtl_433.c:828-830` 检查） |

`new_template` 是 `disabled = 3`（`src/devices/new_template.c:265`），因为它是模板不是真设备。`help_protocols()` 打印时 `disabled <= 2` 才显示（`src/rtl_433.c:180`），且带 `*` 标注默认禁用。

### 1.6 `create_fn` + `flex_decoder`：参数化解码器

`flex_decoder`（`src/devices/flex.c:870-875`）是唯一不写死协议的"元解码器"：

```c
r_device const flex_decoder = {
    .name      = "General purpose decoder",
    .create_fn = &flex_create_device,   // 按 -X spec 造实例
    .decode_fn = &flex_callback,
    .fields    = output_fields,
};
```

`-X 'n=name,m=FSK_PCM,s=10,l=10,r=10240,get=@20:{12}0x7f:status:...'` 里的 spec 字符串被 `flex_create_device()` 解析（`src/devices/flex.c:666`），产出一个**匿名 `r_device` 实例**（`dev->create_fn = flex_create_device;`，`src/devices/flex.c:685`）——所以 flex 实例可以再次实例化。`match_bits` 位匹配（`src/devices/flex.c:103`）+ `bitbuffer_search()`（`src/devices/flex.c:201`）实现"位模板匹配"。

**这是 rtl_433 对"我不想写 C 也能加协议"的答案**：`conf/rtl_433.example.conf` 里塞满 flex 配置，`docs/CONTRIBUTING.md:75-90` 教用户先 `-S unknown` 抓样，再 `-A` 分析脉冲，最后写 flex 或 C 解码器。

---

## 2. 解码器生命周期

### 2.1 输入链路：IQ → 脉冲 → bitbuffer → decode_fn

全链路（`src/r_flow.c:104-302`）：

```
SDR IQ (u8 interleaved)  →  push_sdr_flow()            src/r_flow.c:104
   ├─ magnitude_est_cu8() / envelope_detect() → am_buf (int16 包络)
   ├─ baseband_demod_FM()                     → fm_buf (int16 频偏，FSK 用)
   └─ pulse_detect_package()                  → pulse_data_t / fsk_pulse_data
         src/pulse_detect.c:199  返回 PULSE_DATA_OOK=1 / PULSE_DATA_FSK=2
   ↓
if (PULSE_DATA_OOK)  run_ook_demods(&demod->r_devs, &demod->pulse_data)   src/r_flow.c:259
else (PULSE_DATA_FSK) run_fsk_demods(&demod->r_devs, &demod->fsk_pulse_data) src/r_flow.c:302
   ↓
pulse_slicer_*()  → bitbuffer_t  →  account_event() → device->decode_fn(device, bits)
                                                    src/pulse_slicer.c:31
```

`pulse_data_t`（`include/pulse_data.h:30-50`）是**脉冲级中间表示**：

```c
typedef struct pulse_data {
    uint64_t offset; uint32_t sample_rate;
    unsigned num_pulses;
    int pulse[PD_MAX_PULSES];   // 高电平宽度（采样数），PD_MAX_PULSES=1200
    int gap[PD_MAX_PULSES];     // 低电平宽度
    int fsk_f1_est, fsk_f2_est; // FSK 两频估计
    float freq1_hz, freq2_hz, centerfreq_hz;
    float rssi_db, snr_db, noise_db;
} pulse_data_t;
```

**解码器看不到 IQ，也看不到脉冲**——它只看到 `bitbuffer_t`（`include/bitbuffer.h:33-40`）：

```c
typedef struct bitbuffer {
    uint16_t num_rows;
    uint16_t bits_per_row[BITBUF_ROWS];   // BITBUF_ROWS=50
    uint16_t syncs_before_row[BITBUF_ROWS];
    bitarray_t bb;                        // uint8_t[50][128] 位数据
} bitbuffer_t;
```

**这是极其重要的契约**：解码器只依赖 `bitbuffer_t` + `r_device`，完全不知道采样率、脉冲检测、SDR 硬件。这让 200+ 解码器可以**独立测试**（`pulse_slicer_string()` 从 `"{25}fb2dd58"` 直接造 bitbuffer，`include/pulse_slicer.h:184`）。

**位对齐工具箱**（`include/bitbuffer.h`）：`bitbuffer_search()`（`:112`）前导码搜索、`bitbuffer_find_repeated_row()`（`:150`）重发帧对齐、`bitbuffer_manchester_decode()`（`:123`）、`bitbuffer_invert()`（`:59`）、`bitbuffer_extract_bytes()`（`:55`）、`bitbuffer_count_repeats()`（`:145`）。

### 2.2 解码器内部：三步拒绝法

`src/devices/new_template.c:88-230` 是官方模板，注释里写死了**推荐顺序**：

```c
static int new_template_decode(r_device *decoder, bitbuffer_t *bitbuffer) {
    // 1. 信封检查：长度 / magic header
    if (bitbuffer->bits_per_row[r] < 68) continue;
    if (b[0] != 0x42) continue;              // magic header not found

    // 2. 重发检查（重复帧）
    r = bitbuffer_find_repeated_row(bitbuffer, 3, 68);
    if (r < 0 || bitbuffer->bits_per_row[r] > 68 + 16)
        return DECODE_ABORT_LENGTH;          // 不是我的协议

    // 3. 完整性检查：parity / checksum / CRC
    int chk = crc8(b, 7, 0x07, 0x00);
    if (chk != 0) return DECODE_FAIL_MIC;    // 是我的，但 CRC 错

    // 4. 才解析字段
    data_t *data = data_make(
        "model", "", DATA_STRING, "New-Template",
        "id",    "", DATA_INT,    sensor_id,
        "data",  "", DATA_INT,    value,
        "mic",   "", DATA_STRING, "CHECKSUM",
        NULL);
    decoder_output_data(decoder, data);
    return 1;
}
```

模板注释（`src/devices/new_template.c:95-100`）明确"**尽早拒绝**"：

> Validate message and reject invalid messages as early as possible before attempting to parse data.

### 2.3 输出链路：`data_t` → 输出器广播

`include/data.h:45-78`：`data_t` 是**链表 KV 树**，值支持 7 种类型（`DATA_DATA/INT/DOUBLE/STRING/ARRAY/COUNT/FORMAT/COND`，`include/data.h:45-53`），`data_value_t` 是 `union { double v_dbl; void *v_ptr; int v_int; }`（`:64-68`）。`data_make()`（`include/data.h:81`）是变参构造器，`DATA_COND` 让"条件字段"能就地跳过——解码器不用写 if。

**输出虚表**（`include/data.h:181-192`）：9 个函数指针 = 全部格式差异（`print_data/print_array/print_string/print_double/print_int/output_start/output_print/output_reopen/output_free`）。JSON/CSV/KV/MQTT/Influx/syslog/HTTP/trigger/log 都只实现这张表。新增输出格式不用改解码器。

**广播点**（`src/r_api.c:632-845` `data_acquired_handler`）：

```c
void data_acquired_handler(r_device *r_dev, data_t *data) {
    r_cfg_t *cfg = r_dev->output_ctx;
    // 1. 单位换算（SI / customary，按字段名后缀 _F/_mm_h/_inHg 自动转换）
    // 2. 前置元数据：description / protocol / mod / freq / rssi / snr / time
    if (cfg->report_protocol && r_dev->protocol_num)
        data = data_prepend(data, data_int(NULL, "protocol", ..., r_dev->protocol_num));
    // 3. 应用 data_tag
    // 4. 广播给所有输出器
    for (size_t i = 0; i < cfg->output_handler.len; ++i) {
        data_output_t *output = cfg->output_handler.elems[i];
        data_output_print(output, data);
    }
    data_free(data);
}
```

**CSV 列自动汇总**（`src/r_api.c:414-436` `determine_csv_fields`）：

```c
for (iter = r_devs->elems; iter && *iter; ++iter) {
    r_device *r_dev = *iter;
    if (r_dev->fields) list_push_all(&field_list, (void **)r_dev->fields);
    else fprintf(stderr, "rtl_433: warning: %u \"%s\" does not support CSV output\n", ...);
}
```

`r_device.fields` 是**唯一**让 CSV 知道列顺序/列名的地方。解码器不声明 `fields` = 不能输出 CSV（warning）。**这也是 schema 纪律**：字段名必须在 `output_fields[]` 登记，否则 debug 构建会告警（`src/r_api.c:641-650`）。

### 2.4 失败处理：三态返回码 + 统计桶

`include/r_device.h:42-53`：

```c
/** Decoders should return n>0 for n packets successfully decoded,
    an ABORT code if the bitbuffer is no applicable,
    or a FAIL code if the message is malformed. */
enum decode_return_codes {
    DECODE_FAIL_OTHER   = 0,   // legacy, do not use
    DECODE_ABORT_LENGTH = -1,  // 行数/行长度不对 → 不是我的
    DECODE_ABORT_EARLY  = -2,  // 前导码/头不对 → 不是我的
    DECODE_FAIL_MIC     = -3,  // CRC/checksum/parity 失败 → 是我的但坏
    DECODE_FAIL_SANITY  = -4,  // 字段值不合逻辑
};
```

`account_event()`（`src/pulse_slicer.c:32-46`）把返回码折进统计：

```c
if (ret > 0)                            { decode_ok += 1; decode_messages += ret; }
else if (ret >= DECODE_FAIL_SANITY)     { decode_fails[-ret] += 1; ret = 0; }
else                                    { exit(1); }   // 非法返回值 = 框架 bug
```

`decode_fails[5]` 索引 = `-ret`，0 桶对应 `DECODE_FAIL_OTHER`（已废弃）。

**报告导出**（`src/r_api.c:846-880` `create_report_data`）：`-M level` 可导出每个解码器的 `events/ok/messages/fail_*` 计数：

```c
data = data_make(
    "device", "", DATA_INT, r_dev->protocol_num,
    "name",   "", DATA_STRING, r_dev->name,
    "events", "", DATA_INT, r_dev->decode_events,
    "ok",     "", DATA_INT, r_dev->decode_ok,
    "messages","", DATA_INT, r_dev->decode_messages,
    NULL);
if (r_dev->decode_fails[-DECODE_FAIL_MIC])
    data = data_int(data, "fail_mic", "", NULL, r_dev->decode_fails[-DECODE_FAIL_MIC]);
```

**这个设计的精髓**：「不是我的协议」不打日志、不污染输出，只进 `abort_length/abort_early` 计数器；「是我的但坏」进 `fail_mic` 计数器。200+ 解码器每包都跑，**绝大多数返回 ABORT**，框架必须保证这个路径零开销、零日志。

---

## 3. 可扩展性

### 3.1 加一个新设备：改几处代码？

`docs/CONTRIBUTING.md:12-33` 官方步骤（**原话**："You need to know some C and register the decoder with one line, the rest is automatic"）：

1. `cp src/devices/new_template.c src/devices/my_device.c` —— 拷模板
2. 改 `r_device const new_template` → `r_device const my_device`，`.disabled = 3` → `.disabled = 0`
3. `include/rtl_433_devices.h` 加一行 `DECL(my_device) \`  ← **唯一"注册"动作**
4. `git add` 新文件
5. **`./maintainer_update.py`** —— 自动同步 `src/CMakeLists.txt`、README 协议表、`conf/rtl_433.example.conf`
6. 再 `git add src/CMakeLists.txt`
7. 编码、测试、再跑一次 `maintainer_update.py`、提交

**答案：手工改 2 处（新 `.c` 文件 + 1 行 `DECL`），其余 3 处（CMakeLists/README/conf）由 `maintainer_update.py` 自动同步**。

`maintainer_update.py` 的机制（`maintainer_update.py:103-173`）：

```python
src_files = sorted(glob.glob('*.c'))
device_files = sorted(glob.glob('devices/*.c'))       # 扫描 devices/*.c
r_devices = [grep_lines(r'(?m)^r_device\s*(.*?)\s*=.*', ...)]  # grep r_device 声明
# count r_devices, correct for 'new_template' being used six times
r_devices_used = len(r_devices) + 5
# src/CMakeLists.txt   ← 重写设备源文件列表
# include/rtl_433_devices.h  ← 校验 DEVICES 表与 r_device 声明一致
# README.md            ← 重写协议表（从 rtl_433 -R help 抓）
# conf/rtl_433.example.conf ← 重写 flex 设备段
```

**关键点**：rtl_433 **选了"手工表 + 脚本保同步"**，不是：
- ❌ CMake `file(GLOB)` —— 会丢依赖跟踪、无法排序稳定
- ❌ 构造器自注册（`__attribute__((constructor))`）—— 顺序不可控，`protocol_num` 会漂
- ❌ `dlopen` 插件 —— 全仓 grep 无 `dlopen`/`LoadLibrary`/`dlsym`（实测 0 命中）

**为什么不用动态加载？** 报告判断（源码无明确注释，从设计反推）：
1. **`protocol_num` 是数组下标**（`src/r_api.c:141`），动态加载会导致协议号跨机器/跨版本漂移，而 `-R n` 是对外 CLI 契约
2. **嵌入式/交叉编译场景多**（rtl_433 跑在路由器、树莓派、Docker），静态链接更省事
3. **200+ 解码器都是同一 GPL 仓库**，没有第三方二进制插件的生态需求
4. **flex_decoder 已经覆盖"不写 C 加协议"** 的需求（`src/devices/flex.c`）

对比：OnAir 选了 `dlopen` + `manifest.json` + 稳定模式号 1000+（详见 `01-onair.md` §3.4）——因为它的模式表是**用户可扩展的调谐预设**，不是协议解码器。

### 3.2 静态链接的代价与对策

代价：**改一个解码器要重编全量**（383 个 `r_device const`，`src/devices/*.c` 331 个文件）。对策：
- `maintainer_update.py` 保证 `src/CMakeLists.txt` 永远与 `devices/*.c` 同步，重编是机械的
- `-y` 测试串不依赖 SDR，秒级验证（`src/rtl_433.c:1693`）
- flex_decoder 让"试协议"不用编译

### 3.3 测试：200+ 解码器怎么测？

**`tests/` 目录实测**（`tests/CMakeLists.txt`）：

| 测试 | 测什么 | 行 |
|---|---|---|
| `data-test` | `data_t` 构造/序列化 | `tests/CMakeLists.txt:5-8` |
| `baseband-test` | 基带滤波 | `:10-15` |
| `test_bitbuffer` / `test_fileformat` / `test_optparse` / `test_bit_util` / `test_r_util` / `test_abuf` | 基础构件单元测试（`add_definitions(-D_TEST)` 编进源文件） | `:22-28` |
| `rtl_433_help` / `flex_decoder_help` | smoke | `:32-33` |
| `flex_decoder` × 3 | flex 解码器空文件回放 | `:34-53` |
| `http_server_*` × 3 | HTTP/WS API 集成（shell 脚本） | `:58-90` |
| `style-check` | 代码风格 | `:97-104` |
| `clang-analyzer`（可选） | 静态分析 | `:108-125` |

**没有 200+ 个解码器单测**。真正的验证靠三条 CLI 路径：

1. **`-y "{25}fb2dd58"`**（`src/rtl_433.c:148, 1693`）—— 比特串直接喂 `pulse_slicer_string()`，跳过 SDR/脉冲检测，秒级验证 `decode_fn`。`tests/CMakeLists.txt:38-53` 用它做 flex 冒烟。
2. **`-r file.cu8`** —— IQ 文件回放全链路。`docs/CONTRIBUTING.md:75-90` 教用户 `-S unknown` 抓样存 `gNNN_FFFM_RRRk.cu8`，`-A` 分析脉冲，`-r` 回放验证。
3. **`-S unknown` + 手动对照表** —— 社区贡献的样例库（不在本仓，在 issue/PR 附件）。

**`-y` 的实现**（`src/rtl_433.c:1685-1696`）：

```c
if (cfg->test_data) {
    if (rfraw_check(cfg->test_data)) {
        pulse_data_t pulse_data = {0};
        rfraw_parse(&pulse_data, cfg->test_data);
        if (!pulse_data.fsk_f2_est) r += run_ook_demods(&demod->r_devs, &pulse_data);
        else                        r += run_fsk_demods(&demod->r_devs, &pulse_data);
    } else
    for (iter = demod->r_devs.elems; iter && *iter; ++iter) {
        r += pulse_slicer_string(cfg->test_data, r_dev);
    }
    exit(!r);   // 有解码成功 = exit 0，全失败 = exit 1
}
```

**`exit(!r)` 是 CI 友好设计**：`-y` 可直接做回归断言。`tests/CMakeLists.txt:34-53` 的 flex 测试就靠这个退出码。

**MBDSDR 现有测试对照**：`tests/golden/cross_decode_vectors.json`（跨语言 golden，LLR/IQ base64）+ `tests/*_roundtrip.py` 已经是**比 rtl_433 更强的测试基建**——rtl_433 没有 golden JSON，只有 CLI 退出码。

### 3.4 配置文件：协议也可以"运行时启用"

`src/rtl_433.c:440-479`：

```c
static void parse_conf_text(r_cfg_t *cfg, char *conf);   // :440
static void parse_conf_file(r_cfg_t *cfg, char const *path);  // :455
static void parse_conf_try_default_files(r_cfg_t *cfg);   // :466
```

`rtl_433.conf` 在 `./`、XDG_CONFIG_HOME、`/etc` 等处搜索（`src/rtl_433.c:110-114`）。配置文件里可以写 `-R n`、`-X spec`、`-F json:...` 等任意命令行选项。**协议启用状态是配置，不是编译期常量**。

---

## 4. 与 MBDSDR 现状对照表

**MBDSDR 实测数字**（脚本数出来，不是估的）：

| 项 | 数字 | 位置 |
|---|---|---|
| `dsp/` 下解码器/检测器文件 | **11 个**（ctcss/cdcss/CW/FT8/POCSAG/ACARS/NAVTEX/ADS-B/m17/VOR/LRPT）+ RDS/APT/SSDV/SSTV | `cpp/src/dsp/*_decoder.*` 等 |
| 右栏硬编码页签 | **14 个 `rightTabs_->addTab`** | `cpp/src/ui/main_window.cpp:1392-2595` |
| 解调模式 combo 项 | **15 个**（AM/NFM/WFM/USB/LSB/CW/BPSK/QPSK/ADS-B/POCSAG/m17/VOR/ACARS/NAVTEX/RAW） | `cpp/src/ui/main_window.cpp:666-667` |
| 解调模式分发分支 | **9 个 if/else if**（字符串比较） | `cpp/src/dsp/vfo_manager.cpp:261-280` |
| 解码器输出类型 | 各自定义（`PocsagMessage`/`AircraftInfo`/`AcarsPacket`/`NavtexMessage`/`VorResult`/`Ft8Decoded`...） | `cpp/src/dsp/*.h` |
| 统一 KV 输出模型 | **无** | — |
| 解码器注册表 | **无** | — |

| 维度 | rtl_433 | MBDSDR 现状 | 差距判断 |
|---|---|---|---|
| **注册表形态** | `DEVICES` X-macro 静态数组（`src/r_api.c:133-137`） | 无表，硬编码 `if (mode == "AM")...`（`vfo_manager.cpp:261-280`）+ 14 个 `addTab`（`main_window.cpp:1392-2595`） | **MBDSDR 最大缺口**（建议 1） |
| **解码器元数据** | `r_device` 33 字段一表（`include/r_device.h:59-92`） | 各解码器类独立，无统一 `name/modulation/timing/fields` | **缺**（建议 1） |
| **输入抽象** | `bitbuffer_t`（位流），解码器不知 IQ/采样率 | 各解码器 `feed()` 自定参数：`feed(iq)` / `feed(audio)` / `feed(bits)`（`adsb_decoder.h:57`、`cw_decoder.h:31`、`pocsag_decoder.h`） | **MBDSDR 更现代**（强类型），但缺统一「输入端口」概念（建议 2） |
| **输出模型** | `data_t` KV 树 + `data_output_t` 虚表 9 函数（`include/data.h:181-192`） | 各自 struct + 各自信号（`pocsagMessagesChanged` 等） | **缺统一 KV**（建议 3） |
| **失败协议** | `DECODE_ABORT_*` / `DECODE_FAIL_MIC` / `n>0` 三态 + `decode_fails[5]` 统计（`include/r_device.h:45-53`） | 各解码器自行返回 bool/optional/vector，无统一统计 | **缺**（建议 4） |
| **可扩展性** | 1 文件 + 1 行 `DECL` + `maintainer_update.py` | 加一个解码器要改：`.h/.cpp` + `vfo_manager.h` 成员 + `vfo_manager.cpp` 分发 + `main_window.cpp` 页签 + `main_window.cpp` combo + engine 信号 + agent_tools | **MBDSDR 加一个解码器要改 6-8 处** |
| **协议号稳定性** | `protocol_num` = 数组下标+1，删插会漂 | 无协议号概念 | 借鉴（建议 1：用稳定字符串 ID） |
| **假阳性仲裁** | `priority` 字段 + 同组短路（`src/r_api.c:444`） | 无（每个模式独占通道，不撞帧） | **MBDSDR 不需要**（模式即隔离） |
| **动态插件** | 无 `dlopen`，静态链接 | 无插件缝 | 一致（都不做插件） |
| **测试** | `-y` 比特串 + `-r` IQ 回放 + `-S` 抓样 | `tests/golden/cross_decode_vectors.json` + `*_roundtrip.py` | **MBDSDR 领先**（golden 结构化） |
| **UI 自动化** | 无 UI（CLI/HTTP） | 14 个页签手工 `addTab` | **MBDSDR 缺注册表驱动 UI**（建议 5） |
| **单位换算** | 字段名后缀自动换算（`_F`→`_C` 等，`src/r_api.c:652-800`） | 各面板自己 format | 可借鉴（建议 3） |
| **CSV schema** | `r_device.fields[]` 汇总成列（`src/r_api.c:414-436`） | 无 CSV 导出 | 可借鉴 |

**核心差异总结**：rtl_433 是"**同质解码器海**"（200+ 个都是 OOK/FSK 传感器，输入输出高度统一），所以一张 `r_device` 表 + 一个 `bitbuffer_t` 就够；MBDSDR 是"**异质解码器拼盘**"（POCSAG 位流 / FT8 音频 LLR / ADS-B IQ / VOR 音频相位 / APT 图像），输入输出各不相同。**照抄 rtl_433 的 `r_device` 结构体会削足适履**，但照抄它的"**一表管全部 + 统一输出 + 三态失败**"纪律是必须的。

---

## 5. 给 MBDSDR 的落地建议（按性价比排序）

> 前 4 条优先做。每条给"改哪个文件、加什么结构"。

### ★1 建 `DecoderRegistry`：一张真相表（**性价比最高**）

- **改**：新建 `cpp/src/dsp/decoder_registry.{h,cpp}`；改 `cpp/src/dsp/vfo_manager.cpp:261-280`、`cpp/src/ui/main_window.cpp:666-667`
- **加**：
  ```cpp
  struct DecoderDescriptor {
      QString id;              // 稳定字符串 ID："pocsag" / "adsb" / "ft8" / "m17" ...
      QString displayName;     // UI 显示名："寻呼" / "ADS-B" / "FT8"
      QString modeKey;         // 解调模式键（与 demodCombo 对齐）："POCSAG" / "ADS-B"
      QString group;           // 右栏页签分组："解码" / "观测" / "系统"
      int     priority = 0;    // 同分组内排序（对齐 rtl_433 priority 语义）
      bool    defaultEnabled = true;
      QStringList outputFields; // 声明输出字段（CSV/导出用，对齐 r_device.fields）
      std::function<std::unique_ptr<IDecoder>()> factory;
  };
  class DecoderRegistry {
      static const QVector<DecoderDescriptor>& all();
      static const DecoderDescriptor* byId(const QString&);
      static QStringList modeKeys();   // 喂 demodCombo
  };
  ```
- **抄的点**：`r_device` 的**字段清单**（`include/r_device.h:59-92`）——但**不要抄 C 宏表**，用 C++ 静态初始化的 `QVector` 或 `std::array`（顺序可控、可调试）。
- **关键纪律**：`protocol_num` 换成**稳定字符串 ID**（rtl_433 用数组下标是历史包袱，MBDSDR 从第一天就该避开）。
- **成本**：低（新 2 文件 + 把 15 个 mode 字符串改成查表）。**收益**：加解码器从 6-8 处改到 2 处（新文件 + 注册表加一行）。

### ★2 定义 `IDecoder` 统一接口 + 输入端口枚举

- **改**：新建 `cpp/src/dsp/id_decoder.h`；改各解码器类头（`pocsag_decoder.h:62`、`adsb_decoder.h:52`、`m17_decoder.h:67` 等）
- **加**：
  ```cpp
  enum class DecoderInputKind { Iq, Audio, Bits, Symbol, Text };
  class IDecoder {
  public:
      virtual ~IDecoder() = default;
      virtual const DecoderDescriptor& desc() const = 0;
      virtual DecoderInputKind inputKind() const = 0;
      // 统一失败三态（对齐 rtl_433 decode_return_codes）
      virtual int  feed(const DecoderInput&) = 0;   // >0 成功 n 条 / ABORT_* / FAIL_MIC
      virtual void reset() = 0;
      virtual DecoderStats stats() const = 0;       // events/ok/fails[5]
  };
  ```
- **保留现有强类型 `feed()`**（`adsb_decoder.h:57` `feed(iq)`、`cw_decoder.h:31` `feed(audio)`），`IDecoder` 是**外层适配**，不强行改内部签名。
- **抄的点**：`include/r_device.h:45-53` 的**三态返回码 + `decode_fails[5]` 统计桶**——这是 rtl_433 最被低估的设计。
- **成本**：中（11 个解码器各加 ~30 行适配）。**收益**：可单测、可统计、可批量驱动。

### ★3 统一 KV 输出模型 `DecodeMessage`（对齐 `data_t`）

- **改**：新建 `cpp/src/dsp/decode_message.h`；改各解码器输出 + UI 面板 + agent_tools
- **加**：
  ```cpp
  struct DecodeField { QString key, prettyKey, unit; QVariant value; };
  struct DecodeMessage {
      QString decoderId;        // 谁解的
      QDateTime timestamp;
      QVector<DecodeField> fields;   // model/id/.../mic
      static DecodeMessage fromPocsag(const PocsagMessage&);
      static DecodeMessage fromAdsb(const AircraftInfo&);
      // ... 各解码器一个 fromXxx 工厂
      QJsonObject toJson() const;
      QByteArray  toCsvRow(const QStringList& columns) const;
  };
  ```
- **抄的点**：`include/data.h:181-192` 的 `data_output_t` **虚表**——UI 面板 / agent_tools / HTTP / 导出 CSV 都实现同一张表，解码器零改动。
- **抄的点**：`src/r_api.c:652-800` 的**单位自动换算**（字段名后缀 `_F`/`_mm_h`/`_inHg` 自动转）——MBDSDR 各面板各写一遍 `formatFrequencyAutoHz`，应收口。
- **成本**：中。**收益**：一个解码器出货，UI/导出/agent 同时可用；CSV 列从 `DecoderDescriptor::outputFields` 自动生成。

### ★4 失败三态 + 统计报告

- **改**：`cpp/src/dsp/id_decoder.h`（见建议 2）、`cpp/src/dsp/spectrum_engine.h`、`cpp/src/ai/agent_tools.cpp:594` 起的快照工具
- **加**：`DecoderStats { int events, ok, messages; int fails[5]; }`，engine 定期聚合，agent 工具 `get_decoder_stats` 暴露。
- **抄的点**：`src/r_api.c:846-880` `create_report_data()`——`-M level` 导出全解码器 events/ok/fail_mic 计数。MBDSDR 可做成「解码器健康页」右栏页签（建议 5 自动建）。
- **关键纪律**：**「不是我的协议」必须零日志零输出**，只进 `abort_*` 计数（rtl_433 靠这个让 200+ 解码器每包都跑而不刷屏）。MBDSDR 现在 `adsbEmpty_`/`cwEmpty_` 这类空态提示是对的，但缺全局统计。
- **成本**：低。**收益**：可诊断"哪个解码器在吃 CPU 但没出货"。

### ★5 右栏页签从注册表自动建（`DecoderRegistry::group` 驱动）

- **改**：`cpp/src/ui/main_window.cpp:1355-1600`（`rightTabs_` 构建段）
- **加**：`DecoderRegistry::all()` 按 `group` 排序，每条 `factory` 产出面板 widget，`addTabGroupHeader` 头（`main_window.cpp:1367-1370`）按 group 切。
- **现状对照**：14 个 `rightTabs_->addTab`（`main_window.cpp:1392/1405/1434/1447/1460/1471/1482/1555/1724/2209/2309/2583/2586/2591`）+ 3 个 `addTabGroupHeader`（`:1391/1554/2308`）。这些应变成 **1 个循环**。
- **抄的点**：rtl_433 `help_protocols()`（`src/rtl_433.c:172-188`）—— 协议列表**从注册表生成**，`-R json`（`:189-199`）给机器读。MBDSDR 可加 `GET /api/v1/decoders` 自描述端点（对齐 `01-onair.md` 建议 1）。
- **成本**：中（UI 重构）。**收益**：加解码器自动出页签；页签顺序/分组/显隐全是数据。

### ★6 解调模式 combo 从注册表生成

- **改**：`cpp/src/ui/main_window.cpp:666-667`（`demodCombo_->addItems`）、`cpp/src/dsp/vfo_manager.cpp:261-280`（mode 分发）
- **加**：`DecoderRegistry::modeKeys()` 喂 combo；`vfo_manager` 改成查 `DecoderDescriptor::factory` 而不是 if/else 链。
- **现状对照**：15 个 combo 项 vs 9 个 if/else if 分支——**已经不一致**（`BPSK/QPSK` 走 `digitalMode`，`POCSAG/m17/VOR/ACARS/NAVTEX` 走 `pocsag/m17/vor/acars/navtex` 成员，`CW` 复用 `DemodSSB::LSB`，`ADS-B` 单独分支 `vfo_manager.cpp:63`）。
- **抄的点**：rtl_433 `modulation` 是**枚举驱动的 switch**（`src/r_api.c:456-495`），新增线编码只改一处。MBDSDR 的 modeKey 应该同样是**注册表驱动的单一分发点**。
- **成本**：低（配合建议 1）。**收益**：消灭 if/else 链，combo 与分发永不漂移。

### ★7 输入端口标准化：`DecoderInput` variant

- **改**：新建 `cpp/src/dsp/decoder_input.h`；改 `vfo_manager.cpp` 的解码器喂数据点
- **加**：`std::variant<IqBuffer, AudioBuffer, BitBuffer, SymbolBuffer>`，`IDecoder::feed()` 只吃 `DecoderInput`。
- **抄的点**：rtl_433 的 `bitbuffer_t`（`include/bitbuffer.h:33-40`）是**统一中间表示**，让 200+ 解码器同质化。MBDSDR 解码器异质，但可以用 variant 表达"输入端口类型"，注册表声明 `inputKind`，`vfo_manager` 据此决定喂 IQ/音频/位流。
- **成本**：中。**收益**：消除 `vfo_manager.cpp:140-150` 那段"每个解码器各自喂不同东西"的注释墙。

### ★8 `-y` 式解码器自检命令（对齐 rtl_433 测试基建）

- **改**：`cpp/src/ai/agent_tools.cpp` 加 `verify_decoder` 工具；`tests/` 加 CLI 自检脚本
- **加**：`verify_decoder(decoder_id, test_vector)` —— 喂比特串/合成 IQ，返回 `stats` + 解出的 `DecodeMessage`。`tests/golden/cross_decode_vectors.json` 已有 LLR/IQ golden，直接复用。
- **抄的点**：`src/rtl_433.c:1685-1696` 的 `-y` + `exit(!r)` —— **退出码即断言**，CI 零成本。
- **成本**：低。**收益**：每个解码器有最小回归；加解码器 PR 可强制带 golden 向量。

### ★9 可选：flex 式"声明式解码器"（长期）

- **改**：新建 `cpp/src/dsp/flex_decoder.{h,cpp}`
- **加**：仿 `src/devices/flex.c` 的 `create_fn`（`:666`）+ `match_bits` 位模板（`:103`），允许用户在 `QSettings`/JSON 配置里声明 `{id, match_bits, fields, getters}` 而不写 C++。
- **抄的点**：`flex_decoder` 是 rtl_433 对"不想写 C 也能加协议"的答案（`src/devices/flex.c:870-875`）。MBDSDR 的 `adsb/ft8/pocsag` 这类已实现的走原生 C++，但**门铃/遥控/简单 OOK 传感器**可以走 flex 配置。
- **成本**：高。**收益**：覆盖长尾设备；建议在建议 1-7 落地后再做。

### ★10 可选：`maintainer_update.py` 式一致性校验

- **改**：`tests/` 加 `check_decoder_registry.py`（或 C++ 编译期断言）
- **校验**：注册表里的 `id` 集合 == `dsp/*.h` 解码器类集合 == UI 页签集合 == agent_tools 快照工具集合；`outputFields` 非空；`modeKey` 在 `modeKeys()` 里。
- **抄的点**：`maintainer_update.py:141-145` 校验 `DEVICES` 表与 `r_device` 声明一致；`docs/CONTRIBUTING.md:26` 让它成为 PR 必跑步骤。MBDSDR 已有先例：`agent_tools.h:53-57` 写了 "adding a tool without wiring its executor now fails the build's tests"。
- **成本**：低。**收益**：注册表永不漂移（这是 602 设备规模下的核心风险）。

---

## 6. 未查明的开放问题

1. **`protocol_num` 漂移的历史代价**：`include/rtl_433_devices.h` 里 6 个 `DECL(new_template)` 占位（`:15,19,37,38` 等）是为保持历史协议号稳定，但**没有任何文档说明哪些号被外部工具依赖**。MBDSDR 若要做"稳定 ID"，需自定 ID 规范（建议 1）。
2. **`priority` 的语义边界**：`include/r_device.h:73` 注释说 "Run later and only if no previous events were produced"，但 `src/r_api.c:444` 实现是**同 priority 组内全跑、跨 priority 组短路**。两者是否等价？源码注释与实现有细微出入，未找到测试用例佐证。
3. **`flex_decoder` 的 `create_fn` 递归实例化**（`src/devices/flex.c:685` 设 `dev->create_fn = flex_create_device`）—— 实例可以再实例化，但 `free_protocol()`（`src/r_api.c:277-281`）只 `free(decode_ctx)`，flex 的多层实例是否有泄漏风险未审计。
4. **200+ 解码器的真实回归覆盖**：`tests/` 无解码器级 golden；社区靠 `-S unknown` 抓样在 PR 附件里传。这意味着**回归全靠人工**。MBDSDR 的 `tests/golden/cross_decode_vectors.json` 模式若反向移植到 rtl_433 会更稳。
5. **`r_device` 的 `log_fn`/`output_fn` 何时被覆盖**：`register_protocol()`（`src/r_api.c:265-268`）统一设成 `log_device_handler`/`data_acquired_handler`，但 `output_fn` 字段在 `r_device` 里是公开的——是否有解码器自设 `output_fn` 绕过广播？实测 331 个 `devices/*.c` 未发现（grep 仅命中框架侧）。
6. **MBDSDR 11 个解码器的输入端口清单**：`vfo_manager.cpp:140-150` 注释墙描述了每种模式的喂数据路径，但**未形成可机读的清单**。建议 7 落地前需要先盘一遍。
7. **`DecoderRegistry` 与 `ControlHub::table()` 的关系**：`01-onair.md` 建议 2 提出"能力表"统一 `registeredToolSpecs()` 与 `ControlHub::table()`。`DecoderRegistry` 是第三张表，需决定是合并进同一张能力表，还是独立（报告倾向**独立**，因为解码器生命周期与命令生命周期不同）。

---

## 附录 A：rtl_433 关键文件索引

| 文件 | 行数 | 职责 |
|---|---|---|
| `include/r_device.h` | 94 | `r_device` 结构体 + `modulation_types` + `decode_return_codes` |
| `include/rtl_433_devices.h` | 404 | `DEVICES` X-macro 全表（383 个 `DECL`） |
| `include/bitbuffer.h` | — | `bitbuffer_t` 位流容器 + 位对齐工具 |
| `include/data.h` | 224 | `data_t` KV 树 + `data_output_t` 输出虚表 |
| `include/pulse_data.h` | 82 | `pulse_data_t` 脉冲级中间表示 |
| `include/pulse_slicer.h` | — | 10 个 `pulse_slicer_*` 线编码解码器声明 |
| `include/decoder_util.h` | — | `decoder_output_data` / `decoder_log*` 解码器辅助 |
| `src/r_api.c` | 1308 | 注册/调度/输出广播（`r_init_cfg` `register_protocol` `run_ook_demods` `data_acquired_handler`） |
| `src/rtl_433.c` | 1955 | CLI 解析（`-R`/`-X`/`-F`/`-y`）、`help_protocols` |
| `src/pulse_slicer.c` | — | 10 个 slicer 实现 + `account_event` 统计 |
| `src/pulse_detect.c` | — | `pulse_detect_package` IQ→脉冲 |
| `src/r_flow.c` | — | `push_sdr_flow` IQ→脉冲→demods |
| `src/output_file.c` | — | JSON/CSV/KV 输出器（`data_output_*_create`） |
| `src/output_mqtt.c` `output_influx.c` `output_udp.c` | — | 网络输出器 |
| `src/devices/*.c` | 331 文件 | 383 个 `r_device const` |
| `src/devices/new_template.c` | — | 官方解码器模板（含完整注释工作流） |
| `src/devices/flex.c` | — | `flex_decoder` 通用位模板解码器 |
| `maintainer_update.py` | — | 同步 CMakeLists/README/conf |
| `docs/CONTRIBUTING.md` | 90 | 加解码器官方步骤 |
| `docs/DATA_FORMAT.md` | — | 输出字段命名规范（`model`/`id`/`mic`/`battery_ok`...） |
| `tests/CMakeLists.txt` | — | 测试矩阵（基础构件 + flex 冒烟 + HTTP 集成） |

## 附录 B：MBDSDR 现状关键文件索引

| 文件 | 关键行 | 职责 |
|---|---|---|
| `cpp/src/dsp/vfo_manager.h` | 96, 115-167 | `VfoChannel` 解码器成员 + 模式谓词 |
| `cpp/src/dsp/vfo_manager.cpp` | 28-46, 63, 261-280 | 模式字符串分发（WFM/ADS-B/digital/解调 if 链） |
| `cpp/src/ui/main_window.cpp` | 666-667, 1367-2595 | `demodCombo_` 15 项硬编码 + 14 个 `addTab` |
| `cpp/src/dsp/pocsag_decoder.h` | 33-58 | `PocsagMessage` + `feed/take` 形态 |
| `cpp/src/dsp/adsb_decoder.h` `cw_decoder.h` | 52-58 / 26-32 | `feed(iq)` / `feed(audio)` / `take*` |
| `cpp/src/dsp/ft8_codec.h` | 31 | `Ft8Decoded decode(llr, n)` |
| `cpp/src/dsp/spectrum_engine.h` | 142-207 | FT8 检测器接线 + `Ft8ExportCandidate` |
| `cpp/src/ai/agent_tools.cpp` | 594-640 | 数字解码快照工具（POCSAG/m17/VOR） |
| `tests/golden/cross_decode_vectors.json` | — | 跨语言 golden（LLR/IQ base64） |
| `docs/learn/phase81/research/01-onair.md` | — | 风格范本 + 插件注册表对照 |

---

## 附录 C：rtl_433 `DEVICES` 表规模实测

| 指标 | 数值 | 统计方式 |
|---|---|---|
| `src/devices/*.c` 文件数 | **331** | `ls src/devices/*.c \| wc -l` |
| `r_device const` 声明数 | **383** | `grep -h "^r_device const" src/devices/*.c \| wc -l`（一文件可含多设备） |
| 声明 `output_fields[]` 的文件数 | **313** | `grep -l "static char const \*const output_fields"` |
| `include/rtl_433_devices.h` 中 `DECL(...)` | **383**（含 `new_template` 占位 ×6） | `maintainer_update.py:125` 注释 |
| 设 `priority` 的解码器 | **14+** | `grep -l "\.priority"` |
| 输出格式实现数 | **10** | `data_output_*_create`：json/csv/kv/log/mqtt/influx/syslog/http/trigger/null（`src/r_api.c:982-1057`） |
| `pulse_slicer_*` 线编码 | **10** | `include/pulse_slicer.h:38-184` |
| `modulation_types` 枚举值 | **14**（10 OOK + 3 FSK + 1 dummy） | `include/r_device.h:24-40` |
