"""Staged stdlib-only tool worker. JSON is data, never executable authority."""

import json
import os
from pathlib import Path
import stat
import sys
import tempfile

from agent_smith.tools.contracts import (
    MAX_FILE_BYTES, ToolContext, ToolError, ToolResult, failure,
)
from agent_smith.tools.files import list_files, read_file
from agent_smith.tools.search import search_code

MAX_REQUEST_BYTES = 2 * 1024 * 1024
MAX_RESPONSE_BYTES = 512 * 1024
MAX_STRING_BYTES = MAX_FILE_BYTES
SCHEMAS = {
    'read_file': {'filepath': str, 'start_line': int, 'end_line': int},
    'list_files': {'directory': str, 'pattern': str},
    'search_code': {'pattern': str, 'file_pattern': str},
    'write_file': {'filepath': str, 'content': str},
    'edit_file': {'filepath': str, 'old_text': str, 'new_text': str},
    'run_shell': {'command': str},
}


def validate_arguments(name: object, arguments: object) -> str | None:
    if type(name) is not str or name not in SCHEMAS:
        return 'tool_not_allowed'
    schema = SCHEMAS[name]
    if type(arguments) is not dict or set(arguments) != set(schema):
        return 'invalid_arguments'
    for key, kind in schema.items():
        value = arguments[key]
        if type(value) is not kind:
            return 'invalid_arguments'
        if kind is str:
            try:
                size = len(value.encode('utf-8'))
            except UnicodeError:
                return 'invalid_arguments'
            cap = MAX_STRING_BYTES if key in ('content', 'old_text', 'new_text') else 8192
            if size > cap or '\x00' in value:
                return 'invalid_arguments'
            if not value and key not in ('content', 'new_text'):
                return 'invalid_arguments'
        elif not 1 <= value <= 2**31 - 1:
            return 'invalid_arguments'
    if name == 'read_file' and arguments['end_line'] < arguments['start_line']:
        return 'invalid_arguments'
    return None


def result_dict(result: ToolResult) -> dict:
    return {'ok': result.error is None, 'content': result.content,
            'error': result.error, 'truncated': result.truncated}


def _write(arguments: dict, context: ToolContext, *, edit: bool) -> ToolResult:
    temporary = None
    try:
        # Reject symlinks in the supplied spelling, including internal aliases.
        supplied = Path(arguments['filepath'])
        if not supplied.is_absolute():
            supplied = context.workspace_root / supplied
        path = context.resolve(arguments['filepath'])
        for node in (supplied, *supplied.parents):
            if node == context.workspace_root:
                break
            if node.is_symlink():
                raise ToolError('path_denied', 'symlink write target')
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            metadata = None
        if metadata is not None and not stat.S_ISREG(metadata.st_mode):
            raise ToolError('invalid_input', 'write target is not a regular file')
        mode = stat.S_IMODE(metadata.st_mode) if metadata else 0o600
        if edit:
            if metadata is None:
                raise ToolError('not_found', 'file does not exist')
            # O_NONBLOCK prevents FIFO blocking if an unsupported concurrent host
            # mutation occurs. Seatbelt remains the filesystem boundary.
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, 'rb') as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ToolError('invalid_input', 'edit target is not a regular file')
                data = stream.read(MAX_FILE_BYTES + 1)
            if len(data) > MAX_FILE_BYTES:
                raise ToolError('too_large', 'file exceeds size limit')
            try:
                text = data.decode('utf-8')
            except UnicodeError as exc:
                raise ToolError('encoding_error', 'file is not UTF-8') from exc
            old = arguments['old_text']
            # Count overlapping matches too: ambiguity must never silently edit.
            first = text.find(old)
            if first < 0 or text.find(old, first + 1) >= 0:
                raise ToolError('edit_conflict', 'old_text must match exactly once')
            content = text[:first] + arguments['new_text'] + text[first + len(old):]
        else:
            content = arguments['content']
        data = content.encode('utf-8')
        if len(data) > MAX_FILE_BYTES:
            raise ToolError('too_large', 'result exceeds size limit')
        descriptor, temporary = tempfile.mkstemp(prefix='.agent-write-', dir=path.parent)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
            os.fchmod(stream.fileno(), mode)
        os.replace(temporary, path)
        temporary = None
        return ToolResult('file updated' if edit else 'file written')
    except ToolError as exc:
        return failure(exc)
    except (OSError, ValueError):
        return failure(ToolError('io_error', 'cannot write file'))
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def dispatch(name: str, arguments: dict, context: ToolContext) -> dict:
    error = validate_arguments(name, arguments)
    if error or name == 'run_shell':
        return {'ok': False, 'error': error or 'tool_not_allowed'}
    if name in ('write_file', 'edit_file'):
        return result_dict(_write(arguments, context, edit=name == 'edit_file'))
    handler = {'read_file': read_file, 'list_files': list_files, 'search_code': search_code}[name]
    return result_dict(handler(**arguments, context=context))


class ConfinedToolContext(ToolContext):
    """Backend cwd is canonical; ancestor metadata is intentionally not granted.

    Inherited strict=False resolution is defense-in-depth only, not an
    independent security boundary. Seatbelt enforces actual file operations.
    """

    def __post_init__(self) -> None:
        cwd = Path.cwd()
        if type(self.workspace_root) is not type(cwd) or self.workspace_root != cwd or not cwd.is_dir():
            raise ToolError('invalid_input', 'worker must start in its workspace')


def main() -> None:
    try:
        data = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        if len(data) > MAX_REQUEST_BYTES:
            raise ValueError('request too large')
        request = json.loads(data.decode('utf-8'))
        if type(request) is not dict or set(request) != {'name', 'arguments'}:
            raise ValueError('invalid request')
        result = dispatch(request['name'], request['arguments'], ConfinedToolContext(Path.cwd()))
        output = json.dumps(result, ensure_ascii=True).encode('utf-8')
        if len(output) > MAX_RESPONSE_BYTES:
            output = b'{"ok":false,"error":"output_limit"}'
    except (ValueError, TypeError, UnicodeError, ToolError):
        output = b'{"ok":false,"error":"invalid_request"}'
    sys.stdout.buffer.write(output)


if __name__ == '__main__':
    main()
