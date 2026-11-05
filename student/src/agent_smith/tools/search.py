"""Deterministic, bounded grep-like workspace search (without a shell)."""

from .contracts import (
    MAX_FILE_BYTES, MAX_MATCH_LINE_CHARS, MAX_OUTPUT_CHARS, MAX_RESULTS,
    MAX_SEARCH_BYTES, ToolContext, ToolError, ToolResult,
    failure, matching_files, physical_lines, validate_pattern,
)


def search_code(pattern: str, file_pattern: str, context: ToolContext) -> ToolResult:
    try:
        if not isinstance(pattern, str) or not pattern or len(pattern) > 512:
            raise ToolError('invalid_input', 'expected a nonempty search pattern of at most 512 characters')
        validate_pattern(file_pattern)
        # Literal grep-like search: regex evaluation on untrusted inputs has no
        # safe timeout in the standard library and can stall the caller.
        output = []
        size = 0
        bytes_scanned = 0
        truncated = False
        for candidate in matching_files(context.workspace_root, file_pattern):
            if candidate is None or bytes_scanned >= MAX_SEARCH_BYTES:
                truncated = True
                break
            path = context.resolve(candidate)
            if not path.is_file():
                continue
            try:
                with path.open('rb') as handle:
                    data = handle.read(min(MAX_FILE_BYTES + 1, MAX_SEARCH_BYTES - bytes_scanned + 1))
            except OSError:
                raise ToolError('io_error', 'cannot read search file')
            remaining = MAX_SEARCH_BYTES - bytes_scanned
            bytes_scanned += len(data)
            if len(data) > MAX_FILE_BYTES or len(data) > remaining or b'\x00' in data:
                truncated |= len(data) > MAX_FILE_BYTES or len(data) > remaining
                continue
            try:
                lines = physical_lines(data.decode('utf-8'))
            except UnicodeDecodeError:
                continue  # Non-UTF-8 content is not searchable text.
            for number, line in enumerate(lines, 1):
                if pattern in line:
                    prefix = f'{path}:{number} '
                    allowed = min(MAX_MATCH_LINE_CHARS, MAX_OUTPUT_CHARS - size - len(prefix) - 1)
                    if len(output) == MAX_RESULTS or allowed < 0:
                        truncated = True
                        break
                    entry = prefix + line[:allowed]
                    truncated |= len(line) > allowed
                    output.append(entry)
                    size += len(entry) + 1
            if len(output) == MAX_RESULTS or size >= MAX_OUTPUT_CHARS:
                truncated = True
                break
        return ToolResult('\n'.join(output), truncated=truncated)
    except ToolError as exc:
        return failure(exc)
    except OSError:
        return failure(ToolError('io_error', 'cannot search workspace'))
