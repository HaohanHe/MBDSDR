"""
MBDSDR 桌面端 - 多普勒定轨面板 (DopplerPanel)
==============================================

只接受真实观测：
  - 实时 SDR：外部通过 feed_iq(iq, fs) 喂入真实复基带 IQ，面板做窄带 FFT
    提取载波频偏（多普勒频移），带时间戳累积成观测序列。
  - 离线 IQ 文件：用户选择真实录制的 .cf32/.raw/.iq/.wav，滑窗 FFT 提取观测。

无 SDR 连接且无 IQ 文件 -> 显示「未连接 / 无观测数据」，不画多普勒曲线、
不运行 EKF/RLS、不输出轨道根数。面板内不存在任何 sin/random 合成观测。

定轨估计器（EKF / RLS / 最小二乘）仅在有 >=2 个真实观测时才执行 predict/update；
结果收敛后显示轨道根数（a/e/i/RAAN/argp/M）、位置速度与残差。

配色全部取自 themes.py（低饱和默认主题），不硬编码新颜色。
"""
from __future__ import annotations

import os
import sys
import time
import traceback
from collections import deque
from typing import Deque, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from PySide6.QtCore import Qt, QThread, Signal, QObject, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QComboBox, QPushButton,
    QLabel, QLineEdit, QRadioButton, QButtonGroup, QFrame, QGroupBox,
    QFileDialog, QMessageBox, QPlainTextEdit,
)

import matplotlib
matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402

from themes import get_theme, DEFAULT_THEME


def _theme_colors() -> Dict[str, str]:
    return get_theme(DEFAULT_THEME).colors


# 先验轨道（仅用于 EKF 初值，不是观测数据）：Iridium 风格 LEO。
# TLE 会过期，仅作 EKF/RLS 的初始猜测；真实轨道由多普勒观测修正。
_PRIOR_IRIDIUM_TLE = (
    "1 99999U 24001A   24278.00000000  .00000000  00000-0  00000-0 0  9999",
    "2 99999  86.4000 160.0000 0001000 320.0000 320.0000 14.34000000    00",
)

MIN_OBS = 2                     # EKF/RLS 至少需要的真实观测点数
RT_WINDOW_S = 0.5               # 实时 FFT 窗长
RT_FLUSH_MS = 1000              # 实时观测刷新周期


