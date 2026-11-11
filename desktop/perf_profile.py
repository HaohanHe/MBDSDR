# SPDX-License-Identifier: MIT
"""桌面**性能档**：把采样率 / FFT / 刷新率收成一个用户可调的旋钮。

## 为什么单独一个模块

内核 ``mbdsdr_ai.sdr_backend.DEFAULT_SAMPLE_RATE_HZ``（2.4 MS/s）是
**内核与真机验收的基准**：R4 录制回放、ADS-B 1090 解码、31 个测试都钉着
它。它是"设备能开多快"，不是"桌面默认该开多快"。两者混在一个常量里，
就会出现用户看到的那种情况：一台普通笔记本一插上棒子就被 2.4 MS/s 的
FFT + 解调 + 瀑布吃满，GNSS 面板和设置页"原地不动"。

对照 SDR++ ``core/src/server.cpp:47`` 默认 1 MS/s、``main_window.cpp:51``
``playing=false``：默认档取**够用且省电**，宽带需求交给用户显式切档。

## 档位数值来源（不编）

- 采样率全部取自 ``RTLSDRBackend.SAMPLE_RATES``（SDR++ rtl_sdr_source
  ``main.cpp:27-39`` 的 11 个离散档），保证下发后不被 nearest_sample_rate()
  吸附到别的值、界面不会"选了又跳回去"。
- WFM 广播单声道只需 >= 200 kHz（``dsp.wfm_broadcast_demod`` docstring），
  1.024 MS/s 足够且留了邻道余量。
- CPU 实测（``output/perf_probe.py``，20 ms IQ 块，FFT+WFM 解调）：
  2.40 MS/s 3.60 ms/块 ≈ 18% 单核；1.00 MS/s 1.37 ms/块 ≈ 6.8%；
  0.24 MS/s 0.48 ms/块 ≈ 2.4%。还没算 AGC、瀑布、多 VFO、GNSS 轮询。
"""
from __future__ import annotations

from typing import Dict

#: 档位 key → 采样率 / FFT / 刷新 / GNSS 轮询 / 频谱面板性能档索引。
#: 采样率必须是 RTLSDRBackend.SAMPLE_RATES 里的真实档位。
#:
#: FFT 点数与频谱刷新间隔的**运行时事实源是频谱面板**
#: ``SpectrumPanel._PERF_LEVELS``（高/均衡/低三档，经 ``set_performance_mode``
#: 真改 generator.reconfigure + plot._timer + 同步下拉）。这里的
#: ``fft_size`` / ``spectrum_interval_ms`` 是**展示镜像**，必须与
#: ``_PERF_LEVELS[panel_level]`` 逐值相等——由
#: ``test_perf_profile_mirrors_spectrum_panel_levels`` 钉住，防两处漂移。
#: 桌面性能档只额外拥有采样率（下次连接默认）与 GNSS 轮询两个面板不管的杠杆。
PROFILES: Dict[str, dict] = {
    "eco": {
        "label": "省电（1.024 MS/s）",
        "sample_rate_hz": 1_024_000,
        "panel_level": 2,            # 面板「低」：(1024, 256, 100)
        "fft_size": 1024,
        "spectrum_interval_ms": 100,
        "gnss_poll_ms": 2000,
        "help": "普通笔记本 / 只听广播：CPU 约为 2.4 MS/s 的三分之一，界面最跟手。"
                "宽带解码（ADS-B 1090 等）请切到「精细」。",
    },
    "balanced": {
        "label": "均衡（2.048 MS/s）",
        "sample_rate_hz": 2_048_000,
        "panel_level": 1,            # 面板「均衡」：(1024, 384, 66)
        "fft_size": 1024,
        "spectrum_interval_ms": 66,
        "gnss_poll_ms": 1000,
        "help": "频谱视野与 CPU 折中；适合同时看瀑布和听广播。",
    },
    "quality": {
        "label": "精细（2.400 MS/s）",
        "sample_rate_hz": 2_400_000,
        "panel_level": 0,            # 面板「高」：(2048, 512, 50)
        "fft_size": 2048,
        "spectrum_interval_ms": 50,
        "gnss_poll_ms": 500,
        "help": "内核基准速率，宽带解码 / 录制的最高视野；低配机器会明显变卡。",
    },
}

#: 默认档：用户 2026-11-10 明确「电脑没有那么强大…得让路」。
DEFAULT_PROFILE = "eco"


def profile(key: str) -> dict:
    """未知档一律回默认，绝不抛异常拖垮 GUI。"""
    return PROFILES.get(str(key or "").strip().lower(), PROFILES[DEFAULT_PROFILE])


def sample_rate_for(key: str) -> float:
    """该档的默认连接采样率（Hz）。"""
    return float(profile(key)["sample_rate_hz"])


def is_supported_rate(rate_hz: float) -> bool:
    """检查档位采样率确实在后端离散表里（防止手改档位表写出硬件不收的值）。"""
    try:
        from mbdsdr_ai.sdr_backend import RTLSDRBackend
        return any(abs(int(r) - int(rate_hz)) <= 1
                   for r in RTLSDRBackend.SAMPLE_RATES)
    except Exception:  # noqa: BLE001 - 内核缺失时不做断言，交给设备回读
        return True
