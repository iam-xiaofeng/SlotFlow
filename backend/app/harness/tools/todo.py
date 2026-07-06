"""Todo tool exposure for the harness graph.

Exposes the official ``write_todos`` tool (returns ``Command`` updating ``todos`` + a
``ToolMessage``; ToolNode + the ``todos`` state channel handle the rest). Kept separate from
``harness/steps/todo.py`` so the tool registry can import only the tool without pulling the
step functions.
"""

from __future__ import annotations

from app.harness.steps.todo import write_todos_tool

__all__ = ["write_todos_tool"]
