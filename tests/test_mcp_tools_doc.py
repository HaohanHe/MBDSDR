# -*- coding: utf-8 -*-
"""docs/mcp_tools.md 的自动生成块必须与实时 MCPToolSet 工具面一致。

抓的是「注册面已改、文档没重跑」这种漂移：只要工具名/必填参数/描述
有任何一处不同，比对就会红。本测试只读不写；
修法：``python scripts/gen_mcp_tools_doc.py``。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import gen_mcp_tools_doc as docmod


def test_generated_inventory_matches_live_tool_surface():
    tools = docmod.MCPToolSet().list_tools()
    assert tools, "MCPToolSet 返回空工具面，无法比对"
    delta = docmod.drift(tools)
    assert not delta, (
        "docs/mcp_tools.md 的生成块滞后于 tools/list（%d 行差异）；"
        "跑 python scripts/gen_mcp_tools_doc.py 重生成。首个差异：%s"
        % (len(delta), delta[1] if len(delta) > 1 else delta[0])
    )
