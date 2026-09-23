# ADS-B / Mode-S (1090 ES) 深度代码审查 — 第二轮

**审查范围**: `mbdsdr_ai/adsb.py` (239行), `mbdsdr_ai/adsb_lite.py` (368行)
**参考基准**: `repos/dump1090/` (demod_2400.c, crc.c, ais_charset.c, cpr.c, mode_s.c)
**审查日期**: 2026-09-24

---

## 一、审查结论总览

| 类别 | 数量 | 说明 |
|------|------|------|
| [真bug] | 5 | 导致真机信号无法正确解码，合成信号可通过 |
| [空壳] | 0 | — |
| [占位] | 4 | 明确标注未实现，功能缺失但不伪造 |
| [建议] | 5 | 与 dump1090 算法不一致，影响真实场景鲁棒性 |

**核心判断**: 两个模块都是"合成信号自洽、真机信号失败"的典型案例。CRC 多项式和位序正确，但字符表、呼号位打包、前导码检测相位处理存在系统性偏差。**CPR 位置解码完全未实现**（已明确标注为后续工作）。

---

## 二、CRC 校验审查

### 2.1 CRC 多项式 — 正确 ✅

- `adsb.py:37` — `CRC_POLY = 0xFFF409`
- `adsb_lite.py:27` — `MODES_CRC_GENERATOR = 0xFFF409`
- dump1090 `crc.c:28` — `#define MODES_GENERATOR_POLY 0xfff409U`

**三者一致。** 审查提示中提到的 `0xFFFA0480` 不是 Mode-S CRC-24 多项式（那是 25 位数，不适用于 24 位 CRC），代码使用的 `0xFFF409` 是 ICAO Annex 10 标准多项式，正确。

### 2.2 CRC 算法 — 正确 ✅

两个模块均采用 MSB-first 逐位长除法：

```python
# adsb.py:63-77 / adsb_lite.py:43-55
reg = 0
for bit in bits:
    top = (reg >> 23) & 1
    reg = (reg << 1) & 0xFFFFFF
    if top ^ (bit & 1):
        reg ^= POLY
```

**运行时验证**: 与 dump1090 表驱动法 (`crc.c:65-81`) 在自建帧上结果一致（全帧 112 bit 余数 = 0）。

### 2.3 CRC 校验方向 — 正确 ✅

- **发送**: 对前 88 bit（长帧）或 32 bit（短帧）计算 FCS，附加到帧尾
- **接收**: 对全部 112/56 bit 计算余数，== 0 即通过

这与 dump1090 `modesChecksum` 行为一致（处理 n-3 字节后异或末尾 3 字节）。

### 2.4 [真bug] DF20/DF21 被 CRC 错误拒绝

**文件**: `adsb_lite.py:271`

```python
if crc24(bits, nbits) != 0:
    return None
```

`parse_frame` 对所有长帧统一执行 CRC==0 校验，但 DF20/DF21（Comm-B 高度/识别应答）使用 **Address/Parity** 而非 Data Parity——末尾 3 字节是 ICAO 地址异或值，不是 CRC 校验和。

dump1090 `mode_s.c:587-597` 对 DF20/DF21 明确处理：`mm->addr = mm->crc`，不要求 CRC==0。

**影响**: 城市环境中真实收到的 DF20/DF21 应答会被全部丢弃。DF16（长空对空）同理。

### 2.5 [真bug] DF11 短帧被 CRC 错误拒绝

**文件**: `adsb_lite.py:271`, `adsb.py:208`

DF11（All-call Reply）使用 Parity/Interrogator，末尾 3 字节低 7 bit 是上行链路 II/CL，不是纯 CRC。dump1090 `mode_s.c:568-577` 对此特殊处理：仅检查低 7 bit == 0。

- `adsb.py:208`: 短帧直接 `crc_ok = False`（保守，但放弃了 DF11 的 ICAO 提取）
- `adsb_lite.py:271`: 短帧也走 `crc24 != 0` 拒绝，DF11 全部丢弃

