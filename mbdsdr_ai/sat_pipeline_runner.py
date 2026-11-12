# SPDX-License-Identifier: MIT
"""卫星 pipeline 调度器骨架 + ETA 估算 + 进度回调。

设计对照 SatDump：
  - 按 stage.level 顺序串联，支持 input_level 断点跳过（pipeline_run.cpp:119-170）：
    数据若已落在某一层（如已有解调音频 .wav），则跳过该层及之前的 stage，
    从下一层继续处理。
  - stage 参数以默认值为底、用户参数覆盖同名 key（pipeline_run.cpp:215-229
    Pipeline::prepareParameters）。
  - ETA 用指数滑动平均平滑（module_demod_base.cpp:383）：
    averaged = 0.01 * current + 0.99 * averaged。

本模块是调度骨架：只负责串联、参数合并、进度与统计收集；
真正的解调/解码算法仍在 noaa_apt_lite / meteor_sat / gk2a_lrit 等模块内，
这里只把它们作为 stage 处理函数挂接进来，不重新实现算法。
未挂接处理函数的 stage 不会抛异常，而是返回 success=False 并说明
"stage ... not implemented"，便于后续逐个补齐。
"""

import time
from typing import Any, Callable, Dict, List, Tuple

from .sat_pipeline_params import (
    STAGE_LEVELS,
    PipelineStage,
    SatellitePipeline,
    get_pipeline,
    level_index,
)

# pipeline 处理层级（与 sat_pipeline_params.STAGE_LEVELS 对齐）。
StageLevel = str  # "baseband" | "soft" | "cadu" | "frames" | "products"

# 进度回调：(fraction 0..1, 当前 stage 名/层级)
ProgressCallback = Callable[[float, str], None]

# 各解码器会回传的标准化统计 key；runner 跨 stage 收集。
DECODE_STATS_KEYS = ["synced", "snr_db", "ber", "locked_lines", "lock_ratio"]


def _fmt_time(seconds: float) -> str:
    """秒 -> MM:SS 或 HH:MM:SS（对照 BaseDemodModule::render_eta_string）。"""
    s = max(0, int(round(float(seconds))))
    h = s // 3600
    m = (s % 3600) // 60
    sec = s % 60
    if h > 0:
        return f"{h:02d}:{m:02d}:{sec:02d}"
    return f"{m:02d}:{sec:02d}"


class EtaEstimator:
    """EMA 平滑的 ETA 估算器（对照 module_demod_base.cpp:368-396）。

    current_eta = elapsed * (1 - fraction) / fraction
    averaged    = 0.01 * current_eta + 0.99 * averaged_eta（首次直接取 current_eta）
    """

    def __init__(self) -> None:
        self.start_time: float = 0.0
        self._averaged: float = -1.0

    def start(self) -> None:
        """记录本次处理开始时间。"""
        self.start_time = time.monotonic()
        self._averaged = -1.0

    def update(self, fraction: float) -> Tuple[str, str]:
        """按当前进度 0..1 更新，返回 (已耗时字符串, 预计剩余字符串)。"""
        if self.start_time <= 0.0:
            self.start()
        elapsed = time.monotonic() - self.start_time
        remaining = 0.0
        if fraction > 0.001 and elapsed > 0:
            current_eta = elapsed * (1.0 - fraction) / fraction
            if self._averaged < 0:
                self._averaged = current_eta
            else:
                self._averaged = 0.01 * current_eta + 0.99 * self._averaged
            remaining = self._averaged
        return (_fmt_time(elapsed), _fmt_time(remaining))


def prepare_params(stage_params: Dict[str, Any],
                   user_params: Dict[str, Any]) -> Dict[str, Any]:
    """stage 默认参数为底，用户参数覆盖同名 key（并补充默认表里没有的新 key）。

    对照 SatDump Pipeline::prepareParameters：先拷贝 stage 参数，再用 pipeline 级
    用户参数逐项覆盖；用户传了、stage 没有的 key 也一并带入。
    """
    final: Dict[str, Any] = dict(stage_params or {})
    for k, v in (user_params or {}).items():
        final[k] = v
    return final


# ---------------------------------------------------------------------------
# stage 处理函数注册表
# ---------------------------------------------------------------------------
# 约定每个 handler 签名：handler(input_data, merged_params) -> dict，
# 返回 {"output": Any, "stats": dict, "success": bool, "error": str?}
STAGE_HANDLERS: Dict[str, Callable[[Any, Dict[str, Any]], Dict[str, Any]]] = {}

