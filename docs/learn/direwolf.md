# Direwolf 源码学习笔记：AX.25 / AFSK / HDLC / APRS 编解码

> 仓库：`https://github.com/wb2osz/direwolf.git`（浅克隆 depth=1）
> 阅读范围：`src/demod_afsk.c`、`src/hdlc_rec.c`、`src/hdlc_rec2.c`、`src/fcs_calc.c`、`src/ax25_pad.c`、`src/ax25_pad.h`、`src/decode_aprs.c`、`src/demod.c`、`src/fsk_demod_state.h`、`src/audio.h`、`src/dsp.c`
> 对照文件：`mbdsdr_ai/ax25.py`（BI4MIB）
>
> 约定：所有 `file:line` 均相对于 `direwolf/src/`。

---

## 1. AFSK 1200 真实参数

### 1.1 频点与波特率（默认值）

| 参数 | 值 | 出处 |
|---|---|---|
| Mark 频率 | **1200 Hz** | `audio.h:470` `#define DEFAULT_MARK_FREQ 1200` |
| Space 频率 | **2200 Hz** | `audio.h:471` `#define DEFAULT_SPACE_FREQ 2200` |
| 波特率 | **1200 baud** | `audio.h:472` `#define DEFAULT_BAUD 1200` |
| 默认采样率 | **44100 Hz** | `audio.h:442` `#define DEFAULT_SAMPLES_PER_SEC 44100` |
| 最大采样率 | 192000 Hz | `audio.h:450` |
| 每比特样本数 | 44100/1200 = 36.75 采样/位 | 由 `demod_afsk.c:421` 计算 |

注释明确说明：VHF FM 上 mark/space 对调无影响，因为 NRZI 只关心跳变与否（`audio.h:461-463`）。

### 1.2 两种解调 Profile

direwolf 默认 Profile **A**（`demod.c:204`：无指定 profile 时默认 `"A"`，并自动加 `+` 多 slicer）。Profile A 是**双 NCO 正交相关检测**，Profile B 是**FM 鉴频器**。

#### Profile A（默认，`demod_afsk.c:259-317`）

信号链路（`demod_afsk.c:638-703`）：

1. **带通预滤波**（可选）：FIR 带通滤波器
   - `prefilter_baud = 0.155`（`demod_afsk.c:274`），即滤波器截止频率距 mark/space 各 0.155×1200 = 186 Hz
   - 实际通带：`f1 = min(mark,space) - 0.155*baud = 1200-186 = 1014 Hz`，`f2 = max(mark,space) + 0.155*baud = 2200+186 = 2386 Hz`（`demod_afsk.c:450-451`）
   - 滤波器长度：`pre_filter_len_sym = 383*1200/44100 ≈ 10.4 符号`，取奇数（`demod_afsk.c:282, 432`）
   - 窗函数：`BP_WINDOW_TRUNCATED`（截断窗，即矩形窗，`demod_afsk.c:283`）
   - 实现：`gen_bandpass()`（`dsp.c:207`）

2. **双 NCO 混频**（`demod_afsk.c:643-649`）：
   - Mark NCO：32 位相位累加器，`m_osc_delta = round(2^32 * 1200 / 44100)`（`demod_afsk.c:294`）
   - Space NCO：`s_osc_delta = round(2^32 * 2200 / 44100)`（`demod_afsk.c:297`）
   - 用 256 项 cos/sin 表做 I/Q 混频（`demod_afsk.c:76-79, 643-648`）
   - 即：`m_I = fsam * cos(m_phase)`，`m_Q = fsam * sin(m_phase)`；space 同理

3. **RRC 低通滤波**（`demod_afsk.c:651-657`）：
   - 根升余弦滤波器，`rrc_width_sym = 2.80` 符号，`rrc_rolloff = 0.20`（`demod_afsk.c:303-304`）
   - 抽点数：`(2.80 * 44100/1200) | 1 ≈ 103` 抽头（`demod_afsk.c:474`）
   - 实现：`gen_rrc_lowpass()`（`dsp.c:385`）
   - 对 m_I、m_Q、s_I、s_Q 四路分别低通

4. **包络检测**：`m_amp = hypot(m_I, m_Q)`，`s_amp = hypot(s_I, s_Q)`（`demod_afsk.c:653, 657`）

