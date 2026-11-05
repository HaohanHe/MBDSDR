"""U-03 量纲门禁：采样率语义的变量/档位绝不许格式化成 MHz。

「采样率写成 MHz」比措辞错更危险：MHz 是频率/带宽量纲，用户会把它和中心频率
当成同类数比大小。实测同一 2.048 曾在三端写成 MHz / MS/s / Msps
（见 docs/copy-audit.md §3）。本文件是**机械 lint**，不评价措辞，只认这一种
硬错：「采样率语义的名字 → MHz 单位」。

两条规则（任一命中即 FAIL，报告里给出 文件:行:原文）：

  R1 模板串：同一表达式里 sample_rate / sr / fs / rate_hz 除以 1e6 后接 MHz。
  R2 档位表：字符串字面量形如 "…MHz" 与数字成对出现，且 ±8 行内出现采样率
     语义标识符（sr_combo / sr_edit / sample_rate / rate_hz / …）。

两种显式豁免（都不是「不许报」后门，是量纲合法）：

  * 同一行写了「带宽 / 跨度 / span / bandwidth」——那是带宽量纲，MHz 合法；
  * 行尾标 `unit-lint:allow` 并写理由——只用于频率步进/频率预设这类
    真频率档位表。

反证（已实测，见提交说明）：把 `desktop/module_panel.py` 采样率下拉五个标签
从 "1.024 MS/s" 改回 "1.024 MHz" → R2 命中 5 处，本文件红；改回即绿。
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCAN_EXTS = {".py", ".dart", ".cpp", ".h", ".hpp"}
SKIP_DIRS = {
    "tests", "build", "build_cpp", "__pycache__", ".git", "node_modules",
    ".dart_tool", "flutter", "audit_r2", "third_party_licenses", "repos",
    "skills", ".audit_tmp", "scratch", "output", "logs",
}

# 采样率语义的标识符：sample_rate / srate / rate_hz / sr_combo / sr_edit / fs_hz…
RATE_NAME = (r"(?:sample_rate|srate|samplerate|samp_rate|rate_hz"
             r"|sr_[a-z0-9_]*|fs_[a-z0-9_]*|\bsr\b|\bfs\b)")

R1 = re.compile(
    r"(?i)(" + RATE_NAME + r")\s*/\s*(?:1e6|1\.0e6|1000000\.0?)"
    r"[^\"'\n]{0,20}?MHz")
R2_TUPLE = re.compile(r'"[^"]*MHz"\s*,\s*[-+]?[0-9]')
R2_RATE_CTX = re.compile(r"(?i)" + RATE_NAME)
BW_EXEMPT = re.compile(r"带宽|跨度|span|bandwidth", re.I)
ALLOW_MARK = re.compile(r"unit-lint:allow")


def _scan_text(rel_path: str, text: str) -> list[str]:
    """返回违规清单（`path:line:原文`）；纯函数，便于用合成片段自测。"""
    hits: list[str] = []
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        if ALLOW_MARK.search(line):
            continue
        if R1.search(line) and not BW_EXEMPT.search(line):
            hits.append(f"{rel_path}:{idx + 1}:{line.strip()}")
            continue
        if not R2_TUPLE.search(line) or BW_EXEMPT.search(line):
            continue
        lo, hi = max(0, idx - 8), min(len(lines), idx + 9)
        if R2_RATE_CTX.search("\n".join(lines[lo:hi])):
            hits.append(f"{rel_path}:{idx + 1}:{line.strip()}")
    return hits


def test_lint_function_catches_rate_formatted_as_mhz():
    """R1：模板串里采样率除以 1e6 后接 MHz 必须命中。"""
    src = 'msg = f"采样率 {sample_rate_hz / 1e6:.3f} MHz"\n'
    hits = _scan_text("demo.py", src)
    assert len(hits) == 1, hits
    assert "sample_rate_hz" in hits[0]


def test_lint_function_catches_rate_preset_tuple_in_rate_context():
    """R2：采样率下拉档位写成 MHz 必须命中（±8 行内有 sr_combo）。"""
    src = (
        "self.sr_combo = QComboBox()\n"
        'for _txt, _hz in (("1.024 MHz", 1_024_000.0), ("2.048 MHz", 2_048_000.0)):\n'
        "    self.sr_combo.addItem(_txt, _hz)\n"
    )
    hits = _scan_text("demo.py", src)
    assert len(hits) == 1, hits
    assert "1.024 MHz" in hits[0]


def test_lint_function_allows_bandwidth_and_explicit_allow():
    """带宽量纲写 MHz 合法；显式 unit-lint:allow 的频率档位表也合法。"""
    src = (
        'msg = f"瞬时带宽 {sr / 1e6:.2f}MHz"\n'
        'for _txt, _hz in (("1MHz", 1_000_000.0),):  # unit-lint:allow 频率步进\n'
        "    pass\n"
    )
    assert _scan_text("demo.py", src) == []


def test_repo_has_no_sample_rate_formatted_as_mhz():
    """全仓扫描：采样率语义的名字绝不许挂 MHz 单位。

    这是本文件的主断言；命中即红，输出逐条 文件:行:原文，便于直接定位。
    """
    hits: list[str] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in SCAN_EXTS:
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        rel = path.relative_to(ROOT).as_posix()
        hits.extend(_scan_text(rel, text))
    assert hits == [], "\n".join(hits)


# ---------------------------------------------------------------------------
# R3/R4：sdr_tools 的频率读数精度与工具默认采样率
# ---------------------------------------------------------------------------
SDR_TOOLS = ROOT / "mbdsdr_ai" / "sdr_tools.py"

# 频率读数只允许 3 位小数；需要 Hz 级精度时用 6 位并紧跟「Hz」。
# 1/2/4 位是历史混用（同一 137.62 在不同工具里写成 137.6 / 137.62 / 137.6200）。
COARSE_OR_EXTRA = re.compile(r":(?:[0-9]+)?\.(?:1|2|4)f\}\s*MHz")


def test_precision_rule_catches_coarse_and_extra_digits():
    """纯函数级反证：1/2/4 位必须命中，3 位与「6 位+Hz」放行。"""
    assert COARSE_OR_EXTRA.search('f"{f/1e6:.1f} MHz"')
    assert COARSE_OR_EXTRA.search('f"{f/1e6:9.4f} MHz"')
    assert COARSE_OR_EXTRA.search('f"{f/1e6:.2f}MHz"')
    assert not COARSE_OR_EXTRA.search('f"{f/1e6:.3f} MHz"')
    assert not COARSE_OR_EXTRA.search('f"{f/1e6:.6f} MHz（137620000 Hz）"')


def test_sdr_tools_frequency_readouts_use_three_decimals():
    """sdr_tools 的用户可见频率读数统一 3 位小数（Hz 级精度另走 6 位+Hz）。"""
    lines = SDR_TOOLS.read_text(encoding="utf-8", errors="ignore").splitlines()
    hits = [
        f"mbdsdr_ai/sdr_tools.py:{i}:{line.strip()}"
        for i, line in enumerate(lines, 1)
        if COARSE_OR_EXTRA.search(line)
        and not BW_EXEMPT.search(line)
        and not ALLOW_MARK.search(line)
    ]
    assert hits == [], "\n".join(hits)


def test_sdr_tools_gnss_sample_rate_default_is_project_default():
    """GNSS 工具默认采样率必须跟随项目真值源，不许另写一份 2.048 MS/s。

    理由：这两个工具生成的是模拟 IQ（无硬件依赖），不存在「某后端只支持 2.048」
    的兼容理由；2.048 只是 RTL-SDR 的一个可用档。
    """
    src = SDR_TOOLS.read_text(encoding="utf-8", errors="ignore")
    assert "2.048e6" not in src, "仍有手写的 2.048e6（应引用 DEFAULT_SAMPLE_RATE_HZ）"
    assert '"default": DEFAULT_SAMPLE_RATE_HZ' in src
    assert "args.get('sample_rate_hz', DEFAULT_SAMPLE_RATE_HZ)" in src
    assert "sample_rate = DEFAULT_SAMPLE_RATE_HZ" in src


def test_default_sample_rate_advice_matches_project_default():
    """建议文案里的「默认采样率」必须来自 `DEFAULT_SAMPLE_RATE_HZ`。

    2.048 MS/s 只是 RTL-SDR 的一个可用档，不是项目默认值（当前 2.400 MS/s）；
    建议文案若手写一份数值，会随常量漂移而不自知。
    """
    from mbdsdr_ai.error_handler import SampleRateNotSupportedError
    from mbdsdr_ai.sdr_backend import DEFAULT_SAMPLE_RATE_HZ, format_sample_rate

    err = SampleRateNotSupportedError()
    expected = format_sample_rate(DEFAULT_SAMPLE_RATE_HZ)
    assert expected in err.suggestion, err.suggestion
    assert "2.048 MS/s" not in err.suggestion, (
        "2.048 是可用档、不是项目默认；建议文案不得把它写成默认值")
