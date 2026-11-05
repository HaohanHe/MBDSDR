# SPDX-License-Identifier: MIT
"""启动日志落点策略 / 运行库探测的断言（不依赖 Qt，不需要测试互斥锁）。

铁律：日志绝不落 C 盘用户目录；无 D 盘 + 安装根只读时必须落
``%PROGRAMDATA%\\MBDSDR\\logs``；全部不可写时进内存缓冲并如实说明。
"""
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "desktop"))

import startup_paths as sp  # noqa: E402


def _only_c_drive(monkeypatch):
    """模拟「机器只有 C 盘」：盘符根只看 C，其余路径走真实判断。"""
    real_isdir = os.path.isdir

    def fake_isdir(path):
        if isinstance(path, str) and len(path) == 3 and path[1:3] == ":\\":
            return path[:1].upper() == "C"
        return real_isdir(path)

    monkeypatch.setattr(sp.os.path, "isdir", fake_isdir)


def _programdata_log():
    return os.path.join(os.path.expandvars("%PROGRAMDATA%"), "MBDSDR",
                        "logs", "mbdsdr_startup.log")


def test_no_d_and_readonly_install_root_falls_to_programdata(monkeypatch, tmp_path):
    """无 D 盘 + 安装根不可写 → 落点必须是 ProgramData，不是用户目录。"""
    monkeypatch.delenv("MBDSDR_STARTUP_LOG", raising=False)
    _only_c_drive(monkeypatch)
    monkeypatch.setattr(sp, "program_dir", lambda: str(tmp_path / "install"))
    # 安装根（tmp 目录）写不进，只放行 ProgramData
    real_makedirs = os.makedirs
    blocked_root = os.path.normcase(str(tmp_path))

    def fake_makedirs(path, exist_ok=False):
        if os.path.normcase(str(path)).startswith(blocked_root):
            raise PermissionError(13, "拒绝访问", str(path))
        return real_makedirs(path, exist_ok=exist_ok)

    monkeypatch.setattr(sp.os, "makedirs", fake_makedirs)

    path = sp.startup_log_path()
    assert path == _programdata_log(), f"落点应为 ProgramData，实际 {path!r}"
    lowered = path.lower()
    assert "\\users\\" not in lowered
    assert "appdata" not in lowered
    assert "localappdata" not in lowered


def test_all_unwritable_uses_memory_buffer(monkeypatch, tmp_path):
    """全部落点不可写 → 返回空串，内容进内存缓冲，并给出固定说明。"""
    monkeypatch.delenv("MBDSDR_STARTUP_LOG", raising=False)
    _only_c_drive(monkeypatch)
    monkeypatch.setattr(sp, "program_dir", lambda: str(tmp_path / "install"))

    def always_deny(path, exist_ok=False):
        raise PermissionError(13, "拒绝访问", str(path))

    monkeypatch.setattr(sp.os, "makedirs", always_deny)
    sp._LOG_BUFFER.clear()

    assert sp.startup_log_path() == ""
    assert sp.append_startup_log("缺件自检 A") == ""
    assert "缺件自检 A" in sp.buffered_log()
    assert "本次无持久日志" in sp.persistent_log_note()


def test_candidates_never_under_user_profile(monkeypatch):
    """候选路径里不允许出现用户目录（APPDATA/LOCALAPPDATA/~）。"""
    monkeypatch.delenv("MBDSDR_STARTUP_LOG", raising=False)
    candidates = [os.path.normcase(p) for p in sp.log_candidates()]
    banned = {
        os.path.normcase(os.path.expandvars("%APPDATA%")),
        os.path.normcase(os.path.expandvars("%LOCALAPPDATA%")),
        os.path.normcase(os.path.expanduser("~")),
    }
    banned = {b for b in banned if b and not b.startswith("%")}
    for candidate in candidates:
        for bad in banned:
            assert not candidate.startswith(bad), (
                f"候选落点 {candidate} 落在用户目录 {bad}")


def test_programdata_unwritable_keeps_user_appdata_untouched(monkeypatch, tmp_path):
    """ProgramData 也不可写时：不碰 %APPDATA%，且如实说明无持久日志。"""
    monkeypatch.delenv("MBDSDR_STARTUP_LOG", raising=False)
    _only_c_drive(monkeypatch)
    monkeypatch.setattr(sp, "program_dir", lambda: str(tmp_path / "install"))
    monkeypatch.setattr(sp.os, "makedirs",
                        lambda path, exist_ok=False: (_ for _ in ()).throw(
                            PermissionError(13, "拒绝访问", str(path))))

    appdata = os.path.expandvars("%APPDATA%")
    before = os.stat(appdata).st_mtime if os.path.isdir(appdata) else None

    assert sp.startup_log_path() == ""
    assert "本次无持久日志" in sp.persistent_log_note()

    after = os.stat(appdata).st_mtime if os.path.isdir(appdata) else None
    assert before == after, "%APPDATA% 被写入（mtime 变化）"


def test_runtime_dll_report_finds_and_misses(monkeypatch, tmp_path):
    monkeypatch.setattr(sp, "program_dir", lambda: str(tmp_path))
    monkeypatch.setenv("PATH", "")
    report = sp.runtime_dll_report()
    assert "librtlsdr.dll" in report["missing_packaged"]
    assert "libusb-1.0.dll" in report["missing_packaged"]

    for name in ("librtlsdr.dll", "libusb-1.0.dll"):
        (tmp_path / name).write_bytes(b"")

    report = sp.runtime_dll_report()
    assert report["missing_packaged"] == []
    assert "SoapySDR.dll" in report["missing_optional"]


def test_probe_dir_writable_async_writable(tmp_path):
    done = threading.Event()
    result = {}

    def _cb(error):
        result["error"] = error
        done.set()

    sp.probe_dir_writable_async(str(tmp_path), _cb)
    assert done.wait(10), "后台探测线程未回报"
    assert result["error"] == ""


def test_probe_dir_writable_async_denied(tmp_path):
    """目标路径是个文件 → makedirs 必失败，回调必须是可行动文案。"""
    target = tmp_path / "denied_dir"
    target.write_text("occupied", encoding="utf-8")
    done = threading.Event()
    result = {}

    def _cb(error):
        result["error"] = error
        done.set()

    sp.probe_dir_writable_async(str(target), _cb)
    assert done.wait(10), "后台探测线程未回报"
    assert "录音目录不可写" in result["error"]
    assert str(target) in result["error"]
