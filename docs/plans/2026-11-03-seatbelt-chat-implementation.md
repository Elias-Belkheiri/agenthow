# Seatbelt Workspace Chat Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Enable directly applied workspace edits and individually approved shell commands inside a fail-closed macOS sandbox.

**Architecture:** A trusted parent approves commands and invokes a backend-neutral executor. Seatbelt confines all tool operations in fresh workers with a private runtime, minimal environment, bounded IPC, and explicit lifecycle management. Preserve an explicitly selected read-only mode, never an automatic fallback.

**Tech Stack:** Python 3.10, standard-library subprocess/selectors, macOS sandbox-exec/Seatbelt, pytest.

---

Read `docs/plans/2026-11-03-seatbelt-chat-design.md` first. Use @test-driven-development and @verification-before-completion. Execute each task as failing tests, verification of expected failure, minimal implementation, and passing tests. There is no Git repository: omit commits/worktrees rather than initializing one without permission.

### Task 1: Establish baseline and validate Seatbelt feasibility

**Files:**
- Read: `student/src/agent_smith/sandbox/executor.py`, `worker.py`, `policy.py`
- Create: `student/tests/sandbox/test_seatbelt_integration.py`
- Create: `student/src/agent_smith/sandbox/seatbelt.py`

1. Run `cd student && uv run python -m pytest -q`; record existing failures without silently fixing unrelated fixtures.
2. Add a Darwin-only integration test that invokes `/usr/bin/sandbox-exec` with default deny and the minimum runtime policy needed for `/bin/sh` and the actual Python executable.
3. Assert ordinary scratch writes succeed while reads/writes outside allowed roots and outbound/loopback networking fail. Include workspace paths with spaces, quotes, parentheses, and newlines.
4. Run `cd student && uv run python -m pytest tests/sandbox/test_seatbelt_integration.py -v`; first establish missing implementation failure, then implement parameterized profile construction.
5. Test the actual uv-managed interpreter, its stdlib/shared libraries, and isolated startup. Do not grant broad home, `/Users`, or all-files read permission to make startup work.
6. Validate protected subtree rename/delete, symlink and known hard-link cases, UNIX sockets, Mach-service restrictions, and detached-child cleanup feasibility. If required invariants cannot be enforced, stop and revise the design rather than ship weaker claims.

### Task 2: Define policy and backend contracts

**Files:**
- Create: `student/src/agent_smith/sandbox/workspace_policy.py`
- Create: `student/src/agent_smith/sandbox/backend.py`
- Create: `student/tests/sandbox/test_workspace_policy.py`

1. Test immutable policy construction, canonical roots, safe limits, unsupported platform errors, and invalid workspace/runtime overlaps.
2. Define a process backend protocol and structured failures (`sandbox_unavailable`, `sandbox_setup_failed`, `timeout`, `output_limit`, `cancelled`). Keep these separate from the legacy MBPP policy.
3. Define constants for command, request, result, and output sizes. Reject booleans masquerading as numeric limits, negative limits, NUL paths, filesystem root, home root, credential roots, and writable interpreter/runtime overlaps.
4. Specify exact credential path patterns including nested `.env` files; allow `.env.example`/`.env.sample` only through explicit tested rules. Protect `.git` writes. Document unsupported secret names.
5. Run `cd student && uv run python -m pytest tests/sandbox/test_workspace_policy.py -v`.

### Task 3: Implement confined launch and bounded process supervision

**Files:**
- Modify: `student/src/agent_smith/sandbox/seatbelt.py`
- Create: `student/src/agent_smith/sandbox/process.py`
- Create: `student/tests/sandbox/test_process.py`
- Extend: `student/tests/sandbox/test_seatbelt_integration.py`

1. Test that the launcher never calls unwrapped Python/shell after sandbox failure; unsupported macOS setup is an explicit error.
2. Add a startup enforcement probe before accepting sandbox-enabled chat. Test a deliberately invalid profile.
3. Stage the trusted worker runtime outside workspace writes; use isolated interpreter startup. Build an environment from scratch and close unrelated descriptors. Private HOME/TMPDIR must not expose parent secrets.
4. Use selectors/nonblocking reads to drain separate control/output channels under byte caps and monotonic deadlines. Bound requests before launch and validate bounded JSON responses.
5. Add output-flood, binary-output, oversized-result, malformed-result, timeout, interrupt, spawn-failure, and leaked-descriptor tests.
6. Supervise process groups on normal and abnormal exits. Verify background descendants and detached-child attempts with real process checks; cleanup test processes in test finally blocks. Resolve the feasibility gate from Task 1 before claiming complete descendant cleanup.
7. Run `cd student && uv run python -m pytest tests/sandbox/test_process.py tests/sandbox/test_seatbelt_integration.py -v`.

### Task 4: Add sandboxed workspace operations

**Files:**
- Create: `student/src/agent_smith/sandbox/tool_worker.py`
- Create: `student/src/agent_smith/tools/workspace.py`
- Create: `student/tests/tools/test_workspace.py`
- Read/reuse: `student/src/agent_smith/tools/files.py`, `search.py`, `contracts.py`

