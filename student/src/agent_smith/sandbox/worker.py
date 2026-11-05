"""Sandbox worker: child process that executes code in a restricted namespace.

Communication protocol
----------------------
argv[1]  : path to write the JSON result
stdin    : JSON request with code and policy settings

This module is **self-contained** — it does not import agent_smith.
"""

import ast
import io
import json
import os
import sys
import types

_MAX_OUTPUT = 65_536  # 64 KB default


# ---------------------------------------------------------------------------
# Resource limits
# ---------------------------------------------------------------------------

def _apply_resource_limits(max_memory: int, max_cpu: int) -> None:
    """Set OS resource limits.  Silently skipped when unsupported."""
    try:
        import resource
    except ImportError:
        return
    if max_memory > 0:
        try:
            resource.setrlimit(resource.RLIMIT_AS, (max_memory, max_memory))
        except (ValueError, OSError):
            pass  # RLIMIT_AS not supported (macOS)
    if max_cpu > 0:
        try:
            # Soft = max_cpu, hard = max_cpu + 5 (grace period before SIGKILL)
            resource.setrlimit(resource.RLIMIT_CPU, (max_cpu, max_cpu + 5))
        except (ValueError, OSError):
            pass


# ---------------------------------------------------------------------------
# Import restrictor (meta-path hook — backup defense)
# ---------------------------------------------------------------------------

def _install_import_hook(allowed_set: frozenset[str],
                         preloaded: frozenset[str]) -> None:
    """Insert a meta-path finder that blocks non-whitelisted imports.

    The meta-path hook is only consulted for modules **not** already in
    ``sys.modules``.  It is a backup for the primary ``__import__``
    restriction in the builtins dict.  ``preloaded`` allows internal
    dependencies of whitelisted modules that were loaded during setup.
    """

    class _Finder:
        @staticmethod
        def find_module(fullname: str, path=None):
            if fullname in preloaded:
                return None  # allow — dependency of a whitelisted module
            top = fullname.split('.')[0]
            if top in allowed_set:
                return None  # allow — whitelisted top-level
            return _Finder  # block — _Finder becomes the loader

        @staticmethod
        def load_module(fullname: str):
            raise ImportError(
                f"Import of '{fullname}' is not allowed in the sandbox"
            )

    sys.meta_path.insert(0, _Finder)


# ---------------------------------------------------------------------------
# Restricted builtins builder
# ---------------------------------------------------------------------------

_BLOCKED_BUILTINS: frozenset[str] = frozenset({
    'breakpoint', 'compile', 'eval', 'exec',
    'exit', 'quit', 'help', 'input',
    'credits', 'copyright', 'license',
})


def _build_safe_builtins(
    allowed_set: frozenset[str],
    allowed_dirs: tuple[str, ...],
    real_import,
    real_open,
) -> tuple[dict, list]:
    """Return (safe_builtins_dict, answer_box).

    ``answer_box`` is a one-element list whose ``[0]`` is set by
    ``final_answer()`` inside user code.
    """

    import builtins as _builtins

    safe: dict = {}
    for name in dir(_builtins):
        if name.startswith('_'):
            continue
        if name in _BLOCKED_BUILTINS:
            continue
        safe[name] = getattr(_builtins, name)

    # __build_class__ is needed for ``class`` statements.
    safe['__build_class__'] = _builtins.__build_class__

    # --- restricted __import__ (primary gatekeeper) -------------------------
    def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):
        top = name.split('.')[0]
        if top not in allowed_set:
            raise ImportError(
                f"Import of '{top}' is not allowed in the sandbox"
            )
        return real_import(name, globals, locals, fromlist, level)

    safe['__import__'] = _safe_import

    # --- restricted open ----------------------------------------------------
    resolved_dirs = (
        tuple(os.path.realpath(d) for d in allowed_dirs)
        if allowed_dirs else ()
    )
    sep = os.sep

    def _safe_open(file, mode='r', *args, **kwargs):
        if isinstance(file, int):
            raise PermissionError(
                'Opening file descriptors is not allowed in the sandbox'
            )
        if not resolved_dirs:
            raise PermissionError(
                'File access is not allowed in the sandbox'
            )
        try:
            target = os.path.realpath(str(file))
        except (OSError, ValueError, TypeError) as exc:
            raise PermissionError('Cannot resolve path') from exc
        for base in resolved_dirs:
            if target == base or target.startswith(base + sep):
                return real_open(file, mode, *args, **kwargs)
        raise PermissionError(
            'Access denied: path is outside allowed directories'
        )

    safe['open'] = _safe_open

    # --- final_answer -------------------------------------------------------
    answer_box: list[str | None] = [None]

    def _final_answer(answer):
        answer_box[0] = str(answer) if not isinstance(answer, str) else answer

    safe['final_answer'] = _final_answer

    return safe, answer_box


