#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成物对账：逐个跑生成器，比对「当前树里的生成物」是否与之一致。

背景
----
一批 CI 红点的共同根因是「产品变了、生成物没重跑」。靠人记得去跑就一定会
再发生，所以把**全部可离线重跑的文档/表格/清单生成物**登记成一张表，
统一入口逐条对账：不一致即红，并直接给出「跑这条命令重生成」。

判据
----
不是检查内容对不对，而是**重跑生成器、比对产物**：

* ``check`` 形态：生成器自带 ``--check`` / 校验模式（重算后只比对、不写盘）；
* ``snapshot`` 形态：生成器只能整篇写盘——先快照原字节，跑 ``--write``，
  比对后**无条件还原**（比较按 LF 归一，行尾转换不算漂移）；
* ``mirror`` 形态：产物由模块镜像而来、没有独立生成脚本——按模块重建表行，
  逐行比对（当前只有 docs/error-catalog.md）。

**为什么不在 pre-commit**：钩子变慢会诱发 ``--no-verify``（既有裁定）。
本脚本挂在发版前（``scripts/release_check.py`` 的 ``gen_reconcile`` 步骤），
以及「改动了相关源文件时」手动跑 ``--changed``。

范围
----
只收「由仓库内脚本从源码派生的文档/表格/清单」。不含代码生成、前端构建
产物与证据快照：前者另有守卫，后者不可确定性复现——逐条豁免写在 ``EXEMPT``
里并写明理由，不假装覆盖。

用法::

    python scripts/check_generated_artifacts.py              # 检查全部
    python scripts/check_generated_artifacts.py --list       # 登记表（含豁免）
    python scripts/check_generated_artifacts.py --changed    # 只查输入有改动的
    python scripts/check_generated_artifacts.py --only shortcuts
    python scripts/check_generated_artifacts.py --json

退出码：0 全部一致；1 有不一致/生成器跑不起来；2 用法错误。
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable


@dataclass(frozen=True)
class Artifact:
    """一条生成物登记：谁生成、怎么查、什么条件要重跑、谁负责。"""

    id: str
    path: str
    generator: Sequence[str]
    inputs: Sequence[str]
    owner: str
    trigger: str
    #: check 形态：自带校验模式，退出码即结论（重算后比对，不写盘）
    check: Optional[Sequence[str]] = None
    #: snapshot 形态：只能整篇写盘，先快照再重跑、比对后还原
    rewrite: Optional[Sequence[str]] = None
    #: mirror 形态：由模块镜像而来（见 _check_mirror）
    mirror: Optional[str] = None

    @property
    def kind(self) -> str:
        if self.check is not None:
            return "check"
        if self.rewrite is not None:
            return "snapshot"
        return "mirror"

    def regen_command(self) -> str:
        """给人复制的重生成命令（解释器写 ``python``，不暴露本机绝对路径）。"""
        return _display_cmd(self.generator)

    def check_command(self) -> str:
        if self.check is not None:
            return _display_cmd(self.check)
        return self.regen_command()


def _display_cmd(cmd: Sequence[str]) -> str:
    return " ".join("python" if p == PY else p for p in cmd)


