"""
MBDSDR 桌面端 - 多普勒定轨面板 (DopplerPanel)
==============================================

用多普勒频偏观测序列估计卫星轨道（EKF / 参考历元 RLS），并显示：
  - 三维位置误差 (km) 随时间收敛曲线
  - 残差直方图
  - 天空图（方位/仰角极坐标）

目标：LRO（月球轨道）/ Iridium-107 / 自定义 TLE。
地面站坐标需用户输入或从 ~/.mbdsdr/config.json 读取（ground_station_lat/lon）。
无真实 SDR 硬件时实时模式置灰并显示「未连接SDR设备」；
[模拟] 按钮用合成 Iridium 数据跑收敛，结果标 [模拟]。

配色全部取自 themes.py（日式低饱和），不硬编码新颜色。
"""
from __future__ import annotations

import os
import sys
import time
import traceback
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from PySide6.QtCore import Qt, QThread, Signal, QObject
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QComboBox, QPushButton,
    QLabel, QLineEdit, QRadioButton, QButtonGroup, QFrame, QGroupBox,
    QMessageBox,
)

import matplotlib
matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402

from themes import get_theme, DEFAULT_THEME


def _theme_colors() -> Dict[str, str]:
    return get_theme(DEFAULT_THEME).colors


# 合成 Iridium 风格 TLE（与 tests/test_orbit_determination.py 一致）
_SYN_IRIDIUM_TLE = (
    "1 99999U 24001A   24278.00000000  .00000000  00000-0  00000-0 0  9999",
    "2 99999  86.4000 160.0000 0001000 320.0000 320.0000 14.34000000    00",
)


