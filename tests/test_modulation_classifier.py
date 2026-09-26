"""ModulationClassifier 单测。

合成 AM/FM/ASK/OOK/FSK/PSK 信号，验证分类器准确率 > 90%。
对照 URH ``ainterpretation/AutoInterpretation.py::detect_modulation``。
"""
import numpy as np
import pytest

from mbdsdr_ai.analysis.modulation_classifier import ModulationClassifier


SR = 1_000_000.0
FC = 50_000.0


def _make_t_array(n=20000):
    return np.arange(n) / SR


def _make_am(t, m=0.5, fm=1000.0, seed=0):
    rng = np.random.RandomState(seed)
    return (1.0 + m * np.sin(2 * np.pi * fm * t + rng.uniform(0, 1))) * np.exp(1j * 2 * np.pi * FC * t)


def _make_fm(t, beta=2.0, fm=2000.0, seed=0):
    return np.exp(1j * (2 * np.pi * FC * t + beta * np.cos(2 * np.pi * fm * t + seed)))


def _make_ook(t, nbits=20, seed=0):
    rng = np.random.RandomState(seed)
    bits = rng.randint(0, 2, nbits)
    spb = len(t) // nbits
    out = np.zeros(len(t), dtype=complex)
    for i, b in enumerate(bits):
        sl = slice(i * spb, (i + 1) * spb)
        out[sl] = b * np.exp(1j * 2 * np.pi * FC * t[sl])
    return out, bits


def _make_fsk(t, nbits=20, seed=0, df=30_000.0):
    rng = np.random.RandomState(seed)
    bits = rng.randint(0, 2, nbits)
    spb = len(t) // nbits
    out = np.zeros(len(t), dtype=complex)
    for i, b in enumerate(bits):
        f = FC + (df if b else -df)
        sl = slice(i * spb, (i + 1) * spb)
        out[sl] = np.exp(1j * 2 * np.pi * f * t[sl])
    return out, bits


def _make_bpsk(t, nbits=20, seed=0):
    rng = np.random.RandomState(seed)
    bits = rng.randint(0, 2, nbits)
    spb = len(t) // nbits
    phase = np.pi * np.repeat(bits, spb)
    return np.exp(1j * (2 * np.pi * FC * t + phase)), bits


@pytest.mark.parametrize("seed", range(5))
def test_classify_am(seed):
    t = _make_t_array()
    clf = ModulationClassifier()
    r = clf.classify(_make_am(t, seed=seed), SR)
    assert r.modulation == "AM", f"seed={seed}: expected AM, got {r.modulation} conf={r.confidence}"
    assert r.confidence > 0.5


@pytest.mark.parametrize("seed", range(5))
def test_classify_fm(seed):
    t = _make_t_array()
    clf = ModulationClassifier()
    r = clf.classify(_make_fm(t, seed=seed), SR)
    assert r.modulation == "FM", f"seed={seed}: expected FM, got {r.modulation} conf={r.confidence}"
    assert r.confidence > 0.5


@pytest.mark.parametrize("seed", range(5))
def test_classify_ook(seed):
    t = _make_t_array()
    clf = ModulationClassifier()
    sig, _ = _make_ook(t, seed=seed)
    r = clf.classify(sig, SR)
    assert r.modulation == "OOK", f"seed={seed}: expected OOK, got {r.modulation} conf={r.confidence}"
    assert r.confidence > 0.5


@pytest.mark.parametrize("seed", range(5))
def test_classify_fsk(seed):
    t = _make_t_array()
    clf = ModulationClassifier()
    sig, _ = _make_fsk(t, seed=seed)
    r = clf.classify(sig, SR)
    assert r.modulation == "FSK", f"seed={seed}: expected FSK, got {r.modulation} conf={r.confidence}"
    assert r.confidence > 0.5


@pytest.mark.parametrize("seed", range(5))
def test_classify_bpsk(seed):
    t = _make_t_array()
    clf = ModulationClassifier()
    sig, _ = _make_bpsk(t, seed=seed)
    r = clf.classify(sig, SR)
    assert r.modulation == "PSK", f"seed={seed}: expected PSK, got {r.modulation} conf={r.confidence}"
    assert r.confidence > 0.5


def test_overall_accuracy_above_90pct():
    """综合准确率：5 类 × 5 seed = 25 次，正确 >= 23（92%）。"""
    t = _make_t_array()
    clf = ModulationClassifier()
    correct = 0
    total = 0
    for seed in range(5):
        for expected, maker in [
            ("AM", lambda s: _make_am(t, seed=s)),
            ("FM", lambda s: _make_fm(t, seed=s)),
        ]:
            r = clf.classify(maker(seed), SR)
            correct += int(r.modulation == expected)
            total += 1
        for expected, maker in [("OOK", _make_ook), ("FSK", _make_fsk), ("PSK", _make_bpsk)]:
            sig, _ = maker(t, seed=seed)
            r = clf.classify(sig, SR)
            correct += int(r.modulation == expected)
            total += 1
    acc = correct / total
    assert acc >= 0.9, f"准确率 {correct}/{total} = {acc:.1%}，应 >= 90%"


def test_noise_detection():
    """纯噪声应判为 NOISE。"""
    rng = np.random.RandomState(42)
    noise = (rng.randn(2000) + 1j * rng.randn(2000)) * 0.001
    clf = ModulationClassifier(noise_energy_floor=1e-4)
    r = clf.classify(noise, SR)
    assert r.modulation == "NOISE"


def test_confidence_in_range():
    t = _make_t_array()
    clf = ModulationClassifier()
    r = clf.classify(_make_fsk(t)[0], SR)
    assert 0.0 <= r.confidence <= 1.0
    assert r.suggestions  # 应有解调建议


def test_center_freq_estimate():
    t = _make_t_array()
    clf = ModulationClassifier()
    sig = _make_am(t)
    r = clf.classify(sig, SR)
    # 中心频率应接近 FC=50kHz（±10kHz 容差）
    assert abs(r.center_freq - FC) < 10_000
