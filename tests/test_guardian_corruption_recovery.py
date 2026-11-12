# SPDX-License-Identifier: MIT
"""Guardian 损坏恢复回归锁（第一档 A7，`guardian.py:98`）。

旧实现 ``_load`` 用 ``except Exception: print`` 吞掉损坏，内存里是空索引；
下一次 ``create_snapshot`` / ``commit`` / ``rollback`` 都会 ``_save``，把
``snapshots.json`` 覆盖成「只剩新快照」。磁盘上的快照目录还在，但索引没了，
``rollback`` 再也找不到 backup_path——自进化的安全网变成孤儿目录。

契约：
  1. 缺失 = 首次启动：空索引、不阻塞；
  2. 损坏（解析失败 / 顶层不是列表 / 条目缺 snap_id 等）→ 备份
     ``*.corrupt-时间戳`` + reason.txt + last_error，备份成功才允许写新内容；
  3. 备份失败 / OSError（被占用 / 是目录）→ 不备份、不写入，blocked 挂实例，
     坏索引原样保留；二次写入不重复造备份。

反证：旧 ``except Exception: print`` 实现喂同一组断言必须 AssertionError。

运行（不依赖 conftest / QApplication）:
    python -m pytest tests/test_guardian_corruption_recovery.py -v --noconftest
"""
from __future__ import annotations

import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402

import mbdsdr_ai.guardian as guardian_module  # noqa: E402
from mbdsdr_ai.guardian import Guardian, Snapshot  # noqa: E402

_CORRUPT_BYTES = (
    '[{"snap_id": "snap_1_0001", "label": "旧快照", broken').encode("utf-8")


def _backups(store_dir, name: str = "snapshots.json"):
    return sorted(
        p for p in store_dir.iterdir()
        if p.name.startswith(name + ".corrupt-")
        and not p.name.endswith(".reason.txt")
    )


def _assert_corrupt_evidence_kept(factory, tmp_path) -> None:
    store_dir = tmp_path / "guardian"
    store_dir.mkdir(exist_ok=True)
    path = store_dir / "snapshots.json"
    path.write_bytes(_CORRUPT_BYTES)
    store = factory(str(store_dir))
    assert store.snapshots == {}

    store.create_snapshot(str(tmp_path / "不存在的源"), label="损坏之后")

    backups = _backups(store_dir)
    assert len(backups) == 1, "损坏索引没有备份就被空索引写回，原件已丢"
    assert backups[0].read_bytes() == _CORRUPT_BYTES, "备份不是原件字节"
    assert store.corrupt_backup == str(backups[0])
    assert "已备份为" in (store.last_error or ""), store.last_error
    assert (backups[0].with_name(backups[0].name + ".reason.txt")).exists()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert [s["label"] for s in saved] == ["损坏之后"]


# ---------------------------------------------------------------------------
# 1. 缺失 = 首次启动
# ---------------------------------------------------------------------------
def test_missing_file_is_first_run_not_corruption(tmp_path):
    store = Guardian(store_path=str(tmp_path / "guardian"))
    assert store.snapshots == {}
    assert store.last_error is None
    assert store.corrupt_backup is None
    assert store._save_blocked is False

    snap = store.create_snapshot(str(tmp_path / "不存在"), label="首次")
    assert snap.snap_id in store.snapshots
    assert (tmp_path / "guardian" / "snapshots.json").exists()
    assert _backups(tmp_path / "guardian") == []


# ---------------------------------------------------------------------------
# 2. 损坏：备份 + last_error，写新内容不丢证据
# ---------------------------------------------------------------------------
def test_corrupt_file_is_backed_up_before_overwrite(tmp_path):
    _assert_corrupt_evidence_kept(Guardian, tmp_path)


def test_semantic_corruption_is_backed_up(tmp_path):
    store_dir = tmp_path / "guardian"
    store_dir.mkdir()
    path = store_dir / "snapshots.json"
    raw = json.dumps([{"label": "缺 snap_id"}], ensure_ascii=False).encode("utf-8")
    path.write_bytes(raw)

    store = Guardian(store_path=str(store_dir))
    assert store.snapshots == {}
    backups = _backups(store_dir)
    assert len(backups) == 1
    assert backups[0].read_bytes() == raw
    assert "已备份为" in (store.last_error or "")


def test_second_write_does_not_create_an_extra_backup(tmp_path):
    store_dir = tmp_path / "guardian"
    store_dir.mkdir()
    (store_dir / "snapshots.json").write_bytes(_CORRUPT_BYTES)
    store = Guardian(store_path=str(store_dir))
    store.create_snapshot(str(tmp_path / "a"), label="一")
    store.create_snapshot(str(tmp_path / "b"), label="二")
    assert len(_backups(store_dir)) == 1, "二次写入又造了一份备份"


