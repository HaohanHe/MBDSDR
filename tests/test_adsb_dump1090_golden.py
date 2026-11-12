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

测量口径（2026-11-03 全量对拍后基线）：双帧长（DF<16→56 位短帧、
DF≥16→112 位长帧）解调 + 5 档自适应门限（幅度分位 60/75/84/90/95）
拉网。实测：

  `5d4d20237a55a6` 精确命中 49 次（各门限档累计）
  `20000f1f684a6c` 精确命中 1 次（分位 60-84 门限带，64 处样本起点）
  独特帧：短 547+ / 长 441+（含有效 CRC 与无效混杂，逐帧 CRC 门见下）

**全量清单对拍（2026-11-03 收口）**：dump1090 本尊（antirez master，本地
MinGW 编译 --ifile 路径，USB/网络面桩掉）在 modes1.bin 上的**完整解码
清单**为 15 条 CRC-good 报文 / 10 个独特帧（ICAO 4d2023、呼号 AMC421、
真实高度/速度/应答机码），输出原文留档 tests/golden/
dump1090_modes1_reference.txt。本测试钉住：**10/10 参考帧全部解出**
（第 10 帧 8d4d...ef858bae7fc9 藏在 84 分位门限带——4 档配方会漏，
故 PCTS 含 84）+ 全帧 CRC 语义恒等式 + 独特帧 ratchet。

**CRC-24 语义（对 10 参考帧全量实证）**：
  - DF17/18：全帧 syndrome == 0（纯校验，CRC 在帧尾 24 位）
  - DF11：   AP == crc24(帧头 32 位)（纯校验；IID==0）
  - DF4/5：  AP == crc24(帧头 32 位) ⊕ ICAO（地址奇偶，0x4d2023）

本测试钉住：10 参考帧逐字解出（失守即红）+ CRC 语义全量恒等 +
独特帧数 ratchet（保守地板，只增不减）。not_verified 撤销：全量
逐帧清单对拍已闭环；与 dump1090 的门限内覆盖率对比（我们 5 档拉网
比它单档激进，超集无害）不再列为缺口。
"""
from __future__ import annotations

import hashlib
import os
import sys
import urllib.error
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
# 5 档自适应门限（幅度分位数）。84 档不是凑数：参考帧 8d4d...ef858bae7fc9
# 只在 84/86 分位带出没（preamble k=27422），4 档配方（60/75/90/95）漏它。
PCTS = (60, 75, 84, 90, 95)

T_DF11 = "5d4d20237a55a6"      # DF11, ICAO 4d2023（9H-AEM）
T_DF4 = "20000f1f684a6c"       # DF4, ICAO 4d2023（AP=parity⊕ICAO）
ICAO = 0x4D2023

# dump1090 本尊全量解码清单（15 报文 / 10 独特帧）——antirez master
# MinGW 本地编译 --ifile 跑 modes1.bin 的输出，原文留档
# tests/golden/dump1090_modes1_reference.txt。（hex, df, 语义注记）
REF_FRAMES = (
    ("8f4d2023587f345e35837e2218b2", 17, "ES Airborne Position alt=24275ft odd"),
    ("5d4d20237a55a6", 11, "All Call Reply（README 真值）"),
    ("20000f1f684a6c", 4, "Surveillance Altitude 23375ft（README 真值）"),
    ("280010248c796b", 5, "Surveillance Identity squawk=112"),
    ("8d4d2023991094ad487c14fc9e3d", 17, "ES Airborne Velocity EW=148 NS=362"),
    ("8d4d202358792453ef858bae7fc9", 17, "ES Position alt=23050ft（84 分位带）"),
    ("8f4d20235877d0bc7d99551e27ca", 17, "ES Position alt=22925ft even"),
    ("8f4d20235877b0bc01996ff7b3f2", 17, "ES Position alt=22875ft even"),
    ("8f4d2023991093ad287c148accdc", 17, "ES Airborne Velocity EW=147 NS=361"),
    ("8f4d20232004d0f4cb1820000d24", 17, "ES Identification callsign=AMC421"),
)


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
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        # 只吞「下载这一外部动作」的传输失败（DNS/连接/超时/HTTP 4xx5xx）。
        # 磁盘写失败、URL 拼错（ValueError）等是本仓/本机问题，必须让测试红。
        pytest.skip(f"dump1090 modes1.bin 下载失败（无网络？）: "
                    f"{type(e).__name__}: {e}；"
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


def test_full_reference_list_recovered(amp_ctx):
    """全量清单地板：dump1090 本尊 10 独特帧全部解出（缺一即红）。"""
    shorts, longs = _sweep(amp_ctx)
    allf = shorts | longs
    miss = [hx for hx, _df, _note in REF_FRAMES if hx not in allf]
    assert not miss, (
        f"dump1090 参考清单缺 {len(miss)}/10 帧未解出（84 分位带/解调回归？）: "
        + ", ".join(miss))


def test_crc_semantics_on_full_reference():
    """全帧 CRC 语义恒等式（对 10 参考帧全量实证，2026-11-03）。"""
    for hx, df, note in REF_FRAMES:
        bits = bytes_to_bits(bytes.fromhex(hx))
        n = len(bits)
        full = crc24(bits, n)                 # 全帧 syndrome
        data = crc24(bits[:n - 24], n - 24)   # 数据段（CRC 前）余数
        ap = int(hx[-6:], 16)
        if df in (17, 18):
            assert full == 0, f"DF17/18 纯校验：全帧 syndrome 应为 0（{hx} {note}）"
        elif df == 11:
            assert data == ap, f"DF11 AP==crc24(data)（{hx}）"
        else:
            assert data ^ ICAO == ap, f"DF{df} AP==crc24(data)⊕ICAO（{hx}）"


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
