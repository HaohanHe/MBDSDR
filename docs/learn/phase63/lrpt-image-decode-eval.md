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

---

## 6. 落地记录（2026-10-14）— Huffman 压缩域打通 + VCDU 重组

> 对应 SatDump 克隆审计登记的“应补未补第一缺口：LRPT 图像压缩域未打通”。
> 本轮把 `lrpt_dct.py` docstring 里“Huffman 熵编码留待后续轮”的边界推进到落地。

### 6.1 新文件

| 文件 | 说明 |
|------|------|
| `mbdsdr_ai/lrpt_huffman.py` | JPEG 基线压缩域解码：DQT/DHT/DRI/SOF0·1/SOS 解析、位读取器、DC/AC Huffman、反量化+IDCT、MCU 组装、`decode_image_blocks(jpeg_bytes)`、标记游走 `locate_scan_start` |
| `mbdsdr_ai/lrpt_vcdu.py` | VCDU → 图像段重组：按 (VCID, APID, sequence) 有序拼接 header+scan，诚实 gap 报告 |
| `mbdsdr_ai/tests/test_lrpt_huffman.py` | 45 passed / 1 skipped：合成 JPEG 自验证收 + DC/AC 边界 + 坏流异常 + 采样因子 + VCDU 重组四态 |

### 6.2 函数/类清单（`lrpt_huffman.py`）

- 异常族：`LrptJpegError`（基类）/ `LrptJpegMarkerError` / `LrptJpegTruncatedError` / `LrptJpegHuffmanError` / `LrptJpegUnsupportedError`（均带 offset/marker 上下文）。
- `receive_extend(v, size)`：JPEG Annex F“Extend”符号位数值（clean-room，非 SatDump 源码）。
- `BitReader`：字节对齐起点、MSB-first、0xFF00 去填充、marker 边界即停（`read_bit/read_bits/byte_align/hit_marker/peek_restart`）。
- `HuffmanTable` + `parse_dht`：canonical mincode/maxcode/valptr 查表（DC/AC、多表 id 0..3）。
- `parse_dqt` / `_parse_sof` / `_parse_sos` / `parse_jpeg` / `iter_markers`：分段解析与标记流游走。
- `_decode_blocks` → 每分量系数块（逆 zigzag 64 整数，per-component DC 预测）；`_plane_and_image` → 反量化 × DQT + IDCT（接 `lrpt_dct`）+ 按 SOF 采样因子组装。
- `decode_image_blocks(jpeg_bytes)`：入口，返回 `DecodedImage`（width/height/n_components/mcu_rows/mcu_cols/num_blocks/planes/image）。
- `locate_scan_start(data)`：仅游走 header，判完整 + 定位熵起点（供 `lrpt_vcdu` 判断 header_complete）。

### 6.3 VCDU 重组语义（`lrpt_vcdu.py`）

- 输入 `List[VcduPacket](vcid, apid, sequence, payload)`；按 `target_vcid/target_apid` 过滤（记录 `num_filtered`），按 sequence 去重（保留首达、记录 `num_duplicates`）。
- **单调窗口口径**：sequence 视为捕获窗口内单调递增计数，升序排序后拼接；相邻差 >1 记为一个 `GapRange`，累计 `num_gaps` 与逐条 `missing_sequences`。
- `sequence_modulus`（默认 16384）作越界校验：疑似跨取模翻卷的 sequence 直接 `ValueError` 拒绝，**不静默错拼**。
- 产物：`stream`（header+scan 拼接，含洞则不可信）、`header_complete`（能否定位 SOS 熵起点，用 `locate_scan_start`）、`complete`（头完整 + 无洞 + 见到 EOI）、首/末 sequence。
- 丢包/乱序**诚实跳过**：缺块字节不伪造，`stream` 会因缺块而错位，调用方据 gap 信息决定是否送解码器；测试验证“有洞时 `complete=False` 且 `stream!=原始`”。

