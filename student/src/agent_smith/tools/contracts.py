"""Tool path boundary for a trusted, non-concurrently-mutated workspace.

Resolve/check is not a sandbox: concurrent symlink swaps require a separate
OS-specific descriptor-relative design before exposing adversarial writes.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator
from fnmatch import fnmatchcase
import os

MAX_RESULTS = 200
MAX_OUTPUT_CHARS = 64 * 1024
MAX_FILE_BYTES = 1024 * 1024
MAX_SCANNED_FILES = 10000
MAX_SEARCH_BYTES = 8 * 1024 * 1024
MAX_MATCH_LINE_CHARS = 800


class ToolError(Exception):
    def __init__(self, code: str, detail: str):
        self.code = code
        super().__init__(f'{code}: {detail}')


@dataclass(frozen=True)
class ToolResult:
    content: str
    error: str | None = None
    truncated: bool = False


def failure(error: ToolError) -> ToolResult:
    return ToolResult(str(error), error=error.code)


@dataclass(frozen=True)
class ToolContext:
    workspace_root: Path

    def __post_init__(self) -> None:
        try:
            root = Path(self.workspace_root).resolve(strict=True)
            if not root.is_dir():
                raise ToolError('invalid_input', 'workspace root is not a directory')
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise ToolError('invalid_input', 'workspace root is unavailable') from exc
        object.__setattr__(self, 'workspace_root', root)

    def resolve(self, path: str | Path) -> Path:
        if not isinstance(path, (str, Path)) or not str(path) or '\x00' in str(path):
            raise ToolError('invalid_input', 'expected a nonempty path')
        candidate = Path(path)
        if '..' in candidate.parts:
            raise ToolError('path_denied', 'parent traversal is not allowed')
        if not candidate.is_absolute():
            candidate = self.workspace_root / candidate
        try:
            resolved = candidate.resolve(strict=False)
            resolved.relative_to(self.workspace_root)
        except (ValueError, OSError, RuntimeError) as exc:
            raise ToolError('path_denied', 'path is outside workspace or cannot be resolved') from exc
        return resolved


def validate_pattern(pattern: str) -> None:
    if (not isinstance(pattern, str) or not pattern or '\x00' in pattern
            or Path(pattern).is_absolute() or '..' in Path(pattern).parts
            or len(pattern) > 256 or pattern.count('[') != pattern.count(']')):
        raise ToolError('invalid_input', 'invalid workspace glob pattern')


def physical_lines(text: str) -> list[str]:
    lines = text.split('\n')
    result = [line[:-1] if line.endswith('\r') else line for line in lines[:-1]]
    if lines[-1]:
        result.append(lines[-1])
    return result


def _glob_match(parts: tuple[str, ...], glob: tuple[str, ...]) -> bool:
    # Each row records which glob suffixes match the already-consumed path suffix.
    previous = [False] * (len(glob) + 1)
    previous[-1] = True
    for index in range(len(glob) - 1, -1, -1):
        if glob[index] == '**':
            previous[index] = previous[index + 1]
    for part in reversed(parts):
        current = [False] * (len(glob) + 1)
        for index in range(len(glob) - 1, -1, -1):
            if glob[index] == '**':
                current[index] = current[index + 1] or previous[index]
            else:
                current[index] = fnmatchcase(part, glob[index]) and previous[index + 1]
        previous = current
    return previous[0]


def matching_files(directory: Path, pattern: str) -> Iterator[Path | None]:
    """Walk sorted names without following symlinks; None signals a scan cap."""
    validate_pattern(pattern)
    glob = tuple(Path(pattern).parts)
    scanned = 0
    def on_error(error: OSError) -> None:
        raise ToolError('io_error', 'cannot traverse workspace') from error

    for base, dirs, files in os.walk(directory, followlinks=False, onerror=on_error):
        dirs[:] = sorted(d for d in dirs if not (Path(base) / d).is_symlink())
        scanned += len(dirs)
        if scanned > MAX_SCANNED_FILES:
            yield None
            return
        for name in sorted(files):
            scanned += 1
            if scanned > MAX_SCANNED_FILES:
                yield None
                return
            candidate = Path(base) / name
            if not candidate.is_symlink() and _glob_match(candidate.relative_to(directory).parts, glob):
                yield candidate
