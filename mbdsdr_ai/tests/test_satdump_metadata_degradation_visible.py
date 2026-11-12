# SPDX-License-Identifier: MIT
"""A03：satdump product.cbor 不可用的回退必须可归因（第三批）。

product.cbor 是可选增强，坏了退回文件名规则是对的；但静默 None
会让「按清单取图没生效」和「本来就没有 product.cbor」看起来一样。

反证：删除 _load_product_metadata 里新增的 warning 后本用例变红。
"""
import logging
import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_TESTS_DIR))
sys.path.insert(0, _ROOT)

from mbdsdr_ai import satdump_integration as si  # noqa: E402


class _BadCbor:
    @staticmethod
    def loads(_data):
        raise ValueError("bad cbor")


def test_corrupt_product_cbor_logs_reason(tmp_path, monkeypatch, caplog):
    (tmp_path / "product.cbor").write_bytes(b"\xff\xff\xff")
    monkeypatch.setattr(si, "_cbor2", _BadCbor)

    with caplog.at_level(logging.WARNING, logger="mbdsdr_ai.satdump_integration"):
        assert si._load_product_metadata(tmp_path) is None

    messages = [r.getMessage() for r in caplog.records]
    assert any("product.cbor 不可用" in m for m in messages), "回退必须留下原因"
    assert any("ValueError" in m for m in messages)
