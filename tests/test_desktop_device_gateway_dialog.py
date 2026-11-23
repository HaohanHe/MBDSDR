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


def test_tle_file_enables_offline_passes(qapp, tmp_path):
    """选本地 TLE 后 /passes 可用；停网关再把 TLE 输入解锁。"""
    import json

    from device_gateway_dialog import DeviceGatewayDialog

    tle = tmp_path / "tle.txt"
    tle.write_text("\n".join([
        "ISS (ZARYA)",
        "1 25544U 98067A   24001.50000000  .00016717  00000-0  30303-3 0  9993",
        "2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.49309023 42596",
    ]), encoding="utf-8")
    dlg = DeviceGatewayDialog(_station(), port=0, hosts=["127.0.0.1"],
                              registry=_registry())
    try:
        dlg.tle_edit.setText(str(tle))
        dlg.start_gateway()
        assert "passes 已启用" in dlg.status.text()
        assert dlg.tle_edit.isEnabled() is False
        assert dlg.tle_browse_btn.isEnabled() is False

        paired = dlg._gateway.registry.pair("111111", "头显", "headset")
        status, raw, _ = dlg._gateway.dispatch(
            "GET", "/passes", b"",
            {"Authorization": "Bearer " + paired["token"]})
        assert status == 200
        data = json.loads(raw)
        assert data["schema"] == "mbdsdr.sat-passes/1"
        assert data["count"] == len(data["passes"])
        assert data["count"] >= 1
        assert data["station"]["lat_deg"] == 45.0

        dlg.stop_gateway()
        assert dlg.tle_edit.isEnabled() is True
    finally:
        dlg.close()


def test_without_tle_file_passes_is_503(qapp):
    """TLE 留空时 /passes 明确 503，不拿假过境顶替。"""
    from device_gateway_dialog import DeviceGatewayDialog

    dlg = DeviceGatewayDialog(_station(), port=0, hosts=["127.0.0.1"],
                              registry=_registry())
    try:
        dlg.start_gateway()
        assert "passes 未启用" in dlg.status.text()
        paired = dlg._gateway.registry.pair("111111", "手机", "handheld")
        status, raw, _ = dlg._gateway.dispatch(
            "GET", "/passes", b"",
            {"Authorization": "Bearer " + paired["token"]})
        assert status == 503
        assert '"passes_unavailable"' in raw.decode("utf-8")
    finally:
        dlg.close()


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


def test_poses_label_lists_each_device(qapp):
    """对话框逐设备列出位姿；目镜识别跟在行尾，不并入粗指向。"""
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
        assert dlg.poses_label.text() == "逐设备位姿：—"

        now = time.time()
        lens_body = {
            "schema": "sky-pose/1", "source": lens["device"]["id"],
            "role": "lens", "t_utc": now, "az_deg": 100.0,
            "alt_deg": 40.0, "accuracy_deg": 4.0, "roll_deg": 0.0,
        }
        eye_body = {
            "schema": "sky-pose/1", "source": eye["device"]["id"],
            "role": "eyepiece", "t_utc": now, "az_deg": 110.0,
            "alt_deg": 44.0, "accuracy_deg": 1.0, "roll_deg": 0.0,
            "identification": {"name": "织女一", "hip": 91262,
                               "separation_deg": 0.35},
        }
        for paired, body in ((lens, lens_body), (eye, eye_body)):
            status, raw, _ = gw.dispatch(
                "POST", "/pose", json.dumps(body).encode(),
                {"Authorization": "Bearer " + paired["token"]})
            assert status == 200, raw

        dlg._refresh_roster()
        text = dlg.poses_label.text()
        assert text.startswith("逐设备位姿：")
        lines = text.split("\n")
        assert len(lines) == 2, text
        # recent_poses 按 role 排序：eyepiece 在 lens 之前。
        assert "目镜·方位 110.0° 高度 44.0° ±1.0°" in lines[0]
        assert "织女一 HIP 91262 0.35°" in lines[0]
        assert "镜头·方位 100.0° 高度 40.0° ±4.0°" in lines[1]
        # 识别只跟在目镜一行，不混进镜头行。
        assert "织女一" not in lines[1]
    finally:
        dlg.close()


