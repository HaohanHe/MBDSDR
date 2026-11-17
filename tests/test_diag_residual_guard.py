# SPDX-License-Identifier: MIT
"""诊断脚本「不留残留进程」契约测试（不碰 SDR / 不跑真机诊断）。

钉住三件事：

1. 卡死的设备子进程会在 ``timeout + 宽限`` 后被终止，本机不再有该 PID；
2. 终止不生效时必须走 ``residual`` 分支：结果带 PID、退出码 4、给出处置建议；
3. 启动前的残留实例预检会拒绝启动，``--force`` 可以放行。
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SDR_SCRIPT = os.path.join(ROOT, "scripts", "sdr_diag.py")
SYSTEM_SCRIPT = os.path.join(ROOT, "scripts", "system_diag.py")


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    # dataclass 在 Py3.14 会按 cls.__module__ 回查 sys.modules；先注册再执行。
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_sdr():
    return _load(SDR_SCRIPT, "sdr_diag")


def _load_system():
    return _load(SYSTEM_SCRIPT, "system_diag")


# ---------------------------------------------------------------------------
# 1. 真子进程卡死 → 父进程有界返回并确实终止它
# ---------------------------------------------------------------------------
def test_stuck_child_is_killed_within_timeout_plus_grace(tmp_path):
    from mbdsdr_ai import diag_guard

    result_path = str(tmp_path / "result.json")
    started = time.monotonic()
    outcome = diag_guard.run_worker(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        timeout_s=0.3,
        result_path=result_path,
        startup_grace_s=0.3,
        residual_grace_s=1.0,
    )
    elapsed = time.monotonic() - started

    assert outcome.timed_out is True
    assert outcome.residual is False
    assert outcome.pid > 0
    # 绝不能等到 60 s；给子进程启动/收尸留一点系统抖动。
    assert elapsed < 0.3 + 0.3 + 1.0 + 2.0
    # 本机不再有该 PID（父进程持有真实句柄 terminate，不是自杀式硬杀）。
    assert diag_guard.pid_alive(outcome.pid) is False


def test_worker_result_file_is_returned(tmp_path):
    """正常退出的子进程：父进程读到它写出的 JSON 与退出码。"""
    from mbdsdr_ai import diag_guard

    result_path = str(tmp_path / "result.json")
    code = (
        "import json, sys\n"
        "json.dump({'verdict': 'ok', 'exit_code': 0, 'stages': []},"
        " open(sys.argv[1], 'w'))\n"
    )
    outcome = diag_guard.run_worker(
        [sys.executable, "-c", code, result_path],
        timeout_s=5.0, result_path=result_path, startup_grace_s=5.0)
    assert outcome.timed_out is False
    assert outcome.residual is False
    assert outcome.returncode == 0
    assert outcome.result == {"verdict": "ok", "exit_code": 0, "stages": []}


# ---------------------------------------------------------------------------
# 2. terminate 不生效 → residual 分支与退出码
# ---------------------------------------------------------------------------
class _UnkillableProc:
    """模拟「terminate 永远不生效」的子进程句柄。"""

    def __init__(self, pid: int):
        self.pid = pid
        self.returncode = None

    def wait(self, timeout=None):
        raise subprocess.TimeoutExpired(cmd="fake-worker", timeout=timeout)

    def terminate(self):
        return None

    def poll(self):
        return None


def test_terminate_failure_marks_residual(tmp_path):
    from mbdsdr_ai import diag_guard

    fake = _UnkillableProc(pid=424242)
    outcome = diag_guard.run_worker(
        ["fake-worker"], timeout_s=0.05,
        result_path=str(tmp_path / "result.json"),
        startup_grace_s=0.05, residual_grace_s=0.05,
        spawn=lambda *a, **k: fake,
        terminate_fn=lambda proc: None,
    )
    assert outcome.timed_out is True
    assert outcome.residual is True
    assert outcome.pid == 424242


def test_residual_timeout_result_has_pid_and_exit_code(tmp_path, capsys):
    m = _load_sdr()
    from mbdsdr_ai.diag_guard import WorkerOutcome

    outcome = WorkerOutcome(
        pid=424242, returncode=None, timed_out=True, residual=True,
        result=None, elapsed_s=3.2)

    def _fake_run_worker(command, timeout_s, result_path, **_kwargs):
        return outcome

    m.run_worker = _fake_run_worker
    args = m.build_parser().parse_args(["--timeout", "2"])
    result = m.run_in_subprocess(args)

    assert result.residual is True
    assert result.worker_pid == 424242
    assert result.verdict == "worker_timeout"
    assert result.exit_code == m.EXIT_NO_SAMPLES == 4
    assert result.stages and result.stages[-1].name == "enumerate"
    assert "424242" in result.stages[-1].detail

    m.print_human(result)
    out = capsys.readouterr().out
    assert "424242" in out
    assert "重启系统" in out and "拔插设备" in out
    assert "residual=true" in out


# ---------------------------------------------------------------------------
# 3. 启动前预检：识别残留诊断实例，--force 放行
# ---------------------------------------------------------------------------
def _fake_processes():
    return [
        (4242, r"C:\Python314\python.exe scripts\sdr_diag.py --json"),
        (5151, r"C:\Python314\python.exe scripts\system_diag.py"),
        (9000, r"C:\Python314\python.exe -c import time"),
        # 包装器/测试命令里出现同名子串，不得误报为诊断实例。
        (9001, r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
               r" -Command python -m pytest tests/test_sdr_diag.py"),
    ]


def test_preflight_blocks_start_and_lists_pids(capsys):
    m = _load_sdr()
    rc = m.main(
        [],
        list_devices=lambda: [],
        open_device=lambda _info: None,
        process_provider=_fake_processes,
    )
    assert rc == m.EXIT_DIAG_RESIDUAL == 5
    out = capsys.readouterr().out
    assert "4242" in out and "5151" in out
    assert "9000" not in out and "9001" not in out  # 非诊断进程不得误报
    assert "--force" in out


def test_preflight_force_allows_start(capsys):
    m = _load_sdr()
    rc = m.main(
        ["--force"],
        list_devices=lambda: [],
        open_device=lambda _info: None,
        process_provider=_fake_processes,
    )
    # 放行后按注入的假依赖正常跑：无设备 → 沿用旧退出码 1。
    assert rc == m.EXIT_NO_DEVICE == 1
    out = capsys.readouterr().out
    assert "未发现可用 SDR 设备" in out


def test_system_diag_preflight_blocks(capsys):
    m = _load_system()
    rc = m.main(
        [],
        sdr_runner=lambda _a: {},
        gnss_runner=lambda _a: {},
        process_provider=_fake_processes,
    )
    assert rc == m.EXIT_DIAG_RESIDUAL == 5
    out = capsys.readouterr().out
    assert "已阻止启动" in out and "4242" in out and "--force" in out


def test_system_diag_delegates_sdr_to_subprocess(monkeypatch):
    m = _load_system()
    calls = []

    def _fake_subprocess(args):
        calls.append(args)
        return {"verdict": "no_device", "exit_code": 1, "stages": []}

    def _must_not_run_in_process(_args):
        raise AssertionError("设备 I/O 不得回到聚合层父进程")

    monkeypatch.setattr(m.sdr_diag, "run_in_subprocess", _fake_subprocess)
    monkeypatch.setattr(m.sdr_diag, "run_diagnostics",
                        _must_not_run_in_process)
    args = m.sdr_diag.build_parser().parse_args([])
    assert m._run_sdr(args)["verdict"] == "no_device"
    assert calls and calls[0] is args
