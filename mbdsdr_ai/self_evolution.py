"""
MBDSDR AI 内核 - 自进化引擎
============================
AI 定义无线电的自进化核心，让模型能够改进自身。

核心能力（对照 Hermes Self-Evolution）：
- 技能进化：修改系统提示词、技能描述
- 工具描述进化：优化工具 description，让弱模型更容易选对工具
- 代码进化：在沙箱中修改处理逻辑（信号处理、解调算法等）
- 评估循环：修改后自动评估，好的保留，差的回滚
- 版本控制：git-like 版本存储，支持任意回滚
- 沙箱执行：代码修改在隔离环境中执行，不影响主系统
- 用户确认：高风险修改需要用户确认
- 一键恢复：防幻觉变砖，任何修改都可以回滚
- 用户投稿：类似创意工坊，用户提交改进，专家委员会审查

进化循环：
    1. PROPOSE  - 模型提出进化建议
    2. VALIDATE  - 静态检查安全性
    3. SANDBOX   - 在沙箱中执行修改
    4. EVALUATE  - 评估修改效果（测试用例、性能指标）
    5. CONFIRM   - 用户确认（高风险修改）
    6. COMMIT    - 提交版本（快照）
    7. APPLY     - 应用到主系统（或保持在分支）
    任何一步失败 → 自动回滚
"""


import time

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Callable, Tuple

from .version_store import VersionStore, Version
from .sandbox import Sandbox, SandboxResult


# 进化目标类型
EVOLUTION_TARGETS = {
    "skill": "技能/提示词",
    "tool_description": "工具描述",
    "code": "代码/算法",
    "config": "配置参数",
    "pipeline": "处理流水线",
}

# 风险等级
RISK_LEVELS = {
    "low": "低风险（提示词、描述、配置）",
    "medium": "中风险（工具描述、非核心代码）",
    "high": "高风险（核心算法、系统配置、MCP 协议）",
}


@dataclass
class EvolutionProposal:
    """一个进化建议。"""
    id: str
    timestamp: float
    target_type: str  # skill / tool_description / code / config / pipeline
    target_name: str
    description: str
    proposed_change: str  # 修改后的内容
    original_content: str  # 修改前的内容
    risk_level: str  # low / medium / high
    test_cases: List[Dict[str, Any]] = field(default_factory=list)
    status: str = "proposed"  # proposed / validated / sandboxed / evaluated / confirmed / committed / applied / rejected / rolled_back
    evaluation_result: Dict[str, Any] = field(default_factory=dict)
    version_id: Optional[str] = None
    author: str = "ai"
    real_path: str = ""  # 真实磁盘目标文件（非空则 apply 真落盘，rollback 可恢复）

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "target_type": self.target_type,
            "target_name": self.target_name,
            "description": self.description,
            "risk_level": self.risk_level,
            "status": self.status,
            "test_cases": len(self.test_cases),
            "version_id": self.version_id,
            "author": self.author,
        }


