"""FST4 LDPC (240,101) 译码——与 FT8 同源的 Tanner 图 BP。

复用 wsjtx parity 文件的 Mn/Nm/nrw 结构（每 bit 连 3 个 check），
自动推断 N=len(Mn)/3、M=len(nrw)，做归一化 min-sum BP。
帧结构/波形/CRC24/解包后续再接；本模块先把 LDPC 物理层硬骨头打通。
"""
from __future__ import annotations

import os
import re
from functools import lru_cache

_N = 240  # 码字长 (240,101)


@lru_cache(maxsize=1)
def _graph():
    path = os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "repos",
        "wsjtx", "lib", "fst4", "ldpc_240_101_parity.f90"))
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    txt = open(path, encoding="utf-8").read()
    mn = [int(x) for x in re.findall(r"\d+", txt.split("data Mn/")[1].split("data Nm/")[0])]
    nm = [int(x) for x in re.findall(r"\d+", txt.split("data Nm/")[1].split("data nrw/")[0])]
    nrw = [int(x) for x in re.findall(r"\d+", txt.split("data nrw/")[1])]

    N = len(mn) // 3
    maxb = max(nrw)
    M = len(nm) // maxb  # 校验行数从 Nm 定（nrw 段可能多带一个数）
    var_checks = [[mn[3 * v + k] - 1 for k in range(3)] for v in range(N)]
    check_vars = []
    for c in range(M):
        n = nrw[c]
        check_vars.append([nm[maxb * c + i] - 1 for i in range(n)])
    return N, M, check_vars, var_checks


def ldpc_bp_decode(llr: list[float], max_iter: int = 25):
    """归一化 min-sum BP（dict 消息图，无下标错位）。输入 N 个 LLR（正=偏0）。"""
    N, M, check_vars, var_checks = _graph()
    if len(llr) != N:
        raise ValueError(f"需要 {N} 个 LLR，得到 {len(llr)}")
    # q[(c,v)]: variable v -> check c 的消息
    q = {(c, v): float(llr[v]) for c, vs in enumerate(check_vars) for v in vs}
    bits = [0] * N
    it = 0
    for it in range(1, max_iter + 1):
        # check -> variable: min-sum
        r = {}
        for c, vs in enumerate(check_vars):
            for v in vs:
                others = [q[(c, u)] for u in vs if u != v]
                prod = 1.0
                s = float("inf")
                for x in others:
                    prod *= 1 if x >= 0 else -1
                    s = min(s, abs(x))
                r[(c, v)] = prod * s * 0.75
        # variable -> check + 硬判决
        for v in range(N):
            total = float(llr[v]) + sum(r[(c, v)] for c in var_checks[v])
            bits[v] = 0 if total >= 0 else 1
            for c in var_checks[v]:
                q[(c, v)] = total - r[(c, v)]
        if all(sum(bits[u] for u in check_vars[c]) % 2 == 0 for c in range(M)):
            break
    return bits, it


if __name__ == "__main__":
    N, M, cv, vc = _graph()
    print(f"FST4 (240,101) 图: N={N} M={M}")
    b, it = ldpc_bp_decode([10.0] * N)
    print(f"全零码字: bits 全0={all(x == 0 for x in b)}, 迭代={it}")
    llr = [10.0] * N
    for v in (0, 5, 50, 100, 150, 200):
        llr[v] = -10.0
    b, it = ldpc_bp_decode(llr)
    print(f"翻 6 位后: 迭代={it}, 残留错={sum(1 for x in b if x != 0)}")
