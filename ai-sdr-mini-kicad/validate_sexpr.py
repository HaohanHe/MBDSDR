# -*- coding: utf-8 -*-
"""完整递归下降解析 .kicad_sch, 校验 KiCad schema 结构(不止括号配对)。"""
import sys
src=open("ai-sdr-mini.kicad_sch",encoding="utf-8").read()
src="\n".join(("" if l.strip().startswith(";;") else l) for l in src.split("\n"))
pos=0; N=len(src)

def skip_ws():
    global pos
    while pos<N and src[pos] in ' \t\r\n':
        pos+=1

def parse():
    global pos
    skip_ws()
    if pos<N and src[pos]==';':
        while pos<N and src[pos]!='\n':
            pos+=1
        return parse()
    if src[pos]=='(':
        pos+=1
        lst=[]
        while True:
            skip_ws()
            if pos>=N:
                raise SyntaxError("未闭合括号")
            if src[pos]==')':
                pos+=1
                return lst
            lst.append(parse())
    if src[pos]=='"':
        pos+=1; buf=''
        while True:
            c=src[pos]
            if c=='\\':
                buf+=src[pos:pos+2]; pos+=2; continue
            if c=='"':
                pos+=1; return ('STR',buf)
            buf+=c; pos+=1
    start=pos
    while pos<N and src[pos] not in ' \t\r\n()':
        pos+=1
    return src[start:pos]

tree=parse()
skip_ws()
print("完整解析到文件末尾:", pos==N, " 顶层:", tree[0])
kids=tree[1:]
from collections import Counter
types=Counter(k[0] if isinstance(k,list) else k for k in kids)
print("顶层节点计数:", dict(types))

def get(node,key):
    for x in node:
        if isinstance(x,list) and x and x[0]==key: return x
    return None

lib=[k for k in kids if k[0]=='lib_symbols'][0]
symdefs=[k for k in lib[1:] if isinstance(k,list) and k[0]=='symbol']
print("内嵌库符号定义:", len(symdefs))

insts=[k for k in kids if isinstance(k,list) and k[0]=='symbol']
bad=[]
for s in insts:
    libid=get(s,'lib_id'); uu=get(s,'uuid'); at=get(s,'at')
    if not libid or not uu or not at: bad.append(str(libid))
    if uu and not (isinstance(uu[1],tuple) and uu[1][0]=='STR'): bad.append("uuid非串:"+str(libid))
print("symbol 实例:", len(insts), " 字段异常:", bad[:5] if bad else "无")

for t in ('global_label','wire','no_connect'):
    nodes=[k for k in kids if isinstance(k,list) and k[0]==t]
    nouid=sum(1 for n in nodes if get(n,'uuid') is None)
    noat=sum(1 for n in nodes if get(n,'at') is None)
    print(f"{t}: {len(nodes)} 个, 缺uuid={nouid}, 缺at={noat}")

si=[k for k in kids if k[0]=='sheet_instances']
print("sheet_instances:", "有" if si else "缺失", si[0][1:] if si else "")
# lib_symbols 中每个符号至少有 pin 或图形
for d in symdefs:
    name=d[1]
    npin=sum(1 for x in d[1:] if isinstance(x,list) and x and x[0]=='pin')
    if npin==0: print("  警告:库符号无pin:",name)
print("OK schema 结构校验完成")