**影响**: 无法通过 All-call Reply 发现未发出 DF17 的飞机。

---

## 三、前导码检测与位判决

### 3.1 前导码脉冲位置 — 正确 ✅

- `adsb.py:33` — `PREAMBLE_US = (0.0, 1.0, 3.5, 4.5)`
- `adsb_lite.py:30` — `PREAMBLE_PULSES_US = (0.0, 1.0, 3.5, 4.5)`
- dump1090 `demod_2400.c:147-151` — 四个脉冲在 0/1/3.5/4.5 µs，各 0.5 µs 宽

三者一致。

### 3.2 [建议] 前导码检测算法与 dump1090 差异显著

**dump1090** (`demod_2400.c:134-218`):
1. 在每个样本位置检测上升沿（sample 0→1）和下降沿（sample 12→13）
2. 匹配 5 种相位模板（phase 3-7），通过峰值位置识别脉冲群
3. 检查静区（samples 5,6,7,8,14,15,16,17,18）电平低于峰值
4. 要求 `base_signal * 2 >= 3 * base_noise`（约 3.5 dB SNR）
5. 对每个候选尝试 5 种相位解码位，取评分最高者

**adsb.py** (`_find_preamble`, lines 149-182):
1. 对整段信号做归一化互相关（余弦相似度），取 argmax
2. 要求相关度 > 0.60，脉冲最小值 > 1.8 × 保护区间最大值
3. **只尝试一个固定相位**，不做相位搜索

**adsb_lite.py** (`find_preambles`, lines 184-215):
1. 找超门限样本，聚类为候选区
2. 在 ±phase_search 样本内做相位搜索（±2 样本）
3. 要求四脉冲全超门限，且保护区间 < 脉冲电平 × 0.6

**差异影响**:
- adsb.py 的全局归一化互相关计算量大（O(n·m)），且 `env/env.max()` 全局归一化意味着一个强信号会压低其他弱信号的检测门限——真实多飞机环境下漏检率高
- 两个模块都没有 dump1090 的 **5 相位加权相关切片**（`slice_phase0-4`），位判决仅用前后半窗能量比较，在采样率不匹配或相位偏移时误码率高
- dump1090 每帧尝试 5 种相位取最优评分，两个模块都只解一次

### 3.3 [真bug] adsb.py 全局归一化不适合真实 SDR 输入

**文件**: `adsb.py:194-195`

```python
env = np.abs(iq) ** 2
env = env / max(float(env.max()), 1e-12)  # 归一化便于门限
```

将整个时窗的能量归一化到 max=1。在真实 RTL-SDR 输入中：
- AGC 自动增益控制会导致信号强度随时间变化
- 多飞机信号强弱差异可达 20+ dB
- 一个近距离强信号会使归一化后远处弱信号的前导码相关度远低于 0.60 门限

dump1090 使用自适应门限（`adaptive.c`）和逐脉冲局部对比度，不做全局归一化。

### 3.4 位判决（0.5 µs 半位比较）— 基本正确

- `adsb.py:202-206`: 前半窗能量 > 后半窗 = 1，否则 = 0
- `adsb_lite.py:222-228`: 同上，另计算逐位置信度 `|e1-e0|/(e1+e0)`

PPM 调制：bit=1 时前 0.5 µs 高、后 0.5 µs 低；bit=0 时反之。两个模块的判决逻辑与物理一致。

**但** dump1090 使用加权相关（`5*m[0]-3*m[1]-2*m[2]` 等）而非简单能量比较，对采样相位偏移更鲁棒。

---

## 四、消息解码（DF / ME 字段）

### 4.1 DF/CA 提取 — 正确 ✅

- `adsb_lite.py:273-275`: bits[0:5]=DF, bits[5:8]=CA, bits[8:32]=ICAO
- dump1090 `mode_s.c:540,624-625`: `getbits(msg,1,5)`=DF, `getbits(msg,9,32)`=AA(ICAO)