ARTIFACTS: tuple[Artifact, ...] = (
    Artifact(
        id="shortcuts",
        path="docs/shortcuts.md",
        generator=[PY, "scripts/gen_shortcut_table.py"],
        check=[PY, "scripts/gen_shortcut_table.py", "--check"],
        inputs=("desktop/shortcuts.py",),
        owner="UI 线（改快捷键注册表后）；发版闸门",
        trigger="desktop/shortcuts.py 的 SHORTCUTS 有增删改",
    ),
    Artifact(
        id="mcp_tools",
        path="docs/mcp_tools.md",
        generator=[PY, "scripts/gen_mcp_tools_doc.py"],
        check=[PY, "scripts/gen_mcp_tools_doc.py", "--check"],
        inputs=(
            "mbdsdr_ai/core/mcp_server.py",
            "mbdsdr_ai/tool_registry.py",
            "mbdsdr_ai/sdr_tools.py",
            "mbdsdr_ai/fengyun_sat.py",
            "mbdsdr_ai/orbit_determination.py",
            "mbdsdr_ai/decoders.py",
        ),
        owner="AI 工具面线；发版闸门",
        trigger="MCPToolSet.list_tools() 的注册描述/参数/工具数量变化",
    ),
    Artifact(
        id="settings_inventory",
        path="docs/settings-inventory.md",
        generator=[PY, "scripts/settings_inventory.py", "--write"],
        check=[PY, "scripts/settings_inventory.py", "--check"],
        inputs=("desktop/config_schema.py", "desktop/settings.py", "desktop/panels"),
        owner="设置线；发版闸门",
        trigger="config_schema.ALL_ITEMS 或设置面板入口变化",
    ),
    Artifact(
        id="incident_card_anchors",
        path="docs/COMMIT-INCIDENT-CARD.md",
        generator=[PY, "scripts/check_card_links.py", "--fix-anchors"],
        check=[PY, "scripts/check_card_links.py"],
        inputs=("docs/COORDINATION.md", "docs/COMMIT-INCIDENT-CARD.md"),
        owner="协调/账本线；发版闸门",
        trigger="docs/COORDINATION.md 的 §x 标题行号移动",
    ),
    Artifact(
        id="decoder_matrix",
        path="docs/learn/decoder-capability-matrix.md",
        generator=[PY, "scripts/decoder_capability_report.py", "--write"],
        rewrite=[PY, "scripts/decoder_capability_report.py", "--write"],
        inputs=(
            "mbdsdr_ai/decoders.py",
            "mbdsdr_ai/decode_router.py",
            "scripts/decoder_capability_report.py",
        ),
        owner="解码线；发版闸门",
        trigger="解码器能力/证据/重跑命令变化",
    ),
    Artifact(
        id="backend_capability",
        path="docs/backend-capability-matrix.md",
        generator=[PY, "scripts/gen_backend_capability_matrix.py", "--write"],
        check=[PY, "scripts/gen_backend_capability_matrix.py", "--check"],
        inputs=(
            "mbdsdr_ai/sdr_backend.py",
            "mbdsdr_ai/rtltcp_client.py",
            "mbdsdr_ai/spyserver_client.py",
            "scripts/gen_backend_capability_matrix.py",
        ),
        owner="SDR 后端线；发版闸门",
        trigger="任一后端类的 supports_gain/agc/ppm/offset_tuning 判据、"
                "device.sample_rate_range/max_gain 或后端类清单变化",
    ),
    Artifact(
        id="error_catalog",
        path="docs/error-catalog.md",
        generator=[PY, "scripts/check_generated_artifacts.py", "--write-mirror",
                   "error_catalog"],
        mirror="error_catalog",
        inputs=("mbdsdr_ai/error_catalog.py",),
        owner="错误目录线；发版闸门",
        trigger="mbdsdr_ai/error_catalog.py 的 ERRORS 行变化",
    ),
)


@dataclass(frozen=True)
class Exemption:
    """登记在册、但**不**进对账的生成物；理由必须可核。"""

    path: str
    generator: str
    reason: str


EXEMPT: tuple[Exemption, ...] = (
    Exemption(
        "docs/interaction-audit.md",
        "scripts/ui_interaction_audit.py",
        "运行时审计报告：要拉起真实 MainWindow 并逐个触发动作（有副作用、耗时长），"
        "产物随运行环境排序变化；它的退出码只覆盖死按钮，不覆盖文档新鲜度。"
        "缺口登记在 docs/data-inventory.md §3c。",
    ),
    Exemption(
        "docs/constellations.md",
        "scripts/fetch_constellation_names_cc0.py",
        "上游星文化快照，需联网或本地 reference 快照；不能离线确定性重跑，"
        "只能靠产物内的源 SHA 钉。",
    ),
    Exemption(
        "docs/perf-evidence/*.json",
        "scripts/perf_probe*.py",
        "证据快照（CPU/Qt/负载条件写在 conditions 里），不同次运行数值本就不同；"
        "既有裁定：不加逐字节守卫，否则对合法波动报警。",
    ),
    Exemption(
        "docs/source-pin-ratchet-baseline.json",
        "scripts/audit_source_text_pins.py",
        "棘轮基线：由更新基线的动作产生，更新即基线；由 "
        "tests/test_source_pin_ratchet.py 守「只许降不许升」，不是逐字节对账。",
    ),
    Exemption(
        "docs/a11y/2026-11-12-theme-dead-keys.md",
        "scripts/report_token_dual_values.py",
        "时点报告（文件名带日期）：报告的是当次实测，不是随源码演进的活产物。",
    ),
    Exemption(
        "web/product/docs/wiki/**、08、README.en/ja",
        "web/product/build/gen_docs.py",
        "前端文档构建链产物，需 node 构建；四处接线仍在途，接手条件见 "
        "docs/data-inventory.md §3c（挂起项）。",
    ),
)


