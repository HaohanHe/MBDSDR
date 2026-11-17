# SPDX-License-Identifier: MIT
"""GNSS 卫星天空几何 → 粗指向：把每颗北斗卫星当「无线电参考星」。

先厘清物理，避免又走错
----------------------
NMEA GSV 给出的每颗卫星 ``azimuth / elevation / snr`` 里：

* **方位/仰角是接收机用星历 + 自身站址 + 时间算出来的**，不是测量出来的。
  它们描述「卫星在天上哪」，不描述「你的镜筒指哪」；
* **信噪比是测量出来的**，它随天线增益方向图变化。只有它携带指向信息。

所以单天线 GNSS 想约束指向，只有一条路：**天线随镜筒转，SNR 随指向变**。
天线越定向（螺旋、阵列、带地屏的贴片），这条信息越有用；普通全向贴片
几乎不随指向变，本模块会直接判 ``not_discriminative``，不硬给一个假角度。

融合分工
--------
* GNSS：站址、时刻、卫星天空位置（绝对参考系）+ 粗指向（SNR 拟合）；
* 手机 IMU：相对转动，短时稳、长时漂；
* 视觉：绝对精校（0.1° 级）。

本模块只做「GNSS 粗指向 + 与 IMU 的一致性检查」。它不能替代视觉解算，
也不假装能。输出永远带 ``discriminative`` 与 ``reason``。

方法
----
把 SNR 拟合成 ``c + 10·n·log10(cosθ)``，θ 是卫星方向与候选镜筒指向的夹角。
在半天球上网格搜索指向，``n`` 取自一个离散集合（天线方向图陡峭度未知，不猜），
用**奇偶分半交叉验证**判断这套几何到底有没有分辨力：

* 仰角项系数只在训练半上解，留出半只做预测——否则测试半会替模型再调一次
  仰角曲线，等于白送自由度，天顶方向能凭空「拟合」出来；
* 留出集误差必须同时优于两个基线，否则 ``not_discriminative``：
  1. **仰角零模型**（只含仰角项，不含任何指向项）；
  2. **方位打乱基线**：把卫星方位随机重排后重跑同一套流程。仰角依赖会被保留，
     方位依赖被破坏；如果真实留出误差打不过它，说明方位没带来信息。
* 卫星少于 6 颗、SNR 极差小于 6 dB → 同样拒绝；
* 只有通过分辨力检验，才返回粗指向与残差。
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

_EPS = 1e-9

#: 方向图陡峭度的候选集合。真实指数由数据拟合，不预设。
_PATTERN_N = (0.5, 1.0, 2.0, 4.0, 8.0, 16.0)

#: 粗指向网格步长（度）。粗指向只需要到「哪个天区」，不需要到角分。
_GRID_STEP_DEG = 5.0

#: 判定「有分辨力」的门槛
_MIN_SATS = 6
_MIN_SNR_SPREAD_DB = 6.0
_MIN_HOLDOUT_GAIN = 0.10      # 留出误差至少要比基线好 10%

#: 方位打乱基线的重排轮数。取各轮中**最好**的一次作比较（越严越不容易误报）。
_PERMUTATION_ROUNDS = 3
_PERMUTATION_SEED = 20261116


@dataclass(frozen=True)
class GnssSatellite:
    """一颗可见卫星。``snr_db_hz`` 缺失时只是不参与拟合，不补零。"""

    constellation: str
    prn: int
    azimuth_deg: float
    elevation_deg: float
    snr_db_hz: Optional[float] = None

    def to_dict(self) -> Dict[str, object]:
        return {
            "constellation": self.constellation,
            "prn": self.prn,
            "azimuth_deg": self.azimuth_deg,
            "elevation_deg": self.elevation_deg,
            "snr_db_hz": self.snr_db_hz,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, object]) -> "GnssSatellite":
        snr = d.get("snr_db_hz")
        return cls(
            constellation=str(d.get("constellation", "")),
            prn=int(d.get("prn", 0)),
            azimuth_deg=float(d["azimuth_deg"]),
            elevation_deg=float(d["elevation_deg"]),
            snr_db_hz=float(snr) if snr is not None else None,
        )


@dataclass(frozen=True)
class SkyFit:
    """一次 SNR-指向拟合的结果。``ok=False`` 时角度必须为 None。"""

    ok: bool
    discriminative: bool
    reason: str
    satellites_used: int
    azimuth_deg: Optional[float] = None
    elevation_deg: Optional[float] = None
    pattern_exponent: Optional[float] = None
    residual_db: Optional[float] = None
    baseline_db: Optional[float] = None
    holdout_db: Optional[float] = None
    #: 只含仰角项的零模型在留出集上的误差。指向项必须明显优于它才算有信息。
    elevation_only_db: Optional[float] = None
    #: 方位打乱后的留出误差（取各轮最好）。真实留出误差必须优于它。
    azimuth_permuted_db: Optional[float] = None
    #: 粗指向的保守不确定度（度）。没通过检验时也为 None。
    uncertainty_deg: Optional[float] = None

    def to_dict(self) -> Dict[str, object]:
        return {
            "ok": self.ok,
            "discriminative": self.discriminative,
            "reason": self.reason,
            "satellites_used": self.satellites_used,
            "azimuth_deg": (round(self.azimuth_deg, 2)
                            if self.azimuth_deg is not None else None),
            "elevation_deg": (round(self.elevation_deg, 2)
                              if self.elevation_deg is not None else None),
            "pattern_exponent": self.pattern_exponent,
            "residual_db": (round(self.residual_db, 2)
                            if self.residual_db is not None else None),
            "baseline_db": (round(self.baseline_db, 2)
                            if self.baseline_db is not None else None),
            "holdout_db": (round(self.holdout_db, 2)
                           if self.holdout_db is not None else None),
            "elevation_only_db": (round(self.elevation_only_db, 2)
                                  if self.elevation_only_db is not None else None),
            "azimuth_permuted_db": (round(self.azimuth_permuted_db, 2)
                                    if self.azimuth_permuted_db is not None
                                    else None),
            "uncertainty_deg": self.uncertainty_deg,
        }


# ---------------------------------------------------------------------------
# 向量
# ---------------------------------------------------------------------------
def _unit(az_deg: float, el_deg: float) -> Tuple[float, float, float]:
    az = math.radians(az_deg)
    el = math.radians(el_deg)
    ce = math.cos(el)
    return (ce * math.sin(az), ce * math.cos(az), math.sin(el))


def _az_el(v: Sequence[float]) -> Tuple[float, float]:
    e, n, u = v
    norm = math.sqrt(e * e + n * n + u * u)
    if norm <= _EPS:
        raise ValueError("零向量没有方向")
    if u / norm >= 1.0:
        return 0.0, 90.0
    return (math.degrees(math.atan2(e, n)) % 360.0,
            math.degrees(math.asin(max(-1.0, min(1.0, u / norm)))))


def angular_separation_deg(az1: float, el1: float,
                           az2: float, el2: float) -> float:
    """两个地平坐标方向之间的夹角。"""
    v1 = _unit(az1, el1)
    v2 = _unit(az2, el2)
    dot = sum(a * b for a, b in zip(v1, v2))
    return math.degrees(math.acos(max(-1.0, min(1.0, dot))))


# ---------------------------------------------------------------------------
# 拟合
# ---------------------------------------------------------------------------
def _grid_directions() -> List[Tuple[float, float, float, float, float]]:
    out = []
    el = -10.0
    while el <= 90.0 + _EPS:
        az = 0.0
        while az < 360.0 - _EPS:
            out.append((az, el, *_unit(az, el)))
            az += _GRID_STEP_DEG
        el += _GRID_STEP_DEG
    return out


_GRID = _grid_directions()


def _det3(m: Sequence[Sequence[float]]) -> float:
    return (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
            - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
            + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))


def _solve3(A: Sequence[Sequence[float]], b: Sequence[float]) -> Tuple[float, float, float]:
    d = _det3(A)
    if abs(d) < 1e-12:
        return (b[0] / A[0][0] if abs(A[0][0]) > 1e-12 else 0.0, 0.0, 0.0)
    out = []
    for col in range(3):
        m = [list(row) for row in A]
        for r in range(3):
            m[r][col] = b[r]
        out.append(_det3(m) / d)
    return (out[0], out[1], out[2])


def _solve_nuisance(ps: Sequence[float], es: Sequence[float],
                    ys: Sequence[float]) -> Tuple[float, float, float]:
    """解 ``y = c + k1·s + k2·s² + p``，``s = sin(el)``。

    为什么用二次项：卫星越高路径越短，SNR 随仰角上升；而「指向天顶」的方向图
    也是仰角越高增益越大。只放一次项时，两者会互相冒充，拟合会假报天顶指向。
    二次项把仰角趋势吸收掉，剩下的增益才归因于指向。
    """
    A = [[0.0, 0.0, 0.0] for _ in range(3)]
    b = [0.0, 0.0, 0.0]
    for p, s, y in zip(ps, es, ys):
        row = (1.0, s, s * s)
        z = y - p
        for i in range(3):
            b[i] += row[i] * z
            for j in range(3):
                A[i][j] += row[i] * row[j]
    return _solve3(A, b)


def _nuisance_value(c: float, k1: float, k2: float, s: float) -> float:
    return c + k1 * s + k2 * s * s


def _fit_once(sats: Sequence[GnssSatellite]) -> Tuple[float, float, float, float]:
    """返回 (az, el, n, rmse_db)。"""
    vecs = [_unit(s.azimuth_deg, s.elevation_deg) for s in sats]
    ys = [float(s.snr_db_hz) for s in sats]
    es = [math.sin(math.radians(s.elevation_deg)) for s in sats]
    y_mean = sum(ys) / len(ys)
    baseline_sse = sum((y - y_mean) ** 2 for y in ys)

    # 仰角项的法方程只由 es 决定，与候选指向、指数都无关：提到两层循环外面算一次。
    A = [[0.0, 0.0, 0.0] for _ in range(3)]
    for s in es:
        row = (1.0, s, s * s)
        for i in range(3):
            for j in range(3):
                A[i][j] += row[i] * row[j]

    best = (0.0, 90.0, 1.0, math.sqrt(baseline_sse / len(ys)))
    best_sse = baseline_sse
    for az, el, be, bn, bu in _GRID:
        # 每颗卫星的「方向图项基函数」只与方向有关，指数是它的线性倍数：
        # 每个方向只算一次 log10，避免在指数循环里重复 6 遍。
        ls = [10.0 * math.log10(max(se * be + sn * bn + su * bu, 1e-3))
              for se, sn, su in vecs]
        for n in _PATTERN_N:
            b = [0.0, 0.0, 0.0]
            for s, l, y in zip(es, ls, ys):
                p = n * l
                row = (1.0, s, s * s)
                z = y - p
                for i in range(3):
                    b[i] += row[i] * z
            c, k1, k2 = _solve3(A, b)
            sse = 0.0
            for s, l, y in zip(es, ls, ys):
                r = y - (_nuisance_value(c, k1, k2, s) + n * l)
                sse += r * r
            if sse < best_sse - _EPS:
                best_sse = sse
                best = (az, el, n, math.sqrt(sse / len(ys)))
    return best


def _nuisance_coefs(sats: Sequence[GnssSatellite], az: float, el: float,
                    n: float) -> Tuple[float, float, float]:
    be, bn, bu = _unit(az, el)
    preds = []
    es = []
    for s in sats:
        se, sn, su = _unit(s.azimuth_deg, s.elevation_deg)
        preds.append(10.0 * n * math.log10(max(se * be + sn * bn + su * bu, 1e-3)))
        es.append(math.sin(math.radians(s.elevation_deg)))
    return _solve_nuisance(preds, es, [float(s.snr_db_hz) for s in sats])


def _rmse_with(sats: Sequence[GnssSatellite], az: float, el: float, n: float,
               coefs: Tuple[float, float, float]) -> float:
    """用**训练半解出的**系数在给定卫星上算误差。

    这里绝不能在测试半上重解仰角系数：那等于让模型在考卷上再调一次参数，
    半天天顶方向也能把纯仰角数据「拟合」成指向，误报为有分辨力。
    """
    c, k1, k2 = coefs
    be, bn, bu = _unit(az, el)
    errs = []
    for s in sats:
        se, sn, su = _unit(s.azimuth_deg, s.elevation_deg)
        p = 10.0 * n * math.log10(max(se * be + sn * bn + su * bu, 1e-3))
        e = math.sin(math.radians(s.elevation_deg))
        errs.append(float(s.snr_db_hz) - (_nuisance_value(c, k1, k2, e) + p))
    return math.sqrt(sum(x * x for x in errs) / len(errs))


def _cross_validate_rmse(train: Sequence[GnssSatellite],
                         test: Sequence[GnssSatellite]) -> float:
    az, el, n, _ = _fit_once(train)
    return _rmse_with(test, az, el, n, _nuisance_coefs(train, az, el, n))


def _holdout_rmse(sats: Sequence[GnssSatellite]) -> Optional[float]:
    even = list(sats[0::2])
    odd = list(sats[1::2])
    if len(even) < 3 or len(odd) < 3:
        return None
    return (_cross_validate_rmse(even, odd) + _cross_validate_rmse(odd, even)) / 2.0


def _null_rmse(train: Sequence[GnssSatellite],
               test: Sequence[GnssSatellite]) -> float:
    """仰角-only 零模型在测试集上的误差（不含任何指向项）。"""
    es = [math.sin(math.radians(s.elevation_deg)) for s in train]
    ys = [float(s.snr_db_hz) for s in train]
    c, k1, k2 = _solve_nuisance([0.0] * len(ys), es, ys)
    errs = []
    for s in test:
        e = math.sin(math.radians(s.elevation_deg))
        errs.append(float(s.snr_db_hz) - _nuisance_value(c, k1, k2, e))
    return math.sqrt(sum(x * x for x in errs) / len(errs))


def _permute_azimuths(sats: Sequence[GnssSatellite],
                      seed: int) -> List[GnssSatellite]:
    """把方位角在卫星之间随机重排：保留（仰角, SNR）配对，切断（方位, SNR）配对。"""
    rng = random.Random(seed)
    azs = [s.azimuth_deg for s in sats]
    rng.shuffle(azs)
    return [GnssSatellite(s.constellation, s.prn, az, s.elevation_deg,
                          s.snr_db_hz)
            for s, az in zip(sats, azs)]


def _permuted_holdout_rmse(sats: Sequence[GnssSatellite]) -> Optional[float]:
    """「方位不含信息」时的留出误差水平。

    方位打乱后仰角趋势原样保留、方位趋势被打散；同一套拟合若在这份数据上
    也能得到差不多的留出误差，就说明真实的那点优势不是方位带来的。
    取各轮中最好的（最小）一次作为基线，宁可漏报也不误报。
    """
    best: Optional[float] = None
    for k in range(_PERMUTATION_ROUNDS):
        h = _holdout_rmse(_permute_azimuths(sats, _PERMUTATION_SEED + k))
        if h is None:
            continue
        best = h if best is None else min(best, h)
    return best


def fit_boresight(satellites: Iterable[GnssSatellite]) -> SkyFit:
    """从 GSV 卫星 SNR 拟合镜筒（天线）指向。分辨力不足就拒绝。"""
    sats = [s for s in satellites if s.snr_db_hz is not None]
    n_sats = len(sats)
    if n_sats < _MIN_SATS:
        return SkyFit(ok=False, discriminative=False,
                      reason=f"too_few_satellites: {n_sats} < {_MIN_SATS}",
                      satellites_used=n_sats)

    snrs = [float(s.snr_db_hz) for s in sats]
    spread = max(snrs) - min(snrs)
    if spread < _MIN_SNR_SPREAD_DB:
        return SkyFit(ok=False, discriminative=False,
                      reason=f"flat_snr: 极差 {spread:.1f} dB < {_MIN_SNR_SPREAD_DB} dB",
                      satellites_used=n_sats)

    az, el, n, rmse = _fit_once(sats)
    if el < -5.0:
        return SkyFit(ok=False, discriminative=False,
                      reason=f"fit_below_horizon: el={el:.1f}°",
                      satellites_used=n_sats, residual_db=rmse)

    # 奇偶分半交叉验证：拟合一半，用训练半解出的系数去预测另一半
    holdout = _holdout_rmse(sats)
    if holdout is None:
        return SkyFit(ok=False, discriminative=False,
                      reason="holdout_too_small", satellites_used=n_sats)
    even = sats[0::2]
    odd = sats[1::2]
    null_holdout = (_null_rmse(even, odd) + _null_rmse(odd, even)) / 2.0
    permuted = _permuted_holdout_rmse(sats)

    y_mean = sum(snrs) / n_sats
    baseline = math.sqrt(sum((y - y_mean) ** 2 for y in snrs) / n_sats)
    gain_null = 1.0 - holdout / max(null_holdout, _EPS)
    gain_permuted = (1.0 - holdout / max(permuted, _EPS)
                     if permuted is not None else gain_null)
    if gain_null < _MIN_HOLDOUT_GAIN or gain_permuted < _MIN_HOLDOUT_GAIN:
        detail = (f"留出误差 {holdout:.1f} dB，仰角零模型 {null_holdout:.1f} dB"
                  + (f"，方位打乱基线 {permuted:.1f} dB"
                     if permuted is not None else ""))
        return SkyFit(ok=False, discriminative=False,
                      reason=f"not_discriminative: {detail}",
                      satellites_used=n_sats, residual_db=rmse,
                      baseline_db=baseline, holdout_db=holdout,
                      elevation_only_db=null_holdout,
                      azimuth_permuted_db=permuted)

    # 保守不确定度：留出误差折算成角度（方向图越平，角度越不确定）
    uncertainty = min(45.0, max(3.0, holdout / max(n * 0.3, 0.3) * 4.0))
    return SkyFit(ok=True, discriminative=True, reason="ok",
                  satellites_used=n_sats, azimuth_deg=az, elevation_deg=el,
                  pattern_exponent=n, residual_db=rmse, baseline_db=baseline,
                  holdout_db=holdout, elevation_only_db=null_holdout,
                  azimuth_permuted_db=permuted,
                  uncertainty_deg=round(uncertainty, 1))


# ---------------------------------------------------------------------------
# 与手机 IMU 的一致性
# ---------------------------------------------------------------------------
def compare_with_imu(
    fit: SkyFit,
    imu_azimuth_deg: Optional[float],
    imu_elevation_deg: Optional[float],
    *,
    tolerance_deg: Optional[float] = None,
) -> Dict[str, object]:
    """把 GNSS 粗指向与手机 IMU 指向对一下。

    不做「谁对谁错」的自动裁决：两者量级差太多时，只报冲突并要求复核。
    """
    if not fit.ok or fit.azimuth_deg is None or fit.elevation_deg is None:
        return {"verdict": "unusable_gnss_fit", "reason": fit.reason,
                "separation_deg": None, "action": "keep_imu_only"}
    if imu_azimuth_deg is None or imu_elevation_deg is None:
        return {"verdict": "no_imu", "separation_deg": None,
                "action": "use_gnss_coarse",
                "gnss": {"azimuth_deg": fit.azimuth_deg,
                         "elevation_deg": fit.elevation_deg,
                         "uncertainty_deg": fit.uncertainty_deg}}

    sep = angular_separation_deg(fit.azimuth_deg, fit.elevation_deg,
                                 float(imu_azimuth_deg), float(imu_elevation_deg))
    tol = (tolerance_deg if tolerance_deg is not None
           else max(10.0, (fit.uncertainty_deg or 10.0) * 1.5))
    if sep <= tol:
        return {"verdict": "agree", "separation_deg": round(sep, 2),
                "tolerance_deg": round(tol, 1), "action": "keep_fusion"}
    return {"verdict": "conflict", "separation_deg": round(sep, 2),
            "tolerance_deg": round(tol, 1), "action": "recheck_imu_and_satellite_fit",
            "hint": ("两者差得比 GNSS 粗指向的不确定度还大：先查 IMU 航向基准"
                     "（磁偏角/装夹滚转），再查天线是否真的随镜筒转")}


# ---------------------------------------------------------------------------
# 与现有 NMEA 解析器对接
# ---------------------------------------------------------------------------
_TALKER_NAMES = {
    "GP": "GPS", "GB": "BDS", "BD": "BDS", "GL": "GLONASS",
    "GA": "GAL", "GQ": "QZSS", "GI": "NAVIC",
}


def satellites_from_gsv_frames(frames) -> List[GnssSatellite]:
    """把 ``serial_gnss`` 的 GSV 帧（单帧或列表）转成拟合输入。

    GSV 帧形如 ``{"talker": "GB", "sats": [{"id", "elevation", "azimuth",
    "snr_db"}]}``；缺 elevation/azimuth 的条目跳过（不能补零）。
    """
    if isinstance(frames, dict):
        frames = [frames]
    out: List[GnssSatellite] = []
    for frame in frames or ():
        talker = str(frame.get("talker", "")).upper()
        constellation = _TALKER_NAMES.get(talker, talker or "UNKNOWN")
        for s in frame.get("sats") or ():
            el = s.get("elevation")
            az = s.get("azimuth")
            if el is None or az is None:
                continue
            raw_id = s.get("id", 0)
            try:
                prn = int(raw_id)
            except (TypeError, ValueError):
                prn = 0
            snr = s.get("snr_db")
            out.append(GnssSatellite(
                constellation=constellation, prn=prn,
                azimuth_deg=float(az), elevation_deg=float(el),
                snr_db_hz=float(snr) if snr is not None else None))
    return out


__all__ = [
    "GnssSatellite", "SkyFit", "fit_boresight", "compare_with_imu",
    "angular_separation_deg", "satellites_from_gsv_frames",
]
