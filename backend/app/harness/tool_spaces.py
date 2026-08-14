"""工具空间(tool space)分类。

**这里只剩分类,不再有"渐进式披露"。** 曾经的 `assemble_tool_spaces` / `*_tools` 加载器 /
`promoted_tool_names` 提升机制已在 2026-08-14 整体删除,原因见 `HARNESS_NOTES.md` §59:

- provider 的可缓存前缀是 `tools → system → messages`。加载器一旦把某个工具提升进
  `promoted_tool_names`,下一步 `bind_tools` 的工具数组就变了,**整段前缀缓存从第一个 token
  起全部作废**。省下来的那点 schema token,换来的是每次激活赔一次全量缓存。
- 加载器为了让模型知道该激活什么,又把整个空间的工具目录内联进了自己的 description ——
  MCP 一多,"省下的 schema"以 description 的形式原样回到前缀里,净收益进一步变负。

现在的做法是**工具集全程恒定**:日常工具直接绑定;重工具空间(浏览器自动化)整体交给
`browser` 垂类子代理承载;数量发散的 MCP 收敛成 `mcp_docs` / `mcp_call` 两个固定代理。
分类函数本身仍然有用——子代理的工具面就是按空间切分的(见
`app.harness.subagents.tools.filter_tools_for_spaces`)。
"""

from __future__ import annotations

from langchain_core.tools import BaseTool

TOOL_SPACE_ORDER = ("workspace", "sandbox", "browser", "network", "documents", "extensions", "memory")


def tool_space_for_name(name: str) -> str | None:
    """按工具名前缀判断它属于哪个工具空间;不属于任何空间返回 `None`。"""

    if name.startswith(("workspace_", "artifact_", "context_archive_")):
        return "workspace"
    if name.startswith(("sandbox_", "docker_")):
        return "sandbox"
    if name.startswith("browser_"):
        return "browser"
    if name.startswith(("web_", "agent_reach_")):
        return "network"
    if name.startswith(("convert_", "markitdown_", "view_image")):
        return "documents"
    if name.startswith(("skill_", "find_skills", "find-skills", "search_skill", "mcp_")):
        return "extensions"
    if name.startswith("memory_"):
        return "memory"
    return None


def tool_space_for_tool(tool: BaseTool) -> str | None:
    """按工具对象判断空间;MCP 工具额外看 loader 打上的 server 标签。"""

    space = tool_space_for_name(tool.name)
    if space is not None:
        return space
    metadata = tool.metadata if isinstance(tool.metadata, dict) else {}
    if any(key in metadata for key in ("slotflow_mcp_server", "server_name", "mcp_server")):
        return "extensions"
    return None
