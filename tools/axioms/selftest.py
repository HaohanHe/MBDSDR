"""反证自检：在临时仓库里重放真实违规片段，要求「正例红、负例绿」。

为什么用临时仓库而不是直接在 main 仓上删代码：
- main 仓有并行工作，不能为了自检去改别人的文件；
- 临时仓库仍然走与生产完全相同的检查函数，不是另写一套判定；
- 正例是真实代码片段的逐字复制（provenance 注明来源），负例是对应的修复形态。
正例必须红、负例必须绿；只红不绿说明检查在乱报，只绿不红说明检查空过。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence

from .model import (
    Axiom,
    Finding,
    Repo,
    apply_waivers,
    load_waivers,
    priority_score,
)
from .ratchet import build_baseline, compare, load_baseline, snippet_key, write_baseline
from .survey import Dimension, audit_waiver_items, collect_all, validate_finding_locations


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
        # 与 CLI 同一条路径：豁免由 docs/axioms-waivers.json 显式判定，
        # 已豁免（未过期）的不算红；过期的 apply_waivers 会改回 must-fix 且不 waived。
        waivers = load_waivers(repo)
        want = case.match or case.rule
        out: List[Finding] = []
        for d in dims:
            apply_waivers(d.findings, waivers)
            out.extend(
                f for f in d.findings
                if not f.waived and (f.rule == want or (not case.match and f.rule.startswith(case.rule + "-")))
            )
        return out
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
    Case("A03", "换方法名/包一层不能绕过",
         "`ConfigStore.read()` 这类不叫 load/read_text 的读法（名字白名单的绕过面）",
         {"mbdsdr_ai/store.py": "from pathlib import Path\n\nclass ConfigStore:\n    def __init__(self, root):\n        self._path = Path(root) / 'config.json'\n\n    def _fetch(self):\n        return self._path.read_text()\n\n    def read(self):\n        try:\n            return self._fetch()\n        except Exception:\n            return {}\n"},
         {"mbdsdr_ai/store.py": "import logging\nfrom pathlib import Path\n\nclass ConfigStore:\n    def __init__(self, root):\n        self._path = Path(root) / 'config.json'\n\n    def _fetch(self):\n        return self._path.read_text()\n\n    def read(self):\n        try:\n            return self._fetch()\n        except FileNotFoundError:\n            return {}\n        except Exception as exc:\n            logging.warning('config read failed: %s', exc)\n            return None\n"}),
    Case("A04", "读损坏后仍整文件覆写",
         "mbdsdr_ai/conversation.py 读失败回默认后继续 json.dump",
         {"mbdsdr_ai/store.py": "import json, os\n\ndef load(p):\n    try:\n        return json.load(open(os.path.expanduser(p)))\n    except Exception:\n        return {}\n\ndef save(p, data):\n    json.dump(data, open(os.path.expanduser(p), 'w'))\n"},
         {"mbdsdr_ai/store.py": "import json, os, shutil\n\ndef backup_corrupt(p):\n    shutil.copy(p, p + '.corrupt')\n\ndef load(p):\n    try:\n        return json.load(open(os.path.expanduser(p)))\n    except Exception:\n        backup_corrupt(p)\n        return {}\n\ndef save(p, data):\n    json.dump(data, open(os.path.expanduser(p), 'w'))\n"}),
    Case("A04", "空壳 backup 函数不能关掉检查",
         "旧判据「模块里出现 corrupt|backup 字样就整模块跳过」——命名即开关",
         {"mbdsdr_ai/store.py": "import json, os\n\ndef _backup_corrupt(p):\n    pass\n\ndef load(p):\n    try:\n        return json.load(open(os.path.expanduser(p)))\n    except Exception:\n        return {}\n\ndef save(p, data):\n    json.dump(data, open(os.path.expanduser(p), 'w'))\n"},
         {"mbdsdr_ai/store.py": "import json, os, shutil\n\ndef _backup_corrupt(p):\n    shutil.copy(p, p + '.corrupt')\n\ndef load(p):\n    try:\n        return json.load(open(os.path.expanduser(p)))\n    except Exception:\n        _backup_corrupt(p)\n        return {}\n\ndef save(p, data):\n    json.dump(data, open(os.path.expanduser(p), 'w'))\n"}),
    Case("A04", "pathlib + 改名读法不能绕过",
         "读用 Path.read_text 包在自定义方法里，写用 open(...'w') + json.dump",
         {"mbdsdr_ai/store.py": "import json\nfrom pathlib import Path\n\nclass Store:\n    def __init__(self):\n        self._file = Path.home() / '.mbdsdr' / 'state.json'\n\n    def _grab(self):\n        return self._file.read_text()\n\n    def load(self):\n        try:\n            return json.loads(self._grab())\n        except Exception:\n            return {}\n\n    def save(self, data):\n        with open(self._file, 'w') as f:\n            json.dump(data, f)\n"},
         {"mbdsdr_ai/store.py": "import logging\nfrom pathlib import Path\n\nclass Store:\n    def __init__(self):\n        self._file = Path.home() / '.mbdsdr' / 'state.json'\n\n    def _grab(self):\n        return self._file.read_text()\n\n    def load(self):\n        try:\n            return json.loads(self._grab())\n        except FileNotFoundError:\n            return {}\n        except Exception as exc:\n            logging.warning('state read failed: %s', exc)\n            self._backup()\n            return None\n\n    def _backup(self):\n        import shutil\n        shutil.copy(self._file, str(self._file) + '.corrupt')\n\n    def save(self, data):\n        with open(self._file, 'w') as f:\n            json.dump(data, f)\n"}),
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
    Case("A07", "改名成 panel 也不能当展示",
         "旧判据「接收者名字像控件就放行」——把真实执行路径改个名字即可免检",
         {"desktop/a.py": "def apply(backend, r):\n    if 10 <= r <= 100:\n        backend.set_sample_rate(r)\n",
          "desktop/b.py": "def apply(self, r):\n    self._vfo_panel.set_sample_rate(r)\n"},
         {"desktop/a.py": "def apply(backend, r):\n    if 10 <= r <= 100:\n        backend.set_sample_rate(r)\n",
          "desktop/b.py": "def apply(self, r):\n    self._x = QLabel()\n    self._x.set_sample_rate(r)\n"}),
    Case("A07", "上界写成模块常量也算加了约束",
         "desktop/scanner_panel.py:119 正解是用 desktop/constants.py 的 FREQ_MIN_HZ/FREQ_MAX_HZ；"
         "旧判据只认数字字面量，逼人把 10_000.0/6.0e9 再抄一遍",
         {"desktop/constants.py": "FREQ_MIN_HZ = 10_000.0\nFREQ_MAX_HZ = 6.0e9\n",
          "desktop/a.py": "from desktop import constants\n\ndef apply(backend, r):\n"
                          "    if constants.FREQ_MIN_HZ <= r <= constants.FREQ_MAX_HZ:\n"
                          "        backend.set_frequency(r)\n",
          "desktop/b.py": "def apply(backend, center_hz):\n"
                          "    backend.set_frequency(float(center_hz))\n"},
         {"desktop/constants.py": "FREQ_MIN_HZ = 10_000.0\nFREQ_MAX_HZ = 6.0e9\n",
          "desktop/a.py": "from desktop import constants\n\ndef apply(backend, r):\n"
                          "    if constants.FREQ_MIN_HZ <= r <= constants.FREQ_MAX_HZ:\n"
                          "        backend.set_frequency(r)\n",
          "desktop/b.py": "from desktop.constants import FREQ_MIN_HZ, FREQ_MAX_HZ\n\n"
                          "def apply(backend, center_hz):\n"
                          "    if not (FREQ_MIN_HZ <= center_hz <= FREQ_MAX_HZ):\n"
                          "        return\n"
                          "    backend.set_frequency(float(center_hz))\n"}),
    Case("A07", "上界是运行时值仍报红并注明无法静态确认",
         "`if x < some_runtime_value:` 静态解析不出常量，不能默认放行",
         {"desktop/a.py": "def apply(backend, r):\n    if 10 <= r <= 100:\n        backend.set_frequency(r)\n",
          "desktop/b.py": "LIMIT_HZ = load_limit()\n\ndef apply(backend, r):\n"
                          "    if r <= LIMIT_HZ:\n        backend.set_frequency(r)\n"},
         {"desktop/a.py": "def apply(backend, r):\n    if 10 <= r <= 100:\n        backend.set_frequency(r)\n",
          "desktop/b.py": "LIMIT_HZ = 6e9\n\ndef apply(backend, r):\n"
                          "    if r <= LIMIT_HZ:\n        backend.set_frequency(r)\n"}),
    Case("A07", "常量改成运行时计算后回到红",
         "`FREQ_MAX_HZ = int(os.environ[...])` 不是字面量：登记成常量不等于可静态确认",
         {"desktop/constants.py": "import os\nFREQ_MAX_HZ = int(os.environ.get('FREQ_MAX', '6000000000'))\n",
          "desktop/a.py": "from desktop.constants import FREQ_MAX_HZ\n\ndef apply(backend, r):\n"
                          "    if r <= FREQ_MAX_HZ:\n        backend.set_frequency(r)\n",
          "desktop/b.py": "def apply(backend, r):\n    backend.set_frequency(r)\n",
          "desktop/c.py": "def apply(backend, r):\n    if 10 <= r <= 100:\n        backend.set_frequency(r)\n"},
         {"desktop/constants.py": "FREQ_MAX_HZ = 6.0e9\n",
          "desktop/a.py": "from desktop.constants import FREQ_MAX_HZ\n\ndef apply(backend, r):\n"
                          "    if r <= FREQ_MAX_HZ:\n        backend.set_frequency(r)\n",
          "desktop/b.py": "from desktop.constants import FREQ_MAX_HZ\n\ndef apply(backend, r):\n"
                          "    if r <= FREQ_MAX_HZ:\n        backend.set_frequency(r)\n",
          "desktop/c.py": "def apply(backend, r):\n    if 10 <= r <= 100:\n        backend.set_frequency(r)\n"}),
    Case("A05", "改为无界循环后仍要报",
         "线程体卡在没有 break 的 while True 里（不看阻塞函数叫什么）",
         {"tests/test_t.py": "import threading\n\nclass T:\n    def _pump(self):\n        return 1\n\n    def _run(self):\n        while True:\n            self._pump()\n\n    def test_x(self):\n        t = threading.Thread(target=self._run)\n        t.start()\n        t.join(timeout=1)\n        assert not t.is_alive()\n"},
         {"tests/test_t.py": "import threading\n\nclass T:\n    def _pump(self):\n        return 1\n\n    def _run(self):\n        while True:\n            self._pump()\n            if self._stop:\n                break\n\n    def test_x(self):\n        t = threading.Thread(target=self._run)\n        t.start()\n        t.join(timeout=1)\n        assert not t.is_alive()\n"}),
    Case("A08", "守卫条件恒假",
         "mbdsdr_ai/decoders.py:435 `if False:`",
         {"mbdsdr_ai/d.py": "def f(device):\n    if False:\n        return None\n    return device\n"},
         {"mbdsdr_ai/d.py": "def f(device):\n    if device is None:\n        return None\n    return device\n"}),
    Case("A09", "依赖缺失被登记成环境 skip",
         "tests/test_web_bridge_mcp.py 6 处「MCPToolSet 在此环境不可构造」",
         {"tests/test_x.py": "import pytest\n\ndef test_x():\n    pytest.skip('MCPToolSet 在此环境不可构造')\n"},
         {"tests/test_x.py": "import pytest\n\ndef test_x():\n    assert probe()\n    if not have_bsp():\n        pytest.skip('缺 de421.bsp，未随仓提供')\n"}),
    Case("A12", "同结构不同口径",
         "desktop/main_window.py 的 _fmt_dbfs 与 desktop/readouts.py 的 fmt_dbfs",
         {"desktop/a.py": "def _fmt_level(v):\n    a = 1\n    b = v * a\n"
                          "    c = max(b, -200)\n    return f\"{c:.3f} dBFS\"\n",
          "desktop/b.py": "def render_gain(v):\n    a = 1\n    b = v * a\n"
                          "    c = max(b, -200)\n    return f\"{c:.1f} dBFS\"\n"},
         {"desktop/a.py": "def _fmt_level(v):\n    a = 1\n    b = v * a\n"
                          "    c = max(b, -200)\n    return f\"{c:.3f} dBFS\"\n",
          "desktop/b.py": "def render_gain(v):\n    a = 1\n    b = v * a\n"
                          "    c = max(b, -200)\n    return f\"{c:.3f} dBFS\"\n"}),
    Case("A13", "用名字字段判同一",
         "scripts/file_claim.py:540 `item[\"owner\"] == payload[\"owner\"]`（记录里有 pid）",
         {"scripts/claim.py": "def check(item, payload):\n    rec = {'pid': 1, 'claimant': 'x'}\n"
                              "    if item['claimant'] == payload['claimant']:\n        return True\n    return False\n"},
         {"scripts/claim.py": "def check(item, payload):\n    rec = {'pid': 1, 'claimant': 'x'}\n"
                              "    if item['pid'] == payload['pid']:\n        return True\n    return False\n"}),
    Case("A13", "只有 owner 字样、没有唯一字段记录时必须报无法判定",
         "没有带 pid/serial 的记录时，owner 字样既不能证明违规也不能证明清白；"
         "旧实现按字样直接判违规，仍可换字段名绕过",
         {"scripts/claim.py": "def check(item, payload):\n"
                              "    if item['owner'] == payload['owner']:\n        return True\n"
                              "    return False\n"},
         {"scripts/claim.py": "def check(item, payload):\n"
                              "    rec = {'pid': 1, 'owner': 'x'}\n"
                              "    if item['pid'] == payload['pid']:\n        return True\n"
                              "    return False\n"},
         match="A13-undecidable"),
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
         "desktop 多处各自拼 dB 读数，未登记真值源",
         {"desktop/a.py": "def f(panel, gain):\n    panel.label.setText(f\"{gain:.1f} dB\")\n",
          "desktop/b.py": "def g(panel, gain_db):\n    panel.label.setText(f\"{gain_db:.1f} dB\")\n"},
         {"desktop/a.py": "def f(panel, gain):\n    panel.label.setText(f\"{gain:.1f} dB\")\n"},
         match="D2-multi-producer"),
    Case("D2", "登记一个产生点后只剩另一个违规",
         "docs/truth-sources.json 登记 增益->desktop/a.py；b.py 仍自行拼装",
         {"desktop/a.py": "def format_gain(db):\n    return f\"{db:.1f} dB\"\n\n"
                          "def f(panel, gain):\n    panel.label.setText(f\"{gain:.1f} dB\")\n",
          "desktop/b.py": "def g(panel, gain_db):\n    panel.label.setText(f\"{gain_db:.1f} dB\")\n",
          "docs/truth-sources.json":
              '{"schema": 1, "sources": {"增益": {"producers": ["desktop/a.py"]}}}\n'},
         {"desktop/a.py": "def format_gain(db):\n    return f\"{db:.1f} dB\"\n\n"
                          "def f(panel, gain):\n    panel.label.setText(f\"{gain:.1f} dB\")\n",
          "desktop/b.py": "def g(panel, gain_db):\n    panel.label.setText(f\"{gain_db:.1f} dB\")\n",
          "docs/truth-sources.json":
              '{"schema": 1, "sources": {"增益": {"producers": ["desktop/a.py", "desktop/b.py"]}}}\n'},
         match="D2-multi-producer"),
    Case("D2", "登记指向不存在的文件必须报失真",
         "手改 docs/truth-sources.json 指向一个从未存在的位置",
         {"desktop/a.py": "def format_gain(db):\n    return f\"{db:.1f} dB\"\n",
          "docs/truth-sources.json":
              '{"schema": 1, "sources": {"增益": {"producers": ["desktop/nope.py"]}}}\n'},
         {"desktop/a.py": "def format_gain(db):\n    return f\"{db:.1f} dB\"\n",
          "docs/truth-sources.json":
              '{"schema": 1, "sources": {"增益": {"producers": ["desktop/a.py"]}}}\n'},
         match="D2-truth-source-stale"),
    Case("D3", "同一概念两个名字",
         "仓内 bw / bandwidth 两套命名并存",
         {"desktop/a1.py": "bw = 1\n", "desktop/a2.py": "bw = 2\n",
          "desktop/b1.py": "bandwidth = 1\n", "desktop/b2.py": "bandwidth = 2\n"},
         {"desktop/b1.py": "bandwidth = 1\n", "desktop/b2.py": "bandwidth = 2\n"},
         match="D3-concept-alias"),
    Case("D4", "门禁脚本没有任何入口",
         "scripts/check_*.py 既未接入钩子也未在文档登记",
         {"scripts/check_foo.py": "def main():\n    return 0\n"},
         {"scripts/check_foo.py": "def main():\n    return 0\n",
          "docs/code-guards.md": "运行 `scripts/check_foo.py`。\n"},
         match="D4-silent-gate"),
    Case("D4", "登记只写名字、没有可执行入口",
         "旧判据「门禁名出现在文档任意位置」就算登记：写一行「这个门禁不能用」也能绿",
         {"scripts/check_foo.py": "def main():\n    return 0\n",
          "docs/code-guards.md": "`check_foo.py` 这个门禁不能用，先别接钩子。\n"},
         {"scripts/check_foo.py": "def main():\n    return 0\n",
          "docs/code-guards.md": "手动执行：`python scripts/check_foo.py`。\n"},
         match="D4-gate-unresolvable"),
    Case("D4", "登记指向不存在的脚本",
         "docs/code-guards.md 写 `python scripts/check_missing.py`，该入口在本仓不存在",
         {"scripts/check_foo.py": "def main():\n    return 0\n",
          "docs/code-guards.md": "门禁 `check_foo.py` 由 `python scripts/check_missing.py` 执行。\n"},
         {"scripts/check_foo.py": "def main():\n    return 0\n",
          "docs/code-guards.md": "门禁 `check_foo.py` 由 `python scripts/check_foo.py` 执行。\n"},
         match="D4-gate-unresolvable"),
    Case("D4", "共享库没有任何已登记脚本引用",
         "scripts/guard_common.py 这类只有函数、没有 main() 的共享库：要求它「谁跑」只能逼人编假登记",
         {"scripts/guard_shared.py": "def helper():\n    return 1\n",
          "scripts/check_foo.py": "def main():\n    return 0\n",
          "docs/code-guards.md": "手动执行：`python scripts/check_foo.py`。\n"},
         {"scripts/guard_shared.py": "def helper():\n    return 1\n",
          "scripts/check_foo.py": "from scripts.guard_shared import helper\n\ndef main():\n    return helper()\n",
          "docs/code-guards.md": "手动执行：`python scripts/check_foo.py`。\n"},
         match="D4-unreferenced-lib"),
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
    Case("D6", "路径过期降级为「已迁移」而不是「声明不实」",
         "docs/COORDINATION.md:2354 写 desktop_sdr_session.py 已落，4254 行记着整模块迁到 ai_sdr_session",
         {"docs/x.md": "已落：`mbdsdr_ai/desktop_sdr_session.py`。\n\n"
                       "| 模块 | 结论 |\n| --- | --- |\n"
                       "| `desktop_sdr_session.py:38` | 整模块迁 `desktop/ai_sdr_session.py` |\n",
          "desktop/ai_sdr_session.py": "x = 1\n"},
         {"docs/x.md": "已落：`desktop/ai_sdr_session.py`。\n",
          "desktop/ai_sdr_session.py": "x = 1\n"},
         match="D6-path-moved"),
    Case("D6", "从未存在过的模块仍报声明不实",
         "文档点名一个仓里从来没有的模块，又没有迁移记录：不能降级成「路径过期」",
         {"docs/x.md": "见 `mbdsdr_ai/ghost_module.py`。\n\n"
                       "| `ghost_module.py:38` | 待查 |\n"},
         {"docs/x.md": "见 `mbdsdr_ai/ghost_module.py`。\n",
          "mbdsdr_ai/ghost_module.py": "x = 1\n"},
         match="D6-missing-path"),
    Case("A14", "台账只用行号定位",
         "desktop/main_window.py 改十几行后，豁免 :8327/:12761/:14455 全部失效",
         {"docs/known-red-register.md": "# K01\n\n- 位置：`desktop/ok.py:12`\n",
          "desktop/ok.py": "def apply():\n    return 1\n"},
         {"docs/known-red-register.md": "# K01\n\n- 位置：`desktop/ok.py:12` 的 `apply()`\n",
          "desktop/ok.py": "def apply():\n    return 1\n"}),
    Case("A14", "台账引用了不存在的符号",
         "符号改名/删除后，台账仍指向旧函数名",
         {"docs/known-red-register.md": "# K01\n\n- 位置：`desktop/ok.py:12` 的 `apply_all()`\n",
          "desktop/ok.py": "def apply():\n    return 1\n"},
         {"docs/known-red-register.md": "# K01\n\n- 位置：`desktop/ok.py:12` 的 `apply()`\n",
          "desktop/ok.py": "def apply():\n    return 1\n"}),
    Case("A10", "文档引用不存在的提交",
         "docs/推进报告-本轮排错.md:141 引用不存在的 `5d09bb2`",
         {"docs/x.md": "见提交 `5d09bb2`。\n", "desktop/__init__.py": ""},
         {"docs/x.md": "见提交 `{head}`。\n", "desktop/__init__.py": ""},
         True),
    Case("A11", "绿色声明绑定到不存在的树",
         "docs/learn/phase40/ci-notes.md 的绿色记录距 HEAD 752 个提交",
         {"docs/x.md": "本轮全绿 `5d09bb2`。\n"},
         {"docs/x.md": "本轮全绿 `{head}`。\n转绿条件：4 条全部通过（动态 tooltip 三项）。\n"},
         True),
    Case("A11", "历史快照目录不再是免检开关",
         "docs/incidents/ 里的绿色声明没有命令/提交号/日期：目录本身不构成豁免"
         "（旧的 ARCHIVE_DOC 按路径直接跳过整篇）",
         {"docs/incidents/x.md": "本轮全绿。\n"},
         {"docs/incidents/x.md": "本轮全绿，见 `pytest -q tests/`。\n"},
         match="A11"),
    Case("A11", "显式豁免登记才免检",
         "docs/axioms-waivers.json 按文件登记（文件 + 理由 + 到期 + approver），"
         "过期后重新计入违规",
         {"docs/incidents/x.md": "本轮全绿。\n",
          "docs/axioms-waivers.json": json.dumps({"version": 1, "waivers": [{
              "rule": "A11", "path": "docs/incidents/x.md", "reason": "历史快照",
              "review_by": "2000-01-01", "approver": "user"}]}, ensure_ascii=False)},
         {"docs/incidents/x.md": "本轮全绿。\n",
          "docs/axioms-waivers.json": json.dumps({"version": 1, "waivers": [{
              "rule": "A11", "path": "docs/incidents/x.md", "reason": "历史快照",
              "review_by": "2999-01-01", "approver": "user"}]}, ensure_ascii=False)},
         match="A11"),
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

    results.append(_ratchet_check())
    results.append(_locator_drift_check())
    results.append(_cycle_representative_check())
    results.append(_finding_type_check())
    results.append(_location_check())
    return results


def _location_check() -> dict:
    """finding 指向的路径/行号必须属于被分析的树，否则必须改写并升级。

    历史事故：报告里出现过本仓、全部 worktree、`git log --all` 都不存在的
    路径。判据必须能自己证明「我报的东西在这棵树里」。
    """
    root = tempfile.mkdtemp(prefix="mbdsdr-axiom-loc-")
    try:
        os.makedirs(os.path.join(root, "desktop"), exist_ok=True)
        with open(os.path.join(root, "desktop", "a.py"), "w", encoding="utf-8", newline="\n") as f:
            f.write("x = 1\ny = 2\n")
        dim = Dimension("D6", "目录与文档结构", "")
        dim.findings = [
            Finding("D6-ok", "desktop/a.py", 2, "", "", ""),
            Finding("D6-ghost", "desktop/nope.py", 0, "", "", ""),
            Finding("D6-far", "desktop/a.py", 99, "", "", ""),
        ]
        counts = validate_finding_locations(Repo(root), [dim])
        rules = [f.rule for f in dim.findings]
        ok = (
            counts["unknown-path"] == 1
            and counts["line-out-of-range"] == 1
            and rules == ["D6-ok", "D6-ghost-unknown-path", "D6-far-line-out-of-range"]
            and dim.findings[1].severity == "must-fix"
        )
        return {
            "rule": "R1", "name": "finding 位置必须存在于被分析树",
            "provenance": "报告出现两个全历史都不存在的路径（skyfield_compat / _PHASE9_FINAL_SUMMARY）",
            "positive_hits": 2, "negative_hits": 0,
            "ok": ok,
            "detail": "" if ok else f"位置校验失效：counts={counts} rules={rules}",
        }
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _finding_type_check() -> dict:
    """Finding 构造时 waived/severity 必须类型正确，否则立即报错。

    历史事故：D1-cycle 多传一个位置参数，waived 变成字符串 "review"（真值），
    所有 import 环被当成已豁免，报告与棘轮同时看不见。这里用「必须抛错」来反证。
    """
    rejected: List[str] = []
    cases = [
        ("waived 传字符串", lambda: Finding("D1-cycle", "mbdsdr_ai/x.py", 1, "", "", "", "review", "review")),
        ("waived 传 1", lambda: Finding("D1-cycle", "mbdsdr_ai/x.py", 1, "", "", "", "must-fix", 1)),
        ("severity 传真值", lambda: Finding("D1-cycle", "mbdsdr_ai/x.py", 1, "", "", "", True)),
    ]
    for name, build in cases:
        try:
            build()
        except (TypeError, ValueError):
            rejected.append(name)
    ok = len(rejected) == len(cases)
    return {
        "rule": "R1", "name": "Finding 字段类型硬校验",
        "provenance": "D1-cycle 参数错位导致整类判据被当成已豁免",
        "positive_hits": len(rejected), "negative_hits": 0,
        "ok": ok,
        "detail": "" if ok else f"这些非法构造没有报错：{sorted(set(c[0] for c in cases) - set(rejected))}",
    }


def _cycle_representative_check() -> dict:
    """D1-cycle 的代表模块必须是环里字典序最小的模块，不能随遍历/哈希顺序变。

    代表模块一变，棘轮就把同一个环报成「新增 + 消失」——2026-11-13 实测发生过。
    """
    a = {"mbdsdr_ai/aaa.py": "import mbdsdr_ai.zzz\n", "mbdsdr_ai/zzz.py": "import mbdsdr_ai.aaa\n",
         "desktop/__init__.py": ""}
    b = dict(reversed(list(a.items())))
    seen: List[List[str]] = []
    for files in (a, b):
        root = tempfile.mkdtemp(prefix="mbdsdr-cycle-")
        try:
            _write_tree(root, files)
            _, dims = collect_all(Repo(root))
            seen.append(sorted(f.path for d in dims for f in d.findings if f.rule == "D1-cycle"))
        finally:
            shutil.rmtree(root, ignore_errors=True)
    ok = seen[0] == seen[1] == ["mbdsdr_ai/aaa.py"]
    return {
        "rule": "D1", "name": "环的代表模块稳定", "provenance": "棘轮在干净树上首跑即红：同一环换了代表模块",
        "positive_hits": len(seen[0]), "negative_hits": len(seen[1]),
        "ok": ok, "detail": "" if ok else f"两种遍历顺序下代表模块不同：{seen}",
    }


def _locator_drift_check() -> dict:
    """A14 反证：目标文件顶部插入 20 行空行后，按符号定位的引用必须仍然通过。

    如果插入空行后变红，说明那条判据还是靠行号。
    """
    ledger = "# K01\n\n- 位置：`desktop/ok.py:12` 的 `apply()`\n"
    target = "def apply():\n    return 1\n"
    root = tempfile.mkdtemp(prefix="mbdsdr-locator-")
    try:
        _write_tree(root, {"desktop/ok.py": target, "docs/known-red-register.md": ledger})
        before = [f for d in collect_all(Repo(root))[1] for f in d.findings if f.rule == "A14"]
        _write_tree(root, {"desktop/ok.py": "\n" * 20 + target})
        after = [f for d in collect_all(Repo(root))[1] for f in d.findings if f.rule == "A14"]
        # 台账常写裸文件名（`main_window.py:3679`）：能唯一解析到 desktop/ok.py，
        # 不能因为「不是仓内相对路径」就报模块不存在。
        _write_tree(root, {"docs/known-red-register.md": "- 位置：`ok.py:12` 的 `apply()`\n"})
        bare = [f for d in collect_all(Repo(root))[1] for f in d.findings if f.rule == "A14"]
        ok = not before and not after and not bare
        detail = ""
        if before:
            detail = "按符号定位的引用被误报（符号存在但检查没认出来）"
        elif after:
            detail = "目标文件插入 20 行空行后引用变红：这条判据仍然依赖行号"
        elif bare:
            detail = "裸文件名（唯一可解析）被误报成模块不存在"
        return {
            "rule": "A14", "name": "行号漂移不影响符号定位",
            "provenance": "desktop/main_window.py 改动导致豁免 :8327/:12761/:14455 全部失效",
            "positive_hits": len(before), "negative_hits": len(after),
            "ok": ok, "detail": detail,
        }
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _ratchet_check() -> dict:
    """棘轮自检：新增即红、删掉即绿、换一条同数量的另一条不得互相抵消。"""
    root = tempfile.mkdtemp(prefix="mbdsdr-ratchet-")
    probe = "mbdsdr_ai/ratchet_probe.py"
    probe2 = "mbdsdr_ai/ratchet_probe2.py"
    code = ("import json\n\ndef load(p):\n    try:\n        return json.load(open(p))\n"
            "    except Exception:\n        pass\n")
    code2 = ("import json\n\ndef load2(p):\n    try:\n        return json.load(open(p))\n"
             "    except Exception:\n        return None\n")
    try:
        _write_tree(root, {"desktop/__init__.py": ""})
        repo = Repo(root)
        _, dims = collect_all(repo)
        base = build_baseline(repo, [f for d in dims for f in d.findings], "fixture")
        write_baseline(repo, "docs/base.json", base)
        loaded = load_baseline(repo, "docs/base.json")

        _write_tree(root, {probe: code})
        _, dims = collect_all(repo)
        added = compare([f for d in dims for f in d.findings], loaded)
        red_ok = any(f.path == probe and f.rule == "A03" for _, f in added.new)

        _write_tree(root, {probe: ""})
        os.remove(os.path.join(root, probe))
        _, dims = collect_all(repo)
        removed = compare([f for d in dims for f in d.findings], loaded)
        green_ok = not removed.new and not removed.gone

        # 同数量替换：先把 probe 记为基线，再「删 probe、加 probe2」。
        # 数量仍是 1 对 1，但指纹不同，必须同时报出「新增 1 / 消失 1」。
        _write_tree(root, {probe: code})
        _, dims = collect_all(repo)
        base2 = build_baseline(repo, [f for d in dims for f in d.findings], "fixture2")
        os.remove(os.path.join(root, probe))
        _write_tree(root, {probe2: code2})
        _, dims = collect_all(repo)
        swapped = compare([f for d in dims for f in d.findings], base2)
        swap_ok = (
            any(f.path == probe2 for _, f in swapped.new)
            and any(str(e.get("path")) == probe for _, e in swapped.gone)
        )

        # 片段里内嵌的「对方文件:行号」不得影响指纹：对方挪一行也不能造出新条目。
        drift_ok = (
            snippet_key("file_to_vcdus 与 mbdsdr_ai/fengyun_sat.py:286 同名") ==
            snippet_key("file_to_vcdus 与 mbdsdr_ai/fengyun_sat.py:817 同名")
        )

        ok = red_ok and green_ok and swap_ok and drift_ok
        detail = ""
        if not red_ok:
            detail = "新增静默 except: pass 没有变红"
        elif not green_ok:
            detail = "删掉违规文件后仍然红（或出现幻影消失）"
        elif not swap_ok:
            detail = "同数量替换被抵消：棘轮按数量而不是按指纹"
        elif not drift_ok:
            detail = "片段内的文件行号参与了指纹：对方文件挪一行就会报假新增"
        return {
            "rule": "R1", "name": "棘轮：新增即红/删掉即绿/不可抵消",
            "provenance": "docs/axioms.md 棘轮规则",
            "positive_hits": 1 if red_ok else 0,
            "negative_hits": 0 if green_ok else 1,
            "ok": ok, "detail": detail,
        }
    finally:
        shutil.rmtree(root, ignore_errors=True)
