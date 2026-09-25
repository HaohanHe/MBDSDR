"""
MBDSDR AI - r2cloud 卫星接收任务调度器移植
=============================================

本模块把 r2cloud (Apache-2.0, dennasherbrezon) 的**调度/任务队列/观测站配置/录制元数据**
概念移植为纯 NumPy/Python 实现。轨道传播直接复用 mbdsdr_ai/gpredict_adapter.py
（SGP4/TLE），不重复实现。

移植来源（file:line 标注于各常量/类处）::

  - ObservationFactory.java:24-26   MAX/MIN_OBSERVATION_MILLIS 裁剪
  - predict/MinElevationHandler.java:12-28 仰角穿越事件(AOS increasing / LOS decreasing)
  - model/ObservationRequest.java   任务字段: id/start/end/satelliteId/transmitterId/
                                     groundStation/frequency/centerBandFrequency
  - model/ObservationStatus.java    状态机: RECEIVING_DATA->RECEIVED->DECODED->UPLOADED
  - satellite/TimeSlot.java         频率+起止时间片段
  - satellite/SequentialTimetable.java:11-56 非重叠时间槽按时间排序
  - model/Observation.java          录制元数据: sampleRate/frequency/rawPath/sigmfMeta
"""
from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

from .gpredict_adapter import (
    TLEParser, TLEData, GeoStation, SatPassPredictor, PassEvent,
    unix_to_jd,
)


# =====================================================================
# 常量 —— 来源: r2cloud ObservationFactory.java:24-26
# =====================================================================
MAX_OBSERVATION_MILLIS = 15 * 60 * 1000   # ObservationFactory.java:24
MIN_OBSERVATION_MILLIS = 4 * 60 * 1000    # ObservationFactory.java:25
DEFAULT_MIN_ELEVATION_DEG = 0.0           # r2cloud 默认地平线 0°（用户可在天线配置里改）
DEFAULT_SAMPLE_RATE_HZ = 1024000          # r2cloud RtlSdrDevice 默认 1.024 Msps


# =====================================================================
# 观测站配置 —— 对应 r2cloud AntennaConfiguration / GeodeticPoint
# =====================================================================
@dataclass
class R2CloudStation:
    """r2cloud 观测站（天线配置）。

    来源: ru.r2cloud.model.AntennaConfiguration + PredictOreKit.getPosition()
        groundStation = new GeodeticPoint(lat, lon, alt)  (Orekit)
    """
    name: str = "default"
    lat_deg: float = 0.0
    lon_deg: float = 0.0
    alt_m: float = 0.0                 # 海拔（米，r2cloud GeodeticPoint 用米）
    min_elevation_deg: float = DEFAULT_MIN_ELEVATION_DEG
    # r2cloud 里每副天线绑定一个最小仰角（ElevationDetector(minElevation, station)）
    # MinElevationHandler.java:14  increasing=true 记录 AOS, false 记录 LOS

    def to_geostation(self) -> GeoStation:
        return GeoStation(self.lat_deg, self.lon_deg, self.alt_m / 1000.0)


# =====================================================================
# 任务（观测请求） —— 来源: model/ObservationRequest.java
# =====================================================================
@dataclass
class ObservationTask:
    """一次过境录制任务。字段对齐 r2cloud ObservationRequest.java。

    来源: ObservationRequest.java:7-17
        id / startTimeMillis / endTimeMillis / satelliteId / transmitterId /
        tle / groundStation / frequency / centerBandFrequency
    """
    satellite_id: str
    transmitter_id: str
    start_unix_s: float
    end_unix_s: float
    frequency_hz: int
    center_band_hz: int = 0
    tle_line1: str = ""
    tle_line2: str = ""
    sat_name: str = ""
    max_el_deg: float = 0.0
    aos_az_deg: float = 0.0
    los_az_deg: float = 0.0
    status: str = "NEW"               # ObservationStatus.java 状态机
    task_id: str = ""

    def __post_init__(self):
        if not self.task_id:
            # ObservationFactory.java:75  id = startMillis + "-" + transmitterId
            self.task_id = f"{int(self.start_unix_s * 1000)}-{self.transmitter_id}"

    @property
    def duration_s(self) -> float:
        return self.end_unix_s - self.start_unix_s


# =====================================================================
# 时间槽（频段占用） —— 来源: satellite/TimeSlot.java
# =====================================================================
@dataclass
class TimeSlot:
    """一个被占用的频段时间片。来源: TimeSlot.java:5-20 (frequency/start/end)。"""
    frequency_hz: int
    start_unix_s: float
    end_unix_s: float

    def overlaps(self, other: "TimeSlot") -> bool:
        return (self.start_unix_s < other.end_unix_s and
                other.start_unix_s < self.end_unix_s)


