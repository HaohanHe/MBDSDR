# SPDX-License-Identifier: MIT
"""
tests/test_ui_settings_isolation_guard.py
=========================================
守门：UI 测试**绝不写用户真实的桌面配置**。

背景（2026-11-05，P0 实测）：星象仪形态会把 ``app_mode=planetarium`` 持久化进
``%APPDATA%/mbdsdr/desktop_settings.json``。某条测试跑完后把星象仪形态留在真实
配置里，下一次 ``test_ui_hard_defects.py::test_stretch_gives_left_the_flex``
启动时 ``_restore_app_mode()`` 切到星象仪页，IDE root（含 ``_main_splitter``）
是隐藏的，两档 resize 都不再布局 → 三栏恒为 ``[211, 211, 210]``、``d_left``
从 600 变 0。这不是「测试不稳定」，是**测试在改用户真的配置文件**。

本文件两条硬断言：
  1. 跑完代表性 UI 用例后，真实 ``desktop_settings.json`` 的 sha256 与
     mtime 必须与跑前逐位一致；
  2. ``%APPDATA%`` 重定向到临时目录后，真实 ``mbdsdr/`` 目录不得出现任何
     新增文件（目录清单前后相同）。

实现方式：用例都在**子进程**里跑（子进程是干净的 APPDATA/形态初值），父进程
只做前后快照比对；这样守门本身不会污染被测进程，也不会被别的测试顺序影响。

注意：**不要用 PowerShell 直接改这份 JSON**——``Set-Content -Encoding UTF8``
在 PS 5.1 会写 BOM，应用会把它当损坏文件改名备份（实测发生过：真文件被改名成
``desktop_settings.json.corrupt-<时间戳>``，等于用户配置丢失）。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_ui_settings_isolation_guard.py -v
"""
import hashlib
import os
import subprocess
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402

from settings import default_config_path  # noqa: E402

REAL_CONFIG = default_config_path()
REAL_DIR = os.path.dirname(REAL_CONFIG)


def _sha256(path: str):
    if not os.path.exists(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _snapshot(path: str):
    """(sha256, mtime_ns)；文件不存在记 (None, None)——"不存在"也必须保持。"""
    if not os.path.exists(path):
        return (None, None)
    st = os.stat(path)
    return (_sha256(path), st.st_mtime_ns)


def _dir_listing(path: str):
    if not os.path.isdir(path):
        return set()
    return set(os.listdir(path))


def _run_pytest_subprocess(targets, extra_env=None):
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env.setdefault("MBDSDR_TEST_LOCK", "0")   # 子进程单跑一个用例，不抢全局锁
    if extra_env:
        env.update(extra_env)
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *targets, "-q", "-p", "no:cacheprovider"],
        cwd=_ROOT, env=env, capture_output=True, text=True, timeout=600,
    )
    return proc


# 代表性 UI 用例：星象仪形态切换 + 三栏比例（前者是污染源，后者是受害者）。
_POLLUTING_CASES = [
    # 这条进星象仪形态后不复位（正是 2026-11-05 把 planetarium 写进真实配置的用例）
    "tests/test_planetarium_entry_visibility.py::test_overlay_avoids_ai_bar_only_on_intersection",
    "tests/test_planetarium_entry_visibility.py::test_overlay_hidden_in_ide_mode",
]


@pytest.mark.parametrize("target", _POLLUTING_CASES)
def test_ui_case_does_not_touch_real_desktop_settings(target):
    """跑一条会切形态的 UI 用例：真实配置 sha256 + mtime + 目录清单必须不变。"""
    before_cfg = _snapshot(REAL_CONFIG)
    before_dir = _dir_listing(REAL_DIR)
    proc = _run_pytest_subprocess([target])
    assert proc.returncode == 0, (
        f"子进程用例本身就红了，先修用例再守门：{target}\n{proc.stdout[-4000:]}")
    after_cfg = _snapshot(REAL_CONFIG)
    assert after_cfg == before_cfg, (
        f"UI 用例写坏了用户真实配置（sha256/mtime 变化）: {target}\n"
        f"before={before_cfg} after={after_cfg}")
    after_dir = _dir_listing(REAL_DIR)
    assert after_dir == before_dir, (
        f"UI 用例在真实 %APPDATA%/mbdsdr 里留下新文件: {target}\n"
        f"新增={sorted(after_dir - before_dir)} 消失={sorted(before_dir - after_dir)}")


