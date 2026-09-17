"""
MBDSDR AI 内核 - LLM-as-Judge 多维评分系统
============================================
LLMJudge：用 LLM 作为评判者，对 Agent 的输出进行多维评分。

对照白皮书第四章 4.6.1 LLM-as-Judge 多维评分系统。

核心概念：
- JudgeResult：评分结果，包含各维度得分和总评
- JudgeDimension：评分维度
- LLMJudge：评判器，调用 LLM 对输出进行评分

评分维度：
- 正确性（correctness）：回答是否正确
- 完整性（completeness）：是否覆盖了所有要点
- 相关性（relevance）：是否与问题相关
- 工具使用（tool_usage）：工具调用是否合理、高效
- 安全性（safety）：是否存在安全风险
- 可读性（readability）：表达是否清晰
- 创造性（creativity）：是否有创新点

用途：
- 评估 Agent 输出质量
- 自学习闭环的反馈信号
- 工作流效果评估
- 模型选择的依据
"""

import json
import time
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional, Callable
from enum import Enum


class JudgeDimension(str, Enum):
    """评分维度。"""
    CORRECTNESS = "correctness"  # 正确性
    COMPLETENESS = "completeness"  # 完整性
    RELEVANCE = "relevance"  # 相关性
    TOOL_USAGE = "tool_usage"  # 工具使用
    SAFETY = "safety"  # 安全性
    READABILITY = "readability"  # 可读性
    CREATIVITY = "creativity"  # 创造性
    EFFICIENCY = "efficiency"  # 效率


@dataclass
class DimensionScore:
    """单个维度的评分。"""
    dimension: str  # 维度名
    score: float  # 得分（0-10）
    weight: float  # 权重（0-1）
    reasoning: str = ""  # 评分理由
    evidence: List[str] = field(default_factory=list)  # 证据

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dimension": self.dimension,
            "score": self.score,
            "weight": self.weight,
            "reasoning": self.reasoning,
            "evidence": self.evidence,
        }


@dataclass
class JudgeResult:
    """评判结果。"""
    overall_score: float  # 总分（0-10，加权平均）
    dimensions: List[DimensionScore]  # 各维度得分
    verdict: str  # 总评：excellent/good/fair/poor
    summary: str  # 总结
    strengths: List[str] = field(default_factory=list)  # 优点
    weaknesses: List[str] = field(default_factory=list)  # 缺点
    suggestions: List[str] = field(default_factory=list)  # 改进建议
    judge_model: str = ""  # 评判模型
    timestamp: float = field(default_factory=time.time)
    latency_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "overall_score": self.overall_score,
            "dimensions": [d.to_dict() for d in self.dimensions],
            "verdict": self.verdict,
            "summary": self.summary,
            "strengths": self.strengths,
            "weaknesses": self.weaknesses,
            "suggestions": self.suggestions,
            "judge_model": self.judge_model,
            "timestamp": self.timestamp,
            "latency_ms": self.latency_ms,
        }

    def get_verdict(self) -> str:
        """根据总分获取总评。"""
        if self.overall_score >= 9:
            return "excellent"
        elif self.overall_score >= 7:
            return "good"
        elif self.overall_score >= 5:
            return "fair"
        else:
            return "poor"


# 默认维度权重
DEFAULT_DIMENSION_WEIGHTS = {
    "correctness": 0.25,
    "completeness": 0.15,
    "relevance": 0.15,
    "tool_usage": 0.15,
    "safety": 0.10,
    "readability": 0.10,
    "creativity": 0.05,
    "efficiency": 0.05,
}


