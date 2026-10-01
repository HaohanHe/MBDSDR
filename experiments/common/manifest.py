# SPDX-License-Identifier: MIT
"""
运行清单（manifest）：每次实验 run 落一份 JSON，锁定可复现性
=================================================================

B2 审计 §5：旧 CSV 与脚本漂移（ADS-B 提交 n=20 vs 脚本 trials=200）、无日期/
无 manifest。本模块让每次 run 自证：

  script        : 产生本批产物的脚本名
  seed          : 主随机种子（int）
  data_origin   : "synthetic" | "recorded" | "ota" | "online" —— 口径，图/CSV 必须同标
  params        : 本 run 的关键参数（网格、trials、fs、Rb、B、模式…）
  n_samples     : 总样本数（trials × 格点数 等，由调用方算好传入）
  timestamp_utc : ISO8601 UTC 时间戳
  code_version  : git short sha（拿不到时如实标 "unknown"，不编造）

红线：data_origin 只能是枚举内值（synthetic/recorded/ota/online）；synthetic 绝不在图/manifest 里写成 ota。
"""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from typing import Any, Dict

# data_origin 枚举：
#   synthetic : 脚本内合成信号 + 固定种子加噪（仿真）
#   recorded  : 真实 RTL-SDR 录制（SigMF）回放
#   ota       : 实时空中信号
#   online    : 在线 LLM/API 推理结果（非本地确定性计算）。用于 LLM 基线列；
#               信号本身仍多为 synthetic，但"AI 列"数值来自在线 API，可复现性
#               依赖缓存（见 exp_llm_baseline.py）。云内无 key 时该列为空态。
ALLOWED_ORIGINS = ("synthetic", "recorded", "ota", "online")


def git_sha() -> str:
    """取 git short sha；不在 git 仓库/无 git 时返回 "unknown"（不报错）。"""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5)
        if out.returncode == 0:
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return "unknown"


def write_manifest(out_dir: str, script: str, seed: int, data_origin: str,
                   params: Dict[str, Any], n_samples: int,
                   extra: Dict[str, Any] | None = None,
                   filename: str = "manifest.json") -> str:
    """写一份 run 清单 JSON，返回其路径。

    参数:
        out_dir     : 落点目录（通常 paper/experiments/<run>/）。
        script      : 入口脚本名（__file__ 的 basename）。
        seed        : 主种子。
        data_origin : synthetic|recorded|ota（非法值直接拒绝）。
        params      : 关键参数字典。
        n_samples   : 总样本数。
        extra       : 附加字段（产物文件列表等）。
    """
    if data_origin not in ALLOWED_ORIGINS:
        raise ValueError(
            f"data_origin 必须是 {ALLOWED_ORIGINS}，得到 '{data_origin}'")
    os.makedirs(out_dir, exist_ok=True)
    manifest: Dict[str, Any] = {
        "script": os.path.basename(script),
        "seed": int(seed),
        "data_origin": data_origin,
        "origin_label": {"synthetic": "仿真", "recorded": "录制",
                         "ota": "OTA", "online": "在线LLM"}[data_origin],
        "params": params,
        "n_samples": int(n_samples),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "code_version": git_sha(),
        "license": "MIT",
    }
    if extra:
        manifest["extra"] = extra
    path = os.path.join(out_dir, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    return path
