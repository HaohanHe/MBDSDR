# SPDX-License-Identifier: MIT
"""书签带宽字段（新增能力）回归锁。

定性（2026-11-12 用户更正）：不是「保存时丢了带宽」，是书签**从来没存过
带宽**。旧条目只有 ``(freq_hz, name, mode, builtin)`` 四元组；本文件锁的是
「给书签加带宽字段」这件新增能力，包括持久化格式与旧数据兼容。

现行契约（2026-11-12 应用顺序改造后，先写契约再断言）：
  1. 写入端：收藏时带宽随频率/模式一起落盘，重开书签库仍读得到。
  2. 旧数据兼容：没有 bandwidth_hz 的旧 JSON / 旧四元组照读，带宽记 0.0
     （未记录），绝不因多一列读不出来或崩。
  3. 频谱旗子 ``bookmark_activated(freq, mode, bw)`` 与书签面板
     ``tune_requested(freq, mode, bw)`` 落**同一个槽**
     ``_on_bookmark_tune_requested``，参数集一致、带宽一致。
  4. 槽内顺序固定为 **模式（仅当需要）→ 带宽 → 调谐**：
       * 带宽经 ``_on_vfo_bandwidth_changed(bw)`` 下发；
       * 调谐走 ``_on_tune_sdr(freq, mode, apply_mode=False)``（3 参，
         模式已在前面单独切过，调谐这一步不再重复切）；
       * 模式切换传 ``apply_default_bandwidth=False``，不让中间态顶掉书签带宽。
     带宽落槽值必须来自载荷，不是常量（不是装饰字段）。
  5. ``bw == 0.0``（旧书签「未记录」）按该模式默认带宽
     ``default_bandwidth(mode)`` 真实下发，不是「不动设备带宽」——
     status / 硬件 / ``_vfo_bw`` 三份数不允许分叉。

本文件不改生产实现；任何用例都不给同会话的后续模块留配置状态（见 mw 夹具
的 settings 快照/还原）。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_bookmark_bandwidth.py -v
"""
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_OFFLINE", "1")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))


def _saved_entries(raw):
    """bookmarks.json 可能是 BookmarkManager 的 dict 格式，也可能是旧的裸列表。"""
    if isinstance(raw, dict):
        raw = raw.get("bookmarks", [])
    return [d for d in raw if isinstance(d, dict)]

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

#: 用户点旗子/面板时选的带宽（值本身无意义，只要两侧一致且可辨）。
BW = 12_500.0

#: MainWindow 在这些用例里可能写进的配置键；模块结束一律还原，
#: 否则同会话后面的模块会读到「上一个测试留下的模式」。
_STATE_KEYS = ("app_mode", "demod_mode", "frequency_hz", "bandwidth_hz")


@pytest.fixture(scope="module")
def mw(isolated_profile):
    from main_window import MainWindow
    w = MainWindow()
    saved = {k: w.settings.get(k) for k in _STATE_KEYS}
    yield w
    try:
        w._disconnect()
    except Exception:
        pass
    w._bookmarks.clear()
    w._sync_spectrum_bookmarks()
    # 不给别的模块留状态：写回进入本模块前的值并立即落盘。
    for k, v in saved.items():
        w.settings.set(k, v)
    try:
        w.settings.flush()
    except Exception:
        pass


def _spy_slot(mw):
    """替换槽内的三个下游动作，只记录调用（不碰硬件 / 配置 / 设备状态）。

    ``_on_mode_changed`` 也一起替换：本文件只锁槽内的顺序与参数，真实切模式
    会写 settings 并影响后续模块，属于另一个文件（test_bookmark_apply）的职责。
    """
    calls = []

    def fake_mode(mode, apply_default_bandwidth=True):
        calls.append(("mode", str(mode), bool(apply_default_bandwidth)))
        return True

    def fake_tune(freq_hz, mode, apply_mode=True):
        calls.append(("tune", float(freq_hz), str(mode), bool(apply_mode)))
        return True

    def fake_bw(bw_hz):
        calls.append(("bw", float(bw_hz)))
        return True

    mw._on_mode_changed = fake_mode
    mw._on_tune_sdr = fake_tune
    mw._on_vfo_bandwidth_changed = fake_bw
    return calls


def _tail(calls):
    """槽尾部必须是「模式?(关闭默认带宽) → 带宽 → 调谐(3 参 apply_mode=False)」。"""
    kinds = [c[0] for c in calls]
    assert kinds.count("bw") == 1, f"带宽必须下发一次：{calls}"
    assert kinds.count("tune") == 1, f"调谐必须发生一次：{calls}"
    assert kinds.index("bw") < kinds.index("tune"), f"顺序必须是 bw→tune：{calls}"
    if "mode" in kinds:
        assert kinds.index("mode") == 0, f"模式必须在带宽之前且只切一次：{calls}"
        assert calls[0] == ("mode", calls[0][1], False), \
            f"切模式必须关闭默认带宽，避免中间态顶掉书签带宽：{calls}"
    return calls[-2], calls[-1]


