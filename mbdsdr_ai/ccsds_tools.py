# SPDX-License-Identifier: MIT
"""CCSDS 数字接收链工具化（把已有能力交给模型直接操作）

背景：`mbdsdr_ai/ccsds_rx.py` 里已经有一整条干净的 CCSDS 数字接收链
（ASM 同步状态机 → K=7/r=1/2 Viterbi → 255 字节 PN 解扰 → RS），
但只有 SSDV 专用的 `ssdv_ccsds_decode` 暴露给了模型；像 ASTU-1（阿斯图友谊号，
435.400 MHz BPSK 遥测）这类**同样走 CCSDS TM 体制**的任务，模型没有入口。

本模块把通用链暴露为可编排的积木，并补上编码侧（造向量/自环对拍）：

    ccsds_encode_frame  信息字节 → (解扰对偶的扰码 → 卷积编码 → 32bit ASM) 比特流
    ccsds_decode_bits   比特流 → ASM 同步 → [Viterbi] → 解扰 → [RS] → 帧字节
    ccsds_decode_iq     复 IQ → (统一调制内核 demod) → 比特流 → 同上

参数纪律（AGENTS.md 诚实红线）：帧长、卷积率、解扰开关、RS 参数一律为
**具名参数**并逐级可覆盖；不内置任何"猜出来"的物理层参数。ASTU-1 的教程
只给了星座/比特级链路，帧级参数未给——本模块不替它猜，由调用方按任务参数填。
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

import numpy as np

from .ccsds_rx import (
    CCSDS_ASM_WORD,
    CONV_G1,
    CONV_G2,
    DEFAULT_ASM_MAX_HAMMING,
    RS_NSYM,
    SSDV_PACKET_LEN,
    AsmFramer,
    ConvolutionalEncoder,
    CcsdsReceiveChain,
    CcsdsRxConfig,
    bits_to_bytes_msb,
    bytes_to_bits_msb,
)
from .fec import Scrambler
from .tool_registry import ToolResult

__all__ = [
    "ccsds_encode_frame", "ccsds_decode_bits", "ccsds_decode_iq",
    "register_ccsds_tools", "build_config",
]


def _err(e: Exception) -> ToolResult:
    return ToolResult(success=False,
                      content=f"参数/计算错误: {type(e).__name__}: {e}",
                      data={"error": str(e)})


def build_config(args: Dict[str, Any]) -> CcsdsRxConfig:
    """从工具参数构造接收链配置（逐项可覆盖，不猜默认值）。"""
    use_vit = bool(args.get("use_viterbi", False))
    info_bits = int(args.get("info_bits", SSDV_PACKET_LEN * 8))
    frame_bits = int(args.get("frame_bits_after_asm",
                              info_bits * 2 if use_vit else info_bits))
    return CcsdsRxConfig(
        asm_word=int(args.get("asm_word", CCSDS_ASM_WORD)),
        asm_max_hamming=int(args.get("asm_max_hamming", DEFAULT_ASM_MAX_HAMMING)),
        frame_bits_after_asm=frame_bits,
        use_viterbi=use_vit,
        use_descramble=bool(args.get("use_descramble", True)),
        use_rs=bool(args.get("use_rs", False)),
        rs_nsym=int(args.get("rs_nsym", RS_NSYM)),
        g1=int(args.get("g1", CONV_G1)),
        g2=int(args.get("g2", CONV_G2)),
    )


#: ASM 诊断的最大扫描位数（8 处命中即停），避免大输入上做 O(n×32) 全扫
_ASM_SCAN_LIMIT = 1 << 18


def _asm_diagnostic(bits: List[int], cfg: CcsdsRxConfig) -> Dict[str, Any]:
    """0 帧时的可执行诊断：ASM 是否存在/在何处、帧长差多少、下一步查什么。"""
    asm_bits = bytes_to_bits_msb(int(cfg.asm_word).to_bytes(4, "big"))
    n = len(bits)
    positions: List[int] = []
    # 扫描上界：诊断只需定位"前若干处 ASM"，全量扫描 O(n×32) 在大输入上太慢
    scan_limit = min(n - 31, _ASM_SCAN_LIMIT) if n >= 32 else 0
    for i in range(0, max(0, scan_limit)):
        if list(bits[i:i + 32]) == asm_bits:
            positions.append(i)
            if len(positions) >= 8:
                break
    need = int(cfg.frame_bits_after_asm)
    info: Dict[str, Any] = {
        "asm_hex": f"{int(cfg.asm_word):08X}",
        "asm_found": bool(positions),
        "asm_positions": positions[:8],
        "n_bits_in": n,
        "frame_bits_after_asm": need,
        "asm_max_hamming": int(cfg.asm_max_hamming),
    }
    if scan_limit < max(0, n - 31):
        info["scan_truncated"] = True
        info["scanned_bits"] = scan_limit
    if positions:
        p = positions[0]
        avail = n - p - 32
        info["bits_after_first_asm"] = avail
        if avail < need:
            info["cause"] = "frame_bits_after_asm 与输入不匹配"
            info["hint"] = (f"输入在 ASM 后只有 {avail} bit，不足当前配置的 "
                            f"frame_bits_after_asm={need} bit；请把该参数改回 "
                            f"{avail}（或编码端返回的 frame_bits_after_asm）再试")
        else:
            info["cause"] = "帧已收集但内容校验未过"
            info["hint"] = ("帧长够但没解出帧：检查 use_descramble / use_viterbi / "
                            "use_rs 是否与发送端一致，以及 ASM 是否误同步")
    else:
        info["cause"] = "未找到 ASM 同步字"
        info["hint"] = (f"输入里找不到 ASM 0x{int(cfg.asm_word):08X}"
                        f"（hamming 容差 {int(cfg.asm_max_hamming)}）：依次排查 "
                        "比特序(MSB/LSB)、是否被卷积编码、是否需要先解扰、"
                        "以及发送端是否用了不同 ASM 字；"
                        "若是误码导致可放宽 asm_max_hamming")
    return info


# ── 编码侧：造帧（信息字节 → 比特流）─────────────────────────────────
def ccsds_encode_frame(args) -> Dict[str, Any]:
    """信息字节 → CCSDS 帧比特流（扰码 → K=7 卷积 → 32bit ASM 前导）。

    扰码与解扰是同一操作（PN 表 XOR），故"编码"用扰码、"解码"用解扰。
    """
    data = args.get("data")
    if data is None:
        raise ValueError("data 必须提供（字节列表或 hex 字符串）")
    if isinstance(data, str):
        payload = bytes.fromhex(data.replace(" ", "").replace(":", ""))
    else:
        payload = bytes(int(b) & 0xFF for b in data)
    if not payload:
        raise ValueError("data 为空")
    cfg = build_config(args)
    if cfg.use_descramble:
        payload = Scrambler().scramble(payload)
    if cfg.use_viterbi:
        enc = ConvolutionalEncoder(cfg.g1, cfg.g2)
        tail = int(args.get("conv_tail_bits", 6))
        pairs = enc.encode_bytes(payload, tail=tail)
        body_bits: List[int] = []
        for g1b, g2b in pairs:
            body_bits.extend((g1b, g2b))
    else:
        body_bits = bytes_to_bits_msb(payload)
    asm_bits = bytes_to_bits_msb(
        int(cfg.asm_word).to_bytes(4, "big"))
    bits = asm_bits + body_bits
    return {
        "bits": bits,
        "n_bits": len(bits),
        "info_bytes": len(payload),
        "asm_hex": f"{int(cfg.asm_word):08X}",
        "frame_bits_after_asm": len(body_bits),
        "use_viterbi": cfg.use_viterbi,
        "use_descramble": cfg.use_descramble,
    }


# ── 解码侧：比特 → 帧 ──────────────────────────────────────────────
def ccsds_decode_bits(args) -> Dict[str, Any]:
    """比特流 → CCSDS 帧（ASM 同步 → [Viterbi] → 解扰 → [RS]）。"""
    bits = args.get("bits")
    if bits is None:
        raise ValueError("bits 必须提供（0/1 列表）")
    cfg = build_config(args)
    chain = CcsdsReceiveChain(cfg)
    frames = chain.process_bits([int(b) & 1 for b in bits])
    out_frames = []
    for f in frames:
        out_frames.append({
            "n_bytes": len(f),
            "hex": f.hex().upper(),
            "ascii_preview": "".join(chr(b) if 32 <= b < 127 else "." for b in f[:64]),
        })
    out = {
        "frames": out_frames,
        "count": len(out_frames),
        "config": {
            "asm_word": f"{cfg.asm_word:08X}",
            "frame_bits_after_asm": cfg.frame_bits_after_asm,
            "use_viterbi": cfg.use_viterbi,
            "use_descramble": cfg.use_descramble,
            "use_rs": cfg.use_rs,
        },
    }
    if not out_frames:
        out["diagnostic"] = _asm_diagnostic([int(b) & 1 for b in bits], cfg)
    return out


# ── 解码侧：IQ → 帧（接统一调制内核）───────────────────────────────
def ccsds_decode_iq(args) -> Dict[str, Any]:
    """复 IQ → CCSDS 帧（先用统一调制内核解出比特，再走 CCSDS 链）。

    调制参数（scheme/sps/shaping/alpha）为具名参数；BPSK + RRC 是 ASTU-1
    这类 BPSK 遥测的常见体制。
    """
    iq = args.get("iq")
    if iq is None:
        raise ValueError("iq 必须提供（复数采样列表）")
    from .modulation import demodulate_iq
    scheme = str(args.get("scheme", "bpsk"))
    sps = int(args.get("sps", 4))
    shaping = str(args.get("shaping", "rrc"))
    alpha = float(args.get("alpha", 0.35))
    dem = demodulate_iq(iq, scheme=scheme, sps=sps, shaping=shaping, alpha=alpha)
    bits = [int(b) for b in dem["bits"]]
    cfg = build_config(args)
    chain = CcsdsReceiveChain(cfg)
    frames = chain.process_bits(bits)
    out = {
        "frames": [{"n_bytes": len(f), "hex": f.hex().upper(),
                    "ascii_preview": "".join(chr(b) if 32 <= b < 127 else "."
                                             for b in f[:64])}
                   for f in frames],
        "count": len(frames),
        "demod": dem["params"],
        "n_bits_demodded": len(bits),
    }
    if not out["frames"]:
        out["diagnostic"] = _asm_diagnostic(bits, cfg)
    return out


# ── IQ → 帧（内核版，供工具与上层复用）────────────────────────
def ccsds_iq_to_frames(args) -> Tuple[List[bytes], Dict[str, Any]]:
    """复 IQ → CCSDS 帧字节列表 + 解调统计（ccsds_decode_iq 的内核版）。

    调制参数（scheme/sps/shaping/alpha）与链路参数（asm_word/帧长/卷积/
    解扰/RS）全部来自 args，缺省见 :func:`build_config`。
    """
    iq = args.get("iq")
    if iq is None:
        raise ValueError("iq 必须提供（复数采样列表）")
    from .modulation import demodulate_iq
    scheme = str(args.get("scheme", "bpsk"))
    sps = int(args.get("sps", 4))
    shaping = str(args.get("shaping", "rrc"))
    alpha = float(args.get("alpha", 0.35))
    dem = demodulate_iq(iq, scheme=scheme, sps=sps, shaping=shaping, alpha=alpha)
    bits = [int(b) for b in dem["bits"]]
    cfg = build_config(args)
    chain = CcsdsReceiveChain(cfg)
    frames = chain.process_bits(bits)
    stats = {
        "demod": dem["params"],
        "n_bits_demodded": len(bits),
        "n_frames": len(frames),
        "config": {
            "asm_word": f"{cfg.asm_word:08X}",
            "frame_bits_after_asm": cfg.frame_bits_after_asm,
            "use_viterbi": cfg.use_viterbi,
            "use_descramble": cfg.use_descramble,
            "use_rs": cfg.use_rs,
            "g1": f"{cfg.g1:o}", "g2": f"{cfg.g2:o}",
        },
    }
    return frames, stats


# ── 帧载荷 → 遥测文本流 ───────────────────────────────────────
#: 视为可打印的字节（ASCII 可见字符 + 空白控制符）
_PRINTABLE = set(range(32, 127)) | {9, 10, 13}


def ccsds_frames_to_text(frames: List[bytes], *,
                         min_run: int = 4) -> Dict[str, Any]:
    """CCSDS 帧载荷 → 遥测文本流。

    ASRTU-1 这类小卫星的遥测下行是"CCSDS 帧里装 ASCII 文本"，解码工具在
    控制台打印出来的就是这些字符。本函数把各帧载荷拼接后抽出连续可打印段，
    段间以换行连接。

    返回：text / runs / n_printable / printable_ratio / payload_bytes /
    payload_hex_head。**没有可打印内容时 text 为空**（并给出可打印率与原始
    载荷头），不拿二进制硬编成"遥测"。
    """
    payload = b"".join(bytes(f) for f in frames)
    runs: List[str] = []
    cur = bytearray()
    for b in payload:
        if b in _PRINTABLE:
            cur.append(b)
        else:
            if len(cur) >= max(1, int(min_run)):
                runs.append(cur.decode("ascii", "replace"))
            cur = bytearray()
    if len(cur) >= max(1, int(min_run)):
        runs.append(cur.decode("ascii", "replace"))
    text = "\n".join(runs)
    n_print = sum(1 for b in payload if b in _PRINTABLE)
    return {
        "text": text,
        "runs": runs,
        "n_runs": len(runs),
        "n_printable": n_print,
        "printable_ratio": (n_print / len(payload)) if payload else 0.0,
        "payload_bytes": len(payload),
        "payload_hex_head": payload[:64].hex().upper(),
        "min_run": int(min_run),
    }

# ═══════════════════════════════════════════════════════════
# 工具层
# ═══════════════════════════════════════════════════════════
def register_ccsds_tools(registry) -> None:
    """把 CCSDS 通用数字接收链积木注册进工具注册表（逐块，不打包）。"""

    def _encode(args):
        try:
            r = ccsds_encode_frame(args)
            return ToolResult(
                success=True,
                content=(f"CCSDS 帧已构造：{r['n_bits']} bit"
                         f"（信息 {r['info_bytes']} B，ASM={r['asm_hex']}，"
                         f"卷积={'on' if r['use_viterbi'] else 'off'}，"
                         f"扰码={'on' if r['use_descramble'] else 'off'}）"),
                data=r)
        except Exception as e:
            return _err(e)

    def _astu_telemetry(args):
        """IQ → CCSDS 帧 → 遥测文本流（ASRTU-1 教程收尾产出）。"""
        import base64 as _b64
        import json as _json
        try:
            iq = args.get("iq")
            if iq is None:
                path = args.get("iq_path")
                if path:
                    import numpy as _np
                    iq = _np.fromfile(path, dtype=_np.complex64)
                else:
                    b64 = args.get("iq_base64")
                    if not b64:
                        raise ValueError(
                            "需要 iq（复数采样列表）或 iq_path（complex64 raw 文件）"
                            "或 iq_base64（float32 交错 base64）")
                    import numpy as _np
                    raw = _np.frombuffer(_b64.b64decode(str(b64)),
                                         dtype=_np.float32)
                    if raw.size % 2:
                        raw = raw[:-1]
                    iq = (raw[0::2] + 1j * raw[1::2]).astype(_np.complex64)
            frames, stats = ccsds_iq_to_frames(args)
        except Exception as e:  # noqa: BLE001
            return _err(e)
        text_info = ccsds_frames_to_text(frames,
                                        min_run=int(args.get("min_run", 4)))
        out = {
            "text": text_info["text"],
            "n_frames": stats["n_frames"],
            "n_runs": text_info["n_runs"],
            "printable_ratio": round(text_info["printable_ratio"], 4),
            "payload_bytes": text_info["payload_bytes"],
            "payload_hex_head": text_info["payload_hex_head"],
            "config": stats["config"],
            "demod": stats["demod"],
        }
        if not frames:
            cfg = stats["config"]
            out["error"] = ("未解出任何 CCSDS 帧（ASM 同步未命中）——"
                            "检查帧长 frame_bits_after_asm 是否与实际下行一致，"
                            "以及 scheme/sps/alpha 是否对")
            out["diagnostic"] = {
                "cause": "ASM 同步未命中",
                "hint": ("帧长是任务级参数：按实际下行给 frame_bits_after_asm；"
                         "也可用 ccsds_decode_bits 在比特层先定位 ASM"),
                "config": cfg,
            }
            return ToolResult(success=False,
                              content=_json.dumps(out, ensure_ascii=False,
                                                  default=str),
                              data=out)
        if not text_info["text"]:
            out["note"] = ("帧已解出但几乎没有可打印内容"
                           "（可打印率 %.1f%%）——ASRTU-1 遥测通常是 ASCII 文本，"
                           "此处若无文本，请检查是否错帧/错解扰，或该帧载荷本就是"
                           "二进制" % (100.0 * text_info["printable_ratio"]))
        return ToolResult(
            success=True,
            content=(_json.dumps(out, ensure_ascii=False, default=str)),
            data=out)

    def _decode_bits(args):
        try:
            r = ccsds_decode_bits(args)
            return ToolResult(
                success=True,
                content=f"CCSDS 同步解出 {r['count']} 帧"
                         + (f"；首帧 {r['frames'][0]['n_bytes']} B: "
                            f"{r['frames'][0]['hex'][:64]}"
                            if r["frames"] else "（无有效帧——检查 ASM 字/帧长/卷积率）"),
                data=r)
        except Exception as e:
            return _err(e)

    def _decode_iq(args):
        try:
            r = ccsds_decode_iq(args)
            return ToolResult(
                success=True,
                content=(f"IQ → {r['demod']} 解出 {r['n_bits_demodded']} bit，"
                         f"CCSDS 同步得 {r['count']} 帧"),
                data=r)
        except Exception as e:
            return _err(e)

    registry.register(
        name="ccsds_encode_frame",
        description="CCSDS 帧构造：信息字节 → (扰码 → K=7 r=1/2 卷积 → 32bit ASM) 比特流。"
                    "用于造向量与自环对拍；帧长/卷积/解扰/RS 全部为具名参数。",
        parameters={"type": "object", "properties": {
            "data": {"type": "array", "items": {"type": "integer"},
                     "description": "信息字节（0-255）；也接受 hex 字符串"},
            "use_viterbi": {"type": "boolean", "default": False,
                            "description": "是否用 K=7 r=1/2 卷积（真体制用 true）"},
            "use_descramble": {"type": "boolean", "default": True},
            "info_bits": {"type": "integer", "default": 256 * 8,
                          "description": "ASM 之后的信息比特数（卷积关时=帧长×8）"},
            "asm_word": {"type": "integer", "description": "32bit ASM 同步字（默认 CCSDS 标准）"},
            "g1": {"type": "integer"}, "g2": {"type": "integer"},
            "conv_tail_bits": {"type": "integer", "default": 6},
        }, "required": ["data"]},
        handler=_encode, category="decode")

    registry.register(
        name="ccsds_decode_bits",
        description="CCSDS 比特流解码：ASM 同步 → [Viterbi] → 解扰 → [RS] → 帧字节。"
                    "参数（ASM 字/汉明容限/帧长/卷积/解扰/RS）逐项可覆盖，不猜参数。",
        parameters={"type": "object", "properties": {
            "bits": {"type": "array", "items": {"type": "integer"}},
            "asm_word": {"type": "integer"},
            "asm_max_hamming": {"type": "integer", "default": 0},
            "frame_bits_after_asm": {"type": "integer"},
            "info_bits": {"type": "integer", "default": 256 * 8},
            "use_viterbi": {"type": "boolean", "default": False},
            "use_descramble": {"type": "boolean", "default": True},
            "use_rs": {"type": "boolean", "default": False},
            "rs_nsym": {"type": "integer"},
            "g1": {"type": "integer"}, "g2": {"type": "integer"},
        }, "required": ["bits"]},
        handler=_decode_bits, category="decode")

    registry.register(
        name="astu_telemetry_decode",
        description=(
            "ASRTU-1（阿斯图友谊号）遥测解码：复 IQ → 统一调制内核解调"
            "（BPSK+RRC 常见）→ CCSDS 链（ASM 0x1ACFFC1D 同步 → Viterbi → "
            "解扰 → [RS]) → 帧载荷 → **遥测文本流**（连续可打印段，即教程里"
            "控制台出现的那串字符）。"
            "前置条件：需给 iq（复数采样列表）或 iq_path（complex64 raw 文件）"
            "或 iq_base64（float32 交错 base64）；**帧长是任务级参数**——"
            "frame_bits_after_asm 必须按实际下行给对，否则解不出帧。"
            "失败恢复：0 帧时返回 error + diagnostic（点名最可能是帧长/体制"
            "不对）；帧解出但无可打印内容时返回可打印率与原始载荷头，**不编造"
            "遥测文本**。"
            "副作用：纯计算，不写文件、不动设备。"
            "能力边界：只做 CCSDS TM 帧层与文本还原；帧级协议（ASRTU-1 载荷的"
            "字段级语义）不在本工具范围内，需要按帧载荷自行解析。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "iq": {"type": "array", "items": {"type": "number"},
                       "description": "复数采样（实部 I、虚部 Q）"},
                "iq_path": {"type": "string",
                            "description": "complex64 IQ raw 文件路径"},
                "iq_base64": {"type": "string",
                              "description": "复数 IQ 的 base64（float32 交错）"},
                "scheme": {"type": "string", "enum": ["bpsk", "qpsk", "ask", "8psk"],
                           "default": "bpsk"},
                "sps": {"type": "integer", "default": 4},
                "shaping": {"type": "string", "enum": ["rect", "rrc"],
                            "default": "rrc"},
                "alpha": {"type": "number", "default": 0.35},
                "asm_word": {"type": "integer",
                             "description": "ASM 同步字（默认 0x1ACFFC1D）"},
                "frame_bits_after_asm": {"type": "integer",
                                         "description": "ASM 之后的编码比特数"
                                                        "（任务级参数，需按实际下行给）"},
                "use_viterbi": {"type": "boolean"},
                "use_descramble": {"type": "boolean"},
                "use_rs": {"type": "boolean"},
                "g1": {"type": "integer"}, "g2": {"type": "integer"},
                "min_run": {"type": "integer", "default": 4,
                            "description": "可打印段最小长度（短于此不计入文本）"},
            },
            "required": [],
        },
        handler=_astu_telemetry,
        category="satellite",
    )

    registry.register(
        name="ccsds_decode_iq",
        description="CCSDS 从复 IQ 解码：先用统一调制内核（ASK/BPSK/QPSK/8PSK，"
                    "NRZ 或 RRC）解出比特，再走 CCSDS 链。BPSK 体制（如 ASTU-1 遥测）"
                    "用 scheme=bpsk + shaping=rrc。",
        parameters={"type": "object", "properties": {
            "iq": {"type": "array", "items": {"type": "number"}},
            "scheme": {"type": "string", "default": "bpsk"},
            "sps": {"type": "integer", "default": 4},
            "shaping": {"type": "string", "default": "rrc"},
            "alpha": {"type": "number", "default": 0.35},
            "asm_word": {"type": "integer"},
            "asm_max_hamming": {"type": "integer", "default": 0},
            "frame_bits_after_asm": {"type": "integer"},
            "info_bits": {"type": "integer", "default": 256 * 8},
            "use_viterbi": {"type": "boolean", "default": False},
            "use_descramble": {"type": "boolean", "default": True},
            "use_rs": {"type": "boolean", "default": False},
        }, "required": ["iq"]},
        handler=_decode_iq, category="decode")
