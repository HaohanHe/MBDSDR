# SPDX-License-Identifier: MIT
"""硬编码参数治理：单一事实源与「锁定值」钉子。

本文件对应 ``docs/hardcoded-parameters.md``。它不测业务结果，只钉三件事：

1. 光害可见性口径只有**一份**定义（绘制门与星表 LOD 门共用同一函数对象，
   不是"同式副本"）；
2. B 类"该锁死"的科学常量保持原值（改了会让星等/消光/星表深度失去依据）；
3. 两个进程（Python 桌面端 / C++ 桌面端）各自定义的阈值与文档记录的
   跨语言差异一致——任何一侧悄悄改数，这里就红，迫使先改文档再改代码。

注意：这里**不**断言两侧数值相同。C++ 与 Python 是两个进程，没有共享常量
机制；它们可以不同，但必须"各自定义 + 文档对齐 + 测试对拍"。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import pytest  # noqa: E402

from mbdsdr_ai.skyengine import star_catalog, stars  # noqa: E402

TOKENS_H = REPO / "cpp" / "src" / "core" / "tokens.h"
SKYENGINE_DIR = REPO / "mbdsdr_ai" / "skyengine"


# ---------------------------------------------------------------------------
# 1. 光害可见性：唯一事实源
# ---------------------------------------------------------------------------
def test_mag_limit_is_the_same_function_object():
    """星表 LOD 门与绘制门必须是同一函数，而不是各自复制一条公式。"""
    assert star_catalog.naked_eye_limit_mag is stars.naked_eye_limit_mag


@pytest.mark.parametrize("bortle,expected", [
    (1, 6.5), (2, 6.5), (3, 6.5),
    (4, 6.15), (5, 5.8), (6, 5.45),
    (9, 4.4),
])
def test_mag_limit_values_are_pinned(bortle, expected):
    """锁定裸眼极限口径（Stellarium StelSkyDrawer 同语义）。

    Bortle<=3 恒为 6.5 等，每高一档 -0.35 等。改这些值会同时改变
    「星画不画」与「星点不点得中」两个门，属于科学口径，不许随手调。
    """
    assert stars.naked_eye_limit_mag(bortle) == pytest.approx(expected)


def _code_only(path: Path) -> str:
    """只留代码 token（去注释与文档字符串），避免把说明文字当成副本。"""
    import io
    import tokenize

    with path.open(encoding="utf-8") as fh:
        tokens = tokenize.generate_tokens(io.StringIO(fh.read()).readline)
        return " ".join(t.string for t in tokens
                        if t.type not in (tokenize.COMMENT, tokenize.STRING))


def test_mag_limit_formula_is_not_duplicated_in_sources():
    """源码层防回流：skyengine 里不许再出现『6.5 - 0.35 * ...』同式副本。"""
    offenders = []
    for path in sorted(SKYENGINE_DIR.glob("*.py")):
        if re.search(r"6\.5\s*-\s*0\.35", _code_only(path)):
            offenders.append(path.name)
    assert not offenders, (
        f"{offenders} 里还有裸眼极限公式的字面副本；"
        "请调用 stars.naked_eye_limit_mag()"
    )


def test_bortle_dim_formula_is_not_duplicated_in_sources():
    """同样的，亮度压暗系数不许在别处另写一份 1 - 0.06*(bortle-3)。"""
    offenders = []
    for path in sorted(SKYENGINE_DIR.glob("*.py")):
        if re.search(r"1\.0\s*-\s*0\.06", _code_only(path)):
            offenders.append(path.name)
    assert not offenders, (
        f"{offenders} 里还有 Bortle 压暗公式副本；"
        "请调用 stars.bortle_dim_factor()"
    )


def test_star_draw_matches_vectorized_mirror_at_default_bortle():
    """标量版是事实源，向量版是镜像；默认 Bortle 下逐点一致。"""
    import numpy as np

    mags = np.round(np.arange(-2.0, 7.0, 0.25), 2)
    r, b, g = stars.star_draw_many(mags, stars.DEFAULT_BORTLE)
    for i, m in enumerate(mags):
        sr, sb, sg = stars.star_draw(float(m), stars.DEFAULT_BORTLE)
        assert (r[i], b[i], g[i]) == pytest.approx((sr, sb, sg))


# ---------------------------------------------------------------------------
# 2. B 类锁定值（科学正确性 / 一致性）：改了必须走文档评审
# ---------------------------------------------------------------------------
def test_extinction_model_is_locked():
    """消光模型 k=0.2、Δm 上限 3.5、仰角下限 0.5°。

    这三条不是"调参"，是模型假设：
      * k=0.2 mag/airmass 是中纬度天顶平均值（Bortle 4-5 站点常用）；
      * 上限 3.5 等：再暗就是地平线雾霾，不能给无穷；
      * 下限 0.5°：防 sin(alt)→0 除零，低于此无物理意义。
    改了会让低仰角恒星的可见性门整体漂移。
    """
    assert stars.EXTINCTION_K == pytest.approx(0.2)
    assert stars._EXTINCTION_MAX_DM == pytest.approx(3.5)
    assert stars._EXTINCTION_MIN_ALT == pytest.approx(0.5)


def test_catalog_lod_constants_are_locked():
    """星表 LOD 口径：深度 9.0 等、分档 0.5、默认上限 20000、增益基准。

    CATALOG_DEPTH_MAG 是**星表数据的物理边界**（bsc5p 最暗 ~9 等），
    放大它只会让 limit_magnitude 声称星表没有的深度；
    DETAIL_REF_FOV_DEG / MAX_DETAIL_GAIN_MAG 与 limit_magnitude 的
    Bortle 门叠加，单改任一个都会让同一片天空的星数跳变。
    """
    assert star_catalog.CATALOG_DEPTH_MAG == pytest.approx(9.0)
    assert star_catalog.MAG_BIN_WIDTH == pytest.approx(0.5)
    assert star_catalog.DEFAULT_MAX_COUNT == 20000
    assert star_catalog.DETAIL_REF_FOV_DEG == pytest.approx(30.0)
    assert star_catalog.MAX_DETAIL_GAIN_MAG == pytest.approx(1.5)


BFIELD_DEPTH_GAIN = 0.5  # detail_gain_mag 的 log2 系数（等/倍频程）


def test_detail_gain_slope_is_locked():
    """视场深度增益斜率 0.5 等/倍频程：从 30° 视场起算。"""
    assert star_catalog.detail_gain_mag(15.0) == pytest.approx(BFIELD_DEPTH_GAIN)
    assert star_catalog.detail_gain_mag(60.0) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# 2b. 静噪：一个量纲、一组常量（解调后音频 RMS，-100..-20 dBFS）
# ---------------------------------------------------------------------------
def test_squelch_has_one_domain_and_one_constant_set():
    """滑杆量的唯一事实源：内核 squelch 模块，桌面端不得另写数字。

    历史问题：WFM 比"全带宽 IQ 功率"、窄带比"信道 IQ 功率"，同一根滑杆
    标定两个测量点。统一到解调后音频 RMS 后，带宽/增益变化不再改变语义。
    """
    from mbdsdr_ai.squelch import (
        DEFAULT_SQUELCH_DBFS,
        SQUELCH_MAX_DBFS,
        SQUELCH_MIN_DBFS,
    )

    assert (SQUELCH_MIN_DBFS, SQUELCH_MAX_DBFS, DEFAULT_SQUELCH_DBFS) == (
        -100.0, -20.0, -50.0)


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def test_control_panel_uses_kernel_squelch_constants(qapp):
    """桌面滑杆的范围/默认值必须与内核静噪常量逐项一致（控件回读）。

    行为断言：构造真实 ControlPanel，读 squelch_slider 的 min/max/value，
    再把它设到内核两端确认端点可达（不被别处的范围夹住）。
    写字面量 -120/0 或别的默认值会在下面的相等断言上当场红。
    """
    from control_panel import ControlPanel
    from mbdsdr_ai.squelch import (
        DEFAULT_SQUELCH_DBFS, SQUELCH_MAX_DBFS, SQUELCH_MIN_DBFS)

    cp = ControlPanel()
    try:
        slider = cp.squelch_slider
        assert slider.minimum() == int(SQUELCH_MIN_DBFS), (
            "静噪滑杆下限没跟内核常量走："
            f"{slider.minimum()} != {int(SQUELCH_MIN_DBFS)}")
        assert slider.maximum() == int(SQUELCH_MAX_DBFS), (
            "静噪滑杆上限没跟内核常量走："
            f"{slider.maximum()} != {int(SQUELCH_MAX_DBFS)}")
        assert slider.value() == int(DEFAULT_SQUELCH_DBFS), (
            "静噪滑杆默认值没跟内核常量走："
            f"{slider.value()} != {int(DEFAULT_SQUELCH_DBFS)}")
        slider.setValue(int(SQUELCH_MIN_DBFS))
        assert slider.value() == int(SQUELCH_MIN_DBFS), "下限端点不可达"
        slider.setValue(int(SQUELCH_MAX_DBFS))
        assert slider.value() == int(SQUELCH_MAX_DBFS), "上限端点不可达"
    finally:
        cp.deleteLater()


# ---------------------------------------------------------------------------
# 2c. 过境仰角：两个语义、两个常量，next_pass 的默认必须是对外承诺的门
# ---------------------------------------------------------------------------
def test_pass_and_visible_elevation_are_named_apart():
    """过境列表门(10°) 与可见/跟踪退出门(5°) 必须是两个名字。

    历史 bug：next_pass() 默认 5°，却把它传进默认 10° 的 predict_passes()，
    于是"下一次过境"与直接预测拿到两个门。
    """
    import inspect

    from mbdsdr_ai.sat_passes import PASS_MIN_ALT_DEG, VISIBLE_MIN_ALT_DEG
    from mbdsdr_ai.sat_tracker import (
        DEFAULT_MIN_ELEVATION_DEG,
        SatelliteTracker,
    )

    assert PASS_MIN_ALT_DEG == pytest.approx(10.0)
    assert VISIBLE_MIN_ALT_DEG == pytest.approx(5.0)
    assert DEFAULT_MIN_ELEVATION_DEG == pytest.approx(VISIBLE_MIN_ALT_DEG)
    default = inspect.signature(SatelliteTracker.next_pass).parameters[
        "min_alt"].default
    assert default == pytest.approx(PASS_MIN_ALT_DEG)


# ---------------------------------------------------------------------------
# 3. 跨语言对拍：C++ 侧阈值（tokens.h）与文档记录一致
# ---------------------------------------------------------------------------
#: 文档 docs/hardcoded-parameters.md 里登记的 C++ 侧值。任何一侧改了，
#: 先更新文档与这张表，再改代码。
CPP_EXPECTED = {
    "kDbSpinLowerMin": -140,
    "kDbSpinLowerMax": -20,
    "kDbSpinUpperMin": -40,
    "kDbSpinUpperMax": 20,
    "kDbLowerDefault": -100,
    "kDbViewUpperDefault": -20,   # 默认视窗上限（对齐 Python）
    "kDbCeilingMax": 0,           # 显示天花板硬上限（0 dBFS，满量程）
    # 两条峰门**分开记账**：显示门 6.0 / 校准门 15.0（见 3b 节）。
    "kPeakRelDb": 6.0,
    "kPeakThresholdDefault": 15.0,
    "kPeakAbsFloorDbfs": -100.0,
    "kPeakMatchBins": 5,
    "kPeakMinSeenFrames": 3,
    "kPeakMaxMissFrames": 10,
    "kPeakThresholdMin": 5,
    "kPeakThresholdMax": 40,
    "kSquelchMinDb": -100,
    "kSquelchMaxDb": -20,
    "kSquelchDefaultDb": -50,
    "kAiThinkingBudgetTokens": 4096,
    "kAiDefaultContextWindowTokens": 32768,
    "kAiMaxTransientRetries": 3,
}

_INLINE_CONSTEXPR = re.compile(
    r"inline\s+constexpr\s+(?:int|float|double)\s+(\w+)\s*=\s*"
    r"(-?\d+(?:\.\d+)?)f?\s*;")


@pytest.mark.skipif(not TOKENS_H.exists(), reason="C++ 源码不在当前检出")
def test_cpp_tokens_match_documented_values():
    text = TOKENS_H.read_text(encoding="utf-8")
    found = {m.group(1): float(m.group(2))
             for m in _INLINE_CONSTEXPR.finditer(text)}
    for name, expected in CPP_EXPECTED.items():
        assert found.get(name) is not None, \
            f"tokens.h 里找不到 {name}（被改名或移动了？）"
        assert found[name] == pytest.approx(expected), (
            f"{name} 已从 {expected} 变成 {found[name]}；"
            "跨语言参数表 docs/hardcoded-parameters.md 需同步评审")


@pytest.mark.skipif(not TOKENS_H.exists(), reason="C++ 源码不在当前检出")
def test_tuning_step_tiers_match_cpp_and_schema():
    """调谐步进七档（含 1 Hz）在 C++/Python schema 逐值一致；死 token 已改名。"""
    if str(REPO / "desktop") not in sys.path:
        sys.path.insert(0, str(REPO / "desktop"))
    import config_schema

    expected = (1, 10, 100, 1_000, 10_000, 100_000, 1_000_000)
    item = config_schema.SCHEMA["tuning_step_hz"]
    assert item.kind == "enum"
    assert tuple(item.choice_values) == expected, (
        "Python 侧缺 1 Hz 档会让 1 Hz 配置导入回默认 10 kHz（差 10,000 倍）")
    assert config_schema.validate_value(item, 1)[0] == 1

    cpp = (REPO / "cpp/src/ui/main_window.cpp").read_text(encoding="utf-8")
    m = re.search(r"kStepValuesHz\[\]\s*=\s*\{([^}]*)\}", cpp)
    assert m, "main_window.cpp 里找不到 kStepValuesHz"
    assert tuple(int(x) for x in re.findall(r"\d+", m.group(1))) == expected

    # 旧名 kFreqFineStepHz 是"看起来能用、接上就错 10 倍"的死值，已改为粗调档位。
    toks = _tokens_values()
    assert toks.get("kFreqCoarseStepHz") is not None
    assert toks.get("kFreqFineStepHz") is None


@pytest.mark.skipif(not TOKENS_H.exists(), reason="C++ 源码不在当前检出")
def test_waterfall_depth_is_six_tier_enum_everywhere(caplog):
    """瀑布深度六档：C++ / Python 生成器 / 设置 schema 三者逐值一致。

    反证：任一端允许 384（旧 Python 行为）或多一档，本测试红；非档位值必须
    回落 256 并留下原因，不许静默夹取（夹到 384 会让回看时长差 1.5 倍）。
    """
    import logging
    if str(REPO / "desktop") not in sys.path:
        sys.path.insert(0, str(REPO / "desktop"))
    import config_schema
    import spectrum_widget

    expected = (128, 256, 512, 1024, 2048, 4096)
    # Python 生成器
    assert spectrum_widget.WF_DEPTH_CHOICES == expected
    gen = spectrum_widget.SpectrumDataGenerator(num_bins=512)
    with caplog.at_level(logging.WARNING, logger="spectrum_widget"):
        assert gen.set_waterfall_depth(384) == 256
    assert "不在六档" in caplog.text, "非法档位必须记录原因，不许静默"
    assert gen.set_waterfall_depth(1024) == 1024

    # 设置 schema 不再是任意 128–4096 的 int
    item = config_schema.SCHEMA["waterfall_depth_rows"]
    assert item.kind == "enum"
    assert tuple(item.choice_values) == expected
    value, reason = config_schema.validate_value(item, 384)
    assert value == 256 and reason and "不在可选值" in reason

    # C++ tokens.h 同一数组
    m = re.search(r"kWaterfallDepthChoices\[\]\s*=\s*\{([^}]*)\}",
                  TOKENS_H.read_text(encoding="utf-8"))
    assert m, "tokens.h 里找不到 kWaterfallDepthChoices"
    assert tuple(int(x) for x in re.findall(r"\d+", m.group(1))) == expected


@pytest.mark.skipif(not TOKENS_H.exists(), reason="C++ 源码不在当前检出")
def test_fft_size_tiers_match_schema_exactly(caplog):
    """FFT 档位唯一事实源：生成器 ``FFT_SIZES`` == schema ``fft_size.choices``。

    必须逐项相等（值 + 顺序），不能只比集合：任何一侧单方面加 8192/16384
    立即红。旧配置/直接调用引用已删档位时，必须回落**最近档**并记录原因，
    不许静默改成 2048 当没事发生。
    """
    import logging
    if str(REPO / "desktop") not in sys.path:
        sys.path.insert(0, str(REPO / "desktop"))
    import config_schema
    import spectrum_widget

    expected = (1024, 2048, 4096)
    gen_cls = spectrum_widget.SpectrumDataGenerator
    assert gen_cls.FFT_SIZES == expected
    item = config_schema.SCHEMA["fft_size"]
    assert item.kind == "enum"
    assert item.choice_values == gen_cls.FFT_SIZES, (
        "FFT 档位必须与 config_schema 逐项相等（含顺序）")

    gen = gen_cls(num_bins=512)
    with caplog.at_level(logging.WARNING, logger="spectrum_widget"):
        assert gen.set_fft_size(16384) == 4096   # 最近档，不是默认 2048
        assert gen.set_fft_size(8192) == 4096
    assert "回落到最近档 4096" in caplog.text, "删除档位必须记录回落原因"
    assert gen.set_fft_size(2048) == 2048


@pytest.mark.skipif(not TOKENS_H.exists(), reason="C++ 源码不在当前检出")
def test_fft_bin_width_identical_at_2_4_msps():
    """2.4 MS/s 下两端 bin 宽必须一致（此前 146.48 Hz vs 585.94 Hz）。

    16384 档 = 2.4e6/16384 = 146.484 Hz，4096 档 = 585.938 Hz，差 439.45 Hz
    （4 倍）；删掉 8192/16384 后所有路径都落到 4096，差值为 0。
    """
    if str(REPO / "desktop") not in sys.path:
        sys.path.insert(0, str(REPO / "desktop"))
    import spectrum_widget

    sr = 2.4e6
    widths = tuple(sr / s for s in spectrum_widget.SpectrumDataGenerator.FFT_SIZES)
    assert widths == (2343.75, 1171.875, 585.9375)
    # 最大档（4096）对应最细 bin：585.9375 Hz 是现在两端共同的下限
    assert min(widths) == pytest.approx(585.9375)

    gen = spectrum_widget.SpectrumDataGenerator(num_bins=512)
    gen.sample_rate_hz = sr
    for legacy in (8192, 16384):
        assert gen.set_fft_size(legacy) == 4096
        # 回落后的 bin 宽与合法最大档完全一致：差 = 0 Hz
        assert (sr / gen.fft_size) - (sr / 4096) == 0.0


@pytest.mark.skipif(not TOKENS_H.exists(), reason="C++ 源码不在当前检出")
def test_db_default_range_matches_python_and_keeps_minus50_visible():
    """C++ 默认 dB 视窗冻结在 -100..-20（与 Python 一致）。

    反证：改回 -45..0 时本测试立即红，且 -50 dBFS 会被夹到底边——
    同一录音在 C++ 与 Python 界面显示成两种样子。
    """
    found = {m.group(1): float(m.group(2))
             for m in _INLINE_CONSTEXPR.finditer(
                 TOKENS_H.read_text(encoding="utf-8"))}
    lo, hi = found["kDbLowerDefault"], found["kDbViewUpperDefault"]
    assert (lo, hi) == (-100.0, -20.0), (
        f"C++ 默认 dB 量程应为 -100..-20，实际 {lo:g}..{hi:g}")
    assert lo <= -50.0 <= hi, "-50 dBFS 的常见信号必须落在默认视窗内"
    # 天花板硬上限是另一个量：0 dBFS（满量程），不是默认视窗上限。
    # 合并这两者会让显式 setDbRange(-100, 0) 被夹成 -20，-10 dB 峰值越过天花板
    # 画成纯白（255）而不是色表顶端（229）。
    ceiling = found["kDbCeilingMax"]
    assert ceiling == 0.0, f"显示天花板硬上限应为 0 dBFS，实际 {ceiling:g}"
    assert ceiling > hi, "天花板硬上限必须高于默认视窗上限（0 > -20）"


#: 已知且**有意**的跨语言差异（不是 bug，但必须登记）。若某天两侧统一了，
#: 删掉对应行即可——测试会提醒。
CROSS_LANGUAGE_DELTAS = {
    # 语义                    Python               C++
    # 默认视窗已统一为 -100..-20（见 test_db_default_range_*）；下面只剩
    # “允许拖到的夹取边界”这一层残留，不是默认值差异。
    "dB 轴下限": ("-160.0", "-140"),
    "dB 轴上限": ("0.0", "20"),
}


def test_cross_language_deltas_are_documented():
    """差异本身不报错，但必须在文档里登记（防止"默默统一成某一个值"）。"""
    doc = REPO / "docs" / "hardcoded-parameters.md"
    if not doc.exists():
        pytest.skip("参数普查文档尚未生成")
    text = doc.read_text(encoding="utf-8")
    missing = [semantic for semantic in CROSS_LANGUAGE_DELTAS
               if re.search(re.escape(semantic), text) is None]
    assert missing == [], (
        f"跨语言差异未在 docs/hardcoded-parameters.md 登记：{missing}")


# ---------------------------------------------------------------------------
# 3b. 峰值检测：显示门与校准门分开记账（2026-11-05 裁定）
# ---------------------------------------------------------------------------
CPP_SPECTRUM_DISPLAY_H = REPO / "cpp" / "src" / "ui" / "spectrum_display.h"
CPP_SPECTRUM_WIDGET_CPP = REPO / "cpp" / "src" / "ui" / "spectrum_widget.cpp"
CPP_CALIBRATION_DIALOG_CPP = REPO / "cpp" / "src" / "ui" / "calibration_dialog.cpp"
DESKTOP = REPO / "desktop"

#: Python 侧同一套检测器定义（spectrum_widget.py 的模块常量）。
PY_PEAK_DEFINITION = {
    "_PEAK_REL_DB_DEFAULT": "kPeakRelDb",
    "_PEAK_ABS_FLOOR_DBFS": "kPeakAbsFloorDbfs",
    "_PEAK_MIN_SEEN_FRAMES": "kPeakMinSeenFrames",
    "_PEAK_MATCH_BINS": "kPeakMatchBins",
    "_PEAK_MAX_MISS_FRAMES": "kPeakMaxMissFrames",
}


def _tokens_values():
    text = TOKENS_H.read_text(encoding="utf-8")
    return {m.group(1): float(m.group(2))
            for m in _INLINE_CONSTEXPR.finditer(text)}


def _tokens_comment_text():
    """把 tokens.h 的 // 注释抽出来并归一空白（跨行句子才能整句断言）。"""
    parts = [line.split("//", 1)[1] for line in
             TOKENS_H.read_text(encoding="utf-8").splitlines() if "//" in line]
    return re.sub(r"\s+", " ", " ".join(parts))


