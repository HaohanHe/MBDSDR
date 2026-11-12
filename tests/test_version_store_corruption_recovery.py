# SPDX-License-Identifier: MIT
"""VersionStore 损坏恢复回归锁（第一档 A4，`version_store.py:72`）。

旧实现 ``_load`` 用 ``except Exception: print`` 吞掉损坏，内存里是空库；
下一次 ``snapshot`` 直接 ``_save``，把 ``versions.json`` 覆盖成「只剩新版本」。

关键差异（比 guardian 更狠）：``objects/`` 只有「内容 hash → 内容」，没有文件名、
版本分组、时间戳与父链。索引一丢，这些对象文件不可能反推回版本——所以
``last_error`` 必须写明「对象文件还在，但已无法通过界面找回」，否则用户会以为
数据彻底没了，或者反过来以为点一下还能恢复。

契约：
  1. 缺失 = 首次启动：空库、不阻塞；
  2. 损坏（解析失败 / 顶层不是对象 / 条目缺字段 / head 悬空）→ 备份
     ``*.corrupt-时间戳`` + reason.txt + last_error，备份成功才允许写新内容；
  3. 备份失败 / OSError（被占用 / 是目录）→ 不备份、不写入，blocked 挂实例，
     坏索引原样保留；二次写入不重复造备份。

反证：旧 ``except Exception: print`` 实现喂同一组断言必须 AssertionError。

运行（不依赖 conftest / QApplication）:
    python -m pytest tests/test_version_store_corruption_recovery.py -v --noconftest
"""
from __future__ import annotations

import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402

import mbdsdr_ai.version_store as version_store_module  # noqa: E402
from mbdsdr_ai.version_store import VersionStore  # noqa: E402

_CORRUPT_BYTES = (
    '{"head": "v_1", "versions": [{"id": "v_1", "message": "旧版本", broken'
).encode("utf-8")


def _backups(store_dir, name: str = "versions.json"):
    return sorted(
        p for p in store_dir.iterdir()
        if p.name.startswith(name + ".corrupt-")
        and not p.name.endswith(".reason.txt")
    )


def _seed_objects(store_dir):
    """模拟「索引坏了但对象目录还在」：先放一个真实对象文件。"""
    objects = store_dir / "objects"
    objects.mkdir(exist_ok=True)
    (objects / ("a" * 64)).write_text("旧文件内容", encoding="utf-8")


def _assert_corrupt_evidence_kept(factory, tmp_path) -> None:
    store_dir = tmp_path / "store"
    store_dir.mkdir(exist_ok=True)
    path = store_dir / "versions.json"
    path.write_bytes(_CORRUPT_BYTES)
    _seed_objects(store_dir)
    store = factory(str(store_dir))
    assert store._versions == {}

    store.snapshot("重建", {"a.py": "x"})

    backups = _backups(store_dir)
    assert len(backups) == 1, "损坏索引没有备份就被空库写回，原件已丢"
    assert backups[0].read_bytes() == _CORRUPT_BYTES, "备份不是原件字节"
    assert store.corrupt_backup == str(backups[0])
    assert "已备份为" in (store.last_error or ""), store.last_error
    assert "对象文件还在" in (store.last_error or ""), store.last_error
    assert "已无法通过界面找回" in (store.last_error or ""), store.last_error
    assert (backups[0].with_name(backups[0].name + ".reason.txt")).exists()

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert [v["message"] for v in saved["versions"]] == ["重建"]
    # 对象目录不清空：旧对象仍在，只是没有索引指回去
    assert (store_dir / "objects" / ("a" * 64)).exists()


# ---------------------------------------------------------------------------
# 1. 缺失 = 首次启动
# ---------------------------------------------------------------------------
def test_missing_file_is_first_run_not_corruption(tmp_path):
    store = VersionStore(store_path=str(tmp_path / "store"))
    assert store._versions == {}
    assert store.last_error is None
    assert store.corrupt_backup is None
    assert store._save_blocked is False

    store.snapshot("首次", {"a.py": "1"})
    assert (tmp_path / "store" / "versions.json").exists()
    assert _backups(tmp_path / "store") == []


def test_valid_file_reloads_without_false_positive(tmp_path):
    store = VersionStore(store_path=str(tmp_path / "store"))
    store.snapshot("v1", {"a.py": "1"})

    reloaded = VersionStore(store_path=str(tmp_path / "store"))
    assert reloaded.get_stats()["total_versions"] == 1
    assert reloaded.last_error is None
    assert reloaded.corrupt_backup is None


# ---------------------------------------------------------------------------
# 2. 损坏：备份 + last_error + 写新内容不丢证据
# ---------------------------------------------------------------------------
def test_corrupt_file_is_backed_up_before_overwrite(tmp_path):
    _assert_corrupt_evidence_kept(VersionStore, tmp_path)


def test_semantic_corruption_is_backed_up(tmp_path):
    store_dir = tmp_path / "store"
    store_dir.mkdir()
    path = store_dir / "versions.json"
    raw = json.dumps({"head": None, "versions": [{"id": "v_bad"}]},
                     ensure_ascii=False).encode("utf-8")
    path.write_bytes(raw)

    store = VersionStore(store_path=str(store_dir))
    assert store._versions == {}
    backups = _backups(store_dir)
    assert len(backups) == 1
    assert backups[0].read_bytes() == raw
    assert "已备份为" in (store.last_error or "")