def test_invalid_rtl_tcp_blocks_start(qapp):
    """rtl_tcp 写法非法时不启动网关，状态栏给具体原因。"""
    from device_gateway_dialog import DeviceGatewayDialog

    dlg = DeviceGatewayDialog(_station(), port=0, hosts=["127.0.0.1"],
                              registry=_registry())
    try:
        dlg.sdr_edit.setText("127.0.0.1")
        dlg.start_gateway()
        assert dlg._gateway is None
        assert "rtl_tcp 地址必须是 HOST:PORT" in dlg.status.text()
        assert "网关未启动" in dlg.status.text()
    finally:
        dlg.close()


def test_sdr_lease_label_and_desktop_busy_refusal(qapp):
    """填 rtl_tcp 后：状态行/标签显示空闲与占用；桌面自报占用拒绝新授权。"""
    import json

    from device_gateway_dialog import DeviceGatewayDialog

    dlg = DeviceGatewayDialog(_station(), port=0, hosts=["127.0.0.1"],
                              registry=_registry())
    try:
        dlg.sdr_edit.setText("127.0.0.1:1234")
        dlg.start_gateway()
        gw = dlg._gateway
        assert gw is not None
        assert "SDR 授权已启用" in dlg.status.text()
        assert dlg.sdr_edit.isEnabled() is False
        assert dlg.desktop_busy_check.isEnabled() is False
        assert dlg.sdr_label.text().startswith("SDR 授权：空闲")
        assert "127.0.0.1:1234" in dlg.sdr_label.text()

        status, raw, _ = gw.dispatch("GET", "/health", b"", {})
        assert status == 200
        assert json.loads(raw)["sdr_lease"] is True

        headset = gw.registry.pair("111111", "头显", "headset")
        status, raw, _ = gw.dispatch(
            "POST", "/sdr/lease", json.dumps({"purpose": "直连频谱"}).encode(),
            {"Authorization": "Bearer " + headset["token"]})
        assert status == 200, raw
        dlg._refresh_roster()
        text = dlg.sdr_label.text()
        assert "头显" in text and "占用中" in text

        # 桌面自报占用后，另一台头显申请被拒（503），标签也标注暂停签发。
        dlg.desktop_busy_check.setChecked(True)
        second = gw.registry.pair("111111", "头显2", "headset")
        status, raw, _ = gw.dispatch(
            "POST", "/sdr/lease", b"",
            {"Authorization": "Bearer " + second["token"]})
        assert status == 503
        assert json.loads(raw)["error"] == "sdr_busy_desktop"
        dlg._refresh_roster()
        assert "暂停签发" in dlg.sdr_label.text()

        dlg.stop_gateway()
        assert dlg.sdr_edit.isEnabled() is True
        assert dlg.sdr_label.text() == "SDR 授权：—"
    finally:
        dlg.close()


def test_status_endpoint_from_dialog(qapp):
    """/status 走对话框的站址与 SDR 段；未探测 dongle 要如实标注。"""
    import json

    from device_gateway_dialog import DeviceGatewayDialog

    dlg = DeviceGatewayDialog(_station(), port=0, hosts=["127.0.0.1"],
                              registry=_registry())
    try:
        dlg.sdr_edit.setText("127.0.0.1:1234")
        dlg.start_gateway()
        gw = dlg._gateway
        paired = gw.registry.pair("111111", "手机", "handheld")
        status, raw, _ = gw.dispatch(
            "GET", "/status", b"",
            {"Authorization": "Bearer " + paired["token"]})
        assert status == 200
        data = json.loads(raw)
        assert data["schema"] == "mbdsdr.desktop-status/1"
        assert data["authority"] == "desktop"
        assert data["station"] == {
            "lat_deg": 45.0, "lon_deg": 120.0, "alt_m": 10.0}
        assert data["sdr"]["rtl_tcp"] == {"host": "127.0.0.1", "port": 1234}
        assert data["sdr"]["lease_active"] is False
        assert data["sdr"]["dongle_probed"] is False
        assert data["missing"] == []
    finally:
        dlg.close()