# ======================================================================
# 后台定轨线程
# ======================================================================
class OrbitWorker(QObject):
    finished = Signal(object)   # dict(plot data) 或 ToolResult
    progress = Signal(str)
    failed = Signal(str)

    def __init__(self, action: str, params: Dict, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._action = action
        self._params = params
        self._reg = None

    def _ensure_registry(self):
        if self._reg is None:
            from mbdsdr_ai.tool_registry import ToolRegistry
            self._reg = ToolRegistry()
            self._reg.register_builtin_tools()
        return self._reg

    def run(self):
        try:
            if self._action == "determine":
                self._run_determine()
            elif self._action == "lro":
                self._run_lro()
            elif self._action == "demo":
                self._run_demo()
        except Exception as e:  # noqa: BLE001
            self.failed.emit(f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=2)}")

    # ------------------------------------------------------------------
    def _build_synthetic_iridium(self, rx_lat, rx_lon, rx_alt, rng_seed=7):
        """合成一次 Iridium 过境的多普勒观测（真值 + 钟漂 + 噪声）。"""
        from mbdsdr_ai import orbit_determination as od
        rng = np.random.default_rng(rng_seed)
        f0 = od.F0_DEFAULT_HZ
        rx_pos = od.geodetic_to_ecef(rx_lat, rx_lon, rx_alt)
        rx_vel = np.array([-od.OMEGA_E * rx_pos[1], od.OMEGA_E * rx_pos[0], 0.0])

        # 固定在合成 TLE 历元附近（2024-10-04），保证 SGP4 传播误差最小、EKF 可收敛
        import datetime as _dt
        epoch = _dt.datetime(2024, 10, 4, 0, 0, 0)
        epoch_u = (epoch - _dt.datetime(1970, 1, 1)).total_seconds()
        ts = epoch_u + np.arange(0, 4 * 3600, 5.0)
        l1, l2 = _SYN_IRIDIUM_TLE
        states = [od.leosat_state_from_tle(l1, l2, t) for t in ts]
        els = np.array([od.ecef_to_azel(states[k][0], rx_pos, rx_lat, rx_lon)[1]
                        for k in range(len(ts))])
        ic = int(np.argmax(els))
        above = els > 10.0
        s = ic
        while s > 0 and above[s - 1]:
            s -= 1
        e = ic
        while e < len(els) - 1 and above[e + 1]:
            e += 1

        truth, obs = [], []
        azs, els_obs = [], []
        clk_drift = 30.0
        for k in range(s, e + 1):
            r, v = states[k]
            t = ts[k]
            sv = r - rx_pos
            rho = np.linalg.norm(sv)
            u = sv / rho
            rho_dot = u @ (v - rx_vel)
            fd = od.rangerate_to_fd(rho_dot, f0) + clk_drift + rng.normal(0.0, 10.0)
            obs.append({"t": float(t), "fd": float(fd)})
            truth.append(r)
            az, el = od.ecef_to_azel(r, rx_pos, rx_lat, rx_lon)
            azs.append(az); els_obs.append(el)
        truth = np.array(truth)
        # 初值：真值 + ~10km 偏差
        x0 = np.concatenate([
            truth[0] + np.array([10.0, -5.0, 3.0]),
            states[s][1] + np.array([0.001, -0.002, 0.001]),
            [0.0],
        ])
        return obs, x0, truth, np.array(azs), np.array(els_obs), f0

    def _run_demo(self):
        from mbdsdr_ai import orbit_determination as od
        rx_lat = float(self._params["rx_lat"])
        rx_lon = float(self._params["rx_lon"])
        rx_alt = float(self._params["rx_alt"])
        estimator = self._params["estimator"]
        self.progress.emit("[模拟] 合成 Iridium 过境多普勒观测 ...")

        obs, x0, truth, azs, els_obs, f0 = self._build_synthetic_iridium(
            rx_lat, rx_lon, rx_alt)
        res = od.doppler_orbit_determine(
            [(o["t"], o["fd"]) for o in obs],
            rx_lat, rx_lon, rx_alt, x0, f0=f0, estimator=estimator)
        err, _ = od.position_error_curve(res["positions_ecef"], truth)
        stats = res["residual_stats"]
        self.finished.emit({
            "simulated": True,
            "estimator": res["estimator"],
            "times": (np.asarray(res["times"]) - res["times"][0]).tolist(),
            "pos_error_km": err.tolist(),
            "residuals": np.asarray(res["residuals"]).tolist(),
            "azimuth_deg": azs.tolist(),
            "elevation_deg": els_obs.tolist(),
            "final_error_km": float(err[-1]),
            "residual_rms": stats["rms"],
            "converged": bool(err[-1] < 1.0),
        })

    def _run_lro(self):
        reg = self._ensure_registry()
        rx_lat = float(self._params["rx_lat"])
        rx_lon = float(self._params["rx_lon"])
        self.progress.emit("调用 lro_track 计算 LRO 天空轨迹 ...")
        res = reg.call("lro_track", {
            "rx_lat": rx_lat, "rx_lon": rx_lon, "hours": 24.0, "n": 288,
        })
        data = getattr(res, "data", {}) or {}
        self.finished.emit({
            "simulated": False,
            "estimator": "lro_track",
            "times": list(range(len(data.get("azimuth_deg", [])))),
            "pos_error_km": [],
            "residuals": [],
            "azimuth_deg": data.get("azimuth_deg", []),
            "elevation_deg": data.get("elevation_deg", []),
            "final_error_km": None,
            "residual_rms": None,
            "converged": None,
            "content": getattr(res, "content", ""),
            "success": getattr(res, "success", False),
        })

    def _run_determine(self):
        """离线 IQ / 实时 SDR：真实多普勒观测需先从 IQ 提取频偏。
        这里如实调用后端 doppler_orbit_determine；若未提供观测序列则返回提示。"""
        reg = self._ensure_registry()
        obs = self._params.get("observations")
        if not obs:
            self.finished.emit({
                "simulated": False, "estimator": self._params["estimator"],
                "times": [], "pos_error_km": [], "residuals": [],
                "azimuth_deg": [], "elevation_deg": [],
                "final_error_km": None, "residual_rms": None, "converged": None,
                "content": "未提供多普勒观测序列：请先从 IQ 提取频偏（离线文件需先解调）。",
                "success": False,
            })
            return
        res = reg.call("doppler_orbit_determine", {
            "observations": obs,
            "init_state": self._params["init_state"],
            "rx_lat": self._params["rx_lat"],
            "rx_lon": self._params["rx_lon"],
            "rx_alt": self._params["rx_alt"],
            "estimator": self._params["estimator"],
        })
        data = getattr(res, "data", {}) or {}
        stats = data.get("residual_stats", {})
        self.finished.emit({
            "simulated": False,
            "estimator": data.get("estimator", self._params["estimator"]),
            "times": [t - data["times"][0] for t in data.get("times", [])],
            "pos_error_km": [],
            "residuals": [],
            "azimuth_deg": [], "elevation_deg": [],
            "final_error_km": None,
            "residual_rms": stats.get("rms"),
            "converged": None,
            "content": getattr(res, "content", ""),
            "success": getattr(res, "success", False),
        })


