# SPDX-License-Identifier: MIT
"""``tests/sdr_toolchain.py`` 的契约回归：只有「没硬件」能 skip。

背景（2026-11-12）：两份集成用例原本把 device_id 写死 ``rtl_0``，连不上就
``pytest.skip``。设备身份键换成 ``driver|serial:...`` 之后必然 skip——用例
是绿的，SDR 工具链却一次都没跑过。这里用假注册表把「拿不到硬件」和
「拿不到 id / 契约变了」分开钉死：

* 枚举为空 → ``pytest.skip``（允许）；
* 枚举失败 / 返回不是 JSON 列表 / 条目缺 ``device_id`` / 枚举到却连不上
  → ``AssertionError``（必须红，且说清原因）。

其中「枚举到 A、却拿 B 去连」这条（相当于把 id 写回 ``rtl_0``）专门断言
``sdr_connect`` 收到的就是枚举出来的 id：写死任何字符串都会在这里红。
"""
from __future__ import annotations

import json

import pytest

from sdr_toolchain import connect_first_device_or_skip, first_enumerated_device_id


class _Result:
    def __init__(self, success, content=""):
        self.success = success
        self.content = content


class _FakeRegistry:
    """最小工具注册表：只认 sdr_list_devices / sdr_connect，记录调用参数。"""

    def __init__(self, list_result=None, connect_result=None, list_raises=None):
        self._list_result = list_result
        self._connect_result = connect_result
        self._list_raises = list_raises
        self.calls = []

    def call(self, name, args):
        self.calls.append((name, dict(args)))
        if name == "sdr_list_devices":
            if self._list_raises is not None:
                raise self._list_raises
            return self._list_result
        if name == "sdr_connect":
            return self._connect_result
        raise AssertionError(f"未预期的工具调用：{name}")

    def connect_args(self):
        return [a for n, a in self.calls if n == "sdr_connect"]


def _devices(*ids):
    return _Result(True, json.dumps([{"device_id": i} for i in ids], ensure_ascii=False))


def _assert_red(tr):
    """必须抛 AssertionError——skip 是通过不了的（Skipped 不是 AssertionError）。"""
    try:
        connect_first_device_or_skip(tr)
    except BaseException as e:  # noqa: BLE001 - 这里要区分 skip 与红
        assert type(e) is AssertionError, f"必须红，实际 {type(e).__name__}: {e}"
        return str(e)
    raise AssertionError("枚举到设备却连不上时必须红，实际静默通过了")


def test_empty_enumeration_skips_as_no_hardware():
    """枚举为空 = 本机真没硬件，允许 skip（这是唯一允许 skip 的情形）。"""
    tr = _FakeRegistry(list_result=_Result(True, "[]"))
    with pytest.raises(pytest.skip.Exception):
        connect_first_device_or_skip(tr)
    assert tr.connect_args() == []  # 没设备时不去瞎连


def test_enumeration_failure_is_red_not_skip():
    """枚举本身失败不是「没插设备」：工具面坏了必须红。"""
    tr = _FakeRegistry(list_result=_Result(False, "枚举内部错误"))
    msg = _assert_red(tr)
    assert "sdr_list_devices" in msg and "枚举内部错误" in msg


def test_enumeration_exception_is_red():
    tr = _FakeRegistry(list_raises=RuntimeError("boom"))
    msg = _assert_red(tr)
    assert "接口契约已变" in msg and "boom" in msg


def test_non_json_enumeration_is_red():
    tr = _FakeRegistry(list_result=_Result(True, "设备列表：RTL-SDR #0"))
    msg = _assert_red(tr)
    assert "不是 JSON" in msg


def test_device_item_without_device_id_is_red():
    """枚举到设备但缺 device_id = 契约变了，不许当成「没硬件」跳过。"""
    tr = _FakeRegistry(list_result=_Result(True, json.dumps([{"index": 0}])))
    msg = _assert_red(tr)
    assert "device_id" in msg


def test_connect_failure_is_red_and_uses_enumerated_id():
    """枚举到 A 却连不上 → 红，并且连的就是 A。

    这条同时是「把 id 写回 rtl_0」的反证：helper 若写死任何别的字符串，
    ``connect_args()`` 就不再等于枚举结果，断言直接失败。
    """
    tr = _FakeRegistry(list_result=_devices("driver|serial:00000001"),
                       connect_result=_Result(False, "设备不可用：枚举里没有它"))
    msg = _assert_red(tr)
    assert "driver|serial:00000001" in msg
    assert tr.connect_args() == [{"device_id": "driver|serial:00000001"}]


def test_connect_success_returns_enumerated_id():
    tr = _FakeRegistry(list_result=_devices("a", "b"),
                       connect_result=_Result(True, "已连接"))
    assert connect_first_device_or_skip(tr) == "a"
    assert tr.connect_args() == [{"device_id": "a"}]


def test_first_id_prefers_enumeration_order():
    """取的是枚举顺序里第一台（不是排序后第一台），与 sdr_list_devices 一致。"""
    tr = _FakeRegistry(list_result=_devices("z|0", "a|1"))
    assert first_enumerated_device_id(tr) == "z|0"
