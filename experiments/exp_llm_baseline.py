#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
实验：LLM 调制识别基线（与经典规则 / KNN-AMR 同数据集同指标对比）
======================================================================

被测的"AI 路径"是**在线 LLM 分类器**：与 exp_baseline_compare 完全相同的确定性
数据集（同一 seed、同一 rng 标签 -> 同一批合成信号）、同一指标（三分类识别
准确率），三条路径同场对比：

  - classic : exp_baseline_compare.classic_classify（瞬时频率 std 阈值规则）。
  - knn_amr : mbdsdr_ai.amr.AMRClassifier（25 维特征 + KNN）。
  - llm    : 在线 LLM（OpenAI 兼容 Chat Completions）。LLM **不看原始 IQ**
             （token 成本），而是看本脚本为每个样本抽取的紧凑特征向量
             （瞬时频率 std、幅度 std），由它在 {FSK, PSK, NOISE} 中选一类。

诚实空态（红线）：
  - API key 只从环境变量读（MBDSDR_LLM_API_KEY / MBDSDR_LLM_BASE_URL），
    代码与文档里**没有任何 key 字面量**。
  - 云内无 key 时：classic / knn 照常本地算出（确定性），LLM 列整列写
    ``PENDING_ONLINE_RUN``，**绝不伪造数值**、绝不真调在线 API。
  - 有 key 时才真调，并把每次回答缓存到 paper/experiments/.llm_cache_<model>.json，
    重跑不重复花钱、结果可复现。

口径：信号生成 = synthetic；当 LLM 真被调用时，本 run 的 data_origin 记为
``online``（AI 列来自在线 API），classic/knn 仍是 synthetic（写进 params）。
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from mbdsdr_ai import amr  # noqa: E402
from experiments.common import runner, manifest  # noqa: E402
from experiments.common import plot as eplot  # noqa: E402
# 复用经典/KNN 基线与同一数据集（同 seed、同 rng 标签 -> 同一批信号）
from experiments.exp_baseline_compare import classic_classify, CLASSES, FS  # noqa: E402

OUT_DIR = os.path.join(ROOT, "paper", "experiments")
FIG_DIR = os.path.join(OUT_DIR, "figures")
CLASSES_SET = list(CLASSES)

# 环境变量名（key 只从这里读；本文件不出现任何 key 字面量）
ENV_KEY = "MBDSDR_LLM_API_KEY"
ENV_BASE = "MBDSDR_LLM_BASE_URL"
DEFAULT_BASE_URL = "https://api.siliconflow.cn/v1"

SYSTEM_PROMPT = (
    "You are an RF modulation classifier. You receive a compact feature vector "
    "extracted from a received signal. Reply with EXACTLY ONE word among: "
    "FSK, PSK, NOISE. Do not add explanation."
)


# ---------------------------------------------------------------------------
# 特征抽取（给 LLM 的紧凑向量；与 classic 用同一 std_f）
# ---------------------------------------------------------------------------
def extract_features(iq: np.ndarray, fs: float = FS):
    """返回 (std_f_hz, std_a)：瞬时频率标准差(Hz)、归一化幅度标准差。"""
    x = np.asarray(iq, dtype=np.complex128)
    ph = np.angle(x)
    dph = np.diff(np.unwrap(ph))
    std_f = float(np.std(dph * fs / (2 * np.pi)))
    amp = np.abs(x)
    std_a = float(np.std(amp) / (np.mean(amp) + 1e-12))
    return std_f, std_a


def parse_llm_answer(text: str) -> str:
    """从 LLM 回复里解析出 FSK/PSK/NOISE；认不出返回 'UNKNOWN'。"""
    t = (text or "").upper()
    for c in CLASSES_SET:
        if c in t:
            return c
    return "UNKNOWN"


