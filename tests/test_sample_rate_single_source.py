# SPDX-License-Identifier: MIT
"""采样率档位单一真值源机械校验（tests/test_sample_rate_single_source.py）

四处必须逐项相等（数量 + 值 + 顺序），任何一处多一档/少一档即 FAIL，
并打印四份完整清单与「仅 X 有」差集：

  1. C++ 实际下发表 `cpp/src/dsp/device_capabilities.cpp:kCandidateRatesHz`
     （`buildSampleRateOptions` 的 for 循环只遍历这一份）
  2. Python 后端表 `mbdsdr_ai/sdr_backend.py:RTLSDRBackend.SAMPLE_RATES`
  3. 桌面动态取表 `mbdsdr_ai/device_capabilities.py:sample_rate_options_for_backend`
     （桌面不持有档位副本，`desktop/control_panel.py` 展开时动态取）
  4. 文档 `docs/parameter-unification.md` C.1 表 `kCandidateRatesHz` 行的 11 档

2.8 的吸附关系单列一条：2.8 不是可请求档位；请求 2,800,000 → 假棒实收
2,880,000（+80,000），且 C++ 注释与文档实测行都记录同一关系。

反证：
  * 字符串级自检 `test_detector_is_not_vacuous_missing_216_must_be_red`；
  * 整文件级：`MBDSDR_PARAM_LEDGER=<临时副本>` 指向「删掉 **2.16**」的副本，
    主用例必须红（生产文件不动）。
"""
import os
import re
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "tests"))

import pytest  # noqa: E402

CXX_SRC = _ROOT / "cpp" / "src" / "dsp" / "device_capabilities.cpp"
LEDGER = Path(os.environ.get(
    "MBDSDR_PARAM_LEDGER",
    str(_ROOT / "docs" / "parameter-unification.md")))

EXPECTED_TIERS = 11
C1_SYMBOL = "`cpp/src/dsp/device_capabilities.cpp:kCandidateRatesHz`"


def _fmt(vals):
    return ", ".join(str(int(v)) for v in vals)


def _diff(a, b):
    return ([int(v) for v in a if v not in b],
            [int(v) for v in b if v not in a])


def _require_equal(name_a, a, name_b, b):
    """逐项相等（含顺序）；不等时打印两侧完整清单与差集。"""
    only_a, only_b = _diff(a, b)
    assert a == b, (
        f"{name_b} 与 {name_a} 不一致——档位表必须逐项相等（数量/值/顺序）\n"
        f"  {name_a}（{len(a)} 档）: {_fmt(a)}\n"
        f"  {name_b}（{len(b)} 档）: {_fmt(b)}\n"
        f"  仅 {name_a} 有: {_fmt(only_a) or '无'}\n"
        f"  仅 {name_b} 有: {_fmt(only_b) or '无'}"
    )


def _cxx_tiers():
    src = CXX_SRC.read_text(encoding="utf-8")
    m = re.search(r"kCandidateRatesHz\[\]\s*=\s*\{(.*?)\};", src, re.S)
    assert m, "C++ 找不到 kCandidateRatesHz[] 初始化块（表被改名或搬走？）"
    body = re.sub(r"//[^\n]*", "", m.group(1))
    return [int(round(float(v))) for v in re.findall(r"\d+(?:\.\d+)?", body)]


def _python_tiers():
    from mbdsdr_ai.sdr_backend import RTLSDRBackend
    return [int(round(float(r))) for r in RTLSDRBackend.SAMPLE_RATES]


def _desktop_tiers():
    from mbdsdr_ai.device_capabilities import sample_rate_options_for_backend

    class _Device:
        device_type = "rtlsdr"
        sample_rate_range = (225_001, 3_200_000)

    class _Backend:
        device = _Device()

    opts = sample_rate_options_for_backend(_Backend())
    assert opts, "桌面动态取表返回 None/空：rtl_sdr 应报告 11 档，而不是「未知」"
    return [int(r) for r in opts]


def _doc_row():
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        if C1_SYMBOL in line and line.lstrip().startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) >= 4 and cells[1].isdigit():
                return line
    raise AssertionError("文档 C.1 表找不到 kCandidateRatesHz 行（或行格式变了）")


