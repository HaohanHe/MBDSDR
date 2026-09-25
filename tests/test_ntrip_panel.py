"""NTRIP 配置面板 offscreen 测试
================================

验证 desktop/ntrip_panel.py：
  1. 对话框创建不崩溃，默认字段正确（port=2101、密码 Password 模式、mountpoint 可编辑）；
  2. 配置保存/加载往返一致（get_config -> apply_config -> get_config）；
  3. NTRIPManager mock：连接成功 -> start() 被调、状态变已连接、字节数更新；
  4. NTRIPManager mock：断开 -> stop() 被调、状态回未连接；
  5. sourcetable STR 行解析正确提取挂载点；
  6. host/mountpoint 为空时点连接不调 start()（弹提示但不崩）。

运行:
    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_ntrip_panel.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

# 仓库根 + desktop 目录
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtWidgets import QApplication, QLineEdit, QMessageBox  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

# 测试期间所有模态弹框改为 no-op，避免 offscreen 下阻塞
QMessageBox.warning = staticmethod(lambda *a, **k: None)   # type: ignore
QMessageBox.information = staticmethod(lambda *a, **k: None)  # type: ignore
QMessageBox.critical = staticmethod(lambda *a, **k: None)   # type: ignore

from ntrip_panel import NtripConfigDialog, parse_sourcetable  # noqa: E402


class FakeManager:
    """Mock NTRIPManager：记录调用，可控连接成败与字节数。"""

    def __init__(self, connect_ok: bool = True, bytes_now: int = 1234):
        self.calls = []
        self._connected = False
        self._bytes = 0
        self.connect_ok = connect_ok
        self.bytes_now = bytes_now

    def load_config(self, cfg):
        self.calls.append(("load_config", dict(cfg)))

    def start(self):
        self.calls.append("start")
        self._connected = self.connect_ok
        if self.connect_ok:
            self._bytes = self.bytes_now
        return self.connect_ok

    def stop(self):
        self.calls.append("stop")
        self._connected = False

    def stats(self):
        return {
            "host": "", "mountpoint": "",
            "connected": self._connected,
            "bytes_received": self._bytes,
        }


def _fill(dlg, host="ntrip.example.com", port=2101, mp="BASE0",
          user="u", pw="secret"):
    dlg.host_edit.setText(host)
    dlg.port_spin.setValue(port)
    dlg.mount_combo.setCurrentText(mp)
    dlg.user_edit.setText(user)
    dlg.pass_edit.setText(pw)


# ---------------------------------------------------------------------------
# Test 1: 对话框创建不崩溃，默认字段正确
# ---------------------------------------------------------------------------
def test_dialog_creation_defaults():
    dlg = NtripConfigDialog()
    assert dlg.port_spin.value() == 2101, "默认端口应为 2101"
    assert dlg.pass_edit.echoMode() == QLineEdit.Password, "密码框应 Password 模式"
    assert dlg.mount_combo.isEditable(), "mountpoint 下拉应可编辑"
    assert not dlg.isModal(), "对话框应非模态"
    # 默认状态为未连接（红色点 + 文字）
    assert "未连接" in dlg._state_label.text()
    dlg.close()


# ---------------------------------------------------------------------------
# Test 2: 配置保存/加载往返一致
# ---------------------------------------------------------------------------
def test_config_roundtrip():
    dlg = NtripConfigDialog()
    _fill(dlg, host="rtc2.skylark.co.jp", port=2102, mp="JPL",
          user="bi4mib", pw="hunter2")
    cfg = dlg.get_config()
    dlg.close()

    dlg2 = NtripConfigDialog()
    dlg2.apply_config(cfg)
    out = dlg2.get_config()
    dlg2.close()

    assert out == cfg, f"往返不一致: {out} != {cfg}"
    assert out["password"] == "hunter2"
    assert out["port"] == 2102


# ---------------------------------------------------------------------------
# Test 3: mock 连接成功 -> start() 被调、状态变已连接、字节数更新
# ---------------------------------------------------------------------------
def test_mock_connect_success():
    mgr = FakeManager(connect_ok=True, bytes_now=2048)
    dlg = NtripConfigDialog(manager=mgr)
    _fill(dlg)

    dlg._on_connect_clicked()
    # 后台线程跑 manager.start()，等信号回主线程
    QTest.qWait(120)
    app.processEvents()

    assert "start" in mgr.calls, f"manager.start 未被调用: {mgr.calls}"
    assert "已连接" in dlg._state_label.text(), \
        f"状态应为已连接, 实际: {dlg._state_label.text()}"
    assert "2048" in dlg._state_label.text(), \
        f"应显示字节数 2048, 实际: {dlg._state_label.text()}"
    dlg.close()


# ---------------------------------------------------------------------------
# Test 4: mock 断开 -> stop() 被调、状态回未连接
# ---------------------------------------------------------------------------
def test_mock_disconnect():
    mgr = FakeManager(connect_ok=True)
    dlg = NtripConfigDialog(manager=mgr)
    _fill(dlg)
    dlg._on_connect_clicked()
    QTest.qWait(120)
    app.processEvents()
    assert "已连接" in dlg._state_label.text()

    dlg._on_disconnect_clicked()
    assert "stop" in mgr.calls, f"manager.stop 未被调用: {mgr.calls}"
    assert "未连接" in dlg._state_label.text(), \
        f"断开后应为未连接, 实际: {dlg._state_label.text()}"
    dlg.close()


# ---------------------------------------------------------------------------
# Test 5: sourcetable STR 行解析
# ---------------------------------------------------------------------------
def test_parse_sourcetable():
    sample = (
        "ICY 200 OK\r\n"
        "\r\n"
        "STR,CN,39.9,116.4,BJNEO,Example,RTCM 3.2,Multi-GNSS,G,EASYPATH,"
        "BASE0,OFF,?,?,N,None,N,0\r\n"
        "STR,CN,31.2,121.4,SHANG,Example,RTCM 3.2,GPS,G,NETWORK,"
        "SHANGHAI1,OFF,?,?,N,None,N,0\r\n"
        "NET,ABC,Network Desc,Country\r\n"   # NET 行应被忽略
        "ENDSOURCETABLE\r\n"
    )
    mounts = parse_sourcetable(sample)
    assert mounts == ["BASE0", "SHANGHAI1"], f"解析结果: {mounts}"


# ---------------------------------------------------------------------------
# Test 6: host/mountpoint 为空时点连接不调 start()（弹提示但不崩）
# ---------------------------------------------------------------------------
def test_connect_unconfigured_no_start():
    mgr = FakeManager(connect_ok=True)
    dlg = NtripConfigDialog(manager=mgr)
    # 只填 host，不填 mountpoint
    dlg.host_edit.setText("ntrip.example.com")
    dlg.mount_combo.setCurrentText("")

    dlg._on_connect_clicked()
    QTest.qWait(50)
    app.processEvents()

    assert "start" not in mgr.calls, \
        f"未配置 mountpoint 不应调用 start(): {mgr.calls}"
    assert "未连接" in dlg._state_label.text()
    dlg.close()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
