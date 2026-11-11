# SPDX-License-Identifier: MIT
"""能力来源红线：来源缺省 = 未知，绝不回退成「按声明」。

产品裁定（2026-11-11）：未知能力只显示「未知」，依赖它的操作按不可用处理；
「按声明」也是假数据——声明必须有据（厂商文档/驱动常量），且文案标
「声明支持，未验证」。本文件钉住三件事：

1. 只有范围数值、没有 ``capability_status`` → 未知（不显示为可用）；
2. 裸 ``supports_tx`` 布尔、没有来源 → 未知（不显示「可发射」）；
3. 后端设备对象没标来源 → 不生成采样率档位。

反证：把 ``_range_field`` 里恢复 ``status = STATUS_DECLARED``，第 1 条即红。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from mbdsdr_ai.device_capabilities import (  # noqa: E402
    STATUS_DECLARED,
    STATUS_PROBED,
    STATUS_UNKNOWN,
    build_capability_report,
    sample_rate_options,
    sample_rate_options_for_backend,
)


def test_range_without_source_is_unknown_not_declared():
    report = build_capability_report({
        "freq_range": (500_000, 1_766_000_000),
        "sample_rate_range": (225_001, 3_200_000),
        # 故意不给 capability_status：没有来源就是未知。
    })

    for key in ("freq_range", "sample_rate_range"):
        field = report[key]
        assert field.status == STATUS_UNKNOWN, f"{key} 无来源不得算 declared"
        assert field.known is False
        assert "未知" in field.text
        assert "MHz" not in field.text and "kHz" not in field.text, \
            f"{key} 未知时不得显示任何量程数值（冒充可用）"


def test_declared_source_is_explicit_and_labeled_unverified():
    report = build_capability_report({
        "freq_range": (500_000, 1_766_000_000),
        "capability_status": {"freq_range": STATUS_DECLARED},
    })

    field = report["freq_range"]
    assert field.status == STATUS_DECLARED
    assert field.known is True
    assert "1.77 GHz" in field.text
    assert "声明支持，未验证" in field.text, "声明值必须显式标未验证"


def test_probed_source_is_labeled_probed():
    report = build_capability_report({
        "sample_rate_range": (1_000_000, 2_000_000),
        "capability_status": {"sample_rate_range": STATUS_PROBED},
    })

    assert report["sample_rate_range"].status == STATUS_PROBED
    assert "探测" in report["sample_rate_range"].text


def test_bare_supports_tx_without_source_is_unknown():
    report = build_capability_report({"supports_tx": True})

    field = report["tx"]
    assert field.status == STATUS_UNKNOWN
    assert not field.known
    assert "可发射" not in field.text, "没有来源的布尔值不得显示为可发射"
    assert "未知" in field.text


def test_supports_tx_with_declared_source_is_marked_unverified():
    report = build_capability_report({
        "supports_tx": True,
        "capability_status": {"tx": STATUS_DECLARED},
    })

    field = report["tx"]
    assert field.status == STATUS_DECLARED
    assert "可发射" in field.text
    assert "未验证" in field.text, "声明发射能力必须标未验证"


def test_antennas_without_source_is_unknown():
    report = build_capability_report({"antennas": ["RX2", "TX/RX"]})

    field = report["antenna"]
    assert field.status == STATUS_UNKNOWN
    assert "RX2" not in field.text, "天线口没有来源时不得列出具体口"
    assert "未知" in field.text


def test_sample_rate_options_without_source_is_none():
    assert sample_rate_options({
        "driver": "rtlsdr",
        "sample_rate_range": (225_001, 3_200_000),
    }) is None, "没标来源时不得编出 11 档"


class _Dev:
    device_type = "rtlsdr"
    sample_rate_range = (225_001, 3_200_000)


class _Backend:
    def __init__(self, device):
        self.device = device


def test_backend_options_without_source_is_none():
    assert sample_rate_options_for_backend(_Backend(_Dev())) is None


def test_backend_options_with_declared_source_is_available():
    class _DevDeclared(_Dev):
        capability_status = {"sample_rate_range": STATUS_DECLARED}
        capability_note = {}

    opts = sample_rate_options_for_backend(_Backend(_DevDeclared()))
    assert opts, "显式声明来源时仍应给出后端能力档位"
    assert all(225_001 <= int(r) <= 3_200_000 for r in opts)