class SelfEvolutionEngine:
    """
    自进化引擎。

    管理进化建议的完整生命周期：提出 → 验证 → 沙箱 → 评估 → 确认 → 提交 → 应用。
    任何一步失败自动回滚，防幻觉变砖。
    """

    def __init__(
        self,
        store_path: str = "~/.mbdsdr/evolution",
        sandbox_timeout: int = 10,
        auto_confirm_low_risk: bool = True,
    ):
        self.version_store = VersionStore(store_path)
        self.sandbox = Sandbox(timeout_seconds=sandbox_timeout)
        self.proposals: Dict[str, EvolutionProposal] = {}
        self.auto_confirm_low_risk = auto_confirm_low_risk
        self._proposal_count = 0
        self._evolution_cycles = 0
        self._disk_backups: Dict[str, tuple] = {}  # proposal_id -> (real_path, old_content)

        # 初始快照
        if not self.version_store.get_version():
            self.version_store.snapshot(
                message="初始版本（自进化引擎启动）",
                files={"system_prompt.txt": "", "tool_descriptions.json": "{}"},
                author="system",
            )

    # ── 提出进化建议 ────────────────────────────────────

    def propose(
        self,
        target_type: str,
        target_name: str,
        description: str,
        proposed_change: str,
        original_content: str = "",
        risk_level: str = "low",
        test_cases: List[Dict[str, Any]] = None,
        author: str = "ai",
        real_path: str = "",
    ) -> EvolutionProposal:
        """
        提出一个进化建议。

        这是进化循环的第一步。
        """
        if target_type not in EVOLUTION_TARGETS:
            raise ValueError(f"未知的进化目标类型: {target_type}，可选: {list(EVOLUTION_TARGETS.keys())}")

        if risk_level not in RISK_LEVELS:
            raise ValueError(f"未知的风险等级: {risk_level}，可选: {list(RISK_LEVELS.keys())}")

        self._proposal_count += 1
        proposal_id = f"evo_{int(time.time()*1000)}_{self._proposal_count:04d}"

        proposal = EvolutionProposal(
            id=proposal_id,
            timestamp=time.time(),
            target_type=target_type,
            target_name=target_name,
            description=description,
            proposed_change=proposed_change,
            original_content=original_content,
            risk_level=risk_level,
            test_cases=test_cases or [],
            author=author,
            real_path=real_path,
        )

        self.proposals[proposal_id] = proposal
        return proposal

    # ── 验证安全性 ──────────────────────────────────────

    def validate(self, proposal_id: str) -> Tuple[bool, Dict[str, Any]]:
        """
        验证进化建议的安全性（静态检查）。

        对于代码类型的修改，进行沙箱安全检查。
        """
        proposal = self.proposals.get(proposal_id)
        if not proposal:
            return False, {"error": f"建议 {proposal_id} 不存在"}

        if proposal.target_type == "code":
            result = self.sandbox.validate_code(proposal.proposed_change)
            proposal.status = "validated" if result["safe"] else "rejected"
            return result["safe"], result
        else:
            # 非代码修改（提示词、描述、配置）默认安全
            proposal.status = "validated"
            return True, {"safe": True, "warnings": [], "dangerous_patterns": []}

    # ── 沙箱执行 ────────────────────────────────────────

    def run_in_sandbox(self, proposal_id: str, test_inputs: Dict[str, Any] = None) -> SandboxResult:
        """
        在沙箱中执行进化建议的代码修改。

        仅对 code 类型有效。
        """
        proposal = self.proposals.get(proposal_id)
        if not proposal:
            return SandboxResult(success=False, error=f"建议 {proposal_id} 不存在")

        if proposal.target_type != "code":
            return SandboxResult(success=False, error=f"目标类型 {proposal.target_type} 不需要沙箱执行")

        result = self.sandbox.execute(proposal.proposed_change, test_inputs)
        proposal.status = "sandboxed"
        return result

    # ── 评估 ────────────────────────────────────────────

    def evaluate(
        self,
        proposal_id: str,
        evaluation_fn: Callable = None,
        test_cases: List[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        评估进化建议的效果。

        评估维度：
        - 正确性：测试用例通过率
        - 性能：执行时间、资源占用
        - 兼容性：是否破坏现有功能
        - 改进度：相比原始版本的提升
        """
        proposal = self.proposals.get(proposal_id)
        if not proposal:
            return {"error": f"建议 {proposal_id} 不存在"}

        tests = test_cases or proposal.test_cases
        results = []
        passed = 0

        for i, test in enumerate(tests):
            test_result = {"name": test.get("name", f"test_{i}"), "passed": False}
            try:
                if evaluation_fn:
                    test_result["passed"] = evaluation_fn(proposal, test)
                else:
                    # 默认评估：在沙箱中执行测试代码
                    if "code" in test:
                        sandbox_result = self.sandbox.execute(test["code"])
                        test_result["passed"] = sandbox_result.success
                        test_result["sandbox"] = sandbox_result.to_dict()
                    else:
                        test_result["passed"] = None  # 无测试代码：未知，不自动 accept
                        test_result["skipped"] = True
                if test_result["passed"]:
                    passed += 1
            except Exception as e:
                test_result["error"] = str(e)
            results.append(test_result)

        pass_rate = passed / max(1, len(tests)) if tests else 1.0  # 无测试用例默认通过
        evaluation = {
            "proposal_id": proposal_id,
            "total_tests": len(tests),
            "passed_tests": passed,
            "pass_rate": round(pass_rate, 4),
            "results": results,
            "recommendation": "accept" if pass_rate >= 0.8 else "reject",
        }

        proposal.evaluation_result = evaluation
        proposal.status = "evaluated"
        return evaluation

    # ── 用户确认 ────────────────────────────────────────

    def needs_confirmation(self, proposal_id: str) -> bool:
        """判断是否需要用户确认。"""
        proposal = self.proposals.get(proposal_id)
        if not proposal:
            return False
        if proposal.risk_level == "low" and self.auto_confirm_low_risk:
            return False
        return True

    def confirm(self, proposal_id: str, confirmed: bool, user_feedback: str = "") -> bool:
        """
        用户确认或拒绝进化建议。
        """
        proposal = self.proposals.get(proposal_id)
        if not proposal:
            return False

        if confirmed:
            proposal.status = "confirmed"
            return True
        else:
            proposal.status = "rejected"
            return False

    # ── 提交版本 ────────────────────────────────────────

    def commit(self, proposal_id: str) -> Optional[Version]:
        """
        提交进化建议为一个新版本（快照）。

        这是防幻觉变砖的关键：任何修改都有版本记录，可以回滚。
        """
        proposal = self.proposals.get(proposal_id)
        if not proposal:
            return None

        # 获取当前所有文件
        current_files = self.version_store.get_all_files()

        # 应用修改
        filename = f"{proposal.target_type}_{proposal.target_name}.txt"
        current_files[filename] = proposal.proposed_change

        # 创建快照
        version = self.version_store.snapshot(
            message=f"[进化] {proposal.description}",
            files=current_files,
            author=proposal.author,
            metadata={
                "proposal_id": proposal.id,
                "target_type": proposal.target_type,
                "target_name": proposal.target_name,
                "risk_level": proposal.risk_level,
                "evaluation": proposal.evaluation_result,
            },
        )

        proposal.version_id = version.id
        proposal.status = "committed"
        self._evolution_cycles += 1
        return version

    # ── 应用到主系统 ────────────────────────────────────

    def apply(self, proposal_id: str) -> Tuple[bool, str]:
        """
        应用进化建议到主系统。

        对于提示词/工具描述/配置类型，直接应用。
        对于代码类型，需要外部系统加载新版本。
        """
        proposal = self.proposals.get(proposal_id)
        if not proposal:
            return False, f"建议 {proposal_id} 不存在"

        if proposal.status not in ("confirmed", "committed"):
            return False, f"建议状态为 {proposal.status}，需要先确认和提交"

        proposal.status = "applied"

        # 真落盘：若指定了真实文件路径，原子写入并备份原内容，防变砖
        import os, tempfile
        if proposal.real_path and proposal.target_type == "code":
            rp = os.path.abspath(os.path.expanduser(proposal.real_path))
            if not os.path.exists(rp):
                return False, f"目标文件不存在，拒绝创建以防误写: {rp}"
            with open(rp, "r", encoding="utf-8", errors="replace") as f:
                old = f.read()
            # 备份原内容到内存，回滚可恢复
            self._disk_backups[proposal.id] = (rp, old)
            # 原子写：先写临时文件再 rename
            d = os.path.dirname(rp)
            fd, tmp = tempfile.mkstemp(dir=d, suffix=".mbdtmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(proposal.proposed_change)
                os.replace(tmp, rp)
            finally:
                if os.path.exists(tmp):
                    os.remove(tmp)
            return True, f"已应用 code 并落盘: {rp}（原内容已备份，可 rollback）"
        return True, f"已应用 {proposal.target_type}: {proposal.target_name}（虚拟生效，未指定 real_path）"

    # ── 一键恢复（回滚）─────────────────────────────────

    def rollback(self, version_id: str = None) -> Tuple[bool, str]:
        """
        一键恢复：回滚到指定版本（默认上一个版本）。

        防幻觉变砖的核心功能。先恢复真落盘备份，再回退版本库。
        """
        # 1) 优先恢复真实文件（apply 时存的磁盘备份）
        restored = []
        for pid, (rp, old) in list(self._disk_backups.items()):
            try:
                with open(rp, "w", encoding="utf-8") as f:
                    f.write(old)
                restored.append(rp)
                self._disk_backups.pop(pid, None)
            except Exception as e:
                return False, f"真实文件 {rp} 回滚失败: {e}"
        # 2) 再回退版本库
        if version_id:
            ok, msg = self.version_store.rollback(version_id)
        else:
            ok, msg = self.version_store.rollback_to_parent()
        if restored:
            return True, f"已恢复 {len(restored)} 个真实文件: {restored}；{msg}"
        return ok, msg

    # ── 完整进化循环 ────────────────────────────────────

    def evolve(
        self,
        target_type: str,
        target_name: str,
        description: str,
        proposed_change: str,
        original_content: str = "",
        risk_level: str = "low",
        test_cases: List[Dict[str, Any]] = None,
        evaluation_fn: Callable = None,
        auto_apply: bool = True,
    ) -> Dict[str, Any]:
        """
        执行完整的进化循环（一键进化）。

        流程：提出 → 验证 → 沙箱 → 评估 → 确认 → 提交 → 应用
        任何一步失败自动回滚。

        返回完整的进化结果。
        """
        result = {
            "success": False,
            "steps": [],
            "proposal_id": None,
            "version_id": None,
            "error": None,
        }

        try:
            # 1. 提出
            proposal = self.propose(
                target_type=target_type,
                target_name=target_name,
                description=description,
                proposed_change=proposed_change,
                original_content=original_content,
                risk_level=risk_level,
                test_cases=test_cases,
            )
            result["proposal_id"] = proposal.id
            result["steps"].append("proposed")

            # 2. 验证
            safe, validation = self.validate(proposal.id)
            result["steps"].append(f"validated: {safe}")
            if not safe:
                result["error"] = f"安全验证失败: {validation}"
                return result

            # 3. 沙箱（仅代码类型）
            if target_type == "code":
                sandbox_result = self.run_in_sandbox(proposal.id)
                result["steps"].append(f"sandboxed: {sandbox_result.success}")
                if not sandbox_result.success:
                    result["error"] = f"沙箱执行失败: {sandbox_result.error}"
                    return result

            # 4. 评估
            if test_cases:
                evaluation = self.evaluate(proposal.id, evaluation_fn, test_cases)
                result["steps"].append(f"evaluated: {evaluation['pass_rate']}")
                if evaluation["recommendation"] == "reject":
                    result["error"] = f"评估未通过: 通过率 {evaluation['pass_rate']}"
                    return result

            # 5. 确认（低风险自动确认）
            if self.needs_confirmation(proposal.id):
                result["steps"].append("awaiting_confirmation")
                result["success"] = True
                result["message"] = "需要用户确认"
                return result
            else:
                self.confirm(proposal.id, True)
                result["steps"].append("confirmed(auto)")

            # 6. 提交
            version = self.commit(proposal.id)
            if version:
                result["version_id"] = version.id
                result["steps"].append(f"committed: {version.id}")

            # 7. 应用
            if auto_apply:
                applied, msg = self.apply(proposal.id)
                result["steps"].append(f"applied: {applied}")

            result["success"] = True
            return result

        except Exception as e:
            result["error"] = f"{type(e).__name__}: {e}"
            # 自动回滚
            if result.get("version_id"):
                self.rollback()
                result["steps"].append("rolled_back")
            return result

    # ── 用户投稿（创意工坊）──────────────────────────────

    def submit_user_contribution(
        self,
        user_id: str,
        target_type: str,
        target_name: str,
        description: str,
        content: str,
    ) -> EvolutionProposal:
        """
        用户提交改进（类似创意工坊）。

        用户投稿进入待审查队列，由专家委员会（或 AI 初审）审查后合并。
        """
        proposal = self.propose(
            target_type=target_type,
            target_name=target_name,
            description=f"[用户投稿@{user_id}] {description}",
            proposed_change=content,
            risk_level="medium",
            author=f"user:{user_id}",
        )
        proposal.status = "proposed"
        return proposal

    def review_contribution(self, proposal_id: str, approved: bool, reviewer: str = "expert_committee") -> bool:
        """
        专家委员会审查用户投稿。
        """
        proposal = self.proposals.get(proposal_id)
        if not proposal:
            return False
        if approved:
            return self.confirm(proposal_id, True, f"审查通过 by {reviewer}")
        else:
            proposal.status = "rejected"
            return False

    # ── 查询与统计 ──────────────────────────────────────

    def get_proposal(self, proposal_id: str) -> Optional[EvolutionProposal]:
        return self.proposals.get(proposal_id)

    def list_proposals(self, status: str = None, limit: int = 20) -> List[Dict[str, Any]]:
        """列出进化建议。"""
        proposals = list(self.proposals.values())
        if status:
            proposals = [p for p in proposals if p.status == status]
        proposals.sort(key=lambda p: p.timestamp, reverse=True)
        return [p.to_dict() for p in proposals[:limit]]

    def get_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        """获取进化历史（版本历史）。"""
        return self.version_store.history(limit)

    def get_stats(self) -> Dict[str, Any]:
        """获取自进化引擎统计。"""
        return {
            "total_proposals": len(self.proposals),
            "evolution_cycles": self._evolution_cycles,
            "applied": sum(1 for p in self.proposals.values() if p.status == "applied"),
            "rejected": sum(1 for p in self.proposals.values() if p.status == "rejected"),
            "awaiting_confirmation": sum(1 for p in self.proposals.values() if p.status == "confirmed" and p.risk_level != "low"),
            "version_store": self.version_store.get_stats(),
            "sandbox": self.sandbox.get_stats(),
        }

    def get_status_text(self) -> str:
        """获取人类可读的状态。"""
        stats = self.get_stats()
        lines = [
            "=== MBDSDR 自进化引擎 ===",
            f"进化循环次数: {stats['evolution_cycles']}",
            f"总建议数: {stats['total_proposals']}",
            f"已应用: {stats['applied']}, 已拒绝: {stats['rejected']}",
            f"版本数: {stats['version_store']['total_versions']}",
            f"沙箱执行次数: {stats['sandbox']['execution_count']}",
        ]
        return "\n".join(lines)
