"""
MBDSDR AI - satnogs 地面站自动化移植
======================================

本模块把 satnogs-client + gr-satnogs (AGPLv3, satnogs.org) 的地面站自动化流程移植为
纯 NumPy/Python。轨道传播/多普勒直接复用 mbdsdr_ai/gpredict_adapter.py。

移植来源（file:line 标注）::

  - satnogs-client satnogsclient/observers/observer.py
        观测流程状态机: REQUEST_NEXT -> WAIT -> START OBS -> DOPPLER ->
                        RECORD -> DECODE -> UPLOAD -> STOP
  - satnogs-client satnogsclient/network/tasks.py
        向 satnogs-network 请求下一观测:
        GET /api/observations/next/?ground_station=<id>
        返回 JSON: {id, start, end, observation_frequency, transmitter_uuid,
                    mode_id, baud, ...}
  - gr-satnogs/lib/doppler_correction/doppler_correction_impl.cc
        多普勒校正: 每 dt 秒根据 TLE 计算视线速度 v_r，
        NCO 把中心频率移到 f_rx = f_tx*(1 - v_r/c)
  - gr-satnogs/grc 解调模式枚举:
        CW (Morse), AFSK (1200/2200), FSK (9600), GMSK (9600/4800),
        LRPT (NOAA 66.67kHz subcarrier), Apt
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

from .gpredict_adapter import (
    TLEParser, TLEData, GeoStation, SGP4Propagator,
    CoordinateConverter, Observation, doppler_shift, unix_to_jd, C_LIGHT,
)


# =====================================================================
# 解调模式枚举 —— 来源: gr-satnogs/grc + satnogs-network mode 表
# =====================================================================
class DemodMode(str, Enum):
    """satnogs 支持的解调模式。

    来源: satnogs-network db/satnogs/transmitters/mode 定义 + gr-satnogs flow graph
    """
    CW = "CW"                 # 莫尔斯电报，带宽 ~50-500 Hz
    AFSK = "AFSK"             # 1200/2200 Hz 移频 (AX.25)
    FSK = "FSK"               # 连续 FSK，常用 9600 baud
    GMSK = "GMSK"             # GMSK (9600/4800/48000)
    LRPT = "LRPT"             # NOAA 低分辨率图像，66.67 kHz 副载波
    APT = "APT"               # NOAA 模拟传真，1200 Hz 副载波

    @property
    def default_bandwidth_hz(self) -> float:
        """典型解调带宽。来源: gr-satnogs grc 流图默认参数。"""
        return {
            DemodMode.CW: 500.0,
            DemodMode.AFSK: 3000.0,
            DemodMode.FSK: 9600.0,
            DemodMode.GMSK: 9600.0,
            DemodMode.LRPT: 80000.0,
            DemodMode.APT: 4160.0,
        }[self]

    @property
    def default_baud(self) -> float:
        """典型波特率。来源: satnogs-network mode 表。"""
        return {
            DemodMode.CW: 20.0,
            DemodMode.AFSK: 1200.0,
            DemodMode.FSK: 9600.0,
            DemodMode.GMSK: 9600.0,
            DemodMode.LRPT: 62500.0,   # LRPT 符号率 62.5 ksym/s
            DemodMode.APT: 1200.0,
        }[self]


# =====================================================================
# 多普勒校正 —— 来源: gr-satnogs doppler_correction_impl.cc
# =====================================================================
@dataclass
class DopplerPoint:
    """多普勒校正曲线的一个采样点。"""
    time_unix_s: float
    rx_freq_hz: float          # gr-satnogs NCO 应把中心频率移到这里
    doppler_hz: float          # 相对标称频率的频偏
    range_rate_kms: float


class DopplerCorrector:
    """gr-satnogs 多普勒校正器。

    来源: gr-satnogs/lib/doppler_correction/doppler_correction_impl.cc
        start(): 用 TLE + 地面站位置算整个过境的多普勒曲线
        work(): 每 dt 秒根据当前时刻插值出应调中心频率，驱动 NCO
        gr-satnogs 内部用 gpredict 的视线速度 (range_rate) 算 f_rx = f_tx*(1-v_r/c)
    """

    def __init__(self, tle: TLEData, station: GeoStation, tx_freq_hz: float,
                 sample_dt_s: float = 1.0):
        self.tle = tle
        self.station = station
        self.tx_freq_hz = tx_freq_hz
        self.sample_dt_s = sample_dt_s
        self.prop = SGP4Propagator(tle)
        self._curve: List[DopplerPoint] = []

    def build_curve(self, t_start_s: float, t_end_s: float) -> List[DopplerPoint]:
        """在过境期间采样多普勒曲线。"""
        self._curve = []
        t = t_start_s
        while t <= t_end_s + 1e-6:
            jd = unix_to_jd(t)
            tsince_min = (jd - self.tle.epoch_jd) * 1440.0
            r, v = self.prop.propagate(tsince_min)
            ob = CoordinateConverter.calculate_obs(jd, r, v, self.station)
            f_rx = doppler_shift(ob, self.tx_freq_hz)
            self._curve.append(DopplerPoint(
                time_unix_s=t, rx_freq_hz=f_rx,
                doppler_hz=f_rx - self.tx_freq_hz,
                range_rate_kms=ob.range_rate_kms))
            t += self.sample_dt_s
        return self._curve

    def nco_freq_at(self, t_unix_s: float) -> float:
        """当前时刻 NCO 应调中心频率（线性插值）。"""
        if not self._curve:
            raise RuntimeError("先调用 build_curve() 生成多普勒曲线")
        if t_unix_s <= self._curve[0].time_unix_s:
            return self._curve[0].rx_freq_hz
        if t_unix_s >= self._curve[-1].time_unix_s:
            return self._curve[-1].rx_freq_hz
        for i in range(1, len(self._curve)):
            if self._curve[i].time_unix_s >= t_unix_s:
                a, b = self._curve[i - 1], self._curve[i]
                frac = (t_unix_s - a.time_unix_s) / (b.time_unix_s - a.time_unix_s)
                return a.rx_freq_hz + frac * (b.rx_freq_hz - a.rx_freq_hz)
        return self._curve[-1].rx_freq_hz

    @property
    def max_doppler_hz(self) -> float:
        if not self._curve:
            return 0.0
        return max(abs(p.doppler_hz) for p in self._curve)


# =====================================================================
# 网络调度协议 —— 来源: satnogs-client network/tasks.py
# =====================================================================
@dataclass
class SatnogsObservation:
    """satnogs-network 下发的一次观测任务。

    来源: GET /api/observations/next/?ground_station=<id> 返回 JSON 字段
    """
    id: int
    start_unix_s: float
    end_unix_s: float
    observation_frequency_hz: int
    transmitter_uuid: str
    mode: str
    baud: float = 0.0
    lat: float = 0.0
    lon: float = 0.0
    alt_m: float = 0.0
    station_id: int = 0
    tle_line1: str = ""
    tle_line2: str = ""
    sat_name: str = ""

    def to_request_json(self) -> Dict[str, Any]:
        """客户端上报完成状态时 POST 的 JSON。"""
        return {
            "id": self.id,
            "approved": 0,
            "status": "data_received",
            "observation_frequency": self.observation_frequency_hz,
        }


class SatnogsNetworkClient:
    """模拟 satnogs 网络调度客户端（离线版）。

    真实协议（来源: satnogsclient/network/tasks.py）:
        GET  {base}/api/observations/next/?ground_station=<id>
        -> 200 JSON: {id, start, end, observation_frequency, transmitter_uuid,
                      mode_id, transmitter__description, ...}
        GET  {base}/api/observations/finished/<id>/  ? 标记结束
        POST {base}/api/observations/finished/<id>/  上报结果

    本离线实现不发 HTTP，而是维护一个任务队列供 observer 拉取。
    """

    def __init__(self, station_id: int = 1, station_lat: float = 0.0,
                 station_lon: float = 0.0, station_alt_m: float = 0.0):
        self.station_id = station_id
        self.station_lat = station_lat
        self.station_lon = station_lon
        self.station_alt_m = station_alt_m
        self._queue: List[SatnogsObservation] = []

    def enqueue(self, obs: SatnogsObservation):
        obs.station_id = self.station_id
        obs.lat = self.station_lat
        obs.lon = self.station_lon
        obs.alt_m = self.station_alt_m
        self._queue.append(obs)
        self._queue.sort(key=lambda o: o.start_unix_s)

    def request_next(self, now_unix_s: float) -> Optional[SatnogsObservation]:
        """GET /api/observations/next/ —— 返回下一个开始的观测。"""
        for i, obs in enumerate(self._queue):
            if obs.start_unix_s >= now_unix_s - 30.0:
                return self._queue.pop(i)
        return None

    def report_finished(self, obs: SatnogsObservation, status: str = "data_received"
                        ) -> Dict[str, Any]:
        """POST 观测完成。返回上报 JSON。"""
        body = obs.to_request_json()
        body["status"] = status
        return {"url": f"/api/observations/finished/{obs.id}/",
                "method": "POST", "body": body}


# =====================================================================
# 观测流程状态机 —— 来源: satnogs-client observer.py
# =====================================================================
class ObservationPipeline:
    """satnogs-client 观测流程：doppler→录制→解码→上传。

    状态机（来源: satnogsclient/observers/observer.py）:
        IDLE -> RECEIVING (gr-satnogs flow graph 启动) ->
        DOPPLER_CORRECTING (NCO 跟踪) -> RECORDING (iq_file_sink) ->
        DECODING (fsk_demod / morse_decoder) -> UPLOADING -> DONE
    """

    STATE_IDLE = "IDLE"
    STATE_RECEIVING = "RECEIVING_DATA"
    STATE_DOPPLER = "DOPPLER_CORRECTING"
    STATE_RECORDING = "RECORDING"
    STATE_DECODING = "DECODING"
    STATE_UPLOADING = "UPLOADING"
    STATE_DONE = "DONE"
    STATE_FAILED = "FAILED"

    def __init__(self, obs: SatnogsObservation, tle: TLEData,
                 station: GeoStation):
        self.obs = obs
        self.tle = tle
        self.station = station
        self.state = self.STATE_IDLE
        self.corrector = DopplerCorrector(tle, station,
                                          obs.observation_frequency_hz)
        self.recorded_samples: int = 0
        self.decoded_packets: int = 0
        self.log: List[str] = []

    def start(self) -> Dict[str, Any]:
        """启动观测：构建多普勒曲线，进入 RECEIVING。"""
        self.state = self.STATE_RECEIVING
        self.log.append(f"[{self.obs.id}] gr-satnogs flow graph started")
        self.corrector.build_curve(self.obs.start_unix_s, self.obs.end_unix_s)
        self.state = self.STATE_DOPPLER
        self.log.append(
            f"[{self.obs.id}] doppler curve built, "
            f"max|fd|={self.corrector.max_doppler_hz:.1f} Hz")
        return {
            "observation_id": self.obs.id,
            "mode": self.obs.mode,
            "baud": self.obs.baud,
            "rx_freq_at_aos": self.corrector._curve[0].rx_freq_hz,
            "rx_freq_at_los": self.corrector._curve[-1].rx_freq_hz,
            "max_doppler_hz": self.corrector.max_doppler_hz,
        }

    def tick(self, now_unix_s: float, iq_samples: int = 0) -> str:
        """推进状态机。返回当前状态。"""
        if self.state in (self.STATE_DONE, self.STATE_FAILED):
            return self.state
        if now_unix_s < self.obs.start_unix_s:
            return self.state
        if self.state == self.STATE_DOPPLER:
            self.state = self.STATE_RECORDING
            self.log.append(f"[{self.obs.id}] recording iq to file")
        if self.state == self.STATE_RECORDING:
            self.recorded_samples += iq_samples
            if now_unix_s >= self.obs.end_unix_s:
                self.state = self.STATE_DECODING
                self.log.append(f"[{self.obs.id}] decoding "
                                f"({self.obs.mode}, {self.obs.baud} baud)")
        elif self.state == self.STATE_DECODING:
            self.decoded_packets += 1   # 占位：真实解码由 gr-satnogs 块完成
            self.state = self.STATE_UPLOADING
            self.log.append(f"[{self.obs.id}] uploading to satnogs-network")
        elif self.state == self.STATE_UPLOADING:
            self.state = self.STATE_DONE
            self.log.append(f"[{self.obs.id}] done")
        return self.state


# =====================================================================
# 工具入口
# =====================================================================
def tool_satnogs_doppler_plan(args: Dict[str, Any]) -> Dict[str, Any]:
    """给定 TLE+站+过境时刻，输出 gr-satnogs 多普勒校正曲线。"""
    tle = TLEParser.parse(args["sat_name"], args["line1"], args["line2"])
    st = GeoStation(float(args["lat_deg"]), float(args["lon_deg"]),
                    float(args.get("alt_km", 0.0)))
    corr = DopplerCorrector(tle, st, float(args["frequency_hz"]),
                            sample_dt_s=float(args.get("dt_s", 10.0)))
    t0 = float(args["start_unix_s"])
    t1 = t0 + float(args.get("duration_s", 600.0))
    curve = corr.build_curve(t0, t1)
    return {
        "satellite": tle.sat_name,
        "tx_freq_hz": args["frequency_hz"],
        "points": [
            {"t_rel_s": round(p.time_unix_s - t0, 1),
             "rx_freq_hz": round(p.rx_freq_hz, 1),
             "doppler_hz": round(p.doppler_hz, 1),
             "range_rate_kms": round(p.range_rate_kms, 4)}
            for p in curve[:: max(1, len(curve) // 20)]
        ],
        "max_doppler_hz": round(corr.max_doppler_hz, 2),
        "method": "gr-satnogs-doppler_correction_impl",
    }


def tool_satnogs_modes(args: Dict[str, Any]) -> Dict[str, Any]:
    """列出 satnogs 解调模式及其典型带宽/波特率。"""
    return {
        "modes": [
            {"name": m.value,
             "bandwidth_hz": m.default_bandwidth_hz,
             "baud": m.default_baud}
            for m in DemodMode
        ],
        "method": "gr-satnogs-grc + satnogs-network mode table",
    }


# =====================================================================
# 注册
# =====================================================================
def register_satnogs_tools(registry) -> None:
    """把 satnogs 地面站自动化能力注册到 ToolRegistry。

    提供两个工具：
      - satnogs_doppler_plan : 生成 gr-satnogs 多普勒校正频率曲线
      - satnogs_list_modes   : 列出解调模式(CW/AFSK/FSK/GMSK/LRPT/APT)
    """
    from .tool_registry import ToolResult

    def _doppler(args):
        try:
            data = tool_satnogs_doppler_plan(args)
            return ToolResult(success=True,
                              content=f"多普勒曲线 {len(data['points'])} 点, "
                                      f"max|fd|={data['max_doppler_hz']} Hz",
                              data=data)
        except Exception as e:
            return ToolResult(success=False, content=f"多普勒计算失败: {e}",
                              error="doppler_failed")

    def _modes(args):
        data = tool_satnogs_modes(args)
        return ToolResult(success=True,
                          content=f"satnogs 支持 {len(data['modes'])} 种解调模式",
                          data=data)

    registry.register(
        name="satnogs_doppler_plan",
        description="gr-satnogs 多普勒校正：根据 TLE+观测站+过境时间，输出 NCO 应跟踪的接收频率曲线",
        parameters={
            "type": "object",
            "properties": {
                "sat_name": {"type": "string"},
                "line1": {"type": "string"}, "line2": {"type": "string"},
                "lat_deg": {"type": "number"}, "lon_deg": {"type": "number"},
                "alt_km": {"type": "number"},
                "frequency_hz": {"type": "number"},
                "start_unix_s": {"type": "number"},
                "duration_s": {"type": "number"},
                "dt_s": {"type": "number"},
            },
            "required": ["sat_name", "line1", "line2", "lat_deg", "lon_deg",
                         "frequency_hz", "start_unix_s"],
        },
        handler=_doppler,
        category="sat_groundstation",
    )
    registry.register(
        name="satnogs_list_modes",
        description="列出 satnogs/gr-satnogs 解调模式(CW/AFSK/FSK/GMSK/LRPT/APT)及典型带宽/波特率",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=_modes,
        category="sat_groundstation",
    )
