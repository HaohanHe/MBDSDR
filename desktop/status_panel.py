"""
MBDSDR 设备状态面板
====================
连接状态、RSSI/SNR/S-meter、GPS 定位、IMU 9 轴数据、SDR 接收参数、
SDR 设备信息、UTC 时钟。

所有数值字段均从真实后端（mbdsdr_ai.sdr_backend.SDRBackend）读取；
无数据时一律显 "--"，绝不显示 0 或默认值伪装成有效读数。
"""

import datetime

from PySide6.QtCore import Qt, Signal, Slot, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QGroupBox, QGridLayout, QSizePolicy, QProgressBar
)
from PySide6.QtGui import QFont


# ---------------------------------------------------------------------------
# S-meter 校准参考（业余无线电通用约定）
#   S9  = -73 dBFS（在 500 Hz 带宽内）
#   S0  = -127 dBFS
#   每个 S 点 = 6 dB
#   S9 以上用 "+xx dB" 表示
# ---------------------------------------------------------------------------
_S9_DBFS = -73.0
_S0_DBFS = -127.0
_DB_PER_S = 6.0
_LEVEL_MIN = -120.0
_LEVEL_MAX = 0.0


def _fmt_freq(hz):
    """把 Hz 格式化为易读频率字符串。无值返回 '--'。"""
    if hz is None:
        return "--"
    try:
        hz = float(hz)
    except (TypeError, ValueError):
        return "--"
    if hz >= 1e6:
        return f"{hz / 1e6:.3f} MHz"
    if hz >= 1e3:
        return f"{hz / 1e3:.3f} kHz"
    return f"{hz:.0f} Hz"


def _fmt_rate(hz):
    """采样率格式化。"""
    if hz is None:
        return "--"
    try:
        hz = float(hz)
    except (TypeError, ValueError):
        return "--"
    if hz >= 1e6:
        return f"{hz / 1e6:.3f} MHz"
    return f"{hz / 1e3:.0f} kHz"


def _fmt_bw(hz):
    """解调带宽格式化。0/None 视为自动。"""
    if hz is None:
        return "--"
    try:
        hz = float(hz)
    except (TypeError, ValueError):
        return "--"
    if hz <= 0:
        return "自动"
    if hz >= 1e6:
        return f"{hz / 1e6:.3f} MHz"
    return f"{hz / 1e3:.0f} kHz"


def _dbfs_to_smeter(dbfs):
    """把 dBFS 映射到 S 表字符串。无值返回 '--'。"""
    if dbfs is None:
        return "--"
    try:
        dbfs = float(dbfs)
    except (TypeError, ValueError):
        return "--"
    if dbfs >= _S9_DBFS:
        extra = dbfs - _S9_DBFS
        return f"S9 +{extra:.0f} dB"
    s = (dbfs - _S0_DBFS) / _DB_PER_S
    if s < 0:
        return "S0"
    return f"S{int(round(s))}"


