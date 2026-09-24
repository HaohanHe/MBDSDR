# dump1090 真实解码：从 C 源码到 `mbdsdr_ai/adsb.py` 的落地笔记

> 本地参考克隆：`repos/dump1090/`（FlightAware/mutability 版）
> 配套实现：`mbdsdr_ai/adsb.py`（完整解码器）、`mbdsdr_ai/adsb_lite.py`（轻量在线版）
> 往返验证：`tests/adsb_real_roundtrip.py`（35 项全过）
>
> 本文件只记录**这次真读 .c/.h 源码后逐行对齐到 Python 的常量与位域**，
> 每条都给 `file:line`，便于日后对照。泛泛的协议介绍见同目录 `dump1090.md`。

---

## 1. CRC-24：多项式与算法

| 项 | 值 | 来源 |
|---|---|---|
| 生成多项式 | `0xFFF409` | `repos/dump1090/crc.c:28` `#define MODES_GENERATOR_POLY 0xfff409U` |
| 校验函数 | 表驱动 `modesChecksum` | `repos/dump1090/crc.c:65` |
| 表生成 | 字节放高 8 位，循环查 bit23 | `repos/dump1090/crc.c:31-45` `initLookupTables` |

要点：
- 网上常把这个多项式误写成 `0xFFFA04`，**正确是 `0xFFF409`**（crc.c:28 写死）。
- `modesChecksum` 先对前 `n-3` 字节跑表，再异或最后 3 字节（PI/奇偶域）。
  对完整 112bit 跑，合法 DF17 报文余数为 **0**。
- Python 里我用**位级长除法**（`mode_s_crc24`），和 C 表驱动逐位等价——
  已用真实帧交叉验证余数一致。

**真实测试向量**：`8D40621D58C382D690C8AC2863A7`
- 整帧 syndrome = `0x000000`（CRC 通过）✓
- 翻转任意 1 bit 后 syndrome ≠ 0（证明校验真的在工作）✓

---

## 2. Preamble 检测

来源：`repos/dump1090/demod_2400.c:147-218`。

- 4 个 0.5µs 脉冲位于 **0.0 / 1.0 / 3.5 / 4.5 µs**，8µs 结束后才是数据。
  （demod_2400.c:147-151 给了 5 种采样相位下的理想样本形态表。）
- 快速门限：`preamble[0] < preamble[1]` 且 `preamble[12] > preamble[13]`
  （demod_2400.c:155）。
- 保护窗必须安静：`preamble[5..8]`、`preamble[14..18]` 不得高于 `high`
  （demod_2400.c:208-218）。
- SNR 粗判：`base_signal*2 >= 3*base_noise`（约 3.5dB）（demod_2400.c:204）。

Python 端 `_find_preamble` 用四脉冲模板归一化互相关 + 保护窗对比度判决复刻这套逻辑。

---

## 3. 帧结构与位域（getbits 是 1-based 闭区间）

来源：`repos/dump1090/mode_s.h:104` `getbits(data, firstbit, lastbit)`。

- `firstbit/lastbit` 都是 **1-based、闭区间、MSB-first**。
- 长帧 14 字节：`[DF(5bit)+CA(3bit)][ICAO 24bit][ME 56bit][CRC 24bit]`。
- DF17/18 的 ICAO 在**明文 AA 域 bit8-32**（mode_s.c:624-626），
  不是从 syndrome 反推。

### ME 字段 56bit 位域（1-based，对照 mode_s.c）

