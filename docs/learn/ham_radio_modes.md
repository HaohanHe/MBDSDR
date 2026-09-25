# 业余无线电 5 模式学习笔记 —— pat / ARDOP / JS8Call / Xastir / minimodem(Bell103)

> 本笔记对应 MBDSDR 学习流水线第二批移植：
>
> | 模式 | 上游 | 本地克隆 | Python 移植 | 测试 |
> |------|------|----------|-------------|------|
> | pat (Winlink B2F) | la5nta/pat | `repos/pat/` | `mbdsdr_ai/pat_adapter.py` | `tests/ham_modes_roundtrip.py` |
> | ARDOP OFDM | hamarituc/ardop | `repos/ardop/` | `mbdsdr_ai/ardop_adapter.py` | 同上 |
> | JS8Call | js8call/js8call | `repos/js8call/` | `mbdsdr_ai/js8call_adapter.py` | 同上 |
> | Xastir APRS | xastir/xastir | `repos/xastir/` | `mbdsdr_ai/xastir_adapter.py` | 同上 |
> | minimodem Bell103 | kamalmostafa/minimodem | `repos/minimodem/` | `mbdsdr_ai/minimodem_adapter.py`(追加) | 同上 + `tests/minimodem_test.py` |
>
> 运行：`python3 tests/ham_modes_roundtrip.py`（全绿）。

---

## 1. pat —— Winlink 电子邮件电台（B2F 转发协议）

### 它是什么
pat 是 Linux/macOS 上的 Winlink 客户端（Go 写的），让业余无线电操作员像发邮件一样
通过 HF 电台收发文本和附件，不需要互联网。它通过各种 TNC（ARDOP/Pactor/VARA）
或 Telnet 接入 Winlink RMS（远程邮件服务器）。

### 传输模式枚举（来源 `pat app/app.go:41-47`）
| scheme | 介质 | 说明 |
|--------|------|------|
| `ardop` | HF | ARDOP OFDM 调制解调器 |
| `pactor` | HF | SCS Pactor/Pactor-II |
| `varahf` / `varafm` | HF/VHF | VARA 专有调制 |
| `ax25` | VHF | 分组包 1200/9600 baud |
| `telnet` | Internet | RMS Telnet 网关 |

### B2F（FBB-Forward）消息信封
Winlink RMS 之间用 **FBB 转发协议**交换邮件。一封邮件的帧结构：

```
[<FROMCALL>|<TOCALL>|<SUBJECT>|<YYYYMMDDhhmmss>]
:FBB:Winlink B2F via ...
<空行>
<正文>
Begin-base64 <filename>
<BASE64 附件数据>
End-base64
```

- 信封行用 `|` 分隔四个字段，时间戳 14 位 UTC。
- MID（Message ID）是 RMS 分配的数字消息号，列表帧形如
  `M <mid> <size> <flags> <subject>`（来源 `pat app/exchange.go:121,141`）。
- 附件用 `Begin-base64` / `End-base64` 包裹（`pat app/attachment.go`）。

本移植 `B2FMessage.encode()/decode()` 与 `encode_mid_list()/decode_mid_list()`
实现了上述信封的往返。

---

## 2. ARDOP —— HF OFDM 数字调制

### 它是什么
ARDOP（Amateur Radio Digital Open Protocol）是 John Wiseman G8BPQ 设计的 HF
数字调制解调器，Winlink 用它在短波上做 ARQ（自动重传）数据传输。物理层是
**多载波 OFDM + QAM/PSK + Reed-Solomon FEC**。

### 关键常量（与 `repos/ardop/ARDOP2/` 逐行核对）
| 常量 | 值 | 来源 |
|------|----|----|
| 采样率 | 12000 Hz | `ALSASound.c:1300`, `CalcTemplates.c:93` |
| 中心频率 | 1500 Hz | `CalcTemplates.c:186`(index 5) |
| 11 个载频 | 600,800,1000,1200,1400,**1500**,1600,1800,2000,2200,2400 Hz | `CalcTemplates.c:186` |
| 导频 | 中心 1500 Hz（10 载频模式排除它） | `CalcTemplates.c:191` |
| 双音前导 | 1475/1525 Hz，50 baud | `Modulate.c:48` |
| 符号时长 | 120 采样=100baud；240 采样=50baud | `CalcTemplates.c:207` |
| 帧同步字 | `0x1A 0x59`（0x1A=D4FSK_500_50_E） | `ARDOPC.h:362`, `Modulate.c:91-118` |
| FEC | RS(16,12) ID 帧；RS(160,120) 16QAM | `ARDOPC.c:749,824` |

### 帧结构
```
[双音 leader 1475/1525 Hz] [同步字 0x1A 0x59 (4FSK)] [QAM-OFDM 数据符号]
```
- leader 用于自动增益和位同步；
- 同步字两字节以 50 baud 4FSK（1350/1450/1550/1650 Hz）发送；
- 数据在 10 个数据载频上用 4/16/64-QAM 叠加（导频用中心 1500Hz）。

