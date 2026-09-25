"""
多 VFO 管理器单元测试
======================

纯 Python 状态测试：不接硬件、不接 UI、不产生任何信号数据。
覆盖 add/remove/get/list_all、频率排序、next/prev 循环、三态焦点、
USB/LSB 不对称区间命中、新建 vs 移动裁决、粘滞默认值继承与刷新。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.vfo_manager import VfoState, VfoManager, vfo_coverage


# ── 1. 基本增删查 ──────────────────────────────────────────────

def test_add_get_list_all():
    mgr = VfoManager()
    v1 = mgr.add(100e6, 12.5e3, "FM")
    v2 = mgr.add(200e6, 10e3, "AM")

    assert isinstance(v1, VfoState)
    assert mgr.get(v1.vfo_id) is v1
    assert mgr.get(v2.vfo_id) is v2
    assert mgr.get("nope") is None

    ids = [v.vfo_id for v in mgr.list_all()]
    assert ids == [v1.vfo_id, v2.vfo_id]
    # id 唯一
    assert v1.vfo_id != v2.vfo_id


def test_remove():
    mgr = VfoManager()
    v1 = mgr.add(100e6, 12.5e3, "FM")
    v2 = mgr.add(200e6, 10e3, "AM")
    mgr.remove(v1.vfo_id)
    assert mgr.get(v1.vfo_id) is None
    assert [v.vfo_id for v in mgr.list_all()] == [v2.vfo_id]
    # 删除不存在的 id 不抛错
    mgr.remove("nope")


def test_remove_current_clears_pointer():
    mgr = VfoManager()
    v1 = mgr.add(100e6, 12.5e3, "FM")
    mgr.set_active_context(v1, temporary=False)
    assert mgr.current is v1
    mgr.remove(v1.vfo_id)
    assert mgr.current is None
    assert mgr.active_context is None
    assert mgr.visual is None


# ── 2. 频率排序 ──────────────────────────────────────────────

def test_ordered_by_freq():
    mgr = VfoManager()
    v_low = mgr.add(100e6, 12.5e3, "FM")
    v_mid = mgr.add(200e6, 10e3, "AM")
    v_high = mgr.add(300e6, 5e3, "USB")
    # 故意乱序加入
    mgr2 = VfoManager()
    a = mgr2.add(300e6, 5e3, "USB")
    b = mgr2.add(100e6, 12.5e3, "FM")
    c = mgr2.add(200e6, 10e3, "AM")
    ordered = mgr2.ordered_by_freq()
    assert [v.center_hz for v in ordered] == [100e6, 200e6, 300e6]
    assert ordered == [b, c, a]


# ── 3. next / prev 循环切换 ──────────────────────────────────

def test_next_prev_cycle():
    mgr = VfoManager()
    v1 = mgr.add(100e6, 12.5e3, "FM")
    v2 = mgr.add(200e6, 12.5e3, "FM")
    v3 = mgr.add(300e6, 12.5e3, "FM")

    # 无 current 时返回 None
    assert mgr.next() is None
    assert mgr.prev() is None

    mgr.set_active_context(v2, temporary=False)
    assert mgr.current is v2

    assert mgr.next() is v3       # 200 -> 300
    assert mgr.next() is v1       # 300 -> 100（绕回）
    assert mgr.prev() is v3       # 100 -> 300（绕回）
    assert mgr.prev() is v2       # 300 -> 200
    assert mgr.current is v2


# ── 4. 三态焦点：悬停 vs 点击 ────────────────────────────────

def test_active_context_temporary_does_not_change_current():
    mgr = VfoManager()
    v1 = mgr.add(100e6, 12.5e3, "FM")
    v2 = mgr.add(200e6, 12.5e3, "FM")

    # 悬停：只改 active_context，current 保持空
    mgr.set_active_context(v1, temporary=True)
    assert mgr.active_context is v1
    assert mgr.current is None

    # 再悬停到另一个，current 仍不变
    mgr.set_active_context(v2, temporary=True)
    assert mgr.active_context is v2
    assert mgr.current is None


def test_active_context_permanent_sets_current():
    mgr = VfoManager()
    v1 = mgr.add(100e6, 12.5e3, "FM")
    v2 = mgr.add(200e6, 12.5e3, "FM")
    mgr.set_active_context(v1, temporary=True)
    # 点击确认 v2：current 收编为 v2
    mgr.set_active_context(v2, temporary=False)
    assert mgr.current is v2
    assert mgr.active_context is v2
    assert mgr.visual is v2


def test_clear_active_context_keeps_current():
    mgr = VfoManager()
    v1 = mgr.add(100e6, 12.5e3, "FM")
    v2 = mgr.add(200e6, 12.5e3, "FM")
    mgr.set_active_context(v1, temporary=False)   # current = v1
    mgr.set_active_context(v2, temporary=True)     # 悬停 v2
    assert mgr.active_context is v2
    mgr.clear_active_context()                      # 鼠标离开
    assert mgr.active_context is None
    assert mgr.current is v1                        # current 不动


# ── 5. 区间重叠命中：对称与单边带不对称 ─────────────────────

def test_coverage_pure_function():
    # 对称
    assert vfo_coverage(100e6, 20e3, "FM") == (100e6 - 10e3, 100e6 + 10e3)
    assert vfo_coverage(100e6, 20e3, "AM") == (100e6 - 10e3, 100e6 + 10e3)
    # USB：[center, center+bw]
    assert vfo_coverage(200e6, 3e3, "USB") == (200e6, 200e6 + 3e3)
    # LSB：[center-bw, center]
    assert vfo_coverage(300e6, 3e3, "LSB") == (300e6 - 3e3, 300e6)


def test_get_at_fm_symmetric():
    mgr = VfoManager()
    vfm = mgr.add(100e6, 20e3, "FM")   # [99.99e6, 100.01e6]
    assert mgr.get_at(100e6) == [vfm]          # 中心命中
    assert mgr.get_at(99.995e6) == [vfm]       # 区间内
    assert mgr.get_at(99.99e6) == [vfm]        # 左边界
    assert mgr.get_at(100.01e6) == [vfm]       # 右边界
    assert mgr.get_at(99.98e6) == []           # 左侧外
    assert mgr.get_at(100.02e6) == []         # 右侧外


def test_get_at_usb_asymmetric():
    mgr = VfoManager()
    vusb = mgr.add(200e6, 3e3, "USB")   # [200e6, 200.003e6]
    assert mgr.get_at(200e6) == [vusb]          # 下边沿命中
    assert mgr.get_at(200e6 + 1.5e3) == [vusb] # 区间内
    assert mgr.get_at(200e6 - 1) == []          # 载频以下不命中（USB 不对称）
    assert mgr.get_at(200e6 + 4e3) == []        # 上边沿外不命中


def test_get_at_lsb_asymmetric():
    mgr = VfoManager()
    vlsb = mgr.add(300e6, 3e3, "LSB")   # [299.997e6, 300e6]
    assert mgr.get_at(300e6) == [vlsb]           # 上边沿命中
    assert mgr.get_at(300e6 - 1.5e3) == [vlsb]   # 区间内
    assert mgr.get_at(300e6 + 1) == []           # 载频以上不命中（LSB 不对称）
    assert mgr.get_at(300e6 - 4e3) == []         # 下边沿外不命中


def test_get_at_half_bw_buffer():
    mgr = VfoManager()
    vfm = mgr.add(100e6, 20e3, "FM")   # [99.99e6, 100.01e6]
    # 99.98e6 在区间外，但查询容差 half_bw=10e3 时窗口扩到 [99.97e6, 99.99e6+...]
    assert mgr.get_at(99.98e6, half_bw_hz=10e3) == [vfm]
    # 容差为 0 时不命中
    assert mgr.get_at(99.98e6, half_bw_hz=0) == []


# ── 6. 新建 vs 移动裁决 ──────────────────────────────────────

def test_decide_create_or_move():
    mgr = VfoManager()
    # 无 current：应新建
    assert mgr.decide_create_or_move(shift_down=False) is True

    v1 = mgr.add(100e6, 12.5e3, "FM")
    mgr.set_active_context(v1, temporary=False)   # current = v1
    # 有 current 且未按 shift：应移动
    assert mgr.decide_create_or_move(shift_down=False) is False
    # 按 shift：应新建
    assert mgr.decide_create_or_move(shift_down=True) is True

    # current 被停用：等同无 current，应新建
    v1.active = False
    assert mgr.decide_create_or_move(shift_down=False) is True


# ── 7. 粘滞默认值继承与刷新 ──────────────────────────────────

def test_inherit_last_copies_previous_params():
    mgr = VfoManager()
    # 首个 VFO：尚无快照，使用传入 bw/mode
    v1 = mgr.add(100e6, 12.5e3, "FM")
    v1.squelch_level_db = -60.0
    v1.squelch_enabled = True
    v1.gain_db = 20.0
    v1.muted = True
    v1.delta_lock = True

    # 点击确认 v1 -> current，并刷新粘滞快照
    mgr.set_active_context(v1, temporary=False)

    # 新 VFO 即便传入不同 bw/mode，inherit_last=True 时整体继承上次参数
    v2 = mgr.add(200e6, 5000.0, "AM", inherit_last=True)
    assert v2.center_hz == 200e6          # 频率始终取传入值
    assert v2.bw_hz == 12.5e3             # 继承自 v1
    assert v2.mode == "FM"                 # 继承自 v1
    assert v2.squelch_level_db == -60.0
    assert v2.squelch_enabled is True
    assert v2.gain_db == 20.0
    assert v2.muted is True
    assert v2.delta_lock is True


def test_inherit_last_false_uses_args():
    mgr = VfoManager()
    v1 = mgr.add(100e6, 12.5e3, "FM")
    v1.gain_db = 20.0
    mgr.set_active_context(v1, temporary=False)

    v3 = mgr.add(300e6, 5000.0, "AM", inherit_last=False)
    assert v3.bw_hz == 5000.0
    assert v3.mode == "AM"
    assert v3.gain_db is None              # 未继承，回退默认（AGC）
    assert v3.squelch_level_db == -150.0


def test_first_add_without_snapshot_uses_args():
    mgr = VfoManager()
    # 尚无任何 current 快照：inherit_last=True 也应使用传入值
    v1 = mgr.add(100e6, 12.5e3, "FM", inherit_last=True)
    assert v1.bw_hz == 12.5e3
    assert v1.mode == "FM"
    assert v1.gain_db is None


def test_update_last_state_refreshes():
    mgr = VfoManager()
    v1 = mgr.add(100e6, 12.5e3, "FM")
    v1.squelch_level_db = -55.0
    v1.gain_db = 15.0
    mgr.set_active_context(v1, temporary=False)
    assert mgr.last_squelch_db == -55.0
    assert mgr.last_gain_db == 15.0

    # 修改 current 参数后手动刷新粘滞快照
    v1.squelch_level_db = -80.0
    v1.gain_db = 30.0
    mgr.update_last_state()
    assert mgr.last_squelch_db == -80.0
    assert mgr.last_gain_db == 30.0

    v2 = mgr.add(200e6, 0, "FM", inherit_last=True)
    assert v2.squelch_level_db == -80.0
    assert v2.gain_db == 30.0


if __name__ == "__main__":
    import inspect
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in fns:
        try:
            fn()
            print(f"PASS {name}")
        except Exception as e:
            failed += 1
            print(f"FAIL {name}: {e!r}")
    sys.exit(1 if failed else 0)