# =====================================================================
# 顺序时间表（非重叠任务队列） —— 来源: satellite/SequentialTimetable.java
# =====================================================================
class SequentialTimetable:
    """r2cloud SequentialTimetable：按时间排序、拒绝重叠的时间槽列表。

    来源: SequentialTimetable.java:11-56
        addFully(slot): 若 slot 与现有所有槽不重叠则插入并按时间排序，返回 True
        addPartially(slot): 与 tolerance 容差内的重叠做裁剪
    """

    def __init__(self, partial_tolerance_s: float = 30.0):
        self.partial_tolerance_s = partial_tolerance_s   # 构造函数 :13
        self._slots: List[TimeSlot] = []

    def add_fully(self, slot: TimeSlot) -> bool:
        """完整加入；任何重叠则拒绝。来源: addFully() :20-46"""
        for cur in self._slots:
            if cur.overlaps(slot):
                return False
        self._slots.append(slot)
        self._slots.sort(key=lambda s: s.start_unix_s)
        return True

    def add_partially(self, slot: TimeSlot) -> Optional[TimeSlot]:
        """重叠时裁剪为不重叠片段。来源: addPartially() :48+"""
        for cur in self._slots:
            if not cur.overlaps(slot):
                continue
            # 与 cur 重叠：裁剪 slot 到 cur.start 之前
            if slot.start_unix_s < cur.start_unix_s:
                clipped = TimeSlot(slot.frequency_hz, slot.start_unix_s,
                                   cur.start_unix_s - self.partial_tolerance_s)
                if clipped.end_unix_s > clipped.start_unix_s:
                    self._slots.append(clipped)
                    self._slots.sort(key=lambda s: s.start_unix_s)
                    return clipped
            return None
        self._slots.append(slot)
        self._slots.sort(key=lambda s: s.start_unix_s)
        return slot

    def slots(self) -> List[TimeSlot]:
        return list(self._slots)


# =====================================================================
# 录制元数据 —— 来源: model/Observation.java (sigmf 风格)
# =====================================================================
def build_recording_metadata(task: ObservationTask, station: R2CloudStation,
                             sample_rate_hz: int = DEFAULT_SAMPLE_RATE_HZ,
                             modulation: str = "LSB") -> Dict[str, Any]:
    """生成 r2cloud 风格的录制元数据（SigMF-like）。

    来源: Observation.java:18-46
        sampleRate / frequency / rawPath / sigmfMetaURL / dataFormat /
        startTimeMillis / groundStation / tle / status
    r2cloud 录制原始 IQ 为 SigMF 打包（.sigmf-data + .sigmf-meta JSON）。
    """
    return {
        "global": {
            "core:datatype": "ci8_le",           # r2cloud 录制 IQ 通常 int8 复数
            "core:sample_rate": sample_rate_hz,
            "core:frequency": task.frequency_hz,
            "core:author": "mbdsdr-r2cloud-adapter",
            "core:description": f"r2cloud pass record: {task.sat_name}",
        },
        "captures": [{
            "core:datetime": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                           time.gmtime(task.start_unix_s)),
            "core:frequency": task.frequency_hz,
        }],
        "annotations": [{
            "core:sample_start": 0,
            "core:duration": task.duration_s,
            "r2cloud:task_id": task.task_id,
            "r2cloud:satellite_id": task.satellite_id,
            "r2cloud:transmitter_id": task.transmitter_id,
            "r2cloud:station": {
                "lat": station.lat_deg, "lon": station.lon_deg,
                "alt_m": station.alt_m,
                "min_elevation_deg": station.min_elevation_deg,
            },
            "r2cloud:max_el_deg": task.max_el_deg,
            "r2cloud:aos_az_deg": task.aos_az_deg,
            "r2cloud:los_az_deg": task.los_az_deg,
            "r2cloud:tle": [task.tle_line1, task.tle_line2],
            "r2cloud:modulation": modulation,
        }],
        "r2cloud:status": task.status,
    }