@dataclass
class Result:
    artifact: Artifact
    status: str  # ok / drift / error
    detail: str = ""
    output: str = ""
    #: 单次检查实际耗时（秒）。release_check 的生成物对账段按它单报耗时。
    seconds: float = 0.0


def _env() -> dict:
    env = dict(os.environ)
    env.setdefault("MBDSDR_OFFLINE", "1")
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    return env


def _run(cmd: Sequence[str], timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(list(cmd), cwd=ROOT, capture_output=True, text=True,
                          timeout=timeout, env=_env(), errors="replace")


def _norm(text: str) -> str:
    return text.replace("\r\n", "\n")


def _first_diff(old: str, new: str, label_old: str, label_new: str) -> str:
    delta = list(difflib.unified_diff(
        _norm(old).splitlines(), _norm(new).splitlines(),
        label_old, label_new, lineterm="", n=1))
    if not delta:
        return "（文本归一后一致，仅行尾或空白差异）"
    head = "\n".join(delta[:16])
    if len(delta) > 16:
        head += f"\n... 另有 {len(delta) - 16} 行差异"
    return head


def _check_command(art: Artifact) -> Result:
    proc = _run(art.check)
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode == 0:
        return Result(art, "ok", out.strip().splitlines()[-1][:160] if out.strip() else "", out)
    return Result(art, "drift", f"{art.check_command()} 退出 {proc.returncode}", out)


def _check_snapshot(art: Artifact) -> Result:
    target = ROOT / art.path
    if not target.is_file():
        return Result(art, "error", f"生成物不存在：{art.path}")
    original = target.read_bytes()
    try:
        proc = _run(art.rewrite)
        out = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode != 0:
            return Result(art, "error", f"生成器退出 {proc.returncode}", out)
        new = target.read_bytes()
    finally:
        target.write_bytes(original)
    if _norm(new.decode("utf-8")) == _norm(original.decode("utf-8")):
        return Result(art, "ok", "重跑生成器后逐字节一致（按 LF 归一）", out)
    diff = _first_diff(original.decode("utf-8"), new.decode("utf-8"),
                       art.path + "（当前树）", art.path + "（重跑）")
    return Result(art, "drift", "当前树与生成器重跑结果不一致", diff)


_MIRROR_ROW = re.compile(r"^\| `([A-Za-z][A-Za-z0-9_]*)` \|")


def _catalog_rows() -> dict[str, str]:
    """按 ERRORS 重建「§3 错误码表」的每一行（模块是唯一事实源）。"""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from mbdsdr_ai.error_catalog import ERRORS

    return {
        code: (f"| `{code}` | {spec.layer} | {spec.meaning} | {spec.message} | "
               f"{spec.suggestion} | {'是' if spec.retryable else '否'} |")
        for code, spec in ERRORS.items()
    }


def _catalog_sections(text: str) -> Optional[tuple[List[str], int, int]]:
    """切出 §3 表体：返回 (行列表, 起始行下标, 结束行下标)；找不到返回 None。"""
    lines = _norm(text).splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if ln.startswith("## 3. 错误码表"))
        end = next(i for i, ln in enumerate(lines) if ln.startswith("## 4. 守卫测试"))
    except StopIteration:
        return None
    return lines, start, end