# ======================================================================
# matplotlib 画布：三子图
# ======================================================================
class OrbitCanvas(FigureCanvasQTAgg):
    def __init__(self, parent=None):
        c = _theme_colors()
        self._c = c
        fig = Figure(figsize=(7, 5))
        fig.patch.set_facecolor(c["card"])
        self.ax_err = fig.add_subplot(2, 2, 1)
        self.ax_hist = fig.add_subplot(2, 2, 2)
        self.ax_sky = fig.add_subplot(2, 2, 3, projection="polar")
        self.ax_sky.set_theta_zero_location("N")
        self.ax_sky.set_theta_direction(-1)
        self.ax_status = fig.add_subplot(2, 2, 4)
        self.ax_status.axis("off")
        super().__init__(fig)
        self._style_axes()
        self._show_empty()

    def _style_axes(self):
        c = self._c
        for ax in (self.ax_err, self.ax_hist, self.ax_sky):
            ax.set_facecolor(c["bg_alt"])
            ax.tick_params(colors=c["text_secondary"], labelsize=8)
            for spine in getattr(ax, "spines", {}).values():
                spine.set_color(c["border"])
            ax.title.set_color(c["text"])

    def _show_empty(self, msg: str = "无观测数据"):
        for ax in (self.ax_err, self.ax_hist, self.ax_sky):
            ax.clear()
        self.ax_err.text(0.5, 0.5, msg, ha="center", va="center",
                         transform=self.ax_err.transAxes,
                         color=self._c["text_disabled"], fontsize=11)
        self.ax_err.set_facecolor(self._c["bg_alt"])
        self.draw_idle()

    def plot_results(self, d: Dict):
        c = self._c
        prim = c["primary"]; acc = c["accent"]; txt = c["text"]

        # 1) 位置误差曲线
        self.ax_err.clear()
        self.ax_err.set_facecolor(c["bg_alt"])
        pe = d.get("pos_error_km", [])
        if pe:
            t = d.get("times", list(range(len(pe))))
            self.ax_err.plot(t, pe, color=prim, lw=1.8, label="位置误差")
            self.ax_err.axhline(1.0, color=acc, ls="--", lw=1.0, label="1 km 门限")
            self.ax_err.set_ylabel("误差 (km)", color=txt, fontsize=8)
            self.ax_err.set_xlabel("时间 (s)", color=txt, fontsize=8)
            self.ax_err.set_title("三维位置误差收敛", fontsize=9)
            self.ax_err.legend(fontsize=7, loc="upper right")
            self.ax_err.grid(True, alpha=0.3)
        else:
            self.ax_err.text(0.5, 0.5, "无误差曲线", ha="center", va="center",
                             transform=self.ax_err.transAxes,
                             color=c["text_disabled"], fontsize=9)
            self.ax_err.set_title("三维位置误差收敛", fontsize=9)

        # 2) 残差直方图
        self.ax_hist.clear()
        self.ax_hist.set_facecolor(c["bg_alt"])
        res = d.get("residuals", [])
        if res:
            self.ax_hist.hist(res, bins=25, color=prim, edgecolor=c["border"], alpha=0.85)
            self.ax_hist.set_title("残差直方图", fontsize=9)
            self.ax_hist.set_xlabel("伪距率残差", color=txt, fontsize=8)
            self.ax_hist.set_ylabel("计数", color=txt, fontsize=8)
            self.ax_hist.grid(True, alpha=0.3)
        else:
            self.ax_hist.text(0.5, 0.5, "无残差", ha="center", va="center",
                              transform=self.ax_hist.transAxes,
                              color=c["text_disabled"], fontsize=9)
            self.ax_hist.set_title("残差直方图", fontsize=9)

        # 3) 天空图（极坐标）
        self.ax_sky.clear()
        self.ax_sky.set_facecolor(c["bg_alt"])
        self.ax_sky.set_theta_zero_location("N")
        self.ax_sky.set_theta_direction(-1)
        az = d.get("azimuth_deg", [])
        el = d.get("elevation_deg", [])
        if az and el:
            az_rad = np.deg2rad(np.asarray(az))
            r = 90.0 - np.asarray(el)
            self.ax_sky.plot(az_rad, r, color=acc, lw=1.6, marker="o",
                             markersize=2)
            self.ax_sky.set_yticks([0, 30, 60, 90])
            self.ax_sky.set_yticklabels(["90°", "60°", "30°", "0°"],
                                        color=c["text_secondary"], fontsize=7)
        else:
            self.ax_sky.text(0.5, 0.5, "无天空轨迹", ha="center", va="center",
                             transform=self.ax_sky.transAxes,
                             color=c["text_disabled"], fontsize=8)
        self.ax_sky.set_title("天空图 (AZ/EL)", fontsize=9, va="bottom")

        # 4) 状态文本
        self.ax_status.clear()
        self.ax_status.axis("off")
        self.ax_status.set_facecolor(c["card"])
        lines = []
        if d.get("simulated"):
            lines.append("[模拟]")
        lines.append(f"估计器: {d.get('estimator','?')}")
        fe = d.get("final_error_km")
        lines.append(f"最终位置误差: {fe:.3f} km" if fe is not None else
                     "最终位置误差: --")
        rms = d.get("residual_rms")
        lines.append(f"残差 RMS: {rms:.5f}" if rms is not None else "残差 RMS: --")
        conv = d.get("converged")
        lines.append("收敛状态: " +
                     ("已收敛 (<1km)" if conv is True else
                      ("未收敛" if conv is False else "--")))
        if d.get("content"):
            lines.append(d["content"][:60])
        self.ax_status.text(0.05, 0.95, "\n".join(lines),
                            va="top", ha="left", color=txt, fontsize=9,
                            family="monospace")
        self.figure.tight_layout()
        self.draw_idle()


