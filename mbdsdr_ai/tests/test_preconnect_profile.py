# SPDX-License-Identifier: MIT
"""W44 行为组：断连可预配置（设备/采样率/增益/重连策略）。

验收口径（对应用户要求）：
- 无预配置 → 连接行为与历史一致（单次连接、不额外下发参数、不重试）；
- 有预配置 → 四项被应用，且提示里写明；
- 配置落盘跨重启保留；损坏可见（备份 + 文案）且不影响启动/连接；
- 重试有硬上限，不存在无限静默重试。
"""
import json
import time

import pytest

from mbdsdr_ai import preconnect
from mbdsdr_ai.config import ConfigStore
from mbdsdr_ai.sdr_backend import SDRBackend, SDRBackendManager, SDRDevice
from mbdsdr_ai import sdr_tools


def _device(device_id: str, name: str) -> SDRDevice:
    return SDRDevice(
        device_type="fake",
        device_id=device_id,
        name=name,
        frequency_range=(24e6, 1.7e9),
        sample_rate_range=(1e6, 2.4e6),
        max_gain=40.0,
    )


class _FakeBackend(SDRBackend):
    """记录 connect / set_sample_rate / set_gain 调用，不碰任何硬件。"""

    def __init__(self, device: SDRDevice, fail_times: int = 0):
        super().__init__(device)
        self.connect_calls = 0
        self.fail_times = fail_times
        self.sample_rates = []
        self.gains = []

    def connect(self) -> bool:
        self.connect_calls += 1
        if self.connect_calls <= self.fail_times:
            self.status.error = "模拟连接失败"
            return False
        self.status.connected = True
        return True

    def set_sample_rate(self, rate_hz: float) -> bool:
        self.sample_rates.append(rate_hz)
        self.status.sample_rate_hz = rate_hz
        return True

    def set_gain(self, gain_db: float) -> bool:
        self.gains.append(gain_db)
        return True


@pytest.fixture
def env(tmp_path, monkeypatch):
    """两个假后端 + 独立 config.json（绝不碰真实 ~/.mbdsdr/config.json）。"""
    monkeypatch.setattr(SDRBackendManager, "_discover", lambda self: None)
    mgr = SDRBackendManager()
    mgr._last_discover_ts = time.monotonic()
    a = _FakeBackend(_device("fake_a", "假设备A"))
    b = _FakeBackend(_device("fake_b", "假设备B"))
    mgr.backends = {"fake_a": a, "fake_b": b}
    mgr.active_backend = None
    store = ConfigStore(str(tmp_path / "config.json"))
    mgr._cfg_store = store            # 让「上次设备」也写到临时文件
    return mgr, a, b, store


# ---------------------------------------------------------------------------
# 默认行为不变
# ---------------------------------------------------------------------------

def test_no_profile_behaves_exactly_as_before(env):
    mgr, a, b, store = env
    assert preconnect.load(store) == ({}, [])          # 确实没有预配置
    res = sdr_tools._sdr_connect(mgr, "fake_a", store=store)
    assert res.success is True
    assert a.connect_calls == 1                        # 单次连接，不重试
    assert a.sample_rates == [] and a.gains == []      # 不额外下发参数
    assert "预配置" not in res.content
    assert "已尝试" not in res.content


# ---------------------------------------------------------------------------
# 四项被应用
# ---------------------------------------------------------------------------

def test_profile_four_items_applied(env):
    mgr, a, b, store = env
    ok, notes = preconnect.save(store, {
        "device_id": "fake_b",
        "sample_rate_hz": 1_024_000,
        "gain_db": 12.5,
        "attempts": 2,
        "interval_s": 0,
    })
    assert ok, notes

    res = sdr_tools._sdr_connect(mgr, None, store=store)
    assert res.success is True
    # ① 设备：按预配置连到 fake_b，不是枚举第一台
    assert mgr.get_active() is b and a.connect_calls == 0
    assert "fake_b" in res.content
    # ② 采样率 ③ 增益：真的下发到后端
    assert b.sample_rates == [1_024_000]
    assert b.gains == [12.5]
    assert "1.024 MS/s" in res.content and "12.5" in res.content
    # ④ 重连策略：本次一次就成功，只连一次（策略已保存，见下条）
    assert b.connect_calls == 1
    saved, _ = preconnect.load(store)
    assert preconnect.policy(saved) == (2, 0.0)


