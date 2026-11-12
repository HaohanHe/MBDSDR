# SPDX-License-Identifier: MIT
"""Scheduler 损坏恢复回归锁（第一档 A4，`scheduler.py:113`）。

旧实现 ``_load`` 用 ``except Exception: print`` 吞掉损坏，内存里是空任务表；
下一次 ``add_task`` / ``enable_task`` / ``tick`` 都会 ``_save``，把用户的
定时任务（如 NOAA 过境录制）整份覆盖成「只剩刚加的那条」。

契约：
  1. 缺失 = 首次启动：空任务、不阻塞；
  2. 损坏（解析失败 / 顶层不是列表 / 条目缺 task_id/name）→ 备份
     ``*.corrupt-时间戳`` + reason.txt + last_error，备份成功才允许写新内容；
  3. 备份失败 / OSError（被占用 / 是目录）→ 不备份、不写入，blocked 挂实例，
     坏文件原样保留；二次写入不重复造备份。

反证：旧 ``except Exception: print`` 实现喂同一组断言必须 AssertionError。

运行（不依赖 conftest / QApplication）:
    python -m pytest tests/test_scheduler_corruption_recovery.py -v --noconftest
"""
from __future__ import annotations

import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402

import mbdsdr_ai.scheduler as scheduler_module  # noqa: E402
from mbdsdr_ai.scheduler import ScheduledTask, Scheduler  # noqa: E402

_CORRUPT_BYTES = b'[{"task_id": "task_1_0001", "name": "NOAA", broken'


def _backups(tmp_path, name: str = "tasks.json"):
    return sorted(
        p for p in tmp_path.iterdir()
        if p.name.startswith(name + ".corrupt-")
        and not p.name.endswith(".reason.txt")
    )


def _assert_corrupt_evidence_kept(factory, tmp_path) -> None:
    path = tmp_path / "tasks.json"
    path.write_bytes(_CORRUPT_BYTES)
    store = factory(str(path))
    assert store.tasks == {}

    store.add_task("损坏之后的新任务", task_type="tool", target="scan")

    backups = _backups(tmp_path)
    assert len(backups) == 1, "损坏文件没有备份就被空任务写回，原件已丢"
    assert backups[0].read_bytes() == _CORRUPT_BYTES, "备份不是原件字节"
    assert store.corrupt_backup == str(backups[0])
    assert "已备份为" in (store.last_error or ""), store.last_error
    assert (backups[0].with_name(backups[0].name + ".reason.txt")).exists()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert [t["name"] for t in saved] == ["损坏之后的新任务"]


# ---------------------------------------------------------------------------
# 1. 缺失 = 首次启动
# ---------------------------------------------------------------------------
def test_missing_file_is_first_run_not_corruption(tmp_path):
    store = Scheduler(tasks_file=str(tmp_path / "tasks.json"))
    assert store.tasks == {}
    assert store.last_error is None
    assert store.corrupt_backup is None
    assert store._save_blocked is False

    task = store.add_task("首次任务", task_type="tool", target="scan")
    assert task.task_id in store.tasks
    assert (tmp_path / "tasks.json").exists()
    assert _backups(tmp_path) == []


# ---------------------------------------------------------------------------
# 2. 损坏：备份 + last_error，写新内容不丢证据
# ---------------------------------------------------------------------------
def test_corrupt_file_is_backed_up_before_overwrite(tmp_path):
    _assert_corrupt_evidence_kept(Scheduler, tmp_path)


def test_semantic_corruption_is_backed_up(tmp_path):
    path = tmp_path / "tasks.json"
    raw = json.dumps([{"name": "没有 task_id"}], ensure_ascii=False).encode("utf-8")
    path.write_bytes(raw)

    store = Scheduler(tasks_file=str(path))
    assert store.tasks == {}
    backups = _backups(tmp_path)
    assert len(backups) == 1
    assert backups[0].read_bytes() == raw
    assert "已备份为" in (store.last_error or "")


