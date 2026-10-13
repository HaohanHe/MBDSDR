# SPDX-License-Identifier: MIT
"""nanovna_dump replay 测试：自造协议脚本，不抄 GPL。"""
import json
import os

from mbdsdr_ai.nanovna_client import (
    CAPTURE_FRAME_BYTES,
    CAPTURE_HEIGHT,
    CAPTURE_WIDTH,
    NanoVNADriverError,
    NanoVNAClient,
    ReplayTransport,
    rgb565_to_bmp_bytes,
)
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
        ["tcxo", ["usage: tcxo {TCXO frequency(Hz)}", "current: 26000000"]],
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


def test_summary_min_vswr(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps(_script()), encoding="utf-8")
    out = run_dump("", 430_000_000, 431_000_000, 11, replay=str(p))
    assert "summary" in out
    assert out["summary"]["min_vswr"] == 3.0  # |gamma|=0.5 -> vswr 3
    assert out["summary"]["at_hz"] == 430_000_000


# ---------------------------------------------------------------------------
# tcxo / capture 适配（2026-10 真机审计补缺）
# ---------------------------------------------------------------------------

def test_run_dump_tcxo_field(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps(_script()), encoding="utf-8")
    out = run_dump("", 430_000_000, 431_000_000, 11, replay=str(p))
    assert out["tcxo_hz"] == 26000000


def test_read_tcxo_unsupported_is_none():
    """老固件无 tcxo 命令（回显 usage 行无 current:）-> None，不抛不编。"""
    script = [["tcxo", ["usage: tcxo {TCXO frequency(Hz)}"]]]
    c = NanoVNAClient(transport=ReplayTransport(script))
    c.connect(do_handshake=False)
    assert c.read_tcxo() is None


class _BinaryReplayTransport:
    """二进制夹具传输层：喂 capture 的完整回包（回显行+帧+提示符）。"""

    def __init__(self, stream: bytes):
        self._buf = bytearray(stream)
        self.writes = []
        self.is_open = True

    def open(self):
        self.is_open = True

    def close(self):
        self.is_open = False

    def write(self, data: bytes):
        self.writes.append(data)

    def readline(self) -> bytes:
        idx = self._buf.find(b"\n")
        if idx < 0:
            line = bytes(self._buf)
            self._buf.clear()
            return line
        line = bytes(self._buf[: idx + 1])
        del self._buf[: idx + 1]
        return line

    def read_bytes(self, n: int) -> bytes:
        chunk = bytes(self._buf[:n])
        del self._buf[:n]
        return chunk

    def reset_input_buffer(self):
        # 夹具不清流：reset 语义是冲刷命令前的残留字节，罐头流是既定回包
        pass


def _capture_stream(frame: bytes) -> bytes:
    return b"capture\r\n" + frame + b"ch> "


def test_capture_screen_reads_exact_frame():
    frame = bytes(range(256)) * (CAPTURE_FRAME_BYTES // 256)
    assert len(frame) == CAPTURE_FRAME_BYTES
    tr = _BinaryReplayTransport(_capture_stream(frame))
    c = NanoVNAClient(transport=tr)
    c.connect(do_handshake=False)
    raw = c.capture_screen(timeout_s=1.0)
    assert len(raw) == CAPTURE_FRAME_BYTES
    assert raw[:4] == b"\x00\x01\x02\x03"
    assert tr.writes == [b"capture\r"]


def test_capture_screen_timeout_is_honest():
    tr = _BinaryReplayTransport(b"capture\r\n" + b"\x00" * 100)
    c = NanoVNAClient(transport=tr)
    c.connect(do_handshake=False)
    try:
        c.capture_screen(timeout_s=0.3)
        assert False, "短帧必须诚实报错，不得返回半帧"
    except NanoVNADriverError:
        pass


def test_rgb565_to_bmp_conversion():
    # 纯红 0xF800：BGR 输出应为 (0,0,255)
    frame = bytearray(CAPTURE_FRAME_BYTES)
    frame[0] = 0x00
    frame[1] = 0xF8
    bmp = rgb565_to_bmp_bytes(bytes(frame), CAPTURE_WIDTH, CAPTURE_HEIGHT)
    assert bmp[:2] == b"BM"
    import struct
    declared = struct.unpack("<I", bmp[2:6])[0]
    assert declared == len(bmp)
    # 最后一行像素数据开头（BMP 自底向上；首行=x0 -> 末行=y239 的最后像素行）
    # 直接在像素区找红色三元组
    assert bytes((0, 0, 255)) in bmp
