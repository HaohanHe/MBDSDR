# SPDX-License-Identifier: MIT
"""望远镜/手机指向的传感器位姿融合与「指向即识别」。

解决的问题
----------
GNSS 只给时间与位置、不给航向；IMU 给方位/俯仰/横滚，但户外方位精度常态
5–15°（近金属可 >30°）。所以「镜头指向哪」不能信单一读数，也不能在没有
可信读数时猜一个朝向。本模块只做两件纯逻辑的事：

1. ``fuse_poses`` / ``view_params``：把多条带时间戳与精度的观测融合成
   可直接交给 ``ViewState`` 的位姿参数，并逐条说明哪些观测被丢弃、为什么；
2. ``identify_direction``：给定地平指向，回答这一片天里肉眼可见的恒星与
   所属中国星官，直接复用随仓星表与星官表。

坐标口径
--------
输入/输出位姿全部是**地平坐标**（az 北=0 东=90，alt 地平=0，单位度）。
``identify_direction`` 的 az/alt → 星表 J2000 RA/Dec 换算全部走
``skyengine.star_catalog.select_for_view`` 内部的既有时空链
（``celestial_geometry`` 的岁差/章动/站心矩阵），本模块不另造历元，
也不自行实现黄道/赤道变换。

诚实语义
--------
* 全部观测陈旧/低精度/非法 → ``valid=False`` + 具体 ``reason``，绝不猜朝向；
* 方位用**圆均值**，俯仰/横滚按任务口径用加权均值；
* 融合后的 ``accuracy_deg`` 是统计标准误 ``1/sqrt(Σw)``，不是保证误差；
* 识别没有目标就是 ``matched=False``，不拿最近邻硬凑。

与 UI 的关系
------------
本模块不 import Qt、不碰 ``desktop/main_window.py``；``view_params`` 的字段名
与 ``sky_interaction.ViewState`` 的 ``look_at`` / ``zoom_to`` / ``rotation``
参数一一对应，由调用方负责套用。
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

__all__ = [
    "PoseObservation",
    "apply_calibration",
    "fuse_poses",
    "view_params",
    "identify_direction",
]


@dataclass(frozen=True)
class PoseObservation:
    """一条传感器位姿观测（provider 的输出，不绑定具体设备）。

    Attributes:
        source: 来源标签，如 ``"imu"`` / ``"gnss"`` / ``"camera"``。
        t_utc: 观测时刻的 Unix 秒（UTC）。
        az_deg: 方位角（度，北=0 东=90，顺时针为正）。
        alt_deg: 地平高度（度，地平线=0，天顶=90）。
        accuracy_deg: 该观测自报的 1σ 精度（度），必须为正。
        roll_deg: 画面横滚（度），默认 0。
        fov_deg: 该观测给出的视场直径（度）；未知为 None。
    """

    source: str
    t_utc: float
    az_deg: float
    alt_deg: float
    accuracy_deg: float
    roll_deg: float = 0.0
    fov_deg: Optional[float] = None


def _as_finite(value: Any) -> Optional[float]:
    """转成有限 float；非数值/NaN/Inf 返回 None（调用方负责给原因）。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _invalid_pose(reason: str,
                  dropped: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """不可用位姿的统一信封：字段齐全但不含任何猜测的朝向。"""
    return {
        "valid": False,
        "az_deg": None,
        "alt_deg": None,
        "roll_deg": None,
        "fov_deg": None,
        "accuracy_deg": None,
        "sources": [],
        "dropped": list(dropped or []),
        "level": "none",
        "reason": reason,
    }


def fuse_poses(observations: Iterable[PoseObservation],
               now_utc: float,
               max_age_s: float = 2.0,
               max_accuracy_deg: float = 20.0) -> Dict[str, Any]:
    """融合多条位姿观测，输出现有 ViewState 可直接消费的位姿。

    权重取 ``1/accuracy_deg**2``；方位用圆均值（跨 0°/360° 正确），
    俯仰/横滚用加权算术均值。观测在参与融合前逐条体检，任何一条被拒都
    写进 ``dropped``（含来源与具体原因）。

    Args:
        observations: ``PoseObservation`` 序列；空序列 → ``valid=False``。
        now_utc: 当前 Unix 秒，用于判定陈旧（``now - t > max_age_s``）。
        max_age_s: 允许的最大观测年龄（秒）。
        max_accuracy_deg: 允许的最大自报精度（度），超过即视为低精度丢弃。

    Returns:
        dict 字段：``valid`` / ``az_deg`` / ``alt_deg`` / ``roll_deg`` /
        ``fov_deg`` / ``accuracy_deg`` / ``sources`` / ``dropped`` /
        ``level`` / ``reason``。

        - ``valid=True`` 时朝向字段为数值，``level`` 为 ``"single"`` 或
          ``"fused"``，``reason`` 为空串；
        - ``valid=False`` 时朝向字段一律为 None，``reason`` 说明具体原因
          （没有观测、全部陈旧、全部低精度、或融合退化）。

    注意：不处理「未来时间戳」——它不陈旧；时钟回拨属于 provider 的
    时间对齐责任，这里不静默修正时间。
    """
    if (isinstance(observations, (str, bytes, dict))
            or not isinstance(observations, Iterable)):
        return _invalid_pose("observations 必须是 PoseObservation 序列，"
                             f"收到 {type(observations).__name__}")

    now = _as_finite(now_utc)
    if now is None:
        return _invalid_pose(f"now_utc 非法（需要有限 Unix 秒），收到 {now_utc!r}")

    age_limit = _as_finite(max_age_s)
    if age_limit is None or age_limit < 0.0:
        return _invalid_pose(
            f"max_age_s 非法（需要 >= 0 的有限秒数），收到 {max_age_s!r}")

    acc_limit = _as_finite(max_accuracy_deg)
    if acc_limit is None or acc_limit <= 0.0:
        return _invalid_pose(
            "max_accuracy_deg 非法（需要 > 0 的有限度数），"
            f"收到 {max_accuracy_deg!r}")

    used: List[PoseObservation] = []
    weights: List[float] = []
    dropped: List[Dict[str, Any]] = []

    def drop(obs: Any, reason: str) -> None:
        dropped.append({
            "source": str(getattr(obs, "source", "") or ""),
            "t_utc": getattr(obs, "t_utc", None),
            "reason": reason,
        })

    for obs in observations:
        if not isinstance(obs, PoseObservation):
            drop(obs, f"不是 PoseObservation（收到 {type(obs).__name__}）")
            continue

        t = _as_finite(obs.t_utc)
        az = _as_finite(obs.az_deg)
        alt = _as_finite(obs.alt_deg)
        roll = _as_finite(obs.roll_deg)
        acc = _as_finite(obs.accuracy_deg)
        fov = None if obs.fov_deg is None else _as_finite(obs.fov_deg)

        if t is None or az is None or alt is None or roll is None or acc is None:
            drop(obs, "数值非法（含 NaN/Inf 或非数值）")
            continue
        if obs.fov_deg is not None and fov is None:
            drop(obs, f"fov_deg 非法（需要有限正数），收到 {obs.fov_deg!r}")
            continue
        if fov is not None and fov <= 0.0:
            drop(obs, f"fov_deg 非正（{fov}）")
            continue
        if not (-90.0 <= alt <= 90.0):
            drop(obs, f"alt 越界（{alt}°，合法范围 -90..90）")
            continue
        if acc <= 0.0:
            drop(obs, f"accuracy_deg 非正（{acc}°）")
            continue

        # 陈旧判定按墙钟年龄；未来时间戳不算陈旧（见 docstring）。
        age = now - t
        if age > age_limit:
            drop(obs, f"陈旧（age {age:.3f}s > {age_limit}s）")
            continue
        if acc > acc_limit:
            drop(obs, f"低精度（accuracy {acc}° > 上限 {acc_limit}°）")
            continue

        weight = 1.0 / (acc * acc)
        if not math.isfinite(weight) or weight <= 0.0:
            # 极小/极大 accuracy 会让权重溢出成 inf 或下溢成 0，加权失去意义。
            drop(obs, f"accuracy_deg 数值极端（{acc}°），权重无意义")
            continue
        used.append(obs)
        weights.append(weight)

    if not used:
        if not dropped:
            return _invalid_pose("没有可用观测：observations 为空")
        reasons: List[str] = []
        for item in dropped:
            if item["reason"] not in reasons:
                reasons.append(item["reason"])
        return _invalid_pose(
            f"没有可用观测：{len(dropped)} 条全部被丢弃（"
            + "；".join(reasons) + "）", dropped)

    total_w = sum(weights)
    sin_sum = 0.0
    cos_sum = 0.0
    alt_sum = 0.0
    roll_sum = 0.0
    fov_sum = 0.0
    fov_w = 0.0
    for obs, w in zip(used, weights):
        az_r = math.radians(float(obs.az_deg))
        sin_sum += w * math.sin(az_r)
        cos_sum += w * math.cos(az_r)
        alt_sum += w * float(obs.alt_deg)
        roll_sum += w * float(obs.roll_deg)
        if obs.fov_deg is not None:
            fov_sum += w * float(obs.fov_deg)
            fov_w += w

    # 合矢量长度趋零说明方位读数彼此抵消（例如三条相隔 120° 的等权观测），
    # 此时圆均值没有定义——宁可判不可用，也不返回一个任意方位。
    resultant = math.hypot(sin_sum, cos_sum)
    if resultant <= 1e-12 * total_w:
        return _invalid_pose(
            "方位观测相互抵消，圆均值无定义；无法给出可信朝向", dropped)

    az_deg = math.degrees(math.atan2(sin_sum, cos_sum)) % 360.0
    sources: List[str] = []
    for obs in used:
        label = str(obs.source or "")
        if label not in sources:
            sources.append(label)

    return {
        "valid": True,
        "az_deg": az_deg,
        "alt_deg": alt_sum / total_w,
        "roll_deg": roll_sum / total_w,
        "fov_deg": (fov_sum / fov_w) if fov_w > 0.0 else None,
        "accuracy_deg": 1.0 / math.sqrt(total_w),
        "sources": sources,
        "dropped": dropped,
        "level": "single" if len(used) == 1 else "fused",
        "reason": "",
    }


def view_params(pose: Dict[str, Any],
                fov_deg: Optional[float] = None,
                min_fov: Optional[float] = None) -> Dict[str, Any]:
    """把 ``fuse_poses`` 的结果转成 ViewState 可直接套用的参数。

    Args:
        pose: ``fuse_poses`` 的返回 dict。
        fov_deg: 显式覆盖视场（度）；None 时用位姿里的 ``fov_deg``。
            两者都没有就判不可用——不猜一个默认视场。
        min_fov: 目标视图的视场下限（度），合法范围 0.5..180；None 时用
            ``ViewState`` 的默认下限 30°。目镜级目标视图应传 0.5（或
            与目镜实际视场匹配的下限），避免 2.3° 被桌面口径误夹成 30°。

    Returns:
        ``valid=True`` 时含 ``center_az`` / ``center_alt`` / ``fov_deg`` /
        ``rotation_deg``；``fov_deg`` 已按**目标视图**的 min_fov..180 夹取，
        原始请求值另存 ``fov_requested_deg``，夹取与否见 ``fov_clamped``。
        ``valid=False`` 时返回 ``{"valid": False, "reason": ...}``。
    """
    if not isinstance(pose, dict):
        return {"valid": False,
                "reason": f"pose 必须是 fuse_poses 的 dict，收到 {type(pose).__name__}"}
    if not pose.get("valid"):
        reason = str(pose.get("reason") or "位姿不可用")
        return {"valid": False, "reason": reason}

    az = _as_finite(pose.get("az_deg"))
    alt = _as_finite(pose.get("alt_deg"))
    roll = _as_finite(pose.get("roll_deg", 0.0))
    if az is None or alt is None or roll is None:
        return {"valid": False, "reason": "位姿字段非法（az/alt/roll 非有限）"}
    if not (-90.0 <= alt <= 90.0):
        return {"valid": False,
                "reason": f"位姿 alt 越界（{alt}°，合法范围 -90..90）"}

    raw_fov = fov_deg if fov_deg is not None else pose.get("fov_deg")
    if raw_fov is None:
        return {"valid": False,
                "reason": "缺少 fov_deg：位姿观测与调用参数都未提供视场"}
    fov = _as_finite(raw_fov)
    if fov is None or fov <= 0.0:
        return {"valid": False,
                "reason": f"fov_deg 非法（需要有限正数），收到 {raw_fov!r}"}

    # ViewState 自己会夹到 [min_fov, max_fov]；这里先按**目标视图**口径夹，
    # 保证返回的就是套用后真正生效的值，而不是让调用方以为 2.3° 目镜视场
    # 被原样接受。
    from .sky_interaction import DEFAULT_MAX_FOV_DEG, DEFAULT_MIN_FOV_DEG

    if min_fov is None:
        min_fov_v = float(DEFAULT_MIN_FOV_DEG)
    else:
        min_fov_v = _as_finite(min_fov)
        if min_fov_v is None or not (0.5 <= min_fov_v <= 180.0):
            return {"valid": False,
                    "reason": (f"min_fov 非法（需要 0.5..180 的有限度数），"
                               f"收到 {min_fov!r}")}

    clamped = max(min_fov_v, min(float(DEFAULT_MAX_FOV_DEG), fov))
    return {
        "valid": True,
        "center_az": az % 360.0,
        "center_alt": alt,
        "fov_deg": clamped,
        "rotation_deg": roll % 360.0,
        "fov_requested_deg": fov,
        "fov_clamped": abs(clamped - fov) > 1e-12,
    }


def apply_calibration(pose: Dict[str, Any],
                      calibration: Dict[str, Any]) -> Dict[str, Any]:
    """把校准档案（schema v1）的偏置叠加到一帧位姿上。

    档案语义（见 ``sky_calibration``）：``boresight_az_deg`` /
    ``boresight_alt_deg`` / ``roll_zero_deg`` 是**加在读数上的修正量**，
    不是绝对指向；``fov_deg`` 是该校准对应的视场。本函数只做纯数值
    叠加，不读写文件、不 import Qt。

    叠加口径：
    * ``az = (az + boresight_az_deg) % 360``（跨 0° 正确环绕）；
    * ``alt = alt + boresight_alt_deg``，越界（超出 -90..90）直接拒绝；
    * ``roll = (roll + roll_zero_deg) % 360``；
    * ``fov`` 一律取档案的 ``fov_deg``（覆盖位姿里的视场）。

    调用方把结果交给 ``view_params`` 时应传 ``min_fov=结果["fov_deg"]``，
    否则 2.3° 这类目镜级窄视场会被桌面默认的 30° 下限夹掉——
    ``RFSkyView`` 的校准路径已经这样接线。

    Args:
        pose: ``fuse_poses`` 的输出（或至少含 ``az_deg``/``alt_deg`` 的
            dict）；``valid is False`` 或数值非法都判不可用。
        calibration: 校准档案 dict，至少含 ``fov_deg`` /
            ``boresight_az_deg`` / ``boresight_alt_deg`` / ``roll_zero_deg``；
            带 ``schema_version`` 时必须等于当前支持版本。

    Returns:
        成功：输入位姿的副本（**不修改入参**），朝向/视场已叠加校准，
        ``valid=True`` 且 ``calibration_applied=True``。
        失败：``valid=False`` + ``error``（``"bad_pose"`` /
        ``"bad_calibration"``）+ 具体 ``reason``，朝向字段一律为 None，
        不返回任何猜测值。
    """
    def invalid(reason: str, error: str) -> Dict[str, Any]:
        out = _invalid_pose(reason)
        out["error"] = error
        out["calibration_applied"] = False
        return out

    if not isinstance(pose, Mapping):
        return invalid(
            f"pose 必须是 fuse_poses 的 dict，收到 {type(pose).__name__}",
            "bad_pose")
    if pose.get("valid") is False:
        return invalid(str(pose.get("reason") or "位姿不可用"), "bad_pose")

    az = _as_finite(pose.get("az_deg"))
    alt = _as_finite(pose.get("alt_deg"))
    roll = _as_finite(pose.get("roll_deg", 0.0))
    if az is None or alt is None or roll is None:
        return invalid("位姿字段非法（az/alt/roll 非有限）", "bad_pose")
    if not (-90.0 <= alt <= 90.0):
        return invalid(f"位姿 alt 越界（{alt}°，合法范围 -90..90）",
                       "bad_pose")

    if not isinstance(calibration, Mapping):
        return invalid(
            f"校准档案必须是 dict，收到 {type(calibration).__name__}",
            "bad_calibration")

    if "schema_version" in calibration:
        from .sky_calibration import SCHEMA_VERSION

        version = calibration.get("schema_version")
        if isinstance(version, bool) or not isinstance(version, int) \
                or version != SCHEMA_VERSION:
            return invalid(
                f"不支持的校准档案版本 {version!r}"
                f"（当前支持 {SCHEMA_VERSION}）", "bad_calibration")

    required = ("fov_deg", "boresight_az_deg", "boresight_alt_deg",
                "roll_zero_deg")
    missing = [key for key in required if key not in calibration]
    if missing:
        return invalid(f"校准档案缺少字段：{', '.join(missing)}",
                       "bad_calibration")

    fov = _as_finite(calibration.get("fov_deg"))
    az_off = _as_finite(calibration.get("boresight_az_deg"))
    alt_off = _as_finite(calibration.get("boresight_alt_deg"))
    roll_zero = _as_finite(calibration.get("roll_zero_deg"))
    if None in (fov, az_off, alt_off, roll_zero):
        return invalid("校准档案的偏置/视场必须是有限数值", "bad_calibration")

    # 注意：桌面的 ``DEFAULT_MIN_FOV_DEG``（30°）只是默认视图下限，不是
    # view_params 允许的最小值。校准路径会把档案视场同时当作目标视图的
    # min_fov，故这里只需检查 view_params 的绝对下限 0.5°。
    min_view_fov = 0.5

    if fov is None or not (0.0 < fov <= 180.0):
        return invalid(f"校准档案的 fov_deg 非法（需要 (0, 180] 度），"
                       f"收到 {calibration.get('fov_deg')!r}",
                       "bad_calibration")
    if fov < min_view_fov:
        return invalid(
            f"校准档案的 fov_deg={fov}° 小于视图可表达下限 "
            f"{min_view_fov}°，无法在目标视图表达", "bad_calibration")
    if not (-90.0 <= alt_off <= 90.0):
        return invalid(
            f"校准档案的 boresight_alt_deg 越界（{alt_off}°，合法范围 -90..90）",
            "bad_calibration")

    new_alt = alt + alt_off
    if not (-90.0 <= new_alt <= 90.0):
        return invalid(
            f"校准后俯仰越界（{alt}° + {alt_off}° = {new_alt}°，"
            "合法范围 -90..90）", "bad_calibration")

    out = dict(pose)
    out.update({
        "valid": True,
        "az_deg": (az + az_off) % 360.0,
        "alt_deg": new_alt,
        "roll_deg": (roll + roll_zero) % 360.0,
        "fov_deg": fov,
        "calibration_applied": True,
    })
    out.pop("error", None)
    return out


@lru_cache(maxsize=1)
def _hip_to_asterisms() -> Dict[int, Tuple[str, ...]]:
    """HIP → 所属星官中文名（只读缓存，遍历随仓星官表，不另造数据）。"""
    from .skyengine.xingguan import get_xingguan

    out: Dict[int, List[str]] = {}
    for region in get_xingguan().regions:
        for ast in region.xingguan:
            for star in ast.stars:
                out.setdefault(int(star.hip), []).append(ast.name_zh)
            for poly in ast.lines:
                for star in poly:
                    out.setdefault(int(star.hip), []).append(ast.name_zh)
    deduped: Dict[int, Tuple[str, ...]] = {}
    for hip, names in out.items():
        seen: List[str] = []
        for name in names:
            if name not in seen:
                seen.append(name)
        deduped[hip] = tuple(seen)
    return deduped


def _invalid_query(az_deg: Any, alt_deg: Any, fov_deg: Any,
                   reason: str) -> Dict[str, Any]:
    return {
        "matched": False,
        "reason": reason,
        "query": {"az_deg": az_deg, "alt_deg": alt_deg,
                  "fov_deg": fov_deg},
        "stars": [],
    }


def identify_direction(az_deg: float, alt_deg: float, fov_deg: float,
                       jd: float, lat_deg: float, lon_deg: float,
                       limit: int = 3,
                       culture_ids: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """识别某个地平指向附近肉眼可见的恒星与所属中国星官。

    坐标层说明：输入是**地平 az/alt**；星表存 J2000 RA/Dec。两者的换算
    完全交给 ``skyengine.star_catalog.select_for_view``（内部走
    ``celestial_geometry`` 的岁差/章动与 ``GroundStation`` 站心矩阵），
    本模块只做视锥内角距计算与星官关联，不自行实现历元/黄道变换。

    Args:
        az_deg / alt_deg: 指向（度，北=0 东=90，alt 地平以上为正）。
        fov_deg: 视场直径（度）；只返回落在 fov/2 内的星。
        jd: UT1 儒略日（与工具面同一口径）。
        lat_deg / lon_deg: 观测站纬度/经度（度）。
        limit: 最多返回几颗（按星等从亮到暗取）。
        culture_ids: 可选的文化 id 序列（如 ``"iau_88"`` /
            ``"chinese_xingguan"``）。传入时每颗星多一个 ``culture_names``：
            ``{culture_id: {lang: name}}``，只含请求且**该 HIP 确有名字**的
            文化——缺名的文化不会出现该键，也绝不回退成别家名字。不传时
            输出与旧版逐字一致（不新增任何键）。未知 id 被忽略，并在顶层
            ``unknown_cultures`` 如实列出。

    Returns:
        命中：``matched=True``、``stars``（每项含 ``name`` / ``hip`` /
        ``con`` / ``vmag`` / ``angular_distance_deg`` / ``az_deg`` /
        ``alt_deg`` / ``asterisms``）、``total_matched``、``truncated``、
        ``limit_mag``（裸眼极限）与 ``catalog`` 来源信息。
        没有星落进视锥：``matched=False`` + ``reason``，不返回最近邻凑数。
    """
    az = _as_finite(az_deg)
    alt = _as_finite(alt_deg)
    fov = _as_finite(fov_deg)
    jd_v = _as_finite(jd)
    lat = _as_finite(lat_deg)
    lon = _as_finite(lon_deg)

    if az is None or alt is None:
        return _invalid_query(az_deg, alt_deg, fov_deg,
                              "输入非法：az_deg / alt_deg 必须是有限数值")
    if not (-90.0 <= alt <= 90.0):
        return _invalid_query(az, alt, fov_deg,
                              f"输入非法：alt_deg 越界（{alt}°，合法 -90..90）")
    if fov is None or not (0.0 < fov <= 180.0):
        return _invalid_query(az, alt, fov_deg,
                              f"输入非法：fov_deg 必须在 (0, 180] 度内，收到 {fov_deg!r}")
    if jd_v is None:
        return _invalid_query(az, alt, fov,
                              "输入非法：jd 必须是有限的 UT1 儒略日")
    if lat is None or not (-90.0 <= lat <= 90.0):
        return _invalid_query(az, alt, fov,
                              f"输入非法：lat_deg 越界（{lat_deg!r}）")
    if lon is None or not (-360.0 <= lon <= 360.0):
        return _invalid_query(az, alt, fov,
                              f"输入非法：lon_deg 越界（{lon_deg!r}）")
    try:
        limit_n = int(limit)
    except (TypeError, ValueError):
        return _invalid_query(az, alt, fov,
                              f"输入非法：limit 必须是正整数，收到 {limit!r}")
    if limit_n <= 0:
        return _invalid_query(az, alt, fov,
                              f"输入非法：limit 必须 > 0，收到 {limit!r}")

    culture_requested = culture_ids is not None
    requested_cultures: Tuple[str, ...] = ()
    unknown_cultures: Tuple[str, ...] = ()
    if culture_requested:
        from .skyengine.sky_culture import split_culture_ids

        try:
            requested_cultures, unknown_cultures = split_culture_ids(culture_ids)
        except ValueError as exc:
            return _invalid_query(az, alt, fov, f"输入非法：{exc}")

    from .celestial_geometry import GroundStation
    from .sky_interaction import angular_distance_deg
    from .skyengine.star_catalog import naked_eye_limit_mag, select_for_view

    station = GroundStation(lon, lat, 0.0, "identify")
    # 显式给裸眼极限星等，关闭 select_for_view 的小视场深度增益：
    # 需求是「肉眼可见」，不是「当前 LOD 愿意画多暗」。
    limit_mag = float(naked_eye_limit_mag())
    sel = select_for_view(station, jd_v, az, alt, fov,
                          limit_mag=limit_mag, max_count=4000)

    radius = 0.5 * fov
    hits: List[Dict[str, Any]] = []
    for i in range(len(sel.vmag)):
        dist = angular_distance_deg(az, alt,
                                    float(sel.az_deg[i]),
                                    float(sel.alt_deg[i]))
        # select_for_view 的视锥带 1° 余量，这里按严格的 fov/2 收口。
        if dist > radius:
            continue
        hip = int(sel.hip[i]) if sel.hip[i] else 0
        proper = str(sel.name[i]).strip()
        # HYG 用 -1 表示「无 HIP 编号」。既无专名、又无有效 HIP 的星无法
        # 被认出，列出来只会制造噪声；更不能把 -1 拼成「HIP -1」冒充编号。
        if not proper and hip <= 0:
            continue
        hits.append({
            # proper 名为空时用 HIP 编号，保证 name 可读且不编造中文名。
            "name": proper or (f"HIP {hip}" if hip else ""),
            "hip": hip,
            "con": str(sel.con[i]).strip(),
            "vmag": round(float(sel.vmag[i]), 2),
            "angular_distance_deg": round(dist, 3),
            "az_deg": round(float(sel.az_deg[i]), 3),
            "alt_deg": round(float(sel.alt_deg[i]), 3),
            "asterisms": list(_hip_to_asterisms().get(hip, ()))[:3],
        })

    if not hits:
        out = {
            "matched": False,
            "reason": (f"视场 {fov}° 内没有肉眼可见的星表恒星"
                       f"（极限星等 {limit_mag}）；不返回最近邻凑数"),
            "query": {"az_deg": az, "alt_deg": alt, "fov_deg": fov,
                      "jd_ut1": jd_v, "lat_deg": lat, "lon_deg": lon},
            "stars": [],
        }
        if culture_requested:
            out["unknown_cultures"] = list(unknown_cultures)
        return out

    # 识别优先讲亮星：先按星等升序，再按角距升序打破并列。
    hits.sort(key=lambda s: (s["vmag"], s["angular_distance_deg"]))
    total = len(hits)
    selected = hits[:limit_n]
    if culture_requested:
        from .skyengine.sky_culture import names_for_hip

        for star in selected:
            hip = star["hip"]
            all_names = names_for_hip(hip) if hip > 0 else {}
            # 只给请求的文化；该文化没有这颗星的名字就不出现键，
            # 绝不拿别家名字或默认名回填。
            star["culture_names"] = {
                cid: dict(all_names[cid])
                for cid in requested_cultures if cid in all_names
            }
    out = {
        "matched": True,
        "query": {"az_deg": az, "alt_deg": alt, "fov_deg": fov,
                  "jd_ut1": jd_v, "lat_deg": lat, "lon_deg": lon},
        "coordinate_frame": ("input: horizontal az/alt; catalog: J2000 "
                             "RA/Dec via celestial_geometry"),
        "limit_mag": limit_mag,
        "total_matched": total,
        "truncated": total > limit_n,
        "stars": selected,
        "catalog": {"name": "HYG v4.1", "license": "CC BY-SA 4.0"},
    }
    if culture_requested:
        out["unknown_cultures"] = list(unknown_cultures)
    return out