### 6.4 测试结论（45 passed / 1 skipped）

- **Huffman 跑通数**：SatDump `test_data` 的 840 huffman 向量目录在本 clone **不存在**（仅 `plugins/` 源码，无 `test_data/`），故按预案改用“已知 DQT/DHT/SOI/SOF0/SOS 构造合成 JPEG”自验证收；`TestSatdumpHuffmanVectors` 以 `skipif(not 目录存在)` 诚实跳过（占位）。合成收 round-trip 逐像素精确。
- 覆盖：多长度 canonical 解码（A/B/C/D 教科书树，独立于编码器自校验）、`receive_extend` 全族、DHT/DQT 解析（8/16-bit）、灰度 16×16 round-trip（fixed & huffman 码长）、DC category ≤11 边界、**ZRL（16 零）/ EOB 提前 / DC 差分跨块 / DC-only**、按 SOF 采样因子参数化（Y 2×2 + Cb 1×1 = 2×1 MCU 网格、10 块、分量平面精确）、坏流异常（缺 SOI / 坏 marker length / 坏 Huffman 码 / 截断 scanning / progressive / 非 baseline 扫描 / DRI-restart / 空输入）、`locate_scan_start`、VCDU 重组（正常序 / 有洞 / 乱序 / 非目标 APID 过滤 / 去重 / 多洞 / 取模越界拒绝）。
- 全量回归：`python -m pytest mbdsdr_ai/tests -q` = **615 passed / 11 skipped**（较 AGENTS.md 基线 564/10 净增；本文件贡献 +45 passed +1 skipped，既有测试 0 回退）。

### 6.5 诚实边界（两处口径分歧就地注明）

1. **非 marker 化 JPEG**：真实 Meteor MSUMR LRPT 每包为一段 Segment（14 字节段头 + 14×8×8 块），Huffman 表为 `tables.h` **硬编码**（DC 12 类 + AC 162 类），无 DHT marker。本轮抽象是**标准 JPEG 路径**（SOI/DQT/DHT/SOF0/SOS + 熵扫描），从 DHT 建表。接真实 OTA 需把 tables.h 硬编码表经“等价 DHT 段”注入或另接 hardcoded 表路径——本轮不做，留待后续轮。
2. **QF 缩放**：真实 METEOR 从段头独立字节取 QF 重新缩放标准亮度量化表（`GetQuantizationTable`，huffman.cpp:13-29）；标准 JPEG 的 DQT 已含最终量化表。本解码器直接用 DQT 值，不额外套 QF。
3. **其余参数化**：分量数/采样因子/MCU 网格全部按 SOF0；sequence 计数按 `sequence_modulus`（窗口内翻卷、CadU/VCDU 位级头偏移不做死值，见 `lrpt_vcdu` docstring）。
4. 暂不支持（抛 `LrptJpegUnsupportedError`，绝不静默降级）：progressive (SOF2)、DRI/restart、非 baseline 光谱扫描、单分量非 interleaved 多扫描、>8-bit 精度。

### 6.6 红线执行

- **干净室**：`lrpt_huffman.py`/`lrpt_vcdu.py` 自写 MIT；SatDump 仅读机制（huffman.cpp/segment.cpp/tables.h/lrpt_msumr_reader.cpp 的 file:line 已在 6.2 处引用），未复制任何 GPL 代码；`receive_extend` 用 JPEG Annex F 标准算法。测试自带的最小 baseline JPEG 编码器为 clean-room。
- **未引入上游测试数据**：本 clone 无 `test_data/`，未复制；如后续引入 SatDump 向量，只复制少量并注明出处。
- **不 git 写操作**：仅写文件，未 add/commit/push。
- **隔离文件勿动**：未碰 `cpp/*`、`ui_diag_freeze.cpp`、`cpp/scratch/*`；无 cpp 改动。
- **无 cpp 改动** → 双构型基线 D98/R98 139/139 保持有效。
