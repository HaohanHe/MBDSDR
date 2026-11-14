# SPDX-License-Identifier: MIT
# collection-guard: explicit-nodeid -- 只由 tests/test_ui_settings_isolation_guard.py 按 nodeid 显式执行
"""反证探针：验证「真实配置写保护 / 内容备份还原 / 绕锁禁碰 Qt」三条机检。

* 文件名不以 ``test_`` 开头、目录也不以 ``test`` 开头，全量收集时不会被当成
  正式用例；只有 ``tests/test_ui_settings_isolation_guard.py`` 里的反证测试
  会显式指定 nodeid 跑它。
* 这里的「真实配置」由反证测试用 ``MBDSDR_SETTINGS_DIR`` 指到 tmp_path，
  一次也不碰用户真实目录。
"""
import os
import stat

import pytest


def test_write_to_real_config_is_refused(real_config_snapshot):
    """会话期间真实配置是只读的：写入必须当场失败在写入者进程里。"""
    path = real_config_snapshot["path"]
    with pytest.raises(OSError):
        with open(path, "ab") as f:
            f.write(b"x")


def test_overwrites_existing_real_config(real_config_snapshot):
    """模拟「绕过写保护的写者」：整份覆盖，用来验证兜底守卫会红且能还原。"""
    path = real_config_snapshot["path"]
    os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    with open(path, "w", encoding="utf-8") as f:
        f.write('{"overwritten_by_probe": true}\n')


def test_writes_tmp_only(tmp_path):
    """只写临时目录：所有守卫都必须放行（否则就是永远红的假守卫）。"""
    (tmp_path / "desktop_settings.json").write_text(
        '{"ok": true}\n', encoding="utf-8")


def test_creates_qapplication_without_lock(real_config_snapshot):
    """绕锁状态下构造 QApplication：必须是红的（反证 see test_ui_settings_...）。"""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    assert app is not None
