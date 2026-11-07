# SPDX-License-Identifier: MIT
"""FakeRtlSdr — 可注入的 RTL-SDR 测试双棒（仅 tests/ 使用，不进运行路径）。

模拟 pyrtlsdr.RtlSdr 的最小接口：center_freq / sample_rate / gain / read_samples()。
生成一个中心频率处的 FM 调制信号（默认 98.5 MHz 广播），用于端到端链路测试。

硬红线：本模块只允许被 tests/ 下的测试文件 import。mbdsdr_ai/ 与 desktop/
的生产代码绝不引用本类——对照 tests/test_no_sim_regression.py 的 AST 守卫
（生产代码禁止 SimDataGenerator / MockSDRBackend / mock 降级链）。

信号模型（与 tests/test_audio_chain.py::make_fm_signal 同一条数学）：
    真实 RTL-SDR  tune 到 98.5 MHz 后，read_samples 返回的是下变频到
    基带（baseband）的复 IQ，电台载波落在 DC。我们直接吐基带 IQ：

        IQ(t) = exp(j * -(deviation/mod_freq) * cos(2π*mod_freq*t))

    瞬时频偏 = deviation·sin(2π·mod_freq·t)，经正交鉴频（dsp.fm_demod）
    后输出应忠实复现 mod_freq 的正弦音。再加一点点复高斯噪声当底噪。

调用历史（call_history）：
    所有对设备的控制面调用（set_* 方法 + center_freq/sample_rate/gain/
    freq_correction/bandwidth 属性写入）都按时间顺序追加到 self.call_history，
    格式为 (name, args, kwargs)。测试用它断言 connect() 后真的对设备下发了
    sample_rate / center_freq / gain / agc，而不是停在出厂默认。
"""

import contextlib
import importlib
import sys
import types

import numpy as np


class _StubType:
    """万能占位：真包 exec 打到 fake 子模块缺符号时用它兜底（仅测试假件）。"""

    def __init__(self, *a, **k):
        pass

    def __call__(self, *a, **k):
        return 0

    def __getattr__(self, name):
        return _StubType()


@contextlib.contextmanager
def fake_rtlsdr_package(rtl_cls):
    """把假 rtlsdr **包** 装进 sys.modules（含 rtlsdr.librtlsdr 子模块）。

    为什么必须这么脏（2026-11-07 假 rtlsdr 抢线事故根因，两次返工）：
    desktop/module_panel 的枚举 worker 线程会并发 ``import rtlsdr``。真包
    是先插 sys.modules 空壳再 exec；exec 期 ``from .librtlsdr import ...``
    打到非包 fake 会抛「not a package」→ 真包 exec 失败 → import 机器失败
    清理 ``del sys.modules['rtlsdr']`` 把随后安装的 fake **连锅端掉** →
    connect 转而拿真 pyrtlsdr 开真棒子。三重防御：

    1. 同步 import：先 ``importlib.import_module("rtlsdr")`` 等任何
       in-flight 的真包加载按模块锁收尾（成功/失败都算落定），消灭竞态；
    2. fake 是带 __path__ 的包 + 预埋 rtlsdr.librtlsdr（含万能 __getattr__），
       相对/子模块导入命中 fake 不再触发失败清理；
    3. 安装顺序**先子模块后父模块**（反过来中间窗口相对导入打到旧父模块）。
    """
    # ① 同步：等并发线程 in-flight 的真 rtlsdr 加载落定（import 按模块锁排队）
    try:
        importlib.import_module("rtlsdr")
    except Exception:
        pass  # 本机 DLL 缺符号等 import 期失败也算「落定」，key 已被清理

    fake_mod = types.ModuleType("rtlsdr")
    fake_mod.__path__ = []          # 声明为包
    fake_mod.RtlSdr = rtl_cls
    fake_lib = types.ModuleType("rtlsdr.librtlsdr")
    fake_lib.librtlsdr = fake_lib   # from rtlsdr.librtlsdr import librtlsdr
    fake_lib.rtlsdr_get_device_count = lambda: 0
    fake_lib.rtlsdr_reset_buffer = lambda dev_p: 0
    fake_lib.rtlsdr_set_freq_correction = lambda dev_p, ppm: 0
    fake_lib.__getattr__ = lambda name: _StubType()   # PEP 562：缺啥补啥
    fake_mod.librtlsdr = fake_lib

    keys = ("rtlsdr.librtlsdr", "rtlsdr")
    saved = {k: sys.modules.get(k) for k in keys}
    sys.modules["rtlsdr.librtlsdr"] = fake_lib
    sys.modules["rtlsdr"] = fake_mod
    try:
        yield fake_mod
    finally:
        for k in keys:
            if saved[k] is not None:
                sys.modules[k] = saved[k]
            else:
                sys.modules.pop(k, None)


