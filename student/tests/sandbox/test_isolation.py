"""Sandbox isolation tests.

Verifies that the restricted sandbox correctly:
- Allows safe operations (math, strings, printing, data structures)
- Blocks dangerous imports (os, subprocess, socket, ctypes, …)
- Blocks dangerous builtins (exec, eval, compile, …)
- Restricts file access (open outside allowed dirs, symlinks, fd)
- Enforces time limits (infinite loops)
- Handles errors gracefully (SystemExit, KeyboardInterrupt, exceptions)
- Captures output and supports final_answer()
- Parent survives worker crashes
"""

import sys

import pytest

from agent_smith.sandbox.executor import SubprocessExecutor
from agent_smith.sandbox.policy import MBPP_POLICY, SandboxPolicy

TIMEOUT = 10.0


@pytest.fixture
def executor():
    return SubprocessExecutor(MBPP_POLICY)


@pytest.fixture
def executor_with_dir(tmp_path):
    policy = SandboxPolicy(
        allowed_modules=MBPP_POLICY.allowed_modules,
        allowed_directories=(str(tmp_path),),
        max_memory_bytes=MBPP_POLICY.max_memory_bytes,
        max_cpu_seconds=MBPP_POLICY.max_cpu_seconds,
        max_output_bytes=MBPP_POLICY.max_output_bytes,
    )
    return SubprocessExecutor(policy), tmp_path


# ===================================================================
# Allowed operations
# ===================================================================

def test_basic_print(executor):
    result = executor.execute('print(42)', timeout_seconds=TIMEOUT)
    assert '42' in result.observation
    assert result.error is None


def test_arithmetic(executor):
    result = executor.execute('print(2 ** 10)', timeout_seconds=TIMEOUT)
    assert '1024' in result.observation
    assert result.error is None


def test_math_module(executor):
    result = executor.execute(
        'import math\nprint(math.sqrt(16))', timeout_seconds=TIMEOUT,
    )
    assert '4.0' in result.observation
    assert result.error is None


def test_string_module(executor):
    result = executor.execute(
        'import string\nprint(string.ascii_lowercase)', timeout_seconds=TIMEOUT,
    )
    assert 'abcdefghijklmnopqrstuvwxyz' in result.observation
    assert result.error is None


def test_collections(executor):
    result = executor.execute(
        'from collections import Counter\nprint(Counter("abcab"))',
        timeout_seconds=TIMEOUT,
    )
    assert 'a' in result.observation
    assert result.error is None


def test_itertools(executor):
    result = executor.execute(
        'from itertools import permutations\n'
        'print(list(permutations([1,2,3])))',
        timeout_seconds=TIMEOUT,
    )
    assert '(1, 2, 3)' in result.observation
    assert result.error is None


def test_re_module(executor):
    result = executor.execute(
        "import re\nprint(re.findall(r'\\\\d+', 'abc123def456'))",
        timeout_seconds=TIMEOUT,
    )
    assert '123' in result.observation
    assert result.error is None


def test_json_module(executor):
    result = executor.execute(
        "import json\nprint(json.dumps({'a': 1}))",
        timeout_seconds=TIMEOUT,
    )
    assert '"a"' in result.observation
    assert result.error is None


def test_list_comprehension(executor):
    result = executor.execute(
        'print([x**2 for x in range(5)])', timeout_seconds=TIMEOUT,
    )
    assert '[0, 1, 4, 9, 16]' in result.observation
    assert result.error is None


def test_class_definition(executor):
    code = (
        'class Foo:\n'
        '    def bar(self): return 42\n'
        'print(Foo().bar())\n'
    )
    result = executor.execute(code, timeout_seconds=TIMEOUT)
    assert '42' in result.observation
    assert result.error is None


def test_hashlib(executor):
    result = executor.execute(
        "import hashlib\n"
        "print(hashlib.sha256(b'hello').hexdigest()[:8])",
        timeout_seconds=TIMEOUT,
    )
    assert '2cf24dba' in result.observation
    assert result.error is None


def test_random(executor):
    result = executor.execute(
        'import random\nrandom.seed(0)\nprint(random.randint(1,100))',
        timeout_seconds=TIMEOUT,
    )
    assert result.error is None
    assert result.observation.strip().isdigit()


def test_functools_reduce(executor):
    result = executor.execute(
        'from functools import reduce\nprint(reduce(lambda a,b: a+b, range(1,6)))',
        timeout_seconds=TIMEOUT,
    )
    assert '15' in result.observation
    assert result.error is None


# ===================================================================
# final_answer
# ===================================================================

def test_final_answer_string(executor):
    result = executor.execute(
        "final_answer('def solve(x): return x*x')",
        timeout_seconds=TIMEOUT,
    )
    assert result.final_answer == 'def solve(x): return x*x'
    assert result.error is None