def test_stretch_case_does_not_touch_real_desktop_settings():
    """受害者用例同样要干净：跑完后真实配置与目录清单逐位不变。"""
    target = ("tests/test_ui_hard_defects.py::TestThreeColumnLayout"
              "::test_stretch_gives_left_the_flex")
    before_cfg = _snapshot(REAL_CONFIG)
    before_dir = _dir_listing(REAL_DIR)
    proc = _run_pytest_subprocess([target])
    assert proc.returncode == 0, (
        f"三栏比例用例红了，先修用例再守门：{target}\n{proc.stdout[-4000:]}")
    assert _snapshot(REAL_CONFIG) == before_cfg, (
        "三栏比例用例写坏了用户真实配置（sha256/mtime 变化）")
    assert _dir_listing(REAL_DIR) == before_dir, (
        "三栏比例用例在真实 %APPDATA%/mbdsdr 里留下新文件")


def test_session_guard_is_active(real_config_snapshot):
    """确认 conftest 的会话级守卫真的在跑（不是只守本文件这几条子进程用例）。

    守卫本身在 tests/conftest.py：pytest_configure 取真实配置快照，会话结束
    比对 sha256 + mtime_ns + 目录清单。这里只验证它已装上且快照与当前一致。
    """
    snap = real_config_snapshot
    assert snap["path"], (
        "会话级配置守卫没有定位到真实配置路径："
        f"{snap.get('error')}")
    assert os.path.normcase(os.path.abspath(snap["path"])) == os.path.normcase(
        os.path.abspath(REAL_CONFIG)), (
        f"守卫盯的文件不是真实配置: guard={snap['path']} real={REAL_CONFIG}")
    assert snap["before"] is not None, "守卫没有取到会话前快照"
    assert (snap["before"]["sha256"], snap["before"]["mtime_ns"]) == _snapshot(
        REAL_CONFIG), (
        "会话级守卫快照与当前真实配置不一致——本会话内已经有测试写坏了它")
    assert set(snap["before_listing"]) == _dir_listing(REAL_DIR), (
        "真实 %APPDATA%/mbdsdr 目录清单已经变化（有测试在会话中写了它）")


def test_redirected_appdata_writes_only_to_temp_dir(tmp_path):
    """%APPDATA% 重定向到临时目录后：写盘只落在临时目录，真实目录零新增。

    这条钉住"隔离手段本身有效"——不只是当前这几条用例碰巧没写。
    """
    before_cfg = _snapshot(REAL_CONFIG)
    before_dir = _dir_listing(REAL_DIR)
    appdata = tmp_path / "appdata"
    appdata.mkdir()
    script = (
        "import os, sys\n"
        "sys.path.insert(0, r'%s')\n"
        "sys.path.insert(0, r'%s')\n"
        "os.environ['QT_QPA_PLATFORM']='offscreen'\n"
        "from PySide6.QtWidgets import QApplication\n"
        "app = QApplication([])\n"
        "from settings import DesktopSettings\n"
        "s = DesktopSettings.load()\n"
        "s.set('app_mode', 'planetarium')\n"
        "s.flush()\n"
        "print('WROTE', s._path)\n"
    ) % (_ROOT, os.path.join(_ROOT, "desktop"))
    proc = subprocess.run(
        [sys.executable, "-u", "-c", script], cwd=_ROOT,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen",
             "APPDATA": str(appdata)}, capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == 0, f"隔离写盘脚本失败:\n{proc.stderr[-2000:]}"
    assert str(appdata) in proc.stdout, (
        f"DesktopSettings 没有落在重定向目录，实际: {proc.stdout.strip()}")
    assert (appdata / "mbdsdr" / "desktop_settings.json").exists(), (
        "重定向后临时目录里没有生成配置——隔离手段没生效")
    assert _snapshot(REAL_CONFIG) == before_cfg, (
        "重定向写盘仍改到了真实配置（说明某处绕开了 %APPDATA%）")
    assert _dir_listing(REAL_DIR) == before_dir, (
        "重定向写盘仍在真实 %APPDATA%/mbdsdr 里留下了文件")


