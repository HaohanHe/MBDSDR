# 14 · mbdsdr_ai/ax25.py 深度代码审查（第二轮）

- 文件：`mbdsdr_ai/ax25.py`（1072 行）
- 审查方式：逐行阅读 + 实跑 Python 验证（CRC 测试向量、地址编解码往返、位填充往返、APRS 位置/课程/海拔解码、WIDE 路径上线路径）
- 审查日期：2026-09-24

---

## 0. 总体结论

模块**骨架完整、自测能跑通**：CRC-16/X.25 多项式正确、HDLC 位填充方向正确、NRZI 编解码互逆、UI 帧 / 位置报文 / 消息报文的"自合成→自解调→自解码"闭环是通的。

但存在**至少 2 个会在真实 144.390 MHz 接收/发射时直接翻车的协议层 bug**（地址扩展位 E 错位、WIDEn-N 呼号切分错误），以及**大量 APRS 报文类型是空壳常量**（Mic-E、对象、物品、遥测、状态、气象解码全部缺失）。这正是典型的"实验室绿、真机红"：合成帧走的是自家宽松解码器，真实 direwolf/TNC/HT 发出来的帧按严格 HDLC 解析会直接拒收或解出垃圾。

---

## 1. [真bug] 地址字段 E 位（扩展位）在无中继时双置 1

**位置**：`mbdsdr_ai/ax25.py:164-169`

```python
# 目的地址
is_last = len(self.digipeaters) == 0
frame += encode_address(self.destination, self.dest_ssid, is_last=is_last)

# 源地址
is_last = len(self.digipeaters) == 0          # ← 同一表达式
frame += encode_address(self.source, self.source_ssid, is_last=is_last)
```

**问题**：AX.25 地址字段顺序为 `目的(7) + 源(7) + 0..n 个 digi(7)`，E 位（SSID 字节 bit0）**只能**在最后一个地址上置 1。
- 无 digi 时：应是 `目的 E=0, 源 E=1`；
- 有 digi 时：应是 `目的 E=0, 源 E=0, …, 最后一个 digi E=1`。

代码在无 digi 时把**目的和源都置 E=1**。实跑验证：

```
AX25Frame(destination='APRS', source='BI4MIB-9', ...).to_bytes().hex()
= 82a0a4a64040 61  8492689a9284 73  03 f0 74657374 5cbf
                ^^dest E=1       ^^src E=1
```

**真机后果**：严格 HDLC 接收机（direwolf、硬件 TNC）按 E=1 判定地址字段结束，会在目的地址后就停住，把源地址首字节 `0x84`（`'B'<<1`）当成控制字段——既不是 I/S/U 合法控制字，整帧被丢弃。自家 `from_bytes`（`ax25.py:203-211`）无视 E 位、硬读源地址，所以合成自收发永远绿。这是头号"实验室绿、真机红"。

**修复方向**：目的地址永远 `is_last=False`；源地址 `is_last = (len(digipeaters)==0)`；digi 循环最后一个 `is_last=True`。

---

## 2. [真bug] WIDEn-N 路径切分错误：`WIDE2-2` 被编成呼号 `"WIDE2"` 上链路

**位置**：`mbdsdr_ai/ax25.py:757-760`（`APRSPacket.to_ax25_frame`）、`940-957`（`Digipeater.process_frame`）、`1015`（默认 `['WIDE2-2']`）

```python
digi_list = [(d.split('-')[0] if '-' in d else d,
              int(d.split('-')[1]) if '-' in d and d.split('-')[1].isdigit() else 0,
              False)
             for d in self.digipeaters]
```

`"WIDE2-2".split('-')` → `("WIDE2", 2)`，即上链路呼号字段填 `"WIDE2"`（5 字符补空格成 `"WIDE2 "`），SSID=2。

**APRS 标准**：宽区域泛洪路径在链路上的呼号字段是 **`"WIDE"`**（4 字符），SSID 才是 1/2。用户配置里写的 `WIDE2-2` 是 TNC 记法：`WIDE` + SSID=2 + 剩余跳数 2。代码按 `-` 切分把数字 `"2"` 并进了呼号。

实跑：

```
build_aprs_position_frame(...).digipeaters = [('WIDE2', 2, False)]
```

