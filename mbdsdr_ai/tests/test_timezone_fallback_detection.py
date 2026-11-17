# SPDX-License-Identifier: MIT
"""时区兜底降级必须可被调用方检测（不是拿到一个看着正常的时区对象）。

判据（本文件）：

1. 三态可区分：``system-rules`` / ``fixed-offset`` / ``unavailable``；
2. 构造「系统时区不可发现」的环境（``_probe_system_tz`` 返回 None）时，
   调用方能判定降级：``resolve_tz()`` 返回对象自带标记、``tz_degradation``
   返回 ``degraded=True``、``local_time_note()`` 给得出固定偏移说明；
3. 正常环境（探测到带规则的时区）判定为未降级，说明为空；
4. 降级时区是冻结偏移：同一时区在冬/夏两个时刻偏移相同、``dst()==0``；
5. 本地时间的呈现点拿到同一句说明（时间机器输入框 / 时间信息
   ``local_note``），不让用户以为时间是对的。天空状态卡那条要 import Qt，
   按分层约定放在 ``tests/test_sky_local_clock_degraded_note.py``。

反证（见提交说明）：把 ``_FixedOffsetTZ`` 的 ``mbdsdr_degraded`` 标记去掉，
本文件断言降级的用例必须变红。

运行：python -m pytest mbdsdr_ai/tests/test_timezone_fallback_detection.py -v
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from mbdsdr_ai import time_format as tf  # noqa: E402

UTC = timezone.utc


def _system_tz_unavailable(monkeypatch):
    """构造「系统时区不可发现」：探测不到 DST 规则。"""
    monkeypatch.setattr(
        tf, "_probe_system_tz", lambda: (None, "测试注入：系统时区不可发现"))


def _system_tz_with_rules(monkeypatch):
    """构造「系统时区带规则」：探测直接给出一个真实 tzinfo。"""
    monkeypatch.setattr(
        tf, "_probe_system_tz",
        lambda: (ZoneInfo("UTC"), "zoneinfo:UTC"))


class TestThreeStates:
    def test_system_rules_is_not_degraded(self, monkeypatch):
        _system_tz_with_rules(monkeypatch)
        res = tf.resolve_tz_info()
        assert res.state == tf.TZ_STATE_SYSTEM_RULES
        assert res.degraded is False
        assert res.note == ""
        assert tf.local_time_note() == ""
        assert tf.tz_degradation(res.tz)["degraded"] is False
        assert tf.tz_degradation(tf.resolve_tz())["degraded"] is False

    def test_fixed_offset_is_detected(self, monkeypatch):
        _system_tz_unavailable(monkeypatch)
        res = tf.resolve_tz_info()
        assert res.state == tf.TZ_STATE_FIXED_OFFSET
        assert res.degraded is True
        # 说明必须给出「固定偏移」「不随夏令时调整」两个要点
        assert "固定偏移" in res.note
        assert "不随夏令时调整" in res.note
        assert tf.local_time_note() == tf.DEGRADED_TZ_NOTE

        # ① 返回对象自身可判定（不靠猜类型）
        tz = tf.resolve_tz()
        assert getattr(tz, "mbdsdr_degraded", False) is True
        assert getattr(tz, "mbdsdr_state", "") == tf.TZ_STATE_FIXED_OFFSET

        # ② 可查询状态：同一个对象经查询接口给出同一结论
        info = tf.tz_degradation(tz)
        assert info["degraded"] is True
        assert info["state"] == tf.TZ_STATE_FIXED_OFFSET
        assert info["note"] == tf.DEGRADED_TZ_NOTE

    def test_unavailable_is_detected(self, monkeypatch):
        _system_tz_unavailable(monkeypatch)
        monkeypatch.setattr(tf, "_current_fixed_offset", lambda: None)
        res = tf.resolve_tz_info()
        assert res.state == tf.TZ_STATE_UNAVAILABLE
        assert res.degraded is True
        assert res.tz is UTC
        assert res.note == tf.UNAVAILABLE_TZ_NOTE
        assert tf.local_time_note() == tf.UNAVAILABLE_TZ_NOTE

    def test_live_environment_is_self_consistent(self):
        """真实环境：state 与 degraded 必须一致（不预设本机一定有无规则）。"""
        res = tf.resolve_tz_info()
        assert res.state in (tf.TZ_STATE_SYSTEM_RULES, tf.TZ_STATE_FIXED_OFFSET,
                             tf.TZ_STATE_UNAVAILABLE)
        assert res.degraded is (res.state != tf.TZ_STATE_SYSTEM_RULES)
        assert bool(res.note) is res.degraded


class TestFixedOffsetDoesNotFollowDst:
    def test_summer_and_winter_share_the_same_offset(self, monkeypatch):
        _system_tz_unavailable(monkeypatch)
        tz = tf.resolve_tz()
        winter = datetime(2026, 1, 15, 12, tzinfo=UTC).astimezone(tz)
        summer = datetime(2026, 7, 15, 12, tzinfo=UTC).astimezone(tz)
        assert winter.utcoffset() == summer.utcoffset()
        assert summer.dst() == timedelta(0)
        assert winter.dst() == timedelta(0)


class TestCallersCanSeeDegradation:
    def test_time_basis_reexports_query_api(self, monkeypatch):
        _system_tz_unavailable(monkeypatch)
        from mbdsdr_ai import time_basis as tb

        assert tb.local_time_note() == tf.DEGRADED_TZ_NOTE
        assert tb.resolve_tz_info().degraded is True
        assert tb.tz_degradation(tb.resolve_tz_info().tz)["degraded"] is True

    def test_local_to_utc_degrades_openly(self, monkeypatch):
        """固定偏移下无法检测夏令时歧义——不抛错，但说明必须给得出。"""
        _system_tz_unavailable(monkeypatch)
        from mbdsdr_ai.time_basis import local_to_utc, local_time_note

        out = local_to_utc(datetime(2026, 11, 1, 1, 30))
        assert out.tzinfo is UTC
        assert "固定偏移" in local_time_note()

    def test_time_info_and_engine_carry_local_note(self, monkeypatch):
        _system_tz_unavailable(monkeypatch)
        from mbdsdr_ai.new_spacetime import TimeEngine, get_time_info

        info = get_time_info(prefer_ntp=False)
        assert info.local_note == tf.DEGRADED_TZ_NOTE

        eng = TimeEngine()
        disp = eng.local_display()
        assert disp["local_note"] == tf.DEGRADED_TZ_NOTE
        assert disp["tz_degraded"] is True
        assert disp["tz_state"] == tf.TZ_STATE_FIXED_OFFSET

    def test_local_time_input_dialog_consults_note(self):
        """时间机器输入框的说明行必须接降级说明（源级钉住，免开 GUI）。

        ``mbdsdr_ai`` 是纯逻辑层，不得 import Qt/desktop（分层守卫
        ``tests/test_import_direction_contract.py``），所以这里读源文件，
        而不是 import 对话框模块。
        """
        path = os.path.join(ROOT, "desktop", "time_machine.py")
        with open(path, "r", encoding="utf-8") as fh:
            src = fh.read()
        assert "local_time_note(" in src
