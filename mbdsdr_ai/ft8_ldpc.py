"""FT8 (174,91) LDPC 译码——从 wsjtx 权威源码提取 H 矩阵。

以前因"拿不到 LDPC H 矩阵"卡壳；现在直接解析
repos/wsjtx/lib/ft8/ldpc_174_91_c_parity.f90 的 Mn 数组，
建稀疏校验矩阵，做 min-sum BP 译码。

参数（ft8_params.f90）：KK=91 信息位（77+CRC14），NN=174 码字位，
83 个校验方程，每个校验 3 个变量节点。
"""
from __future__ import annotations

import math
import re
from functools import lru_cache

_N = 174  # 码字长
_K = 91   # 信息位
_M = 83   # 校验方程数


@lru_cache(maxsize=1)
def get_colorder() -> list[int]:
    """LDPC 码字顺序→传输顺序的置换（encode174.f90: codeword(colorder+1)=itmp）。

    解码反映射：itmp[j] = codeword[colorder[j]]。返回 174 个 0-based 索引。
    """
    import os
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "repos",
                        "wsjtx", "lib", "ft8", "ldpc_174_91_c_colorder.f90")
    path = os.path.normpath(path)
    txt = open(path, "r", encoding="utf-8").read()
    nums = [int(x) for x in re.findall(r"\d+", txt.split("colorder")[1])]
    return nums[:_N]


@lru_cache(maxsize=1)
def _parse_graph():
    """从 wsjtx parity.f90 解析 Tanner 图。

    wsjtx: Mn(3,N)=每 bit 连的 3 个 check；Nm(7,M)=每 check 连的 bit；
    nrw(M)=每 check 实际 bit 数（≤7）。全部 1-based。
    返回 (check_vars, var_checks)，0-based。
    """
    import os
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "repos",
                        "wsjtx", "lib", "ft8", "ldpc_174_91_c_parity.f90")
    path = os.path.normpath(path)
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    txt = open(path, "r", encoding="utf-8").read()
    mn_nums = [int(x) for x in re.findall(r"\d+", txt.split("data Mn/")[1].split("data Nm/")[0])]
    nm_nums = [int(x) for x in re.findall(r"\d+", txt.split("data Nm/")[1].split("data nrw/")[0])]
    nrw_nums = [int(x) for x in re.findall(r"\d+", txt.split("data nrw/")[1])]

    # Mn(3,N): 每列 3 个 check → var_checks
    var_checks: list[list[int]] = [[] for _ in range(_N)]
    for v in range(_N):
        var_checks[v] = [mn_nums[3 * v + k] - 1 for k in range(3)]
    # Nm(7,M): 每列最多 7 个 bit，nrw 给实际个数
    check_vars: list[list[int]] = []
    for c in range(_M):
        n = nrw_nums[c]
        check_vars.append([nm_nums[7 * c + i] - 1 for i in range(n)])
    return check_vars, var_checks


def ldpc_bp_decode(llr: list[float], max_iter: int = 25) -> tuple[list[int], int]:
    """min-sum BP 译码。

    输入：174 个信道 LLR（正=偏向 0，负=偏向 1；或反之，按约定）。
    输出：(硬判决码字 0/1, 实际迭代次数)。

    早停判据（移植自 wsjtx/lib/ft8/bpdecode174_91.f90:69-83）：
    每轮统计未满足校验数 ncheck；与上一轮 nclast 比较，若 ncheck 连续 5 轮
    不下降（nd=ncheck-nclast >= 0），且已迭代 >=10 轮、ncheck > 15，则提前
    退出——此时校验方程长期卡在高残差，继续迭代已无收益。该判据只省无效迭代，
    不改变"ncheck==0 即收敛返回"这一正确译码路径的结果。
    """
    check_vars, var_checks = _parse_graph()

    # 初始化：变量→校验消息 = 信道 LLR（bpdecode174_91.f90:29-33）
    q: dict[tuple[int, int], float] = {}  # (check, var) -> 信道到校验消息 toc
    for c, vs in enumerate(check_vars):
        for v in vs:
            q[(c, v)] = llr[v]

    decoded = [0] * _N
    nclast: int | None = None   # 上一轮未满足校验数（nclast）
    no_improve = 0              # ncheck 连续不下降计数（ncnt）
    for it in range(max_iter):
        # 校验→变量：归一化 min-sum BP（WSJT-X bpdecode174_91.f90:100-112 的
        # 工程近似；精确 tanh/atanh 在 Python 浮点 + 本模块小 LLR 尺度下不如
        # min-sum 鲁棒。0.75 为标准归一化因子）。
        r: dict[tuple[int, int], float] = {}
        for c, vs in enumerate(check_vars):
            for v in vs:
                prod_sign = 1.0
                min_abs = float("inf")
                for u in vs:
                    if u != v:
                        x = q[(c, u)]
                        prod_sign *= 1.0 if x >= 0 else -1.0
                        min_abs = min(min_abs, abs(x))
                r[(c, v)] = prod_sign * min_abs * 0.75
        # 变量→校验 + 硬判决（bpdecode174_91.f90:41-47, 87-95）
        for v in range(_N):
            total = llr[v] + sum(r[(c, v)] for c in var_checks[v])
            decoded[v] = 0 if total >= 0 else 1
            for c in var_checks[v]:
                q[(c, v)] = total - r[(c, v)]
        # 统计未满足校验数 ncheck（bpdecode174_91.f90:55-58）
        ncheck = sum(
            1 for c in range(_M)
            if sum(decoded[v] for v in check_vars[c]) % 2 != 0
        )
        # 收敛：所有校验满足（bpdecode174_91.f90:53-57）
        if ncheck == 0:
            return decoded, it + 1
        # 早停：ncheck 连续 5 轮不下降且残差长期高企（bpdecode174_91.f90:69-83）
        if nclast is not None:
            if ncheck - nclast < 0:
                no_improve = 0          # ncheck 下降，重置计数
            else:
                no_improve += 1
            if no_improve >= 5 and it >= 10 and ncheck > 15:
                break
        nclast = ncheck
    return decoded, it + 1


if __name__ == "__main__":
    cv, vc = _parse_graph()
    print(f"校验行 M={len(cv)}, 每校验变量数={sorted(set(len(x) for x in cv))}")
    print(f"变量 N={len(vc)}, 每变量校验数={sorted(set(len(x) for x in vc))}")
    # 自测1：全零码字
    d, iters = ldpc_bp_decode([10.0] * _N)
    print(f"全零解码: 全零={sum(d)==0}, 迭代={iters}")
    # 自测2：全零码字但中间翻几位（模拟噪声），LLR 反转
    import random
    random.seed(1)
    llr = [10.0] * _N
    flipped = random.sample(range(_N), 6)
    for v in flipped:
        llr[v] = -8.0
    d2, it2 = ldpc_bp_decode(llr)
    print(f"噪声测试(翻{len(flipped)}位): 全零={sum(d2)==0}, 迭代={it2}")
