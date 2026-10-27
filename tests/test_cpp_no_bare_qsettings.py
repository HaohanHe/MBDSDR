# -*- coding: utf-8 -*-
"""生产代码 QSettings 构造收编门禁：cpp/src 里只允许经 core::AppSettings。

依据 cpp/src/core/app_settings.h 头注释的纪律：产品代码一律经
mbdsdr::core::AppSettings 读写设置；再直接写 QSettings 构造视为 bug。

为什么这是 bug 而不是风格问题：main.cpp 只设 applicationName、从不设
organizationName，裸 QSettings() 落到 org 为空的另一个存储，与
AppSettings（显式 "MBDSDR"/"MBDSDR"）读写不到同一份配置——写入被
静默丢弃（先例：校准 ppm 保存后主窗口永远读不回，活动日志重启清零，
control/httpPort 设置档失效）。app_settings.cpp 的构造器本身是唯一
合法例外（其初始化列表形如 `: QSettings(pickFormat()...)`，不以
变量声明形态出现，不会被本门禁误伤）。

规则（逐行扫描 cpp/src 下 .h/.cpp，先剥掉行注释再匹配）：
1. 变量声明形态：行首 `QSettings <name>;` / `QSettings <name>(` / `{`；
2. 堆构造形态：`new QSettings`；
3. 双参字面量形态：`QSettings("MBDSDR"...` / `QSettings(QStringLiteral("MBDSDR"...`。

块注释须以 // 或 * 开头（本仓 C++ 注释均为该风格，2026-10-27 全量核对）。
cpp/tests 不在扫描范围：测试在 qputenv(MBDSDR_SETTINGS_DIR) + 显式
org/app 下裸构造与 AppSettings 等价，收编属后续批次。
"""
import os
import re

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(_ROOT, "cpp", "src")

# 变量声明形态（含带参构造）。
_BARE_DECL = re.compile(r"^\s*QSettings\s+\w+\s*[;({]")
# 堆构造。
_NEW = re.compile(r"\bnew\s+QSettings\b")
# 双参字面量（含临时构造），防绕过声明形态直接链式调用。
_LITERAL2 = re.compile(
    r'QSettings\s*\(\s*(?:QStringLiteral\s*\(\s*)?"MBDSDR')

RULES = [
    ("裸构造(声明形态)", _BARE_DECL),
    ("裸构造(new)", _NEW),
    ("双参字面量构造", _LITERAL2),
]


def _strip_comment(line):
    # 剥行注释：先去掉字符串里的 // 误伤基本可控（本仓设置相关代码
    # 无含 // 的字符串字面量），再整体跳过纯注释行。
    idx = line.find("//")
    if idx >= 0:
        line = line[:idx]
    return line.rstrip()


def _iter_source_files():
    for dirpath, dirnames, filenames in os.walk(SRC):
        dirnames[:] = [d for d in dirnames
                       if d not in {"build", "build_cpp", "__pycache__"}]
        for fn in sorted(filenames):
            if fn.endswith((".cpp", ".h", ".hpp")):
                yield os.path.join(dirpath, fn)


def _scan():
    hits = []
    for path in _iter_source_files():
        rel = os.path.relpath(path, _ROOT).replace(os.sep, "/")
        try:
            with open(path, encoding="utf-8", errors="ignore") as fh:
                for i, raw in enumerate(fh, 1):
                    line = raw.rstrip("\n")
                    stripped = _strip_comment(line)
                    if not stripped.strip():
                        continue
                    head = stripped.lstrip()
                    # 块注释续行（以 * 开头）与纯行注释跳过。
                    if head.startswith("//") or head.startswith("*"):
                        continue
                    for label, pat in RULES:
                        m = pat.search(stripped)
                        if m:
                            hits.append(f"{rel}:{i}: {label} -> {m.group(0)}")
        except OSError:
            continue
    return hits


def test_no_bare_qsettings_construction_in_cpp_src():
    hits = _scan()
    assert not hits, (
        "cpp/src 出现裸 QSettings 构造（与 core::AppSettings 落不同存储，"
        "写入会被静默丢弃，按 bug 处理）——一律改用 mbdsdr::core::AppSettings：\n"
        + "\n".join(hits))


def test_app_settings_still_exists():
    # 门禁的前提：唯一合法构造点必须存在且仍提供默认构造。
    header = os.path.join(SRC, "core", "app_settings.h")
    impl = os.path.join(SRC, "core", "app_settings.cpp")
    assert os.path.isfile(header) and os.path.isfile(impl)
    with open(header, encoding="utf-8", errors="ignore") as fh:
        assert "class AppSettings" in fh.read()
