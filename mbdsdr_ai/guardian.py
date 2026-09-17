"""
MBDSDR AI 内核 - 守护者快照引擎
================================
Guardian Snapshots：修改前自动备份，失败自动回滚。

核心机制（对照之前 ~/.mbdsdr/guardian_snapshots/ 的设计）：
- 任何代码/配置/提示词修改前，自动创建快照
- 快照包含修改前的所有文件内容
- 修改后评估，如果失败自动回滚到快照
- 快照有 phase 状态机：created → committed → rolled_back
- 快照有 label 标识用途（"修改前: good", "修改前: bad" 等）
- 防幻觉变砖的核心防线：任何 AI 自修改都必须经过守护者

这是自进化的安全基石。没有守护者，AI 自进化就是裸奔。
"""

import os
import json
import time
import shutil
import hashlib
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Callable, Tuple


@dataclass
class Snapshot:
    """一个守护者快照。"""
    snap_id: str
    timestamp: float
    label: str
    source_path: str
    backup_path: str
    file_count: int = 0
    total_size: int = 0
    phase: str = "created"  # created / committed / rolled_back
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "snap_id": self.snap_id,
            "timestamp": self.timestamp,
            "label": self.label,
            "source_path": self.source_path,
            "backup_path": self.backup_path,
            "file_count": self.file_count,
            "total_size": self.total_size,
            "phase": self.phase,
            "metadata": self.metadata,
        }


