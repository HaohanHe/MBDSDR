# SPDX-License-Identifier: MIT
"""同一条连接错误，在「人话文案」与「补救码」两条路径上必须同分类。

回归背景：``_humanize_error`` 与 ``classify_connect_error`` 曾各自维护一套
关键词表且分支顺序相反——``"Permission denied [Errno 13]"`` 的文案说
「USB 权限不足」，补救链接却指向「设备被占用，请关闭 SDR#/GQRX」。
用户照着关掉别人的程序，再连仍然失败。

现在两处都由 ``error_remedy.CONNECT_ERROR_RULES`` 一张有序表产出；
把 permission 挪到 busy 之后（或把裸 ``errno`` 放回占用关键词）本文件必红。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import error_remedy  # noqa: E402
from main_window import MainWindow  # noqa: E402


CASES = (
    ("could not open device: Permission denied [Errno 13]", "usb_permission"),
    # libusb 的 ACCESS(=EACCES) 是权限错误，不能被同串里的 "busy" 抢走
    ("LIBUSB_ERROR_ACCESS device busy", "usb_permission"),
    ("Resource busy: device is used by another program", "device_busy"),
    ("[Errno 111] Connection refused", "network_unreachable"),
    ("No devices found", "device_not_found"),
    ("librtlsdr.dll not found", "driver_not_found"),
    ("No module named 'SoapySDR'", "missing_dependency"),
    ("USB bulk transfer failed: LIBUSB_ERROR_NO_DEVICE", "usb_disconnected"),
    ("Invalid sample rate 3.2M", "sample_rate_not_supported"),
)


def test_permission_denied_is_not_classified_as_busy():
    """EACCES 是权限问题，补救动作是改系统设置，不是去关别人的程序。"""
    msg = "could not open device: Permission denied [Errno 13]"
    code = error_remedy.classify_connect_error(msg)
    assert code == "usb_permission", f"实际 {code!r}"
    text = MainWindow._humanize_error(msg)
    assert "权限" in text
    assert "占用" not in text
    assert "关闭 SDR#" not in text


def test_text_and_code_come_from_the_same_classification():
    """每个样本：文案必须等于该码对应的文案，不能一条说 A、一条指向 B。"""
    for msg, expected in CASES:
        code = error_remedy.classify_connect_error(msg)
        assert code == expected, f"{msg!r} 被分类成 {code!r}"
        assert MainWindow._humanize_error(msg) == error_remedy.CONNECT_ERROR_TEXT[code], msg


def test_permission_has_a_real_remedy_entry():
    """既然分类出了 usb_permission，就必须有可点开的条目，不能是空链接。"""
    assert error_remedy.remedy_for_code("usb_permission") is not None
    assert error_remedy.mapping_gaps() == []
