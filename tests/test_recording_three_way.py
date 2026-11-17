# SPDX-License-Identifier: MIT
"""一次录制三方一致（spec D3 / Task 15）。

模拟一次完整录制：数据文件、SigMF meta、``.json`` sidecar 三者必须给出
同一个样本总数、采样率与中心频率；样本数还要能由文件长度按 datatype 的
单样本字节数复算出来。

为什么三方对拍不用 ``IQRecorder``：
``IQRecorder`` 只产出 ``<stem>.sigmf-data`` + ``<stem>.sigmf-meta``
（样本数写在 ``core:total_num_samples``），**不写** ``.json`` sidecar；
而 ``recording_meta.write_sigmf_meta`` 的 meta 落点与它相同
（``<stem>.sigmf-meta``），两者先后调用会互相覆盖，无法在同一份产物上
同时呈现三方。因此这里用等价的最小写入路径：数据文件由
``baseband_io.save_iq``（cf32，真实写入函数）或按 datatype 直接落盘
（cu8）产生，sidecar 由 ``baseband_io.write_sidecar`` 写，meta 由
``recording_meta.write_sigmf_meta`` 写。``IQRecorder`` 的真实离线产出
另有一条两方对拍（数据文件 vs 它自己的 meta），避免把它的行为说丢。
"""

import json
import os
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mbdsdr_ai import baseband_io  # noqa: E402
from mbdsdr_ai import iq_sigmf  # noqa: E402
from mbdsdr_ai import recording_meta  # noqa: E402
from mbdsdr_ai.recorder import IQRecorder  # noqa: E402

SAMPLE_RATE = 2_400_000.0
CENTER_FREQ_HZ = 100_100_000.0
GAIN_DB = 23.5
START_ISO = "2026-11-17T08:00:00+00:00"
TOTAL_SAMPLES = 4096

#: datatype -> sidecar 里的 format 名（与 baseband_io 的写出/读回口径一致）
DATATYPE_TO_FORMAT = {"cf32_le": "cf32", "cu8": "cu8"}


def _iq_samples(count: int = TOTAL_SAMPLES) -> np.ndarray:
    """一段固定的小 IQ（单音），只为产生真实字节，不参与任何断言数值。"""
    t = np.arange(count, dtype=np.float64)
    return (0.5 * np.exp(2j * np.pi * 0.01 * t)).astype(np.complex64)


def _cu8_bytes(iq: np.ndarray) -> np.ndarray:
    """complex -> 交错 uint8（0..255，直流 127.5；与 baseband_io 读回互逆）。"""
    interleaved = np.empty(iq.size * 2, dtype=np.float32)
    interleaved[0::2] = iq.real
    interleaved[1::2] = iq.imag
    return np.clip(np.round(interleaved * 127.5 + 127.5), 0, 255).astype(np.uint8)


def _record_once(tmp_path: Path, datatype: str):
    """写数据文件 + sidecar + SigMF meta，返回 (数据路径, 样本数)。"""
    data_path = tmp_path / f"rec_{datatype}" / "cap.sigmf-data"
    data_path.parent.mkdir(parents=True, exist_ok=True)
    iq = _iq_samples()

    if datatype == "cf32_le":
        info = baseband_io.save_iq(
            iq, str(data_path), SAMPLE_RATE,
            center_freq_hz=CENTER_FREQ_HZ, note="three-way",
            start_time=START_ISO, gain_db=GAIN_DB,
            device="TestHW", driver="test",
        )
        samples = int(info["samples"])
    else:  # cu8：save_iq 不做 cu8，按 baseband_io 的口径直接落交错字节
        _cu8_bytes(iq).tofile(str(data_path))
        samples = int(iq.size)
        baseband_io.write_sidecar(str(data_path), {
            "center_freq_hz": CENTER_FREQ_HZ,
            "sample_rate": SAMPLE_RATE,
            "start_time": START_ISO,
            "duration_s": round(samples / SAMPLE_RATE, 3),
            "samples": samples,
            "format": DATATYPE_TO_FORMAT[datatype],
            "gain_db": GAIN_DB,
            "device": "TestHW",
            "driver": "test",
            "note": "three-way",
        })

    recording_meta.write_sigmf_meta(
        str(data_path),
        sample_rate=SAMPLE_RATE,
        center_freq_hz=CENTER_FREQ_HZ,
        gain_db=GAIN_DB,
        start_time_iso=START_ISO,
        hardware="TestHW",
        datatype=datatype,
        num_samples=samples,
    )
    return data_path, samples


