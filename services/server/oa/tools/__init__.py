"""Tool layer (ARCHITECTURE §6.5). Importing this package registers every tool in REGISTRY."""
from oa.tools import data, editor, media, plan, research, terminal, web  # noqa: F401  (registration side effects)
from oa.tools.base import REGISTRY, ToolContext, ToolResult, execute, schemas

__all__ = ["REGISTRY", "ToolContext", "ToolResult", "execute", "schemas"]
