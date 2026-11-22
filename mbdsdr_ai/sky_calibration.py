# SPDX-License-Identifier: MIT
"""望远镜/手机指向的校准档案（schema v1）。

解决什么问题
------------
IMU 的方位/俯仰/横滚读数带固定偏置，同一台设备每次开机都差不多。
把「已知方向 ↔ 当前读数」的偏置存成档案，下一次开机直接套用，就不必
每次重新对星。本模块只负责三件事：校准求解、原子落盘、结构化读取。

schema v1
---------
::

    {
        "schema_version": 1,
        "device_id": "用户命名的设备",
        "fov_deg": 8.0,
        "boresight_az_deg": -0.4,   # 方位偏置（加在读数上）
        "boresight_alt_deg": 0.2,   # 俯仰偏置（加在读数上）
        "roll_zero_deg": 1.1,       # 横滚零位（加在读数上）
        "created_utc": "2026-11-17T00:00:00Z",
        "verified_utc": "2026-11-17T00:00:00Z",
        "source": "two_star"        # two_star | landmark
    }

``boresight_az_deg`` / ``boresight_alt_deg`` 是**修正量而非绝对指向**：
两个不同方向的校准点不可能共用一个绝对视轴读数。字段名沿用 schema
约定，语义在代码与测试里钉死。

版本与迁移
----------
* ``SCHEMA_VERSION`` 是当前写入版本，``SUPPORTED_SCHEMA_VERSIONS`` 列出本机
  可直接读取的版本；
* 旧版本若在 ``MIGRATIONS`` 中登记了迁移函数（版本号 → 迁移函数），
  ``load_profile`` 会在内存里迁移到当前版本，返回 ``migrated_from``，但
  **不写回磁盘**——是否落盘由调用方决定；
* 比本机更新或未知且无迁移路径的版本一律拒绝，原文件保持原样；
* 档案里不认识的字段收进 ``profile["extra_fields"]``，``save_profile``
  原样写回，保证「升级不丢数据」；保留字段不覆盖任何已知字段。

诚实语义
--------
* 损坏 / 版本不支持 / 字段非法 → ``load_profile`` 返回
  ``{"ok": False, "calibrated": False, ...}``，**不修改、不覆盖原文件**；
* 校准残差超过阈值或输入病态（两点过近、方向相反、接近天顶）→ 拒绝
  写入，不产出「差不多能用」的档案；
* 所有入口返回结构化 dict，不向 UI 抛异常。

原子写
------
``tempfile.mkstemp`` 同目录临时文件 + ``fsync``，替换目标文件前先用
``load_profile`` 读回临时文件校验（写后读回）；任何一步失败都清掉临时
文件，目标文件保持原样（中断不会留半文件，也不会落入读不回的内容）。

不新增依赖，不联网，不 import Qt。
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from datetime import datetime, timezone
from typing import (Any, Callable, Dict, List, Mapping, Optional, Sequence,
                    Tuple)

__all__ = [
    "MAX_CALIBRATION_RESIDUAL_DEG",
    "MIGRATIONS",
    "MIN_PAIR_SEPARATION_DEG",
    "SCHEMA_VERSION",
    "SUPPORTED_SCHEMA_VERSIONS",
    "calibrate",
    "calibrate_and_save",
    "load_profile",
    "save_profile",
]

#: 当前校准档案 schema 版本；不认识的版本一律拒绝加载。
SCHEMA_VERSION: int = 1

#: 本机可以直接读取的版本。迁移后的目标版本必须在此列表中。
SUPPORTED_SCHEMA_VERSIONS: Tuple[int, ...] = (1,)

#: 已知 schema 字段；其余字段按「未知字段」原样保留。
_SCHEMA_FIELDS = frozenset({
    "schema_version", "device_id", "fov_deg", "boresight_az_deg",
    "boresight_alt_deg", "roll_zero_deg", "created_utc", "verified_utc",
    "source",
})

#: 未知字段在加载结果中的容器键名。
_EXTRA_CONTAINER = "extra_fields"


def _identity_migration(profile: Mapping[str, Any]) -> Dict[str, Any]:
    """v1 → v1 恒等迁移（v1 即当前版本时的注册锚点）。"""
    return dict(profile)


#: 旧版本 → 当前版本的迁移函数注册表：``{起始版本: 迁移函数}``。
#: 迁移函数接收原始档案（含未知字段），返回当前版本的档案；不得原地修改
#: 入参。当前版本为 1，故 1 号迁移是恒等；将来提升 ``SCHEMA_VERSION`` 时
#: 必须把它替换为真正的 1 → N 迁移。
MIGRATIONS: Dict[int, Callable[[Mapping[str, Any]], Dict[str, Any]]] = {
    1: _identity_migration,
}

#: 校准残差上限（度）。超过即拒绝写盘——宁可不校准，也不写错档案。
MAX_CALIBRATION_RESIDUAL_DEG: float = 0.5

#: 两个校准方向的最小夹角（度）。过近时横滚不可辨识，属病态输入。
MIN_PAIR_SEPARATION_DEG: float = 5.0

_SOURCES = ("two_star", "landmark")

# 地平坐标方向向量沿用 celestial_geometry 的约定：x=北, y=东, z=天底。


def _finite(value: Any) -> Optional[float]:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _wrap180(deg: float) -> float:
    out = (float(deg) + 180.0) % 360.0 - 180.0
    return 180.0 if out == -180.0 else out


def _vec_from_azalt(az_deg: float, alt_deg: float) -> Tuple[float, float, float]:
    az = math.radians(az_deg)
    alt = math.radians(alt_deg)
    ca = math.cos(alt)
    return (ca * math.cos(az), ca * math.sin(az), -math.sin(alt))


def _norm(v: Sequence[float]) -> float:
    return math.sqrt(sum(float(c) * float(c) for c in v))


def _unit(v: Sequence[float]) -> Optional[Tuple[float, float, float]]:
    n = _norm(v)
    if n <= 0.0 or not math.isfinite(n):
        return None
    return (float(v[0]) / n, float(v[1]) / n, float(v[2]) / n)


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(float(x) * float(y) for x, y in zip(a, b))


def _cross(a: Sequence[float], b: Sequence[float]) -> Tuple[float, float, float]:
    return (float(a[1]) * float(b[2]) - float(a[2]) * float(b[1]),
            float(a[2]) * float(b[0]) - float(a[0]) * float(b[2]),
            float(a[0]) * float(b[1]) - float(a[1]) * float(b[0]))


def _angle_deg(a: Sequence[float], b: Sequence[float]) -> float:
    c = _cross(a, b)
    return math.degrees(math.atan2(_norm(c), _dot(a, b)))


def _rotate(rotvec: Sequence[float],
            v: Sequence[float]) -> Tuple[float, float, float]:
    """Rodrigues 旋转：把向量 v 按旋转向量 rotvec 转动。"""
    theta = _norm(rotvec)
    if theta <= 1e-15:
        return (float(v[0]), float(v[1]), float(v[2]))
    k = (float(rotvec[0]) / theta, float(rotvec[1]) / theta,
         float(rotvec[2]) / theta)
    kxv = _cross(k, v)
    kdv = _dot(k, v)
    c, s = math.cos(theta), math.sin(theta)
    return (float(v[0]) * c + kxv[0] * s + k[0] * kdv * (1.0 - c),
            float(v[1]) * c + kxv[1] * s + k[1] * kdv * (1.0 - c),
            float(v[2]) * c + kxv[2] * s + k[2] * kdv * (1.0 - c))


def _solve3(matrix: List[List[float]],
            rhs: Sequence[float]) -> Optional[Tuple[float, float, float]]:
    """3×3 线性方程组的 Cramer 解；退化时返回 None。"""
    a = [[float(matrix[r][c]) for c in range(3)] for r in range(3)]
    det = (a[0][0] * (a[1][1] * a[2][2] - a[1][2] * a[2][1])
           - a[0][1] * (a[1][0] * a[2][2] - a[1][2] * a[2][0])
           + a[0][2] * (a[1][0] * a[2][1] - a[1][1] * a[2][0]))
    scale = max(1e-12, (abs(a[0][0]) + abs(a[1][1]) + abs(a[2][2])) / 3.0)
    if not math.isfinite(det) or abs(det) <= 1e-9 * scale ** 3:
        return None

    def solve_col(col: int) -> float:
        m = [row[:] for row in a]
        for r in range(3):
            m[r][col] = float(rhs[r])
        d = (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
             - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
             + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))
        return d / det

    return (solve_col(0), solve_col(1), solve_col(2))


def _utc_now_iso() -> str:
    return (datetime.now(timezone.utc).replace(microsecond=0)
            .isoformat().replace("+00:00", "Z"))


def _valid_iso(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _parse_observation(index: int,
                       item: Any) -> Tuple[Optional[Dict[str, float]], Optional[str]]:
    if not isinstance(item, Mapping):
        return None, f"第 {index} 个校准点必须是字典，收到 {type(item).__name__}"
    vals: Dict[str, float] = {}
    for key in ("known_az_deg", "known_alt_deg", "read_az_deg", "read_alt_deg"):
        v = _finite(item.get(key))
        if v is None:
            return None, f"第 {index} 个校准点缺少有效的 {key}"
        vals[key] = v
    for prefix in ("known", "read"):
        alt = vals[f"{prefix}_alt_deg"]
        if not (-90.0 <= alt <= 90.0):
            return None, (f"第 {index} 个校准点的 {prefix}_alt_deg 越界"
                          f"（{alt}°，合法范围 -90..90）")
    return vals, None


def _fit_rotation(observations: List[Dict[str, float]]
                  ) -> Tuple[Optional[List[List[float]]],
                             Optional[List[float]],
                             Optional[List[float]],
                             Optional[str]]:
    """最小二乘解小角度旋转向量 r（t ≈ e + r × e）。"""
    n_mat = [[0.0] * 3 for _ in range(3)]
    g = [0.0, 0.0, 0.0]
    true_vecs: List[Tuple[float, float, float]] = []
    read_vecs: List[Tuple[float, float, float]] = []

    for item in observations:
        e = _vec_from_azalt(item["read_az_deg"], item["read_alt_deg"])
        t = _vec_from_azalt(item["known_az_deg"], item["known_alt_deg"])
        true_vecs.append(t)
        read_vecs.append(e)
        b = (t[0] - e[0], t[1] - e[1], t[2] - e[2])
        # r × e 的矩阵形式，见模块 docstring 的模型说明。
        a_mat = [[0.0, e[2], -e[1]],
                 [-e[2], 0.0, e[0]],
                 [e[1], -e[0], 0.0]]
        for row in range(3):
            for col in range(3):
                n_mat[row][col] += a_mat[0][row] * a_mat[0][col] \
                    + a_mat[1][row] * a_mat[1][col] \
                    + a_mat[2][row] * a_mat[2][col]
            g[row] += (a_mat[0][row] * b[0] + a_mat[1][row] * b[1]
                       + a_mat[2][row] * b[2])

    r = _solve3(n_mat, g)
    if r is None:
        return None, None, None, ("病态输入：校准几何退化（方向过近或共线），"
                                  "无法解出唯一的偏置与横滚零位")
    return n_mat, g, [r[0], r[1], r[2]], None


def _residuals(rotvec: Sequence[float],
               true_vecs: Sequence[Sequence[float]],
               read_vecs: Sequence[Sequence[float]]) -> List[float]:
    out: List[float] = []
    for t, e in zip(true_vecs, read_vecs):
        out.append(_angle_deg(_rotate(rotvec, e), t))
    return out


def _collect_extra_fields(profile: Mapping[str, Any]) -> Dict[str, Any]:
    """收集 schema 之外的字段（含 ``extra_fields`` 容器里的内容）。

    已知字段与容器键本身永不进入结果，因此保留字段不可能覆盖已知字段。
    """
    extra: Dict[str, Any] = {}
    nested = profile.get(_EXTRA_CONTAINER)
    if isinstance(nested, Mapping):
        for key, value in nested.items():
            if isinstance(key, str) and key not in _SCHEMA_FIELDS:
                extra[key] = value
    for key, value in profile.items():
        if (isinstance(key, str) and key not in _SCHEMA_FIELDS
                and key != _EXTRA_CONTAINER):
            extra[key] = value
    return extra


def _validate_profile(profile: Any
                      ) -> Tuple[Optional[Dict[str, Any]],
                                 Dict[str, Any],
                                 Optional[str]]:
    """校验并归一化当前版本档案；返回 (已知字段, 未知字段, 错误)。"""
    if not isinstance(profile, Mapping):
        return None, {}, f"校准档案必须是字典，收到 {type(profile).__name__}"

    version = profile.get("schema_version")
    if not isinstance(version, int) or isinstance(version, bool):
        return None, {}, f"校准档案缺少整数 schema_version，收到 {version!r}"
    if version != SCHEMA_VERSION:
        return None, {}, (f"不支持的校准档案版本 {version}"
                          f"（当前支持 {SCHEMA_VERSION}）")

    device_id = profile.get("device_id")
    if not isinstance(device_id, str) or not device_id.strip():
        return None, {}, "校准档案的 device_id 必须是非空字符串"

    fov = _finite(profile.get("fov_deg"))
    if fov is None or not (0.0 < fov <= 180.0):
        return None, {}, f"校准档案的 fov_deg 非法：{profile.get('fov_deg')!r}"

    az = _finite(profile.get("boresight_az_deg"))
    alt = _finite(profile.get("boresight_alt_deg"))
    roll = _finite(profile.get("roll_zero_deg"))
    if az is None or alt is None or roll is None:
        return None, {}, ("校准档案的 boresight_az_deg / boresight_alt_deg / "
                          "roll_zero_deg 必须为有限数值")
    if not (-90.0 <= alt <= 90.0):
        return None, {}, f"校准档案的 boresight_alt_deg 越界（{alt}°）"

    created = profile.get("created_utc")
    verified = profile.get("verified_utc")
    if not _valid_iso(created):
        return None, {}, f"校准档案的 created_utc 不是 ISO-8601 时间：{created!r}"
    if not _valid_iso(verified):
        return None, {}, f"校准档案的 verified_utc 不是 ISO-8601 时间：{verified!r}"

    source = profile.get("source")
    if source not in _SOURCES:
        return None, {}, (f"校准档案的 source 非法：{source!r}"
                          f"（可选 {', '.join(_SOURCES)}）")

    normalized = {
        "schema_version": SCHEMA_VERSION,
        "device_id": device_id.strip(),
        "fov_deg": float(fov),
        "boresight_az_deg": _wrap180(az),
        "boresight_alt_deg": float(alt),
        "roll_zero_deg": _wrap180(roll),
        "created_utc": str(created).strip(),
        "verified_utc": str(verified).strip(),
        "source": str(source),
    }
    return normalized, _collect_extra_fields(profile), None


def _apply_migration(version: int,
                     profile: Mapping[str, Any]
                     ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """把 ``version`` 版档案迁移到当前版本；迁移函数异常折算成错误原因。"""
    migration = MIGRATIONS.get(version)
    if migration is None:
        return None, (f"没有从版本 {version} 到 {SCHEMA_VERSION} 的迁移路径")
    try:
        migrated = migration(dict(profile))
    except Exception as exc:  # noqa: BLE001 - 迁移来自注册表，失败须降级
        return None, (f"从版本 {version} 迁移失败："
                      f"{type(exc).__name__}: {exc}")
    if not isinstance(migrated, Mapping):
        return None, (f"从版本 {version} 的迁移返回了 "
                      f"{type(migrated).__name__} 而非对象")
    if migrated.get("schema_version") != SCHEMA_VERSION:
        return None, (f"从版本 {version} 的迁移未产出当前版本 "
                      f"{SCHEMA_VERSION}")
    return dict(migrated), None


def calibrate(observations: Sequence[Mapping[str, Any]],
              *,
              device_id: str,
              fov_deg: float,
              source: str = "two_star",
              max_residual_deg: float = MAX_CALIBRATION_RESIDUAL_DEG
              ) -> Dict[str, Any]:
    """用两星/地标观测解出方位/俯仰偏置与横滚零位。

    每个观测点给出「已知方向」与「当前读数」两组 az/alt。模型是
    ``已知 ≈ 读数 + r × 读数`` 的小角度旋转；由最小二乘解出旋转向量 r，
    再分解为视轴修正量（方位/俯仰）与绕视轴的横滚零位。

    Args:
        observations: 至少两项，每项含 ``known_az_deg`` / ``known_alt_deg`` /
            ``read_az_deg`` / ``read_alt_deg``。
        device_id: 用户命名的设备标识。
        fov_deg: 校准时使用的视场（度）。
        source: ``"two_star"`` 或 ``"landmark"``。
        max_residual_deg: 允许的最大逐点残差（度），默认
            ``MAX_CALIBRATION_RESIDUAL_DEG``。

    Returns:
        成功：``{"ok": True, "profile": {...}, "residual_deg": float,
        "residuals_deg": [...], "reason": ""}``；
        失败：``{"ok": False, "profile": None, "reason": ..., "error": ...}``。
        失败时不写任何文件。
    """
    if not isinstance(device_id, str) or not device_id.strip():
        return {"ok": False, "profile": None, "error": "bad_input",
                "reason": "device_id 必须是非空字符串"}
    fov = _finite(fov_deg)
    if fov is None or not (0.0 < fov <= 180.0):
        return {"ok": False, "profile": None, "error": "bad_input",
                "reason": f"fov_deg 必须在 (0, 180] 度内，收到 {fov_deg!r}"}
    if source not in _SOURCES:
        return {"ok": False, "profile": None, "error": "bad_input",
                "reason": f"source 非法：{source!r}（可选 {', '.join(_SOURCES)}）"}
    limit = _finite(max_residual_deg)
    if limit is None or limit <= 0.0:
        return {"ok": False, "profile": None, "error": "bad_input",
                "reason": f"max_residual_deg 必须为正数，收到 {max_residual_deg!r}"}

    if isinstance(observations, (str, bytes, Mapping)):
        return {"ok": False, "profile": None, "error": "bad_input",
                "reason": "observations 必须是校准点序列"}
    try:
        items = list(observations)
    except TypeError:
        return {"ok": False, "profile": None, "error": "bad_input",
                "reason": (f"observations 必须是校准点序列，"
                           f"收到 {type(observations).__name__}")}
    if len(items) < 2:
        return {"ok": False, "profile": None, "error": "too_few_points",
                "reason": f"校准至少需要 2 个方向，收到 {len(items)} 个"}

    parsed: List[Dict[str, float]] = []
    for i, item in enumerate(items):
        vals, err = _parse_observation(i, item)
        if err is not None:
            return {"ok": False, "profile": None, "error": "bad_input",
                    "reason": err}
        assert vals is not None
        parsed.append(vals)

    vecs = [_vec_from_azalt(v["known_az_deg"], v["known_alt_deg"]) for v in parsed]
    max_sep = 0.0
    for i in range(len(vecs)):
        for j in range(i + 1, len(vecs)):
            max_sep = max(max_sep, _angle_deg(vecs[i], vecs[j]))
    if max_sep < MIN_PAIR_SEPARATION_DEG:
        return {"ok": False, "profile": None, "error": "pathological",
                "reason": (f"病态输入：校准方向最大夹角 {max_sep:.2f}° < "
                           f"{MIN_PAIR_SEPARATION_DEG}°，横滚不可辨识")}

    _, _, rotvec, err = _fit_rotation(parsed)
    if err is not None or rotvec is None:
        return {"ok": False, "profile": None, "error": "pathological",
                "reason": err or "病态输入：无法解出旋转"}

    sum_vec = [sum(v[i] for v in vecs) for i in range(3)]
    boresight = _unit(sum_vec)
    if boresight is None:
        return {"ok": False, "profile": None, "error": "pathological",
                "reason": "病态输入：校准方向相互抵消，无法定义视轴"}

    az0 = math.degrees(math.atan2(boresight[1], boresight[0])) % 360.0
    alt0 = math.degrees(math.asin(max(-1.0, min(1.0, -boresight[2]))))
    cos_alt = math.cos(math.radians(alt0))
    if abs(cos_alt) < 1e-6:
        return {"ok": False, "profile": None, "error": "pathological",
                "reason": "病态输入：校准方向接近天顶，方位偏置不可辨识"}

    # 局部切平面基：east_h = ∂u/∂az（单位模），up_h = ∂u/∂alt。
    az_r = math.radians(az0)
    alt_r = math.radians(alt0)
    east_h = (-math.sin(az_r), math.cos(az_r), 0.0)
    up_h = (-math.sin(alt_r) * math.cos(az_r),
            -math.sin(alt_r) * math.sin(az_r), -math.cos(alt_r))
    du = _cross(rotvec, boresight)
    delta_az = _dot(du, east_h) / cos_alt
    delta_alt = _dot(du, up_h)
    roll_zero = _dot(rotvec, boresight)

    residuals = _residuals(rotvec, vecs,
                           [_vec_from_azalt(v["read_az_deg"], v["read_alt_deg"])
                            for v in parsed])
    worst = max(residuals)
    if worst > limit:
        return {"ok": False, "profile": None, "error": "residual_too_large",
                "reason": (f"校准残差过大：最大 {worst:.3f}° > 阈值 "
                           f"{limit:g}°，拒绝写入"),
                "residual_deg": worst,
                "residuals_deg": residuals}

    now = _utc_now_iso()
    profile = {
        "schema_version": SCHEMA_VERSION,
        "device_id": device_id.strip(),
        "fov_deg": float(fov),
        "boresight_az_deg": _wrap180(math.degrees(delta_az)),
        "boresight_alt_deg": math.degrees(delta_alt),
        "roll_zero_deg": _wrap180(math.degrees(roll_zero)),
        "created_utc": now,
        "verified_utc": now,
        "source": source,
    }
    normalized, _extra, err = _validate_profile(profile)
    if err is not None:
        return {"ok": False, "profile": None, "error": "invalid",
                "reason": f"校准结果未通过 schema 校验：{err}"}

    return {
        "ok": True,
        "profile": normalized,
        "residual_deg": worst,
        "residuals_deg": residuals,
        "reason": "",
    }


def _verify_written(tmp: str, payload: Mapping[str, Any]) -> Optional[str]:
    """读回刚写好的临时文件并比对已知字段；通过返回 None，否则返回原因。

    只比已知 schema 字段：``load_profile`` 可能补充的 ``extra_fields`` 容器
    与迁移元数据不参与比对。
    """
    result = load_profile(tmp)  # 按全局名调用，测试可替换
    if not isinstance(result, Mapping) or not result.get("ok"):
        reason = result.get("reason") if isinstance(result, Mapping) else None
        return str(reason or "读回失败且未给出原因")
    profile = result.get("profile")
    if not isinstance(profile, Mapping):
        return "读回结果缺少 profile"
    mismatched = sorted(key for key in _SCHEMA_FIELDS
                        if profile.get(key) != payload.get(key))
    if mismatched:
        return f"字段 {', '.join(mismatched)} 与写盘内容不一致"
    return None


def save_profile(profile: Mapping[str, Any],
                 path: str) -> Dict[str, Any]:
    """原子写入校准档案；失败不破坏原文件。

    Args:
        profile: 当前版本档案；``extra_fields`` 里或顶层多余的字段会原样
            写回（不覆盖已知字段）。
        path: 目标 JSON 路径。

    Returns:
        ``{"ok": True, "path": 绝对路径}`` 或
        ``{"ok": False, "reason": ..., "error": ...}``。
    """
    normalized, extra, err = _validate_profile(profile)
    if err is not None:
        return {"ok": False, "error": "invalid",
                "reason": f"校准档案未通过 schema 校验：{err}"}
    assert normalized is not None
    payload: Dict[str, Any] = dict(normalized)
    for key, value in extra.items():
        if key not in payload:
            payload[key] = value

    if not isinstance(path, (str, os.PathLike)) or not os.fspath(path):
        return {"ok": False, "error": "bad_path",
                "reason": f"校准档案路径非法：{path!r}"}
    target = os.path.abspath(os.path.expanduser(os.fspath(path)))
    directory = os.path.dirname(target)
    tmp: Optional[str] = None
    try:
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".sky-cal-", suffix=".tmp",
                                   dir=directory)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        # 写后读回：临时文件必须能被完整读回且字段一致，才替换目标文件。
        verify_error = _verify_written(tmp, payload)
        if verify_error is not None:
            return {"ok": False, "error": "verify_failed",
                    "reason": f"写后读回校验失败：{verify_error}"}
        os.replace(tmp, target)
        tmp = None
        return {"ok": True, "path": target}
    except (OSError, TypeError, ValueError) as exc:
        return {"ok": False, "error": "write_failed",
                "reason": (f"校准档案写入失败（原文件未改动）："
                           f"{type(exc).__name__}: {exc}")}
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def calibrate_and_save(observations: Sequence[Mapping[str, Any]],
                       *,
                       device_id: str,
                       fov_deg: float,
                       path: str,
                       source: str = "two_star",
                       max_residual_deg: float = MAX_CALIBRATION_RESIDUAL_DEG
                       ) -> Dict[str, Any]:
    """校准并落盘的一步入口：求解失败不写文件，保存失败原样返回失败信封。

    成功返回 ``{"ok": True, "path": 绝对路径, "profile": {...},
    "residual_deg": float, "reason": ""}``。
    """
    solved = calibrate(observations, device_id=device_id, fov_deg=fov_deg,
                       source=source, max_residual_deg=max_residual_deg)
    if not solved.get("ok"):
        return solved
    saved = save_profile(solved["profile"], path)
    if not saved.get("ok"):
        return saved
    return {"ok": True, "path": saved["path"], "profile": solved["profile"],
            "residual_deg": float(solved["residual_deg"]), "reason": ""}


def load_profile(path: str) -> Dict[str, Any]:
    """读取并校验校准档案；任何异常都折算成结构化结果。

    损坏、版本不支持或字段非法时只返回失败信封，**不备份、不覆盖、不
    自动修复**原文件——证据留给用户与后续诊断。旧版本经 ``MIGRATIONS``
    迁移后只在内存中返回（信封带 ``migrated_from``），是否落盘由调用方
    决定；未知字段收进 ``profile["extra_fields"]`` 供 ``save_profile``
    原样写回。

    Args:
        path: JSON 档案路径。

    Returns:
        ``{"ok": True, "calibrated": True, "profile": {...}, "reason": ""}``
        或 ``{"ok": False, "calibrated": False, "profile": None,
        "reason": ..., "error": ...}``。
    """
    if not isinstance(path, (str, os.PathLike)) or not os.fspath(path):
        return {"ok": False, "calibrated": False, "profile": None,
                "error": "bad_path", "reason": f"校准档案路径非法：{path!r}"}
    target = os.path.abspath(os.path.expanduser(os.fspath(path)))
    if not os.path.isfile(target):
        return {"ok": False, "calibrated": False, "profile": None,
                "error": "not_found",
                "reason": f"未找到校准档案：{target}（按未校准降级）"}
    try:
        with open(target, "r", encoding="utf-8") as fh:
            raw = fh.read()
    except (OSError, UnicodeError) as exc:
        return {"ok": False, "calibrated": False, "profile": None,
                "error": "unreadable",
                "reason": f"校准档案读取失败：{type(exc).__name__}: {exc}"}

    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as exc:
        return {"ok": False, "calibrated": False, "profile": None,
                "error": "corrupt",
                "reason": f"校准档案损坏（JSON 解析失败）：{exc}"}

    if not isinstance(data, Mapping):
        return {"ok": False, "calibrated": False, "profile": None,
                "error": "corrupt",
                "reason": (f"校准档案损坏（根节点是 "
                           f"{type(data).__name__} 而非对象）")}

    version = data.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int):
        return {"ok": False, "calibrated": False, "profile": None,
                "error": "corrupt",
                "reason": f"校准档案缺少整数 schema_version，收到 {version!r}"}

    migrated_from: Optional[int] = None
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        if version < SCHEMA_VERSION and version in MIGRATIONS:
            migrated, migration_err = _apply_migration(version, data)
            if migration_err is not None or migrated is None:
                return {"ok": False, "calibrated": False, "profile": None,
                        "error": "unsupported_version",
                        "reason": (f"{migration_err}，按未校准降级")}
            data = migrated
            migrated_from = version
        elif version > SCHEMA_VERSION:
            return {"ok": False, "calibrated": False, "profile": None,
                    "error": "unsupported_version",
                    "reason": (f"校准档案版本 {version} 比本机支持的 "
                               f"{SCHEMA_VERSION} 更新，拒绝加载（按未校准"
                               f"降级），不覆盖原文件")}
        else:
            return {"ok": False, "calibrated": False, "profile": None,
                    "error": "unsupported_version",
                    "reason": (f"不支持的校准档案版本 {version}"
                               f"（当前支持 {SCHEMA_VERSION}，"
                               f"无迁移路径），按未校准降级")}

    normalized, extra, err = _validate_profile(data)
    if err is not None:
        return {"ok": False, "calibrated": False, "profile": None,
                "error": "invalid", "reason": f"校准档案字段非法：{err}"}
    assert normalized is not None
    if extra:
        normalized[_EXTRA_CONTAINER] = extra

    result = {"ok": True, "calibrated": True, "profile": normalized,
              "reason": "", "path": target}
    if migrated_from is not None:
        result["migrated_from"] = migrated_from
    return result
