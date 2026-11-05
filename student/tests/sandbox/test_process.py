"""Bounded native backend: fixture-only enforcement and lifecycle evidence."""
from dataclasses import FrozenInstanceError
import math
import os
from pathlib import Path
import sys
import threading
import time

import pytest

from agent_smith.sandbox.backend import ProcessLimits, ProcessRequest
from agent_smith.sandbox.process import BackendSetupError, SeatbeltBackend


@pytest.mark.parametrize('kwargs', [
    {'timeout': math.inf}, {'timeout': float('nan')}, {'timeout': 0},
    {'stdout_bytes': True}, {'stdin_bytes': -1}, {'stderr_bytes': 2**30},
    {'timeout': True}, {'timeout': '1'}, {'stdout_bytes': 1.5},
])
def test_invalid_limits(kwargs):
    with pytest.raises((ValueError, TypeError)):
        ProcessLimits(**kwargs)


@pytest.mark.parametrize('argv,data', [([], b''), (('x\0',), b''),
    (('x', 1), b''), (('x',), 'text'), ('x', b''), (('x' * 70000,), b'')])
def test_invalid_request(argv, data):
    with pytest.raises((ValueError, TypeError)):
        ProcessRequest(argv, data)


def test_requests_limits_and_results_are_immutable():
    from agent_smith.sandbox.backend import ProcessResult
    for value, field, replacement in (
        (ProcessLimits(), 'timeout', 1),
        (ProcessRequest(('x',)), 'argv', ('y',)),
        (ProcessResult('exited', 0), 'stdout', b'changed'),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(value, field, replacement)
    with pytest.raises(ValueError, match='stdin'):
        ProcessRequest(('x',), b'xx', ProcessLimits(stdin_bytes=1))


@pytest.fixture
def roots(tmp_path):
    workspace, scratch, runtime = (tmp_path / name for name in ('work', 'scratch', 'runtime'))
    for root in (workspace, scratch, runtime):
        root.mkdir()
    return workspace, scratch, runtime


def make_backend(roots):
    workspace, scratch, runtime = roots
    return SeatbeltBackend(workspace=workspace, scratch=scratch,
        runtime_roots=(runtime, Path(sys.base_prefix).resolve()))


def test_platform_and_missing_binary(roots, monkeypatch):
    import agent_smith.sandbox.process as module
    monkeypatch.setattr(module.platform, 'system', lambda: 'Linux')
    with pytest.raises(BackendSetupError, match='Darwin'):
        make_backend(roots)
    monkeypatch.setattr(module.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(module.os, 'access', lambda *args: False)
    with pytest.raises(BackendSetupError, match='sandbox-exec'):
        make_backend(roots)


def test_admission_failure_no_launch(roots, monkeypatch):
    import agent_smith.sandbox.process as module
    def fail(**kwargs):
        raise ValueError('admission fixture')
    monkeypatch.setattr(module, 'build_seatbelt_profile', fail)
    monkeypatch.setattr(module.subprocess, 'Popen', lambda *a, **k: pytest.fail('launched'))
    with pytest.raises(BackendSetupError, match='admission fixture'):
        make_backend(roots)


def test_fresh_admission_after_construction(roots, monkeypatch):
    if sys.platform != 'darwin':
        pytest.skip('requires Darwin')
    import agent_smith.sandbox.process as module
    backend = make_backend(roots)
    source = roots[0] / 'original'
    source.write_bytes(b'fixture')
    os.link(source, roots[0] / 'alias')
    monkeypatch.setattr(module.subprocess, 'Popen', lambda *a, **k: pytest.fail('launched'))
    with pytest.raises(BackendSetupError, match='hardlink'):
        backend.execute(ProcessRequest(('/usr/bin/true',)))


@pytest.mark.parametrize('profile', ['(version 1) (invalid-fixture-operation)',
                                     '(version 1) (allow default)',
                                     '(version 1) (deny default)'])
def test_profile_failure_or_ineffective_probe_no_payload(roots, monkeypatch, profile):
    if sys.platform != 'darwin':
        pytest.skip('requires Darwin')
    import agent_smith.sandbox.process as module
    from agent_smith.sandbox.seatbelt import SeatbeltProfile
    backend = make_backend(roots)
    monkeypatch.setattr(module, 'build_seatbelt_profile',
        lambda **kwargs: SeatbeltProfile(profile, ()))
    with pytest.raises(BackendSetupError, match='probe'):
        python(backend, "open('payload-started', 'w').write('bad')")
    assert not (roots[0] / 'payload-started').exists()


def test_launch_oserror_no_fallback(roots, monkeypatch):
    if sys.platform != 'darwin':
        pytest.skip('requires Darwin')
    import agent_smith.sandbox.process as module
    backend = make_backend(roots)
    calls = []
    def fail(*args, **kwargs):
        calls.append(args)
        raise OSError('fixture launch refusal')
    monkeypatch.setattr(module.subprocess, 'Popen', fail)
    with pytest.raises(BackendSetupError, match='launch'):
        backend.execute(ProcessRequest(('/usr/bin/true',)))
    assert len(calls) == 1
    assert calls[0][0][0] == '/usr/bin/sandbox-exec'


@pytest.fixture
def backend(roots):
    if sys.platform != 'darwin':
        pytest.skip('real Seatbelt requires Darwin')
    return make_backend(roots)


def python(backend, code, data=b'', limits=None, cancel=None):
    return backend.execute(ProcessRequest((str(Path(sys.executable).resolve()),
        '-I', '-S', '-B', '-c', code), data, limits or ProcessLimits()), cancel=cancel)


def test_enforcement_environment_and_fd(backend, roots, tmp_path, monkeypatch):
    canary = tmp_path / 'outside'
    canary.write_bytes(b'fixture')
    monkeypatch.setenv('OPENAI_API_KEY', 'fixture-not-secret')
    monkeypatch.setenv('HTTPS_PROXY', 'fixture')
    fd = os.open(canary, os.O_RDONLY)
    os.set_inheritable(fd, True)
    try:
        result = python(backend, f'''
import os
assert set(os.environ) == {{'PATH', 'LC_ALL', 'HOME', 'TMPDIR'}}
try: os.fstat({fd})
except OSError: pass
else: raise AssertionError('inherited FD')
for mode in ('rb', 'ab'):
    try: open({str(canary)!r}, mode)
    except PermissionError: pass
    else: raise AssertionError('outside access')
open(os.environ['TMPDIR'] + '/allowed', 'wb').write(b'ok')
print('ok')
''')
    finally:
        os.close(fd)
    assert result.status == 'exited', result
    assert result.returncode == 0, result.stderr
    assert canary.read_bytes() == b'fixture'
    assert (roots[1] / 'allowed').read_bytes() == b'ok'


@pytest.mark.parametrize('stream', [1, 2])
def test_output_flood(backend, stream):
    result = python(backend, f'import os\nwhile True: os.write({stream}, b"x" * 8192)',
        limits=ProcessLimits(stdout_bytes=1024, stderr_bytes=1024))
    assert result.status == 'output_limit'
    assert result.truncated and result.error
    assert len(result.stdout) <= 1024 and len(result.stderr) <= 1024


def test_large_input_simultaneous_output_and_binary(backend):
    result = python(backend, '''
import os
os.write(1, b'x' * 100000)
data = bytearray()
while True:
    part = os.read(0, 8192)
    if not part: break
    data.extend(part)
os.write(2, b'\\xff\\x00' + str(len(data)).encode())
''', b'y' * 200000)
    assert result.returncode == 0, result
    assert result.stdout == b'x' * 100000
    assert result.stderr == b'\xff\x00200000'
    assert result.stderr_text.startswith('\ufffd\x00')


def test_nonzero_payload_exit_is_not_setup_error(backend):
    result = python(backend, 'import sys; sys.exit(23)')
    assert result.status == 'exited'
    assert result.returncode == 23
    assert not result.truncated and result.error is None


def test_output_flood_while_feeding_large_input(backend):
    result = python(backend, '''
import os
while True:
    os.write(1, b'x' * 8192)
    os.write(2, b'y' * 8192)
''', b'z' * 200000, limits=ProcessLimits(stdout_bytes=1024, stderr_bytes=1024))
    assert result.status == 'output_limit'
    assert result.truncated and result.error
    assert len(result.stdout) <= 1024 and len(result.stderr) <= 1024


def test_public_profile_rebuilt_and_leaders_reaped(backend, monkeypatch):
    import agent_smith.sandbox.process as module
    build = module.build_seatbelt_profile
    launch = module.subprocess.Popen
    calls, children = [], []
    def profile(**kwargs):
        calls.append(kwargs)
        return build(**kwargs)
    def popen(*args, **kwargs):
        child = launch(*args, **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(module, 'build_seatbelt_profile', profile)
    monkeypatch.setattr(module.subprocess, 'Popen', popen)
    for _ in range(2):
        assert python(backend, 'print("ok")').returncode == 0
    assert len(calls) == 2
    assert len(children) == 4  # Each execute has its own enforcement probe.
    for child in children:
        assert child.returncode == 0
        with pytest.raises(ChildProcessError):
            os.waitpid(child.pid, os.WNOHANG)


def test_precancelled_request_never_launches(backend, monkeypatch):
    import agent_smith.sandbox.process as module
    event = threading.Event()
    event.set()
    monkeypatch.setattr(module.subprocess, 'Popen', lambda *a, **k: pytest.fail('launched'))
    result = python(backend, 'raise RuntimeError("must not run")', cancel=event)
    assert result.status == 'cancelled'
    assert result.returncode is None and result.error


def test_admission_time_counts_toward_deadline(backend, monkeypatch):
    import agent_smith.sandbox.process as module
    build = module.build_seatbelt_profile
    def slow_profile(**kwargs):
        time.sleep(.02)
        return build(**kwargs)
    monkeypatch.setattr(module, 'build_seatbelt_profile', slow_profile)
    monkeypatch.setattr(module.subprocess, 'Popen', lambda *a, **k: pytest.fail('launched'))
    result = python(backend, 'pass', limits=ProcessLimits(timeout=.001))
    assert result.status == 'timeout' and result.returncode is None


def test_timeout_and_cancellation(backend):
    start = time.monotonic()
    result = python(backend, 'import time; time.sleep(10)', limits=ProcessLimits(timeout=.15))
    assert result.status == 'timeout'
    assert result.error and result.truncated
    assert time.monotonic() - start < 1.5
    event = threading.Event()
    timer = threading.Timer(.1, event.set)
    timer.start()
    try:
        result = python(backend, 'import time; time.sleep(10)', cancel=event)
    finally:
        timer.join(1)
    assert result.status == 'cancelled'


def test_leader_exit_with_group_child_pipes(backend):
    start = time.monotonic()
    result = python(backend, '''
import os, time
if os.fork() == 0:
    time.sleep(2)
    os._exit(0)
os._exit(0)
''')
    assert result.status == 'exited'
    assert time.monotonic() - start < 1.5


def test_detached_survivor_best_effort(backend, roots):
    # Not a containment assertion: the child escapes the group and self-exits
    # independently within 2 seconds. Trusted fixture cleanup uses its known PID.
    pid = None
    start = time.monotonic()
    try:
        result = python(backend, '''
import os, time
pid = os.fork()
if pid == 0:
    os.setsid()
    open('detached-ready', 'w').write(str(os.getpid()))
    time.sleep(2)
    os._exit(0)
while not os.path.exists('detached-ready'): time.sleep(.01)
print(pid, flush=True)
os._exit(0)
''')
        pid = int(result.stdout)
        assert result.status == 'exited'
        assert time.monotonic() - start < 1.5
        assert result.truncated and result.error
        os.kill(pid, 0)  # Honest evidence of the approved BEST-EFFORT limit.
    finally:
        if pid is not None:
            try: os.kill(pid, 9)
            except ProcessLookupError: pass
