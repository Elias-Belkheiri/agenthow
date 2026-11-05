# 0002. Minimum restricted sandbox

**Date**: 2026-10-15
**Status**: In Progress (legacy MBPP executor)

This spec describes the legacy restricted-Python MBPP executor, **not** the
workspace chat backend. Native chat tools are documented separately in
[0003](0003-seatbelt-workspace-tools.md); that implementation neither hardens nor
replaces this executor. The limitations below remain legacy constraints.

## Summary

Execute model-generated Python code in a subprocess with restricted imports,
restricted builtins, file access controls, resource limits, and output bounds.
The sandbox implements the `Executor` protocol from `agent_smith.loop.runner`
and communicates with the parent via stdin (request) and a temp file (result).

## Threat model

The sandbox defends against **accidental harm** from LLM-generated code solving
MBPP tasks: runaway loops, excessive memory, unintended file access, and
accidental network use. It does **not** claim to defend against a determined
adversary who deliberately crafts sandbox escapes.

### What the sandbox enforces

| Control | Mechanism |
|---------|-----------|
| Import restriction | Custom `__import__` in restricted builtins + `sys.meta_path` hook |
| Builtins restriction | Restricted `__builtins__` dict in `exec()` globals |
| File access | Restricted `open()` with `realpath` resolution and directory allowlist |
| Network blocking | Import restriction blocks `socket`, `http`, `urllib`, etc. |
| CPU time | `RLIMIT_CPU` (where OS supports it) |
| Memory | `RLIMIT_AS` (Linux; unreliable on macOS) |
| Wall-clock timeout | Parent kills child via `subprocess.Popen.kill()` |
| Output bounds | Truncation of captured stdout/stderr |
| Process isolation | Separate subprocess via `subprocess.Popen` |

### What standard Python cannot guarantee

- **Type hierarchy escapes**: `object.__subclasses__()`, `func.__globals__`,
  `cell.cell_contents` can reach restricted objects. This is a fundamental
  CPython limitation.
- **Memory limits on macOS**: `RLIMIT_AS` is not reliably enforced.
- **OS-level network blocking**: Import restriction blocks the `socket` module
  but cannot prevent raw syscalls via the type hierarchy.
- **File descriptor escapes**: Restricting `open()` does not prevent low-level
  fd operations reachable through the type hierarchy.

Full isolation requires OS-level controls (containers, seccomp, namespaces).
The later full sandbox spec must address these gaps.

## Allowed modules (MBPP)

Pure computation modules only — no I/O, network, process, or system access:

`math`, `cmath`, `decimal`, `fractions`, `statistics`, `numbers`,
`collections`, `heapq`, `bisect`, `array`, `queue`,
`itertools`, `functools`, `operator`,
`string`, `re`,
`copy`, `types`, `typing`, `dataclasses`, `abc`, `enum`,
`datetime`, `calendar`,
`io`, `json`, `hashlib`, `random`,
`textwrap`, `contextlib`, `warnings`, `struct`

Explicitly blocked: `os`, `sys`, `subprocess`, `shutil`, `socket`, `http`,
`urllib`, `ctypes`, `importlib`, `pickle`, `threading`, `multiprocessing`,
`signal`, `code`, `time`, `tempfile`, `pathlib`, `glob`.

## Blocked builtins

`exec`, `eval`, `compile`, `breakpoint`, `exit`, `quit`, `help`, `input`,
`credits`, `copyright`, `license`.

`open` is replaced with a restricted version. `__import__` is replaced with a
restricted version. `final_answer` is injected as a namespace function.

## Communication protocol

1. Parent creates a temp file path for the result.
2. Parent spawns: `python -u -I worker.py <result_path>`
3. Parent writes JSON request to child stdin, closes stdin.
4. Child sets resource limits, installs restrictions, executes code.
5. Child writes JSON result to the result file.
6. Parent reads result file, deletes it, returns `ExecutionResult`.
7. If the child exceeds the timeout, parent kills it and returns timeout error.

## Consequences

**Positive**: Generated code runs in a separate process with restricted
namespace. Accidental import of `os`, `subprocess`, or `socket` is blocked.
Resource limits prevent runaway CPU and memory. The parent always survives.

**Negative**: Python-level restrictions can be bypassed through CPython
internals. Memory limits are unreliable on macOS. The sandbox adds subprocess
overhead (~100-300ms per execution).

## References

- `agentsmith.pdf`, sandbox and security requirements.
- `docs/plans/2026-10-14-agent-smith-first-milestone.md`, Task 7.
- CPython import system: https://docs.python.org/3.10/reference/import.html
- `resource` module: https://docs.python.org/3.10/library/resource.html
