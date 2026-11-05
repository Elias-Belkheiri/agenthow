"""MBPP test observation contract; only an injected executor can run tests.

A real executor must wait for the reviewed sandbox. This tool does not execute
candidate code or test expressions itself.
"""

from dataclasses import dataclass
from typing import Protocol

from .contracts import MAX_OUTPUT_CHARS, MAX_RESULTS


@dataclass(frozen=True)
class TestRunResult:
    passed: int
    failed: int
    stdout: str = ''
    stderr: str = ''
    failure_details: tuple[str, ...] = ()
    timed_out: bool = False
    truncated: bool = False


class TestExecutor(Protocol):
    def __call__(
        self, candidate_code: str, test_imports: list[str], test_list: list[str],
    ) -> TestRunResult: ...


def run_tests(
    candidate_code: str, test_imports: list[str], test_list: list[str],
    executor: TestExecutor,
) -> TestRunResult:
    """Forward exact MBPP inputs and bound the executor's reported output."""
    result = executor(candidate_code, test_imports, test_list)
    remaining = MAX_OUTPUT_CHARS
    truncated = result.truncated

    def take(text: str) -> str:
        nonlocal remaining, truncated
        portion = text[:remaining]
        remaining -= len(portion)
        truncated |= len(portion) < len(text)
        return portion

    stdout = take(result.stdout)
    stderr = take(result.stderr)
    details = []
    for detail in result.failure_details:
        if remaining == 0 or len(details) == MAX_RESULTS:
            truncated = True
            break
        details.append(take(detail))
    return TestRunResult(
        passed=result.passed, failed=result.failed, stdout=stdout, stderr=stderr,
        failure_details=tuple(details), timed_out=result.timed_out, truncated=truncated,
    )
