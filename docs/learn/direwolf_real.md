# Direwolf 真实源码移植笔记（AX.25 / AFSK / APRS）

> 本笔记记录从 `repos/direwolf/src` 真实 C 源码校准 MBDSDR 协议栈的过程与关键结论。
> 所有常量/公式均标注 `file:line`，禁止凭 README 猜。

## 1. FCS / CRC-16（AX.25 帧校验）

**来源：`repos/direwolf/src/fcs_calc.c:76-87`**

```c
unsigned short fcs_calc (unsigned char *data, int len) {
    unsigned short crc = 0xffff;
    for (j=0; j<len; j++)
        crc = (crc >> 8) ^ ccitt_table[(crc ^ data[j]) & 0xff];
    return crc ^ 0xffff;
}
```

- 这是 **CRC-16/X.25**（反射式）：多项式 `0x1021`（正常）→ 反射 `0x8408`（表项 `table[0x80]=0x8408`，`fcs_calc.c:52`）。
- 初值 `0xFFFF`，输入/输出均反转，最终异或 `0xFFFF`。
- 表来自 RFC1549（`fcs_calc.c:34`）。
- **校验矢量**：`"123456789"` → `0x906E`（已逐字节与 C 表核对一致）。
- Python 位级实现（`mbdsdr_ai/ax25.py:crc16_ccitt`）与 C 表驱动结果完全一致。

## 2. 地址字段（7 字节/站）

**来源：`ax25_pad.c:67-145`，掩码在 `ax25_pad.h:118-127`**

- 前 6 字节：呼号大写、空格补齐、**左移 1 位**（LSB 恒 0）。
- 第 7 字节：`C/RR SSID 0`
  - `SSID_H_MASK=0x80`（bit7）：目的站=C/R 命令位；中继站=has-been-repeated(H) 位。
  - `SSID_RR_MASK=0x60`（bits6-5）：保留位，恒 `11`。
  - `SSID_SSID_MASK=0x1E`（bits4-1）：SSID 0-15。
  - `SSID_LAST_MASK=0x01`（bit0）：地址扩展位，**仅地址段最后一站=1**。
- 目的站 C/R=1，源站 C/R=0（`ax25_pad.c:90-95`）。
- 目的站永远不是最后地址（L=0）；无中继时源站是最后地址（L=1）。
- **LSB first** 发送（`hdlc_rec.c:511`）。

## 3. HDLC 位填充 / 标志 / abort

**来源：`hdlc_rec.c`**

- 标志 `0x7E` = `01111110`（`hdlc_rec.c:526`）。
- 位填充：连续 5 个 `1` 后插入一个 `0`；接收端 `(pat_det & 0xfc)==0x7c` 即 `0111110xx` 时丢弃该 `0`（`hdlc_rec.c:695-705`）。
- abort：`0xFE` = `11111110`（连续 7 个 1），丢弃当前帧（`hdlc_rec.c:677`）。
- **坑**：位填充后位长不一定是 8 的整数倍，打包成字节时末尾补 0；去填充后必须丢弃尾部不完整字节，否则多一个 `0x00`（已在 `bits_to_bytes`/`hdlc_bit_unstuff` 修正）。

## 4. AFSK Bell 202（1200 baud）

**来源：`audio.h:442,470-472`**

| 常量 | 值 | 位置 |
|---|---|---|
| Mark 频率 | 1200 Hz | `audio.h:470 DEFAULT_MARK_FREQ` |
| Space 频率 | 2200 Hz | `audio.h:471 DEFAULT_SPACE_FREQ` |
| 波特率 | 1200 | `audio.h:472 DEFAULT_BAUD` |
| 默认采样率 | 44100 | `audio.h:442 DEFAULT_SAMPLES_PER_SEC` |

- 48000 采样率也支持（`audio.h:447-450`，SDR 常用）。
- 预滤波带宽：`f1=min-0.155*baud`, `f2=max+0.155*baud`，prefilter_baud=0.155（`demod_afsk.c:274,450-451`）。
- **NRZI**：`0`=频率翻转，`1`=不翻转（`hdlc_rec.c:481-499`：`dbit = (raw == prev_raw)`）。

## 5. APRS 位置格式

### 5.1 未压缩（DDMM.hhN / DDDMM.hhW）
- 纬度固定 8 字符 `DDMM.hhN`，符号表 1 字符，经度固定 9 字符 `DDDMM.hhW`，符号代码 1 字符。
- 判定：位置首字符是数字 → 未压缩（`decode_aprs.c:926`）。

### 5.2 压缩（base-91）
**来源：`decode_aprs.c:3518-3582`**

```
lat = 90 - ((y0-33)*91^3 + (y1-33)*91^2 + (y2-33)*91 + (y3-33)) / 380926.0   (line 3522)
lon = -180 + ((x0-33)*91^3 + (x1-33)*91^2 + (x2-33)*91 + (x3-33)) / 190463.0  (line 3535)
alt = 1.002^((c-33)*91 + (s-33))                                              (line 3569)
```
- 首字符非数字 → 压缩（`decode_aprs.c:970`）。

### 5.3 MIC-E（`'` 或 `` ` ``）
**来源：`decode_aprs.c:1403-1656`**
- 纬度编进目的地址 6 字符：`lat = d0*10 + d1 + (d2*1000+d3*100+d4*10+d5)/6000`（line 1440）。
- N/S 由目的第 4 字符决定；经度在信息字段前 3 字节；E/W 由目的第 6 字符决定。
- 符号顺序相反：info[7]=symbol_code, info[8]=sym_table_id（line 1610-1611）。
- 速度(节)/航向在 info[4:7]（line 1640-1656）。

## 6. 气象报告

**来源：`decode_aprs.c:2956-3200`**
- 无位置 `_`：`_` + 8 字节时间戳 + 气象字段。
- 字段标签：`c`=风向 `s`=风速(节) `g`=阵风 `t`=温度F `r`=1h雨(1/100in) `p`=24h雨 `P`=午夜起雨 `h`=湿度 `b`=气压(1/10hPa)。
- 符号代码 `_` 即气象报告标志（`decode_aprs.c:930,974`）。

## 7. 往返验证结果

`python3 tests/ax25_aprs_roundtrip.py`：
- CRC 矢量 `0x906E` ✓
- 帧构建→FCS→解帧 ✓
- HDLC 位填充/去填充 ✓
- **AFSK 调制→加噪(SNR 10dB)→解调：30/30 = 100%**（要求 >80%）
- 未压缩位置/气象、压缩位置、无位置气象、消息解析全部 ✓
