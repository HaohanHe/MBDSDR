# SPDX-License-Identifier: MIT
"""
MBDSDR AI 内核 - 自学习闭环系统
================================
SelfLearning：从经验中学习，优化工具调用策略和系统提示词。

对照白皮书第四章 4.6.2 自学习闭环系统。

核心概念：
- Experience：经验记录（问题、回答、工具调用、评分、反馈）
- LearningEngine：学习引擎，从经验中提取规律
- StrategyOptimizer：策略优化器，优化工具调用策略
- PromptOptimizer：提示词优化器，优化系统提示词

学习闭环：
1. 执行任务 → 记录经验
2. LLM-as-Judge 评分 → 获取反馈
3. 分析经验 → 提取规律
4. 优化策略/提示词 → 提升性能
5. 验证优化效果 → 确认改进

用途：
- 优化工具调用顺序和参数
- 优化系统提示词
- 识别高频任务模式
- 自动生成工作流模板
- 持续提升 Agent 性能
"""

import json
import logging
import time
import os
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional, Set
from enum import Enum

from .corrupt_evidence import REASON_SUFFIX, write_reason

logger = logging.getLogger(__name__)


def _same_content_backup(path: str) -> Optional[str]:
    """同目录下是否已有一份与当前文件字节相同的 ``*.corrupt-*`` 备份。"""
    directory = os.path.dirname(os.path.abspath(path))
    prefix = os.path.basename(path) + ".corrupt-"
    try:
        names = sorted(
            (n for n in os.listdir(directory)
             if n.startswith(prefix) and not n.endswith(REASON_SUFFIX)),
            reverse=True)
        if not names:
            return None
        candidate = os.path.join(directory, names[0])
        if os.path.getsize(candidate) != os.path.getsize(path):
            return None
        with open(candidate, "rb") as a, open(path, "rb") as b:
            return candidate if a.read() == b.read() else None
    except OSError:
        return None


def _backup_corrupt(path: str) -> Optional[str]:
    """把损坏的学习数据改名为 ``*.corrupt-<时间戳>``，返回备份路径（失败 None）。

    同一份内容只留一份副本；改名后原路径不再被扫描，也不会被下一次保存覆盖。
    """
    dst = _same_content_backup(path)
    if dst is None:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dst = f"{path}.corrupt-{stamp}"
        n = 0
        while os.path.exists(dst):
            n += 1
            dst = f"{path}.corrupt-{stamp}-{n}"
    try:
        os.replace(path, dst)
    except OSError:
        return None
    return dst


class ExperienceType(str, Enum):
    """经验类型。"""
    TOOL_CALL = "tool_call"  # 工具调用经验
    TASK_COMPLETION = "task_completion"  # 任务完成经验
    ERROR_RECOVERY = "error_recovery"  # 错误恢复经验
    USER_FEEDBACK = "user_feedback"  # 用户反馈经验
    JUDGE_FEEDBACK = "judge_feedback"  # 评判反馈经验


@dataclass
class Experience:
    """经验记录。"""
    experience_id: str
    experience_type: ExperienceType
    question: str = ""  # 用户问题
    answer: str = ""  # Agent 回答
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)  # 工具调用记录
    score: float = 0.0  # 评分（0-10）
    feedback: str = ""  # 反馈
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    learned: bool = False  # 是否已被学习

    def to_dict(self) -> Dict[str, Any]:
        return {
            "experience_id": self.experience_id,
            "experience_type": self.experience_type.value,
            "question": self.question,
            "answer": self.answer[:500] if self.answer else "",  # 截断长回答
            "tool_calls_count": len(self.tool_calls),
            "tool_calls": [{"name": tc.get("name"), "parameters": tc.get("parameters", {})} for tc in self.tool_calls[:10]],
            "score": self.score,
            "feedback": self.feedback,
            "metadata": self.metadata,
            "timestamp": self.timestamp,
            "learned": self.learned,
        }


@dataclass
class LearnedPattern:
    """学习到的模式。"""
    pattern_id: str
    pattern_type: str  # tool_sequence / prompt_improvement / error_pattern / task_pattern
    description: str
    trigger: str = ""  # 触发条件
    action: str = ""  # 建议动作
    confidence: float = 0.0  # 置信度（0-1）
    frequency: int = 0  # 出现频率
    avg_score: float = 0.0  # 平均评分
    created_at: float = field(default_factory=time.time)
    applied: bool = False  # 是否已应用

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pattern_id": self.pattern_id,
            "pattern_type": self.pattern_type,
            "description": self.description,
            "trigger": self.trigger,
            "action": self.action,
            "confidence": self.confidence,
            "frequency": self.frequency,
            "avg_score": self.avg_score,
            "created_at": self.created_at,
            "applied": self.applied,
        }


