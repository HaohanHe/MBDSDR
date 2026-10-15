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


---

## RFC 2435 RTP/JPEG 接入点落地（2026-10-14，联网核实后）

依据公开规范（RFC 2435 §3.1 头布局 / §3.1.6 Q 字段公式 / ITU T.81 Annex
K.1-K.2 量化表），非任何 GPL 源码：

- ijg_quant_table(q, chroma)：IJG 公式缩放表（Q=50 为基准表本身、
  Q=99 全 1、Q=1 全 255，均 clamp 1..255；Q 越大量化越粗），边界断言
  钉死。
- parse_rtp_jpeg_header(seg)：解析 Meteor LRPT 图像 Segment 的 4 字节
  JPEG 头（MBZ|TypeSpecific|FragmentOffset 24bit）+ fragment_offset==0 段
  的 8 字节尺寸头（宽/高大端 + Type/Q/色深/通道）；短输入与 Q≥128 带内
  量化表路径诚实抛错（LrptJpegTruncated/Unsupported）。
- 测试 11 例（test_lrpt_rtp_jpeg.py）。

仍未做（登记，接真实 OTA 的前提项）：
1. Segment 重组为完整扫描（fragment_offset 升序拼接 + Meteor 硬编码
   Huffman 表（T.81 Annex K.3）的 DHT 化导出）；
2. Q≥128 带内量化表头解析；
3. 上述两件完成后，把 decode_image_blocks 从“整 JPEG”入口切到
   “RTP 头 + 硬编码表 + 重组流”入口，即真实 Meteor LRPT 解码路径。


---

## Annex K.3 硬编码 Huffman 表落地（2026-10-14，真实路径第二块）

- mbdsdr_ai/jpeg_k3_tables.py（新）：T.81 Annex K.3.1/K.3.2（DC 12 值）、
  K.3.3/K.3.4（AC 162 值）四张规范表以纯数据声明，经 lrpt_huffman.HuffmanTable
  构造为可解码表；k3_tables_for_component(分量序号) 按 JPEG 口径选择
  luma/chroma 表（分量 0 -> DC0/AC0，其余 -> DC1/AC1）。
- 表值来自 ITU T.81 公开规范文本，非任何 GPL 源码转录。
- 测试 7 例（test_jpeg_k3_tables.py）：表长/counts 和钉死、构造、真实码流
  首符号解码（DC luma '00' -> 0x00、DC chroma '10' -> 0x02）、分量选择、
  AC luma/chroma 差异、构造期校验同 DHT 口径。

进度：RFC 2435 头 + IJG 量化表 + K.3 硬编码表三块已就位；剩余
Segment 重组入口与 Q≥128 解析（未做项见上节）。

---

## Segment 级组入口落地（2026-10-14，真实路径第三块）

- lrpt_vcdu.py 新增 reassemble_meteor_scan(segments)：按 RFC 2435
  fragment_offset（原子化扫描流内位置）升序拼接，剥掉每段 4B/12B RTP
  头；由首段 Q 字段经 IJG 公式产出 luma(表 0)/chroma(表 1)
  量化表；缺首段拒绝解码（尺寸/Type/Q 未知）；Q>=128 带内表路径诚实抛 Unsupported；
  断点 offset 计入 gap_offsets 且 complete=False（绝不静默拼错图）。
- 测试 7 例（test_meteor_reassembly.py）：有序拼接、空洞检测、缺首段拒绝、
  Q 域外/越界、空输入、量化表与 IJG 一致。

至此真实 Meteor LRPT 路径的头/表/重组三块齐备；剩余收口项：把
decode_image_blocks 的入口从“整 JPEG”改为“MeteorScan + K.3 表 + 量化表”
驱动（单块入口适配），以及 fill_missing 黑行回填（SatDump 同款参数）。


---

## decode_meteor_scan 入口落地（2026-10-15，真实路径收口）

- lrpt_huffman.py 新增 decode_meteor_scan(scan, quant_tables=None)：上一节
  MeteorScan 产物的 JPEG 侧入口。与 decode_image_blocks 的区别：
  Segment 不带 DHT -> DC/AC 表用 Annex K.3 硬编码表；不带 DQT ->
  量化表由首段 Q 字段经 IJG 公式生成或调用方显传入；扫描无
  SOI/SOS 头 -> SOF 字段由参数化提供，缺省 Meteor 4:2:0
  （Y 2x2，Cb/Cr 1x1）。
- 三条守卫：空扫描/缺尺寸/缺量化表各自诚实抛错，不补零、不给
  半截图。
- 测试 4 例（test_decode_meteor_scan.py）：守卫三拦、全零块 8x8
  解出三平面常量 128（JPEG 电平偏置，手工按 K.3 码表编码的
  32 bit 比特流）、短空扫描拒绝、Q=20/Q=80 量化差分
  （DC +1 重建 133 vs 128）、缺省 4:2:0 分量口径。

真实 Meteor LRPT 解码路径（断言层面）今后为：
reassemble_meteor_scan -> decode_meteor_scan，无需 DHT/DQT/SOI。
剩余：真实 OTA 采样验证（Viterbi 口径 + 整链）。


---

## fill_missing_lines 落地（2026-10-15，真实路径收口项／）

- lrpt_vcdu.py 新增 fill_missing_lines(image, line_status, max_fill_lines=50)：
  对齐 SatDump meteor_m2_lrpt 的 fill_missing / max_fill_lines 语义（默认 50
  连续行）。长度超限的缺口不填（诚实返回填充行数，不拿假图
  境填入）；缺口顶到图像开头时同样不填。
- 测试 8 例（test_fill_missing_lines.py）：短缺口填充、超限不填、边界含等价、
  无缺口返回副本、开头缺口跳过、多通道、均形律拒绝、空状态。

真实 Meteor LRPT 路径的断言层面现在为：
VCDU 图像包重组 -> reassemble_meteor_scan -> decode_meteor_scan
-> 行状态维护 + fill_missing_lines 黑行回填；
行状态本身产生于真实 VCDU 采样（APID 序列完整度），
需真实 OTA 样本方能验证。
