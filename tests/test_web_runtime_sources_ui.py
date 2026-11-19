# SPDX-License-Identifier: MIT
"""Web 前端「数据源」行：显示 SDR/GNSS/时间/站址，与桌面/HUD 同口径。

覆盖：
  * index.html 有只读数据源行（``id="ro-sources"``）；
  * app.js 消费 ``runtime_sources`` 并用统一文案渲染；
  * 若本机有 node，则抽出 ``fmtRuntimeSources`` 做行为断言（无 node 诚实跳过）。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC_DIR = Path(__file__).resolve().parent.parent / "mbdsdr_ai" / "web" / "static"
_HTML = STATIC_DIR / "index.html"
_JS = STATIC_DIR / "app.js"

_FN_RE = re.compile(
    r"\n  function fmtRuntimeSources\(rs\) \{.*?\n  \}\n", re.DOTALL)


def _js_text() -> str:
    return _JS.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# 静态契约
# --------------------------------------------------------------------------- #
def test_html_has_sources_readout():
    from html.parser import HTMLParser

    class _Readout(HTMLParser):
        """按 DOM 结构收集：元素 id、该元素内文本、整页文本。"""

        def __init__(self):
            super().__init__()
            self.ids = set()
            self.page_text = []
            self._depth = 0
            self._buffer = []
            self.inner = None

        def handle_starttag(self, tag, attrs):
            attributes = dict(attrs)
            if attributes.get("id") == "ro-sources":
                self.ids.add("ro-sources")
                self._depth = 1
                self._buffer = []

        def handle_endtag(self, tag):
            if self._depth == 1:
                self.inner = "".join(self._buffer).strip()
            if self._depth:
                self._depth -= 1

        def handle_data(self, data):
            self.page_text.append(data)
            if self._depth:
                self._buffer.append(data)

    html = _HTML.read_text(encoding="utf-8")
    parser = _Readout()
    parser.feed(html)
    assert "ro-sources" in parser.ids, "缺数据源读数行"
    assert "数据源" in "".join(parser.page_text)
    # 初始空态不是假值：元素内文本必须是 "--"
    assert parser.inner == "--", f"初始空态被写成了 {parser.inner!r}"


def test_appjs_consumes_runtime_sources():
    js = _js_text()
    assert "runtime_sources" in js
    assert "function fmtRuntimeSources(" in js
    assert '$("ro-sources")' in js
    # status 分支里更新该行（缺失字段不动它）
    assert "if (sources) roSources.textContent = sources;" in js


def test_appjs_copies_desktop_wording():
    js = _js_text()
    for text in (
        "SDR 已连接 · 无样本（超时）",   # stalled（Task 107 口径）
        "SDR 已连接",
        "SDR 未连接",
        "GNSS 已定位",
        "GNSS 未连接",
        "天线开路",
        "天线短路",
        "卫星授时",
        "系统时钟",
        "位置：",
        "GNSS",
        "手动",
        "未设置",
    ):
        assert text in js, f"app.js 缺少文案：{text}"


def test_appjs_branches_exist():
    """字段缺失不报错、不补假值：各分支以快照字段是否存在为前提。"""
    js = _js_text()
    assert 'typeof sdr.connected === "boolean"' in js
    assert 'typeof gnss.connected === "boolean"' in js
    assert 'gnss.antenna === "open"' in js
    assert 'gnss.antenna === "short"' in js
    assert 'typeof time.source === "string"' in js
    assert 'typeof site.source === "string"' in js


# --------------------------------------------------------------------------- #
# 行为断言（可选：本机有 node 才跑）
# --------------------------------------------------------------------------- #
def _extract_fn() -> str:
    m = _FN_RE.search(_js_text())
    assert m is not None, "未能从 app.js 抽出 fmtRuntimeSources"
    return m.group(0).strip("\n")


@pytest.mark.skipif(shutil.which("node") is None, reason="未安装 node，跳过 JS 行为断言")
def test_fmt_runtime_sources_behavior(tmp_path):
    fn_src = _extract_fn()
    script = fn_src + """

const chunks = [];
process.stdin.on("data", (c) => chunks.push(c));
process.stdin.on("end", () => {
  const cases = JSON.parse(Buffer.concat(chunks).toString("utf8"));
  process.stdout.write(JSON.stringify(cases.map(fmtRuntimeSources)));
});
"""
    script_path = tmp_path / "fmt_sources.js"
    script_path.write_text(script, encoding="utf-8")

    cases = [
        # 全量：SDR 有连接但无样本；GNSS 已定位且天线开路；卫星授时 + GNSS 站址
        {
            "sdr": {"connected": True, "stalled": True},
            "gnss": {"connected": True, "fix": True, "antenna": "open",
                     "time_source": "nmea", "site_source": "gnss"},
            "time": {"source": "gnss"},
            "site": {"source": "gnss"},
        },
        # 全断开 / 回退：系统时钟 + 未设置站址
        {
            "sdr": {"connected": False},
            "gnss": {"connected": False, "fix": False, "antenna": "unknown"},
            "time": {"source": "system"},
            "site": {"source": "none"},
        },
        # 天线短路 + 手动站址
        {
            "sdr": {"connected": True},
            "gnss": {"connected": True, "fix": True, "antenna": "short",
                     "time_source": "none", "site_source": "manual"},
            "time": {"source": "system"},
            "site": {"source": "manual"},
        },
        # 已连接但未定位
        {
            "sdr": {"connected": True},
            "gnss": {"connected": True, "fix": False, "antenna": "ok"},
        },
        # 字段缺失：只显示写明的部分，不补默认值
        {"sdr": {"connected": True}},
        {},
        None,
    ]

    proc = subprocess.run(
        ["node", str(script_path)],
        input=json.dumps(cases).encode("utf-8"),
        capture_output=True,
        timeout=20,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    got = json.loads(proc.stdout.decode("utf-8"))

    assert got[0] == (
        "SDR 已连接 · 无样本（超时） · GNSS 已定位 · 天线开路"
        " · 时间：卫星授时 · 位置：GNSS")
    assert got[1] == "SDR 未连接 · GNSS 未连接 · 时间：系统时钟 · 位置：未设置"
    assert got[2] == "SDR 已连接 · GNSS 已定位 · 天线短路 · 时间：系统时钟 · 位置：手动"
    assert got[3] == "SDR 已连接 · GNSS 已连接（未定位）"
    assert got[4] == "SDR 已连接"
    assert got[5] == ""
    assert got[6] == ""
