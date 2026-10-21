# -*- coding: utf-8 -*-
"""身份红线门禁：tracked 文本文件里不得含个人信息与就业意向措辞。

依据 AGENTS.md 第 1 条（公开仓库 = 永久公开）：禁作者姓名/呼号（中性示例
除外）、年龄、就学计划与学校、联系方式、财务与行踪、真实地理位置默认值、
以及与就业意向相关的措辞。

本门禁只用**低误报**的硬规则：
· 邮箱：域名不在 example.* / noreply 名单、也不在显式允许的第三方公开
  服务邮箱名单内即命中（域首标签须字母开头，避免 iOS 资产文件名伪影）；
· 手机号：11 位大陆号段；
· 就业意向类措辞：只收无歧义的三个中文词（见下方 _JOB* 字面量）；
  英文 "resume" 在本仓是"恢复扫描/会话"的技术用法，"投递"在本仓是
  "论文投递"，误报率高，均不入门禁；
· 学校类词；
· 居所/行踪类词。

呼号类正则误报更高（协议缩写、卫星编号会撞），不入门禁——那部分靠人工
审阅与 AGENTS.md 约定。

规则字面量用拼接构造，避免本文件自身命中自己的规则。
"""
import os
import re
import subprocess

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = _ROOT

# 字面量拼接：本文件自身不命中自己的规则
_JOB = "简" + "历"
_JOB2 = "求" + "职"
_JOB3 = "面" + "试"
_SCHOOL = "大" + "学"
_SCHOOL2 = "学" + "院"
_ADDR = "住" + "址"
_ADDR2 = "老" + "家"
_ADDR3 = "所" + "在地"
_ADDR4 = "宿" + "舍"

RULES = [
    ("邮箱(非 example 域)",
     re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z][A-Za-z0-9.-]*\.[A-Za-z]{2,})")),
    ("手机号", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("就业类措辞",
     re.compile(f"{_JOB}|{_JOB2}|{_JOB3}")),
    ("学校措辞", re.compile(f"{_SCHOOL}|{_SCHOOL2}")),
    ("居所/行踪措辞",
     re.compile(f"{_ADDR}|{_ADDR2}|{_ADDR3}|{_ADDR4}")),
]

TEXT_EXT = (".py", ".md", ".cpp", ".h", ".hpp", ".c", ".dart", ".js", ".ts",
            ".json", ".yaml", ".yml", ".sh", ".ps1", ".txt", ".ini", ".cfg",
            ".toml", ".qml", ".ui")

ALLOWED_EMAIL_DOMAINS = ("example.com", "example.org", "example.net",
                         "noreply.github.com", "users.noreply.github.com")

#: 厂商公开支持邮箱（第三方公开联系方式，非作者个人信息）：
#: siliconflow 是本仓默认 LLM 端点提供方，其支持邮箱出现在 API 文档里。
ALLOWED_EMAIL_EXACT = {
    # 厂商公开支持邮箱（第三方公开地址）
    "contact@siliconflow.cn",
    # 业余卫星图像活动的公开服务邮箱（第三方公开地址，runbook 需要）
    "casc70@asesspace.com",
}


def _tracked_files():
    try:
        out = subprocess.run(
            ["git", "-C", REPO, "ls-files"],
            capture_output=True, text=True, encoding="utf-8",
            errors="ignore", timeout=120).stdout
    except Exception:
        out = ""
    files = [f for f in out.split("\n") if f]
    if files:
        return files
    # 非 git 环境：退化为目录扫描
    acc = []
    for dirpath, dirnames, filenames in os.walk(REPO):
        dirnames[:] = [d for d in dirnames
                       if d not in {".git", "build", "build_cpp", "__pycache__",
                                    "node_modules", ".dart_tool"}]
        for fn in filenames:
            if fn.lower().endswith(TEXT_EXT):
                acc.append(os.path.relpath(os.path.join(dirpath, fn), REPO))
    return acc


def test_no_personal_info_in_tracked_files():
    hits = []
    for rel in _tracked_files():
        if not rel.lower().endswith(TEXT_EXT):
            continue
        p = os.path.join(REPO, rel.replace("/", os.sep))
        try:
            with open(p, encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except OSError:
            continue
        for label, pat in RULES:
            for m in pat.finditer(text):
                if label.startswith("邮箱"):
                    dom = m.group(1).lower()
                    if any(dom.endswith(d) for d in ALLOWED_EMAIL_DOMAINS):
                        continue
                    if m.group(0).lower() in ALLOWED_EMAIL_EXACT:
                        continue
                hits.append(f"{rel}: {label} -> {m.group(0)[:40]}")
    assert not hits, "身份红线命中（AGENTS.md 第 1 条）:\n" + "\n".join(hits[:30])


def test_redline_gate_is_not_self_triggering():
    """门禁文件自身不得命中自己的规则（否则改规则就得改门禁）。"""
    p = os.path.join(REPO, "tests", "test_identity_redline.py")
    with open(p, encoding="utf-8", errors="ignore") as f:
        text = f.read()
    for label, pat in RULES:
        for m in pat.finditer(text):
            if label.startswith("邮箱"):
                dom = m.group(1).lower()
                if any(dom.endswith(d) for d in ALLOWED_EMAIL_DOMAINS):
                    continue
                if m.group(0).lower() in ALLOWED_EMAIL_EXACT:
                    continue
            raise AssertionError(f"门禁自身命中 {label}: {m.group(0)[:40]}")
