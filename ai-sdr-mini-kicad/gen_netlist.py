# -*- coding: utf-8 -*-
import importlib.util, datetime
spec=importlib.util.spec_from_file_location("gs","./generate_schematic.py")
gs=importlib.util.module_from_spec(spec); spec.loader.exec_module(gs)

# libid -> footprint
fp_by_lib={d[0]:d[3] for d in gs.LIB_DEFS}
C805={"C7","C9","C11","C15"}
def footprint(ref,libid):
    if libid in fp_by_lib: return fp_by_lib[libid]
    if libid=="Device:R": return "Resistor_SMD:R_0402_1005Metric"
    if libid=="Device:C": return "Capacitor_SMD:C_0805_2012Metric" if ref in C805 else "Capacitor_SMD:C_0402_1005Metric"
    if libid=="Device:C8": return "Capacitor_SMD:C_0805_2012Metric"
    if libid=="Device:L": return "Inductor_SMD:L_0402_1005Metric"
    if libid=="Device:Fuse": return "Fuse:Fuse_0805_2012Metric"
    if libid=="Device:LED": return "LED_SMD:LED_0603_1608Metric"
    if libid=="Device:Crystal4": return "Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm"
    if libid=="Switch:SW_Push": return "Button_Switch_SMD:SW_SPST_TS1101"
    return ""

def esc(s): return str(s).replace('"','\\"')
out=[]
out.append('(export (version D)')
out.append('  (design')
out.append('    (source "ai-sdr-mini.kicad_sch")')
out.append(f'    (date "{datetime.date.today().isoformat()}")')
out.append('    (tool "MBDSDR data-driven generator")')
out.append('    (sheet (number "1") (name "ai-sdr Mini") (tstamps "/"))')
out.append('  )')
out.append('  (components')
for ref in gs.INST:
    libid,value,x,y,fpov=gs.INST[ref]
    fp=fpov or footprint(ref,libid)
    out.append(f'    (comp (ref {ref})')
    out.append(f'      (value "{esc(value)}")')
    out.append(f'      (footprint "{esc(fp)}")')
    out.append('    )')
out.append('  )')
# nets
from collections import defaultdict
netnodes=defaultdict(list)
for (ref,pin),net in gs.NET.items():
    if net=="NC": continue
    netnodes[net].append((ref,pin))
# 固定电源网络排序在前
order=["GND","+3V3","+5V","VBUS"]
names=sorted(netnodes.keys(), key=lambda n:(n not in order, order.index(n) if n in order else 0, n))
out.append('  (nets')
for code,name in enumerate(names,1):
    out.append(f'    (net (code {code}) (name "{esc(name)}")')
    for ref,pin in sorted(netnodes[name]):
        out.append(f'      (node (ref {ref}) (pin "{pin}"))')
    out.append('    )')
out.append('  )')
out.append(')')
open("ai-sdr-mini.net","w",encoding="utf-8").write("\n".join(out))
print(f"网表已写: {len(gs.INST)} 元件, {len(names)} 网络")
# 校验括号
s="\n".join(out); print("括号匹配:", s.count("(")==s.count(")"), s.count("("),s.count(")"))
# 每网络节点数
for n in names:
    print(f"  {n:12s} {len(netnodes[n])}")