# ---------------------------------------------------------------------------
# 1. 写入端：带宽必须落盘
# ---------------------------------------------------------------------------
def test_add_bookmark_persists_bandwidth(mw):
    mw._vfo_bw = BW
    before = len(mw._bookmarks)
    mw._on_bookmark_added(145_500_000.0, "带宽持久化", "NFM")

    entry = mw._bookmarks[before]
    assert len(entry) == 5
    assert entry[:4] == (145_500_000.0, "带宽持久化", "NFM", False)
    assert entry[4] == pytest.approx(BW)

    # 落盘：JSON 里有 bandwidth_hz；重新打开书签库仍读得到
    path = mw._freq_mgr.user_path
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    saved = _saved_entries(raw)
    assert saved and saved[-1]["bandwidth_hz"] == pytest.approx(BW)

    from mbdsdr_ai.frequency_manager import FrequencyManager
    reloaded = FrequencyManager(path)
    hit = [b for b in reloaded.list() if b.name == "带宽持久化"]
    assert hit and hit[-1].bandwidth_hz == pytest.approx(BW)
    # 收尾：不把测试书签留在共享临时库里
    mw._freq_mgr.remove("带宽持久化")


# ---------------------------------------------------------------------------
# 2. 旧数据兼容：没有带宽字段也必须能读
# ---------------------------------------------------------------------------
def test_legacy_json_without_bandwidth_is_readable(tmp_path):
    path = tmp_path / "legacy_bookmarks.json"
    path.write_text(json.dumps([
        {"name": "旧书签", "freq_hz": 100e6, "mode": "AM"},
    ]), encoding="utf-8")

    from mbdsdr_ai.frequency_manager import FrequencyManager
    mgr = FrequencyManager(str(path))
    hit = [b for b in mgr.list() if b.name == "旧书签"]
    assert hit, "旧 JSON（无 bandwidth_hz）读不出来"
    assert hit[0].freq_hz == pytest.approx(100e6)
    assert hit[0].mode == "AM"
    assert hit[0].bandwidth_hz == 0.0      # 未记录，不是猜出来的值


def test_legacy_four_tuple_normalizes_to_zero_bandwidth(mw):
    """旧四元组进内存表 → 仍能同步到频谱，带宽记 0.0。"""
    from main_window import _normalize_bookmark

    legacy = (100e6, "旧四元组", "AM", False)
    assert _normalize_bookmark(legacy) == (100e6, "旧四元组", "AM", False, 0.0)

    mw._bookmarks[:] = [legacy]
    mw._sync_spectrum_bookmarks()          # 多一列就读不出来 = 红
    tags = mw.spectrum._state.bookmarks
    assert tags == [(100e6, "旧四元组", "AM", 0.0)]
    mw._bookmarks.clear()
    mw._sync_spectrum_bookmarks()


# ---------------------------------------------------------------------------
# 3. 两条路径 → 同一个槽，顺序与参数一致
# ---------------------------------------------------------------------------
def test_flag_and_panel_paths_land_in_same_slot(mw):
    calls = _spy_slot(mw)
    mw.spectrum.bookmark_activated.emit(145.0e6, "NFM", BW)
    flag_calls = list(calls)
    calls.clear()
    mw.bookmark_panel.tune_requested.emit(145.0e6, "NFM", BW)
    panel_calls = list(calls)

    for label, got in (("flag", flag_calls), ("panel", panel_calls)):
        bw_call, tune_call = _tail(got)
        assert bw_call == ("bw", float(BW)), f"{label} 带宽落槽值不对：{got}"
        assert tune_call == ("tune", 145.0e6, "NFM", False), \
            f"{label} 调谐必须是 3 参且 apply_mode=False：{got}"
    assert flag_calls == panel_calls, "旗子与面板两路参数集必须一致"


def test_flag_bandwidth_is_not_decorative(mw):
    """真断言：带宽与调谐都必须发生，且带宽值来自载荷、不是常量。

    旧版本这条是假绿——fake_tune 只有 2 参，3 参调用抛 TypeError 后
    ``calls[-1]`` 恰好停在 ``("bw", ...)``，看起来像过了，其实调谐根本没跑。
    """
    calls = _spy_slot(mw)
    mw.spectrum.bookmark_activated.emit(145.0e6, "NFM", 6_000.0)
    bw_call, tune_call = _tail(calls)
    assert bw_call == ("bw", 6_000.0)
    assert tune_call == ("tune", 145.0e6, "NFM", False)

    calls.clear()
    mw.spectrum.bookmark_activated.emit(145.0e6, "NFM", 15_000.0)
    bw_call, tune_call = _tail(calls)
    assert bw_call == ("bw", 15_000.0)
    assert tune_call == ("tune", 145.0e6, "NFM", False)
    # 实现若写死 BW / 忽略载荷 / 把调谐吞掉，上面任一条必红


def test_flag_zero_bandwidth_uses_mode_default_bandwidth(mw):
    """旧书签带宽 0.0 = 未记录：按该模式默认带宽真实下发，不许三份数分叉。"""
    from mode_registry import default_bandwidth

    expected_bw = float(default_bandwidth("AM"))

    calls = _spy_slot(mw)
    mw.spectrum.bookmark_activated.emit(145.0e6, "AM", 0.0)
    bw_call, tune_call = _tail(calls)
    assert bw_call == ("bw", expected_bw), \
        f"bw=0 必须下发 AM 默认带宽 {expected_bw}，实际：{calls}"
    assert tune_call == ("tune", 145.0e6, "AM", False)


def test_spectrum_set_bookmarks_accepts_legacy_two_tuple():
    from spectrum_widget import SpectrumPanel
    panel = SpectrumPanel()
    panel.set_bookmarks([(98.5e6, "旧两元组")])
    assert panel._state.bookmarks == [(98.5e6, "旧两元组", "", 0.0)]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