class SelfLearningEngine:
    """
    自学习引擎。

    从经验中学习，优化工具调用策略和系统提示词。
    """

    def __init__(
        self,
        storage_dir: str = None,
        judge=None,
        min_score_for_learning: float = 7.0,
        max_experiences: int = 10000,
    ):
        if storage_dir is None:
            storage_dir = os.path.expanduser("~/.mbdsdr/learning")
        self.storage_dir = storage_dir
        os.makedirs(storage_dir, exist_ok=True)

        self.judge = judge  # LLM-as-Judge 评判器
        self.min_score_for_learning = min_score_for_learning
        self.max_experiences = max_experiences

        self.experiences: Dict[str, Experience] = {}
        self.patterns: Dict[str, LearnedPattern] = {}
        self._counter = 0
        # 降级可见：加载失败的经验/模式文件记在这里（kind + path + 原因）。
        # 「经验本来就是空的」与「经验文件读不出来」不再等价。
        self.load_errors: List[Dict[str, str]] = []
        #: 已落盘的损坏备份路径。
        self.corrupt_backups: List[str] = []
        #: 写回被拒绝的记录（读失败未备份/备份失败），避免静默丢数据。
        self.save_warnings: List[Dict[str, str]] = []
        #: 读失败且未备份的路径：后续写入这些路径必须 fail-closed。
        self._save_blocked_paths: Set[str] = set()

        # 加载历史经验和模式
        self._load_experiences()
        self._load_patterns()

    def _record_load_error(self, kind: str, path: str, exc: Exception) -> None:
        """记录一次加载降级；单个坏文件不阻断其余文件的加载。"""
        reason = f"{type(exc).__name__}: {exc}"
        self.load_errors.append({"kind": kind, "path": path, "error": reason})
        logger.warning("学习数据读取失败（%s），已记入 load_errors：%s | %s",
                       kind, path, reason)

    def _quarantine_corrupt(self, kind: str, path: str, exc: Exception) -> bool:
        """备份损坏文件并写理由；备份失败则登记该路径禁止后续写回。"""
        backup = _backup_corrupt(path)
        reason = f"{type(exc).__name__}: {exc}"
        if kind == "save-target":
            self.save_warnings.append(
                {"path": path, "error": reason, "backup": backup or ""})
        else:
            self.load_errors.append({
                "kind": kind, "path": path, "error": reason,
                "backup": backup or "",
            })
        if backup is None:
            self._save_blocked_paths.add(path)
            logger.error("学习数据损坏且备份失败，后续写入该路径将被拒绝：%s | %s",
                         path, reason)
            return False
        write_reason(backup, f"学习数据读取失败（{reason}）：{path}", logger)
        self.corrupt_backups.append(backup)
        logger.warning("学习数据损坏/不可读，已备份为 %s | %s", backup, reason)
        return True

    def _write_json(self, path: str, payload: Dict[str, Any]) -> bool:
        """写回前确认目标可读且已备份；不许写回时返回 False（不抛异常）。

        说明：真正的磁盘写失败仍按原行为向上抛 OSError；返回 False 仅表示
        「为了保护原件而主动拒绝写回」，原因已记入 ``save_warnings``。
        """
        if path in self._save_blocked_paths:
            self.save_warnings.append({
                "path": path, "error": "读取失败未备份，拒绝写回", "backup": "",
            })
            logger.error("学习数据未写入（此前读取失败且未备份）：%s", path)
            return False
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    json.load(f)
            except FileNotFoundError:
                pass  # 竞态：判存在后刚被删，等价于首次创建
            except OSError as exc:
                self._save_blocked_paths.add(path)
                self._record_load_error("save-target", path, exc)
                return False
            except ValueError as exc:
                if not self._quarantine_corrupt("save-target", path, exc):
                    return False
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return True

    def record_experience(
        self,
        experience_type: ExperienceType,
        question: str = "",
        answer: str = "",
        tool_calls: List[Dict[str, Any]] = None,
        score: float = 0.0,
        feedback: str = "",
        metadata: Dict[str, Any] = None,
    ) -> Experience:
        """
        记录一条经验。

        这是学习闭环的第一步：执行任务后记录经验。
        """
        self._counter += 1
        exp_id = f"exp_{int(time.time() * 1000)}_{self._counter}"

        experience = Experience(
            experience_id=exp_id,
            experience_type=experience_type,
            question=question,
            answer=answer,
            tool_calls=tool_calls or [],
            score=score,
            feedback=feedback,
            metadata=metadata or {},
        )

        self.experiences[exp_id] = experience

        # 限制经验数量
        if len(self.experiences) > self.max_experiences:
            oldest = min(self.experiences.values(), key=lambda e: e.timestamp)
            del self.experiences[oldest.experience_id]

        # 保存到文件
        self._save_experience(experience)

        return experience

    def learn_from_experience(self, experience_id: str) -> Optional[LearnedPattern]:
        """
        从单条经验中学习。

        分析经验，提取规律，生成学习模式。
        """
        experience = self.experiences.get(experience_id)
        if not experience:
            return None

        # 只从高分经验中学习
        if experience.score < self.min_score_for_learning:
            return None

        # 分析工具调用序列
        pattern = self._analyze_tool_sequence(experience)
        if pattern:
            self.patterns[pattern.pattern_id] = pattern
            self._save_pattern(pattern)

        experience.learned = True
        return pattern

    def learn_batch(self, limit: int = 100) -> List[LearnedPattern]:
        """
        批量学习：从未学习的经验中提取规律。
        """
        unlearned = [
            e for e in self.experiences.values()
            if not e.learned and e.score >= self.min_score_for_learning
        ][:limit]

        patterns = []
        for exp in unlearned:
            pattern = self.learn_from_experience(exp.experience_id)
            if pattern:
                patterns.append(pattern)

        return patterns

    def _analyze_tool_sequence(self, experience: Experience) -> Optional[LearnedPattern]:
        """分析工具调用序列，提取模式。"""
        if not experience.tool_calls:
            return None

        # 提取工具调用序列
        tool_names = [tc.get("name", "") for tc in experience.tool_calls]
        sequence = " -> ".join(tool_names)

        # 生成模式 ID
        pattern_id = f"pat_{int(time.time() * 1000)}_{self._counter}"

        # 根据经验类型生成不同模式
        if experience.experience_type == ExperienceType.TOOL_CALL:
            pattern = LearnedPattern(
                pattern_id=pattern_id,
                pattern_type="tool_sequence",
                description=f"高效工具调用序列: {sequence}",
                trigger=experience.question[:100],
                action=f"按以下顺序调用工具: {sequence}",
                confidence=min(1.0, experience.score / 10),
                frequency=1,
                avg_score=experience.score,
            )
        elif experience.experience_type == ExperienceType.ERROR_RECOVERY:
            pattern = LearnedPattern(
                pattern_id=pattern_id,
                pattern_type="error_recovery",
                description=f"错误恢复策略: {experience.feedback[:100]}",
                trigger=experience.question[:100],
                action=experience.feedback,
                confidence=min(1.0, experience.score / 10),
                frequency=1,
                avg_score=experience.score,
            )
        else:
            pattern = LearnedPattern(
                pattern_id=pattern_id,
                pattern_type="task_pattern",
                description=f"任务模式: {experience.question[:100]}",
                trigger=experience.question[:100],
                action=f"参考工具序列: {sequence}",
                confidence=min(1.0, experience.score / 10),
                frequency=1,
                avg_score=experience.score,
            )

        return pattern

    def get_suggestion(self, question: str) -> Optional[LearnedPattern]:
        """
        根据问题获取学习建议。

        查找与问题相关的学习模式，给出建议。
        """
        if not self.patterns:
            return None

        # 简单匹配：查找触发条件与问题有重叠的模式
        question_words = set(question.lower().split())
        best_pattern = None
        best_overlap = 0

        for pattern in self.patterns.values():
            trigger_words = set(pattern.trigger.lower().split())
            overlap = len(question_words & trigger_words)
            if overlap > best_overlap:
                best_overlap = overlap
                best_pattern = pattern

        return best_pattern if best_overlap > 0 else None

    def get_stats(self) -> Dict[str, Any]:
        """获取学习引擎统计信息。"""
        total_experiences = len(self.experiences)
        learned_experiences = sum(1 for e in self.experiences.values() if e.learned)
        high_score_experiences = sum(1 for e in self.experiences.values() if e.score >= self.min_score_for_learning)
        avg_score = sum(e.score for e in self.experiences.values()) / max(total_experiences, 1)

        pattern_types = {}
        for p in self.patterns.values():
            pattern_types[p.pattern_type] = pattern_types.get(p.pattern_type, 0) + 1

        return {
            "total_experiences": total_experiences,
            "learned_experiences": learned_experiences,
            "high_score_experiences": high_score_experiences,
            "average_score": round(avg_score, 2),
            "total_patterns": len(self.patterns),
            "applied_patterns": sum(1 for p in self.patterns.values() if p.applied),
            "patterns_by_type": pattern_types,
            "min_score_for_learning": self.min_score_for_learning,
            "storage_dir": self.storage_dir,
            # 加载降级条数：非 0 表示「统计里少了东西」，不是「本来就没有」。
            "load_error_count": len(self.load_errors),
            # 写回被拒绝条数：非 0 表示有数据没落盘，原件被保护但本次保存失败。
            "save_error_count": len(self.save_warnings),
            "corrupt_backup_count": len(self.corrupt_backups),
        }

    def get_experiences(self, limit: int = 20, min_score: float = 0) -> List[Dict[str, Any]]:
        """获取经验列表。"""
        filtered = [e for e in self.experiences.values() if e.score >= min_score]
        filtered.sort(key=lambda e: e.timestamp, reverse=True)
        return [e.to_dict() for e in filtered[:limit]]

    def get_patterns(self, limit: int = 20, pattern_type: str = None) -> List[Dict[str, Any]]:
        """获取学习模式列表。"""
        filtered = list(self.patterns.values())
        if pattern_type:
            filtered = [p for p in filtered if p.pattern_type == pattern_type]
        filtered.sort(key=lambda p: p.confidence, reverse=True)
        return [p.to_dict() for p in filtered[:limit]]

    def _save_experience(self, experience: Experience):
        """保存经验到文件。"""
        date_str = time.strftime("%Y-%m-%d", time.localtime(experience.timestamp))
        day_dir = os.path.join(self.storage_dir, "experiences", date_str)
        os.makedirs(day_dir, exist_ok=True)
        path = os.path.join(day_dir, f"{experience.experience_id}.json")
        self._write_json(path, experience.to_dict())

    def _save_pattern(self, pattern: LearnedPattern):
        """保存模式到文件。"""
        pattern_dir = os.path.join(self.storage_dir, "patterns")
        os.makedirs(pattern_dir, exist_ok=True)
        path = os.path.join(pattern_dir, f"{pattern.pattern_id}.json")
        self._write_json(path, pattern.to_dict())

    def _load_experiences(self):
        """加载历史经验（只加载元数据）。"""
        exp_dir = os.path.join(self.storage_dir, "experiences")
        if not os.path.exists(exp_dir):
            return
        for date_dir in os.listdir(exp_dir):
            full_dir = os.path.join(exp_dir, date_dir)
            if not os.path.isdir(full_dir):
                continue
            for filename in os.listdir(full_dir):
                if not filename.endswith('.json'):
                    continue
                path = os.path.join(full_dir, filename)
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    exp = Experience(
                        experience_id=data["experience_id"],
                        experience_type=ExperienceType(data["experience_type"]),
                        question=data.get("question", ""),
                        score=data.get("score", 0),
                        feedback=data.get("feedback", ""),
                        timestamp=data.get("timestamp", 0),
                        learned=data.get("learned", False),
                    )
                except FileNotFoundError:
                    continue  # 竞态：列目录后刚被删，与「本来就没有」同义
                except OSError as exc:
                    # 读不了 ≠ 损坏：不备份不改名，但禁止后续写回该路径。
                    self._record_load_error("experience", path, exc)
                    self._save_blocked_paths.add(path)
                    continue
                except (ValueError, KeyError, TypeError) as exc:
                    # 真损坏：先落备份 + 理由，原件移出扫描路径。
                    self._quarantine_corrupt("experience", path, exc)
                    continue
                self.experiences[exp.experience_id] = exp
                try:
                    self._counter = max(self._counter, int(exp.experience_id.split('_')[-1]))
                except (ValueError, IndexError, AttributeError) as exc:
                    # 数据本身已加载，只是编号解析不了：不影响经验可用性，但留痕。
                    self._record_load_error("experience-counter", path, exc)

    def _load_patterns(self):
        """加载历史模式。"""
        pattern_dir = os.path.join(self.storage_dir, "patterns")
        if not os.path.exists(pattern_dir):
            return
        for filename in os.listdir(pattern_dir):
            if not filename.endswith('.json'):
                continue
            path = os.path.join(pattern_dir, filename)
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                pattern = LearnedPattern(
                    pattern_id=data["pattern_id"],
                    pattern_type=data["pattern_type"],
                    description=data.get("description", ""),
                    trigger=data.get("trigger", ""),
                    action=data.get("action", ""),
                    confidence=data.get("confidence", 0),
                    frequency=data.get("frequency", 0),
                    avg_score=data.get("avg_score", 0),
                    created_at=data.get("created_at", 0),
                    applied=data.get("applied", False),
                )
            except FileNotFoundError:
                continue  # 竞态：列目录后刚被删，与「本来就没有」同义
            except OSError as exc:
                # 读不了 ≠ 损坏：不备份不改名，但禁止后续写回该路径。
                self._record_load_error("pattern", path, exc)
                self._save_blocked_paths.add(path)
                continue
            except (ValueError, KeyError, TypeError) as exc:
                # 真损坏：先落备份 + 理由，原件移出扫描路径。
                self._quarantine_corrupt("pattern", path, exc)
                continue
            self.patterns[pattern.pattern_id] = pattern
