# fldigi 多模式数字通信移植笔记

> 来源仓库: [w1hkj/fldigi](https://github.com/w1hkj/fldigi) (GPLv3)
> 本地镜像: `repos/fldigi/`
> 移植模块: `mbdsdr_ai/fldigi_modes.py`
> 往返测试: `tests/fldigi_roundtrip.py`

fldigi 是业余无线电最全面的多模式数字通信软件，支持 PSK31/63/125、RTTY、
MFSK、Olivia、FeldHell/Hellschreiber、Thor、MT63、Throb、Contestia 等数十种模式。
本笔记记录从 fldigi 真实 `.cxx` 源码移植到 MBDSDR Python 栈的关键事实。

---

## 1. PSK31 (PSK31)

### 关键常量
| 量 | 值 | 来源 |
|---|---|---|
| 波特率 | 31.25 baud | `psk/psk.cxx:382-387` symbollen=256, sr=8000 → 8000/256=31.25 |
| 采样率 | 8000 Hz | `psk/psk.cxx:370` |
| 调制方式 | 差分 BPSK (DBPSK) | `psk/psk.cxx:2252` symbol = prev × sym_vec_pos[] |
| 字符间隔 | "00" 两位 | `psk/psk.cxx:2484-2485` tx_bit(0); tx_bit(0) |

### Varicode 编码
- 来源: `psk/pskvaricode.cxx:28-288` (varicodetab1[], 256 项)
- 可变长度比特串：高频字符短（e="11", t="101"），低频字符长
- 编码规则：每个字符后跟 "00" 作为分隔；Varicode 自身不含 "00" 子串
- 解码: `psk/psk.cxx:1113-1135` 收满 bit 后，`(shreg & 3) == 0` 时查 `shreg>>2`

### 差分映射（fldigi 约定）
```
bit=1 → sym=2 → sym*4=8 → sym_vec_pos[8]=+1 (无相位跳变)
bit=0 → sym=0 → sym*4=0 → sym_vec_pos[0]=-1 (相位翻转 π)
```
即 bit=1 同相，bit=0 反相。差分解调 = sign(Re(s_k · conj(s_{k-1})))。

---

## 2. RTTY (Baudot-Murray / ITA-2)

### 关键常量
| 量 | 值 | 来源 |
|---|---|---|
| 频偏 | 170 Hz (默认) | `cw_rtty/rtty.cxx:83` SHIFT[3]=170 |
| 波特率 | 45.45 baud (默认) | `cw_rtty/rtty.cxx:85` BAUD[1]=45.45 |
| Mark 频率 | 2125 Hz | 业余 RTTY 标准 |
| Space 频率 | 2295 Hz | mark+170 |
| 帧格式 | start(0) + 5 data(LSB first) + 1.5 stop(1) | `cw_rtty/rtty.cxx:480-510` |

### ITA-2 字母表
- 来源: `cw_rtty/rtty.cxx:62-79`
- `letters[32]` / `figures[32]` 两张表，索引 = 5bit 符号
- LTRS 符号 = 0x1F (11111), FIGS 符号 = 0x1B (11011) — `rtty.cxx:52,56`
- 发送数字前自动插入 FIGS，发送字母前插入 LTRS
- U.S. figures 表: `3-E`, `-A`, `8-I`, `7-U`, `$D`, `4-R`, `,N`, `!F`, `:C`, `(K`, `5-T`, `"Z`, `)L`, `2-W`, `#H`, `6-Y`, `0-P`, `1-Q`, `9-O`, `?B`, `&G`, `.M`, `/X`, `;V`

---

## 3. MFSK (Multiple Frequency Shift Keying)

### 关键常量
| 模式 | symlen | basetone | 音调数 | 波特率 | 音调间隔 | 来源 |
|---|---|---|---|---|---|---|
| MFSK8 | 1024 | 128 | 32 | 7.8125 | 7.8125 Hz | `mfsk/mfsk.cxx:191-198` |
| MFSK16 | 256 | 32 | 16 | 31.25 | 31.25 Hz | `mfsk/mfsk.cxx:209-216` |
| MFSK31 | 256 | 32 | 8 | 31.25 | 31.25 Hz | `mfsk/mfsk.cxx:200-207` |

- tonespacing = samplerate/symlen — `mfsk/mfsk.cxx:291`
- basefreq = samplerate × basetone / symlen — `mfsk/mfsk.cxx:292`
- 非相干 FFT 检测：每符号周期内找能量最大的音调 bin — `mfsk/mfsk.cxx:294` sfft

---

## 4. FeldHell (Hellschreiber)

### 关键常量
| 量 | 值 | 来源 |
|---|---|---|
| 列速率 | 17.5 列/秒 | `feld/feld.cxx:154` feldcolumnrate=17.5 |
| 列数/字符 | 14 | `include/feld.h:42` FELD_COLUMN_LEN=14 |
| 行数 | 7 | 字体 7x14 |
| 调制方式 | 振幅键控 (AM) | 像素亮 → 载波幅度大 |
| 字体 | 7x7-14 | `feld/Feld7x7-14.cxx` |

- 每列时长 = 1/17.5 = 57.14 ms
- 每字符 = 14 列 = 0.8 秒
- 接收：包络检波 → 列亮度 → 字体匹配

---

## 5. Olivia MFSK / Thor

### Olivia
- 来源: `olivia/olivia.cxx:324-329`
- 标准音调间隔 125 Hz, 31.25 baud
- RS(15,5) 纠错 + 交织
- 常用模式: 16/500, 16/1000, 8/250, 4/125

### Thor
- 来源: `thor/thor.cxx`, `psk/psk.cxx:87-89`
- K=15 卷积码，POLY1=0o44735, POLY2=0o63057
- 模式: Thor-M/16/8/4

---

## 6. 移植的 Python 类

| 类 | 功能 |
|---|---|
| `Varicode` | PSK31 Varicode 编解码表 |
| `PSK31Modem` | DBPSK 调制/差分解调 |
| `ITA2` | ITA-2 字母/数字切换编解码 |
| `RTTYModem` | 2FSK 调制/正交非相干检测 |
| `MFSKModem` | MFSK8/16/32 音调调制/Goertzel 检测 |
| `FeldHellDecoder` | AM 调制/包络检波/字体匹配 |
| `OliviaMFSK` | Olivia 参数表 |
| `ThorMode` | Thor 参数表 |

## 7. 注册的 ToolRegistry 工具

- `psk31_encode` — 文本 → Varicode 比特串
- `psk31_decode` — PSK31 往返验证
- `rtty_decode` — RTTY 往返验证
- `mfsk_decode` — MFSK 符号往返
- `feldhell_decode` — FeldHell 文本重建

## 8. 往返测试

```bash
python3 tests/fldigi_roundtrip.py
```

全部通过：
- Varicode 95 个可打印字符往返
- PSK31 DBPSK 多文本往返
- ITA-2 字母/数字切换
- RTTY 2FSK 多文本往返
- MFSK8/16/32 符号还原
- FeldHell 框架重建
