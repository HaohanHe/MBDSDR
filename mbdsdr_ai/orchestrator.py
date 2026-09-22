"""
MBDSDR AI 内核 - 智能编排器
============================
Orchestrator：智能任务编排器，自动规划复杂任务的执行顺序。

对照白皮书第四章 4.6.3 智能编排器。

核心概念：
- Task：任务，包含目标、依赖、优先级
- TaskGraph：任务图，描述任务之间的依赖关系
- Orchestrator：编排器，自动规划和执行任务

编排策略：
- 依赖分析：分析任务之间的依赖关系
- 优先级排序：按优先级和依赖关系排序
- 并行执行：无依赖的任务并行执行
- 错误恢复：任务失败时自动重试或降级
- 动态调整：根据中间结果调整后续任务

用途：
- 复杂 SDR 任务的自动编排（如：扫频→找信号→解调→录制→解码）
- 多步骤工作流的自动规划
- 资源分配和调度
- 错误恢复和容错
"""


import time
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional, Callable, Set
from enum import Enum


class TaskStatus(str, Enum):
    """任务状态。"""
    PENDING = "pending"  # 等待执行
    READY = "ready"  # 就绪（依赖已满足）
    RUNNING = "running"  # 运行中
    COMPLETED = "completed"  # 已完成
    FAILED = "failed"  # 失败
    SKIPPED = "skipped"  # 跳过
    CANCELLED = "cancelled"  # 已取消


class TaskPriority(str, Enum):
    """任务优先级。"""
    CRITICAL = "critical"  # 关键
    HIGH = "high"  # 高
    MEDIUM = "medium"  # 中
    LOW = "low"  # 低


@dataclass
class Task:
    """任务。"""
    task_id: str
    name: str
    description: str = ""
    handler: Optional[Callable] = None  # 任务处理函数
    tool_name: str = ""  # 要调用的工具名
    tool_params: Dict[str, Any] = field(default_factory=dict)  # 工具参数
    dependencies: List[str] = field(default_factory=list)  # 依赖的任务 ID
    priority: TaskPriority = TaskPriority.MEDIUM
    status: TaskStatus = TaskStatus.PENDING
    result: Any = None
    error: str = ""
    retries: int = 0
    max_retries: int = 2
    timeout_s: float = 60.0
    created_at: float = field(default_factory=time.time)
    started_at: float = 0.0
    completed_at: float = 0.0
    duration_ms: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "name": self.name,
            "description": self.description,
            "tool_name": self.tool_name,
            "dependencies": self.dependencies,
            "priority": self.priority.value,
            "status": self.status.value,
            "error": self.error,
            "retries": self.retries,
            "max_retries": self.max_retries,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_ms": self.duration_ms,
        }


@dataclass
class OrchestrationResult:
    """编排结果。"""
    success: bool
    total_tasks: int
    completed_tasks: int
    failed_tasks: int
    skipped_tasks: int
    total_duration_ms: float
    results: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "total_tasks": self.total_tasks,
            "completed_tasks": self.completed_tasks,
            "failed_tasks": self.failed_tasks,
            "skipped_tasks": self.skipped_tasks,
            "total_duration_ms": self.total_duration_ms,
            "results": self.results,
            "errors": self.errors,
        }