5. **AGC**（`demod_afsk.c:690-691`）：对 mark/space 包络分别做 fast-attack/slow-decay 包络归一化
   - `agc_fast_attack = 0.70`，`agc_slow_decay = 0.000090`（`demod_afsk.c:312-313`）
   - 输出 `demod_out = m_norm - s_norm`（`demod_afsk.c:698`），范围约 -1 ~ +1

#### Profile B（FM 鉴频，`demod_afsk.c:319-382, 735-761`）

- 只用一个中心频率 NCO（`(mark+space)/2 = 1700 Hz`），混频后取 `atan2(Q,I)` 得瞬时相位，再差分相位得瞬时频率（`demod_afsk.c:745-756`）
- `normalize_rpsam = 1 / (0.5 * |mark-space| * 2π / fs)`（`demod_afsk.c:369`）把频偏归一化到 ±1
- RRC 参数不同：`rrc_width_sym=2.0`，`rrc_rolloff=0.40`（`demod_afsk.c:359-360`）

### 1.3 采样率要求

- 注释明确说 44100 比 22050 效果好（`audio.h:444`）
- ARM 单板可自动 3 倍抽取（`demod.c:224-228`），44100/3=14700 Hz 仍能解调
- `MAX_FILTER_SIZE = 480` 抽头上限（`fsk_demod_state.h:48`）

---

## 2. 位定时恢复（DPLL）

### 2.1 算法：32 位累加器 DPLL（不是过零检测）

direwolf **不用过零检测**，也不用"过采样取最佳点"，而是用一个**带惯性的数字锁相环（DPLL）**：

- `data_clock_pll` 是 **signed 32 位计数器**（`fsk_demod_state.h:213`，注释明确"Must be 32 bits"）
- 每来一个音频样本，`data_clock_pll += pll_step_per_sample`（`demod_afsk.c:875`）
- `pll_step_per_sample = round(2^32 * baud / fs)`（`demod_afsk.c:421`）
  - 44100 Hz / 1200 baud：`step = round(4294967296 * 1200 / 44100) = 116859872`
- **当 `data_clock_pll` 从正溢出到负（符号位翻转）时，就是采样点**（`demod_afsk.c:880`）
  - 此时取 `demod_out > 0` 作为解调比特，调用 `hdlc_rec_bit()`（`demod_afsk.c:909`）

### 2.2 跳变沿牵引（nudge）

当解调输出 `demod_data` 发生跳变时（`demod_afsk.c:922`）：

```c
if (D->data_detect)
    D->data_clock_pll *= pll_locked_inertia;    // 已锁定：0.74
else
    D->data_clock_pll *= pll_searching_inertia; // 搜索中：0.50
```

（`demod_afsk.c:928-931`）

- `pll_locked_inertia = 0.74`，`pll_searching_inertia = 0.50`（`demod_afsk.c:315-316`）
- 含义：跳变时把 PLL 相位**乘上一个 <1 的系数**，即把相位往 0（理想跳变点）拉。锁相后拉得轻（0.74），搜索时拉得重（0.50）
- 这是**二阶 PLL 的一阶近似**：相位累加 + 跳变牵引

### 2.3 DCD（载波检测）

基于跳变沿是否出现在预期窗口内打分（`fsk_demod_state.h:514-554`）：
- 跳变时 `dpll_phase` 在 `±DCD_GOOD_WIDTH * 1024*1024 = ±512*1M` 内 = good（`fsk_demod_state.h:517`）
- 32 位滑动窗口内 good 减 bad ≥ `DCD_THRESH_ON=30` → 判为有载波（`fsk_demod_state.h:542`）
- ≤ `DCD_THRESH_OFF=6` → 判为无载波（`fsk_demod_state.h:548`）

---

## 3. HDLC 真实实现

### 3.1 NRZI 解码

HDLC 帧线上是 **NRZI**：电平不变=1，电平跳变=0（`hdlc_rec.c:481-484`）。

```c
dbit = (raw == H->prev_raw);   // hdlc_rec.c:497，hdlc_rec2.c:676
H->prev_raw = raw;
```

- `raw` 是解调出来的 mark/space 电平
- `dbit=1` 表示相邻 raw 相同（无跳变），`dbit=0` 表示跳变

### 3.2 Flag 检测 0x7E

8 位移位寄存器 `pat_det`，LSB-first 移入（`hdlc_rec.c:514-517`、`hdlc_rec2.c:654,682`）：

