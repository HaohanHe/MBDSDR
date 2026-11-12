# SPDX-License-Identifier: MIT
"""A03：AMR 缓存损坏的回退必须可归因（第三批）。

缓存只是加速手段，坏了重算是对的；但原因必须留在日志里，
否则「每次启动都慢」永远查不到是缓存一直在坏。

反证：删除 _load_cached_builtin 里新增的 warning 后本用例变红。
"""
import logging
import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_TESTS_DIR))
sys.path.insert(0, _ROOT)

from mbdsdr_ai import amr  # noqa: E402


def test_corrupt_cache_logs_reason(tmp_path, monkeypatch, caplog):
    cache = tmp_path / "builtin.npz"
    cache.write_bytes(b"not-a-numpy-archive")
    monkeypatch.setattr(amr, "builtin_cache_path", lambda: str(cache))

    with caplog.at_level(logging.WARNING, logger="mbdsdr_ai.amr"):
        ok = amr.AMRClassifier()._load_cached_builtin()

    assert ok is False
    messages = [r.getMessage() for r in caplog.records]
    assert any("缓存不可用" in m for m in messages), "缓存损坏必须留下原因"
    assert any("ValueError" in m or "OSError" in m for m in messages), \
        "日志必须带异常类型，便于区分损坏与版本不符"


def test_missing_cache_is_not_degradation(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(amr, "builtin_cache_path",
                        lambda: str(tmp_path / "nope.npz"))
    with caplog.at_level(logging.WARNING, logger="mbdsdr_ai.amr"):
        assert amr.AMRClassifier()._load_cached_builtin() is False
    assert not [r for r in caplog.records if "缓存不可用" in r.getMessage()]
