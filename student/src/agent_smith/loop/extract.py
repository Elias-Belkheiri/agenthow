"""Extract one Python fenced action, never an alleged model observation.

XML, JSON/Hermes, and ReAct tool-call conversion is deferred. This module
only parses text; it must not execute generated Python or claim execution results.
"""

import ast
import re
from dataclasses import dataclass
from typing import Literal


Status = Literal['action', 'no_code', 'malformed', 'ambiguous']
_FENCE = re.compile(r'^[ \t]*```(?P<label>[^`\r\n]*)$')
MAX_ACTION_CHARS = 20_000


@dataclass(frozen=True)
class ExtractedAction:
    status: Status
    code: str | None
    feedback: str | None


def extract_action(response: str) -> ExtractedAction:
    """Return exactly one syntactically valid Python fenced action, or feedback.

    Thought and Observation prose are not actions; observations must come from
    the future sandbox, not from this model response. Malformed fences are
    rejected instead of guessing which text was intended as executable code.
    """
    blocks: list[tuple[str, str]] = []
    label: str | None = None
    lines: list[str] = []
    for line in response.splitlines(keepends=True):
        fence = _FENCE.fullmatch(line.rstrip('\r\n'))
        if label is None:
            if fence:
                label = fence.group('label').strip()
                lines = []
        elif fence and not fence.group('label').strip():
            blocks.append((label, ''.join(lines)))
            label = None
        else:
            lines.append(line)

    if label is not None:
        return ExtractedAction('malformed', None, 'Unclosed fenced block; provide one complete Python fenced code block.')
    if len(blocks) > 1:
        return ExtractedAction('ambiguous', None, 'Multiple fenced blocks; provide exactly one Python code action.')
    if not blocks:
        return ExtractedAction('no_code', None, 'No valid Python fenced code block found; provide one ```python block.')

    language, code = blocks[0]
    if language != 'python':
        if language.startswith('python'):
            return ExtractedAction('malformed', None, 'Malformed Python fence label; use ```python on its own line.')
        return ExtractedAction('no_code', None, 'No valid Python fenced code block found; XML, JSON/Hermes, ReAct, and unlabelled fences are not supported.')
    if not code.strip():
        return ExtractedAction('malformed', None, 'Empty Python fenced code block; provide one code action.')
    if len(code) > MAX_ACTION_CHARS:
        return ExtractedAction('malformed', None, 'Malformed Python code: fenced action is too large.')
    try:
        tree = ast.parse(code)
        if not tree.body:
            return ExtractedAction('malformed', None, 'Python fenced block contains no code action.')
    except (SyntaxError, ValueError, RecursionError) as error:
        return ExtractedAction('malformed', None, f'Malformed Python code: {error}')
    # Context-only errors (e.g. a top-level return) await sandbox validation.
    return ExtractedAction('action', code, None)
