# SPDX-License-Identifier: MIT
"""帮助条目「操作路径」核验器。

帮助文案里的路径用「」标出控件/页签名、用 ``→`` 表示层级，例如
``「设置」→「数据与操作」``、``「音频输出 → 输出设备」``。控件改名或面板
搬家后，文案会变成假话，而用户照着做会一直找不到。本脚本把**每一段**
拿去生产源码里找字符串字面量：找不到就报红，并给出条目 key。

「设置」→「X」的 X 只认 ``settings_panel.PANEL_SECTIONS`` 里**真正渲染**的
节名（源码 AST 解析，不 import Qt）；``config_schema.GROUP_ORDER`` 是 schema
侧的数据分组，面板重排后两者不再等价，拿它验文案会假绿。

只读 git 里的源码文本（默认 HEAD 快照，``--worktree`` 时读工作树），不启动
Qt、不 import 桌面 UI。

用法：
    python scripts/check_help_paths.py            # 按 HEAD 快照检查，失败退出码 1
    python scripts/check_help_paths.py --worktree # 改读工作树（含未提交改动）
    python scripts/check_help_paths.py --markdown # 额外输出 52 条核验表

默认读 **HEAD 快照**（``git show HEAD:<path>`` / ``git grep ... HEAD``）：并行开发
时工作树每一分钟都在被别人改，拿它判「文档有没有漂」会把「别人正在编辑」误报成
「文档漂移」。单人本地自测可用 ``--worktree``，输出会显式标注口径。
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

_DOC_REL = "docs/error-code-visibility.md"
_DOC = os.path.join(_ROOT, _DOC_REL)

#: 读源头。默认 **HEAD 快照**：并行开发时工作树每一分钟都在被别人改，拿它判
#: 「文档有没有漂」会把「别人正在编辑」误报成「文档漂移」（2026-11-11 夜间
#: main_window.py 未提交 +59 行，渲染闸门当场红在尚未成为事实的代码上）。
#: 单人本地自测可用 ``--worktree`` 切到工作树，输出会显式标注口径。
_SOURCE = "head"


def set_source(mode: str) -> None:
    """切换读取口径：``head``（默认）或 ``worktree``。"""
    global _SOURCE
    _SOURCE = "worktree" if mode == "worktree" else "head"


def source_label() -> str:
    return "工作树（可能含未提交改动）" if _SOURCE == "worktree" else "HEAD 快照"


def _read_text(path: str) -> str | None:
    """按当前口径读仓库内文件；读不到返回 None。"""
    if _SOURCE == "worktree":
        try:
            with open(os.path.join(_ROOT, path), encoding="utf-8") as f:
                return f.read()
        except OSError:
            return None
    proc = subprocess.run(
        ["git", "show", f"HEAD:{path}"], cwd=_ROOT, capture_output=True,
        text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        return None
    return proc.stdout

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
    cmd = ["git", "grep", "-l", "-F", "-e", seg]
    if _SOURCE != "worktree":
        cmd.append("HEAD")       # 读提交里的事实，不看别人正在编辑的副本
    cmd += ["--", *_CODE_PATHSPECS]
    proc = subprocess.run(
        cmd, cwd=_ROOT, capture_output=True, text=True, encoding="utf-8",
        errors="replace",
    )
    if proc.returncode not in (0, 1):      # 1 = 没有命中
        raise RuntimeError(proc.stderr.strip() or "git grep 失败")
    out: list[str] = []
    for ln in proc.stdout.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        if _SOURCE != "worktree" and ln.startswith("HEAD:"):
            ln = ln[len("HEAD:"):]        # git grep <rev> 会把 rev 前缀带进路径
        out.append(ln)
    return out


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
        text = _read_text(name)
        if text is not None and is_ui_literal(seg, text):
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
    text = _read_text(path)
    if text is None:
        raise FileNotFoundError(f"读不到 {path}（口径：{source_label()}）")
    return text.splitlines()


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
    """文档里每个 ``文件:行号`` 引用都要能定位；渲染通道声明的还要落在字面量上。

    **不再按章节窗口截断**（局部窗口本身就是漏点：§五 的 ``6119`` 实际在
    ``9620``，把窗口扩到 §五 也扫不到）。现在全文每个 ``path:N`` 都进池：

    * 结构：文件在读取口径下存在、行号在范围内；
    * 语义：只有 P1–P5 的通道定义（含续行）与「可见路径=是」的判定行才是
      **渲染点声明**，它们的行号必须仍落在 ``_RENDER_ANCHORS`` 的字面量上。
      其余引用是触发点/底层实现，不套渲染字面量（§八/§九 的步骤卡定位就属于
      这一类，套锚点会制造假红）。
    """
    problems: list[str] = []
    src = doc_path or _DOC
    try:
        if doc_path:
            with open(src, encoding="utf-8") as f:   # 显式传入的路径按磁盘读
                doc = f.read()
        else:
            doc = _read_text(_DOC_REL)              # 默认按当前口径（HEAD）
    except OSError:
        doc = None
    if doc is None:
        return [f"读不到 {src}（口径：{source_label()}）"]
    cache: dict[str, list[str] | None] = {}

    def _lines_for(path: str) -> list[str] | None:
        if path not in cache:
            text = _read_text(path)
            cache[path] = None if text is None else text.splitlines()
        return cache[path]

    # 1) 结构：全文每一个引用都必须能定位（文件被删/行号越界都算失效）
    for m in _CITE_RE.finditer(doc):
        path, spec = m.groups()
        lines = _lines_for(path)
        if lines is None:
            msg = f"文档引用的 {path} 在{source_label()}里不存在（{path}:{spec}）"
            if msg not in problems:
                problems.append(msg)
            continue
        bad = [p for p in spec.split(",")
               if any(not (1 <= n <= len(lines)) for n in _expand_lines(p))]
        if bad:
            msg = (f"文档引用越界：{path}:{','.join(bad)}"
                   f"（该文件共 {len(lines)} 行）")
            if msg not in problems:
                problems.append(msg)

    # 2) 语义：渲染点声明必须落在渲染字面量上。判据是**行内容**（P1–P5 通道
    #    定义 / 可见路径=是），不再是「第几节到第几节」的位置窗口。
    targets: list[str] = [b for b in _bullet_blocks(doc)
                          if re.match(r"- \*\*P\d", b)]
    for ln in doc.splitlines():
        m = re.match(r"\| (\d+) \| `[^`]+` \|.*", ln)
        if not m or int(m.group(1)) > 52:
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(cells) >= 7 and cells[3] == "是":
            targets.append(ln)
    for line in targets:
        for m in _CITE_RE.finditer(line):
            path, spec = m.groups()
            anchors = _RENDER_ANCHORS.get(os.path.basename(path))
            if not anchors:
                continue
            lines = _lines_for(path) or []
            if not lines:
                continue
            cited = _expand_lines(spec)
            if any(
                any(a in lines[n - 1] for a in anchors)
                for n in cited if 1 <= n <= len(lines)
            ):
                continue
            actual = [str(i + 1) for i, ln in enumerate(lines)
                      if any(a in ln for a in anchors)]
            msg = (f"文档行号漂移：{path}:{spec} 已不是渲染点"
                   f"（渲染字面量现在在 :{', '.join(actual[:6]) or '（源码里也没了）'}）")
            if msg not in problems:      # 同一坐标被多个通道引用时只报一次
                problems.append(msg)
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
    text = _read_text("desktop/settings_panel.py")
    if text is None:
        return {}
    tree = ast.parse(text)
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


#: 导航路径的根词：只有以它开头的链才继续核对「设置」页的分组名。
_NAV_ROOT = "设置"

#: 归属其他开发线 / 本次禁改的文件：扫到的问题只报告、不进闸门判定。
#: 键是仓库相对路径；值是「为什么不能改」。这不是静默白名单：``check_
#: settings_nav_paths`` 会把它们单独返回，测试与 CLI 都必须显式打印。
_SCAN_DEFERRED = {
    "desktop/main_window.py":
        "归 G1 采样率线（本任务禁改），文案修正已登记 "
        "docs/pending-patches/2026-11-11-mainwindow-copy-A6-A7-A10.md",
    "desktop/rf_sky_view.py":
        "归 A03 第一批线，claim 未释放",
}

#: 段尾切分：箭头段里常混着散文（「点「选择目录…」」），只取紧贴箭头的那个词。
_SEG_TAIL_CUT = re.compile(r"[」』）)】\]\s，。；：,;:！？!?·]")
_SEG_HEAD_CUT = re.compile(r"[「『（(【\[\s，。；：,;:！？!?·]")


def _clean_head(raw: str) -> str:
    """箭头**右侧**的一段：取该段开头、紧贴箭头的那个词。"""
    return _SEG_TAIL_CUT.split(raw.strip(), 1)[0].strip(" 「『（(【[")


def _clean_tail(raw: str) -> str:
    """箭头**左侧**的一段：取该段结尾、紧贴箭头的那个词。"""
    return _SEG_HEAD_CUT.split(raw.strip())[-1].strip().rstrip("」』）)】]")


def _string_literals(text: str) -> list[tuple[int, str]]:
    """文件里所有用户可见的字符串常量（跳过注释与 docstring）。

    注释/文档字符串不是用户能看到的话；把它们算进来会把「改设置 → 重启」
    这种说明文字误报成导航路径。
    """
    tree = ast.parse(text)
    skip: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                skip.add(id(body[0].value))
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            # f-string：已拼好的常量片段里出现整条路径才能核验
            parts = [v.value for v in node.values
                     if isinstance(v, ast.Constant) and isinstance(v.value, str)]
            skip.update(id(v) for v in node.values
                        if isinstance(v, ast.Constant))
            joined = "".join(parts)
            if "→" in joined:
                out.append((node.lineno, joined))
        elif (isinstance(node, ast.Constant) and isinstance(node.value, str)
              and id(node) not in skip and "→" in node.value):
            out.append((node.lineno, node.value))
    return out


def _arrow_files() -> list[str]:
    """生产源码里含 ``→`` 的文件：只有它们可能有导航路径。"""
    cmd = ["git", "grep", "-l", "-I", "-e", "→"]
    if _SOURCE != "worktree":
        cmd.append("HEAD")
    # 与 ``_CODE_PATHSPECS`` 不同：帮助文案本体 troubleshooting.py 必须扫，
    # 这里查的是「路径指向的分组存不存在」，不靠字面量自证，不存在自指漏洞。
    cmd += ["--", "desktop", "mbdsdr_ai"]
    proc = subprocess.run(cmd, cwd=_ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode not in (0, 1):
        raise RuntimeError(proc.stderr.strip() or "git grep 失败")
    out = []
    for ln in proc.stdout.splitlines():
        ln = ln.strip()
        if ln.startswith("HEAD:"):
            ln = ln[len("HEAD:"):]
        if ln.endswith(".py"):
            out.append(ln)
    return out


def scan_settings_nav_paths(literals=None) -> list[dict]:
    """按结构解析文案里的「设置 → X …」链，返回每条路径的核验结果。

    ``literals`` 可传 ``[(origin, lineno, text), ...]`` 注入反证文案；
    不传则扫生产源码。判据是**位置**：``→`` 链的第一段必须是「设置」，
    第二段才拿去核对设置页分组名——不是「文案里出现过某个分组词」。
    """
    groups = rendered_settings_sections()
    if literals is None:
        literals = []
        for path in _arrow_files():
            text = _read_text(path)
            if not text:
                continue
            for line_no, value in _string_literals(text):
                literals.append((path, line_no, value))
    rows: list[dict] = []
    seen: set[tuple] = set()
    for origin, line_no, text in literals:
        for piece in text.splitlines() or [text]:
            if "→" not in piece:
                continue
            parts = piece.split("→")
            segs = [_clean_tail(parts[0])] + [
                _clean_head(p) for p in parts[1:]]
            for idx, seg in enumerate(segs):
                if seg != _NAV_ROOT or idx + 1 >= len(segs):
                    continue
                group = segs[idx + 1]
                if not group:
                    continue
                chain = segs[idx:]
                key = (origin, line_no, tuple(chain))
                if key in seen:
                    continue
                seen.add(key)
                rows.append({
                    "origin": origin,
                    "line": line_no,
                    "chain": chain,
                    "group": group,
                    "ok": group in groups,
                })
    return rows


def check_settings_nav_paths(literals=None):
    """返回 ``(problems, deferred, rows)``。

    problems  = 归属本线/无人占用文件里的过期路径（闸门判定）；
    deferred  = 归属他人或禁改文件里的过期路径（只报告，带原因）；
    rows      = 扫到的全部「设置 → …」路径（含通过项）。
    """
    groups = sorted(rendered_settings_sections())
    problems: list[str] = []
    deferred: list[str] = []
    rows = scan_settings_nav_paths(literals)
    for row in rows:
        if row["ok"]:
            continue
        where = f"{row['origin']}:{row['line']}"
        chain = " → ".join(row["chain"])
        msg = (f"{where} 路径「{chain}」里的设置分组「{row['group']}」不存在；"
               f"设置页现有分组：{' / '.join(groups)}")
        reason = _SCAN_DEFERRED.get(row["origin"])
        if reason:
            deferred.append(f"{msg}（未纳入闸门：{reason}）")
        else:
            problems.append(msg)
    return problems, deferred, rows


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
    parser.add_argument("--worktree", action="store_true",
                        help="读工作树而不是 HEAD 快照；输出会标注"
                             "「工作树口径，可能含未提交改动」")
    args = parser.parse_args(argv)
    set_source("worktree" if args.worktree else "head")
    print(f"口径：{source_label()}")
    problems = check_paths()
    problems += check_render_points()
    problems += check_mechanisms()
    nav_problems, nav_deferred, nav_rows = check_settings_nav_paths()
    problems += nav_problems
    print(f"「设置 → …」路径：扫到 {len(nav_rows)} 条，"
          f"对不上 {len(nav_problems) + len(nav_deferred)} 条"
          f"（其中闸门内 {len(nav_problems)} 条、他人占用 {len(nav_deferred)} 条）")
    for d in nav_deferred:
        print("  [未纳入闸门] " + d)
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