接收侧 `Digipeater.process_frame`（`ax25.py:940`）的判定反过来又错：

```python
if call_upper.startswith('WIDE') and len(call_upper) > 4:
```

它要求呼号长度 >4（即 `"WIDE2"`、`"WIDE1"` 这种错形式）才认，而链路上**正确**的 `"WIDE"`（len=4）反而被忽略。等于收发自闭环里"假 WIDE2"能被自家 digi 认出来，但真 direwolf digi 听到 `"WIDE2"` 不会转发（它找的是 `"WIDE"`），自家 digi 听到真 `"WIDE"` 也不会转发。

**修复方向**：解析 `WIDEn-m` 时把 `WIDE` 后那个数字抽出来当 SSID，呼号固定为 `"WIDE"`；digi 侧判定改成 `call_upper == 'WIDE'` 并按 SSID 递减。

---

## 3. [真bug] `build_aprs_position_frame` 在带时间戳时产出畸形帧

**位置**：`mbdsdr_ai/ax25.py:995-1021`

```python
data_type='!',
payload=pos.encode()[1:],  # 去掉数据类型标识符
```

`APRSPosition.encode()`（`ax25.py:598-601`）：
- 无时间戳 → 返回 `"!DDMM.hhN/..."`；
- 有时间戳 → 返回 `"/"+timestamp+"DDMM.hhN/..."`。

调用方硬编码 `data_type='!'` 并 `[1:]` 剥首字符。一旦 `pos.timestamp` 被设置，`encode()` 返回 `/...`，`[1:]` 剥掉 `/` 后剩下 `"241200z3954.00N..."`，再拼上 `data_type='!'` 变成 `"!241200z3954.00N..."`——解码器把 `"241200"` 当纬度度数 `int("24")`、把 `"1200z"` 当分，整位置全错。

目前工具函数默认不传 timestamp，所以日常不触发；但这是个埋着的雷。

---

## 4. [真bug] APRS 位置报文只认 `!` 和 `/`，不认 `=` 和 `@`

**位置**：`mbdsdr_ai/ax25.py:800-803`

```python
if packet.data_type in ('!', '/'):
    packet.position = APRSPosition.decode(info)
elif packet.data_type == ':':
    packet.message = APRSMessage.decode(info)
```

APRS 位置报文标准数据类型：
- `!` — 无时间戳、无消息确认；
- `=` — 无时间戳、**有消息确认**（大量带 Message 能力的 HT/车台用这个）；
- `/` — 有时间戳（HHMMSSz）；
- `@` — 有时间戳（DDHHMM，老式）。

实跑 `APRSPosition.decode("=3954.25N/11624.44E-xxx")` 直接返回 `None`。真机上 144.390 MHz 大量帧是 `=` 开头（所有开启 Messaging 的电台），这部分位置全部丢失。`@` 同样未支持。

**修复**：`=` 与 `!` 同格式，直接加入 dispatch；`@` 与 `/` 类似但时间戳是 6 位 DDHHMM，需要单独分支。

---

## 5. [真bug] 海拔 `/A=` 解析把真正的注释丢掉

**位置**：`mbdsdr_ai/ax25.py:655-669`

```python
alt_idx = payload.find('/A=')
if alt_idx >= 0:
    pos.altitude = int(payload[alt_idx+3:alt_idx+9])
...
comment_start = 19
if pos.course is not None:
    comment_start = 26
if alt_idx >= 0:
    comment_start = alt_idx + 9   # ← 注释从 /A= 之后开始
```

APRS 里 `/A=ddddd` 通常**追加在注释末尾**（例如 `"By the park/A=001000"`）。代码把 `comment_start` 设成 `alt_idx+9`，等于把 `/A=` **之前**的注释 `"By the park"` 扔掉，只保留 `/A=` 之后（通常为空）的尾巴。实跑验证：

```
!3954.25N/11624.44E-/Comment/A=001000
→ comment = ''   # 期望 'Comment'
```

---

## 6. [空壳] Mic-E 编码完全未实现

**位置**：全文无 `mic-e / MicE / mic_e` 任何匹配。