def _check_error_catalog(art: Artifact) -> Result:
    """docs/error-catalog.md 是 error_catalog.ERRORS 的镜像，逐行对。"""
    expected = _catalog_rows()
    text = (ROOT / art.path).read_text(encoding="utf-8")
    sections = _catalog_sections(text)
    if sections is None:
        return Result(art, "error", "找不到「## 3. 错误码表」与「## 4. 守卫测试」之间的表")
    lines, start, end = sections

    actual = {}
    for line in lines[start:end]:
        m = _MIRROR_ROW.match(line.strip())
        if m:
            actual[m.group(1)] = line.strip()

    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    drifted = sorted(c for c in set(expected) & set(actual)
                     if expected[c] != actual[c])
    if not (missing or extra or drifted):
        return Result(art, "ok", f"{len(expected)} 行与 ERRORS 一致")
    parts = []
    if missing:
        parts.append(f"文档缺行：{', '.join(missing)}")
    if extra:
        parts.append(f"文档多出：{', '.join(extra)}")
    if drifted:
        parts.append("行内容不一致：" + ", ".join(drifted))
        c = drifted[0]
        detail = _first_diff(actual[c], expected[c], "文档", "模块")
        return Result(art, "drift", "；".join(parts), detail)
    return Result(art, "drift", "；".join(parts))


def write_mirror(artifact_id: str) -> int:
    """把镜像型生成物按模块重建（当前只支持 error_catalog）。"""
    art = next((a for a in ARTIFACTS if a.id == artifact_id), None)
    if art is None or art.mirror != "error_catalog":
        print(f"不支持 write-mirror：{artifact_id}", file=sys.stderr)
        return 2
    path = ROOT / art.path
    text = path.read_text(encoding="utf-8")
    sections = _catalog_sections(text)
    if sections is None:
        print("找不到 §3 表，拒绝写入", file=sys.stderr)
        return 1
    lines, start, end = sections
    expected = _catalog_rows()
    out: List[str] = []
    written = set()
    for i, line in enumerate(lines):
        m = _MIRROR_ROW.match(line.strip())
        if start < i < end and m and m.group(1) in expected:
            out.append(expected[m.group(1)])
            written.add(m.group(1))
            continue
        out.append(line)
    # 文档里缺的码追加到表尾（§4 之前最后一个非空行之后）
    missing = [c for c in expected if c not in written]
    if missing:
        insert_at = max(i for i in range(start + 1, end) if out[i].strip())
        out[insert_at + 1:insert_at + 1] = [expected[c] for c in missing]
    new_text = "\n".join(out) + ("\n" if text.endswith(("\n", "\r\n")) else "")
    path.write_bytes(new_text.replace("\n", "\r\n" if "\r\n" in text else "\n")
                     .encode("utf-8"))
    print(f"已按 ERRORS 重写 {art.path}：更新 {len(written)} 行，"
          f"补 {len(missing)} 行")
    return 0


def _check(art: Artifact) -> Result:
    try:
        if art.kind == "check":
            return _check_command(art)
        if art.kind == "snapshot":
            return _check_snapshot(art)
        if art.mirror == "error_catalog":
            return _check_error_catalog(art)
        return Result(art, "error", f"未知的检查形态：{art.kind}")
    except subprocess.TimeoutExpired:
        return Result(art, "error", "生成器超时")
    except Exception as exc:  # noqa: BLE001 - 跑不起来也必须报红，不能当绿
        return Result(art, "error", f"{type(exc).__name__}: {exc}")