| 内容 | 位域 | 来源 |
|---|---|---|
| TC 类型码 | ME bit 1-5 | mode_s.c:1505 dispatch |
| 呼号（TC1-4） | ME bit 9..14,15..20,...,51..56（8×6bit） | mode_s.c:805-812 |
| 子类型 mesub（速度） | ME bit 6-8 | mode_s.c:863 |
| 地速 E/W 方向/幅值 | bit14 方向，bit15-24 幅值 | mode_s.c:886-891 |
| 地速 N/S 方向/幅值 | bit25 方向，bit26-35 幅值 | mode_s.c:886-891 |
| 航向+空速（子类型3/4） | bit14 有效，bit15-24 航向；bit26-35 空速 | mode_s.c:914-924 |
| 垂直速率 | bit37 符号，bit38-46 幅值（×64 ft/min） | mode_s.c:941-944 |
| 气压高度 AC12 | ME bit 9-20 | mode_s.c:1041 |
| CPR 偶/奇 F 标志 | ME bit 22 | mode_s.c:1068 |
| CPR 纬度 | ME bit 23-39（17bit） | mode_s.c:1051 |
| CPR 经度 | ME bit 40-56（17bit） | mode_s.c:1052 |

### 呼号字符表
`ais_charset.c:4` 一字不差搬进 Python `CHARSET`：
`@ABC…XYZ[\]^_ !\"#$%&'()*+,-./0123456789:;<=>?`（64 项，6bit 索引）。

### 高度 AC12（Q 分支）
`mode_s.c:156 decodeAC12Field`：Q=1 时
`n = ((ac & 0x0FE0)>>1) | (ac & 0x000F)`，`alt = n*25 - 1000`（ft）。
真实帧 `8D40621D…` 的 AC12=3128 → n=1560 → **38000 ft** ✓。

---

## 4. CPR 全局解码（偶/奇帧对 + NL 表）

来源：`repos/dump1090/cpr.c`。

- **NL 表**（`cpr.c:77 cprNLFunction`）：按纬度绝对值查经度带数。
  赤道 NL=59；50.0°→38；51.5°→37；88°→1。我把 57 个断点逐条搬进
  `_CPR_NL_BREAKS`（cpr.c:79-137）。
- **全局解码**（`cpr.c:162 decodeCPRairborne`）：
  - `j = floor((59*lat0 - 60*lat1)/131072 + 0.5)` 定纬度格号；
  - 两帧纬度必须落在同一 NL 带（否则 `cpr.c:189` 返回 -1，等下一帧对）；
  - 再用偶/奇帧选 `ni = NL(lat)` 或 `NL(lat)-1` 定经度格号 `m`。
- `131072 = 2^17`（CPR lat/lon 都是 17bit）。

Python 里 `ADSBDecoder` 按 ICAO 缓存最近的偶/奇 CPR 帧，配对成功才输出 `(lat, lon)`，
对应 dump1090 track.c 里的配对逻辑。`encode_cpr_airborne` 是逆变换，
用于编码→解码往返（多个纬度误差 < 0.001°）。

---

## 5. 这次实现覆盖了什么

| TC | 含义 | adsb.py / adsb_lite.py |
|---|---|---|
| 1-4 | 航空器识别呼号 | ✓ 6bit 字符解码 |
| 0, 9-18, 20-22 | 空中位置（高度+CPR） | ✓ AC12 高度 + CPR 分量提取 + 全局配对 |
| 5-8 | 地面位置（CPR） | ✓ CPR 分量提取 |
| 19 | 空中速度 | ✓ 子类型1/2 地速航向、3/4 空速、垂直速率 |

CRC / preamble / PPM 位判决全部与 dump1090 对齐。

---

## 6. 验证（真实通过，未造假）

```
python3 -c "from mbdsdr_ai.adsb import ADSBDecoder; print('ok')"   # ok
python3 tests/adsb_real_roundtrip.py
  == 结果: 35 passed, 0 failed ==
```

覆盖：真实帧 CRC、呼号往返×4、速度手算对照、CPR 四纬度往返、有状态配对、
preamble 合成检测、ToolRegistry 注册。

---

## 7. 已知边界

- 高度只实现 Q=1（25ft 间隔）分支；Gillham Q=0 分支返回 None（现代 ES 多为 Q=1）。
- 地面位置（TC5-8）只抽 CPR 分量，全局解码需参考点（`decodeCPRsurface`，cpr.c:216），
  未在本轮展开。
- 真实空口还需重采样、多帧去重叠、长时配对与 NL 带穿越重配；本模块是基带级
  可复现实现，不替代适航认证接收机。
