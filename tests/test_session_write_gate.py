# SPDX-License-Identifier: MIT
"""会话写门：占用期间拒绝写 + 可读原因；连接流程自身零拒绝。

三条裁定（2026-11-13，用户原话要点）：
  ① 门 = 拒绝 + 可读原因，不静默忽略、不排队延后；
  ② 第一个反证是「正常连接永远不被拒」→ ``begin_session`` 接收初始载荷
     一次性写入，连接参数下发不再与建会话抢时序；
  ③ 被拒必须证明硬件真没变：``backend.set_*`` 一次都没被调用、store 意图
     保持原值、给用户的原因非空——三个一起断，单看返回值 False 不算数。
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DESKTOP = os.path.join(_ROOT, "desktop")
for _p in (_ROOT, _DESKTOP):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402

# 与桌面端同一导入方式（裸名）：sdr_tools 只认 sys.modules 里的这一个对象，
# 包名导入会造出第二份空存储，写门就永远开着。
import connect_request_store as store  # noqa: E402

_KEY = "rtlsdr|serial:A-0001"


@pytest.fixture(autouse=True)
def _clean_store():
    store.reset()
    yield
    store.reset()


# ---------------------------------------------------------------- store 写门


def test_connect_flow_is_never_rejected():
    """裁定②：正常连接流程（写意图 → 载荷 → 建会话）一次都不被拒。

    门关上的时刻严格在 ``begin_session`` 返回之后；连接参数下发本身是
    ``backend.set_*``，不经过 store，因此不会撞上自己的门。
    """
    assert not store.update(
        _KEY, sample_rate_hz=2_048_000.0, gain_db=30.0, ppm=0).rejected

    payload = store.fill_payload({}, _KEY)
    # 故意让「实际下发值」与之前写入的意图不同（主窗口连接时用的是调用方
    # 参数，不是 store 现值）。会话起点必须以实际下发值顶上去，且这一步
    # 不经过写门——否则连接流程会被自己的门拒掉。
    initial = store.ConnectRequest(
        sample_rate_hz=payload[store.CONNECT_SAMPLE_RATE_KEY],
        gain_db=35.0,
        ppm=payload["_ppm"],
        bandwidth_hz=None,
    )
    snap = store.begin_session(_KEY, initial=initial)

    assert store.is_occupied(_KEY)
    assert snap.sample_rate_hz == 2_048_000.0
    assert snap.gain_db == 35.0, "会话起点没采用实际下发值"
    assert store.intent_for(_KEY).gain_db == 30.0, \
        "建会话不应改写意图：意图=用户想设的，快照=实际生效的，写回时才合一"
    assert snap.ppm == 0
    # 建会话之后读到的就是这份快照，不会再被连接流程自己改写。
    assert store.request_for(_KEY) == snap


def test_session_write_is_rejected_with_reason_and_keeps_intent():
    """裁定①③：会话中写入被拒，store 意图与读侧都保持原值，原因非空。"""
    store.update(_KEY, sample_rate_hz=2_048_000.0, gain_db=30.0, ppm=0)
    store.begin_session(_KEY)

    result = store.update(_KEY, sample_rate_hz=1_024_000.0, gain_db=40.0, ppm=5)

    assert result.rejected is True
    assert result.reason, "拒绝必须给可读原因，不能静默忽略"
    assert "会话" in result.reason
    assert store.intent_for(_KEY) == store.ConnectRequest(
        sample_rate_hz=2_048_000.0, gain_db=30.0, ppm=0), \
        "被拒的写入改动了意图值"
    assert store.request_for(_KEY) == store.intent_for(_KEY)


def test_rejected_write_notifies_read_side():
    """被拒也要刷新读侧：控件不能停在被拒的新值上（假回显）。"""
    seen = []
    listener = lambda key: seen.append(key)  # noqa: E731
    store.subscribe(listener)
    try:
        store.begin_session(_KEY)
        assert store.update(_KEY, gain_db=40.0).rejected
        assert seen == [_KEY], "被拒后没有触发读侧重渲染"
    finally:
        store.unsubscribe(listener)


def test_gate_reopens_after_session_end():
    """反证：门不是「永远拒写」——会话结束后同一笔写入正常生效。"""
    store.update(_KEY, gain_db=30.0)
    store.begin_session(_KEY)
    assert store.update(_KEY, gain_db=40.0).rejected

    store.end_session(_KEY)
    result = store.update(_KEY, gain_db=40.0)

    assert not result.rejected
    assert store.intent_for(_KEY).gain_db == 40.0


def test_begin_session_initial_is_validated_not_bypassed():
    """初始载荷也走同一处 update 校验：0 Hz 被拒，不能靠 initial 塞进快照。"""
    store.update(_KEY, sample_rate_hz=2_048_000.0)
    store.begin_session(
        _KEY, initial=store.ConnectRequest(sample_rate_hz=0.0, gain_db=12.0))

    snap = store.session_values(_KEY)
    assert snap.sample_rate_hz == 2_048_000.0, \
        "initial 绕过了 update 的采样率校验（0 Hz 被写进快照）"
    assert snap.gain_db == 12.0


# ---------------------------------------------------------------- AI 工具路径


class _Dev:
    driver = "rtlsdr"
    name = "RTL-A"
    # 与桌面端 device_panel 连接时同源的规范化身份（键必须完全一致）
    identity = {"driver": "rtlsdr", "serial": "A-0001",
                "label": "RTL-A", "device_args": {"index": 0}}


class _Backend:
    def __init__(self):
        self.device = _Dev()
        self.calls = []

    def set_sample_rate(self, value):
        self.calls.append(("set_sample_rate", value))
        return True

    def set_bandwidth(self, value):
        self.calls.append(("set_bandwidth", value))
        return True

    def set_gain(self, value):
        self.calls.append(("set_gain", value))
        return True

    def set_agc(self, value):
        self.calls.append(("set_agc", value))
        return True

    def set_ppm(self, value):
        self.calls.append(("set_ppm", value))
        return True


class _Mgr:
    def __init__(self, backend):
        self._backend = backend

    def get_active(self):
        return self._backend


class _Agent:
    def __init__(self, mgr):
        from mbdsdr_ai.tool_registry import ToolRegistry
        self.sdr_manager = mgr
        self.tool_registry = ToolRegistry()


def _agent(backend):
    from mbdsdr_ai.sdr_tools import register_sdr_tools
    agent = _Agent(_Mgr(backend))
    register_sdr_tools(agent)
    return agent


def _identity_key():
    from mbdsdr_ai.device_identity import describe
    return describe(dict(_Dev.identity)).key


_AI_WRITES = [
    ("sdr_set_sample_rate", {"sample_rate_hz": 1_024_000}),
    ("sdr_set_bandwidth", {"bandwidth_hz": 12_500}),
    ("sdr_set_gain", {"gain_db": 40}),
    ("sdr_set_agc", {"enabled": True}),
]


def test_ai_write_tools_are_blocked_and_never_touch_hardware():
    """裁定③：AI 工具被拒时 backend.set_* 零调用、意图原值、原因非空。"""
    backend = _Backend()
    agent = _agent(backend)
    key = _identity_key()
    assert key == _KEY, "测试身份键与桌面端键不同源，写门会对不上"

    store.update(key, sample_rate_hz=2_048_000.0, gain_db=30.0, ppm=0)
    store.begin_session(key)

    for name, args in _AI_WRITES:
        result = agent.tool_registry.call(name, dict(args))
        assert result.success is False, f"{name} 在会话占用期间不该成功"
        assert result.content, f"{name} 被拒却没说原因"
        assert "会话" in result.content
        assert result.error == "device_session_locked"

    assert backend.calls == [], \
        f"被拒的写仍然调了硬件 setter：{backend.calls}"
    assert store.intent_for(key) == store.ConnectRequest(
        sample_rate_hz=2_048_000.0, gain_db=30.0, ppm=0), \
        "被拒的 AI 写改动 store 意图"


def test_ai_write_tools_work_after_session_end():
    """反证：门只挡会话期间；同一工具在会话结束后正常下发到硬件。"""
    backend = _Backend()
    agent = _agent(backend)
    key = _identity_key()
    store.update(key, gain_db=30.0)
    store.begin_session(key)
    store.end_session(key)

    result = agent.tool_registry.call("sdr_set_gain", {"gain_db": 40})

    assert result.success is True
    assert backend.calls == [("set_gain", 40)]
    assert store.intent_for(key).gain_db == 30.0, \
        "AI 工具直写后端时不应顺手改桌面端意图（那是另一条路径的事）"
