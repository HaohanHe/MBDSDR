# SPDX-License-Identifier: MIT
"""反证探针：故意写「真实配置」，用来证明逐用例守卫会红并点名。

* 文件名不以 ``test_`` 开头、目录也不以 ``test`` 开头，全量收集时不会被当成
  正式用例；只有 ``tests/test_ui_settings_isolation_guard.py`` 里的反证测试
  会显式指定 nodeid 跑它。
* 两条用例一正一反：一条写守卫声明的真实配置（必须红），一条只写 tmp_path
  （必须绿）。反证若永远红或永远绿，都不算守卫生效。
"""


def test_writes_declared_real_config(real_config_snapshot):
    """写 pytest_configure 快照下来的「真实配置」：守卫必须点名本用例。"""
    path = real_config_snapshot["path"]
    with open(path, "w", encoding="utf-8") as f:
        f.write('{"polluted_by_probe": true}\n')


def test_writes_tmp_only(tmp_path):
    """只写临时目录：守卫必须放行（否则它就是个永远红的假守卫）。"""
    (tmp_path / "desktop_settings.json").write_text(
        '{"ok": true}\n', encoding="utf-8")


def test_overwrites_existing_real_config(real_config_snapshot):
    """整份覆盖已有内容：会话结束时必须按会话备份逐字节还原。"""
    with open(real_config_snapshot["path"], "w", encoding="utf-8") as f:
        f.write('{"overwritten_by_probe": true}\n')
