#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""几何重叠检测 + SVG 布局预览(自检用)。import 生成器复用其数据。"""
import sys, math, importlib.util
spec=importlib.util.spec_from_file_location("gs","./generate_schematic.py")
gs=importlib.util.module_from_spec(spec); spec.loader.exec_module(gs)

def body_box(ref):
    libid,value,x,y,fpov=gs.INST[ref]
    coords=gs.lib_pin_coords(libid)
    xs=[p[0] for p in coords.values()]; ys=[p[1] for p in coords.values()]
    # body 边界 = 引脚根部(去掉引脚长度), 再加少量余量
    if libid.startswith("MBDSDR"):
        rec=None
        for d in gs.LIB_DEFS:
            if d[0]==libid: rec=d
        lp,rp,bp=rec[4],rec[5],rec[6]
        n=max(len(lp),len(rp)); hh=(n-1)*gs.GRID/2+gs.GRID*0.8
        ml=max([len(str(p[1])) for p in lp+rp] or [4]); hw=max(10.16,min(20.32,ml*1.6))
        return (x-hw,y-hh,x+hw,y+hh)
    # 两端/LED/开关/晶振
    xmin=min(xs)+gs.PLEN-1.0; xmax=max(xs)-gs.PLEN+1.0
    ymin=min(ys)+2.0; ymax=max(ys)-2.0
    return (x+xmin, y+ymin if False else y-4, x+xmax, y+4)

boxes={r:body_box(r) for r in gs.INST}
def overlap(a,b): return not (a[2]<b[0] or b[2]<a[0] or a[3]<b[1] or b[3]<a[1])
keys=list(boxes)
issues=[]
for i in range(len(keys)):
    for j in range(i+1,len(keys)):
        if overlap(boxes[keys[i]],boxes[keys[j]]):
            issues.append((keys[i],keys[j]))
print("重叠元件对:", issues if issues else "无")

# ---- SVG 预览 ----
S=1.6; W=int(594*S); H=int(420*S)
def X(v): return v*S
def Y(v): return v*S
col={"ic":"#ffffff","conn":"#e8f0fe","passive":"#f6f6f6","led":"#fff7e0","xtal":"#eef7ee","sw":"#f3e8fd"}
def kind(libid):
    if libid in("Device:R","Device:C","Device:C8","Device:L","Device:Fuse"):return "passive"
    if libid=="Device:LED":return "led"
    if "Crystal" in libid:return "xtal"
    if "Switch" in libid:return "sw"
    if libid.startswith("MBDSDR") and ("TYPEC" in libid or "USB" in libid or "SMA" in libid or "UFL" in libid):return "conn"
    return "ic"
svg=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="sans-serif">']
svg.append(f'<rect width="{W}" height="{H}" fill="#fbfbf8"/>')
svg.append(f'<rect x="{X(10)}" y="{Y(10)}" width="{X(574)}" height="{Y(400)}" fill="none" stroke="#ccc" stroke-dasharray="3,3"/>')
# 先画引脚短线+网络点
for ref in gs.INST:
    libid,value,x,y,_=gs.INST[ref]
    for pin,(px,py,ao) in gs.lib_pin_coords(libid).items():
        net=gs.NET.get((ref,pin))
        ax,ay=x+px,y+py; a=math.radians(ao)
        ex,ey=ax+2.54*math.cos(a), ay+2.54*math.sin(a)
        svg.append(f'<line x1="{X(ax):.1f}" y1="{Y(ay):.1f}" x2="{X(ex):.1f}" y2="{Y(ey):.1f}" stroke="#999" stroke-width="0.6"/>')
        if net and net!="NC":
            c="#d33" if net in("+3V3","+5V","VBUS") else ("#070" if net=="GND" else "#357")
            svg.append(f'<circle cx="{X(ex):.1f}" cy="{Y(ey):.1f}" r="1.1" fill="{c}"/>')
# 元件 body
for ref in keys:
    libid,value,x,y,_=gs.INST[ref]
    x0,y0,x1,y1=boxes[ref]; kd=kind(libid)
    svg.append(f'<rect x="{X(x0):.1f}" y="{Y(y0):.1f}" width="{X(x1-x0):.1f}" height="{Y(y1-y0):.1f}" rx="2" fill="{col[kd]}" stroke="#444" stroke-width="0.8"/>')
    svg.append(f'<text x="{X(x):.1f}" y="{Y(y0-1.5):.1f}" font-size="5.5" text-anchor="middle" fill="#b00" font-weight="bold">{ref}</text>')
    svg.append(f'<text x="{X(x):.1f}" y="{Y(y1+4.5):.1f}" font-size="4" text-anchor="middle" fill="#333">{value}</text>')
svg.append('</svg>')
open("schematic_layout_preview.svg","w",encoding="utf-8").write("\n".join(svg))
print("已写 schematic_layout_preview.svg  元件数:",len(keys))
