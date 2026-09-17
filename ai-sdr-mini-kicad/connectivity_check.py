# -*- coding: utf-8 -*-
import math
F="ai-sdr-mini.kicad_sch"
src=open(F).read()
src="\n".join(("" if l.strip().startswith(";;") else l) for l in src.split("\n"))
pos=0;N=len(src)
def skip():
    global pos
    while pos<N and src[pos] in ' \t\r\n':pos+=1
def parse():
    global pos
    skip()
    if src[pos]=='(':
        pos+=1;L=[]
        while True:
            skip()
            if src[pos]==')':pos+=1;return L
            L.append(parse())
    if src[pos]=='"':
        pos+=1;b=''
        while True:
            c=src[pos]
            if c=='\\':b+=src[pos:pos+2];pos+=2;continue
            if c=='"':pos+=1;return ('S',b)
            b+=c;pos+=1
    st=pos
    while pos<N and src[pos] not in ' \t\r\n()':pos+=1
    return src[st:pos]
t=parse()
def S(x):return x[1] if isinstance(x,tuple) else x
kids=t[1:]
def find_all(n,tag,acc):
    if isinstance(n,list):
        if n and n[0]==tag:acc.append(n)
        for c in n:
            if isinstance(c,list):find_all(c,tag,acc)
# 库引脚
libpins={}
for k in kids:
    if isinstance(k,list) and k[0]=='lib_symbols':
        for sym in k[1:]:
            if not(isinstance(sym,list) and sym[0]=='symbol'):continue
            name=S(sym[1]);pins={}
            pn=[];find_all(sym,'pin',pn)
            for p in pn:
                at=[c for c in p if isinstance(c,list) and c[0]=='at'][0]
                num=[c for c in p if isinstance(c,list) and c[0]=='number'][0]
                pins[S(num[1])]=(float(at[1]),float(at[2]))
            libpins[name]=pins
# 实例
insts=[]
for k in kids:
    if isinstance(k,list) and k[0]=='symbol':
        d={'pins':[]}
        for c in k[1:]:
            if not isinstance(c,list):continue
            if c[0]=='lib_id':d['lib']=S(c[1])
            elif c[0]=='at':d['x']=float(c[1]);d['y']=float(c[2]);d['rot']=float(c[3]) if len(c)>3 else 0
            elif c[0]=='property' and S(c[1])=='Reference':d['ref']=S(c[2])
            elif c[0]=='mirror':d['mirror']=S(c[1])
        insts.append(d)
# wire 端点
wpts=set()
for k in kids:
    if isinstance(k,list) and k[0]=='wire':
        xys=[];find_all(k,'xy',xys)
        for xy in xys:wpts.add((round(float(xy[1]),2),round(float(xy[2]),2)))
# label 点
lpts={}
for k in kids:
    if isinstance(k,list) and k[0] in ('global_label','label'):
        at=[c for c in k if isinstance(c,list) and c[0]=='at'][0]
        lpts[(round(float(at[1]),2),round(float(at[2]),2))]=S(k[1])
def tr(lx,ly,rot,mir):
    a=math.radians(rot);x,y=lx,ly
    if mir=='x':y=-y
    if mir=='y':x=-x
    return x*math.cos(a)-y*math.sin(a), x*math.sin(a)+y*math.cos(a)
bad=[]
for d in insts:
    for num,(lx,ly) in libpins.get(d['lib'],{}).items():
        dx,dy=tr(lx,ly,d['rot'],d.get('mirror'))
        g=(round(d['x']+dx,2),round(d['y']+dy,2))
        onwire=g in wpts
        # 引脚热点也可能直接压 label(无短线)
        onlabel=g in lpts
        if not(onwire or onlabel):
            bad.append((d['ref'],num,g))
print(f"元件数={len(insts)} 导线端点数={len(wpts)}")
print(f"热点未接触任何导线/label 的引脚数={len(bad)}")
for ref,num,g in bad:print(f"  {ref:5s} pad{num:7s} @ {g}")