# ======================================================================
# 后台定轨线程：只跑真实观测
# ======================================================================
class OrbitWorker(QObject):
    finished = Signal(dict)
    progress = Signal(str)
    failed = Signal(str)

    def __init__(self, action: str, params: Dict, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._action = action
        self._params = params

    def run(self):
        try:
            if self._action == "determine":
                self._run_determine()
            elif self._action == "lro":
                self._run_lro()
        except Exception as e:  # noqa: BLE001
            self.failed.emit(f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=2)}")

    def _run_determine(self):
        """用真实多普勒观测序列运行 EKF/RLS/LS。观测已在面板侧校验非空。"""
        from mbdsdr_ai import orbit_determination as od

        obs: List[Tuple[float, float]] = self._params["observations"]
        rx_lat = float(self._params["rx_lat"])
        rx_lon = float(self._params["rx_lon"])
        rx_alt = float(self._params["rx_alt"])
        f0 = float(self._params["f0"])
        estimator = self._params["estimator"]
        init_state = np.asarray(self._params["init_state"], dtype=float)

        self.progress.emit(f"运行 {estimator.upper()} 定轨，N={len(obs)} ...")
        res = od.doppler_orbit_determine(
            obs, rx_lat, rx_lon, rx_alt, init_state, f0=f0, estimator=estimator)

        times = np.asarray(res["times"], dtype=float)
        pos = np.asarray(res["positions_ecef"], dtype=float)
        vels = np.asarray(res["velocities_ecef"], dtype=float)
        residuals = np.asarray(res["residuals"], dtype=float)
        stats = res["residual_stats"]

        # 由末状态算轨道根数（展示用）
        final_state = res.get("state", res.get("state_ref"))
        kepler = od.ecef_state_to_keplerian(final_state, float(times[-1]))

        # 天空轨迹（由估计位置反算方位/仰角）
        rx_pos = od.geodetic_to_ecef(rx_lat, rx_lon, rx_alt)
        azs, els = [], []
        for k in range(len(pos)):
            az, el = od.ecef_to_azel(pos[k], rx_pos, rx_lat, rx_lon)
            azs.append(az)
            els.append(el)

        # 收敛判据：残差 RMS 折算回频偏 < 20 Hz
        fd_rms = abs(stats["rms"]) * f0 / od.C_LIGHT_KMS
        self.finished.emit({
            "simulated": False,
            "estimator": res["estimator"],
            "times": (times - times[0]).tolist(),
            "observations_fd": [float(fd) for _, fd in obs],
            "observations_t": [float(t - obs[0][0]) for t, _ in obs],
            "residuals": residuals.tolist(),
            "azimuth_deg": azs,
            "elevation_deg": els,
            "residual_rms": stats["rms"],
            "fd_rms_hz": fd_rms,
            "converged": bool(fd_rms < 20.0),
            "kepler": kepler,
            "r_ecef_km": final_state[0:3].tolist(),
            "v_ecef_kmps": final_state[3:6].tolist(),
            "n_obs": len(obs),
        })

    def _run_lro(self):
        """LRO 天空预测（星历参考，非多普勒定轨）。"""
        from mbdsdr_ai import orbit_determination as od
        rx_lat = float(self._params["rx_lat"])
        rx_lon = float(self._params["rx_lon"])
        self.progress.emit("计算 LRO 参考天空轨迹 ...")
        t0 = time.time()
        times = t0 + np.linspace(0, 24 * 3600.0, 288)
        ref = od.get_lro_reference(times)
        rx_pos = od.geodetic_to_ecef(rx_lat, rx_lon, 0.0)
        azs, els = [], []
        for i in range(len(times)):
            az, el = od.ecef_to_azel(ref["r_ecef"][i], rx_pos, rx_lat, rx_lon)
            azs.append(az)
            els.append(el)
        self.finished.emit({
            "simulated": False,
            "estimator": "lro_predict",
            "times": (times - times[0]).tolist(),
            "observations_fd": [], "observations_t": [],
            "residuals": [], "azimuth_deg": azs, "elevation_deg": els,
            "residual_rms": None, "fd_rms_hz": None, "converged": None,
            "kepler": None, "r_ecef_km": None, "v_ecef_kmps": None, "n_obs": 0,
            "content": "LRO 天空预测（参考星历，非多普勒定轨）",
        })