def _tiers_from_row(row):
    cells = [c.strip() for c in row.strip().strip("|").split("|")]
    assert cells[1] == str(EXPECTED_TIERS), \
        f"C.1 档数写 {cells[1]}，应为 {EXPECTED_TIERS}"
    vals = []
    for tok in cells[2].split("/"):
        t = tok.replace("**", "").replace("M", "").strip()
        vals.append(int(Decimal(t) * 1_000_000))
    return vals


def _doc_tiers():
    return _tiers_from_row(_doc_row())


def _four_sources():
    return {
        "C++ kCandidateRatesHz（实际下发）": _cxx_tiers(),
        "Python RTLSDRBackend.SAMPLE_RATES": _python_tiers(),
        "桌面 sample_rate_options_for_backend": _desktop_tiers(),
        "文档 C.1 表": _doc_tiers(),
    }


def test_four_sources_list_the_same_tiers():
    sources = _four_sources()
    base_name = "C++ kCandidateRatesHz（实际下发）"
    base = sources[base_name]
    for name, vals in sources.items():
        if name != base_name:
            _require_equal(base_name, base, name, vals)
    assert base == sorted(base), f"C++ 档位表必须升序：{_fmt(base)}"


def test_cxx_dispatch_loop_traverses_the_same_table():
    src = CXX_SRC.read_text(encoding="utf-8")
    m = re.search(r"buildSampleRateOptions\([^)]*\)\s*\{", src)
    assert m, "C++ 找不到 buildSampleRateOptions 定义"
    body = src[m.start():src.index("\n}\n", m.end())]
    # 取循环真正遍历的标识符，而不是「函数体里提到过表名」
    loop = re.search(r"for\s*\([^:]*:\s*(\w+)\s*\)", body)
    assert loop, "buildSampleRateOptions 没有 for 循环真正遍历档位表"
    assert loop.group(1) == "kCandidateRatesHz", \
        f"buildSampleRateOptions 遍历的是 {loop.group(1)}——表和实际下发脱钩"


def _desktop_tier_table_reference_counts():
    return {
        name: (_ROOT / "desktop" / name).read_text(
            encoding="utf-8").count("SAMPLE_RATES")
        for name in ("control_panel.py", "main_window.py")
    }


def test_desktop_holds_no_local_tier_copy():
    counts = _desktop_tier_table_reference_counts()
    assert counts == {"control_panel.py": 0, "main_window.py": 0}, \
        "desktop 仍在维护本地档位表（应经 sample_rate_options_for_backend 动态取）：" \
        f"{counts}"


def _fake_rtl_backend():
    from fake_rtl import FakeRtlSdr
    from mbdsdr_ai.sdr_backend import RTLSDRBackend

    backend = RTLSDRBackend(device_index=0)
    fake = FakeRtlSdr(device_index=0)
    backend._sdr = fake
    backend._ring_reader = None
    backend.status.connected = True
    backend.status.sample_rate_hz = float(fake.sample_rate)
    return backend, fake


def test_2_8m_is_absorbed_to_2_88m_and_is_not_a_requestable_tier():
    sources = _four_sources()
    for name, vals in sources.items():
        assert 2_800_000 not in vals, f"{name} 把 2.8M 列成可请求档位"
        assert 2_880_000 in vals, f"{name} 缺 2.88M（设备实际给出的档）"

    backend, fake = _fake_rtl_backend()
    assert backend.set_sample_rate(2_800_000) is True
    readback = int(round(fake.sample_rate))
    assert readback == 2_880_000, f"请求 2,800,000 后硬件侧实收 {readback}"
    assert readback - 2_800_000 == 80_000
    assert int(round(backend.get_sample_rate())) == 2_880_000

    src = CXX_SRC.read_text(encoding="utf-8")
    recorded_base = re.search(r"2,800,000", src)
    assert recorded_base, \
        "C++ 候选表必须注释 2.8M→2.88M 的吸附关系（不许只在 Python/文档里写）"
    window = src[recorded_base.start():recorded_base.start() + 200]
    recorded_delta = re.search(r"\+\d[\d,]*", window)
    assert recorded_delta, "C++ 注释未记录吸附量（+80,000）"
    # 注释记录的吸附量必须与实测差值一致（不是「随便写了几个数字」）
    assert int(recorded_delta.group(0).lstrip("+").replace(",", "")) \
        == readback - 2_800_000

    row = next((line for line in LEDGER.read_text(encoding="utf-8").splitlines()
                if line.lstrip().startswith("| 2,800,000 |")), None)
    assert row, "文档 C.10.2 实测表缺 2,800,000 探针行"
    cells = [c.strip().replace("*", "").replace(",", "")
             for c in row.strip().strip("|").split("|")]
    assert cells[1] == "True" and cells[2] == "2880000" and cells[3] == "2880000", \
        f"文档实测行未记录 2.8→2.88 吸附：{cells[:4]}"