# stage 数据契约（对照 SatDump ProcessingModule::getInputTypes/getOutputTypes）：
# {module: {"inputs": (...), "outputs": (...)}}。只在注册时显式声明才登记；
# 未声明的旧 handler 不参与校验，行为与以前逐字一致。
STAGE_CONTRACTS: Dict[str, Dict[str, Tuple[str, ...]]] = {}


def register_stage_handler(
        module: str,
        fn: Callable[[Any, Dict[str, Any]], Dict[str, Any]],
        *,
        inputs: Tuple[str, ...] = None,
        outputs: Tuple[str, ...] = None) -> None:
    """把一个处理函数注册到某个 stage.module 名下。

    ``inputs`` / ``outputs`` 是该 stage 的数据类型声明（如 ``("iq",)`` ->
    ``("cadu",)``）。声明后 ``run_pipeline`` 会在执行前校验相邻 stage 是否
    接得上：前一 stage 的 outputs 与后一 stage 的 inputs 无交集即报错，不再
    等到运行期才炸。两者都不传时保持旧行为（只绑定、不校验）。
    """
    STAGE_HANDLERS[module] = fn
    if inputs is None and outputs is None:
        STAGE_CONTRACTS.pop(module, None)
        return
    STAGE_CONTRACTS[module] = {
        "inputs": tuple(str(t) for t in (inputs or ())),
        "outputs": tuple(str(t) for t in (outputs or ())),
    }


def _check_stage_contracts(stages: List[PipelineStage]) -> str:
    """执行前校验相邻 stage 的类型声明；接不上返回错误串，否则返回 ""。

    只有前后两个 stage 都声明了类型时才校验；任一未声明即跳过（增量兼容）。
    """
    prev_outputs: Tuple[str, ...] = ()
    prev_label = ""
    for stage in stages:
        contract = STAGE_CONTRACTS.get(stage.module)
        if contract is None:
            prev_outputs, prev_label = (), ""
            continue
        label = f"{stage.level} ({stage.module})"
        if prev_outputs and contract["inputs"]:
            if not set(prev_outputs) & set(contract["inputs"]):
                return (f"stage 类型不兼容：{prev_label} 输出 "
                        f"{sorted(prev_outputs)}，{label} 需要 "
                        f"{sorted(contract['inputs'])}")
        prev_outputs = contract["outputs"]
        prev_label = label
    return ""


