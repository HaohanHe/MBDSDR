"""Runtime verification of ToolRegistry call chain."""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.tool_registry import ToolRegistry, ToolResult

tr = ToolRegistry(tool_output_max_chars=4000)

# 1. Register a test tool
tr.register(
    name="test_echo",
    description="Echo back input",
    parameters={"type": "object", "properties": {"msg": {"type": "string"}}, "required": ["msg"]},
    handler=lambda args: ToolResult(True, f"ECHO: {args['msg']}"),
    category="test",
)

# 2. Success path
r = tr.call("test_echo", {"msg": "hello"})
print("SUCCESS:", r.success, "|", r.content)

# 3. Missing required param
r = tr.call("test_echo", {})
print("MISSING:", r.success, "|", r.error, "|", r.content[:80])

# 4. Nonexistent tool
r = tr.call("nonexistent_tool", {})
print("NOEXIST:", r.success, "|", r.error)

# 5. Exception in handler
def _boom(a):
    raise RuntimeError("kaboom")
tr.register(name="test_boom", description="boom",
            parameters={"type": "object", "properties": {}}, handler=_boom)
r = tr.call("test_boom", {})
print("EXC:", r.success, "|", r.error, "|", r.content[:80])

# 6. Output truncation
tr.register(name="test_long", description="long",
            parameters={"type": "object", "properties": {}},
            handler=lambda a: ToolResult(True, "X" * 10000))
r = tr.call("test_long", {})
print("TRUNC:", r.truncated, "| len=", len(r.content))

# 7. call_from_model with bad JSON
r = tr.call_from_model({"function": {"name": "test_echo", "arguments": "{bad json"}}})
print("BADJSON:", r.success, "|", r.content[:80])

# 8. Alias normalization test
tr.register(name="test_alias", description="alias test",
            parameters={"type": "object",
                        "properties": {"file_path": {"type": "string"}, "frequency_hz": {"type": "number"}},
                        "required": ["file_path"]},
            handler=lambda a: ToolResult(True, f"path={a.get('file_path')} freq={a.get('frequency_hz')}"))
r = tr.call("test_alias", {"path": "/tmp/x", "freq": 100.0})
print("ALIAS:", r.success, "|", r.content)

print()
print("=== Stats ===")
print(json.dumps(tr.get_stats(), indent=2, ensure_ascii=False))