# ---------------------------------------------------------------------------
# LLM 客户端（无 key 走空态）
# ---------------------------------------------------------------------------
def build_llm(model: str):
    """读环境变量构造 ModelManager。无 key 返回 (None, reason)。"""
    api_key = os.environ.get(ENV_KEY, "").strip()
    if not api_key:
        return None, f"环境变量 {ENV_KEY} 未设置"
    base_url = os.environ.get(ENV_BASE, DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL
    from mbdsdr_ai.model_manager import ModelManager
    mm = ModelManager(api_key=api_key, base_url=base_url, model=model,
                      temperature=0.0, max_output_tokens=8)
    return mm, base_url


def ask_llm(mm, std_f: float, std_a: float, snr: float) -> str:
    """问一次 LLM 分类；失败返回 'ERROR'（不伪造）。"""
    user = (f"Received signal features (in-band SNR={snr:.1f} dB): "
            f"instantaneous-frequency std = {std_f:.0f} Hz, "
            f"amplitude std = {std_a:.3f}. "
            f"Which modulation? Answer one of FSK/PSK/NOISE.")
    try:
        r = mm.chat([{"role": "system", "content": SYSTEM_PROMPT},
                     {"role": "user", "content": user}])
        if not r.get("success"):
            return "ERROR"
        return parse_llm_answer(r.get("content", ""))
    except Exception:
        return "ERROR"


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="LLM 调制识别基线（无 key 时诚实空态）")
    ap.add_argument("--model", default="Qwen/Qwen2.5-7B-Instruct",
                    help="OpenAI 兼容模型名（可插拔；默认一个常见模型）")
    ap.add_argument("--trials-per-class", type=int, default=20)
    ap.add_argument("--seed", type=int, default=20261001)
    ap.add_argument("--snr-grid", type=str, default="0,5,10,15,20")
    ap.add_argument("--max-llm-samples", type=int, default=0,
                    help="最多真调多少次 LLM（0=不限；有 key 时用于控成本）")
    ap.add_argument("--out", default=OUT_DIR)
    args = ap.parse_args()

    runner_obj = runner.ExperimentRunner(args.seed)
    knn = amr.AMRClassifier(k=5)
    snrs = [float(s) for s in args.snr_grid.split(",")]

    mm, llm_reason = build_llm(args.model)
    llm_enabled = mm is not None
    base_url = llm_reason if llm_enabled else ""

    # LLM 结果缓存（有 key 才读写；键=模型+rng标签，重跑不重复调用）
    cache_path = os.path.join(args.out, f".llm_cache_{args.model.replace('/', '_')}.json")
    cache: dict = {}
    llm_calls = 0
    if llm_enabled:
        if os.path.exists(cache_path):
            try:
                with open(cache_path, encoding="utf-8") as f:
                    cache = json.load(f)
            except Exception:
                cache = {}
        print(f"[LLM] 已启用 model={args.model}  base_url={base_url}  "
              f"缓存={cache_path}  已缓存样本={len(cache)}")
    else:
        print(f"[LLM] 空态：{llm_reason}；LLM 列将输出 PENDING_ONLINE_RUN（不伪造、不联网）")

    rows = []
    bar_groups, bar_classic, bar_knn, bar_llm = [], [], [], []
    err_classic, err_knn, err_llm = [], [], []
    n_llm_correct = 0
    n_llm_total = 0

    for snr in snrs:
        classic_ok = knn_ok = llm_ok = 0
        total = 0
        for cls in CLASSES_SET:
            for k in range(args.trials_per_class):
                tag = f"base|{cls}|{snr}|{k}"   # 与 exp_baseline_compare 同标签
                rng = runner_obj.make_rng(tag)
                iq = amr.synthesize_modulation_iq(cls, rng, snr_db=snr, fs=FS)
                if classic_classify(iq) == cls:
                    classic_ok += 1
                if knn.classify_iq(list(iq), FS).predicted_modulation.value == cls:
                    knn_ok += 1
                # --- LLM 列 ---
                if llm_enabled:
                    if tag in cache:
                        pred = cache[tag]
                    else:
                        if args.max_llm_samples and llm_calls >= args.max_llm_samples:
                            pred = "PENDING"
                        else:
                            std_f, std_a = extract_features(iq, FS)
                            pred = ask_llm(mm, std_f, std_a, snr)
                            cache[tag] = pred
                            llm_calls += 1
                    if pred != "PENDING":
                        n_llm_total += 1
                        if pred == cls:
                            llm_ok += 1
                total += 1
        c_cell = runner.BinomialCell(classic_ok, total)
        k_cell = runner.BinomialCell(knn_ok, total)
        row = {
            "snr_inband_db": snr,
            "classic_correct": classic_ok, "knn_correct": knn_ok,
            "n_trials": total,
            "classic_acc": round(c_cell.rate, 4),
            "classic_wilson_lo": round(c_cell.wilson[0], 4),
            "classic_wilson_hi": round(c_cell.wilson[1], 4),
            "knn_acc": round(k_cell.rate, 4),
            "knn_wilson_lo": round(k_cell.wilson[0], 4),
            "knn_wilson_hi": round(k_cell.wilson[1], 4),
        }
        if llm_enabled and n_llm_total > 0:
            l_cell = runner.BinomialCell(llm_ok, n_llm_total)
            row.update({
                "llm_model": args.model,
                "llm_correct": llm_ok, "llm_n": n_llm_total,
                "llm_acc": round(l_cell.rate, 4),
                "llm_wilson_lo": round(l_cell.wilson[0], 4),
                "llm_wilson_hi": round(l_cell.wilson[1], 4),
            })
            bar_llm.append(l_cell.rate)
            err_llm.append(l_cell.half_width_wilson)
        else:
            row.update({"llm_model": args.model, "llm_agent": "PENDING_ONLINE_RUN",
                        "llm_acc": "", "llm_wilson_lo": "", "llm_wilson_hi": ""})
        row["data_origin"] = "online" if llm_enabled else "synthetic"
        rows.append(row)
        bar_groups.append(f"{snr:g} dB")
        bar_classic.append(c_cell.rate); bar_knn.append(k_cell.rate)
        err_classic.append(c_cell.half_width_wilson); err_knn.append(k_cell.half_width_wilson)
        llm_str = (f"llm={llm_ok}/{n_llm_total}" if llm_enabled else "llm=PENDING_ONLINE_RUN")
        print(f"[SNR={snr:5.1f} dB] classic={c_cell.rate*100:5.1f}%  "
              f"knn={k_cell.rate*100:5.1f}%  {llm_str}", flush=True)

    # 缓存落盘（有 key 才写）
    if llm_enabled:
        os.makedirs(args.out, exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)

    os.makedirs(args.out, exist_ok=True)
    csv_path = os.path.join(args.out, "llm_baseline.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    print(f"\n[写] {csv_path}")

    # 图：classic/knn 总是画；LLM 有数据才加入
    n_samples = len(snrs) * args.trials_per_class * len(CLASSES_SET)
    methods = ["classic_rules", "knn_amr"]
    values = {"classic_rules": bar_classic, "knn_amr": bar_knn}
    errors = {"classic_rules": err_classic, "knn_amr": err_knn}
    if llm_enabled and bar_llm:
        methods.append("llm_" + args.model.split("/")[-1])
        values["llm"] = bar_llm
        errors["llm"] = err_llm
    origin = "online" if llm_enabled else "synthetic"
    fig_path = eplot.plot_grouped_bars(
        groups=bar_groups, methods=methods, values=values, errors=errors,
        origin=origin, n_samples=n_samples, out_dir=FIG_DIR,
        fname_prefix="llm_baseline_amr", ylabel="Recognition accuracy",
        title=f"Classic / KNN / LLM modulation recognition (LLM={'online' if llm_enabled else 'pending'})")
    print(f"[写] {fig_path}")

    mpath = manifest.write_manifest(
        out_dir=args.out, script=__file__, seed=args.seed, data_origin=origin,
        params={"model": args.model,
                "trials_per_class": args.trials_per_class,
                "classes": CLASSES_SET, "snr_grid": snrs,
                "dataset": "与 exp_baseline_compare 同 seed/同 rng 标签",
                "llm_status": "online_called" if llm_enabled else "PENDING_ONLINE_RUN",
                "llm_calls_this_run": llm_calls,
                "llm_env": f"{ENV_KEY}/{ENV_BASE}（仅读环境变量，无 key 字面量）",
                "classic_knn_origin": "synthetic",
                "csv": os.path.basename(csv_path),
                "figure": os.path.basename(fig_path)},
        n_samples=n_samples,
        extra={"note": ("LLM 无 key 时整列 PENDING_ONLINE_RUN，未伪造；"
                        "有 key 时回答经 JSON 缓存复现") if not llm_enabled
              else "LLM 列在线推理，结果缓存于 .llm_cache_*.json"},
        filename="manifest_llm_baseline.json")
    print(f"[写] {mpath}")
    print(f"\n口径: {origin}；总样本 N={n_samples}；"
          f"LLM 列={'在线已跑' if llm_enabled else 'PENDING_ONLINE_RUN（未伪造）'}")


if __name__ == "__main__":
    main()
