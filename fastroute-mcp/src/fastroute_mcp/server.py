from __future__ import annotations
import asyncio, logging, os
from pathlib import Path
from .config import load_settings
from .service.job_manager import JobManager
from .mcp.tools_board import register as reg_board
from .mcp.tools_routing import register as reg_routing
from .mcp.tools_diagnostics import register as reg_diag
from .mcp.tools_results import register as reg_results
from .mcp.resources import register as reg_resources
from .mcp.prompts import register as reg_prompts


def build_server():
    settings = load_settings()
    manager = JobManager(settings)
    from mcp.server.mcpserver import MCPServer
    mcp = MCPServer("fastroute-mcp", instructions="FastRoute MCP server")
    reg_board(mcp, manager)
    reg_routing(mcp, manager)
    reg_diag(mcp, manager)
    reg_results(mcp, manager)
    reg_resources(mcp, manager)
    reg_prompts(mcp, manager)
    return mcp, manager


def main():
    logging.basicConfig(level=os.environ.get("FASTROUTE_MCP_LOG_LEVEL", "INFO"))
    mcp, manager = build_server()
    asyncio.run(mcp.run_stdio_async())


if __name__ == "__main__":
    main()
