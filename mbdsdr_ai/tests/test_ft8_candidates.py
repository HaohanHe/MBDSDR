# SPDX-License-Identifier: MIT
from mbdsdr_ai.ft8_decode import collect_ft8_candidates


def test_candidates_sorted():
    ds = [
        (7.074e6, {"crc_ok": False, "costas_score": 1.0}),
        (7.080e6, {"crc_ok": True, "costas_score": 5.0}),
        (7.077e6, {"crc_ok": True, "costas_score": 9.0}),
    ]
    out = collect_ft8_candidates(ds)
    assert out[0]["freq_hz"] == 7.077e6  # crc_ok & highest score first
    assert out[0]["crc_ok"] is True
    assert "reject_reason" in out[0]
