"""
MBDSDR AI 内核 - 调度器
========================
Scheduler：定时任务调度。

核心能力（对照之前 ~/.mbdsdr/scheduler/ 的设计）：
- 定时任务（cron 表达式或固定间隔）
- 任务可以是工作流执行或工具调用
- 任务持久化到 JSON
- 支持一次性任务和周期性任务
- 支持任务启用/禁用
- 支持任务执行历史记录

典型用途：
- 定时接收 NOAA 卫星过境
- 定时扫描特定频段
- 定时录制特定信号
- 定时执行干扰源扫描
"""

import os
import json
import time
import threading
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Callable


@dataclass
class ScheduledTask:
    """一个定时任务。"""
    task_id: str
    name: str
    description: str = ""
    task_type: str = "workflow"  # workflow / tool / command
    target: str = ""  # workflow name or tool name
    params: Dict[str, Any] = field(default_factory=dict)
    schedule_type: str = "interval"  # interval / cron / once
    interval_seconds: int = 3600  # interval 类型的间隔
    cron_expression: str = ""  # cron 类型的表达式（简化版）
    run_at: float = 0.0  # once 类型的执行时间戳
    enabled: bool = True
    last_run: float = 0.0
    next_run: float = 0.0
    run_count: int = 0
    success_count: int = 0
    created_at: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "name": self.name,
            "description": self.description,
            "task_type": self.task_type,
            "target": self.target,
            "params": self.params,
            "schedule_type": self.schedule_type,
            "interval_seconds": self.interval_seconds,
            "cron_expression": self.cron_expression,
            "run_at": self.run_at,
            "enabled": self.enabled,
            "last_run": self.last_run,
            "next_run": self.next_run,
            "run_count": self.run_count,
            "success_count": self.success_count,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ScheduledTask":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class Scheduler:
    """
    定时任务调度器。

    使用方式：
        scheduler = Scheduler()

        # 添加定时任务（每小时执行一次 NOAA 卫星接收工作流）
        scheduler.add_task(
            name="NOAA卫星定时接收",
            task_type="workflow",
            target="noaa_apt_receive_decode",
            schedule_type="interval",
            interval_seconds=3600,
        )

        # 启动调度线程
        scheduler.start()

        # 或者手动检查并执行到期任务
        scheduler.tick()
    """

    def __init__(self, tasks_file: str = "~/.mbdsdr/scheduler/tasks.json"):
        self.tasks_file = os.path.expanduser(tasks_file)
        self.tasks: Dict[str, ScheduledTask] = {}
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._task_counter = 0
        self.workflow_engine = None  # 外部注入
        self.tool_executor = None  # 外部注入
        os.makedirs(os.path.dirname(self.tasks_file), exist_ok=True)
        self._load()

    # ── 持久化 ──────────────────────────────────────────

    def _load(self):
        """加载任务。"""
        if os.path.exists(self.tasks_file):
            try:
                with open(self.tasks_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for item in data:
                    task = ScheduledTask.from_dict(item)
                    self.tasks[task.task_id] = task
                if self.tasks:
                    max_id = max(int(t.task_id.split("_")[-1]) for t in self.tasks.values())
                    self._task_counter = max_id + 1
            except Exception as e:
                print(f"警告: 调度器任务加载失败: {e}")

    def _save(self):
        """保存任务。"""
        data = [t.to_dict() for t in self.tasks.values()]
        with open(self.tasks_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    # ── 任务管理 ────────────────────────────────────────

    def add_task(
        self,
        name: str,
        task_type: str = "workflow",
        target: str = "",
        params: Dict[str, Any] = None,
        schedule_type: str = "interval",
        interval_seconds: int = 3600,
        cron_expression: str = "",
        run_at: float = 0.0,
        enabled: bool = True,
        description: str = "",
    ) -> ScheduledTask:
        """添加定时任务。"""
        self._task_counter += 1
        task_id = f"task_{int(time.time())}_{self._task_counter:04d}"

        now = time.time()
        if schedule_type == "once":
            next_run = run_at if run_at > 0 else now + 60
        else:
            next_run = now + interval_seconds

        task = ScheduledTask(
            task_id=task_id,
            name=name,
            description=description,
            task_type=task_type,
            target=target,
            params=params or {},
            schedule_type=schedule_type,
            interval_seconds=interval_seconds,
            cron_expression=cron_expression,
            run_at=run_at,
            enabled=enabled,
            next_run=next_run,
            created_at=now,
        )

        self.tasks[task_id] = task
        self._save()
        return task

    def _resolve_task_id(self, task_id) -> str:
        """解析任务 ID（支持传入 ScheduledTask 对象）。"""
        if isinstance(task_id, ScheduledTask):
            return task_id.task_id
        return task_id

    def remove_task(self, task_id) -> bool:
        """删除任务。"""
        task_id = self._resolve_task_id(task_id)
        if task_id in self.tasks:
            del self.tasks[task_id]
            self._save()
            return True
        return False

    def enable_task(self, task_id) -> bool:
        """启用任务。"""
        task_id = self._resolve_task_id(task_id)
        task = self.tasks.get(task_id)
        if task:
            task.enabled = True
            task.next_run = time.time() + task.interval_seconds
            self._save()
            return True
        return False

    def disable_task(self, task_id) -> bool:
        """禁用任务。"""
        task_id = self._resolve_task_id(task_id)
        task = self.tasks.get(task_id)
        if task:
            task.enabled = False
            self._save()
            return True
        return False

    def get_task(self, task_id) -> Optional[ScheduledTask]:
        task_id = self._resolve_task_id(task_id)
        return self.tasks.get(task_id)

    def list_tasks(self, enabled_only: bool = False) -> List[Dict[str, Any]]:
        """列出所有任务。"""
        tasks = list(self.tasks.values())
        if enabled_only:
            tasks = [t for t in tasks if t.enabled]
        tasks.sort(key=lambda t: t.next_run)
        return [t.to_dict() for t in tasks]

    # ── 任务执行 ────────────────────────────────────────

    def _execute_task(self, task: ScheduledTask) -> bool:
        """执行一个任务。"""
        try:
            if task.task_type == "workflow" and self.workflow_engine:
                result = self.workflow_engine.execute(task.target, task.params)
                return result.success
            elif task.task_type == "tool" and self.tool_executor:
                self.tool_executor(task.target, task.params)
                return True
            else:
                return False
        except Exception as e:
            print(f"任务 {task.name} 执行失败: {e}")
            return False

    def tick(self) -> int:
        """
        检查并执行所有到期任务。

        返回执行的任务数。
        """
        now = time.time()
        executed = 0

        for task in self.tasks.values():
            if not task.enabled:
                continue
            if task.next_run > now:
                continue

            # 执行任务
            success = self._execute_task(task)
            task.last_run = now
            task.run_count += 1
            if success:
                task.success_count += 1

            # 计算下次执行时间
            if task.schedule_type == "once":
                task.enabled = False  # 一次性任务执行后禁用
            elif task.schedule_type == "interval":
                task.next_run = now + task.interval_seconds
            # cron 类型暂不支持复杂解析，按 interval 处理

            executed += 1

        if executed > 0:
            self._save()

        return executed

    # ── 后台线程 ────────────────────────────────────────

    def start(self):
        """启动后台调度线程。"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        """停止后台调度线程。"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self):
        """后台调度循环。"""
        while self._running:
            try:
                self.tick()
            except Exception as e:
                print(f"调度器循环错误: {e}")
            time.sleep(30)  # 每 30 秒检查一次

    # ── 统计 ────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        """获取调度器统计。"""
        total = len(self.tasks)
        enabled = sum(1 for t in self.tasks.values() if t.enabled)
        total_runs = sum(t.run_count for t in self.tasks.values())
        total_success = sum(t.success_count for t in self.tasks.values())
        return {
            "total_tasks": total,
            "enabled_tasks": enabled,
            "disabled_tasks": total - enabled,
            "total_runs": total_runs,
            "total_success": total_success,
            "success_rate": round(total_success / max(1, total_runs), 4),
            "running": self._running,
        }

    def get_status_text(self) -> str:
        """获取人类可读的调度器状态。"""
        stats = self.get_stats()
        lines = [
            "=== MBDSDR 调度器 ===",
            f"任务总数: {stats['total_tasks']} (启用 {stats['enabled_tasks']}, 禁用 {stats['disabled_tasks']})",
            f"总执行次数: {stats['total_runs']} (成功 {stats['total_success']}, 成功率 {stats['success_rate']:.1%})",
            f"后台运行: {'是' if stats['running'] else '否'}",
        ]
        if self.tasks:
            lines.append("\n任务列表:")
            for task in sorted(self.tasks.values(), key=lambda t: t.next_run):
                status = "启用" if task.enabled else "禁用"
                next_run = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(task.next_run)) if task.next_run > 0 else "N/A"
                lines.append(f"  - [{status}] {task.name}: {task.target} (下次: {next_run})")
        return "\n".join(lines)
