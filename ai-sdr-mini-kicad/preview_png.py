import importlib.util, math
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
spec=importlib.util.spec_from_file_location("gs","./generate_schematic.py")
gs=importlib.util.module_from_spec(spec); spec.loader.exec_module(gs)

def body_box(ref):
    libid,value,x,y,_=gs.INST[ref]
    if libid.startswith("MBDSDR"):
        for d in gs.LIB_DEFS:
            if d[0]==libid: rec=d
        lp,rp=rec[4],rec[5]
        n=max(len(lp),len(rp)); hh=(n-1)*gs.GRID/2+gs.GRID*0.8
        ml=max([len(str(p[1])) for p in lp+rp] or [4]); hw=max(10.16,min(20.32,ml*1.6))
        return (x-hw,y-hh,x+hw,y+hh)
    return (x-4,y-4,x+4,y+4)

fig,ax=plt.subplots(figsize=(20,14),dpi=110)
ax.set_xlim(0,594); ax.set_ylim(420,0)
ax.add_patch(Rectangle((10,10),574,400,fill=False,ec="#bbb",ls="--"))
for ref in gs.INST:
    libid,value,x,y,_=gs.INST[ref]
    for pin,(px,py,ao) in gs.lib_pin_coords(libid).items():
        net=gs.NET.get((ref,pin)); ax_,ay=x+px,y+py
        a=math.radians(ao); ex,ey=ax_+2.54*math.cos(a),ay+2.54*math.sin(a)
        ax.plot([ax_,ex],[ay,ey],color="#aaa",lw=.5,zorder=1)
        if net and net!="NC":
            c="#d22" if net in("+3V3","+5V","VBUS") else ("#0a0" if net=="GND" else "#36c")
            ax.plot(ex,ey,"o",ms=2.2,color=c,zorder=2)
for ref in gs.INST:
    libid,value,x,y,_=gs.INST[ref]; x0,y0,x1,y1=body_box(ref)
    if libid.startswith("MBDSDR"): fc="#e8f0fe"
    elif libid=="Device:LED": fc="#fff3d6"
    elif "Crystal" in libid: fc="#eaf7ea"
    elif "Switch" in libid: fc="#f1e8fb"
    else: fc="#f5f5f5"
    ax.add_patch(Rectangle((x0,y0),x1-x0,y1-y0,fc=fc,ec="#333",lw=.8,zorder=3))
    ax.text(x,y0-1.3,ref,ha="center",va="bottom",fontsize=6,color="#b00",weight="bold",zorder=4)
    ax.text(x,y1+1.3,value,ha="center",va="top",fontsize=4.6,color="#222",zorder=4)
ax.set_xticks([]); ax.set_yticks([]); ax.set_title("MBDSDR ai-sdr Mini schematic layout preview v0.7.1 (62 comps)",fontsize=12)
plt.tight_layout(); plt.savefig("schematic_layout_preview.png",bbox_inches="tight"); print("png ok")
