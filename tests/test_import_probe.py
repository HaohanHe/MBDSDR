"""``--import-probe`` 入口的洁净性钉子。

该入口只给发布清单/构建方使用：在产物解释器里逐个 import 模块并打印结果。
本组用例钉住四条约束（源码直跑与产物同源，产物侧由 release_manifest 复验）：

1. **不初始化 Qt**：在 PYTHONPATH 里放一个「一被 import 就抛异常」的假
   ``PySide6``，探针仍必须成功——证明它早于 ``from PySide6...`` 退出；
2. **不写任何文件/配置**：``MBDSDR_STARTUP_LOG`` / ``APPDATA`` 都指向临时目录，
   跑完必须一个文件都没产生（含启动日志与 desktop_settings.json）；
3. **既有入口没被改坏**：``--help`` 仍退出 0、``--bogus`` 仍退出 2、
   ``--import-probe`` 缺值时仍由 argparse 报错退出 2；
4. **源码直跑也支持**：不需要冻结产物，``python desktop/main.py --import-probe``
   即可用；导入失败返回 2 并逐条打印 FAIL。
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "desktop" / "main.py"


def _env(**extra):
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # 探针不应受 Qt 平台插件环境影响；不设 QT_QPA_PLATFORM 也能跑。
    env.pop("QT_QPA_PLATFORM", None)
    env.update({k: str(v) for k, v in extra.items()})
    return env


def _run(args, env=None):
    return subprocess.run(
        [sys.executable, str(MAIN), *args],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env or _env(),
        timeout=120,
    )


def _fake_qt_dir(tmp_path):
    """放一个被 import 就炸的 PySide6，用来证明探针没有 import Qt。"""
    fake = tmp_path / "fake_site"
    pkg = fake / "PySide6"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(
        'raise RuntimeError("Qt was imported by --import-probe")\n',
        encoding="utf-8",
    )
    return fake


def test_probe_ok_and_does_not_import_qt(tmp_path):
    env = _env(PYTHONPATH=str(_fake_qt_dir(tmp_path)))
    proc = _run(["--import-probe", "json,os"], env)
    assert proc.returncode == 0, proc.stderr
    assert "[probe] OK json" in proc.stdout
    assert "[probe] OK os" in proc.stdout
    assert "Qt was imported" not in (proc.stdout + proc.stderr)


def test_probe_equals_form_and_failure_exit_code(tmp_path):
    env = _env(PYTHONPATH=str(_fake_qt_dir(tmp_path)))
    ok = _run(["--import-probe=json"], env)
    assert ok.returncode == 0, ok.stderr
    assert "[probe] OK json" in ok.stdout

    bad = _run(["--import-probe", "no_such_module_mbdsdr"], env)
    assert bad.returncode == 2
    assert "[probe] FAIL no_such_module_mbdsdr" in bad.stdout


def test_probe_writes_no_files_or_config(tmp_path):
    log = tmp_path / "startup.log"
    appdata = tmp_path / "appdata"
    localappdata = tmp_path / "localappdata"
    env = _env(
        PYTHONPATH=str(_fake_qt_dir(tmp_path)),
        MBDSDR_STARTUP_LOG=str(log),
        APPDATA=str(appdata),
        LOCALAPPDATA=str(localappdata),
        XDG_CONFIG_HOME=str(tmp_path / "xdg"),
    )
    proc = _run(["--import-probe", "json"], env)
    assert proc.returncode == 0, proc.stderr
    assert not log.exists(), "探针写了启动日志"
    assert not appdata.exists(), "探针建了 APPDATA 目录/配置"
    assert not localappdata.exists(), "探针建了 LOCALAPPDATA 目录"
    assert not (tmp_path / "xdg").exists(), "探针建了 XDG 配置目录"


def test_existing_entry_points_unchanged():
    help_proc = _run(["--help"])
    assert help_proc.returncode == 0, help_proc.stderr
    assert "usage:" in help_proc.stdout.lower()

    bogus = _run(["--bogus"])
    assert bogus.returncode == 2, "未知开关应由 argparse 退出 2"

    missing = _run(["--import-probe"])
    assert missing.returncode == 2, "缺值应由 argparse 报错退出 2"


def test_source_run_supported_without_freezing():
    """源码直跑即支持：不依赖 sys.frozen / 产物布局。"""
    proc = _run(["--import-probe", "json"])
    assert proc.returncode == 0, proc.stderr
    assert "[probe] OK json" in proc.stdout
