# ACARS (acarsdec + libacars) 移植学习笔记

> 本文件记录 MBDSDR 移植 **acarsdec** (TLeconte) 与 **libacars** (szpajder) 两个真实开源项目到 Python 的过程与协议要点。
>
> - 上游仓库: <https://github.com/TLeconte/acarsdec>
> - 上游仓库: <https://github.com/szpajder/libacars>
> - 本地 clone: `repos/acarsdec/`, `repos/libacars/`
> - Python 移植: `mbdsdr_ai/acars_decoder.py`
> - 测试: `tests/acars_test.py`

## 1. ACARS 是什么

ACARS = Aircraft Communications Addressing and Reporting System，航空器通信寻址与报告系统。
飞机通过 VHF 话音段（约 118–137 MHz）以短报文形式自动下传：
- OOOI（推出/起飞/落地/靠桥）
- ADS-C / 位置报告
- 发动机与航后文本
- CPDLC（管制员-飞行员数据链）

常用 VHF 标准信道（**不绑定任何地区台站**，仅列国际通用频点）：

| 频点 (MHz) | 说明 |
|-----------|------|
| 131.550   | 主用 ACARS 信道举例 |
| 131.725   | 常用 ACARS 信道 |
| 131.850   | 常用 ACARS 信道 |

> acarsdec 帮助里举例 `131.525 131.725 131.825`，这些是北美/欧洲常用频点。
> 实际频点由运营方分配，MBDSDR 不硬编码具体台站。

## 2. 空中接口物理层

| 参数 | 值 | 来源 |
|------|----|----|
| 调制 | MSK = 连续相位 FSK | msk.c |
| 波特率 | 1200 bps | acarsdec.h:31 `INTRATE 12500`，msk.c FLEN=sr/1200 |
| mark 音调 | 2400 Hz | 中心 1800 + 600 |
| space 音调 | 1200 Hz | 中心 1800 − 600 |
| 中心频率 | 1800 Hz | msk.c:81 `s = 1800.0/INTRATE*2π` |
| 每比特采样 | 12500/1200 ≈ 10.4 | msk.c:25 `FLEN=(INTRATE/1200)+1` |
| 位顺序 | LSB first | msk.c:53-63 `putbit()` |

### 2.1 msk.c 的解调结构

`repos/acarsdec/msk.c`:
- **NCO**（msk.c:80-83）：相位 `p += 2π*1800/sr + MskDf`
- **混频**（msk.c:86-90）：`inb[idx] = dm_buffer[n] * cexp(-j*p)`
- **匹配滤波**（msk.c:44-48, 102-107）：半波整流的 600 Hz 余弦
- **位时钟**（msk.c:94-100）：MskClk 累加，每 3π/2 采样一次（I/Q 交错，OQPSK 解释）
- **PLL**（msk.c:65-66, 129-130）：`PLLG=38e-4`, `PLLC=0.52`

MBDSDR 用等价的**双音锁相检测**：分别以 2400/1200 Hz 本振下变频、低通、比能量，
正=mark、负=space，再用过零沿做位同步。

## 3. 帧结构

控制字符来源 `repos/acarsdec/acars.c:22-27`：

```c
#define SYN  0x16   // 同步字（帧前导，连续两个）
#define SOH  0x01   // 帧起始
#define STX  0x02   // 文本起始
#define ETX  0x83   // 文本结束（最终块）
#define ETB  0x97   // 传输结束（非最终块）
#define DLE  0x7F   // 帧尾 DEL
```

完整帧（每个字节带偶校验位 bit7）：

```
┌──────┬──────┬──────┬───────┬────┬─────┬──────┬──────┬─────┬───────┬───────┬──────┬──────┬──────┐
│ SYN  │ SYN  │ SOH  │ mode  │reg │ ack │label │blkid │ STX │msg_num│ flight│ text │ ETX  │ crc  │
│ 0x16 │ 0x16 │ 0x01 │ (1B)  │(7B)│ (1B)│ (2B) │ (1B) │0x02 │ (10B) │ (6B)  │ ...  │0x83  │2B+DEL│
└──────┴──────┴──────┴───────┴────┴─────┴──────┴──────┴─────┴───────┴───────┴──────┴──────┴──────┘
                                                                      仅下行(block_id '0'-'9')
```

状态机来源 `acars.c:246-375`：
`WSYN → SYN2 → SOH1 → TXT → CRC1 → CRC2 → END`

## 4. CRC-16-CCITT

来源 `repos/libacars/libacars/crc.c:73-115` 与 `repos/acarsdec/syndrom.h`：

- 多项式：0x1021
- 初值：0x0000
- 算法：右移查表 `crc = (crc>>8) ^ table[(crc ^ byte)&0xff]`
- 校验：对 `[SOH后..ETX] + crc_lo + crc_hi` 求 CRC，余数 == 0 即通过
- CRC 字节顺序：**低字节先发，高字节后发**（本移植已用查表验证）
- DEL(0x7f) 不参与 CRC

## 5. 字段解析

来源 `repos/libacars/libacars/acars.c:272-486`：

1. 去末尾 DEL（acars.c:290）
2. CRC 校验，去 2 字节 CRC（acars.c:296-299）
3. 逐字节 `& 0x7f` 去偶校验位（acars.c:303）
4. 末尾应是 ETX(0x03)/ETB(0x17)（acars.c:308）
5. 依次取 mode(1) + reg(7) + ack(1) + label(2) + block_id(1)
6. 下行额外：msg_num(3) + seq(1) + flight_id(6)

特殊值映射（acars.c:334-346）：
- ACK(0x06) → `^`
- NAK(0x15) → `!`
- label 第二字节 0x7f → `d`

## 6. MBDSDR 移植文件

| 文件 | 说明 |
|------|------|
| `mbdsdr_ai/acars_decoder.py` | ACARSMDemod / ACARSMessageParser / ACARSDecoder |
| `tests/acars_test.py` | 15 个测试：MSK/解析/CRC/同步/参数 |
| `mbdsdr_ai/agent.py` | 注册 `acars_decode_iq` / `acars_parse_message` / `acars_msk_demod` |

## 7. 测试结论

```
tests/acars_test.py: 15 passed
  - 常量：波特率1200/mark2400/space1200/中心1800/控制字
  - CRC：查表首项匹配 libacars；好帧余数0；翻转1bit余数非0
  - 解析：下行 reg/mode/label/block_id/flight 全对；上行无 flight
  - MSK：交替位恢复；2400/1200 能量判决方向正确
  - 端到端：加噪合成信号 → 检测到 SOH → CRC 通过 → 文本正确
```
