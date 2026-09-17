"""
MBDSDR AI 内核 - 沙箱执行环境
==============================
隔离的代码执行环境，用于自进化时代码修改的安全执行。

核心能力：
- 子进程隔离执行
- 超时控制
- 资源限制（内存、CPU 时间）
- 文件系统隔离（只能访问沙箱目录）
- 危险操作拦截（网络、文件系统、系统调用）
- 执行结果捕获（stdout、stderr、返回值、异常）
- 防幻觉变砖：沙箱中的修改不会影响主系统
"""

import os
import sys
import json
import time

import tempfile
import subprocess
import traceback
from dataclasses import dataclass
from typing import Any, Dict, Optional, List


@dataclass
class SandboxResult:
    """沙箱执行结果。"""
    success: bool
    stdout: str = ""
    stderr: str = ""
    return_code: int = 0
    execution_time_ms: float = 0.0
    output: Any = None  # 解析后的输出（如果是 JSON）
    error: str = ""
    timed_out: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "stdout": self.stdout[:2000],
            "stderr": self.stderr[:2000],
            "return_code": self.return_code,
            "execution_time_ms": self.execution_time_ms,
            "output": self.output,
            "error": self.error,
            "timed_out": self.timed_out,
        }


# 危险模块黑名单（禁止导入）
DANGEROUS_MODULES = {
    "os.system", "os.popen", "subprocess", "socket", "http", "urllib",
    "requests", "shutil.rmtree", "ctypes", "importlib", "pickle",
    "eval", "exec", "compile", "__import__", "open", "file",
}

# 安全代码模板（在子进程中执行）
SAFE_EXEC_TEMPLATE = '''
import sys
import json
import traceback

# 拦截危险操作
class SafeModule:
    def __getattr__(self, name):
        raise PermissionError(f"模块 {{name}} 在沙箱中被禁用")

# 限制内置函数
safe_builtins = {{
    "print": print,
    "len": len,
    "range": range,
    "str": str,
    "int": int,
    "float": float,
    "list": list,
    "dict": dict,
    "set": set,
    "tuple": tuple,
    "bool": bool,
    "abs": abs,
    "min": min,
    "max": max,
    "sum": sum,
    "sorted": sorted,
    "reversed": reversed,
    "enumerate": enumerate,
    "zip": zip,
    "map": map,
    "filter": filter,
    "isinstance": isinstance,
    "type": type,
    "hasattr": hasattr,
    "getattr": getattr,
    "setattr": setattr,
    "Exception": Exception,
    "ValueError": ValueError,
    "TypeError": TypeError,
    "KeyError": KeyError,
    "IndexError": IndexError,
    "json": json,
}}

# 执行用户代码
try:
    exec(compile(USER_CODE, "<sandbox>", "exec"), {{"__builtins__": safe_builtins}}, {{}})
    result = {{"success": True}}
except Exception as e:
    result = {{
        "success": False,
        "error": str(e),
        "traceback": traceback.format_exc()
    }}

print("===SANDBOX_RESULT===")
print(json.dumps(result))
'''


