# SPDX-License-Identifier: MIT
"""指向传感器的 provider 注册表（Task 94）。

解决的问题
----------
IMU、手机、相机各自都能给出「镜头指向哪」的读数，但来源、可用性、失败
方式各不相同：一个设备掉线不该让其它读数也消失，更不该让上层看到一份
真假掺半的融合结果。本模块只做一件事：把多个 provider 的读数收集成
``fuse_poses`` 能直接消费的观测，并把单个 provider 的失败**原样隔离**。

接口
----
* ``ProviderRegistry.register(name, provider)``：provider 可以是可调用
  对象，也可以是带 ``read()`` 方法的对象（两者都有时优先 ``read()``）；
* ``unregister(name)``：注销并返回是否真的删除了；
* ``names()``：当前已注册的名称（按注册顺序）；
* ``read_all(now_utc=None)``：逐个读取，返回
  ``{"readings": {name: PoseObservation}, "errors": {name: {"type", "message"}}}``。
  某个 provider 抛异常只写进它自己的 ``errors`` 条目，绝不会中断其它
  provider，也不会让整个调用抛异常。

时间口径
--------
provider 读数可以自己带 ``t_utc``；不带时用 ``read_all`` 的 ``now_utc``
（读取时刻）补上——这是「登记读取时刻」，不是猜观测时刻。两者都没有就
把该 provider 记为错误，不让一条无时间的读数混进融合。

诚实语义
--------
* 不启动线程、不碰硬件、不联网；
* 不校验读数的物理合理性（越界/NaN/陈旧交给 ``fuse_poses`` 逐条丢弃并
  说明原因），注册表只负责「谁给了什么、谁为什么没给」。

用法::

    registry = ProviderRegistry()
    registry.register("imu", lambda: ProviderReading(
        source="imu", t_utc=now, az_deg=33.0, alt_deg=52.0,
        accuracy_deg=3.0, fov_deg=8.0))
    result = registry.read_all(now_utc=now)
    pose = fuse_poses(result["readings"].values(), now)
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from .sky_pointing import PoseObservation

__all__ = [
    "ProviderReading",
    "ProviderRegistry",
]


@dataclass(frozen=True)
class ProviderReading:
    """一个 provider 的原始读数；口径与 ``PoseObservation`` 相同。

    Attributes:
        source: 来源标签；为空时注册表用注册名补上。
        az_deg: 方位角（度，北=0 东=90）。
        alt_deg: 地平高度（度）。
        accuracy_deg: 自报 1σ 精度（度）。
        t_utc: 观测 Unix 秒（UTC）；None = 由 ``read_all`` 的 now_utc 补。
        roll_deg: 画面横滚（度），默认 0。
        fov_deg: 视场直径（度）；未知为 None。
    """

    source: str
    az_deg: float
    alt_deg: float
    accuracy_deg: float
    t_utc: Optional[float] = None
    roll_deg: float = 0.0
    fov_deg: Optional[float] = None

    def to_observation(self, *, now_utc: Optional[float] = None
                       ) -> PoseObservation:
        """转成 ``PoseObservation``；缺时间且无 ``now_utc`` 时拒绝。

        Raises:
            ValueError: 读数没有 ``t_utc``，调用方也没有提供 ``now_utc``。
                无时间的读数无法参与陈旧判定，这里不替它编一个时间。
        """
        t = self.t_utc if self.t_utc is not None else now_utc
        if t is None:
            raise ValueError(
                f"provider {self.source!r} 的读数缺少 t_utc，"
                "且 read_all 未提供 now_utc；无法确定观测时刻")
        return PoseObservation(
            source=self.source,
            t_utc=t,
            az_deg=self.az_deg,
            alt_deg=self.alt_deg,
            accuracy_deg=self.accuracy_deg,
            roll_deg=self.roll_deg,
            fov_deg=self.fov_deg,
        )


_READING_FIELDS = ("source", "az_deg", "alt_deg", "accuracy_deg",
                   "t_utc", "roll_deg", "fov_deg")


def _coerce_reading(raw: Any, name: str,
                    now_utc: Optional[float]) -> PoseObservation:
    """把 provider 返回值归一成 PoseObservation；结构不对就报错。"""
    if isinstance(raw, ProviderReading):
        return raw.to_observation(now_utc=now_utc)
    if isinstance(raw, PoseObservation):
        return raw
    if isinstance(raw, Mapping):
        kwargs = {key: raw[key] for key in _READING_FIELDS if key in raw}
        kwargs.setdefault("source", name)
        return ProviderReading(**kwargs).to_observation(now_utc=now_utc)
    raise TypeError(
        "provider 返回值必须是 ProviderReading / PoseObservation / 映射，"
        f"收到 {type(raw).__name__}")


class ProviderRegistry:
    """朴素的名字 → provider 注册表；不启动线程、不碰硬件。"""

    def __init__(self) -> None:
        self._providers: Dict[str, Any] = {}

    def register(self, name: str, provider: Any) -> None:
        """注册一个 provider；重名直接拒绝，避免静默覆盖。

        Args:
            name: 非空字符串，也是 ``read_all`` 结果里的键。
            provider: 可调用对象，或带 ``read()`` 方法的对象。

        Raises:
            ValueError: 名称为空或已注册。
            TypeError: provider 既不可调用也没有 ``read()``。
        """
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"provider 名称必须是非空字符串，收到 {name!r}")
        label = name.strip()
        if not callable(provider) and not callable(getattr(provider, "read", None)):
            raise TypeError(
                f"provider {label!r} 必须可调用或带 read() 方法，"
                f"收到 {type(provider).__name__}")
        if label in self._providers:
            raise ValueError(
                f"provider 名称 {label!r} 已注册；先 unregister 再重新注册")
        self._providers[label] = provider

    def unregister(self, name: str) -> bool:
        """注销 provider；返回是否真的删除了一个已注册项。"""
        return self._providers.pop(name, None) is not None

    def names(self) -> Tuple[str, ...]:
        """已注册的名称（按注册顺序）。"""
        return tuple(self._providers)

    def read_all(self, now_utc: Optional[float] = None) -> Dict[str, Any]:
        """逐个读取全部 provider，失败按来源隔离。

        Args:
            now_utc: 读取时刻（Unix 秒 UTC），只用于给没有 ``t_utc`` 的
                读数打时间戳。必须是有限数值或 None，否则抛 ValueError。

        Returns:
            ``{"readings": {name: PoseObservation},
            "errors": {name: {"type": str, "message": str}}}``。
            ``readings`` 的值可直接传给 ``fuse_poses``。
        """
        now: Optional[float] = None
        if now_utc is not None:
            try:
                value = float(now_utc)
            except (TypeError, ValueError):
                value = math.nan
            if not math.isfinite(value):
                raise ValueError(
                    f"now_utc 必须是有限 Unix 秒或 None，收到 {now_utc!r}")
            now = value

        readings: Dict[str, PoseObservation] = {}
        errors: Dict[str, Dict[str, str]] = {}
        for name, provider in self._providers.items():
            try:
                raw = self._invoke(provider)
                readings[name] = _coerce_reading(raw, name, now)
            except Exception as exc:  # noqa: BLE001 - 单个 provider 失败必须隔离
                errors[name] = {"type": type(exc).__name__,
                                "message": str(exc)}
        return {"readings": readings, "errors": errors}

    @staticmethod
    def _invoke(provider: Any) -> Any:
        """调用 provider：优先 read()，其次直接调用对象本身。"""
        read = getattr(provider, "read", None)
        if callable(read):
            return read()
        if callable(provider):
            return provider()
        raise TypeError(
            f"provider {type(provider).__name__} 既不可调用也没有 read()")
