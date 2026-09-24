"""
MBDSDR 设备状态面板
====================
连接状态、RSSI/SNR、GPS 定位、IMU 9 轴数据、录音状态。
"""

import datetime

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QGroupBox, QGridLayout, QSizePolicy
)
from PySide6.QtGui import QFont


class StatusPanel(QWidget):
    """设备状态面板。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # ---- 连接状态 ----
        conn_group = QGroupBox("连接")
        conn_layout = QHBoxLayout(conn_group)

        self.conn_indicator = QLabel("OFF")
        self.conn_indicator.setStyleSheet("color: #B85C5C; font-size: 16pt;")
        conn_layout.addWidget(self.conn_indicator)

        self.conn_status = QLabel("未连接")
        self.conn_status.setObjectName("sectionTitle")
        conn_layout.addWidget(self.conn_status)
        conn_layout.addStretch()

        layout.addWidget(conn_group)

        # ---- 信号质量 ----
        signal_group = QGroupBox("信号质量")
        signal_layout = QGridLayout(signal_group)

        signal_layout.addWidget(QLabel("RSSI:"), 0, 0)
        self.rssi_value = QLabel("--")
        self.rssi_value.setObjectName("statusValue")
        signal_layout.addWidget(self.rssi_value, 0, 1)

        signal_layout.addWidget(QLabel("SNR:"), 0, 2)
        self.snr_value = QLabel("--")
        self.snr_value.setObjectName("statusValue")
        signal_layout.addWidget(self.snr_value, 0, 3)

        signal_layout.addWidget(QLabel("模式:"), 1, 0)
        self.mode_value = QLabel("--")
        self.mode_value.setObjectName("statusValue")
        signal_layout.addWidget(self.mode_value, 1, 1)

        signal_layout.addWidget(QLabel("频率:"), 1, 2)
        self.freq_value = QLabel("--")
        self.freq_value.setObjectName("statusValue")
        signal_layout.addWidget(self.freq_value, 1, 3)

        layout.addWidget(signal_group)

        # ---- GPS ----
        self.gps_group = QGroupBox("GPS / 北斗")
        gps_layout = QGridLayout(self.gps_group)

        gps_layout.addWidget(QLabel("定位:"), 0, 0)
        self.gps_fix = QLabel("--")
        self.gps_fix.setObjectName("statusValue")
        gps_layout.addWidget(self.gps_fix, 0, 1)

        gps_layout.addWidget(QLabel("卫星:"), 0, 2)
        self.gps_sats = QLabel("--")
        self.gps_sats.setObjectName("statusValue")
        gps_layout.addWidget(self.gps_sats, 0, 3)

        gps_layout.addWidget(QLabel("纬度:"), 1, 0)
        self.gps_lat = QLabel("--")
        self.gps_lat.setObjectName("statusValue")
        gps_layout.addWidget(self.gps_lat, 1, 1, 1, 3)

        gps_layout.addWidget(QLabel("经度:"), 2, 0)
        self.gps_lon = QLabel("--")
        self.gps_lon.setObjectName("statusValue")
        gps_layout.addWidget(self.gps_lon, 2, 1, 1, 3)

        gps_layout.addWidget(QLabel("高度:"), 3, 0)
        self.gps_alt = QLabel("--")
        self.gps_alt.setObjectName("statusValue")
        gps_layout.addWidget(self.gps_alt, 3, 1)

        gps_layout.addWidget(QLabel("HDOP:"), 3, 2)
        self.gps_hdop = QLabel("--")
        self.gps_hdop.setObjectName("statusValue")
        gps_layout.addWidget(self.gps_hdop, 3, 3)

        # 数据时间戳（来源/更新时间）
        gps_layout.addWidget(QLabel("更新:"), 4, 0)
        self.gps_timestamp = QLabel("--")
        self.gps_timestamp.setObjectName("statusValue")
        gps_layout.addWidget(self.gps_timestamp, 4, 1, 1, 3)

        layout.addWidget(self.gps_group)

        # ---- IMU 9 轴 ----
        self.imu_group = QGroupBox("IMU 9 轴姿态")
        imu_layout = QGridLayout(self.imu_group)

        # 加速度
        imu_layout.addWidget(QLabel("加速度:"), 0, 0)
        self.acc_x = QLabel("--")
        self.acc_x.setObjectName("statusValue")
        imu_layout.addWidget(self.acc_x, 0, 1)
        self.acc_y = QLabel("--")
        self.acc_y.setObjectName("statusValue")
        imu_layout.addWidget(self.acc_y, 0, 2)
        self.acc_z = QLabel("--")
        self.acc_z.setObjectName("statusValue")
        imu_layout.addWidget(self.acc_z, 0, 3)

        imu_layout.addWidget(QLabel("g"), 0, 4)

        # 陀螺仪
        imu_layout.addWidget(QLabel("陀螺仪:"), 1, 0)
        self.gyr_x = QLabel("--")
        self.gyr_x.setObjectName("statusValue")
        imu_layout.addWidget(self.gyr_x, 1, 1)
        self.gyr_y = QLabel("--")
        self.gyr_y.setObjectName("statusValue")
        imu_layout.addWidget(self.gyr_y, 1, 2)
        self.gyr_z = QLabel("--")
        self.gyr_z.setObjectName("statusValue")
        imu_layout.addWidget(self.gyr_z, 1, 3)

        imu_layout.addWidget(QLabel("dps"), 1, 4)

        # 磁力计
        imu_layout.addWidget(QLabel("磁力计:"), 2, 0)
        self.mag_x = QLabel("--")
        self.mag_x.setObjectName("statusValue")
        imu_layout.addWidget(self.mag_x, 2, 1)
        self.mag_y = QLabel("--")
        self.mag_y.setObjectName("statusValue")
        imu_layout.addWidget(self.mag_y, 2, 2)
        self.mag_z = QLabel("--")
        self.mag_z.setObjectName("statusValue")
        imu_layout.addWidget(self.mag_z, 2, 3)

        imu_layout.addWidget(QLabel("uT"), 2, 4)

        # 温度
        imu_layout.addWidget(QLabel("温度:"), 3, 0)
        self.imu_temp = QLabel("--")
        self.imu_temp.setObjectName("statusValue")
        imu_layout.addWidget(self.imu_temp, 3, 1)
        imu_layout.addWidget(QLabel("C"), 3, 2)

        # 数据时间戳
        imu_layout.addWidget(QLabel("更新:"), 4, 0)
        self.imu_timestamp = QLabel("--")
        self.imu_timestamp.setObjectName("statusValue")
        imu_layout.addWidget(self.imu_timestamp, 4, 1, 1, 3)

        layout.addWidget(self.imu_group)

        # ---- 设备信息 ----
        info_group = QGroupBox("设备信息")
        info_layout = QGridLayout(info_group)

        info_layout.addWidget(QLabel("固件:"), 0, 0)
        self.fw_version = QLabel("--")
        self.fw_version.setObjectName("statusValue")
        info_layout.addWidget(self.fw_version, 0, 1)

        info_layout.addWidget(QLabel("SDR:"), 1, 0)
        self.sdr_chip = QLabel("--")
        self.sdr_chip.setObjectName("statusValue")
        info_layout.addWidget(self.sdr_chip, 1, 1)

        info_layout.addWidget(QLabel("MCU:"), 2, 0)
        self.mcu_chip = QLabel("--")
        self.mcu_chip.setObjectName("statusValue")
        info_layout.addWidget(self.mcu_chip, 2, 1)

        info_layout.addWidget(QLabel("运行:"), 3, 0)
        self.uptime = QLabel("--")
        self.uptime.setObjectName("statusValue")
        info_layout.addWidget(self.uptime, 3, 1)

        layout.addWidget(info_group)

        layout.addStretch()

    # ========================================================================
    # 槽函数：从 MCP Worker 接收数据
    # ========================================================================

    @Slot(bool, str)
    def on_connection_changed(self, connected: bool, message: str):
        """连接状态改变。"""
        if connected:
            self.conn_indicator.setStyleSheet("color: #6BA89A; font-size: 12pt; font-weight: 600;")
            self.conn_indicator.setText("ON")
            self.conn_status.setText(message)
        else:
            self.conn_indicator.setStyleSheet("color: #B85C5C; font-size: 12pt; font-weight: 600;")
            self.conn_indicator.setText("OFF")
            self.conn_status.setText(message)

    @Slot(dict)
    def on_status_updated(self, status: dict):
        """SDR 状态更新。"""
        rssi = status.get("rssi", 0)
        snr = status.get("snr", 0)
        mode = status.get("mode_name", "--")
        freq = status.get("freq_display", "--")
        uptime = status.get("uptime", 0)

        self.rssi_value.setText(f"{rssi} dBm")
        self.snr_value.setText(f"{snr} dB")
        self.mode_value.setText(mode)
        self.freq_value.setText(freq)

        # 运行时间
        if uptime > 0:
            h, rem = divmod(uptime, 3600)
            m, s = divmod(rem, 60)
            self.uptime.setText(f"{h:02d}:{m:02d}:{s:02d}")

        # RSSI 颜色指示
        if rssi > -50:
            self.rssi_value.setStyleSheet("color: #6BA89A;")  # 强信号-绿
        elif rssi > -70:
            self.rssi_value.setStyleSheet("color: #C4B85C;")  # 中等-黄
        else:
            self.rssi_value.setStyleSheet("color: #B85C5C;")  # 弱-红

    @Slot(dict)
    def on_gps_updated(self, gps: dict):
        """GPS 数据更新。

        三要素：来源（real/sim/none）、定位状态、数据时间戳。
        source=="none" 或（无 fix 且非 sim）时一律显示 "--"，绝不用 0 伪装。
        """
        source = gps.get("source", "real")
        fix = gps.get("fix", False)

        # 数据时间戳
        self._update_timestamp(self.gps_timestamp, gps.get("timestamp"))

        if source == "none" or (not fix and source != "sim"):
            # 未连接 / 未定位：灰色，全部 "--"
            self.gps_fix.setText("未连接")
            self.gps_fix.setStyleSheet("color: #999999;")
            self.gps_sats.setText("--")
            self.gps_lat.setText("--")
            self.gps_lon.setText("--")
            self.gps_alt.setText("--")
            self.gps_hdop.setText("--")
            self.gps_group.setTitle("GPS / 北斗")
        elif source == "sim":
            # 模拟模式：橙色"模拟定位"角标，坐标加 [模拟] 前缀
            self.gps_fix.setText("模拟定位")
            self.gps_fix.setStyleSheet("color: #C4845C;")
            self.gps_group.setTitle("GPS / 北斗 [模拟]")
            lat = gps.get("lat")
            lon = gps.get("lon")
            alt = gps.get("alt")
            hdop = gps.get("hdop")
            self.gps_sats.setText(str(gps.get("sats", 0)))
            self.gps_lat.setText(f"[模拟] {lat:.6f}" if lat is not None else "--")
            self.gps_lon.setText(f"[模拟] {lon:.6f}" if lon is not None else "--")
            self.gps_alt.setText(f"[模拟] {alt:.1f} m" if alt is not None else "--")
            self.gps_hdop.setText(f"[模拟] {hdop:.1f}" if hdop is not None else "--")
        else:
            # source=="real" 且 fix==True：绿色"已定位"，真实坐标
            self.gps_fix.setText("已定位")
            self.gps_fix.setStyleSheet("color: #6BA89A;")
            self.gps_group.setTitle("GPS / 北斗")
            lat = gps.get("lat")
            lon = gps.get("lon")
            alt = gps.get("alt")
            hdop = gps.get("hdop")
            self.gps_sats.setText(str(gps.get("sats", 0)))
            self.gps_lat.setText(f"{lat:.6f}" if lat is not None else "--")
            self.gps_lon.setText(f"{lon:.6f}" if lon is not None else "--")
            self.gps_alt.setText(f"{alt:.1f} m" if alt is not None else "--")
            self.gps_hdop.setText(f"{hdop:.1f}" if hdop is not None else "--")

    def update_gnss(self, fix_dict: dict):
        """由真实串口 GNSS（mbdsdr_ai.serial_gnss.get_fix）驱动 GPS 组。

        fix_dict 口径：{source, latitude, longitude, altitude_m, satellites,
                       hdop, speed_kmh, course_deg, utc_time, fix_quality, timestamp}
        source=="real"  → 绿色(#5B8C5A)显示真实坐标/卫星数/HDOP/高度；
        source=="none"  → 灰色“未连接/无数据”，坐标一律空，绝不造假。
        （UI 与真硬件联调待插模块后再细化。）
        """
        source = fix_dict.get("source", "none")
        ts = fix_dict.get("timestamp")
        self._update_timestamp(self.gps_timestamp, ts)

        if source != "real":
            # 未连接 / 无定位：灰色
            self.gps_fix.setText("未连接")
            self.gps_fix.setStyleSheet("color: #999999;")
            self.gps_sats.setText("--")
            self.gps_lat.setText("--")
            self.gps_lon.setText("--")
            self.gps_alt.setText("--")
            self.gps_hdop.setText("--")
            self.gps_group.setTitle("GPS / 北斗")
            return

        # 已定位：绿色 #5B8C5A
        self.gps_fix.setText("已定位")
        self.gps_fix.setStyleSheet("color: #5B8C5A;")
        self.gps_group.setTitle("GPS / 北斗")
        lat = fix_dict.get("latitude")
        lon = fix_dict.get("longitude")
        alt = fix_dict.get("altitude_m")
        hdop = fix_dict.get("hdop")
        sats = fix_dict.get("satellites")
        self.gps_sats.setText(str(sats) if sats is not None else "--")
        self.gps_lat.setText(f"{lat:.6f}" if lat is not None else "--")
        self.gps_lon.setText(f"{lon:.6f}" if lon is not None else "--")
        self.gps_alt.setText(f"{alt:.1f} m" if alt is not None else "--")
        self.gps_hdop.setText(f"{hdop:.1f}" if hdop is not None else "--")

    @Slot(dict)
    def on_imu_updated(self, imu: dict):
        """IMU 数据更新。

        source=="none" 时全部 "--"；sim 模式在 group 标题打 [模拟] 角标。
        """
        source = imu.get("source", "real")

        # 数据时间戳
        self._update_timestamp(self.imu_timestamp, imu.get("timestamp"))

        if source == "none":
            # 未连接：全部 "--"
            self.acc_x.setText("--")
            self.acc_y.setText("--")
            self.acc_z.setText("--")
            self.gyr_x.setText("--")
            self.gyr_y.setText("--")
            self.gyr_z.setText("--")
            self.mag_x.setText("--")
            self.mag_y.setText("--")
            self.mag_z.setText("--")
            self.imu_temp.setText("--")
            self.imu_group.setTitle("IMU 9 轴姿态 [未连接]")
            return

        # 模拟模式：标题加角标；真实模式：正常标题
        if source == "sim":
            self.imu_group.setTitle("IMU 9 轴姿态 [模拟]")
        else:
            self.imu_group.setTitle("IMU 9 轴姿态")

        acc = imu.get("acc") or [None, None, None]
        gyr = imu.get("gyr") or [None, None, None]
        mag = imu.get("mag") or [None, None, None]
        temp = imu.get("temp")

        def _fmt(v, nd):
            return f"{v:.{nd}f}" if v is not None else "--"

        self.acc_x.setText(_fmt(acc[0], 2))
        self.acc_y.setText(_fmt(acc[1], 2))
        self.acc_z.setText(_fmt(acc[2], 2))

        self.gyr_x.setText(_fmt(gyr[0], 1))
        self.gyr_y.setText(_fmt(gyr[1], 1))
        self.gyr_z.setText(_fmt(gyr[2], 1))

        self.mag_x.setText(_fmt(mag[0], 1))
        self.mag_y.setText(_fmt(mag[1], 1))
        self.mag_z.setText(_fmt(mag[2], 1))

        self.imu_temp.setText(_fmt(temp, 1))

    def _update_timestamp(self, label: QLabel, ts):
        """把 unix 时间戳格式化为 '更新于 HH:MM:SS'；无值时显示 '--'。"""
        if ts is None:
            label.setText("--")
            return
        try:
            dt = datetime.datetime.fromtimestamp(float(ts))
            label.setText(f"更新于 {dt.strftime('%H:%M:%S')}")
        except Exception:
            label.setText("--")

    @Slot(str, dict)
    def on_tool_result(self, tool_name: str, result: dict):
        """工具调用结果（用于更新设备信息等）。"""
        if tool_name == "get_version":
            self.fw_version.setText(result.get("firmware", "--"))
            self.sdr_chip.setText(result.get("sdr", "--"))
            self.mcu_chip.setText(result.get("mcu", "--"))
