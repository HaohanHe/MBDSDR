# SPDX-License-Identifier: MIT
"""nanovna_dump replay 测试：自造协议脚本，不抄 GPL。"""
import json
import math
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
    # 官方 11 种迹线格式之 PHASE 的 Python 侧接线（2026-10-13 半适配转正）
    assert len(out["s11_phase_deg"]) == 11
    assert len(out["s21_phase_deg"]) == 11
    # data1 = "0.1 -0.02" -> atan2(-0.02, 0.1)
    _expect = round(math.degrees(math.atan2(-0.02, 0.1)), 2)
    assert abs(out["s21_phase_deg"][0] - _expect) < 0.01
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
        # 注入零间隔重试预算，避免测试真睡 2.5s x N（重试逻辑由下方假传输层
        # 测试覆盖）；诚实失败语义保持不变
        run_dump("/dev/definitely-not-a-real-tty-1234",
                 430_000_000, 431_000_000, 11, replay=None,
                 open_retries=1, retry_delay_s=0.0)


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


# ---------------------------------------------------------------------------
# 真机路径：串口打开有界重试 + 失败诊断（usbser.sys 偶发 PermissionError(13)）
# ---------------------------------------------------------------------------

class _FlakyOpenTransport:
    """假传输层：按**全局尝试序号**前 fail_first 次 open() 抛
    PermissionError(13)（镜像 Windows usbser.sys 已知怪癖），之后委托
    ReplayTransport 正常回放协议脚本。

    只干扰 open 这一个动作，其余协议行为全部走真 ReplayTransport——
    验证的是真机路径的"重试 open"逻辑，不碰任何硬件。
    run_dump 每次尝试都会新建 client/传输层，故 open 计数经 counter
    注入跨实例共享，模拟"同一串口的多次打开尝试"。
    """

    def __init__(self, inner: ReplayTransport, fail_first: int,
                 counter: dict | None = None):
        self._inner = inner
        self._fail_first = fail_first
        self._counter = counter if counter is not None else {}
        self._counter.setdefault("open_calls", 0)

    def open(self) -> None:
        self._counter["open_calls"] += 1
        if self._counter["open_calls"] <= self._fail_first:
            raise PermissionError(
                13, f"模拟 usbser 拒绝 #{self._counter['open_calls']}")
        self._inner.open()

    def close(self) -> None:
        self._inner.close()

    @property
    def is_open(self) -> bool:
        return self._inner.is_open

    def write(self, data: bytes) -> None:
        self._inner.write(data)

    def readline(self) -> bytes:
        return self._inner.readline()

    def read_bytes(self, n: int) -> bytes:
        return self._inner.read_bytes(n)

    def reset_input_buffer(self) -> None:
        self._inner.reset_input_buffer()


def _install_flaky_real_path(monkeypatch, fail_first: int) -> dict:
    """把 nanovna_dump 的真机 client 构造换成"闪断 open + 回放底座"。

    候选端口枚举一律静默为空（不探测真机）；返回 dict：
    ``counter["open_calls"]`` 为累计 open 尝试次数，``transport`` 为最后
    一次尝试的假传输层。
    """
    import mbdsdr_ai.nanovna_dump as nd

    holder: dict = {"counter": {}}

    def fake_client(port=None, transport=None):
        tr = _FlakyOpenTransport(ReplayTransport(_script()),
                                 fail_first=fail_first,
                                 counter=holder["counter"])
        holder["transport"] = tr
        return NanoVNAClient(transport=tr)

    monkeypatch.setattr(nd, "NanoVNAClient", fake_client)
    monkeypatch.setattr(nd, "find_nanovna_ports", lambda: [])
    return holder


def test_real_path_open_retry_succeeds_on_last_default_attempt(monkeypatch):
    """默认预算（重试 4 次 = 共 5 次尝试）内第 5 次 open 成功 -> 最终成功。"""
    holder = _install_flaky_real_path(monkeypatch, fail_first=4)
    out = run_dump("COM_FAKE", 430_000_000, 431_000_000, 11,
                   retry_delay_s=0.0)  # 注入零间隔，别真睡 2.5s x 4
    assert out["model"] == "NanoVNA-H"
    assert len(out["frequencies_hz"]) == 11
    assert len(out["S11"]) == 11
    # 前 4 次失败（PermissionError）+ 第 5 次成功，总尝试 == 默认预算 5
    assert holder["counter"]["open_calls"] == 5


def test_real_path_open_retry_exhausted_is_honest_failure(monkeypatch):
    """重试耗尽 -> SystemExit 诚实失败，错误信息来自最后一次失败。"""
    import pytest
    holder = _install_flaky_real_path(monkeypatch, fail_first=99)
    with pytest.raises(SystemExit) as ei:
        run_dump("COM_FAKE", 430_000_000, 431_000_000, 11,
                 retry_delay_s=0.0)
    msg = str(ei.value)
    assert "无法打开串口 COM_FAKE" in msg
    assert "模拟 usbser 拒绝 #5" in msg  # 最后一次失败的错误信息
    assert "模拟 usbser 拒绝 #1" not in msg  # 不放旧错误充数
    assert holder["counter"]["open_calls"] == 5  # 首次 + 4 次重试，有界


def test_real_path_open_retry_bounded_by_param(monkeypatch):
    """重试次数可注入：预算=1 时最多 2 次尝试，不超发。"""
    import pytest
    holder = _install_flaky_real_path(monkeypatch, fail_first=99)
    with pytest.raises(SystemExit):
        run_dump("COM_FAKE", 430_000_000, 431_000_000, 11,
                 open_retries=1, retry_delay_s=0.0)
    assert holder["counter"]["open_calls"] == 2  # 首次 + 1 次重试