位偏移一致。

### 4.2 ME 字段提取 — 正确 ✅

- `adsb_lite.py:282`: bits[32:37] = TC（ME 字节 0 的高 5 bit）
- dump1090 `mode_s.c:742-743`: `memcpy(mm->ME, &msg[4], 7)`，ME 从第 5 字节开始

ME 起始位置（帧 bit 32 = 字节 4）一致。

### 4.3 [占位] DF19 军用扩展 squitter 未处理

**文件**: `adsb_lite.py:294-297`

```python
def expected_len_for_df(df: int) -> int:
    if df in (0, 4, 5, 11):
        return 56
    return 112
```

DF19 走长帧路径，被 CRC 校验拒绝（军用帧奇偶性不同）。dump1090 的 `valid_df_long_bitset` 也不包含 DF19（默认），所以行为与 dump1090 一致——但代码未明确标注 DF19 不支持。

### 4.4 [建议] TC=29 错误标记为 needs_cpr

**文件**: `adsb_lite.py:259-260`

```python
if tc == 29:
    return "target state & status", True
```

TC=29（Target State and Status）**不包含 CPR 经纬度**，只包含高度、滚转角等。`needs_cpr` 应为 `False`。

dump1090 `mode_s.c` 中 TC=29 走 `decodeTargetStateAndStatus`，不解 CPR。

---

## 五、航空器呼号解码

### 5.1 [真bug] adsb.py 字符表完全错误

**文件**: `adsb.py:41-42`

```python
CHARSET = (" " + "ABCDEFGHIJKLMNOPQRSTUVWXYZ" + " " * 5 +
           "0123456789" + "..=+:?" + " " * 16)
```

**运行时验证结果**（对真实 6-bit 值 `[21,1,12,49,50,51,52,0]` 即 "UAL1234@"）:

| 索引 | 正确值 (dump1090) | adsb.py 值 | 解码结果 |
|------|-------------------|------------|----------|
| 0 | `@` | ` ` | 填充字符变空格 |
| 27-31 | `[\]^_` | `     ` (5空格) | 特殊字符全丢 |
| 32 | ` ` | `0` | **空格变数字0** |
| 33-47 | `!"#$%&'()*+,-./` | `123456789..=+:` | **标点全变数字/符号** |
| 48-57 | `0123456789` | `        ` (8空格) | **数字全变空格** |
| 58-63 | `:;<=>?` | `        ` (6空格) | 尾部符号全丢 |

**实际解码**: 真实飞机呼号 "UAL1234" 被 adsb.py 解码为 `"UAL"`（数字部分全变空格后被 strip 掉）。

**为什么合成信号能通过**: `encode_callsign` 和 `decode_callsign` 使用同一个错误 CHARSET，自洽往返。但真实空口使用 ICAO 标准表，数字 0-9 的 6-bit 值是 48-57，在 adsb.py 中对应 idx 48-57 = 空格。

### 5.2 [真bug] adsb.py 呼号位打包方式错误（8-bit/字符 vs 6-bit/字符）

**文件**: `adsb.py:80-88, 91-96, 112`

```python
def encode_callsign(callsign: str) -> bytes:
    cs = callsign.upper().ljust(6)[:6]  # 只取6字符
    out = bytearray()
    for ch in cs:
        out.append(CHARSET.index(ch))   # 每字符占1字节(8bit)
    return bytes(out)
```

```python
def decode_callsign(six_bytes: bytes) -> str:
    for byte in six_bytes:
        idx = byte & 0x3F               # 每字节取低6bit
```

**ICAO 标准**: 呼号占 ME 字节 1-6（6 字节 = 48 bit），每字符 **6 bit**，共 **8 个字符**。

```
ME byte 1-6 (48 bit) = [char0(6b)][char1(6b)]...[char7(6b)]
```

**adsb.py 实际做法**: 每字符占 8 bit（1 字节），只塞 6 个字符到 6 字节中。

