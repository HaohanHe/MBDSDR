# SPDX-License-Identifier: MIT
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
import logging
import time
import threading
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

from .corrupt_evidence import write_reason

logger = logging.getLogger(__name__)


def _backup_corrupt_tasks(path: str) -> Optional[str]:
    """把损坏的任务文件改名成 ``*.corrupt-时间戳``，返回备份路径（失败 None）。"""
    backup = f"{path}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}"
    try:
        os.replace(path, backup)
    except OSError as exc:
        logger.error("调度任务文件损坏备份失败：%s（%s）", path, exc)
        return None
    return backup


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

        # 添加定时任务（目标须为已存在的用户/AI 自定义工作流；引擎无内置流程）
        scheduler.add_task(
            name="自定义工作流定时执行",
            task_type="workflow",
            target="my_user_workflow",
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
        #: 上次读取失败的人话说明（None = 正常）。目前无独立界面消费，
        #: 但会进 get_status_text()，接线更完整的提示等 main_window.py 那一笔。
        self.last_error: Optional[str] = None
        #: 损坏文件的备份路径（None = 未损坏或备份失败）。
        self.corrupt_backup: Optional[str] = None
        #: 读失败/损坏且备份失败时拒绝写盘：add/enable/tick 都会 _save，
        #: 一旦放行就会把「无任务」写成唯一版本。
        self._save_blocked = False
        os.makedirs(os.path.dirname(self.tasks_file), exist_ok=True)
        self._load()

    # ── 持久化 ──────────────────────────────────────────

    def _load(self):
        """加载任务：缺失 = 空任务；损坏先备份；读不了不写。

        三种情况分开：
          * 文件不存在 —— 首次启动正常路径，空任务，不算错误；
          * 文件损坏（解析失败 / 顶层不是列表 / 条目缺 task_id/name）——
            备份原件 + last_error；备份失败则 _save_blocked，拒绝覆盖；
          * OSError（被占用 / 是目录 / 无权限）—— 不备份、不写入、置 block。
        """
        if not os.path.exists(self.tasks_file):
            return
        try:
            with open(self.tasks_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except OSError as exc:
            self.last_error = (
                f"调度任务文件读取失败（{exc.__class__.__name__}）：{self.tasks_file}；"
                "本次不写入，避免覆盖可能仍可恢复的文件")
            self._save_blocked = True
            logger.warning("调度任务文件读取失败（非损坏，不备份）：%s", exc)
            return
        except ValueError as exc:
            self._mark_corrupt(f"解析失败（{exc.__class__.__name__}: {exc}）")
            return
        try:
            if not isinstance(data, list):
                raise ValueError("根节点不是列表")
            loaded: Dict[str, ScheduledTask] = {}
            for item in data:
                task = ScheduledTask.from_dict(item)
                loaded[task.task_id] = task
            next_counter = 0
            if loaded:
                max_id = max(int(t.task_id.split("_")[-1]) for t in loaded.values())
                next_counter = max_id + 1
        except (TypeError, KeyError, ValueError, AttributeError) as exc:
            self._mark_corrupt(f"条目非法（{exc.__class__.__name__}: {exc}）")
            return
        self.tasks = loaded
        self._task_counter = next_counter
        self.last_error = None
        self.corrupt_backup = None
        self._save_blocked = False

    def _mark_corrupt(self, reason: str) -> None:
        """损坏：先备份原件再留人话错误；备份失败则禁止后续写盘。"""
        self.corrupt_backup = _backup_corrupt_tasks(self.tasks_file)
        self._save_blocked = self.corrupt_backup is None
        message = (
            f"调度任务文件损坏（{reason}）：{self.tasks_file}；"
            f"原文件已备份为 {self.corrupt_backup or '（备份失败）'}，"
            "本次按无任务启动")
        if self.corrupt_backup:
            write_reason(self.corrupt_backup, message, logger)
        self.last_error = message
        logger.warning("调度任务文件不可用（%s）：%s；备份=%s", self.tasks_file,
                       reason, self.corrupt_backup or "（备份失败）")

    def _save(self) -> bool:
        """保存任务。读失败/损坏未备份时拒绝写盘。"""
        if self._save_blocked:
            self.last_error = (
                f"调度任务未保存：{self.tasks_file} 读取失败或已损坏且无法备份；"
                "为避免覆盖唯一证据，本次不写入")
            logger.error("调度任务写入被拒绝（文件不可用且未备份）：%s",
                         self.tasks_file)
            return False
        data = [t.to_dict() for t in self.tasks.values()]
        with open(self.tasks_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True

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
        elif schedule_type == "cron" and cron_expression:
            # 初值走 cron 解析，而不是 interval_seconds（D10 接真实）
            next_run = self._next_cron_run(cron_expression, now)
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

    @staticmethod
    def _parse_cron_field(field: str, min_val: int, max_val: int) -> set:
        """解析单个 cron 字段，返回匹配的整数集合。支持 *, */N, a-b, a,b,c。"""
        values = set()
        for part in field.split(","):
            part = part.strip()
            if part == "*":
                values.update(range(min_val, max_val + 1))
            elif part.startswith("*/"):
                step = int(part[2:])
                values.update(range(min_val, max_val + 1, step))
            elif "-" in part:
                a, b = part.split("-", 1)
                values.update(range(int(a), int(b) + 1))
            else:
                values.add(int(part))
        return values

    @classmethod
    def _next_cron_run(cls, cron_expr: str, after_ts: float) -> float:
        """根据简化版 5 字段 cron 表达式计算 after_ts 之后的下次执行时间戳。

        字段：分 时 日 月 周（0=周日）。不点名则按每分钟步进查找。
        解析失败时回退到 1 小时间隔。
        """
        import datetime
        try:
            parts = cron_expr.strip().split()
            if len(parts) != 5:
                return after_ts + 3600
            minutes = cls._parse_cron_field(parts[0], 0, 59)
            hours = cls._parse_cron_field(parts[1], 0, 23)
            days = cls._parse_cron_field(parts[2], 1, 31)
            months = cls._parse_cron_field(parts[3], 1, 12)
            # cron 约定周 0=周日；Python datetime.isoweekday() 周一=1..周日=7。
            # 映射到 isoweekday 集合再比较（原先直接用 dt.weekday() 与 cron 集合比较，
            # 周一/周日错位——真实 bug 修复）。
            cron_weekdays = cls._parse_cron_field(parts[4], 0, 6)
            py_weekdays = {7 if w == 0 else w for w in cron_weekdays}
        except Exception:
            return after_ts + 3600

        dt = datetime.datetime.fromtimestamp(after_ts)
        dt = dt.replace(second=0, microsecond=0) + datetime.timedelta(minutes=1)
        for _ in range(60 * 24 * 366):  # 最多向后找一年
            if (dt.minute in minutes and dt.hour in hours
                    and dt.day in days and dt.month in months
                    and dt.isoweekday() in py_weekdays):
                return dt.timestamp()
            dt += datetime.timedelta(minutes=1)
        return after_ts + 3600

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
            elif task.schedule_type == "cron":
                task.next_run = self._next_cron_run(task.cron_expression, now)

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
        if self.last_error:
            # 已有文本通道：状态查询能看到损坏/拒写原因，不新建界面。
            lines.append(f"持久化错误: {self.last_error}")
        if self.tasks:
            lines.append("\n任务列表:")
            for task in sorted(self.tasks.values(), key=lambda t: t.next_run):
                status = "启用" if task.enabled else "禁用"
                next_run = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(task.next_run)) if task.next_run > 0 else "N/A"
                lines.append(f"  - [{status}] {task.name}: {task.target} (下次: {next_run})")
        return "\n".join(lines)
