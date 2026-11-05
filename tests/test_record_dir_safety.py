# SPDX-License-Identifier: MIT
"""录音默认目录 / 「随程序目录一起删除」提示的钉扎测试。

裁定（2026-11-06）：绿色包按「删目录即卸载」分发，默认录音目录必须落在
**用户目录**，否则删除/升级程序目录会把用户录音一起删掉。本文件钉住：

  1. 默认目录在用户目录内、且**不在**程序目录内；
  2. 旧配置的相对路径按旧根（程序目录上一级）解析，不随 cwd 漂移；
  3. 程序目录内 + 从未确认 → 首次录音必须弹窗；确认过一次才放行；
  4. 弹窗默认动作是「改用用户目录」，选它会把 record_dir 置空（回到默认）。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from settings import (  # noqa: E402
    default_record_dir, program_install_dir, record_dir_inside_program,
    record_dir_needs_confirmation, resolve_record_dir,
)


# ---------------------------------------------------------------------------
# 1. 默认位置
# ---------------------------------------------------------------------------
def test_default_record_dir_is_in_user_profile_not_program_dir():
    default = default_record_dir()
    assert default == os.path.join(os.path.expanduser("~"), "MBDSDR",
                                   "recordings")
    assert not record_dir_inside_program(default), (
        "默认录音目录落在程序目录内：删目录即卸载会连带删除用户录音")
    # 源码直跑时程序根 = 仓库根；冻结版才上跳一级（_MEIPASS 的上一级含 exe）
    assert record_dir_inside_program(
        os.path.join(program_install_dir(), "recordings"))


# ---------------------------------------------------------------------------
# 2. 旧配置迁移
# ---------------------------------------------------------------------------
def test_legacy_relative_record_dir_resolves_against_old_root():
    legacy = resolve_record_dir("recordings")
    assert os.path.isabs(legacy)
    # 源码直跑：程序根 = 仓库根，旧位置就在它的上一级
    assert legacy == os.path.join(os.path.dirname(_ROOT), "recordings")


def test_legacy_relative_is_inside_program_for_frozen_bundle(monkeypatch):
    """冻结版几何：_MEIPASS = <dist>/MBDSDR/_internal，旧默认正好在程序目录内。"""
    import settings as settings_mod

    bundle = os.path.join(os.sep, "dist", "MBDSDR")
    monkeypatch.setattr(settings_mod, "project_root",
                        lambda: os.path.join(bundle, "_internal"))
    monkeypatch.setattr(settings_mod.sys, "frozen", True, raising=False)
    legacy = resolve_record_dir("recordings")
    assert legacy == os.path.join(bundle, "recordings")
    # 旧位置仍在程序目录内 → 仍会命中首次确认
    assert record_dir_inside_program(legacy)


def test_absolute_record_dir_is_kept_verbatim(tmp_path):
    target = tmp_path / "my_rec"
    assert resolve_record_dir(str(target)) == str(target)


# ---------------------------------------------------------------------------
# 3. 确认闸门（纯判定）
# ---------------------------------------------------------------------------
def test_needs_confirmation_matrix(tmp_path):
    inside = os.path.join(program_install_dir(), "recordings")
    outside = str(tmp_path / "rec")
    assert record_dir_needs_confirmation(inside, acked=False)
    assert not record_dir_needs_confirmation(inside, acked=True)
    assert not record_dir_needs_confirmation(outside, acked=False)


# ---------------------------------------------------------------------------
# 4. 首次录音弹窗的真实行为（不构造 MainWindow，直接调方法）
# ---------------------------------------------------------------------------
class _FakeSettings:
    def __init__(self, record_dir_value, acked=False):
        self._data = {"record_dir": record_dir_value,
                      "record_dir_inside_program_ack": acked}
        self.flushed = False
        self.written = []

    @property
    def record_dir(self):
        return resolve_record_dir(self._data.get("record_dir", ""))

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self.written.append((key, value))
        self._data[key] = value

    def flush(self):
        self.flushed = True


class _FakeSelf:
    def __init__(self, record_dir_value, acked=False):
        self.settings = _FakeSettings(record_dir_value, acked)
        self.status = []

    def statusBar(self):
        outer = self

        class _SB:
            def showMessage(self, text, level=0):
                outer.status.append(text)

        return _SB()


class _FakeBox:
    """替代 QMessageBox：记录按钮，按 ``choice_text`` 回放点击。"""

    Warning = "warning"
    AcceptRole = "accept"
    DestructiveRole = "destructive"
    choice_text = "改用用户目录并录音"

    def __init__(self, parent=None):
        self.buttons = {}
        self.clicked = None

    def setIcon(self, *_a):
        pass

    def setWindowTitle(self, *_a):
        pass

    def setText(self, *_a):
        pass

    def addButton(self, text, _role):
        btn = object()
        self.buttons[text] = btn
        return btn

    def setDefaultButton(self, button):
        self.default_button = button

    def exec(self):
        self.clicked = self.buttons.get(type(self).choice_text)

    def clickedButton(self):
        return self.clicked


def _patch_box(monkeypatch, choice):
    import main_window  # noqa: WPS433  (只在用例里导入，避免拖慢其它测试)

    _FakeBox.choice_text = choice
    monkeypatch.setattr(main_window, "QMessageBox", _FakeBox)
    return main_window


def test_gate_switches_to_user_dir_by_default(monkeypatch):
    """程序目录内、未确认：弹窗并选「改用用户目录」→ record_dir 置空。"""
    fake = _FakeSelf(os.path.join(program_install_dir(), "recordings"))
    mw = _patch_box(monkeypatch, "改用用户目录并录音")
    assert mw.MainWindow._confirm_record_dir_safe(fake) is True
    assert ("record_dir", "") in fake.settings.written
    assert fake.settings.flushed
    assert fake.status and "已改为" in fake.status[0]


def test_gate_keeps_program_dir_only_after_ack(monkeypatch):
    fake = _FakeSelf(os.path.join(program_install_dir(), "recordings"))
    mw = _patch_box(monkeypatch, "仍用当前目录")
    assert mw.MainWindow._confirm_record_dir_safe(fake) is True
    assert ("record_dir_inside_program_ack", True) in fake.settings.written


def test_gate_cancel_blocks_recording(monkeypatch):
    fake = _FakeSelf(os.path.join(program_install_dir(), "recordings"))
    mw = _patch_box(monkeypatch, "不存在的按钮")
    assert mw.MainWindow._confirm_record_dir_safe(fake) is False


def test_gate_silent_when_dir_outside_program(monkeypatch, tmp_path):
    fake = _FakeSelf(str(tmp_path / "rec"))
    mw = _patch_box(monkeypatch, "改用用户目录并录音")
    assert mw.MainWindow._confirm_record_dir_safe(fake) is True
    assert fake.settings.written == []


def test_gate_silent_after_ack(monkeypatch):
    fake = _FakeSelf(os.path.join(program_install_dir(), "recordings"),
                     acked=True)
    mw = _patch_box(monkeypatch, "改用用户目录并录音")
    assert mw.MainWindow._confirm_record_dir_safe(fake) is True
    assert fake.settings.written == []


# ---------------------------------------------------------------------------
# 5. 取消路径的文案口径（A+B，2026-11-06 与录音线确认）
#
# 「已停止回放」只陈述回放确实被停；「开始录音」这类完成态只能出现在
# 文件真正打开成功之后。取消/未确认路径一律不出现完成态，并补一句
# 「已取消录音（录音目录未确认）」收尾。
# ---------------------------------------------------------------------------
class _FakeButton:
    def __init__(self):
        self.checked = None

    def setChecked(self, value):
        self.checked = value


class _FakeBackend:
    """只提供 _start_recording 走到确认闸门之前所需的读接口。"""

    class status:
        gain_db = 0.0

    class device:
        name = "FakeSDR"

    def read_samples(self, *_a, **_k):
        return None

    def get_sample_rate(self):
        return 2_048_000.0

    def get_frequency(self):
        return 100_000_000.0


class _FakeRecordSelf:
    """替换 MainWindow 实例：_replay_timer 置哨兵，让前置句真的被执行。"""

    def __init__(self):
        self._replay_timer = object()
        self._active_sdr_backend = _FakeBackend()
        self._recording = True          # 进入前假装在录，取消后必须复位
        self._record_guard = False
        self.stopped_replay = 0
        self.status = []
        self.record_btn = _FakeButton()
        self.control_panel = type("_CP", (), {"record_button": _FakeButton()})()

    def _stop_replay(self):
        self.stopped_replay += 1
        self._replay_timer = None

    def statusBar(self):
        outer = self

        class _SB:
            def showMessage(self, text, level=0):
                outer.status.append(text)

        return _SB()


def test_cancel_path_says_cancelled_and_never_says_started():
    """取消确认：有「已停止回放」+「已取消录音」，绝无「开始录音/开始录制」。

    变异反证：把 :6999 的前置句改回「已停止回放，开始录音」时，
    下面 ``"开始录音" not in joined`` 必须变红。因此本用例必须先让
    ``_replay_timer`` 非 None、桩掉 ``_stop_replay``，否则旧句根本走不到，
    变异就是空反证（假绿）。
    """
    import main_window  # noqa: WPS433  (只在用例里导入，避免拖慢其它测试)

    fake = _FakeRecordSelf()
    fake._confirm_record_dir_safe = lambda: False
    main_window.MainWindow._start_recording(fake)

    joined = "\n".join(fake.status)
    assert fake.stopped_replay == 1
    assert fake._replay_timer is None
    assert "已停止回放" in joined
    assert "已取消录音（录音目录未确认）" in joined
    assert "开始录音" not in joined
    assert "开始录制" not in joined
    assert fake._recording is False
    assert fake.record_btn.checked is False
    assert fake.control_panel.record_button.checked is False
