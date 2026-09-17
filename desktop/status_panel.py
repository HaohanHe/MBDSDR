"""
MBDSDR 设备状态面板
====================
连接状态、RSSI/SNR、GPS 定位、IMU 9 轴数据、录音状态。
"""

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

        self.conn_indicator = QLabel("●")
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
        gps_group = QGroupBox("GPS / 北斗")
        gps_layout = QGridLayout(gps_group)

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

        layout.addWidget(gps_group)

        # ---- IMU 9 轴 ----
        imu_group = QGroupBox("IMU 9 轴姿态")
        imu_layout = QGridLayout(imu_group)

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

        layout.addWidget(imu_group)

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
            self.conn_indicator.setStyleSheet("color: #6BA89A; font-size: 16pt;")
            self.conn_status.setText(message)
        else:
            self.conn_indicator.setStyleSheet("color: #B85C5C; font-size: 16pt;")
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
        """GPS 数据更新。"""
        fix = gps.get("fix", False)
        self.gps_fix.setText("已定位" if fix else "未定位")
        self.gps_fix.setStyleSheet("color: #6BA89A;" if fix else "color: #B85C5C;")

        self.gps_sats.setText(str(gps.get("sats", "--")))
        self.gps_lat.setText(f"{gps.get('lat', 0):.6f}")
        self.gps_lon.setText(f"{gps.get('lon', 0):.6f}")
        self.gps_alt.setText(f"{gps.get('alt', 0):.1f} m")
        self.gps_hdop.setText(f"{gps.get('hdop', 0):.1f}")

    @Slot(dict)
    def on_imu_updated(self, imu: dict):
        """IMU 数据更新。"""
        acc = imu.get("acc", [0, 0, 0])
        gyr = imu.get("gyr", [0, 0, 0])
        mag = imu.get("mag", [0, 0, 0])
        temp = imu.get("temp", 0)

        self.acc_x.setText(f"{acc[0]:.2f}")
        self.acc_y.setText(f"{acc[1]:.2f}")
        self.acc_z.setText(f"{acc[2]:.2f}")

        self.gyr_x.setText(f"{gyr[0]:.1f}")
        self.gyr_y.setText(f"{gyr[1]:.1f}")
        self.gyr_z.setText(f"{gyr[2]:.1f}")

        self.mag_x.setText(f"{mag[0]:.1f}")
        self.mag_y.setText(f"{mag[1]:.1f}")
        self.mag_z.setText(f"{mag[2]:.1f}")

        self.imu_temp.setText(f"{temp:.1f}")

    @Slot(str, dict)
    def on_tool_result(self, tool_name: str, result: dict):
        """工具调用结果（用于更新设备信息等）。"""
        if tool_name == "get_version":
            self.fw_version.setText(result.get("firmware", "--"))
            self.sdr_chip.setText(result.get("sdr", "--"))
            self.mcu_chip.setText(result.get("mcu", "--"))
