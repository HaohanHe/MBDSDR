# SPDX-License-Identifier: MIT
"""DVB-S2 DATAFIELD → CCSDS VCDU 流式重组（解码侧，P5.1）。

事实层（标准原文亲验，2026-11-04 核对本机留存 PDF，出处随函数标注）：

  * ETSI EN 302 307-1 V1.4.1 §5.1.6（`standards/etsi-en-302-307-1/
    en_30230701v010401p.pdf` 第 19-20 页）：BBHEADER 固定 10 字节（80 bit），
    字段顺序 MATYPE(2B) + UPL(2B) + DFL(2B) + SYNC(1B) + SYNCD(2B) + CRC-8(1B)；
    SYNCD「distance in bits from the beginning of the DATA FIELD and the first
    UP from this frame (first bit of the CRC-8)」，且「SYNCD = 0D means that the
    first USER PACKET is aligned to the DATA FIELD」，SYNCD = 65535D 表示本
    DATA FIELD 内没有 UP 起点。
  * 同标准 §5.1.4：BBHEADER 的 CRC-8 覆盖前 9 字节，多项式
    g(X) = X^8+X^7+X^6+X^4+X^2+1（0xD5）、MSB-first、寄存器初值全零。
    该实现只在 `fengyun_sat.dvbs2_bb_crc8` 一处（单一来源，本模块不复制）。
  * CCSDS TM Space Data Link Protocol（132.0-B）：VCDU 主头 6B =
    version(2) + spacecraft id(8) + vcid(6) + frame counter(24) +
    replay flag(1) + reserved(7)；version 合法值 0b01；VCID=63 (0x3F) 是填充
    虚拟信道（填充帧丢弃）；24 bit 帧计数器逐帧 +1，跳变即链路丢帧证据——
    如实计数上报，绝不脑补丢失内容。
  * 帧定界：CADU 前缀 ASM = 0x1ACFFC1D（CCSDS 同步字，与 `lrpt_fec.ASM` 同值）。

建模假设（不冒充标准结论）：
  标准里的「UP」是 DVB 用户包；本模块要把 DATA FIELD 拼成「以 VCDU 帧为
  传输单元」的字节流，因此把 **VCDU 帧当作 UP 的等价物**——SYNCD 被当作
  「DATA FIELD 起点到首个整帧起点的比特距离」。GCS 直灌（SYNCD=0）场景成立；
  若上游是 188B TS 包流，SYNCD 只保证 TS 包对齐，调用方须自行预对齐后
  再以 `syncd_bits=0` 喂入。

对齐证据强度（`flush()` 报告里如实区分，不混为一谈）：
  * `syncd`   —— BBHEADER 明文声明（标准字段）；
  * `asm`     —— 流内找到 CCSDS 同步字，硬锁；
  * `assumed` —— 无声明、无 ASM，按「起点即帧边界」假设，需连续两帧主头
                 同时合法才认，标记 `assumed_aligned=True`（证据最弱）。

职责边界：本层只产出「帧边界正确的 VCDU + 主头解析 + 完整性统计」；
M_PDU/TP_PDU/文件重组是上层（`fengyun_sat.LRITParser` / `ccsds_tools`）的事。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

# CADU 同步字（CCSDS；与 mbdsdr_ai.lrpt_fec.ASM 同一真值）
ASM = bytes([0x1A, 0xCF, 0xFC, 0x1D])

VCDU_HEADER_BYTES = 6
VCDU_FILL_VCID = 0x3F             # CCSDS 填充虚拟信道
VCDU_VALID_VERSION = 1            # TM SDLP version=0b01
SYNCD_NO_UP = 65535               # ETSI §5.1.6：本 DATA FIELD 内无 UP 起点


@dataclass
class VcduFrame:
    """一条边界正确、主头合法的 VCDU 帧。"""
    raw: bytes                    # 完整帧（vcdu_len 字节，含 6B 主头）
    version: int
    spacecraft_id: int
    vcid: int
    replay: bool
    counter: int
    crc_ok: Optional[bool]        # 帧尾 CRC 校验结果；未启用校验为 None


@dataclass
class ReassemblyStats:
    """重组完整性统计（全部如实计数，不粉饰）。"""
    asm_locks: int = 0            # ASM 硬对齐成功次数
    assumed_aligned: bool = False # 无声明无 ASM 的假设对齐（证据最弱）
    frames: int = 0               # 产出的合法 VCDU 数
    fill_dropped: int = 0         # 填充帧（VCID=63）丢弃数
    version_bad: int = 0          # version 非法丢弃数（非法主头）
    crc_failed: int = 0           # 帧尾 CRC-16 失败丢弃数
    counter_jumps: int = 0        # 帧计数器跳变次数（链路丢帧证据）
    bytes_dropped: int = 0        # 非载荷字节：垃圾前缀 / SYNCD 跳过 / ASM
    residue_bytes: int = 0        # flush 时不足一帧的残留字节数


def _crc16_x25(data: bytes) -> int:
    """帧尾 FHEC 的 CRC-16/X.25（CCSDS 可选项）。复用 `ax25.crc16_ccitt`
    单一来源；检查值 `CRC-16/X.25("123456789") = 0x906E` 由测试钉死。"""
    from mbdsdr_ai.ax25 import crc16_ccitt
    return crc16_ccitt(data)


class DatafieldVcduReassembler:
    """DATA FIELD 字节流 → VCDU 帧的流式重组器。

    用法（带 BBHEADER 的完整链）::

        r = DatafieldVcduReassembler(vcdu_len=892)
        for frame in fy4_dvbs2_sync(symbols):
            if frame["success"]:
                r.feed_bbframe(frame["bbframe"])   # 10B BBHEADER + DATA FIELD
        report = r.flush()

    裸字节流入口::

        r.feed(datafield_bytes, syncd_bits=df_syncd)   # 已知 SYNCD
        r.feed(datafield_bytes)                        # 未知：ASM/两帧假设
    """

    def __init__(self, vcdu_len: int = 892, expect_crc: bool = False,
                 accepted_versions=(VCDU_VALID_VERSION,)):
        """vcdu_len：VCDU 帧长（字节）；expect_crc：帧尾 CRC-16 校验开关；
        accepted_versions：接受的 VCDU version 集合（默认 (1,) 为 CCSDS
        标准口径；仓内 `fengyun_sat.file_to_vcdus` 编码侧造 version=0 帧，
        兼容该链时显式传 (0, 1)）。"""
        if vcdu_len < VCDU_HEADER_BYTES + 2:
            raise ValueError(f"VCDU 长度非法: {vcdu_len}")
        self.vcdu_len = int(vcdu_len)
        self.expect_crc = bool(expect_crc)
        self.accepted_versions = frozenset(int(v) for v in accepted_versions)
        self.stats = ReassemblyStats()
        self._buf = bytearray()
        self._aligned = False
        self._basis: Optional[str] = None     # "syncd" / "asm" / "assumed"
        self._prev_counter: Optional[int] = None

    # ------------------------------------------------------------------ #
    def feed(self, datafield: bytes, *, syncd_bits: Optional[int] = None
             ) -> List[VcduFrame]:
        """喂入一段 DATA FIELD 字节，返回本次新完成的合法 VCDU 列表。

        syncd_bits：该 DATA FIELD 所属 BBHEADER 的 SYNCD（比特单位，
        ETSI §5.1.6）。仅在尚未对齐时生效；已对齐后流连续，后续字段的
        SYNCD 不再参与切分（标准里它是「本字段首个整帧的距离」）。
        `None` 表示调用方不知道 SYNCD——此时只能靠 ASM 或两帧假设对齐。
        """
        if not isinstance(datafield, (bytes, bytearray, memoryview)):
            raise TypeError("datafield 必须是字节序列")
        data = bytes(datafield)

        if not self._aligned and syncd_bits is not None:
            data = self._apply_syncd(data, int(syncd_bits))

        self._buf += data
        out: List[VcduFrame] = []
        self._resolve_alignment()
        if not self._aligned:
            return out
        while len(self._buf) >= self.vcdu_len:
            if self._buf[:len(ASM)] == ASM:
                del self._buf[:len(ASM)]          # CADU 口径：帧间 ASM 剥离
                self.stats.bytes_dropped += len(ASM)
                continue
            frame = bytes(self._buf[:self.vcdu_len])
            del self._buf[:self.vcdu_len]
            parsed = self._parse_frame(frame)
            if parsed is not None:
                out.append(parsed)
        return out

    # ------------------------------------------------------------------ #
    def feed_bbframe(self, bbframe_bits) -> List[VcduFrame]:
        """喂入一个已解扰的 BBFRAME（np.uint8 0/1 比特序列）：解析 10B
        BBHEADER（含 CRC-8 校验）→ 按 DFL 截取 DATA FIELD → 交 `feed`。

        BBHEADER 校验失败 / DFL 越界由 `fengyun_sat.parse_bbheader` 直接抛
        `ValueError`（诚实报错，不吞）；DFL 或 SYNCD 非整字节时同样抛错，
        因为字节级 VCDU 无法落在半个字节上。
        """
        import numpy as np

        from mbdsdr_ai.fengyun_sat import parse_bbheader, DVBS2_BBHEADER_BITS
        header = parse_bbheader(bbframe_bits)      # CRC-8 失败即 ValueError
        dfl = int(header["dfl_bits"])
        if dfl % 8:
            raise ValueError(f"DFL={dfl} bit 非整字节，无法切字节级 VCDU")
        bits = np.asarray(bbframe_bits, dtype=np.uint8)
        payload = np.packbits(
            bits[DVBS2_BBHEADER_BITS:DVBS2_BBHEADER_BITS + dfl]
        ).tobytes() if dfl else b""
        return self.feed(payload, syncd_bits=int(header["syncd_bits"]))

    # ------------------------------------------------------------------ #
    def _apply_syncd(self, data: bytes, syncd: int) -> bytes:
        """按 ETSI §5.1.6 的 SYNCD 语义，把本 DATA FIELD 的起点挪到首个
        整帧起点；返回可继续重组的字节。整段跳过时返回 b""。"""
        if not 0 <= syncd <= SYNCD_NO_UP:
            raise ValueError(f"SYNCD 越界: {syncd}（合法 0..65535）")
        if syncd == SYNCD_NO_UP:
            # 本字段内无 UP 起点：字段内容不构成任何完整帧，如实整段计入丢弃；
            # 不对齐——下一个 DATA FIELD 会带自己的 SYNCD 再定位。
            self.stats.bytes_dropped += len(data)
            return b""
        self._basis = "syncd"
        if syncd % 8:
            raise ValueError(
                f"SYNCD={syncd} bit 非整字节，字节级 VCDU 无法对齐")
        skip = syncd // 8
        if skip:
            dropped = min(skip, len(data))
            self.stats.bytes_dropped += dropped
            data = data[dropped:]
        self._aligned = True
        return data

    # ------------------------------------------------------------------ #
    def _header_plausible(self, offset: int) -> bool:
        """某一偏移处的主头 version 是否落在接受集合（假设对齐的证据判据）。"""
        if offset >= len(self._buf):
            return False
        return ((self._buf[offset] >> 6) & 0x3) in self.accepted_versions

    def _resolve_alignment(self) -> None:
        """无声明（syncd_bits=None）时的对齐判定：先找 ASM 硬锁，再两帧假设。"""
        if self._aligned:
            return
        i = self._buf.find(ASM)
        if i >= 0:
            del self._buf[:i + len(ASM)]
            self.stats.bytes_dropped += i + len(ASM)
            self.stats.asm_locks += 1
            self._basis = "asm"
            self._aligned = True
            return
        # 两帧证据：首帧与次帧的 version 同时合法才认（单帧判据 1/4 误锁）
        while len(self._buf) >= 2 * self.vcdu_len:
            if (self._header_plausible(0)
                    and self._header_plausible(self.vcdu_len)):
                self._basis = "assumed"
                self.stats.assumed_aligned = True
                self._aligned = True
                return
            # 假设不成立：按单字节滑动重找起点（丢弃字节如实计数）
            del self._buf[:1]
            self.stats.bytes_dropped += 1

    # ------------------------------------------------------------------ #
    def _parse_frame(self, frame: bytes) -> Optional[VcduFrame]:
        b0, b1 = frame[0], frame[1]
        version = (b0 >> 6) & 0x3
        if version not in self.accepted_versions:
            self.stats.version_bad += 1
            return None
        spacecraft_id = ((b0 & 0x3F) << 2) | (b1 >> 6)
        vcid = b1 & 0x3F
        if vcid == VCDU_FILL_VCID:
            self.stats.fill_dropped += 1
            return None
        replay = bool(frame[5] & 0x80)
        counter = (frame[2] << 16) | (frame[3] << 8) | frame[4]
        crc_ok: Optional[bool] = None
        if self.expect_crc:
            got = int.from_bytes(frame[-2:], "big")
            crc_ok = _crc16_x25(frame[:-2]) == got
            if not crc_ok:
                self.stats.crc_failed += 1
                return None
        if self._prev_counter is not None and \
                counter != (self._prev_counter + 1) & 0xFFFFFF:
            self.stats.counter_jumps += 1
        self._prev_counter = counter
        self.stats.frames += 1
        return VcduFrame(raw=frame, version=version,
                         spacecraft_id=spacecraft_id, vcid=vcid,
                         replay=replay, counter=counter, crc_ok=crc_ok)

    # ------------------------------------------------------------------ #
    def flush(self) -> dict:
        """收尾：残留字节如实计数，返回完整性报告（dict 快照）。"""
        self.stats.residue_bytes = len(self._buf)
        return {
            "vcdu_len": self.vcdu_len,
            "aligned": self._aligned,
            "alignment_basis": self._basis,
            "asm_locks": self.stats.asm_locks,
            "assumed_aligned": self.stats.assumed_aligned,
            "frames": self.stats.frames,
            "fill_dropped": self.stats.fill_dropped,
            "version_bad": self.stats.version_bad,
            "crc_failed": self.stats.crc_failed,
            "counter_jumps": self.stats.counter_jumps,
            "bytes_dropped": self.stats.bytes_dropped,
            "residue_bytes": self.stats.residue_bytes,
        }
