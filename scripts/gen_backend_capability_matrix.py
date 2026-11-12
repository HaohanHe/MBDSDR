#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按后端类生成「设备能力对照表」（AGC / 增益 / PPM / Offset / 采样率区间 / 增益区间）。

为什么要有这张表
----------------
能力判据只存在于代码里（``SDRBackend.supports_gain`` / ``supports_agc`` /
``supports_ppm`` / ``supports_offset_tuning``），但用户在帮助页/文档里看到的
是「空口描述」。这份文档把**每个真实注册到的后端类**的六项真值列出来，且

  * 判据不手抄：每格都直接调用对应实例的 ``supports_*``；
  * 区间不手抄：取实例 ``device.sample_rate_range`` / ``device.max_gain``
    与 ``device.capability_status``（declared / probed / unknown）；
  * 后端清单不手抄：扫描 ``mbdsdr_ai.sdr_backend`` / ``rtltcp_client`` /
    ``spyserver_client`` 里所有 ``SDRBackend`` 子类。新增后端类却没登记
    构造方式时，本脚本直接报错退出——宁可红，也不静默漏一行。

探测上下文
----------
全部用**未连接的离线实例**探测（构造不打开硬件、不联网）。因此：
  * ``RTLSDRBackend.supports_offset_tuning()`` 依赖已连接的驱动句柄，
    未连接时恒为 False（表里如实写 False，并在备注里说明「需连接后按驱动判定」）；
  * SoapySDR 的增益区间在连接后才由 ``getGainRange`` 探测，未连接时
    ``max_gain`` 为声明的 0.0。

用法::

    python scripts/gen_backend_capability_matrix.py --write   # 重写文档
    python scripts/gen_backend_capability_matrix.py --check   # 快照→--write→比对→还原

``--check`` 的口径与 ``scripts/check_generated_artifacts.py`` 的 snapshot 形态
一致：先快照原字节，再重写，比对后**无条件还原**；不一致时逐项点名
「后端类 + 能力项」，退出码 1。

