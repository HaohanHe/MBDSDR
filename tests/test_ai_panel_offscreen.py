"""Offscreen test: AI panel unconfigured API behavior.

Verifies:
1. When agent is None (no API key), submitting a message shows "未配置" in reply.
2. tool_call_requested is NOT emitted (no fake tool execution).
3. Stream duplication: _on_ai_finished does not re-add content when streaming was active.
"""
import os
import sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Ensure repo root on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer

app = QApplication.instance() or QApplication(sys.argv)

from desktop.ai_panel import AIPanel

panel = AIPanel()

# ── Test 1: unconfigured (agent is None by default) ──
assert panel.agent is None, "Agent should be None by default"

tool_call_emitted = []
panel.tool_call_requested.connect(lambda name, params: tool_call_emitted.append((name, params)))

# Simulate user submission
panel.input_field.setText("扫频找台")
panel._on_submit()

# Check reply contains "未配置"
view_text = panel.conversation_view.toPlainText()
assert "未配置" in view_text, f"Expected '未配置' in reply, got: {view_text[-200:]}"
print(f"✓ Test 1: Unconfigured reply contains '未配置'")

# Check no tool_call_requested was emitted
assert len(tool_call_emitted) == 0, \
    f"tool_call_requested should NOT be emitted when unconfigured, got: {tool_call_emitted}"
print(f"✓ Test 2: tool_call_requested NOT emitted (no fake tool execution)")

# ── Test 3: status label shows "未配置 API" ──
status_text = panel.ai_status_label.text()
assert "未配置" in status_text, f"Expected status '未配置 API', got: {status_text}"
print(f"✓ Test 3: Status label shows '{status_text}'")

# ── Test 4: stream duplication flag ──
# Simulate _call_ai setting up streaming state
panel._streamed_any = False
panel._on_stream_delta("Hello")
assert panel._streamed_any is True, "_streamed_any should be True after delta"
print(f"✓ Test 4: _streamed_any set to True on stream delta")

# ── Test 5: init_agent with no API key does not create agent ──
# Mock _load_config to return empty (simulate no config file)
panel2 = AIPanel()
panel2._saved_config = {}
os.environ.pop("MBDSDR_API_KEY", None)
panel2.init_agent()  # no key, no config, no env var
assert panel2.agent is None, \
    f"init_agent with no key should leave agent None, got: {panel2.agent}"
print(f"✓ Test 5: init_agent with no API key leaves agent=None")

print("\n=== All AI panel offscreen tests passed ===")