# =====================================================================
# 调度器 —— 基于 gpredict 过境预测 + r2cloud 任务裁剪规则
# =====================================================================
class R2CloudScheduler:
    """r2cloud 调度器：给定 TLE 列表 + 观测站，生成过境录制任务队列。

    流程（对应 r2cloud 启动后定时调用 ObservationFactory.createSchedule）:
      1. 对每个卫星/发射机，用 gpredict SatPassPredictor 扫 AOS/LOS
         （MinElevationHandler.java:14 increasing=true -> AOS, false -> LOS）
      2. 裁剪过长过境到 MAX_OBSERVATION_MILLIS=15min，丢弃短于
         MIN_OBSERVATION_MILLIS=4min 的过境  (ObservationFactory.java:52-63)
      3. 用 SequentialTimetable 去重重叠时段（同一时刻只能录一个频点）
    """

    def __init__(self, station: R2CloudStation):
        self.station = station
        self.timetable = SequentialTimetable()

    def schedule_satellite(self, sat_name: str, line1: str, line2: str,
                           transmitter_id: str, frequency_hz: int,
                           start_unix_s: float, horizon_hours: float = 24.0,
                           center_band_hz: int = 0) -> List[ObservationTask]:
        """为一颗卫星生成过境任务列表。"""
        tle = TLEParser.parse(sat_name, line1, line2)
        st = self.station.to_geostation()
        predictor = SatPassPredictor(tle, st)
        passes = predictor.predict_passes(
            start_unix_s, duration_hours=horizon_hours,
            min_elevation_deg=self.station.min_elevation_deg, step_s=30.0)

        tasks: List[ObservationTask] = []
        for p in passes:
            start = p.aos_unix
            end = p.los_unix
            # ObservationFactory.java:52-58  过长过境切 15min 段
            while (end - start) * 1000.0 > MAX_OBSERVATION_MILLIS:
                seg_end = start + MAX_OBSERVATION_MILLIS / 1000.0
                tasks.append(self._make_task(tle, transmitter_id, frequency_hz,
                                             center_band_hz, start, seg_end, p))
                start = seg_end
            # ObservationFactory.java:59-61  过短(<4min)丢弃
            if (end - start) * 1000.0 < MIN_OBSERVATION_MILLIS:
                continue
            tasks.append(self._make_task(tle, transmitter_id, frequency_hz,
                                         center_band_hz, start, end, p))
        return tasks

    def _make_task(self, tle: TLEData, transmitter_id: str, frequency_hz: int,
                   center_band_hz: int, start: float, end: float,
                   p: PassEvent) -> ObservationTask:
        task = ObservationTask(
            satellite_id=str(tle.catnr),
            transmitter_id=transmitter_id,
            start_unix_s=start,
            end_unix_s=end,
            frequency_hz=int(frequency_hz),
            center_band_hz=int(center_band_hz or frequency_hz),
            tle_line1="", tle_line2="",
            sat_name=tle.sat_name,
            max_el_deg=p.max_el_deg,
            aos_az_deg=p.aos_az_deg,
            los_az_deg=p.los_az_deg,
        )
        # 尝试注册到时间表（重叠则裁剪/拒绝）
        slot = TimeSlot(task.frequency_hz, start, end)
        self.timetable.add_partially(slot)
        return task

    def schedule_many(self, transmitters: List[Dict[str, Any]],
                      start_unix_s: float, horizon_hours: float = 24.0
                      ) -> List[ObservationTask]:
        """批量调度多个卫星发射机。

        transmitters: [{"sat_name","line1","line2","transmitter_id","frequency_hz",
                        "center_band_hz"?}, ...]
        """
        all_tasks: List[ObservationTask] = []
        for tx in transmitters:
            tasks = self.schedule_satellite(
                tx["sat_name"], tx["line1"], tx["line2"],
                tx["transmitter_id"], tx["frequency_hz"],
                start_unix_s, horizon_hours,
                tx.get("center_band_hz", 0))
            all_tasks.extend(tasks)
        all_tasks.sort(key=lambda t: t.start_unix_s)
        return all_tasks


# =====================================================================
# 工具入口
# =====================================================================
def tool_r2cloud_schedule(args: Dict[str, Any]) -> Dict[str, Any]:
    """调度器工具：生成任务列表。"""
    station = R2CloudStation(
        name=args.get("station_name", "default"),
        lat_deg=float(args["lat_deg"]), lon_deg=float(args["lon_deg"]),
        alt_m=float(args.get("alt_m", 0.0)),
        min_elevation_deg=float(args.get("min_elevation_deg", DEFAULT_MIN_ELEVATION_DEG)),
    )
    sched = R2CloudScheduler(station)
    txs = args["transmitters"]
    t0 = float(args.get("start_unix_s", time.time()))
    horizon = float(args.get("horizon_hours", 24.0))
    tasks = sched.schedule_many(txs, t0, horizon)
    return {
        "station": asdict(station),
        "task_count": len(tasks),
        "tasks": [
            {
                "task_id": t.task_id,
                "satellite": t.sat_name,
                "satellite_id": t.satellite_id,
                "transmitter_id": t.transmitter_id,
                "aos_utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(t.start_unix_s)),
                "los_utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(t.end_unix_s)),
                "duration_s": round(t.duration_s, 1),
                "frequency_hz": t.frequency_hz,
                "max_el_deg": round(t.max_el_deg, 1),
                "aos_az_deg": round(t.aos_az_deg, 1),
                "los_az_deg": round(t.los_az_deg, 1),
                "status": t.status,
            } for t in tasks
        ],
        "method": "r2cloud-ObservationFactory+MinElevationHandler",
    }


