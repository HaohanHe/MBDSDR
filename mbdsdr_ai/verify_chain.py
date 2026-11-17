# SPDX-License-Identifier: MIT
"""P0-1/P0-6 协议级解码验证链（样本 → 帧同步 → CRC → 字段抽取 → 断言）。

真值钉扎原则：说"会解码某星"不算数，**样本 → 逐字段断言
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

import json
import math
from typing import Any, Dict, List, Optional, Sequence

from .crc import CRC16_CCSDS, CRC16_CCITT_FALSE, crc_generic
from .sat_profile import load_frame_layout, load_sat_profile

__all__ = [
    "verify_sample", "extract_fields", "sync_and_frame",
    "register_tool_registry", "run_p0_anchors",
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


def _decode_bits(payload: bytes, bit_offset: int, bit_len: int) -> int:
    """按 MSB-first 从载荷中取一段无符号位域（bit_offset 以帧首为 0）。"""
    total = len(payload) * 8
    if bit_offset < 0 or bit_len <= 0 or bit_offset + bit_len > total:
        raise ValueError(
            f"位字段越界：bit_offset={bit_offset} bit_len={bit_len}，"
            f"载荷仅 {len(payload)} 字节（{total} bit）——布局与帧长不匹配")
    v = 0
    for i in range(bit_offset, bit_offset + bit_len):
        v = (v << 1) | ((payload[i >> 3] >> (7 - (i & 7))) & 1)
    return v


def extract_fields(payload: bytes, *,
                   layout_name: str,
                   ) -> Dict[str, Any]:
    """帧载荷 → 按布局字段表逐字段抽取（offset/len/type/scale/unit）。

    字段若声明 ``bit_len``，则走位级路径：``bit_offset``（以帧首为 0 的
    绝对位偏移，MSB-first）+ ``bit_len`` 取无符号整数，``type`` 须为
    ``uint``；``bit_offset + bit_len`` 超出载荷位数即报错，**绝不截断或
    补零**。未声明 ``bit_len`` 的旧字段仍走原字节路径，行为不变。
    """
    layout = load_frame_layout(layout_name)
    fields: Dict[str, Any] = {}
    for spec in layout.get("fields", []):
        name = str(spec.get("name"))
        if spec.get("bit_len") is not None:
            ftype = str(spec.get("type", "uint"))
            if ftype != "uint":
                raise ValueError(
                    f"位字段 {name!r} 类型 {ftype!r} 未支持（位级只支持 uint）")
            v: Any = _decode_bits(payload, int(spec.get("bit_offset", 0)),
                                  int(spec["bit_len"]))
            if "scale" in spec:
                v = v * float(spec["scale"])
            fields[name] = {"value": v, "unit": spec.get("unit", "")}
            continue
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
    """注册协议级解码验证链工具（真值钉扎链的可编排形态）。"""
    from .tool_results import ToolResult, clean_exc
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
            "逐字段抽取 → 断言列表逐条判定（真值钉扎）。任一步失败如实报错"
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


# ═══════════════════════════════════════════════════════════
# P0 工具锚点（Task 6.4：Task 2/3/4/5 各挂一条进解码链回归网）
# ═══════════════════════════════════════════════════════════
def _golden_path(name: str):
    """仓库内 golden 目录（缺失即锚点失败，不做网络/外部回退）。"""
    from pathlib import Path
    return Path(__file__).resolve().parents[1] / "tests" / "golden" / name


def _anchor_dvbs2_bch() -> str:
    """P0-2：BCH 编码 → 注入 t 个错 → 解码纠回，码字逐位一致。

    用短帧 1/2（k≈6.8k）控制验收耗时；期望值来自编码前的消息本身，
    不引用解码器的输出当期望（无自环）。
    """
    from .dvbs2_bch import bch_parameters, decode_bch, encode_bch

    frame, rate = "short", "1/2"
    p = bch_parameters(frame, rate)
    k, n, t = int(p["k"]), int(p["n"]), int(p["t"])
    data = [((i * 7 + (i >> 3)) & 1) for i in range(k)]
    codeword = encode_bch(data, frame, rate)
    if len(codeword) != n:
        raise AssertionError(f"码长 {len(codeword)} != N_bch {n}")
    errors = max(1, min(t, 3))
    positions = (0, n // 3, n - 1)[:errors]
    received = codeword.copy()
    for pos in positions:
        received[pos] ^= 1
    r = decode_bch(received, frame, rate)
    if not r["success"]:
        raise AssertionError(f"BCH 解码失败: {r['reason']}")
    if int(r["n_errors"]) != len(positions):
        raise AssertionError(f"纠错数 {r['n_errors']} != 注入 {len(positions)}")
    if [int(x) for x in r["codeword"]] != [int(x) for x in codeword]:
        raise AssertionError("纠错后码字与编码结果不一致")
    return (f"{frame}/{rate} K={k} N={n} t={t}：注入 {len(positions)} 错 → "
            f"全部纠回")


def _anchor_gps_pvt() -> str:
    """P0-3：golden LNAV 位流 → 星历 → 4 星合成伪距 → PVT 收敛。

    真值是**已知的用户坐标与钟差**（合成伪距按解析几何算出），不是
    pvt_solve 自己的输出；位置 1 m、钟差 1 cm 容差。
    """
    from .gps_nav import (ephemeris_sat_pos, nav_bits_to_subframes,
                          pvt_solve, subframes_to_ephemeris)

    golden = json.loads(_golden_path("gps_lnav_golden.json")
                        .read_text(encoding="utf-8"))
    eph = subframes_to_ephemeris(nav_bits_to_subframes(golden["nav_bits"]))
    want_sqrt_a = float(golden["expected_decoded"]["sqrt_a"])
    if abs(float(eph["sqrt_a"]) - want_sqrt_a) > 1e-6 * max(1.0, want_sqrt_a):
        raise AssertionError(
            f"sqrt_a 解码值 {eph['sqrt_a']} != golden {want_sqrt_a}")

    user = (6378137.0 * 0.6, 6378137.0 * 0.3, 6378137.0 * 0.2)
    bias_true = 1.23e-4
    obs = []
    for dt in (0.0, 1500.0, 3000.0, 4500.0):
        pos = ephemeris_sat_pos(eph, eph["toe_s"] + dt)["pos_ecef_m"]
        rng = math.sqrt(sum((pos[i] - user[i]) ** 2 for i in range(3)))
        obs.append({"pr_m": rng + bias_true, "sat_pos_ecef_m": pos})
    sol = pvt_solve(obs)
    if not sol["converged"]:
        raise AssertionError("PVT 未收敛")
    pos_err = math.sqrt(sum((sol["pos_ecef_m"][i] - user[i]) ** 2
                            for i in range(3)))
    if pos_err >= 1.0:
        raise AssertionError(f"位置误差 {pos_err:.3f} m 超 1 m 容差")
    if abs(float(sol["clock_bias_m"]) - bias_true) >= 0.01:
        raise AssertionError("钟差误差超 1 cm 容差")
    return (f"golden LNAV → {len(obs)} 星 PVT：位置误差 {pos_err:.3f} m、"
            f"钟差误差 {abs(float(sol['clock_bias_m']) - bias_true) * 1e3:.3f} mm")


def _anchor_df_bearing() -> str:
    """P0-4：3×3 半波长方阵、解析平面波 37° → 相关测向误差 < 1°、无歧义。"""
    import numpy as np

    from .df_position import array_correlator_df
    from .interferometry import C_M_S

    fs, fc = 1e6, 435.4e6
    spacing = (C_M_S / fc) / 2.0
    positions = [[i * spacing, j * spacing] for i in range(3) for j in range(3)]
    theta = math.radians(37.0)
    direction = np.array([math.sin(theta), math.cos(theta)])
    times = np.arange(256) / fs
    carrier = np.exp(2j * math.pi * 50e3 * times)
    snapshots = np.asarray([
        np.exp(-2j * math.pi * fc
               * float(np.dot(np.asarray(p, dtype=float), direction)) / C_M_S)
        * carrier for p in positions])
    res = array_correlator_df(snapshots, fs, fc, positions)
    if res["ambiguity"]:
        raise AssertionError(f"单源方阵报歧义: {res['peaks'][:3]}")
    got = float(res["primary_bearing_deg"])
    err = abs((got - 37.0 + 180.0) % 360.0 - 180.0)
    if err > 1.0:
        raise AssertionError(f"方位 {got:.2f}° 偏离真值 37° 达 {err:.2f}°")
    return f"3×3 阵 37° → {got:.2f}°（误差 {err:.2f}°）"


def _anchor_tc_frame() -> str:
    """P0-5：CCSDS 230.1-G-3 Annex F Example 10 逐字节再生 + CRC。"""
    from .tc_uplink import tc_build_frame, tc_frame_parse

    golden = json.loads(_golden_path("ccsds_tc_frame_golden.json")
                        .read_text(encoding="utf-8"))
    inp = golden["input"]
    built = tc_build_frame(
        bytes.fromhex(inp["data_hex"]),
        spacecraft_id=inp["spacecraft_id"],
        virtual_channel_id=inp["virtual_channel_id"],
        bypass=inp["bypass"], ctrl_cmd=inp["ctrl_cmd"],
        frame_seq=inp["frame_seq"])
    if built["frame_hex"] != golden["frame_hex"]:
        raise AssertionError(
            f"再生帧 {built['frame_hex']} != golden {golden['frame_hex']}")
    parsed = tc_frame_parse(bytes.fromhex(golden["frame_hex"]))
    if not parsed["crc_ok"]:
        raise AssertionError("官方 golden 帧 CRC 校验失败")
    if parsed["data_hex"] != inp["data_hex"]:
        raise AssertionError("数据域回环不一致")
    return f"Annex F Example 10 → {built['frame_hex']}（CRC OK）"


def run_p0_anchors() -> Dict[str, Any]:
    """跑 Task 2/3/4/5 的最小锚点集（Task 6.4 的验收入口）。

    返回 ``{"ok": bool, "items": [{task, tool, ok, detail}]}``。
    锚点内部抛错被收集成 ``ok=False`` 的数据项（验收报告要看到是哪条、为什么，
    而不是整个脚本崩掉）。任何一条失败，解码链回归网就该红。
    """
    anchors = (
        ("P0-2", "dvbs2_bch", _anchor_dvbs2_bch),
        ("P0-3", "gps_nav", _anchor_gps_pvt),
        ("P0-4", "df_position", _anchor_df_bearing),
        ("P0-5", "tc_uplink", _anchor_tc_frame),
    )
    items = []
    for task, tool, fn in anchors:
        try:
            items.append({"task": task, "tool": tool, "ok": True,
                          "detail": fn()})
        except Exception as exc:  # noqa: BLE001 - 锚点失败是数据，不是崩溃
            items.append({"task": task, "tool": tool, "ok": False,
                          "detail": f"{type(exc).__name__}: {exc}"})
    return {"ok": all(item["ok"] for item in items), "items": items}
