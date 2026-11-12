# SPDX-License-Identifier: MIT
"""命令面板「最近使用」损坏恢复回归锁（第一档 A6，`command_palette.py:64`）。

旧实现把 ``FileNotFoundError`` 之外的读写异常全部 ``logger.debug`` 吞掉、按空
列表处理；下一次 ``remember_recent`` 直接覆盖同一个小文件——线索一起抹掉。

这里影响面最小（只丢「最近用过」排序，功能不受损），但口径与其它配置一致：
  1. 缺失 = 首次启动，空列表、不阻塞；
  2. 解析失败 / 顶层不是列表 / 条目不是字符串 → 备份 ``*.corrupt-时间戳``
     + reason.txt，``recent_error()`` 写明原件在哪；之后正常写新列表；
  3. OSError（被占用 / 是目录）→ 不备份、不写入，第二次写盘也被拒绝；
  4. 备份失败 → 同样拒绝写盘，坏文件原样保留。

反证：旧 ``except (OSError, ValueError): debug`` 实现喂同一组断言必须 AssertionError。

运行（不依赖 conftest / QApplication）:
    python -m pytest tests/test_command_palette_recent_corruption.py -v --noconftest
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402

import command_palette as cp  # noqa: E402

_CORRUPT_BYTES = b'["tab:settings", "menu:foo", broken'


@pytest.fixture(autouse=True)
def _isolated_recent(tmp_path, monkeypatch):
    """每条用例独立文件 + 清空模块级缓存/错误状态。"""
    monkeypatch.setattr(cp, "_recent_path", lambda: tmp_path / "recent.json")
    cp.reset_recent()
    yield
    cp.reset_recent()


def _backups(tmp_path: Path):
    return sorted(
        p for p in tmp_path.iterdir()
        if p.name.startswith("recent.json.corrupt-")
        and not p.name.endswith(".reason.txt")
    )


def _assert_corrupt_evidence_kept(palette, tmp_path) -> None:
    path = tmp_path / "recent.json"
    path.write_bytes(_CORRUPT_BYTES)
    palette.reset_recent()
    assert palette.load_recent() == []

    backups = _backups(tmp_path)
    assert len(backups) == 1, "损坏文件没有备份就被当成空列表，原件去向不明"
    assert backups[0].read_bytes() == _CORRUPT_BYTES, "备份不是原件字节"
    assert "已备份为" in (palette.recent_error() or ""), palette.recent_error()
    assert (backups[0].with_name(backups[0].name + ".reason.txt")).exists()

    palette.remember_recent("tab:settings")
    assert json.loads(path.read_text(encoding="utf-8")) == ["tab:settings"]
    assert len(_backups(tmp_path)) == 1, "二次写盘又造了一份备份"


# ---------------------------------------------------------------------------
# 1. 缺失 = 首次启动
# ---------------------------------------------------------------------------
def test_missing_file_is_first_run(tmp_path):
    assert cp.load_recent() == []
    assert cp.recent_error() is None

    cp.remember_recent("tab:settings")
    assert json.loads((tmp_path / "recent.json").read_text(encoding="utf-8")) \
        == ["tab:settings"]
    assert _backups(tmp_path) == []


def test_valid_file_loads_without_false_positive(tmp_path):
    (tmp_path / "recent.json").write_text(
        json.dumps(["tab:settings"], ensure_ascii=False), encoding="utf-8")
    assert cp.load_recent() == ["tab:settings"]
    assert cp.recent_error() is None


# ---------------------------------------------------------------------------
# 2. 损坏：备份 + 错误说明 + 可以继续写
# ---------------------------------------------------------------------------
def test_corrupt_file_is_backed_up(tmp_path):
    _assert_corrupt_evidence_kept(cp, tmp_path)


def test_non_list_root_is_corruption(tmp_path):
    raw = json.dumps({"recent": ["tab:settings"]}).encode("utf-8")
    (tmp_path / "recent.json").write_bytes(raw)

    assert cp.load_recent() == []
    backups = _backups(tmp_path)
    assert len(backups) == 1
    assert backups[0].read_bytes() == raw
    assert "根节点不是列表" in (cp.recent_error() or "")


def test_non_string_item_is_corruption(tmp_path):
    raw = b'["tab:settings", 42]'
    (tmp_path / "recent.json").write_bytes(raw)

    assert cp.load_recent() == []
    assert _backups(tmp_path), "条目类型不对同样是损坏，必须留证据"
    assert "条目不是字符串" in (cp.recent_error() or "")


# ---------------------------------------------------------------------------
# 3. OSError / 备份失败：不写
# ---------------------------------------------------------------------------
def test_oserror_is_not_corruption_but_blocks(tmp_path):
    blocked = tmp_path / "recent.json"
    blocked.mkdir()

    assert cp.load_recent() == []
    assert _backups(tmp_path) == []
    assert "读取失败" in (cp.recent_error() or "")
    assert "已备份为" not in (cp.recent_error() or "")

    cp.remember_recent("tab:settings")
    assert blocked.is_dir(), "读不了的最近使用被当成空列表覆盖了"
    assert not any(blocked.iterdir())
    assert "未保存" in (cp.recent_error() or "")


def test_backup_failure_blocks_writes(tmp_path, monkeypatch):
    path = tmp_path / "recent.json"
    path.write_bytes(_CORRUPT_BYTES)
    monkeypatch.setattr(cp, "backup_corrupt_recent", lambda _p: None)

    assert cp.load_recent() == []
    assert "备份失败" in (cp.recent_error() or "")

    cp.remember_recent("tab:settings")
    assert path.read_bytes() == _CORRUPT_BYTES, "唯一证据被空列表覆盖"
    assert not (tmp_path / "recent.json.tmp").exists()


# ---------------------------------------------------------------------------
# 4. 反证：旧实现喂同一组断言必须红
# ---------------------------------------------------------------------------
class _LegacyRecentPalette:
    """旧实现：除缺失外全部按空处理；写盘直接覆盖。"""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._recent = None

    def reset_recent(self):
        self._recent = None

    def recent_error(self):
        return None

    def _read_recent_file(self):
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as exc:  # noqa: BLE001 - 旧实现就是吞掉
            print(f"命令面板最近使用读取失败（按空处理）：{type(exc).__name__}: {exc}")
            return []
        if not isinstance(raw, list):
            return []
        return [str(x) for x in raw if isinstance(x, str)][:cp.RECENT_LIMIT]

    def load_recent(self):
        if self._recent is None:
            self._recent = self._read_recent_file()
        return self._recent

    def remember_recent(self, source, persist=True):
        items = self.load_recent()
        if source in items:
            items.remove(source)
        items.insert(0, source)
        del items[cp.RECENT_LIMIT:]
        if persist:
            self._path.write_text(json.dumps(items, ensure_ascii=False),
                                  encoding="utf-8")


def test_legacy_silent_fallback_turns_red(tmp_path):
    with pytest.raises(AssertionError):
        _assert_corrupt_evidence_kept(
            _LegacyRecentPalette(tmp_path / "recent.json"), tmp_path)
