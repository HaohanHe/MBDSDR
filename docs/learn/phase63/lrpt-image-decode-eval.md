# LRPT 图像段压缩域解码评估（Huffman + IDCT）

> 日期：2026-10-10  作者：MBDSDR 工程轮（phase63）
> 配套：`lrpt-render.md`（渲染研究）、`weather-sat-digital-study.md`（机制笔记）

---

## 1. 压缩域研究结论（file:line 证据）

### 1.1 段解码流水线（SatDump 机制）

```
bytes → bit array (MSB first)
  → 14 × (DC Huffman + AC Huffman run-length)
  → 64 DCT 系数 (zigzag order)
  → dequantize × quantization table
  → IDCT → 64 pixels
  → +128 offset, clamp 0..255
  → 8×112 段
```

| 步骤 | SatDump 位置 | 说明 |
|------|-------------|------|
| bytes→bits | `huffman.cpp:131-144` | MSB first，convertToArray() |
| DC Huffman 表 | `tables.h:60-72` | 12 类，2..9 bit 码 |
| DC 解码 | `huffman.cpp:50-85` | Huffman 前缀 + 符号位数值 |
| AC Huffman 表 | `tables.h:75-229` | 162 类，2..16 bit 码（zrun + value） |
| AC 解码 | `huffman.cpp:88-129` | run-length: zrun 个零 + 非零值 |
| 符号位数值 | `huffman.cpp:31-46` | getValue()：首符号位决定正负 |
| 量化表 | `tables.h:16-25` | JPEG 标准亮度表 64 项 |
| QF 修正 | `huffman.cpp:13-29` | qf≥50: 200-2qf；qf<50: 5000/qf |
| Zigzag | `tables.h:28-37` | 标准 JPEG 之字形扫描 |
| 反量化 | `segment.cpp:147` | block[Zigzag[x]] * qTable[x] |
| IDCT | `idct.cpp:35-159` | Loeffler 快速算法，定点 8×8 |
| 像素输出 | `segment.cpp:156-165` | +128, clamp 0..255 |

### 1.2 关键数据结构

- **DC 差分编码**：每块 DC = 前一块 DC + Huffman 差值（segment.cpp:106-107）
- **AC 游程编码**：Huffman 码 = (zrun, size)，后跟 size 位符号位数值（huffman.cpp:110-117）
- **EOB（End of Block）**：Huffman 码 = {zrun:0, clen:0}，剩余 AC 系数全零（huffman.cpp:101-107）
- **CFC（填充码）**：无效码流标记，段不完整（huffman.cpp:84,128）

---

## 2. 工作量评估

| 模块 | 规模 | 工作量 | 说明 |
|------|------|--------|------|
| bytes→bits 转换器 | ~10 行 | **S** | 机械 |
| DC Huffman 表 + 解码 | ~30 行（表 12 项） | **S** | 小表，易移植 |
| AC Huffman 表 + 解码 | ~180 行（表 162 项） | **M** | 机械但繁琐，易错 |
| getValue() 符号位解码 | ~10 行 | **S** | 机械 |
| 量化表 + QF 修正 | ~20 行 | **S** | 标准 JPEG |
| Zigzag 正反 | ~10 行 | **S** | 标准 JPEG |
| IDCT（Loeffler 定点） | ~100 行 | **M** | 算术密集，易出错 |
| 段组装（14 块→8×112） | ~40 行 | **S** | 机械 |
| **合计** | **~400 行** | **S+M 混合** | |

### 落地决策

**本轮落地**：DCT/IDCT + 量化 + Zigzag 核心路径（scipy.fftpack 准确实现），**不落地 Huffman 熵编码**（162 项 AC 表转录繁琐且易错，Python 原型阶段价值有限）。

**排期建议**：
- Huffman DC（12 项）：下一轮可快速落地（S）
- Huffman AC（162 项）：建议用 C++ SatDump 既有表做机制参考，Python 侧仅做"系数直存"模式（无熵编码）验证管线；真实 OTA 数据接入时再补全
- IDCT 定点 vs 浮点：Python 侧用 scipy 浮点版已验证；C++ 移植时用 Loeffler 定点（idct.cpp:35-159）

---

## 3. 本轮落地（DCT 核心路径）

### 3.1 文件

| 文件 | 说明 |
|------|------|
| `mbdsdr_ai/lrpt_dct.py` | DCT/IDCT + 量化 + Zigzag |
| `mbdsdr_ai/tests/test_lrpt_dct.py` | 7 个确定性 pytest |

### 3.2 file:line

| 功能 | 位置 |
|------|------|
| 量化表（JPEG 标准） | `lrpt_dct.py:24-33` |
| Zigzag 顺序 | `lrpt_dct.py:36-45` |
| `dct_8x8()` | `lrpt_dct.py:55-64`（scipy DCT-II ortho） |
| `idct_8x8()` | `lrpt_dct.py:67-76`（scipy DCT-III ortho） |
| `quantize()` | `lrpt_dct.py:82-88`（QF 修正） |
| `dequantize()` | `lrpt_dct.py:91-97` |
| `zigzag_order()` / `unzigzag_order()` | `lrpt_dct.py:103-112` |

### 3.3 测试结论（7/7 通过）

```
7 passed, 1 warning in 1.47s
```

| 测试 | 结论 |
|------|------|
| DCT→IDCT 完美恢复 | max err < 1e-6（无量化时可逆） |
| 全平块 → DC-only | AC 全零，恢复精确 |
| 量化→反量化（qf=50） | 有损，max err < 60/像素（符合 JPEG 预期） |
| 高 QF（qf=95） | 近无损，max err < 10/像素 |
| Zigzag 正反 | 可逆，64 索引完整 |
| 量化表 | JPEG 标准表首项=16 |

---

## 4. 诚实边界

1. **Huffman 未落地**：本轮未实现 DC/AC Huffman 熵编解码。真实 LRPT 段是 Huffman 压缩的，本轮直接操作 DCT 系数域验证核心路径。
2. **DCT 用 scipy 而非 Loeffler 定点**：Python 侧用 scipy.fftpack（准确），C++ 移植时需用 Loeffler 定点（idct.cpp:35-159）。
3. **无真实 OTA 数据**：合成 DCT 系数验证管线，非真实 Meteor-M2 接收数据。
4. **段头未解析**：14 字节段头（时间戳/MCUN/QF）未实现解析。
5. **CCSDS 包解包未接**：从 VCDU 到 Segment 的完整链路未打通。

---

## 5. 红线与纪律

- **干净室**：lrpt_dct.py 自写 MIT，未复制 SatDump DCT/Huffman 代码；file:line 仅作机制证据。
- **不预置呼号**：无呼号。
- **不 git add/commit/push**：仅写文件。
- **隔离文件勿动**：未碰 `cpp/*`、`ui_diag_freeze.cpp`、`cpp/scratch/*`。
- **无 cpp 改动** → 双构型基线 D98/R98 139/139 保持有效。
