# SPDX-License-Identifier: MIT
"""
非模态通知条判据（第 1 条：QGIS ``QgsMessageBar`` 等价物）
==========================================================

钉住的行为（清单第 7 节验收口径）：
  1. **有 remedy** 的错误：显示目录 message + ``error_catalog.suggestion()``
     + 「查看怎么办」按钮，且不再弹模态框；
  2. **无 remedy** 的错误：不显示、只写日志（不出现死链接）；
  3. 同一个码重复发生只保留一条并计数（``×N``），不刷屏；
  4. 「重试」只在该码可重试且调用方给了回调时出现；
  5. 批量操作期间只累计，``end_batch()`` 汇总成一条；
  6. 关闭后聚合状态清空，下次同码重新计数。
"""
import logging
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "desktop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def _bar():
    from notification_bar import NotificationBar
    return NotificationBar()


# ---------------------------------------------------------------------------
# 组件本体
# ---------------------------------------------------------------------------
def test_remedy_error_shows_catalog_message_and_action(qapp):
    """有 remedy：显示目录原文 + 建议 + 「查看怎么办」，不出现「重试」。"""
    from mbdsdr_ai import error_catalog

    b = _bar()
    ok = b.notify("device_busy", fields={"detail": "GQRX 占用"})

    assert ok is True
    assert b.is_active(), "有 remedy 的错误必须显示在通知条上"
    assert b.message_text() == error_catalog.user_message(
        "device_busy", detail="GQRX 占用")
    assert b.suggestion_text() == error_catalog.suggestion("device_busy")
    assert b.suggestion_text().strip(), "建议不能为空——否则等于没接呈现层"
    assert b.has_remedy_action(), "有 remedy 必须出现「查看怎么办」按钮"
    assert not b.has_retry_action(), "没给回调时不该出现「重试」"
    assert b.active_codes() == ["device_busy"]


def test_no_remedy_error_only_logs_and_stays_hidden(qapp, caplog):
    """无 remedy：不显示、只进日志（不能出现点开空页的死链接）。"""
    b = _bar()
    with caplog.at_level(logging.INFO, logger="notification_bar"):
        ok = b.notify("io_error", fields={"detail": "x"})

    assert ok is False
    assert not b.is_active()
    assert b.isHidden()
    assert any("io_error" in rec.getMessage() for rec in caplog.records), \
        f"无 remedy 的错误必须留下日志：{[r.getMessage() for r in caplog.records]}"


def test_repeated_code_aggregates_instead_of_spamming(qapp):
    """同一个码重复发生：一条 + ×N，不堆多行。"""
    b = _bar()
    for detail in ("a", "b", "c"):
        assert b.notify("device_busy", fields={"detail": detail}) is True

    assert b.count("device_busy") == 3
    assert b.active_codes() == ["device_busy"], "同码不得产生多个条目"
    assert b.count_label.text() == "×3", b.count_label.text()
    assert "c" in b.message_text(), "显示最新一次的上下文"


def test_retry_action_requires_retryable_code_and_callback(qapp):
    """「重试」= 可重试的码 且 调用方确实给了回调，两者缺一不出按钮。"""
    def _noop():
        return None

    b = _bar()
    b.notify("device_busy", retry=_noop)      # 可重试 + 有回调
    assert b.has_retry_action()

    b.dismiss()
    b.notify("device_not_found", retry=_noop)  # 不可重试
    assert not b.has_retry_action(), "不可重试的码不得出现「重试」"

    b.dismiss()
    b.notify("device_busy")                    # 可重试但没回调
    assert not b.has_retry_action(), "没有回调时「重试」按了也没用"


def test_retry_button_runs_callback_once(qapp):
    """点「重试」：发信号、执行回调、关闭通知。"""
    fired = []
    b = _bar()
    b.notify("device_busy", retry=lambda: fired.append(1))
    b.retry_requested.connect(lambda code: fired.append(code))

    b.retry_btn.click()

    assert fired == ["device_busy", 1], fired
    assert not b.is_active(), "重试后通知条应收起，等下一次结果"


def test_batch_summarises_into_single_entry(qapp):
    """批量：期间不刷屏，结束时只汇总一条。"""
    b = _bar()
    b.begin_batch("批量导入")
    b.notify("device_busy")
    b.notify("device_busy")
    b.notify("device_not_found")
    assert not b.is_active(), "批量期间不得逐条弹/逐条刷通知条"

    assert b.end_batch() == 3
    assert b.is_active()
    assert "批量导入" in b.message_text()
    assert "3 条错误" in b.message_text(), b.message_text()
    assert "2 类" in b.message_text(), b.message_text()
    assert len(b.active_codes()) == 1, "批量只汇总成一条，不残留逐条条目"


def test_batch_skips_no_remedy_and_logs(qapp, caplog):
    """批量里的无 remedy 错误同样只进日志，不进汇总正文。"""
    b = _bar()
    b.begin_batch("批量导入")
    assert b.notify("device_busy") is True
    with caplog.at_level(logging.INFO, logger="notification_bar"):
        assert b.notify("io_error") is False
    assert b.end_batch() == 1, "无 remedy 的错误不得计入可见汇总"
    assert "1 条错误" in b.message_text(), b.message_text()


def test_dismiss_clears_aggregation(qapp):
    """关闭后计数归零，同码下次重新从 1 开始。"""
    b = _bar()
    b.notify("device_busy")
    b.notify("device_busy")
    assert b.count("device_busy") == 2

    b.dismiss()
    assert not b.is_active()
    assert b.active_codes() == []
    assert b.count("device_busy") == 0

    b.notify("device_busy")
    assert b.count("device_busy") == 1


# ---------------------------------------------------------------------------
# MainWindow 接线：有码的错误不再走模态框
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def main_window(qapp, isolated_profile):
    from main_window import MainWindow
    w = MainWindow()
    yield w
    try:
        w._disconnect()
    except Exception:  # noqa: BLE001 - 收尾失败不掩盖判据
        pass
    w.close()


def test_main_window_hosts_bar_and_routes_coded_error(main_window, monkeypatch):
    """MainWindow 顶部有通知条；有码错误走它，不弹 QMessageBox。"""
    import main_window as mw

    bar = main_window.notification_bar
    assert bar.isHidden(), "默认不占界面高度"

    modal_calls = []
    monkeypatch.setattr(mw.QMessageBox, "exec",
                        lambda self: modal_calls.append(self.text()) or 0)

    main_window._show_error_dialog(
        QMessageBox.Warning, "连接失败", "设备被占用", code="device_busy")

    assert modal_calls == [], f"有 remedy 的错误仍弹了模态框：{modal_calls}"
    assert bar.is_active()
    assert bar.message_text() == "连接失败：设备被占用"
    assert bar.suggestion_text().strip(), "目录建议必须进呈现层"
    assert bar.has_remedy_action()


def test_main_window_no_remedy_code_is_not_shown(main_window):
    """MainWindow 对无 remedy 的码不显示通知条（只进日志）。"""
    bar = main_window.notification_bar
    bar.dismiss()
    assert main_window._notify_error("io_error", fields={"detail": "x"}) is False
    assert not bar.is_active()
