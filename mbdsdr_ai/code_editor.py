"""
MBDSDR AI 内核 - 代码编辑器（自编程核心）
==========================================
CodeEditor：让模型能够读取、修改、热加载源代码，并提交到 git 仓库。

这是自编程/自进化的核心能力：
- 读取源代码文件
- 修改源代码文件（修改前自动备份）
- 热加载修改后的模块（无需重启）
- 运行测试验证修改
- Git commit（提交到版本控制）
- 一键恢复（回滚到修改前）

对照用户需求：
- "源代码都是可那啥的" → 可读取、可修改
- "一键可恢复" → 修改前自动备份，可回滚
- "可贡献，可 commit" → git commit
- "模型可以自编程" → 读取→修改→测试→热加载→commit
- "开源软件" → 所有源代码可访问、可修改
"""

import os
import sys

import time
import shutil
import subprocess
import importlib
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional, Tuple
from enum import Enum


class EditStatus(str, Enum):
    """编辑状态。"""
    PENDING = "pending"  # 待编辑
    BACKED_UP = "backed_up"  # 已备份
    MODIFIED = "modified"  # 已修改
    TESTED = "tested"  # 已测试
    HOT_RELOADED = "hot_reloaded"  # 已热加载
    COMMITTED = "committed"  # 已 commit
    ROLLED_BACK = "rolled_back"  # 已回滚
    FAILED = "failed"  # 失败


@dataclass
class EditRecord:
    """编辑记录。"""
    edit_id: str
    file_path: str
    original_content: str
    modified_content: str
    backup_path: str = ""
    status: EditStatus = EditStatus.PENDING
    test_result: Dict[str, Any] = field(default_factory=dict)
    commit_hash: str = ""
    error: str = ""
    timestamp: float = field(default_factory=time.time)
    description: str = ""
    guardian_snap_id: str = ""  # 守护者快照 ID（修改前由 guardian 真备份）

    def to_dict(self) -> Dict[str, Any]:
        return {
            "edit_id": self.edit_id,
            "file_path": self.file_path,
            "backup_path": self.backup_path,
            "status": self.status.value,
            "test_result": self.test_result,
            "commit_hash": self.commit_hash,
            "error": self.error,
            "timestamp": self.timestamp,
            "description": self.description,
            "original_size": len(self.original_content),
            "modified_size": len(self.modified_content),
        }


