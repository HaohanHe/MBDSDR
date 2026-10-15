# SPDX-License-Identifier: MIT
"""VNA 单端口报告渲染：log-mag + Smith 双面板 PNG（第十六批）。

复用 spectrum_snapshot 的 PNG 核心（_png_rgb/_draw_text，私有名跨模块
导入已注释说明，后续如需第三处复用再提升为公共模块——两处先不造抽象）。

Smith 图口径：Γ 单位圆盘直接映射（横=Re Γ，纵=−Im Γ，容性朝下）；
网格画常数 r 圆（0.5/1/2/∞）与常数 x 弧（±0.5/±1/±2）——画法为参数
方程逐点，不依赖绘图库。迹线按 (Re,Im) 落点，|Γ|>1（无源网络不应出现；
数值越界）钳到盘沿并如实计数。

诚实边界：单端口 S11 报告（S21/Smith 导纳族不在本批）；网格为参考线
非精确坐标纸；无抗锯齿（与频谱快照同口径）。
"""
from __future__ import annotations

from typing import Tuple

import numpy as np

from .spectrum_snapshot import _draw_text, _png_rgb

__all__ = ["render_s11_report_png"]

_GRID = (128, 128, 128)
_TRACE = (20, 60, 200)
_BG = (255, 255, 255)


def _panel_logmag(img: np.ndarray, f, s11, rect) -> None:
    x0, y0, x1, y1 = rect
    db = 20.0 * np.log10(np.maximum(np.abs(s11), 1e-9))
    top, lo = 0.0, -60.0
    img[y0:y1, x0] = (0, 0, 0)
    img[y1 - 1, x0:x1] = (0, 0, 0)
    for k in range(4):
        lvl = top + (lo - top) * k / 3.0
        yy = y0 + int(round((k / 3.0) * (y1 - y0 - 1)))
        img[yy, x0 - 3:x0] = (0, 0, 0)
        _draw_text(img, max(0, x0 - 28), yy - 3, f"{lvl:.0f}")
    _draw_text(img, x0 - 28, y0 - 8, "dB")
    for i in range(len(f)):
        fx = x0 + int(round((i / max(1, len(f) - 1)) * (x1 - x0 - 1)))
        yy = (db[i] - top) / (lo - top)
        yy = min(max(yy, 0.0), 1.0)
        fy = y0 + int(round(yy * (y1 - y0 - 1)))
        img[fy, max(x0, min(fx, x1 - 1))] = _TRACE


def _smith_point(gamma: complex, cx: float, cy: float, r: float
                 ) -> Tuple[int, int]:
    gx = min(max(gamma.real, -1.0), 1.0)
    gy = min(max(-gamma.imag, -1.0), 1.0)      # 容性朝下
    return int(round(cx + gx * r)), int(round(cy + gy * r))


def _panel_smith(img: np.ndarray, s11, rect, ) -> dict:
    x0, y0, x1, y1 = rect
    cx = (x0 + x1) / 2.0
    cy = (y0 + y1) / 2.0
    r = min(x1 - x0, y1 - y0) / 2.0 - 2
    # 外圆与实轴
    for deg in range(360):
        a = np.radians(deg)
        px, py = int(round(cx + r * np.cos(a))), int(round(cy + r * np.sin(a)))
        img[py, px] = _GRID
    img[int(cy), x0 + 1:x1 - 1] = _GRID
    img[y0 + 1:y1 - 1, int(cx)] = (220, 220, 220)
    # 常数 r 圆：圆心 (r0/(1+r0), 0)，半径 1/(1+r0)（归一化盘坐标）
    for r0 in (0.5, 1.0, 2.0):
        c = r0 / (1.0 + r0)
        rad = 1.0 / (1.0 + r0)
        for deg in range(0, 360):
            a = np.radians(deg)
            gx, gy = c + rad * np.cos(a), rad * np.sin(a)
            if gx * gx + gy * gy <= 1.0:
                px = int(round(cx + gx * r))
                py = int(round(cy + gy * r))
                if x0 <= px < x1 and y0 <= py < y1:
                    img[py, px] = _GRID
    # 常数 x 弧：圆心 (1, 1/x)，半径 1/|x|；取盘内弧
    for x0c in (0.5, 1.0, 2.0, -0.5, -1.0, -2.0):
        rad = 1.0 / abs(x0c)
        ccx, ccy = 1.0, 1.0 / x0c
        for deg in range(0, 360):
            a = np.radians(deg)
            gx, gy = ccx + rad * np.cos(a), ccy + rad * np.sin(a)
            if gx * gx + gy * gy <= 1.0 and gy * (1 if x0c > 0 else -1) >= 0:
                px = int(round(cx + gx * r))
                py = int(round(cy + gy * r))
                if x0 <= px < x1 and y0 <= py < y1:
                    img[py, px] = (200, 200, 200)
    # 迹线
    clipped = 0
    for g in s11:
        if abs(g) > 1.0:
            clipped += 1
        px, py = _smith_point(complex(g), cx, cy, r)
        if x0 <= px < x1 and y0 <= py < y1:
            img[py, px] = _TRACE
    _draw_text(img, x0 + 2, y1 - 10, "Smith")
    return {"clipped_points": clipped}


def render_s11_report_png(freqs_hz, s11, width: int = 800,
                          height: int = 420) -> bytes:
    """单端口报告 PNG：左 log-mag（0..−60dB）右 Smith，返回 PNG 字节。
    越界钳位统计走 render_s11_report（不在这个热路径里返 dict）。"""
    f = np.asarray(freqs_hz, dtype=np.float64)
    s = np.asarray(s11, dtype=np.complex128)
    if len(f) != len(s) or len(f) < 2:
        raise ValueError("freqs/s11 须等长且 >=2 点")
    if np.any(~np.isfinite(f)) or np.any(~np.isfinite(s)):
        raise ValueError("输入含非有限值")
    if width < 320 or height < 200:
        raise ValueError("画布过小（最小 320x200）")
    img = np.full((height, width, 3), 255, dtype=np.uint8)
    mid = width // 2
    _panel_logmag(img, f, s, (36, 16, mid - 8, height - 8))
    _panel_smith(img, s, (mid + 8, 8, width - 8, height - 8))
    return _png_rgb(img)


def render_s11_report(freqs_hz, s11, width: int = 800, height: int = 420
                      ) -> Tuple[bytes, dict]:
    """渲染 + 元数据（越界钳位计数等），供工具面如实上报。"""
    f = np.asarray(freqs_hz, dtype=np.float64)
    s = np.asarray(s11, dtype=np.complex128)
    clipped = int(np.sum(np.abs(s) > 1.0))
    return render_s11_report_png(f, s, width, height), {
        "clipped_points": clipped, "points": len(s)}