def test_spectrum_endpoint_from_dialog(qapp):
    """/spectrum 只转桌面当前帧：没帧 503，有帧给只读快照。"""
    import json

    from device_gateway_dialog import DeviceGatewayDialog

    live = {"frame": None}
    dlg = DeviceGatewayDialog(_station(), port=0, hosts=["127.0.0.1"],
                              registry=_registry(),
                              spectrum_frame_provider=lambda: live["frame"])
    try:
        dlg.start_gateway()
        gw = dlg._gateway
        paired = gw.registry.pair("111111", "头显", "headset")
        headers = {"Authorization": "Bearer " + paired["token"]}

        status, raw, _ = gw.dispatch("GET", "/spectrum", b"", headers)
        assert status == 503
        assert json.loads(raw)["error"] == "spectrum_unavailable"

        live["frame"] = {
            "center_freq_hz": 100e6,
            "sample_rate_hz": 1e6,
            "fft_size": 2,
            "powers_db": [-90.0, -30.0],
            "source": "live",
        }
        status, raw, _ = gw.dispatch("GET", "/spectrum", b"", headers)
        assert status == 200
        data = json.loads(raw)
        assert data["schema"] == "mbdsdr.spectrum-snapshot/1"
        assert data["readonly"] is True and data["realtime"] is False
        assert data["bins"] == 2
        assert data["peak_freq_hz"] == 100.25e6
        assert data["powers_db"] == [-90.0, -30.0]
    finally:
        dlg.close()


def test_spectrum_label_reflects_frame_source(qapp):
    """桌面状态行如实反映 /spectrum 有没有帧，不造假帧。"""
    from device_gateway_dialog import DeviceGatewayDialog

    live = {"frame": None}
    dlg = DeviceGatewayDialog(_station(), port=0, hosts=["127.0.0.1"],
                              registry=_registry(),
                              spectrum_frame_provider=lambda: live["frame"])
    try:
        dlg._refresh_spectrum_label()
        assert "无帧" in dlg.spectrum_label.text()

        live["frame"] = {
            "center_freq_hz": 100e6,
            "sample_rate_hz": 1e6,
            "fft_size": 2,
            "powers_db": [-90.0, -20.0],
        }
        dlg._refresh_spectrum_label()
        text = dlg.spectrum_label.text()
        assert "SDR 在收" in text
        assert "2 bin" in text
        assert "帧龄 未知" in text
    finally:
        dlg.close()


def test_spectrum_label_without_provider(qapp):
    from device_gateway_dialog import DeviceGatewayDialog

    dlg = DeviceGatewayDialog(_station(), port=0, hosts=["127.0.0.1"],
                              registry=_registry())
    try:
        dlg._refresh_spectrum_label()
        assert "未接入" in dlg.spectrum_label.text()
    finally:
        dlg.close()


def test_status_without_station_marks_missing(qapp):
    import json

    from device_gateway_dialog import DeviceGatewayDialog

    dlg = DeviceGatewayDialog(lambda: (None, None, None), port=0,
                              hosts=["127.0.0.1"], registry=_registry())
    try:
        dlg.start_gateway()
        gw = dlg._gateway
        paired = gw.registry.pair("111111", "手机", "handheld")
        status, raw, _ = gw.dispatch(
            "GET", "/status", b"",
            {"Authorization": "Bearer " + paired["token"]})
        assert status == 200
        data = json.loads(raw)
        assert data["station"] is None
        assert "station" in data["missing"]
        assert "sdr" in data["missing"]  # rtl_tcp 留空
    finally:
        dlg.close()
