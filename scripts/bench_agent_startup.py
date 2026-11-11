# SPDX-License-Identifier: MIT
"""Agent 后端启动性能基线（无硬件 / 隔离 profile / 可复跑）。

量的是 ``MBDSDRAgent()`` 构造：496 个工具注册、SDR 后端发现、历史变更与
学习记录加载、git 可用性探测都压在这条启动链上。桌面端每次构造 AIPanel
都会走一遍，测试里每个 module fixture 也会走。

复跑（在仓库根）：

    python scripts/bench_agent_startup.py --repeat 5
    python scripts/bench_agent_startup.py --repeat 5 --profile
    python scripts/bench_agent_startup.py --repeat 5 --json D:/mbdsdr/output/agent_startup.json

脚本**先把 HOME/USERPROFILE/APPDATA/LOCALAPPDATA 重定向到临时目录**，再
import mbdsdr_ai：历史记录与配置全部落在临时目录，绝不碰用户真实配置
（2026-11-11 实测 dry-call 曾写脏 ~/.mbdsdr/bookmarks.json）。

输出里的 ``tools_fingerprint`` 是全部注册工具的 (write, category,
available, parallel_safe) 摘要哈希，优化前后必须逐字节一致——性能改动
不许顺手改语义。
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def seed_history(home: str, changes: int, experiences: int) -> dict:
    """在隔离目录里铺一份**合成**历史，复现真实用户积累后的启动 IO。

    全是中性合成路径/文本，零个人信息。没有这一步，干净临时目录会让
    file_changes / learning 的历史加载热点完全测不到。
    """
    root = Path(home) / ".mbdsdr"
    day = "2026-11-10"
    chg_dir = root / "file_changes" / day
    exp_dir = root / "learning" / "experiences" / day
    pat_dir = root / "learning" / "patterns"
    for d in (chg_dir, exp_dir, pat_dir):
        d.mkdir(parents=True, exist_ok=True)
    for i in range(changes):
        (chg_dir / f"chg_{i:05d}.json").write_text(json.dumps({
            "change_id": f"chg_{i:05d}",
            "file_path": f"bench/module_{i % 40:02d}.py",
            "change_type": "modify",
            "hash_before": f"{i:064x}",
            "hash_after": f"{(i + 1):064x}",
            "reason": "synthetic benchmark record",
            "actor": "bench",
            "timestamp": 1730000000 + i,
            "metadata": {},
            "reverted": False,
            "reverted_to": "",
        }, ensure_ascii=False), encoding="utf-8")
    for i in range(experiences):
        (exp_dir / f"exp_{i:05d}.json").write_text(json.dumps({
            "experience_id": f"exp_{i:05d}",
            "experience_type": "tool_call",
            "question": "synthetic benchmark question",
            "score": 7,
            "feedback": "",
            "timestamp": 1730000000 + i,
            "learned": False,
        }, ensure_ascii=False), encoding="utf-8")
    for i in range(12):
        (pat_dir / f"pat_{i:03d}.json").write_text(json.dumps({
            "pattern_id": f"pat_{i:03d}",
            "pattern_type": "synthetic",
            "description": "benchmark pattern",
            "trigger": "bench",
            "action": "noop",
            "confidence": 0.5,
            "frequency": 1,
            "avg_score": 7.0,
            "created_at": 1730000000 + i,
            "applied": False,
        }, ensure_ascii=False), encoding="utf-8")
    return {"change_files": changes, "experience_files": experiences,
            "pattern_files": 12}


def isolate_profile(history_files: int) -> tuple[str, dict]:
    """把用户配置根目录挪到临时目录，必须在 import mbdsdr_ai 之前调用。"""
    home = tempfile.mkdtemp(prefix="mbdsdr-bench-")
    for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
        os.environ[key] = home
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("MBDSDR_NO_HW", "1")
    os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")
    seeded = seed_history(home, history_files, max(1, history_files // 4))
    return home, seeded


def fingerprint(agent) -> tuple[str, int]:
    """工具注册表语义指纹：优化前后必须完全一致。"""
    tools = agent.tool_registry.tools
    rows = []
    for name in sorted(tools):
        t = tools[name]
        rows.append((
            name,
            bool(t.get("write")),
            str(t.get("category")),
            bool(t.get("available")),
            bool(t.get("parallel_safe")),
        ))
    blob = json.dumps(rows, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest(), len(rows)


def run(repeat: int, profile: bool, history_files: int) -> dict:
    home, seeded = isolate_profile(history_files)
    sys.path.insert(0, str(ROOT))
    t_import = time.perf_counter()
    from mbdsdr_ai import AgentConfig, MBDSDRAgent
    import_ms = (time.perf_counter() - t_import) * 1000.0

    durations: list[float] = []
    fps: list[str] = []
    tool_count = 0
    profiler = None
    if profile:
        import cProfile
        profiler = cProfile.Profile()
        profiler.enable()
    for _ in range(repeat):
        t0 = time.perf_counter()
        agent = MBDSDRAgent(AgentConfig(api_key="bench", model="bench-model"))
        durations.append((time.perf_counter() - t0) * 1000.0)
        fp, tool_count = fingerprint(agent)
        fps.append(fp)
    if profiler is not None:
        profiler.disable()
        import pstats
        buf = io.StringIO()
        pstats.Stats(profiler, stream=buf).sort_stats("cumulative").print_stats(18)
        profile_text = buf.getvalue()
    else:
        profile_text = ""

    assert len(set(fps)) == 1, "同一进程内多次构造的工具注册表指纹不一致"
    cold = durations[0]
    warm = durations[1:] or [cold]
    return {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "python": sys.version.split()[0],
        "repeat": repeat,
        "import_ms": round(import_ms, 2),
        "cold_ms": round(cold, 2),
        "warm_median_ms": round(statistics.median(warm), 2),
        "warm_min_ms": round(min(warm), 2),
        "warm_max_ms": round(max(warm), 2),
        "all_ms": [round(v, 2) for v in durations],
        "tool_count": tool_count,
        "tools_fingerprint": fps[0],
        "history_files": seeded,
        "profile_home": home,
        "profile": profile_text,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repeat", type=int, default=5, help="构造次数（>=2 才有冷热对比）")
    ap.add_argument("--history-files", type=int, default=800,
                    help="合成历史变更条数（另按 1/4 生成学习经验）")
    ap.add_argument("--profile", action="store_true", help="输出 cProfile 累计耗时 top18")
    ap.add_argument("--json", default="", help="把结果写到仓库外 JSON 文件")
    args = ap.parse_args(argv)
    if args.repeat < 2:
        ap.error("--repeat 至少 2")

    result = run(args.repeat, args.profile, args.history_files)
    print(f"history      : {result['history_files']}")
    print(f"import        : {result['import_ms']:.1f} ms")
    print(f"cold construct: {result['cold_ms']:.1f} ms")
    print(f"warm median   : {result['warm_median_ms']:.1f} ms "
          f"(min {result['warm_min_ms']:.1f} / max {result['warm_max_ms']:.1f})")
    print(f"all           : {result['all_ms']}")
    print(f"tools         : {result['tool_count']}  fingerprint {result['tools_fingerprint'][:12]}")
    print(f"isolated home : {result['profile_home']}")
    if args.profile:
        print(result["profile"])
    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        print(f"json          : {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
