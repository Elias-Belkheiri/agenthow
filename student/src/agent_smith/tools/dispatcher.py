"""Allowlisted dispatcher for read-only workspace tools."""

import logging
from typing import Any, Protocol

from .contracts import ToolContext, ToolResult
from .files import list_files, read_file
from .search import search_code


READ_ONLY_TOOL_GUIDANCE = '''Respond with exactly one JSON object per turn. Call only these read-only tools,
using the exact argument keys shown:
- read_file: {"tool":"read_file","arguments":{"filepath":"relative/path","start_line":1,"end_line":10}}
- list_files: {"tool":"list_files","arguments":{"directory":".","pattern":"*.py"}}
- search_code: {"tool":"search_code","arguments":{"pattern":"text","file_pattern":"*.py"}}
To finish use {"final":"your answer"}. Only read-only tools are available.
Never claim a tool result before it is returned as an observation.'''


class ChatToolDispatcher(Protocol):
    system_guidance: str

    def call(self, name: str, arguments: dict[str, Any], *,
             timeout_seconds: float) -> dict[str, Any]: ...


logger = logging.getLogger(__name__)


class ReadOnlyToolDispatcher:
    """Dispatch only bounded read-only tools against one trusted workspace."""

    system_guidance = READ_ONLY_TOOL_GUIDANCE

    def __init__(self, context: ToolContext):
        self.context = context

    def call(self, name: str, arguments: dict[str, Any], *, timeout_seconds: float = 10.0) -> dict[str, Any]:
        handlers = {
            'read_file': ({'filepath', 'start_line', 'end_line'}, read_file),
            'list_files': ({'directory', 'pattern'}, list_files),
            'search_code': ({'pattern', 'file_pattern'}, search_code),
        }
        entry = handlers.get(name)
        if entry is None:
            logger.info('tool_call_rejected', extra={'tool_name': name, 'reason': 'not_allowed'})
            return {'ok': False, 'error': 'tool_not_allowed'}
        keys, handler = entry
        if not isinstance(arguments, dict) or set(arguments) != keys:
            logger.info('tool_call_rejected', extra={'tool_name': name, 'reason': 'invalid_arguments'})
            return {'ok': False, 'error': 'invalid_arguments'}
        try:
            result: ToolResult = handler(**arguments, context=self.context)
        except (TypeError, ValueError):
            logger.info('tool_call_rejected', extra={'tool_name': name, 'reason': 'invalid_arguments'})
            return {'ok': False, 'error': 'invalid_arguments'}
        logger.info('readonly_tool_completed', extra={
            'tool_name': name,
            'ok': result.error is None,
            'error_code': result.error,
            'truncated': result.truncated,
            'result_chars': len(result.content),
        })
        return {
            'ok': result.error is None,
            'content': result.content,
            'error': result.error,
            'truncated': result.truncated,
        }
