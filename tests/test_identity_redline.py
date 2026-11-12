# -*- coding: utf-8 -*-
"""身份红线门禁：tracked 文本文件里不得含个人信息与就业意向措辞。

依据 AGENTS.md 第 1 条（公开仓库 = 永久公开）：禁作者姓名/呼号（中性示例
除外）、年龄、就学计划与学校、联系方式、财务与行踪、真实地理位置默认值、
以及与就业意向相关的措辞。

本门禁只用**低误报**的硬规则：
· 邮箱：域名不在 example.* / noreply 名单、也不在显式允许的第三方公开
  服务邮箱名单内即命中（域首标签须字母开头，避免 iOS 资产文件名伪影）；
· 手机号：11 位大陆号段；两侧不得再邻小数分隔点，排除实测数据里
  小数位恰为 11 位号码形态的 S11/S21 测量值（如 0.148…9825 这类
  小数位形似手机号的数）——真实手机号不会与小数点相邻；卫星链路
  频率字段（downlink/uplink_*）的纯数值是 Hz，不是号码；
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
    ("手机号", re.compile(r"(?<![\d.])1[3-9]\d{9}(?![\d.])")),
    ("就业类措辞",
     re.compile(f"{_JOB}|{_JOB2}|{_JOB3}")),
    ("学校措辞", re.compile(f"{_SCHOOL}|{_SCHOOL2}")),
    ("居所/行踪措辞",
     re.compile(f"{_ADDR}|{_ADDR2}|{_ADDR3}|{_ADDR4}")),
]

TEXT_EXT = (".py", ".md", ".cpp", ".h", ".hpp", ".c", ".dart", ".js", ".ts",
            ".json", ".yaml", ".yml", ".sh", ".ps1", ".txt", ".ini", ".cfg",
            ".toml", ".qml", ".ui")

#: RFC 2606 保留域（.invalid / .test / .example 及 example.com/.net/.org）：
#: 这些域名不可投递、不指向真实身份，测试 fixture 允许使用。
ALLOWED_EMAIL_DOMAINS = ("example.com", "example.org", "example.net",
                         ".invalid", ".test", ".example",
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


def _embedded_in_data_blob(text: str, start: int, end: int) -> bool:
    """命中是否嵌在长 hex/base64 数据 token 里（摘要/金样字节的数字巧合）。

    2026-11-03 实测假警报：SHA-256 摘要十六进制串内嵌 11 位号码形态
    （摘要片段形似大陆手机号），被手机号规则咬中。真实手机号出现在
    散文里（被中文/标点/空白/短前缀包围），不会嵌进 16+ 字符的连续
    [A-Za-z0-9+/=_-] token。
    """
    token_re = re.compile(r"[A-Za-z0-9+/=_-]")
    ls = start
    while ls > 0 and token_re.match(text[ls - 1]):
        ls -= 1
    le = end
    while le < len(text) and token_re.match(text[le]):
        le += 1
    return (le - ls) >= 16


def _is_satellite_frequency_value(text: str, start: int) -> bool:
    """命中是否为 JSON 里卫星链路频率字段的纯数值（Hz）。

    2026-11-13 实测假警报：
    ``docs/evidence/satnogs-transmitters-20261112.json`` 的
    ``downlink_low/high/drifted = 15,003,400,000``（15.0034 GHz）被 11 位
    手机号规则咬中。手机号不会以 ``"(downlink|uplink)_(low|high|drift|
    drifted)":`` 作为 JSON 键出现；这里只豁免「该键 + 纯数字」这一形状，
    其它键或散文里的真号照常命中（见
    ``test_phone_rule_flags_non_frequency_json_value``）。
    """
    line_start = text.rfind("\n", 0, start) + 1
    prefix = text[line_start:start]
    return bool(re.search(
        r'"(?:downlink|uplink)_(?:low|high|drift|drifted)"\s*:\s*$', prefix))


def _is_escaped_newline_email(text: str, start: int, matched: str) -> bool:
    """命中是否来自「反斜杠 + n + @」的换行转义假象。

    2026-11-13 实测假警报：``tools/axioms/selftest.py`` 的示例字符串里
    有换行转义后紧跟 ``@pytest.mark.xfail`` 的片段，其中单字符 local
    part ``n`` 被邮箱规则咬中。它前面是反斜杠且 local part 只有 1 个
    字符，是转义序列不是邮箱；真实邮箱不会紧跟在反斜杠后且 local part
    恰为单字符。
    """
    local = matched.split("@", 1)[0]
    return start > 0 and text[start - 1] == "\\" and len(local) == 1


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
                    if _is_escaped_newline_email(text, m.start(), m.group(0)):
                        continue
                if label == "手机号" and (
                        _embedded_in_data_blob(text, m.start(), m.end())
                        or _is_satellite_frequency_value(text, m.start())):
                    continue
                hits.append(f"{rel}: {label} -> {m.group(0)[:40]}")
    assert not hits, "身份红线命中（AGENTS.md 第 1 条）:\n" + "\n".join(hits[:30])


def test_phone_rule_ignores_hashes_but_flags_prose():
    """手机号规则双向钉扎：摘要/金样里的数字巧合不咬，散文里的真号照咬。"""
    fake = 'sha256: 90714f9531aa4a1f43464c29905756df77f7eb3ea556466e0c17004199546a72'
    # 拼接构造，避免门禁自咬（同 _JOB 姿势）
    real = "请联系 " + "138" + "1234" + "5678" + " 咨询"
    phone = dict(RULES)["手机号"]
    assert phone.search(fake), "锚点自检：哈希里的号码形态要能被正则找到"
    assert _embedded_in_data_blob(fake, phone.search(fake).start(),
                                  phone.search(fake).end()), \
        "哈希内嵌号码必须被识别为数据巧合"
    m = phone.search(real)
    assert m and m.group(0) == "138" + "1234" + "5678", "散文里的真手机号必须命中"
    assert not _embedded_in_data_blob(real, m.start(), m.end()), \
        "散文手机号不得被误判为数据巧合"


def test_phone_rule_flags_non_frequency_json_value():
    """手机号规则双向钉扎：频率字段的纯数值不咬，其它键里的真号照咬。"""
    phone = dict(RULES)["手机号"]
    freq = '     "downlink_low": ' + "1500340" + "0000" + ',\n'
    m = phone.search(freq)
    assert m and _is_satellite_frequency_value(freq, m.start()), \
        "卫星链路频率字段的数字不应判为手机号"
    # 拼接构造，避免门禁自咬（同 _JOB 姿势）
    other = '     "serial": ' + "138" + "1234" + "5678" + ",\n"
    m2 = phone.search(other)
    assert m2 and not _is_satellite_frequency_value(other, m2.start()), \
        "非频率键里的 11 位号码必须照常命中"
    prose = "请联系 " + "138" + "1234" + "5678" + " 咨询"
    m3 = phone.search(prose)
    assert m3 and not _is_satellite_frequency_value(prose, m3.start()), \
        "散文里的真手机号必须照常命中"


def test_email_rule_flags_real_address_but_not_escape_artifact():
    """邮箱规则双向钉扎：转义假象不咬，普通真地址照咬。"""
    email = dict(RULES)["邮箱(非 example 域)"]
    fake = ("import pytest" + "\\" + "n" + "@"
            + "pytest.mark.xfail(reason='known')")
    m = email.search(fake)
    assert m and _is_escaped_newline_email(fake, m.start(), m.group(0)), \
        "换行转义紧接 @pytest 的文本不应判为邮箱"
    real = "联系 " + "x" + "@" + "foobar" + ".org"
    m2 = email.search(real)
    assert m2 and not _is_escaped_newline_email(
        real, m2.start(), m2.group(0)), "普通真实地址必须照常命中"


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
                if _is_escaped_newline_email(text, m.start(), m.group(0)):
                    continue
            if label == "手机号" and _embedded_in_data_blob(
                    text, m.start(), m.end()):
                continue
            raise AssertionError(f"门禁自身命中 {label}: {m.group(0)[:40]}")