@pytest.mark.parametrize("datatype", ["cf32_le", "cu8"])
def test_data_meta_and_sidecar_agree(tmp_path, datatype):
    data_path, samples = _record_once(tmp_path, datatype)

    meta = recording_meta.read_sigmf_meta(str(data_path))
    assert meta is not None
    sidecar = json.loads(
        Path(str(data_path) + ".json").read_text(encoding="utf-8"))

    itemsize = iq_sigmf.datatype_itemsize(datatype)
    assert itemsize == {"cf32_le": 8, "cu8": 2}[datatype]
    size = os.path.getsize(data_path)
    assert size % itemsize == 0, (
        f"{size} 字节不是 {datatype} 单样本 {itemsize} 字节的整数倍")
    file_samples = size // itemsize

    # 三方样本数：文件长度复算 == meta 声明 == sidecar 声明
    assert file_samples == samples == TOTAL_SAMPLES
    assert meta["global"]["core:num_samples"] == file_samples
    assert sidecar["samples"] == file_samples
    # 采样率 / 中心频率：meta 与 sidecar 一致
    assert meta["global"]["core:sample_rate"] == sidecar["sample_rate"] \
        == SAMPLE_RATE
    assert meta["captures"][0]["core:frequency"] == sidecar["center_freq_hz"] \
        == CENTER_FREQ_HZ
    # 录制侧 global 频率副本与 capture 段同值（早期版本写法）
    assert meta["global"]["core:frequency"] == CENTER_FREQ_HZ
    # 时长可由同一组样本数/采样率复算
    assert sidecar["duration_s"] == pytest.approx(
        file_samples / SAMPLE_RATE, abs=1e-3)

    # 严格的交换侧读取器也必须从这三份产物得到同一组事实
    info = iq_sigmf.read_meta(
        recording_meta.sigmf_meta_path(str(data_path)),
        data_path=str(data_path))
    assert info["ok"] is True
    assert info["datatype"] == datatype
    assert info["samples"] == file_samples
    assert info["sample_rate"] == SAMPLE_RATE
    assert info["center_freq"] == CENTER_FREQ_HZ
    # 录制器不替用户决定授权：缺 license 只影响「能否进清单」
    assert info["license_ok"] is False


def test_iq_recorder_offline_data_and_meta_counts_agree(tmp_path):
    """真实录制器离线产出的数据文件与它自己的 meta 样本数一致。"""
    data_path = tmp_path / "producer" / "cap.cf32"
    data_path.parent.mkdir(parents=True, exist_ok=True)
    iq = _iq_samples()
    rec = IQRecorder(
        str(data_path), sample_rate=SAMPLE_RATE,
        center_freq_hz=CENTER_FREQ_HZ, gain_db=GAIN_DB,
        device="TestHW", driver="test")
    assert rec.open() is True
    assert rec.write(iq[: TOTAL_SAMPLES // 2]) == TOTAL_SAMPLES // 2
    assert rec.write(iq[TOTAL_SAMPLES // 2:]) == TOTAL_SAMPLES // 2
    meta_path = rec.close()

    assert rec.n_samples == TOTAL_SAMPLES
    assert os.path.getsize(rec.data_path) // 8 == TOTAL_SAMPLES
    meta = json.loads(Path(meta_path).read_text(encoding="utf-8"))
    assert meta["global"]["core:total_num_samples"] == TOTAL_SAMPLES
    assert meta["global"]["core:sample_rate"] == SAMPLE_RATE
    assert meta["global"]["core:frequency"] == CENTER_FREQ_HZ
    # 它不写 .json sidecar——三方对拍改用 sidecar 写入路径的原因
    assert not os.path.exists(rec.data_path + ".json")
