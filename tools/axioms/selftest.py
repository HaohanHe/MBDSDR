"""反证自检：在临时仓库里重放真实违规片段，要求「正例红、负例绿」。

为什么用临时仓库而不是直接在 main 仓上删代码：
- main 仓有并行工作，不能为了自检去改别人的文件；
- 临时仓库仍然走与生产完全相同的检查函数，不是另写一套判定；
- 正例是真实代码片段的逐字复制（provenance 注明来源），负例是对应的修复形态。
正例必须红、负例必须绿；只红不绿说明检查在乱报，只绿不红说明检查空过。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence

from .model import Axiom, Finding, Repo, priority_score
from .survey import audit_waiver_items, collect_all


@dataclass
class Case:
    rule: str
    name: str
    provenance: str
    files_pos: Dict[str, str]
    files_neg: Dict[str, str]
    git: bool = False
    match: str = ""  # 只认这条具体规则；为空时按 rule 前缀匹配


def _write_tree(root: str, files: Dict[str, str]) -> None:
    for rel, text in files.items():
        full = os.path.join(root, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)


def _init_git(root: str) -> None:
    env = dict(os.environ)
    env.update({
        "GIT_AUTHOR_NAME": "axiom", "GIT_AUTHOR_EMAIL": "axiom@example.invalid",
        "GIT_COMMITTER_NAME": "axiom", "GIT_COMMITTER_EMAIL": "axiom@example.invalid",
    })
    subprocess.run(["git", "init", "-q"], cwd=root, env=env, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=root, env=env, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=root, env=env, capture_output=True)


def _run_case(case: Case, files: Dict[str, str]) -> List[Finding]:
    root = tempfile.mkdtemp(prefix="mbdsdr-axiom-")
    try:
        _write_tree(root, files)
        if case.git:
            _init_git(root)
            head = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"],
                                  capture_output=True, text=True).stdout.strip()
            if head:
                for rel, text in list(files.items()):
                    if "{head}" in text:
                        _write_tree(root, {rel: text.replace("{head}", head)})
        repo = Repo(root)
        _, dims = collect_all(repo)
        want = case.match or case.rule
        return [
            f for d in dims for f in d.findings
            if f.rule == want or (not case.match and f.rule.startswith(case.rule + "-"))
        ]
    finally:
        shutil.rmtree(root, ignore_errors=True)


BASIC = "desktop/__init__.py"

CASES: List[Case] = [
    Case("A01", "读数 f-string 就地拼装",
         "desktop/main_window.py 状态条 / desktop/control_panel.py 的 MHz f-string",
         {"desktop/panel.py": "def label(self, freq_hz):\n    self.freq_label.setText(f\"{freq_hz/1e6:.3f} MHz\")\n"},
         {"desktop/readouts.py": "def fmt_frequency_hz(hz):\n    return f\"{hz/1e6:.6f} MHz\"\n"},
         match="A01"),
    Case("A02", "频率被 3 位小数 MHz 量化",
         "desktop/main_window.py 状态条 `.3f} MHz`；readouts.py 文档记录的 2400 Hz→2 kHz",
         {"desktop/panel.py": "def label(freq_hz):\n    return f\"{freq_hz/1e6:.3f} MHz\"\n"},
         {"desktop/panel.py": "def label(freq_hz):\n    return f\"{freq_hz/1e6:.6f} MHz\"\n"}),
    Case("A03", "持久化读取静默回空",
         "mbdsdr_ai/conversation.py 读会话文件 except Exception: pass",
         {"mbdsdr_ai/store.py": "import json\n\ndef load(p):\n    try:\n        return json.load(open(p))\n    except Exception:\n        pass\n"},
         {"mbdsdr_ai/store.py": "import json, logging\n\ndef load(p):\n    try:\n        return json.load(open(p))\n    except Exception as exc:\n        logging.warning('load failed: %s', exc)\n        return None\n"}),
    Case("A04", "读损坏后仍整文件覆写",
         "mbdsdr_ai/conversation.py 读失败回默认后继续 json.dump",
         {"mbdsdr_ai/store.py": "import json, os\n\ndef load(p):\n    try:\n        return json.load(open(os.path.expanduser(p)))\n    except Exception:\n        return {}\n\ndef save(p, data):\n    json.dump(data, open(os.path.expanduser(p), 'w'))\n"},
         {"mbdsdr_ai/store.py": "import json, os\n\ndef load(p):\n    try:\n        return json.load(open(os.path.expanduser(p)))\n    except Exception:\n        backup_corrupt(p)\n        return {}\n\ndef save(p, data):\n    json.dump(data, open(os.path.expanduser(p), 'w'))\n"}),
    Case("A05", "物理上不可能通过的断言",
         "tests/test_network_failure_modes.py:407 慢滴握手（消息自述需要 307s）",
         {"tests/test_x.py": "def test_x():\n    assert False, \"按当前实现需要 307s 才可能结束\"\n"},
         {"tests/test_x.py": "def test_x():\n    value = 1\n    assert value == 1\n"}),
    Case("A06", "非 strict xfail 的空反证",
         "docs/code-guards.md 要求删除被测逻辑后必须变红",
         {"tests/test_x.py": "import pytest\n\n@pytest.mark.xfail(reason='known')\ndef test_x():\n    assert 0\n"},
         {"tests/test_x.py": "import pytest\n\n@pytest.mark.xfail(strict=True, reason='known')\ndef test_x():\n    assert 0\n"}),
    Case("A07", "约束只放在一个入口",
         "desktop/device_panel.py 的采样率范围校验 vs 其他 set_sample_rate 路径",
         {"desktop/a.py": "def apply(backend, r):\n    if 10 <= r <= 100:\n        backend.set_sample_rate(r)\n",
          "desktop/b.py": "def apply(backend, r):\n    backend.set_sample_rate(r)\n"},
         {"desktop/a.py": "def apply(backend, r):\n    if 10 <= r <= 100:\n        backend.set_sample_rate(r)\n",
          "desktop/b.py": "def apply(backend, r):\n    if 10 <= r <= 100:\n        backend.set_sample_rate(r)\n"}),
    Case("A08", "守卫条件恒假",
         "mbdsdr_ai/decoders.py:435 `if False:`",
         {"mbdsdr_ai/d.py": "def f(device):\n    if False:\n        return None\n    return device\n"},
         {"mbdsdr_ai/d.py": "def f(device):\n    if device is None:\n        return None\n    return device\n"}),
    Case("A09", "依赖缺失被登记成环境 skip",
         "tests/test_web_bridge_mcp.py 6 处「MCPToolSet 在此环境不可构造」",
         {"tests/test_x.py": "import pytest\n\ndef test_x():\n    pytest.skip('MCPToolSet 在此环境不可构造')\n"},
         {"tests/test_x.py": "import pytest\n\ndef test_x():\n    pytest.skip('缺 de421.bsp，未随仓提供')\n"}),
    Case("A12", "同名不同实现",
         "desktop/main_window.py 的 _fmt_dbfs 与 desktop/readouts.py 的 fmt_dbfs",
         {"desktop/a.py": "def _fmt_dbfs(v):\n    a = 1\n    b = 2\n    c = 3\n    return a + b + c\n",
          "desktop/b.py": "def _fmt_dbfs(v):\n    a = 1\n    b = 2\n    c = 4\n    return a + b + c\n"},
         {"desktop/a.py": "def _fmt_dbfs(v):\n    a = 1\n    b = 2\n    c = 3\n    return a + b + c\n",
          "desktop/b.py": "def _fmt_dbfs(v):\n    a = 1\n    b = 2\n    c = 3\n    return a + b + c\n"}),
    Case("A13", "用 owner 名字判同一",
         "scripts/file_claim.py:540 `if holder != owner:`（release 只比名字）",
         {"scripts/claim.py": "def check(holder, owner):\n    if holder == owner:\n        return True\n    return False\n"},
         {"scripts/claim.py": "def check(holder, owner, pid, current_pid):\n    if holder == owner and pid == current_pid:\n        return True\n    return False\n"}),
    Case("D1", "纯 Python 层依赖 Qt",
         "mbdsdr_ai 不得 import PySide6（分层契约）",
         {"mbdsdr_ai/core.py": "import PySide6\n"},
         {"mbdsdr_ai/core.py": "import json\n"},
         match="D1-qt-in-core"),
    Case("D1", "desktop 内部裸模块名成环",
         "desktop 用 sys.path 垫片裸名导入（from tokens import ...）；"
         "环检测必须按裸名归一，且函数内延迟 import 也算边",
         {"desktop/a.py": "from b import thing\n", "desktop/b.py": "def f():\n    from a import other\n"},
         {"desktop/a.py": "from b import thing\n", "desktop/b.py": "thing = 1\n"},
         match="D1-cycle"),
    Case("D1", "核心库反向依赖开发工具",
         "mbdsdr_ai 不得 import tools/（pip 装核心包的人拿不到开发工具）；"
         "tests/scripts -> tools 才是允许方向",
         {"mbdsdr_ai/x.py": "from tools.onboarding.onboard import step_record\n"},
         {"mbdsdr_ai/x.py": "from mbdsdr_ai.playback import parse_sigmf_meta\n"},
         match="D1-core-depends-tools"),
    Case("D2", "同一读数多文件产生",
         "desktop 多处各自拼 Hz 读数，未登记真值源",
         {"desktop/a.py": "def f(x):\n    return f\"{x} Hz\"\n",
          "desktop/b.py": "def g(x):\n    return f\"{x} Hz\"\n"},
         {"desktop/a.py": "def f(x):\n    return f\"{x} Hz\"\n"},
         match="D2-multi-producer"),
    Case("D3", "同一概念两个名字",
         "仓内 bw / bandwidth 两套命名并存",
         {"desktop/a1.py": "bw = 1\n", "desktop/a2.py": "bw = 2\n",
          "desktop/b1.py": "bandwidth = 1\n", "desktop/b2.py": "bandwidth = 2\n"},
         {"desktop/b1.py": "bandwidth = 1\n", "desktop/b2.py": "bandwidth = 2\n"},
         match="D3-concept-alias"),
    Case("D4", "门禁脚本没有任何入口",
         "scripts/check_*.py 既未接入钩子也未在文档登记",
         {"scripts/check_foo.py": "print('gate')\n"},
         {"scripts/check_foo.py": "print('gate')\n", "docs/code-guards.md": "运行 `scripts/check_foo.py`。\n"},
         match="D4-silent-gate"),
    Case("D5", "已知红缺转绿条件",
         "docs/known-red-register.md 要求每条红必须带转绿条件",
         {"docs/known-red-register.md": "## K01 坏\n\n- 症状：x\n"},
         {"docs/known-red-register.md": "## K01 坏\n\n- 首次记录：2026-11-12\n- 转绿条件：修好 x\n- 复跑：python -m pytest tests/x.py\n"},
         match="D5-known-red-incomplete"),
    Case("D6", "文档引用不存在的路径",
         "本轮文档与代码不一致（例如「不改颜色」但代码仍有硬编码颜色）",
         {"docs/x.md": "见 `desktop/nope.py`。\n"},
         {"docs/x.md": "见 `desktop/ok.py`。\n", "desktop/ok.py": "x = 1\n"},
         match="D6-missing-path"),
    Case("A10", "文档引用不存在的提交",
         "docs/推进报告-本轮排错.md:141 引用不存在的 `5d09bb2`",
         {"docs/x.md": "见提交 `5d09bb2`。\n", "desktop/__init__.py": ""},
         {"docs/x.md": "见提交 `{head}`。\n", "desktop/__init__.py": ""},
         True),
    Case("A11", "绿色声明绑定到不存在的树",
         "docs/learn/phase40/ci-notes.md 的绿色记录距 HEAD 752 个提交",
         {"docs/x.md": "本轮全绿 `5d09bb2`。\n"},
         {"docs/x.md": "本轮全绿 `{head}`。\n"},
         True),
]


def run(repo: Repo) -> List[dict]:
    results: List[dict] = []
    for case in CASES:
        try:
            pos = _run_case(case, dict(case.files_pos))
            neg = _run_case(case, dict(case.files_neg))
            ok = len(pos) > 0 and len(neg) == 0
            detail = ""
            if not pos:
                detail = "正例没有变红：检查空过或判定条件失效"
            elif neg:
                detail = f"负例仍然变红：检查在乱报（{len(neg)} 条）"
        except Exception as exc:
            pos, neg, ok = [], [], False
            detail = f"自检崩溃：{type(exc).__name__}: {exc}"
        results.append({
            "rule": case.rule,
            "name": case.name,
            "provenance": case.provenance,
            "positive_hits": len(pos),
            "negative_hits": len(neg),
            "ok": ok,
            "detail": detail,
        })
    results.extend(_extra_checks())
    return results


def _extra_checks() -> List[dict]:
    results: List[dict] = []

    # D8：豁免三要素
    bad = audit_waiver_items([{"rule": "A01", "path": "desktop/x.py", "reason": "暂时"}])
    good = audit_waiver_items([{
        "rule": "A01", "path": "desktop/x.py", "reason": "冻结",
        "review_by": "2027-01-01", "approver": "user",
    }])
    results.append({
        "rule": "D8", "name": "豁免缺到期/批准人", "provenance": "docs/axioms.md 豁免三要素",
        "positive_hits": len(bad), "negative_hits": len(good),
        "ok": len(bad) == 1 and len(good) == 0,
        "detail": "" if len(bad) == 1 and not good else "豁免三要素检查失效",
    })

    # D7：优先级必须把用户可感知损失排在内部整洁之前
    class _A:
        def __init__(self, loss):
            self.loss = loss
            self.freq = 2
            self.evidence = 2
    f_loss = Finding("A01", "desktop/x.py", 1, "", "", "")
    f_clean = Finding("A08", "mbdsdr_ai/x.py", 1, "", "", "", "review")
    order_ok = priority_score(f_loss, _A(3)) > priority_score(f_clean, _A(2))
    results.append({
        "rule": "D7", "name": "损失优先于整洁", "provenance": "docs/axioms.md 派活规则",
        "positive_hits": 1 if order_ok else 0, "negative_hits": 0,
        "ok": order_ok,
        "detail": "" if order_ok else "优先级没有把用户可感知损失排在前面",
    })
    return results
