# SPDX-License-Identifier: MIT
"""P0-5 TT&C 上行链路原子工具（TC 帧构建/解析 + PCM/PSK/PM 调制 + 发射人工门）。

能力拆解（218 条）P0-5 的原子能力：遥控上行帧构建与调制。下行接收链仓内
已厚（ccsds_tools），上行此前完全没有。本模块补齐（全部纯软件，可 golden）：

  * ``tc_build_frame``   数据 → CCSDS TC 传输帧（6 字节头 + 数据 + CRC16-CCSDS）
  * ``tc_frame_parse``   帧字节 → 头字段 + 数据 + CRC 校验（对拍/验收侧）
  * ``pcm_psk_pm_modulate`` 比特 → NRZ-L → PSK 副载波 → PM 载波（复基带）
  * ``tc_uplink_transmit`` **发射工具**：arm_manual 人工门 + 无设备诚实 NEED_HW

诚实/安全纪律（AGENTS §2/§3，最高优先级）：
  * 发射工具未显式 ``arm_manual=true`` 一律拒绝（gated 信封，不假装成功）；
  * 已武装但环境无可用发射设备 → 如实 NEED_HW，绝不伪造"已发射"；
  * 频点/帧布局不硬编码：布局从 frame_layouts.json 注入，频点调用方给；
  * TC 帧头各字段位宽按 CCSDS 232.0-B 公开标准，golden 自编码器单源生成。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np

from .crc import CRC16_CCSDS, crc_generic
from .sat_profile import load_frame_layout

__all__ = [
    "tc_build_frame", "tc_frame_parse", "pcm_psk_pm_modulate",
    "tc_uplink_transmit", "register_tool_registry",
]

#: TC 帧固定头长（本仓布局口径：5 字节标准主头 + 1 字节保留）
_TC_HEADER_LEN = 6
#: FECF（帧差错控制域）长度
_TC_FECF_LEN = 2


def tc_build_frame(data: bytes, *,
                   spacecraft_id: int = 0,
                   virtual_channel_id: int = 0,
                   bypass: int = 0,
                   ctrl_cmd: int = 0,
                   frame_seq: int = 0,
                   layout_name: str = "ccsds_tc_classic",
                   ) -> Dict[str, Any]:
    """数据字节 → CCSDS TC 传输帧（头 + 数据 + CRC16-CCSDS）。

    头布局（CCSDS 232.0-B，6 字节口径见 frame_layouts.json）：
      byte0  : TFVN(2b)=0 | bypass(1b) | ctrl_cmd(1b) | spare(2b) | SCID 高 2 位
      byte1  : SCID 低 8 位（共 10 位）
      byte2  : VCID(6b) | 帧长字段高 2 位
      byte3  : 帧长字段低 8 位（= 头+数据+CRC 总字节数 − 1）
      byte4  : 帧序列号（8b）
      byte5  : 保留（0）
    字段取值域越界即报错（不静默截断）；CRC 覆盖 头+数据。
    """
    layout = load_frame_layout(layout_name)
    if layout.get("crc", {}).get("name") != "crc16_ccsds":
        raise ValueError(f"布局 {layout_name} 的 CRC 不是 crc16_ccsds，"
                         "本构建器只按 CCSDS TC 口径工作")
    if not 0 <= spacecraft_id < (1 << 10):
        raise ValueError("spacecraft_id 须 0..1023（10 bit）")
    if not 0 <= virtual_channel_id < (1 << 6):
        raise ValueError("virtual_channel_id 须 0..63（6 bit）")
    for name, val, bits in (("bypass", bypass, 1), ("ctrl_cmd", ctrl_cmd, 1),
                            ("frame_seq", frame_seq, 8)):
        if not 0 <= val < (1 << bits):
            raise ValueError(f"{name} 须 0..{(1 << bits) - 1}（{bits} bit）")
    payload = bytes(data)
    total = _TC_HEADER_LEN + len(payload) + _TC_FECF_LEN
    length_field = total - 1
    header = bytes([
        (0 << 6) | ((bypass & 1) << 5) | ((ctrl_cmd & 1) << 4)
        | ((spacecraft_id >> 8) & 0b11),
        spacecraft_id & 0xFF,
        ((virtual_channel_id & 0x3F) << 2) | ((length_field >> 8) & 0b11),
        length_field & 0xFF,
        frame_seq & 0xFF,
        0,
    ])
    body = header + payload
    fecf = crc_generic(body, CRC16_CCSDS).to_bytes(_TC_FECF_LEN, "big")
    frame = body + fecf
    return {
        "frame_hex": frame.hex().upper(),
        "n_bytes": len(frame),
        "header_hex": header.hex().upper(),
        "data_hex": payload.hex().upper(),
        "fecf_hex": fecf.hex().upper(),
        "fields": {
            "tfvn": 0, "bypass": bypass, "ctrl_cmd": ctrl_cmd,
            "spacecraft_id": spacecraft_id,
            "virtual_channel_id": virtual_channel_id,
            "frame_length_field": length_field,
            "frame_seq": frame_seq,
        },
        "layout": layout_name,
    }


def tc_frame_parse(frame: bytes, *,
                   layout_name: str = "ccsds_tc_classic",
                   ) -> Dict[str, Any]:
    """TC 帧字节 → 头字段 + 数据 + CRC 校验结果。

    CRC 不过如实报 crc_ok=False 并在 reason 说明（坏帧不产出"看似正常"的
    字段集）；头/总长不自洽（帧长字段 ≠ 总字节 −1）同理拒绝。
    """
    layout = load_frame_layout(layout_name)
    b = bytes(frame)
    if len(b) < _TC_HEADER_LEN + _TC_FECF_LEN:
        raise ValueError(
            f"帧长 {len(b)} < 头{_TC_HEADER_LEN}+CRC{_TC_FECF_LEN}——"
            "不是完整 TC 帧")
    h = b[:_TC_HEADER_LEN]
    length_field = ((h[2] & 0b11) << 8) | h[3]
    total = length_field + 1
    if total != len(b):
        raise ValueError(
            f"帧长字段 {length_field} → 期望总长 {total}，"
            f"实际 {len(b)}——帧边界不对齐或截断，拒绝解析")
    body, fecf = b[:-2], b[-2:]
    want = crc_generic(body, CRC16_CCSDS).to_bytes(_TC_FECF_LEN, "big")
    crc_ok = (fecf == want)
    fields = {
        "tfvn": (h[0] >> 6) & 0b11,
        "bypass": (h[0] >> 5) & 1,
        "ctrl_cmd": (h[0] >> 4) & 1,
        "spare": (h[0] >> 2) & 0b11,
        "spacecraft_id": ((h[0] & 0b11) << 8) | h[1],
        "virtual_channel_id": (h[2] >> 2) & 0x3F,
        "frame_length_field": length_field,
        "frame_seq": h[4],
        "reserved": h[5],
    }
    out = {
        "fields": fields,
        "data_hex": body[_TC_HEADER_LEN:].hex().upper(),
        "data": list(body[_TC_HEADER_LEN:]),
        "fecf_hex": fecf.hex().upper(),
        "crc_ok": crc_ok,
        "layout": layout_name,
    }
    if not crc_ok:
        out["reason"] = (f"FECF {fecf.hex().upper()} ≠ 计算 {want.hex().upper()}"
                         "——帧内容损坏，字段仅供参考，不可用于上行")
    return out


def pcm_psk_pm_modulate(bits: List[int], *,
                        sample_rate_hz: float,
                        bit_rate_hz: float,
                        subcarrier_hz: float,
                        pm_index_rad: float = 1.0,
                        nrz: str = "nrz_l",
                        ) -> Dict[str, Any]:
    """比特 → PCM/PSK/PM 复基带（CCSDS TC 物理层口径，参数全具名）。

    bits            : 0/1 列表；
    sample_rate_hz  : 输出采样率；
    bit_rate_hz     : 码速率（= 副载波速率的一阶约定，二者独立给）；
    subcarrier_hz   : 副载波频率；
    pm_index_rad    : PM 调制指数（弧度，默认 1.0）；
    nrz             : "nrz_l"（默认）/ "nrz_m"（ marks 翻转）/ "nrz_s"。

    流程：NRZ 编码 → 按码元矩形成形 → 与副载波方波相乘（PSK 副载波）
    → PM 载波 exp(j·m·x)。返回复基带数组与参数回执（诚实复算）。
    """
    if not bits:
        raise ValueError("bits 为空（无输入，不产调制信号）")
    if sample_rate_hz <= 0 or bit_rate_hz <= 0 or subcarrier_hz <= 0:
        raise ValueError("sample_rate_hz / bit_rate_hz / subcarrier_hz 须为正")
    if subcarrier_hz >= sample_rate_hz / 2:
        raise ValueError("subcarrier_hz 超出奈奎斯特（≥ fs/2）——参数不自洽")
    sps = sample_rate_hz / bit_rate_hz
    if sps < 4:
        raise ValueError(
            f"每码元采样 {sps:.1f} < 4——采样率不足以无混叠表示该码速率")
    n = int(round(len(bits) * sps))
    t = np.arange(n) / sample_rate_hz
    # NRZ 编码
    if nrz == "nrz_l":
        levels = np.where(np.asarray(bits) & 1, 1.0, -1.0)
    elif nrz == "nrz_m":
        lv = []
        cur = 1.0
        for b in bits:
            if b:
                cur = -cur
            lv.append(cur)
        levels = np.asarray(lv)
    elif nrz == "nrz_s":
        lv = []
        cur = 1.0
        for b in bits:
            if not b:
                cur = -cur
            lv.append(cur)
        levels = np.asarray(lv)
    else:
        raise ValueError(f"未知 NRZ 口径: {nrz}（nrz_l/nrz_m/nrz_s）")
    sym_index = np.minimum((t * bit_rate_hz).astype(int), len(bits) - 1)
    base = levels[sym_index]
    # 副载波方波（PSK onto subcarrier）+ PM 载波
    sub = np.sign(np.sin(2.0 * np.pi * subcarrier_hz * t))
    phase = pm_index_rad * base * sub
    iq = np.exp(1j * phase)
    return {
        "iq": iq.astype(np.complex64),
        "n_samples": int(n),
        "params": {
            "sample_rate_hz": float(sample_rate_hz),
            "bit_rate_hz": float(bit_rate_hz),
            "subcarrier_hz": float(subcarrier_hz),
            "pm_index_rad": float(pm_index_rad),
            "nrz": nrz,
            "sps": float(sps),
            "n_bits": int(len(bits)),
        },
    }


def tc_uplink_transmit(*, arm_manual: bool = False,
                       iq=None,
                       freq_hz: Optional[float] = None,
                       device: Optional[str] = None,
                       gain_db: float = 0.0,
                       ) -> Dict[str, Any]:
    """TC 上行发射（人工门 + 无设备诚实 NEED_HW）。

    门语义（AGENTS §3，四道串联的最后一道）：
      * arm_manual 不是 True → gated 拒绝（不发射、不假装）；
      * 已武装但无可用发射设备 → NEED_HW 诚实空态；
      * 频点缺省 → 参数错误（频点是安全关键参数，不猜）。
    本函数不内置任何设备驱动；有设备时由各设备适配层在门通过后接管。
    """
    if arm_manual is not True:
        return {
            "status": "GATED",
            "reason": ("未过人工门：arm_manual 不是 true——"
                       "发射动作被明确拒绝（写工具 arm_manual 契约）"),
        }
    if freq_hz is None or float(freq_hz) <= 0:
        return {
            "status": "PARAM_ERROR",
            "reason": "频点 freq_hz 缺失或非法——安全关键参数不猜默认",
        }
    if iq is None:
        return {"status": "PARAM_ERROR", "reason": "iq 未提供（复基带采样）"}
    # 纯软件环境：无任何发射设备适配层声明可用 → 诚实 NEED_HW
    return {
        "status": "NEED_HW",
        "armed": True,
        "freq_hz": float(freq_hz),
        "n_samples": int(len(iq)),
        "device": device,
        "reason": ("已武装，但当前环境无可用发射设备适配层"
                   "（SDR TX 未接入/未声明）——不伪造发射，"
                   "接入设备后由设备层在门通过后执行"),
    }


# ═══════════════════════════════════════════════════════════
# 工具层
# ═══════════════════════════════════════════════════════════
def register_tool_registry(registry) -> None:
    """注册 P0-5 上行链路积木（帧构建/解析/调制为纯计算，发射走人工门）。"""
    from .tool_registry import ToolResult, clean_exc
    import json as _json

    def _build(args):
        try:
            data = args.get("data")
            if data is None:
                raise ValueError("data 必须提供（hex 字符串或字节列表）")
            if isinstance(data, str):
                payload = bytes.fromhex(data.replace(" ", ""))
            else:
                payload = bytes(int(b) & 0xFF for b in data)
            r = tc_build_frame(
                payload,
                spacecraft_id=int(args.get("spacecraft_id", 0)),
                virtual_channel_id=int(args.get("virtual_channel_id", 0)),
                bypass=int(args.get("bypass", 0)),
                ctrl_cmd=int(args.get("ctrl_cmd", 0)),
                frame_seq=int(args.get("frame_seq", 0)),
                layout_name=str(args.get("layout_name",
                                         "ccsds_tc_classic")),
            )
        except Exception as e:  # noqa: BLE001
            return ToolResult(success=False,
                              content=f"TC 帧构建失败: {clean_exc(e)}")
        return ToolResult(
            success=True,
            content=(f"TC 帧 {r['n_bytes']}B：{r['frame_hex'][:64]}"
                     + ("…" if len(r["frame_hex"]) > 64 else "")),
            data=r)

    def _parse(args):
        try:
            frame = args.get("frame")
            if frame is None:
                raise ValueError("frame 必须提供（hex 字符串或字节列表）")
            if isinstance(frame, str):
                raw = bytes.fromhex(frame.replace(" ", ""))
            else:
                raw = bytes(int(b) & 0xFF for b in frame)
            r = tc_frame_parse(raw,
                               layout_name=str(args.get(
                                   "layout_name", "ccsds_tc_classic")))
        except Exception as e:  # noqa: BLE001
            return ToolResult(success=False,
                              content=f"TC 帧解析失败: {clean_exc(e)}")
        ok = r["crc_ok"]
        return ToolResult(
            success=ok,
            content=(f"TC 帧解析：SCID={r['fields']['spacecraft_id']} "
                     f"VCID={r['fields']['virtual_channel_id']} "
                     f"CRC={'OK' if ok else 'FAIL'}"),
            data=r)

    def _mod(args):
        try:
            bits = args.get("bits")
            if not bits:
                raise ValueError("bits 必须提供（0/1 列表）")
            r = pcm_psk_pm_modulate(
                [int(b) & 1 for b in bits],
                sample_rate_hz=float(args["sample_rate_hz"]),
                bit_rate_hz=float(args["bit_rate_hz"]),
                subcarrier_hz=float(args["subcarrier_hz"]),
                pm_index_rad=float(args.get("pm_index_rad", 1.0)),
                nrz=str(args.get("nrz", "nrz_l")),
            )
        except Exception as e:  # noqa: BLE001
            return ToolResult(success=False,
                              content=f"PCM/PSK/PM 调制失败: {clean_exc(e)}")
        return ToolResult(
            success=True,
            content=(f"已调制 {r['n_samples']} 采样（fs={r['params']['sample_rate_hz']:.0f} "
                     f"Hz, {r['params']['nrz']}）"),
            data={"params": r["params"]})

    def _tx(args):
        r = tc_uplink_transmit(
            arm_manual=args.get("arm_manual", False),
            iq=args.get("iq"),
            freq_hz=args.get("freq_hz"),
            device=args.get("device"),
            gain_db=float(args.get("gain_db", 0.0)),
        )
        ok = r["status"] not in ("GATED", "PARAM_ERROR", "NEED_HW")
        return ToolResult(
            success=ok,
            content=_json.dumps(r, ensure_ascii=False),
            data=r)

    registry.register(
        name="tc_build_frame",
        description=(
            "构建 CCSDS TC 遥控帧：数据 + 6 字节头（SCID/VCID/帧长/序列号）"
            "+ CRC16-CCSDS。布局从 frame_layouts.json 注入（默认 ccsds_tc_classic）。"
            "字段取值域越界报错不静默截断。纯计算无副作用；产物供 tc_frame_parse "
            "对拍与上行链路使用。"
        ),
        parameters={"type": "object", "properties": {
            "data": {"type": "string", "description": "数据（hex 字符串）"},
            "spacecraft_id": {"type": "integer", "default": 0},
            "virtual_channel_id": {"type": "integer", "default": 0},
            "bypass": {"type": "integer", "default": 0},
            "ctrl_cmd": {"type": "integer", "default": 0},
            "frame_seq": {"type": "integer", "default": 0},
            "layout_name": {"type": "string", "default": "ccsds_tc_classic"},
        }, "required": ["data"]},
        handler=_build, category="satellite",
    )
    registry.register(
        name="tc_frame_parse",
        description=(
            "解析 CCSDS TC 帧：头字段 + 数据 + CRC16-CCSDS 校验。"
            "帧长字段与总长不自洽或 CRC 失败如实报错/标记，不产出伪正常帧。"
        ),
        parameters={"type": "object", "properties": {
            "frame": {"type": "string", "description": "帧（hex 字符串）"},
            "layout_name": {"type": "string", "default": "ccsds_tc_classic"},
        }, "required": ["frame"]},
        handler=_parse, category="satellite",
    )
    registry.register(
        name="pcm_psk_pm_modulate",
        description=(
            "PCM/PSK/PM 调制：比特 → NRZ（L/M/S）→ PSK 副载波 → PM 载波，"
            "输出复基带。参数全具名（采样率/码速率/副载波/调制指数），"
            "不自洽（副载波超奈奎斯特、sps<4）报错。纯计算无副作用。"
        ),
        parameters={"type": "object", "properties": {
            "bits": {"type": "array", "items": {"type": "integer"},
                     "description": "0/1 比特列表"},
            "sample_rate_hz": {"type": "number"},
            "bit_rate_hz": {"type": "number"},
            "subcarrier_hz": {"type": "number"},
            "pm_index_rad": {"type": "number", "default": 1.0},
            "nrz": {"type": "string", "default": "nrz_l"},
        }, "required": ["bits", "sample_rate_hz", "bit_rate_hz",
                        "subcarrier_hz"]},
        handler=_mod, category="satellite",
    )
    registry.register(
        name="tc_uplink_transmit",
        description=(
            "TC 上行发射（**写/动设备工具，arm_manual 人工门**）："
            "未显式 arm_manual=true 一律 GATED 拒绝；已武装但无可用发射设备"
            "诚实 NEED_HW；频点缺失 PARAM_ERROR（安全关键参数不猜）。"
            "绝不在未过门或未接设备时谎报发射成功。"
        ),
        parameters={"type": "object", "properties": {
            "arm_manual": {"type": "boolean",
                           "description": "人工确认门：发射必须显式 true"},
            "iq": {"type": "array", "items": {"type": "number"},
                   "description": "复基带（实/虚或复数）"},
            "freq_hz": {"type": "number", "description": "上行频点（Hz）"},
            "device": {"type": "string", "description": "发射设备标识"},
            "gain_db": {"type": "number", "default": 0.0},
        }, "required": ["arm_manual"]},
        handler=_tx, category="satellite",
    )