def _handle_noaa_apt_decode(input_data: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """products 层：APT 音频 -> A/B 云图。挂接 noaa_apt_lite.decode_apt。"""
    from . import noaa_apt_lite

    sr = float(params.get("audio_samplerate", 50e3))
    result = noaa_apt_lite.decode_apt(
        input_data,
        sample_rate=sr,
        polarity=int(params.get("polarity", 1)),
        min_lines=int(params.get("min_lines", 4)),
    )
    present = bool(result.get("apt_present", False))
    out: Dict[str, Any] = {"apt_present": present}
    if present:
        out["image_a"] = result.get("image_a")
        out["image_b"] = result.get("image_b")
    return {"output": out, "stats": result, "success": present}


def _handle_meteor_lrpt_demod(input_data: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """soft 层：IQ -> CADU 帧。挂接 meteor_sat.demodulate_lrpt（内部含解调+去交织+Viterbi+解扰）。"""
    from . import meteor_sat

    constellation = str(params.get("constellation", "qpsk")).upper()
    sat_params = meteor_sat.MeteorSatParams(
        name=str(params.get("name", "meteor")),
        norad_id=int(params.get("norad_id", 0)),
        downlink_freq_hz=float(params.get("downlink_freq_hz", 137.1e6)),
        symbol_rate=float(params.get("symbolrate", 72e3)),
        modulation="OQPSK" if constellation == "OQPSK" else "QPSK",
        viterbi_rate=float(params.get("viterbi_rate", 0.5)),
        viterbi_K=int(params.get("viterbi_K", 7)),
        viterbi_g1=int(params.get("viterbi_g1", 171)),
        viterbi_g2=int(params.get("viterbi_g2", 133)),
        descrambler=str(params.get("descrambler", "CCDB")),
        cadu_length=int(params.get("cadu_length", 1024)),
        orbital_type="LEO",
    )
    cadus = meteor_sat.demodulate_lrpt(
        input_data,
        sample_rate=float(params.get("samplerate", 1e6)),
        sat_params=sat_params,
    )
    cadus = list(cadus)
    return {"output": {"cadu_frames": cadus, "n_frames": len(cadus)},
            "stats": {}, "success": len(cadus) > 0}


def _handle_gk2a_lrit_decode(input_data: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """products 层：IQ -> LRIT 云图 PNG。挂接 gk2a_lrit.decode_iq_to_image。"""
    from . import gk2a_lrit

    res = gk2a_lrit.decode_iq_to_image(
        input_data,
        out_png=str(params.get("out_png", "")),
        sps=int(params.get("sps", 8)),
        f_offset=float(params.get("f_offset", 0.0)),
    )
    return {"output": {"success": bool(res.success),
                       "png_path": getattr(res, "png_path", None),
                       "error": getattr(res, "error", None)},
            "stats": {},
            "success": bool(res.success)}


register_stage_handler("noaa_apt_decode", _handle_noaa_apt_decode,
                       inputs=("audio",), outputs=("images",))
register_stage_handler("meteor_lrpt_demod", _handle_meteor_lrpt_demod,
                       inputs=("iq",), outputs=("cadu",))
register_stage_handler("gk2a_lrit_decode", _handle_gk2a_lrit_decode,
                       inputs=("iq",), outputs=("images",))


def run_pipeline(pipeline_id: str,
                 input_data: Any = None,
                 input_level: StageLevel = "baseband",
                 user_params: Dict[str, Any] = None,
                 progress_cb: ProgressCallback = None) -> Dict[str, Any]:
    """按注册的 pipeline 顺序串联执行 stage。

    参数:
        pipeline_id: sat_pipeline_params 中的 pipeline 标识。
        input_data:  输入数据（IQ baseband / 解调音频 / CADU 等，取决于 input_level）。
        input_level: 数据已处在哪一层；该层及之前的 stage 会被跳过。
        user_params: 用户可调参数，覆盖各 stage 同名 key。
        progress_cb: 进度回调 (fraction 0..1, stage 名)。

    返回:
        {"success": bool, "output": Any, "stats": dict, "error": str}
        未挂接处理函数的 stage 不抛异常，直接返回 success=False。
        相邻 stage 都声明了数据契约但接不上时，在执行前即返回失败
        （error 以「stage 类型不兼容」开头），不跑任何 handler。
    """
    try:
        pipe: SatellitePipeline = get_pipeline(pipeline_id)
    except KeyError as e:
        return {"success": False, "output": None, "stats": {}, "error": str(e)}

    user_params = user_params or {}
    if input_level not in STAGE_LEVELS:
        return {"success": False, "output": input_data, "stats": {},
                "error": f"未知 input_level: {input_level}"}
    start_idx = level_index(input_level)

    # 只执行严格深于 input_level 的 stage（跳过该层及之前）。
    todo: List[PipelineStage] = [s for s in pipe.stages
                                 if level_index(s.level) > start_idx]
    total = max(1, len(todo))

    contract_err = _check_stage_contracts(todo)
    if contract_err:
        return {"success": False, "output": input_data, "stats": {},
                "error": contract_err}

    eta = EtaEstimator()
    eta.start()
    stats: Dict[str, Any] = {}
    current = input_data

    def _report(fraction: float, stage_name: str) -> None:
        if progress_cb is None:
            return
        try:
            progress_cb(fraction, stage_name)
        except Exception:
            # 进度回调异常不得中断解码主链。
            pass

    for i, stage in enumerate(todo):
        fraction = i / total
        _report(fraction, stage.level)
        eta.update(fraction)

        handler = STAGE_HANDLERS.get(stage.module)
        if handler is None:
            return {"success": False, "output": current, "stats": stats,
                    "error": f"stage {stage.level} ({stage.module}) not implemented"}

        merged = prepare_params(stage.params, user_params)
        try:
            res = handler(current, merged)
        except Exception as e:  # 解码器运行期异常（如缺依赖/空数据）一律收敛为失败
            return {"success": False, "output": current, "stats": stats,
                    "error": f"stage {stage.level} ({stage.module}) raised "
                             f"{type(e).__name__}: {e}"}

        st = res.get("stats", {}) or {}
        for k in DECODE_STATS_KEYS:
            if k in st and st[k] is not None:
                stats[k] = st[k]

        if not res.get("success", False):
            err = res.get("error", "unknown")
            return {"success": False, "output": res.get("output"), "stats": stats,
                    "error": f"stage {stage.level} ({stage.module}) failed: {err}"}

        current = res.get("output", current)

    _report(1.0, "done")
    eta.update(1.0)
    return {"success": True, "output": current, "stats": stats, "error": ""}
