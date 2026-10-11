# SPDX-License-Identifier: MIT
"""nanovna_dump replay 测试：自造协议脚本，不抄 GPL。"""
import json
import os

from mbdsdr_ai.nanovna_dump import run_dump


def _script():
    freqs = [430_000_000 + i * 100_000 for i in range(11)]
    data0 = ["0.5 0.0"] * 11
    data1 = ["0.1 -0.02"] * 11
    return [
        ["help", []],
        ["version", ["1.0.174-hugen"]],
        ["info", ["NanoVNA-H"]],
        ["cal", ["load open short thru cal'ed"]],
        ["sweep 430000000 431000000 11", []],
        ["frequencies", [str(f) for f in freqs]],
        ["data 0", data0],
        ["data 1", data1],
    ]


def test_replay_dump_shape(tmp_path):
    p = tmp_path / "script.json"
    p.write_text(json.dumps(_script()), encoding="utf-8")
    out = run_dump("", 430_000_000, 431_000_000, 11, replay=str(p))
    assert out["model"] == "NanoVNA-H"
    assert out["version"] == "1.0.174-hugen"
    assert len(out["frequencies_hz"]) == 11
    assert len(out["S11"]) == 11 and len(out["S21"]) == 11
    assert len(out["vswr"]) == 11
    # |Gamma|=0.5 -> VSWR = (1+0.5)/(1-0.5) = 3.0
    assert abs(out["vswr"][0] - 3.0) < 0.05


def test_replay_cal_recorded(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps(_script()), encoding="utf-8")
    out = run_dump("", 430_000_000, 431_000_000, 11, replay=str(p))
    assert any("cal" in c for c in out["cal"])


def test_nonexistent_port_fails_cleanly():
    import pytest
    with pytest.raises(SystemExit):
        run_dump("/dev/definitely-not-a-real-tty-1234",
                 430_000_000, 431_000_000, 11, replay=None)


def test_selftest_runs(tmp_path):
    from mbdsdr_ai import nanovna_dump as nd
    script = nd._selftest_script(430_000_000, 431_000_000, 11)
    p = tmp_path / "s.json"
    p.write_text(json.dumps(script), encoding="utf-8")
    out = run_dump("", 430_000_000, 431_000_000, 11, replay=str(p))
    assert out["model"] == "NanoVNA-H(selftest)"
    assert len(out["vswr"]) == 11


def test_json_pretty_parseable(tmp_path):
    from mbdsdr_ai import nanovna_dump as nd
    import io, contextlib
    rc = nd.main(["--selftest", "--points", "11", "--json-pretty"])
    assert rc == 0
