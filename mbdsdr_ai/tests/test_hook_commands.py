# SPDX-License-Identifier: MIT
"""外部命令 hooks 测试（2026-10-14，kimi-code 审计 Top-3 完整形态）。

进程内 PreToolUse 钩子（test_tool_hooks.py）之后的第二批：配置驱动的
外部命令 hook（hook_commands.HookCommandRunner）。本测试钉住：

① exit code 2 否决（真起子进程，sys.executable -c）；
② stdout JSON {"decision": "deny", ...} 否决（reason / message 两字段）；
③ exit 0 放行、stdout 非 deny 不拦；
④ 超时 fail-open + stderr 留痕（timeout_s=1，子进程 sleep 5）；
⑤ 配置缺失 / 坏 JSON / 未知 event / 坏 command 数组——诚实跳过不炸；
⑥ MBDSDR_HOOKS_CONFIG 覆盖路径（环境变量 vs 显式 path 优先级）；
⑦ MCPToolSet 集成：命令 veto 时 stdin 事件 JSON 含 tool/arguments/write；
⑧ 配置 mtime 变化触发重载（复用 skill_registry 缓存思路的回归钉）。

所有配置与命令脚本一律落在 tmp_path / 重定向 USERPROFILE，不触碰真实
~/.mbdsdr；子进程为真起（不 mock subprocess），fail-open 语义按实测钉死。
"""
from __future__ import annotations

import json
import os
import sys
import time

from mbdsdr_ai.core.mcp_server import MCPToolSet
from mbdsdr_ai.hook_commands import (DEFAULT_TIMEOUT_S, HOOKS_ENV,
                                      SUPPORTED_EVENTS, HookCommandRunner)
from mbdsdr_ai.hooks import Event, EventType

_PRE = EventType.AGENT_PRE_TOOL_USE


# ---------------------------------------------------------------------------
# 助手
# ---------------------------------------------------------------------------
def _write_config(tmp_path, hooks, name: str = "hooks.json") -> str:
    cfg = tmp_path / name
    cfg.write_text(json.dumps({"hooks": hooks}, ensure_ascii=False),
                   encoding="utf-8")
    return str(cfg)


def _rule(code: str, timeout_s=None, event=_PRE) -> dict:
    """一条 external hook 规则：command 用 sys.executable -c 跑 code。"""
    rule = {"event": event, "command": [sys.executable, "-c", code]}
    if timeout_s is not None:
        rule["timeout_s"] = timeout_s
    return rule


def _event() -> Event:
    return Event(event_type=_PRE,
                 data={"tool": "set_frequency",
                       "arguments": {"hz": 98_500_000},
                       "write": True},
                 source="test")


def _touch(path: str, delta_s: float = 120.0) -> None:
    """显式把文件 mtime 推到未来（绕开文件系统 mtime 粒度不确定性）。"""
    t = time.time() + delta_s
    os.utime(path, (t, t))


# ---------------------------------------------------------------------------
# ① exit 2 否决
# ---------------------------------------------------------------------------
def test_exit_2_vetoes(tmp_path):
    runner = HookCommandRunner(path=_write_config(
        tmp_path, [_rule("import sys; sys.exit(2)")]))
    verdict = runner.decide(_event())
    assert verdict is not None
    assert verdict["reason"]


# ---------------------------------------------------------------------------
# ② stdout JSON deny 否决
# ---------------------------------------------------------------------------
def test_stdout_json_deny_reason(tmp_path):
    code = ("import json;"
            "print(json.dumps({'decision': 'deny',"
            "                  'reason': '外部脚本禁止调谐'}))")
    runner = HookCommandRunner(path=_write_config(tmp_path, [_rule(code)]))
    assert runner.decide(_event()) == {"reason": "外部脚本禁止调谐"}


def test_stdout_json_deny_message_field(tmp_path):
    code = ("import json;"
            "print(json.dumps({'decision': 'deny',"
            "                  'message': 'message 字段理由'}))")
    runner = HookCommandRunner(path=_write_config(tmp_path, [_rule(code)]))
    assert runner.decide(_event()) == {"reason": "message 字段理由"}


