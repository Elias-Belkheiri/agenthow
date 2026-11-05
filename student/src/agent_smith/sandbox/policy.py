"""Sandbox policy: whitelisted modules, allowed directories, and resource limits."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SandboxPolicy:
    """Immutable sandbox configuration.

    Attributes:
        allowed_modules: Module names that user code may import.
        allowed_directories: Absolute paths where ``open()`` is permitted.
            Empty tuple means no file access.
        max_memory_bytes: RLIMIT_AS ceiling; 0 disables the limit.
        max_cpu_seconds: RLIMIT_CPU ceiling; 0 disables the limit.
        max_output_bytes: Truncate captured stdout/stderr beyond this size.
    """

    allowed_modules: frozenset[str]
    allowed_directories: tuple[str, ...] = ()
    max_memory_bytes: int = 0
    max_cpu_seconds: int = 0
    max_output_bytes: int = 65_536


# ---------------------------------------------------------------------------
# MBPP default policy: pure computation, no I/O, no network, no processes.
# ---------------------------------------------------------------------------

MBPP_ALLOWED_MODULES: frozenset[str] = frozenset({
    # Math and numbers
    'math', 'cmath', 'decimal', 'fractions', 'statistics', 'numbers',
    # Data structures
    'collections', 'heapq', 'bisect', 'array', 'queue',
    # Iteration, functions, operators
    'itertools', 'functools', 'operator',
    # Strings and text
    'string', 're',
    # Types and protocols
    'copy', 'types', 'typing', 'dataclasses', 'abc', 'enum',
    # Date/time
    'datetime', 'calendar',
    # I/O helpers (StringIO/BytesIO)
    'io',
    # Serialization (read-only in practice)
    'json',
    # Hashing (pure computation)
    'hashlib',
    # Random (seedable, deterministic)
    'random',
    # Text helpers
    'textwrap',
    # Context managers, warnings, struct
    'contextlib', 'warnings', 'struct',
})

MBPP_POLICY = SandboxPolicy(
    allowed_modules=MBPP_ALLOWED_MODULES,
    allowed_directories=(),
    max_memory_bytes=256 * 1024 * 1024,   # 256 MB
    max_cpu_seconds=30,
    max_output_bytes=65_536,              # 64 KB
)
