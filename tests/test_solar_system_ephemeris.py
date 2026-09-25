"""
tests/test_solar_system_ephemeris.py
=====================================
验证 mbdsdr_ai/solar_system.py 的 de421.bsp 自动下载 + 后台热切换 + VSOP87 回退：

  1. 缓存目录自动创建；
  2. download_de421 下载逻辑（mock urlopen，不真下载）；
  3. 本地已有完整 .bsp 时直接命中缓存、不重复下载；
  4. 下载失败时回退到 astropy（VSOP87）后端，不抛异常；
  5. 返回值包含 backend 字段并正确标注当前后端；
  6. 后台下载完成后可热切换到 de421_bsp；
  7. 后台下载在 daemon 线程进行，不阻塞构造函数。

运行：
    python3 -m pytest tests/test_solar_system_ephemeris.py -v
"""
import os
import sys
import time
import threading

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from mbdsdr_ai import solar_system as s  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_ephemeris_dir(tmp_path, monkeypatch):
    """把星历缓存目录重定向到临时目录，避免污染真实 ~/.mbdsdr。"""
    eph_dir = str(tmp_path / "ephemeris")
    monkeypatch.setattr(s, "EPHEMERIS_DIR", eph_dir)
    monkeypatch.setattr(s, "DE421_PATH", os.path.join(eph_dir, "de421.bsp"))
    # 每个用例重置全局单例
    monkeypatch.setattr(s, "_backend", None)
    yield eph_dir


