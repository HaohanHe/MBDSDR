# SPDX-License-Identifier: MIT
"""帮助条目「操作路径」核验器。

帮助文案里的路径用「」标出控件/页签名、用 ``→`` 表示层级，例如
``「设置」→「数据与操作」``、``「音频输出 → 输出设备」``。控件改名或面板
搬家后，文案会变成假话，而用户照着做会一直找不到。本脚本把**每一段**
拿去生产源码里找字符串字面量：找不到就报红，并给出条目 key。

「设置」→「X」的 X 只认 ``settings_panel.PANEL_SECTIONS`` 里**真正渲染**的
节名（源码 AST 解析，不 import Qt）；``config_schema.GROUP_ORDER`` 是 schema
侧的数据分组，面板重排后两者不再等价，拿它验文案会假绿。

只读 git 索引/工作区里的源码文本，不启动 Qt、不 import 桌面 UI。

用法：
    python scripts/check_help_paths.py            # 检查，失败退出码 1
    python scripts/check_help_paths.py --markdown # 额外输出 52 条核验表
"""
from __future__ import annotations

import argparse
import ast
import os
import re
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import troubleshooting as ts  # noqa: E402

#: 只查生产代码；tests/ 与 docs/ 里的同名不算「用户能看到」。
#: 必须排除本帮助文案的数据层 ``desktop/troubleshooting.py``：文案本身就在
#: 那里以 Python 字符串字面量存在，不排除的话任何「」词都会自证通过
#: （2026-11-06 反证抓到的自指漏洞）。
_CODE_PATHSPECS = (
    "desktop", "mbdsdr_ai",
    ":(exclude)desktop/troubleshooting.py",
)

_TOKEN_RE = re.compile(r"「([^」]+)」")

_DOC = os.path.join(_ROOT, "docs", "error-code-visibility.md")

#: 用户可见通道的渲染文件 → 渲染点必须包含的字面量。
#: 文档（第三节）里引用的行号必须仍落在这些字面量的行上，否则就是行号漂移。
_RENDER_ANCHORS = {
    "ai_panel.py": ("错误 ", "[错误]", "step.summary"),
    "status_panel.py": ("错误码 ", "DECODE_ERROR_LABELS", "error_code", "is_decode_tool"),
    "main_window.py": ("设备已断开", "_handle_backend_lost", "_on_health_tick"),
}

#: 文档 `file.py:12,34-56` 形式的引用
_CITE_RE = re.compile(
    r"`((?:desktop|mbdsdr_ai)/[A-Za-z0-9_./-]+\.py):"
    r"([0-9]+(?:-[0-9]+)?(?:,[0-9]+(?:-[0-9]+)?)*)`"
)

#: 「操作结果是否真的改变」的机制锚点：文案让用户做的动作，代码里必须有实现。
#: 这些是**机制**，不是期望值；删掉其中任何一条，对应的建议就变成空话。
_MECHANISM_ANCHORS = (
    ("采样率整数倍校验（sample_rate_mismatch 的修法）",
     "mbdsdr_ai/decode_router.py", "abs(fs - sps * step) > 1e-6"),
    ("最短一帧校验（insufficient_samples 的修法）",
     "mbdsdr_ai/decode_router.py", "if actual_n < need_n:"),
    ("解码流水线采样率拒绝",
     "mbdsdr_ai/decode_pipeline.py", "REASON_SAMPLE_RATE_MISMATCH"),
    ("录音目录设置项（disk_full / not_writable 的修法）",
     "desktop/config_schema.py", 'key="record_dir"'),
    ("静噪默认值设置项（empty_signal 的修法）",
     "desktop/config_schema.py", 'key="squelch_default_db"'),
    ("调谐步进设置项（sample_rate_not_supported 的修法）",
     "desktop/config_schema.py", 'key="tuning_step_hz"'),
)


def segments_of(text: str) -> list[str]:
    """取出所有「」词，内部再按 → 切成层级段。"""
    out: list[str] = []
    for token in _TOKEN_RE.findall(text):
        for seg in token.split("→"):
            seg = seg.strip()
            if seg and seg not in out:
                out.append(seg)
    return out


