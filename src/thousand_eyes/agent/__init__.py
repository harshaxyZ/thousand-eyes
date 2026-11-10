"""Thousand Eyes v0.5 — the agentic self-audit layer (read-only, evidence-first)."""

from .analytics import Analytics
from .classify import classify, tools_for
from .llm import LLMClient
from .planner import FENCE, Planner
from .providers import client_for, discover_key, best_model
from .tools import Tool, ToolResult, all_tools, count_tools, tool_manifest

__all__ = ["Analytics", "Planner", "Tool", "ToolResult", "all_tools",
           "count_tools", "tool_manifest", "classify", "tools_for",
           "client_for", "discover_key", "best_model", "FENCE", "LLMClient"]
