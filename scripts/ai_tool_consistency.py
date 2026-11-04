# SPDX-License-Identifier: MIT
"""
工具描述与实际行为一致性扫描（P7 收尾）
=========================================

对注册表里的每个工具做三类检查：

1. **结构性错误（fatal）**：名字/描述为空、parameters 不是 object、
   required 里的键没有在 properties 声明、类型不在 JSON Schema 常用集合、
   enum 非列表、minimum > maximum。
2. **schema 漂移（drift）**：参数声明了 `default` 但类型不符；handler
   源码里读取了未声明的参数（模型无从得知该参数）；声明了但 handler
   从未读取的参数（疑似死参数）。
3. **描述一致性线索**：描述为空已归 fatal；描述里没有提到的参数不做
   语义判断（避免用自然语言猜测，误报比漏报贵）。

用法：
    python scripts/ai_tool_consistency.py [--limit N]

输出：output/ai/tool_consistency_<时间戳>.json
退出码：存在 fatal 问题时 1，否则 0。
"""
import argparse
import inspect
import json
import os
import re
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

ALLOWED_TYPES = {"string", "number", "integer", "boolean", "array", "object",
                 "null"}  # 联合类型（["string","null"]）里的可空标识

# 由注册表层统一校验、handler 不自己读取的网关参数（如人工门 arm_manual）
_GATE_ARGS = {"arm_manual"}

# handler 读取参数的三类常见写法：
#   args.get("k") / args["k"] / args.pop("k") / args.setdefault("k")
#   numeric_array(args, "k") / hex_bytes(args, "k") / _hex_list(args, "k")
# 注意只认已知参数读取助手：(args, "x") 里的字面量也可能是工具名/模式，
# 不能一概当成参数名（例如 _arm_gate(args, "code_modify_file")）。
_ARG_HELPERS = r"numeric_array|hex_bytes|_hex_list"
_ARG_READ_RE = re.compile(
    r"""args\s*(?:\.(?:get|pop|setdefault)\(\s*['"]([A-Za-z_]\w*)['"]"""
    r"""|\[\s*['"]([A-Za-z_]\w*)['"]\s*\])"""
    r"""|(?:\b(?:""" + _ARG_HELPERS + r""")\s*\(\s*args\s*,\s*['"]([A-Za-z_]\w*)['"])""")

_UNSET = object()


def _type_ok(param_type: str, value) -> bool:
    """默认值类型是否匹配声明类型（bool 不算 integer/number）。"""
    if param_type == "string":
        return isinstance(value, str)
    if param_type == "boolean":
        return isinstance(value, bool)
    if param_type == "array":
        return isinstance(value, list)
    if param_type == "object":
        return isinstance(value, dict)
    if param_type in ("number", "integer"):
        if isinstance(value, bool):
            return False
        return isinstance(value, (int, float))
    return True


def _handler_source(handler) -> str:
    try:
        return inspect.getsource(handler)
    except Exception:
        return ""