退出码：0 一致 / 1 文档与代码不一致 / 2 取数失败（导入或探测异常）。
"""
from __future__ import annotations

import argparse
import difflib
import importlib
import inspect
import os
import sys
import traceback
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: 离线：构造后端不应该触碰网络/硬件，这里再钉一次，防止未来某后端构造期探测。
os.environ.setdefault("MBDSDR_OFFLINE", "1")

DOC_PATH = ROOT / "docs" / "backend-capability-matrix.md"

#: 六项的顺序与列名（同时是比对时的项名，漂移点名就用它）。
ITEMS: Tuple[str, ...] = ("AGC", "增益", "PPM", "Offset", "采样率区间", "增益区间")

#: 后端类 → (构造方式, 一句话说明)。顺序即文档行序。
#: 说明只写「这个类代表什么硬件」，不写能力判据（判据只在 supports_* 里）。
FACTORIES: Dict[str, Tuple[str, "callable"]] = {}


def _register_factories() -> None:
    """延迟导入并登记构造方式（导入失败必须可见，不能当空表）。"""
    from mbdsdr_ai.sdr_backend import (
        AISDRMiniBackend, FileIQBackend, HackRFBackend, NetworkIQBackend,
        PlutoSDRBackend, RTLSDRBackend, SoapySDRBackend, USRPBackend,
    )
    from mbdsdr_ai.rtltcp_client import RtlTcpBackend
    from mbdsdr_ai.spyserver_client import SpyServerBackend

    FACTORIES.update({
        "RTLSDRBackend": (
            "RTL-SDR 原生 USB（rtl_tcp 亦复用本类的 host/port 模式）",
            lambda: RTLSDRBackend(device_index=0),
        ),
        "RtlTcpBackend": (
            "rtl_tcp 网络源（自研 RtlTcpClient，协议 0x05 系）",
            lambda: RtlTcpBackend("127.0.0.1", 1234),
        ),
        "SpyServerBackend": (
            "SpyServer 网络源（Airspy 系服务端）",
            lambda: SpyServerBackend("127.0.0.1", 7355),
        ),
        "PlutoSDRBackend": (
            "PlutoSDR / ADALM-PLUTO（libiio 或 SoapyPlutoSDR）",
            lambda: PlutoSDRBackend(),
        ),
        "HackRFBackend": (
            "HackRF One（libhackrf / 原生）",
            lambda: HackRFBackend(device_index=0),
        ),
        "USRPBackend": (
            "USRP（UHD）",
            lambda: USRPBackend(),
        ),
        "SoapySDRBackend": (
            "SoapySDR 通用后端（未专用化的驱动走这里）",
            lambda: SoapySDRBackend(device_args={}),
        ),
        "AISDRMiniBackend": (
            "自研 ai-sdr Mini（SI4732 + ESP32，经 MCP/WebSocket）",
            lambda: AISDRMiniBackend(),
        ),
        "FileIQBackend": (
            "IQ 文件离线回放（用户显式选择的录制文件）",
            lambda: FileIQBackend("/nonexistent/capability-probe.cu8"),
        ),
        "NetworkIQBackend": (
            "裸 IQ 网络流（sdrpp network_sink 兼容）",
            lambda: NetworkIQBackend(
                protocol="tcp", host="127.0.0.1", port=9,
                sample_rate_hz=2_400_000.0, center_freq_hz=100_000_000.0),
        ),
    })


#: 名字像后端、但**不是** SDRBackend 子类的类：不参与 supports_* 判据，
#: 单独列在 §3 并写明原因（不假装它们有这六项能力）。
NON_SUBCLASS = (
    ("mbdsdr_ai.bladerf_params", "BladeRFBackend",
     "不是 SDRBackend 子类，没有 supports_*；增益按 bladerf_set_gain 固定下发，"
     "AGC / PPM / Offset 均未实现"),
    ("mbdsdr_ai.limesuite_params", "LimeSDRBackend",
     "不是 SDRBackend 子类，没有 supports_*；增益按 LMS_SetGaindB 固定下发，"
     "AGC / PPM / Offset 均未实现"),
)

SCAN_MODULES = (
    "mbdsdr_ai.sdr_backend",
    "mbdsdr_ai.rtltcp_client",
    "mbdsdr_ai.spyserver_client",
)


def _backend_classes() -> List[type]:
    """扫描三个模块，返回其中定义的全部 SDRBackend 具体子类（按名字排序）。"""
    from mbdsdr_ai.sdr_backend import SDRBackend

    found: Dict[str, type] = {}
    for mod_name in SCAN_MODULES:
        mod = importlib.import_module(mod_name)
        for obj in vars(mod).values():
            if (inspect.isclass(obj) and obj is not SDRBackend
                    and issubclass(obj, SDRBackend)
                    and obj.__module__ == mod.__name__):
                found[obj.__name__] = obj
    return [found[name] for name in sorted(found)]


def _human_hz(value: float) -> str:
    if value >= 1e9:
        return f"{value / 1e9:g} GHz"
    if value >= 1e6:
        return f"{value / 1e6:g} MHz"
    if value >= 1e3:
        return f"{value / 1e3:g} kHz"
    return f"{value:g} Hz"


def _range_text(rng: Sequence[float], status: str) -> str:
    lo, hi = float(rng[0]), float(rng[1])
    kind = {"declared": "声明", "probed": "探测", "unknown": "未声明"}.get(
        status, status or "未标注")
    return f"{_human_hz(lo)} – {_human_hz(hi)}（{kind}）"


def _probe(cls: type, note: str, factory) -> Dict[str, str]:
    """构造一个未连接实例，调用 supports_* 取六项真值。"""
    inst = factory()
    device = inst.device
    sr_status = getattr(device, "capability_status", {}).get(
        "sample_rate_range", "declared")
    gain_status = getattr(device, "capability_status", {}).get(
        "gain_range", "declared")
    gain_max = float(device.max_gain)
    if gain_max <= 0.0 and inst.supports_gain():
        # 例如 SoapySDR：增益区间在连接后由 getGainRange 探测，未连接时声明为 0。
        gain_text = "未连接：连接后由驱动探测（当前声明 0 dB）"
    elif gain_max <= 0.0:
        gain_text = "0 – 0 dB（无增益控制）"
    else:
        gain_text = f"0 – {gain_max:g} dB（{'未声明' if gain_status == 'unknown' else '声明'}）"
    return {
        "后端类": f"`{cls.__name__}`",
        "说明": note,
        "AGC": "是" if inst.supports_agc() else "否",
        "增益": "是" if inst.supports_gain() else "否",
        "PPM": "是" if inst.supports_ppm() else "否",
        "Offset": "是" if inst.supports_offset_tuning() else "否",
        "采样率区间": _range_text(device.sample_rate_range, sr_status),
        "增益区间": gain_text,
    }


def render() -> Tuple[str, List[Dict[str, str]]]:
    """返回 (文档全文, 行数据)。行数据同时用于 --check 的逐项点名。"""
    _register_factories()
    classes = _backend_classes()
    names = [c.__name__ for c in classes]
    missing = [n for n in names if n not in FACTORIES]
    stale = [n for n in FACTORIES if n not in names]
    if missing or stale:
        raise RuntimeError(
            "后端清单与构造登记不一致：未登记 "
            f"{missing or '无'}；已登记但不再是子类 {stale or '无'}。"
            "请补/删 FACTORIES 后重跑。")

    rows = [_probe(c, FACTORIES[c.__name__][0], FACTORIES[c.__name__][1])
            for c in classes]

    counts = {item: sum(1 for r in rows if r[item] == "是")
              for item in ITEMS[:4]}
    lines: List[str] = [
        "# SDR 后端能力对照表（生成物，勿手改）",
        "",
        "> 生成：`python scripts/gen_backend_capability_matrix.py --write`  ",
        "> 对账：`python scripts/gen_backend_capability_matrix.py --check`"
        "（发版前由 `scripts/release_check.py` 的 gen_reconcile 段自动跑）",
        "",
        "六项真值**直接调用各后端实例的 `supports_gain` / `supports_agc` / "
        "`supports_ppm` / `supports_offset_tuning`**，本页不另写一份判据；"
        "区间取实例的 `device.sample_rate_range` / `device.max_gain` 与"
        "`capability_status`（声明=declared / 探测=probed / 未声明=unknown）。",
        "",
        f"探测上下文：**未连接的离线实例**（构造不打开硬件、不联网），"
        f"共 {len(rows)} 个后端类；其中 AGC {counts['AGC']} 个、增益 "
        f"{counts['增益']} 个、PPM {counts['PPM']} 个、Offset "
        f"{counts['Offset']} 个真支持。",
        "",
        "## 1. 能力矩阵",
        "",
        "| 后端类 | 说明 | " + " | ".join(ITEMS) + " |",
        "|---|---|" + "---|" * len(ITEMS),
    ]
    for r in rows:
        lines.append("| " + " | ".join(
            [r["后端类"], r["说明"]] + [r[i] for i in ITEMS]) + " |")
    lines += [
        "",
        "## 2. 判据只有一个地方",
        "",
        "**本页不复制判据**。AGC / 增益 / PPM / Offset 的判定分别是 "
        "`mbdsdr_ai/sdr_backend.py` 里的 `SDRBackend.supports_agc()` / "
        "`supports_gain()` / `supports_ppm()` / `supports_offset_tuning()`，"
        "表里每一格都是对**该类实例**调用这些方法得到的返回值。"
        "想确认某格为什么是「是/否」，请直接看对应方法，不要以本页文字为准。",
        "",
        "`RTLSDRBackend` 的 Offset 依赖已连接的驱动句柄"
        "（`hasattr(sdr, 'set_offset_tuning')`），未连接实例恒为「否」；"
        "连接后由盒子/驱动实际能力决定。",
        "",
        "## 3. 未纳入本表的「后端」",
        "",
        "这些类名字像后端，但**不是 `SDRBackend` 子类**，没有 `supports_*`；"
        "如果哪天它们接进统一后端接口，应当补上 `supports_*` 再自动进入本表。",
        "",
        "| 类 | 原因 |",
        "|---|---|",
    ]
    for mod_name, cls_name, reason in NON_SUBCLASS:
        mod = importlib.import_module(mod_name)
        if not hasattr(mod, cls_name):
            raise RuntimeError(f"{mod_name}.{cls_name} 不存在，本页 §3 已过期")
        lines.append(f"| `{mod_name}.{cls_name}` | {reason} |")
    lines += [
        "| `StubBackend` | 生产代码里没有这个类；只有 `tests/`、`scripts/repro/` "
        "里的测试桩（各自自定义，不参与设备能力判定） |",
        "",
        "## 4. 怎么用这张表",
        "",
        "界面上某项控件是否可用，以所连后端实例的 `supports_*` 返回值为准"
        "（`desktop/control_panel.py` 的 `set_device_capabilities` 按它收紧控件）；"
        "本表是**逐类**的静态对照，用于回答「这台设备/这个后端理论上能不能做」。"
        "实例级的实际结果（例如 RTLSDR 的 Offset、Soapy 的增益区间）在连接后才确定，"
        "表里已如实标注依赖条件。",
        "",
    ]
    return "\n".join(lines), rows


def _write(path: Path = DOC_PATH) -> str:
    text, _rows = render()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return text


_ROW_ITEMS = ("后端类",) + ITEMS


def _parse_rows(text: str) -> Dict[str, Dict[str, str]]:
    """从文档里取出 §1 表体：后端类 → 六项单元格原文。"""
    out: Dict[str, Dict[str, str]] = {}
    in_table = False
    for line in text.replace("\r\n", "\n").splitlines():
        if line.startswith("## 1. 能力矩阵"):
            in_table = True
            continue
        if in_table and line.startswith("## "):
            break
        if not in_table or not line.startswith("| `"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 + len(ITEMS):
            continue
        name = cells[0].strip("`")
        out[name] = dict(zip(("说明",) + ITEMS, cells[1:1 + 1 + len(ITEMS)]))
    return out


def check(path: Path = DOC_PATH) -> int:
    """快照 → --write → 比对 → 无条件还原（口径同 check_generated_artifacts）。"""
    if not path.is_file():
        print(f"[FAIL] 生成物不存在：{path.relative_to(ROOT)}")
        return 1
    original = path.read_bytes()
    try:
        _text, expected_list = render()
        expected_rows = {r["后端类"].strip("`"): r for r in expected_list}
        _write(path)
        produced = path.read_bytes()
    except Exception:  # noqa: BLE001 - 取数失败必须红，不能当绿
        traceback.print_exc()
        print("[FAIL] 取数失败：见上方异常")
        return 2
    finally:
        path.write_bytes(original)

    old_text = original.decode("utf-8", "replace")
    new_text = produced.decode("utf-8", "replace")
    if old_text.replace("\r\n", "\n") == new_text.replace("\r\n", "\n"):
        print(f"[ OK ] {path.relative_to(ROOT)} 与代码一致"
              f"（{len(expected_rows)} 个后端类）")
        return 0

    actual_rows = _parse_rows(old_text)
    problems: List[str] = []
    for name in sorted(set(expected_rows) | set(actual_rows)):
        if name not in actual_rows:
            problems.append(f"{name}：文档缺行")
            continue
        if name not in expected_rows:
            problems.append(f"{name}：文档多出（代码里已无此类）")
            continue
        for item in ITEMS:
            got = actual_rows[name].get(item, "")
            want = expected_rows[name][item]
            if got != want:
                problems.append(
                    f"{name} · {item}：文档「{got}」，代码「{want}」")
    if not problems:  # 表体一致但表外文本漂移（说明/统计行等）
        delta = list(difflib.unified_diff(
            old_text.splitlines(), new_text.splitlines(),
            path.name + "（当前树）", path.name + "（重跑）", lineterm="", n=1))
        problems.append("表外文本不一致：\n" + "\n".join(delta[:14]))
    print(f"[FAIL] 后端能力表与代码不一致（{len(problems)} 处）：")
    for p in problems[:20]:
        print(f"  - {p}")
    if len(problems) > 20:
        print(f"  … 另有 {len(problems) - 20} 处")
    print("重生成：python scripts/gen_backend_capability_matrix.py --write")
    return 1


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成/核对 SDR 后端能力对照表")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", action="store_true", help="重写文档")
    group.add_argument("--check", action="store_true", help="快照→写→比对→还原")
    args = ap.parse_args(argv)
    try:
        if args.write:
            _text, rows = render()
            _write()
            counts = {i: sum(1 for r in rows if r[i] == "是")
                      for i in ITEMS[:4]}
            print(f"已写入 {DOC_PATH.relative_to(ROOT)}：{len(rows)} 个后端类；"
                  f"AGC {counts['AGC']} / 增益 {counts['增益']} / "
                  f"PPM {counts['PPM']} / Offset {counts['Offset']}")
            return 0
        return check()
    except Exception:  # noqa: BLE001 - 取数失败必须红
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
