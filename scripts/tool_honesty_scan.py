# SPDX-License-Identifier: MIT
# -*- coding: utf-8 -*-
"""AI 工具诚实性全扫（2026-11-05 接入层批次）。

对 MCP 工具表 82 个工具，在三种环境下各调一次：
  ① no_device  ：SDRController 无后端（未连接任何 SDR）
  ② offline    ：拦截所有外部网络连接（保留本机监听/绑定）
  ③ no_api_key ：清空常见模型 API key 环境变量（本表无模型工具，作反证）

调用方式：**直接调用 handler，绕过写工具的一次性 confirm_token 门**。
原因：门在无 token 时一律诚实拒绝，无法观察实现本身是否会假成功；
本脚本要看的是「授权后，环境不具备时它会不会编数据」。

输出：JSON（--json）与可贴进 docs 的 Markdown（--md）。
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import socket
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from mbdsdr_ai.core.mcp_server import MCPToolSet, _TOOL_DOMAIN


API_KEY_ENVS = (
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY",
    "DASHSCOPE_API_KEY", "GEMINI_API_KEY", "MOONSHOT_API_KEY",
    "ARK_API_KEY", "ZHIPU_API_KEY",
)

# 真正依赖网络的工具（只有这些在 offline 环境有判定意义）
NETWORK_TOOLS = {
    "mcp_client_connect", "mcp_client_call",
}
# 只在本机起监听、不访问外网的（不计为网络工具）
LOCAL_SERVER_TOOLS = {"start_remote_control", "start_web_server"}

# 无设备时「数值只能来自硬件测量」的读取类工具
DEVICE_MEASURE_TOOLS = {
    "read_spectrum", "read_iq", "read_audio", "learn_noise_floor",
    "get_scan_results", "get_decoder_messages", "get_recording_status",
    "start_analyze", "start_scan", "start_decoder",
}
# 无设备时 ok=true 但没有任何「未应用」说明的设值类工具
DEVICE_SETTER_TOOLS = {
    "set_frequency", "set_demod", "set_gain", "set_sample_rate",
    "set_bandwidth", "set_squelch", "set_deemphasis", "set_iq_invert",
    "set_audio_output", "set_volume", "set_mute", "set_anr_enabled",
    "set_anr_strength", "add_vfo", "remove_vfo", "set_primary_vfo",
    "start_decoder", "stop_recording", "stop_audio",
}
# 无设备时返回「配置值」但不声明 connected=false 的读取类工具
DEVICE_CONFIG_READERS = {"get_frequency", "get_demod"}

_TMP = os.path.join(_ROOT, ".audit_tmp")


def _args_for(name: str) -> dict:
    """每个工具一次调用所需的最小合法参数（缺省按真实语义填）。"""
    os.makedirs(_TMP, exist_ok=True)
    path = os.path.join(_TMP, name + ".out")
    common = {
        "list_devices": {},
        "connect": {"device_id": "rtlsdr:MBDSDR_AUDIT_NO_SUCH_DEVICE"},
        "disconnect": {},
        "is_connected": {},
        "set_frequency": {"hz": 100_000_000.0},
        "get_frequency": {},
        "set_demod": {"mode": "FM"},
        "get_demod": {},
        "set_gain": {"db": 20.0},
        "set_sample_rate": {"sr": 2_400_000.0},
        "set_bandwidth": {"hz": 200_000.0},
        "set_squelch": {"db": -50.0},
        "set_deemphasis": {"tau_us": "75"},
        "set_iq_invert": {"invert": False},
        "list_audio_outputs": {},
        "set_audio_output": {"index": 0},
        "start_audio": {},
        "stop_audio": {},
        "set_volume": {"db": 0.0},
        "set_mute": {"muted": False},
        "read_spectrum": {"nfft": 1024},
        "read_iq": {"n": 1024},
        "read_audio": {"n": 1024},
        "export_spectrum_snapshot": {
            "freqs_hz": [99e6, 100e6, 101e6],
            "db": [-80.0, -50.0, -75.0], "path": path},
        "export_peaks_csv": {
            "peaks": [[100e6, -50.0, "audit"]], "path": path},
        "vna_analyze_sweep": {
            "freqs_hz": [1e6, 2e6],
            "s11": [[0.5, 0.0], [0.4, 0.1]]},
        "vna_solve_error_box": {
            "freqs_hz": [1e6, 2e6],
            "meas_open": [[0.9, 0.0], [0.8, 0.1]],
            "meas_short": [[-0.9, 0.0], [-0.8, 0.1]],
            "meas_load": [[0.0, 0.0], [0.0, 0.0]]},
        "vna_apply_calibration": {
            "s11": [[0.5, 0.0]],
            "e00": [[0.0, 0.0]],
            "t": [[1.0, 0.0]],
            "e11": [[0.0, 0.0]]},
        "export_vna_report": {
            "freqs_hz": [1e6, 2e6],
            "s11": [[0.5, 0.0], [0.4, 0.1]], "path": path},
        "start_analyze": {},
        "start_scan": {"start_hz": 90e6, "stop_hz": 110e6,
                       "step_hz": 1e6},
        "stop_scan": {"handle": "audit-scan"},
        "get_scan_results": {"handle": "audit-scan"},
        "start_decoder": {"decoder_type": "pocsag"},
        "stop_decoder": {"handle": "audit-dec"},
        "get_decoder_messages": {"handle": "audit-dec"},
        "list_satellites": {},
        "get_satellite_pass": {"norad": 25544},
        "tune_satellite": {"norad": 25544},
        "set_ground_station": {"lat": 31.0, "lon": 121.0, "alt_m": 10.0},
        "sat_link_list": {},
        "sat_link_get": {"name": "ISS"},
        "sat_link_identify": {"freq_hz": 145_800_000.0},
        "sat_link_tuning": {"norad_name": "ISS"},
        "add_bookmark": {"freq_hz": 145_500_000.0, "name": "audit"},
        "list_bookmarks": {},
        "delete_bookmark": {"id": 145500000},
        "find_nearest_bookmark": {"freq_hz": 145_500_000.0},
        "start_recording": {"path": path},
        "stop_recording": {},
        "get_recording_status": {},
        "start_remote_control": {"port": 18089},
        "stop_remote_control": {},
        "start_web_server": {"port": 18090},
        "stop_web_server": {},
        "get_service_status": {},
        "set_anr_enabled": {"enabled": True},
        "set_anr_strength": {"level": 3},
        "learn_noise_floor": {},
        "add_vfo": {"freq_hz": 145_500_000.0},
        "remove_vfo": {"vfo_id": "audit-vfo"},
        "set_primary_vfo": {"vfo_id": "audit-vfo"},
        "list_vfos": {},
        "get_status": {},
        "shutdown": {},
        "decoder_dab_fic_decode": {"fic_hex": "00"},
        "decoder_nrsc5_frame_parse": {"data_hex": "00"},
        "decoder_rtl433_list": {},
        "decoder_rtl433_decode": {"device_type": "weather",
                                  "message_hex": "00"},
        "decoder_gk2a_parse_headers": {"buf_hex": "00"},
        "decoder_fst4_encode_text": {"text": "AUDIT"},
        "device_bladerf_params_query": {},
        "aigui_component_catalog": {},
        "aigui_validate_message": {
            "components": [{"type": "text", "text": "audit"}]},
        "aigui_render_preview": {
            "components": [{"type": "text", "text": "audit"}],
            "out_path": path},
        "ask_question": {"question": "audit?", "options": ["yes", "no"]},
        "answer_question": {"question_id": "audit-q", "answer": "yes"},
        "question_status": {},
        "mcp_client_list": {},
        "mcp_client_connect": {"name": "audit-nonexistent"},
        "mcp_client_disconnect": {"name": "audit-nonexistent"},
        "mcp_client_call": {"server": "audit-nonexistent", "tool": "x"},
    }
    return copy.deepcopy(common.get(name, {}))


def _install_offline():
    """把所有外部连接打为拒绝；保留本机 bind/listen（服务器启停仍需真执行）。"""
    real_create = socket.create_connection
    real_connect = socket.socket.connect

    def _is_local(addr):
        if not isinstance(addr, tuple) or not addr:
            return False
        host = str(addr[0])
        return host in ("127.0.0.1", "::1", "localhost")

    def deny_create(*a, **k):
        raise ConnectionRefusedError(111, "Connection refused (audit offline)")

    def deny_connect(self, addr, *a, **k):
        if _is_local(addr):
            return real_connect(self, addr, *a, **k)
        raise OSError(101, "Network is unreachable (audit offline)")

    socket.create_connection = deny_create
    socket.socket.connect = deny_connect
    return real_create, real_connect


def _classify(name: str, env: str, result) -> tuple:
    """返回 (ok, code, fabricated, verdict, note)。

    verdict ∈ 诚实 / 假成功 / 静默 / 不适用。
    """
    # 先解析真实回执（ok/code），再判环境适用性——表里的 ok 列必须是调用方
    # 真正看到的 ok，不能因为「环境不适用」就一律写成 false。
    exc = isinstance(result, dict) and bool(result.get("exception"))
    if exc:
        ok, code = False, str(result["exception"])
        has_expl = True
    elif isinstance(result, dict):
        ok = bool(result.get("ok")) if "ok" in result else not (
            result.get("error") or result.get("code"))
        code = str(result.get("code") or result.get("error") or
                   ("ok" if ok else ""))
        has_expl = any(result.get(k) for k in
                       ("error", "suggestion", "hint", "note", "reason"))
    elif isinstance(result, (list, tuple)):
        ok, code, has_expl = True, "ok", False
    else:
        ok, code, has_expl = bool(result), "ok", False

    not_applicable = None
    if env == "no_api_key":
        not_applicable = "本表无模型类工具（模型调用在 agent/provider 层）"
    elif env == "offline" and name not in NETWORK_TOOLS:
        not_applicable = "工具不访问外网，离线环境对它无区分力"
    if not_applicable:
        return ok, code, False, "不适用", not_applicable

    if not ok:
        note = "抛出用户可见错误" if exc else ""
        return False, code, False, "诚实", note

    # ok=true
    blob = json.dumps(result, ensure_ascii=False, default=str)
    fabricated = False
    if env == "no_device":
        if name in DEVICE_MEASURE_TOOLS:
            # 测量类：出现非空数值/数组即视为编造
            def _nums(x):
                if isinstance(x, dict):
                    return any(_nums(v) for v in x.values())
                if isinstance(x, (list, tuple)):
                    return bool(x) and any(_nums(v) for v in x)
                return isinstance(x, (int, float)) and not isinstance(x, bool)
            fabricated = bool(_nums(result))
        elif name in DEVICE_CONFIG_READERS:
            # 配置读取：返回具体频率/模式却不声明未连接
            declared = isinstance(result, dict) and (
                result.get("connected") is False
                or "未连接" in blob or "configured" in result)
            fabricated = not declared
        if name in DEVICE_SETTER_TOOLS and not has_expl:
            return True, code, fabricated, "静默", "ok=true 且无未应用说明"
        if fabricated:
            return True, code, True, "假成功", "无设备仍返回具体数值"
        if has_expl:
            return True, code, False, "诚实", "ok=true 且说明了不可测/未应用"
        return True, code, False, "诚实", ""

    if env == "offline" and name in NETWORK_TOOLS:
        if ok and not has_expl and not result.get("connected"):
            return True, code, False, "静默", "离线仍 ok=true 无说明"
        if fabricated:
            return True, code, True, "假成功", ""
    return True, code, fabricated, "诚实", ""


def _safe_call(handler, controller, args):
    t0 = time.time()
    try:
        out = handler(controller, args)
        err = ""
    except Exception as e:  # 如实记录异常，不吞成成功
        out = {"exception": type(e).__name__, "detail": str(e)}
        err = str(e)
    return out, (time.time() - t0), err


def run(json_path: str, md_path: str) -> int:
    envs = ["no_device", "offline", "no_api_key"]
    rows = []
    for env in envs:
        saved_env = {}
        restored = None
        if env == "no_api_key":
            for k in API_KEY_ENVS:
                saved_env[k] = os.environ.pop(k, None)
        if env == "offline":
            restored = _install_offline()
        toolset = MCPToolSet()
        try:
            if env == "offline":
                # 真正走一次网络：注册一个不可达的远端 server，让 connect 撞上
                # 被拦截的连接（否则 unknown_server 会先返回，离线路径根本没跑到）。
                toolset.mcp_clients.register({
                    "name": "audit-offline",
                    "type": "streamable_http",
                    "url": "http://10.255.255.1:9/mcp",
                })
            for tool in toolset._table:
                name = tool["name"]
                args = _args_for(name)
                if env == "offline" and name == "mcp_client_connect":
                    args = {"name": "audit-offline"}
                elif env == "offline" and name == "mcp_client_call":
                    args = {"server": "audit-offline", "tool": "ping",
                            "arguments": {}}
                result, elapsed, _err = _safe_call(
                    tool["handler"], toolset.controller, args)
                ok, code, fab, verdict, note = _classify(name, env, result)
                rows.append({
                    "tool": name, "domain": _TOOL_DOMAIN.get(name, ""),
                    "env": env, "code": code, "ok": ok,
                    "fabricated": fab, "verdict": verdict,
                    "note": note, "elapsed_ms": round(elapsed * 1e3, 1),
                    "result": result,
                })
        finally:
            if env == "offline" and restored:
                socket.create_connection, socket.socket.connect = restored
            for k, v in saved_env.items():
                if v is not None:
                    os.environ[k] = v

    os.makedirs(os.path.dirname(json_path), exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2, default=str)

    # ── 汇总 ──
    per = {}
    for r in rows:
        per.setdefault(r["env"], []).append(r)
    summary = {}
    for env, rs in per.items():
        v = [r["verdict"] for r in rs]
        summary[env] = {
            "诚实": v.count("诚实"),
            "假成功": v.count("假成功"),
            "静默": v.count("静默"),
            "不适用": v.count("不适用"),
            "total": len(rs),
        }

    lines = ["# AI 工具诚实性矩阵", "",
             "> 生成命令：`python scripts/tool_honesty_scan.py "
             "--json D:\\mbdsdr\\.tool_honesty_run.json "
             "--md docs/tool-honesty-matrix.md`", "",
             "判定口径（写死）：假成功 = `ok=true` 且返回不可测的具体数值；"
             "诚实 = `ok=false` + 可行动文案，或 `ok=true` 但不可测量为 null/—；"
             "静默 = `ok=true` 且空数据、无说明（不合格）。", "",
             "## 汇总", "", "| 环境 | 诚实 | 假成功 | 静默 | 未覆盖（不适用） | 总计 |",
             "| --- | --- | --- | --- | --- | --- |"]
    for env in envs:
        s = summary[env]
        lines.append(f"| {env} | {s['诚实']} | {s['假成功']} | {s['静默']} | "
                     f"{s['不适用']} | {s['total']} |")
    lines += [
        "",
        "### 未覆盖（J）及原因",
        "",
        f"- `no_device`：**J={summary['no_device']['不适用']}**，全部工具都在无设备环境下有区分力。",
        f"- `offline`：**J={summary['offline']['不适用']}**，这些工具不访问外网"
        "（纯本地 DSP/文件/配置/解码），断网不会改变它们的正确行为。",
        f"- `no_api_key`：**J={summary['no_api_key']['不适用']}**，本 MCP 工具表"
        "**没有任何模型类工具**；模型调用在 agent/provider 层，不在此表，"
        "因此清空 API key 对 82 个工具均无区分力（这是覆盖边界，不是诚实性结论）。",
        "",
        "### 修复前后对照（反例）",
        "",
        "修复前实测（同一脚本、绕过 confirm_token 直接调 handler）：",
        "",
        "- `set_frequency`/`set_demod`/`set_gain`/`set_sample_rate`/`set_bandwidth`/"
        "`set_squelch`/`set_volume`/`set_mute`/`set_anr_enabled`/`set_anr_strength`/"
        "`add_vfo`/`remove_vfo`/`stop_recording`/`stop_audio` 等设值工具在无设备时"
        "返回 `{\"ok\": true}`，无 `connected` / `applied` / `note` —— 调用方会误以为"
        "已下发硬件（静默，等同不合格）。",
        "- `get_frequency` / `get_demod` 返回具体数值/模式，不声明 `connected=false`，"
        "无法区分「配置目标」与「硬件回读」。",
        "- `start_scan` / `start_decoder` 在未连接时返回成功；`stop_scan` / "
        "`stop_decoder` 对不存在的句柄返回成功；`get_scan_results` / "
        "`get_decoder_messages` 空结果无说明。",
        "- `start_audio` 失败只回 `{\"ok\": false}`，无可行动文案；"
        "`learn_noise_floor` 只回 `{\"error\": \"not_connected\"}`。",
        "",
        "修复后：设值工具统一回执 `connected=false / applied=false / stored=true / note`；"
        "配置读取带 `source: configured-target`；扫描/解码器未连接与未知句柄一律 "
        "`ok=false`；`start_audio` / `learn_noise_floor` 失败带 error + suggestion。"
        "上表汇总即修复后实测。",
        "",
        "## 明细", "",
              "| 工具 | 域 | 环境 | code | ok | 编造值 | 判定 | 备注 |",
              "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in rows:
        lines.append(
            f"| `{r['tool']}` | {r['domain']} | {r['env']} | "
            f"`{r['code']}` | {str(r['ok']).lower()} | "
            f"{'是' if r['fabricated'] else '否'} | {r['verdict']} | "
            f"{r['note']} |")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=os.path.join(_ROOT, ".audit_tmp",
                                                  "tool_honesty.json"))
    ap.add_argument("--md", default=os.path.join(_ROOT, "docs",
                                                "tool-honesty-matrix.md"))
    return run(ap.parse_args().json, ap.parse_args().md)


if __name__ == "__main__":
    raise SystemExit(main())
