# P2 SSDV 完整解码器 — 实现笔记（Wave1）

> 基线：python 套件 `mbdsdr_ai/tests/` 83 passed / 7 skipped（含本批新增 5 项全绿）；
> 未触碰 `onboard.py`、`fec.py`、cpp/flutter；仅新增 `mbdsdr_ai/ssdv_decoder.py`、
> `mbdsdr_ai/tests/test_ssdv_decoder.py`、本文档。MIT SPDX，未 commit/push。

## 1. 协议学习结论（仅学公开标准，未抄 GPL 实现）

来源：SSDV API V0 官方文档 <https://ssdv.habhub.org/about.php> + 中文协议解读
（kechuang.org，纯原理叙述）。

### 包结构（FEC 模式 0x66，整包 256 字节）

| 偏移 | 字段 | 长度 | 说明 |
|---|---|---|---|
| 0 | sync | 1 | 0x55（前可叠任意多个 0x55） |
| 1 | type | 1 | 0x66=带FEC / 0x67=无FEC |
| 2..5 | callsign | 4 | Base40 |
| 6 | image_id | 1 | |
| 7..8 | packet_id | 2 | 大端 |
| 9 | width_mcu | 1 | 像素宽/16 |
| 10 | height_mcu | 1 | 像素高/16 |
| 11 | flags | 1 | 00qqqexx |
| 12 | mcu_offset | 1 | 0xFF=无 |
| 13..14 | mcu_index | 2 | 大端，0xFFFF=无 |
| 15..219 | payload | 205 | （无FEC模式为237B，15..251） |
| 220..223 | CRC32 | 4 | 对 bytes[1..219] |
| 224..255 | RS parity | 32 | 对 bytes[1..223] |

RS 码字 = bytes[1..255]（223 数据 + 32 校验）。

## 2. RS 参数核对结论（重要，纠正了先前猜测）

phase14 原 spec 猜测“fec.py 默认 nsym=32/fcr=112 即 SSDV 标准”。**核对后否定**：

| 参数 | CCSDS（fec.py 默认） | **SSDV（实测确认）** |
|---|---|---|
| nsym | 32 | 32 |
| fcr | 112 | **0** |
| 本原多项式 | 0x187 | **0x11d**（x^8+x^4+x^3+x^2+1） |
| 字节反转 | 每字节 ^=0xFF | **不反转** |

验证方法：用独立 MIT 库 `reedsolo` 构造 RS(255,223, fcr=0, prim=0x11d) 码字，
与本模块参数（`ReedSolomon(nsym=32, fcr=0, prim_poly=0x11d, ccsds_invert=False)`）
逐字节比对，并用 fec.py 解码器对注入 1/5/10/16 字节错的码字全部正确还原。

> 附带发现（未改 fec.py，仅记录）：`fec.ReedSolomon.encode` 的合成除法方向
> 仅在 CCSDS fcr=112 时因生成多项式常数项恰好为 1 而正确；对 fcr=0 会得到
> 错误校验字节。**接收机只依赖 decode**（已验证正确），故不影响本模块；
> 若后续需要 encode，建议在独立批次修正该除法方向（对 CCSDS 输出保持逐字节不变）。

## 3. 实现要点（file:line）

- `mbdsdr_ai/ssdv_decoder.py:107` `SsdvDecoder.__init__` — 用 SSDV 参数实例化 RS。
- `ssdv_decoder.py:128` `correct_packet` — 单包纠错：RS.decode → 写回 223B →
  CRC32 把关（CRC 不过即丢包，挡住 >t 时的 RS 静默误纠）。
- `ssdv_decoder.py:182` `feed` — 字节流状态机同步，容忍前导噪声/多余 0x55/错位。
- `ssdv_decoder.py:238` `ImageReassembler.reassemble` — 按 packet_id 升序拼载荷、报 missing。
- `ssdv_decoder.py:249/254` `restuff/wrap_jpeg` — 扫描流 0xFF→0xFF00 回填 + EOI。

## 4. 测试结果（数字）

`mbdsdr_ai/tests/test_ssdv_decoder.py` — **5 passed**：

1. `test_roundtrip_within_error_capacity`（:154）：128×128 图 → 切包 → 偶数包各注入
   10 个错字节 → 全部纠错 → 重建 JPEG 与原始 JPEG **逐字节一致**，Pillow 正常打开。
2. `test_sync_with_preamble_and_noise`（:186）：前导噪声+多余0x55 → 正确切出包。
3. `test_over_capacity_packet_is_dropped_via_crc`（:195）：单包 17 错（>t=16）→
   CRC 把关丢包（诚实失败，不静默接受）。
4. `test_packet_loss_is_honestly_reported`（:207）：丢 1 包 → missing==[1]，不伪造完整图。
5. `test_restuff_roundtrip`（:219）：去填充/再填充互逆。

基线：`mbdsdr_ai/tests/` 83 passed / 7 skipped，无回归。

## 5. 未完成项 / 诚实标注

- **物理层 4FSK/GMSK**：云内无射频，未落地符号解调。本模块完整覆盖“解调字节流 →
  包”的以上各层并可确定性测试。真机串接路径：解调比特/字节流 → `SsdvDecoder.feed()`；
  候选零件 cpp `fsk_demod` / `mbdsdr_ai/demod_nfm`。列为 backlog，待 P4 评估与真机。
- **固定标准 DQT/DHT/SOF 头生成**：`wrap_jpeg` 接受 `prefix_header`；确定性测试中
  直接复用 Pillow 编码出的头段，保证头与扫描流严格匹配。真实 SSDV 接收机应按 flags
  的 quality/subsampling 用固定 Annex K 标准表生成头（未在本批内嵌表）。
- **onboard.py 的 ssdv 模式**：按 spec 由后续批次追加（Wave2），本批未碰。
- CRC32 字节序（大端）与计算范围(bytes[1..219])按公开实现约定采用；如与实际星上
  略有出入，仅影响次级完整性门，不影响 RS 纠错主路径——建议真机联调时再核对。
