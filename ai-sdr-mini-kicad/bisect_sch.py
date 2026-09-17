# -*- coding: utf-8 -*-
"""把 .kicad_sch 解析后按节点类别重组, 生成二分测试变体, 用 kicad-cli 定位非法节点类。"""
import subprocess, os, sys

SRC="ai-sdr-mini.kicad_sch"
src=open(SRC,encoding="utf-8").read()
src="\n".join(("" if l.strip().startswith(";;") else l) for l in src.split("\n"))
pos=0; N=len(src)
def skip():
    global pos
    while pos<N and src[pos] in ' \t\r\n': pos+=1
def parse():
    global pos
    skip()
    if src[pos]==';':
        while pos<N and src[pos]!='\n': pos+=1
        return parse()
    if src[pos]=='(':
        pos+=1; lst=[]
        while True:
            skip()
            if src[pos]==')': pos+=1; return lst
            lst.append(parse())
    if src[pos]=='"':
        pos+=1; b=''
        while True:
            c=src[pos]
            if c=='\\': b+=src[pos:pos+2]; pos+=2; continue
            if c=='"': pos+=1; return ('S',b)
            b+=c; pos+=1
    st=pos
    while pos<N and src[pos] not in ' \t\r\n()': pos+=1
    return src[st:pos]
tree=parse()

def ser(n,ind=0):
    if isinstance(n,tuple) and n[0]=='S':
        return '"'+n[1].replace('\\','\\\\').replace('"','\\"')+'"'
    if not isinstance(n,list): return str(n)
    if len(n)<=2 and all(not isinstance(x,list) for x in n[1:]):
        return "("+" ".join(ser(x) for x in n)+")"
    sp="\n"+"  "*(ind+1)
    return "("+n[0]+sp+sp.join(ser(x,ind+1) for x in n[1:])+"\n"+"  "*ind+")"

head=tree[0]  # 'kicad_sch'
kids=tree[1:]
# 分类
keep_meta=[]; libsym=None; figures=[]
for k in kids:
    if isinstance(k,list):
        t=k[0]
        if t=='lib_symbols': libsym=k
        elif t in ('symbol','wire','global_label','no_connect','text'): figures.append(k)
        else: keep_meta.append(k)

def build(include_lib=False, fig_types=None):
    out=[['kicad_sch']]
    for m in keep_meta: out[0].append(m)
    if include_lib: out[0].append(libsym)
    for f in figures:
        if fig_types is None or f[0] in fig_types: out[0].append(f)
    return ser(out[0])+"\n"

variants={
 "T0_meta_only": build(False,set()),
 "T1_lib": build(True,set()),
 "T2_lib_symbols": build(True,{'symbol'}),
 "T3_add_wire": build(True,{'symbol','wire'}),
 "T4_add_label": build(True,{'symbol','wire','global_label'}),
 "T5_full": build(True,{'symbol','wire','global_label','no_connect','text'}),
}
os.makedirs("/tmp/bisect",exist_ok=True)
for name,content in variants.items():
    p=f"/tmp/bisect/{name}.kicad_sch"
    open(p,"w").write(content)

for name in variants:
    p=f"/tmp/bisect/{name}.kicad_sch"
    r=subprocess.run(["kicad-cli","sch","export","netlist","-o",f"/tmp/bisect/{name}.net",p],
                     capture_output=True,text=True,timeout=120)
    ok=os.path.exists(f"/tmp/bisect/{name}.net")
    print(f"{name:16s} {'OK 通过' if ok else 'FAIL '+(r.stdout.strip() or r.stderr.strip())[:60]}")
