"""Trusted-parent, backend-neutral bounded process contract (no model overrides)."""

from dataclasses import dataclass, field
import math
from threading import Event
from typing import Literal, Protocol


@dataclass(frozen=True)
class ProcessLimits:
    timeout: float = 10.0
    stdin_bytes: int = 1024 * 1024
    stdout_bytes: int = 1024 * 1024
    stderr_bytes: int = 1024 * 1024

    def __post_init__(self) -> None:
        if type(self.timeout) not in (int, float):
            raise TypeError('timeout must be a finite number')
        if not 0 < self.timeout <= 300 or not math.isfinite(self.timeout):
            raise ValueError('timeout must be finite and in (0, 300]')
        for name in ('stdin_bytes', 'stdout_bytes', 'stderr_bytes'):
            value = getattr(self, name)
            if type(value) is not int:
                raise TypeError(f'{name} must be an integer')
            if not 0 <= value <= 16 * 1024 * 1024:
                raise ValueError(f'{name} outside supported byte bounds')


@dataclass(frozen=True)
class ProcessRequest:
    argv: tuple[str, ...]
    input: bytes = b''
    limits: ProcessLimits = field(default_factory=ProcessLimits)

    def __post_init__(self) -> None:
        if type(self.argv) is not tuple or not self.argv or len(self.argv) > 256:
            raise ValueError('argv must be a nonempty tuple of at most 256 strings')
        size = 0
        for arg in self.argv:
            if type(arg) is not str:
                raise TypeError('argv entries must be strings')
            if '\x00' in arg:
                raise ValueError('NUL in argv')
            size += len(arg.encode('utf-8')) + 1
        if not self.argv[0] or size > 65536:
            raise ValueError('empty executable or oversized argv')
        if type(self.limits) is not ProcessLimits or type(self.input) is not bytes:
            raise TypeError('expected ProcessLimits and bytes input')
        if len(self.input) > self.limits.stdin_bytes:
            raise ValueError('input exceeds stdin byte limit')


ProcessStatus = Literal['exited', 'timeout', 'cancelled', 'output_limit', 'error']


@dataclass(frozen=True)
class ProcessResult:
    status: ProcessStatus
    returncode: int | None
    stdout: bytes = b''
    stderr: bytes = b''
    truncated: bool = False
    error: str | None = None

    @property
    def stdout_text(self) -> str:
        return self.stdout.decode('utf-8', errors='replace')

    @property
    def stderr_text(self) -> str:
        return self.stderr.decode('utf-8', errors='replace')


class ProcessBackend(Protocol):
    def execute(self, request: ProcessRequest, *, cancel: Event | None = None) -> ProcessResult:
        """Run with trusted configured authority; setup failure raises, never falls back."""
        ...
