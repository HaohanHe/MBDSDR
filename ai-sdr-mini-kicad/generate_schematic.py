#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MBDSDR ai-sdr Mini  v0.7.1  ->  KiCad 7 原理图 (.kicad_sch) 数据驱动生成器
================================================================================
设计原则:
  * 所有符号内嵌在 (lib_symbols ...) 内 -> 单文件自包含, 不依赖外部符号库,
    Altium Designer (File > Import Wizard > KiCad) 与 立创EDA(导入KiCad) 都不丢符号。
  * 每个引脚经一段 2.54mm 短导线 (wire) 连接到一个 global_label;
    同名 global_label 即同一网络 -> 网表由"连接表"数据驱动, 不依赖手绘走线, 网络绝对正确。
  * 所有元件 rot=0, 引脚只在左右两侧(少数 GND/EP 在底部), 引脚坐标可精确计算。
  * NC 引脚放 (no_connect) 标记, 避免 ERC 浮空警告。
  * 严格对应《原理图连接表-v0.7》: 62 元件 / 37 网络。
运行: python3 generate_schematic.py
"""
import uuid as _uuid

NS = "http://www.w3.org/2001/XMLSchema-instance"

# ----------------------------------------------------------------------------
# UUID 确定性生成
# ----------------------------------------------------------------------------
_ucount = [0]
def u():
    # 标准 RFC4122 UUID(8-4-4-4-12), 用 uuid5 确定性生成: 合法、唯一、可复现
    _ucount[0] += 1
    return str(_uuid.uuid5(_uuid.NAMESPACE_DNS, f"mbdsdr-aisdr-mini-{_ucount[0]}"))

# 网络形状
PWR = "power"; BIDIR = "bidirectional"; IN = "input"; OUT = "output"; TRI = "tri_down"

# ----------------------------------------------------------------------------
# 元件库(符号)定义
#   IC 符号: left=[(num,name,type),... 上->下], right=[(num,name,type),... 上->下]
#   pin type: passive/input/output/power_in/power_out
# ----------------------------------------------------------------------------
GRID = 2.54
PLEN = 3.81           # 引脚长度
FZ = 1.27            # 字号

def pin_line(x, y, ang_out, name, num, ptype="passive"):
    # 入参为图纸坐标(y 向下为正)与图纸朝外方向 ang_out(左180/右0/下90/上270)。
    # KiCad 库符号 y 向上, 实例化时 y 取反, 故写库时 y->-y;
    # 库引脚角度: 左0/右180/下90/上270。
    ylib = -y
    lib_ang = {180: 0, 0: 180, 90: 90, 270: 270}.get(ang_out, ang_out)
    return (f'      (pin {ptype} line (at {x:.2f} {ylib:.2f} {lib_ang}) (length {PLEN}) '
            f'(name "{name}" (effects (font (size {FZ} {FZ})))) '
            f'(number "{num}" (effects (font (size {FZ} {FZ})))))\n')

def ic_lib(libid, refpref, value, footprint, left, right, bottom=None,
           halfw=None, datasheet="~", desc=""):
    n = max(len(left), len(right))
    halfh = (n - 1) * GRID / 2 + GRID * 0.8
    if halfw is None:
        maxlen = max([len(str(p[1])) for p in left+right] or [4])
        halfw = max(10.16, min(20.32, maxlen*1.6))
    s = f'    (symbol "{libid}" (pin_names (offset 1.016)) (in_bom yes) (on_board yes)\n'
    s += f'      (property "Reference" "{refpref}" (at 0 {-halfh-2.5:.2f} 0) (effects (font (size {FZ} {FZ}) bold)))\n'
    s += f'      (property "Value" "{value}" (at 0 {halfh+2.5:.2f} 0) (effects (font (size {FZ} {FZ}) bold)))\n'
    s += f'      (property "Footprint" "{footprint}" (at 0 0 0) (effects hide))\n'
    s += f'      (property "Datasheet" "{datasheet}" (at 0 0 0) (effects hide))\n'
    if desc:
        s += f'      (property "Description" "{desc}" (at 0 0 0) (effects hide))\n'
    s += f'      (rectangle (start {-halfw:.2f} {-halfh:.2f}) (end {halfw:.2f} {halfh:.2f}) (stroke (width 0.254) (type default)) (fill (type background)))\n'
    def ypos(i, cnt):
        return -(cnt-1)*GRID/2 + i*GRID
    for i,(num,name,pt) in enumerate(left):
        y = ypos(i,len(left)); x = -halfw-PLEN
        s += pin_line(x, y, 180, name, num, pt)
    for i,(num,name,pt) in enumerate(right):
        y = ypos(i,len(right)); x = halfw+PLEN
        s += pin_line(x, y, 0, name, num, pt)
    if bottom:
        for j,(num,name,pt) in enumerate(bottom):
            x = (j-(len(bottom)-1)/2)*2*GRID
            s += pin_line(x, halfh+PLEN, 90, name, num, pt)
    s += '    )\n'
    return s

def twopin_lib(libid, refpref, value, footprint, kind="rect", datasheet="~"):
    """两端元件, 引脚在左右: pin1 左, pin2 右。kind: rect/R/C/fuse"""
    s = f'    (symbol "{libid}" (in_bom yes) (on_board yes)\n'
    s += f'      (property "Reference" "{refpref}" (at 0 -5.08 0) (effects (font (size {FZ} {FZ}))))\n'
    s += f'      (property "Value" "{value}" (at 0 5.08 0) (effects (font (size {FZ} {FZ}))))\n'
    s += f'      (property "Footprint" "{footprint}" (at 0 0 0) (effects hide))\n'
    s += f'      (property "Datasheet" "{datasheet}" (at 0 0 0) (effects hide))\n'
    if kind == "R":
        s += ('      (polyline (pts (xy -2.032 0) (xy -1.524 0.762) (xy -1.016 -0.762) '
              '(xy -0.508 0.762) (xy 0  -0.762) (xy 0.508 0.762) (xy 1.016 -0.762) '
              '(xy 1.524 0.762) (xy 2.032 0)) (stroke (width 0.254) (type default)) (fill (type none)))\n')
    elif kind == "C":
        s += ('      (polyline (pts (xy -0.762 -1.524) (xy -0.762 1.524)) (stroke (width 0.508) (type default)) (fill (type none)))\n'
              '      (polyline (pts (xy 0.762 -1.524) (xy 0.762 1.524)) (stroke (width 0.508) (type default)) (fill (type none)))\n')
    elif kind == "fuse":
        s += ('      (rectangle (start -2.032 -1.016) (end 2.032 1.016) (stroke (width 0.254) (type default)) (fill (type none)))\n'
              '      (polyline (pts (xy -2.032 0) (xy 2.032 0)) (stroke (width 0.254) (type default)) (fill (type none)))\n')
    else:  # rect (电感/通用)
        s += ('      (rectangle (start -2.032 -1.016) (end 2.032 1.016) (stroke (width 0.254) (type default)) (fill (type none)))\n')
    s += pin_line(-3.81, 0, 180, "~", "1")
    s += pin_line( 3.81, 0, 0, "~", "2")
    s += '    )\n'
    return s

def led_lib():
    return f'''    (symbol "Device:LED" (in_bom yes) (on_board yes)
      (property "Reference" "D" (at 0 -5.08 0) (effects (font (size {FZ} {FZ}))))
      (property "Value" "LED" (at 0 5.08 0) (effects (font (size {FZ} {FZ}))))
      (property "Footprint" "LED_SMD:LED_0603_1608Metric" (at 0 0 0) (effects hide))
      (property "Datasheet" "~" (at 0 0 0) (effects hide))
      (polyline (pts (xy -1.27 1.27) (xy -1.27 -1.27) (xy 1.27 0) (xy -1.27 1.27)) (stroke (width 0.254) (type default)) (fill (type none)))
      (polyline (pts (xy -0.254 1.778) (xy 0.762 2.794)) (stroke (width 0.254) (type default)) (fill (type none)))
      (polyline (pts (xy 0.254 1.778) (xy 1.27 2.794)) (stroke (width 0.254) (type default)) (fill (type none)))
{pin_line(-3.81,0,180,"K","1")}{pin_line(3.81,0,0,"A","2")}    )
'''

def sw_lib():
    # 图纸 y-down: 引脚热点在 y=+1.27(下); 库 y-up 写库时图形/触点 y 取负
    return f'''    (symbol "Switch:SW_Push" (in_bom yes) (on_board yes)
      (property "Reference" "SW" (at 0 -5.08 0) (effects (font (size {FZ} {FZ}))))
      (property "Value" "SW_Push" (at 0 5.08 0) (effects (font (size {FZ} {FZ}))))
      (property "Footprint" "Button_Switch_SMD:SW_SPST_TS1101" (at 0 0 0) (effects hide))
      (property "Datasheet" "~" (at 0 0 0) (effects hide))
      (polyline (pts (xy -2.54 -1.27) (xy -2.54 1.27)) (stroke (width 0) (type default)) (fill (type none)))
      (polyline (pts (xy 2.54 -1.27) (xy 2.54 1.27)) (stroke (width 0) (type default)) (fill (type none)))
      (circle (center -2.54 -1.27) (radius 0.508) (stroke (width 0) (type default)) (fill (type none)))
      (circle (center 2.54 -1.27) (radius 0.508) (stroke (width 0) (type default)) (fill (type none)))
      (polyline (pts (xy -2.54 -1.27) (xy 2.0 1.0)) (stroke (width 0.254) (type default)) (fill (type none)))
{pin_line(-5.08,1.27,180,"1","1")}{pin_line(5.08,1.27,0,"2","2")}    )
'''

def xtal4_lib():
    return f'''    (symbol "Device:Crystal4" (in_bom yes) (on_board yes)
      (property "Reference" "Y" (at 0 -5.08 0) (effects (font (size {FZ} {FZ}))))
      (property "Value" "Crystal" (at 0 5.08 0) (effects (font (size {FZ} {FZ}))))
      (property "Footprint" "Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm" (at 0 0 0) (effects hide))
      (property "Datasheet" "~" (at 0 0 0) (effects hide))
      (rectangle (start -3.81 -2.54) (end 3.81 2.54) (stroke (width 0.254) (type default)) (fill (type background)))
      (polyline (pts (xy -1.27 -1.27) (xy -1.27 1.27)) (stroke (width 0.508) (type default)) (fill (type none)))
      (polyline (pts (xy 1.27 -1.27) (xy 1.27 1.27)) (stroke (width 0.508) (type default)) (fill (type none)))
{pin_line(-6.35,0,180,"1","1")}{pin_line(6.35,0,0,"3","3")}{pin_line(0,-5.08,270,"2","2")}{pin_line(0,5.08,90,"4","4")}    )
'''

# ----------------------------------------------------------------------------
# 符号库清单 (libid, 左引脚数, 右引脚数, 底部引脚数) —— 与下面实例必须一致
# ----------------------------------------------------------------------------
P=lambda *a: a  # (num,name,type)

LIB_DEFS = []

# U1 USB2514B (QFN36+EP)
u1_left = [
 ("1","USBDM_DN1","passive"),("2","USBDP_DN1","passive"),("3","USBDM_DN2","passive"),
 ("4","USBDP_DN2","passive"),("5","VDD33","power_in"),("6","USBDM_DN3","passive"),
 ("7","USBDP_DN3","passive"),("8","USBDM_DN4","passive"),("9","USBDP_DN4","passive"),
 ("10","VDDA33","power_in"),("11","TEST","power_in"),("12","PRTPWR1","passive"),
 ("13","OCS_N1","passive"),("14","CRFILT","passive"),("15","VDD33","power_in"),
 ("16","PRTPWR2","passive"),("17","OCS_N2","passive"),("18","PRTPWR3","passive"),
]
u1_right = [  # 上->下 = 36..19
 ("36","VDD33","power_in"),("35","RBIAS","passive"),("34","PLLFILT","passive"),
 ("33","XTALIN","passive"),("32","XTALOUT","passive"),("31","USBDP_UP","passive"),
 ("30","USBDM_UP","passive"),("29","VDDA33","power_in"),("28","LOCAL_PWR","passive"),
 ("27","VBUS_DET","passive"),("26","RESET_N","input"),("25","CFG_SEL1","input"),
 ("24","CFG_SEL0","input"),("23","VDD33","power_in"),("22","NON_REM1","passive"),
 ("21","NC","passive"),("20","PRTPWR4","passive"),("19","OCS_N3","passive"),
]
u1_bottom=[("37","EP","power_in")]
LIB_DEFS.append(("MBDSDR:USB2514B","U","USB2514B-AEZC-TR","Package_DFN_QFN:QFN-36-1EP_6x6mm_P0.5mm_EP4.1x4.1mm",
                 u1_left,u1_right,u1_bottom))

# U2 SI4732 SOIC16
u2_left=[
 ("1","LOUT/DFS","output"),("2","GPO3/DCLK","output"),("3","GPO2/INTB","output"),
 ("4","GPO1","passive"),("5","NC","passive"),("6","FMI","input"),
 ("7","RFGND","power_in"),("8","AMI","input"),
]
u2_right=[
 ("16","ROUT/DOUT","output"),("15","GND","power_in"),("14","VDD","power_in"),
 ("13","RCLK","input"),("12","SDIO","bidirectional"),("11","SCLK","input"),
 ("10","SENB","input"),("9","RST","input"),
]
LIB_DEFS.append(("MBDSDR:SI4732","U","SI4732-A10-GSR","Package_SO:SOIC-16_3.9x9.9mm_P1.27mm",
                 u2_left,u2_right,None))

# U3 TMAG5273 SOT23-6  (左1,2,3  右6,5,4)
u3_left=[("1","SCL","input"),("2","GND","power_in"),("3","GND/TEST","power_in")]
u3_right=[("6","SDA","bidirectional"),("5","INT","output"),("4","VCC","power_in")]
LIB_DEFS.append(("MBDSDR:TMAG5273","U","TMAG5273A1QDBVT","Package_TO_SOT_SMD:SOT-23-6",
                 u3_left,u3_right,None))

# U4 ESP32-S3-WROOM-1 (引脚号用GPIO/信号名, 投产时在立创替换为真实模组符号按GPIO网络连接)
u4_left=[
 ("3V3","3V3","power_in"),("EN","EN","input"),("IO0","GPIO0","input"),("GND","GND","power_in"),
 ("IO1","GPIO1","bidirectional"),("IO2","GPIO2","bidirectional"),("IO3","GPIO3","bidirectional"),
 ("IO46","GPIO46","bidirectional"),("IO21","GPIO21","bidirectional"),("IO11","GPIO11","bidirectional"),
 ("IO10","GPIO10","bidirectional"),("IO16","GPIO16","bidirectional"),
]
u4_right=[
 ("IO19","GPIO19","bidirectional"),("IO20","GPIO20","bidirectional"),("IO8","GPIO8","bidirectional"),
 ("IO9","GPIO9","bidirectional"),("IO4","GPIO4","bidirectional"),("IO5","GPIO5","bidirectional"),
 ("IO7","GPIO7","bidirectional"),("IO17","GPIO17","bidirectional"),("IO18","GPIO18","bidirectional"),
 ("IO12","GPIO12","bidirectional"),("IO13","GPIO13","bidirectional"),("IO14","GPIO14","bidirectional"),
 ("IO15","GPIO15","bidirectional"),
]
LIB_DEFS.append(("MBDSDR:ESP32S3","U","ESP32-S3-WROOM-1-N8R8","RF_Module:ESP32-S3-WROOM-1",
                 u4_left,u4_right,None))

# U5 BMI260 LGA14 (引脚号用信号名, 投产以Bosch手册/立创库pad为准)
u5_left=[("VDD","VDD","power_in"),("VDDIO","VDDIO","power_in"),("GND","GND","power_in"),("CSB","CSB","input")]
u5_right=[("SCL","SCL","input"),("SDA","SDA","bidirectional"),("SDO","SDO","input"),
          ("INT1","INT1","output"),("INT2","INT2","output")]
LIB_DEFS.append(("MBDSDR:BMI260","U","BMI260","Package_LGA:Bosch_LGA-14_3x2.5mm_P0.7mm",
                 u5_left,u5_right,None))

# U6 ATGM336H
u6_left=[("VCC","VCC","power_in"),("GND","GND","power_in"),("ONOFF","ON/OFF","input"),("VBAT","VBAT","power_in")]
u6_right=[("TXD","TXD","output"),("RXD","RXD","input"),("PPS","PPS","output"),("ANT","ANT","passive")]
LIB_DEFS.append(("MBDSDR:ATGM336H","U","ATGM336H-5NR32-G","RF_Module:ATGM336H-5NR32",
                 u6_left,u6_right,None))

# U7 AMS1117
u7_left=[("3","VIN","power_in")]
u7_right=[("2","VOUT","power_out")]
u7_bottom=[("1","GND","power_in")]
LIB_DEFS.append(("MBDSDR:AMS1117","U","AMS1117-3.3","Package_TO_SOT_SMD:SOT-223-3_TabPin2",
                 u7_left,u7_right,u7_bottom))

# J1 Type-C 6P
j_left=[("VBUS","VBUS","power_in"),("GND","GND","power_in"),("CC1","CC1","passive")]
j_right=[("DM","D-","passive"),("DP","D+","passive"),("CC2","CC2","passive")]
LIB_DEFS.append(("MBDSDR:TYPEC6","J","KH-TYPE-C-6P","Connector_USB:USB_C_Receptacle_6P",
                 j_left,j_right,None))

# USB1 USB-A 4P
ua_left=[("1","VBUS","power_in"),("4","GND","power_in")]
ua_right=[("2","D-","passive"),("3","D+","passive")]
LIB_DEFS.append(("MBDSDR:USBA","USB","USB-234-BCW","Connector_USB:USB_A_Receptacle_HRO_TYPE-A-STA-2F",
                 ua_left,ua_right,None))

# RF1 SMA, RF2 u.FL (中心+外壳)
LIB_DEFS.append(("MBDSDR:SMA","RF","BWSMA-KE-Z001","Connector_Coaxial:SMA_BWSMA-KE-Z011_Vertical",
                 [("1","SIG","passive")],[("2","SHIELD","power_in")],None))
LIB_DEFS.append(("MBDSDR:UFL","RF","HMT-U.FL-R-SMT","Connector_Coaxial:U.FL_HRS_U.FL-R-SMT-1_Vertical",
                 [("1","SIG","passive")],[("2","SHIELD","power_in")],None))

# ----------------------------------------------------------------------------
# 构建 lib_symbols 文本
# ----------------------------------------------------------------------------
def build_libsymbols():
    out=["  (lib_symbols\n"]
    # 两端元件
    out.append(twopin_lib("Device:R","R","R","Resistor_SMD:R_0402_1005Metric","R"))
    out.append(twopin_lib("Device:C","C","C","Capacitor_SMD:C_0402_1005Metric","C"))
    out.append(twopin_lib("Device:C8","C","C_0805","Capacitor_SMD:C_0805_2012Metric","C"))
    out.append(twopin_lib("Device:L","L","L","Inductor_SMD:L_0402_1005Metric","rect"))
    out.append(twopin_lib("Device:Fuse","F","Fuse","Fuse:Fuse_0805_2012Metric","fuse"))
    out.append(led_lib())
    out.append(sw_lib())
    out.append(xtal4_lib())
    # IC / 连接器
    for (libid,ref,val,fp,lp,rp,bp) in LIB_DEFS:
        out.append(ic_lib(libid,ref,val,fp,lp,rp,bp))
    out.append("  )\n")
    return "".join(out)

# 记录每个 libid 的引脚坐标(符号局部坐标), 供实例标签定位
def lib_pin_coords(libid):
    """返回 {num:(px,py,ang_out)} ang_out=label/短线朝外方向(度)"""
    # 找到定义
    rec=None
    if libid in ("Device:R","Device:C","Device:C8","Device:L","Device:Fuse"):
        return {"1":(-3.81,0,180),"2":(3.81,0,0)}
    if libid=="Device:LED":
        return {"1":(-3.81,0,180),"2":(3.81,0,0)}
    if libid=="Switch:SW_Push":
        return {"1":(-5.08,1.27,180),"2":(5.08,1.27,0)}
    if libid=="Device:Crystal4":
        return {"1":(-6.35,0,180),"3":(6.35,0,0),"2":(0,-5.08,270),"4":(0,5.08,90)}
    for (lid,ref,val,fp,lp,rp,bp) in LIB_DEFS:
        if lid==libid: rec=(lp,rp,bp)
    if rec is None: raise KeyError(libid)
    lp,rp,bp=rec
    n=max(len(lp),len(rp)); halfh=(n-1)*GRID/2+GRID*0.8
    maxlen=max([len(str(p[1])) for p in lp+rp] or [4])
    halfw=max(10.16, min(20.32, maxlen*1.6))
    coords={}
    for i,(num,name,pt) in enumerate(lp):
        y=-(len(lp)-1)*GRID/2+i*GRID
        coords[num]=(-halfw-PLEN,y,180)
    for i,(num,name,pt) in enumerate(rp):
        y=-(len(rp)-1)*GRID/2+i*GRID
        coords[num]=(halfw+PLEN,y,0)
    if bp:
        for j,(num,name,pt) in enumerate(bp):
            x=(j-(len(bp)-1)/2)*2*GRID
            coords[num]=(x,halfh+PLEN,90)
    return coords

# ----------------------------------------------------------------------------
# 实例数据: (ref, libid, value, x, y, footprint_override 或 None)
# ----------------------------------------------------------------------------
INST={}
def add(ref,libid,value,x,y,fp=None):
    INST[ref]=(libid,value,x,y,fp)

# --- 主芯片/连接器 (手工布局, A2 横向 594x420) ---
add("J1","MBDSDR:TYPEC6","KH-TYPE-C-6P",48,95)
add("F1","Device:Fuse","500mA",108,95)
add("U7","MBDSDR:AMS1117","AMS1117-3.3",168,95)
add("U1","MBDSDR:USB2514B","USB2514B",300,128)
add("USB1","MBDSDR:USBA","USB-A",452,92)
add("X2","Device:Crystal4","24MHz",212,58)
add("U4","MBDSDR:ESP32S3","ESP32-S3-N8R8",300,262)
add("SW1","Switch:SW_Push","RESET",200,205)
add("U2","MBDSDR:SI4732","SI4732",96,252)
add("RF1","MBDSDR:SMA","SMA",30,212)
add("X1","Device:Crystal4","32.768kHz",36,312)
add("U6","MBDSDR:ATGM336H","ATGM336H",486,250)
add("RF2","MBDSDR:UFL","u.FL",552,212)
add("U5","MBDSDR:BMI260","BMI260",196,368)
add("U3","MBDSDR:TMAG5273","TMAG5273",268,372)
# LED + 限流电阻 一排 (右上角空白区, 避开HUB元件组)
for i in range(4):
    y=44+i*16
    add(f"LED{i+1}","Device:LED","Yellow",548,y)
    add(f"R{i+1}","Device:R","1k",502,y)

# --- 两端无源元件自动布局: 按 group 锚点网格排列 ---
# (ref, libid, value, net1(pin1), net2(pin2), group)
PASSIVE=[
 # SI4732 周边
 ("C1","Device:C","12pF","RCLK","GND","U2"),
 ("C3","Device:C","100nF","+3V3","GND","U2"),
 ("C4","Device:C","100nF","ANT_SMA","FM_IN","U2"),
 ("R19","Device:R","4.7k","+3V3","SI4732_RST","U2"),
 # AMS1117 电源
 ("C7","Device:C8","10uF","+5V","GND","PWR"),
 ("C8","Device:C","100nF","+5V","GND","PWR"),
 ("C9","Device:C8","10uF","+3V3","GND","PWR"),
 ("C10","Device:C","100nF","+3V3","GND","PWR"),
 # Hub 去耦 (6)
 ("C5","Device:C","100nF","+3V3","GND","HUB"),
 ("C6","Device:C","100nF","+3V3","GND","HUB"),
 ("C19","Device:C","100nF","+3V3","GND","HUB"),
 ("C20","Device:C","100nF","+3V3","GND","HUB"),
 ("C21","Device:C","100nF","+3V3","GND","HUB"),
 ("C22","Device:C","100nF","+3V3","GND","HUB"),
 # Hub 晶振/滤波/配置/偏置/检测
 ("C17","Device:C","18pF","XTALIN","GND","HUB"),
 ("C18","Device:C","18pF","XTALOUT","GND","HUB"),
 ("C23","Device:C","100nF","CRFILT","GND","HUB"),
 ("C24","Device:C","100nF","PLLFILT","GND","HUB"),
 ("R12","Device:R","12k 1%","RBIAS","GND","HUB"),
 ("R13","Device:R","10k","CFG22","GND","HUB"),
 ("R14","Device:R","10k","CFG24","GND","HUB"),
 ("R15","Device:R","10k","CFG25","GND","HUB"),
 ("R16","Device:R","10k","CFG28","GND","HUB"),
 ("R17","Device:R","100k","+5V","VBUS_DET","HUB"),
 ("R18","Device:R","100k","VBUS_DET","GND","HUB"),
 ("R7","Device:R","10k","+3V3","HUB_RESET","HUB"),
 # ESP32 周边
 ("C11","Device:C8","10uF","+3V3","GND","ESP"),
 ("C12","Device:C","100nF","+3V3","GND","ESP"),
 ("R8","Device:R","10k","+3V3","EN","ESP"),
 ("R11","Device:R","10k","+3V3","BOOT","ESP"),
 ("R5","Device:R","4.7k","+3V3","I2C_SCL","ESP"),
 ("R6","Device:R","4.7k","+3V3","I2C_SDA","ESP"),
 # 传感器
 ("C13","Device:C","100nF","+3V3","GND","IMU"),
 ("C14","Device:C","100nF","+3V3","GND","MAG"),
 # GPS
 ("C15","Device:C8","10uF","+3V3","GND","GPS"),
 ("C16","Device:C","100nF","GPS_ANT","GND","GPS"),
 ("L2","Device:L","100nH","+3V3","GPS_ANT","GPS"),
 # Type-C CC 下拉
 ("R9","Device:R","5.1k","CC1","GND","PWR"),
 ("R10","Device:R","5.1k","CC2","GND","PWR"),
]

# group 锚点 (起始 x,y), 元件水平排列, 每行 PER 个后换行
GROUP_ANCHOR={
 "PWR":(150,150),
 "HUB":(360,150),
 "ESP":(150,220),
 "U2" :(30,345),
 "IMU":(150,400),
 "MAG":(245,400),
 "GPS":(420,330),
}
PER=5; DX=30; DY=16
group_cursor={}
for (ref,libid,value,n1,n2,grp) in PASSIVE:
    bx,by=GROUP_ANCHOR[grp]
    k=group_cursor.get(grp,0); group_cursor[grp]=k+1
    col=k%PER; row=k//PER
    x=bx+col*DX; y=by+row*DY
    add(ref,libid,value,x,y)

# ----------------------------------------------------------------------------
# 引脚 -> 网络 映射  (针对多引脚 IC/连接器)
# ----------------------------------------------------------------------------
NET={}
def nets(ref,m):
    for pin,net in m.items(): NET[(ref,pin)]=net

# U1 USB2514B
nets("U1",{
 "1":"NC","2":"NC","3":"USB_DM","4":"USB_DP","5":"+3V3","6":"USBA_DM","7":"USBA_DP",
 "8":"NC","9":"NC","10":"+3V3","11":"GND","12":"NC","13":"+3V3","14":"CRFILT","15":"+3V3",
 "16":"NC","17":"+3V3","18":"NC",
 "36":"+3V3","35":"RBIAS","34":"PLLFILT","33":"XTALIN","32":"XTALOUT","31":"TYPEC_DP",
 "30":"TYPEC_DM","29":"+3V3","28":"CFG28","27":"VBUS_DET","26":"HUB_RESET","25":"CFG25",
 "24":"CFG24","23":"+3V3","22":"CFG22","21":"NC","20":"NC","19":"+3V3","37":"GND"})
# U2 SI4732
nets("U2",{
 "1":"I2S_LRCLK","2":"I2S_BCLK","3":"SI4732_INT","4":"NC","5":"NC","6":"FM_IN",
 "7":"GND","8":"ANT_SMA",
 "16":"I2S_DIN","15":"GND","14":"+3V3","13":"RCLK","12":"I2C_SDA","11":"I2C_SCL",
 "10":"GND","9":"SI4732_RST"})
# U3 TMAG
nets("U3",{"1":"I2C_SCL","2":"GND","3":"GND","6":"I2C_SDA","5":"MAG_INT","4":"+3V3"})
# U4 ESP32
nets("U4",{
 "3V3":"+3V3","EN":"EN","IO0":"BOOT","GND":"GND",
 "IO1":"LED1_K","IO2":"LED2_K","IO3":"LED3_K","IO46":"LED4_K",
 "IO21":"HUB_RESET","IO11":"SI4732_RST","IO10":"SI4732_INT","IO16":"GPS_PPS",
 "IO19":"USB_DM","IO20":"USB_DP","IO8":"I2C_SCL","IO9":"I2C_SDA",
 "IO4":"I2S_BCLK","IO5":"I2S_LRCLK","IO7":"I2S_DIN",
 "IO17":"UART_RX","IO18":"UART_TX","IO12":"IMU_INT","IO13":"MAG_INT","IO14":"NC","IO15":"NC"})
# U5 BMI260 (INT1 接 ESP IO12, INT2 预留NC)
nets("U5",{"VDD":"+3V3","VDDIO":"+3V3","GND":"GND","CSB":"+3V3",
           "SCL":"I2C_SCL","SDA":"I2C_SDA","SDO":"GND","INT1":"IMU_INT","INT2":"NC"})
# U6 GPS
nets("U6",{"VCC":"+3V3","GND":"GND","ONOFF":"+3V3","VBAT":"+3V3",
           "TXD":"UART_RX","RXD":"UART_TX","PPS":"GPS_PPS","ANT":"GPS_ANT"})
# U7 AMS1117
nets("U7",{"3":"+5V","2":"+3V3","1":"GND"})
# J1 Type-C
nets("J1",{"VBUS":"VBUS","GND":"GND","CC1":"CC1","DM":"TYPEC_DM","DP":"TYPEC_DP","CC2":"CC2"})
# USB1 USB-A
nets("USB1",{"1":"+5V","4":"GND","2":"USBA_DM","3":"USBA_DP"})
# RF
nets("RF1",{"1":"ANT_SMA","2":"GND"})
nets("RF2",{"1":"GPS_ANT","2":"GND"})
# X2 24MHz (1 XTALIN,3 XTALOUT,2/4 GND)
nets("X2",{"1":"XTALIN","3":"XTALOUT","2":"GND","4":"GND"})
# X1 32.768k (4脚3215符号: 1 RCLK, 3 晶体另一端=GND, 2/4 壳地=GND; SI4732-A10 单脚RCLK晶振)
nets("X1",{"1":"RCLK","2":"GND","3":"GND","4":"GND"})
# SW1
nets("SW1",{"1":"EN","2":"GND"})
# LED  K=pin1, A=pin2
for i in range(4):
    nets(f"LED{i+1}",{"1":f"LED{i+1}_K","2":f"LED{i+1}_A"})
    nets(f"R{i+1}",{"1":"+3V3","2":f"LED{i+1}_A"})   # LED 限流电阻
# 自恢复保险丝: Type-C VBUS -> +5V
nets("F1",{"1":"VBUS","2":"+5V"})
# 两端无源
for (ref,libid,value,n1,n2,grp) in PASSIVE:
    NET[(ref,"1")]=n1; NET[(ref,"2")]=n2

# ----------------------------------------------------------------------------
# 生成实例 + 引脚短线 + global_label
# ----------------------------------------------------------------------------
DX_LABEL=2.54
wires=[]; labels=[]; instances=[]; ncs=[]
net_count={}

def rotvec(px,py,rot):
    import math
    a=math.radians(rot)
    return (px*math.cos(a)-py*math.sin(a), px*math.sin(a)+py*math.cos(a))

for ref in INST:
    libid,value,x,y,fpov=INST[ref]
    coords=lib_pin_coords(libid)
    fp = fpov if fpov else ""
    # footprint 从库定义取
    if not fp:
        for (lid,r,v,f,lp,rp,bp) in LIB_DEFS:
            if lid==libid: fp=f
    instances.append(
f'''  (symbol (lib_id "{libid}") (at {x} {y} 0) (unit 1)
    (in_bom yes) (on_board yes) (dnp no) (uuid "{u()}")
    (property "Reference" "{ref}" (at {x} {y-18 if libid.startswith("MBDSDR") else y-6} 0) (effects (font (size {FZ} {FZ}) bold)))
    (property "Value" "{value}" (at {x} {y+18 if libid.startswith("MBDSDR") else y+6} 0) (effects (font (size {FZ} {FZ}))))
    (property "Footprint" "{fp}" (at 0 0 0) (effects hide))
    (property "Datasheet" "~" (at 0 0 0) (effects hide))
  )
''')
    for pin,(px,py,ang_out) in coords.items():
        net=NET.get((ref,pin))
        ax=x+px; ay=y+py
        # 短线朝外
        import math
        a=math.radians(ang_out)
        ex=ax+DX_LABEL*math.cos(a); ey=ay+DX_LABEL*math.sin(a)
        if net is None or net=="NC":
            ncs.append(f'  (no_connect (at {ax:.2f} {ay:.2f}) (uuid "{u()}"))\n')
            continue
        wires.append(f'  (wire (pts (xy {ax:.2f} {ay:.2f}) (xy {ex:.2f} {ey:.2f})) '
                     f'(stroke (width 0) (type default)) (uuid "{u()}"))\n')
        # global_label 的 shape 枚举不含 power(power 是电源符号专用); 电源网络用 passive
        shape = "passive" if net in ("+3V3","+5V","VBUS","GND") else BIDIR
        # justify
        if ang_out==0: just="left"
        elif ang_out==180: just="right"
        else: just="left"
        labels.append(f'  (global_label "{net}" (shape {shape}) (at {ex:.2f} {ey:.2f} {ang_out}) '
                      f'(effects (font (size {FZ} {FZ})) (justify {just})) (uuid "{u()}"))\n')
        net_count[net]=net_count.get(net,0)+1

# ----------------------------------------------------------------------------
# 分区标题文字
# ----------------------------------------------------------------------------
titles=[
 (20,20,"MBDSDR  ai-sdr Mini  原理图  v0.7.1",7,True),
 (20,30,"AI-Defined Radio | 80x60mm 2-layer | 62 components / global-label nets | GPL-3.0 | BI4MIB",3.5,False),
 (30,55,"[1] 电源输入与稳压 (Type-C / Fuse / AMS1117)",4,True),
 (250,40,"[2] USB2514B Hub + 24MHz (上行Type-C / 下行ESP32 & USB-A)",4,True),
 (468,30,"[3] USB-A扩展口 / 状态LED",4,True),
 (30,180,"[4] SI4732 广播接收前端 (AM/FM/SW + I2S) + SMA",4,True),
 (200,200,"[5] ESP32-S3 主控 (WiFi/BLE/USB/I2C/I2S/UART)",4,True),
 (420,180,"[6] ATGM336H GNSS (GPS/BDS) + u.FL有源天线",4,True),
 (150,330,"[7] 传感器 BMI260 IMU + TMAG5273 磁力计 (I2C)",4,True),
 (20,395,"注: 去耦/上拉/偏置等两端元件按功能分组排列, 经同名全局标签连网; 投产时模组符号(ESP32/ATGM336H/BMI260)在立创/Altium替换为官方库封装, 按GPIO/信号名网络连接。",3,False),
]
texts=[]
for (x,y,t,sz,bold) in titles:
    b=" bold" if bold else ""
    texts.append(f'  (text "{t}" (at {x} {y} 0)\n'
                 f'    (effects (font (size {sz} {sz}){b}) (justify left bottom))\n'
                 f'    (uuid "{u()}"))\n')

# ----------------------------------------------------------------------------
# 组装文件
# ----------------------------------------------------------------------------
header=f'''(kicad_sch (version 20221115) (generator eeschema)

  (uuid 00000000-0000-0000-0000-00000000a101)

  (paper "A2")

  (title_block
    (title "MBDSDR ai-sdr Mini - AI Defined Radio Hardware")
    (date "2026-09-16")
    (rev "v0.7.1")
    (company "MBDSDR Open Source Project (BI4MIB)")
    (comment 1 "Complete v0.7.1 schematic, global-label nets, auto-routable")
    (comment 2 "GPL-3.0 License")
    (comment 3 "80x60mm 2-layer, JLCPCB SMT")
    (comment 4 "Si4732 + ESP32-S3 + USB2514B + GNSS + IMU + Mag")
  )

'''
footer='''
  (sheet_instances
    (path "/" (page "1"))
  )

)
'''
content = header + build_libsymbols() + "\n" + "".join(texts) + "\n" + "".join(instances) \
          + "\n  ;; ===== 引脚短线与全局网络标签 =====\n" + "".join(wires) + "".join(labels) \
          + "".join(ncs) + footer

# 先写紧凑原始版(便于 diff/调试)
with open("ai-sdr-mini.kicad_sch.src","w",encoding="utf-8") as f:
    f.write(content)
# 规范化: 剥离 KiCad sexpr 不支持的 ';' 注释 + KiCad 官方缩进风格。
# 这是立创EDA "转换异常 decoding-error" 的根因(注释被解析为非法节点)。
import normalize_sch
with open("ai-sdr-mini.kicad_sch","w",encoding="utf-8") as f:
    f.write(normalize_sch.normalize(content))

# ----------------------------------------------------------------------------
# 校验
# ----------------------------------------------------------------------------
op=content.count("("); cp=content.count(")")
print(f"括号: ( {op}  ) {cp}  匹配={op==cp}")
print(f"元件实例数: {len(INST)}  (期望62)")
refs=list(INST.keys())
print(f"位号唯一: {len(refs)==len(set(refs))}")
# 网络连接点统计
print(f"网络数: {len(net_count)}")
single=[n for n,c in net_count.items() if c<2]
print(f"仅1个连接点的网络(需检查): {single}")
# 每网络明细
for n in sorted(net_count):
    print(f"  {n:14s} {net_count[n]}")