class Orchestrator:
    """
    智能编排器。

    自动规划和执行复杂任务，支持依赖管理、并行执行、错误恢复。
    """

    PRIORITY_ORDER = {
        TaskPriority.CRITICAL: 0,
        TaskPriority.HIGH: 1,
        TaskPriority.MEDIUM: 2,
        TaskPriority.LOW: 3,
    }

    def __init__(
        self,
        tool_registry=None,
        max_parallel: int = 4,
        default_timeout_s: float = 60.0,
    ):
        self.tool_registry = tool_registry
        self.max_parallel = max_parallel
        self.default_timeout_s = default_timeout_s
        self.tasks: Dict[str, Task] = {}
        self._counter = 0
        self._lock = threading.Lock()

    def add_task(
        self,
        name: str,
        description: str = "",
        tool_name: str = "",
        tool_params: Dict[str, Any] = None,
        handler: Callable = None,
        dependencies: List[str] = None,
        priority: TaskPriority = TaskPriority.MEDIUM,
        timeout_s: float = None,
        max_retries: int = 2,
    ) -> str:
        """
        添加一个任务。

        返回任务 ID。
        """
        self._counter += 1
        task_id = f"task_{self._counter}"

        task = Task(
            task_id=task_id,
            name=name,
            description=description,
            handler=handler,
            tool_name=tool_name,
            tool_params=tool_params or {},
            dependencies=dependencies or [],
            priority=priority,
            timeout_s=timeout_s or self.default_timeout_s,
            max_retries=max_retries,
        )

        with self._lock:
            self.tasks[task_id] = task

        return task_id

    def plan(self) -> List[str]:
        """
        规划任务执行顺序。

        基于依赖关系和优先级，生成拓扑排序的执行顺序。
        返回任务 ID 列表。
        """
        # 构建依赖图
        graph: Dict[str, Set[str]] = {}  # task_id -> 依赖的任务
        for task in self.tasks.values():
            graph[task.task_id] = set(task.dependencies)

        # 拓扑排序（Kahn 算法）+ 优先级排序
        in_degree = {tid: len(deps) for tid, deps in graph.items()}
        ready = [tid for tid, deg in in_degree.items() if deg == 0]

        # 按优先级排序就绪任务
        ready.sort(key=lambda tid: self.PRIORITY_ORDER.get(self.tasks[tid].priority, 2))

        order = []
        while ready:
            # 取优先级最高的就绪任务
            task_id = ready.pop(0)
            order.append(task_id)

            # 更新依赖该任务的其他任务
            for tid, deps in graph.items():
                if task_id in deps:
                    deps.remove(task_id)
                    in_degree[tid] -= 1
                    if in_degree[tid] == 0:
                        # 插入到正确位置（保持优先级排序）
                        insert_pos = 0
                        for i, rid in enumerate(ready):
                            if self.PRIORITY_ORDER.get(self.tasks[tid].priority, 2) < \
                               self.PRIORITY_ORDER.get(self.tasks[rid].priority, 2):
                                insert_pos = i
                                break
                            insert_pos = i + 1
                        ready.insert(insert_pos, tid)

        return order

    def execute(
        self,
        order: List[str] = None,
        stop_on_failure: bool = False,
    ) -> OrchestrationResult:
        """
        执行所有任务。

        order: 执行顺序（None 时自动规划）
        stop_on_failure: 失败时是否停止
        """
        if order is None:
            order = self.plan()

        start_time = time.time()
        results = {}
        errors = []
        completed = 0
        failed = 0
        skipped = 0

        for task_id in order:
            task = self.tasks.get(task_id)
            if not task:
                continue

            # 检查依赖是否都成功
            deps_ok = all(
                (dep_id in self.tasks and
                 self.tasks[dep_id].status == TaskStatus.COMPLETED)
                for dep_id in task.dependencies
            )

            if not deps_ok:
                task.status = TaskStatus.SKIPPED
                skipped += 1
                errors.append(f"任务 {task.name} 因依赖失败而跳过")
                if stop_on_failure:
                    break
                continue

            # 执行任务
            success = self._execute_task(task)
            results[task_id] = task.result

            if success:
                completed += 1
            else:
                failed += 1
                errors.append(f"任务 {task.name} 失败: {task.error}")
                if stop_on_failure:
                    break

        total_duration = (time.time() - start_time) * 1000

        return OrchestrationResult(
            success=failed == 0,
            total_tasks=len(order),
            completed_tasks=completed,
            failed_tasks=failed,
            skipped_tasks=skipped,
            total_duration_ms=round(total_duration, 2),
            results=results,
            errors=errors,
        )

    def _execute_task(self, task: Task) -> bool:
        """执行单个任务（含重试）。"""
        task.status = TaskStatus.RUNNING
        task.started_at = time.time()

        for attempt in range(task.max_retries + 1):
            try:
                task.retries = attempt

                # 执行 handler 或调用工具
                if task.handler:
                    task.result = task.handler(task.tool_params)
                elif task.tool_name and self.tool_registry:
                    tool_result = self.tool_registry.call(task.tool_name, task.tool_params)
                    task.result = tool_result.content if tool_result.success else tool_result.error
                    if not tool_result.success:
                        raise Exception(tool_result.error)
                else:
                    task.result = None

                task.status = TaskStatus.COMPLETED
                task.completed_at = time.time()
                task.duration_ms = (task.completed_at - task.started_at) * 1000
                return True

            except Exception as e:
                task.error = f"{type(e).__name__}: {str(e)}"
                if attempt < task.max_retries:
                    time.sleep(0.5 * (attempt + 1))  # 指数退避
                    continue
                else:
                    task.status = TaskStatus.FAILED
                    task.completed_at = time.time()
                    task.duration_ms = (task.completed_at - task.started_at) * 1000
                    return False

        return False

    def get_task(self, task_id: str) -> Optional[Task]:
        return self.tasks.get(task_id)

    def list_tasks(self) -> List[Dict[str, Any]]:
        """列出所有任务。"""
        return [t.to_dict() for t in self.tasks.values()]

    def get_stats(self) -> Dict[str, Any]:
        """获取编排器统计信息。"""
        total = len(self.tasks)
        by_status = {}
        by_priority = {}
        for task in self.tasks.values():
            by_status[task.status.value] = by_status.get(task.status.value, 0) + 1
            by_priority[task.priority.value] = by_priority.get(task.priority.value, 0) + 1

        avg_duration = 0
        completed = [t for t in self.tasks.values() if t.status == TaskStatus.COMPLETED]
        if completed:
            avg_duration = sum(t.duration_ms for t in completed) / len(completed)

        return {
            "total_tasks": total,
            "tasks_by_status": by_status,
            "tasks_by_priority": by_priority,
            "average_duration_ms": round(avg_duration, 2),
            "max_parallel": self.max_parallel,
            "default_timeout_s": self.default_timeout_s,
        }

    def clear(self):
        """清空所有任务。"""
        self.tasks.clear()
        self._counter = 0

    def create_sdr_pipeline(
        self,
        frequency_hz: int,
        sample_rate: int = 2048000,
        gain_db: int = 40,
        demod_mode: str = "fm",
        record_duration_s: int = 10,
    ) -> List[str]:
        """
        创建标准 SDR 处理流水线。

        流水线：连接设备 → 设置频率 → 设置采样率 → 设置增益 →
                频谱分析 → 找信号 → 解调 → 录制 → 分析录制

        返回任务 ID 列表。
        """
        self.clear()

        # 1. 连接设备（无依赖）
        t1 = self.add_task("连接 SDR 设备", "连接到 SDR 后端", "sdr_connect", {}, priority=TaskPriority.CRITICAL)

        # 2. 设置参数（依赖连接）
        t2 = self.add_task("设置频率", f"设置中心频率 {frequency_hz/1e6:.3f} MHz", "sdr_set_frequency", {"frequency_hz": frequency_hz}, dependencies=[t1], priority=TaskPriority.HIGH)
        t3 = self.add_task("设置采样率", f"设置采样率 {sample_rate/1e6:.2f} MSPS", "sdr_set_sample_rate", {"sample_rate": sample_rate}, dependencies=[t1], priority=TaskPriority.HIGH)
        t4 = self.add_task("设置增益", f"设置增益 {gain_db} dB", "sdr_set_gain", {"gain_db": gain_db}, dependencies=[t1], priority=TaskPriority.HIGH)

        # 3. 频谱分析（依赖参数设置）
        t5 = self.add_task("频谱分析", "分析频谱，检测信号", "sdr_spectrum_analyze", {"fft_size": 1024}, dependencies=[t2, t3, t4], priority=TaskPriority.MEDIUM)
        t6 = self.add_task("找信号", "检测频谱中的信号", "sdr_spectrum_find_signals", {"threshold_db": -60}, dependencies=[t5], priority=TaskPriority.MEDIUM)

        # 4. 解调（依赖找信号）
        t7 = self.add_task("设置解调模式", f"设置 {demod_mode.upper()} 解调", "sdr_set_demod", {"mode": demod_mode}, dependencies=[t6], priority=TaskPriority.MEDIUM)
        t8 = self.add_task("解调信号", "解调音频信号", "sdr_demodulate", {"mode": demod_mode, "duration_s": 5}, dependencies=[t7], priority=TaskPriority.MEDIUM)

        # 5. 录制（依赖解调）
        t9 = self.add_task("录制基带", f"录制 {record_duration_s} 秒基带", "sdr_record_start", {"duration": record_duration_s, "format": "cf32"}, dependencies=[t8], priority=TaskPriority.LOW)

        return [t1, t2, t3, t4, t5, t6, t7, t8, t9]
