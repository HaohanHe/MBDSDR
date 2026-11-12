# SPDX-License-Identifier: MIT
"""设备身份的**制度**守卫（不是再验一次行为，而是防止明天又散开）。

上一轮（``20d36199``）把四套身份收进 ``mbdsdr_ai/device_identity.py``，
但那只是「结构」。本文件把它变成「制度」，锁两条：

1. **身份拼写只许有两处实现**：``device_identity.py``（门面/端口身份/拼写
   定义）与 ``sdr_backend.py``（canonical 键的唯一字符串实现）。其它生产
   代码里出现手工拼身份的形状就判红并点名文件行：
   ``|serial:``、``|index:``、``|label:``、``uri:usb:``、``uri:`` 前缀、
   ``f"{driver}:{serial}"``。要防的是「下一个人觉得直接拼一下更快」——
   一旦有人拼出第二套，互斥/面板/热插拔就会再次各认各的设备。
2. **provisional 永不判拔出**：热插拔事件流里出现 ``unplugged`` 且
   ``grade=provisional`` 即判红。理由：``index``/``label`` 会随插拔前移、
   会重名，拿它判拔出等于把「邻居插了一台」报成「你这台拔了」——这正是
   上一版 watchdog 的病根。行为侧用多拍事件流守，构造侧用 AST 守
   （``unplugged`` 必须显式声明 grade，且不得是 provisional）。

扫描范围是生产代码（``mbdsdr_ai/``、``desktop/``，排除 ``tests/`` 与
``__pycache__``）。测试里写死期望键是**规格**（金样），不是第二实现，
故不扫；这与 ``tests/test_error_catalog_guard.py`` 「生产代码里不得出现
野码」同一口径。

已知未迁移的登记已清零：``bladerf_params.py`` 与 ``limesuite_params.py``
原先各有一处手拼占用键，已改调 ``device_identity.describe()``。白名单保留
机制（按文件计处数、必须写理由），但当前为空——它的意义是「暂时登记、
有归属线、有迁移方向」，不是长期允许两套实现。
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = (ROOT / "mbdsdr_ai", ROOT / "desktop")

#: 允许持有身份拼写的两个模块（basename）。
IMPL_BASENAMES = frozenset({"device_identity.py", "sdr_backend.py"})

#: 身份字符串的形状指纹（手工拼/解析都算「形状知识散落」）。
SHAPES = ("|serial:", "|index:", "|label:", "uri:usb:")

#: 已知未迁移：相对路径 -> (允许处数, 理由)。处数而不是布尔，防止白名单
#: 变成黑洞：同文件新增第二处会红。当前已清零（bladerf/lime 两处已改调
#: ``describe()``）；以后登记必须带归属线与迁移方向，不许长期挂着。
_PRE_EXISTING: dict = {}


# ------------------------------------------------------------------ 静态扫描
def _docstring_ids(tree: ast.AST) -> set:
    ids = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            ids.add(id(body[0].value))
    return ids


def _identity_spellings(src: str) -> list:
    """返回 ``[(行号, 形状, 片段)]``：手工拼身份的常量与 f-string。"""
    tree = ast.parse(src)
    docs = _docstring_ids(tree)
    hits: list = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docs:
                continue
            text = node.value
            for shape in SHAPES:
                if shape in text:
                    hits.append((node.lineno, shape, text[:60]))
            if text == "uri:" or text.endswith("uri:"):
                hits.append((node.lineno, "uri:", text[:60]))
        # ``f"{driver}:{serial}"``：DeviceManager 那种拼写也不能有第二份。
        if isinstance(node, ast.JoinedStr):
            parts = node.values
            for i in range(len(parts) - 2):
                left, sep, right = parts[i], parts[i + 1], parts[i + 2]
                if (isinstance(left, ast.FormattedValue)
                        and isinstance(sep, ast.Constant) and sep.value == ":"
                        and isinstance(right, ast.FormattedValue)):
                    head = ast.unparse(left.value).lower()
                    tail = ast.unparse(right.value).lower()
                    if ("driver" in head or "name" in head) and "serial" in tail:
                        hits.append((node.lineno, "driver:serial",
                                     ast.unparse(node)[:70]))
    return hits


def _scan_identity_spellings() -> dict:
    found: dict = {}
    for base in SCAN_DIRS:
        for path in sorted(base.rglob("*.py")):
            rel = path.relative_to(ROOT)
            if "tests" in rel.parts or "__pycache__" in rel.parts:
                continue
            if path.name in IMPL_BASENAMES:
                continue
            hits = _identity_spellings(
                path.read_text(encoding="utf-8", errors="replace"))
            if hits:
                found[rel.as_posix()] = hits
    return found


def test_identity_spelling_only_in_the_two_implementations():
    """身份拼写只许在 device_identity.py / sdr_backend.py 里出现。"""
    found = _scan_identity_spellings()
    bad: list = []
    for rel, hits in sorted(found.items()):
        allowed = _PRE_EXISTING.get(rel, (0, ""))[0]
        for line, shape, snippet in hits[allowed:]:
            bad.append(f"{rel}:{line} -> {shape} :: {snippet}")
    assert not bad, (
        "发现第二套身份拼写（只允许 device_identity.py / sdr_backend.py）：\n"
        + "\n".join(bad)
        + "\n需要身份请调 device_identity.describe()/manager_key()；"
          "别在这里手拼——拼出第二套就会各认各的设备。")


def test_pre_existing_allowlist_is_not_a_dump():
    """白名单不是垃圾桶：每条都要有理由，文件要真实存在。"""
    found = _scan_identity_spellings()
    for rel, (allowed, reason) in _PRE_EXISTING.items():
        assert (ROOT / rel).exists(), f"白名单文件不存在: {rel}"
        assert reason.strip(), f"白名单 {rel} 缺少理由"
        hits = found.get(rel, [])
        assert len(hits) <= allowed, (
            f"{rel} 手工拼身份处数 {len(hits)} 超过登记上限 {allowed}："
            f"{[h[0] for h in hits]}")


def test_unplugged_events_never_carry_provisional_grade():
    """构造侧：``unplugged`` 必须显式声明 grade，且不得是 provisional。

    provisional（index/label）会随插拔前移、会重名：拿它判拔出等于把
    「邻居插了一台」报成「你这台拔了」。这条与行为侧
    ``test_provisional_never_reports_unplugged`` 互为钉子。
    """
    bad: list = []
    for path in sorted((ROOT / "mbdsdr_ai").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if "tests" in path.parts or "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "HotplugEvent"):
                continue
            kwargs = {kw.arg: kw.value for kw in node.keywords}
            kind = kwargs.get("kind")
            kind_name = ""
            if isinstance(kind, ast.Name):
                kind_name = kind.id
            elif isinstance(kind, ast.Attribute):
                kind_name = kind.attr
            elif isinstance(kind, ast.Constant):
                kind_name = str(kind.value)
            if kind_name not in ("EVENT_UNPLUGGED", "unplugged"):
                continue
            if "grade" not in kwargs:
                bad.append(f"{rel}:{node.lineno} unplugged 未显式声明 grade")
                continue
            grade_text = ast.unparse(kwargs["grade"]).lower()
            if "provisional" in grade_text:
                bad.append(f"{rel}:{node.lineno} unplugged 用 provisional "
                           f"判定拔出：{grade_text}")
    assert not bad, (
        "provisional 身份不得判拔出（index/label 会前移、会重名）：\n"
        + "\n".join(bad))


# ------------------------------------------------------------------ 行为扫描
class _OkProbe:
    """假物理探针：不碰真机，结论以枚举为准。"""

    def probe(self):
        from mbdsdr_ai import usb_hotplug as uh
        return uh.Presence(uh.PROBE_OK, authoritative=True)


def _provisional(index: int, label: str = "", driver: str = "rtlsdr") -> dict:
    """无序列号、只有枚举序号：provisional 身份（最容易被误判拔出的一类）。"""
    return {
        "driver": driver,
        "label": label or f"{driver} #{index}",
        "serial": "",
        "device_args": {"index": index},
    }


def _serial(serial: str, index: int = 0, label: str = "") -> dict:
    return {
        "driver": "rtlsdr",
        "label": label or f"RTL {serial}",
        "serial": serial,
        "device_args": {"index": index},
    }


def _unplugged(events) -> list:
    from mbdsdr_ai import usb_hotplug as uh
    return [e for e in events if e.kind == uh.EVENT_UNPLUGGED]


def test_provisional_never_reports_unplugged():
    """provisional 设备消失、序号前移、名字变化，多拍都不得报「拔出」。"""
    from mbdsdr_ai import usb_hotplug as uh

    monitor = uh.HotplugMonitor(probe=_OkProbe())
    p0 = _provisional(0)
    assert monitor.observe([p0]) == []

    events: list = []
    for _ in range(4):                       # 1) 从枚举消失（连续多拍）
        events += monitor.observe([])
    events += monitor.observe([p0])          # 2) 又出现（同序号）
    for _ in range(4):                       # 3) 序号前移后消失（邻居插过）
        events += monitor.observe([_provisional(1)])
    for _ in range(4):                       # 4) 序号再前移，名字也变了
        events += monitor.observe([_provisional(2, label="RTL 换了个位置")])

    assert not _unplugged(events), (
        "provisional 身份被判拔出：index/label 会随插拔前移，"
        f"这等于把邻居插入报成这台拔出；实际事件：{events}")
    assert not [e for e in events
                if e.kind == uh.EVENT_UNPLUGGED
                and e.grade == uh.GRADE_PROVISIONAL]


def test_neighbour_churn_never_reports_the_connected_serial_device():
    """serial 设备在位、旁边的 provisional 设备消失：不许报 serial 那台拔出。"""
    from mbdsdr_ai import usb_hotplug as uh

    monitor = uh.HotplugMonitor(probe=_OkProbe())
    a = _serial("A-0001", index=0)
    p = _provisional(1, label="RTL-P")
    assert monitor.observe([a, p]) == []

    events = monitor.observe([a])            # 邻居 P 从枚举消失
    events += monitor.observe([a])           # 连续多拍也保持同一结论
    assert not _unplugged(events), f"邻居消失被报成本台拔出：{events}"
    assert all(e.key != "rtlsdr|serial:A-0001" for e in events), (
        f"正在用的设备被邻居的插拔牵连：{events}")