```c
H->pat_det >>= 1;
if (dbit) H->pat_det |= 0x80;
```

- **Flag = 0x7E** = 01111110（bit 时间顺序：0,1,1,1,1,1,1,0）（`hdlc_rec.c:526`、`hdlc_rec2.c:696`）
- **Abort = 0xFE** = 11111110（连续 7 个 1）→ 丢弃当前帧（`hdlc_rec.c:677`、`hdlc_rec2.c:684`）

### 3.3 Bit 去填充

连续 5 个 1 后面跟的 0 是填充位，要丢弃：

- 旧路径（`hdlc_rec.c:695`）：`(H->pat_det & 0xfc) == 0x7c`，即 `011111xx`，丢弃当前 0 位（`;` 空语句不累积）
- 新路径（`hdlc_rec2.c:711`）：`(H2.pat_det >> 2) == 0x1f`，等价条件，`continue` 跳过

### 3.4 字节组装顺序

**LSB first**（HDLC 标准）：

```c
H->oacc >>= 1;
if (dbit) H->oacc |= 0x80;   // hdlc_rec2.c:691-692, 714
H->olen++;
if (H->olen & 8) { H->olen = 0; frame_buf[frame_len++] = H->oacc; }  // hdlc_rec2.c:724-729
```

每收 8 位，`oacc` 就是一个完整字节，直接写入帧缓冲。

### 3.5 FCS / CRC-16

**多项式：CRC-16/X.25（即 CRC-16-CCITT 反射版）**

- 查表法（`fcs_calc.c:32-69`），表来自 RFC 1549（`fcs_calc.c:34`）
- 初始值 `crc = 0xFFFF`（`fcs_calc.c:78`）
- 算法：右移，反射输入（`fcs_calc.c:83`）：
  ```c
  crc = (crc >> 8) ^ ccitt_table[(crc ^ data[j]) & 0xff];
  ```
- 最终异或 `crc ^ 0xFFFF`（`fcs_calc.c:86`）
- 验证：`ccitt_table[1] = 0x1189`（`fcs_calc.c:36`），这正是多项式 **0x1021 反射为 0x8408** 的标准 CRC-16/X.25 表第一项
- **字节序：线上小端**（`hdlc_rec.c:569`、`hdlc_rec2.c:768`）：
  ```c
  actual_fcs = frame_buf[len-2] | (frame_buf[len-1] << 8);
  ```
  即 FCS 低字节在前、高字节在后
- 计算范围：`fcs_calc(frame_buf, frame_len - 2)`（`hdlc_rec2.c:770`），不含 FCS 本身

---

## 4. AX.25 帧格式

### 4.1 地址字段（每站 7 字节）

出处：`ax25_pad.h:28-32`、`ax25_pad.c:1054-1058, 1352`

每个地址占 **7 字节**：

```
Byte 0..5:  callsign[6]，每个 ASCII 字符左移 1 位（<<1），不足 6 位补空格（0x20<<1 = 0x40）
Byte 6:     SSID 控制字节
```

打包（`ax25_pad.c:1054-1057`）：
```c
memset(frame_data + n*7, ' ' << 1, 6);   // 0x40 填充
for (i=0; i<6 && atemp[i]; i++)
    frame_data[n*7+i] = atemp[i] << 1;
```

解包（`ax25_pad.c:1352`）：
```c
station[i] = (frame_data[n*7+i] >> 1) & 0x7f;
```

### 4.2 SSID 字节位域

出处：`ax25_pad.h:99-127`

```
 Bit:   7   6   5   4   3   2   1   0
       H   R   R  SSID          L
```

| 位 | 掩码 | 含义 |
|---|---|---|
| H (bit7) | `0x80` (`SSID_H_MASK`) | 目的站=C/R 命令位；中继站=已被重复位 |
| RR (bit6:5) | `0x60` (`SSID_RR_MASK`) | 保留位，固定 `11` |
| SSID (bit4:1) | `0x1e` (`SSID_SSID_MASK`) | 子台编号 0-15 |
| L (bit0) | `0x01` (`SSID_LAST_MASK`) | 地址字段结束标志，最后一个地址=1 |

