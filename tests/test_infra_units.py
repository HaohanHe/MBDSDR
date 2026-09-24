"""
MBDSDR 高风险基础设施单元测试
================================
sandbox（安全沙箱）、sdr_backend（状态机）、hal（设备抽象）。
这些模块之前零功能覆盖，重点验证"该拦的拦住、该算的算对、状态不串"。
"""
import os, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── 1. 沙箱：危险代码必须被拦 ──────────────────────────────────────

def test_sandbox_blocks_dangerous_import():
    from mbdsdr_ai.sandbox import Sandbox
    sb = Sandbox()
    for bad in ["import os", "import subprocess", "import sys",
                "from os import path", "__import__('os')"]:
        r = sb.execute(bad + "\nx = 1")
        assert not r.success, f"应拦截但放行: {bad!r}"
        assert "拦截" in r.error
    return True


def test_sandbox_blocks_eval_exec_open():
    from mbdsdr_ai.sandbox import Sandbox
    sb = Sandbox()
    for bad in ["eval('1+1')", "exec('x=1')", "open('/etc/passwd')",
                "os.system('ls')", "subprocess.run(['ls'])"]:
        r = sb.execute(bad)
        assert not r.success, f"应拦截但放行: {bad!r}"
    return True


def test_sandbox_runs_safe_math():
    from mbdsdr_ai.sandbox import Sandbox
    sb = Sandbox()
    r = sb.execute("x = 2 + 3 * 4", inputs={})
    assert r.success, f"安全代码失败: {r.error}"
    # 纯计算无副作用，应成功
    return True


# ── 2. sdr_backend：内存状态机正确性 ──────────────────────────────

def test_backend_state_set_get():
    from mbdsdr_ai.sdr_backend import SDRBackend, SDRDevice
    dev = SDRDevice(device_type="mock", device_id="0", name="sim",
                    frequency_range=(1e6, 2e9),
                    sample_rate_range=(96000, 10e6), max_gain=50.0)
    be = SDRBackend(dev)
    be.status.connected = True  # mock 直连（connect 是抽象）
    assert be.set_frequency(145.8e6)
    assert abs(be.get_frequency() - 145.8e6) < 1.0
    assert be.set_sample_rate(2.048e6)
    assert abs(be.get_sample_rate() - 2.048e6) < 1.0
    assert be.set_gain(30.0)
    assert abs(be.get_gain() - 30.0) < 0.1
    assert be.set_agc(True)
    st = be.get_status()
    assert abs(st.frequency_hz - 145.8e6) < 1.0
    return True


def test_backend_demod_modes():
    from mbdsdr_ai.sdr_backend import SDRBackend, SDRDevice
    dev = SDRDevice(device_type="mock", device_id="0", name="sim",
                    frequency_range=(1e6, 2e9),
                    sample_rate_range=(96000, 10e6), max_gain=50.0)
    be = SDRBackend(dev)
    be.status.connected = True
    for m in ("FM", "AM", "USB", "LSB", "CW", "WFM"):
        assert be.set_demod(m), f"调制方式 {m} 应被接受"
    return True


# ── 3. hal：抽象接口与设备信息 ────────────────────────────────────

def test_hal_device_info_contract():
    from mbdsdr_ai.hal import DeviceInfo, DeviceCapability
    d = DeviceInfo(name="rtlsdr", driver="soapyrtlsdr",
                   manufacturer="RTL", product="RTL2832U")
    assert d.name == "rtlsdr"
    assert d.driver == "soapyrtlsdr"
    assert isinstance(DeviceCapability.RX.value, str) or \
           isinstance(DeviceCapability.RX.value, int)
    return True


def test_hal_base_not_instantiable():
    import inspect
    from mbdsdr_ai.hal import SDRBackendBase
    assert inspect.isabstract(SDRBackendBase), "SDRBackendBase 应为抽象类"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for t in tests:
        try:
            t()
            passed += 1
            print(f"  OK  {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"  FAIL {t.__name__}: {e}")
    print(f"\nsandbox/hal/backend 单测: 通过 {passed}, 失败 {failed}")
