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
* ``ProviderRegistry.register(name, provider, *, capabilities=(), max_age_s=None)``：
  provider 可以是可调用对象，也可以是带 ``read()`` 方法的对象（两者都有时
  优先 ``read()``）；``capabilities`` 是该来源的能力标签，``max_age_s``
  是读数最大允许年龄（秒，``None`` = 不做过期判定）；
* ``replace(name, provider, *, capabilities=None, max_age_s=None)``：替换已
  注册 provider；``None`` 表示沿用原配置；
* ``unregister(name)``：注销并返回是否真的删除了；
* ``names(capability=None)``：当前已注册的名称（按注册顺序）；给
  ``capability`` 时只返回具备该能力的名称；
* ``capabilities(name)``：该名称注册的能力标签（按注册顺序）；
* ``read_all(now_utc=None)``：逐个读取，返回
  ``{"readings": {name: PoseObservation}, "errors": {name: {"type", "message"}}}``。
  某个 provider 抛异常只写进它自己的 ``errors`` 条目，绝不会中断其它
  provider，也不会让整个调用抛异常。配置了 ``max_age_s`` 且读数年龄超限
  时进 ``errors``（``type`` 为 ``StaleReading``）；读数时间晚于 ``now_utc``
  时不改时间戳，只在 ``warnings``（``code`` 为 ``clock_skew``）里说明——
  该键仅在确有警告时出现。

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
from collections.abc import Iterable, Mapping
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


def _validate_capabilities(capabilities: Iterable[str]) -> Tuple[str, ...]:
    """校验能力标签：每项必须是非空字符串，保留注册顺序。"""
    if isinstance(capabilities, (str, bytes)):
        raise ValueError(
            f"capabilities 必须是字符串序列，不能是单个字符串 {capabilities!r}")
    try:
        items = list(capabilities)
    except TypeError:
        raise ValueError(
            f"capabilities 必须是字符串序列，收到 {capabilities!r}") from None
    labels = []
    for cap in items:
        if not isinstance(cap, str) or not cap.strip():
            raise ValueError(f"capability 必须是非空字符串，收到 {cap!r}")
        labels.append(cap.strip())
    return tuple(labels)


def _validate_max_age(max_age_s: Optional[float]) -> Optional[float]:
    """``max_age_s`` 必须是有限正数或 None。"""
    if max_age_s is None:
        return None
    try:
        age = float(max_age_s)
    except (TypeError, ValueError):
        age = math.nan
    if not math.isfinite(age) or age <= 0.0:
        raise ValueError(
            f"max_age_s 必须是有限正数或 None，收到 {max_age_s!r}")
    return age


def _validate_provider(label: str, provider: Any) -> None:
    if not callable(provider) and not callable(getattr(provider, "read", None)):
        raise TypeError(
            f"provider {label!r} 必须可调用或带 read() 方法，"
            f"收到 {type(provider).__name__}")


def _check_source(source: Any, name: str) -> None:
    """读数自报来源必须是非空字符串，且不得与注册名不一致。"""
    if not isinstance(source, str) or not source.strip():
        raise TypeError(f"读数 source 必须是非空字符串，收到 {source!r}")
    if source != name:
        raise ValueError(
            f"读数 source {source!r} 与注册名 {name!r} 不一致；不静默改名")


def _coerce_reading(raw: Any, name: str,
                    now_utc: Optional[float]) -> PoseObservation:
    """把 provider 返回值归一成 PoseObservation；结构不对就报错。"""
    if isinstance(raw, ProviderReading):
        _check_source(raw.source, name)
        return raw.to_observation(now_utc=now_utc)
    if isinstance(raw, PoseObservation):
        _check_source(raw.source, name)
        return raw
    if isinstance(raw, Mapping):
        kwargs = {key: raw[key] for key in _READING_FIELDS if key in raw}
        kwargs.setdefault("source", name)
        _check_source(kwargs["source"], name)
        return ProviderReading(**kwargs).to_observation(now_utc=now_utc)
    raise TypeError(
        "provider 返回值必须是 ProviderReading / PoseObservation / 映射，"
        f"收到 {type(raw).__name__}")


