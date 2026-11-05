from agent_smith.tools.contracts import MAX_OUTPUT_CHARS, MAX_RESULTS
from agent_smith.tools.mbpp import TestRunResult as Observation, run_tests


class FakeExecutor:
    def __init__(self, result: Observation):
        self.result = result
        self.calls = []

    def __call__(self, candidate_code, test_imports, test_list):
        self.calls.append((candidate_code, test_imports, test_list))
        return self.result


def test_run_tests_forwards_exact_inputs_and_reports_executor_passes_and_failures():
    code = 'def solve(x): return x + 1\n'
    imports = ['import math', 'from fractions import Fraction']
    tests = ['assert solve(1) == 2', 'assert solve(2) == 3', 'assert solve(3) == 5']
    observation = Observation(
        passed=2, failed=1, failure_details=('assert solve(3) == 5: expected 5, got 4',),
        stdout='two passed', stderr='assertion failure',
    )
    executor = FakeExecutor(observation)

    result = run_tests(code, imports, tests, executor)

    assert len(executor.calls) == 1
    assert executor.calls[0][0] is code
    assert executor.calls[0][1] is imports
    assert executor.calls[0][2] is tests
    assert result == observation
    assert result.failed == 1 and result.passed == 2
    assert not result.timed_out


def test_run_tests_reports_fake_timeout_without_claiming_success():
    executor = FakeExecutor(Observation(
        passed=0, failed=0, timed_out=True, stderr='test execution timed out',
    ))

    result = run_tests('untrusted candidate', [], ['assert False'], executor)

    assert executor.calls == [('untrusted candidate', [], ['assert False'])]
    assert result.timed_out and result.passed == 0 and result.failed == 0
    assert result.stderr == 'test execution timed out'


def test_run_tests_bounds_all_output_including_failure_details():
    observation = Observation(
        passed=0, failed=1, stdout='o' * MAX_OUTPUT_CHARS,
        stderr='e' * MAX_OUTPUT_CHARS,
        failure_details=('f' * MAX_OUTPUT_CHARS, 'second failure'),
    )

    result = run_tests('code', [], [], FakeExecutor(observation))

    assert result.passed == 0 and result.failed == 1
    assert result.truncated
    assert sum(map(len, (result.stdout, result.stderr, *result.failure_details))) <= MAX_OUTPUT_CHARS
    assert result.stdout == 'o' * MAX_OUTPUT_CHARS
    assert result.stderr == '' and result.failure_details == ()


def test_run_tests_bounds_empty_failure_details_by_count():
    observation = Observation(passed=0, failed=MAX_RESULTS + 1,
                              failure_details=('',) * (MAX_RESULTS + 1))
    result = run_tests('code', [], [], FakeExecutor(observation))
    assert result.failure_details == ('',) * MAX_RESULTS
    assert result.truncated


def test_run_tests_bounds_mixed_failure_details_by_count():
    details = ('first',) + ('',) * (MAX_RESULTS - 2) + ('last', 'overflow')
    result = run_tests('code', [], [], FakeExecutor(Observation(
        passed=0, failed=MAX_RESULTS + 1, failure_details=details,
    )))
    assert result.failure_details == details[:MAX_RESULTS]
    assert result.truncated


def test_run_tests_bounds_failure_details_when_stdout_is_short():
    observation = Observation(
        passed=1, failed=2, stdout='ok', stderr='error',
        failure_details=('f' * MAX_OUTPUT_CHARS, 'another failure'),
    )

    result = run_tests('code', [], [], FakeExecutor(observation))

    assert result.truncated
    assert result.stdout == 'ok' and result.stderr == 'error'
    assert result.failure_details == ('f' * (MAX_OUTPUT_CHARS - len('okerror')),)
    assert result.passed == 1 and result.failed == 2
