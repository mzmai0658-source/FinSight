"""作品说明：财务 Agent 的工具路由模块。"""

from collections.abc import Callable, Mapping
from typing import Any, Dict, List, Tuple

from loguru import logger
from src.api.observability import metrics

ToolHandler = Callable[[Dict[str, Any], Any], Tuple[List[Any], Dict[str, Any]]]
EventFactory = Callable[[str, Dict[str, Any]], Any]


class ToolRouter:
    """作品说明：只分派已登记处理器，统一规范失败结果。"""

    def __init__(self, handlers: Mapping[str, ToolHandler], event_factory: EventFactory):
        self._handlers = dict(handlers)
        self._event = event_factory

    @property
    def tool_names(self) -> List[str]:
        return sorted(self._handlers.keys())

    def dispatch(self, name: str, args: Dict[str, Any], state: Any) -> Tuple[List[Any], Dict[str, Any]]:
        handler = self._handlers.get(name)
        if handler is None:
            metrics.record_agent_tool(name, "error")
            metrics.record_agent_tool_error(name)
            return [
                self._event("tool_result", {
                    "tool": name,
                    "status": "error",
                    "summary": f"Unknown tool: {name}",
                })
            ], {"status": "error", "message": f"Unknown tool: {name}"}

        try:
            return handler(args, state)
        except Exception as exc:
            logger.warning(f"Tool {name} failed: {exc}")
            metrics.record_agent_tool(name, "error")
            metrics.record_agent_tool_error(name)
            if hasattr(state, "tools_used"):
                state.tools_used = True
            return [
                self._event("tool_call", {"tool": name, "label": f"Call {name}", "detail": ""}),
                self._event("tool_result", {
                    "tool": name,
                    "status": "error",
                    "summary": f"Execution failed: {exc}"[:120],
                }),
            ], {
                "status": "error",
                "message": f"Tool execution failed: {exc}. Please correct the arguments and retry.",
            }
