# multimon-ng 真实解码：从 C 源码到 `mbdsdr_ai/multimon_decoders.py` 的落地笔记

> 本地参考克隆：`repos/multimon-ng/`（EliasOenal/multimon-ng）
> 配套实现：`mbdsdr_ai/multimon_decoders.py`
> 往返验证：`tests/multimon_roundtrip.py`（6 项全过）
>
> 本文件只记录**这次真读 .c 源码后逐行对齐到 Python 的常量与位域**，
> 每条都给 `file:line`。覆盖 POCSAG(BCH 纠错)、AFSK1200、DTMF、ZVEI-1。

---

## 1. POCSAG 寻呼

### 1.1 关键码字常量

| 项 | 值 | 来源 |
|---|---|---|
| 同步码字 `POCSAG_SYNC` | `0x7CD215D8` | `pocsag.c:57` |
| 空闲码字 `POCSAG_IDLE` | `0x7A89C197` | `pocsag.c:58` |
| 消息/地址标志位 | bit31 = `0x80000000` | `pocsag.c:63` `POCSAG_MESSAGE_DETECTION` |
| 一批字数 | 17 字（字0=同步，字1..16=数据帧） | `pocsag.c:853-855` |
| 比特率 | 512 / 1200 / 2400 bps | `demod_poc5.c:36`、`demod_poc12.c`、`demod_poc24.c` |

### 1.2 32-bit 码字布局

来源：`bch.h:69-72`、`bch.c:201-206`。

```
 bit31 ────────────── bit11 │ bit10 ── bit1 │ bit0
   21 位数据字段            10 位 BCH 奇偶    整体偶校验
```

- 地址码字：bit31(R)=0；bit30..13 为地址高位；bit12..11 为 2 位功能位。
- 消息码字：bit31(R)=1；bit30..11 为 20 位负载（5 个 nibble）。

### 1.3 BCH(31,21,2) 纠错

| 项 | 值 | 来源 |
|---|---|---|
| 数据位 / 奇偶位 | 21 / 10 | `bch.c:46-47` |
| 码长 | 31 (=2^5−1) | `bch.c:48` |
| POCSAG 生成多项式 | `0x769`（八进制 03551） | `bch.c:54` `POCSAG_POLY` |
| 等价传统记法 | `0xED2000 = 0x769<<13` | 推导自上 |
| 可纠错数 | 最多 2 bit | `bch.c:34` |

算法（`bch.c:228-268`）：
- 编码：`bch_pocsag_encode` 用查表 `pocsag_parity_tbl[]`，对每个置位数据位异或其 10 位奇偶。
- 纠错：`bch_pocsag_correct` 先算 11 位伴随式（10 位 BCH 伴随 + 偶校验位拼成 `0x400`），
  再查 `pocsag_err_tbl[]` 得到错误图样，异或回码字。
- 单 bit 错误伴随式带 `0x400`（必致偶校验错）；双 bit 错误两单 bit 伴随式异或，偶校验位抵消
  （`bch.c:461-473`）。

Python 里 `_POCSAGBCH` 完全照搬这两张表的构建过程（多项式除法求奇偶/伴随）。

**校验矢量**（已验证）：
- `0x7CD215D8`(SYNC) 与 `0x7A89C197`(IDLE) 伴随式 = 0（合法码字）。
- 任意 21bit 数据编码后干净还原；注入 1bit/2bit 错误必纠回原码字。

### 1.4 地址 / 消息解码

来源：`pocsag.c:906-960`。

- 功能位：`function = (cw >> 11) & 3`（`pocsag.c:916`）。
- 地址：`address = ((cw >> 10) & 0x1FFFF8) | ((rxword >> 1) & 7)`
  （`pocsag.c:917`）——低 3 位来自批内字位置，高 17 位来自码字。
- 消息负载：`data = (cw >> 11)`，按奇/偶 `numnibbles` 分支把 20bit 拆成 5 nibble 累积
  （`pocsag.c:951-959`）。
- BCD 数字表：`conv_table = "084 2.6]195-3U7["`（`pocsag.c:454`）。
- ASCII 文本：每 7bit 一个字符，`rev7` 反转位序（`pocsag.c:475-488`）。

---

## 2. AFSK 1200 (Bell-202)

来源：`demod_afsk12.c`。

| 项 | 值 | 来源 |
|---|---|---|
| Mark 频率 | 1200 Hz | `demod_afsk12.c:39` |
| Space 频率 | 2200 Hz | `demod_afsk12.c:40` |
| 波特率 | 1200 | `demod_afsk12.c:42` |
| 相关窗长 | `FREQ_SAMP/BAUD` ≈ 18 样本/bit | `demod_afsk12.c:47` |

解调（`demod_afsk12.c:92-118`）：
- 对 mark / space 各做正交相关（cos/sin 本地振荡 × 接收信号）。
- 判决量 `f = |corr_mark|² − |corr_space|²`；`f>0` 判 mark。
- 位同步：在 dcd 跳变处按 `±SPHASEINC/8` 微调 sphase，溢出时采一个比特
  （`demod_afsk12.c:103-110`）。

---

## 3. DTMF

来源：`demod_dtmf.c`。

| 组 | 频率 Hz | 来源 |
|---|---|---|
| 高频列 | 1209 / 1336 / 1477 / 1633 | `demod_dtmf.c:58` |
| 低频行 | 697 / 770 / 852 / 941 | `demod_dtmf.c:59` |

- 字符表 `"123A456B789C*0#D"`（`demod_dtmf.c:55`）。
- 索引：`(high_idx & 3) | (low_idx << 2)`（`demod_dtmf.c:129`）。
- 能量法：每个频率做正交相关 `I²+Q²`，高/低组各取最大，次大不得超过最大的 10%
  （`demod_dtmf.c:73-90`）。

---

## 4. ZVEI-1 选呼

来源：`demod_zvei1.c:27-32` + `selcall.c`。

16 音调表（索引即 hex 0..F）：

```
2400,1060,1160,1270,1400,1530,1670,1830,
2000,2200,2800, 810, 970, 885,2600, 680
```

- 10ms 分块（`selcall.c:35` `BLOCKLEN = SAMPLE_RATE/100`），4 块平滑。
- 每块 16 个正交能量取最大；主音能量需 > 全局能量 40%（`selcall.c:96-99`）。
- 选呼通常 5 个连续音调，输出 5 位 hex 码。

---

## 5. 注册的工具

`register_multimon_tools(registry)` 注册：

| 工具名 | 作用 |
|---|---|
| `pocsag_decode` | 喂入一批 17 个 32bit 码字，输出地址/功能/数字/文本消息 |
| `pocsag_bch_correct` | 单码字 BCH(31,21,2) 纠错 |
| `afsk1200_demod` | Bell-202 AFSK1200 正交相关解调 |
| `dtmf_decode` | DTMF 双音识别 |
| `zvei_decode` | ZVEI-1 五音调选呼解码 |

---

## 6. 往返验证结论

`tests/multimon_roundtrip.py` 6 项全过：

1. BCH 校验矢量：SYNC/IDLE 伴随=0，1/2bit 必纠。
2. POCSAG 全链路：同步+地址(12345)+BCD 消息 "12345" 解出一致。
3. POCSAG BCH 2bit 注入纠错后地址(7777)/消息 "98765" 不变。
4. AFSK1200 调制→解调，数据段 16/16 比特对齐匹配。
5. DTMF 16 个按键全部识别正确。
6. ZVEI 三组 5 音调选呼码全部识别正确。