def _candidate_files(seg: str) -> list[str]:
    proc = subprocess.run(
        ["git", "grep", "-l", "-F", "-e", seg, "--", *_CODE_PATHSPECS],
        cwd=_ROOT, capture_output=True, text=True, encoding="utf-8",
        errors="replace",
    )
    if proc.returncode not in (0, 1):      # 1 = 没有命中
        raise RuntimeError(proc.stderr.strip() or "git grep 失败")
    return [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]


def is_ui_literal(seg: str, text: str) -> bool:
    """界面词必须落在**引号里的字符串字面量**中；注释里的同名不算。"""
    return re.search(
        r"[\"'][^\"'\n]*" + re.escape(seg) + r"[^\"'\n]*[\"']", text
    ) is not None


def _literal_ok(seg: str, cache: dict[str, bool]) -> tuple[bool, str]:
    if seg in cache:
        return cache[seg], ""
    files = _candidate_files(seg)
    for name in files:
        with open(os.path.join(_ROOT, name), encoding="utf-8") as f:
            if is_ui_literal(seg, f.read()):
                cache[seg] = True
                return True, ""
    cache[seg] = False
    if not files:
        return False, "生产源码里 grep 不到该词"
    return False, f"只在 {', '.join(files[:3])} 出现，但不是字符串字面量（多在注释里）"


def _remedy_texts() -> dict[str, str]:
    """code → 帮助文案。精选 15 条有独立 next_step；其余走族级/逐码文案。"""
    out: dict[str, str] = {}
    for rec in ts.build_remedies():
        out[rec.code] = rec.next_step
    for key, text in ts.VISIBLE_NEXT_STEPS.items():
        out.setdefault("族:" + key, text)
    for code, text in ts.CODE_NEXT_STEPS.items():
        out[code] = text
    return out


def _expand_lines(spec: str) -> list[int]:
    out: list[int] = []
    for part in spec.split(","):
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return out


def _read_lines(path: str) -> list[str]:
    with open(os.path.join(_ROOT, path), encoding="utf-8") as f:
        return f.read().splitlines()


def _bullet_blocks(section: str) -> list[str]:
    """把「- 开头 + 两空格续行」合成一块，续行里的引用也算这条通道的断言。

    只看 ``ln.startswith("- **P")`` 会漏掉被折行的通道定义：P2 的
    ``status_panel.py:N`` 正好落在续行里，旧扫描器从未检查过它（2026-11-11
    实测：漏报 1 处）。
    """
    blocks: list[str] = []
    cur: list[str] = []
    for ln in section.splitlines():
        if ln.startswith("- "):
            if cur:
                blocks.append("\n".join(cur))
            cur = [ln]
        elif cur and ln.startswith("  ") and not ln.startswith("  |"):
            cur.append(ln)
        else:
            if cur:
                blocks.append("\n".join(cur))
            cur = []
    if cur:
        blocks.append("\n".join(cur))
    return blocks


def check_render_points(doc_path: str | None = None) -> list[str]:
    """文档第三、四节引用的渲染点行号必须仍落在渲染字面量上（防行号漂移）。

    扫描窗口是 ``## 三、``–``## 五、``：§四是「判定口径」里对同一批通道的
    复述，不在窗口内时它漂了也没人知道（2026-11-11 实测：漏报 3 处）。
    """
    problems: list[str] = []
    src = doc_path or _DOC
    try:
        with open(src, encoding="utf-8") as f:
            doc = f.read()
    except OSError:
        return [f"读不到 {src}"]
    start = doc.find("## 三、")
    end = doc.find("## 五、")
    if start < 0:
        return ["文档缺少「## 三、」章节，无法核验渲染点"]
    if end < 0:
        end = len(doc)
    section = doc[start:end]
    cache: dict[str, list[str]] = {}
    # 只核「渲染点」：P1–P5 的通道定义（含续行）+ 可见行（用户可见=是）的
    # 判定依据。不可见行的 文件:行 是触发点/底层实现，不是渲染点，不套渲染字面量。
    targets: list[str] = _bullet_blocks(section)
    for ln in section.splitlines():
        m = re.match(r"\| (\d+) \| `[^`]+` \|.*", ln)
        if m and int(m.group(1)) > 52:
            continue
        if m:
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            if len(cells) >= 7 and cells[3] == "是":
                targets.append(ln)
    for line in targets:
        for m in _CITE_RE.finditer(line):
            path, spec = m.groups()
            anchors = _RENDER_ANCHORS.get(os.path.basename(path))
            if not anchors:
                continue
            lines = cache.setdefault(path, _read_lines(path))
            cited = _expand_lines(spec)
            if any(
                any(a in lines[n - 1] for a in anchors)
                for n in cited if 1 <= n <= len(lines)
            ):
                continue
            actual = [str(i + 1) for i, ln in enumerate(lines)
                      if any(a in ln for a in anchors)]
            problems.append(
                f"文档行号漂移：{path}:{spec} 已不是渲染点"
                f"（渲染字面量现在在 :{', '.join(actual[:6]) or '（源码里也没了）'}）")
    return problems


