# SPDX-License-Identifier: MIT
"""离线批处理解码入口（P5 之后的无阻塞工作之一）。

给一段（或一目录）捕获文件跑 `decode_digital_mode`，产出**结构化 JSON 报告**，
不依赖硬件、不开声卡、不需要界面——同一份输入谁跑都得到同样结构的结果。

设计原则（与仓内「诚实优先」一致）：
  * 每种模式的结果原样保留 ok/status/error_code，不把「解码器没跑起来」
    折叠成「0 条报文」；
  * 每个文件都记 SHA-256 与字节数，报告可当审计证据；
  * 退出码只表示「报告是否生成成功」，不表示「解出东西没有」——
    零报文是合法结果，不是错误。

用法：
    python scripts/decode_captures.py --mode ais --file cap.cf32 --fs 192000
    python scripts/decode_captures.py --mode all --dir captures/ --out report.json
    python scripts/decode_captures.py --mode ais --file cap.cf32 --audit-dir logs/
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

AUDIO_EXTS = (".wav",)
IQ_EXTS = (".cf32", ".cfile")
ALL_EXTS = AUDIO_EXTS + IQ_EXTS


def sha256_of(path: str, limit: int = 0) -> str:
    """文件摘要（limit>0 时只算前 limit 字节，用于大文件快速指纹）。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        if limit:
            h.update(f.read(limit))
        else:
            while True:
                chunk = f.read(1 << 20)
                if not chunk:
                    break
                h.update(chunk)
    return h.hexdigest()


def discover_files(directory: str) -> list:
    """目录内按扩展名收捕获文件（排序稳定，报告可复现）。"""
    out = []
    for name in sorted(os.listdir(directory)):
        path = os.path.join(directory, name)
        if os.path.isfile(path) and os.path.splitext(name)[1].lower() in ALL_EXTS:
            out.append(path)
    return out


def _summarize(results: list) -> dict:
    summary = {
        "files": len(results),
        "decoded": 0, "empty": 0, "error": 0, "not_implemented": 0,
        "messages_total": 0,
        "by_error_code": {},
    }
    for r in results:
        code = r.get("error_code") or ""
        if code == "not_implemented":
            summary["not_implemented"] += 1
            continue
        if code:
            summary["error"] += 1
            summary["by_error_code"][code] = summary["by_error_code"].get(code, 0) + 1
            continue
        n = int(r.get("num_messages") or 0)
        summary["messages_total"] += n
        if n > 0:
            summary["decoded"] += 1
        else:
            summary["empty"] += 1
    return summary


def decode_file(path: str, mode: str, sample_rate: float = None) -> dict:
    """单个文件解码（转调 P5.2 能力表路由，不复制任何解码逻辑）。"""
    from mbdsdr_ai.decode_router import run_digital_mode

    t0 = time.time()
    result = run_digital_mode(path, mode, sample_rate=sample_rate)
    entry = {
        "file": path,
        "bytes": os.path.getsize(path) if os.path.exists(path) else 0,
        "sha256": sha256_of(path) if os.path.exists(path) else "",
        "mode": result.get("mode", mode),
        "ok": bool(result.get("ok", False)),
        "status": result.get("status", ""),
        "error_code": result.get("error_code", ""),
        "evidence_level": result.get("evidence_level", ""),
        "output_kind": result.get("output_kind", ""),
        "num_messages": int(result.get("num_messages") or 0),
        "messages": result.get("messages") or [],
        "error": result.get("error", ""),
        "note": result.get("note", ""),
        "elapsed_ms": round((time.time() - t0) * 1000.0, 1),
    }
    return entry


def build_report(files: list, mode: str, sample_rate: float = None) -> dict:
    results = [decode_file(p, mode, sample_rate) for p in files]
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "mode": mode,
        "sample_rate_hz": (float(sample_rate) if sample_rate is not None else None),
        "environment": {
            "null_audio": os.environ.get("MBDSDR_NULL_AUDIO", ""),
            "no_hw": os.environ.get("MBDSDR_NO_HW", ""),
            "python": sys.version.split()[0],
        },
        "results": results,
        "summary": _summarize(results),
    }


def write_report(report: dict, out_path: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return out_path


def iter_results(report: dict) -> list:
    """兼容两种报告结构：单模式（results）与多模式（reports → results）。"""
    if "reports" in report:
        return [r for rep in report["reports"] for r in rep.get("results", [])]
    return list(report.get("results", []))


def _audit(report: dict, audit_dir: str) -> str:
    from mbdsdr_ai.decode_audit import AUDIT_FILENAME, append_audit, audit_record

    path = os.path.join(audit_dir, AUDIT_FILENAME)
    for r in iter_results(report):
        append_audit(path, audit_record("batch_decode", r, r.get("file", "")))
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="离线批处理解码（不碰硬件/声卡）")
    ap.add_argument("--mode", required=True,
                    help="数字模式（见 decode_router.supported_modes()）或 all")
    ap.add_argument("--file", help="单个捕获文件")
    ap.add_argument("--dir", help="捕获目录（与 --file 二选一）")
    ap.add_argument("--fs", type=float, default=None,
                    help="IQ 采样率 Hz（缺省按模式取合法值）")
    ap.add_argument("--out", help="报告 JSON 输出路径")
    ap.add_argument("--audit-dir", help="另写结构化审计日志 decode_audit.jsonl")
    argv = sys.argv[1:] if argv is None else argv
    args = ap.parse_args(argv)

    if not args.file and not args.dir:
        ap.error("需要 --file 或 --dir")
    if args.file and args.dir:
        ap.error("--file 与 --dir 只能二选一")

    os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")
    os.environ.setdefault("MBDSDR_NO_HW", "1")

    if args.mode == "all":
        from mbdsdr_ai.decode_router import supported_modes
        modes = supported_modes()
    else:
        modes = [args.mode]

    files = [args.file] if args.file else discover_files(args.dir)
    if not files:
        print("未发现可处理的捕获文件（.wav/.cf32/.cfile）", file=sys.stderr)
        return 2

    per_mode = [build_report(files, m, args.fs) for m in modes]
    if len(per_mode) == 1:
        report = per_mode[0]
    else:
        report = {
            "generated_at": per_mode[0]["generated_at"],
            "mode": "all",
            "reports": per_mode,
            "summary": _summarize([r for rep in per_mode for r in rep["results"]]),
        }

    if args.audit_dir:
        _audit(report, args.audit_dir)
    if args.out:
        print(write_report(report, args.out))
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
