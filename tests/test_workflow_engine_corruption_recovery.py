# SPDX-License-Identifier: MIT
"""WorkflowEngine 损坏恢复回归锁（第一档 A5，`workflow_engine.py:176`）。

旧实现 ``_load_user_workflows`` 用 ``except Exception: print`` 跳过坏文件，
``save_workflow`` 只按名字拼 ``{name}.json`` 直接覆盖。两者叠加的后果：
用户已有一份 ``foo``，文件坏了没提示 → 界面照样能保存同名 → 新内容写回
同一路径，用户原来那份被删掉，而界面只认名字，看不出区别。

判断（写在这里，便于以后回溯）：损坏时**拒绝保存同名**，而不是「跳过坏文件
然后覆盖」。理由是覆盖语义上是删除，且用户没有做出这个决定；正确做法是让
用户改名新建，或显式处理备份。

契约：
  1. 缺失 = 首次启动，正常可写；
  2. 单个文件损坏 → 备份 ``*.corrupt-时间戳`` + reason.txt + last_error，
     其它工作流照常加载；同名保存被拒绝，且不产生任何新文件；
  3. 读不了（OSError）→ 不备份、不移动原文件，同样拒绝同名覆盖；
  4. 拒绝状态跨重启有效（只剩 ``*.corrupt-*`` 备份时仍拒绝同名）。

反证：旧 ``except Exception: print`` + 直接覆盖实现喂同一组断言必须 AssertionError。

运行（不依赖 conftest / QApplication）:
    python -m pytest tests/test_workflow_engine_corruption_recovery.py -v --noconftest
"""
from __future__ import annotations

import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402

import mbdsdr_ai.workflow_engine as workflow_engine_module  # noqa: E402
from mbdsdr_ai.workflow_engine import Workflow, WorkflowEngine  # noqa: E402

_CORRUPT_BYTES = (
    '{"name": "foo", "description": "旧工作流", "steps": [ broken'
).encode("utf-8")


def _backups(wf_dir, stem: str = "foo"):
    return sorted(
        p for p in wf_dir.iterdir()
        if p.name.startswith(stem + ".json.corrupt-")
        and not p.name.endswith(".reason.txt")
    )


def _assert_corrupt_evidence_kept(factory, tmp_path) -> None:
    wf_dir = tmp_path / "workflows"
    wf_dir.mkdir(exist_ok=True)
    (wf_dir / "foo.json").write_bytes(_CORRUPT_BYTES)
    engine = factory(str(wf_dir))
    assert "foo" not in engine.workflows

    backups = _backups(wf_dir)
    assert len(backups) == 1, "损坏文件没有备份就被跳过，用户原件去向不明"
    assert backups[0].read_bytes() == _CORRUPT_BYTES, "备份不是原件字节"
    assert "已备份为" in (engine.last_error or ""), engine.last_error
    assert (backups[0].with_name(backups[0].name + ".reason.txt")).exists()

    # 同名保存必须拒绝，且不能写出新的 foo.json 顶着用户原来的名字
    ok = engine.save_workflow(Workflow(name="foo"))
    assert ok is False, "损坏后同名保存被放行 = 静默删掉用户原来那份"
    assert not (wf_dir / "foo.json").exists()
    assert "未保存" in (engine.last_error or ""), engine.last_error
    assert "已拒绝" in (engine.last_error or ""), engine.last_error


# ---------------------------------------------------------------------------
# 1. 缺失 = 首次启动
# ---------------------------------------------------------------------------
def test_missing_dir_is_first_run(tmp_path):
    engine = WorkflowEngine(workflows_dir=str(tmp_path / "workflows"))
    assert engine.workflows == {}
    assert engine.last_error is None
    assert engine._blocked_names == {}
    assert engine.save_workflow(Workflow(name="新流程")) is True
    assert (tmp_path / "workflows" / "新流程.json").exists()


def test_valid_file_loads_without_false_positive(tmp_path):
    wf_dir = tmp_path / "workflows"
    wf_dir.mkdir()
    wf = Workflow(name="可读流程", steps=[])
    engine = WorkflowEngine(workflows_dir=str(wf_dir))
    assert engine.save_workflow(wf) is True

    reloaded = WorkflowEngine(workflows_dir=str(wf_dir))
    assert "可读流程" in reloaded.workflows
    assert reloaded.last_error is None
    assert reloaded._blocked_names == {}


# ---------------------------------------------------------------------------
# 2. 损坏：备份 + 拒绝同名 + 其它工作流不受影响
# ---------------------------------------------------------------------------
def test_corrupt_file_blocks_same_name_save(tmp_path):
    _assert_corrupt_evidence_kept(WorkflowEngine, tmp_path)


def test_other_workflows_still_load(tmp_path):
    wf_dir = tmp_path / "workflows"
    wf_dir.mkdir()
    (wf_dir / "foo.json").write_bytes(_CORRUPT_BYTES)
    good = Workflow(name="好的", steps=[])
    (wf_dir / "好的.json").write_text(
        json.dumps(good.to_dict(), ensure_ascii=False), encoding="utf-8")

    engine = WorkflowEngine(workflows_dir=str(wf_dir))
    assert "好的" in engine.workflows, "一个坏文件不该拖垮其它工作流"
    assert "foo" not in engine.workflows


