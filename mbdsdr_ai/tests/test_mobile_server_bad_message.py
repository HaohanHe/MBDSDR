# SPDX-License-Identifier: MIT
"""A03：手机端非法消息不得被静默丢弃（第二批）。

反证：把 handle 的 json 解析异常改回 ``except Exception: continue`` 后，
本用例收不到 error 回执 → 变红。
"""
import asyncio
import json
import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_TESTS_DIR))
sys.path.insert(0, _ROOT)

from mbdsdr_ai.mobile_server import MobileHub  # noqa: E402


class _FakeWS:
    def __init__(self, messages):
        self._messages = list(messages)
        self.sent = []

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._messages:
            raise StopAsyncIteration
        return self._messages.pop(0)

    async def send(self, text):
        self.sent.append(text)


def test_bad_json_gets_error_receipt_and_loop_continues():
    hub = MobileHub()
    ws = _FakeWS([
        "{ not json",
        json.dumps({"type": "handshake", "payload": {"device": "phone"}}),
    ])

    asyncio.run(hub.handle(ws))

    frames = [json.loads(s) for s in ws.sent]
    assert any(f.get("type") == "error" and f["payload"]["code"] == "bad_json"
               for f in frames), "非法消息必须回执，不能静默丢弃"
    assert any(f.get("type") == "hello" for f in frames), "一条坏消息不能打断后续消息"


def test_non_object_json_gets_error_receipt():
    hub = MobileHub()
    ws = _FakeWS([json.dumps([1, 2, 3])])

    asyncio.run(hub.handle(ws))

    frames = [json.loads(s) for s in ws.sent]
    assert any(f.get("type") == "error" and f["payload"]["code"] == "bad_message"
               for f in frames)