class StatusPanel(QWidget):
    """设备状态面板。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sdr_connected = False
        self._build_ui()
        self._start_utc_clock()

    # ========================================================================
    # UI 构建
    # ========================================================================

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # ---- 连接状态（含 UTC 时钟）----
        conn_group = QGroupBox("连接")
        conn_layout = QHBoxLayout(conn_group)

        self.conn_indicator = QLabel("OFF")
        self.conn_indicator.setStyleSheet("color: #B85C5C; font-size: 16pt;")
        conn_layout.addWidget(self.conn_indicator)

        self.conn_status = QLabel("未连接")
        self.conn_status.setObjectName("sectionTitle")
        conn_layout.addWidget(self.conn_status)
        conn_layout.addStretch()

        # UTC 时钟（对标 SDR++ / GQRX 顶栏 UTC 显示）
        self.utc_label = QLabel("UTC: --:--:--")
        self.utc_label.setStyleSheet("color: #5B7B8C; font-family: monospace;")
        conn_layout.addWidget(self.utc_label)

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

        # S-meter 读数
        signal_layout.addWidget(QLabel("S表:"), 2, 0)
        self.smeter_value = QLabel("--")
        self.smeter_value.setObjectName("statusValue")
        signal_layout.addWidget(self.smeter_value, 2, 1)
        signal_layout.addWidget(QLabel("电平:"), 2, 2)

        # 可视化电平条（范围 -120~0 dBFS）
        self.level_bar = QProgressBar()
        self.level_bar.setRange(0, int(_LEVEL_MAX - _LEVEL_MIN))  # 0..120
        self.level_bar.setValue(0)
        self.level_bar.setTextVisible(False)
        self.level_bar.setFixedHeight(10)
        self._set_level_bar_color("#9AA5AC")  # 灰色（无信号）
        signal_layout.addWidget(self.level_bar, 2, 3)

        layout.addWidget(signal_group)

        # ---- SDR 接收参数（新增）----
        rx_group = QGroupBox("SDR 接收参数")
        rx_layout = QGridLayout(rx_group)

        # 每行两个字段：标签 | 值 | 标签 | 值
        rx_layout.addWidget(QLabel("中心频率:"), 0, 0)
        self.rx_freq = QLabel("--")
        self.rx_freq.setObjectName("statusValue")
        rx_layout.addWidget(self.rx_freq, 0, 1)

        rx_layout.addWidget(QLabel("采样率:"), 0, 2)
        self.rx_srate = QLabel("--")
        self.rx_srate.setObjectName("statusValue")
        rx_layout.addWidget(self.rx_srate, 0, 3)

        rx_layout.addWidget(QLabel("解调带宽:"), 1, 0)
        self.rx_bw = QLabel("--")
        self.rx_bw.setObjectName("statusValue")
        rx_layout.addWidget(self.rx_bw, 1, 1)

        rx_layout.addWidget(QLabel("解调模式:"), 1, 2)
        self.rx_demod = QLabel("--")
        self.rx_demod.setObjectName("statusValue")
        rx_layout.addWidget(self.rx_demod, 1, 3)

        rx_layout.addWidget(QLabel("射频增益:"), 2, 0)
        self.rx_gain = QLabel("--")
        self.rx_gain.setObjectName("statusValue")
        rx_layout.addWidget(self.rx_gain, 2, 1)

        rx_layout.addWidget(QLabel("AGC:"), 2, 2)
        self.rx_agc = QLabel("--")
        self.rx_agc.setObjectName("statusValue")
        rx_layout.addWidget(self.rx_agc, 2, 3)

        rx_layout.addWidget(QLabel("静噪:"), 3, 0)
        self.rx_squelch = QLabel("--")
        self.rx_squelch.setObjectName("statusValue")
        rx_layout.addWidget(self.rx_squelch, 3, 1)

        rx_layout.addWidget(QLabel("音量:"), 3, 2)
        self.rx_volume = QLabel("--")
        self.rx_volume.setObjectName("statusValue")
        rx_layout.addWidget(self.rx_volume, 3, 3)

        rx_layout.addWidget(QLabel("PPM:"), 4, 0)
        self.rx_ppm = QLabel("--")
        self.rx_ppm.setObjectName("statusValue")
        rx_layout.addWidget(self.rx_ppm, 4, 1)

        rx_layout.addWidget(QLabel("Offset:"), 4, 2)
        self.rx_offset = QLabel("--")
        self.rx_offset.setObjectName("statusValue")
        rx_layout.addWidget(self.rx_offset, 4, 3)

        layout.addWidget(rx_group)

        # ---- GPS ----
        self.gps_group = QGroupBox("GPS / 北斗")
        gps_layout = QGridLayout(self.gps_group)

        gps_layout.addWidget(QLabel("定位:"), 0, 0)
        self.gps_fix = QLabel("未连接")
        self.gps_fix.setObjectName("statusValue")
        self.gps_fix.setStyleSheet("color: #999999;")
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

        # 卫星信号强度（可选）：fix_dict 带 satellite_signals 时显示最强 5 颗 SNR；
        # 无该字段时保持 "--"，不报错。
        gps_layout.addWidget(QLabel("信号:"), 5, 0)
        self.gps_signals = QLabel("--")
        self.gps_signals.setObjectName("statusValue")
        gps_layout.addWidget(self.gps_signals, 5, 1, 1, 3)

        layout.addWidget(self.gps_group)

        # ---- IMU 9 轴 ----
        self.imu_group = QGroupBox("IMU 9 轴姿态 [未连接]")
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

        # ---- 设备信息（通用 SDR 设备字段 + 原有 ai-sdr Mini 字段）----
        info_group = QGroupBox("设备信息")
        info_layout = QGridLayout(info_group)

        info_layout.addWidget(QLabel("设备名:"), 0, 0)
        self.dev_name = QLabel("未连接")
        self.dev_name.setObjectName("statusValue")
        info_layout.addWidget(self.dev_name, 0, 1, 1, 3)

        info_layout.addWidget(QLabel("驱动:"), 1, 0)
        self.dev_type = QLabel("--")
        self.dev_type.setObjectName("statusValue")
        info_layout.addWidget(self.dev_type, 1, 1)

        info_layout.addWidget(QLabel("调谐器:"), 1, 2)
        self.dev_tuner = QLabel("--")
        self.dev_tuner.setObjectName("statusValue")
        info_layout.addWidget(self.dev_tuner, 1, 3)

        info_layout.addWidget(QLabel("序列号:"), 2, 0)
        self.dev_serial = QLabel("--")
        self.dev_serial.setObjectName("statusValue")
        info_layout.addWidget(self.dev_serial, 2, 1, 1, 3)

        info_layout.addWidget(QLabel("连接时长:"), 3, 0)
        self.dev_uptime = QLabel("--")
        self.dev_uptime.setObjectName("statusValue")
        info_layout.addWidget(self.dev_uptime, 3, 1)

        info_layout.addWidget(QLabel("已采样:"), 3, 2)
        self.dev_samples = QLabel("--")
        self.dev_samples.setObjectName("statusValue")
        info_layout.addWidget(self.dev_samples, 3, 3)

        # 原有 ai-sdr Mini 字段（on_tool_result 仍会写入，保留接口）
        info_layout.addWidget(QLabel("固件:"), 4, 0)
        self.fw_version = QLabel("--")
        self.fw_version.setObjectName("statusValue")
        info_layout.addWidget(self.fw_version, 4, 1)

        info_layout.addWidget(QLabel("SDR:"), 4, 2)
        self.sdr_chip = QLabel("--")
        self.sdr_chip.setObjectName("statusValue")
        info_layout.addWidget(self.sdr_chip, 4, 3)

        info_layout.addWidget(QLabel("MCU:"), 5, 0)
        self.mcu_chip = QLabel("--")
        self.mcu_chip.setObjectName("statusValue")
        info_layout.addWidget(self.mcu_chip, 5, 1)

        info_layout.addWidget(QLabel("运行:"), 5, 2)
        self.uptime = QLabel("--")
        self.uptime.setObjectName("statusValue")
        info_layout.addWidget(self.uptime, 5, 3)

        layout.addWidget(info_group)

        layout.addStretch()

    # ========================================================================
    # UTC 时钟
    # ========================================================================

    def _start_utc_clock(self):
        """每秒刷新一次 UTC 时间（系统真实时间，非模拟）。"""
        self._utc_timer = QTimer(self)
        self._utc_timer.setInterval(1000)
        self._utc_timer.timeout.connect(self._update_utc)
        self._utc_timer.start()
        self._update_utc()

    def _update_utc(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        self.utc_label.setText(f"UTC: {now.strftime('%H:%M:%S')}")

    # ========================================================================
    # 统一后端驱动入口（新增）
    # ========================================================================

    def update_from_backend(self, backend):
        """从 SDRBackend 读取全部可用字段并刷新本面板。

        backend 为 None 或任意属性缺失时，对应字段显 "--"，绝不抛异常。
        主窗口应在 IQ 轮询周期 / 状态变化时调用本方法。
        """
        # ---- SDR 接收参数 ----
        status = None
        try:
            status = backend.get_status() if backend is not None else None
        except Exception:
            status = None

        if status is None:
            self.rx_freq.setText("--")
            self.rx_srate.setText("--")
            self.rx_bw.setText("--")
            self.rx_demod.setText("--")
            self.rx_gain.setText("--")
            self.rx_agc.setText("--")
            self.rx_squelch.setText("--")
            self.rx_volume.setText("--")
        else:
            self.rx_freq.setText(_fmt_freq(self._safe_get(status, "frequency_hz")))
            self.rx_srate.setText(_fmt_rate(self._safe_get(status, "sample_rate_hz")))
            self.rx_bw.setText(_fmt_bw(self._safe_get(status, "bandwidth_hz")))
            self.rx_demod.setText(self._safe_get(status, "demod_mode") or "--")
            gain = self._safe_get(status, "gain_db")
            self.rx_gain.setText(f"{gain:.1f} dB" if gain is not None else "--")
            agc = self._safe_get(status, "agc_enabled")
            self.rx_agc.setText("开" if agc else ("关" if agc is False else "--"))
            squelch = self._safe_get(status, "squelch_db")
            self.rx_squelch.setText(f"{squelch:.0f} dBFS" if squelch is not None else "--")
            vol = self._safe_get(status, "volume")
            # volume: 后端 0.0~1.0；按 0-63 量程显示（GQRX 风格）
            if vol is None:
                self.rx_volume.setText("--")
            else:
                try:
                    v63 = max(0, min(63, int(round(float(vol) * 63))))
                    self.rx_volume.setText(str(v63))
                except (TypeError, ValueError):
                    self.rx_volume.setText("--")

            # SNR（从 status 读取，保持信号质量组同步）
            snr = self._safe_get(status, "snr_db")
            if snr is not None:
                self.snr_value.setText(f"{snr:.1f} dB")

            # 频率/模式同步到信号质量组（避免主窗口重复槽函数）
            freq = self._safe_get(status, "frequency_hz")
            if freq is not None:
                self.freq_value.setText(_fmt_freq(freq))
            demod = self._safe_get(status, "demod_mode")
            if demod:
                self.mode_value.setText(str(demod))

        # PPM / Offset（后端可选属性）
        ppm = self._safe_get(backend, "ppm") if backend is not None else None
        if ppm is None:
            self.rx_ppm.setText("--")
        else:
            try:
                self.rx_ppm.setText(f"{float(ppm):+.0f} ppm")
            except (TypeError, ValueError):
                self.rx_ppm.setText("--")

        offset = self._safe_get(backend, "offset_tuning") if backend is not None else None
        if offset is None:
            self.rx_offset.setText("--")
        else:
            self.rx_offset.setText("开" if bool(offset) else "关")

        # ---- SDR 设备信息 ----
        if backend is None:
            self.dev_name.setText("未连接")
            self.dev_type.setText("--")
            self.dev_tuner.setText("--")
            self.dev_serial.setText("--")
            self.dev_uptime.setText("--")
            self.dev_samples.setText("--")
            return

        device = self._safe_get(backend, "device")
        name = self._safe_get(device, "name")
        self.dev_name.setText(name if name else "未连接")
        self.dev_type.setText(self._safe_get(device, "device_type") or "--")
        self.dev_tuner.setText(self._safe_get(backend, "tuner_name") or "--")
        self.dev_serial.setText(self._safe_get(device, "device_id") or "--")

        # 连接时长：优先用 status.uptime_seconds（基类 get_status 会更新）
        uptime_s = None
        if status is not None:
            uptime_s = self._safe_get(status, "uptime_seconds")
        if uptime_s is None or uptime_s <= 0:
            start = self._safe_get(backend, "_start_time")
            if start:
                try:
                    uptime_s = max(0.0, datetime.datetime.now().timestamp() - float(start))
                except Exception:
                    uptime_s = None
        if uptime_s is None or uptime_s <= 0:
            self.dev_uptime.setText("--")
        else:
            h, rem = divmod(int(uptime_s), 3600)
            m, s = divmod(rem, 60)
            self.dev_uptime.setText(f"{h:02d}:{m:02d}:{s:02d}")

        # 已采样数：优先 status.samples_read，回退 backend._samples_read
        samples = None
        if status is not None:
            samples = self._safe_get(status, "samples_read")
        if samples is None:
            samples = self._safe_get(backend, "_samples_read")
        if samples is None:
            self.dev_samples.setText("--")
        else:
            try:
                s = float(samples)
                if s >= 1e6:
                    self.dev_samples.setText(f"{s / 1e6:.1f} M")
                elif s >= 1e3:
                    self.dev_samples.setText(f"{s / 1e3:.1f} k")
                else:
                    self.dev_samples.setText(f"{int(s)}")
            except (TypeError, ValueError):
                self.dev_samples.setText("--")

    def update_signal_level(self, dbfs: float):
        """由主窗口用 IQ 功率估计的 dBFS 驱动 RSSI / S-meter / 电平条。

        dbfs=None 时全部 "--"，电平条置零。
        """
        if dbfs is None:
            self.rssi_value.setText("--")
            self.smeter_value.setText("--")
            self.level_bar.setValue(0)
            self._set_level_bar_color("#9AA5AC")
            return
        try:
            dbfs = float(dbfs)
        except (TypeError, ValueError):
            self.rssi_value.setText("--")
            self.smeter_value.setText("--")
            self.level_bar.setValue(0)
            self._set_level_bar_color("#9AA5AC")
            return

        self.rssi_value.setText(f"{dbfs:.1f} dBFS")
        self.smeter_value.setText(_dbfs_to_smeter(dbfs))

        # 电平条映射：[-120, 0] -> [0, 120]
        clamped = max(_LEVEL_MIN, min(_LEVEL_MAX, dbfs))
        pct = int((clamped - _LEVEL_MIN) / (_LEVEL_MAX - _LEVEL_MIN) * 100)
        self.level_bar.setValue(pct)

        # 颜色：弱=灰橙，中=橙，强=绿
        if dbfs > -40:
            self._set_level_bar_color("#6BA89A")      # 强-绿
        elif dbfs > -70:
            self._set_level_bar_color("#C4845C")      # 中-橙
        else:
            self._set_level_bar_color("#9AA5AC")      # 弱-灰

        # RSSI 文字颜色同步
        if dbfs > -40:
            self.rssi_value.setStyleSheet("color: #6BA89A;")
        elif dbfs > -70:
            self.rssi_value.setStyleSheet("color: #C4845C;")
        else:
            self.rssi_value.setStyleSheet("color: #B85C5C;")

    def set_sdr_connected(self, connected: bool):
        """连接状态切换：连接时正常显示；断开时把 SDR 相关字段全部 "--"。"""
        self._sdr_connected = bool(connected)
        if connected:
            return
        # 断开：清空 SDR 接收参数 / 设备 / 信号电平
        self.rx_freq.setText("--")
        self.rx_srate.setText("--")
        self.rx_bw.setText("--")
        self.rx_demod.setText("--")
        self.rx_gain.setText("--")
        self.rx_agc.setText("--")
        self.rx_squelch.setText("--")
        self.rx_volume.setText("--")
        self.rx_ppm.setText("--")
        self.rx_offset.setText("--")

        self.dev_name.setText("未连接")
        self.dev_type.setText("--")
        self.dev_tuner.setText("--")
        self.dev_serial.setText("--")
        self.dev_uptime.setText("--")
        self.dev_samples.setText("--")

        self.update_signal_level(None)
        self.snr_value.setText("--")
        self.mode_value.setText("--")
        self.freq_value.setText("--")

    # ========================================================================
    # 内部工具
    # ========================================================================

    @staticmethod
    def _safe_get(obj, attr):
        """安全取属性；obj 为 None 或属性缺失/异常时返回 None。"""
        if obj is None:
            return None
        try:
            return getattr(obj, attr, None)
        except Exception:
            return None

    def _set_level_bar_color(self, hex_color: str):
        """用 stylesheet 设置 QProgressBar 高亮色（保留默认底色）。"""
        self.level_bar.setStyleSheet(
            f"QProgressBar {{ border: 1px solid #CBD5D9; border-radius: 5px;"
            f" background: #ECEAE4; }} "
            f"QProgressBar::chunk {{ background: {hex_color}; border-radius: 4px; }}"
        )

    # ========================================================================
    # 槽函数：从 MCP Worker 接收数据（保持原有接口不变）
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
            self.rssi_value.setStyleSheet("color: #C4845C;")  # 中等-橙
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
        source=="real"  → 绿色(#6BA89A)显示真实坐标/卫星数/HDOP/高度；
        source=="none"  → 灰色“未连接/无数据”，坐标一律空，绝不造假。
        （UI 与真硬件联调待插模块后再细化。）
        """
        source = fix_dict.get("source", "none")
        ts = fix_dict.get("timestamp")
        self._update_timestamp(self.gps_timestamp, ts)

        if source != "real":
            # 未连接 / 无定位：灰色，坐标一律空，绝不保留旧值
            self.gps_fix.setText("未连接")
            self.gps_fix.setStyleSheet("color: #999999;")
            self.gps_sats.setText("--")
            self.gps_lat.setText("--")
            self.gps_lon.setText("--")
            self.gps_alt.setText("--")
            self.gps_hdop.setText("--")
            self.gps_signals.setText("--")
            self.gps_group.setTitle("GPS / 北斗")
            return

        # 已定位：绿色 #6BA89A
        self.gps_fix.setText("已定位")
        self.gps_fix.setStyleSheet("color: #6BA89A;")
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
        # 可选：最强 5 颗卫星 SNR（无 satellite_signals 字段时显示 '--'，不报错）
        self._update_signal_strength(fix_dict.get("satellite_signals"))

    def _update_signal_strength(self, signals):
        """可选：显示最强 5 颗卫星的 SNR。

        signals: [{"id": int, "snr_db": float}, ...]；缺省/为空/异常时显示 '--'，
        绝不抛错。当前串口 GNSS 暂未上报该字段，故平时保持 '--'（前向兼容）。
        """
        if not signals:
            self.gps_signals.setText("--")
            return
        try:
            ranked = sorted(
                [s for s in signals if s.get("snr_db") is not None],
                key=lambda s: float(s.get("snr_db", 0)),
                reverse=True,
            )[:5]
        except Exception:
            self.gps_signals.setText("--")
            return
        if not ranked:
            self.gps_signals.setText("--")
            return
        parts = [f"#{s.get('id', '?')} {float(s['snr_db']):.0f}dB" for s in ranked]
        self.gps_signals.setText("  ".join(parts))

    @Slot(dict)
    def on_imu_updated(self, imu: dict):
        """IMU 数据更新。

        source=="none" 或 acc 为 None（无九轴硬件/未连接）时：
        所有数值 label 一律 "--"，标题打 [未连接]，绝不显示任何姿态数值。
        """
        source = imu.get("source", "real")
        acc = imu.get("acc")

        if source == "none" or acc is None:
            # 未连接：全部 "--"，时间戳也清空（不伪造更新时间）
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
            self.imu_timestamp.setText("--")
            self.imu_group.setTitle("IMU 9 轴姿态 [未连接]")
            return

        # 真实模式：正常标题
        self.imu_group.setTitle("IMU 9 轴姿态")
        # 数据时间戳
        self._update_timestamp(self.imu_timestamp, imu.get("timestamp"))

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
