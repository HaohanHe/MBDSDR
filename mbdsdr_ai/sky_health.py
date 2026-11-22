# SPDX-License-Identifier: MIT
"""AR 指向链路的健康快照（纯逻辑，无 Qt、无设备依赖）。

为什么先做「观测列表 + 解算状态」口径
------------------------------------
仓库当前还没有 provider 注册表（Task 3 未落地）。健康诊断不能等注册表：
只要调用方手里有一批带时间戳的观测、一个解算库/解算结果状态和一个相机
最后一帧时间，就足以回答「现在这条链路能信到什么级别」。本模块不探测
硬件、不读时钟、不写盘，只把输入如实折算成一份可查的快照。

级别语义（与 ``fuse_poses`` 的 ``level`` 同一套，集中在本模块一处判定）
------------------------------------------------------------------------
* ``L0``：没有可用观测（空、全部陈旧/非法），或位姿融合不可用；
* ``L1``：单源粗略位姿（典型是 IMU，方位精度 5–15°）；
* ``L2``：多源融合位姿（IMU + GNSS 时间/位置等）；
* ``L3``：星点解算成功（调用方在 ``solver_status`` 里给 ``solved=True``），
  且残差未超阈值（``solve_within_tolerance`` 不是 ``False``）；
* ``L4``：手动接管（``manual_override=True``，最高优先级）。

残差超阈值（``solve_within_tolerance=False``）时，即使解算成功也不进 L3，
按融合来源数退回 L2/L1——解算本身成功了，但这次的修正量不可信。

相机超过 ``CAMERA_NO_FRAME_S`` 秒没有新帧时，``camera_no_frame=True``；
若原本靠解算到达 L3，则**降级**回融合级别（解算所需的新帧已经不可信），
并在 ``reason`` 里说明原因。

诚实边界
--------
* 相机帧时间未提供（``None``）表示「不知道」，不是「无帧」：此时
  ``camera_no_frame=False`` 且 ``camera_age_s=None``，不拿默认值冒充；
* ``now_utc`` / 相机帧时间非法（NaN/Inf/非数值）不参与任何时间差计算，
  非法 ``now_utc`` 直接返回 ``ok=False`` 的快照，不猜一个当下时刻。
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any, Dict, List, Mapping, Optional

from .sky_pointing import fuse_poses

__all__ = [
    "CAMERA_NO_FRAME_S",
    "DEFAULT_MAX_OBS_AGE_S",
    "classify_level",
    "health_snapshot",
]

#: 相机超过该秒数没有新帧即标记「相机无帧」。
CAMERA_NO_FRAME_S: float = 5.0

#: 观测可用性阈值：与 ``fuse_poses`` 的默认 ``max_age_s`` 对齐。
DEFAULT_MAX_OBS_AGE_S: float = 2.0


def _finite(value: Any) -> Optional[float]:
    """转成有限 float；非数值/NaN/Inf 返回 None。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _solver_summary(status: Any) -> Dict[str, Any]:
    """归一化解算状态；非法输入按「不可用」如实报告，不伪造成功。"""
    if not isinstance(status, Mapping):
        return {
            "available": False,
            "solved": False,
            "name": "",
            "reason": (f"solver_status 必须是 dict，收到 {type(status).__name__}")
            if status is not None else "未提供解算库状态",
        }

    available = status.get("available")
    if available is None:
        available = status.get("ok")
    available = bool(available) if available is not None else False

    solved = any(status.get(key) is True
                 for key in ("solved", "last_solve_ok", "ok"))
    name = status.get("name") or status.get("solver") or ""
    reason = str(status.get("reason") or "")
    return {
        "available": available,
        "solved": solved and available,
        "name": str(name),
        "reason": reason,
    }