def test_explicit_device_id_overrides_profile(env):
    mgr, a, b, store = env
    preconnect.save(store, {"device_id": "fake_b"})
    res = sdr_tools._sdr_connect(mgr, "fake_a", store=store)
    assert res.success is True
    assert mgr.get_active() is a and b.connect_calls == 0


# ---------------------------------------------------------------------------
# 落盘 / 跨重启
# ---------------------------------------------------------------------------

def test_profile_persists_across_restart(env):
    _mgr, _a, _b, store = env
    preconnect.save(store, {"device_id": "fake_b", "sample_rate_hz": 2_048_000,
                            "gain_db": 20, "attempts": 2, "interval_s": 1.5})
    # 模拟重启：全新的 ConfigStore / 读取方
    fresh = ConfigStore(store.path)
    profile, issues = preconnect.load(fresh)
    assert issues == []
    assert profile["device_id"] == "fake_b"
    assert profile["sample_rate_hz"] == 2_048_000
    assert profile["gain_db"] == 20
    assert preconnect.policy(profile) == (2, 1.5)


def test_unknown_keys_in_config_are_preserved(env):
    _mgr, _a, _b, store = env
    with open(store.path, "w", encoding="utf-8") as f:
        json.dump({"api_key": "keep-me", "future_key": {"x": 1}}, f)
    ok, notes = preconnect.save(store, {"device_id": "fake_a"})
    assert ok, notes
    on_disk = json.loads(open(store.path, encoding="utf-8").read())
    assert on_disk["api_key"] == "keep-me"
    assert on_disk["future_key"] == {"x": 1}
    assert on_disk[preconnect.KEY]["device_id"] == "fake_a"


# ---------------------------------------------------------------------------
# 坏配置：看得见、不崩、不覆盖
# ---------------------------------------------------------------------------

def test_corrupt_config_is_visible_and_connect_still_works(env, tmp_path):
    mgr, a, _b, store = env
    broken = tmp_path / "config.json"
    broken.write_text("{ this is not json", encoding="utf-8")

    profile, issues = preconnect.load(store)
    assert profile == {}
    assert issues and "存在但读不出内容" in issues[0]

    res = sdr_tools._sdr_connect(mgr, "fake_a", store=store)
    assert res.success is True                       # 坏配置不妨碍连接
    assert "存在但读不出内容" in res.content          # 但用户必须看见
    backups = list(tmp_path.glob("config.json.corrupt-*"))
    assert backups, "损坏配置必须留 *.corrupt-* 备份证据"


def test_invalid_fields_ignored_with_reason(env):
    _mgr, _a, _b, store = env
    store.update(lambda d: d.__setitem__(preconnect.KEY, {
        "device_id": 123,
        "sample_rate_hz": -1,
        "gain_db": "abc",
        "reconnect": {"attempts": 0},
    }) or True)
    profile, issues = preconnect.load(store)
    assert profile == {}
    assert issues and "字段非法" in issues[-1]
    for field in ("device_id", "sample_rate_hz", "gain_db", "reconnect.attempts"):
        assert field in issues[-1]


def test_save_rejects_unknown_field(env):
    _mgr, _a, _b, store = env
    ok, notes = preconnect.save(store, {"freq_hz": 100e6})
    assert ok is False
    assert "未知字段" in notes[0]


# ---------------------------------------------------------------------------
# 重试：有上限、失败可见
# ---------------------------------------------------------------------------

def test_retry_is_bounded_and_visible(env, monkeypatch):
    mgr, _a, b, store = env
    b.fail_times = 99                                # 永远失败
    preconnect.save(store, {"attempts": 3, "interval_s": 0})
    res = sdr_tools._sdr_connect(mgr, "fake_b", store=store)
    assert res.success is False
    assert b.connect_calls == preconnect.MAX_ATTEMPTS   # 恰好 3 次，不多
    assert "已尝试 3 次" in res.content
    assert "不会无限静默重试" in res.content


