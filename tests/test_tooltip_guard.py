"""tooltip 文案守门测试（docs/discoverability.md「tooltip 三条规矩」）。

规矩（新增文案必须满足，历史文案进基线不追溯）：
  1. 说清「这是什么 + 什么时候用」；
  2. 能给出下一步就给出；
  3. 不重复控件名、不写实现细节。

可机械校验的部分：
  * 新增字面量 tooltip 长度必须 >= 12 个字符（不含首尾空白）；
  * tooltip 不得与同一函数内该控件可见文字（setText / 构造入参 / 表单标签）
    逐字相同。

历史项走 ``docs/tooltip-baseline.json`` 基线白名单，**只降不升**：
  * 出现基线里没有的新违规 → 红（不许新增历史项豁免）；
  * 基线里记的项已经改好 → 也红（提示把基线改小，而不是留着不管）。

反证：把任意一处 tooltip 改回「解调模式」这种短名词、或把文案改成与控件
可见文字逐字相同，本文件立刻红；修好后必须同步下调基线才绿。
"""

import ast
import json
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DESKTOP = os.path.join(_ROOT, "desktop")
_BASELINE = os.path.join(_ROOT, "docs", "tooltip-baseline.json")

_MIN_CHARS = 12
_WIDGET_CLASSES = {"QPushButton", "QLabel", "QCheckBox", "QGroupBox", "QAction",
                   "QToolButton", "QRadioButton"}


def _literal(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _class_name(call):
    fn = call.func
    if isinstance(fn, ast.Name):
        return fn.id
    if isinstance(fn, ast.Attribute):
        return fn.attr
    return None


def _receiver(node):
    return ast.unparse(node)


def _visible_names(func_node):
    """收集函数内「控件 → 可见文字」：构造入参 / setText / 表单标签。"""
    names = {}
    for node in ast.walk(func_node):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
            if (isinstance(value, ast.Call) and _class_name(value) in _WIDGET_CLASSES
                    and value.args):
                lit = _literal(value.args[0])
                if lit:
                    names[_receiver(target)] = lit.strip()
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            attr = node.func.attr
            if attr in ("setText", "setTitle") and node.args:
                lit = _literal(node.args[0])
                if lit:
                    names[_receiver(node.func.value)] = lit.strip()
            elif attr == "addRow" and len(node.args) == 2:
                label = _literal(node.args[0])
                if label:
                    names.setdefault(_receiver(node.args[1]), label.strip())
    return names


def scan_tooltips():
    """扫描 desktop/ 下所有字面量 setToolTip。

    返回 (全部字面量条目, 违规条目)。条目为 dict：
    {file, line, text, reason}；reason: too_short / same_as_name。
    """
    all_items, violations = [], []
    for name in sorted(os.listdir(_DESKTOP)):
        if not name.endswith(".py"):
            continue
        path = os.path.join(_DESKTOP, name)
        rel = "desktop/" + name
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=path)
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            names = _visible_names(func)
            for node in ast.walk(func):
                if not (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "setToolTip"):
                    continue
                text = _literal(node.args[0]) if node.args else None
                if text is None:
                    continue  # 动态文案（f 字符串等）无法静态取字面量
                text = text.strip()
                if not text:
                    continue  # setToolTip("") 是清空提示，不是一段文案
                item = {"file": rel, "line": node.lineno, "text": text}
                all_items.append(item)
                same = names.get(_receiver(node.func.value), "")
                if len(text) < _MIN_CHARS:
                    violations.append(dict(item, reason="too_short"))
                elif same and text == same:
                    violations.append(dict(item, reason="same_as_name"))
    return all_items, violations


def _fingerprint(item):
    return f"{item['file']}::{item['text']}::{item['reason']}"


def _load_baseline():
    with open(_BASELINE, encoding="utf-8") as fh:
        return json.load(fh)["entries"]