def _source_snapshots(observations: Iterable[Any],
                      now_utc: float) -> List[Dict[str, Any]]:
    """按 source 汇总：最近观测时刻、年龄与可用性（不重复计数）。"""
    latest: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for obs in observations:
        source = str(getattr(obs, "source", "") or "")
        if not source:
            source = "(未命名来源)"
        if source not in latest:
            latest[source] = {"source": source, "t_utc": None,
                              "age_s": None, "available": False,
                              "reason": ""}
            order.append(source)
        item = latest[source]
        t = _finite(getattr(obs, "t_utc", None))
        if t is None:
            if item["t_utc"] is None:
                item["reason"] = "观测时间非法（NaN/Inf/非数值）"
            continue
        # 同一来源可能有多条观测：取最新的那条判定可用性。
        if item["t_utc"] is None or t > item["t_utc"]:
            item["t_utc"] = t
            item["age_s"] = now_utc - t
            if item["age_s"] > DEFAULT_MAX_OBS_AGE_S:
                item["available"] = False
                item["reason"] = (
                    f"陈旧（age {item['age_s']:.3f}s > {DEFAULT_MAX_OBS_AGE_S}s）")
            else:
                item["available"] = True
                item["reason"] = ""
    return [latest[name] for name in order]


def classify_level(pose_valid: bool, source_count: int, solve_ok: bool,
                   manual_override: bool,
                   within_tolerance: Optional[bool] = None) -> str:
    """级别判定的唯一出处（L0–L4）；上层只消费结果，不再各写一套。

    ``within_tolerance=None`` 表示调用方没给残差信息，保持旧行为（解算成功
    即 L3）；显式 ``False`` 时解算成功也不进 L3，按来源数退回 L2/L1。
    """
    if manual_override:
        return "L4"
    if not pose_valid or source_count <= 0:
        return "L0"
    if solve_ok and within_tolerance is not False:
        return "L3"
    if source_count >= 2:
        return "L2"
    return "L1"


def _reason_for_level(level: str, sources: List[str], solve_name: str) -> str:
    if level == "L4":
        return "手动接管：当前指向以用户微调为准"
    if level == "L3":
        solver = f"（{solve_name}）" if solve_name else ""
        return f"星点解算成功{solver}"
    if level == "L2":
        return ("多源融合：" + "、".join(sources)) if sources else "多源融合"
    if level == "L1":
        who = sources[0] if sources else "单一来源"
        return f"单源粗略位姿（{who}），未经融合或解算"
    return "没有可用观测，当前指向不可信"


def _runtime_block(value: Any) -> Optional[Dict[str, Any]]:
    """运行时数据源快照透传：只接受 Mapping，原样复制，不另造第二份。"""
    if isinstance(value, Mapping):
        return dict(value)
    return None


