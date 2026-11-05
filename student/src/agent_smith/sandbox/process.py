"""Native Seatbelt execution, with BEST-EFFORT process-group cleanup only.

Detached descendants may survive, hold pipes, and keep their original filesystem
permissions. Return/cancellation never establishes workspace quiescence. Trusted
host cooperation must outlive any such descendants (see workspace_policy).
"""

import os
from pathlib import Path
import platform
import select
import selectors
import signal
import subprocess
import sys
import tempfile
from threading import Event
import time

from .backend import ProcessLimits, ProcessRequest, ProcessResult
from .seatbelt import SeatbeltProfile, build_seatbelt_profile


class BackendSetupError(RuntimeError):
    """Admission, launch or actual enforcement failed; no payload fallback."""


class SeatbeltBackend:
    """Roots and argv are trusted-parent inputs, never model policy overrides."""

    def __init__(self, *, workspace: Path, scratch: Path,
                 runtime_roots: tuple[Path, ...]) -> None:
        if platform.system() != 'Darwin':
            raise BackendSetupError('Seatbelt requires Darwin')
        self._binary = '/usr/bin/sandbox-exec'
        if not os.access(self._binary, os.X_OK):
            raise BackendSetupError('missing executable sandbox-exec')
        try:
            self._workspace = Path(workspace).resolve(strict=True)
            self._scratch = Path(scratch).resolve(strict=True)
            self._runtime_roots = tuple(Path(p).resolve(strict=True) for p in runtime_roots)
            self._profile()  # Early admission only; never retained for execution.
        except (OSError, ValueError, TypeError) as exc:
            raise BackendSetupError(f'admission failed: {exc}') from exc

    def _profile(self) -> SeatbeltProfile:
        try:
            return build_seatbelt_profile(workspace=self._workspace,
                scratch=self._scratch, runtime_roots=self._runtime_roots)
        except (OSError, ValueError, TypeError) as exc:
            raise BackendSetupError(f'admission failed: {exc}') from exc

    def execute(self, request: ProcessRequest, *, cancel: Event | None = None) -> ProcessResult:
        if type(request) is not ProcessRequest:
            raise TypeError('expected ProcessRequest')
        if cancel is not None and not isinstance(cancel, Event):
            raise TypeError('cancel must be threading.Event')
        deadline = time.monotonic() + request.limits.timeout
        profile = self._profile()  # Public admission/compiler on EVERY call.
        if cancel is not None and cancel.is_set():
            return ProcessResult('cancelled', None, error='execution cancelled before launch')
        if time.monotonic() >= deadline:
            return ProcessResult('timeout', None, error='execution deadline exceeded before launch')
        try:
            interrupted = self._probe(profile, deadline=deadline, cancel=cancel)
        except OSError as exc:
            raise BackendSetupError(f'enforcement/startup probe setup failed: {exc}') from exc
        if interrupted is not None:
            return interrupted
        if cancel is not None and cancel.is_set():
            return ProcessResult('cancelled', None, error='execution cancelled before launch')
        if time.monotonic() >= deadline:
            return ProcessResult('timeout', None, error='execution deadline exceeded before launch')
        return self._run(profile, request, cancel=cancel, deadline=deadline)

    def _probe(self, profile: SeatbeltProfile, *, deadline: float,
               cancel: Event | None) -> ProcessResult | None:
        # The outside canary EXISTS and is host-readable/writable. Only EACCES /
        # EPERM counts as refusal; missing files and failed startup cannot pass.
        with tempfile.TemporaryDirectory(prefix='seatbelt-probe-') as directory:
            outside = Path(directory).resolve() / 'outside'
            if any(outside.is_relative_to(root) for root in
                   (self._workspace, self._scratch, *self._runtime_roots)):
                raise BackendSetupError('probe canary is not outside configured roots')
            outside.write_bytes(b'fixture-canary')
            with tempfile.TemporaryDirectory(prefix='startup-', dir=self._scratch) as allowed:
                control = Path(allowed) / 'control'
                code = f'''
import errno, os
outside = {str(outside)!r}
for mode in ('rb', 'ab'):
    try:
        with open(outside, mode) as stream:
            if mode == 'ab': stream.write(b'ineffective')
    except OSError as exc:
        if exc.errno not in (errno.EACCES, errno.EPERM): raise
    else:
        raise RuntimeError('ineffective enforcement')
with open({str(control)!r}, 'wb') as stream: stream.write(b'allowed')
with open({str(control)!r}, 'rb') as stream: assert stream.read() == b'allowed'
os.write(1, b'seatbelt-probe-ok')
'''
                probe = ProcessRequest((str(Path(sys.executable).resolve()),
                    '-I', '-S', '-B', '-c', code), limits=ProcessLimits(
                        timeout=2, stdout_bytes=4096, stderr_bytes=4096))
                result = self._run(profile, probe, cancel=cancel,
                                   deadline=min(deadline, time.monotonic() + 2))
                if result.status == 'cancelled':
                    return ProcessResult('cancelled', None, error='cancelled during startup probe')
                if result.status == 'timeout' and time.monotonic() >= deadline:
                    return ProcessResult('timeout', None, error='deadline exceeded during startup probe')
                if (result.status != 'exited' or result.returncode != 0
                        or result.stdout != b'seatbelt-probe-ok'
                        or not control.is_file() or control.read_bytes() != b'allowed'
                        or outside.read_bytes() != b'fixture-canary'):
                    raise BackendSetupError(f'enforcement/startup probe failed: '
                                            f'{result.status}: {result.error}: {result.stderr_text}')

    def _run(self, profile: SeatbeltProfile, request: ProcessRequest, *,
             cancel: Event | None, deadline: float) -> ProcessResult:
        environment = {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C',
                       'HOME': str(self._scratch), 'TMPDIR': str(self._scratch)}
        try:
            child = subprocess.Popen((self._binary, *profile.arguments(), *request.argv),
                cwd=self._workspace, env=environment, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                close_fds=True, start_new_session=True)
        except (OSError, ValueError) as exc:
            raise BackendSetupError(f'sandbox-exec launch failed: {exc}') from exc
        output = {'stdout': bytearray(), 'stderr': bytearray()}
        caps = {'stdout': request.limits.stdout_bytes, 'stderr': request.limits.stderr_bytes}
        status = 'exited'
        error = None
        truncated = False
        sent = 0
        group_signalled = False
        selector = None
        exit_events = None

        def kill_group() -> None:
            nonlocal group_signalled
            if not group_signalled:
                # Never poll()/wait() before this signal: the unreaped leader
                # reserves its PID/PGID, avoiding signals to a recycled group.
                group_signalled = True
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except PermissionError:
                    # Darwin can return EPERM for a group containing only a
                    # zombie. The still-reserved leader is also a safe direct
                    # best-effort target; never signal again after wait().
                    try:
                        os.kill(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

        def close(stream) -> None:
            try:
                selector.unregister(stream)
            except KeyError:
                pass
            stream.close()

        def drain(stream, name: str) -> bool:
            nonlocal status, error, truncated
            try:
                chunk = os.read(stream.fileno(), min(65536, caps[name] - len(output[name]) + 1))
            except BlockingIOError:
                return False
            if not chunk:
                close(stream)
                return False
            remaining = caps[name] - len(output[name])
            output[name].extend(chunk[:remaining])
            if len(chunk) > remaining:
                status, error, truncated = 'output_limit', f'{name} byte limit exceeded', True
            return True

        try:
            selector = selectors.DefaultSelector()
            exit_events = select.kqueue()
            exited = False
            try:
                exit_events.control([select.kevent(child.pid, filter=select.KQ_FILTER_PROC,
                    flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
                    fflags=select.KQ_NOTE_EXIT)], 0, 0)
            except ProcessLookupError:
                # Already exited, but still unreaped and reserving its PID.
                exited = True
            for stream, name in ((child.stdin, 'stdin'), (child.stdout, 'stdout'),
                                 (child.stderr, 'stderr')):
                os.set_blocking(stream.fileno(), False)
                if name == 'stdin' and not request.input:
                    stream.close()
                else:
                    selector.register(stream, selectors.EVENT_WRITE if name == 'stdin'
                                      else selectors.EVENT_READ, name)
            while True:
                # Process events observe exit without reaping. Do not poll()
                # the Popen: retain the PID reservation until group cleanup.
                exited = exited or bool(exit_events.control(None, 1, 0))
                if cancel is not None and cancel.is_set():
                    status, error = 'cancelled', 'execution cancelled'
                    break
                if time.monotonic() >= deadline:
                    status, error = 'timeout', 'execution deadline exceeded'
                    break
                if exited:
                    kill_group()
                    # Drain only immediately available bounded bytes. Do not wait
                    # for EOF from escaped descendants holding inherited pipes.
                    for key in list(selector.get_map().values()):
                        if key.data != 'stdin':
                            while drain(key.fileobj, key.data) and status == 'exited':
                                pass
                            if not key.fileobj.closed:
                                truncated = True
                                if error is None:
                                    error = 'output pipe remained open after leader exit'
                    break
                for key, _ in selector.select(min(.02, max(0, deadline - time.monotonic()))):
                    if key.data == 'stdin':
                        try:
                            sent += os.write(key.fd, memoryview(request.input)[sent:sent + 65536])
                        except BrokenPipeError:
                            close(key.fileobj)
                        except BlockingIOError:
                            pass
                        else:
                            if sent == len(request.input):
                                close(key.fileobj)
                    else:
                        drain(key.fileobj, key.data)
                    if status == 'output_limit':
                        break
                if status == 'output_limit':
                    break
        except OSError as exc:
            status, error = 'error', f'process I/O failed: {exc}'
        finally:
            try:
                kill_group()
            finally:
                for stream in (child.stdin, child.stdout, child.stderr):
                    if not stream.closed:
                        stream.close()
                if selector is not None:
                    selector.close()
                if exit_events is not None:
                    exit_events.close()
                child.wait()
        if status != 'exited':
            truncated = True
        return ProcessResult(status, child.returncode, bytes(output['stdout']),
                             bytes(output['stderr']), truncated, error)
