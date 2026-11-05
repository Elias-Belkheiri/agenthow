"""Trusted terminal-only, one-command approval; no execution or policy grants."""

import json
from pathlib import Path
import sys
from typing import TextIO


class TerminalApproval:
    """Callable ``(exact_command: str, canonical_cwd: Path) -> bool``.

    Both streams must be TTYs. Defaults bind at construction, not import time.
    Only case-insensitive ASCII ``yes`` approves, with surrounding ASCII space,
    tab or CR permitted and a required terminating LF (including CRLF).
    Reads at most 129 characters; lines over 128 characters including LF deny.
    Incomplete/oversized input disables this instance rather than allowing an
    unread tail to approve a later command. No draining, caching or approve-all.

    The dispatcher supplies canonical cwd. Validation here is lexical only:
    no filesystem resolution, admission checks, or containment guarantees.
    N3 must install this callback only for the interactive REPL, never --task.
    """

    def __init__(self, input_stream: TextIO | None = None,
                 output_stream: TextIO | None = None) -> None:
        self._input = sys.stdin if input_stream is None else input_stream
        self._output = sys.stderr if output_stream is None else output_stream
        self._incomplete_input = False

    def __call__(self, command: str, cwd: Path) -> bool:
        try:
            if (self._incomplete_input or type(command) is not str
                    or not command or '\x00' in command
                    or len(command.encode('utf-8')) > 8192
                    or type(cwd) is not type(Path())
                    or not cwd.is_absolute() or '..' in cwd.parts
                    or '\x00' in str(cwd)):
                return False
            if (self._input.closed or self._output.closed
                    or self._input.isatty() is not True
                    or self._output.isatty() is not True):
                return False
            # JSON string boundaries and ASCII escapes prevent terminal controls
            # and embedded newlines in either model text or workspace spelling.
            prompt = (
                f'Command: {json.dumps(command, ensure_ascii=True)}\n'
                f'Workspace: {json.dumps(str(cwd), ensure_ascii=True)}\n'
                'Approve THIS command once? Type yes to approve [default: no]: '
            )
            if self._output.write(prompt) != len(prompt):
                return False
            self._output.flush()
            answer = self._input.readline(129)
            if type(answer) is not str or len(answer) > 128 or not answer.endswith('\n'):
                self._incomplete_input = True
                return False
            return answer[:-1].strip(' \t\r').lower() == 'yes'
        except (Exception, KeyboardInterrupt):
            # A failed read may have consumed only part of a line. Never reuse
            # the remaining bytes as a fresh decision after any stream failure.
            self._incomplete_input = True
            return False
