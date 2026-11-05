"""Subprocess executor implementing the runner's Executor protocol.

Spawns the sandbox worker as a child process, sends the code and policy
via stdin, reads the JSON result from a temp file, and enforces a
wall-clock timeout by killing the child if it exceeds the deadline.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from agent_smith.loop.runner import ExecutionResult
from agent_smith.sandbox.policy import SandboxPolicy

_WORKER = str(Path(__file__).parent / 'worker.py')


class SubprocessExecutor:
    """Execute code in a sandboxed child process.

    Satisfies the ``Executor`` protocol defined in
    ``agent_smith.loop.runner``.
    """

    def __init__(self, policy: SandboxPolicy) -> None:
        self._policy = policy

    def execute(self, code: str, *, timeout_seconds: float) -> ExecutionResult:
        if not isinstance(code, str) or not code.strip():
            return ExecutionResult(observation='(empty code)', error='empty_code')
        if timeout_seconds <= 0:
            return ExecutionResult(
                observation='No time remaining.', error='timeout',
            )

        request = {
            'code': code,
            'allowed_modules': sorted(self._policy.allowed_modules),
            'allowed_directories': list(self._policy.allowed_directories),
            'max_memory_bytes': self._policy.max_memory_bytes,
            'max_cpu_seconds': self._policy.max_cpu_seconds,
            'max_output_bytes': self._policy.max_output_bytes,
        }

        tmp = tempfile.NamedTemporaryFile(
            suffix='.json', prefix='sandbox_result_', delete=False,
        )
        result_path = tmp.name
        tmp.close()

        try:
            proc = subprocess.Popen(
                [sys.executable, '-u', '-I', _WORKER, result_path],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            try:
                _, stderr = proc.communicate(
                    input=json.dumps(request).encode('utf-8'),
                    timeout=timeout_seconds,
                )
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
                return ExecutionResult(
                    observation='Execution timed out.', error='timeout',
                )

            # Read result file
            result_file = Path(result_path)
            if (not result_file.exists()
                    or result_file.stat().st_size == 0):
                detail = ''
                if stderr:
                    detail = stderr.decode('utf-8', errors='replace')[:1024]
                return ExecutionResult(
                    observation=(
                        f'Sandbox worker exited with code '
                        f'{proc.returncode}. {detail}'
                    ).strip(),
                    error='worker_crash',
                )

            with open(result_path) as fh:
                result = json.load(fh)

            return ExecutionResult(
                observation=result.get('observation', '(no output)'),
                final_answer=result.get('final_answer'),
                error=result.get('error'),
            )

        except json.JSONDecodeError:
            return ExecutionResult(
                observation='Sandbox returned invalid result.',
                error='worker_crash',
            )
        except OSError as exc:
            return ExecutionResult(
                observation=f'Sandbox error: {exc}',
                error='sandbox_error',
            )
        finally:
            try:
                Path(result_path).unlink(missing_ok=True)
            except OSError:
                pass
