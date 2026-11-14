# SPDX-License-Identifier: MIT
"""README 契约钉扎：门面必须全绿，且守卫对故意注入的坏样本必须真的会红。

对应 `scripts/verify_readme.py`。守卫只做静态核对，不执行 README 里的命令、
不联网、不碰硬件，所以这些用例在任何机器上都能确定性跑完。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import verify_readme as vr  # noqa: E402


def _text() -> str:
    return vr.read_readme()


def test_readme_contract_is_green():
    result = vr.run_checks()
    assert all(not issues for issues in result.values()), result


def test_cli_json_is_parseable_and_ok():
    """CLI 的 --json 契约（CI 消费）不能被后续重构悄悄改掉。"""
    import json
    import subprocess

    p = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "verify_readme.py"), "--json"],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
        timeout=60)
    data = json.loads(p.stdout)
    assert p.returncode == 0, data
    assert data["ok"] is True and data["total"] == 0


def test_guard_catches_broken_link():
    broken = _text().replace("](LICENSE)", "](LICENSE-DOES-NOT-EXIST)", 1)
    assert any("链接目标不存在" in x for x in vr.check_links(broken))


def test_guard_catches_bare_filename_without_full_path():
    """曾经把 llm_client.cpp 写成裸名，守卫要求写全路径或仓内唯一。"""
    broken = _text().replace("`cpp/src/ai/llm_client.cpp`", "`llm_client.cpp`", 1)
    assert any("不唯一" in x or "写全路径" in x for x in vr.check_paths(broken))


def test_guard_catches_missing_script():
    broken = _text().replace("python3 experiments/exp_ebno_decode.py",
                             "python3 experiments/no_such_experiment.py", 1)
    assert any("脚本不存在" in x for x in vr.check_commands(broken))


def test_guard_catches_fake_cli_flag():
    """README 写过不存在的 --sim，这类假参数必须被 argparse 静态对拍拦下。"""
    broken = _text().replace("--trials 50", "--not-a-real-flag 50", 1)
    assert any("未注册参数 --not-a-real-flag" in x
               for x in vr.check_commands(broken))


def test_guard_catches_japanese_chinese_mix():
    broken = _text().replace("ソース監査済み", "源码監査済み", 1)
    assert any("简体中文词: 源码" in x for x in vr.check_japanese(broken))


def test_guard_catches_frozen_tool_table():
    broken = _text() + "\n| tune_frequency | 写 |\n"
    assert any("工具清单表" in x for x in vr.check_structure(broken))


def test_guard_catches_version_drift():
    lines = _text().splitlines()
    lines[0] = lines[0].replace("v0.2.0", "v9.9.9")
    assert any("≠ canonical" in x for x in vr.check_version("\n".join(lines)))