def test_dangling_head_is_corruption(tmp_path):
    store_dir = tmp_path / "store"
    store_dir.mkdir()
    path = store_dir / "versions.json"
    raw = json.dumps({"head": "v_不存在", "versions": []},
                     ensure_ascii=False).encode("utf-8")
    path.write_bytes(raw)

    store = VersionStore(store_path=str(store_dir))
    assert store._head is None
    assert _backups(store_dir), "head 悬空同样是索引损坏，必须留证据"


def test_second_write_does_not_create_an_extra_backup(tmp_path):
    store_dir = tmp_path / "store"
    store_dir.mkdir()
    (store_dir / "versions.json").write_bytes(_CORRUPT_BYTES)
    store = VersionStore(store_path=str(store_dir))
    store.snapshot("一", {"a.py": "1"})
    store.snapshot("二", {"a.py": "2"})
    assert len(_backups(store_dir)) == 1, "二次写入又造了一份备份"


# ---------------------------------------------------------------------------
# 3. 备份失败 / 读不了：拒绝写盘，坏索引原样保留
# ---------------------------------------------------------------------------
def test_backup_failure_blocks_index_writes(tmp_path, monkeypatch):
    store_dir = tmp_path / "store"
    store_dir.mkdir()
    path = store_dir / "versions.json"
    path.write_bytes(_CORRUPT_BYTES)
    monkeypatch.setattr(version_store_module, "backup_corrupt_versions",
                        lambda _p: None)

    store = VersionStore(store_path=str(store_dir))
    assert store._save_blocked is True, "备份失败后没有挂 blocked 状态"
    assert store.corrupt_backup is None
    assert "备份失败" in (store.last_error or "")

    with pytest.raises(RuntimeError):
        store.snapshot("不能落盘", {"a.py": "1"})
    assert path.read_bytes() == _CORRUPT_BYTES, "唯一索引被空库覆盖"
    assert _backups(store_dir) == []


def test_save_refuses_when_blocked(tmp_path, monkeypatch):
    store_dir = tmp_path / "store"
    store_dir.mkdir()
    (store_dir / "versions.json").write_bytes(_CORRUPT_BYTES)
    monkeypatch.setattr(version_store_module, "backup_corrupt_versions",
                        lambda _p: None)
    store = VersionStore(store_path=str(store_dir))

    assert store._save() is False
    assert "未保存" in (store.last_error or "")


def test_oserror_is_not_corruption_but_blocks(tmp_path):
    store_dir = tmp_path / "store"
    store_dir.mkdir()
    blocked = store_dir / "versions.json"
    blocked.mkdir()
    store = VersionStore(store_path=str(store_dir))

    assert store._versions == {}
    assert _backups(store_dir) == []
    assert "读取失败" in (store.last_error or "")
    assert "已备份为" not in (store.last_error or "")
    assert store._save_blocked is True

    with pytest.raises(RuntimeError):
        store.snapshot("不写", {"a.py": "1"})
    assert blocked.is_dir(), "读不了的索引被当成空库替换掉了"
    assert not any(blocked.iterdir()), "读失败后仍在目标位置写了文件"


# ---------------------------------------------------------------------------
# 4. 反证：旧实现喂同一组断言必须红
# ---------------------------------------------------------------------------
class _LegacyVersionStore:
    """旧实现：``except Exception: print`` 后按空库继续，下次 snapshot 覆盖。"""

    def __init__(self, store_path: str) -> None:
        self.store_path = os.path.expanduser(store_path)
        self.objects_path = os.path.join(self.store_path, "objects")
        self.versions_file = os.path.join(self.store_path, "versions.json")
        os.makedirs(self.objects_path, exist_ok=True)
        self._versions = {}
        self._head = None
        self.last_error = None
        self.corrupt_backup = None
        self._save_blocked = False
        self._load()

    def _load(self):
        if os.path.exists(self.versions_file):
            try:
                with open(self.versions_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                loaded = {}
                for v in data.get("versions", []):
                    loaded[v["id"]] = v
                self._versions = loaded
                self._head = data.get("head")
            except Exception as exc:  # noqa: BLE001 - 旧实现就是静默吞掉
                print(f"警告: 版本存储加载失败: {exc}")

    def _save(self):
        data = {"head": self._head, "versions": list(self._versions.values())}
        with open(self.versions_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def snapshot(self, message, files, author="ai", metadata=None):
        version = {
            "id": f"v_{len(self._versions) + 1}",
            "timestamp": 0.0,
            "message": message,
            "parent_id": self._head,
            "files": {},
            "author": author,
            "metadata": metadata or {},
        }
        self._versions[version["id"]] = version
        self._head = version["id"]
        self._save()
        return version


def test_legacy_silent_fallback_turns_red(tmp_path):
    with pytest.raises(AssertionError):
        _assert_corrupt_evidence_kept(_LegacyVersionStore, tmp_path)