def test_detector_is_not_vacuous_missing_216_must_be_red():
    """反证：删掉 C.1 行的 **2.16**，比较器必须报「不一致」并给出差集。"""
    row = _doc_row()
    mutated = row.replace("**2.16** / ", "")
    assert "**2.16**" not in mutated
    mutated_tiers = _tiers_from_row(mutated)
    assert len(mutated_tiers) == EXPECTED_TIERS - 1
    with pytest.raises(AssertionError, match="不一致"):
        _require_equal("C++ kCandidateRatesHz（实际下发）", _cxx_tiers(),
                       "文档 C.1（变异副本）", mutated_tiers)


def _head_text(rel_path: str) -> str:
    """优先读 HEAD 里的文件文本，避免把别线在途的工作区编辑当成既成事实。

    git 不可用或该文件尚未入库时，回退到磁盘副本（CI 的常规状态）。
    """
    try:
        proc = subprocess.run(
            ["git", "show", f"HEAD:{rel_path}"],
            cwd=str(_ROOT), capture_output=True, text=True,
            encoding="utf-8", check=True)
        return proc.stdout
    except Exception:  # noqa: BLE001 - 任何 git 不可用都退回磁盘
        return (_ROOT / rel_path).read_text(encoding="utf-8")


def _tier_literal_hits(src):
    """module_panel 里写死的档位数值（任何后端表里的档位都算违规）。"""
    return re.findall(
        r"\b(?:1_024_000|1_440_000|1_536_000|1_792_000|1_920_000|2_048_000"
        r"|2_160_000|2_400_000|2_560_000|2_880_000|3_200_000)\b", src)


def _quoted_msps_hits(src):
    """module_panel 里带引号的本地档位文案（如 "1.440 MS/s"）。"""
    return re.findall(r"[\"'][^\"'\n]*MS/s[\"']", src)


def test_module_panel_derives_from_device_capabilities():
    """第五处：module_panel 不得持本地档位表，必须经 constants 按设备派生。

    HEAD 现状：`_fill_sample_rate_options` 已改为
    `sample_rate_combo_items(device)`；本用例锁死这条，防止把 5 档静态表
    （含任何内核表都没有的 `1.440 MS/s`）加回来。
    """
    src = _head_text("desktop/module_panel.py")

    # 1) 必须调用唯一的派生入口，且传入设备（不传设备就拿不到能力表）
    assert "sample_rate_combo_items(device)" in src, \
        "module_panel 未按设备能力派生采样率下拉（应调 sample_rate_combo_items(device)）"

    # 2) 不得出现档位数值字面量（任何一份后端表里的档位都不许写死在前端）
    assert not _tier_literal_hits(src), \
        f"module_panel 又出现档位字面量（应只经能力表派生）：{_tier_literal_hits(src)}"

    # 3) 不得有带引号的档位文案，也不得引用档位表符号
    assert not _quoted_msps_hits(src), \
        f"module_panel 又出现本地档位文案：{_quoted_msps_hits(src)}"
    for symbol in ("SAMPLE_RATES", "kCandidateRatesHz", "sampleRatesHz"):
        assert symbol not in src, f"module_panel 引用了本地档位表符号 {symbol}"

    # 4) 派生入口自身必须来自内核能力表（否则「已派生」只是换了个名字）
    const_src = _head_text("desktop/constants.py")
    assert "from mbdsdr_ai.device_capabilities import sample_rate_options" \
        in const_src, "constants.sample_rate_combo_items 未走内核能力表"
    assert "sample_rate_options(device)" in const_src, \
        "constants.sample_rate_combo_items 未按设备查询档位"


def test_module_panel_derivation_detector_is_not_vacuous():
    """反证：把已删掉的 5 档静态表片段塞进来，两个检测器都必须报。"""
    mutated = (
        'self.sr_combo.addItem("1.440 MS/s", 1_440_000)\n'
        "items = sample_rate_combo_items(device)\n"
    )
    assert _tier_literal_hits(mutated) == ["1_440_000"]
    assert _quoted_msps_hits(mutated) == ['"1.440 MS/s"']
