# SPDX-License-Identifier: MIT
"""一键复跑 README §4「离线复验」里的六条命令，如实报告每条的退出码。

设计红线：
* 先把 HOME/USERPROFILE/APPDATA/LOCALAPPDATA 重定向到临时目录再启动子进程，
  不碰用户真实配置；不联网、不发射、不打开真机。
* 真机自检返回 1 是**预期的诚实失败**，不是脚本坏了；本脚本不把它粉饰成通过。
* 只报真实 stdout/rc，不伪造数字、不写死「N passed」。

用法::

    python3 scripts/repro_readme_offline.py            # 人读汇总
    python3 scripts/repro_readme_offline.py --json    # 结构化结果

退出码 0 = 六条都按预期执行完（真机项按预期失败也算）；1 = 出现超时/崩溃/意外退出码。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable

#: (名称, 命令, 期望退出码集合, 超时秒)
CASES = (
    ("hw_selfcheck --json",
     [PY, "tools/hw_selfcheck/selfcheck.py", "--json", "--timeout", "3"], {1}, 180),
    ("onboard --step detect --json",
     [PY, "tools/onboarding/onboard.py", "--json", "--step", "detect"], {1}, 180),
    ("离线工具自测",
     [PY, "-m", "pytest", "tools/hw_selfcheck/test_selfcheck.py",
      "tools/onboarding/test_onboarding.py", "-q", "--tb=line",
      "-p", "no:cacheprovider"], {0}, 300),
    ("Eb/N0 仿真实验",
     [PY, "experiments/exp_ebno_decode.py", "--trials", "10", "--seed", "20261001"],
     {0}, 300),
    ("aigui_demo 静态页",
     [PY, "-m", "mbdsdr_ai.aigui_demo", "--out", "__OUT__"], {0}, 180),
    ("desktop 导入自检",
     [PY, "desktop/main.py", "--import-probe", "desktop.main_window"], {0}, 180),
)


def run() -> dict:
    results = []
    with tempfile.TemporaryDirectory(prefix="mbdsdr-readme-repro-") as home:
        env = dict(os.environ)
        for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
            env[key] = home
        env.update({
            "QT_QPA_PLATFORM": "offscreen",
            "MBDSDR_NO_HW": "1",
            "MBDSDR_OFFLINE": "1",
            "MBDSDR_NULL_AUDIO": "1",
            "MBDSDR_TEST_LOCK": "0",
            "PYTHONUNBUFFERED": "1",
        })
        for name, cmd, expected, timeout in CASES:
            real = [str(Path(home) / "demo.html") if a == "__OUT__" else a for a in cmd]
            rec = {"name": name, "cmd": " ".join(real), "expected_rc": sorted(expected)}
            try:
                p = subprocess.run(real, cwd=str(ROOT), env=env, capture_output=True,
                                   text=True, encoding="utf-8", errors="replace",
                                   timeout=timeout)
                rec["rc"] = p.returncode
                rec["as_expected"] = p.returncode in expected
                rec["stdout_tail"] = p.stdout[-500:]
                rec["stderr_tail"] = p.stderr[-300:]
            except subprocess.TimeoutExpired:
                rec["rc"] = "TIMEOUT"
                rec["as_expected"] = False
                rec["stdout_tail"] = ""
                rec["stderr_tail"] = f"超过 {timeout}s"
            results.append(rec)
    return {"ok": all(r["as_expected"] for r in results), "results": results}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="复跑 README §4 的离线命令")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args(argv)
    data = run()
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        for r in data["results"]:
            mark = "OK" if r["as_expected"] else "UNEXPECTED"
            print(f"[{mark:10s}] {r['name']:32s} rc={r['rc']} "
                  f"(预期 {r['expected_rc']})")
        print("\n" + ("README_OFFLINE_REPRO_OK" if data["ok"]
                      else "README_OFFLINE_REPRO_UNEXPECTED"))
        print("注：真机自检 rc=1 是设计行为（无设备时诚实失败），不是失败。")
    return 0 if data["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
