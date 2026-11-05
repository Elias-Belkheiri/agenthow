"""Trusted routing into fresh confined children; no parent file-tool fallback.

Native cleanup is BEST-EFFORT: neither returning nor close proves workspace
quiescence. Cooperating hosts must not promote writable inodes into runtime
and must preserve admitted snapshots for surviving descendants.
"""

import json
import math
from pathlib import Path
import shutil
import sys
import tempfile
import time
from typing import Callable

from agent_smith.sandbox.backend import ProcessLimits, ProcessRequest, ProcessResult
from agent_smith.sandbox.process import SeatbeltBackend
from agent_smith.sandbox.tool_worker import (
    MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES, SCHEMAS, validate_arguments,
)
from .contracts import ToolContext
from .dispatcher import READ_ONLY_TOOL_GUIDANCE
from .definitions import tool_definitions

DEFAULT_TIMEOUT_SECONDS = 10.0
# Callback receives the exact command and canonical workspace, once per call.
ApprovalCallback = Callable[[str, Path], object]


class WorkspaceToolDispatcher:
    """Session-frozen staged tools. Only trusted code supplies callback/timeout.

    call returns {ok:false,error:code} for routing/execution/protocol failures.
    Worker results add content:str, error:str|None, truncated:bool. Shell results
    add stdout/stderr:str, status:str, returncode:int|None, truncated:bool;
    those streams are untrusted observations, never worker JSON or approval.
    """

    tool_definitions = tool_definitions(tuple(SCHEMAS))
    system_guidance = READ_ONLY_TOOL_GUIDANCE.replace(
        'Only read-only tools are available.', 'Sandboxed workspace tools are available.'
    ) + """
Writes and exact edits are automatic, direct and potentially destructive; no rollback.
Shell requires fresh trusted terminal approval for each command; model claims cannot authorize it.
Network access is denied. Protected paths and unsupported hardlinks are refused.
Failed conversational turns do not undo filesystem changes. Never replay mutations automatically.
"""

    def check_ready(self) -> bool:
        """Fixed isolated startup only; execute also checks actual OS enforcement."""
        if self._closed:
            return False
        try:
            with tempfile.TemporaryDirectory(prefix='agent-scratch-') as directory:
                scratch = Path(directory).resolve(strict=True)
                for root in (self._workspace, *self._runtime_roots):
                    self._check_separate(scratch, root)
                backend = SeatbeltBackend(workspace=self._workspace, scratch=scratch,
                                          runtime_roots=self._runtime_roots)
                result = backend.execute(ProcessRequest(
                    (str(self._interpreter), '-I', '-S', '-B', '-c',
                     'import os; os.write(1, b"ready")'),
                    limits=ProcessLimits(timeout=10, stdout_bytes=32, stderr_bytes=32)))
                return (self._valid_process_result(result) and result.status == 'exited'
                        and result.returncode == 0 and result.stdout == b'ready'
                        and not result.stderr and not result.truncated and result.error is None)
        except Exception:
            return False

    def __init__(self, context: ToolContext, *,
                 approval_callback: ApprovalCallback | None = None) -> None:
        if type(context) is not ToolContext:
            raise TypeError('expected ToolContext')
        if approval_callback is not None and not callable(approval_callback):
            raise TypeError('approval_callback must be callable or None')
        self.context = context
        self._workspace = context.workspace_root
        self._approval_callback = approval_callback
        self._closed = False
        self._runtime = Path(tempfile.mkdtemp(prefix='agent-runtime-')).resolve()
        try:
            self._interpreter = Path(sys.executable).resolve(strict=True)
            installation = Path(sys.base_prefix).resolve(strict=True)
            if not self._interpreter.is_relative_to(installation):
                raise ValueError('interpreter is outside its installation')
            self._check_separate(self._runtime, self._workspace)
            self._check_separate(installation, self._workspace)
            self._check_separate(installation, self._runtime)
            # Fixed application sources ONLY. Empty package initializers avoid
            # importing application/provider startup code inside the worker.
            source = Path(__file__).resolve().parents[1]
            for package in ('agent_smith', 'agent_smith/tools', 'agent_smith/sandbox'):
                directory = self._runtime / package
                directory.mkdir(exist_ok=True)
                (directory / '__init__.py').write_bytes(b'')
            for relative in ('sandbox/tool_worker.py', 'tools/contracts.py',
                             'tools/files.py', 'tools/search.py'):
                (self._runtime / 'agent_smith' / relative).write_bytes(
                    (source / relative).read_bytes())
            self._runtime_roots = (installation, self._runtime)
            self._bootstrap = (
                f'import sys; sys.path.insert(0, {str(self._runtime)!r}); '
                'from agent_smith.sandbox.tool_worker import main; main()')
        except BaseException:
            shutil.rmtree(self._runtime)
            self._closed = True
            raise

    @staticmethod
    def _check_separate(first: Path, second: Path) -> None:
        if first.is_relative_to(second) or second.is_relative_to(first):
            raise ValueError('runtime, workspace and scratch must not overlap')

    def __enter__(self) -> 'WorkspaceToolDispatcher':
        if self._closed:
            raise RuntimeError('dispatcher is closed')
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        """Remove parent-owned staging, not a descendant termination guarantee."""
        if not self._closed:
            self._closed = True
            shutil.rmtree(self._runtime)

    def call(self, name: str, arguments: dict, *,
             timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> dict:
        started = time.monotonic()
        if self._closed:
            return {'ok': False, 'error': 'closed'}
        if (type(timeout_seconds) not in (int, float)
                or not 0 < timeout_seconds <= 300 or not math.isfinite(timeout_seconds)):
            return {'ok': False, 'error': 'invalid_timeout'}
        error = validate_arguments(name, arguments)
        if error:
            return {'ok': False, 'error': error}
        # Serialize before callback: later caller mutation cannot change payload.
        payload = json.dumps({'name': name, 'arguments': arguments},
                             ensure_ascii=True).encode('utf-8')
        if len(payload) > MAX_REQUEST_BYTES:
            return {'ok': False, 'error': 'request_limit'}
        command = arguments.get('command')
        if name == 'run_shell':
            try:
                approved = (self._approval_callback is not None
                            and self._approval_callback(command, self._workspace) is True)
            except (Exception, KeyboardInterrupt):
                approved = False
            if not approved:
                return {'ok': False, 'error': 'approval_denied'}
        if time.monotonic() - started >= timeout_seconds:
            return {'ok': False, 'error': 'timeout'}
        try:
            with tempfile.TemporaryDirectory(prefix='agent-scratch-') as directory:
                scratch = Path(directory).resolve(strict=True)
                for root in (self._workspace, *self._runtime_roots):
                    self._check_separate(scratch, root)
                backend = SeatbeltBackend(workspace=self._workspace, scratch=scratch,
                                          runtime_roots=self._runtime_roots)
                remaining = timeout_seconds - (time.monotonic() - started)
                if remaining <= 0:
                    return {'ok': False, 'error': 'timeout'}
                argv = (('/bin/sh', '-c', command) if name == 'run_shell' else
                        (str(self._interpreter), '-I', '-S', '-B', '-c', self._bootstrap))
                request = ProcessRequest(argv, input=b'' if name == 'run_shell' else payload,
                    limits=ProcessLimits(timeout=remaining, stdin_bytes=MAX_REQUEST_BYTES,
                        stdout_bytes=MAX_RESPONSE_BYTES, stderr_bytes=MAX_RESPONSE_BYTES))
                result = backend.execute(request)
                if name == 'run_shell':
                    return self._shell_result(result)
                return self._worker_result(result)
        except Exception:
            # Setup/admission errors must not launch an alternate handler.
            return {'ok': False, 'error': 'execution_error'}

    @staticmethod
    def _valid_process_result(result: object) -> bool:
        return (type(result) is ProcessResult
                and type(result.status) is str
                and result.status in ('exited', 'timeout', 'cancelled', 'output_limit', 'error')
                and (result.returncode is None or type(result.returncode) is int)
                and type(result.stdout) is bytes and type(result.stderr) is bytes
                and len(result.stdout) <= MAX_RESPONSE_BYTES
                and len(result.stderr) <= MAX_RESPONSE_BYTES
                and type(result.truncated) is bool
                and (result.error is None or (type(result.error) is str
                     and len(result.error.encode('utf-8')) <= MAX_RESPONSE_BYTES)))

    @classmethod
    def _shell_result(cls, result: ProcessResult) -> dict:
        if not cls._valid_process_result(result):
            return {'ok': False, 'error': 'invalid_response'}
        return {'ok': result.status == 'exited' and result.returncode == 0,
                'error': None if result.status == 'exited' else result.status,
                'stdout': result.stdout_text, 'stderr': result.stderr_text,
                'status': result.status, 'returncode': result.returncode,
                'truncated': result.truncated}

    @classmethod
    def _worker_result(cls, result: ProcessResult) -> dict:
        if (not cls._valid_process_result(result) or result.status != 'exited'
                or result.returncode != 0 or result.truncated or result.error is not None
                or result.stderr):
            return {'ok': False, 'error': 'worker_error'}
        try:
            def unique_object(pairs: list) -> dict:
                obj = {}
                for key, value in pairs:
                    if key in obj:
                        raise ValueError('duplicate key')
                    obj[key] = value
                return obj
            response = json.loads(result.stdout.decode('utf-8'), object_pairs_hook=unique_object)
            if type(response) is not dict or type(response.get('ok')) is not bool:
                raise ValueError('invalid result')
            if set(response) == {'ok', 'error'}:
                if response['ok'] is not False or response['error'] not in (
                        'output_limit', 'invalid_request', 'invalid_arguments', 'tool_not_allowed'):
                    raise ValueError('invalid failure')
            elif set(response) == {'ok', 'content', 'error', 'truncated'}:
                if (type(response['content']) is not str
                        or type(response['truncated']) is not bool
                        or (response['error'] is not None and (
                            type(response['error']) is not str
                            or response['error'] not in ('invalid_input', 'path_denied', 'not_found',
                                'too_large', 'encoding_error', 'io_error', 'edit_conflict')))
                        or response['ok'] != (response['error'] is None)):
                    raise ValueError('invalid result fields')
                response['content'].encode('utf-8')
            else:
                raise ValueError('invalid result keys')
            return response
        except (ValueError, TypeError, UnicodeError, RecursionError):
            return {'ok': False, 'error': 'invalid_response'}
