# SPDX-License-Identifier: MIT
"""
统一论文出图：Agg 无头、强制图注规范、无中文乱码
=====================================================

B2 审计 §2.5 强制图注规范：
- 图题与**文件名**都必须含：口径(synthetic/recorded/ota) + 样本数 N + 生成日期(UTC)。
- 坐标轴带物理单位；图例写明 method；误差棒 = 二项 Wilson 95% CI。
- 无头 Agg；图内文字一律用**英文**（云 VM 无中文字体，避免乱码方块），
  口径在图题里以 "synthetic/recorded/OTA" 显式标注。
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

# 固定风格，避免依赖系统中文字体导致方块乱码。
plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "axes.unicode_minus": False,
    "figure.dpi": 110,
})


def _date_tag() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def _safe(s: str) -> str:
    return s.replace(" ", "_").replace("/", "-").replace("(", "").replace(")", "")


def _title_suffix(origin: str, n_samples: int) -> str:
    return f"[{origin}, N={n_samples}, {_date_tag()}]"


def plot_confusion_matrix(matrix_norm, labels, origin: str, n_samples: int,
                          out_dir: str, fname_prefix: str,
                          title: str = "") -> str:
    """归一化混淆矩阵热图（行=真实，列=预测），格内写百分比。

    参数:
        matrix_norm: (n_classes, n_classes) 归一化矩阵（每行和≈1），可转 list。
        labels     : 类别名列表（与矩阵行/列顺序一致）。
    返回: 写出的 PNG 绝对路径。图题/文件名含口径+样本数+日期（统一规范）。
    """
    import numpy as _np
    os.makedirs(out_dir, exist_ok=True)
    M = _np.asarray(matrix_norm, dtype=float)
    n = len(labels)
    fig, ax = plt.subplots(figsize=(6.5, 5.5))
    im = ax.imshow(M, cmap="Blues", vmin=0.0, vmax=1.0, aspect="equal")
    ax.set_xticks(range(n)); ax.set_xticklabels(labels, rotation=45, ha="right")
    ax.set_yticks(range(n)); ax.set_yticklabels(labels)
    ax.set_xlabel("Predicted"); ax.set_ylabel("True")
    for i in range(n):
        for j in range(n):
            v = M[i, j]
            color = "white" if v > 0.5 else "black"
            ax.text(j, i, f"{v*100:.0f}", ha="center", va="center",
                    color=color, fontsize=9)
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04, label="Row-normalized rate")
    suffix = _title_suffix(origin, n_samples)
    ax.set_title((title + " " if title else "") + suffix, fontsize=10)
    fig.tight_layout()
    fname = f"{_safe(fname_prefix)}__{origin}__N{n_samples}__{_date_tag()}.png"
    path = os.path.join(out_dir, fname)
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def plot_success_vs_ebn0(curves: Dict[str, Dict[str, Sequence[float]]],
                         origin: str, n_samples: int, out_dir: str,
                         fname_prefix: str,
                         xlabel: str = "Eb/N0 (dB)",
                         ylabel: str = "Decode success rate",
                         title: str = "") -> str:
    """多条"成功率 vs Eb/N0"曲线，带 Wilson CI 误差棒。

    参数:
        curves : {method_name: {"x": [...ebn0_db...], "rate": [...],
                               "lo": [...], "hi": [...]}}
        origin : synthetic|recorded|ota（进图题与文件名）。
        n_samples: 总样本数 N（进图题与文件名）。
        fname_prefix: 文件名前缀（不含扩展名）。
    返回: 写出的 PNG 绝对路径。
    """
    os.makedirs(out_dir, exist_ok=True)
    fig, ax = plt.subplots(figsize=(7, 5))
    for name, c in curves.items():
        x = list(c["x"])
        rate = list(c["rate"])
        lo = list(c["lo"])
        hi = list(c["hi"])
        yerr_lo = [r - l for r, l in zip(rate, lo)]
        yerr_hi = [h - r for r, h in zip(rate, hi)]
        ax.errorbar(x, rate, yerr=[yerr_lo, yerr_hi],
                    marker="o", capsize=3, lw=1.4, label=name)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.set_ylim(-0.03, 1.05)
    ax.grid(True, alpha=0.3)
    ax.legend()
    suffix = _title_suffix(origin, n_samples)
    ax.set_title((title + " " if title else "") + suffix, fontsize=10)
    fig.tight_layout()
    fname = f"{_safe(fname_prefix)}__{origin}__N{n_samples}__{_date_tag()}.png"
    path = os.path.join(out_dir, fname)
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def plot_grouped_bars(groups: List[str], methods: List[str],
                      values: Dict[str, List[float]],
                      errors: Optional[Dict[str, List[float]]],
                      origin: str, n_samples: int, out_dir: str,
                      fname_prefix: str, ylabel: str = "Accuracy",
                      title: str = "") -> str:
    """分组柱状图（group × method），可选误差棒。

    values: {method: [value_per_group]}；errors: {method: [half_width_per_group]}。
    """
    import numpy as _np
    os.makedirs(out_dir, exist_ok=True)
    fig, ax = plt.subplots(figsize=(7, 5))
    ng, nm = len(groups), len(methods)
    idx = _np.arange(ng)
    width = 0.8 / max(1, nm)
    for j, m in enumerate(methods):
        v = values[m]
        err = errors[m] if errors else None
        ax.bar(idx + j * width, v, width, yerr=err, capsize=3, label=m)
    ax.set_xticks(idx + width * (nm - 1) / 2.0)
    ax.set_xticklabels(groups)
    ax.set_ylabel(ylabel)
    ax.set_ylim(0, 1.05)
    ax.grid(True, axis="y", alpha=0.3)
    ax.legend()
    suffix = _title_suffix(origin, n_samples)
    ax.set_title((title + " " if title else "") + suffix, fontsize=10)
    fig.tight_layout()
    fname = f"{_safe(fname_prefix)}__{origin}__N{n_samples}__{_date_tag()}.png"
    path = os.path.join(out_dir, fname)
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def plot_convergence(t: Optional[Sequence[float]], curves: Dict[str, Sequence[float]],
                     origin: str, n_samples: int, out_dir: str,
                     fname_prefix: str,
                     xlabel: str = "Time (s)",
                     ylabel: str = "3D position error (km)",
                     title: str = "",
                     curves_x: Optional[Dict[str, Sequence[float]]] = None) -> str:
    """定轨收敛曲线（纵轴 log），可多条方法。

    t 为公共 x 轴；若某条曲线采样点不同（如 RLS 抽样增长窗），用 curves_x
    提供 {method: [x...]} 覆盖公共 t。
    """
    os.makedirs(out_dir, exist_ok=True)
    fig, ax = plt.subplots(figsize=(7, 5))
    for name, err in curves.items():
        xs = curves_x[name] if curves_x and name in curves_x else t
        ax.plot(list(xs), list(err), marker=".", lw=1.4, label=name)
    ax.axhline(1.0, color="r", ls="--", lw=1.0, label="1 km threshold")
    ax.set_yscale("log")
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.grid(True, which="both", alpha=0.3)
    ax.legend()
    suffix = _title_suffix(origin, n_samples)
    ax.set_title((title + " " if title else "") + suffix, fontsize=10)
    fig.tight_layout()
    fname = f"{_safe(fname_prefix)}__{origin}__N{n_samples}__{_date_tag()}.png"
    path = os.path.join(out_dir, fname)
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path