def test_real_path_open_failure_diagnostic_lists_candidates(monkeypatch, capsys):
    """打开失败时诊断打印当前可见的 NanoVNA 候选（VID:PID 0483:5740）。"""
    import pytest
    import mbdsdr_ai.nanovna_dump as nd
    holder = _install_flaky_real_path(monkeypatch, fail_first=99)
    monkeypatch.setattr(
        nd, "find_nanovna_ports",
        lambda: [("COM7", 0x0483, 0x5740), ("COM9", 0x0483, 0x5740)])
    with pytest.raises(SystemExit):
        run_dump("COM_FAKE", 430_000_000, 431_000_000, 11,
                 open_retries=1, retry_delay_s=0.0)
    err = capsys.readouterr().err
    assert "第 1/2 次" in err and "第 2/2 次" in err
    assert "COM7 (VID:PID 0483:5740)" in err
    assert "COM9 (VID:PID 0483:5740)" in err
    assert holder["counter"]["open_calls"] == 2

# ---------------------------------------------------------------------------
# 分段扫描拼接（2026-10-14，官方 NanoVNASaver 同机制：突破单 sweep 101 点上限）
# ---------------------------------------------------------------------------

def _segmented_script():
    # 21 点 / 430-431MHz / 段长 11 -> 两段：430.0-430.5M(11 点) + 430.5-431.0M(11 点)
    f1 = [str(430_000_000 + i * 50_000) for i in range(11)]
    f2 = [str(430_500_000 + i * 50_000) for i in range(11)]
    d0 = ["0.5 0.0"] * 11
    d1 = ["0.1 -0.02"] * 11
    return [
        ["help", []],
        ["version", ["1.0.174-hugen"]],
        ["info", ["NanoVNA-H"]],
        ["cal", ["load open short thru cal'ed"]],
        ["tcxo", ["usage: tcxo {TCXO frequency(Hz)}", "current: 26000000"]],
        ["sweep 430000000 430500000 11", []],
        ["frequencies", f1],
        ["data 0", d0],
        ["data 1", d1],
        ["sweep 430500000 431000000 11", []],
        ["frequencies", f2],
        ["data 0", d0],
        ["data 1", d1],
    ]


def test_replay_segmented_dump(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps(_segmented_script()), encoding="utf-8")
    out = run_dump("", 430_000_000, 431_000_000, 21, replay=str(p),
                   seg_points=11)
    # 拼接后 21 点：11 + 10（去边界重点），频率连续到 431.0MHz
    assert len(out["frequencies_hz"]) == 21
    assert out["frequencies_hz"][0] == 430_000_000
    assert out["frequencies_hz"][10] == 430_500_000   # 边界点只出现一次
    assert out["frequencies_hz"][-1] == 431_000_000
    assert out["sweep"]["points"] == 21
    seg = out["segments"]
    assert seg["segmented"] is True
    assert seg["segment_count"] == 2
    assert seg["segments"][0] == {"start_hz": 430_000_000,
                                  "stop_hz": 430_500_000, "points": 11}
    assert seg["segments"][1] == {"start_hz": 430_500_000,
                                  "stop_hz": 431_000_000, "points": 11}
    # cal 绑定诚实警告必须在场（每段独立扫描范围）
    assert "校准" in seg["cal_caveat"] and "互补" in seg["cal_caveat"]
    assert len(out["s11_phase_deg"]) == 21


def test_single_sweep_has_no_segments_key(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps(_script()), encoding="utf-8")
    out = run_dump("", 430_000_000, 431_000_000, 11, replay=str(p))
    assert "segments" not in out


def test_segmented_sweep_input_validation():
    from mbdsdr_ai.nanovna_client import (NanoVNAClient, NanoVNADriverError,
                                          ReplayTransport)
    c = NanoVNAClient(transport=ReplayTransport([]))
    cases = [
        ((440_000_000, 430_000_000, 21), {}),      # stop<=start
        ((430_000_000, 431_000_000, 1), {}),       # <2 点
        ((430_000_000, 431_000_000, 21), {"seg_points": 1}),     # 段长越界
        ((430_000_000, 431_000_000, 21), {"seg_points": 500}),   # >401
    ]
    for args, kwargs in cases:
        try:
            c.segmented_sweep(*args, **kwargs)
            assert False, f"应当诚实报错: {args} {kwargs}"
        except NanoVNADriverError:
            pass


def test_segmented_sweep_short_frame_is_error():
    """某段取数不足（设备点数上限被突破等）-> 拼装即抛错，不出半截曲线。"""
    from mbdsdr_ai.nanovna_client import (NanoVNAClient, NanoVNADriverError,
                                          ReplayTransport)
    script = [
        ["sweep 430000000 430500000 11", []],
        ["frequencies", [str(430_000_000 + i * 50_000) for i in range(11)]],
        ["data 0", ["0.5 0.0"] * 11],
        ["data 1", ["0.1 -0.02"] * 11],
        ["sweep 430500000 431000000 11", []],
        ["frequencies", [str(430_500_000 + i * 50_000) for i in range(5)]],
        ["data 0", ["0.5 0.0"] * 5],
        ["data 1", ["0.1 -0.02"] * 5],
    ]
    c = NanoVNAClient(transport=ReplayTransport(script))
    try:
        c.segmented_sweep(430_000_000, 431_000_000, 21)
        assert False, "短帧必须诚实抛错"
    except NanoVNADriverError:
        pass
