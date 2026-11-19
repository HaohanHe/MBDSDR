# SPDX-License-Identifier: MIT
"""M1 三条演示脚本（``scripts/demo_chain.py``）的行为测试。

全部用 ``subprocess`` 真跑脚本，不 mock 脚本内部：
* 无硬件档退出 0，8 步齐全并含「无 SDR」降级说明；
* 断网档退出 0，联网步骤标「离线：显式拒绝/本地可用」，不出现「已联网」；
* 有硬件档在无设备环境退出 1 并说明缺设备；
* 三档输出都不出现「已对准」「假数据」「示例过境时间」；
* TLE 缓存存在时能真算出一次过境；无缓存时明确说「无 TLE」；
* 每档 8 步与预计时长 ≤12 分钟。

测试隔离：``HOME`` / ``USERPROFILE`` / ``MBDSDR_TLE_CACHE_DIR`` 指到临时
目录，并清掉父进程的 ``MBDSDR_*`` 与模型 key，不联网、不碰用户配置。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "demo_chain.py"
FORBIDDEN = ("已对准", "假数据", "示例过境时间")
#: 测试专用 TLE（与 tests/fixtures/tle_samples.py 的 ISS 同源）。
ISS_TLE = (
    "ISS (ZARYA)",
    "1 25544U 98067A   26250.50000000  .00000050  00000-0  10000-3 0  9996",
    "2 25544  51.6400 100.0000 0006700  90.0000 270.0000 15.50000000400000",
)


def _env(home: Path, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("MBDSDR_")}
    for key in ("OPENAI_API_KEY", "MBDSDR_API_KEY", "OPENAI_BASE_URL"):
        env.pop(key, None)
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["MBDSDR_TLE_CACHE_DIR"] = str(home / "tle_cache")
    if extra:
        env.update(extra)
    return env


def _run(home: Path, mode: str,
         extra: Optional[Dict[str, str]] = None) -> Tuple[subprocess.CompletedProcess, float]:
    started = time.monotonic()
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--mode", mode],
        cwd=str(ROOT), env=_env(home, extra), capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=25,
    )
    return proc, time.monotonic() - started


def _out(proc: subprocess.CompletedProcess) -> str:
    return (proc.stdout or "") + "\n" + (proc.stderr or "")


def _write_station_config(home: Path) -> None:
    cfg_dir = home / ".mbdsdr"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.json").write_text(json.dumps({
        "ground_station_lat": 0.0,
        "ground_station_lon": 0.0,
        "ground_station_alt_m": 0.0,
    }, ensure_ascii=False), encoding="utf-8")


def _write_iss_cache(home: Path) -> None:
    cache = home / "tle_cache"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "25544.tle").write_text("\n".join(ISS_TLE), encoding="utf-8")


def test_nohw_exit_zero_and_eight_steps(tmp_path):
    proc, _ = _run(tmp_path, "nohw")
    out = _out(proc)
    assert proc.returncode == 0, out
    for i in range(1, 9):
        assert f"[{i}/8]" in out
    assert "无 SDR" in out
    assert "退出码 0" in out


def test_offline_explicit_refusal_and_local_available(tmp_path):
    proc, _ = _run(tmp_path, "offline")
    out = _out(proc)
    assert proc.returncode == 0, out
    assert "离线：显式拒绝" in out
    assert "本地可用" in out
    assert "已联网" not in out


def test_hw_without_device_exits_nonzero_and_names_missing(tmp_path):
    proc, _ = _run(tmp_path, "hw", {"MBDSDR_NO_HW": "1"})
    out = _out(proc)
    assert proc.returncode != 0, out
    assert "缺设备" in out
    assert "MBDSDR_NO_HW" in out or "未枚举到" in out


def test_all_modes_avoid_forbidden_phrases(tmp_path):
    outputs = []
    for mode, extra in (("nohw", None), ("offline", None),
                        ("hw", {"MBDSDR_NO_HW": "1"})):
        proc, _ = _run(tmp_path, mode, extra)
        outputs.append(_out(proc))
    for out in outputs:
        for phrase in FORBIDDEN:
            assert phrase not in out


def test_cached_tle_computes_one_pass(tmp_path):
    _write_station_config(tmp_path)
    _write_iss_cache(tmp_path)
    proc, _ = _run(tmp_path, "nohw")
    out = _out(proc)
    assert proc.returncode == 0, out
    assert "无 TLE" not in out
    assert "过境实算" in out
    assert "TLE 实读" in out


def test_missing_tle_says_no_tle(tmp_path):
    _write_station_config(tmp_path)
    proc, _ = _run(tmp_path, "nohw")
    out = _out(proc)
    assert proc.returncode == 0, out
    assert "无 TLE" in out
    assert "过境实算" not in out


def test_each_mode_budget_under_twelve_minutes(tmp_path):
    for mode, extra in (("nohw", None), ("offline", None),
                        ("hw", {"MBDSDR_NO_HW": "1"})):
        proc, elapsed = _run(tmp_path, mode, extra)
        out = _out(proc)
        match = re.search(r"预计总时长：([\d.]+) 分钟（(\d+) 步", out)
        assert match, out
        minutes = float(match.group(1))
        steps = int(match.group(2))
        assert steps == 8
        assert minutes <= 12.0
        if mode in ("nohw", "offline"):
            for i in range(1, 9):
                assert f"[{i}/8]" in out
        assert elapsed < 25.0
