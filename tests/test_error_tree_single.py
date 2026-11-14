# SPDX-License-Identifier: MIT
"""错误树收成一条后的跨树用例。

历史：``error_handler``（桌面/启动链）与 ``core.errors``（MCP/CLI 链）两棵树
构造签名第 2/3 个参数顺序相反，``format_error`` 只认自己那棵的 ``isinstance``，
``core.errors`` 侧实例会落进 ``unexpected_error`` 兜底。这里把合并后的契约钉住。
"""

import inspect

from mbdsdr_ai import error_handler
from mbdsdr_ai.core import errors as core_errors


def test_two_import_paths_are_the_same_class():
    # 不是 alias 两个类，而是同一个类对象（定义只有一处）。
    assert core_errors.SDRUserError is error_handler.SDRUserError
    assert core_errors.DeviceBusyError is error_handler.DeviceBusyError
    assert core_errors.OwnerRegistryUnavailableError is \
        error_handler.OwnerRegistryUnavailableError


def test_format_error_core_busy_hits_busy_branch():
    # 旧行为：core 侧实例落 unexpected_error 兜底。
    err = core_errors.DeviceBusyError("被另一个 mbdsdr 实例占用")
    ui = error_handler.format_error(err)
    assert ui["title"] == "设备被占用"
    assert ui["title"] != error_handler.spec("unexpected_error").meaning
    assert ui["message"] == "被另一个 mbdsdr 实例占用"
    assert "unexpected" not in ui["title"].lower()


def test_format_error_core_only_class_also_hits_its_branch():
    # OwnerRegistryUnavailableError 原先只存在于 core 树，UI 树根本没有它。
    ui = error_handler.format_error(
        core_errors.OwnerRegistryUnavailableError("登记文件读不出来"))
    assert ui["title"] == "设备占用登记不可读"
    assert ui["title"] != error_handler.spec("unexpected_error").meaning


def test_signature_order_is_message_suggestion_detail():
    params = list(inspect.signature(error_handler.SDRUserError).parameters)
    assert params == ["message", "suggestion", "technical_detail"]
    err = error_handler.DeviceBusyError("m", "s", "t")
    assert (err.message, err.suggestion, err.technical_detail) == ("m", "s", "t")
    assert err.to_dict()["technical_detail"] == "t"


def test_to_dict_and_to_ui_stay_separate_contracts():
    err = core_errors.DeviceBusyError("占用", "关掉别的程序", "LIBUSB_ERROR_BUSY")
    assert err.to_dict() == {
        "error": "device_busy",
        "message": "占用",
        "suggestion": "关掉别的程序",
        "technical_detail": "LIBUSB_ERROR_BUSY",
    }
    ui = err.to_ui()
    assert set(ui) == {"title", "message", "suggestion"}
    assert ui["title"] == "设备被占用"
    # UI 契约不暴露技术细节
    assert "LIBUSB_ERROR_BUSY" not in str(ui)


def test_default_suggestion_still_works_from_either_side():
    for cls in (core_errors.DeviceBusyError, error_handler.DeviceBusyError):
        err = cls("占用")
        assert "关闭" in err.suggestion and "释放" in err.suggestion
        assert err.to_dict()["suggestion"] == err.to_ui()["suggestion"]