def health_snapshot(observations: Iterable[Any],
                    now_utc: float,
                    solver_status: Any,
                    camera_last_frame_utc: Optional[float] = None,
                    *,
                    manual_override: bool = False,
                    last_error: Optional[str] = None,
                    runtime_sources: Optional[Mapping[str, Any]] = None,
                    solve_within_tolerance: Optional[bool] = None
                    ) -> Dict[str, Any]:
    """生成当前 AR 指向链路的健康快照。

    Args:
        observations: 带 ``source`` / ``t_utc`` 的观测序列（``PoseObservation``
            或同形对象）。空序列 → L0。
        now_utc: 当前 Unix 秒（UTC），用于计算观测年龄与相机无帧时长。
        solver_status: 解算状态。通常是 ``sky_solve.solver_status()`` 的
            返回 dict；若要表达「最近一次解算成功」，在该 dict 里给
            ``solved=True``（或 ``last_solve_ok=True`` / ``ok=True``）。
        camera_last_frame_utc: 相机最后一帧的 Unix 秒；``None`` 表示调用方
            未提供，按「未知」处理，不冒充无帧。
        manual_override: 用户是否已手动接管指向；True → L4。
        last_error: 调用方持有的最近一次错误文本；本模块原样透传。
        runtime_sources: 可选的 ``SDRController.get_runtime_sources()`` 快照；
            原样放进结果的 ``runtime_sources`` 字段，本模块不另造时间/站址。
        solve_within_tolerance: 最近一次解算的残差是否在阈值内。``False`` 时
            即使 ``solved=True`` 也不报 L3；``None`` 表示调用方没提供残差
            信息，保持旧行为（解算成功即 L3）。

    Returns:
        dict 字段：

        - ``ok``：输入是否可用于判定（``now_utc``/观测序列非法时 False）；
        - ``level``：``L0``–``L4``；
        - ``sources``：每个来源的 ``source`` / ``t_utc`` / ``age_s`` /
          ``available`` / ``reason``；
        - ``pose_valid`` / ``pose_reason``：融合位姿是否可用及原因；
        - ``solver``：``available`` / ``solved`` / ``name`` / ``reason``；
        - ``camera_last_frame_utc`` / ``camera_age_s`` / ``camera_no_frame``；
        - ``last_error``：调用方传入的最近错误（原样）；
        - ``runtime_sources``：调用方传入的运行时数据源快照（原样；未提供为 None）；
        - ``reason``：当前级别的人话说明（含降级原因）。
    """
    solver = _solver_summary(solver_status)
    runtime = _runtime_block(runtime_sources)

    now = _finite(now_utc)
    if now is None:
        reason = f"now_utc 非法（需要有限 Unix 秒），收到 {now_utc!r}"
        return {
            "ok": False,
            "level": "L0",
            "sources": [],
            "pose_valid": False,
            "pose_reason": reason,
            "solver": solver,
            "camera_last_frame_utc": _finite(camera_last_frame_utc),
            "camera_age_s": None,
            "camera_no_frame": False,
            "last_error": last_error,
            "runtime_sources": runtime,
            "reason": reason,
        }

    if (isinstance(observations, (str, bytes, dict))
            or not isinstance(observations, Iterable)):
        reason = ("observations 必须是观测序列，"
                  f"收到 {type(observations).__name__}")
        return {
            "ok": False,
            "level": "L0",
            "sources": [],
            "pose_valid": False,
            "pose_reason": reason,
            "solver": solver,
            "camera_last_frame_utc": _finite(camera_last_frame_utc),
            "camera_age_s": None,
            "camera_no_frame": False,
            "last_error": last_error,
            "runtime_sources": runtime,
            "reason": reason,
        }

    items = list(observations)
    sources = _source_snapshots(items, now)
    pose = fuse_poses(items, now)
    pose_valid = bool(pose.get("valid"))
    pose_sources = [str(s) for s in pose.get("sources") or []]

    camera_t = _finite(camera_last_frame_utc)
    camera_age: Optional[float] = None
    camera_reason = ""
    if camera_last_frame_utc is None:
        camera_reason = "相机帧时间未提供，无法判定是否无帧"
    elif camera_t is None:
        camera_reason = "相机帧时间非法（NaN/Inf/非数值），无法判定是否无帧"
    else:
        camera_age = now - camera_t
    camera_no_frame = camera_age is not None and camera_age > CAMERA_NO_FRAME_S

    solve_ok = solver["solved"]
    degraded = ""
    if camera_no_frame and solve_ok and not manual_override:
        # 相机已经断帧：上一次解算结果不再代表当前画面，不能继续报 L3。
        solve_ok = False
        degraded = (f"；相机超过 {CAMERA_NO_FRAME_S:g}s 无帧，"
                    "星点解算不可用，级别按融合结果降级")
    if (solve_within_tolerance is False and solver["solved"]
            and not manual_override):
        # 解算成功但残差超阈值：修正量不可信，不提升到 L3。
        degraded += "；残差超阈值，级别按融合结果降级"

    level = classify_level(pose_valid, len(pose_sources), solve_ok,
                           bool(manual_override),
                           within_tolerance=solve_within_tolerance)
    if pose_valid or level == "L4":
        reason = _reason_for_level(level, pose_sources, str(solver["name"]))
    else:
        reason = str(pose.get("reason") or _reason_for_level("L0", [], ""))
    if degraded:
        reason += degraded
    if camera_reason:
        reason += f"；{camera_reason}"

    return {
        "ok": True,
        "level": level,
        "sources": sources,
        "pose_valid": pose_valid,
        "pose_reason": str(pose.get("reason") or ""),
        "solver": solver,
        "camera_last_frame_utc": camera_t,
        "camera_age_s": camera_age,
        "camera_no_frame": camera_no_frame,
        "last_error": last_error,
        "runtime_sources": runtime,
        "reason": reason,
    }
