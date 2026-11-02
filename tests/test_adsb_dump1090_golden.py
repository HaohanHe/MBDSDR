# SPDX-License-Identifier: MIT
"""ADS-B 外部真值对拍：dump1090 官方测试样本 modes1.bin（2026-11-02）。

背景：本仓 ADS-B/Mode-S 链此前只有合成信号往返 + 本机自采 1090MHz 实录
（2026-10-29 三次实采 0 CRC 帧，如实记为负证据）。本测试把解调+解码链钉在
**dump1090 作者发布的官方测试样本**上：

- 样本：`antirez/dump1090` 仓库 `testfiles/modes1.bin`（2 MS/s u8 IQ，
  0.178 s 实录；SHA-256 3a33e16025da8669149c780075950b4e908ca036ea21f9
  583c113f60d5fb3094 钉真值；第三方字节**不入库**，按需下载、离线诚实
  skip——与 FT8 golden / de421 同款姿势）。
- 真值：dump1090 README 对**该文件**的示例输出（`dump1090 --infile
  testfiles\\modes1.bin`）给出两条可逐字比对的帧：
  - `5d4d20237a55a6`（DF11 All Call Reply，ICAO 4d2023 / 9H-AEM）
  - `20000f1f684a6c`（DF4 Surveillance Altitude Reply，ICAO 4d2023，
    高度 23375 ft——DF4 的 ICAO 不在帧内，由 AP=parity⊕ICAO 还原）

测量口径（2026-11-02 实测基线）：双帧长（DF<16→56 位短帧、DF≥16→112 位
长帧）解调 + 4 档自适应门限（幅度分位 60/75/90/95）拉网。实测：

  `5d4d20237a55a6` 精确命中 49 次（各门限档累计）
  `20000f1f684a6c` 精确命中 1 次（分位 60-84 门限带，64 处样本起点）
  独特帧：短 547 / 长 441（含有效 CRC 与无效混杂，逐帧 CRC 门见下）

**顺带钉死 CRC-24 实现**（对两条真值帧的 AP 字段实证）：
  - DF11：AP == crc24(帧头 32 位)（纯校验，无 ICAO 异或）
  - DF4： AP == crc24(帧头 32 位) ⊕ ICAO（0x4d2023）

本测试钉住：两条真值帧必须逐字解出（失守即红）+ CRC 关系必须成立 +
独特帧数 ratchet（保守地板，实测 547/441）。not_verified：全量逐帧
CRC 白名单对拍（dump1090 全解码清单未随 README 发布）、与 dump1090
的覆盖率对比。
"""
from __future__ import annotations

import hashlib
import os
import sys
import urllib.request

import numpy as np
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from mbdsdr_ai.adsb_lite import (  # noqa: E402
    find_preambles, _decode_bits_at, bytes_to_bits, crc24,
)

WAV_URL = "https://raw.githubusercontent.com/antirez/dump1090/master/testfiles/modes1.bin"
BIN_SHA256 = "3a33e16025da8669149c780075950b4e908ca036ea21f9583c113f60d5fb3094"
BIN_NAME = "modes1.bin"

SR = 2_000_000.0
PCTS = (60, 75, 90, 95)   # 自适应门限档（幅度分位数）

T_DF11 = "5d4d20237a55a6"      # DF11, ICAO 4d2023（9H-AEM）
T_DF4 = "20000f1f684a6c"       # DF4, ICAO 4d2023（AP=parity⊕ICAO）
ICAO = 0x4D2023


def _cache_path() -> str:
    return os.path.join(os.path.expanduser("~"), ".mbdsdr", "adsb_golden", BIN_NAME)


def _ensure_bin() -> str:
    """取样本：缓存 SHA 校验；缺则下载。失败诚实 skip，不造数据。"""
    path = _cache_path()
    if os.path.exists(path):
        with open(path, "rb") as f:
            if hashlib.sha256(f.read()).hexdigest() == BIN_SHA256:
                return path
        os.remove(path)   # 缓存污染不沿用
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        urllib.request.urlretrieve(WAV_URL, path)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"dump1090 modes1.bin 下载失败（无网络？）: {e}；"
                    f"可手动下载 {WAV_URL} 放到 {path} 后重跑")
    with open(path, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    if digest != BIN_SHA256:
        os.remove(path)
        pytest.skip(f"样本 SHA-256 不符（期望 {BIN_SHA256[:16]}…，得 {digest[:16]}…），"
                    f"不使用未钉真的字节")
    return path


def _load_amp(path: str) -> np.ndarray:
    raw = np.fromfile(path, dtype=np.uint8)
    assert raw.size % 2 == 0, "u8 IQ 交错样本数必须为偶数"
    i = raw[0::2].astype(np.float64) - 127.5
    q = raw[1::2].astype(np.float64) - 127.5
    return np.abs(i + 1j * q)


def _sweep(amp: np.ndarray):
    """4 档门限双帧长拉网，返回 (short_set, long_set)。"""
    shorts, longs = set(), set()
    for pct in PCTS:
        thr = float(np.percentile(amp, pct))
        for k in find_preambles(amp, sr=SR, threshold=thr):
            for nbits in (56, 112):
                bits, _ = _decode_bits_at(amp, SR, k, nbits)
                hx = "%0*x" % (nbits // 4, int("".join(str(b) for b in bits), 2))
                df = int(hx[0:2], 16) >> 3
                if nbits == 56 and df < 16:
                    shorts.add(hx)
                elif nbits == 112 and df >= 16:
                    longs.add(hx)
    return shorts, longs


@pytest.fixture(scope="module")
def amp_ctx():
    path = _ensure_bin()
    return _load_amp(path)


def test_sample_integrity(amp_ctx):
    """样本字节与 SHA-256 真值一致（缓存/下载双路径都验过）。"""
    amp = amp_ctx
    assert amp.size == 356868, f"样本应为 356868 个 IQ 点，实际 {amp.size}"


def test_truth_frames_decode(amp_ctx):
    """回归地板：dump1090 README 两条真值帧必须逐字解出（失守即红）。"""
    shorts, _ = _sweep(amp_ctx)
    assert T_DF11 in shorts, f"DF11 真值帧 {T_DF11} 未解出（短帧集 {len(shorts)} 条）"
    assert T_DF4 in shorts, f"DF4 真值帧 {T_DF4} 未解出（短帧集 {len(shorts)} 条）"


def test_crc_relationship_on_truth_frames(amp_ctx):
    """CRC-24 实现对真值帧成立：DF11 纯校验、DF4 与 ICAO 异或。"""
    # DF11：AP == crc24(data)
    bits = bytes_to_bits(bytes.fromhex(T_DF11))
    assert crc24(bits[:32], 32) == int(T_DF11[8:14], 16), "DF11 应为纯 CRC 校验"
    # DF4：AP == crc24(data) ⊕ ICAO
    bits = bytes_to_bits(bytes.fromhex(T_DF4))
    assert crc24(bits[:32], 32) ^ ICAO == int(T_DF4[8:14], 16), "DF4 应为 CRC⊕ICAO"


def test_unique_frame_floor(amp_ctx):
    """独特帧数 ratchet：只增不减（保守地板，实测短 547 / 长 441）。"""
    shorts, longs = _sweep(amp_ctx)
    assert len(shorts) >= 400, f"独特短帧从地板退化：{len(shorts)}"
    assert len(longs) >= 300, f"独特长帧从地板退化：{len(longs)}"