#: 反证探针（文件名不以 test_ 开头，全量收集不会把它当正式用例）。
_PROBE = os.path.join(_ROOT, "tests", "_pollution_probe",
                      "pollutes_real_config.py")


def test_real_config_is_write_protected_in_session(tmp_path):
    """反证写保护：会话期间真实配置只读，故意写一次必须当场失败。

    归因不靠事后猜：写入失败发生在写入者进程里，谁写谁红。
    """
    real = tmp_path / "realdir"
    real.mkdir()
    target = real / "desktop_settings.json"
    original = b'{"record_dir": ""}\n'
    target.write_bytes(original)
    proc = _run_pytest_subprocess(
        [f"{_PROBE}::test_write_to_real_config_is_refused"],
        extra_env={"MBDSDR_SETTINGS_DIR": str(real)})
    text = proc.stdout + proc.stderr
    assert proc.returncode == 0, (
        f"真实配置没被写保护（写入成功了）：\n{text[-3000:]}")
    assert target.read_bytes() == original, "写保护没生效，内容被改了"
    assert os.access(target, os.W_OK), (
        "会话结束没有恢复原属性（用户文件被永久设成只读）")


def test_tmp_only_write_passes(tmp_path):
    """阴性对照：只写 tmp_path 的用例必须绿，证明守卫不是「永远红」。"""
    real = tmp_path / "realdir"
    real.mkdir()
    proc = _run_pytest_subprocess(
        [f"{_PROBE}::test_writes_tmp_only"],
        extra_env={"MBDSDR_SETTINGS_DIR": str(real)})
    assert proc.returncode == 0, (
        f"只写临时目录的用例被误伤（永远红的守卫等于没有）：\n"
        f"{(proc.stdout + proc.stderr)[-3000:]}")


def test_session_backup_restores_overwritten_content(tmp_path):
    """反证还原：绕过写保护整份覆盖后，会话结束必须按**内容备份**逐字节还原。

    守卫只存 sha256 时只能证明「变了」；用户真实配置 2026-11-13 被探针覆盖
    后能逐字节找回，靠的是别人留下的副本。这里钉住「会话自己就留副本、自己
    就还原」，不再依赖运气。
    """
    real = tmp_path / "realdir"
    real.mkdir()
    target = real / "desktop_settings.json"
    original = b'{"record_dir": "", "frequency_hz": 98000000}\n'
    target.write_bytes(original)
    proc = _run_pytest_subprocess(
        [f"{_PROBE}::test_overwrites_existing_real_config"],
        extra_env={"MBDSDR_SETTINGS_DIR": str(real)})
    text = proc.stdout + proc.stderr
    assert proc.returncode != 0, (
        f"绕过写保护覆盖配置后兜底守卫没红：\n{text[-3000:]}")
    assert "[mbdsdr-guard]" in text, (
        f"不是配置守卫判的红（可能是别的原因）：\n{text[-3000:]}")
    assert target.read_bytes() == original, (
        "会话结束没有按备份还原被覆盖的真实配置："
        f"now={target.read_bytes()!r}")


def test_lock_bypass_constructing_qapp_is_red(tmp_path):
    """反证绕锁机检：``MBDSDR_TEST_LOCK=0`` + 构造 QApplication 必须当场判红。

    锁存在的理由就是「同一时刻只有一个进程碰 Qt 全局态」；绕锁还去建窗口
    等于拆了刹车踩油门。这里把锁路径指到 tmp_path，确保本机没有别的会话
    持锁，判定条件与真实并发时一致。
    """
    lock_path = tmp_path / "qt_test.lock"
    proc = _run_pytest_subprocess(
        [f"{_PROBE}::test_creates_qapplication_without_lock"],
        extra_env={"MBDSDR_TEST_LOCK": "0",
                   "MBDSDR_TEST_LOCK_PATH": str(lock_path)})
    text = proc.stdout + proc.stderr
    assert proc.returncode != 0, (
        f"绕锁下构造 QApplication 却没被判红：\n{text[-3000:]}")
    assert "[mbdsdr-lock]" in text, (
        f"不是绕锁机检判的红：\n{text[-3000:]}")
    assert "test_creates_qapplication_without_lock" in text, (
        f"机检红了但没点名用例：\n{text[-3000:]}")