class ProviderRegistry:
    """朴素的名字 → provider 注册表；不启动线程、不碰硬件。"""

    def __init__(self) -> None:
        self._providers: Dict[str, Any] = {}
        self._capabilities: Dict[str, Tuple[str, ...]] = {}
        self._max_age: Dict[str, Optional[float]] = {}

    def register(self, name: str, provider: Any, *,
                 capabilities: Iterable[str] = (),
                 max_age_s: Optional[float] = None) -> None:
        """注册一个 provider；重名直接拒绝，避免静默覆盖。

        Args:
            name: 非空字符串，也是 ``read_all`` 结果里的键。
            provider: 可调用对象，或带 ``read()`` 方法的对象。
            capabilities: 能力标签序列，每项必须是非空字符串。
            max_age_s: 读数最大允许年龄（秒）；有限正数或 None。

        Raises:
            ValueError: 名称为空/已注册、能力标签非法、``max_age_s`` 非法。
            TypeError: provider 既不可调用也没有 ``read()``。
        """
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"provider 名称必须是非空字符串，收到 {name!r}")
        label = name.strip()
        _validate_provider(label, provider)
        caps = _validate_capabilities(capabilities)
        max_age = _validate_max_age(max_age_s)
        if label in self._providers:
            raise ValueError(
                f"provider 名称 {label!r} 已注册；先 unregister 再重新注册")
        self._providers[label] = provider
        self._capabilities[label] = caps
        self._max_age[label] = max_age

    def replace(self, name: str, provider: Any, *,
                capabilities: Optional[Iterable[str]] = None,
                max_age_s: Optional[float] = None) -> None:
        """替换已注册 provider；``None`` 表示沿用原配置。

        Raises:
            KeyError: 名称未注册。
            ValueError: 能力标签或 ``max_age_s`` 非法。
            TypeError: provider 既不可调用也没有 ``read()``。
        """
        label = name.strip() if isinstance(name, str) else name
        if label not in self._providers:
            raise KeyError(f"provider 名称 {name!r} 未注册，无法替换")
        _validate_provider(label, provider)
        caps = (self._capabilities[label] if capabilities is None
                else _validate_capabilities(capabilities))
        max_age = (self._max_age[label] if max_age_s is None
                   else _validate_max_age(max_age_s))
        self._providers[label] = provider
        self._capabilities[label] = caps
        self._max_age[label] = max_age

    def unregister(self, name: str) -> bool:
        """注销 provider；返回是否真的删除了一个已注册项。"""
        if self._providers.pop(name, None) is None:
            return False
        self._capabilities.pop(name, None)
        self._max_age.pop(name, None)
        return True

    def names(self, capability: Optional[str] = None) -> Tuple[str, ...]:
        """已注册的名称（按注册顺序）；可按能力过滤。"""
        if capability is None:
            return tuple(self._providers)
        if not isinstance(capability, str):
            return ()
        cap = capability.strip()
        return tuple(name for name in self._providers
                     if cap in self._capabilities[name])

    def capabilities(self, name: str) -> Tuple[str, ...]:
        """该名称注册的能力标签；名称未注册时抛 KeyError。"""
        label = name.strip() if isinstance(name, str) else name
        if label not in self._capabilities:
            raise KeyError(f"provider 名称 {name!r} 未注册")
        return self._capabilities[label]

    def read_all(self, now_utc: Optional[float] = None) -> Dict[str, Any]:
        """逐个读取全部 provider，失败按来源隔离。

        Args:
            now_utc: 读取时刻（Unix 秒 UTC），用于给没有 ``t_utc`` 的
                读数打时间戳，并判定陈旧/时钟偏移。必须是有限数值或 None，
                否则抛 ValueError。

        Returns:
            ``{"readings": {name: PoseObservation},
            "errors": {name: {"type": str, "message": str}}}``，仅有警告时
            额外含 ``"warnings": {name: {"code", "message"}}``。
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
        warnings: Dict[str, Dict[str, str]] = {}
        for name, provider in self._providers.items():
            try:
                raw = self._invoke(provider)
                obs = _coerce_reading(raw, name, now)
                if now is not None:
                    if obs.t_utc > now:
                        warnings[name] = {
                            "code": "clock_skew",
                            "message": (
                                f"读数 t_utc={obs.t_utc!r} 晚于 "
                                f"now_utc={now!r}；保留原时间戳，请检查时钟"),
                        }
                        readings[name] = obs
                        continue
                    max_age = self._max_age.get(name)
                    if max_age is not None:
                        age = now - obs.t_utc
                        if age > max_age:
                            errors[name] = {
                                "type": "StaleReading",
                                "message": (
                                    f"读数年龄 {age:.3f}s 超过 "
                                    f"max_age_s={max_age:g}s"),
                            }
                            continue
                readings[name] = obs
            except Exception as exc:  # noqa: BLE001 - 单个 provider 失败必须隔离
                errors[name] = {"type": type(exc).__name__,
                                "message": str(exc)}
        result: Dict[str, Any] = {"readings": readings, "errors": errors}
        if warnings:
            result["warnings"] = warnings
        return result

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
