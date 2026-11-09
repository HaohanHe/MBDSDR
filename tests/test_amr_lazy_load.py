# SPDX-License-Identifier: MIT
"""AMR 内置模板：懒加载 + 特征缓存。

2026-11-09 实测背景：``AMRClassifier.__init__`` 原来同步合成 8 类 × 16 条
= 128 段 IQ 并提 25 维特征，主窗口冷启 8.19 s（cProfile）里有 6.9 s 花在这里
（``numpy.correlate`` 3.2 s + 特征生成器 3.2 s）；而 AMR 只在用户真问
「这是什么调制」时才用得上。改懒加载 + 特征落盘后冷启 6.36 s → 2.80 s。

本文件钉住四件事，任何一条回退都要红：

1. 构造**不得**加载模板（否则启动又被拖慢）；
2. 四个公开入口都会触发加载（否则第一次分类拿到空模板）；
3. 缓存与真算的特征**完全相等**（Δ == 0，不是近似——缓存不能改变结论）；
4. 缓存版本不符/文件损坏时**回退重算而不是崩**或静默用错模板。
"""

from __future__ import annotations

import os
import time

import numpy as np
import pytest

from mbdsdr_ai import amr


@pytest.fixture()
def cache_dir(tmp_path, monkeypatch):
    """把模板缓存隔离到临时目录（不碰真实 ~/.mbdsdr）。"""
    path = tmp_path / "cache"
    monkeypatch.setenv("MBDSDR_CACHE_DIR", str(path))
    return path


@pytest.fixture(scope="module")
def warm_cache(tmp_path_factory):
    """算一份模板写进缓存，供「读缓存」类用例复用，避免每例都付 3 s。"""
    path = tmp_path_factory.mktemp("warm") / "cache"
    old = os.environ.get("MBDSDR_CACHE_DIR")
    os.environ["MBDSDR_CACHE_DIR"] = str(path)
    try:
        classifier = amr.AMRClassifier()
        classifier.get_stats()          # 触发真算 + 落盘
        assert (path / "amr_builtin_features.npz").exists()
        yield path
    finally:
        if old is None:
            os.environ.pop("MBDSDR_CACHE_DIR", None)
        else:
            os.environ["MBDSDR_CACHE_DIR"] = old


# ------------------------------------------------------------------ 懒加载


def test_construction_does_not_load_templates():
    """构造必须是纯赋值：不合成、不提特征。"""
    t0 = time.perf_counter()
    classifier = amr.AMRClassifier(k=5)
    elapsed = time.perf_counter() - t0
    assert classifier.builtin_loaded is False, "构造时不该加载内置模板"
    assert classifier.classifier.samples == []
    # 3 s 的真算与 0 ms 的赋值差三个数量级，200 ms 足够区分且不受机器抖动影响
    assert elapsed < 0.2, f"构造耗时 {elapsed:.2f}s，模板又被同步加载了"


@pytest.mark.parametrize("entry", ["get_stats", "classify", "classify_iq",
                                  "add_training_sample"])
def test_every_public_entry_triggers_load(entry, warm_cache, monkeypatch):
    """四个公开入口都必须自带加载，否则第一次调用拿到空模板静默错判。"""
    monkeypatch.setenv("MBDSDR_CACHE_DIR", str(warm_cache))
    classifier = amr.AMRClassifier()
    assert classifier.builtin_loaded is False
    if entry == "get_stats":
        classifier.get_stats()
    elif entry == "classify":
        classifier.classify(amr.AMRFeature())
    elif entry == "classify_iq":
        classifier.classify_iq([1 + 0j] * 64, 1000.0)
    else:
        classifier.add_training_sample([1 + 0j] * 64, amr.ModulationType.AM, 1000.0)
    assert classifier.builtin_loaded is True, f"{entry} 没有触发内置模板加载"
    assert len(classifier.classifier.samples) >= 128


def test_loaded_once_only(warm_cache, monkeypatch):
    """重复调用不得重复加载（否则样本翻倍、置信度全变 1.0）。"""
    monkeypatch.setenv("MBDSDR_CACHE_DIR", str(warm_cache))
    classifier = amr.AMRClassifier()
    classifier.get_stats()
    n = len(classifier.classifier.samples)
    for _ in range(3):
        classifier.get_stats()
        classifier.classify_iq([1 + 0j] * 64, 1000.0)
    assert len(classifier.classifier.samples) == n