def check_mechanisms() -> list[str]:
    """文案承诺的操作结果，代码里必须有对应机制；否则就是空话。"""
    problems: list[str] = []
    for label, path, anchor in _MECHANISM_ANCHORS:
        lines = _read_lines(path)
        if not any(anchor in ln for ln in lines):
            problems.append(f"{label}：{path} 里找不到机制锚点 {anchor!r}")
    return problems


def rendered_settings_sections() -> dict[str, tuple[str, ...]]:
    """设置面板**真正渲染**的节标题 → 该节包含的设置键。

    从 ``desktop/settings_panel.py`` 的 ``PANEL_SECTIONS`` 字面量解析（AST，
    不 import Qt）。不能用 ``config_schema.GROUP_ORDER`` 代替：那是 schema 侧
    的数据分组（导入/导出/文档用），面板已按用途重排成 6 节；拿 schema 分组
    验文案，「存储与日志」这种已并入别页的旧名会假绿（P1-41 的根因）。
    """
    path = os.path.join(_ROOT, "desktop", "settings_panel.py")
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    value = None
    for node in tree.body:
        if isinstance(node, ast.AnnAssign):
            target, rhs = node.target, node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, rhs = node.targets[0], node.value
        else:
            continue
        if getattr(target, "id", "") == "PANEL_SECTIONS":
            value = rhs
            break
    if value is None:
        return {}
    try:
        data = ast.literal_eval(value)
    except (ValueError, SyntaxError):
        return {}
    out: dict[str, tuple[str, ...]] = {}
    for row in data:
        if isinstance(row, (tuple, list)) and len(row) >= 3 and isinstance(row[0], str):
            out[row[0]] = tuple(k for k in row[2] if isinstance(k, str))
    return out


def check_paths() -> list[str]:
    """返回所有问题；空列表 = 全绿。"""
    problems: list[str] = []
    cache: dict[str, bool] = {}
    groups = rendered_settings_sections()
    for key, text in _remedy_texts().items():
        for seg in segments_of(text):
            ok, why = _literal_ok(seg, cache)
            if not ok:
                problems.append(f"{key}: 界面词「{seg}」{why}")
        for group in re.findall(r"「设置」\s*→\s*「([^」]+)」", text):
            if group not in groups:
                problems.append(f"{key}: 设置面板里没有「{group}」这一节")
    return problems


def _markdown_table() -> str:
    rows = ["| code / 条目 | 路径分段（→ 链） | 结果 |",
            "| --- | --- | --- |"]
    cache: dict[str, bool] = {}
    groups = set(rendered_settings_sections())
    for key, text in _remedy_texts().items():
        segs = segments_of(text)
        bad = [s for s in segs if not _literal_ok(s, cache)[0]]
        depth_bad = [g for g in re.findall(r"「设置」\s*→\s*「([^」]+)」", text)
                     if g not in groups]
        verdict = "对不上：" + "、".join(bad + [f"设置无「{g}」" for g in depth_bad]) \
            if (bad or depth_bad) else "一致"
        rows.append(f"| {key} | {' → '.join(segs) or '（无路径）'} | {verdict} |")
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--markdown", action="store_true", help="输出逐条核验表")
    args = parser.parse_args(argv)
    problems = check_paths()
    problems += check_render_points()
    problems += check_mechanisms()
    if args.markdown:
        print(_markdown_table())
        print()
    if problems:
        print(f"帮助路径核验失败（{len(problems)} 处）：")
        for p in problems:
            print("  - " + p)
        return 1
    print("帮助路径核验通过：所有 → 分段都是生产源码里的字符串字面量。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
