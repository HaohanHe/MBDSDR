"""新时空 TLE 真联网拉取/解析模块测试
=====================================

测试样本为从 celestrak 真实抓取的 TLE 行（ISS / DMSP F16 / FENGYUN 3B），
不使用占位假数据；联网部分用 monkeypatch 替换 urllib.request.urlopen。

运行:
    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_new_spacetime_tle.py -v
"""
import os
import sys
import time
import urllib.request

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from mbdsdr_ai.new_spacetime_tle import TLEManager, TLEEntry, CELESTRAK_BASE  # noqa: E402

# ---------------------------------------------------------------------------
# 真实 celestrak TLE 样本（gp.php?FORMAT=tle 实际返回，校验和经核对有效）
# ---------------------------------------------------------------------------
ISS_NAME = "ISS (ZARYA)"
ISS_L1 = "1 25544U 98067A   26268.43198945  .00011731  00000+0  21853-3 0  9991"
ISS_L2 = "2 25544  51.6316 163.9608 0004776 179.9710 180.1280 15.49288785587293"

DMSP_NAME = "DMSP 5D-3 F16 (USA 172)"
DMSP_L1 = "1 28054U 03048A   26268.90058141  .00000054  00000+0  51686-4 0  9994"
DMSP_L2 = "2 28054  98.9853 293.3779 0006409 214.7354 244.1503 14.14492178183729"

F3B_NAME = "FENGYUN 3B"
F3B_L1 = "1 37214U 10059A   26268.94379207 -.00000187  00000+0 -73124-4 0  9995"
F3B_L2 = "2 37214  98.9528 315.3359 0021004 260.8517 201.1279 14.14857109821671"

GROUP_TEXT = (
    ISS_NAME + "             \r\n"
    + ISS_L1 + "\r\n"
    + ISS_L2 + "\r\n"
    + "\r\n"
    + DMSP_NAME + " \r\n"
    + DMSP_L1 + "\r\n"
    + DMSP_L2 + "\r\n"
    + "\r\n"
    + F3B_NAME + "               \r\n"
    + F3B_L1 + "\r\n"
    + F3B_L2 + "\r\n"
)


class _FakeResponse:
    """模拟 urllib 响应：支持 with 语句与 .read()。"""

    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _make_manager(tmp_path) -> TLEManager:
    return TLEManager(cache_dir=str(tmp_path), timeout=1.0)


# ---------------------------------------------------------------------------
def test_tle_checksum_validation():
    """真实 TLE 行校验和有效；篡改后失效。"""
    # >=3 组已知有效行（含带负号指数的行）
    assert TLEManager.validate_checksum(ISS_L1) is True
    assert TLEManager.validate_checksum(ISS_L2) is True
    assert TLEManager.validate_checksum(DMSP_L1) is True
    assert TLEManager.validate_checksum(DMSP_L2) is True
    assert TLEManager.validate_checksum(F3B_L1) is True   # 含多个 '-'
    assert TLEManager.validate_checksum(F3B_L2) is True

    # 篡改行：改动正文一个数字，末位校验和不变 -> 必须失败
    tampered = ISS_L1[:30] + "5" + ISS_L1[31:]
    assert tampered != ISS_L1
    assert TLEManager.validate_checksum(tampered) is False

    # 空行 / 非数字末位 -> False
    assert TLEManager.validate_checksum("") is False
    assert TLEManager.validate_checksum("1 25544U .................A") is False


def test_parse_tle_text():
    """解析含多个 3 行块的文本，验证数量、名称、CATNR。"""
    entries = TLEManager.parse_tle_text(GROUP_TEXT)
    assert len(entries) == 3

    by_catnr = {e.catnr: e for e in entries}
    assert 25544 in by_catnr
    assert 28054 in by_catnr
    assert 37214 in by_catnr

    iss = by_catnr[25544]
    assert iss.name.strip() == ISS_NAME
    assert iss.line1 == ISS_L1
    assert iss.line2 == ISS_L2

    f3b = by_catnr[37214]
    assert f3b.name.strip() == F3B_NAME


def test_catnr_extraction():
    """从 line1 正确提取 CATNR（第 3-7 列）。"""
    assert TLEManager.catnr_from_line1(ISS_L1) == 25544
    assert TLEManager.catnr_from_line1(DMSP_L1) == 28054
    assert TLEManager.catnr_from_line1(F3B_L1) == 37214


def test_fetch_network_failure_returns_none(tmp_path, monkeypatch):
    """urlopen 抛异常且无任何缓存时，fetch 返回 None，绝不伪造。"""
    def _raise(*args, **kwargs):
        raise OSError("network unreachable")

    monkeypatch.setattr(urllib.request, "urlopen", _raise)

    mgr = _make_manager(tmp_path)
    # 磁盘上不存在 25544 的缓存文件
    assert not os.path.exists(os.path.join(str(tmp_path), "25544.tle"))

    result = mgr.fetch(25544)
    assert result is None
    # 内存也不应有
    assert mgr.get(25544) is None


def test_fetch_disk_cache_fallback(tmp_path, monkeypatch):
    """磁盘缓存存在但已过期时，联网失败应回退读到磁盘缓存的正确 TLE。"""
    mgr = _make_manager(tmp_path)

    # 先写一个磁盘缓存文件（名称 + line1 + line2 三行）
    cache_file = os.path.join(str(tmp_path), "25544.tle")
    with open(cache_file, "w", encoding="utf-8") as f:
        f.write(ISS_NAME + "\n")
        f.write(ISS_L1 + "\n")
        f.write(ISS_L2 + "\n")

    # 把缓存文件 mtime 改到 48 小时前 -> 过期，迫使流程尝试联网
    old = time.time() - 48 * 3600
    os.utime(cache_file, (old, old))

    # 联网必定失败
    def _raise(*args, **kwargs):
        raise OSError("network unreachable")

    monkeypatch.setattr(urllib.request, "urlopen", _raise)

    entry = mgr.fetch(25544)
    assert entry is not None
    assert entry.catnr == 25544
    assert entry.line1 == ISS_L1
    assert entry.line2 == ISS_L2
    assert entry.source == "cache"
    # 回退后应进入内存缓存
    assert mgr.get(25544) is entry


def test_fetch_group_parse(tmp_path, monkeypatch):
    """用预置文本模拟群组响应，验证解析出多颗卫星。"""
    mgr = _make_manager(tmp_path)

    def _fake_urlopen(req, *args, **kwargs):
        assert "GROUP=" in req.full_url
        return _FakeResponse(GROUP_TEXT.encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

    entries = mgr.fetch_group(f"{CELESTRAK_BASE}?GROUP=weather&FORMAT=tle")
    assert len(entries) == 3
    catnrs = sorted(e.catnr for e in entries)
    assert catnrs == [25544, 28054, 37214]
    # 群组拉取成功后应进入内存缓存
    assert mgr.get(25544) is not None
    assert mgr.get(28054).line2 == DMSP_L2
    # 且写了磁盘缓存
    assert os.path.exists(os.path.join(str(tmp_path), "25544.tle"))