# ------------------------------------------------------------------ 缓存等价


def test_cache_matches_recompute_exactly(cache_dir):
    """缓存必须与真算**逐位相等**：缓存改变结论就是静默错判。"""
    fresh = amr.AMRClassifier()
    fresh._load_builtin_training_data()      # 绕过缓存，真算
    fresh._save_cached_builtin()

    cached = amr.AMRClassifier()
    assert cached._load_cached_builtin() is True

    a = np.array([s.feature.to_list() for s in fresh.classifier.samples])
    b = np.array([s.feature.to_list() for s in cached.classifier.samples])
    assert [s.label.value for s in fresh.classifier.samples] == \
           [s.label.value for s in cached.classifier.samples]
    assert a.shape == b.shape
    assert float(np.max(np.abs(a - b))) == 0.0, "缓存特征与真算有差异"

    # 端到端：同一段信号两边分类结论与置信度都要一致
    rng = np.random.default_rng(20261109)
    for mod in amr.AMRClassifier.BUILTIN_MODULATIONS:
        iq = list(amr.synthesize_modulation_iq(mod, rng, snr_db=20.0, fs=100_000.0))
        r1 = fresh.classify_iq(iq, 100_000.0)
        r2 = cached.classify_iq(iq, 100_000.0)
        assert r1.predicted_modulation == r2.predicted_modulation, f"{mod} 两边结论不同"
        assert abs(r1.confidence - r2.confidence) < 1e-12, f"{mod} 两边置信度不同"


def test_cached_load_is_fast(warm_cache, monkeypatch):
    """第二次起读缓存，不该再付 3 s。"""
    monkeypatch.setenv("MBDSDR_CACHE_DIR", str(warm_cache))
    classifier = amr.AMRClassifier()
    t0 = time.perf_counter()
    classifier.get_stats()
    elapsed = time.perf_counter() - t0
    assert classifier.builtin_loaded is True
    assert len(classifier.classifier.samples) >= 128
    assert elapsed < 0.5, f"读缓存耗时 {elapsed:.2f}s，可能没命中缓存"


# ------------------------------------------------------------------ 缓存失效


def test_stale_version_falls_back_to_recompute(warm_cache, monkeypatch):
    """缓存版本不符必须重算，不能拿旧特征定义继续用。"""
    monkeypatch.setenv("MBDSDR_CACHE_DIR", str(warm_cache))
    path = warm_cache / "amr_builtin_features.npz"
    with np.load(path, allow_pickle=False) as data:
        labels = data["labels"]
        features = data["features"]
    np.savez_compressed(path, version=np.int64(amr._BUILTIN_CACHE_VERSION + 99),
                        labels=labels, features=features)

    classifier = amr.AMRClassifier()
    assert classifier._load_cached_builtin() is False, "版本不符必须拒绝缓存"
    # _ensure_loaded 会接着真算，样本数仍要对
    classifier.get_stats()
    assert len(classifier.classifier.samples) >= 128


def test_corrupt_cache_does_not_crash(cache_dir):
    """缓存文件损坏要能回退重算，不能把启动/分类搞崩。"""
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "amr_builtin_features.npz").write_bytes(b"not a real npz at all")

    classifier = amr.AMRClassifier()
    assert classifier._load_cached_builtin() is False
    stats = classifier.get_stats()
    assert stats["total_samples"] >= 128


def test_shape_mismatch_rejected(cache_dir):
    """字段数对不上（例如 AMRFeature 改了维度）必须拒绝缓存。"""
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / "amr_builtin_features.npz"
    np.savez_compressed(path, version=np.int64(amr._BUILTIN_CACHE_VERSION),
                        labels=np.array(["AM"], dtype="U16"),
                        features=np.zeros((1, 3), dtype=np.float64))
    classifier = amr.AMRClassifier()
    assert classifier._load_cached_builtin() is False


def test_no_cache_dir_means_no_write(monkeypatch, tmp_path):
    """取不到缓存目录时安静跳过（不缓存、不抛异常）。"""
    monkeypatch.delenv("MBDSDR_CACHE_DIR", raising=False)
    monkeypatch.setattr(amr.os.path, "expanduser", lambda p: "~")
    assert amr.builtin_cache_path() is None
    classifier = amr.AMRClassifier()
    classifier._save_cached_builtin()       # 不该抛