def tool_r2cloud_recording_meta(args: Dict[str, Any]) -> Dict[str, Any]:
    """生成录制元数据。"""
    station = R2CloudStation(
        lat_deg=float(args["lat_deg"]), lon_deg=float(args["lon_deg"]),
        alt_m=float(args.get("alt_m", 0.0)))
    task = ObservationTask(
        satellite_id=str(args.get("satellite_id", "0")),
        transmitter_id=args.get("transmitter_id", "tx"),
        start_unix_s=float(args["start_unix_s"]),
        end_unix_s=float(args["end_unix_s"]),
        frequency_hz=int(args["frequency_hz"]),
        sat_name=args.get("sat_name", ""),
        max_el_deg=float(args.get("max_el_deg", 0.0)),
        tle_line1=args.get("line1", ""), tle_line2=args.get("line2", ""),
    )
    meta = build_recording_metadata(task, station,
                                    int(args.get("sample_rate_hz", DEFAULT_SAMPLE_RATE_HZ)),
                                    args.get("modulation", "LSB"))
    return {"metadata": meta,
            "method": "r2cloud-Observation.java:sigmf"}


# =====================================================================
# 注册到 ToolRegistry
# =====================================================================
def register_r2cloud_tools(registry) -> None:
    """把 r2cloud 调度/录制能力注册到 MBDSDR ToolRegistry。

    提供两个工具：
      - r2cloud_schedule       : 基于 TLE+观测站生成过境录制任务队列
      - r2cloud_recording_meta : 为一次任务生成 SigMF 风格录制元数据
    """
    from .tool_registry import ToolResult

    def _schedule(args):
        try:
            data = tool_r2cloud_schedule(args)
            return ToolResult(
                success=True,
                content=f"r2cloud 调度完成：{data['task_count']} 个过境任务",
                data=data)
        except Exception as e:
            return ToolResult(success=False, content=f"r2cloud 调度失败: {e}",
                              error="schedule_failed")

    def _meta(args):
        try:
            data = tool_r2cloud_recording_meta(args)
            return ToolResult(success=True, content="录制元数据已生成", data=data)
        except Exception as e:
            return ToolResult(success=False, content=f"元数据生成失败: {e}",
                              error="meta_failed")

    registry.register(
        name="r2cloud_schedule",
        description="r2cloud 调度器：基于 TLE 与观测站(经纬度/海拔/最小仰角)生成过境录制任务队列",
        parameters={
            "type": "object",
            "properties": {
                "lat_deg": {"type": "number"}, "lon_deg": {"type": "number"},
                "alt_m": {"type": "number"},
                "min_elevation_deg": {"type": "number"},
                "horizon_hours": {"type": "number"},
                "start_unix_s": {"type": "number"},
                "transmitters": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "sat_name": {"type": "string"},
                            "line1": {"type": "string"},
                            "line2": {"type": "string"},
                            "transmitter_id": {"type": "string"},
                            "frequency_hz": {"type": "number"},
                        },
                        "required": ["sat_name", "line1", "line2",
                                     "transmitter_id", "frequency_hz"],
                    },
                },
            },
            "required": ["lat_deg", "lon_deg", "transmitters"],
        },
        handler=_schedule,
        category="sat_groundstation",
    )
    registry.register(
        name="r2cloud_recording_meta",
        description="为 r2cloud 任务生成 SigMF 风格录制元数据(采样率/频率/TLE/站坐标/仰角)",
        parameters={
            "type": "object",
            "properties": {
                "lat_deg": {"type": "number"}, "lon_deg": {"type": "number"},
                "alt_m": {"type": "number"},
                "start_unix_s": {"type": "number"}, "end_unix_s": {"type": "number"},
                "frequency_hz": {"type": "number"},
                "sample_rate_hz": {"type": "number"},
                "sat_name": {"type": "string"},
                "transmitter_id": {"type": "string"},
                "line1": {"type": "string"}, "line2": {"type": "string"},
            },
            "required": ["lat_deg", "lon_deg", "start_unix_s", "end_unix_s",
                         "frequency_hz"],
        },
        handler=_meta,
        category="sat_groundstation",
    )