@pytest.mark.skipif(not TOKENS_H.exists(), reason="C++ 源码不在当前检出")
def test_display_gate_and_calibration_gate_are_locked_apart():
    """显示门 6.0 与校准门 15.0 是两条门：各自冻结、各自接线、注释禁止合并。

    反证：把 kPeakRelDb 改成别的值 → 冻结红；把 kPeakThresholdDefault 改成
    别的值 → 冻结红。两个都红，才说明两条门各自有守卫（而不是一条锁死、
    另一条自由漂）。
    """
    found = _tokens_values()
    assert found["kPeakRelDb"] == pytest.approx(6.0), (
        "显示检测器门限漂了；这是 2026-11-05 裁定的定义值，"
        "改它必须同步 docs/hardcoded-parameters.md 与两份实现")
    assert found["kPeakThresholdDefault"] == pytest.approx(15.0), (
        "频率校准参考门漂了；校准必须拒绝纯噪声（test_frequency_calibrator）")
    assert found["kPeakRelDb"] != found["kPeakThresholdDefault"], \
        "两条门被合并成一个值：显示反证与校准诚实性要求相反"

    # 接线：显示路径只许用显示门，校准路径只许用校准门。
    display_h = CPP_SPECTRUM_DISPLAY_H.read_text(encoding="utf-8")
    widget_cpp = CPP_SPECTRUM_WIDGET_CPP.read_text(encoding="utf-8")
    calib_cpp = CPP_CALIBRATION_DIALOG_CPP.read_text(encoding="utf-8")
    gates = {
        "display": set(re.findall(r"tokens::(kPeak\w+)", display_h)),
        "widget": set(re.findall(r"tokens::(kPeak\w+)", widget_cpp)),
        "calib": set(re.findall(r"tokens::(kPeak\w+)", calib_cpp)),
    }
    assert "kPeakRelDb" in gates["display"], \
        "spectrum_display.h 没有引用显示门（又写回 15 了？）"
    assert "kPeakRelDb" in gates["widget"], \
        "峰值自旋框默认值没有引用显示门"
    assert "kPeakThresholdDefault" in gates["calib"], \
        "频率校准没有引用校准门"
    assert "kPeakThresholdDefault" not in gates["display"], \
        "显示路径又引用了校准门：两条门被串线了"
    assert "kPeakRelDb" not in gates["calib"], \
        "校准路径引用了显示门：校准会放进纯噪声"

    # 注释必须保留「不要合并」的裁定（跨行句子按空白归一后断言）。
    assert "Do NOT merge the two" in _tokens_comment_text(), \
        "tokens.h 丢了禁止合并的注释：后来人会以为两条门可以统一"


@pytest.mark.skipif(not TOKENS_H.exists(), reason="C++ 源码不在当前检出")
def test_python_peak_detector_definition_matches_cpp():
    """Python 路径必须按同一套检测器定义（不是只对齐相对门那一个数字）。"""
    if str(DESKTOP) not in sys.path:
        sys.path.insert(0, str(DESKTOP))
    import spectrum_widget as sw

    found = _tokens_values()
    for py_name, cpp_name in PY_PEAK_DEFINITION.items():
        assert hasattr(sw, py_name), (
            f"spectrum_widget.py 未定义 {py_name}：检测器定义没有落成常量")
        assert float(getattr(sw, py_name)) == pytest.approx(found[cpp_name]), (
            f"{py_name}={getattr(sw, py_name)} 与 C++ {cpp_name}="
            f"{found[cpp_name]} 不一致；两条路径的检测器定义必须同一套")