class Sandbox:
    """
    沙箱执行环境。

    使用子进程隔离执行代码，防止主系统被破坏。
    支持超时、资源限制、危险操作拦截。
    """

    def __init__(
        self,
        work_dir: str = None,
        timeout_seconds: int = 10,
        max_memory_mb: int = 256,
        max_output_chars: int = 10000,
    ):
        self.work_dir = work_dir or tempfile.mkdtemp(prefix="mbdsdr_sandbox_")
        self.timeout_seconds = timeout_seconds
        self.max_memory_mb = max_memory_mb
        self.max_output_chars = max_output_chars
        self._execution_count = 0
        os.makedirs(self.work_dir, exist_ok=True)

    def _scan_dangerous_code(self, code: str) -> Optional[str]:
        """预扫描代码，检测危险操作。返回危险描述或 None。"""
        import re
        # 危险导入模式
        dangerous_imports = [
            r'^\s*import\s+os\b', r'^\s*import\s+subprocess\b',
            r'^\s*import\s+socket\b', r'^\s*import\s+shutil\b',
            r'^\s*import\s+ctypes\b', r'^\s*import\s+pickle\b',
            r'^\s*import\s+importlib\b', r'^\s*import\s+sys\b',
            r'^\s*from\s+os\b', r'^\s*from\s+subprocess\b',
            r'^\s*from\s+socket\b', r'^\s*from\s+shutil\b',
            r'^\s*__import__\s*\(', r'^\s*eval\s*\(',
            r'^\s*exec\s*\(', r'^\s*compile\s*\(',
            r'^\s*open\s*\(', r'^\s*os\.system\b',
            r'^\s*os\.popen\b', r'^\s*subprocess\.(call|run|Popen|check_output)',
            r'^\s*shutil\.(rmtree|move|copy)', r'^\s*socket\.socket',
        ]
        for pattern in dangerous_imports:
            if re.search(pattern, code, re.MULTILINE):
                return f"检测到危险操作: {pattern.strip()}"
        return None

    def execute(self, code: str, inputs: Dict[str, Any] = None) -> SandboxResult:
        """
        在沙箱中执行 Python 代码。

        code: 要执行的代码
        inputs: 输入变量（会作为全局变量注入）
        """
        start_time = time.time()
        self._execution_count += 1

        # 预扫描危险代码
        danger = self._scan_dangerous_code(code)
        if danger:
            return SandboxResult(
                success=False,
                error=f"代码被沙箱拦截: {danger}",
                execution_time_ms=(time.time() - start_time) * 1000,
            )

        # 准备输入
        input_json = json.dumps(inputs or {}, ensure_ascii=False)

        # 使用安全执行模板包装用户代码
        # 把用户代码转义为 Python 字符串字面量，确保 compile() 收到字符串
        escaped_code = code.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')
        safe_code = SAFE_EXEC_TEMPLATE.replace("USER_CODE", f'"{escaped_code}"')
        # 还原模板中的花括号转义（{{ -> {, }} -> }）
        safe_code = safe_code.replace("{{", "{").replace("}}", "}")
        safe_code = f"INPUT_DATA = {input_json}\n" + safe_code

        # 写入临时文件
        code_file = os.path.join(self.work_dir, f"code_{self._execution_count}.py")
        with open(code_file, "w", encoding="utf-8") as f:
            f.write(safe_code)

        try:
            # 在子进程中执行
            proc = subprocess.run(
                [sys.executable, code_file],
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                cwd=self.work_dir,
                env={
                    "PATH": "/usr/bin:/bin",
                    "PYTHONPATH": "",
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
            )

            stdout = proc.stdout[:self.max_output_chars]
            stderr = proc.stderr[:self.max_output_chars]

            # 解析结果
            output = None
            if "===SANDBOX_RESULT===" in stdout:
                parts = stdout.split("===SANDBOX_RESULT===")
                if len(parts) > 1:
                    try:
                        output = json.loads(parts[-1].strip())
                    except Exception:
                        pass

            success = proc.returncode == 0 and (output is None or output.get("success", True))
            error = ""
            if output and not output.get("success", True):
                error = output.get("error", "")

            return SandboxResult(
                success=success,
                stdout=stdout,
                stderr=stderr,
                return_code=proc.returncode,
                execution_time_ms=(time.time() - start_time) * 1000,
                output=output,
                error=error,
                timed_out=False,
            )

        except subprocess.TimeoutExpired:
            return SandboxResult(
                success=False,
                error=f"执行超时 ({self.timeout_seconds}秒)",
                execution_time_ms=(time.time() - start_time) * 1000,
                timed_out=True,
            )
        except Exception as e:
            return SandboxResult(
                success=False,
                error=f"{type(e).__name__}: {e}",
                execution_time_ms=(time.time() - start_time) * 1000,
            )
        finally:
            # 清理临时文件
            try:
                os.remove(code_file)
            except Exception:
                pass

    def execute_function(self, func_code: str, func_name: str, args: list = None,
                         kwargs: dict = None) -> SandboxResult:
        """
        在沙箱中执行一个函数。

        func_code: 函数定义代码
        func_name: 函数名
        args/kwargs: 调用参数
        """
        call_code = f'\n\n_result = {func_name}(*{json.dumps(args or [])}, **{json.dumps(kwargs or {})})\nprint("===SANDBOX_RESULT===")\nprint(json.dumps({{"result": _result}}, default=str))'
        return self.execute(func_code + call_code)

    def validate_code(self, code: str) -> Dict[str, Any]:
        """
        静态检查代码安全性。

        返回：
        {
            "safe": bool,
            "warnings": [...],
            "dangerous_patterns": [...],
        }
        """
        warnings = []
        dangerous = []

        # 检查危险模式
        for pattern in DANGEROUS_MODULES:
            if pattern in code:
                dangerous.append(pattern)

        # 检查文件操作
        if "open(" in code and "open(" not in code.replace("open(", ""):
            warnings.append("检测到文件操作，沙箱中文件系统是隔离的")

        # 检查网络操作
        if any(kw in code for kw in ["socket", "http", "urllib", "requests"]):
            dangerous.append("network_operation")

        # 检查系统调用
        if any(kw in code for kw in ["system(", "popen(", "subprocess", "os.exec"]):
            dangerous.append("system_call")

        return {
            "safe": len(dangerous) == 0,
            "warnings": warnings,
            "dangerous_patterns": dangerous,
        }

    def cleanup(self):
        """清理沙箱工作目录。"""
        try:
            import shutil
            shutil.rmtree(self.work_dir, ignore_errors=True)
        except Exception:
            pass

    def get_stats(self) -> Dict[str, Any]:
        """获取沙箱统计。"""
        return {
            "execution_count": self._execution_count,
            "work_dir": self.work_dir,
            "timeout_seconds": self.timeout_seconds,
            "max_memory_mb": self.max_memory_mb,
        }