class Guardian:
    """
    守护者快照引擎。

    使用方式：
        guardian = Guardian(store_path="~/.mbdsdr/guardian_snapshots")

        # 方式1：手动快照+回滚
        snap = guardian.create_snapshot("/path/to/src", label="修改前")
        # ... 修改文件 ...
        if failed:
            guardian.rollback(snap.snap_id)
        else:
            guardian.commit(snap.snap_id)

        # 方式2：with 语句自动守护（推荐）
        with guardian.protect("/path/to/src", label="自进化修改") as snap:
            # 修改文件
            modify_files()
            # 如果抛出异常，自动回滚
            # 如果正常退出，自动提交
            result = evaluate()
            if not result:
                raise Exception("评估失败，触发回滚")

        # 方式3：守护函数执行
        result = guardian.safely_execute(
            "/path/to/src",
            modify_func,
            evaluate_func,
            label="自进化"
        )
    """

    def __init__(self, store_path: str = "~/.mbdsdr/guardian_snapshots"):
        self.store_path = os.path.expanduser(store_path)
        self.snapshots_file = os.path.join(self.store_path, "snapshots.json")
        self.snapshots: Dict[str, Snapshot] = {}
        self._snap_counter = 0
        os.makedirs(self.store_path, exist_ok=True)
        self._load()

    # ── 持久化 ──────────────────────────────────────────

    def _load(self):
        """加载快照记录。"""
        if os.path.exists(self.snapshots_file):
            try:
                with open(self.snapshots_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for item in data:
                    snap = Snapshot(**item)
                    self.snapshots[snap.snap_id] = snap
                # 恢复计数器
                if self.snapshots:
                    max_id = max(int(s.snap_id.split("_")[-1]) for s in self.snapshots.values())
                    self._snap_counter = max_id + 1
            except Exception as e:
                print(f"警告: 守护者快照加载失败: {e}")

    def _save(self):
        """保存快照记录。"""
        data = [s.to_dict() for s in self.snapshots.values()]
        with open(self.snapshots_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    # ── 创建快照 ────────────────────────────────────────

    def create_snapshot(self, source_path: str, label: str = "",
                        metadata: Dict[str, Any] = None) -> Snapshot:
        """
        创建快照：备份 source_path 下的所有文件。

        返回创建的 Snapshot。
        """
        self._snap_counter += 1
        snap_id = f"snap_{int(time.time())}_{self._snap_counter:04d}"
        backup_path = os.path.join(self.store_path, snap_id)

        source_path = os.path.abspath(source_path)
        file_count = 0
        total_size = 0

        if os.path.exists(source_path):
            os.makedirs(backup_path, exist_ok=True)
            if os.path.isdir(source_path):
                for root, dirs, files in os.walk(source_path):
                    for fname in files:
                        src_file = os.path.join(root, fname)
                        rel_path = os.path.relpath(src_file, source_path)
                        dst_file = os.path.join(backup_path, rel_path)
                        os.makedirs(os.path.dirname(dst_file), exist_ok=True)
                        try:
                            shutil.copy2(src_file, dst_file)
                            file_count += 1
                            total_size += os.path.getsize(src_file)
                        except Exception:
                            pass
            else:
                # 单个文件
                shutil.copy2(source_path, backup_path)
                file_count = 1
                total_size = os.path.getsize(source_path)

        snap = Snapshot(
            snap_id=snap_id,
            timestamp=time.time(),
            label=label or f"快照_{snap_id}",
            source_path=source_path,
            backup_path=backup_path,
            file_count=file_count,
            total_size=total_size,
            phase="created",
            metadata=metadata or {},
        )

        self.snapshots[snap_id] = snap
        self._save()
        return snap

    # ── 提交快照 ────────────────────────────────────────

    def commit(self, snap_id: str) -> bool:
        """
        提交快照：修改成功，保留快照作为历史记录。
        （不删除备份，只是标记为 committed）
        """
        snap = self.snapshots.get(snap_id)
        if not snap:
            return False
        snap.phase = "committed"
        self._save()
        return True

    # ── 回滚快照 ────────────────────────────────────────

    def rollback(self, snap_id) -> Tuple[bool, str]:
        """
        回滚到快照：用备份覆盖源文件。

        返回 (成功, 消息)。
        """
        if isinstance(snap_id, Snapshot):
            snap_id = snap_id.snap_id
        snap = self.snapshots.get(snap_id)
        if not snap:
            return False, f"快照 {snap_id} 不存在"

        if not os.path.exists(snap.backup_path):
            return False, f"快照备份目录不存在: {snap.backup_path}"

        try:
            source_path = snap.source_path

            if os.path.isdir(snap.backup_path):
                # 目录回滚
                if os.path.exists(source_path):
                    if os.path.isdir(source_path):
                        shutil.rmtree(source_path)
                    else:
                        os.remove(source_path)
                # 检查备份目录中是否只有一个文件（单文件快照）
                backup_files = os.listdir(snap.backup_path)
                if len(backup_files) == 1 and os.path.isfile(os.path.join(snap.backup_path, backup_files[0])):
                    # 单文件快照：直接复制文件
                    shutil.copy2(os.path.join(snap.backup_path, backup_files[0]), source_path)
                else:
                    shutil.copytree(snap.backup_path, source_path)
            elif os.path.isfile(snap.backup_path):
                # 单文件回滚
                shutil.copy2(snap.backup_path, source_path)

            snap.phase = "rolled_back"
            self._save()
            return True, f"已回滚到 {snap_id}（{snap.label}），恢复 {snap.file_count} 个文件"

        except Exception as e:
            return False, f"回滚失败: {type(e).__name__}: {e}"

    def rollback_to_last_committed(self) -> Tuple[bool, str]:
        """回滚到最近一个 committed 快照。"""
        committed = [s for s in self.snapshots.values() if s.phase == "committed"]
        if not committed:
            return False, "没有已提交的快照可回滚"
        last = max(committed, key=lambda s: s.timestamp)
        return self.rollback(last.snap_id)

    # ── with 语句守护 ───────────────────────────────────

    def protect(self, source_path: str, label: str = "",
                metadata: Dict[str, Any] = None) -> "_GuardianContext":
        """
        with 语句守护：进入时创建快照，退出时根据是否异常决定提交或回滚。

        用法:
            with guardian.protect("/path/to/src", label="自进化") as snap:
                modify_files()
                if not evaluate():
                    raise Exception("评估失败")
            # 正常退出 → 自动提交
            # 异常退出 → 自动回滚
        """
        return _GuardianContext(self, source_path, label, metadata)

    # ── 安全执行函数 ────────────────────────────────────

    def safely_execute(
        self,
        source_path: str,
        modify_func: Callable,
        evaluate_func: Callable = None,
        label: str = "",
        metadata: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        """
        安全执行：创建快照 → 执行修改 → 评估 → 提交或回滚。

        modify_func: 修改函数，无参数，返回修改结果
        evaluate_func: 评估函数，接收 modify_func 的返回值，返回 bool（True=成功）
        label: 快照标签

        返回:
        {
            "success": bool,
            "snap_id": str,
            "phase": str,  # committed / rolled_back
            "result": Any,  # modify_func 的返回值
            "error": str,
        }
        """
        snap = self.create_snapshot(source_path, label, metadata)
        result = {
            "success": False,
            "snap_id": snap.snap_id,
            "phase": "created",
            "result": None,
            "error": "",
        }

        try:
            # 执行修改
            modify_result = modify_func()
            result["result"] = modify_result

            # 评估
            if evaluate_func:
                success = evaluate_func(modify_result)
            else:
                success = True  # 无评估函数默认成功

            if success:
                self.commit(snap.snap_id)
                result["success"] = True
                result["phase"] = "committed"
            else:
                self.rollback(snap.snap_id)
                result["phase"] = "rolled_back"
                result["error"] = "评估失败，已回滚"

        except Exception as e:
            self.rollback(snap.snap_id)
            result["phase"] = "rolled_back"
            result["error"] = f"{type(e).__name__}: {e}"

        return result

    # ── 查询与统计 ──────────────────────────────────────

    def get_snapshot(self, snap_id) -> Optional[Snapshot]:
        if isinstance(snap_id, Snapshot):
            snap_id = snap_id.snap_id
        return self.snapshots.get(snap_id)

    def list_snapshots(self, phase: str = None, limit: int = 20) -> List[Dict[str, Any]]:
        """列出快照。"""
        snaps = list(self.snapshots.values())
        if phase:
            snaps = [s for s in snaps if s.phase == phase]
        snaps.sort(key=lambda s: s.timestamp, reverse=True)
        return [s.to_dict() for s in snaps[:limit]]

    def get_stats(self) -> Dict[str, Any]:
        """获取守护者统计。"""
        total = len(self.snapshots)
        committed = sum(1 for s in self.snapshots.values() if s.phase == "committed")
        rolled_back = sum(1 for s in self.snapshots.values() if s.phase == "rolled_back")
        created = sum(1 for s in self.snapshots.values() if s.phase == "created")
        total_files = sum(s.file_count for s in self.snapshots.values())
        total_size = sum(s.total_size for s in self.snapshots.values())

        return {
            "total_snapshots": total,
            "committed": committed,
            "rolled_back": rolled_back,
            "created": created,
            "rollback_rate": round(rolled_back / max(1, total), 4),
            "total_files_backed_up": total_files,
            "total_size_bytes": total_size,
            "store_path": self.store_path,
        }

    def get_status_text(self) -> str:
        """获取人类可读的守护者状态。"""
        stats = self.get_stats()
        lines = [
            "=== MBDSDR 守护者快照 ===",
            f"总快照数: {stats['total_snapshots']}",
            f"已提交: {stats['committed']}, 已回滚: {stats['rolled_back']}, 创建中: {stats['created']}",
            f"回滚率: {stats['rollback_rate']:.1%}",
            f"备份文件总数: {stats['total_files_backed_up']}",
            f"存储路径: {stats['store_path']}",
        ]
        return "\n".join(lines)

    def cleanup_old(self, keep: int = 50):
        """清理旧快照（保留最近 keep 个）。"""
        snaps = sorted(self.snapshots.values(), key=lambda s: s.timestamp, reverse=True)
        to_delete = snaps[keep:]
        for snap in to_delete:
            if os.path.exists(snap.backup_path):
                shutil.rmtree(snap.backup_path, ignore_errors=True)
            del self.snapshots[snap.snap_id]
        self._save()
        return len(to_delete)


class _GuardianContext:
    """with 语句的上下文管理器。"""

    def __init__(self, guardian: Guardian, source_path: str, label: str, metadata: Dict[str, Any]):
        self.guardian = guardian
        self.source_path = source_path
        self.label = label
        self.metadata = metadata
        self.snapshot = None

    def __enter__(self):
        self.snapshot = self.guardian.create_snapshot(
            self.source_path, self.label, self.metadata
        )
        return self.snapshot

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            # 异常 → 回滚
            self.guardian.rollback(self.snapshot.snap_id)
            return False  # 不抑制异常
        else:
            # 正常 → 提交
            self.guardian.commit(self.snapshot.snap_id)
            return True