Mic-E 是 Yaesu VX-8 / FT-2D / FT-3D / FT-D 系列 HT 在 144.390 MHz 上最主流的位置报文格式（目的地呼号字段经过特殊编码承载压缩纬度/经度），国内 APRS 网里占比很高。本模块连解码器入口都没有，意味着**所有 FT 系列手台发出的位置帧会被当成普通 UI 帧、payload 里没有 `!/` 开头字符，`APRSPacket.from_ax25_frame` 不会填充 `position`，上游拿到一堆无法解析的裸 payload**。

---

## 7. [空壳] 对象 / 物品 / 遥测 / 状态 / 气象只有常量没有类

**位置**：`mbdsdr_ai/ax25.py:61-69`（常量定义）vs `800-803`（dispatch）

| 常量 | 值 | 有编码类 | 有解码 | dispatch 到 |
|---|---|---|---|---|
| `APRS_POSITION` | `!` | `APRSPosition` | ✅ | ✅ |
| `APRS_POSITION_TIME` | `/` | `APRSPosition` | ✅ | ✅ |
| `APRS_MESSAGE` | `:` | `APRSMessage` | ✅ | ✅ |
| `APRS_STATUS` | `>` | — | — | ❌ 仅裸 payload |
| `APRS_WEATHER` | `_` | `APRSWeather` | ❌ 无 decode | ❌ |
| `APRS_OBJECT` | `;` | — | — | ❌ |
| `APRS_ITEM` | `)` | — | — | ❌ |
| `APRS_TELEMETRY` | `T` | — | — | ❌ |
| `APRS_QUERY` | `?` | — | — | ❌ |
| `APRS_USERDEF` | `{` | — | — | ❌ |
| `APRS_THIRDPARTY` | `}` | — | — | ❌ |
| `APRS_POSITION_MSG` | `'` | — | — | ❌ |

`APRSWeather.encode`（`ax25.py:726-739`）能发但不能收；对象/物品/遥测/状态连类都没有。这意味着气象站、APRS 遥测传感器、对象信标（SX 网常见）全部只能看到一个 data_type 字符。

---

## 8. [建议] 时间戳格式与数据类型不匹配

**位置**：`mbdsdr_ai/ax25.py:567, 598-601, 612-618`

`APRSPosition.timestamp` 注释写 `DDHHMMz`（`ax25.py:567`），但 encode 在有 timestamp 时用 `/` 前缀（`ax25.py:599`）。APRS 规范：
- `/` 后接 **`HHMMSSz`**（UTC 时分秒）；
- `@` 后接 **`DDHHMM`**（日月时分）。

代码把 `DDHHMMz` 塞在 `/` 后面，和标准对不上。decode 侧 `payload[1:8]` 取 7 字符勉强能吃下 `HHMMSSz`，但和 encode 的 `DDHHMMz`（也是 7 字符）混在一起，自洽但不合规。

---

## 9. [建议] 目的地址 C/R 位被当成 H 位处理

**位置**：`mbdsdr_ai/ax25.py:113, 135`

SSID 字节 bit7 在**目的地址**上是 C/R（命令/响应）位，在**源/digi 地址**上才是 H（已中继）位。代码统一用 `0x80` 当 H 位。APRS UI 帧通常 C/R 置 1（命令方向），多数接收端不较真，但和严格 AX.25 互操作时会丢字段语义。

---

## 10. [建议] PID 存在性判定用了不全的魔数元组

**位置**：`mbdsdr_ai/ax25.py:180, 218`

```python
if self.control in (AX25_CTRL_UI, 0x00, 0x02, 0x04, 0x06, 0x08, 0x0A, 0x0C, 0x0E):
```

正确判据是"bit0=0 的 I 帧或 UI(0x03)"。元组里只列了 Nr=0 的低半段，漏了 0x10/0x20/…/0xE0。APRS 只用 UI，不影响现状；但真要做链路层连接会出错。

---

## 11. [建议] 独立 `hdlc_bit_stuff/unstuff` 不是严格互逆

**位置**：`mbdsdr_ai/ax25.py:250-300`

实跑：`hdlc_bit_unstuff(hdlc_bit_stuff(b'\xff\x00\x7e\x01'))` 返回 `b'\xff\x00\x7e\x01\x00'`（末尾多一个 `\x00`）。原因：位填充后位数不一定是 8 的整数倍，字节打包时末字节低位补 0，unstuff 不会把这个补位减掉。