- 目的站 SSID 字节初值：`0x80 | 0x60 = 0xE0`（`ax25_pad.c:428`）
- 源站 SSID 字节初值：`0x60 | 0x01 = 0x61`（`ax25_pad.c:431`）——源站是默认最后地址
- 取 SSID：`(byte & 0x1e) >> 1`（`ax25_pad.c:1482`）
- 设 SSID：`(byte & ~0x1e) | ((ssid << 1) & 0x1e)`（`ax25_pad.c:1517-1518`）

### 4.3 地址数量解析

扫描每个地址的第 7 字节，找到第一个 L 位=1 的字节（`ax25_pad.c:1253-1257`）：
```c
for (a=0; a<frame_len && addr_bytes==0; a++)
    if (frame_data[a] & SSID_LAST_MASK) addr_bytes = a+1;
addrs = addr_bytes / 7;   // 必须在 2..10 之间
```
最多 10 个地址（目的+源+8 中继），`ax25_pad.h:13-15`。

### 4.4 控制字段 + PID + 信息字段

帧布局（`ax25_pad.h:180-269`）：

```
[地址字段: num_addr * 7 字节][控制字段: 1 或 2 字节][PID: 0/1/2 字节][信息字段]
```

- **控制字段偏移** = `num_addr * 7`（`ax25_pad.h:182`）
- **UI 帧控制 = 0x03**（`ax25_pad.h:66` `#define AX25_UI_FRAME 3`）
- **APRS PID = 0xF0**（`ax25_pad.h:68` `#define AX25_PID_NO_LAYER_3 0xf0`）
- PID 仅对 I 帧（控制字节 LSB=0）和 UI 帧（0x03/0x13）存在（`ax25_pad.h:231-232`）
- 最小帧长 = `2*7 + 1 = 15` 字节（不含 FCS，`ax25_pad.h:55`）
- 信息字段首字节 = **DTI（数据类型标识符）**（`ax25_pad.c:1833`）

---

## 5. APRS 解析

### 5.1 数据类型标识符（DTI）

DTI 是信息字段第 1 个字节（`decode_aprs.c:325`）。完整 switch 表（`decode_aprs.c:336-488`）：

