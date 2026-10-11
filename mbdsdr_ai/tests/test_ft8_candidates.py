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


def test_csv_rows():
    from mbdsdr_ai.ft8_decode import candidates_to_csv
    ds = [(7.074e6, {"crc_ok": True, "costas_score": 5.0, "iters": 20})]
    csv = candidates_to_csv(collect_ft8_candidates(ds))
    lines = csv.strip().splitlines()
    assert lines[0].startswith("freq_hz")
    assert "7074000.0" in lines[1]
    # 空态：仅表头
    empty = candidates_to_csv(collect_ft8_candidates([]))
    assert empty.strip().splitlines() == ["freq_hz,crc_ok,costas_score,rejected,reject_reason,iters"]
