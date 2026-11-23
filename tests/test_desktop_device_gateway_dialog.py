# SPDX-License-Identifier: MIT
"""桌面「多设备网关」对话框（M2.1b）：显式开关 + 配对码 + 名册。

不变量：
1. 默认回环；只有主机下拉选到非 127.0.0.1 才向内核传 allow_lan。
2. 关闭/停止对话框必须真的停掉监听（不留下无人看管的端口）。
3. 未设站址仍可启动，但状态栏明说 /panorama 会 500（不拿示例站顶替）。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from mbdsdr_ai.device_gateway import PairingRegistry  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def _registry():
    codes = iter(["111111", "222222", "333333"])
    tokens = iter(["t1", "t2", "t3"])
    return PairingRegistry(code_factory=lambda: next(codes, "999999"),
                           token_factory=lambda: next(tokens, "tX"))


def _station(lat=45.0, lon=120.0, alt=10.0):
    return lambda: (lat, lon, alt)


def test_start_stop_and_code(qapp):
    from device_gateway_dialog import DeviceGatewayDialog

    dlg = DeviceGatewayDialog(_station(), port=0, hosts=["127.0.0.1"],
                              registry=_registry())
    try:
        assert dlg._gateway is None
        dlg.start_gateway()
        assert dlg._gateway is not None
        assert dlg.code_label.text() == "111111"
        assert "127.0.0.1" in dlg.url_label.text()
        assert dlg.start_btn.isEnabled() is False
        assert dlg.stop_btn.isEnabled() is True
        dlg.stop_gateway()
        assert dlg._gateway is None
        assert dlg.code_label.text() == "—"
        assert dlg.table.rowCount() == 0
    finally:
        dlg.close()


def test_roster_and_rotation(qapp):
    from device_gateway_dialog import DeviceGatewayDialog

    dlg = DeviceGatewayDialog(_station(), port=0, hosts=["127.0.0.1"],
                              registry=_registry())
    try:
        dlg.start_gateway()
        gw = dlg._gateway
        paired = gw.registry.pair("111111", "镜头手机", "lens")
        assert paired["ok"] is True
        dlg._refresh_roster()
        assert dlg.table.rowCount() == 1
        assert dlg.table.item(0, 0).text() == "lens"
        assert dlg.table.item(0, 1).text() == "镜头手机"
        dlg.rotate_code()
        assert dlg.code_label.text() == "222222"
        # 旧码失效，新码可用
        assert gw.registry.pair("111111", "x", "lens")["ok"] is False
        assert gw.registry.pair("222222", "手持", "handheld")["ok"] is True
    finally:
        dlg.close()


def test_close_really_stops_listener(qapp):
    from device_gateway_dialog import DeviceGatewayDialog

    dlg = DeviceGatewayDialog(_station(), port=0, hosts=["127.0.0.1"],
                              registry=_registry())
    dlg.start_gateway()
    gw = dlg._gateway
    assert gw is not None
    dlg.close()
    assert dlg._gateway is None
    assert gw._server is None  # 监听已关闭


def test_without_station_starts_but_warns(qapp):
    from device_gateway_dialog import DeviceGatewayDialog

    dlg = DeviceGatewayDialog(lambda: (None, None, None), port=0,
                              hosts=["127.0.0.1"], registry=_registry())
    try:
        dlg.start_gateway()
        assert dlg._gateway is not None
        assert "未设置站址" in dlg.status.text()
        dlg._gateway.registry.pair("111111", "手机", "handheld")
        status, raw, _ = dlg._gateway.dispatch(
            "GET", "/panorama", b"", {"Authorization": "Bearer t1"})
        assert status == 500
        assert "未配置站址" in raw.decode("utf-8")
    finally:
        dlg.close()


def test_private_host_requires_allow_lan_path(qapp, monkeypatch):
    """下拉只有私有地址时才可能传 allow_lan；公网/通配不在候选里。"""
    from device_gateway_dialog import local_private_ips

    ips = local_private_ips()
    for ip in ips:
        import ipaddress
        assert ipaddress.ip_address(ip).is_private
        assert ip != "0.0.0.0"


def test_main_window_wires_gateway_action():
    from main_window import MainWindow
    import inspect

    src = inspect.getsource(MainWindow._open_device_gateway)
    assert "DeviceGatewayDialog" in src
    assert "self._device_station_triplet" in src
    assert hasattr(MainWindow, "_device_station_triplet")


def test_eyepiece_identification_line(qapp):
    """目镜识别结果单独一行显示，不并进融合位姿。"""
    import json
    import time

    from device_gateway_dialog import DeviceGatewayDialog

    dlg = DeviceGatewayDialog(_station(), port=0, hosts=["127.0.0.1"],
                              registry=_registry())
    try:
        dlg.start_gateway()
        gw = dlg._gateway
        lens = gw.registry.pair("111111", "镜头手机", "lens")
        eye = gw.registry.pair("111111", "目镜手机", "eyepiece")
        dlg._refresh_roster()
        assert dlg.eye_id_label.text() == "目镜识别：—"

        now = time.time()
        for paired, role, extra in [
            (lens, "lens", {}),
            (eye, "eyepiece", {"identification": {
                "name": "织女一", "hip": 91262, "separation_deg": 0.35}}),
        ]:
            body = {
                "schema": "sky-pose/1",
                "source": paired["device"]["id"],
                "role": role,
                "t_utc": now,
                "az_deg": 100.0,
                "alt_deg": 40.0,
                "accuracy_deg": 2.0,
                "roll_deg": 0.0,
                **extra,
            }
            status, raw, _ = gw.dispatch(
                "POST", "/pose", json.dumps(body).encode(),
                {"Authorization": "Bearer " + paired["token"]})
            assert status == 200, raw

        dlg._refresh_roster()
        text = dlg.eye_id_label.text()
        assert text.startswith("目镜识别：")
        assert "织女一" in text
        assert "HIP 91262" in text
        assert "角距 0.35°" in text
    finally:
        dlg.close()


def test_fuse_label_shows_weighted_pose_and_reason(qapp):
    """对话框把 lens+eyepiece 两条真实上报喂 fuse，显示加权结果与不可用原因。"""
    import json
    import time

    from device_gateway_dialog import DeviceGatewayDialog

    dlg = DeviceGatewayDialog(_station(), port=0, hosts=["127.0.0.1"],
                              registry=_registry())
    try:
        dlg.start_gateway()
        gw = dlg._gateway
        lens = gw.registry.pair("111111", "镜头手机", "lens")
        eye = gw.registry.pair("111111", "目镜手机", "eyepiece")
        dlg._refresh_roster()
        # 还没位姿：如实说不可用，并带原因，不显示猜测朝向。
        assert "不可用" in dlg.fuse_label.text()
        assert "observations 为空" in dlg.fuse_label.text()

        now = time.time()
        rows = [
            (lens, "lens", 100.0, 40.0, 4.0),
            (eye, "eyepiece", 110.0, 44.0, 1.0),
        ]
        for paired, role, az, alt, acc in rows:
            body = {
                "schema": "sky-pose/1",
                "source": paired["device"]["id"],
                "role": role,
                "t_utc": now,
                "az_deg": az,
                "alt_deg": alt,
                "accuracy_deg": acc,
                "roll_deg": 0.0,
            }
            status, raw, _ = gw.dispatch(
                "POST", "/pose", json.dumps(body).encode(),
                {"Authorization": "Bearer " + paired["token"]})
            assert status == 200, raw

        dlg._refresh_roster()
        text = dlg.fuse_label.text()
        assert text.startswith("融合位姿：方位")
        assert "多机融合" in text
        assert "2 条观测" in text
        # 精度加权：目镜 1° 权重大，融合方位更靠近 110°。
        az_text = text.split("方位 ")[1].split("°")[0]
        assert 105.0 < float(az_text) < 110.0
    finally:
        dlg.close()
