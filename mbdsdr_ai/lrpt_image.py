# SPDX-License-Identifier: MIT
"""真录 Meteor-M2 LRPT 图像链：VCDU → M_PDU → 空间包 → MCU → RGB 图像。

结构真值（2026-11-03，同一录音的第三方解码器逐行核对）：
TU Berlin lar-tech/satellite-decoder（MIT）``src/extraction.m``（VCDU/M_PDU/
空间包重组）、``src/jpegdecoding.m``（MCU 熵解码/反量化/放置）、
``src/getconfig.m``（Huffman/量化/解扰常量）。此处只学结构语义，代码自写。

链路（extraction.m:30-91 口径）：
  1. VCDU 892B = 8B VCDU 头（byte0>>6=1，24bit 计数器 bytes[2:5]，
     vcduId=bytes[0:2]）+ 2B M_PDU 头（first header pointer=低 11 位，
     0x7FF=本帧无新包头）+ 882B 数据区；
  2. M_PDU 跨帧拼包：计数器连续（mod 2^24 == 1）且同 vcduId 才拼接；
  3. 空间包 6B CCSDS 头（byte0=0x08：version=000/type=0/sec_hdr=1，
     APID=byte1 ∈ {64,65,68}）+ 次头（2B 日 + 4B 毫秒）+ MCU 计数器
     （byte14，0..182 步 14）+ 0xFFF0 标记 + Q 因子（byte19，1..100）
     + MCU 熵数据（byte20:）；
  4. MCU 熵解码（jpegdecoding.m:82-182）：每包 14 个 8×8 块，Annex K.3
     luma Huffman（DC 差分链**每包重置**），尾部全 1 填充为停止哨兵；
  5. 反量化（jpegdecoding.m:32-38）：Q→F（≤20→100；<50→5000/Q；
     else 200-2Q），quant=max(1,floor((K1·F+50)/100))，K1=JPEG 标准
     亮度基表；zigzag→IDCT→+128；
  6. 放置（aligned 路径 jpegdecoding.m:201-215）：行=唯一扫描时刻×8，
     列=MCU计数×8+(块序)×8，宽 1568；RGB=(ch64, ch65, 255−ch68)。

比特约定（第二处分叉，2026-11-03 实证裁决）：真录 VCDU 重组必须
``decode_cadu(..., order="woven")``（CCSDS 往返切分）；concat 序下
M_PDU 解析 0 包、FHP 越界，woven 序下 vcduId 恒 0x4005、FHP 全域、
包结构逐字命中 extraction.m 判据。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .lrpt_dct import idct_8x8, unzigzag_order
from .lrpt_huffman import BitReader, HuffmanTable, receive_extend
from .jpeg_k3_tables import build_k3_tables

__all__ = [
    "VcduHeader",
    "SpacePacket",
    "ImagePacket",
    "ChannelImage",
    "parse_vcdu_header",
    "reassemble_space_packets",
    "parse_image_packet",
    "decode_mcu_packet",
    "render_channel",
    "compose_rgb",
    "lrpt_packets_to_rgb",
    "IMAGE_APIDS",
    "IMAGE_WIDTH",
    "BLOCKS_PER_PACKET",
]

# --------------------------------------------------------------------------- #
# 结构常量（extraction.m / jpegdecoding.m 实证）
# --------------------------------------------------------------------------- #
VCDU_HDR_LEN = 8
MPDU_HDR_LEN = 2
DATA_ZONE_LEN = 882
IMAGE_APIDS: Tuple[int, ...] = (64, 65, 68)
BLOCKS_PER_PACKET = 14          # 每包 14 个 8×8 块（jpegdecoding.m:96）
IMAGE_WIDTH = 1568              # 196 块 × 8（jpegdecoding.m:49）
MCU_STEP = 14                   # MCU 计数步长（0..182）
MAX_MCU_INDEX = 182

# JPEG 标准亮度量化基表（K.1；getconfig.m:128-137 同款）
K1_BASE = np.array([
    [16, 11, 10, 16, 24, 40, 51, 61],
    [12, 12, 14, 19, 26, 58, 60, 55],
    [14, 13, 16, 24, 40, 57, 69, 56],
    [14, 17, 22, 29, 51, 87, 80, 62],
    [18, 22, 37, 56, 68, 109, 103, 77],
    [24, 35, 55, 64, 81, 104, 113, 92],
    [49, 64, 78, 87, 103, 121, 120, 101],
    [72, 92, 95, 98, 112, 100, 103, 99],
], dtype=np.float64)

_K3 = build_k3_tables()
_DC_LUMA: HuffmanTable = _K3[("DC", 0)]
_AC_LUMA: HuffmanTable = _K3[("AC", 0)]


# --------------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------------- #
@dataclass
class VcduHeader:
    version: int
    vcdu_id: int
    counter: int
    fhp: int                    # M_PDU first header pointer（0x7FF=无新包头）
    data: bytes                 # 882B 数据区


@dataclass
class SpacePacket:
    """一个完整空间包（6B CCSDS 头 + 数据，extraction.m tempPP 口径）。"""
    raw: bytes

    @property
    def apid(self) -> int:
        return self.raw[1]

    @property
    def sequence(self) -> int:
        return ((self.raw[2] << 8) | self.raw[3]) & 0x3FFF

    @property
    def data_len(self) -> int:
        return (self.raw[4] << 8) | self.raw[5] + 1


@dataclass
class ImagePacket:
    apid: int
    sequence: int
    day: int
    ms: int                     # 次头：2B 日 + 4B 当日毫秒
    mcu_index: int              # byte14：0..182 步 14
    quality: int                # byte19：1..100
    mcu_bytes: bytes            # byte20:：MCU 熵数据

    @property
    def scan_key(self) -> Tuple[int, int]:
        return (self.day, self.ms)


@dataclass
class ChannelImage:
    apid: int
    image: np.ndarray           # (H, W) uint8，缺块处 128
    valid: np.ndarray           # (H, W) bool
    n_packets: int = 0
    n_blocks: int = 0


# --------------------------------------------------------------------------- #
# 1) VCDU 头 + M_PDU 数据区
# --------------------------------------------------------------------------- #
def parse_vcdu_header(payload: bytes) -> VcduHeader:
    """892B VCDU 载荷 → 头字段 + 数据区（extraction.m:30-38 口径）。"""
    if len(payload) < VCDU_HDR_LEN + MPDU_HDR_LEN:
        raise ValueError(f"VCDU 载荷过短：{len(payload)}")
    return VcduHeader(
        version=payload[0] >> 6,
        vcdu_id=(payload[0] << 8) | payload[1],
        counter=(payload[2] << 16) | (payload[3] << 8) | payload[4],
        fhp=((payload[8] << 8) | payload[9]) & 0x7FF,
        data=payload[VCDU_HDR_LEN + MPDU_HDR_LEN:],
    )


# --------------------------------------------------------------------------- #
# 2) M_PDU → 空间包重组（extraction.m readPackets 等价）
# --------------------------------------------------------------------------- #
def _walk_packets(buf: bytes, multiple: bool
                  ) -> Tuple[List[bytes], bytes, int]:
    """readPackets 语义：返回 (合格包 raw 列表, 未完尾, 被消费的非图像包数)。

    - 头不全 → 尾巴留待下一帧；
    - byte0>>5≠0 → 丢弃并停走（去同步保护）；
    - 包不完整 → 尾巴留待下一帧；
    - 形状不合格（非图像包）→ 按长度消费但不收集；
    - multiple=False 只走一包（续包场景，走完即弃余字节——FHP 语义下
      余字节不可能是新包头）。
    """
    pkts: List[bytes] = []
    skipped = 0
    pos = 0
    while pos < len(buf):
        if len(buf) - pos < 6:
            return pkts, buf[pos:], skipped
        len_dec = (buf[pos + 4] << 8) | buf[pos + 5]
        total = 6 + len_dec + 1
        if (buf[pos] >> 5) != 0:
            return pkts, b"", skipped
        if pos + total > len(buf):
            return pkts, buf[pos:], skipped
        pkt = buf[pos:pos + total]
        if (total >= 21 and pkt[0] == 0x08 and pkt[1] in (64, 65, 68)
                and pkt[17] == 0xFF and pkt[18] == 0xF0
                and pkt[14] <= MAX_MCU_INDEX and pkt[14] % MCU_STEP == 0
                and 1 <= pkt[19] <= 100):
            pkts.append(pkt)
        else:
            skipped += 1
        pos += total
        if not multiple:
            return pkts, b"", skipped
    return pkts, b"", skipped


def reassemble_space_packets(payloads: Sequence[bytes]
                             ) -> Tuple[List[SpacePacket], Dict]:
    """RS 全纠正 VCDU 载荷序列（流序）→ 图像空间包列表 + 统计。

    统计：n_frames/n_packets/n_skipped/n_resets（计数器断链/换 vcduId/坏版本
    触发的拼接重置）、n_fhp_out_of_range。
    """
    stats = {"n_frames": 0, "n_packets": 0, "n_skipped": 0,
             "n_resets": 0, "n_fhp_out_of_range": 0, "n_no_new_header": 0}
    pkts: List[bytes] = []
    partial = b""
    last_counter: Optional[int] = None
    last_id: Optional[int] = None
    for payload in payloads:
        stats["n_frames"] += 1
        hdr = parse_vcdu_header(payload)
        if hdr.version != 1:
            partial = b""
            last_counter = None
            stats["n_resets"] += 1
            continue
        if last_counter is not None:
            if ((hdr.counter - last_counter) % (1 << 24)) != 1 \
                    or hdr.vcdu_id != last_id:
                partial = b""
                stats["n_resets"] += 1
        last_counter, last_id = hdr.counter, hdr.vcdu_id
        zone = hdr.data
        if hdr.fhp == 0x7FF:
            stats["n_no_new_header"] += 1
            got, partial, skip = _walk_packets(partial + zone, False)
        elif hdr.fhp < len(zone):
            got, tail, skip = _walk_packets(partial + zone[:hdr.fhp], False)
            partial = b""
            got2, partial, skip2 = _walk_packets(zone[hdr.fhp:], True)
            got = got + got2
            skip = skip + skip2
        else:
            stats["n_fhp_out_of_range"] += 1
            partial = b""
            continue
        pkts.extend(got)
        stats["n_skipped"] += skip
    stats["n_packets"] = len(pkts)
    return [SpacePacket(raw=p) for p in pkts], stats


# --------------------------------------------------------------------------- #
# 3) 空间包 → 图像包（字段抽出）
# --------------------------------------------------------------------------- #
def parse_image_packet(pkt: SpacePacket) -> Optional[ImagePacket]:
    """按 extraction.m 判据抽图像包字段；非图像包返回 None。"""
    raw = pkt.raw
    if not (len(raw) >= 21 and raw[0] == 0x08 and raw[1] in IMAGE_APIDS
            and raw[17] == 0xFF and raw[18] == 0xF0
            and raw[14] <= MAX_MCU_INDEX and raw[14] % MCU_STEP == 0
            and 1 <= raw[19] <= 100):
        return None
    return ImagePacket(
        apid=raw[1],
        sequence=((raw[2] << 8) | raw[3]) & 0x3FFF,
        day=(raw[6] << 8) | raw[7],
        ms=int.from_bytes(raw[8:12], "big"),
        mcu_index=raw[14],
        quality=raw[19],
        mcu_bytes=raw[20:],
    )


# --------------------------------------------------------------------------- #
# 4) MCU 熵解码（jpegdecoding.m:82-182 口径）
# --------------------------------------------------------------------------- #
def decode_mcu_packet(mcu_bytes: bytes, max_blocks: int = BLOCKS_PER_PACKET
                      ) -> List[Optional[np.ndarray]]:
    """一包 MCU 熵数据 → ≤14 个 8×8 空间块（float64，已 +128 电平偏置）。

    - Annex K.3 luma DC/AC 表（Meteor 不带 DHT，getconfig.m:88-104 实证
      即 K.3 luma）；
    - DC 差分链**每包重置**（j==1 预测归零，jpegdecoding.m:174-178）；
    - 尾部全 1 填充为停止哨兵（jpegdecoding.m:102）；
    - 解不全的块序列在断裂处截断（其后块为 None）。
    """
    reader = BitReader(mcu_bytes, 0)
    blocks: List[Optional[np.ndarray]] = []
    dc_pred = 0

    def bits_all_ones_left() -> bool:
        """剩余位是否全为 1（尾部填充哨兵）。"""
        rest = mcu_bytes[reader.pos:]
        if reader.bit:
            first = rest[0] if rest else 0xFF
            mask = (0xFF >> reader.bit) if reader.bit else 0
            if (first & mask) != mask:
                return False
            rest = rest[1:]
        return all(b == 0xFF for b in rest)

    while len(blocks) < max_blocks:
        if reader.pos >= len(mcu_bytes):
            break
        if bits_all_ones_left():
            break
        try:
            # --- DC ---
            size = _DC_LUMA.decode(reader)
            if size > 15:
                break
            diff = 0 if size == 0 else \
                receive_extend(reader.read_bits(size), size)
            dc_pred += diff
            # --- AC 游程 ---
            coef = np.zeros(64, dtype=np.float64)
            coef[0] = float(dc_pred)
            k = 1
            complete = False
            while k < 64:
                rs = _AC_LUMA.decode(reader)
                r, sz = (rs >> 4) & 0x0F, rs & 0x0F
                if sz == 0:
                    if r == 15:
                        k += 16
                        continue
                    complete = True       # EOB
                    break
                k += r
                if k >= 64:
                    break
                coef[k] = receive_extend(reader.read_bits(sz), sz)
                k += 1
            if not (complete or k == 64):
                break
            blocks.append(coef)
        except Exception:  # noqa: BLE001  坏码/截断：诚实停止本包
            break
    return blocks


# --------------------------------------------------------------------------- #
# 5) 反量化 + IDCT + 放置 + RGB
# --------------------------------------------------------------------------- #
def _quality_scale(q: int) -> float:
    """jpegdecoding.m:32-38 的 Q→F 映射。"""
    if q <= 20:
        return 100.0
    if q < 50:
        return 5000.0 / q
    return 200.0 - 2.0 * q


def _blocks_to_spatial(coef: np.ndarray, quality: int) -> np.ndarray:
    """zigzag 系数 → 8×8 空间块（jpegdecoding.m:184-198 口径）。

    idct_8x8 的契约是「输入中心化 DCT 系数 → 输出已 +128 并 clamp 的
    像素域」，故此处不再二次 +128（历史 bug：双重偏置把整块推向 255
    饱和，由独立 meteor_decode 内容级对拍揪出，队列 #10②）。
    """
    f = _quality_scale(quality)
    quant = np.maximum(1.0, np.floor((K1_BASE * f + 50.0) / 100.0))
    natural = unzigzag_order(coef).reshape(8, 8)
    return idct_8x8(natural * quant)


def render_channel(packets: List[ImagePacket], apid: int,
                   height: Optional[int] = None,
                   scan_rows: Optional[Dict[Tuple[int, int], int]] = None
                   ) -> ChannelImage:
    """单通道图像包 → 通道图（aligned 放置：行=扫描时刻，列=MCU×8+块×8）。

    ``height`` 为像素高度；未给定时按本通道扫描时刻数 ×8 计。
    ``scan_rows`` 给定 (day,ms)→行号 时按其放置（多通道共用行网格）；
    否则按本通道出现过的扫描时刻自建。缺块填 128 并记 valid 掩码。
    """
    pkts = [p for p in packets if p.apid == apid]
    if scan_rows is None:
        keys = sorted({p.scan_key for p in pkts})
        scan_rows = {k: i for i, k in enumerate(keys)}
    n_px = height if height is not None else \
        (max(scan_rows.values(), default=-1) + 1) * 8
    img = np.full((n_px, IMAGE_WIDTH), 128.0)
    valid = np.zeros((n_px, IMAGE_WIDTH), dtype=bool)
    n_blocks = 0
    for p in pkts:
        row = scan_rows.get(p.scan_key)
        if row is None:
            continue
        for j, coef in enumerate(decode_mcu_packet(p.mcu_bytes)):
            if coef is None:
                continue
            spatial = _blocks_to_spatial(coef, p.quality)
            c0 = p.mcu_index * 8 + j * 8
            r0 = row * 8
            img[r0:r0 + 8, c0:c0 + 8] = spatial
            valid[r0:r0 + 8, c0:c0 + 8] = True
            n_blocks += 1
    return ChannelImage(apid=apid, image=img, valid=valid,
                        n_packets=len(pkts), n_blocks=n_blocks)


def compose_rgb(ch64: ChannelImage, ch65: ChannelImage,
                ch68: ChannelImage) -> np.ndarray:
    """RGB = (ch64, ch65, 255−ch68)（jpegdecoding.m:274-275 实证）。"""
    rgb = np.stack([
        ch64.image,
        ch65.image,
        255.0 - ch68.image,
    ], axis=-1)
    return np.clip(rgb, 0.0, 255.0).astype(np.uint8)


def lrpt_packets_to_rgb(image_packets: List[ImagePacket]
                        ) -> Tuple[np.ndarray, Dict[int, ChannelImage], Dict]:
    """图像包列表 → (RGB uint8, 各通道 ChannelImage, 统计)。

    行网格按全部包的扫描时刻并集构建（三通道共用）；统计含每通道
    包数/块数/valid 比例与总扫描行数。
    """
    keys = sorted({p.scan_key for p in image_packets})
    scan_rows = {k: i for i, k in enumerate(keys)}
    height = len(keys) * 8
    channels: Dict[int, ChannelImage] = {}
    for apid in IMAGE_APIDS:
        channels[apid] = render_channel(image_packets, apid,
                                        height=height, scan_rows=scan_rows)
    rgb = compose_rgb(channels[64], channels[65], channels[68])
    stats = {
        "n_packets": len(image_packets),
        "n_scan_rows": len(keys),
        "height": height,
        "width": IMAGE_WIDTH,
        "channels": {
            apid: {"n_packets": c.n_packets, "n_blocks": c.n_blocks,
                   "valid_frac": float(c.valid.mean())}
            for apid, c in channels.items()
        },
    }
    return rgb, channels, stats
