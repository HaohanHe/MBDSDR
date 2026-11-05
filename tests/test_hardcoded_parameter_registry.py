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
# 3. 跨语言对拍：C++ 侧阈值（tokens.h）与文档记录一致
# ---------------------------------------------------------------------------
#: 文档 docs/hardcoded-parameters.md 里登记的 C++ 侧值。任何一侧改了，
#: 先更新文档与这张表，再改代码。
CPP_EXPECTED = {
    "kDbSpinLowerMin": -140,
    "kDbSpinLowerMax": -20,
    "kDbSpinUpperMin": -40,
    "kDbSpinUpperMax": 20,
    "kDbLowerDefault": -45,
    "kDbUpperDefault": 0,
    "kPeakThresholdDefault": 15.0,
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
        assert name in found, f"tokens.h 里找不到 {name}（被改名或移动了？）"
        assert found[name] == pytest.approx(expected), (
            f"{name} 已从 {expected} 变成 {found[name]}；"
            "跨语言参数表 docs/hardcoded-parameters.md 需同步评审")


#: 已知且**有意**的跨语言差异（不是 bug，但必须登记）。若某天两侧统一了，
#: 删掉对应行即可——测试会提醒。
CROSS_LANGUAGE_DELTAS = {
    # 语义                    Python               C++
    "dB 轴下限": ("-160.0", "-140"),
    "dB 轴上限": ("0.0", "20"),
    "dB 默认下限": ("-100.0", "-45"),
    "FFT 档位数量": ("5", "3"),
}


def test_cross_language_deltas_are_documented():
    """差异本身不报错，但必须在文档里登记（防止"默默统一成某一个值"）。"""
    doc = REPO / "docs" / "hardcoded-parameters.md"
    if not doc.exists():
        pytest.skip("参数普查文档尚未生成")
    text = doc.read_text(encoding="utf-8")
    for semantic in CROSS_LANGUAGE_DELTAS:
        assert semantic in text, (
            f"跨语言差异「{semantic}」未在 docs/hardcoded-parameters.md 登记")
