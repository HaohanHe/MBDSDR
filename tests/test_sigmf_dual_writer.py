# SPDX-License-Identifier: MIT
"""两套 SigMF 写入路径对拍（spec D3 / Task 14）。

仓库有两条独立的 SigMF meta 写入路径：

- 录制主流程 ``mbdsdr_ai.recording_meta.write_sigmf_meta``（SigMF 1.0.0）；
- 数据集交换 ``mbdsdr_ai.iq_sigmf.export_samples``（SigMF 1.2.0）。

同一组参数（采样率、中心频率、样本数、datatype、增益、起始时间）分别经
两条路径写到临时目录，先钉住两边的必填结构与数值一致；再把所有差异逐项
登记进 ``ALLOWED_DIFFS``——实际差异集合必须与登记表**完全相等**，出现任何
未登记的差异（多字段、少字段、值不同）都判失败。不提供「忽略所有差异」
的开关。
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mbdsdr_ai import iq_sigmf as sig  # noqa: E402
from mbdsdr_ai import recording_meta as rec_meta  # noqa: E402

SAMPLE_RATE = 2_400_000.0
CENTER_FREQ_HZ = 100_100_000.0
NUM_SAMPLES = 480_000
GAIN_DB = 23.5
START_ISO = "2026-11-17T08:00:00+00:00"

#: 已知且允许的两条路径差异，逐项登记；键是被比较的扁平字段路径。
ALLOWED_DIFFS = {
    "global.core:version":
        "规范版本不同：录制主流程写 SigMF 1.0.0，数据集交换写 1.2.0",
    "global.core:num_channels":
        "仅录制侧显式写单通道；SigMF 缺省即 1，交换侧省略",
    "global.core:frequency":
        "仅录制侧保留 global.core:frequency（早期版本写法）；"
        "交换侧只写 captures[0].core:frequency",
    "global.core:hw":
        "仅录制侧记录硬件；交换清单没有 hw 字段，不编造",
    "global.core:author":
        "仅录制侧写作者；交换清单没有作者字段",
    "global.core:description":
        "仅录制侧写描述；交换清单没有描述字段",
    "global.core:num_samples":
        "仅录制侧写样本总数；交换侧按数据文件长度推断，不写样本数字段",
    "captures[0].mbdsdr:gain_db":
        "仅录制侧把增益放进 mbdsdr 私有扩展；交换清单不携带增益",
    "captures[0].core:datetime":
        "仅录制侧写录制起始时间；交换清单没有时间字段",
    "global.mbdsdr:license":
        "清单未声明 license 时交换侧仍写空的 mbdsdr:license；"
        "录制侧不写 license（不替用户决定授权）",
}


def _write_recording_meta(directory: Path, datatype: str) -> dict:
    """录制主流程：recording_meta 写出 <stem>.sigmf-meta。"""
    directory.mkdir(parents=True, exist_ok=True)
    iq_path = directory / "cap.sigmf-data"
    rec_meta.write_sigmf_meta(
        str(iq_path),
        sample_rate=SAMPLE_RATE,
        center_freq_hz=CENTER_FREQ_HZ,
        gain_db=GAIN_DB,
        start_time_iso=START_ISO,
        hardware="TestHW",
        datatype=datatype,
        num_samples=NUM_SAMPLES,
    )
    return json.loads((directory / "cap.sigmf-meta").read_text(encoding="utf-8"))


def _write_exchange_meta(directory: Path, datatype: str) -> dict:
    """数据集交换：iq_sigmf 写出同 stem 的 .sigmf-meta（不落数据文件）。"""
    directory.mkdir(parents=True, exist_ok=True)
    result = sig.export_samples(
        [{
            "sample_id": "cap",
            "datatype": datatype,
            "sample_rate": SAMPLE_RATE,
            "center_freq": CENTER_FREQ_HZ,
            "iq_path": str(directory / "cap.sigmf-data"),
        }],
        check_data=False,
    )
    assert result["counts"] == {"written": 1, "skipped": 0}
    return json.loads((directory / "cap.sigmf-meta").read_text(encoding="utf-8"))


def _flatten(node, prefix: str = "") -> dict:
    """把 meta 拍平成 ``路径 -> 值``；空数组保留为叶子，缺失即差异。"""
    flat = {}
    if isinstance(node, dict):
        for key, value in node.items():
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                flat.update(_flatten(value, path))
            elif isinstance(value, list):
                if value:
                    for index, item in enumerate(value):
                        flat.update(_flatten(item, f"{path}[{index}]"))
                else:
                    flat[path] = []
            else:
                flat[path] = value
    else:
        flat[prefix] = node
    return flat


@pytest.mark.parametrize("datatype", ["cf32_le", "cu8"])
def test_required_structure_and_values_agree(tmp_path, datatype):
    rec = _write_recording_meta(tmp_path / "recording", datatype)
    exch = _write_exchange_meta(tmp_path / "exchange", datatype)

    for meta in (rec, exch):
        for key in ("global", "captures", "annotations"):
            assert key in meta
        assert isinstance(meta["global"], dict)
        assert isinstance(meta["captures"], list) and len(meta["captures"]) == 1
        assert meta["annotations"] == []
        assert "core:datatype" in meta["global"]
        assert "core:version" in meta["global"]
        assert "core:sample_start" in meta["captures"][0]

    # 必填字段与参数数值：两边逐字相等
    assert rec["global"]["core:datatype"] == datatype
    assert exch["global"]["core:datatype"] == datatype
    assert rec["global"]["core:sample_rate"] == SAMPLE_RATE
    assert exch["global"]["core:sample_rate"] == SAMPLE_RATE
    assert rec["captures"][0]["core:sample_start"] == 0
    assert exch["captures"][0]["core:sample_start"] == 0
    assert rec["captures"][0]["core:frequency"] == CENTER_FREQ_HZ
    assert exch["captures"][0]["core:frequency"] == CENTER_FREQ_HZ
    # 录制侧自己的样本数与入参一致（交换侧没有样本数字段，见白名单）
    assert rec["global"]["core:num_samples"] == NUM_SAMPLES


def test_differences_are_exactly_the_registered_whitelist(tmp_path):
    rec = _write_recording_meta(tmp_path / "recording", "cf32_le")
    exch = _write_exchange_meta(tmp_path / "exchange", "cf32_le")

    rec_flat = _flatten(rec)
    exch_flat = _flatten(exch)
    only_rec = set(rec_flat) - set(exch_flat)
    only_exch = set(exch_flat) - set(rec_flat)
    value_diff = {key for key in set(rec_flat) & set(exch_flat)
                  if rec_flat[key] != exch_flat[key]}
    actual = only_rec | only_exch | value_diff

    assert actual == set(ALLOWED_DIFFS)
    assert all(reason.strip() for reason in ALLOWED_DIFFS.values())

    # 必填字段永远不在差异里
    assert not actual & {
        "global.core:datatype",
        "global.core:sample_rate",
        "captures[0].core:sample_start",
        "captures[0].core:frequency",
    }
    # 唯一的「同字段值不同」只有登记过的版本号
    assert value_diff == {"global.core:version"}
    assert rec_flat["global.core:version"] == rec_meta.SIGMF_VERSION == "1.0.0"
    assert exch_flat["global.core:version"] == sig.SIGMF_VERSION == "1.2.0"
