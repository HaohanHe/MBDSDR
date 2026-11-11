#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""P0-13 独立复现脚本：窄宽闸门是否仍漏测 ``topBarDetail``（把 24 px 记成 6 px）。

roadmap 断言（原文）：
    「窄宽闸门漏测 ``topBarDetail``，24 px 越界被记成 6 px」
    证据等级：实测（同一窗口闸门输出 6 px，实际 24 px，低估 18 px）。

做法：直接跑产品自带的闸门 ``scripts/ui_narrow_breakdown.py --json``
（它会构造真实 MainWindow，按四个可见性状态分别量「声明最小宽」档的滑杆列），
读回 JSON：
  * ``legacy_gate``：旧闸门口径（只测专注态 + 手工 hide/show），应得 +6 px
  * ``default``    ：真实「读取条 + 工具栏可见」形态，应得 +24 px
两者相差 18 px 就是本条的核心。

退出码（三态）：
    0  复现成功   —— 闸门仍只报 6 px（没有 default 状态，或 default 也报 6）
    1  未复现     —— 闸门已同时报出 legacy 6 / default 24
    2  无法判定   —— 字体库为空（offscreen）、窗口被夹、或闸门运行失败

反证对照：闸门自身保留 ``legacy_gate`` 状态作 6 px 对照；本脚本要求该值
必须仍是 6（否则说明 6 px 的口径也变了，无法做差）。

用法：
    python scripts/repro/2026-11-11-p0-13-narrow-gate-topbar-detail.py
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.environ.get("MBDSDR_REPRO_ROOT") or os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TMP_ROOT = r"D:\mbdsdr\output\tmp"


def _hr(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def _extract_json(text: str):
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except ValueError:
        return None


def main() -> int:
    gate = os.path.join(ROOT, "scripts", "ui_narrow_breakdown.py")
    print(f"repo   : {ROOT}")
    print(f"python : {sys.version.split()[0]}")
    print(f"闸门   : {gate}")
    if not os.path.exists(gate):
        print("[无法判定] 闸门脚本不存在。")
        return 2

    profile = tempfile.mkdtemp(prefix="p0-13-home-", dir=TMP_ROOT)
    env = dict(os.environ)
    for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
        env[key] = profile
    # 闸门默认 offscreen，但 offscreen 下字体库为 0（闸门自己会退出码 2）。
    # 这里强制 windows 平台插件；无桌面会话时闸门会以 2 退出。
    env["QT_QPA_PLATFORM"] = "windows"
    env.setdefault("MBDSDR_OFFLINE", "1")
    env.setdefault("MBDSDR_NO_HW", "1")
    env.setdefault("MBDSDR_NULL_AUDIO", "1")
    env["PYTHONIOENCODING"] = "utf-8"

    _hr("实测：运行产品闸门（windows 平台插件，--json）")
    try:
        proc = subprocess.run(
            [sys.executable, gate, "--json"],
            cwd=ROOT, env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=240)
    except Exception as exc:  # noqa: BLE001
        print(f"[无法判定] 闸门启动失败：{exc!r}")
        return 2

    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    print(f"  退出码 : {proc.returncode}")
    if err:
        print("  stderr : " + err.splitlines()[-1][:300])
    if proc.returncode == 2:
        print("[无法判定] 闸门自报环境不满足（字体库为空/平台插件缺失）。")
        return 2
    if proc.returncode != 0:
        print(f"[无法判定] 闸门非预期退出（{proc.returncode}）。")
        print("  stdout 末行：" + (out.splitlines()[-1][:300] if out else "（空）"))
        return 2

    data = _extract_json(out)
    if not isinstance(data, dict):
        print("[无法判定] 闸门输出不是可解析 JSON。")
        return 2

    states = data.get("states")
    if not isinstance(states, dict) or "default" not in states:
        print("  闸门输出无 states/default —— 仍是旧版单状态口径。")
        legacy = None
        m = re.search(r"\+(\d+)\s*px", out)
        if m:
            legacy = int(m.group(1))
        print(f"  旧版可见越界：{legacy} px")
        print("\n结论：当前实现复现 P0-13（闸门未测 topBarDetail）: True")
        print("[复现] 闸门没有 default 状态，无法量到 24 px。")
        return 0

    def _overflow(state_name: str):
        try:
            return int(states[state_name]["tiers"]["declared"]["slider_col"]
                       .get("overflow_px", 0))
        except (KeyError, TypeError, ValueError):
            return None

    legacy = _overflow("legacy_gate")
    default = _overflow("default")
    detail = _overflow("detail_open")
    print(f"  legacy_gate（旧口径）: +{legacy} px")
    print(f"  detail_open（只展开读数条）: +{detail} px")
    print(f"  default（读数条+工具栏）: +{default} px")
    if legacy is None or default is None:
        print("[无法判定] 闸门 JSON 缺少 legacy_gate/default 的滑杆列读数。")
        return 2

    if legacy != 6:
        # 旧口径本身已经不越界：通常是滑杆被移出右栏（后续布局改动），
        # 此时 6→24 的差值样本在本快照不可再生成，但「闸门漏测」已无对象。
        all_zero = all(_overflow(s) == 0 for s in states)
        print(f"\n  旧口径不再是 6 px（实得 {legacy}）；全部状态越界为 0：{all_zero}")
        if all_zero:
            print("结论：当前实现复现 P0-13 : False")
            print("[未复现] 闸门已按状态矩阵测量，且三根滑杆所在列已无纵向越界"
                  "（24 px 这个被测对象本身已消失）。6→24 的历史差值需在"
                  "滑杆仍在右栏的旧快照上复跑，见文档对照组。")
            return 1
        print("[无法判定] 旧口径与真实口径都未给出 6 px 对照，差值口径失效。")
        return 2

    reproduced = default <= legacy
    print(f"\n结论：当前实现复现 P0-13（default 仍被量成 6 px）: {reproduced}")
    if reproduced:
        print("[复现] 闸门仍未把 topBarDetail 纳入，24 px 被低估。")
        return 0
    print(f"[未复现] 闸门已量到真实 default +{default} px，"
          f"与旧口径 +{legacy} px 相差 {default - legacy} px。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
