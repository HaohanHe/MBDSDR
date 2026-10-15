# SPDX-License-Identifier: MIT
"""vna_calio 测试：误差盒/定义件落盘往返。"""
import json

import numpy as np
import pytest

from mbdsdr_ai.vna_cal import (ErrorBox, LoadStandard, OpenStandard,
                               ShortStandard, apply_calibration)
from mbdsdr_ai.vna_calio import (load_cal_kit, load_error_box,
                                 save_cal_kit, save_error_box)

F = np.linspace(1e6, 3e9, 201)


def _box():
    rng = np.random.default_rng(7)
    e00 = 0.05 + 0.02j + 1e-3 * rng.standard_normal(len(F)) * (1 + 1j)
    t = 0.97 - 0.01j + 1e-3 * rng.standard_normal(len(F)) * (1 + 1j)
    e11 = 0.1 - 0.04j + 1e-3 * rng.standard_normal(len(F)) * (1 + 1j)
    return ErrorBox(e00=e00, t=t, e11=e11)


def test_error_box_roundtrip(tmp_path):
    p = tmp_path / "box.json"
    save_error_box(str(p), F, _box())
    f2, box2 = load_error_box(str(p))
    assert np.allclose(f2, F)
    assert np.allclose(box2.e00, _box().e00)
    assert np.allclose(box2.t, _box().t)
    assert np.allclose(box2.e11, _box().e11)


def test_loaded_box_calibrates_identically(tmp_path):
    """落盘往返后 apply_calibration 逐点一致（复数 1e-12）。"""
    box = _box()
    p = tmp_path / "box.json"
    save_error_box(str(p), F, box)
    _, box2 = load_error_box(str(p))
    gm = 0.3 * np.exp(1j * np.linspace(0, 4, len(F)))
    assert np.allclose(apply_calibration(gm, box),
                       apply_calibration(gm, box2), atol=1e-12)


def test_box_length_mismatch_honest(tmp_path):
    box = _box()
    box2 = ErrorBox(e00=box.e00[:-1], t=box.t, e11=box.e11)
    with pytest.raises(ValueError):
        save_error_box(str(tmp_path / "x.json"), F, box2)


def test_load_bad_version_and_format(tmp_path):
    p = tmp_path / "box.json"
    save_error_box(str(p), F, _box())
    doc = json.loads(p.read_text(encoding="utf-8"))
    doc["schema_version"] = 99
    p.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError):
        load_error_box(str(p))
    doc = json.loads(p.read_text(encoding="utf-8"))
    doc["format"] = "something.else"
    p.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError):
        load_error_box(str(p))


def test_load_missing_file_honest(tmp_path):
    with pytest.raises(OSError):
        load_error_box(str(tmp_path / "nope.json"))


def _kit():
    return (OpenStandard(c0_pF=2.2, c1=0.3, offset_len_m=0.008),
            ShortStandard(l0_nH=1.2, offset_len_m=0.008),
            LoadStandard(r_ohm=49.9, l_nH=0.05))


def test_cal_kit_roundtrip(tmp_path):
    p = tmp_path / "kit.json"
    save_cal_kit(str(p), *_kit())
    o, s, l = load_cal_kit(str(p))
    assert o.c0_pF == pytest.approx(2.2) and o.offset_len_m == pytest.approx(0.008)
    assert s.l0_nH == pytest.approx(1.2)
    assert l.r_ohm == pytest.approx(49.9)
    # 定义件 Γ 逐点一致
    o0, s0, l0 = _kit()
    for a, b in ((o, o0), (s, s0), (l, l0)):
        assert np.allclose(a.gamma(F), b.gamma(F))


def test_cal_kit_corrupt_honest(tmp_path):
    p = tmp_path / "kit.json"
    save_cal_kit(str(p), *_kit())
    doc = json.loads(p.read_text(encoding="utf-8"))
    del doc["open"]["c0_pF"]
    doc["open"]["bogus_field"] = 1
    p.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError):
        load_cal_kit(str(p))
