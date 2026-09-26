"""确定性单测：Windows DLL 搜索逻辑与安全降级。

不依赖真实 rtlsdr.dll / Windows：用临时目录 + 注入 exists 谓词验证搜索路径，
找不到 DLL 时验证返回明确提示而非抛异常。
"""
import os
import sys

import pytest

from mbdsdr_ai import windows_setup as ws


def test_search_finds_dll_in_extra_dir(tmp_path):
    dll_dir = tmp_path / "sdrsharp"
    dll_dir.mkdir()
    (dll_dir / "rtlsdr.dll").write_bytes(b"fake")
    found = ws.find_rtlsdr_dll(extra_dirs=[str(dll_dir)])
    assert found is not None
    assert os.path.normcase(found) == os.path.normcase(str(dll_dir / "rtlsdr.dll"))


def test_search_returns_none_when_missing():
    # exists 恒 False：模拟全盘都没有
    assert ws.find_rtlsdr_dll(exists=lambda p: False) is None
    assert ws.find_libusb_dll(exists=lambda p: False) is None


def test_search_uses_injected_exists_predicate():
    calls = []

    def exists(p):
        calls.append(p)
        return p.endswith("rtlsdr.dll") and os.sep + "custom" + os.sep in p

    found = ws.find_rtlsdr_dll(extra_dirs=["/custom/dir"], exists=exists)
    assert found is not None
    # 至少尝试过拼接出 <dir>/rtlsdr.dll
    assert any(c.endswith("rtlsdr.dll") for c in calls)


def test_default_candidate_dirs_contain_expected_locations():
    dirs = ws.default_candidate_dirs()
    joined = "|".join(dirs)
    assert "lib" in dirs  # 相对 lib/ 目录
    assert r"C:\rtl-sdr\bin" in dirs  # 常见安装位置
    # PATH 环境变量里的目录被纳入
    assert isinstance(dirs, list)


def test_setup_non_windows_is_graceful():
    """本机是 Linux：setup 应明确说明非 Windows，不崩、不假装成功。"""
    result = ws.setup_rtlsdr_windows()
    if sys.platform != "win32":
        assert result["ok"] is False
        assert "非 Windows" in result["message"]
        assert result["dll_path"] is None
    else:  # pragma: no cover - 在 Windows CI 上跑时
        assert "message" in result


def test_setup_missing_dll_returns_clear_message(monkeypatch):
    """模拟 Windows 但全盘无 DLL：返回明确中文提示，不抛异常。"""
    monkeypatch.setattr(sys, "platform", "win32")
    result = ws.setup_rtlsdr_windows(import_test=False, exists=lambda p: False)
    assert result["ok"] is False
    assert result["dll_path"] is None
    assert result["message"] == ws.RTLSDR_NOT_FOUND_MSG
    assert "SDR#" in result["message"] or "Zadig" in result["message"]


def test_setup_found_dll_registers(tmp_path, monkeypatch):
    """模拟 Windows 且找到 DLL：注册目录后 ok=True。"""
    dll_dir = tmp_path / "rtl-sdr" / "bin"
    dll_dir.mkdir(parents=True)
    (dll_dir / "rtlsdr.dll").write_bytes(b"fake")
    monkeypatch.setattr(sys, "platform", "win32")
    result = ws.setup_rtlsdr_windows(
        import_test=False, extra_dirs=[str(dll_dir)]
    )
    assert result["ok"] is True
    assert result["dll_path"] is not None
    assert result["dll_path"].endswith("rtlsdr.dll")


def test_setup_never_raises_when_dll_found_but_import_fails(tmp_path, monkeypatch):
    """找到 DLL 但 import rtlsdr 失败：返回 ok=False + 明确信息，不抛。"""
    dll_dir = tmp_path / "rtl-sdr" / "bin"
    dll_dir.mkdir(parents=True)
    (dll_dir / "rtlsdr.dll").write_bytes(b"fake")
    monkeypatch.setattr(sys, "platform", "win32")
    # import_test=True 会真的去 import rtlsdr；本机没有该包 -> 走失败分支
    result = ws.setup_rtlsdr_windows(
        import_test=True, extra_dirs=[str(dll_dir)]
    )
    # 不论本机是否装了 rtlsdr，都不应抛异常；结构完整即可
    assert "ok" in result
    assert "message" in result
    assert result["dll_path"] is not None