**运行时验证**:
- 真实 ADS-B 呼号 "UAL1234" 的 6-bit 打包字节为 `541331cb3d00`
- adsb.py 解码结果: `"TS K"`（完全错误）
- 正确解码: `"UAL1234@"`

**影响**: 即使修复字符表，adsb.py 也无法解码真实呼号，因为位打包方式根本不对。

### 5.3 [真bug] adsb.py 呼号长度截断为 6 字符

**文件**: `adsb.py:82`

```python
cs = callsign.upper().ljust(6)[:6]
```

ICAO 标准呼号为 **8 字符**（如 "DLH1234"、"UAL1234"）。截断为 6 字符会丢失尾部。

dump1090 `mode_s.c:805-812` 解码 8 个字符（ME bits 9-14 到 51-56）。

### 5.4 adsb_lite.py 字符表 — 基本正确，有小瑕疵

**文件**: `adsb_lite.py:37`

```python
_ADSB_CHAR = "?ABCDEFGHIJKLMNOPQRSTUVWXYZ##### ###############0123456789######"
```

**运行时验证**:

| 索引段 | 正确值 (dump1090) | lite 值 | 状态 |
|--------|-------------------|---------|------|
| 0 | `@` | `?` | ⚠️ 填充字符解码为 `?` 而非 `@`/空格 |
| 1-26 | `A-Z` | `A-Z` | ✅ |
| 27-31 | `[\]^_` | `#####` | ⚠️ 罕见特殊字符（呼号中极少出现） |
| 32 | ` ` | ` ` | ✅ |
| 33-47 | `!"#$%&'()*+,-./` | `###############` | ⚠️ 标点（呼号中极少出现） |
| 48-57 | `0123456789` | `0123456789` | ✅ **数字位置正确** |
| 58-63 | `:;<=>?` | `######` | ⚠️ 罕见尾部符号 |

**结论**: lite 的字母和数字位置正确（与 dump1090 一致），呼号主体（A-Z, 0-9）可正确解码。唯一实际问题是 idx 0 填充字符解码为 `?` 而非被 `.strip()` 去除——真实飞机用 `@`(0) 填充尾部，会显示为尾部 `?`。

### 5.5 adsb_lite.py 呼号位提取 — 正确 ✅

**文件**: `adsb_lite.py:232-243`

```python
def _decode_callsign(bits: Sequence[int]) -> Optional[str]:
    for i in range(40, 88, 6):  # 8个字符 × 6 bit = 48 bit
        v = 0
        for b in bits[i:i+6]:
            v = (v << 1) | b
```

- 从 bit 40 开始（DF 5b + CA 3b + ICAO 24b + TC 5b + 类别 3b = 40 bit）
- 每 6 bit 提取一个字符，共 8 个字符
- 与 dump1090 `mode_s.c:805-812` 的 ME bits 9-14...51-56 对应关系一致

**正确。**

---

## 六、CPR 位置解码

### 6.1 [占位] CPR 完全未实现

两个模块均**不包含任何 CPR 位置解码代码**。

- `adsb.py:14` 文档字符串明确承认: "真实空口还需...CPR 位置解码等"
- `adsb_lite.py:13` 文档字符串: "CPR 经纬度具体位置标注 needs_cpr（后续）"
- `adsb_lite.py:11` 注释: "CPR 奇偶位置解算...留给完整 dump1090 后端"

dump1090 `cpr.c` 实现了完整的:
- `cprNLFunction` — 59 段纬度分区表（lat 0°-87°）
- `cprNFunction` — 偶数/偶数帧 N 值
- `decodeCPRairborne` — 全局位置解码（奇偶帧配对）
- `decodeCPRsurface` — 地面位置解码（参考点近邻）
- `decodeCPRrelative` — 本地位置解码

`adsb_lite.py:246-264` 的 `message_type_for_tc` 正确标记了哪些 TC 需要 CPR（TC 5-18, 20-22），但仅设置 `needs_cpr=True` 标志，不做实际解算。

