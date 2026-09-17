# -*- coding: utf-8 -*-
exec(open("connectivity_check.py").read().split("bad=[]")[0])
pinhot={}
for d in insts:
    for num,(lx,ly) in libpins.get(d['lib'],{}).items():
        dx,dy=tr(lx,ly,d['rot'],d.get('mirror'))
        key=(round(d['x']+dx,2),round(d['y']+dy,2))
        pinhot[key]=(d['ref'],num)
nc=set()
for k in kids:
    if isinstance(k,list) and k[0]=='no_connect':
        at=[c for c in k if isinstance(c,list) and c[0]=='at'][0]
        nc.add((round(float(at[1]),2),round(float(at[2]),2)))
wires=[]
for k in kids:
    if isinstance(k,list) and k[0]=='wire':
        xys=[];find_all(k,'xy',xys)
        if len(xys)==2:
            a=(round(float(xys[0][1]),2),round(float(xys[0][2]),2))
            b=(round(float(xys[1][1]),2),round(float(xys[1][2]),2))
            wires.append((a,b))
def kind(p):
    if p in pinhot:return "PIN:%s-%s"%pinhot[p]
    if p in lpts:return "LABEL:%s"%lpts[p]
    if p in nc:return "NC"
    cnt=sum(1 for a,b in wires if a==p or b==p)
    if cnt>1:return "JUNC"
    return "FLOAT"
lab_float=[(p,n) for p,n in lpts.items() if p not in wpts and p not in pinhot]
print("=== 孤立 label 数:",len(lab_float))
for p,n in lab_float[:20]:print("  ",n,p)
fl=[(a,b,kind(a),kind(b)) for a,b in wires if "FLOAT" in kind(a) or "FLOAT" in kind(b)]
print("\n=== 端点 FLOAT 的导线数:",len(fl))
for a,b,ka,kb in fl[:20]:
    print(f"  {a} [{ka}]  --  {b} [{kb}]")