def _changed_artifacts(base: str) -> Optional[set[str]]:
    """BASE 以来改动过的路径 → 需要重查的生成物 id；git 不可用时返回 None。"""
    proc = subprocess.run(["git", "diff", "--name-only", base],
                          cwd=ROOT, capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    changed = {p.strip().replace("\\", "/") for p in proc.stdout.splitlines()
               if p.strip()}
    picked = set()
    for art in ARTIFACTS:
        if art.path in changed:
            picked.add(art.id)
            continue
        for path in art.inputs:
            if any(c == path or c.startswith(path.rstrip("/") + "/")
                   or path.startswith(c + "/") for c in changed):
                picked.add(art.id)
                break
    return picked


def _print_inventory() -> None:
    print(f"生成物对账登记表（{len(ARTIFACTS)} 项受查，{len(EXEMPT)} 项豁免）\n")
    for art in ARTIFACTS:
        print(f"[{art.kind}] {art.path}")
        print(f"    生成器：{art.regen_command()}")
        print(f"    触发：{art.trigger}")
        print(f"    负责：{art.owner}")
    print("\n豁免（不进对账，理由可核）：")
    for ex in EXEMPT:
        print(f"    {ex.path}  ←  {ex.generator}")
        print(f"        {ex.reason}")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="生成物对账：重跑生成器并比对产物")
    ap.add_argument("--list", action="store_true", help="打印登记表与豁免清单")
    ap.add_argument("--changed", nargs="?", const="HEAD", default=None,
                    metavar="BASE", help="只查输入自 BASE（默认 HEAD）以来有改动的生成物")
    ap.add_argument("--only", action="append", default=[], help="只查指定 id（可重复）")
    ap.add_argument("--write-mirror", metavar="ID",
                    help="按模块重建镜像型生成物（当前支持 error_catalog）")
    ap.add_argument("--json", action="store_true", help="输出机器可读结果")
    args = ap.parse_args(argv)

    if args.list:
        _print_inventory()
        return 0

    if args.write_mirror:
        return write_mirror(args.write_mirror)

    wanted: Iterable[Artifact] = ARTIFACTS
    if args.only:
        known = {a.id for a in ARTIFACTS}
        unknown = [i for i in args.only if i not in known]
        if unknown:
            print(f"未知生成物 id：{unknown}；可用：{sorted(known)}", file=sys.stderr)
            return 2
        wanted = [a for a in ARTIFACTS if a.id in set(args.only)]
    elif args.changed is not None:
        picked = _changed_artifacts(args.changed)
        if picked is None:
            print(f"git diff 失败（BASE={args.changed}），改为检查全部", file=sys.stderr)
        else:
            wanted = [a for a in ARTIFACTS if a.id in picked]
            if not picked:
                print(f"自 {args.changed} 以来没有生成物或输入发生改动，跳过。")
                return 0

    results = []
    for a in wanted:
        t0 = time.perf_counter()
        r = _check(a)
        r.seconds = time.perf_counter() - t0
        results.append(r)
    red = [r for r in results if r.status != "ok"]

    if args.json:
        print(json.dumps({
            "checked": len(results),
            "out_of_sync": sum(1 for r in results if r.status == "drift"),
            "broken": sum(1 for r in results if r.status == "error"),
            "exempt": len(EXEMPT),
            "results": [{"id": r.artifact.id, "path": r.artifact.path,
                         "status": r.status, "detail": r.detail,
                         "seconds": round(r.seconds, 3)}
                        for r in results],
        }, ensure_ascii=False, indent=2))
        return 1 if red else 0

    for r in results:
        if r.status == "ok":
            print(f"[ OK ] {r.artifact.path}  {r.detail}")
            continue
        print(f"[FAIL] {r.artifact.path}  （{r.artifact.id}）")
        print(f"    {r.detail}")
        if r.output.strip():
            for line in r.output.strip().splitlines()[:12]:
                print(f"    | {line}")
        print(f"    重生成：{r.artifact.regen_command()}")
        print(f"    触发条件：{r.artifact.trigger}；负责：{r.artifact.owner}")

    print("-" * 72)
    print(f"汇总：受查 {len(results)}；一致 {len(results) - len(red)}；"
          f"不一致 {sum(1 for r in results if r.status == 'drift')}；"
          f"生成器失败 {sum(1 for r in results if r.status == 'error')}；"
          f"豁免 {len(EXEMPT)}")
    return 1 if red else 0


if __name__ == "__main__":
    raise SystemExit(main())
