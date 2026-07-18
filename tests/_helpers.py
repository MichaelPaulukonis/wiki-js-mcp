def tool_fn(tool):
    """Return the underlying coroutine function of an @mcp.tool()-decorated
    object, whether fastmcp wrapped it (a FunctionTool with a .fn attribute,
    fastmcp 2.x) or returned the plain function directly (fastmcp 3.x)."""
    return getattr(tool, "fn", tool)
