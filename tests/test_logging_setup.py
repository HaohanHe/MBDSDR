# SPDX-License-Identifier: MIT
"""日志初始化与「改设置→重启生效」实测。

全部用子进程：每次都是干净的 logging 全局状态，等价于一次重启。
"""
import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DESKTOP_DIR = os.path.join(REPO_ROOT, "desktop")


def _run(code: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([DESKTOP_DIR, REPO_ROOT, env.get("PYTHONPATH", "")])
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    return subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, env=env, cwd=REPO_ROOT, timeout=60)


def test_import_has_no_side_effect():
    """导入 logging_setup 不得装 handler、不得打开文件。"""
    code = (
        "import logging, logging_setup\n"
        "root = logging.getLogger()\n"
        "print('handlers', len(root.handlers))\n"
        "print('configured', logging_setup._configured)\n"
    )
    proc = _run(code)
    assert proc.returncode == 0, proc.stderr
    assert "handlers 0" in proc.stdout
    assert "configured False" in proc.stdout


def test_debug_level_emits_debug_output():
    code = (
        "import logging, logging_setup\n"
        "logging_setup.configure_logging('DEBUG')\n"
        "logging.getLogger('demo').debug('debug-detail-visible')\n"
    )
    proc = _run(code)
    assert proc.returncode == 0, proc.stderr
    assert "level=DEBUG" in proc.stderr
    assert "debug-detail-visible" in proc.stderr


def test_info_level_hides_debug_output():
    code = (
        "import logging, logging_setup\n"
        "logging_setup.configure_logging('INFO')\n"
        "logging.getLogger('demo').debug('debug-detail-visible')\n"
    )
    proc = _run(code)
    assert proc.returncode == 0, proc.stderr
    assert "debug-detail-visible" not in proc.stderr
    assert "level=DEBUG" not in proc.stderr


def test_setting_change_applies_after_restart(tmp_path):
    """设置文件写 DEBUG → 新进程从同一文件启动 → DEBUG 输出生效。"""
    settings_path = tmp_path / "settings.json"
    settings_path.write_text('{"log_level": "DEBUG"}', encoding="utf-8")

    code = (
        "import logging\n"
        "from settings import DesktopSettings\n"
        "from logging_setup import configure_from_settings\n"
        f"s = DesktopSettings.load(path={str(settings_path)!r})\n"
        "configure_from_settings(s)\n"
        "logging.getLogger('demo').debug('after-restart-debug')\n"
        "print('setting', s.get('log_level'))\n"
    )
    proc = _run(code)
    assert proc.returncode == 0, proc.stderr
    assert "setting DEBUG" in proc.stdout
    assert "level=DEBUG" in proc.stderr
    assert "after-restart-debug" in proc.stderr
