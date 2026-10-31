# SPDX-License-Identifier: MIT
"""P1-2 帧字段布局配置注入框架原子工具。

能力拆解（218 条）P1-2：帧布局/字段提取/CRC 从代码里抽出来变成
可注入配置（ASTU 教程只给帧样例未给通用布局机制）。

  frame_layout_spec_load(text) —— 布局 spec 加载与结构校验。
    输入为 JSON 文本（标准库保证可用）；YAML 文本在 PyYAML 可用时
    亦接受（本环境未安装 PyYAML——如实降级，不内置玩具解析器）。
  field_extract(frame, spec)   —— 按 spec 从原始帧字节流做 MSB-first
    位提取（uint/int/ascii/bytes 四类 + scale/offset/enum 解释），
    越界/重叠/未知类型显式报错，不静默丢字段。
  crc_compute / crc_check      —— Rocksoft 参数化 CRC（任意 ≤32 位宽，
    poly/init/refin/refout/xorout 全参数），check 支持 CRC 位于帧尾
    或显式偏移，计算值与帧内值不一致如实上报。

spec 结构（dict / JSON）：
  {
    "name": "示例布局",
    "fields": [
      {"name": "version", "offset_bits": 0, "length_bits": 3,
       "type": "uint"},
      {"name": "snr", "offset_bits": 8, "length_bits": 8,
       "type": "uint", "scale": 0.5, "units": "dB"},
      {"name": "mode", "offset_bits": 16, "length_bits": 2,
       "type": "uint", "enum": {"0": "idle", "1": "tx"}}
    ]
  }
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence, Union

__all__ = [
    "frame_layout_spec_load",
    "field_extract",
    "crc_compute",
    "crc_check",
    "register_tool_registry",
]

try:
    import yaml as _yaml
    _HAS_YAML = True
except Exception:  # noqa: BLE001 - PyYAML 为可选依赖
    _HAS_YAML = False

_FIELD_TYPES = ("uint", "int", "ascii", "bytes")


def frame_layout_spec_load(text_or_dict: Union[str, Dict[str, Any]],
                           ) -> Dict[str, Any]:
    """帧布局 spec 加载 + 结构校验（配置注入入口）。

    接受 dict（已解析）或 JSON 文本；YAML 文本仅在 PyYAML 可用时接受，
    否则显式报错（诚实降级，不内置不完整 YAML 解析器）。
    """
    if isinstance(text_or_dict, dict):
        spec = dict(text_or_dict)
    elif isinstance(text_or_dict, str):
        body = text_or_dict.strip()
        if not body:
            raise ValueError("spec 文本为空")
        try:
            spec = json.loads(body)
        except json.JSONDecodeError:
            if _HAS_YAML:
                spec = _yaml.safe_load(body)
            else:
                raise ValueError(
                    "spec 非 JSON 且本环境无 PyYAML（YAML 支持为可选依赖）")
    else:
        raise ValueError(f"spec 须为 dict 或文本，收到 {type(text_or_dict)}")

    if not isinstance(spec, dict):
        raise ValueError("spec 顶层须为对象")
    name = spec.get("name")
    if not name or not isinstance(name, str):
        raise ValueError("spec.name 缺失或非字符串")
    fields = spec.get("fields")
    if not isinstance(fields, list) or not fields:
        raise ValueError("spec.fields 须为非空列表")
    seen: set = set()
    for i, f in enumerate(fields):
        if not isinstance(f, dict):
            raise ValueError(f"fields[{i}] 非对象")
        fname = f.get("name")
        if not fname or not isinstance(fname, str):
            raise ValueError(f"fields[{i}].name 缺失或非字符串")
        if fname in seen:
            raise ValueError(f"字段名重复: {fname}")
        seen.add(fname)
        off = f.get("offset_bits")
        ln = f.get("length_bits")
        if not isinstance(off, int) or off < 0:
            raise ValueError(f"{fname}.offset_bits 须为非负整数")
        if not isinstance(ln, int) or ln < 1:
            raise ValueError(f"{fname}.length_bits 须为正整数")
        ftype = f.get("type", "uint")
        if ftype not in _FIELD_TYPES:
            raise ValueError(
                f"{fname}.type {ftype!r} 未知（可用 {_FIELD_TYPES}）")
        if ftype in ("ascii", "bytes") and ln % 8 != 0:
            raise ValueError(f"{fname}: ascii/bytes 字段须按整字节对齐")
        if "scale" in f and not isinstance(f["scale"], (int, float)):
            raise ValueError(f"{fname}.scale 须为数值")
        if "offset" in f and not isinstance(f["offset"], (int, float)):
            raise ValueError(f"{fname}.offset 须为数值")
        if "enum" in f and not isinstance(f["enum"], dict):
            raise ValueError(f"{fname}.enum 须为映射")
    return spec


def _read_bits(frame: bytes, offset_bits: int, length_bits: int) -> int:
    """MSB-first 位提取：bit 0 = 首字节最高位。"""
    total = len(frame) * 8
    if offset_bits + length_bits > total:
        raise ValueError(
            f"字段越界：bit [{offset_bits}, {offset_bits + length_bits}) "
            f"超出帧长 {total} bit")
    value = 0
    for b in range(length_bits):
        pos = offset_bits + b
        byte = frame[pos // 8]
        bit = (byte >> (7 - pos % 8)) & 1
        value = (value << 1) | bit
    return value


def field_extract(frame: Union[bytes, bytearray, Sequence[int]],
                  spec: Union[str, Dict[str, Any]],
                  ) -> Dict[str, Any]:
    """按布局 spec 提取帧字段。

    返回 {字段名: 解释值}；enum 命中时值为枚举字符串（原始值放
    `<name>_raw`），未命中保留原始整数并附 `_unmapped: True`。
    """
    if isinstance(spec, str):
        spec = frame_layout_spec_load(spec)
    else:
        spec = frame_layout_spec_load(spec)
    data = bytes(frame)
    if not data:
        raise ValueError("帧为空")
    out: Dict[str, Any] = {}
    for f in spec["fields"]:
        name = f["name"]
        raw = _read_bits(data, f["offset_bits"], f["length_bits"])
        ftype = f.get("type", "uint")
        if ftype == "uint":
            value: Any = raw
        elif ftype == "int":
            bits = f["length_bits"]
            value = raw - (1 << bits) if raw & (1 << (bits - 1)) else raw
        elif ftype == "ascii":
            nbytes = f["length_bits"] // 8
            bs = raw.to_bytes(nbytes, "big")
            value = bs.decode("ascii", errors="replace")
        else:  # bytes
            nbytes = f["length_bits"] // 8
            value = raw.to_bytes(nbytes, "big").hex()
        if "scale" in f or "offset" in f:
            value = value * f.get("scale", 1) + f.get("offset", 0)
            out[name] = value
            continue
        enum = f.get("enum")
        if enum is not None:
            key = str(raw)
            out[name + "_raw"] = raw
            if key in enum:
                out[name] = enum[key]
            else:
                out[name] = raw
                out[name + "_unmapped"] = True
            continue
        out[name] = value
    return out


# ─────────────────────────────────────────────────────────────
# Rocksoft 参数化 CRC
# ─────────────────────────────────────────────────────────────
def _reflect(value: int, width: int) -> int:
    """位反转（width 位内）。"""
    out = 0
    for _ in range(width):
        out = (out << 1) | (value & 1)
        value >>= 1
    return out


def crc_compute(data: Union[bytes, bytearray, Sequence[int]],
                width: int,
                poly: int,
                init: int = 0,
                refin: bool = False,
                refout: Optional[bool] = None,
                xorout: int = 0,
                ) -> int:
    """Rocksoft 模型参数化 CRC（按位实现，宽度 ≤32）。

    width  : CRC 位宽（8/16/32 常用，≤32）；
    poly   : 生成多项式，**正规表示**（MSB 对齐，如 CRC-32 的
             0x04C11DB7；反射型算法内部自行反射）；
    init   : 寄存器初值；
    refin  : 输入字节按位反射（LSB-first 数据流）；
    refout : 输出反射（None = 跟随 refin，即常规约定）；
    xorout : 结果异或值。

    校验锚点（"123456789" 的 ASCII 字节）：
      CRC-16/CCITT-FALSE (16, 0x1021, FFFF)        → 0x29B1
      CRC-16/UMTS        (16, 0x8005, 0000)        → 0xFEE8
      CRC-32/ISO-HDLC    (32, 0x04C11DB7, FFFFFFFF,
                          refin, xorout FFFFFFFF)  → 0xCBF43926
    """
    if not (1 <= width <= 32):
        raise ValueError("CRC 宽度须在 1..32")
    mask = (1 << width) - 1
    poly &= mask
    init &= mask
    xorout &= mask
    if refout is None:
        refout = refin
    data_b = bytes(data)
    if refin:
        rpoly = _reflect(poly, width)
        reg = init
        for b in data_b:
            reg ^= b
            for _ in range(8):
                reg = (reg >> 1) ^ rpoly if (reg & 1) else (reg >> 1)
                reg &= mask
    else:
        topbit = 1 << (width - 1)
        reg = init
        for b in data_b:
            reg ^= b << (width - 8)
            for _ in range(8):
                reg = ((reg << 1) ^ poly) if (reg & topbit) else (reg << 1)
                reg &= mask
    if refout != refin:
        reg = _reflect(reg, width)
    return (reg ^ xorout) & mask


def crc_check(frame: Union[bytes, bytearray, Sequence[int]],
              width: int,
              poly: int,
              init: int = 0,
              refin: bool = False,
              refout: Optional[bool] = None,
              xorout: int = 0,
              crc_offset_bytes: Optional[int] = None,
              ) -> Dict[str, Any]:
    """整帧 CRC 校验：计算值与帧内值比对，不一致如实上报。

    crc_offset_bytes : CRC 字段起始字节（默认帧尾 width/8 字节）。
                       显式给出时其后只取 width/8 字节作 CRC，其余尾部
                       字节（如填充）不计入。
    返回 {ok, computed, expected, data_bytes, crc_bytes}。
    """
    data_b = bytes(frame)
    if width % 8 != 0:
        raise ValueError(
            f"width={width} 非整字节：帧内 CRC 定位只支持整字节宽度")
    nbytes = width // 8
    if crc_offset_bytes is not None:
        if not (0 <= crc_offset_bytes < len(data_b)):
            raise ValueError("crc_offset_bytes 越界")
        if crc_offset_bytes + nbytes > len(data_b):
            raise ValueError("crc_offset_bytes 后不足 width/8 字节")
        payload = data_b[:crc_offset_bytes]
        tail = data_b[crc_offset_bytes: crc_offset_bytes + nbytes]
    else:
        if len(data_b) < nbytes:
            raise ValueError(f"帧长 {len(data_b)} < CRC 宽 {nbytes} 字节")
        payload = data_b[:-nbytes]
        tail = data_b[-nbytes:]
    computed = crc_compute(payload, width, poly, init, refin, refout, xorout)
    expected = int.from_bytes(tail, "big")
    return {
        "ok": bool(computed == expected),
        "computed": computed,
        "expected": expected,
        "data_bytes": len(payload),
        "crc_bytes": len(tail),
    }


# ─────────────────────────────────────────────────────────────
# 工具注册
# ─────────────────────────────────────────────────────────────
def register_tool_registry(registry) -> None:
    """注册 P1-2 帧布局配置注入积木。"""
    from .tool_registry import ToolResult, clean_exc
    import json as _json

    def _load(args):
        try:
            res = frame_layout_spec_load(args.get("spec", ""))
        except Exception as e:  # noqa: BLE001
            return ToolResult(success=False,
                              content=f"布局加载失败: {clean_exc(e)}")
        return ToolResult(success=True,
                          content=_json.dumps(res, ensure_ascii=False),
                          data=res)

    def _extract(args):
        try:
            frame = args.get("frame", b"")
            if isinstance(frame, str):
                frame = bytes.fromhex(frame)
            res = field_extract(frame, args.get("spec", {}))
        except Exception as e:  # noqa: BLE001
            return ToolResult(success=False,
                              content=f"字段提取失败: {clean_exc(e)}")
        return ToolResult(success=True,
                          content=_json.dumps(res, ensure_ascii=False),
                          data=res)

    def _crc(args):
        try:
            frame = args.get("frame", b"")
            if isinstance(frame, str):
                frame = bytes.fromhex(frame)
            res = crc_check(
                frame,
                width=int(args.get("width", 16)),
                poly=int(args.get("poly", 0x1021)),
                init=int(args.get("init", 0)),
                refin=bool(args.get("refin", False)),
                xorout=int(args.get("xorout", 0)),
                crc_offset_bytes=(
                    int(args["crc_offset_bytes"])
                    if args.get("crc_offset_bytes") is not None else None),
            )
        except Exception as e:  # noqa: BLE001
            return ToolResult(success=False,
                              content=f"CRC 校验失败: {clean_exc(e)}")
        ok = bool(res["ok"])
        return ToolResult(success=ok,
                          content=_json.dumps(res, ensure_ascii=False),
                          data=res)

    registry.register(
        name="frame_layout_spec_load",
        description=(
            "帧布局 spec 加载与结构校验（JSON 文本/dict；YAML 需 PyYAML，"
            "缺失时显式报错）。字段名唯一/位偏移非负/类型白名单硬校验。"),
        parameters={
            "type": "object",
            "properties": {
                "spec": {"type": ["string", "object"],
                         "description": "JSON 文本或已解析 dict"},
            },
            "required": ["spec"],
        },
        handler=_load,
        category="protocol")

    registry.register(
        name="field_extract",
        description=(
            "按布局 spec 从原始帧 MSB-first 位提取（uint/int/ascii/bytes"
            "+scale/offset/enum），越界显式报错，enum 未命中如实保留"
            "原始值。"),
        parameters={
            "type": "object",
            "properties": {
                "frame": {"type": ["string", "array"],
                          "description": "hex 字符串或字节数组"},
                "spec": {"type": ["string", "object"]},
            },
            "required": ["frame", "spec"],
        },
        handler=_extract,
        category="protocol")

    registry.register(
        name="crc_check",
        description=(
            "Rocksoft 参数化 CRC 整帧校验（poly/init/refin/xorout 可配，"
            "任意 ≤32 位宽），计算值与帧内值不一致 ok=False 如实上报。"),
        parameters={
            "type": "object",
            "properties": {
                "frame": {"type": ["string", "array"],
                          "description": "hex 字符串或字节数组"},
                "width": {"type": "integer", "default": 16},
                "poly": {"type": "integer", "default": 0x1021},
                "init": {"type": "integer", "default": 0},
                "refin": {"type": "boolean", "default": False},
                "xorout": {"type": "integer", "default": 0},
                "crc_offset_bytes": {"type": ["integer", "null"],
                                     "default": None},
            },
            "required": ["frame"],
        },
        handler=_crc,
        category="protocol")