def test_final_answer_non_string(executor):
    result = executor.execute('final_answer(42)', timeout_seconds=TIMEOUT)
    assert result.final_answer == '42'
    assert result.error is None


def test_final_answer_last_wins(executor):
    result = executor.execute(
        "final_answer('first')\nfinal_answer('second')",
        timeout_seconds=TIMEOUT,
    )
    assert result.final_answer == 'second'


def test_final_answer_with_output(executor):
    result = executor.execute(
        "print('working...')\nfinal_answer('done')",
        timeout_seconds=TIMEOUT,
    )
    assert result.final_answer == 'done'
    assert 'working...' in result.observation
    assert result.error is None


# ===================================================================
# Blocked imports
# ===================================================================

@pytest.mark.parametrize('module', [
    'os', 'subprocess', 'socket', 'shutil', 'ctypes',
    'multiprocessing', 'threading', 'pickle', 'sys',
    'importlib', 'signal', 'code', 'time', 'tempfile',
    'pathlib', 'glob', 'http', 'urllib',
])
def test_blocked_import(executor, module):
    result = executor.execute(f'import {module}', timeout_seconds=TIMEOUT)
    assert result.error is not None
    assert 'not allowed' in result.observation.lower() or 'ImportError' in result.observation


def test_blocked_from_import(executor):
    result = executor.execute(
        'from os import system', timeout_seconds=TIMEOUT,
    )
    assert result.error is not None


def test_blocked_from_os_path(executor):
    result = executor.execute(
        'from os.path import join', timeout_seconds=TIMEOUT,
    )
    assert result.error is not None


def test_blocked_dunder_import(executor):
    result = executor.execute(
        "__import__('os')", timeout_seconds=TIMEOUT,
    )
    assert result.error is not None


# ===================================================================
# Blocked builtins
# ===================================================================

@pytest.mark.parametrize('builtin_name', [
    'exec', 'eval', 'compile', 'breakpoint', 'input',
])
def test_blocked_builtin(executor, builtin_name):
    result = executor.execute(
        f"{builtin_name}('1')", timeout_seconds=TIMEOUT,
    )
    assert result.error is not None


def test_blocked_exit(executor):
    result = executor.execute('exit()', timeout_seconds=TIMEOUT)
    assert result.error is not None


def test_blocked_quit(executor):
    result = executor.execute('quit()', timeout_seconds=TIMEOUT)
    assert result.error is not None


# ===================================================================
# File access restrictions
# ===================================================================

def test_open_blocked_without_allowed_dirs(executor):
    result = executor.execute(
        "open('/etc/passwd')", timeout_seconds=TIMEOUT,
    )
    assert result.error is not None
    assert 'not allowed' in result.observation.lower()


def test_open_blocked_fd(executor):
    result = executor.execute('open(3)', timeout_seconds=TIMEOUT)
    assert result.error is not None
    assert 'not allowed' in result.observation.lower()


def test_open_allowed_in_dir(executor_with_dir):
    exe, tmp = executor_with_dir
    test_file = tmp / 'test.txt'
    test_file.write_text('hello sandbox')
    result = exe.execute(
        f"print(open('{test_file}').read())", timeout_seconds=TIMEOUT,
    )
    assert 'hello sandbox' in result.observation
    assert result.error is None


def test_open_write_in_allowed_dir(executor_with_dir):
    exe, tmp = executor_with_dir
    out_file = tmp / 'out.txt'
    code = (
        f"with open('{out_file}', 'w') as f:\n"
        f"    f.write('written')\n"
        f"print('ok')\n"
    )
    result = exe.execute(code, timeout_seconds=TIMEOUT)
    assert result.error is None
    assert out_file.read_text() == 'written'


def test_open_blocked_outside_allowed_dir(executor_with_dir):
    exe, _ = executor_with_dir
    result = exe.execute(
        "open('/etc/passwd')", timeout_seconds=TIMEOUT,
    )
    assert result.error is not None
    assert 'outside' in result.observation.lower() or 'denied' in result.observation.lower()


def test_open_blocked_symlink_escape(executor_with_dir):
    exe, tmp = executor_with_dir
    link = tmp / 'escape'
    try:
        link.symlink_to('/etc/passwd')
    except OSError:
        pytest.skip('Cannot create symlinks')
    result = exe.execute(
        f"open('{link}').read()", timeout_seconds=TIMEOUT,
    )
    assert result.error is not None


def test_open_blocked_parent_traversal(executor_with_dir):
    exe, tmp = executor_with_dir
    result = exe.execute(
        f"open('{tmp}/../../../etc/passwd')", timeout_seconds=TIMEOUT,
    )
    assert result.error is not None


# ===================================================================
# Timeouts
# ===================================================================

def test_infinite_loop_killed(executor):
    result = executor.execute('while True: pass', timeout_seconds=3)
    assert result.error is not None
    assert 'timeout' in result.error.lower()