# ---------------------------------------------------------------------------
# ③ exit 0 放行且 stdout 非 deny 不拦
# ---------------------------------------------------------------------------
def test_exit_0_and_non_deny_stdout_allow(tmp_path):
    code = ("import json;"
            "print(json.dumps({'decision': 'allow', 'note': 'just watching'}))")
    runner = HookCommandRunner(path=_write_config(tmp_path, [_rule(code)]))
    assert runner.decide(_event()) is None          # 放行、不拦
    # 非 JSON stdout 同样放行（exit 2 才是强信号，杂散输出不应当拦）
    runner2 = HookCommandRunner(path=_write_config(
        tmp_path, [_rule("print('not json at all')")],
        name="hooks2.json"))
    assert runner2.decide(_event()) is None


# ---------------------------------------------------------------------------
# ④ 超时放行 + stderr 留痕
# ---------------------------------------------------------------------------
def test_timeout_allows_with_stderr(tmp_path, capsys):
    runner = HookCommandRunner(path=_write_config(
        tmp_path, [_rule("import time; time.sleep(5)", timeout_s=1)]))
    assert runner.decide(_event()) is None          # fail-open：不拦截
    err = capsys.readouterr().err
    assert "超时" in err                            # stderr 留痕可查


# ---------------------------------------------------------------------------
# ⑤ 坏配置 / 未知 event / 坏 command——诚实跳过不炸
# ---------------------------------------------------------------------------
def test_missing_config_is_silent_noop(tmp_path, capsys):
    runner = HookCommandRunner(path=str(tmp_path / "nope.json"))
    assert runner.decide(_event()) is None          # 缺失 = 空转
    assert capsys.readouterr().err == ""            # 不制造噪声


def test_bad_json_skips_with_stderr(tmp_path, capsys):
    cfg = tmp_path / "hooks.json"
    cfg.write_text("{not json", encoding="utf-8")
    runner = HookCommandRunner(path=str(cfg))
    assert runner.decide(_event()) is None
    assert "读取" in capsys.readouterr().err        # 诚实留痕


def test_unknown_event_skips_with_stderr(tmp_path, capsys):
    cfg = _write_config(tmp_path, [
        _rule("import sys; sys.exit(2)", event="agent.tool_result")])
    runner = HookCommandRunner(path=cfg)
    assert runner.decide(_event()) is None           # 不执行、不拦
    err = capsys.readouterr().err
    assert "agent.tool_result" in err                # 明说哪里不支持


def test_bad_command_array_skips_with_stderr(tmp_path, capsys):
    cfg = _write_config(tmp_path, [
        {"event": _PRE, "command": "python check.py"},   # 字符串而非数组
        {"event": _PRE, "command": []},                  # 空数组
        {"event": _PRE, "command": ["py", 3]},           # 非字符串元素
        "not-a-dict",                                    # 非对象
    ])
    runner = HookCommandRunner(path=cfg)
    assert runner.decide(_event()) is None
    err = capsys.readouterr().err
    assert "command" in err                             # 逐条说明


def test_hooks_not_array_skips_with_stderr(tmp_path, capsys):
    cfg = tmp_path / "hooks.json"
    cfg.write_text(json.dumps({"hooks": "nope"}), encoding="utf-8")
    runner = HookCommandRunner(path=str(cfg))
    assert runner.decide(_event()) is None
    assert "必须是数组" in capsys.readouterr().err


def test_other_nonzero_exit_allows_with_stderr(tmp_path, capsys):
    code = "import sys; print('boom', file=sys.stderr); sys.exit(1)"
    runner = HookCommandRunner(path=_write_config(tmp_path, [_rule(code)]))
    assert runner.decide(_event()) is None          # 非 0/2：放行
    err = capsys.readouterr().err
    assert "退出码 1" in err


def test_missing_command_allows_with_stderr(tmp_path, capsys):
    runner = HookCommandRunner(path=_write_config(
        tmp_path, [{"event": _PRE,
                    "command": ["mbdsdr_no_such_binary_xyz"]}]))
    assert runner.decide(_event()) is None           # 启动失败：放行
    err = capsys.readouterr().err
    assert "启动" in err and "放行" in err