def test_tooltip_baseline_only_shrinks():
    _, violations = scan_tooltips()
    baseline = set(_fingerprint(x) for x in _load_baseline())
    current = set(_fingerprint(x) for x in violations)
    new = current - baseline
    gone = baseline - current
    assert not new, (
        "新增了不合规 tooltip（长度 <12 或与控件可见文字逐字相同）：\n  "
        + "\n  ".join(sorted(new))
        + "\n请按 docs/discoverability.md 三条规矩改写，不要加进基线。")
    assert not gone, (
        "基线里的以下不合规项已经改好，请把 docs/tooltip-baseline.json 同步改小：\n  "
        + "\n  ".join(sorted(gone)))


def test_baseline_file_stays_in_sync_with_counter():
    """基线条数 == 当前违规数（防止只改基线数字不改条目）。"""
    _, violations = scan_tooltips()
    assert len(_load_baseline()) == len(violations)


def test_upgraded_tooltips_meet_the_rules():
    """本次改写的 20 处必须真的落地：任一处退回旧文案即红。"""
    texts = {item["text"] for item in scan_tooltips()[0]}
    upgraded = [
        "切换当前信号的解调方式；听模拟语音用 FM/NFM，听短波/航空用 AM",
        "现在使用的解调方式；要切换可用顶栏下拉，频谱获焦时按 A/N/F/W/C 更直接",
        "现在没有正在跟踪的卫星，所以此项目前不可用；先到「卫星跟踪」页签选一颗卫星再开始跟踪",
        "说明本设备能不能发射；显示「驱动级已实现」只代表代码支持，未经真机验证前请谨慎发射",
        "每秒采集的样本数；越高能看的带宽越宽，默认值通常够用",
        "接收放大倍数；信号弱就调大，过强出现削顶就调小，开启硬件 AGC 时此项不生效",
        "选择要解码的录制文件：IQ 录制用 .iq/.cfile/.raw，NOAA APT 录音用 .wav",
        "卫星下行信号的中心频率（MHz）；选好制式会自动填入，手动改动前先确认",
        "卫星信号的符号率（ksps）；要与所选制式匹配，不确定就用制式默认值",
        # 单位按产品单一事实源写成 MS/s（weather_panel.py:493 的输入标签
        # 也是「采样率 (MS/s)」），不是早期草稿里的 Msps；文案其余部分
        # 与「这是什么 + 什么时候用 + 下一步」三条规矩一字未改。
        "录制文件的采样率（MS/s）；必须与录制时一致，不一致会解码失败",
        "开始解码当前文件或实时信号；完成后右侧出图，失败时下方会写明原因",
        "停止当前解码；已经生成的图会保留，想重来再点「开始解码」",
        # 三处产品文案后来被改得更准确（NTRIP 说清 Caster 是什么、频率
        # 控件补上「未接后端时禁用」与「范围以设备为准」）。守门仍逐字钉
        # 完整落地文案，只是把期望值追平产品，不放宽成「长度够就行」。
        "Caster 是分发 NTRIP 差分改正数的服务器；这里填它的域名或 IP（如 ntrip.example.com），不清楚就问服务商，端口通常是 2101",
        "服务器端口；NTRIP 服务默认 2101，服务商另有说明时按说明填",
        "勾选后由设备自动调增益；信号忽强忽弱时开，想手动控增益就关",
        "设备中心频率（MHz）；改完立即生效，实际可调范围看左侧设备信息。当前调谐未接入后端时控件禁用，显示值不是设备读数",
        "跟踪时调谐到的下行频率（MHz）；实际可调范围以当前 SDR 设备为准；先用所选制式的默认值，通联确认后再微调",
        "拖动调节收听音量；与控制面板的音量是同一个值，需要静音可按 Ctrl+M",
    ]
    for text in upgraded:
        assert text in texts, f"文案未落地：{text}"
    # 下面两处是动态拼接/f 字符串，静态扫描取不到，改为源码存在性校验
    src = open(os.path.join(_DESKTOP, "main_window.py"), encoding="utf-8").read()
    assert "失败时状态栏会显示真实错误原因" in src
    dock = open(os.path.join(_DESKTOP, "dock_layout_manager.py"), encoding="utf-8").read()
    assert "面板：点击显示或隐藏" in dock