# ======================================================================
# matplotlib 画布
# ======================================================================
class OrbitCanvas(FigureCanvasQTAgg):
    def __init__(self, parent=None):
        c = _theme_colors()
        self._c = c
        fig = Figure(figsize=(7, 5))
        fig.patch.set_facecolor(c["card"])
        self.ax_obs = fig.add_subplot(2, 2, 1)
        self.ax_hist = fig.add_subplot(2, 2, 2)
        self.ax_sky = fig.add_subplot(2, 2, 3, projection="polar")
        self.ax_sky.set_theta_zero_location("N")
        self.ax_sky.set_theta_direction(-1)
        self.ax_status = fig.add_subplot(2, 2, 4)
        self.ax_status.axis("off")
        super().__init__(fig)
        self._style_axes()
        self.show_disconnected("未连接 / 无观测数据")

    def _style_axes(self):
        c = self._c
        for ax in (self.ax_obs, self.ax_hist, self.ax_sky):
            ax.set_facecolor(c["bg_alt"])
            ax.tick_params(colors=c["text_secondary"], labelsize=8)
            for spine in getattr(ax, "spines", {}).values():
                spine.set_color(c["border"])
            ax.title.set_color(c["text"])

    def show_disconnected(self, msg: str = "未连接 / 无观测数据"):
        for ax in (self.ax_obs, self.ax_hist, self.ax_sky):
            ax.clear()
            ax.set_facecolor(self._c["bg_alt"])
        self.ax_obs.text(0.5, 0.5, msg, ha="center", va="center",
                         transform=self.ax_obs.transAxes,
                         color=self._c["text_disabled"], fontsize=11)
        self.ax_obs.set_title("多普勒频偏观测 (Hz)", fontsize=9)
        self.ax_status.clear()
        self.ax_status.axis("off")
        self.ax_status.text(0.05, 0.95, "等待真实观测 ...",
                            va="top", ha="left",
                            color=self._c["text_disabled"], fontsize=9,
                            family="monospace")
        self.draw_idle()

    def plot_results(self, d: Dict):
        c = self._c
        prim = c["primary"]; acc = c["accent"]; txt = c["text"]

        # 1) 真实多普勒频偏观测
        self.ax_obs.clear()
        self.ax_obs.set_facecolor(c["bg_alt"])
        ot = d.get("observations_t", [])
        ofd = d.get("observations_fd", [])
        if ot and ofd:
            self.ax_obs.plot(ot, ofd, color=prim, lw=1.6, marker="o",
                             markersize=2, label="实测 fd")
            self.ax_obs.axhline(0, color=c["border"], lw=0.6)
            self.ax_obs.set_ylabel("频偏 (Hz)", color=txt, fontsize=8)
            self.ax_obs.set_xlabel("时间 (s)", color=txt, fontsize=8)
            self.ax_obs.set_title("真实多普勒频偏观测", fontsize=9)
            self.ax_obs.legend(fontsize=7)
            self.ax_obs.grid(True, alpha=0.3)
        else:
            self.ax_obs.text(0.5, 0.5, "无观测", ha="center", va="center",
                             transform=self.ax_obs.transAxes,
                             color=c["text_disabled"], fontsize=9)
            self.ax_obs.set_title("真实多普勒频偏观测", fontsize=9)

        # 2) 残差直方图
        self.ax_hist.clear()
        self.ax_hist.set_facecolor(c["bg_alt"])
        res = d.get("residuals", [])
        if len(res) >= 2:
            self.ax_hist.hist(res, bins=15, color=prim, edgecolor=c["border"], alpha=0.85)
            self.ax_hist.set_title("滤波残差", fontsize=9)
            self.ax_hist.grid(True, alpha=0.3)
        else:
            self.ax_hist.text(0.5, 0.5, "残差不足", ha="center", va="center",
                              transform=self.ax_hist.transAxes,
                              color=c["text_disabled"], fontsize=9)
            self.ax_hist.set_title("滤波残差", fontsize=9)

        # 3) 天空图
        self.ax_sky.clear()
        self.ax_sky.set_facecolor(c["bg_alt"])
        self.ax_sky.set_theta_zero_location("N")
        self.ax_sky.set_theta_direction(-1)
        az = d.get("azimuth_deg", [])
        el = d.get("elevation_deg", [])
        if az and el:
            az_rad = np.deg2rad(np.asarray(az))
            r = 90.0 - np.asarray(el)
            self.ax_sky.plot(az_rad, r, color=acc, lw=1.4, marker="o", markersize=2)
            self.ax_sky.set_yticks([0, 30, 60, 90])
            self.ax_sky.set_yticklabels(["90°", "60°", "30°", "0°"],
                                        color=c["text_secondary"], fontsize=7)
        else:
            self.ax_sky.text(0.5, 0.5, "无轨迹", ha="center", va="center",
                            transform=self.ax_sky.transAxes,
                            color=c["text_disabled"], fontsize=8)
        self.ax_sky.set_title("天空图 (AZ/EL)", fontsize=9, va="bottom")

        # 4) 状态 / 轨道根数
        self.ax_status.clear()
        self.ax_status.axis("off")
        self.ax_status.set_facecolor(c["card"])
        lines = [f"估计器: {d.get('estimator','?')}  N={d.get('n_obs',0)}"]
        k = d.get("kepler")
        if k:
            lines += [
                f"a   = {k['a_km']:.1f} km",
                f"e   = {k['e']:.4f}",
                f"i   = {k['i_deg']:.2f} deg",
                f"RAAN= {k['raan_deg']:.2f} deg",
                f"argp= {k['argp_deg']:.2f} deg",
                f"M   = {k['M_deg']:.2f} deg",
            ]
            if d.get("r_ecef_km"):
                rx, ry, rz = d["r_ecef_km"]
                lines.append(f"r=({rx:.0f},{ry:.0f},{rz:.0f}) km")
        else:
            lines.append("轨道根数: --")
        rms = d.get("fd_rms_hz")
        if rms is not None:
            lines.append(f"残差RMS= {rms:.2f} Hz")
        conv = d.get("converged")
        lines.append("收敛: " + ("已收敛" if conv is True else
                                ("迭代中/未收敛" if conv is False else "--")))
        self.ax_status.text(0.05, 0.95, "\n".join(lines),
                            va="top", ha="left", color=txt, fontsize=8,
                            family="monospace")
        self.figure.tight_layout()
        self.draw_idle()


