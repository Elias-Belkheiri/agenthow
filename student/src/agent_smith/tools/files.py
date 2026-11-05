"""Direct, workspace-scoped file tools."""

from pathlib import Path

from .contracts import (
    MAX_FILE_BYTES, MAX_OUTPUT_CHARS, MAX_RESULTS,
    ToolContext, ToolError, ToolResult, failure, matching_files, physical_lines, validate_pattern,
)


def read_file(filepath: str, start_line: int, end_line: int, context: ToolContext) -> ToolResult:
    try:
        if (type(start_line) is not int or type(end_line) is not int
                or start_line < 1 or end_line < start_line):
            raise ToolError('invalid_input', 'expected positive inclusive line range')
        path = context.resolve(filepath)
        if not path.exists():
            raise ToolError('not_found', 'file does not exist')
        if not path.is_file():
            raise ToolError('invalid_input', 'path is not a file')
        with path.open('rb') as handle:
            data = handle.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise ToolError('too_large', 'file exceeds size limit')
        try:
            lines = physical_lines(data.decode('utf-8'))
        except UnicodeDecodeError as exc:
            raise ToolError('encoding_error', 'file is not UTF-8') from exc
        selected = lines[start_line - 1:end_line]
        output = []
        size = 0
        for number, line in enumerate(selected, start_line):
            entry = f'{number}: {line}'
            if len(output) == MAX_RESULTS or size + len(entry) + (1 if output else 0) > MAX_OUTPUT_CHARS:
                break
            output.append(entry)
            size += len(entry) + (1 if len(output) > 1 else 0)
        return ToolResult('\n'.join(output), truncated=len(output) < len(selected))
    except ToolError as exc:
        return failure(exc)
    except (OSError, ValueError) as exc:
        return failure(ToolError('io_error', f'cannot read file: {exc.strerror}' if isinstance(exc, OSError) else 'cannot read file'))


def list_files(directory: str, pattern: str, context: ToolContext) -> ToolResult:
    try:
        validate_pattern(pattern)
        path = context.resolve(directory)
        if not path.exists():
            raise ToolError('not_found', 'directory does not exist')
        if not path.is_dir():
            raise ToolError('invalid_input', 'path is not a directory')
        output = []
        size = 0
        truncated = False
        for candidate in matching_files(path, pattern):
            if candidate is None:
                truncated = True
                break
            # The walk does not follow symlinks; still check containment explicitly.
            resolved = context.resolve(candidate)
            entry = str(resolved)
            if len(output) == MAX_RESULTS or size + len(entry) + (1 if output else 0) > MAX_OUTPUT_CHARS:
                truncated = True
                break
            output.append(entry)
            size += len(entry) + (1 if len(output) > 1 else 0)
        return ToolResult('\n'.join(output), truncated=truncated)
    except ToolError as exc:
        return failure(exc)
    except OSError:
        return failure(ToolError('io_error', 'cannot list directory'))
