# SPDX-License-Identifier: MIT
"""MemoryStore 损坏恢复回归锁（第一档 A5，`memory.py:68`）。

旧实现 ``_load`` 用 ``except Exception: print(...)`` 吞掉损坏，内存里是空记忆；
下一次 ``add`` / ``update`` / ``forget`` 都会 ``_save``，于是空的「唯一版本」
直接把用户积累的长期记忆覆盖掉。本文件锁住三条：

  1. 文件缺失 = 首次启动：空记忆、不算错误、可正常写入；
  2. 文件损坏：先备份 ``*.corrupt-时间戳`` + 成对 reason.txt + ``last_error``，
     备份成功才允许后续写新内容，证据留在备份里；
  3. 备份失败 / 读不了（OSError）：不备份、不写入，``_save_blocked`` 挂实例，
     坏文件原样保留；二次写入不重复造备份。

反证：旧 ``except Exception: print`` 实现原样喂同一组断言，必须 AssertionError。

运行（不依赖 conftest / QApplication）:
    python -m pytest tests/test_memory_store_corruption_recovery.py -v --noconftest
"""
from __future__ import annotations

import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402

import mbdsdr_ai.memory as memory_module  # noqa: E402
from mbdsdr_ai.memory import Memory, MemoryStore  # noqa: E402

_CORRUPT_BYTES = b'{"next_id": 2, "memories": [ {"id": "mem_1", broken'


def _backups(tmp_path, name: str = "memory.json"):
    return sorted(
        p for p in tmp_path.iterdir()
        if p.name.startswith(name + ".corrupt-")
        and not p.name.endswith(".reason.txt")
    )


def _assert_corrupt_evidence_kept(factory, tmp_path) -> None:
    """损坏后写新记忆：必须先备份、必须留人话、备份字节等于原件。"""
    path = tmp_path / "memory.json"
    path.write_bytes(_CORRUPT_BYTES)
    store = factory(str(path))
    assert store.memories == {}

    store.add("损坏之后的新记忆")

    backups = _backups(tmp_path)
    assert len(backups) == 1, "损坏文件没有备份就被空记忆写回，原件已丢"
    assert backups[0].read_bytes() == _CORRUPT_BYTES, "备份不是原件字节"
    assert store.corrupt_backup == str(backups[0])
    assert "已备份为" in (store.last_error or ""), store.last_error
    assert (backups[0].with_name(backups[0].name + ".reason.txt")).exists(), \
        "备份旁没有成对的损坏理由"
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert [m["content"] for m in saved["memories"]] == ["损坏之后的新记忆"]


# ---------------------------------------------------------------------------
# 1. 缺失 = 首次启动，不是损坏
# ---------------------------------------------------------------------------
def test_missing_file_is_first_run_not_corruption(tmp_path):
    store = MemoryStore(storage_path=str(tmp_path / "memory.json"))
    assert store.memories == {}
    assert store.last_error is None
    assert store.corrupt_backup is None
    assert store._save_blocked is False
    assert _backups(tmp_path) == []

    store.add("第一次记住")
    assert (tmp_path / "memory.json").exists()
    assert _backups(tmp_path) == []


# ---------------------------------------------------------------------------
# 2. 损坏：备份 + last_error，写新内容不丢证据
# ---------------------------------------------------------------------------
def test_corrupt_file_is_backed_up_before_overwrite(tmp_path):
    _assert_corrupt_evidence_kept(MemoryStore, tmp_path)


def test_semantic_corruption_is_not_treated_as_empty(tmp_path):
    """合法 JSON 但条目缺 id/字段类型错，同样必须备份，不能静默空记忆。"""
    path = tmp_path / "memory.json"
    raw = json.dumps({"next_id": 2, "memories": [{"content": "缺 id"}]},
                     ensure_ascii=False).encode("utf-8")
    path.write_bytes(raw)

    store = MemoryStore(storage_path=str(path))
    assert store.memories == {}
    backups = _backups(tmp_path)
    assert len(backups) == 1
    assert backups[0].read_bytes() == raw
    assert "已备份为" in (store.last_error or "")