| DTI | 含义 | 处理函数 | 出处 |
|---|---|---|---|
| `!` | 位置（无时间戳，无消息） | `aprs_ll_pos` | `decode_aprs.c:338` |
| `=` | 位置（无时间戳，**有消息**） | `aprs_ll_pos` | `decode_aprs.c:341` |
| `/` | 位置（有时间戳，无消息） | `aprs_ll_pos_time` | `decode_aprs.c:386` |
| `@` | 位置（有时间戳，有消息） | `aprs_ll_pos_time` | `decode_aprs.c:387` |
| `:` | 消息 /  bulletin / 遥测元数据 | `aprs_message` | `decode_aprs.c:394` |
| `;` | Object | `aprs_object` | `decode_aprs.c:428` |
| `)` | Item | `aprs_item` | `decode_aprs.c:380` |
| `>` | 状态报告 | `aprs_status_report` | `decode_aprs.c:440` |
| `?` | 查询 | `aprs_general_query` | `decode_aprs.c:447` |
| `T` | 遥测 | `aprs_telemetry` | `decode_aprs.c:453` |
| `_` | 无位置气象报告 | `aprs_positionless_weather_report` | `decode_aprs.c:459` |
| `$` | 原始 GPS / Ultimeter | `aprs_raw_nmea` / `aprs_ultimeter` | `decode_aprs.c:359` |
| `'` `` ` `` | Mic-E 压缩位置 | `aprs_mic_e` | `decode_aprs.c:373-374` |
| `{` | 用户自定义 | `aprs_user_defined` | `decode_aprs.c:465` |
| `<` | 电台能力 | `aprs_station_capabilities` | `decode_aprs.c:434` |

### 5.2 位置格式（未压缩）

结构体（`decode_aprs.c:68-73`）：

```
lat[8]         = DDMM.hh + N/S  （2 度 + 4 分.百分位 + 半球，共 8 字符）
sym_table_id   = 1 字符 （/ 或 \ 或 0-9 A-Z）
lon[9]         = DDDMM.hh + E/W （3 度 + 4 分.百分位 + 半球，共 9 字符）
symbol_code    = 1 字符
```

即：`DDMM.hhNsDDDMM.hhWe`，符号表字符夹在纬度和经度之间。

### 5.3 压缩位置

Base-91 编码（`decode_aprs.c:95-97`，`'!'`=33 到 `'{'`=123）：
- 纬度：`lat = 90 - (y[0]-33)*91^3 + (y[1]-33)*91^2 + (y[2]-33)*91 + (y[3]-33)) / 380926.0`（`decode_aprs.c:3522`）
- 经度：`lon = -180 + (...)/190463.0`（`decode_aprs.c:3535`）

### 5.4 气象格式

位置型气象（`decode_aprs.c:3067-3200`），在位置+符号代码之后：

| 前缀 | 含义 | 单位 | 出处 |
|---|---|---|---|
| `ccc/sss` | Data Extension：课程/速度（ knots ） | 度/节 | `decode_aprs.c:3075-3089` |
| `cddd` | 风向 | 度 | `decode_aprs.c:3093` |
| `sddd` | 风速 | mph | `decode_aprs.c:3099` |
| `gddd` | 阵风（5 分钟内峰值） | mph | `decode_aprs.c:3130` |
| `ttt` | 温度（可负，如 -01） | 华氏度 | `decode_aprs.c:3144` |
| `rrr` | 雨量（最近 1 小时） | 0.01 英寸 | `decode_aprs.c:3164` |
| `ppp` | 雨量（最近 24 小时） | 0.01 英寸 | `decode_aprs.c:3174` |
| `PPP` | 雨量（自午夜） | 0.01 英寸 | `decode_aprs.c:3184` |

无位置气象报告 DTI=`_`，后面直接跟上述字段（`decode_aprs.c:459-462`）。

---

## 6. 对照 mbdsdr_ai/ax25.py 的问题清单

> 对照文件：`/home/user/Doubao/chats/38438160041798146/mbdsdr_ai/ax25.py`

### 6.1 AFSK 解调

| 项目 | mbdsdr 现状 | direwolf 真实值 | 评价 |
|---|---|---|---|
| mark/space/baud | 1200/2200/1200（`ax25.py:38-40`） | 同（`audio.h:470-472`） | ✅ 正确 |
| 采样率 | 48000（`ax25.py:41`） | 默认 44100，支持 48000（`audio.h:442,450`） | ✅ 可用 |
| 解调方法 | FFT 希尔伯特变换→瞬时频率→鉴频（`ax25.py:424-447`） | Profile A 双 NCO 相关 + RRC；Profile B 鉴频 | ⚠️ mbdsdr 用的是 Profile B 路线，**没有带通预滤波**（direwolf `demod_afsk.c:450-460` 有 1014–2386 Hz FIR 带通），噪声抑制差 |
| AGC | 仅全局峰值归一化（`ax25.py:413-415`） | mark/space 分别 fast-attack/slow-decay AGC（`demod_afsk.c:690-691`，参数 0.70/0.000090） | ⚠️ mbdsdr 没有**分音调 AGC**，当 mark/space 幅度不平衡（VHF FM 常见，比例 1.5~3.8）时判决门限会偏 |
| 平滑 | 半位移动平均（`ax25.py:436-444`） | RRC 低通（rolloff=0.20, 2.8 符号）（`demod_afsk.c:303-304`） | ⚠️ 功能类似但 RRC 对 ISI 优化更好 |

### 6.2 位定时恢复

| 项目 | mbdsdr 现状 | direwolf 真实值 | 评价 |
|---|---|---|---|
| 算法 | 浮点 edge-triggered PLL，gain=0.3/0.5/0.7 多试（`ax25.py:452-472, 537-538`） | 32 位累加器 DPLL，跳变时乘惯性 0.74（锁）/0.50（搜）（`demod_afsk.c:928-931`） | ✅ 思路等价（都是跳变牵引），mbdsdr 用多 start phase 暴力搜索补偿 |
| 采样点 | `prev_clock < spb/2 <= clock`（位中心，`ax25.py:468-469`） | 32 位溢出即采样（`demod_afsk.c:880`） | ✅ 等价 |
| DCD | 无显式 DCD，靠 FCS 通过判定 | 跳变沿位置打分，thresh_on=30/off=6（`fsk_demod_state.h:501-508`） | ⚠️ mbdsdr 靠多 slicer+FCS 投票，CPU 开销大但可行 |

### 6.3 HDLC / CRC

| 项目 | mbdsdr 现状 | direwolf 真实值 | 评价 |
|---|---|---|---|
| Flag 0x7E | `_is_flag` 检查 0,1,1,1,1,1,1,0（`ax25.py:474-477`） | `pat_det==0x7e`（`hdlc_rec.c:526`） | ✅ 正确 |
| Abort 0xFE | **未实现** | `pat_det==0xfe` 丢弃帧（`hdlc_rec.c:677`） | ⚠️ mbdsdr 不识别 7 连 1 abort，会把噪声当帧继续解析 |
| Bit unstuff | 5 个 1 后丢 0（`ax25.py:503-514`） | `(pat_det>>2)==0x1f` 丢 0（`hdlc_rec2.c:711`） | ✅ 逻辑正确 |
| NRZI | `0 if b!=prev else 1`（`ax25.py:544`） | `dbit=(raw==prev_raw)`（`hdlc_rec2.c:676`） | ✅ 正确 |
| CRC 多项式 | 0x8408 右移，init=0xFFFF，xorout=0xFFFF（`ax25.py:83-86`） | 同（`fcs_calc.c:78-86`，table[1]=0x1189） | ✅ **完全正确** |
| FCS 字节序 | `struct.pack('<H')` / `unpack('<H')`（`ax25.py:191,227`） | `frame_buf[len-2] | (frame_buf[len-1]<<8)`（`hdlc_rec2.c:768`） | ✅ 小端，正确 |

### 6.4 AX.25 地址解析 —— 发现一个 BUG

| 项目 | mbdsdr 现状 | direwolf 真实值 | 评价 |
|---|---|---|---|
| callsign <<1 | `ord(ch)<<1`（`ax25.py:108`） | `atemp[i]<<1`（`ax25_pad.c:1057`） | ✅ 正确 |
| 空格补位 | `ljust(6)`（`ax25.py:103`） | `memset(..., ' '<<1, 6)` = 0x40（`ax25_pad.c:1054`） | ✅ 正确 |
| SSID 位域 | `(ssid&0xF)<<1 \| 0x60 \| H \| last`（`ax25.py:112-118`） | `(byte & ~0x1e)\|(ssid<<1 & 0x1e)` + RR=0x60（`ax25_pad.c:1517-1518`） | ✅ 位域正确（注释里"bits6-4"写法有误，但代码对） |
| **目的站 L 位** | **BUG**：`to_bytes` 第 165 行 `is_last = len(digi)==0`，然后第 166 行把这个 `is_last` 传给**目的站**！无中继时目的站 SSID 字节 bit0=1 | 目的站 L 位永远=0，初值 `0xE0`（`ax25_pad.c:428`）；只有最后一个地址 L=1（`ax25_pad.c:431` 源站初值 `0x61`） | ❌ **BUG**：无中继时目的站 SSID 字节错误地带了 L=1。direwolf 的 `ax25_get_num_addr`（`ax25_pad.c:1253-1257`）扫到第一个 L=1 就停，会在第 7 字节停下，认为只有 1 个地址（< MIN_ADDRS=2），帧被拒收 |

**修复建议**：`ax25.py:165-166` 应改为：
```python
# 目的站永远不是最后地址
dest_addr = bytearray(encode_address(self.destination, self.dest_ssid, is_last=False))
# 源站是否最后，取决于有无中继
src_is_last = (len(self.digipeaters) == 0)
frame += encode_address(self.source, self.source_ssid, is_last=src_is_last)
```

### 6.5 APRS DTI 表问题

| mbdsdr 常量（`ax25.py:58-69`） | direwolf 真实 DTI | 评价 |
|---|---|---|
| `APRS_POSITION = '!'` | `!` = 位置无消息（`decode_aprs.c:338`） | ✅ |
| `APRS_POSITION_MSG = '\''` | `'` = **旧 Mic-E**（`decode_aprs.c:373`），不是"位置有消息" | ❌ **错**：有消息的位置 DTI 是 **`=`**（`decode_aprs.c:341`），mbdsdr 漏了 `=` |
| `APRS_POSITION_TIME = '/'` | `/` = 位置有时间戳无消息（`decode_aprs.c:386`） | ✅（但漏了 `@` = 有时间戳有消息） |
| `APRS_MESSAGE = ':'` | `:` = 消息/bulletin/遥测元数据（`decode_aprs.c:394`） | ✅ |
| `APRS_WEATHER = '_'` | `_` = 无位置气象（`decode_aprs.c:459`） | ✅ |
| `APRS_OBJECT = ';'` | `;` = Object（`decode_aprs.c:428`） | ✅ |
| `APRS_ITEM = ')'` | `)` = Item（`decode_aprs.c:380`） | ✅ |
| `APRS_TELEMETRY = 'T'` | `T` = 遥测（`decode_aprs.c:453`） | ✅ |
| `APRS_STATUS = '>'` | `>` = 状态（`decode_aprs.c:440`） | ✅ |

