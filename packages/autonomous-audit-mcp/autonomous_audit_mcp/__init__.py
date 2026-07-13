"""autonomous-audit-mcp - an MCP server wrapping autonomous-audit's tamper-evident decision log.

Exposes ``record_decision`` / ``verify_log`` / ``export_report`` / ``chain_head`` as native,
agent-callable MCP tools. No new audit logic - it delegates to the ``autonomous-audit`` package.
"""

from .server import mcp, main

__version__ = "0.1.0"
__all__ = ["mcp", "main", "__version__"]
