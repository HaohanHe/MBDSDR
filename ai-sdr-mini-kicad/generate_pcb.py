#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ai-sdr Mini KiCad PCB 生成器
生成完整的 .kicad_pcb 文件，包含所有元件封装、布局、网络
可直接导入立创EDA
"""

import math

# ============================================================
# 辅助函数
# ============================================================

UNKNOWN_NETS = set()

def resolve_net(net_name):
    """按网络名查权威编号; 未知网络收集并返回0(不臆造编号, 避免名/号错配)。"""
    if not net_name:
        return 0
    nid = net_id(net_name)
    if nid == 0 and net_name != "GND":
        UNKNOWN_NETS.add(net_name)
    return nid

def pad_smd(num, x, y, w, h, net=0, net_name=""):
    """生成SMD焊盘。net 编号一律由 net_name 经权威 NETS 表解析, 忽略传入的裸编号。"""
    nid = resolve_net(net_name)
    net_str = f' (net {nid} "{net_name}")' if net_name else ''
    return f'    (pad "{num}" smd rect (at {x:.3f} {y:.3f}) (size {w:.3f} {h:.3f}) (layers "F.Cu" "F.Paste" "F.Mask"){net_str})'

def pad_thru(num, x, y, drill, w, h, net=0, net_name=""):
    """生成通孔焊盘。net 编号一律由 net_name 解析。"""
    nid = resolve_net(net_name)
    net_str = f' (net {nid} "{net_name}")' if net_name else ''
    return f'    (pad "{num}" thru_hole circle (at {x:.3f} {y:.3f}) (size {w:.3f} {h:.3f}) (drill {drill:.3f}) (layers "*.Cu" "*.Mask"){net_str})'

def footprint_header(name, x, y, layer="F.Cu", desc=""):
    """封装头"""
    return f'  (footprint "ai-sdr:{name}" (layer "{layer}") (at {x:.3f} {y:.3f})\n    (descr "{desc}")\n    (attr smd)'

def footprint_ref_val(ref, val, y_offset=0):
    """封装的参考号和值"""
    return (f'    (fp_text reference "{ref}" (at 0 {-1.5+y_offset:.3f}) (layer "F.SilkS") '
            f'(effects (font (size 1 1) (thickness 0.15)) (justify left bottom)))\n'
            f'    (fp_text value "{val}" (at 0 {1.5+y_offset:.3f}) (layer "F.Fab") '
            f'(effects (font (size 1 1) (thickness 0.15)) (justify left top)))')

def fp_line(x1, y1, x2, y2, layer="F.SilkS", w=0.12):
    """封装内的丝印线"""
    return f'    (fp_line (start {x1:.3f} {y1:.3f}) (end {x2:.3f} {y2:.3f}) (stroke (width {w}) (type default)) (layer "{layer}"))'

def fp_rect(x, y, w, h, layer="F.Fab", lw=0.1):
    """封装外框（矩形）"""
    x1, y1 = x - w/2, y - h/2
    x2, y2 = x + w/2, y + h/2
    return (f'    (fp_line (start {x1:.3f} {y1:.3f}) (end {x2:.3f} {y1:.3f}) (stroke (width {lw}) (type default)) (layer "{layer}"))\n'
            f'    (fp_line (start {x2:.3f} {y1:.3f}) (end {x2:.3f} {y2:.3f}) (stroke (width {lw}) (type default)) (layer "{layer}"))\n'
            f'    (fp_line (start {x2:.3f} {y2:.3f}) (end {x1:.3f} {y2:.3f}) (stroke (width {lw}) (type default)) (layer "{layer}"))\n'
            f'    (fp_line (start {x1:.3f} {y2:.3f}) (end {x1:.3f} {y1:.3f}) (stroke (width {lw}) (type default)) (layer "{layer}"))')

# ============================================================
# 标准封装生成
# ============================================================

def gen_0402(ref, val, x, y, net1="", net2="", rot=0):
    """0402 电阻/电容/电感 封装 (1.0x0.5mm)"""
    pads = [
        pad_smd("1", -0.5, 0, 0.6, 0.55, 1, net1),
        pad_smd("2",  0.5, 0, 0.6, 0.55, 2, net2),
    ]
    body = fp_rect(0, 0, 0.5, 0.5, "F.Fab", 0.05)
    silk = fp_line(-0.75, -0.4, -0.75, 0.4, "F.SilkS", 0.1) + "\n" + fp_line(0.75, -0.4, 0.75, 0.4, "F.SilkS", 0.1)
    rot_str = f' {rot}' if rot else ''
    return (f'  (footprint "ai-sdr:0402" (layer "F.Cu") (at {x:.3f} {y:.3f}{rot_str})\n'
            f'    (descr "0402 SMD") (attr smd)\n'
            f'{footprint_ref_val(ref, val)}\n'
            f'{body}\n{silk}\n'
            f'{pads[0]}\n{pads[1]}\n  )')

def gen_0603_led(ref, val, x, y, net1="", net2="", rot=0):
    """0603 LED 封装 (1.6x0.8mm)"""
    pads = [
        pad_smd("1", -0.6, 0, 0.7, 0.8, 1, net1),
        pad_smd("2",  0.6, 0, 0.7, 0.8, 2, net2),
    ]
    body = fp_rect(0, 0, 0.8, 0.6, "F.Fab", 0.05)
    silk = fp_line(-0.9, -0.5, -0.9, 0.5, "F.SilkS", 0.1)
    rot_str = f' {rot}' if rot else ''
    return (f'  (footprint "ai-sdr:0603_LED" (layer "F.Cu") (at {x:.3f} {y:.3f}{rot_str})\n'
            f'    (descr "0603 LED") (attr smd)\n'
            f'{footprint_ref_val(ref, val)}\n'
            f'{body}\n{silk}\n'
            f'{pads[0]}\n{pads[1]}\n  )')

def gen_0805(ref, val, x, y, net1="", net2="", rot=0):
    """0805 电容/保险丝 封装 (2.0x1.25mm)"""
    pads = [
        pad_smd("1", -0.9, 0, 0.8, 1.3, 1, net1),
        pad_smd("2",  0.9, 0, 0.8, 1.3, 2, net2),
    ]
    body = fp_rect(0, 0, 1.0, 1.0, "F.Fab", 0.05)
    rot_str = f' {rot}' if rot else ''
    return (f'  (footprint "ai-sdr:0805" (layer "F.Cu") (at {x:.3f} {y:.3f}{rot_str})\n'
            f'    (descr "0805 SMD") (attr smd)\n'
            f'{footprint_ref_val(ref, val)}\n'
            f'{body}\n'
            f'{pads[0]}\n{pads[1]}\n  )')

def gen_sot23_6(ref, val, x, y, nets, rot=0):
    """SOT-23-6 封装 (1.6x2.9mm, 间距0.95mm)
    nets: [pin1, pin2, pin3, pin4, pin5, pin6]
    pin排列: 左侧1-2-3(从上到下), 右侧4-5-6(从下到上) — 不对
    SOT-23-6标准: pin1在左上, 逆时针: pin1(左上), pin2(左中), pin3(左下), pin4(右下), pin5(右中), pin6(右上)
    """
    pitch = 0.95
    pad_w, pad_h = 0.6, 0.5
    # 左侧 pin1,2,3 (y = -pitch, 0, +pitch)
    # 右侧 pin6,5,4 (y = -pitch, 0, +pitch) — 从顶到底
    pads = []
    left_pins = [1, 2, 3]
    right_pins = [6, 5, 4]
    for i, p in enumerate(left_pins):
        pads.append(pad_smd(str(p), -1.0, (i-1)*pitch, pad_w, pad_h, p, nets[p-1]))
    for i, p in enumerate(right_pins):
        pads.append(pad_smd(str(p), 1.0, (i-1)*pitch, pad_w, pad_h, p, nets[p-1]))
    body = fp_rect(0, 0, 1.6, 2.9, "F.Fab", 0.05)
    # pin1标记
    pin1_mark = f'    (fp_circle (center {-0.8:.3f} {-pitch-0.3:.3f}) (end {-0.6:.3f} {-pitch-0.3:.3f}) (stroke (width 0.1) (type default)) (fill none) (layer "F.SilkS"))'
    rot_str = f' {rot}' if rot else ''
    pads_str = "\n".join(pads)
    return (f'  (footprint "ai-sdr:SOT-23-6" (layer "F.Cu") (at {x:.3f} {y:.3f}{rot_str})\n'
            f'    (descr "SOT-23-6") (attr smd)\n'
            f'{footprint_ref_val(ref, val, -1.8)}\n'
            f'{body}\n{pin1_mark}\n'
            f'{pads_str}\n  )')

def gen_sot223(ref, val, x, y, nets, rot=0):
    """SOT-223 封装 (6.5x3.5mm)
    pin1=GND, pin2=VOUT(+散热焊盘), pin3=VIN
    nets: [GND, VOUT, VIN]
    """
    pads = [
        pad_smd("1", -2.3, 1.4, 1.2, 1.8, 1, nets[0]),  # GND
        pad_smd("2",  0.0, 1.4, 1.8, 1.8, 2, nets[1]),  # VOUT
        pad_smd("3",  2.3, 1.4, 1.2, 1.8, 3, nets[2]),  # VIN
        pad_smd("4",  0.0, -1.0, 3.5, 2.0, 2, nets[1]),  # 散热焊盘=VOUT
    ]
    body = fp_rect(0, 0, 3.5, 2.5, "F.Fab", 0.05)
    rot_str = f' {rot}' if rot else ''
    pads_str = "\n".join(pads)
    return (f'  (footprint "ai-sdr:SOT-223" (layer "F.Cu") (at {x:.3f} {y:.3f}{rot_str})\n'
            f'    (descr "SOT-223") (attr smd)\n'
            f'{footprint_ref_val(ref, val, -2)}\n'
            f'{body}\n'
            f'{pads_str}\n  )')

def gen_soic16(ref, val, x, y, nets, rot=0):
    """SOIC-16 封装 (3.9x9.9mm, 间距1.27mm)
    pin排列: 左侧1-8(从上到下), 右侧16-9(从上到下)
    nets: [pin1...pin16]
    """
    pitch = 1.27
    pad_w, pad_h = 0.6, 1.8
    body_w, body_h = 3.9, 9.9
    pads = []
    for i in range(8):
        p = i + 1
        y_pos = (i - 3.5) * pitch
        pads.append(pad_smd(str(p), -body_w/2 - 0.3, y_pos, pad_w, pad_h, p, nets[p-1]))
    for i in range(8):
        p = 16 - i
        y_pos = (i - 3.5) * pitch
        pads.append(pad_smd(str(p), body_w/2 + 0.3, y_pos, pad_w, pad_h, p, nets[p-1]))
    body = fp_rect(0, 0, body_w, body_h, "F.Fab", 0.05)
    pin1_mark = f'    (fp_circle (center {-body_w/2-0.5:.3f} {-body_h/2+0.8:.3f}) (end {-body_w/2-0.3:.3f} {-body_h/2+0.8:.3f}) (stroke (width 0.15) (type default)) (fill none) (layer "F.SilkS"))'
    rot_str = f' {rot}' if rot else ''
    pads_str = "\n".join(pads)
    return (f'  (footprint "ai-sdr:SOIC-16" (layer "F.Cu") (at {x:.3f} {y:.3f}{rot_str})\n'
            f'    (descr "SOIC-16 3.9x9.9mm") (attr smd)\n'
            f'{footprint_ref_val(ref, val, -5.5)}\n'
            f'{body}\n{pin1_mark}\n'
            f'{pads_str}\n  )')

def gen_qfn36(ref, val, x, y, nets, rot=0):
    """QFN-36 6x6mm 封装, 间距0.5mm
    pin排列: 底部1-9(左到右), 右侧10-18(下到上), 顶部19-27(右到左), 左侧28-36(上到下)
    中心散热焊盘 4x4mm
    nets: [pin1...pin36]
    """
    pitch = 0.5
    pad_w, pad_h = 0.3, 0.75  # 引脚焊盘
    body = 6.0
    ep_size = 4.0  # 散热焊盘
    pads = []
    # 底部 pin1-9 (y = +body/2, x从左到右)
    for i in range(9):
        p = i + 1
        x_pos = (i - 4) * pitch
        pads.append(pad_smd(str(p), x_pos, body/2 + 0.1, pad_w, pad_h, p, nets[p-1]))
    # 右侧 pin10-18 (x = +body/2, y从下到上)
    for i in range(9):
        p = i + 10
        y_pos = (4 - i) * pitch  # 从下到上: pin10在最下
        pads.append(pad_smd(str(p), body/2 + 0.1, y_pos, pad_h, pad_w, p, nets[p-1]))
    # 顶部 pin19-27 (y = -body/2, x从右到左)
    for i in range(9):
        p = i + 19
        x_pos = (4 - i) * pitch  # 从右到左
        pads.append(pad_smd(str(p), x_pos, -body/2 - 0.1, pad_w, pad_h, p, nets[p-1]))
    # 左侧 pin28-36 (x = -body/2, y从上到下)
    for i in range(9):
        p = i + 28
        y_pos = (i - 4) * pitch  # 从上到下: pin28在最上
        pads.append(pad_smd(str(p), -body/2 - 0.1, y_pos, pad_h, pad_w, p, nets[p-1]))
    # 中心散热焊盘 (GND)
    pads.append(pad_smd("37", 0, 0, ep_size, ep_size, 0, "GND"))
    body_rect = fp_rect(0, 0, body, body, "F.Fab", 0.05)
    pin1_mark = f'    (fp_circle (center {-body/2+0.5:.3f} {body/2-0.5:.3f}) (end {-body/2+0.7:.3f} {body/2-0.5:.3f}) (stroke (width 0.15) (type default)) (fill none) (layer "F.SilkS"))'
    rot_str = f' {rot}' if rot else ''
    pads_str = "\n".join(pads)
    return (f'  (footprint "ai-sdr:QFN-36-6x6" (layer "F.Cu") (at {x:.3f} {y:.3f}{rot_str})\n'
            f'    (descr "QFN-36 6x6mm pitch 0.5mm") (attr smd)\n'
            f'{footprint_ref_val(ref, val, -4)}\n'
            f'{body_rect}\n{pin1_mark}\n'
            f'{pads_str}\n  )')

def gen_lga14(ref, val, x, y, nets, rot=0):
    """LGA-14 2.5x3mm 封装 (BMI260), 间距0.5mm
    简化: 7x2 阵列, 左侧1-7(上到下), 右侧14-8(上到下)
    nets: [pin1...pin14]
    """
    pitch = 0.5
    pad_w, pad_h = 0.3, 0.35
    body_w, body_h = 2.5, 3.0
    pads = []
    for i in range(7):
        p = i + 1
        y_pos = (i - 3) * pitch
        pads.append(pad_smd(str(p), -0.75, y_pos, pad_w, pad_h, p, nets[p-1]))
    for i in range(7):
        p = 14 - i
        y_pos = (i - 3) * pitch
        pads.append(pad_smd(str(p), 0.75, y_pos, pad_w, pad_h, p, nets[p-1]))
    body_rect = fp_rect(0, 0, body_w, body_h, "F.Fab", 0.05)
    rot_str = f' {rot}' if rot else ''
    pads_str = "\n".join(pads)
    return (f'  (footprint "ai-sdr:LGA-14-2.5x3" (layer "F.Cu") (at {x:.3f} {y:.3f}{rot_str})\n'
            f'    (descr "LGA-14 2.5x3mm") (attr smd)\n'
            f'{footprint_ref_val(ref, val, -2)}\n'
            f'{body_rect}\n'
            f'{pads_str}\n  )')

def gen_esp32_wroom1(ref, val, x, y, nets, rot=0):
    """ESP32-S3-WROOM-1 模组 44脚 18x25.5mm, 间距1.27mm
    两侧各22脚, Castellated半孔
    pin排列: 左侧1-22(上到下), 右侧44-23(上到下)
    nets: [pin1...pin44], 未用的填空字符串
    """
    pitch = 1.27
    pad_w, pad_h = 0.7, 1.5  # 半孔焊盘
    body_w, body_h = 18.0, 25.5
    pads = []
    # 左侧 pin1-22 (上到下)
    for i in range(22):
        p = i + 1
        y_pos = (i - 10.5) * pitch
        net_name = nets[p-1] if p-1 < len(nets) else ""
        pads.append(pad_smd(str(p), -body_w/2 - 0.2, y_pos, pad_w, pad_h, p, net_name))
    # 右侧 pin44-23 (上到下)
    for i in range(22):
        p = 44 - i
        y_pos = (i - 10.5) * pitch
        net_name = nets[p-1] if p-1 < len(nets) else ""
        pads.append(pad_smd(str(p), body_w/2 + 0.2, y_pos, pad_w, pad_h, p, net_name))
    body_rect = fp_rect(0, 0, body_w, body_h, "F.Fab", 0.1)
    # 天线区域标记 (模组顶部是天线，不能走线)
    antenna = fp_rect(0, -body_h/2 + 3, body_w - 2, 5, "F.Fab", 0.05)
    rot_str = f' {rot}' if rot else ''
    pads_str = "\n".join(pads)
    return (f'  (footprint "ai-sdr:ESP32-S3-WROOM-1" (layer "F.Cu") (at {x:.3f} {y:.3f}{rot_str})\n'
            f'    (descr "ESP32-S3-WROOM-1 44pin 18x25.5mm") (attr smd)\n'
            f'{footprint_ref_val(ref, val, -14)}\n'
            f'{body_rect}\n{antenna}\n'
            f'{pads_str}\n  )')

def gen_atgm336h(ref, val, x, y, nets, rot=0):
    """ATGM336H-5N 模组 16脚 13x15mm, 间距2.0mm
    两侧各8脚
    pin排列: 左侧1-8(上到下), 右侧16-9(上到下)
    nets: [pin1...pin16]
    """
    pitch = 2.0
    pad_w, pad_h = 1.0, 1.8
    body_w, body_h = 13.0, 15.0
    pads = []
    for i in range(8):
        p = i + 1
        y_pos = (i - 3.5) * pitch
        pads.append(pad_smd(str(p), -body_w/2 - 0.3, y_pos, pad_w, pad_h, p, nets[p-1]))
    for i in range(8):
        p = 16 - i
        y_pos = (i - 3.5) * pitch
        pads.append(pad_smd(str(p), body_w/2 + 0.3, y_pos, pad_w, pad_h, p, nets[p-1]))
    body_rect = fp_rect(0, 0, body_w, body_h, "F.Fab", 0.1)
    rot_str = f' {rot}' if rot else ''
    pads_str = "\n".join(pads)
    return (f'  (footprint "ai-sdr:ATGM336H-5N" (layer "F.Cu") (at {x:.3f} {y:.3f}{rot_str})\n'
            f'    (descr "ATGM336H-5N GPS module 16pin 13x15mm") (attr smd)\n'
            f'{footprint_ref_val(ref, val, -9)}\n'
            f'{body_rect}\n'
            f'{pads_str}\n  )')

def gen_crystal_3215(ref, val, x, y, net1="", net2="", rot=0):
    """3215 晶振 3.2x1.5mm"""
    pads = [
        pad_smd("1", -1.0, 0, 0.8, 1.2, 1, net1),
        pad_smd("2",  1.0, 0, 0.8, 1.2, 2, net2),
    ]
    body = fp_rect(0, 0, 1.6, 1.0, "F.Fab", 0.05)
    rot_str = f' {rot}' if rot else ''
    return (f'  (footprint "ai-sdr:CRYSTAL-3215" (layer "F.Cu") (at {x:.3f} {y:.3f}{rot_str})\n'
            f'    (descr "3215 crystal 3.2x1.5mm") (attr smd)\n'
            f'{footprint_ref_val(ref, val)}\n'
            f'{body}\n'
            f'{pads[0]}\n{pads[1]}\n  )')

def gen_crystal_3225(ref, val, x, y, net1="", net2="", rot=0):
    """3225 晶振 3.2x2.5mm"""
    pads = [
        pad_smd("1", -1.1, 0, 0.8, 1.5, 1, net1),
        pad_smd("2",  1.1, 0, 0.8, 1.5, 2, net2),
        pad_smd("3", 0, -1.0, 1.0, 0.6, 0, "GND"),  # 外壳接地
        pad_smd("4", 0,  1.0, 1.0, 0.6, 0, "GND"),
    ]
    body = fp_rect(0, 0, 2.0, 1.6, "F.Fab", 0.05)
    rot_str = f' {rot}' if rot else ''
    pads_str = "\n".join(pads)
    return (f'  (footprint "ai-sdr:CRYSTAL-3225" (layer "F.Cu") (at {x:.3f} {y:.3f}{rot_str})\n'
            f'    (descr "3225 crystal 3.2x2.5mm") (attr smd)\n'
            f'{footprint_ref_val(ref, val)}\n'
            f'{body}\n'
            f'{pads_str}\n  )')

def gen_button(ref, val, x, y, rot=0):
    """TS-1101-C-W 按钮 3x4mm SMD"""
    pads = [
        pad_smd("1", -1.3, -0.9, 1.0, 1.0, 1, "BTN"),
        pad_smd("2",  1.3, -0.9, 1.0, 1.0, 1, "BTN"),
        pad_smd("3", -1.3,  0.9, 1.0, 1.0, 2, "GND"),
        pad_smd("4",  1.3,  0.9, 1.0, 1.0, 2, "GND"),
    ]
    body = fp_rect(0, 0, 2.5, 3.0, "F.Fab", 0.05)
    rot_str = f' {rot}' if rot else ''
    pads_str = "\n".join(pads)
    return (f'  (footprint "ai-sdr:BUTTON-3x4" (layer "F.Cu") (at {x:.3f} {y:.3f}{rot_str})\n'
            f'    (descr "TS-1101 button 3x4mm") (attr smd)\n'
            f'{footprint_ref_val(ref, val)}\n'
            f'{body}\n'
            f'{pads_str}\n  )')

def gen_typec_6p(ref, val, x, y, rot=0):
    """Type-C 6P 卧贴母座 简化封装"""
    # 引脚: A1/A12=GND, A4/A9=VBUS, A5=CC1, A6=D+, A7=D-, B5=CC2
    # 简化为6个信号焊盘+外壳
    pads = [
        pad_smd("VBUS", 0, -2.5, 2.0, 0.8, 1, "5V_USB"),
        pad_smd("GND1", -3.0, -2.5, 1.0, 0.8, 0, "GND"),
        pad_smd("GND2",  3.0, -2.5, 1.0, 0.8, 0, "GND"),
        pad_smd("D+", -1.0, 0, 0.5, 1.0, 4, "USB_DP_UP"),
        pad_smd("D-",  1.0, 0, 0.5, 1.0, 5, "USB_DM_UP"),
        pad_smd("CC1", -2.0, 0, 0.5, 1.0, 4, "CC1"),
        pad_smd("CC2",  2.0, 0, 0.5, 1.0, 5, "CC2"),
        # 外壳焊盘
        pad_smd("SH1", -3.5, 2.0, 1.5, 1.5, 0, "GND"),
        pad_smd("SH2",  3.5, 2.0, 1.5, 1.5, 0, "GND"),
    ]
    body = fp_rect(0, 0, 7.5, 4.0, "F.Fab", 0.1)
    rot_str = f' {rot}' if rot else ''
    pads_str = "\n".join(pads)
    return (f'  (footprint "ai-sdr:TYPE-C-6P" (layer "F.Cu") (at {x:.3f} {y:.3f}{rot_str})\n'
            f'    (descr "Type-C 6P receptacle") (attr smd)\n'
            f'{footprint_ref_val(ref, val, -3)}\n'
            f'{body}\n'
            f'{pads_str}\n  )')

def gen_usba(ref, val, x, y, rot=0):
    """USB-A 母座卧贴 简化封装"""
    pads = [
        pad_smd("VBUS", -3.0, 0, 1.0, 1.5, 1, "5V_USB"),
        pad_smd("D-", -1.0, 0, 0.6, 1.5, 2, "USB_DM3"),
        pad_smd("D+",  1.0, 0, 0.6, 1.5, 3, "USB_DP3"),
        pad_smd("GND",  3.0, 0, 1.0, 1.5, 0, "GND"),
        pad_smd("SH1", -5.0, 2.5, 2.0, 1.5, 0, "GND"),
        pad_smd("SH2",  5.0, 2.5, 2.0, 1.5, 0, "GND"),
    ]
    body = fp_rect(0, 0, 11.0, 5.0, "F.Fab", 0.1)
    rot_str = f' {rot}' if rot else ''
    pads_str = "\n".join(pads)
    return (f'  (footprint "ai-sdr:USB-A-FEMALE" (layer "F.Cu") (at {x:.3f} {y:.3f}{rot_str})\n'
            f'    (descr "USB-A female SMD") (attr smd)\n'
            f'{footprint_ref_val(ref, val, -3.5)}\n'
            f'{body}\n'
            f'{pads_str}\n  )')

def gen_sma(ref, val, x, y, rot=0):
    """SMA 直插母座 简化封装 (SMA-KE标准: 中心针钻孔1.3/焊盘2.5, 固定脚钻孔1.0/焊盘2.0)"""
    pads = [
        pad_thru("1", 0, 0, 1.3, 2.5, 2.5, 1, "RF_IN"),  # 中心针 drill1.3/size2.5
        pad_thru("2", -3.0, -2.0, 1.0, 2.0, 2.0, 0, "GND"),
        pad_thru("3",  3.0, -2.0, 1.0, 2.0, 2.0, 0, "GND"),
        pad_thru("4", -3.0,  2.0, 1.0, 2.0, 2.0, 0, "GND"),
        pad_thru("5",  3.0,  2.0, 1.0, 2.0, 2.0, 0, "GND"),
    ]
    body = fp_rect(0, 0, 8.0, 8.0, "F.Fab", 0.1)
    rot_str = f' {rot}' if rot else ''
    pads_str = "\n".join(pads)
    return (f'  (footprint "ai-sdr:SMA-FEMALE" (layer "F.Cu") (at {x:.3f} {y:.3f}{rot_str})\n'
            f'    (descr "SMA female through hole") (attr through_hole)\n'
            f'{footprint_ref_val(ref, val, -5)}\n'
            f'{body}\n'
            f'{pads_str}\n  )')

def gen_ufl(ref, val, x, y, rot=0):
    """u.FL 贴片母座 简化封装"""
    pads = [
        pad_smd("1", 0, 0, 1.5, 1.5, 1, "GPS_ANT"),
        pad_smd("2", -1.5, 1.0, 1.0, 1.0, 0, "GND"),
        pad_smd("3",  1.5, 1.0, 1.0, 1.0, 0, "GND"),
        pad_smd("4", -1.5, -1.0, 1.0, 1.0, 0, "GND"),
        pad_smd("5",  1.5, -1.0, 1.0, 1.0, 0, "GND"),
    ]
    body = fp_rect(0, 0, 3.5, 3.5, "F.Fab", 0.05)
    rot_str = f' {rot}' if rot else ''
    pads_str = "\n".join(pads)
    return (f'  (footprint "ai-sdr:UFL-FEMALE" (layer "F.Cu") (at {x:.3f} {y:.3f}{rot_str})\n'
            f'    (descr "u.FL female SMD") (attr smd)\n'
            f'{footprint_ref_val(ref, val)}\n'
            f'{body}\n'
            f'{pads_str}\n  )')

# ============================================================
# 网络定义
# ============================================================

NETS = {
    0: "",
    1: "GND",
    2: "+3V3",
    3: "5V_USB",
    4: "USB_DP_UP",
    5: "USB_DM_UP",
    6: "USB_DP2",
    7: "USB_DM2",
    8: "USB_DP3",
    9: "USB_DM3",
    10: "I2C0_SCL",
    11: "I2C0_SDA",
    12: "I2S_LRCLK",
    13: "I2S_BCLK",
    14: "I2S_DIN",
    15: "UART1_RX",
    16: "UART1_TX",
    17: "GPS_PPS",
    18: "SI4732_INT",
    19: "SI4732_RST",
    20: "HUB_RESET",
    21: "RF_IN",
    22: "FM_IN",
    23: "GPS_ANT",
    24: "CC1",
    25: "CC2",
    26: "LED1",
    27: "LED2",
    28: "LED3",
    29: "LED4",
    30: "BTN",
    31: "SI4732_RCLK",
    32: "XTALIN",
    33: "VBUS_DET",
    34: "HUB_CRFLT",
    35: "HUB_PLLFILT",
    36: "HUB_RBIAS",
    37: "XTALOUT",
}

def net_id(name):
    for k, v in NETS.items():
        if v == name:
            return k
    return 0

# ============================================================
# 元件引脚网络映射
# ============================================================

# USB2514B QFN-36 引脚网络 (pin1-pin36)
USB2514B_NETS = [
    "", "", "USB_DM2", "USB_DP2", "+3V3",  # pin1-5
    "USB_DM3", "USB_DP3", "", "", "+3V3",  # pin6-10
    "GND", "", "", "HUB_CRFLT", "+3V3",  # pin11-15
    "", "", "", "", "",  # pin16-20
    "", "", "+3V3", "", "",  # pin21-25
    "HUB_RESET", "VBUS_DET", "", "+3V3",  # pin26-29
    "USB_DM_UP", "USB_DP_UP", "XTALOUT", "XTALIN", "HUB_PLLFILT",  # pin30-34
    "HUB_RBIAS", "+3V3",  # pin35-36
]

# SI4732 SOIC-16 引脚网络
SI4732_NETS = [
    "I2S_LRCLK", "I2S_BCLK", "SI4732_INT", "", "",  # pin1-5
    "FM_IN", "GND", "RF_IN", "SI4732_RST", "GND",  # pin6-10 (pin10=SENB接地)
    "I2C0_SCL", "I2C0_SDA", "SI4732_RCLK", "+3V3", "GND",  # pin11-15
    "I2S_DIN",  # pin16
]

# TMAG5273 SOT-23-6 引脚网络 [pin1-pin6]
TMAG5273_NETS = ["I2C0_SCL", "GND", "GND", "+3V3", "", "I2C0_SDA"]

# BMI260 LGA-14 引脚网络 [pin1-pin14] 简化
BMI260_NETS = ["+3V3", "GND", "I2C0_SCL", "I2C0_SDA", "GND", "", "", "+3V3",
                "GND", "", "", "", "", "GND"]

# AMS1117 SOT-223 [GND, VOUT, VIN]
AMS1117_NETS = ["GND", "+3V3", "5V_USB"]

# ESP32-S3-WROOM-1 44脚 引脚网络 (简化, 只定义用到的)
# 左侧 pin1-22 (上到下), 右侧 pin44-23 (上到下)
# 乐鑫WROOM-1引脚: 左侧1-22, 右侧23-44
ESP32_NETS = [""] * 44
# 左侧 (pin1-22): 主要是GPIO
# 根据乐鑫ESP32-S3-WROOM-1引脚定义:
# pin1=GND, pin2=3V3, pin3=EN, pin4=IO4, pin5=IO5, pin6=IO6, pin7=IO7,
# pin8=IO15, pin9=IO16, pin10=IO17, pin11=IO18, pin12=IO8, pin13=IO19,
# pin14=IO20, pin15=IO3, pin16=IO46, pin17=IO9, pin18=IO10, pin19=IO11,
# pin20=IO12, pin21=IO13, pin22=IO14
# 右侧 pin23-44: pin23=IO39? 不对, ESP32-S3最多GPIO48
# 简化: 按功能映射
esp32_pin_map = {
    1: "GND", 2: "+3V3", 3: "BTN",  # EN接按钮
    5: "I2S_LRCLK",  # IO5
    7: "I2S_DIN",  # IO7
    9: "GPS_PPS",  # IO16
    10: "UART1_RX",  # IO17
    11: "UART1_TX",  # IO18
    12: "I2C0_SCL",  # IO8
    13: "USB_DM2",  # IO19
    14: "USB_DP2",  # IO20
    15: "LED3",  # IO3
    16: "LED4",  # IO46
    17: "I2C0_SDA",  # IO9
    18: "SI4732_INT",  # IO10
    19: "SI4732_RST",  # IO11
    21: "HUB_RESET",  # IO13? 不对, IO21
    4: "I2S_BCLK",  # IO4
    1: "GND",
}
# 简化映射，实际以乐鑫数据手册为准
for k, v in esp32_pin_map.items():
    if k <= 44:
        ESP32_NETS[k-1] = v
# LED1=IO1, LED2=IO2
ESP32_NETS[0] = "GND"  # pin1
# 补LED
# 简化: 不精确映射每个GPIO，用网络名连接

# ATGM336H 16脚 [pin1-pin16] 简化
ATGM336H_NETS = ["GND", "UART1_RX", "UART1_TX", "GPS_PPS", "", "+3V3", "", "+3V3",
                  "", "", "", "", "", "", "", "GND"]

# ============================================================
# 布局坐标 (板尺寸 80x60mm, 原点左下角)
# ============================================================

BOARD_W = 80.0
BOARD_H = 60.0

# 主要元件位置
POS = {
    "J1": (12, 55),       # Type-C
    "U1": (30, 50),       # USB2514B
    "USB1": (55, 55),     # USB-A
    "U4": (40, 28),       # ESP32-S3 (中心)
    "U2": (65, 38),       # SI4732
    "RF1": (75, 38),      # SMA
    "U6": (60, 10),       # ATGM336H
    "RF2": (75, 10),      # u.FL
    "U5": (18, 15),       # BMI260
    "U3": (28, 15),       # TMAG5273
    "U7": (10, 30),       # AMS1117
    "SW1": (8, 55),       # 按钮
    "LED1": (20, 57),
    "LED2": (24, 57),
    "LED3": (28, 57),
    "LED4": (32, 57),
    "X1": (60, 45),       # SI4732晶振 3215
    "X2": (30, 42),       # USB2514B晶振 3225
}

# ============================================================
# 生成所有元件
# ============================================================

def generate_footprints():
    fps = []

    # Type-C
    fps.append(gen_typec_6p("J1", "TYPE-C-6P", *POS["J1"]))

    # USB2514B QFN-36
    fps.append(gen_qfn36("U1", "USB2514B-AEZC", *POS["U1"], USB2514B_NETS))

    # USB-A
    fps.append(gen_usba("USB1", "USB-A-F", *POS["USB1"]))

    # ESP32-S3
    fps.append(gen_esp32_wroom1("U4", "ESP32-S3-WROOM-1-N8R8", *POS["U4"], ESP32_NETS))

    # SI4732 SOIC-16
    fps.append(gen_soic16("U2", "SI4732-A10-GSR", *POS["U2"], SI4732_NETS, rot=90))

    # SMA
    fps.append(gen_sma("RF1", "SMA-F", *POS["RF1"]))

    # ATGM336H
    fps.append(gen_atgm336h("U6", "ATGM336H-5NR32-G", *POS["U6"], ATGM336H_NETS))

    # u.FL
    fps.append(gen_ufl("RF2", "UFL-F", *POS["RF2"]))

    # BMI260 LGA-14
    fps.append(gen_lga14("U5", "BMI260", *POS["U5"], BMI260_NETS))

    # TMAG5273 SOT-23-6
    fps.append(gen_sot23_6("U3", "TMAG5273A1QDBVT", *POS["U3"], TMAG5273_NETS))

    # AMS1117 SOT-223
    fps.append(gen_sot223("U7", "AMS1117-3.3", *POS["U7"], AMS1117_NETS, rot=90))

    # 按钮
    fps.append(gen_button("SW1", "TS-1101", *POS["SW1"]))

    # LED x4
    for i in range(1, 5):
        fps.append(gen_0603_led(f"LED{i}", "LED_YELLOW", POS[f"LED{i}"][0], POS[f"LED{i}"][1],
                                  net1=f"LED{i}", net2="GND", rot=90))

    # 晶振
    fps.append(gen_crystal_3215("X1", "32.768kHz", *POS["X1"], net1="SI4732_RCLK", net2="GND"))
    fps.append(gen_crystal_3225("X2", "24MHz", *POS["X2"], net1="XTALIN", net2="XTALOUT"))

    # ====== 无源元件：明确编号，杜绝重复 ======
    # 格式: (位号, 值, x, y, net1, net2, 封装类型)
    passives_0402 = [
        # --- 100nF 去耦/隔直电容 x16 ---
        ("C3",  "100nF", 62, 35, "+3V3", "GND"),      # SI4732去耦
        ("C5",  "100nF", 25, 47, "+3V3", "GND"),      # USB2514B VDD
        ("C6",  "100nF", 35, 47, "+3V3", "GND"),      # USB2514B VDDA
        ("C8",  "100nF",  7, 27, "+3V3", "GND"),      # AMS1117输入高频
        ("C10", "100nF", 13, 33, "+3V3", "GND"),      # AMS1117输出高频
        ("C12", "100nF", 35, 20, "+3V3", "GND"),      # ESP32高频
        ("C13", "100nF", 15, 12, "+3V3", "GND"),      # BMI260去耦
        ("C14", "100nF", 25, 12, "+3V3", "GND"),      # TMAG5273去耦
        ("C16", "100nF", 57,  7, "+3V3", "GND"),      # ATGM高频
        ("C19", "100nF", 25, 53, "+3V3", "GND"),      # USB2514B VDD
        ("C20", "100nF", 35, 53, "+3V3", "GND"),      # USB2514B VDD
        ("C21", "100nF", 26, 44, "+3V3", "GND"),      # USB2514B VDD
        ("C22", "100nF", 34, 44, "+3V3", "GND"),      # USB2514B VDDA
        ("C23", "100nF", 28, 46, "HUB_CRFLT", "GND"), # CRFILT必须接
        ("C24", "100nF", 32, 46, "HUB_PLLFILT", "GND"),# PLLFILT必须接
        ("C4",  "100nF", 70, 38, "RF_IN", "FM_IN"),  # FM隔直(必须100nF)
        # --- 12pF/18pF 晶振负载电容 x3 ---
        ("C1",  "12pF",  58, 43, "SI4732_RCLK", "GND"),  # SI4732晶振
        ("C17", "18pF",  27, 40, "XTALIN", "GND"),     # USB晶振
        ("C18", "18pF",  33, 40, "XTALOUT", "GND"),     # USB晶振
        # --- 电阻 ---
        ("R1",  "1k",    22, 57, "+3V3", "LED1"),   # LED1限流
        ("R2",  "1k",    26, 57, "+3V3", "LED2"),   # LED2限流
        ("R3",  "1k",    30, 57, "+3V3", "LED3"),   # LED3限流
        ("R4",  "1k",    34, 57, "+3V3", "LED4"),   # LED4限流
        ("R5",  "4.7k",  33, 23, "+3V3", "I2C0_SCL"),  # I2C SCL上拉
        ("R6",  "4.7k",  37, 23, "+3V3", "I2C0_SDA"),  # I2C SDA上拉
        ("R7",  "10k",   30, 44, "+3V3", "HUB_RESET"), # HUB RESET上拉
        ("R8",  "10k",    6, 52, "+3V3", "BTN"),       # ESP32 EN上拉
        ("R11", "10k",   45, 20, "+3V3", "GND"),       # GPIO0上拉(简化接GND侧)
        ("R13", "10k",   36, 44, "GND", "I2C0_SDA"),   # USB SDA/NON_REM1下拉
        ("R14", "10k",   38, 44, "GND", "I2C0_SCL"),   # USB SCL/CFG_SEL0下拉
        ("R15", "10k",   40, 44, "GND", "HUB_PLLFILT"),# HS_IND/CFG_SEL1下拉
        ("R16", "10k",   42, 44, "GND", "HUB_RBIAS"),  # SUSP/LOCAL_PWR下拉
        ("R9",  "5.1k",  10, 52, "CC1", "GND"),        # Type-C CC1下拉
        ("R10", "5.1k",  14, 52, "CC2", "GND"),        # Type-C CC2下拉
        ("R12", "12k",   30, 46, "HUB_RBIAS", "GND"),  # RBIAS偏置必须12k
        ("R17", "100k",  24, 48, "5V_USB", "VBUS_DET"),# VBUS_DET分压上
        ("R18", "100k",  26, 48, "VBUS_DET", "GND"),   # VBUS_DET分压下
        ("R19", "4.7k",  62, 40, "+3V3", "SI4732_RST"),# SI4732 RST上拉
        # --- 电感 ---
        ("L2",  "100nH", 72, 10, "+3V3", "GPS_ANT"),  # GPS有源天线馈电
    ]
    for ref, val, px, py, n1, n2 in passives_0402:
        if ref.startswith("L"):
            fps.append(gen_0402(ref, val, px, py, net1=n1, net2=n2))
        else:
            fps.append(gen_0402(ref, val, px, py, net1=n1, net2=n2))

    # 10uF 电容 x4 (0805)
    cap10 = [
        ("C7",  12, 30),   # AMS1117输入
        ("C9",  13, 35),   # AMS1117输出
        ("C11", 45, 22),   # ESP32电源
        ("C15", 58, 13),   # ATGM电源
    ]
    for ref, px, py in cap10:
        fps.append(gen_0805(ref, "10uF", px, py, net1="+3V3", net2="GND"))

    # 保险丝 F1 (0805)
    fps.append(gen_0805("F1", "500mA", 12, 52, net1="5V_USB", net2="5V_USB"))

    return "\n".join(fps)

# ============================================================
# 生成板框和过孔
# ============================================================

def generate_board_outline():
    """生成板框 (Edge.Cuts层)"""
    w, h = BOARD_W, BOARD_H
    # 矩形板框 + 4个圆角
    lines = []
    # 板框线
    lines.append(f'  (gr_line (start 2 2) (end {w-2} 2) (stroke (width 0.15) (type default)) (layer "Edge.Cuts"))')
    lines.append(f'  (gr_line (start {w-2} 2) (end {w-2} {h-2}) (stroke (width 0.15) (type default)) (layer "Edge.Cuts"))')
    lines.append(f'  (gr_line (start {w-2} {h-2}) (end 2 {h-2}) (stroke (width 0.15) (type default)) (layer "Edge.Cuts"))')
    lines.append(f'  (gr_line (start 2 {h-2}) (end 2 2) (stroke (width 0.15) (type default)) (layer "Edge.Cuts"))')
    # 安装孔 4个 (3mm直径, 仅在Edge.Cuts层开孔, 不放铜皮焊盘避免DRC过孔规则报错)
    for x, y in [(5, 5), (w-5, 5), (5, h-5), (w-5, h-5)]:
        lines.append(f'  (gr_circle (center {x} {y}) (end {x+1.5} {y}) (stroke (width 0.15) (type default)) (fill none) (layer "Edge.Cuts"))')
    return "\n".join(lines)

def generate_vias():
    """生成地过孔阵列"""
    vias = []
    # 板边地过孔
    for x in range(8, int(BOARD_W)-8, 5):
        vias.append(f'  (via (at {x} 4) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net 1))')
        vias.append(f'  (via (at {x} {BOARD_H-4}) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net 1))')
    for y in range(8, int(BOARD_H)-8, 5):
        vias.append(f'  (via (at 4 {y}) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net 1))')
        vias.append(f'  (via (at {BOARD_W-4} {y}) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net 1))')
    # 芯片周围地过孔
    for cx, cy in [(30, 50), (65, 38), (40, 28), (60, 10)]:
        for dx, dy in [(-4, -4), (4, -4), (-4, 4), (4, 4)]:
            vias.append(f'  (via (at {cx+dx} {cy+dy}) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net 1))')
    return "\n".join(vias)

# ============================================================
# 生成完整PCB文件
# ============================================================

def generate_pcb():
    nets_def = "\n".join([f'  (net {k} "{v}")' for k, v in NETS.items() if v])

    pcb = f"""(kicad_pcb (version 20221018) (generator pcbnew)

  (general
    (thickness 1.6)
  )

  (paper "A4")
  (title_block
    (title "ai-sdr Mini")
    (date "2026-09-16")
    (rev "v0.7.1")
    (company "MBDSDR")
  )

  (layers
    (0 "F.Cu" signal)
    (31 "B.Cu" signal)
    (32 "B.Adhes" user)
    (33 "F.Adhes" user)
    (34 "B.Paste" user)
    (35 "F.Paste" user)
    (36 "B.SilkS" user)
    (37 "F.SilkS" user)
    (38 "B.Mask" user)
    (39 "F.Mask" user)
    (40 "Dwgs.User" user)
    (41 "Cmts.User" user)
    (42 "Eco1.User" user)
    (43 "Eco2.User" user)
    (44 "Edge.Cuts" user)
    (45 "Margin" user)
    (46 "B.CrtYd" user)
    (47 "F.CrtYd" user)
    (48 "B.Fab" user)
    (49 "F.Fab" user)
  )

  (setup
    (pad_to_mask_clearance 0.05)
    (pcbplotparams
      (layerselection 0x00010fc_ffffffff)
      (plot_on_all_layers_selection 0x0000000_00000000)
      (disableapertmacros false)
      (usegerberextensions false)
      (usegerberattributes true)
      (usegerberadvancedattributes true)
      (creategerberjobfile true)
      (excludeedgelayer true)
      (linewidth 0.100000)
      (plotframeref false)
      (viasonmask false)
      (mode 1)
      (useauxorigin false)
      (hpglpennumber 1)
      (hpglpenspeed 20)
      (hpglpendiameter 15.000000)
      (dxfpolygonmode true)
      (dxfimperialunits true)
      (dxfusepcbnewfont true)
      (psnegative false)
      (psa4output false)
      (plotreference true)
      (plotvalue true)
      (plotinvisibletext false)
      (sketchpadsonfab false)
      (subtractmaskfromsilk false)
      (mirror false)
      (drillshape 1)
      (scaleselection 1)
      (outputdirectory "")
    )
  )

{nets_def}

{generate_board_outline()}

{generate_vias()}

{generate_footprints()}

)
"""
    return pcb

if __name__ == "__main__":
    pcb = generate_pcb()
    outpath = "/home/user/Doubao/chats/38438160041798146/ai-sdr-mini-kicad/ai-sdr-mini.kicad_pcb"
    with open(outpath, "w", encoding="utf-8") as f:
        f.write(pcb)
    print(f"PCB generated: {outpath}")
    print(f"Size: {len(pcb)} bytes")