1. Write strict-schema tests for existing read/list/search plus `run_shell(command)`, `write_file(filepath, content)`, and `edit_file(filepath, old_text, new_text)`.
2. Implement fresh confined worker dispatch for every operation, including reads; no parent-side file-tool fallback in sandboxed mode.
3. Execute shell strings through a fixed `/bin/sh -c` with no login/startup files, canonical workspace cwd, and the minimal environment. Never embed command text in additional shell syntax.
4. Implement bounded UTF-8 structured writes and exact-one-match edits inside the worker. Reject symlink/nonregular targets as appropriate and enforce OS confinement independently of Python path checks. Use atomic writes where supported and preserve existing file modes.
5. Test outside paths, symlink swaps, protected files, missing/multiple edit matches, Unicode byte caps, permissions, and read/search secret leakage.
6. Run `cd student && uv run python -m pytest tests/tools/test_workspace.py -v` and the real Seatbelt tests.

### Task 5: Place shell approval exclusively in trusted code

**Files:**
- Create: `student/src/agent_smith/tools/approval.py`
- Modify: `student/src/agent_smith/tools/workspace.py`
- Create: `student/tests/tools/test_approval.py`

1. Define a parent-side approval callback, default deny. An approved call binds the exact immutable command and canonical cwd; do not return reusable approval tokens to the model.
2. Before spawning shell execution, display a terminal-safe representation of the full command and cwd. Escape control/ANSI characters rather than letting commands alter the prompt display.
3. Accept only explicit affirmative input; EOF, Ctrl-C, no callback, and noninteractive input deny. Each subsequent command prompts again, including identical commands.
4. Test no execution occurs before approval, denial produces a tool observation, model-supplied approval keys are rejected, and edits do not invoke approval.
5. Run `cd student && uv run python -m pytest tests/tools/test_approval.py -v`.

### Task 6: Integrate tool capabilities into bounded chat

**Files:**
- Modify: `student/src/agent_smith/loop/chat.py`
- Modify: `student/src/agent_smith/tools/dispatcher.py`
- Extend: `student/tests/loop/test_chat.py`, `test_session.py`
- Create: `student/tests/integration/test_sandbox_chat.py`

1. Introduce a small dispatcher protocol with capability-specific guidance while retaining legacy read-only APIs/tests.
2. Use sandbox dispatcher guidance in the system message: exact tool schemas, approval semantics, direct edit behavior, no network, no claims of execution before observations.
3. Pass remaining wall-clock budget to dispatch; account for approval time before launching. Do not let a model choose arbitrary timeout/policy/environment parameters.
4. Test fake-model conversations invoking denied shell, approved shell, automatic edit, protected read rejection, and final answer. Verify real observations only, usage retention, reset, failure-history rollback, and absence of duplicate side effects.
5. Run `cd student && uv run python -m pytest tests/loop tests/integration/test_sandbox_chat.py -v`.

### Task 7: CLI wiring and explicit mode selection

**Files:**
- Modify: `student/agent_chat.py`
- Extend: `student/tests/test_chat_cli.py`, `test_chat_repl.py`

1. Add explicit `--tools sandboxed|read-only`; use sandboxed mode for the new enabled workflow and explicitly selected read-only for compatibility. Test unsupported platform never silently downgrades.
2. Initialize and probe confinement before advertising execution capabilities. Keep provider credentials solely in the parent.
3. Wire trusted interactive approval. Deny shell execution in `--task` mode without an interactive approval channel; never consume task stdin as approval and do not add an auto-approve flag.
4. Update banner/help to explain live workspace changes, per-command approvals, denied network, protected paths, and no rollback.
5. Test missing sandbox binary, invalid workspace, setup failure, command denial, Ctrl-C/EOF, and existing REPL session/reset behavior.
6. Run `cd student && uv run python -m pytest tests/test_chat_cli.py tests/test_chat_repl.py -v`.

### Task 8: Adversarial regression suite and documentation

**Files:**
- Extend: `student/tests/sandbox/test_seatbelt_integration.py`
- Modify: `student/README.md`
- Create: `docs/specs/0003-seatbelt-workspace-tools.md`
- Modify: `docs/specs/0002-minimum-restricted-sandbox.md` only to distinguish legacy MBPP restrictions from the new backend

1. Exercise forbidden file access via direct syscalls/ordinary unrestricted Python, not merely blocked builtins. Verify child inheritance, external symlinks, environment key absence, credential protections, .git write denial, network denial, output bounds, cancellation, and cleanup.
2. Test realistic command workflows (listing, Python tests, workspace edits) with all dependencies already installed; explain that dependency downloads require unavailable network access.
3. Document supported macOS versions actually tested, runtime paths allowed, secret patterns, direct-edit risks, hard-link/host-service limitations, memory-limit limitations, and future application-launch risk if project source is edited.
4. Run `cd student && uv run python -m pytest -q`; distinguish pre-existing failures from regressions. Real macOS enforcement tests must run, not all skip.
5. Review all subprocess launches and file-write paths with `rg -n 'Popen|subprocess|write_text|write_bytes|open\\(' student/src student/agent_chat.py`; verify no new unsandboxed tool path or fallback exists.
6. Summarize exact commands/results and outstanding limitations. Do not claim adversarial isolation or complete cleanup without evidence.