def test_zero_timeout(executor):
    result = executor.execute('print(1)', timeout_seconds=0)
    assert result.error == 'timeout'


def test_negative_timeout(executor):
    result = executor.execute('print(1)', timeout_seconds=-1)
    assert result.error == 'timeout'


# ===================================================================
# Memory (Linux only — RLIMIT_AS unreliable on macOS)
# ===================================================================

@pytest.mark.skipif(
    sys.platform == 'darwin',
    reason='RLIMIT_AS is not reliably enforced on macOS',
)
def test_excessive_memory(executor):
    result = executor.execute(
        "x = bytearray(512 * 1024 * 1024)",  # 512 MB vs 256 MB limit
        timeout_seconds=TIMEOUT,
    )
    assert result.error is not None


# ===================================================================
# Error handling
# ===================================================================

def test_system_exit_caught(executor):
    result = executor.execute('raise SystemExit(0)', timeout_seconds=TIMEOUT)
    assert result.error is not None
    assert 'SystemExit' in result.observation


def test_keyboard_interrupt_caught(executor):
    result = executor.execute(
        'raise KeyboardInterrupt', timeout_seconds=TIMEOUT,
    )
    assert result.error is not None
    assert 'KeyboardInterrupt' in result.observation


def test_runtime_error(executor):
    result = executor.execute(
        "raise ValueError('test error')", timeout_seconds=TIMEOUT,
    )
    assert result.error is not None
    assert 'ValueError' in result.observation
    assert 'test error' in result.observation


def test_syntax_error(executor):
    result = executor.execute('def f(:', timeout_seconds=TIMEOUT)
    assert result.error is not None
    assert 'SyntaxError' in result.observation


def test_division_by_zero(executor):
    result = executor.execute('print(1/0)', timeout_seconds=TIMEOUT)
    assert result.error is not None
    assert 'ZeroDivisionError' in result.observation


def test_name_error(executor):
    result = executor.execute(
        'print(undefined_variable)', timeout_seconds=TIMEOUT,
    )
    assert result.error is not None
    assert 'NameError' in result.observation


def test_type_error(executor):
    result = executor.execute("print(1 + 'a')", timeout_seconds=TIMEOUT)
    assert result.error is not None
    assert 'TypeError' in result.observation


# ===================================================================
# Output handling
# ===================================================================

def test_stdout_captured(executor):
    result = executor.execute(
        "print('hello')\nprint('world')", timeout_seconds=TIMEOUT,
    )
    assert 'hello' in result.observation
    assert 'world' in result.observation


def test_no_output_gives_placeholder(executor):
    result = executor.execute('x = 1', timeout_seconds=TIMEOUT)
    assert result.observation == '(no output)'
    assert result.error is None


def test_error_appears_in_observation(executor):
    result = executor.execute(
        "print('before')\nraise RuntimeError('boom')",
        timeout_seconds=TIMEOUT,
    )
    assert 'before' in result.observation
    assert 'RuntimeError' in result.observation
    assert 'boom' in result.observation


def test_empty_code(executor):
    result = executor.execute('', timeout_seconds=TIMEOUT)
    assert result.error is not None


def test_whitespace_only_code(executor):
    result = executor.execute('   \n  \n', timeout_seconds=TIMEOUT)
    assert result.error is not None


# ===================================================================
# Parent resilience
# ===================================================================

def test_parent_survives_blocked_import_crash(executor):
    """Parent handles import errors gracefully."""
    result = executor.execute(
        "import ctypes\nctypes.string_at(0)", timeout_seconds=TIMEOUT,
    )
    assert result.error is not None
    # Parent is still alive if we reach this assertion.


def test_parent_survives_timeout(executor):
    """Parent survives killing a stuck worker."""
    result = executor.execute('while True: pass', timeout_seconds=2)
    assert result.error is not None
    # Run another execution to prove the parent is healthy.
    result2 = executor.execute('print(1)', timeout_seconds=TIMEOUT)
    assert '1' in result2.observation
    assert result2.error is None


# ===================================================================
# Protocol conformance
# ===================================================================

def test_result_type_is_execution_result(executor):
    """Verify the executor returns the correct protocol type."""
    result = executor.execute('print(1)', timeout_seconds=TIMEOUT)
    assert hasattr(result, 'observation')
    assert hasattr(result, 'final_answer')
    assert hasattr(result, 'error')


def test_executor_matches_protocol(executor):
    """SubprocessExecutor satisfies the Executor protocol."""
    from agent_smith.loop.runner import Executor
    assert hasattr(executor, 'execute')
    # Structural check — the protocol expects execute(code, *, timeout_seconds)
    import inspect
    sig = inspect.signature(executor.execute)
    assert 'code' in sig.parameters or list(sig.parameters)[0] == 'code'
    assert 'timeout_seconds' in sig.parameters
