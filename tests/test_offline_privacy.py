# SPDX-License-Identifier: MIT
"""Task 63：默认离线 / 隐私边界。

两条判据：

1. **默认环境不发起网络**：未显式设置 ``MBDSDR_ALLOW_NETWORK`` 时，
   ``net_policy.network_allowed()`` 为 False；按名称取 TLE、算可见卫星
   等自动路径的网络入口调用次数必须为 0（socket 连接与 urlopen 都计数）。
2. **相机/位置数据不写日志明文**：静态扫描 ``mbdsdr_ai/`` 与 ``desktop/``
   的日志调用，参数里不得出现 lat/lon/latitude/longitude/坐标 等原始字段。

测试本身不联网、不读真机。
"""
from __future__ import annotations

import ast
import os
import re
import socket
import sys
import urllib.request
from pathlib import Path

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT_PATH = Path(_ROOT)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai import net_policy, orbit  # noqa: E402
from mbdsdr_ai.orbit import TLEUnavailableError  # noqa: E402

CAT = 25544
LOG_METHODS = {"debug", "info", "warning", "warn", "error", "exception", "critical"}
COORD_RE = re.compile(
    r"\b(lat|lon|lats|lons|latitude|longitude|gps_lat|gps_lon|coord|coords|coordinates)\b",
    re.IGNORECASE)


@pytest.fixture(autouse=True)
def _default_env(monkeypatch, tmp_path):
    """还原成产品默认：不设 MBDSDR_OFFLINE / MBDSDR_ALLOW_NETWORK。"""
    monkeypatch.delenv(net_policy.OFFLINE_ENV, raising=False)
    monkeypatch.delenv(net_policy.ALLOW_NETWORK_ENV, raising=False)
    monkeypatch.setenv("MBDSDR_TLE_CACHE_DIR", str(tmp_path / "tle_cache"))
    orbit.reset_tle_cache()
    yield
    orbit.reset_tle_cache()


class _NetworkGuard:
    """计数并阻断网络连接；任何命中都说明默认路径偷偷联网了。"""

    def __init__(self, monkeypatch):
        self.hits = []
        self._mp = monkeypatch

    def __enter__(self):
        guard = self

        def _connect(sock_self, *args, **kwargs):
            guard.hits.append(("socket.connect", args))
            raise AssertionError("默认环境不得发起网络连接")

        def _urlopen(*args, **kwargs):
            guard.hits.append(("urlopen", args))
            raise AssertionError("默认环境不得发起 urlopen")

        self._mp.setattr(socket.socket, "connect", _connect)
        self._mp.setattr(urllib.request, "urlopen", _urlopen)
        return self

    def __exit__(self, *exc):
        return False


def test_network_gate_is_off_by_default():
    assert net_policy.network_allowed() is False
    assert net_policy.network_disabled() is False


def test_explicit_allow_network_flips_gate(monkeypatch):
    monkeypatch.setenv(net_policy.ALLOW_NETWORK_ENV, "1")
    assert net_policy.network_allowed() is True


def test_default_tle_fetch_makes_zero_network_calls(monkeypatch):
    with _NetworkGuard(monkeypatch) as guard:
        with pytest.raises(TLEUnavailableError) as excinfo:
            orbit.fetch_tle_record(CAT)
    assert guard.hits == []
    assert excinfo.value.failure["code"] == "offline"
    assert getattr(excinfo.value, "network_used", None) is False


def test_default_visible_satellites_make_zero_network_calls(monkeypatch):
    from mbdsdr_ai.new_spacetime import compute_visible_satellite_count

    with _NetworkGuard(monkeypatch) as guard:
        result = compute_visible_satellite_count(
            39.9, 116.4, min_elevation=0.0, timestamp=1_700_000_000.0)
    assert guard.hits == []
    assert result["unavailable"] is True
    assert result["network_used"] is False


def _logged_coordinate_calls(path):
    """返回日志调用里出现坐标字段的 ``path:line`` 列表。"""
    source = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = getattr(func, "attr", None) or getattr(func, "id", None)
        if name not in LOG_METHODS:
            continue
        parts = [ast.unparse(arg) for arg in node.args]
        parts += [ast.unparse(kw.value) for kw in node.keywords]
        text = " ".join(parts)
        if COORD_RE.search(text):
            hits.append(f"{path}:{node.lineno}: {text[:120]}")
    return hits


def test_no_coordinate_plaintext_in_log_calls():
    roots = [ROOT_PATH / "mbdsdr_ai", ROOT_PATH / "desktop"]
    violations = []
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            violations.extend(_logged_coordinate_calls(path))
    assert violations == [], (
        "日志调用出现坐标原始字段（默认本机处理，不得明文落日志）：\n"
        + "\n".join(violations[:10]))


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