# ---------------------------------------------------------------------------
# Execution core
# ---------------------------------------------------------------------------

def _run(code: str, allowed_modules: list[str], allowed_dirs: list[str],
         max_memory: int, max_cpu: int, max_output: int,
         ) -> tuple[str, str | None, str | None]:
    """Execute *code* in a restricted namespace.

    Returns ``(observation, final_answer, error)``.
    """

    # 1. Resource limits (before any user code)
    _apply_resource_limits(max_memory, max_cpu)

    # 2. Pre-import allowed modules so their internal dependencies load
    allowed_set = frozenset(allowed_modules)
    for name in sorted(allowed_set):
        try:
            __import__(name)
        except ImportError:
            pass

    preloaded = frozenset(sys.modules.keys())

    # 3. Meta-path hook (backup defense for lazy library imports)
    _install_import_hook(allowed_set, preloaded)

    # 4. Restricted builtins
    import builtins as _builtins
    safe, answer_box = _build_safe_builtins(
        allowed_set,
        tuple(allowed_dirs),
        _builtins.__import__,
        _builtins.open,
    )

    # 5. Redirect stdout/stderr
    out = io.StringIO()
    err = io.StringIO()
    old_stdout, old_stderr = sys.stdout, sys.stderr
    sys.stdout = out
    sys.stderr = err

    # 6. Execute
    error_msg: str | None = None
    try:
        compiled = compile(code, '<sandbox>', 'exec')
        exec(compiled, {'__builtins__': safe, '__name__': '__sandbox__'})
    except SystemExit as exc:
        error_msg = f'SystemExit: {exc.code}'
    except KeyboardInterrupt:
        error_msg = 'KeyboardInterrupt'
    except MemoryError:
        error_msg = 'MemoryError: execution exceeded memory limit'
    except Exception as exc:
        error_msg = f'{type(exc).__name__}: {exc}'
    except BaseException as exc:
        error_msg = f'{type(exc).__name__}: {exc}'
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr

    # 7. Build observation
    stdout_text = out.getvalue()
    stderr_text = err.getvalue()
    if len(stdout_text) > max_output:
        stdout_text = stdout_text[:max_output] + '\n[output truncated]'
    if len(stderr_text) > max_output:
        stderr_text = stderr_text[:max_output] + '\n[output truncated]'

    parts: list[str] = []
    if stdout_text:
        parts.append(stdout_text)
    if stderr_text:
        parts.append(stderr_text)
    if error_msg:
        parts.append(error_msg)
    observation = '\n'.join(parts) if parts else '(no output)'

    return observation, answer_box[0], error_msg


# ---------------------------------------------------------------------------
# Result writer
# ---------------------------------------------------------------------------

def _write_result(path: str, observation: str,
                  final_answer: str | None, error: str | None) -> None:
    with open(path, 'w') as fh:
        json.dump({
            'observation': observation,
            'final_answer': final_answer,
            'error': error,
        }, fh)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(2)
    result_path: str = sys.argv[1]

    # Read request
    try:
        payload = sys.stdin.buffer.read()
        request = json.loads(payload)
    except Exception:
        _write_result(result_path, '(invalid request)', None, 'invalid_request')
        return

    code: str = request.get('code', '')
    if not isinstance(code, str) or not code.strip():
        _write_result(result_path, '(empty code)', None, 'empty_code')
        return

    # Syntax check before execution
    try:
        ast.parse(code)
    except SyntaxError as exc:
        msg = f'SyntaxError: {exc}'
        _write_result(result_path, msg, None, msg)
        return

    observation, final, error = _run(
        code=code,
        allowed_modules=request.get('allowed_modules', []),
        allowed_dirs=request.get('allowed_directories', []),
        max_memory=request.get('max_memory_bytes', 0),
        max_cpu=request.get('max_cpu_seconds', 0),
        max_output=request.get('max_output_bytes', _MAX_OUTPUT),
    )
    _write_result(result_path, observation, final, error)


if __name__ == '__main__':
    main()
