#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MBDSDR 应用图标生成脚本
========================
使用 Pillow 绘制 MBDSDR（AI 定义无线电）主题图标，呼号 BI4MIB。
设计语言：低饱和浅色配色
  - 米白 #F5F3EF 背景
  - 蓝灰 #5B7B8C 主色（字母 M / 内层电波弧）
  - 橙   #C4845C 点缀（外层电波弧 / 卫星点）

输出（与本脚本同目录 assets/）：
  icon.png      256x256 主图标
  icon_48.png   48x48
  icon_16.png   16x16
  icon.ico      Windows 多尺寸图标（16/32/48/256）

用法:
  python3 generate_icon.py
"""

import os
from PIL import Image, ImageDraw

# ---- 配色 ----
CREAM = (0xF5, 0xF3, 0xEF, 255)      # 米白背景
BLUE = (0x5B, 0x7B, 0x8C, 255)       # 蓝灰主色
ORANGE = (0xC4, 0x84, 0x5C, 255)     # 橙点缀
STROKE = (0x5B, 0x7B, 0x8C, 60)      # 描边（半透明蓝灰）

S = 1024  # 超采样画布尺寸（再缩小得到抗锯齿效果）

# ---- 几何布局（基于 1024 画布）----
# 圆角方形底
BG_INSET = 36
BG_RADIUS = 240

# 字母 M
M_LEFT = (280, 430, 372, 760)    # 左竖条 (x0,y0,x1,y1)
M_RIGHT = (652, 430, 744, 760)   # 右竖条
M_VALLEY = (512, 650)            # 中间 V 谷底
M_WIDTH = 92                     # V 斜线线宽

# 无线电波弧（以上方一点为圆心向上辐射的半圆）
ARC_CENTER = (512, 400)
ARC_RADII = (70, 125, 180)       # 由内到外
ARC_WIDTH = 26
# 卫星点缀：最外层弧顶端的小圆点
SAT_DOT = (512, 400 - 180)       # = (512, 220)
SAT_R = 20


def render_master() -> Image.Image:
    """在 S x S 画布上绘制完整图标，返回 RGBA 图像。"""
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 1) 圆角方形底色块
    d.rounded_rectangle(
        [BG_INSET, BG_INSET, S - BG_INSET, S - BG_INSET],
        radius=BG_RADIUS, fill=CREAM, outline=STROKE, width=8,
    )

    # 2) 字母 M：左右竖条
    d.rectangle(M_LEFT, fill=BLUE)
    d.rectangle(M_RIGHT, fill=BLUE)
    # 中间 V 斜撑（粗折线连接左竖条顶内 -> 谷底 -> 右竖条顶内）
    d.line(
        [(M_LEFT[2], M_LEFT[1]), M_VALLEY, (M_RIGHT[0], M_RIGHT[1])],
        fill=BLUE, width=M_WIDTH,
    )

    # 3) 无线电波弧：内层两弧蓝灰，最外一弧橙色
    cx, cy = ARC_CENTER
    for i, r in enumerate(ARC_RADII):
        bbox = [cx - r, cy - r, cx + r, cy + r]
        color = ORANGE if i == len(ARC_RADII) - 1 else BLUE
        d.arc(bbox, start=180, end=360, fill=color, width=ARC_WIDTH)

    # 4) 卫星点缀小圆点（橙）
    d.ellipse(
        [SAT_DOT[0] - SAT_R, SAT_DOT[1] - SAT_R,
         SAT_DOT[0] + SAT_R, SAT_DOT[1] + SAT_R],
        fill=ORANGE,
    )
    return img


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    master = render_master()

    targets = {
        "icon.png": (256, 256),
        "icon_48.png": (48, 48),
        "icon_16.png": (16, 16),
    }
    for name, size in targets.items():
        out = master.resize(size, Image.LANCZOS)
        path = os.path.join(here, name)
        out.save(path, format="PNG")
        print(f"[ok] {path}  {size[0]}x{size[1]}")

    # Windows .ico：单文件内嵌 16/32/48/256 多尺寸
    ico_path = os.path.join(here, "icon.ico")
    master.resize((256, 256), Image.LANCZOS).save(
        ico_path, format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48), (256, 256)],
    )
    print(f"[ok] {ico_path}  ICO(16/32/48/256)")


if __name__ == "__main__":
    main()