def test_second_write_does_not_create_an_extra_backup(tmp_path):
    path = tmp_path / "memory.json"
    path.write_bytes(_CORRUPT_BYTES)
    store = MemoryStore(storage_path=str(path))
    store.add("第一条")
    store.add("第二条")
    assert len(_backups(tmp_path)) == 1, "二次写入又造了一份备份"


# ---------------------------------------------------------------------------
# 3. 备份失败 / 读不了：拒绝写盘，坏文件原样保留
# ---------------------------------------------------------------------------
def test_backup_failure_blocks_memory_writes(tmp_path, monkeypatch):
    path = tmp_path / "memory.json"
    path.write_bytes(_CORRUPT_BYTES)
    monkeypatch.setattr(memory_module, "_backup_corrupt_memory", lambda _p: None)

    store = MemoryStore(storage_path=str(path))
    assert store._save_blocked is True, "备份失败后没有挂 blocked 状态"
    assert store.corrupt_backup is None
    assert "备份失败" in (store.last_error or "")

    store.add("这条不能落盘")
    assert path.read_bytes() == _CORRUPT_BYTES, "唯一证据被空记忆覆盖"
    assert _backups(tmp_path) == []
    assert "未保存" in (store.last_error or "")


def test_oserror_is_not_corruption_but_blocks(tmp_path):
    blocked = tmp_path / "memory.json"
    blocked.mkdir()
    store = MemoryStore(storage_path=str(blocked))

    assert store.memories == {}
    assert _backups(tmp_path) == []
    assert "读取失败" in (store.last_error or "")
    assert "已备份为" not in (store.last_error or "")
    assert store._save_blocked is True

    store.add("不写")
    assert blocked.is_dir(), "读不了的文件被当成空记忆替换掉了"
    assert not any(blocked.iterdir()), "读失败后仍在目标位置写了文件"


# ---------------------------------------------------------------------------
# 4. 反证：旧实现喂同一组断言必须红
# ---------------------------------------------------------------------------
class _LegacyMemoryStore:
    """旧实现：``except Exception: print`` 后按空记忆继续，下一次 add 覆盖。"""

    def __init__(self, path: str) -> None:
        self.storage_path = path
        self.memories = {}
        self._next_id = 1
        self.last_error = None
        self.corrupt_backup = None
        self._save_blocked = False
        self._load()

    def _load(self):
        if os.path.exists(self.storage_path):
            try:
                with open(self.storage_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for item in data.get("memories", []):
                    mem = Memory(**item)
                    self.memories[mem.id] = mem
                self._next_id = data.get("next_id", len(self.memories) + 1)
            except Exception as exc:  # noqa: BLE001 - 旧实现就是静默吞掉
                print(f"警告: 记忆文件加载失败: {exc}")

    def _save(self):
        os.makedirs(os.path.dirname(self.storage_path), exist_ok=True)
        data = {
            "next_id": self._next_id,
            "memories": [m.to_dict() for m in self.memories.values()],
        }
        with open(self.storage_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def add(self, content: str):
        mem_id = f"mem_{self._next_id}"
        self._next_id += 1
        mem = Memory(id=mem_id, content=content)
        self.memories[mem_id] = mem
        self._save()
        return mem


def test_legacy_silent_fallback_turns_red(tmp_path):
    with pytest.raises(AssertionError):
        _assert_corrupt_evidence_kept(_LegacyMemoryStore, tmp_path)


# ---------------------------------------------------------------------------
# 5. 文本口径：last_error 必须进 get_status_text，无错时必须消失
# ---------------------------------------------------------------------------
def test_status_text_carries_and_drops_persistence_error(tmp_path):
    db = tmp_path / "memory.json"
    db.write_bytes(_CORRUPT_BYTES)
    store = MemoryStore(storage_path=str(db))

    text = store.get_status_text()
    assert store.last_error, "损坏后 last_error 为空，这条用例前提不成立"
    assert "持久化错误" in text
    assert store.last_error in text, "有错误字段但状态文本看不到，等于没有提示通道"
    assert "已备份为" in text

    # 损坏文件已改名备份：新实例看到的是「缺失」= 干净状态，旧句子不能残留
    clean = MemoryStore(storage_path=str(db))
    assert clean.last_error is None
    clean_text = clean.get_status_text()
    assert "持久化错误" not in clean_text
    assert "已备份为" not in clean_text
