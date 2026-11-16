# SPDX-License-Identifier: MIT
"""AMR 相位特征：圆均值 / 圆标准差（跨 ±π 回绕正确性）。

2026-11-15 #107 背景：``extract_features_from_iq`` 原来用
``mean_phase = sum(phases)/n`` 线性平均相位、``std_phase`` 围绕线性均值求
RMS。相位是**圆周量**，线性平均在 ±π 回绕处错：+135°/-135° 两样本线性
mean_phase=0°（真值 180°）、std_phase=135°（真值 45°）。这与同仓
``signal_quality.py`` 已确立的圆均值口径不一致（同信号给 180° 正确）。

真实消费方：注册 AI 工具 ``amr_extract_features`` → ``AMRFeature.to_dict()``
把 mean_phase/std_phase 暴露给用户，且这两维是 KNN 分类特征——线性平均
会让跨回绕信号的特征停错值，静默错判。

本文件钉住：
1. 跨 ±π 回绕的 mean_phase / std_phase 取圆均值 / 圆标准差（多组态）；
2. 与独立 numpy 复指数参考逐值相等（不同实现路径，非自证）；
3. 与 signal_quality.py 的 mean_phase_deg 跨模块一致（既有正确口径）；
4. 同相位 identity（mean=该相位, std=0）+ 非回绕小展宽圆==线性（防过度修正）；
5. 空输入不崩；to_dict 用户可见读数正确；
6. 算法改了 → _BUILTIN_CACHE_VERSION 必须 >= 2（amr.py:45-49 强制不变量，
   否则旧 v1 缓存静默错判）。

全部用固定 IQ，不绑墙钟 / 不联网。缓存隔离到 tmp（不碰真实 ~/.mbdsdr，
也不付 3 s 模板合成——extract_features_from_iq 不触发模板加载）。

运行：python -m pytest tests/test_amr_circular_phase.py -q
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from mbdsdr_ai import amr
from mbdsdr_ai.signal_quality import signal_quality


@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path, monkeypatch):
    """把模板缓存隔离到临时目录；本文件只提特征，不触发模板合成。"""
    monkeypatch.setenv("MBDSDR_CACHE_DIR", str(tmp_path / "cache"))


@pytest.fixture()
def clf():
    return amr.AMRClassifier()


# ─────────────────────────── 独立参考（numpy 复指数路径） ───────────────────────────

def _iq_from_deg(deg_list):
    return [complex(math.cos(math.radians(d)), math.sin(math.radians(d))) for d in deg_list]


def _ref_circular_mean_rad(deg_list):
    """独立圆均值：复指数均值的辐角（与 amr.py 的 Σsin/Σcos 不同实现路径）。"""
    ph = np.radians(np.asarray(deg_list, dtype=float))
    return float(np.angle(np.mean(np.exp(1j * ph))))


def _ref_circular_std_rad(deg_list):
    """独立圆标准差：偏差用 np.angle(exp(j·)) 回绕（与 amr.py 的 modulo 不同路径）。"""
    ph = np.radians(np.asarray(deg_list, dtype=float))
    m = _ref_circular_mean_rad(deg_list)
    dev = np.angle(np.exp(1j * (ph - m)))
    return float(np.sqrt(np.mean(dev ** 2)))


def _circ_dist_deg(a, b):
    """两角度（度）的最小弧距。"""
    return abs((a - b + 180.0) % 360.0 - 180.0)


# ─────────────────────────── 1. 跨回绕正确性 ───────────────────────────

@pytest.mark.parametrize("degs,expect_mean_deg,expect_std_deg", [
    ([135.0, -135.0], 180.0, 45.0),
    ([170.0, -170.0], 180.0, 10.0),
    ([179.0, -179.0], 180.0, 1.0),
    ([100.0, -100.0], 180.0, 80.0),
    ([91.0, -91.0], 180.0, 89.0),
])
def test_mean_phase_wrap(clf, degs, expect_mean_deg, expect_std_deg):
    f = clf.extract_features_from_iq(_iq_from_deg(degs), 1.0)
    got_mean = math.degrees(f.mean_phase)
    assert _circ_dist_deg(got_mean, expect_mean_deg) < 1e-6, \
        f"{degs}: mean_phase={got_mean}° 应 {expect_mean_deg}°"


@pytest.mark.parametrize("degs,expect_std_deg", [
    ([135.0, -135.0], 45.0),
    ([170.0, -170.0], 10.0),
    ([179.0, -179.0], 1.0),
    ([100.0, -100.0], 80.0),
])
def test_std_phase_wrap(clf, degs, expect_std_deg):
    f = clf.extract_features_from_iq(_iq_from_deg(degs), 1.0)
    got_std = math.degrees(f.std_phase)
    assert abs(got_std - expect_std_deg) < 1e-6, \
        f"{degs}: std_phase={got_std}° 应 {expect_std_deg}°"


def test_wrap_mean_not_zero(clf):
    """显式钉死旧缺陷：+135/-135 线性平均会得 0°，圆均值绝不能是 0°。"""
    f = clf.extract_features_from_iq(_iq_from_deg([135.0, -135.0]), 1.0)
    assert _circ_dist_deg(math.degrees(f.mean_phase), 0.0) > 170.0


# ─────────────────────────── 2. 独立参考逐值相等 ───────────────────────────

@pytest.mark.parametrize("degs", [
    [135.0, -135.0],
    [170.0, -170.0, 180.0],
    [10.0, 20.0, 30.0],
    [150.0, -150.0, 180.0],
    [-179.0, 179.0, 178.0, -178.0],
    [45.0, 45.0, 45.0, 45.0],
])
def test_matches_independent_circular_reference(clf, degs):
    f = clf.extract_features_from_iq(_iq_from_deg(degs), 1.0)
    ref_mean = _ref_circular_mean_rad(degs)
    ref_std = _ref_circular_std_rad(degs)
    assert _circ_dist_deg(math.degrees(f.mean_phase), math.degrees(ref_mean)) < 1e-6
    assert abs(f.std_phase - ref_std) < 1e-9


# ─────────────────────────── 3. 跨模块一致（signal_quality 既有口径） ───────────────────────────

@pytest.mark.parametrize("degs", [
    [135.0, -135.0],
    [170.0, -170.0],
    [100.0, -100.0],
    [10.0, 20.0, 30.0],
    [150.0, -150.0, 180.0],
])
def test_consistent_with_signal_quality(clf, degs):
    iq = _iq_from_deg(degs)
    f = clf.extract_features_from_iq(iq, 1.0)
    sq = signal_quality(np.array(iq, dtype=complex))
    assert _circ_dist_deg(math.degrees(f.mean_phase), sq["mean_phase_deg"]) < 1e-6, \
        f"{degs}: amr={math.degrees(f.mean_phase)}° signal_quality={sq['mean_phase_deg']}°"


# ─────────────────────────── 4. identity + 非回绕等价 ───────────────────────────

@pytest.mark.parametrize("d", [0.0, 90.0, -90.0, 179.0, -179.0, 45.0, -135.0])
def test_co_located_identity(clf, d):
    f = clf.extract_features_from_iq(_iq_from_deg([d] * 8), 1.0)
    assert _circ_dist_deg(math.degrees(f.mean_phase), d) < 1e-6
    assert abs(f.std_phase) < 1e-9


@pytest.mark.parametrize("degs", [
    [10.0, 20.0, 30.0],
    [-30.0, -10.0, 10.0],
    [0.0, 1.0, 2.0, 3.0],
])
def test_non_wrap_small_spread_equals_linear(clf, degs):
    """小展宽（不跨回绕）圆均值/圆std 必须退化为线性——防过度修正。"""
    f = clf.extract_features_from_iq(_iq_from_deg(degs), 1.0)
    ph = [math.radians(d) for d in degs]
    lin_mean = sum(ph) / len(ph)
    lin_std = math.sqrt(sum((p - lin_mean) ** 2 for p in ph) / len(ph))
    assert abs(f.mean_phase - lin_mean) < 1e-9
    assert abs(f.std_phase - lin_std) < 1e-9


# ─────────────────────────── 5. 边界 + 用户可见读数 ───────────────────────────

def test_empty_input_no_crash(clf):
    f = clf.extract_features_from_iq([], 1.0)
    assert f.mean_phase == 0.0
    assert f.std_phase == 0.0


def test_single_sample(clf):
    f = clf.extract_features_from_iq(_iq_from_deg([135.0]), 1.0)
    assert _circ_dist_deg(math.degrees(f.mean_phase), 135.0) < 1e-6
    assert f.std_phase == 0.0  # n==1 → std=0


def test_degenerate_uniform_spread_is_finite(clf):
    """均匀铺满圆周（合矢量≈0）时圆均值数学上无定义：只钉「有限、不崩、
    落在 [-π,π]」，不钉具体角度（任何实现都只能返回浮点残差方向，断言
    具体值等于在测噪声而非正确性）。"""
    f = clf.extract_features_from_iq(_iq_from_deg([0.0, 90.0, 180.0, -90.0]), 1.0)
    assert math.isfinite(f.mean_phase)
    assert -math.pi - 1e-9 <= f.mean_phase <= math.pi + 1e-9
    assert math.isfinite(f.std_phase) and f.std_phase >= 0.0


def test_to_dict_exposes_circular_phase(clf):
    """注册 AI 工具 amr_extract_features 经 to_dict 暴露的读数（弧度，4 位小数）。"""
    f = clf.extract_features_from_iq(_iq_from_deg([135.0, -135.0]), 1.0)
    d = f.to_dict()
    assert abs(abs(d["mean_phase"]) - round(math.pi, 4)) < 1e-4
    assert abs(d["std_phase"] - round(math.pi / 4, 4)) < 1e-4


def test_feature_list_phase_slots(clf):
    """to_list 的 mean_phase/std_phase 槽位（第 21/22 维，0-based 20/21）取圆值。"""
    f = clf.extract_features_from_iq(_iq_from_deg([135.0, -135.0]), 1.0)
    lst = f.to_list()
    assert len(lst) == 25
    assert abs(abs(lst[20]) - math.pi) < 1e-9      # mean_phase
    assert abs(lst[21] - math.pi / 4) < 1e-9       # std_phase


# ─────────────────────────── 6. 缓存版本不变量 ───────────────────────────

def test_cache_version_bumped_for_algorithm_change():
    """相位算法改了 → 缓存版本必须 >= 2（amr.py:45-49），否则旧 v1 缓存静默错判。"""
    assert amr._BUILTIN_CACHE_VERSION >= 2, \
        "extract_features_from_iq 相位算法已改，_BUILTIN_CACHE_VERSION 必须 >= 2"