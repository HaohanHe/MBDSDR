# SPDX-License-Identifier: MIT
"""
mbdsdr_ai/background_decoder.py — 后台解码调度
==============================================

  * 解码器注册表：每个解码器声明频率/模式/周期；
  * 调度器按周期轮询调用解码器，结果持久化到 JSONL；
  * 无客户端连接时也持续运行。

增强：
  * :class:`AnomalyDetector` —— 对解码结果做异常检测（如 ADS-B 出现
    不可能的高度/速度、APRS callsign 缺失），触发告警。
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

__all__ = ["BackgroundDecoder", "BackgroundScheduler", "AnomalyDetector", "DecodeResult"]


@dataclass
class DecodeResult:
    decoder: str
    frequency_hz: float
    timestamp: float
    data: Dict[str, Any]
    anomaly: Optional[str] = None


class AnomalyDetector:
    """简单规则异常检测（可替换为 ML 模型）。"""

    def check(self, result: DecodeResult) -> Optional[str]:
        d = result.data
        if result.decoder == "adsb":
            alt = d.get("altitude_ft")
            if alt is not None and (alt < -1000 or alt > 60000):
                return f"impossible altitude {alt} ft"
            speed = d.get("speed_kts")
            if speed is not None and (speed < 0 or speed > 800):
                return f"impossible speed {speed} kts"
        if result.decoder == "aprs":
            if not d.get("callsign"):
                return "APRS packet missing callsign"
        return None


@dataclass
class BackgroundDecoder:
    name: str
    frequency_hz: float
    period_s: float = 1.0
    decode_fn: Optional[Callable[[], Dict[str, Any]]] = None


class BackgroundScheduler:
    """后台解码调度器：注册解码器，按周期运行，结果写 JSONL。"""

    def __init__(self, storage_path: Optional[str] = None,
                 clock: Callable[[], float] = time.time):
        self._decoders: Dict[str, BackgroundDecoder] = {}
        self._lock = threading.RLock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._storage_path = storage_path
        self._clock = clock
        self._anomaly = AnomalyDetector()
        self._alerts: List[DecodeResult] = []
        self._results: List[DecodeResult] = []

    def register(self, decoder: BackgroundDecoder) -> None:
        with self._lock:
            self._decoders[decoder.name] = decoder

    def unregister(self, name: str) -> None:
        with self._lock:
            self._decoders.pop(name, None)

    @property
    def decoders(self) -> List[BackgroundDecoder]:
        with self._lock:
            return list(self._decoders.values())

    def run_once(self) -> List[DecodeResult]:
        """立即运行所有解码器一次（用于测试/手动触发）。"""
        results: List[DecodeResult] = []
        with self._lock:
            decoders = list(self._decoders.values())
        for dec in decoders:
            if dec.decode_fn is None:
                continue
            try:
                data = dec.decode_fn()
            except Exception as e:  # noqa: BLE001
                data = {"error": str(e)}
            r = DecodeResult(decoder=dec.name, frequency_hz=dec.frequency_hz,
                             timestamp=self._clock(), data=data)
            r.anomaly = self._anomaly.check(r)
            if r.anomaly:
                self._alerts.append(r)
            results.append(r)
            self._persist(r)
        with self._lock:
            self._results.extend(results)
        return results

    def _persist(self, result: DecodeResult) -> None:
        if not self._storage_path:
            return
        os.makedirs(os.path.dirname(self._storage_path) or ".", exist_ok=True)
        with open(self._storage_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "decoder": result.decoder, "freq": result.frequency_hz,
                "ts": result.timestamp, "data": result.data,
                "anomaly": result.anomaly,
            }) + "\n")

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            self._running = False

    def _loop(self) -> None:
        next_run: Dict[str, float] = {}
        while self._running:
            now = self._clock()
            for dec in self.decoders:
                if next_run.get(dec.name, 0.0) <= now:
                    self.run_once()
                    next_run[dec.name] = now + dec.period_s
            time.sleep(0.1)

    @property
    def alerts(self) -> List[DecodeResult]:
        return list(self._alerts)

    @property
    def results(self) -> List[DecodeResult]:
        return list(self._results)