# ======================================================================
# 面板主体
# ======================================================================
class DopplerPanel(QWidget):
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._c = _theme_colors()
        self._sdr_connected = False
        self._worker: Optional[QThread] = None
        self._worker_obj: Optional[OrbitWorker] = None

        # 真实观测状态
        self._observations: List[Tuple[float, float]] = []   # (t_unix, fd_hz)
        self._iq_path: Optional[str] = None
        self._rt_buffer: Deque[np.ndarray] = deque()
        self._rt_fs: float = 0.0
        self._rt_timer = QTimer(self)
        self._rt_timer.setInterval(RT_FLUSH_MS)
        self._rt_timer.timeout.connect(self._rt_flush)

        self._build_ui()
        self._refresh_gate()

    # ------------------------------------------------------------------
    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)

        ctrl = QFrame()
        ctrl.setObjectName("card")
        cl = QVBoxLayout(ctrl)
        cl.setContentsMargins(10, 8, 10, 10)
        cl.setSpacing(6)

        title = QLabel("多普勒定轨")
        title.setObjectName("sectionTitle")
        cl.addWidget(title)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.target_combo = QComboBox()
        self.target_combo.setToolTip("先验轨道目标（TLE 仅作初值，真实轨道由观测修正）")
        self.target_combo.addItems(["LRO (月球轨道)", "Iridium-107", "自定义 TLE"])
        self.target_combo.currentIndexChanged.connect(self._refresh_gate)
        form.addRow("目标", self.target_combo)

        self.mode_combo = QComboBox()
        self.mode_combo.setToolTip("观测来源：实时 SDR 流 或 离线录制 IQ 文件")
        self.mode_combo.addItems(["离线 IQ 文件", "实时 SDR"])
        self.mode_combo.currentIndexChanged.connect(self._refresh_gate)
        form.addRow("观测来源", self.mode_combo)

        # 中心频率 / 采样率
        freq_row = QHBoxLayout()
        self.f0_edit = QLineEdit(str(2271.0e6))
        self.f0_edit.setToolTip("下行中心频率 (Hz)")
        self.fs_edit = QLineEdit(str(2.4e6))
        self.fs_edit.setToolTip("采样率 (Hz)；裸 IQ 文件需与此一致")
        freq_row.addWidget(QLabel("中心Hz")); freq_row.addWidget(self.f0_edit)
        freq_row.addWidget(QLabel("采样Hz")); freq_row.addWidget(self.fs_edit)
        form.addRow("射频", freq_row)

        st = QHBoxLayout()
        self.lat_edit = QLineEdit("")
        self.lat_edit.setPlaceholderText("未设置")
        self.lon_edit = QLineEdit("")
        self.lon_edit.setPlaceholderText("未设置")
        self.alt_edit = QLineEdit("0.0")
        st.addWidget(QLabel("纬度")); st.addWidget(self.lat_edit)
        st.addWidget(QLabel("经度")); st.addWidget(self.lon_edit)
        st.addWidget(QLabel("km")); st.addWidget(self.alt_edit)
        form.addRow("地面站", st)

        est = QHBoxLayout()
        self.ekf_radio = QRadioButton("EKF")
        self.ekf_radio.setChecked(True)
        self.rls_radio = QRadioButton("RLS")
        self.ls_radio = QRadioButton("最小二乘")
        self.est_group = QButtonGroup(self)
        for b in (self.ekf_radio, self.rls_radio, self.ls_radio):
            self.est_group.addButton(b)
            est.addWidget(b)
        est.addStretch()
        form.addRow("估计器", est)

        cl.addLayout(form)

        # IQ 文件选择
        file_row = QHBoxLayout()
        self.pick_btn = QPushButton("选择 IQ 文件 ...")
        self.pick_btn.setToolTip("选择真实录制的 .cf32/.raw/.iq/.wav")
        self.pick_btn.clicked.connect(self._on_pick_iq)
        self.iq_label = QLabel("未加载")
        self.iq_label.setStyleSheet(f"color: {self._c['text_disabled']};")
        file_row.addWidget(self.pick_btn)
        file_row.addWidget(self.iq_label, 1)
        cl.addLayout(file_row)

        # 自定义 TLE
        self.tle_edit = QPlainTextEdit()
        self.tle_edit.setPlaceholderText("自定义 TLE 两行（选填）：\n1 xxxxU ...\n2 xxxx ...")
        self.tle_edit.setMaximumHeight(56)
        self.tle_edit.setToolTip("仅「自定义 TLE」时使用，作为 EKF 先验初值")
        cl.addWidget(self.tle_edit)

        btn_row = QHBoxLayout()
        self.start_btn = QPushButton("开始定轨")
        self.start_btn.setObjectName("recordButton")
        self.start_btn.clicked.connect(self._on_start)
        btn_row.addWidget(self.start_btn)
        self.stop_btn = QPushButton("停止")
        self.stop_btn.clicked.connect(self._on_stop)
        self.stop_btn.setEnabled(False)
        btn_row.addWidget(self.stop_btn)
        cl.addLayout(btn_row)

        outer.addWidget(ctrl)

        plot_card = QFrame()
        plot_card.setObjectName("card")
        pl = QVBoxLayout(plot_card)
        pl.setContentsMargins(10, 8, 10, 10)
        self.plot_title = QLabel("定轨结果")
        self.plot_title.setObjectName("sectionTitle")
        pl.addWidget(self.plot_title)
        self.canvas = OrbitCanvas()
        pl.addWidget(self.canvas, 1)
        outer.addWidget(plot_card, 1)

        self.status_label = QLabel("未连接 / 无观测数据")
        self.status_label.setObjectName("statusValue")
        outer.addWidget(self.status_label)

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------
    def set_sdr_connected(self, connected: bool):
        self._sdr_connected = bool(connected)
        if not self._sdr_connected:
            self._rt_timer.stop()
            self._rt_buffer.clear()
        self._refresh_gate()

    def feed_iq(self, iq: np.ndarray, fs: float):
        """实时 SDR 复基带喂入（由 main_window 在 IQ 轮询时调用）。

        累积到缓冲，由定时器定期做窄带 FFT 提取多普勒频偏观测。无连接时丢弃。
        """
        if not self._sdr_connected:
            return
        iq = np.asarray(iq, dtype=np.complex128)
        if iq.size == 0:
            return
        self._rt_fs = float(fs)
        self._rt_buffer.append(iq)
        # 缓冲最多保留 ~2 秒
        max_samples = max(4096, int(self._rt_fs * 2.0))
        total = sum(len(x) for x in self._rt_buffer)
        while total > max_samples and len(self._rt_buffer) > 1:
            total -= len(self._rt_buffer.popleft())
        if not self._rt_timer.isActive():
            self._rt_timer.start()
        self._refresh_gate()

    def set_iq_file(self, path: str):
        """加载离线 IQ 文件并提取观测序列。"""
        from mbdsdr_ai import orbit_determination as od
        try:
            fs = float(self.fs_edit.text())
        except ValueError:
            fs = None
        try:
            iq, fs_actual = od.load_iq_file(path, sample_rate=fs)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "IQ 文件", f"加载失败: {e}")
            return
        self._iq_path = path
        self._iq_loaded_fs = fs_actual
        try:
            f0 = float(self.f0_edit.text())
        except ValueError:
            f0 = od.F0_DEFAULT_HZ
        obs = od.extract_doppler_observations(
            iq, fs_actual, f0=f0, t0=time.time())
        self._observations = obs
        self.iq_label.setText(os.path.basename(path))
        if obs:
            self.status_label.setText(
                f"IQ 已加载：{os.path.basename(path)}，检出 {len(obs)} 个观测")
        else:
            self.status_label.setText("IQ 文件中未检出有效载波（SNR 不足）")
        self._refresh_gate()

    def start_processing(self):
        self._on_start()

    def stop_processing(self):
        self._on_stop()

    # ------------------------------------------------------------------
    def _is_realtime(self) -> bool:
        return self.mode_combo.currentText() == "实时 SDR"

    def _estimator(self) -> str:
        if self.rls_radio.isChecked():
            return "rls"
        if self.ls_radio.isChecked():
            return "ls"
        return "ekf"

    def _ground_station(self) -> Optional[Tuple[float, float, float]]:
        try:
            lat = float(self.lat_edit.text())
            lon = float(self.lon_edit.text())
            alt = float(self.alt_edit.text())
        except ValueError:
            return None
        return lat, lon, alt

    def _refresh_gate(self):
        """根据 连接/文件/观测 状态决定控件可用性与提示文案。"""
        busy = self._is_busy()
        realtime = self._is_realtime()
        target = self.target_combo.currentText()

        # LRO 是天空预测，不需要多普勒观测；其余目标需要真实观测
        need_obs = not target.startswith("LRO")

        if realtime and not self._sdr_connected:
            self.start_btn.setEnabled(False)
            self.status_label.setText("未连接SDR设备 — 实时观测不可用")
            self.canvas.show_disconnected("未连接SDR设备 / 无观测数据")
            return

        if not need_obs:
            # LRO 预测：随时可跑
            self.start_btn.setEnabled(not busy)
            if self.status_label.text().startswith("就绪"):
                self.status_label.setText("就绪 — LRO 天空预测")
            return

        # 需要真实观测的目标
        n = len(self._observations)
        if realtime:
            if n >= MIN_OBS:
                self.start_btn.setEnabled(not busy)
                self.status_label.setText(f"实时观测已积累 {n} 点")
            else:
                self.start_btn.setEnabled(False)
                self.status_label.setText(f"实时观测不足（{n}/{MIN_OBS}）— 等待载波")
                self.canvas.show_disconnected("实时采集中 / 观测不足")
        else:
            if self._iq_path is None:
                self.start_btn.setEnabled(False)
                self.status_label.setText("未加载 IQ 文件 — 无观测数据")
                self.canvas.show_disconnected("未加载 IQ 文件 / 无观测数据")
            elif n >= MIN_OBS:
                self.start_btn.setEnabled(not busy)
                self.status_label.setText(f"IQ 观测就绪 N={n}")
            else:
                self.start_btn.setEnabled(False)
                self.status_label.setText("IQ 文件未检出有效载波 — 观测不足")
                self.canvas.show_disconnected("无有效观测 / SNR 不足")

    def _rt_flush(self):
        """实时 FFT：对缓冲做一次窄带 FFT，提取多普勒频偏。"""
        from mbdsdr_ai import orbit_determination as od
        if not self._rt_buffer or self._rt_fs <= 0:
            return
        win = int(self._rt_fs * RT_WINDOW_S)
        if win < 64:
            return
        # 拼出最近 win 个样本
        chunks = list(self._rt_buffer)
        data = np.concatenate(chunks)
        if len(data) < win:
            return
        seg = data[-win:]
        try:
            f0 = float(self.f0_edit.text())
        except ValueError:
            f0 = od.F0_DEFAULT_HZ
        fd, snr = od.detect_doppler(seg, f0=f0, fs=self._rt_fs)
        if snr >= 8.0:
            self._observations.append((time.time(), float(fd)))
        self._refresh_gate()

    # ------------------------------------------------------------------
    def _on_pick_iq(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择真实录制的 IQ 文件", "",
            "IQ Files (*.cf32 *.raw *.iq *.wav);;All Files (*)")
        if path:
            self.set_iq_file(path)

    def _initial_state(self, t_unix: float):
        """由先验 TLE 计算 ECEF 初值 (7 维)。"""
        from mbdsdr_ai import orbit_determination as od
        target = self.target_combo.currentText()
        if target.startswith("LRO"):
            return None
        if target.startswith("自定义"):
            lines = [l.strip() for l in self.tle_edit.toPlainText().splitlines()
                     if l.strip()]
            if len(lines) < 2:
                raise ValueError("自定义 TLE 需要两行数据")
            l1, l2 = lines[0], lines[1]
        else:
            l1, l2 = _PRIOR_IRIDIUM_TLE
        r, v = od.leosat_state_from_tle(l1, l2, t_unix)
        return np.concatenate([r, v, [0.0]])

    def _on_start(self):
        if self._is_busy():
            return
        gs = self._ground_station()
        if gs is None:
            QMessageBox.warning(self, "参数错误", "地面站坐标需为数字。")
            return
        target = self.target_combo.currentText()

        if target.startswith("LRO"):
            self._start_worker("lro", {"rx_lat": gs[0], "rx_lon": gs[1]})
            return

        # 需要真实观测
        if self._is_realtime() and not self._sdr_connected:
            QMessageBox.warning(self, "未连接 SDR", "未连接SDR设备，无法实时观测。")
            return
        if len(self._observations) < MIN_OBS:
            QMessageBox.information(self, "观测不足",
                                   "尚无足够真实多普勒观测，EKF/RLS 不执行。")
            return
        try:
            f0 = float(self.f0_edit.text())
        except ValueError:
            f0 = 2271.0e6
        try:
            init_state = self._initial_state(self._observations[0][0])
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "先验初值", f"无法从 TLE 生成初值: {e}")
            return
        self._start_worker("determine", {
            "rx_lat": gs[0], "rx_lon": gs[1], "rx_alt": gs[2],
            "estimator": self._estimator(),
            "observations": list(self._observations),
            "init_state": init_state, "f0": f0,
        })

    def _on_stop(self):
        self._rt_timer.stop()
        if self._worker is not None:
            self._worker.quit()
            self._worker.wait(1500)
        self._on_idle()

    def _is_busy(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def _start_worker(self, action: str, params: Dict):
        self._worker = QThread()
        self._worker_obj = OrbitWorker(action, params)
        self._worker_obj.moveToThread(self._worker)
        self._worker.started.connect(self._worker_obj.run)
        self._worker_obj.progress.connect(self._on_progress)
        self._worker_obj.failed.connect(self._on_failed)
        self._worker_obj.finished.connect(self._on_result)
        self._worker_obj.finished.connect(self._worker.quit)
        self._worker.finished.connect(self._on_idle)
        self._worker.start()
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.status_label.setText("定轨计算中 ...")

    def _on_idle(self):
        self.stop_btn.setEnabled(False)
        if self._worker is not None:
            self._worker = None
            self._worker_obj = None
        self._refresh_gate()

    def _on_progress(self, text: str):
        self.status_label.setText(text)

    def _on_failed(self, text: str):
        self.status_label.setText(f"错误: {text.splitlines()[0]}")
        QMessageBox.critical(self, "定轨错误", text)

    def _on_result(self, d: Dict):
        self.canvas.plot_results(d)
        self.plot_title.setText(f"定轨结果 — {self.target_combo.currentText()}")
        rms = d.get("fd_rms_hz")
        conv = d.get("converged")
        parts = [f"{d.get('estimator','?')} N={d.get('n_obs',0)}"]
        parts.append(f"残差RMS: {rms:.2f} Hz" if rms is not None else "残差RMS: --")
        parts.append("收敛: " + ("已收敛" if conv is True else
                                ("未收敛" if conv is False else "--")))
        self.status_label.setText(" | ".join(parts))


if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    get_theme(DEFAULT_THEME).apply(app)
    w = DopplerPanel()
    w.resize(900, 700)
    w.show()
    print("DopplerPanel launched; sdr_connected =", w._sdr_connected)
    sys.exit(app.exec())