# ---------------------------------------------------------------------------
# 3. 备份失败 / 读不了：拒绝写盘，坏索引原样保留
# ---------------------------------------------------------------------------
def test_backup_failure_blocks_snapshot_index_writes(tmp_path, monkeypatch):
    store_dir = tmp_path / "guardian"
    store_dir.mkdir()
    path = store_dir / "snapshots.json"
    path.write_bytes(_CORRUPT_BYTES)
    monkeypatch.setattr(guardian_module, "_backup_corrupt_snapshots",
                        lambda _p: None)

    store = Guardian(store_path=str(store_dir))
    assert store._save_blocked is True, "备份失败后没有挂 blocked 状态"
    assert store.corrupt_backup is None
    assert "备份失败" in (store.last_error or "")

    store.create_snapshot(str(tmp_path / "不存在"), label="不能落盘")
    assert path.read_bytes() == _CORRUPT_BYTES, "唯一索引被空快照覆盖"
    assert _backups(store_dir) == []
    assert "未保存" in (store.last_error or "")


def test_oserror_is_not_corruption_but_blocks(tmp_path):
    store_dir = tmp_path / "guardian"
    store_dir.mkdir()
    blocked = store_dir / "snapshots.json"
    blocked.mkdir()
    store = Guardian(store_path=str(store_dir))

    assert store.snapshots == {}
    assert _backups(store_dir) == []
    assert "读取失败" in (store.last_error or "")
    assert "已备份为" not in (store.last_error or "")
    assert store._save_blocked is True

    store.create_snapshot(str(tmp_path / "不存在"), label="不写")
    assert blocked.is_dir(), "读不了的索引被当成空索引替换掉了"
    assert not any(blocked.iterdir()), "读失败后仍在目标位置写了文件"


# ---------------------------------------------------------------------------
# 4. 已有文本通道：状态查询能看见错误，不新建界面
# ---------------------------------------------------------------------------
def test_status_text_surfaces_last_error(tmp_path, monkeypatch):
    store_dir = tmp_path / "guardian"
    store_dir.mkdir()
    (store_dir / "snapshots.json").write_bytes(_CORRUPT_BYTES)
    monkeypatch.setattr(guardian_module, "_backup_corrupt_snapshots",
                        lambda _p: None)
    store = Guardian(store_path=str(store_dir))

    text = store.get_status_text()
    assert "持久化错误" in text
    assert "备份失败" in text


# ---------------------------------------------------------------------------
# 5. 反证：旧实现喂同一组断言必须红
# ---------------------------------------------------------------------------
class _LegacyGuardian:
    """旧实现：``except Exception: print`` 后按空索引继续，下次 _save 覆盖。"""

    def __init__(self, store_path: str) -> None:
        self.store_path = store_path
        self.snapshots_file = os.path.join(store_path, "snapshots.json")
        self.snapshots = {}
        self._snap_counter = 0
        self.last_error = None
        self.corrupt_backup = None
        self._save_blocked = False
        os.makedirs(self.store_path, exist_ok=True)
        self._load()

    def _load(self):
        if os.path.exists(self.snapshots_file):
            try:
                with open(self.snapshots_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for item in data:
                    snap = Snapshot(**item)
                    self.snapshots[snap.snap_id] = snap
                if self.snapshots:
                    max_id = max(int(s.snap_id.split("_")[-1])
                                 for s in self.snapshots.values())
                    self._snap_counter = max_id + 1
            except Exception as exc:  # noqa: BLE001 - 旧实现就是静默吞掉
                print(f"警告: 守护者快照加载失败: {exc}")

    def _save(self):
        data = [s.to_dict() for s in self.snapshots.values()]
        with open(self.snapshots_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def create_snapshot(self, source_path, label="", metadata=None):
        self._snap_counter += 1
        snap = Snapshot(
            snap_id=f"snap_{len(self.snapshots)}_0001",
            timestamp=0.0,
            label=label,
            source_path=source_path,
            backup_path=os.path.join(self.store_path, "backup"),
            phase="created",
            metadata=metadata or {},
        )
        self.snapshots[snap.snap_id] = snap
        self._save()
        return snap


def test_legacy_silent_fallback_turns_red(tmp_path):
    with pytest.raises(AssertionError):
        _assert_corrupt_evidence_kept(_LegacyGuardian, tmp_path)