# ======================================================================
# 多普勒定轨面板主体
# ======================================================================
class DopplerPanel(QWidget):
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._c = _theme_colors()
        self._sdr_connected = False
        self._worker: Optional[QThread] = None
        self._worker_obj: Optional[OrbitWorker] = None
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)

        # ===== 控制卡片 =====
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
        self.target_combo.setToolTip("选择定轨目标：LRO 月球轨道 / Iridium-107 / 自定义 TLE")
        self.target_combo.addItems(["LRO (月球轨道)", "Iridium-107", "自定义 TLE"])
        form.addRow("目标", self.target_combo)

        self.mode_combo = QComboBox()
        self.mode_combo.setToolTip("离线 IQ 文件或实时 SDR（无硬件时实时置灰）")
        self.mode_combo.addItems(["离线 IQ 文件", "实时 SDR"])
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        form.addRow("观测模式", self.mode_combo)

        # 地面站参数（默认留空，提示用户输入；可从 ~/.mbdsdr/config.json 读取）
        st = QHBoxLayout()
        self.lat_edit = QLineEdit("")
        self.lat_edit.setPlaceholderText("未设置")
        self.lat_edit.setToolTip("地面站纬度 (°N)，请输入；或在 ~/.mbdsdr/config.json 设置 ground_station_lat")
        self.lon_edit = QLineEdit("")
        self.lon_edit.setPlaceholderText("未设置")
        self.lon_edit.setToolTip("地面站经度 (°E)，请输入；或在 ~/.mbdsdr/config.json 设置 ground_station_lon")
        self.alt_edit = QLineEdit("0.0")
        self.alt_edit.setToolTip("地面站高度 (km)")
        st.addWidget(QLabel("纬度")); st.addWidget(self.lat_edit)
        st.addWidget(QLabel("经度")); st.addWidget(self.lon_edit)
        st.addWidget(QLabel("高度km")); st.addWidget(self.alt_edit)
        form.addRow("地面站", st)

        # 估计器
        est = QHBoxLayout()
        self.ekf_radio = QRadioButton("EKF")
        self.ekf_radio.setToolTip("扩展卡尔曼滤波（序贯估计）")
        self.ekf_radio.setChecked(True)
        self.rls_radio = QRadioButton("RLS")
        self.rls_radio.setToolTip("参考历元递推最小二乘（批处理）")
        self.est_group = QButtonGroup(self)
        self.est_group.addButton(self.ekf_radio)
        self.est_group.addButton(self.rls_radio)
        est.addWidget(self.ekf_radio)
        est.addWidget(self.rls_radio)
        est.addStretch()
        form.addRow("估计器", est)

        cl.addLayout(form)

        btn_row = QHBoxLayout()
        self.start_btn = QPushButton("开始定轨")
        self.start_btn.setObjectName("recordButton")
        self.start_btn.setToolTip("在后台线程调用后端定轨工具")
        self.start_btn.clicked.connect(self._on_start)
        btn_row.addWidget(self.start_btn)

        self.stop_btn = QPushButton("停止")
        self.stop_btn.setToolTip("停止后台定轨线程")
        self.stop_btn.clicked.connect(self._on_stop)
        self.stop_btn.setEnabled(False)
        btn_row.addWidget(self.stop_btn)

        self.demo_btn = QPushButton("[模拟] Iridium 合成定轨演示")
        self.demo_btn.setToolTip("用合成 Iridium 过境数据跑 EKF/RLS 收敛，标 [模拟]")
        self.demo_btn.clicked.connect(self._on_demo)
        btn_row.addWidget(self.demo_btn)
        cl.addLayout(btn_row)

        outer.addWidget(ctrl)

        # ===== 曲线显示 =====
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

        # ===== 结果状态栏 =====
        self.status_label = QLabel("就绪 — 无观测数据")
        self.status_label.setObjectName("statusValue")
        self.status_label.setToolTip("最终位置误差 / 残差 RMS / 收敛状态")
        outer.addWidget(self.status_label)

        self._on_mode_changed()

    # ------------------------------------------------------------------
    def set_sdr_connected(self, connected: bool):
        self._sdr_connected = bool(connected)
        self._on_mode_changed()

    def _is_busy(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def _on_mode_changed(self, *_):
        realtime = self.mode_combo.currentText() == "实时 SDR"
        if realtime and not self._sdr_connected:
            self.start_btn.setEnabled(False)
            self.status_label.setText("未连接SDR设备 — 实时观测不可用")
        else:
            self.start_btn.setEnabled(not self._is_busy())

    def _refresh_start_enabled(self):
        """结束后台后只恢复按钮可用性，不覆盖已显示的结果状态。"""
        realtime = self.mode_combo.currentText() == "实时 SDR"
        if realtime and not self._sdr_connected:
            self.start_btn.setEnabled(False)
        else:
            self.start_btn.setEnabled(True)

    def _ground_station(self):
        try:
            lat = float(self.lat_edit.text())
            lon = float(self.lon_edit.text())
            alt = float(self.alt_edit.text())
        except ValueError:
            QMessageBox.warning(self, "参数错误", "地面站坐标需为数字。")
            return None
        return lat, lon, alt

    def _estimator(self) -> str:
        return "rls" if self.rls_radio.isChecked() else "ekf"

    # ------------------------------------------------------------------
    def _on_start(self):
        if self._is_busy():
            return
        gs = self._ground_station()
        if gs is None:
            return
        realtime = self.mode_combo.currentText() == "实时 SDR"
        if realtime and not self._sdr_connected:
            QMessageBox.warning(self, "未连接 SDR", "未连接SDR设备，无法实时观测。")
            return

        target = self.target_combo.currentText()
        if target.startswith("LRO"):
            self._start_worker("lro", {"rx_lat": gs[0], "rx_lon": gs[1]})
        else:
            # Iridium-107 / 自定义 TLE：真实观测需先从 IQ 提取多普勒频偏
            self._start_worker("determine", {
                "rx_lat": gs[0], "rx_lon": gs[1], "rx_alt": gs[2],
                "estimator": self._estimator(),
                "observations": [],   # 真实观测未接入时如实提示
            })

    def _on_demo(self):
        if self._is_busy():
            return
        gs = self._ground_station()
        if gs is None:
            return
        self._start_worker("demo", {
            "rx_lat": gs[0], "rx_lon": gs[1], "rx_alt": gs[2],
            "estimator": self._estimator(),
        })

    def _on_stop(self):
        if self._worker is not None:
            self._worker.quit()
            self._worker.wait(1500)
        self._on_idle()

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
        self._refresh_start_enabled()

    def _on_progress(self, text: str):
        self.status_label.setText(text)

    def _on_failed(self, text: str):
        self.status_label.setText(f"错误: {text.splitlines()[0]}")
        QMessageBox.critical(self, "定轨错误", text)

    def _on_result(self, d: Dict):
        if not isinstance(d, dict):
            d = {}
        self.canvas.plot_results(d)
        tag = " [模拟]" if d.get("simulated") else ""
        self.plot_title.setText(f"定轨结果{tag} — {self.target_combo.currentText()}")

        fe = d.get("final_error_km")
        rms = d.get("residual_rms")
        conv = d.get("converged")
        parts = [f"{d.get('estimator','?')}{tag}"]
        parts.append(f"最终位置误差: {fe:.3f} km" if fe is not None else
                     "最终位置误差: --")
        parts.append(f"残差RMS: {rms:.5f}" if rms is not None else "残差RMS: --")
        parts.append("收敛: " +
                     ("已收敛" if conv is True else ("未收敛" if conv is False else "--")))
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
