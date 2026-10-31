# SPDX-License-Identifier: MIT
"""P0-1/P0-6 协议级解码验证链（样本 → 帧同步 → CRC → 字段抽取 → 断言）。

能力拆解（218 条）的验收脊柱：说"会解码某星"不算数，**样本 → 逐字段断言
→ golden 回归 PASS** 才算数。本模块把这条链做成可编排工具：

  ``verify_sample``  位流 →（布局表同步字）帧同步 →（布局表 CRC）校验
                     →（布局表字段表）逐字段抽取 → 断言列表逐条判定

诚实纪律：
  * 布局里的 honesty 标注如实透传（example_layout 不得冒充 public_standard）；
  * 找不到同步字 / CRC 失败 / 字段越界 → 每步如实报错，不产出半截"结果"；
  * 断言全列输出（PASS/FAIL 逐条），FAIL 即整体 success=False；
  * 换星换布局只换 frame_layouts.json / sat_profiles.json，本模块零硬编码。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from .crc import CRC16_CCSDS, CRC16_CCITT_FALSE, crc_generic
from .sat_profile import load_frame_layout, load_sat_profile

__all__ = [
    "verify_sample", "extract_fields", "sync_and_frame",
    "register_tool_registry",
]

#: 布局表 CRC 名 → 内核参数
_CRC_TABLE = {
    "crc16_ccsds": CRC16_CCSDS,
    "crc16_ccitt_false": CRC16_CCITT_FALSE,
}


def sync_and_frame(bits: Sequence[int], *,
                   layout_name: str,
                   ) -> Dict[str, Any]:
    """位流 → 按布局同步字切出完整帧（载荷区字节）。

    返回 payload 字节 + 命中位置；找不到同步字或长度不足如实报错。
    """
    layout = load_frame_layout(layout_name)
    sync = layout.get("sync") or {}
    if sync.get("type") != "asm":
        raise ValueError(
            f"布局 {layout_name} 的同步类型 {sync.get('type')!r} 暂不受支持"
            "（本链只实现 asm 同步字切割）")
    word = int(str(sync.get("hex", "")), 16)
    if not 0 < word < (1 << 32):
        raise ValueError(f"布局 {layout_name} 同步字非法: {sync.get('hex')!r}")
    pattern = [(word >> (31 - i)) & 1 for i in range(32)]
    b = [int(x) & 1 for x in bits]
    hit = -1
    for i in range(0, max(0, len(b) - 31)):
        if b[i:i + 32] == pattern:
            hit = i
            break
    if hit < 0:
        raise ValueError(
            f"位流中找不到 ASM 0x{word:08X}——同步未建立，不产出帧"
            "（检查位序/调制/编码参数是否与发送端一致）")
    body = b[hit + 32:]
    nbytes = len(body) // 8
    payload = bytes(
        int("".join(str(x) for x in body[i * 8:(i + 1) * 8]), 2)
        for i in range(nbytes))
    return {
        "asm_offset_bits": hit,
        "payload": payload,
        "payload_hex": payload.hex().upper(),
        "trailing_bits": len(body) % 8,
    }


def _decode_field(raw: bytes, spec: Dict[str, Any]) -> Any:
    ftype = str(spec.get("type", "hex"))
    if ftype == "uint8":
        v: Any = raw[0]
    elif ftype == "uint16_be":
        v = int.from_bytes(raw, "big")
    elif ftype == "int16_be":
        v = int.from_bytes(raw, "big", signed=True)
    elif ftype == "uint32_be":
        v = int.from_bytes(raw, "big")
    elif ftype == "ascii":
        v = raw.decode("ascii", "replace")
    elif ftype == "hex":
        v = raw.hex().upper()
    else:
        raise ValueError(f"布局字段 {spec.get('name')!r} 类型 {ftype!r} 未支持")
    if "scale" in spec and isinstance(v, (int, float)):
        v = v * float(spec["scale"])
    return v


def extract_fields(payload: bytes, *,
                   layout_name: str,
                   ) -> Dict[str, Any]:
    """帧载荷 → 按布局字段表逐字段抽取（offset/len/type/scale/unit）。"""
    layout = load_frame_layout(layout_name)
    fields: Dict[str, Any] = {}
    for spec in layout.get("fields", []):
        name = str(spec.get("name"))
        offset = int(spec.get("offset", 0))
        length = int(spec.get("len", 0))
        if name == "data" and length < 0:  # 其余全部
            length = len(payload) - offset
        if offset < 0 or length < 0 or offset + length > len(payload):
            raise ValueError(
                f"字段 {name!r} 越界：offset={offset} len={length}，"
                f"载荷仅 {len(payload)} 字节——布局与帧长不匹配")
        fields[name] = {
            "value": _decode_field(payload[offset:offset + length], spec),
            "unit": spec.get("unit", ""),
        }
    return fields


_OPS = {
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "gt": lambda a, b: a > b,
    "lt": lambda a, b: a < b,
    "ge": lambda a, b: a >= b,
    "le": lambda a, b: a <= b,
    "in_range": lambda a, b: b[0] <= a <= b[1],
}


def _run_assertions(fields: Dict[str, Any],
                    assertions: Optional[Sequence[Dict[str, Any]]],
                    ) -> List[Dict[str, Any]]:
    results = []
    for a in assertions or []:
        name = str(a.get("field"))
        op = str(a.get("op", "eq"))
        want = a.get("value")
        if name not in fields:
            results.append({"field": name, "op": op, "pass": False,
                            "reason": "字段不存在（布局表无此字段）"})
            continue
        got = fields[name]["value"]
        fn = _OPS.get(op)
        if fn is None:
            results.append({"field": name, "op": op, "pass": False,
                            "reason": f"未知断言操作 {op!r}"})
            continue
        try:
            ok = bool(fn(got, want))
        except TypeError:
            ok = False
        results.append({"field": name, "op": op, "want": want, "got": got,
                        "pass": ok})
    return results


def verify_sample(bits: Sequence[int], *,
                  layout_name: str,
                  profile_name: Optional[str] = None,
                  assertions: Optional[Sequence[Dict[str, Any]]] = None,
                  ) -> Dict[str, Any]:
    """协议级解码验证链（一步到结论，每步如实报错）。

    bits：解调后位流；layout_name：帧布局（同步字/CRC/字段表）；
    profile_name：可选卫星剖面（honesty/source 透传进报告）；
    assertions：[{field, op, value}] 逐条判定（缺省只抽取不判定）。

    返回：sync/crc/fields/assertions/honesty 全链结果；任一步失败抛
    ValueError（不产出半截结论）。
    """
    layout = load_frame_layout(layout_name)
    sync = sync_and_frame(bits, layout_name=layout_name)
    payload: bytes = sync["payload"]
    crc_info: Dict[str, Any] = {"name": None, "ok": None}
    crc_spec = layout.get("crc") or {}
    crc_name = str(crc_spec.get("name", ""))
    if crc_name:
        params = _CRC_TABLE.get(crc_name)
        if params is None:
            raise ValueError(
                f"布局 CRC {crc_name!r} 未注册进验证链 CRC 表")
        if crc_spec.get("cover") == "payload":
            body, expect = payload[:-2], payload[-2:]
            if len(expect) < 2:
                raise ValueError("载荷不足 2 字节，无法承载 CRC")
            got = crc_generic(body, params).to_bytes(2, "big")
            crc_info = {"name": crc_name, "cover": "payload",
                        "ok": got == expect,
                        "expected_hex": got.hex().upper(),
                        "frame_hex": expect.hex().upper()}
            if not crc_info["ok"]:
                raise ValueError(
                    f"CRC {crc_name} 校验失败：帧 {expect.hex().upper()} "
                    f"≠ 计算 {got.hex().upper()}——帧内容损坏，不产出字段结论")
            payload = body
    fields = extract_fields(payload, layout_name=layout_name)
    assertions_res = _run_assertions(fields, assertions)
    profile_meta: Dict[str, Any] = {}
    if profile_name:
        prof = load_sat_profile(profile_name)
        profile_meta = {
            "profile": profile_name,
            "display_name": prof.get("display_name"),
            "source": prof.get("source"),
        }
    return {
        "layout": layout_name,
        "honesty": layout.get("honesty"),
        "profile": profile_meta or None,
        "sync": {k: v for k, v in sync.items() if k != "payload"},
        "crc": crc_info,
        "fields": fields,
        "assertions": assertions_res,
        "n_pass": sum(1 for r in assertions_res if r["pass"]),
        "n_fail": sum(1 for r in assertions_res if not r["pass"]),
        "overall_pass": all(r["pass"] for r in assertions_res),
    }


# ═══════════════════════════════════════════════════════════
# 工具层
# ═══════════════════════════════════════════════════════════
def register_tool_registry(registry) -> None:
    """注册协议级解码验证链工具（验收脊柱的可编排形态）。"""
    from .tool_registry import ToolResult, clean_exc
    import json as _json

    def _verify(args):
        try:
            bits = args.get("bits")
            if bits is None:
                raise ValueError("bits 必须提供（解调后 0/1 位流）")
            r = verify_sample(
                [int(b) & 1 for b in bits],
                layout_name=str(args.get("layout_name",
                                         "astu_1_tm_example")),
                profile_name=args.get("profile_name"),
                assertions=args.get("assertions"),
            )
        except Exception as e:  # noqa: BLE001
            return ToolResult(success=False,
                              content=f"验证链失败: {clean_exc(e)}")
        ok = r["overall_pass"] and (r["n_fail"] == 0)
        return ToolResult(
            success=ok,
            content=_json.dumps(
                {"overall_pass": r["overall_pass"], "n_pass": r["n_pass"],
                 "n_fail": r["n_fail"],
                 "fields": {k: v["value"] for k, v in r["fields"].items()}},
                ensure_ascii=False, default=str),
            data=r)

    registry.register(
        name="verify_decode_chain",
        description=(
            "协议级解码验证链：位流 → 布局表同步字切帧 → 布局表 CRC 校验 → "
            "逐字段抽取 → 断言列表逐条判定（验收脊柱）。任一步失败如实报错"
            "不产出半截结论；布局 honesty 标注透传。换星换布局只换 "
            "frame_layouts.json / sat_profiles.json，本工具零硬编码。"
            "前置条件：bits（解调后位流）、layout_name。"
            "副作用：纯计算。"
        ),
        parameters={"type": "object", "properties": {
            "bits": {"type": "array", "items": {"type": "integer"},
                     "description": "解调后 0/1 位流"},
            "layout_name": {"type": "string",
                            "default": "astu_1_tm_example"},
            "profile_name": {"type": "string",
                             "description": "卫星剖面（honesty 透传）"},
            "assertions": {"type": "array",
                           "description": "[{field,op,value}] 断言列表"},
        }, "required": ["bits"]},
        handler=_verify, category="satellite",
    )