def test_retry_policy_hard_cap():
    # 请求无穷大 → 仍被钳到上限（策略层）。这里写**字面量**而不是引用常量：
    # 一旦上限被删/被改成无穷，本断言必须红。
    assert preconnect.MAX_ATTEMPTS == 3
    assert preconnect.MAX_INTERVAL_S == 5.0
    assert preconnect.policy({"reconnect": {"attempts": 10 ** 9,
                                            "interval_s": 10 ** 9}}) == (3, 5.0)


def test_huge_attempts_clamped_on_load_and_save(env):
    _mgr, _a, _b, store = env
    ok, notes = preconnect.save(store, {"attempts": 999, "interval_s": 999})
    assert ok is True
    assert any("超过上限" in n for n in notes)
    profile, issues = preconnect.load(store)
    assert preconnect.policy(profile) == (preconnect.MAX_ATTEMPTS,
                                          preconnect.MAX_INTERVAL_S)

    # 直接写盘绕过 save 的钳位，读取侧同样钳位并给出原因
    store.update(lambda d: d.__setitem__(preconnect.KEY, {
        "reconnect": {"attempts": 50, "interval_s": 60}}) or True)
    profile, issues = preconnect.load(store)
    assert preconnect.policy(profile) == (preconnect.MAX_ATTEMPTS,
                                          preconnect.MAX_INTERVAL_S)
    assert any("超过上限" in i for i in issues)


def test_deterministic_failure_not_retried(env):
    """设备不在枚举表里是确定结果：只试一次，不做无意义重试。"""
    mgr, a, b, store = env
    preconnect.save(store, {"attempts": 3, "interval_s": 0})
    res = sdr_tools._sdr_connect(mgr, "fake_gone", store=store)
    assert res.success is False
    assert "fake_gone" in res.content
    assert "假设备A" in res.content and "假设备B" in res.content


# ---------------------------------------------------------------------------
# 工具层：show / set / clear 与「不在枚举」提示
# ---------------------------------------------------------------------------

def test_tool_show_set_clear(env, monkeypatch):
    mgr, _a, _b, store = env
    tmp_store = store
    monkeypatch.setattr(sdr_tools, "_preconnect_store",
                        lambda store=None: tmp_store)

    show = sdr_tools._sdr_preconnect(mgr, {})
    assert show.success is True and "未配置" in show.content

    set_res = sdr_tools._sdr_preconnect(
        mgr, {"action": "set", "device_id": "fake_b",
              "sample_rate_hz": 2_400_000, "gain_db": 30,
              "attempts": 2, "interval_s": 1})
    assert set_res.success is True
    assert "已保存预配置" in set_res.content

    show2 = sdr_tools._sdr_preconnect(mgr, {"action": "show"})
    assert "fake_b" in show2.content and "2.400 MS/s" in show2.content

    clear = sdr_tools._sdr_preconnect(mgr, {"action": "clear"})
    assert clear.success is True and "已清除预配置" in clear.content
    assert preconnect.load(store) == ({}, [])


def test_tool_set_warns_unknown_device(env, monkeypatch):
    mgr, _a, _b, store = env
    tmp_store = store
    monkeypatch.setattr(sdr_tools, "_preconnect_store",
                        lambda store=None: tmp_store)
    res = sdr_tools._sdr_preconnect(mgr, {"action": "set", "device_id": "ghost"})
    assert res.success is True                      # 允许预先配置未插的设备
    assert "不在枚举列表中" in res.content


def test_tool_apply_failure_is_not_reported_as_success(env, monkeypatch):
    """后端拒绝下发时必须说「未生效」，不许把没生效写成已生效。"""
    mgr, _a, b, store = env

    def _refuse(rate_hz):
        b.status.error = "设备不支持该采样率"
        return False

    monkeypatch.setattr(b, "set_sample_rate", _refuse)
    preconnect.save(store, {"sample_rate_hz": 1_000_000})
    res = sdr_tools._sdr_connect(mgr, "fake_b", store=store)
    assert res.success is True
    assert "预配置未生效" in res.content
    assert "设备不支持该采样率" in res.content
