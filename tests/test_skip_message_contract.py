#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""skip 消息契约守卫：硬件类 skip 必须写清三件事。

判据来源 `docs/evidence-standard.md` §2 附则：skip 是「这台机器不具备条件」，
不是「这条断言现在不成立」。所以每条硬件 skip 的消息都得让人能判断：该去修
环境，还是该来查代码。只断言「消息非空」不够——非空但含糊（「无设备」
「跳过硬件测试」）正是要消掉的现状，它既不能让人判断该不该修环境，也分不开
「真没插」和「插了但打不开」。

约定：硬件类 skip 的消息必须同时含三个标签——

  * `缺：`   —— 缺什么（设备 / 服务 / 环境变量 / 屏幕宽度，取实测值）；
  * `可跑：` —— 怎么让它可跑（插什么、装什么、设哪个环境变量、跑哪条命令）；
  * `区分：` —— 区分「没插」与「插了但打不开」，或区分「环境不凑巧」与
                「断言已经不再成立」（后者必须红，不该出现在 skip 里）。

登记表 `_SITES` 是显式的：(文件, 含 skip 的函数名)。新增硬件 skip 时把锚点
加进来——不登记等于「不知道它在不在」，那正是这条守卫要防的状态。
注意 `tests/test_listen_state_machine.py` 原来 4 处「无音频设备，AudioPlayer
降级为 None」不在此表：查下来 `_audio_player is None` 只可能是本仓组件导入/
构造失败（没声卡时 AudioPlayer 仍存在、只是 available=False），已按依赖缺失
就地改红。
"""
import ast
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

LABELS = ("缺：", "可跑：", "区分：")

_SITES = [
    ("tests/sdr_toolchain.py", "connect_first_device_or_skip"),
    ("tests/test_sdr_pipeline.py", "test_available_path_when_device_present"),
    ("tests/test_ui_hard_defects.py", "test_stretch_gives_left_the_flex"),
    ("tests/test_ui_layout_locks.py", "test_window_size_is_run_order_independent"),
    ("mbdsdr_ai/tests/test_rtlsdr_ctypes_fallback.py",
     "test_real_device_enumeration_shape"),
    ("mbdsdr_ai/tests/test_sdr_rtltcp_registration.py",
     "test_registered_backend_connects_to_live_rtl_tcp"),
    ("mbdsdr_ai/tests/test_sdr_rtltcp_registration.py",
     "test_device_manager_open_rtl_tcp_live"),
    ("tests/test_satdump_live_envelope.py",
     "test_missing_satdump_is_failure_not_success"),
    ("tests/test_satellite_tracker.py", "test_sgp4_crosscheck_skyfield"),
    ("tests/test_tool_param_fuzz_guard.py", "test_fuzz_memory_cap_aborts_case"),
]


def _find_func(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _text(node):
    """把 skip 的参数还原成可读文本；f-string 的插值处记为 `{}`。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            str(v.value) if isinstance(v, ast.Constant) else "{}" for v in node.values
        )
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _text(node.left) + _text(node.right)
    return ""


def _skip_messages(func):
    msgs = []
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        nm = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
        if nm != "skip":
            continue
        msgs.append(_text(node.args[0]) if node.args else "")
    return msgs


def test_hardware_skip_messages_state_missing_and_how_to_run():
    problems = []
    for rel, fname in _SITES:
        path = os.path.join(ROOT, rel)
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        func = _find_func(tree, fname)
        if func is None:
            problems.append(f"{rel}::{fname}：登记的函数不存在——先修登记表")
            continue
        msgs = _skip_messages(func)
        if not msgs:
            problems.append(
                f"{rel}::{fname}：登记为硬件 skip，但函数里已没有 pytest.skip——"
                "要么它变成了红，要么锚点漂了，先修登记表")
            continue
        for idx, msg in enumerate(msgs, 1):
            lacking = [label for label in LABELS if label not in msg]
            if lacking:
                problems.append(
                    f"{rel}::{fname} 第 {idx} 条 skip 缺标签 "
                    f"{'/'.join(lacking)}：{msg[:70]!r}")

    assert not problems, (
        "硬件类 skip 的消息没写清「缺什么 / 怎么让它可跑 / 区分」：\n  - "
        + "\n  - ".join(problems)
        + "\n修法：消息里同时给出 `缺：`、`可跑：`、`区分：` 三个标签，"
        "「区分」要能分开「没插」与「插了但打不开」；若查下来不是环境问题"
        "（本仓该有的依赖导不进来等），按 docs/evidence-standard.md §2 附则改红，"
        "并从 _SITES 移除。"
    )