**修复建议**：把 `APRS_POSITION_MSG` 改为 `'='`，并补充 `'@'`（带时间戳+消息位置）。

### 6.6 其他可迁移改进点

1. **加带通预滤波**：direwolf 在混频前先过 1014–2386 Hz FIR 带通（`demod_afsk.c:450-460`），mbdsdr 直接对全带信号鉴频，邻道干扰抑制差。
2. **加分音调 AGC**：direwolf 对 mark/space 包络分别 fast-attack(0.70)/slow-decay(0.000090)（`demod_afsk.c:312-313`），mbdsdr 只做全局峰值归一化。
3. **加 Abort 检测**：连续 7 个 1（0xFE）应丢弃当前帧（`hdlc_rec.c:677`），mbdsdr 没有。
4. **DCD 打分**：direwolf 用跳变沿相位位置做 32 滑窗打分（`fsk_demod_state.h:526-554`），比 mbdsdr 的"多 start phase + FCS 投票"省 CPU。
5. **压缩位置 base-91**：mbdsdr 只支持未压缩位置（`ax25.py:628-649`），direwolf 支持 base-91 压缩位置（`decode_aprs.c:3522,3535`），VHF 上常见。
6. **Mic-E 位置**：direwolf 专门解码 `'` 和 `` ` ``（`decode_aprs.c:373-378`），mbdsdr 完全不支持。

---

## 附：关键文件行速查

| 主题 | 文件:行 |
|---|---|
| mark/space/baud 默认值 | `audio.h:470-472` |
| 默认采样率 44100 | `audio.h:442` |
| Profile A 初始化参数 | `demod_afsk.c:259-317` |
| 预带通截止计算 | `demod_afsk.c:450-451` |
| RRC 参数 | `demod_afsk.c:303-304` |
| NCO delta 计算 | `demod_afsk.c:294,297` |
| I/Q 混频 | `demod_afsk.c:643-649` |
| AGC 参数 | `demod_afsk.c:312-313` |
| DPLL step 计算 | `demod_afsk.c:421` |
| DPLL 溢出采样 | `demod_afsk.c:880` |
| DPLL 跳变牵引 | `demod_afsk.c:928-931` |
| TICKS_PER_PLL_CYCLE=2^32 | `fsk_demod_state.h:64` |
| DCD 阈值 | `fsk_demod_state.h:501-511` |
| NRZI 解码 | `hdlc_rec.c:497`、`hdlc_rec2.c:676` |
| Flag 0x7E | `hdlc_rec.c:526`、`hdlc_rec2.c:696` |
| Abort 0xFE | `hdlc_rec.c:677`、`hdlc_rec2.c:684` |
| Bit unstuff | `hdlc_rec.c:695`、`hdlc_rec2.c:711` |
| FCS 小端组装 | `hdlc_rec2.c:768` |
| CRC 查表 init/xorout | `fcs_calc.c:78,86` |
| CRC 表首项 0x1189 | `fcs_calc.c:36` |
| 地址 7 字节 callsign<<1 | `ax25_pad.c:1054-1057` |
| SSID 字节位域 | `ax25_pad.h:99-127` |
| 目的站 SSID 初值 0xE0 | `ax25_pad.c:428` |
| 源站 SSID 初值 0x61 | `ax25_pad.c:431` |
| 地址数量扫描（L 位） | `ax25_pad.c:1253-1257` |
| UI 控制 0x03 / PID 0xF0 | `ax25_pad.h:66,68` |
| APRS DTI switch | `decode_aprs.c:336-488` |
| 位置结构 lat[8]/sym/lon[9] | `decode_aprs.c:68-73` |
| base-91 压缩位置 | `decode_aprs.c:3522,3535` |
| 气象字段 c/s/g/t/r/p/P | `decode_aprs.c:3093-3186` |