**判断**: 这是明确标注的功能缺失，不是伪造实现。属于 [占位]。

---

## 七、"实验室绿、真机红" 根因分析

### 7.1 问题复现路径

```
合成帧生成 (build_identification_frame)
    → 错误字符表自洽编码
    → 错误位打包 (8-bit/char)
    → PPM 调制
    → 加噪
    → 前导码检测（已知相位）
    → 位判决
    → CRC 校验 ✅ 通过（因为是自己编的）
    → 呼号解码 ✅ 往返正确（因为编码/解码用同一个错误表）
```

```
真实 RTL-SDR 信号
    → 标准 ICAO 字符表（数字在 idx 48-57）
    → 标准 6-bit/char 打包
    → 任意相位偏移
    → adsb.py 全局归一化压低弱信号
    → 单相位解码误码
    → CRC 校验失败 ❌（位判决错误）
    → 或 CRC 通过但呼号解码错误 ❌（字符表/打包错误）
```

### 7.2 具体失败点

| 失败点 | adsb.py | adsb_lite.py | dump1090 行为 |
|--------|---------|--------------|---------------|
| 真实呼号含数字 | ❌ 解码为空格 | ✅ 数字位置正确 | ✅ |
| 真实呼号 7-8 字符 | ❌ 截断为 6 | ✅ 解 8 字符 | ✅ |
| 真实呼号位打包 | ❌ 8-bit/char | ✅ 6-bit/char | ✅ |
| DF20/DF21 应答 | 未处理 | ❌ CRC 拒绝 | ✅ 提取 ICAO |
| DF11 All-call | ❌ 直接 False | ❌ CRC 拒绝 | ✅ 特殊处理 |
| 多飞机信号 | ❌ 全局归一化 | ⚠️ 自适应门限但无评分 | ✅ 自适应+评分 |
| 相位偏移 | ❌ 单相位 | ⚠️ ±2 样本搜索 | ✅ 5 相位加权 |
| CPR 位置 | ❌ 未实现 | ❌ 未实现 | ✅ 完整实现 |

---

## 八、其他发现

### 8.1 [建议] adsb.py 前导码相关度计算效率

**文件**: `adsb.py:165-168`

```python
corr = np.convolve(envf, template[::-1], mode="valid")
local_energy = np.convolve(envf * envf, np.ones(plen), mode="valid")
```

对整段信号做两次卷积，O(n·m) 复杂度。在 2 MHz 采样率、1 秒数据 = 200 万样本时，计算量巨大。dump1090 逐样本边缘检测，O(n)。

### 8.2 [建议] adsb_lite.py 聚类窗口可能过宽

**文件**: `adsb_lite.py:198`

```python
while j < len(hot) and hot[j] - hot[j - 1] < min_sep // 4 + 1:
```

`min_sep = 8 µs × sr`，在 2 MHz = 16 样本。聚类容差 = 16//4+1 = 5 样本。两个相距 5 样本的超门限尖峰会被聚类为同一候选——在密集信号环境中可能合并两个相邻前导码。后续的 `k - starts[-1] >= min_sep` 去重只保证已确认候选间的间隔，不阻止聚类内部合并。

### 8.3 [建议] adsb.py `_bits_to_bytes` 尾部处理

**文件**: `adsb.py:55`

```python
for i in range(0, len(bits) - 7, 8):
```

若 bits 长度不是 8 的整数倍，尾部位被静默丢弃。112 bit 和 56 bit 都是 8 的倍数，当前无问题，但函数通用性差。

### 8.4 [占位] 无纠错能力

dump1090 `crc.c:130-352` 实现了 1-2 bit 纠错表（`prepareErrorTable`），可纠正偶发 1-2 bit 误码。两个模块均只做 CRC 校验，不纠错。城市多径环境中偶发单 bit 错误会导致整帧丢弃。