def _touch_fake_bsp(path: str, nbytes: int = s.DE421_MIN_BYTES + 1024):
    """写一个足够大的假 .bsp 文件（仅用于大小校验，不真解析）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"#" * nbytes)
    return path


class _FakeResp:
    """urllib.urlopen 的假上下文管理器，配合 patch 掉 copyfileobj。"""
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_ephemeris_dir_creation(monkeypatch):
    """缓存目录在需要时自动创建。"""
    assert not os.path.isdir(s.EPHEMERIS_DIR)
    created = {}

    def _fake_urlopen(req, timeout=0):
        created["made"] = os.path.isdir(s.EPHEMERIS_DIR)
        return _FakeResp()

    def _fake_copy(src, dst, length=0):
        dst.write(b"#" * (s.DE421_MIN_BYTES + 1))  # 写够大小

    monkeypatch.setattr(s.urllib.request, "urlopen", _fake_urlopen)
    monkeypatch.setattr(s.shutil, "copyfileobj", _fake_copy)

    path = s.download_de421(timeout=5)
    assert os.path.isdir(s.EPHEMERIS_DIR)          # 目录已建
    assert path == s.DE421_PATH                     # 下载成功返回正式路径
    assert created.get("made") is True


def test_download_with_mock(monkeypatch):
    """mock urlopen 验证下载→校验→原子重命名，返回路径。"""
    calls = {"urls": []}

    def _fake_urlopen(req, timeout=0):
        calls["urls"].append(req.full_url)
        return _FakeResp()

    written = {}

    def _fake_copy(src, dst, length=0):
        dst.write(b"#" * (s.DE421_MIN_BYTES + 5))
        written["path"] = getattr(dst, "name", None)

    monkeypatch.setattr(s.urllib.request, "urlopen", _fake_urlopen)
    monkeypatch.setattr(s.shutil, "copyfileobj", _fake_copy)

    path = s.download_de421(timeout=10)
    assert path == s.DE421_PATH
    assert os.path.isfile(path)
    assert s._file_valid(path)
    # 临时 .part 已被重命名，不应残留
    assert not os.path.exists(path + ".part")
    # 至少请求了主 URL
    assert any("naif.jpl.nasa.gov" in u for u in calls["urls"])


def test_local_cache_detection(monkeypatch):
    """已有完整 .bsp → 直接命中，不发起网络请求。"""
    _touch_fake_bsp(s.DE421_PATH)

    def _must_not_call(*a, **k):
        raise AssertionError("不应在缓存命中时发起下载")

    monkeypatch.setattr(s.urllib.request, "urlopen", _must_not_call)
    path = s.download_de421()
    assert path == s.DE421_PATH


def test_download_failure_fallback(monkeypatch):
    """下载失败返回 None 且不抛异常；后端停留在 VSOP87 回退。"""
    def _boom(req, timeout=0):
        raise OSError("network unreachable")

    monkeypatch.setattr(s.urllib.request, "urlopen", _boom)
    assert s.download_de421(timeout=2) is None

    # 构造后端：本地无 bsp，下载被 mock 成失败 → astropy 回退
    be = s._EphemerisBackend(auto_download=False)
    assert be.kind == "vsop87_astropy"
    assert be.backend_name == "vsop87_astropy[回退]"
    assert be.available is True


def test_backend_name_in_result(monkeypatch):
    """结果 to_dict() 必须含 backend 字段，且与后端名一致。"""
    be = s._EphemerisBackend(auto_download=False)
    from datetime import datetime, timezone
    pos = s.get_sun_position(datetime(2025, 6, 21, 12, 0, tzinfo=timezone.utc),
                             s.BEIJING)
    assert pos is not None
    d = pos.to_dict()
    assert "backend" in d
    assert d["backend"] == be.backend_name == "vsop87_astropy[回退]"
    assert "precision" in d and "arcsec" in d["precision"]


def test_hot_switch(monkeypatch):
    """后台下载完成后 _hot_swap_to_bsp 把后端切到 de421_bsp。"""
    be = s._EphemerisBackend(auto_download=False)
    assert be.kind == "vsop87_astropy"

    # 构造一个 jplephem 引擎句柄（spk 用占位对象，不真正算位置）
    fake_handle = s._EphemerisHandle(engine="jplephem", spk=object())
    be._hot_swap_to_bsp(fake_handle)

    assert be.kind == "de421_bsp"
    assert be.backend_name == "de421_bsp"
    assert be._engine == "jplephem"
    assert "亚角秒" in be.precision_note


def test_background_download_nonblocking(monkeypatch):
    """后台下载在 daemon 线程进行，构造函数不阻塞；完成后热切换。"""
    ready = threading.Event()
    proceed = threading.Event()

    def _slow_download(timeout=60):
        ready.set()
        proceed.wait(timeout=5)            # 模拟慢速下载
        return None                         # 失败：保持回退

    monkeypatch.setattr(s, "download_de421", _slow_download)

    t0 = time.time()
    be = s._EphemerisBackend()            # auto_download=True
    elapsed = time.time() - t0
    # 构造函数立刻返回（不等待下载）
    assert elapsed < 1.0
    ready.wait(timeout=2)
    assert be.download_in_progress is True
    assert be.kind == "vsop87_astropy"   # 下载未完成期间用回退
    proceed.set()
    # 等待线程结束
    t = be._download_thread
    if t is not None:
        t.join(timeout=5)
    assert not t.is_alive()


def test_invalid_local_file_triggers_redownload(monkeypatch):
    """本地 .bsp 太小（<10MB）视为无效，应重新下载。"""
    os.makedirs(s.EPHEMERIS_DIR, exist_ok=True)
    with open(s.DE421_PATH, "wb") as f:
        f.write(b"tiny")                  # 不达标

    called = {"n": 0}

    def _fake_urlopen(req, timeout=0):
        called["n"] += 1
        return _FakeResp()

    def _fake_copy(src, dst, length=0):
        dst.write(b"#" * (s.DE421_MIN_BYTES + 1))

    monkeypatch.setattr(s.urllib.request, "urlopen", _fake_urlopen)
    monkeypatch.setattr(s.shutil, "copyfileobj", _fake_copy)

    path = s.download_de421()
    assert path == s.DE421_PATH
    assert called["n"] >= 1
    assert s._file_valid(path)