**实际影响很小**：`AFSKModem._extract_frames`（`ax25.py:514`）是在 flag 之间按位切帧、再按 8 位成字节，末段 `range(0, len(unstuffed)-7, 8)` 主动丢弃不足 8 位的尾巴，所以真机解调路径不受影响。但作为公共工具函数，它不是干净的逆映射，写单测时会误判。

---

## 12. [建议] 解调侧未做频率偏移容差的明确窗口

**位置**：`mbdsdr_ai/ax25.py:443-444`

```python
threshold = (self.mark_freq + self.space_freq) / 2.0
level = (inst_freq < threshold).astype(np.uint8)
```

门限固定在 1700 Hz。真 144.390 MHz FM 鉴频输出受接收机调谐偏差、多径、多普勒影响，mark/space 中心可能整体偏移 ±100~300 Hz。多 PLL 起点重试（`ax25.py:534-536`）只能恢复位定时，不能恢复频偏；当频偏把两峰都推过门限时，判决会整体反相或抖动。direwolf 会做自动频率跟踪/带通选频，本模块没有。这是"录一个干净 WAV 能解、上真机就解不出"的次级嫌疑点。

---

## 13. [建议] FCS 校验是唯一门限，无弱信号/位错误重试

**位置**：`mbdsdr_ai/ax25.py:521-526`

只有 `frame.fcs_valid` 的帧才被接受。真实空中信号有位错误，FCS 是极强的校验（残错率 ~2⁻¹⁶），单一位错整帧丢弃是合理的；但没有做"位反转重试"或"软判决"，弱信号下灵敏度会比 direwolf 差。这是性能问题不是正确性问题。

---

## 14. 正确的部分（对照清单）

- **CRC-16/X.25**（`ax25.py:76-86`）：init=0xFFFF、poly=0x8408（0x1021 反射）、xorout=0xFFFF，实跑 `crc16_ccitt(b'123456789') = 0x906E`，与标准测试向量一致。FCS 小端打包 `<H` 正确（`ax25.py:188`）。
- **呼号左移 1 位编码**（`ax25.py:108`）：`ord(ch)<<1` 正确；SSID 字节保留位 `0x60` 置 1 正确。
- **HDLC 位填充方向**（`ax25.py:250-273`）：LSB 优先、连续 5 个 1 后插 0，符合 HDLC。
- **NRZI 编码**（`ax25.py:324-335`）：0=翻转、1=保持，与 Bell 202 AFSK 一致；解调侧 `raw_bits.append(0 if b != prev else 1)`（`ax25.py:541`）互逆。
- **Flag 识别**（`ax25.py:471-474`）：`01111110` = 0x7E，LSB 位序正确。
- **UI 控制字 0x03、PID 0xF0**（`ax25.py:29, 31`）正确。
- **KISS 转义**（`ax25.py:815-872`）FEND/FESC 双向转义正确。
- **位置经纬度 DDMM.hhN / DDDMM.hhE** 解析（`ax25.py:627-646`）索引正确，实跑 `!3954.25N/11624.44E-...` → lat=39.9042, lon=116.4073。
- **课程/速度 CCC/SSS**（`ax25.py:649-652`）在格式正确时解析正确（实跑 90°/60 节）。

---

## 15. 修复优先级建议

| 优先级 | 条目 | 理由 |
|---|---|---|
| P0 | §1 E 位双置 1 | 无 digi 的帧在真实接收机上直接拒收 |
| P0 | §2 WIDE2-2 切分 | 默认路径就错，真 digi 不转发 |
| P1 | §4 不认 `=`/`@` | 漏掉一半真实位置帧 |
| P1 | §6 Mic-E 未实现 | 漏掉大量 HT 位置帧 |
| P2 | §5 `/A=` 注释截断 | 信息丢失但不丢帧 |
| P2 | §3 timestamp 畸形帧 | 埋雷，触发条件窄 |
| P2 | §7 对象/遥测/气象解码空壳 | 功能缺失 |
| P3 | §8/§9/§10/§12/§13 | 协议合规与鲁棒性增强 |