class FakeRtlSdr:
    """pyrtlsdr.RtlSdr 的最小可注入替身。

    只实现 RTLSDRBackend 真正用到的属性/方法：
      - center_freq / sample_rate / gain / freq_correction / bandwidth：
        可读写 property，写入时记录到 call_history
      - tuner_type：int（R820T = 5，与 `RTLSDRBackend._probe_tuner_type` 的码表一致）
      - read_samples(n) -> np.complex64 一维数组
      - set_manual_gain_mode / set_agc_mode / set_direct_sampling /
        set_bias_tee / set_offset_tuning / reset_buffer / close

    参数（夹具固定值，真实棒同样接受这几档）：
      center_freq=98.5 MHz（FM 广播段，一上来就有"台"），
      sample_rate=2.048 MS/s（伪设备自身默认，与产品默认
        `DEFAULT_SAMPLE_RATE_HZ` = 2.400 MS/s 解耦：夹具固定用这一档，
        不代表产品默认；产品默认值以常量名为准），
      gain=20 dB，调制音 1 kHz，频偏 75 kHz（广播 FM 标准）。

    signal_offset_hz：可选的发射频偏。真实场景里 RTL-SDR 只 tune 到一个中心
      频率，而"想听的台"往往不在中心，而是落在中心右侧/左侧若干 kHz/MHz。
      传 signal_offset_hz=+100_000 即模拟"电台在中心频率 +100 kHz 处"：
      read_samples 在吐基带前用 NCO exp(j·2π·offset·t) 把整条 FM 信号搬离 DC。
      默认 0 = 电台恰好落在 DC（与历史行为一致，旧测试不受影响）。
    """

    def __init__(self, device_index: int = 0,
                 fm_carrier_hz: float = 98.5e6,
                 mod_freq: float = 1000.0,
                 deviation: float = 75000.0,
                 noise_amplitude: float = 0.01,
                 signal_offset_hz: float = 0.0,
                 seed: int = 20260926):
        # 调用历史：测试断言 connect() 后真的对设备下发了哪些设置
        self.call_history = []
        # 与 pyrtlsdr.RtlSdr 同名同语义：写进去的是 RF 中心频率，
        # read_samples 返回的是它下变频后的基带 IQ（载波在 DC）。
        self._center_freq = int(fm_carrier_hz)
        self._sample_rate: float = 2_048_000.0
        self._gain = 20.0
        self._freq_correction = 0
        self._bandwidth = 0
        self._agc_mode = False
        # pyrtlsdr RtlSdr.tuner_type：5 = R820T（RTLSDRBackend._TUNER_NAMES[5]）
        self.tuner_type = 5

        self._mod_freq = float(mod_freq)
        self._deviation = float(deviation)
        self._noise_amp = float(noise_amplitude)
        # 电台相对中心频率的位置：0=在 DC；+100k=在中心右侧 100 kHz。
        # read_samples 吐基带前用 NCO 把信号搬到这个位置，模拟"棒 tune 到
        # 中心，但台不在中心"的真实射频场景（供 VFO DDC 搬频测试用）。
        self._signal_offset_hz = float(signal_offset_hz)
        # 固定种子：同一支棒每次吐的信号可复现，测试不抖动
        self._rng = np.random.default_rng(seed)
        # 全局样本计数器：让跨次 read_samples 的相位严格连续，
        # 不会在块边界打出相位跳变 click（对照 make_fm_signal 用绝对 t）。
        self._global_idx = 0
        self._closed = False
        # 真实棒属性占位（RTLSDRBackend 探测时可能读）
        self._direct_sampling = 0
        self._offset_tuning = False
        self._manual_gain_mode = 1  # 1=manual, 0=AGC（pyrtlsdr 默认 manual）

    # ------------------------------------------------------------------
    # 调用历史记录
    # ------------------------------------------------------------------
    def _record(self, name: str, *args, **kwargs):
        """追加一条控制面调用记录。属性写入通过 property setter 调用此方法。"""
        self.call_history.append((name, args, kwargs))

    def calls_named(self, name: str) -> list:
        """返回所有名为 name 的调用记录 [(args, kwargs), ...]，便于断言。"""
        return [(a, kw) for n, a, kw in self.call_history if n == name]

    # ------------------------------------------------------------------
    # 可读写属性（写入时记录到 call_history）
    # ------------------------------------------------------------------
    @property
    def center_freq(self) -> int:
        return self._center_freq

    @center_freq.setter
    def center_freq(self, value):
        self._record("center_freq", int(value))
        self._center_freq = int(value)

    @property
    def sample_rate(self) -> float:
        return self._sample_rate

    @sample_rate.setter
    def sample_rate(self, value):
        self._record("sample_rate", float(value))
        self._sample_rate = float(value)

    @property
    def gain(self) -> float:
        return self._gain

    @gain.setter
    def gain(self, value):
        self._record("gain", float(value))
        self._gain = float(value)

    @property
    def freq_correction(self) -> int:
        return self._freq_correction

    @freq_correction.setter
    def freq_correction(self, value):
        self._record("freq_correction", int(value))
        self._freq_correction = int(value)

    @property
    def bandwidth(self) -> int:
        return self._bandwidth

    @bandwidth.setter
    def bandwidth(self, value):
        self._record("bandwidth", int(value))
        self._bandwidth = int(value)

    @property
    def agc_mode(self) -> bool:
        return self._agc_mode

    @agc_mode.setter
    def agc_mode(self, value):
        self._record("agc_mode", bool(value))
        self._agc_mode = bool(value)

    @property
    def direct_sampling(self):
        return self._direct_sampling

    @direct_sampling.setter
    def direct_sampling(self, value):
        # pyrtlsdr 接受 int (0/1/2) 或 str ("off"/"i"/"q")
        self._record("direct_sampling", value)
        self._direct_sampling = value

    @property
    def gain_mode(self) -> int:
        return self._manual_gain_mode

    @gain_mode.setter
    def gain_mode(self, value):
        self._record("gain_mode", int(value))
        self._manual_gain_mode = int(value)

    # ------------------------------------------------------------------
    # 数据面：吐基带 FM IQ
    # ------------------------------------------------------------------
    def read_samples(self, num_samples: int) -> np.ndarray:
        """返回 num_samples 个复基带 IQ，dtype=complex64。

        积分相位法生成 FM：
            瞬时角频偏 Δω(t) = 2π·deviation·sin(2π·mod_freq·t)
            相位      φ(t)  = ∫Δω dt = -(deviation/mod_freq)·cos(2π·mod_freq·t)
            IQ(t)           = exp(j·φ(t))
        载波在 DC（已下变频），再加少量复高斯底噪。
        """
        if self._closed:
            raise OSError("FakeRtlSdr: device closed")
        n = int(num_samples)
        sr = float(self._sample_rate)
        t = (self._global_idx + np.arange(n)) / sr
        self._global_idx += n

        phase = -(self._deviation / self._mod_freq) * np.cos(
            2.0 * np.pi * self._mod_freq * t)
        iq = np.exp(1j * phase)

        # NCO 搬频：把"在 DC 的电台"搬到 signal_offset_hz 处。
        # t 用的是绝对样本号（含 _global_idx 累计），所以跨块相位严格连续，
        # 不会在块边界打出跳变。offset=0 时 mult=1，行为与旧版完全一致。
        if self._signal_offset_hz != 0.0:
            iq = iq * np.exp(1j * 2.0 * np.pi * self._signal_offset_hz * t)

        # 复高斯底噪：I/Q 各一份，幅度 noise_amp
        noise = self._noise_amp * (
            self._rng.standard_normal(n) + 1j * self._rng.standard_normal(n))
        return (iq + noise).astype(np.complex64)

    # ------------------------------------------------------------------
    # 控制面：与 pyrtlsdr.RtlSdr 同名的方法（全部记录到 call_history）
    # ------------------------------------------------------------------
    def set_manual_gain_mode(self, mode):
        """pyrtlsdr：1=手动增益，0=AGC。记录不报错。"""
        self._record("set_manual_gain_mode", int(mode))
        self._manual_gain_mode = int(mode)

    def set_agc_mode(self, enabled):
        """RTL2832 数字 AGC（与 tuner 前端 AGC 相互独立）。"""
        self._record("set_agc_mode", bool(enabled))
        self._agc_mode = bool(enabled)

    def set_direct_sampling(self, mode):
        """0=off, 1=I branch, 2=Q branch。"""
        self._record("set_direct_sampling", int(mode))
        self._direct_sampling = int(mode)
        return True

    def set_bias_tee(self, enabled: bool):
        self._record("set_bias_tee", bool(enabled))
        return True

    def set_offset_tuning(self, enabled: bool):
        self._record("set_offset_tuning", bool(enabled))
        self._offset_tuning = bool(enabled)
        return True

    def reset_buffer(self):
        """丢弃内部缓冲（真实棒用于清 USB 残留）。我们无缓冲，空操作。"""
        self._record("reset_buffer")
        return None

    def close(self):
        self._record("close")
        self._closed = True

    # 方便测试断言
    @property
    def closed(self) -> bool:
        return self._closed

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return (f"FakeRtlSdr(center_freq={self._center_freq/1e6:.1f}MHz, "
                f"sr={self._sample_rate/1e3:.0f}k, gain={self._gain}dB, "
                f"closed={self._closed})")
