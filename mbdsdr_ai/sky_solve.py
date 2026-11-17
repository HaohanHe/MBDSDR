# SPDX-License-Identifier: MIT
"""星点解算适配：tetra3（可选依赖）与 ASTAP（外部进程）。

为什么先有契约
--------------
IMU 只能粗对准（户外方位 5–15°），要靠相机星点匹配做二级校正。但解算库
（tetra3 / ASTAP）是**可选依赖**：没装就必须如实说不可用，不能假装解算
成功，也不能把「星点不够」说成「解算失败」。

稳定入口
--------
- ``solver_status()``：探测 tetra3 是否可导入、ASTAP 是否配置了可执行文件；
- ``solve_frame(...)``：校验输入后自动选择可用解算器；
- ``solve_tetra3(...)`` / ``solve_astap(...)``：显式走某一条适配路径。

成功返回契约
------------
成功时返回（键名固定，供上层与工具面直接消费）::

    {
        "ok": True,
        "solver": "tetra3" | "astap",
        "az_delta_deg": float,       # 叠加在先验指向上的方位修正量
        "alt_delta_deg": float,      # 俯仰修正量
        "roll_delta_deg": float,     # 横滚修正量
        "matched_stars": int,        # 参与解算的星数
        "residual_arcsec": float,    # 匹配残差（角秒）
        "fov_deg": float,            # 本次解算实际使用的视场先验
        "reason": "",
    }

修正量是**增量**而非绝对指向：调用方拿 ``sky_pointing.fuse_poses`` 的
位姿加上这些增量再交给 ViewState。失败一律 ``{"ok": False, "reason": ...,
"error": <结构化代码>}``，不返回半信半疑的朝向。

要算「修正量」必须有先验位姿（``prior``：az/alt/roll）与观测者位置/时刻
（``observer``：lat/lon/jd_ut1）；tetra3/ASTAP 给出的是赤道坐标，缺任何
一项都无法诚实地折算成地平修正量。缺项时返回结构化原因，不猜。

ASTAP 适配
----------
可执行文件路径按参数 ``astap_path`` 或环境变量 ``MBDSDR_ASTAP_PATH``
解析；用 ``subprocess`` 调用，超时/非零退出/输出不可解析分别映射为
``timeout`` / ``nonzero_exit`` / ``parse_error``。ASTAP 的原始日志与
WCS 文本不会向上层泄漏，只映射成固定字段。

诚实边界
--------
本模块的适配器只用测试替身（假 tetra3 模块、假可执行脚本）验证过协议与
错误映射；**没有在真实 tetra3 数据库或真实 ASTAP 二进制上验证过解算
精度**。真实坐标换算假设 tetra3 ``RMSE`` 单位为度、ASTAP 键值按常见
``.ini``/WCS 头解析，这些假设在接真机前必须复核。
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import tempfile
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

__all__ = [
    "ASTAP_ENV_VAR",
    "DEFAULT_TIMEOUT_S",
    "MIN_SOLVE_STARS",
    "solver_status",
    "solve_frame",
    "solve_tetra3",
    "solve_astap",
]

#: 解算所需的最少星点数。低于此值不进入解算（FOV/像素尺度先验也救不回来）。
MIN_SOLVE_STARS: int = 8

#: ASTAP 可执行文件路径的环境变量名。
ASTAP_ENV_VAR: str = "MBDSDR_ASTAP_PATH"

#: ASTAP 子进程默认超时（秒）。
DEFAULT_TIMEOUT_S: float = 10.0

#: 支持的解算器名称。
_SOLVERS = ("tetra3", "astap")

_UNAVAILABLE_REASON = "未安装 tetra3；ASTAP 需外部可执行文件"


def solver_status(astap_path: Optional[str] = None) -> Dict[str, Any]:
    """探测星点解算库是否可用。

    只做 ``import tetra3`` 探测与 ASTAP 路径存在性检查，不安装、不下载、
    不启动外部进程。

    Args:
        astap_path: 显式 ASTAP 可执行文件路径；None 时读环境变量
            ``MBDSDR_ASTAP_PATH``。

    Returns:
        ``{"available": True, "name": "tetra3"|"astap"}`` 或
        ``{"available": False, "reason": "..."}``。tetra3 缺失且 ASTAP
        未配置时原因与既有契约逐字一致。
    """
    tetra3_reason = _UNAVAILABLE_REASON
    try:
        import tetra3  # noqa: F401
    except ImportError:
        pass
    except Exception as exc:  # 装了但导入失败（缺原生库等）也要如实报告
        tetra3_reason = f"tetra3 导入失败：{type(exc).__name__}: {exc}"
    else:
        return {"available": True, "name": "tetra3"}

    exe = astap_path if astap_path is not None else os.environ.get(ASTAP_ENV_VAR)
    if exe:
        try:
            if os.path.isfile(os.path.expanduser(str(exe))):
                return {"available": True, "name": "astap"}
        except (OSError, TypeError, ValueError):
            pass
    return {"available": False, "reason": tetra3_reason}


def _finite(value: Any) -> Optional[float]:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _wrap180(deg: float) -> float:
    """把角度差折到 (-180, 180]。"""
    out = (float(deg) + 180.0) % 360.0 - 180.0
    return 180.0 if out == -180.0 else out


def _fail(reason: str, code: str, **extra: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {"ok": False, "reason": reason, "error": code}
    out.update(extra)
    return out


def _parse_stars(stars_px: Any) -> Tuple[Optional[List[Tuple[float, float]]],
                                         Optional[str]]:
    """把星点像素输入规整成 [(x, y), ...]；非法时返回 (None, reason)。"""
    if stars_px is None:
        return None, "输入非法：stars_px 不能为空"
    if isinstance(stars_px, (str, bytes, dict)):
        return None, ("输入非法：stars_px 必须是 (x, y) 序列，"
                      f"收到 {type(stars_px).__name__}")
    try:
        items = list(stars_px)
    except TypeError:
        return None, ("输入非法：stars_px 必须是 (x, y) 序列，"
                      f"收到 {type(stars_px).__name__}")

    points: List[Tuple[float, float]] = []
    for i, item in enumerate(items):
        if isinstance(item, (str, bytes, dict)):
            return None, f"输入非法：第 {i} 个星点不是 (x, y)，收到 {type(item).__name__}"
        try:
            coords = list(item)
        except TypeError:
            return None, f"输入非法：第 {i} 个星点不是 (x, y)，收到 {type(item).__name__}"
        if len(coords) < 2:
            return None, f"输入非法：第 {i} 个星点缺少 x/y（收到 {len(coords)} 个分量）"
        x, y = _finite(coords[0]), _finite(coords[1])
        if x is None or y is None:
            return None, f"输入非法：第 {i} 个星点坐标非有限（NaN/Inf/非数值）"
        points.append((x, y))
    return points, None


def _parse_fov_image(fov_deg: Any, image_size: Any
                     ) -> Tuple[Optional[float], Optional[Tuple[float, float]],
                                Optional[str]]:
    fov = _finite(fov_deg)
    if fov is None or not (0.0 < fov <= 180.0):
        return None, None, (f"输入非法：fov_deg 必须在 (0, 180] 度内，"
                            f"收到 {fov_deg!r}")

    if image_size is None or isinstance(image_size, (str, bytes, dict)):
        return None, None, (f"输入非法：image_size 必须是 (width, height)，"
                            f"收到 {type(image_size).__name__}")
    try:
        size = list(image_size)
    except TypeError:
        return None, None, (f"输入非法：image_size 必须是 (width, height)，"
                            f"收到 {type(image_size).__name__}")
    if len(size) < 2:
        return None, None, (f"输入非法：image_size 缺少 width/height"
                            f"（收到 {len(size)} 个分量）")
    width, height = _finite(size[0]), _finite(size[1])
    if width is None or height is None or width <= 0.0 or height <= 0.0:
        return None, None, f"输入非法：image_size 必须为正，收到 {image_size!r}"
    return fov, (width, height), None


def _parse_prior(prior: Any) -> Tuple[Optional[Dict[str, float]], Optional[str]]:
    """先验位姿：az/alt/roll 三个数，缺一个都不猜。"""
    if prior is None:
        return None, ("缺少先验位姿（prior.az_deg / alt_deg / roll_deg）："
                      "没有先验就无法把绝对解算结果折算成修正量")
    if not isinstance(prior, Mapping):
        return None, (f"输入非法：prior 必须是含 az_deg/alt_deg/roll_deg 的字典，"
                      f"收到 {type(prior).__name__}")
    az = _finite(prior.get("az_deg"))
    alt = _finite(prior.get("alt_deg"))
    roll = _finite(prior.get("roll_deg", 0.0))
    if az is None or alt is None or roll is None:
        return None, "输入非法：prior 的 az_deg / alt_deg / roll_deg 必须是有限数值"
    if not (-90.0 <= alt <= 90.0):
        return None, f"输入非法：prior.alt_deg 越界（{alt}°，合法范围 -90..90）"
    return {"az_deg": az % 360.0, "alt_deg": alt, "roll_deg": roll}, None


def _parse_observer(observer: Any) -> Tuple[Optional[Dict[str, float]], Optional[str]]:
    """观测者位置/时刻：把赤道坐标折成地平坐标所必需。"""
    if observer is None:
        return None, ("缺少观测者位置/时刻（observer.lat_deg / lon_deg / "
                      "jd_ut1）：无法把赤道坐标折算成地平修正量")
    if not isinstance(observer, Mapping):
        return None, (f"输入非法：observer 必须是含 lat_deg/lon_deg/jd_ut1 的字典，"
                      f"收到 {type(observer).__name__}")
    lat = _finite(observer.get("lat_deg"))
    lon = _finite(observer.get("lon_deg"))
    jd = _finite(observer.get("jd_ut1"))
    elev = _finite(observer.get("elevation_m", 0.0))
    if lat is None or lon is None or jd is None or elev is None:
        return None, ("输入非法：observer 的 lat_deg / lon_deg / jd_ut1 "
                      "必须是有限数值")
    if not (-90.0 <= lat <= 90.0):
        return None, f"输入非法：observer.lat_deg 越界（{lat}°）"
    if not (-360.0 <= lon <= 360.0):
        return None, f"输入非法：observer.lon_deg 越界（{lon}°）"
    return {"lat_deg": lat, "lon_deg": lon, "jd_ut1": jd,
            "elevation_m": elev}, None


def _radec_to_azalt(ra_deg: float, dec_deg: float,
                    observer: Dict[str, float]) -> Tuple[float, float]:
    from .celestial_geometry import (GroundStation, azalt_from_vec,
                                     j2000_to_altaz, vec_from_radec)

    station = GroundStation(observer["lon_deg"], observer["lat_deg"],
                            observer["elevation_m"], "sky_solve")
    v_altaz = j2000_to_altaz(vec_from_radec(ra_deg, dec_deg), station,
                             observer["jd_ut1"])
    return azalt_from_vec(v_altaz)


def _success(solver: str, solved_az: float, solved_alt: float,
             solved_roll: float, matched_stars: int, residual_arcsec: float,
             fov_used: float, prior: Dict[str, float]) -> Dict[str, Any]:
    return {
        "ok": True,
        "solver": solver,
        "az_delta_deg": _wrap180(solved_az - prior["az_deg"]),
        "alt_delta_deg": solved_alt - prior["alt_deg"],
        "roll_delta_deg": _wrap180(solved_roll - prior["roll_deg"]),
        "matched_stars": int(matched_stars),
        "residual_arcsec": float(residual_arcsec),
        "fov_deg": float(fov_used),
        "solved": {
            "az_deg": solved_az % 360.0,
            "alt_deg": solved_alt,
            "roll_deg": solved_roll % 360.0,
        },
        "reason": "",
    }


def _solution_fields(result: Mapping[str, Any]) -> Dict[str, Any]:
    """从解算结果里取公共字段，容忍大小写与少量别名。"""
    def first(*names: str) -> Any:
        for name in names:
            if name in result and result[name] is not None:
                return result[name]
        return None

    return {
        "ra": _finite(first("RA", "ra", "RA_deg", "ra_deg")),
        "dec": _finite(first("Dec", "DEC", "dec", "Dec_deg", "dec_deg")),
        "az": _finite(first("az_deg", "AZ", "Az", "az")),
        "alt": _finite(first("alt_deg", "ALT", "Alt", "alt")),
        "roll": _finite(first("Roll", "ROLL", "roll", "roll_deg")),
        "fov": _finite(first("FOV", "fov", "fov_deg")),
        "matches": first("Matches", "matches", "matched_stars", "NUMSTARS"),
        "rmse": first("RMSE", "rmse", "RMSE_deg", "rmse_deg"),
        "rmse_arcsec": first("RMSE_arcsec", "rmse_arcsec"),
    }


def _tetra3_module() -> Tuple[Any, Optional[str]]:
    try:
        import tetra3  # noqa: F401
    except ImportError:
        return None, _UNAVAILABLE_REASON
    except Exception as exc:
        return None, f"tetra3 导入失败：{type(exc).__name__}: {exc}"
    return tetra3, None


def solve_tetra3(stars_px: Sequence[Sequence[float]],
                 image_size: Sequence[float],
                 fov_deg: float,
                 *,
                 prior: Optional[Mapping[str, Any]] = None,
                 observer: Optional[Mapping[str, Any]] = None,
                 fov_max_error_deg: Optional[float] = None) -> Dict[str, Any]:
    """用 tetra3 解算星点（只有 import 成功才走这条路径）。

    Args:
        stars_px: 星点像素坐标序列，每项 ``(x, y)``，至少 ``MIN_SOLVE_STARS``。
        image_size: 图像尺寸 ``(width, height)``。
        fov_deg: 视场直径先验（度）。
        prior: 先验位姿 ``{"az_deg", "alt_deg", "roll_deg"}``；用于把
            tetra3 的绝对赤道解算折算成地平修正量。
        observer: 观测者 ``{"lat_deg", "lon_deg", "jd_ut1"}``（可选
            ``elevation_m``）。
        fov_max_error_deg: 传给 tetra3 的视场容差；None 时按先验的 25%
            夹在 0.5–5°。

    Returns:
        成功返回固定契约（见模块 docstring）；失败返回
        ``{"ok": False, "reason": ..., "error": ...}``。
    """
    fov, size, err = _parse_fov_image(fov_deg, image_size)
    if err is not None:
        return _fail(err, "bad_input")
    assert fov is not None and size is not None

    points, err = _parse_stars(stars_px)
    if err is not None:
        return _fail(err, "bad_input")
    assert points is not None
    if len(points) < MIN_SOLVE_STARS:
        return _fail((f"星点不足：检测到 {len(points)} 颗，"
                      f"解算至少需要 {MIN_SOLVE_STARS} 颗"), "too_few_stars")

    module, import_reason = _tetra3_module()
    if module is None:
        return _fail(import_reason or _UNAVAILABLE_REASON, "unavailable")

    prior_v, err = _parse_prior(prior)
    if err is not None:
        return _fail(f"tetra3 解算需要先验位姿：{err}", "missing_prior")
    assert prior_v is not None

    try:
        solver_obj = module.Tetra3()
    except Exception as exc:
        return _fail(f"tetra3 初始化失败：{type(exc).__name__}: {exc}",
                     "solver_error")

    tol = _finite(fov_max_error_deg)
    if tol is None or tol <= 0.0:
        tol = min(5.0, max(0.5, fov * 0.25))

    try:
        import numpy as np

        result = solver_obj.solve_from_centroids(
            np.asarray(points, dtype=float),
            (int(round(size[0])), int(round(size[1]))),
            fov_estimate=float(fov),
            fov_max_error=float(tol),
        )
    except Exception as exc:
        return _fail(f"tetra3 解算异常：{type(exc).__name__}: {exc}",
                     "solver_error")

    if result is None:
        return _fail("tetra3 无匹配：星点未能在星表中找到对应", "no_match")
    if not isinstance(result, Mapping):
        return _fail(f"tetra3 输出不可解析（收到 {type(result).__name__}）",
                     "parse_error")

    fields = _solution_fields(result)
    matches = _finite(fields["matches"])
    if matches is None or int(matches) <= 0:
        return _fail("tetra3 无匹配：匹配星数为 0", "no_match")
    if int(matches) < MIN_SOLVE_STARS:
        return _fail((f"tetra3 匹配星数不足：{int(matches)} 颗，"
                      f"至少需要 {MIN_SOLVE_STARS} 颗"), "no_match")

    if fields["rmse_arcsec"] is not None:
        residual = _finite(fields["rmse_arcsec"])
    else:
        # tetra3 的 RMSE 单位是度；换算成契约要求的角秒。
        rmse_deg = _finite(fields["rmse"])
        residual = None if rmse_deg is None else rmse_deg * 3600.0
    if residual is None or residual < 0.0:
        return _fail("tetra3 输出缺少可解析的残差（RMSE）", "parse_error")

    roll = fields["roll"]
    if roll is None:
        return _fail("tetra3 输出缺少横滚（Roll）", "parse_error")

    az = fields["az"]
    alt = fields["alt"]
    if az is None or alt is None:
        if fields["ra"] is None or fields["dec"] is None:
            return _fail("tetra3 输出既无地平坐标也无 RA/Dec", "parse_error")
        observer_v, err = _parse_observer(observer)
        if err is not None:
            return _fail(f"tetra3 解算需要观测者位置/时刻：{err}",
                         "missing_observer")
        assert observer_v is not None
        try:
            az, alt = _radec_to_azalt(fields["ra"], fields["dec"], observer_v)
        except Exception as exc:
            return _fail(f"tetra3 赤道→地平换算失败：{type(exc).__name__}: {exc}",
                         "solver_error")

    fov_used = fields["fov"]
    if fov_used is None or not (0.0 < fov_used <= 180.0):
        fov_used = fov

    return _success("tetra3", float(az), float(alt), float(roll),
                    int(matches), float(residual), float(fov_used), prior_v)


def _parse_astap_text(text: str) -> Dict[str, Any]:
    """把 ASTAP 的 .ini/.wcs/JSON 输出解析成扁平键值字典。

    只做键值归一，不把原始文本交给上层；无法识别时抛 ``ValueError``。
    """
    stripped = text.strip()
    if not stripped:
        raise ValueError("输出为空")
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"JSON 解析失败：{exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("JSON 根不是对象")
        return {str(k).upper(): v for k, v in data.items()}

    out: Dict[str, Any] = {}
    for line in stripped.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ";", "//")):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().upper()
        if not key:
            continue
        value = value.strip()
        # FITS 头与 ASTAP ini 都可能带「/ 注释」；先去掉行尾注释。
        if "/" in value:
            value = value.split("/", 1)[0].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        out[key] = value
    if not out:
        raise ValueError("没有可识别的键值行")
    return out


def _kv_float(data: Mapping[str, Any], *names: str) -> Optional[float]:
    for name in names:
        if name in data:
            v = _finite(data[name])
            if v is not None:
                return v
    return None


def _kv_int(data: Mapping[str, Any], *names: str) -> Optional[int]:
    for name in names:
        if name in data:
            v = _finite(data[name])
            if v is not None:
                return int(v)
    return None


def _astap_roll_from_cd(data: Mapping[str, Any]) -> Optional[float]:
    cd1_2 = _kv_float(data, "CD1_2")
    cd2_2 = _kv_float(data, "CD2_2")
    if cd1_2 is None or cd2_2 is None:
        return None
    return math.degrees(math.atan2(cd1_2, cd2_2))


def _astap_fov_from_cd(data: Mapping[str, Any],
                       size: Tuple[float, float]) -> Optional[float]:
    cd1_1 = _kv_float(data, "CD1_1")
    cd2_1 = _kv_float(data, "CD2_1")
    if cd1_1 is None or cd2_1 is None:
        return None
    scale = math.hypot(cd1_1, cd2_1)
    if not math.isfinite(scale) or scale <= 0.0:
        return None
    return scale * max(size[0], size[1])


def _astap_output_file(out_base: str) -> Optional[str]:
    for suffix in (".ini", ".wcs", ".txt", ""):
        path = out_base + suffix
        if os.path.isfile(path):
            return path
    return None


def solve_astap(image_path: str,
                image_size: Sequence[float],
                fov_deg: float,
                *,
                astap_path: Optional[str] = None,
                prior: Optional[Mapping[str, Any]] = None,
                observer: Optional[Mapping[str, Any]] = None,
                timeout_s: float = DEFAULT_TIMEOUT_S) -> Dict[str, Any]:
    """调用 ASTAP 外部进程解算一张图像。

    Args:
        image_path: 待解算的图像文件路径（ASTAP 需要图像，不是星点列表）。
        image_size: 图像尺寸 ``(width, height)``，用于从 CD 矩阵折算视场。
        fov_deg: 视场先验（度），作为 ASTAP ``-fov`` 参数与缺省返回值。
        astap_path: ASTAP 可执行文件路径；None 时读 ``MBDSDR_ASTAP_PATH``。
        prior: 先验位姿；用于把绝对解算结果折算成修正量。
        observer: 观测者位置/时刻；用于赤道→地平换算。
        timeout_s: 子进程超时（秒）。

    Returns:
        成功返回固定契约；失败按 ``timeout`` / ``nonzero_exit`` /
        ``parse_error`` / ``no_match`` / ``not_configured`` / ``missing_image``
        等结构化 ``error`` 返回。ASTAP 原始输出不向上层泄漏。
    """
    fov, size, err = _parse_fov_image(fov_deg, image_size)
    if err is not None:
        return _fail(err, "bad_input")
    assert fov is not None and size is not None

    if not isinstance(image_path, (str, os.PathLike)):
        return _fail("ASTAP 需要图像文件路径（image_path）", "missing_image")
    image = os.path.expanduser(os.fspath(image_path))
    if not os.path.isfile(image):
        return _fail(f"ASTAP 图像不存在：{image}", "missing_image")

    raw_exe = astap_path if astap_path is not None \
        else os.environ.get(ASTAP_ENV_VAR)
    if not raw_exe:
        return _fail(f"未配置 ASTAP 可执行文件（参数 astap_path 或环境变量 "
                     f"{ASTAP_ENV_VAR}）", "not_configured")
    exe = os.path.expanduser(str(raw_exe))
    if not os.path.isfile(exe):
        return _fail(f"ASTAP 可执行文件不存在：{exe}", "not_configured")

    prior_v, err = _parse_prior(prior)
    if err is not None:
        return _fail(f"ASTAP 解算需要先验位姿：{err}", "missing_prior")
    assert prior_v is not None

    timeout = _finite(timeout_s)
    if timeout is None or timeout <= 0.0:
        return _fail(f"输入非法：timeout_s 必须为正数，收到 {timeout_s!r}",
                     "bad_input")

    with tempfile.TemporaryDirectory(prefix="mbd-astap-") as workdir:
        out_base = os.path.join(workdir, "solve")
        command = [exe, "-f", image, "-fov", f"{fov:.4f}",
                   "-o", out_base, "-wcs", "-s", "500"]
        try:
            proc = subprocess.run(command, capture_output=True, text=True,
                                  timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            return _fail(f"ASTAP 解算超时（>{timeout:g}s）", "timeout")
        except (OSError, ValueError) as exc:
            return _fail(f"ASTAP 启动失败：{type(exc).__name__}: {exc}",
                         "solver_error")

        if proc.returncode != 0:
            return _fail(f"ASTAP 退出码非零（{proc.returncode}）",
                         "nonzero_exit", exit_code=int(proc.returncode))

        out_path = _astap_output_file(out_base)
        if out_path is None:
            return _fail("ASTAP 未产出可解析的结果文件", "parse_error")
        try:
            with open(out_path, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError as exc:
            return _fail(f"ASTAP 结果文件读取失败：{type(exc).__name__}: {exc}",
                         "parse_error")

    try:
        data = _parse_astap_text(text)
    except ValueError as exc:
        return _fail(f"ASTAP 输出不可解析：{exc}", "parse_error")

    solved_flag = data.get("PLTSOLVD", data.get("SOLVED", data.get("SUCCESS")))
    if solved_flag is False:
        return _fail("ASTAP 无匹配：求解标志为假", "no_match")
    if (isinstance(solved_flag, (int, float)) and not isinstance(solved_flag, bool)
            and float(solved_flag) == 0.0):
        return _fail("ASTAP 无匹配：求解标志为假", "no_match")
    if isinstance(solved_flag, str) and solved_flag.strip().upper() in {
            "F", "FALSE", "0", "N", "NO"}:
        return _fail("ASTAP 无匹配：求解标志为假", "no_match")

    matches = _kv_int(data, "MATCHES", "MATCHED", "NUMSTARS", "STARS")
    if matches is None or matches <= 0:
        return _fail("ASTAP 输出缺少匹配星数或匹配星数为 0", "no_match")
    if matches < MIN_SOLVE_STARS:
        return _fail((f"ASTAP 匹配星数不足：{matches} 颗，"
                      f"至少需要 {MIN_SOLVE_STARS} 颗"), "no_match")

    residual = _kv_float(data, "RMSE_ARCSEC", "RMSE", "ERROR", "RESIDUAL")
    if residual is None:
        residual = _kv_float(data, "RMSE_DEG")
        if residual is not None:
            residual *= 3600.0
    if residual is None or residual < 0.0:
        return _fail("ASTAP 输出缺少可解析的残差", "parse_error")

    roll = _kv_float(data, "ROLL", "CROTA2")
    if roll is None:
        roll = _astap_roll_from_cd(data)
    if roll is None:
        return _fail("ASTAP 输出缺少横滚（ROLL/CD 矩阵）", "parse_error")

    az = _kv_float(data, "AZ", "AZ_DEG", "AZIMUTH")
    alt = _kv_float(data, "ALT", "ALT_DEG", "ELEVATION")
    if az is None or alt is None:
        ra = _kv_float(data, "RA", "CRVAL1")
        dec = _kv_float(data, "DEC", "CRVAL2")
        if ra is None or dec is None:
            return _fail("ASTAP 输出缺少 RA/Dec（CRVAL1/CRVAL2）", "parse_error")
        observer_v, err = _parse_observer(observer)
        if err is not None:
            return _fail(f"ASTAP 解算需要观测者位置/时刻：{err}",
                         "missing_observer")
        assert observer_v is not None
        try:
            az, alt = _radec_to_azalt(ra, dec, observer_v)
        except Exception as exc:
            return _fail(f"ASTAP 赤道→地平换算失败：{type(exc).__name__}: {exc}",
                         "solver_error")

    fov_used = _kv_float(data, "FOV")
    if fov_used is None or not (0.0 < fov_used <= 180.0):
        fov_used = _astap_fov_from_cd(data, size)
    if fov_used is None or not (0.0 < fov_used <= 180.0):
        fov_used = fov

    return _success("astap", float(az), float(alt), float(roll),
                    int(matches), float(residual), float(fov_used), prior_v)


def solve_frame(stars_px: Sequence[Sequence[float]],
                image_size: Sequence[float],
                fov_deg: float,
                *,
                solver: Optional[str] = None,
                astap_path: Optional[str] = None,
                image_path: Optional[str] = None,
                prior: Optional[Mapping[str, Any]] = None,
                observer: Optional[Mapping[str, Any]] = None,
                timeout_s: float = DEFAULT_TIMEOUT_S) -> Dict[str, Any]:
    """用带 FOV 先验的星点像素做解算，自动选择可用解算器。

    输入校验先于依赖探测：星点不足或参数非法时，无论库装没装都应得到
    「输入问题」而不是笼统的「库不可用」，调用方才知道该改什么。

    Args:
        stars_px: 星点像素坐标序列，每项 ``(x, y)``，至少
            ``MIN_SOLVE_STARS`` 颗。
        image_size: 图像尺寸 ``(width, height)``，像素，必须为正。
        fov_deg: 视场直径先验（度），必须为 ``(0, 180]`` 内的有限值。
            盲解算不可靠，FOV 是必需输入而非提示。
        solver: ``None``（自动）/ ``"tetra3"`` / ``"astap"``。
        astap_path: ASTAP 可执行文件路径；None 时读 ``MBDSDR_ASTAP_PATH``。
        image_path: ASTAP 需要的图像文件；tetra3 路径忽略。
        prior: 先验位姿（az/alt/roll），用于输出修正量。
        observer: 观测者位置/时刻，用于赤道→地平换算。
        timeout_s: ASTAP 子进程超时（秒）。

    Returns:
        成功返回固定契约（见模块 docstring）；失败返回
        ``{"ok": False, "reason": ..., "error": ...}``。
    """
    if solver is not None and solver not in _SOLVERS:
        return _fail(f"未知解算器 {solver!r}；可选 {', '.join(_SOLVERS)}",
                     "bad_input")

    fov, size, err = _parse_fov_image(fov_deg, image_size)
    if err is not None:
        return _fail(err, "bad_input")
    assert fov is not None and size is not None

    if solver == "astap":
        return solve_astap(image_path, size, fov, astap_path=astap_path,
                           prior=prior, observer=observer, timeout_s=timeout_s)

    points, err = _parse_stars(stars_px)
    if err is not None:
        return _fail(err, "bad_input")
    assert points is not None
    if len(points) < MIN_SOLVE_STARS:
        return _fail((f"星点不足：检测到 {len(points)} 颗，"
                      f"解算至少需要 {MIN_SOLVE_STARS} 颗"), "too_few_stars")

    if solver is None:
        status = solver_status(astap_path)
        if not status["available"]:
            return _fail(status["reason"], "unavailable")
        solver = status["name"]

    if solver == "astap":
        return solve_astap(image_path, size, fov, astap_path=astap_path,
                           prior=prior, observer=observer, timeout_s=timeout_s)
    return solve_tetra3(points, size, fov, prior=prior, observer=observer)