### 连接状态机（`pktSession.c`）
```
IDLE -> LISTENING -> CONNECT_REQ -> CONNECTED -> DATA -> DISC -> IDLE
        (SearchingForLeader ofdm.c:986)  (ConAck)  (PktFrameData)
```
本移植 `ARDOPSession` 用枚举实现了这条迁移链。

---

## 3. JS8Call —— JS8 消息模式（WSJT-X/FT8 衍生）

### 它是什么
JS8Call 是 KC3AWI 基于 WSJT-X/FT8 物理层改造的键盘-to-键盘数字聊天模式，
在 HF 弱信号下做短消息（呼号、网格、自由文本）。

### 关键常量（`repos/js8call/commons.h`）
| 常量 | 值 | 来源 |
|------|----|----|
| 采样率 | 12000 Hz | `commons.h:15` |
| 每帧符号数 | 79 | `commons.h:29` (JS8_NUM_SYMBOLS) |
| Slow/Normal/Fast/Turbo 每符号采样 | 3840/1920/1200/600 | `commons.h:36-49` |
| 音调间隔 | fs / symbolSamples | `JS8Submode.cpp:73` |
| 8-FSK 音调数 | 8（FT8 衍生，任务约定） | — |
| 有效载荷 | 77 bit（任务约定） | — |

> 注：官方 JS8Call Normal=6.25 baud；本学习流水线按任务硬约束采用
> FT8 衍生的 15.625/31.25/62.5 baud 三档 8-FSK 模型，77-bit 载荷布局为
> `type(3) | de呼号(28) | to呼号(28) | 网格(15) | spare(3)`。

### 呼号/网格编码
- 呼号 28bit（对齐 wsjtx `unpack28.f90`，本仓库 `ft8_callsign.py`）：
  `C1(36) C2(36) C3(10) C4(27)^3`。
- 4 字符 Maidenhead 网格 15bit：前两字母 A-R(18)，后两数字 0-9(10)。

---

## 4. Xastir / YAAC —— APRS 地图与对象

### 它是什么
Xastir 是 Linux 上的 APRS 地图工作站：接收 APRS 分组、在地图上画电台位置、
跟踪对象/物品、发气象报告。YAAC 是 Java 实现的同类。

### APRS 对象/物品帧（`xastir src/objects.c:142`）
```
对象: ;<9字符名><*|_><8字节纬度><符号表><9字节经度><符号码><注释>
物品: )<最长9字符名> <8字节纬度><符号表><9字节经度><符号码><注释>
```
- `*` = 对象存活，`_` = 对象死亡（killed）。
- 位置用未压缩格式 `DDMM.hhN`（纬度 8 字节）/ `DDDMM.hhW`（经度 9 字节）。
- 符号表 `/` 选标准符号集，`\` 选备用集；符号码如 `>`=电台车。

### 地理围栏
本移植用 Haversine 大圆距离实现圆形围栏：给定圆心 (lat,lon) 和半径（米），
判定一组点是否在围栏内。Xastir 原生还支持矩形/走廊围栏（`objects.c`
area_object），这里先实现最常用的圆形。

---

## 5. minimodem 补全 —— Bell 103 300 baud

### 它是什么
Bell 103 是 1962 年贝尔公司的 300 bps 电话调制解调器标准，也是 HF/VHF 电台
常用的 300 baud FSK 呼叫音。本仓库已有 minimodem 移植（FSKModem/ASCIIFrame/
BaudotCodec），本次**追加** Bell103 专用模式，不改动现有 Bell202/ASCII/Baudot 接口。

### Bell 103 参数（`minimodem.c:911-921`）
| 参数 | 值 |
|------|----|
| 速率 | 300 baud |
| mark(=1) | 1270 Hz |
| space(=0) | 1070 Hz |
| shift | 200 Hz |
| 分析带宽 | 50 Hz |
| 帧 | 8N1 UART（1 起始 + 8 数据 LSB + 1 停止） |

### 追加内容（仅追加，签名不变）
- 常量 `BELL103_BAUD/MARK_FREQ/SPACE_FREQ/BAND_WIDTH`
- `bell103_300baud_modem()` 工厂
- `bell103_modulate_text()/bell103_demodulate_text()` 端到端文本函数
- 模块级 `register_minimodem_tools(registry)` 注册 `bell103_modulate_text` /
  `bell103_demodulate_text` 两个新工具

回归保护：`tests/ham_modes_roundtrip.py` 同时验证 Bell202 (1200/2200Hz) 与
Baudot 字母/数字换档仍正常。

---

## 测试覆盖矩阵

| 测试 | 验证点 |
|------|--------|
| `test_pat_b2f_roundtrip` | B2F 信封+附件往返、MID 列表、6 传输模式 |
| `test_ardop_sync_and_constellation` | QAM(4/16/64) 星座往返、同步字 0x1A0x59 检测、状态机闭环 |
| `test_js8_message_roundtrip` | 呼号/网格编码、77bit 消息往返、8-FSK 符号、4 子模式 |
| `test_xastir_aprs_roundtrip` | 位置编解码、对象/物品帧往返、地理围栏内外 |
| `test_bell103_roundtrip` | Bell103 文本往返、Bell202/Baudot 无回归 |
| `test_registration_counts` | 5 个适配器各注册 ≥2 个工具 |
