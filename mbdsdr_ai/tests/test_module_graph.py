# SPDX-License-Identifier: MIT
"""
module_graph.py SDR++ 式模块图——确定性离线测试

不联网、无硬件：
  - Module 流读写校验：写非输出流/读非输入流显式 ValueError；
    读未写过的流返回空 complex64（诚实空态，不造假数据）；
  - set_param 运行期改参；info() 诊断字典；
  - SourceModule.set_freq；SoapySource 无 device_info 拒绝启动
    （红线：无设备不伪造）；未运行时 read_block 返回空；
  - DemodModule.set_bandwidth 钳位到 [min,max]（SDR++ 口径）；
    deemphasize：tau<=0 恒等、空输入原样、RC 状态跨调用连续；
  - WFM 模块参数钉扎 SDR++ 真实标定值（防下游凭记忆改数）；
  - ModuleGraph：重名注册拒绝；未知内置标签 KeyError；LSB 工厂注册；
    connect 两端校验（模块不存在/流不存在/方向错均显式报错）；
    拓扑排序 source 优先；拓扑启停容错（单模块启动失败不拖垮全图）。

运行：python3 -m pytest mbdsdr_ai/tests/test_module_graph.py -v
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from mbdsdr_ai.module_graph import (  # noqa: E402
    BUILTIN_MODULES,
    DemodModule,
    Module,
    ModuleGraph,
    RTLSourceModule,
    SoapySourceModule,
    WFMModule,
)


class _Src(Module):
    module_type = "source"
    input_streams = []
    output_streams = ["out"]

    def start(self) -> None:
        self.running = True

    def stop(self) -> None:
        self.running = False


class _Proc(Module):
    module_type = "processing"
    input_streams = ["in"]
    output_streams = ["out"]

    def start(self) -> None:
        self.running = True

    def stop(self) -> None:
        self.running = False


class _Sink(Module):
    module_type = "sink"
    input_streams = ["in"]
    output_streams = []

    def start(self) -> None:
        self.running = True

    def stop(self) -> None:
        self.running = False


# ---------------------------------------------------------------- Module 基类

def test_write_stream_validates_output():
    m = _Src("s")
    with pytest.raises(ValueError, match="没有输出流"):
        m.write_stream("nope", np.zeros(4, dtype=np.complex64))
    # source 只能写自己的输出流（读回是下游的事，source 无输入流）
    m.write_stream("out", np.ones(4, dtype=np.complex64))
    assert np.all(m._buffers["out"] == 1.0)


def test_read_stream_validates_input():
    m = _Sink("k")
    with pytest.raises(ValueError, match="没有输入流"):
        m.read_stream("nope")


def test_read_unwritten_stream_returns_empty_complex64():
    m = _Sink("k")
    got = m.read_stream("in")
    assert got.dtype == np.complex64
    assert got.size == 0


def test_set_param_and_info():
    m = _Proc("p")
    m.set_param("gain", 12.5)
    assert m.gain == 12.5
    info = m.info()
    assert info["name"] == "p"
    assert info["inputs"] == ["in"] and info["outputs"] == ["out"]
    assert info["running"] is False


# ---------------------------------------------------------------- Source

def test_source_set_freq():
    s = SoapySourceModule("soapy")
    s.set_freq(433.9e6)
    assert s.frequency == 433.9e6


def test_soapy_source_refuses_start_without_device_info():
    """无 device_info 拒绝启动——不伪造数据（红线）。"""
    s = SoapySourceModule("soapy")
    with pytest.raises(RuntimeError, match="拒绝伪造数据"):
        s.start()
    assert s.is_running() is False


def test_soapy_read_block_when_not_running_returns_empty():
    s = SoapySourceModule("soapy")
    got = s.read_block(1024)
    assert got.dtype == np.complex64 and got.size == 0


def test_rtl_source_lifecycle():
    s = RTLSourceModule("rtl")
    s.start()
    assert s.is_running()
    s.stop()
    assert not s.is_running()


# ---------------------------------------------------------------- Demod

def test_set_bandwidth_clamps():
    # DemodModule 的 start/stop 已实现（非抽象），可直接实例化
    d = DemodModule("d")
    d.set_bandwidth(10.0)                       # 低于 min
    assert d.bandwidth == d.min_bandwidth
    d.set_bandwidth(1e9)                        # 高于 max
    assert d.bandwidth == d.max_bandwidth
    d.set_bandwidth(20000.0)                    # 区间内不动
    assert d.bandwidth == 20000.0


def test_deemphasize_zero_tau_is_identity():
    d = DemodModule("d")
    d.deemph_tau = 0.0
    x = np.array([1.0, -2.0, 3.0])
    assert np.array_equal(d.deemphasize(x, 48000.0), x)


def test_deemphasize_empty_passthrough():
    d = DemodModule("d")
    d.deemph_tau = 50e-6
    assert d.deemphasize(np.zeros(0), 48000.0).size == 0


def test_deemphasize_rc_step_response_and_state_continuity():
    """一阶 RC：阶跃响应趋近输入；状态跨调用连续（不分段复位）。"""
    d = DemodModule("d")
    d.deemph_tau = 75e-6
    sr = 48000.0
    step = np.ones(2000, dtype=np.float32)
    out = d.deemphasize(step, sr)
    assert out.dtype == np.float32
    assert out[-1] > 0.99                       # 已收敛到输入
    assert out[0] < 0.5                         # 起点被状态（0）拉低
    # 继续推恒定值：末值应保持在 1 附近（状态延续，无跳变复位）
    out2 = d.deemphasize(np.ones(100, dtype=np.float32), sr)
    assert abs(out2[-1] - 1.0) < 1e-3


def test_deemphasize_attenuates_high_frequency_more_than_low():
    """去加重本质是低通：高频分量衰减应大于低频。"""
    d = DemodModule("d")
    d.deemph_tau = 75e-6
    sr = 48000.0
    n = 48000
    t = np.arange(n) / sr
    low = np.sin(2 * np.pi * 100 * t)
    high = np.sin(2 * np.pi * 10000 * t)
    # 取稳定段（跳过瞬态）比幅度
    lo_amp = np.abs(d.deemphasize(low.astype(np.float32), sr))[2000:].mean()
    hi_amp = np.abs(d.deemphasize(high.astype(np.float32), sr))[2000:].mean()
    assert hi_amp < lo_amp * 0.5


# ---------------------------------------------------------------- SDR++ 参数钉扎

def test_wfm_params_match_sdrpp_calibration():
    """wfm.h 标定值钉扎：改数即失败，逼后人同步上游出处。"""
    w = WFMModule("wfm")
    assert w.if_samplerate == 250_000.0
    assert w.default_bandwidth == 150_000.0
    assert w.min_bandwidth == 50_000.0
    assert w.max_bandwidth == 250_000.0
    assert w.default_deemph_us == 50.0
    assert w.pilot_band == (18_750.0, 19_250.0)
    assert w.audio_lowpass == 15_000.0
    assert w.rds_subcarrier == 57_000.0
    assert w.rds_resample_rate == 5_000.0


# ---------------------------------------------------------------- ModuleGraph

def test_register_duplicate_name_rejected():
    g = ModuleGraph()
    g.register_module(_Src("s"))
    with pytest.raises(ValueError, match="已存在"):
        g.register_module(_Src("s"))


def test_register_builtin_unknown_label():
    g = ModuleGraph()
    with pytest.raises(KeyError, match="未知内置模块"):
        g.register_builtin("No Such Module")


def test_register_builtin_lsb_factory():
    g = ModuleGraph()
    m = g.register_builtin("LSB")
    assert m.name == "LSB"
    assert m.sideband == "lsb"


def test_connect_validates_endpoints():
    g = ModuleGraph()
    g.register_module(_Src("s"))
    g.register_module(_Sink("k"))
    with pytest.raises(KeyError, match="上游模块不存在"):
        g.connect("ghost", "out", "k", "in")
    with pytest.raises(ValueError, match="没有输出流"):
        g.connect("s", "wrong", "k", "in")
    with pytest.raises(ValueError, match="没有输入流"):
        g.connect("s", "out", "k", "wrong")
    g.connect("s", "out", "k", "in")               # 正确连线不抛
    assert len(g.edges) == 1
    assert len(g.modules["s"].consumers) == 1       # 上游登记下游消费者


def test_topo_order_source_first_and_sink_last():
    g = ModuleGraph()
    g.register_module(_Sink("sink"))                # 故意先注册 sink
    g.register_module(_Src("src"))
    g.register_module(_Proc("mid"))
    g.connect("src", "out", "mid", "in")
    g.connect("mid", "out", "sink", "in")
    order = [m.name for m in g._topo_order()]
    assert order[0] == "src"
    assert order[-1] == "sink"
    assert order.index("mid") > order.index("src")


def test_start_stop_runs_all_modules():
    g = ModuleGraph()
    for name in ("src", "mid", "sink"):
        g.register_module({"src": _Src, "mid": _Proc, "sink": _Sink}[name](name))
    g.connect("src", "out", "mid", "in")
    g.connect("mid", "out", "sink", "in")
    g.start()
    assert all(m.is_running() for m in g.modules.values())
    g.stop()
    assert not any(m.is_running() for m in g.modules.values())


def test_start_module_failure_does_not_crash_graph(capsys):
    """单模块启动失败只如实报告，其余模块照常启动（红线：不整图崩）。"""
    class BadSrc(_Src):
        def start(self) -> None:
            raise RuntimeError("no device")

    g = ModuleGraph()
    g.register_module(BadSrc("bad"))
    g.register_module(_Sink("k"))
    g.connect("bad", "out", "k", "in")
    g.start()                                        # 不抛
    assert g.modules["k"].is_running()               # 下游照常起
    assert "启动 bad 失败" in capsys.readouterr().out


def test_list_modules_and_has_source():
    g = ModuleGraph()
    g.register_module(_Src("s"))
    g.register_module(_Sink("k"))
    infos = g.list_modules()
    assert {i["name"] for i in infos} == {"s", "k"}
    assert g.has_source() is True
    g2 = ModuleGraph()
    g2.register_module(_Proc("p"))
    assert g2.has_source() is False


def test_builtin_registry_covers_all_types():
    types = set(BUILTIN_MODULES.values())
    assert len(BUILTIN_MODULES) >= 12
    assert len(types) >= 8                          # 至少 8 个不同类/工厂