def test_second_write_does_not_create_an_extra_backup(tmp_path):
    path = tmp_path / "tasks.json"
    path.write_bytes(_CORRUPT_BYTES)
    store = Scheduler(tasks_file=str(path))
    store.add_task("第一条", task_type="tool", target="a")
    store.add_task("第二条", task_type="tool", target="b")
    assert len(_backups(tmp_path)) == 1, "二次写入又造了一份备份"


# ---------------------------------------------------------------------------
# 3. 备份失败 / 读不了：拒绝写盘，坏文件原样保留
# ---------------------------------------------------------------------------
def test_backup_failure_blocks_task_writes(tmp_path, monkeypatch):
    path = tmp_path / "tasks.json"
    path.write_bytes(_CORRUPT_BYTES)
    monkeypatch.setattr(scheduler_module, "_backup_corrupt_tasks", lambda _p: None)

    store = Scheduler(tasks_file=str(path))
    assert store._save_blocked is True, "备份失败后没有挂 blocked 状态"
    assert store.corrupt_backup is None
    assert "备份失败" in (store.last_error or "")

    store.add_task("这条不能落盘", task_type="tool", target="scan")
    assert path.read_bytes() == _CORRUPT_BYTES, "唯一证据被空任务覆盖"
    assert _backups(tmp_path) == []
    assert "未保存" in (store.last_error or "")


def test_oserror_is_not_corruption_but_blocks(tmp_path):
    blocked = tmp_path / "tasks.json"
    blocked.mkdir()
    store = Scheduler(tasks_file=str(blocked))

    assert store.tasks == {}
    assert _backups(tmp_path) == []
    assert "读取失败" in (store.last_error or "")
    assert "已备份为" not in (store.last_error or "")
    assert store._save_blocked is True

    store.add_task("不写", task_type="tool", target="scan")
    assert blocked.is_dir(), "读不了的文件被当成空任务替换掉了"
    assert not any(blocked.iterdir()), "读失败后仍在目标位置写了文件"


# ---------------------------------------------------------------------------
# 4. 已有文本通道：状态查询能看见错误，不新建界面
# ---------------------------------------------------------------------------
def test_status_text_surfaces_last_error(tmp_path, monkeypatch):
    path = tmp_path / "tasks.json"
    path.write_bytes(_CORRUPT_BYTES)
    monkeypatch.setattr(scheduler_module, "_backup_corrupt_tasks", lambda _p: None)
    store = Scheduler(tasks_file=str(path))

    text = store.get_status_text()
    assert "持久化错误" in text
    assert "备份失败" in text


# ---------------------------------------------------------------------------
# 5. 反证：旧实现喂同一组断言必须红
# ---------------------------------------------------------------------------
class _LegacyScheduler:
    """旧实现：``except Exception: print`` 后按空任务继续，下次 add 覆盖。"""

    def __init__(self, path: str) -> None:
        self.tasks_file = path
        self.tasks = {}
        self._task_counter = 0
        self.last_error = None
        self.corrupt_backup = None
        self._save_blocked = False
        self._load()

    def _load(self):
        if os.path.exists(self.tasks_file):
            try:
                with open(self.tasks_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for item in data:
                    task = ScheduledTask.from_dict(item)
                    self.tasks[task.task_id] = task
                if self.tasks:
                    max_id = max(int(t.task_id.split("_")[-1])
                                 for t in self.tasks.values())
                    self._task_counter = max_id + 1
            except Exception as exc:  # noqa: BLE001 - 旧实现就是静默吞掉
                print(f"警告: 调度器任务加载失败: {exc}")

    def _save(self):
        data = [t.to_dict() for t in self.tasks.values()]
        with open(self.tasks_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def add_task(self, name, task_type="tool", target="scan", **_kwargs):
        self._task_counter += 1
        task = ScheduledTask(
            task_id=f"task_new_{self._task_counter:04d}",
            name=name, task_type=task_type, target=target)
        self.tasks[task.task_id] = task
        self._save()
        return task


def test_legacy_silent_fallback_turns_red(tmp_path):
    with pytest.raises(AssertionError):
        _assert_corrupt_evidence_kept(_LegacyScheduler, tmp_path)