def test_semantic_corruption_is_backed_up(tmp_path):
    wf_dir = tmp_path / "workflows"
    wf_dir.mkdir()
    raw = json.dumps(["不是对象"], ensure_ascii=False).encode("utf-8")
    (wf_dir / "foo.json").write_bytes(raw)

    engine = WorkflowEngine(workflows_dir=str(wf_dir))
    backups = _backups(wf_dir)
    assert len(backups) == 1
    assert backups[0].read_bytes() == raw
    assert engine.save_workflow(Workflow(name="foo")) is False


def test_missing_name_is_corruption(tmp_path):
    wf_dir = tmp_path / "workflows"
    wf_dir.mkdir()
    (wf_dir / "foo.json").write_text(json.dumps({"steps": []}), encoding="utf-8")

    WorkflowEngine(workflows_dir=str(wf_dir))
    assert _backups(wf_dir), "没有 name 的工作流无法归属，必须当损坏处理"


def test_renamed_save_still_works(tmp_path):
    wf_dir = tmp_path / "workflows"
    wf_dir.mkdir()
    (wf_dir / "foo.json").write_bytes(_CORRUPT_BYTES)
    engine = WorkflowEngine(workflows_dir=str(wf_dir))

    assert engine.save_workflow(Workflow(name="foo2")) is True
    assert (wf_dir / "foo2.json").exists()
    assert "foo2" in engine.workflows


def test_refusal_survives_restart(tmp_path):
    wf_dir = tmp_path / "workflows"
    wf_dir.mkdir()
    (wf_dir / "foo.json").write_bytes(_CORRUPT_BYTES)
    WorkflowEngine(workflows_dir=str(wf_dir))       # 第一次：备份并改名

    reloaded = WorkflowEngine(workflows_dir=str(wf_dir))
    assert "foo" in reloaded._blocked_names, "重启后 guard 丢了，等于允许覆盖备份对应名字"
    assert reloaded.save_workflow(Workflow(name="foo")) is False
    assert not (wf_dir / "foo.json").exists()


# ---------------------------------------------------------------------------
# 3. 读不了（OSError）：不备份、不移动、拒绝同名
# ---------------------------------------------------------------------------
def test_oserror_is_not_backed_up_but_blocked(tmp_path):
    wf_dir = tmp_path / "workflows"
    wf_dir.mkdir()
    blocked = wf_dir / "foo.json"
    blocked.mkdir()

    engine = WorkflowEngine(workflows_dir=str(wf_dir))
    assert engine.workflows == {}
    assert _backups(wf_dir) == []
    assert "读取失败" in (engine.last_error or "")
    assert "已备份为" not in (engine.last_error or "")

    assert engine.save_workflow(Workflow(name="foo")) is False
    assert blocked.is_dir(), "读不了的工作流被同名新文件替换掉了"
    assert not any(blocked.iterdir())


def test_write_oserror_keeps_memory_clean(tmp_path, monkeypatch):
    wf_dir = tmp_path / "workflows"
    wf_dir.mkdir()
    engine = WorkflowEngine(workflows_dir=str(wf_dir))

    def _boom(*_args, **_kwargs):
        raise OSError("磁盘满")

    monkeypatch.setattr(workflow_engine_module.json, "dump", _boom)
    assert engine.save_workflow(Workflow(name="写不进")) is False
    assert "写入失败" in (engine.last_error or "")
    assert "写不进" not in engine.workflows, "写失败的版本不得留在内存里冒充已保存"
    assert not (wf_dir / "写不进.json").exists(), "失败写入留下了半截文件"
    assert not (wf_dir / "写不进.json.tmp").exists(), "临时文件没有清理"


# ---------------------------------------------------------------------------
# 4. 反证：旧实现喂同一组断言必须红
# ---------------------------------------------------------------------------
class _LegacyWorkflowEngine:
    """旧实现：``except Exception: print`` 跳过 + 同名直接覆盖。"""

    def __init__(self, workflows_dir: str) -> None:
        self.workflows_dir = workflows_dir
        self.workflows = {}
        self.last_error = None
        self._blocked_names = {}
        os.makedirs(self.workflows_dir, exist_ok=True)
        self._load_user_workflows()

    def _load_user_workflows(self):
        for fname in os.listdir(self.workflows_dir):
            if fname.endswith(".json"):
                try:
                    with open(os.path.join(self.workflows_dir, fname),
                              "r", encoding="utf-8") as f:
                        data = json.load(f)
                    wf = Workflow.from_dict(data)
                    self.workflows[wf.name] = wf
                except Exception as exc:  # noqa: BLE001 - 旧实现就是静默跳过
                    print(f"警告: 加载工作流 {fname} 失败: {exc}")

    def save_workflow(self, workflow):
        self.workflows[workflow.name] = workflow
        path = os.path.join(self.workflows_dir, f"{workflow.name}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(workflow.to_dict(), f, indent=2, ensure_ascii=False)


def test_legacy_silent_skip_turns_red(tmp_path):
    with pytest.raises(AssertionError):
        _assert_corrupt_evidence_kept(_LegacyWorkflowEngine, tmp_path)
