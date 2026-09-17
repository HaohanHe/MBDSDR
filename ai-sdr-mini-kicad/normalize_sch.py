#!/usr/bin/env python3
"""KiCad S-表达式规范化器: tokenize -> parse -> pretty print。
生成器输出的紧凑 S 表达式经此重排为 KiCad 官方风格的缩进格式,
确保 kicad-cli / 立创EDA 能稳定解析(规避紧凑格式 decoding-error)。
用法: python3 normalize_sch.py in.kicad_sch [out.kicad_sch]
"""
import sys


def tokenize(text):
    toks = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
            continue
        if c == '(':
            toks.append('('); i += 1; continue
        if c == ')':
            toks.append(')'); i += 1; continue
        if c == ';':  # KiCad sexpr 不支持注释, 剥离到行尾
            while i < n and text[i] != '\n':
                i += 1
            continue
        if c == '"':
            # 字符串字面量, 整体保留(含引号), 处理转义
            j = i + 1
            buf = ['"']
            while j < n:
                if text[j] == '\\' and j + 1 < n:
                    buf.append(text[j]); buf.append(text[j+1]); j += 2; continue
                buf.append(text[j])
                if text[j] == '"':
                    j += 1; break
                j += 1
            toks.append(''.join(buf)); i = j; continue
        # 普通原子
        j = i
        while j < n and not text[j].isspace() and text[j] not in '()"':
            j += 1
        toks.append(text[i:j]); i = j
    return toks


def parse(toks):
    pos = 0

    def rd():
        nonlocal pos
        t = toks[pos]; pos += 1
        if t == '(':
            lst = []
            while toks[pos] != ')':
                lst.append(rd())
            pos += 1  # skip )
            return lst
        return t

    out = []
    while pos < len(toks):
        out.append(rd())
    return out


def is_atom(x):
    return isinstance(x, str)


def ser(node, ind=0):
    if is_atom(node):
        return node
    # 纯原子 list(无嵌套) -> 单行
    if all(is_atom(x) for x in node):
        return "(" + " ".join(node) + ")"
    pad = "  " * ind
    inner = "  " * (ind + 1)
    parts = []
    head = ser(node[0], ind + 1) if not is_atom(node[0]) else node[0]
    parts.append("(" + head)
    for child in node[1:]:
        if is_atom(child):
            # 头部紧跟的原子(如 version/generator)放首行; 其余缩进
            parts.append(inner + child)
        elif all(is_atom(x) for x in child):
            parts.append(inner + ser(child, ind + 1))
        else:
            parts.append(inner + ser(child, ind + 1))
    parts.append(pad + ")")
    return "\n".join(parts)


def normalize(text):
    tree = parse(tokenize(text))
    if len(tree) != 1:
        # 多顶层节点也接受, 逐个输出
        return "\n".join(ser(t, 0) for t in tree) + "\n"
    return ser(tree[0], 0) + "\n"


if __name__ == "__main__":
    src = sys.argv[1]
    dst = sys.argv[2] if len(sys.argv) > 2 else src
    raw = open(src, encoding="utf-8").read()
    norm = normalize(raw)
    open(dst, "w", encoding="utf-8").write(norm)
    print(f"规范化 {src} -> {dst}: {len(raw)}B -> {len(norm)}B")