# ---------------------------------------------------------------------------
# ⑥ MBDSDR_HOOKS_CONFIG 覆盖路径
# ---------------------------------------------------------------------------
def test_env_override_path(tmp_path, monkeypatch):
    cfg = _write_config(tmp_path, [_rule("import sys; sys.exit(2)")])
    monkeypatch.setenv(HOOKS_ENV, cfg)
    assert HookCommandRunner().decide(_event()) is not None  # 走 env 路径


def test_explicit_path_beats_env(tmp_path, monkeypatch):
    """显式 path 优先于环境变量（对齐 tool_policy 解析序）。"""
    env_allow = _write_config(tmp_path, [_rule("pass")], name="env.json")
    explicit_deny = _write_config(tmp_path, [_rule("import sys; sys.exit(2)")],
                                  name="explicit.json")
    monkeypatch.setenv(HOOKS_ENV, env_allow)
    runner = HookCommandRunner(path=explicit_deny)
    assert runner.decide(_event()) is not None              # 显式路径的否决生效


def test_env_bad_config_falls_back_to_allow_with_stderr(tmp_path, monkeypatch,
                                                        capsys):
    cfg = tmp_path / "env_hooks.json"
    cfg.write_text("{bad", encoding="utf-8")
    monkeypatch.setenv(HOOKS_ENV, str(cfg))
    assert HookCommandRunner().decide(_event()) is None
    assert "读取" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# ⑦ MCPToolSet 集成：命令 veto 出现在 stdin 事件 JSON 里
# ---------------------------------------------------------------------------
def test_mcp_integration_veto_sees_event_json_on_stdin(tmp_path):
    seen = tmp_path / "seen.json"
    code = (
        "import sys;"
        "data = sys.stdin.read();"
        "f = open(%r, 'w', encoding='utf-8');"
        "f.write(data);"
        "f.close();"
        "sys.exit(2)" % str(seen)
    )
    cfg = _write_config(tmp_path, [_rule(code)])
    ts = MCPToolSet(hook_commands=HookCommandRunner(path=cfg))
    out = ts.call_tool("set_frequency",
                       {"hz": 98_500_000, "arm_manual": True})
    assert out["ok"] is False and out["gated"] is True
    assert out["outcome"] == "vetoed"

    payload = json.loads(seen.read_text(encoding="utf-8"))
    assert payload["event_type"] == "agent.pre_tool_use"
    assert payload["data"]["tool"] == "set_frequency"
    assert payload["data"]["write"] is True
    assert payload["data"]["arguments"]["hz"] == 98_500_000


def test_mcp_default_toolset_command_hooks_noop_without_config(
        tmp_path, monkeypatch):
    """默认 MCPToolSet 的外部命令 hook 在无配置时空转（不拦调用）。"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv(HOOKS_ENV, raising=False)
    ts = MCPToolSet()  # 默认 HookCommandRunner()：~/.mbdsdr/hooks.json 缺失
    assert ts.call_tool("set_frequency",
                        {"hz": 98_500_000, "arm_manual": True}) == {"ok": True}


# ---------------------------------------------------------------------------
# ⑧ 配置 mtime 变化触发重载
# ---------------------------------------------------------------------------
def test_config_reload_on_mtime_change(tmp_path):
    cfg = tmp_path / "hooks.json"
    cfg.write_text(json.dumps(
        {"hooks": [_rule("import sys; sys.exit(2)")]},
        ensure_ascii=False), encoding="utf-8")
    runner = HookCommandRunner(path=str(cfg))
    assert runner.decide(_event()) is not None       # 首次：否决

    # 改成放行 + 显式推进 mtime → 重载后放行
    cfg.write_text(json.dumps({"hooks": [_rule("pass")]},
                              ensure_ascii=False), encoding="utf-8")
    _touch(str(cfg))
    assert runner.decide(_event()) is None

    # 删配置 → mtime 指纹变 None，回到空转（无需再 touch）
    os.remove(str(cfg))
    assert runner.decide(_event()) is None


# ---------------------------------------------------------------------------
# 常量钉死（与 kimi-code externalHooks 对应关系）
# ---------------------------------------------------------------------------
def test_constants_align_with_kimi_code():
    assert DEFAULT_TIMEOUT_S == 30.0               # kimi-code 30s 默认超时
    assert SUPPORTED_EVENTS == ("agent.pre_tool_use",)