def scan_tool(name: str, tool) -> dict:
    """扫描单个工具，返回 {"name", "fatal": [...], "drift": [...], "source_scanned": bool}。"""
    fatal = []
    drift = []
    fn = (tool.get("definition") or {}).get("function") or {}
    desc = (fn.get("description") or "").strip()
    params = fn.get("parameters") or {}

    if not name or not name.strip():
        fatal.append("工具名为空")
    if not desc:
        fatal.append("工具描述为空")
    if not isinstance(params, dict) or params.get("type") != "object":
        fatal.append("parameters 必须是 object")
    props = params.get("properties") if isinstance(params, dict) else None
    if not isinstance(props, dict):
        fatal.append("parameters.properties 缺失或不是对象")
        props = {}

    required = params.get("required") if isinstance(params, dict) else None
    if required is not None and not isinstance(required, list):
        fatal.append("parameters.required 必须是数组")
        required = []
    for rname in required or []:
        if rname not in props:
            fatal.append(f"required 参数 '{rname}' 未在 properties 声明")

    for pname, spec in props.items():
        if not isinstance(spec, dict):
            fatal.append(f"参数 '{pname}' 的 schema 不是对象")
            continue
        ptype = spec.get("type")
        types = ptype if isinstance(ptype, list) else ([ptype] if ptype else [])
        if isinstance(ptype, list) and not ptype:
            fatal.append(f"参数 '{pname}' 类型数组为空")
        for t in types:
            if not isinstance(t, str) or t not in ALLOWED_TYPES:
                fatal.append(f"参数 '{pname}' 类型 '{t}' 不受支持")
        enum = spec.get("enum")
        if enum is not None and not isinstance(enum, list):
            fatal.append(f"参数 '{pname}' 的 enum 必须是数组")
        lo, hi = spec.get("minimum"), spec.get("maximum")
        if lo is not None and hi is not None:
            try:
                if lo > hi:
                    fatal.append(f"参数 '{pname}' minimum({lo}) > maximum({hi})")
            except TypeError:
                fatal.append(f"参数 '{pname}' 的 minimum/maximum 不可比较")
        default = spec.get("default", _UNSET)
        if default is not _UNSET and types:
            # 联合类型（["string","null"]）只要匹配其中一个即可
            if not any(_type_ok(t, default) for t in types):
                drift.append(
                    f"参数 '{pname}' default={default!r} 与声明类型 '{ptype}' 不符")

    src = _handler_source(tool.get("handler"))
    if src:
        # 去掉函数签名，避免 "def h(args):" 被误判成「把 args 整体交给某个助手」
        body = re.sub(r"def\s+\w+\s*\(\s*args\s*(?:[^)]*)\)\s*:", "", src,
                      count=1)
        reads = set()
        for m in _ARG_READ_RE.finditer(body):
            reads.add(m.group(1) or m.group(2) or m.group(3))
        undeclared = sorted(reads - set(props))
        if undeclared:
            drift.append("handler 读取未声明参数: " + ", ".join(undeclared))
        # 参数名以字符串形式传给下层助手（如 _load("sig_a_path", ...)）时
        # 无法按属性访问识别；只要字面量出现过就视为「被引用」，宁漏不误报。
        literals = set(re.findall(r"""['"]([A-Za-z_]\w*)['"]""", body))
        unread = sorted(set(props) - reads - literals - _GATE_ARGS)
        # `def h(args): ... **args` 这类整体转发、或 `_helper(args)` 把整个
        # 参数对象交给下层解析的 handler，无法按名字判断读取了哪些键，
        # 直接跳过「声明但未读取」检查，避免假阳性。
        _delegate_names = {m.group(1) for m in
                           re.finditer(r"(\w+)\s*\(\s*args\s*(?:,|\))", body)}
        forwards_wholesale = ("**args" in body or "**kwargs" in body
                              or bool(_delegate_names))
        if reads and unread and not forwards_wholesale:
            drift.append("声明但 handler 未读取: " + ", ".join(unread))

    return {
        "name": name,
        "fatal": fatal,
        "drift": drift,
        "source_scanned": bool(src),
    }


def scan_registry(registry) -> dict:
    """扫描整个注册表，返回汇总报告（fatal 非空即视为不一致）。"""
    tools = getattr(registry, "tools", {}) or {}
    fatal = []
    drift = []
    scanned_sources = 0
    for name, tool in tools.items():
        result = scan_tool(name, tool)
        if result["source_scanned"]:
            scanned_sources += 1
        for issue in result["fatal"]:
            fatal.append({"tool": name, "issue": issue})
        for issue in result["drift"]:
            drift.append({"tool": name, "issue": issue})
    return {
        "total": len(tools),
        "fatal": fatal,
        "fatal_count": len(fatal),
        "drift": drift,
        "drift_count": len(drift),
        "source_scanned": scanned_sources,
        "ok": not fatal,
        "note": ("结构性错误视为不一致（fatal）；drift 为线索，需人工判断，"
                 "不做语义猜测。handler 源码不可读（C 扩展/包装器）时跳过参数读取检查。"),
    }


def _build_agent():
    from mbdsdr_ai.agent import MBDSDRAgent
    from mbdsdr_ai.config import AgentConfig
    return MBDSDRAgent(AgentConfig(api_key="sk-test", model="test-model"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0,
                        help="只打印前 N 条 drift（0 表示全部）")
    args = parser.parse_args(argv)

    agent = _build_agent()
    report = scan_registry(agent.tool_registry)
    report["generated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    report["offline"] = True
    report["note"] += " 本扫描不调用任何工具、不联网、不产生副作用。"

    out_dir = os.path.join(_ROOT, "output", "ai")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir,
                        f"tool_consistency_{time.strftime('%Y%m%d_%H%M%S')}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"工具总数: {report['total']}，可读源码: {report['source_scanned']}")
    print(f"结构性错误: {report['fatal_count']}，漂移线索: {report['drift_count']}")
    shown = report["drift"] if args.limit <= 0 else report["drift"][:args.limit]
    for item in shown:
        print(f"  [drift] {item['tool']}: {item['issue']}")
    for item in report["fatal"]:
        print(f"  [fatal] {item['tool']}: {item['issue']}")
    print(f"报告: {path}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