class CodeEditor:
    """
    代码编辑器：自编程核心。

    让模型能够读取、修改、热加载源代码，并提交到 git 仓库。
    所有修改前自动备份，支持一键恢复。
    """

    def __init__(
        self,
        project_root: str = None,
        backup_dir: str = None,
        auto_backup: bool = True,
        git_enabled: bool = True,
    ):
        if project_root is None:
            # 默认项目根目录（mbdsdr_ai 的上级目录）
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.project_root = project_root

        if backup_dir is None:
            backup_dir = os.path.expanduser("~/.mbdsdr/code_editor/backups")
        self.backup_dir = backup_dir
        os.makedirs(backup_dir, exist_ok=True)

        self.auto_backup = auto_backup
        self.git_enabled = git_enabled
        self.edits: Dict[str, EditRecord] = {}
        self._counter = 0

        # 守护者快照引擎：修改前真备份（与 code_editor 自身的 .bak 互补）。
        # 失败不阻断主流程——guardian 是额外防线，不是单点依赖。
        self.guardian = None
        try:
            from .guardian import Guardian
            self.guardian = Guardian(
                store_path=os.path.join(backup_dir, "guardian_snapshots"))
        except Exception as e:
            print(f"警告: CodeEditor 守护者初始化失败（不影响备份主流程）: {e}")

        # 检查 git 是否可用
        self.git_available = self._check_git()

    def _check_git(self) -> bool:
        """检查 git 是否可用。"""
        try:
            result = subprocess.run(
                ["git", "--version"],
                capture_output=True, text=True, timeout=5
            )
            return result.returncode == 0
        except Exception:
            return False

    def read_file(self, file_path: str, max_lines: int = 0) -> Tuple[str, int]:
        """
        读取源代码文件。

        file_path: 相对于项目根目录的路径，或绝对路径
        max_lines: 最大读取行数（0=全部）
        返回 (内容, 行数)
        """
        full_path = self._resolve_path(file_path)

        if not file_path.strip():
            raise FileNotFoundError("file_path 不能为空：请指定要读取的文件（相对项目根目录或绝对路径）。")
        if os.path.isdir(full_path):
            raise FileNotFoundError(f"目标是目录而非文件: {full_path}（请指定具体文件路径）。")
        if not os.path.exists(full_path):
            raise FileNotFoundError(f"文件不存在: {full_path}")

        with open(full_path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()

        lines = content.split('\n')
        if max_lines > 0 and len(lines) > max_lines:
            content = '\n'.join(lines[:max_lines]) + f"\n... (共 {len(lines)} 行，已截断)"

        return content, len(lines)

    def modify_file(
        self,
        file_path: str,
        new_content: str,
        description: str = "",
        auto_backup: bool = None,
    ) -> EditRecord:
        """
        修改源代码文件。

        修改前自动备份，支持一键恢复。

        file_path: 相对于项目根目录的路径
        new_content: 新的文件内容
        description: 修改描述
        返回 EditRecord
        """
        full_path = self._resolve_path(file_path)
        use_backup = auto_backup if auto_backup is not None else self.auto_backup

        if not file_path.strip():
            raise FileNotFoundError("file_path 不能为空：请指定要修改的文件。")
        if os.path.isdir(full_path):
            raise FileNotFoundError(f"目标是目录而非文件: {full_path}（请指定具体文件路径）。")

        # 安全：禁止修改核心安全文件（防自编程破坏安全边界）
        _PROTECTED_FILES = {
            "agent.py", "tool_registry.py", "context_manager.py",
            "sandbox.py", "config.py", "code_editor.py",
        }
        if os.path.basename(full_path) in _PROTECTED_FILES:
            raise PermissionError(
                f"安全限制：禁止修改核心安全文件 {os.path.basename(full_path)!r}")

        # 读取原始内容
        original_content = ""
        if os.path.exists(full_path):
            with open(full_path, 'r', encoding='utf-8', errors='replace') as f:
                original_content = f.read()

        # 创建编辑记录
        self._counter += 1
        edit_id = f"edit_{int(time.time() * 1000)}_{self._counter}"
        record = EditRecord(
            edit_id=edit_id,
            file_path=full_path,
            original_content=original_content,
            modified_content=new_content,
            description=description,
        )

        # 自动备份
        if use_backup:
            backup_path = self._backup_file(full_path, original_content, edit_id)
            record.backup_path = backup_path
            record.status = EditStatus.BACKED_UP

        # 守护者真快照：在写盘之前由 guardian 复制原文件到快照库。
        # 这是防幻觉变砖的第二道防线（独立于上面的 .bak）。
        if self.guardian is not None and os.path.exists(full_path):
            try:
                snap = self.guardian.create_snapshot(
                    full_path,
                    label=f"修改前: {os.path.basename(full_path)}",
                    metadata={"edit_id": edit_id, "description": description},
                )
                record.guardian_snap_id = snap.snap_id
            except Exception as e:
                # 守护者快照失败不阻断编辑本身（.bak 仍在）
                record.error = f"guardian 快照失败: {e}"

        # 写入新内容
        try:
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, 'w', encoding='utf-8') as f:
                f.write(new_content)
            record.status = EditStatus.MODIFIED
        except Exception as e:
            record.status = EditStatus.FAILED
            record.error = f"写入失败: {type(e).__name__}: {str(e)}"
            self.edits[edit_id] = record
            raise

        self.edits[edit_id] = record
        return record

    def modify_section(
        self,
        file_path: str,
        old_section: str,
        new_section: str,
        description: str = "",
    ) -> EditRecord:
        """
        修改文件中的一段内容（局部修改）。

        old_section: 要替换的旧内容
        new_section: 新内容
        """
        content, _ = self.read_file(file_path)

        if old_section not in content:
            raise ValueError("未找到要替换的内容段")

        new_content = content.replace(old_section, new_section, 1)
        return self.modify_file(file_path, new_content, description)

    def run_tests(
        self,
        edit_id: str,
        test_command: str = None,
        test_files: List[str] = None,
    ) -> Dict[str, Any]:
        """
        运行测试验证修改。

        test_command: 自定义测试命令
        test_files: 要测试的文件列表（运行 py_compile）
        """
        record = self.edits.get(edit_id)
        if not record:
            return {"success": False, "error": f"编辑记录不存在: {edit_id}"}

        results = {
            "success": True,
            "tests": [],
            "passed": 0,
            "failed": 0,
            "total": 0,
        }

        # 运行 py_compile 语法检查
        if test_files:
            for test_file in test_files:
                full_path = self._resolve_path(test_file)
                if os.path.exists(full_path):
                    try:
                        result = subprocess.run(
                            [sys.executable, "-m", "py_compile", full_path],
                            capture_output=True, text=True, timeout=30
                        )
                        passed = result.returncode == 0
                        results["tests"].append({
                            "file": test_file,
                            "type": "py_compile",
                            "passed": passed,
                            "output": result.stderr[:500] if not passed else "",
                        })
                        if passed:
                            results["passed"] += 1
                        else:
                            results["failed"] += 1
                            results["success"] = False
                    except Exception as e:
                        results["tests"].append({
                            "file": test_file,
                            "type": "py_compile",
                            "passed": False,
                            "error": str(e),
                        })
                        results["failed"] += 1
                        results["success"] = False

        # 运行测试命令：禁止 shell=True，只允许 python/pytest/unittest 子命令
        if test_command:
            try:
                import shlex as _shlex
                argv = _shlex.split(test_command)
                if not argv:
                    raise ValueError("空测试命令")
                base = os.path.basename(argv[0]).lower()
                if base not in ("python", "python3", "pytest", "py.test"):
                    raise ValueError(
                        f"安全限制：测试命令只允许 python/pytest，收到 {base!r}")
                result = subprocess.run(
                    argv, shell=False, capture_output=True, text=True,
                    timeout=120, cwd=self.project_root
                )
                passed = result.returncode == 0
                results["tests"].append({
                    "command": test_command,
                    "type": "custom",
                    "passed": passed,
                    "output": (result.stdout + result.stderr)[:1000],
                })
                if passed:
                    results["passed"] += 1
                else:
                    results["failed"] += 1
                    results["success"] = False
            except Exception as e:
                results["tests"].append({
                    "command": test_command,
                    "type": "custom",
                    "passed": False,
                    "error": str(e),
                })
                results["failed"] += 1
                results["success"] = False

        results["total"] = len(results["tests"])
        record.test_result = results
        if results["success"]:
            record.status = EditStatus.TESTED
        return results

    def hot_reload(self, module_name: str) -> Tuple[bool, str]:
        """
        热加载模块（无需重启）。

        module_name: 模块名，如 "mbdsdr_ai.dsp"
        """
        try:
            if module_name in sys.modules:
                importlib.reload(sys.modules[module_name])
                return True, f"模块 {module_name} 已热加载"
            else:
                importlib.import_module(module_name)
                return True, f"模块 {module_name} 已导入"
        except Exception as e:
            return False, f"热加载失败: {type(e).__name__}: {str(e)}"

    def git_commit(
        self,
        message: str,
        files: List[str] = None,
        edit_id: str = None,
    ) -> Tuple[bool, str]:
        """
        Git commit（提交到版本控制）。

        message: commit 信息
        files: 要提交的文件列表（None=全部修改）
        edit_id: 关联的编辑记录 ID
        """
        if not self.git_available:
            return False, "git 不可用"

        try:
            # git add
            if files:
                add_files = [self._resolve_path(f) for f in files]
                subprocess.run(["git", "add"] + add_files, cwd=self.project_root,
                               capture_output=True, text=True, timeout=30)
            else:
                subprocess.run(["git", "add", "-A"], cwd=self.project_root,
                               capture_output=True, text=True, timeout=30)

            # git commit
            result = subprocess.run(
                ["git", "commit", "-m", message],
                cwd=self.project_root, capture_output=True, text=True, timeout=30
            )

            if result.returncode == 0:
                # 获取 commit hash
                hash_result = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=self.project_root, capture_output=True, text=True, timeout=10
                )
                commit_hash = hash_result.stdout.strip()[:8]

                # 更新编辑记录
                if edit_id and edit_id in self.edits:
                    self.edits[edit_id].commit_hash = commit_hash
                    self.edits[edit_id].status = EditStatus.COMMITTED

                return True, f"已提交: {commit_hash}\n{result.stdout[:500]}"
            else:
                return False, f"提交失败: {result.stderr[:500]}"

        except Exception as e:
            return False, f"Git 操作失败: {type(e).__name__}: {str(e)}"

    def git_status(self) -> Dict[str, Any]:
        """获取 git 状态。"""
        if not self.git_available:
            return {"available": False}

        try:
            result = subprocess.run(
                ["git", "status", "--short"],
                cwd=self.project_root, capture_output=True, text=True, timeout=10
            )
            files = [line.strip() for line in result.stdout.strip().split('\n') if line.strip()]

            # 获取当前分支
            branch_result = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=self.project_root, capture_output=True, text=True, timeout=10
            )
            branch = branch_result.stdout.strip()

            # 获取最近 commit
            log_result = subprocess.run(
                ["git", "log", "--oneline", "-5"],
                cwd=self.project_root, capture_output=True, text=True, timeout=10
            )
            recent_commits = [line.strip() for line in log_result.stdout.strip().split('\n') if line.strip()]

            return {
                "available": True,
                "branch": branch,
                "modified_files": files,
                "recent_commits": recent_commits,
            }
        except Exception as e:
            return {"available": True, "error": str(e)}

    def rollback(self, edit_id: str) -> Tuple[bool, str]:
        """
        一键恢复：回滚到修改前的状态。

        防幻觉变砖的核心功能。
        """
        record = self.edits.get(edit_id)
        if not record:
            return False, f"编辑记录不存在: {edit_id}"

        if not record.backup_path or not os.path.exists(record.backup_path):
            return False, "备份文件不存在，无法回滚"

        try:
            # 从备份恢复
            shutil.copy2(record.backup_path, record.file_path)
            record.status = EditStatus.ROLLED_BACK
            return True, f"已回滚到修改前: {record.file_path}"
        except Exception as e:
            return False, f"回滚失败: {type(e).__name__}: {str(e)}"

    def list_edits(self, limit: int = 20) -> List[Dict[str, Any]]:
        """列出编辑记录。"""
        edits = sorted(self.edits.values(), key=lambda e: e.timestamp, reverse=True)
        return [e.to_dict() for e in edits[:limit]]

    def get_edit(self, edit_id: str) -> Optional[EditRecord]:
        return self.edits.get(edit_id)

    def get_stats(self) -> Dict[str, Any]:
        """获取代码编辑器统计。"""
        total = len(self.edits)
        by_status = {}
        for e in self.edits.values():
            by_status[e.status.value] = by_status.get(e.status.value, 0) + 1

        return {
            "total_edits": total,
            "edits_by_status": by_status,
            "git_available": self.git_available,
            "project_root": self.project_root,
            "backup_dir": self.backup_dir,
            "auto_backup": self.auto_backup,
        }

    # 禁止读取的敏感路径片段（防 SSH 私钥/凭证外泄）
    _FORBIDDEN_FRAGMENTS = (
        ".ssh", ".aws", ".gnupg", ".config/gcloud", ".docker",
        "id_rsa", "id_dsa", ".npmrc", ".pypirc", ".netrc",
        "credentials", "secrets", ".env",
    )

    def _resolve_path(self, file_path: str) -> str:
        """解析文件路径：限制在 project_root 内，拒绝敏感凭证路径。"""
        if not file_path or not file_path.strip():
            raise FileNotFoundError("file_path 不能为空")
        # 敏感片段黑名单（无论相对/绝对）
        low = file_path.lower()
        for frag in self._FORBIDDEN_FRAGMENTS:
            if frag in low:
                raise PermissionError(
                    f"安全限制：禁止访问敏感路径 {frag!r}")
        if os.path.isabs(file_path):
            full = os.path.normpath(file_path)
        else:
            full = os.path.normpath(os.path.join(self.project_root, file_path))
        # 必须落在 project_root 内，防 ../../etc/passwd
        root = os.path.normpath(self.project_root)
        if not (full == root or full.startswith(root + os.sep)):
            raise PermissionError(
                f"安全限制：只能访问项目根目录内文件，{full!r} 越界")
        return full

    def _backup_file(self, file_path: str, content: str, edit_id: str) -> str:
        """备份文件。"""
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        backup_name = f"{os.path.basename(file_path)}.{timestamp}.{edit_id}.bak"
        backup_path = os.path.join(self.backup_dir, backup_name)

        with open(backup_path, 'w', encoding='utf-8') as f:
            f.write(content)

        return backup_path

    def generate_code(
        self,
        requirement: str,
        language: str = "python",
        context: str = "",
    ) -> Dict[str, Any]:
        """
        根据需求生成代码框架（模板生成，不调用 LLM）。

        这是一个辅助方法，实际的代码生成应该由 LLM 完成。
        这里提供常见的代码模板生成。
        """
        templates = {
            "python_module": f'"""\n{requirement}\n"""\n\nimport json\nimport time\nfrom typing import Dict, List, Any, Optional\n\n\nclass {requirement.split()[0].capitalize()}:\n    """\n    {requirement}\n    """\n\n    def __init__(self):\n        pass\n\n    def execute(self, input_data: Dict[str, Any]) -> Dict[str, Any]:\n        """执行处理。"""\n        return {{"success": True, "input": input_data}}\n',
            "sdr_tool": f'"""\nSDR 工具: {requirement}\n"""\n\ndef {requirement.lower().replace(" ", "_")}(params: Dict[str, Any]) -> Dict[str, Any]:\n    """\n    {requirement}\n    """\n    result = {{\n        "success": True,\n        "tool": "{requirement.lower().replace(" ", "_")}",\n        "params": params,\n    }}\n    return result\n',
        }

        # 简单的模板选择
        if "sdr" in requirement.lower() or "tool" in requirement.lower():
            template = templates["sdr_tool"]
        else:
            template = templates["python_module"]

        return {
            "requirement": requirement,
            "language": language,
            "code": template,
            "note": "这是基础模板，建议用 LLM 生成更完整的代码",
        }