### 8.5 [建议] adsb_lite.py `synthesize_modes_iq` 的 `start_us_ref` hack

**文件**: `adsb_lite.py:119-126`

```python
def put(t0_us: float, dur_us: float):
    a = int(round((start_us_ref[0] + t0_us) * sps))
    ...
for start_us, bits, nbits in layout:
    start_us_ref = [start_us]
```

用可变列表 `start_us_ref` 做闭包变量，虽然能工作但不优雅。建议直接传参。

---

## 九、逐文件问题清单

### adsb.py

| # | 级别 | 行号 | 问题 |
|---|------|------|------|
| 1 | [真bug] | 41-42 | CHARSET 完全错误：数字在 idx 32-41 而非 48-57，标点位置全错 |
| 2 | [真bug] | 80-88 | 呼号按 8-bit/字符打包，标准应为 6-bit/字符；只支持 6 字符 |
| 3 | [真bug] | 194-195 | 全局归一化 `env/env.max()` 不适合多信号真实场景 |
| 4 | [真bug] | 208 | 短帧 CRC 直接置 False，不解析 DF11 ICAO |
| 5 | [建议] | 149-182 | 前导码只做单相位解码，无 5 相位加权 |
| 6 | [建议] | 165-168 | 整段卷积计算量大，实时性差 |
| 7 | [占位] | — | 无 CPR 位置解码（文档已承认） |

### adsb_lite.py

| # | 级别 | 行号 | 问题 |
|---|------|------|------|
| 1 | [真bug] | 271 | DF20/DF21/DF16 被 CRC==0 错误拒绝（应 Address/Parity） |
| 2 | [真bug] | 271 | DF11 短帧被 CRC==0 错误拒绝（应检查低 7 bit IID） |
| 3 | [真bug] | 37 | idx 0 填充字符为 `?` 而非 `@`，尾部呼号显示多余 `?` |
| 4 | [建议] | 259-260 | TC=29 错误标记 needs_cpr=True（实际无 CPR） |
| 5 | [建议] | 198 | 聚类窗口可能合并相邻前导码 |
| 6 | [占位] | — | 无 CPR 位置解码（文档已承认） |
| 7 | [占位] | — | 无 bit 纠错能力（dump1090 可纠 1-2 bit） |

---

## 十、与 dump1090 关键算法对比总结

| 算法 | dump1090 | adsb.py | adsb_lite.py | 一致性 |
|------|----------|---------|--------------|--------|
| CRC 多项式 | 0xFFF409 | 0xFFF409 | 0xFFF409 | ✅ |
| CRC 位序 | MSB-first | MSB-first | MSB-first | ✅ |
| 前导码位置 | 0/1/3.5/4.5 µs | 同左 | 同左 | ✅ |
| 位判决方式 | 5 相位加权相关 | 单相位能量比较 | ±2 样本搜索+能量比较 | ⚠️ |
| 前导码检测 | 边缘检测+静区检查 | 全局互相关 | 门限聚类+局部对比度 | ⚠️ |
| 自适应门限 | 自适应增益 | 无（全局归一化） | 中位数+MAD | ⚠️ |
| 帧评分 | scoreModesMessage | 无 | 逐位置信度均值 | ⚠️ |
| 字符表 | 64 字符标准表 | 错误表 | 基本正确（idx0 小瑕疵） | ❌/⚠️ |
| 呼号打包 | 8 字符×6 bit | 6 字符×8 bit | 8 字符×6 bit | ❌/✅ |
| DF20/21 处理 | Address/Parity | 未处理 | CRC 拒绝 | ❌ |
| DF11 处理 | IID 低 7 bit | 拒绝 | CRC 拒绝 | ❌ |
| CPR 全局解码 | 完整实现 | 无 | 无 | ❌ |
| CPR 本地解码 | 完整实现 | 无 | 无 | ❌ |
| 纠错能力 | 1-2 bit | 无 | 无 | ❌ |