class LLMJudge:
    """
    LLM-as-Judge 评判器。

    调用 LLM 对 Agent 的输出进行多维评分。
    支持自定义维度、权重、评判提示词。
    """

    def __init__(
        self,
        model_manager=None,
        judge_model: str = "",
        dimension_weights: Dict[str, float] = None,
        custom_prompt: str = "",
    ):
        self.model_manager = model_manager
        self.judge_model = judge_model
        self.dimension_weights = dimension_weights or DEFAULT_DIMENSION_WEIGHTS
        self.custom_prompt = custom_prompt
        self.history: List[JudgeResult] = []

    def judge(
        self,
        question: str,
        answer: str,
        tool_calls: List[Dict[str, Any]] = None,
        context: str = "",
        use_llm: bool = True,
    ) -> JudgeResult:
        """
        对 Agent 的输出进行评判。

        question: 用户问题
        answer: Agent 回答
        tool_calls: 工具调用记录
        context: 额外上下文
        use_llm: 是否使用 LLM（False 时用规则评分）
        """
        start_time = time.time()

        if use_llm and self.model_manager:
            result = self._judge_with_llm(question, answer, tool_calls, context)
        else:
            result = self._judge_with_rules(question, answer, tool_calls, context)

        result.latency_ms = (time.time() - start_time) * 1000
        result.judge_model = self.judge_model or (self.model_manager.model if self.model_manager else "rule-based")
        self.history.append(result)
        return result

    def _judge_with_llm(
        self,
        question: str,
        answer: str,
        tool_calls: List[Dict[str, Any]] = None,
        context: str = "",
    ) -> JudgeResult:
        """用 LLM 进行评判。"""
        # 构建评判提示词
        prompt = self._build_judge_prompt(question, answer, tool_calls, context)

        try:
            # 调用 LLM
            response = self.model_manager.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=2000,
            )
            content = response.get("content", "")

            # 解析 LLM 输出（期望 JSON 格式）
            return self._parse_judge_response(content)
        except Exception as e:
            # LLM 调用失败，回退到规则评分
            return self._judge_with_rules(question, answer, tool_calls, context)

    def _build_judge_prompt(
        self,
        question: str,
        answer: str,
        tool_calls: List[Dict[str, Any]] = None,
        context: str = "",
    ) -> str:
        """构建评判提示词。"""
        dimensions_desc = "\n".join([
            f"- {dim}: {weight:.0%} 权重"
            for dim, weight in self.dimension_weights.items()
        ])

        tool_calls_desc = ""
        if tool_calls:
            tool_calls_desc = f"\n工具调用记录（共 {len(tool_calls)} 次）：\n"
            for i, tc in enumerate(tool_calls):
                tool_calls_desc += f"{i+1}. {tc.get('name', 'unknown')}({json.dumps(tc.get('parameters', {}), ensure_ascii=False)[:100]})\n"

        prompt = f"""你是一个严格的 AI 输出评判者。请对以下 Agent 的回答进行多维评分。

## 评分维度（0-10 分）
{dimensions_desc}

## 用户问题
{question}

## Agent 回答
{answer}
{tool_calls_desc}
## 额外上下文
{context or "无"}

## 输出格式
请严格输出 JSON 格式，不要输出其他内容：
{{
  "dimensions": [
    {{"dimension": "correctness", "score": 8.5, "reasoning": "理由", "evidence": ["证据1"]}},
    ...
  ],
  "summary": "总评总结",
  "strengths": ["优点1", "优点2"],
  "weaknesses": ["缺点1", "缺点2"],
  "suggestions": ["改进建议1", "改进建议2"]
}}

请严格、客观地评分，不要给人情分。"""

        return prompt

    def _parse_judge_response(self, content: str) -> JudgeResult:
        """解析 LLM 的评判响应。"""
        try:
            # 尝试提取 JSON
            json_start = content.find('{')
            json_end = content.rfind('}')
            if json_start >= 0 and json_end > json_start:
                data = json.loads(content[json_start:json_end+1])
            else:
                data = json.loads(content)

            dimensions = []
            for d in data.get("dimensions", []):
                dim_name = d.get("dimension", "unknown")
                dimensions.append(DimensionScore(
                    dimension=dim_name,
                    score=float(d.get("score", 5)),
                    weight=self.dimension_weights.get(dim_name, 0.1),
                    reasoning=d.get("reasoning", ""),
                    evidence=d.get("evidence", []),
                ))

            # 计算加权总分
            total_weight = sum(d.weight for d in dimensions)
            if total_weight > 0:
                overall = sum(d.score * d.weight for d in dimensions) / total_weight
            else:
                overall = sum(d.score for d in dimensions) / max(len(dimensions), 1)

            result = JudgeResult(
                overall_score=round(overall, 2),
                dimensions=dimensions,
                verdict="",
                summary=data.get("summary", ""),
                strengths=data.get("strengths", []),
                weaknesses=data.get("weaknesses", []),
                suggestions=data.get("suggestions", []),
            )
            result.verdict = result.get_verdict()
            return result

        except Exception:
            # 解析失败，返回默认评分
            return JudgeResult(
                overall_score=5.0,
                dimensions=[DimensionScore(dimension="parse_error", score=5.0, weight=1.0, reasoning="LLM 输出解析失败")],
                verdict="fair",
                summary="评判响应解析失败，使用默认评分",
            )

    def _judge_with_rules(
        self,
        question: str,
        answer: str,
        tool_calls: List[Dict[str, Any]] = None,
        context: str = "",
    ) -> JudgeResult:
        """用规则进行评分（不依赖 LLM）。"""
        dimensions = []

        # 正确性：检查回答是否包含关键信息（简化）
        correctness = 7.0 if len(answer) > 50 else 5.0
        dimensions.append(DimensionScore(
            dimension="correctness", score=correctness,
            weight=self.dimension_weights.get("correctness", 0.25),
            reasoning="基于回答长度的启发式评分",
        ))

        # 完整性：检查回答长度
        completeness = min(10, 5 + len(answer) / 200)
        dimensions.append(DimensionScore(
            dimension="completeness", score=round(completeness, 1),
            weight=self.dimension_weights.get("completeness", 0.15),
            reasoning="基于回答长度的启发式评分",
        ))

        # 相关性：检查回答是否包含问题中的关键词
        question_words = set(question.lower().split())
        answer_words = set(answer.lower().split())
        overlap = len(question_words & answer_words) / max(len(question_words), 1)
        relevance = 5 + overlap * 5
        dimensions.append(DimensionScore(
            dimension="relevance", score=round(relevance, 1),
            weight=self.dimension_weights.get("relevance", 0.15),
            reasoning=f"关键词重叠率 {overlap:.0%}",
        ))

        # 工具使用：检查工具调用是否合理
        if tool_calls:
            tool_score = 7.0 if len(tool_calls) <= 5 else 5.0
        else:
            tool_score = 6.0
        dimensions.append(DimensionScore(
            dimension="tool_usage", score=tool_score,
            weight=self.dimension_weights.get("tool_usage", 0.15),
            reasoning=f"工具调用 {len(tool_calls or [])} 次",
        ))

        # 安全性：检查是否包含危险内容（简化）
        safety = 9.0  # 默认安全
        dimensions.append(DimensionScore(
            dimension="safety", score=safety,
            weight=self.dimension_weights.get("safety", 0.10),
            reasoning="未检测到危险内容",
        ))

        # 可读性：检查回答结构
        readability = 7.0 if '\n' in answer else 5.0
        dimensions.append(DimensionScore(
            dimension="readability", score=readability,
            weight=self.dimension_weights.get("readability", 0.10),
            reasoning="基于回答结构的启发式评分",
        ))

        # 创造性：默认中等
        dimensions.append(DimensionScore(
            dimension="creativity", score=6.0,
            weight=self.dimension_weights.get("creativity", 0.05),
            reasoning="规则评分无法评估创造性",
        ))

        # 效率：基于工具调用次数
        efficiency = 10 - min(5, len(tool_calls or []) * 0.5)
        dimensions.append(DimensionScore(
            dimension="efficiency", score=efficiency,
            weight=self.dimension_weights.get("efficiency", 0.05),
            reasoning=f"工具调用 {len(tool_calls or [])} 次",
        ))

        # 计算加权总分
        total_weight = sum(d.weight for d in dimensions)
        overall = sum(d.score * d.weight for d in dimensions) / total_weight

        result = JudgeResult(
            overall_score=round(overall, 2),
            dimensions=dimensions,
            verdict="",
            summary="规则评分（非 LLM 评判）",
            strengths=["回答有一定长度", "工具调用记录完整"],
            weaknesses=["规则评分精度有限", "无法评估深层语义"],
            suggestions=["建议使用 LLM 评判获得更精确结果"],
        )
        result.verdict = result.get_verdict()
        return result

    def get_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        """获取评判历史。"""
        return [r.to_dict() for r in self.history[-limit:]]

    def get_average_scores(self) -> Dict[str, float]:
        """获取各维度的历史平均分。"""
        if not self.history:
            return {}

        dimension_scores: Dict[str, List[float]] = {}
        for result in self.history:
            for dim in result.dimensions:
                if dim.dimension not in dimension_scores:
                    dimension_scores[dim.dimension] = []
                dimension_scores[dim.dimension].append(dim.score)

        return {
            dim: round(sum(scores) / len(scores), 2)
            for dim, scores in dimension_scores.items()
        }

    def get_stats(self) -> Dict[str, Any]:
        """获取评判器统计信息。"""
        return {
            "total_judgments": len(self.history),
            "average_overall": round(
                sum(r.overall_score for r in self.history) / max(len(self.history), 1), 2
            ),
            "average_by_dimension": self.get_average_scores(),
            "judge_model": self.judge_model,
            "dimension_weights": self.dimension_weights,
        }
