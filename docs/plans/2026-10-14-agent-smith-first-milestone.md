# Agent Smith First Milestone Implementation Plan

> Follow this plan in order. There is no Git repository in the current working directory, so do not pretend to commit steps. Initialize or connect the intended repository separately before using commit checkpoints.

**Goal:** Prove that independent tools, a real observation loop, and a restricted sandbox work together on one MBPP task without ever executing model generated code outside the sandbox.

**Architecture:** Implement ordinary Python tool functions first, with a configured workspace boundary and an injected executor for `run_tests`. Build the agent loop against fake model and executor ports so it cannot run generated code. Build and security test the sandbox, then implement Groq and Gemini adapters against mocked HTTP responses. Finally connect the parts for an MBPP fixture with a fake model and real sandbox observations. MCP stays out of this milestone; later MCP wrappers should call the same tested tool functions.

**Tech stack:** Python 3.10, uv, pytest, Pydantic v2 at external JSON boundaries, synchronous HTTPX for Groq and Gemini adapters. No agent orchestration library.

**Source of truth:** `agentsmith.pdf`, `docs/scope/scope.md`, `docs/specs/0001-stack-and-architecture.md`. Exact sandbox policy and MBPP behavior need their own design specs before implementation; this plan names the seams and tests, not permission to weaken security requirements.

## Picture of the first milestone

```text
Task fixture ──> fake model ──> agent loop ──> tool dispatcher
                                  │               ├── read_file
                                  │               ├── list_files
                                  │               ├── search_code
                                  │               └── run_tests(fake executor)
                                  └<── real observations

                    NO generated code runs yet

After sandbox security tests pass:
Groq or Gemini ──> shared model client ──> loop ──> sandbox process
                       (mocked HTTP tests)       └──> allowed tools
                           actual observations <────── test results
```

The diagram's first line is a unit test flow. A fake executor records what was requested and returns a fixed result. It does **not** call `exec`, `eval`, or a shell. The final line is a later integration test, after the sandbox blocks unsafe operations.

## Completion gates

1. All tools can be called directly without the loop and have deterministic unit tests.
2. The loop can complete and fail against fake components, including malformed output and limits, without running generated code.
3. The sandbox has automated tests for imports, builtins, paths, symlinks, network, timeout, memory, cleanup, and allowed behavior. No claim of security relies on a subprocess alone.
4. Groq and Gemini adapters pass independent mocked HTTP tests with one attempt per request, no key rotation or provider fallback by default, and no secrets in logs.
5. Only after gate 3, connect a real executor and prove one MBPP fixture with a fake model and actual sandbox observations. A live provider run on one dumped MBPP task is an optional follow on when credentials and evaluation tools are available. Do not claim the exam pass threshold from one task.

## Task 0: Runnable project skeleton

**Files:** `student/pyproject.toml`, `student/src/agent_smith/__init__.py`, `student/tests/conftest.py`. Put the package under `student/` because the PDF runs the evaluation CLI from there. Reserve `student/agent_mbpp.py` as the later exam entry point. Keep root `docs/` as planning artifacts.

1. Write a failing smoke test in `student/tests/test_package.py` that imports `agent_smith` and checks its package version or marker.
2. Run `cd student && uv run pytest tests/test_package.py -q`. Expect failure before the package exists.
3. Add the minimal uv project with Python 3.10 target and pytest dev dependency; use source packages for `tools`, `loop`, and `sandbox`. Do not install every future dependency now.
4. Re-run the test. Expect pass. Check `uv run python --version` reports Python 3.10. Document environment setup if that interpreter is unavailable.

## Task 1: Shared tool boundary and result contract

**Files:** `student/src/agent_smith/tools/contracts.py`, `student/tests/tools/test_contracts.py`.

1. Test that `ToolContext(workspace_root=...)` resolves a contained path and rejects an absolute path outside the root, `../` traversal, and symlinks escaping the root. The root is a configured task workspace, initially `/testbed` in evaluation and a temporary directory in tests. Do not follow the entire host filesystem.
2. Test that each tool returns a bounded success or explicit error result with enough information to distinguish invalid input, missing file, denied path, and truncation. Do not silently return an empty string for an error.
3. Implement the minimal path resolver and result types. Avoid relying on a simple string prefix check: `/testbed-elsewhere` must not count as within `/testbed`.
4. Run `cd student && uv run pytest tests/tools/test_contracts.py -q`. Expect pass.

Illustration: with workspace `/testbed`, `src/a.py` can resolve to `/testbed/src/a.py`; `../../private.txt` and a symlink to `/private.txt` must fail with `path_denied`.

## Task 2: Direct file tools

**Files:** `student/src/agent_smith/tools/files.py`, `student/tests/tools/test_files.py`.

1. Write failing tests for `read_file(filepath, start_line, end_line)` using inclusive line numbers and `N: content` lines; test invalid ranges, a missing file, encoding errors, oversized files, and outside root.
2. Write failing tests for `list_files(directory, pattern)` that only returns paths from the configured workspace, in stable order, with bounded output. Test glob patterns, no matches, symlink escapes, and invalid directory.
3. Implement these as ordinary Python functions accepting a trusted `ToolContext`; keep their public argument names compatible with the PDF. No model or MCP client is needed.
4. Run `cd student && uv run pytest tests/tools/test_files.py -q`. Expect pass.

Illustration: a file containing `alpha\nbeta\n` read from lines 1 through 2 returns `1: alpha\n2: beta`, not bare text without numbers.

## Task 3: Direct code search

**Files:** `student/src/agent_smith/tools/search.py`, `student/tests/tools/test_search.py`.

1. Write failing tests for `search_code(pattern, file_pattern)` across two files. Expect `/absolute/path.py:line content`, matching the PDF's output shape.
2. Test no matches, invalid pattern, binary content, long lines, huge search results, and symlinks outside the workspace. Make truncated output explicit and deterministic.
3. Implement scoped grep like search using Python libraries; never launch a shell command for this milestone.
4. Run `cd student && uv run pytest tests/tools/test_search.py -q`. Expect pass.

Illustration: searching `def solve` with `*.py` finds the exact matching line and its absolute workspace path, not a guessed symbol definition.

## Task 4: MBPP run_tests contract with a fake executor

**Files:** `student/src/agent_smith/tools/mbpp.py`, `student/tests/tools/test_mbpp.py`.

1. Define a small executor interface taking candidate code, test imports, and test expressions, and returning bounded stdout, stderr, pass counts, and failure details. The implementation used in this task is a fake; it never executes candidate code.
2. Write failing tests proving `run_tests()` forwards the exact candidate and test data, returns actual fake executor results, handles a fake timeout, and does not mark failed tests as passed.
3. Implement `run_tests` by injecting the executor, without `exec`, `eval`, shell, or MCP. Keep it independent of the agent loop.
4. Run `cd student && uv run pytest tests/tools/test_mbpp.py -q`. Expect pass.

Illustration: the fake executor returns `passed=2, failed=1` and a failed assertion. `run_tests` reports exactly that, rather than interpreting the model's text as an observation. Real tests wait until the sandbox is ready.

## Task 5: Define the loop contract and extraction rules

**Files:** `student/src/agent_smith/loop/contracts.py`, `student/src/agent_smith/loop/extract.py`, `student/tests/loop/test_extract.py`.

1. Design the MBPP message and stop conditions against the PDF. Keep `final_answer(answer_string)` as a built in execution namespace function, never a tool provided by MCP.
2. Write failing tests for valid Python fenced code, no code, malformed code, and a response that falsely includes an Observation. Extract an action only; never trust claimed execution results.
3. Implement the smallest extractor that passes the tests. Document later XML, JSON/Hermes, and ReAct conversion in a follow on ticket instead of silently treating them as Python.
4. Run `cd student && uv run pytest tests/loop/test_extract.py -q`. Expect pass.

## Task 6: Build loop against fakes, with budgets

**Files:** `student/src/agent_smith/loop/runner.py`, `student/tests/loop/test_runner.py`.

1. Write a test with a fake model that first asks for a tool call, then calls `final_answer`; a fake executor supplies the observation. Assert the next model input contains the **real fake executor output**, not a model invented observation.
2. Write tests for missing code, tool errors, model errors, cancellation, and exhausted cumulative limits: 10 iterations, 6,000 input tokens, 1,500 output tokens including reasoning tokens, 120 seconds overall. In exam mode, one provider request attempt means no retry or fallback.
3. Implement the loop with injected `ModelClient`, `Executor`, clock, and usage counter. It must not directly invoke generated code or a shell. Persist real per step response, sent code, observation, and counts for eventual SolutionOutput.
4. Run `cd student && uv run pytest tests/loop -q`. Expect pass; assert that no test in this phase instantiates a real generated code executor.

Illustration:
```text
step 1 model:  print(run_tests())
step 1 executor: 1 test failed: expected 2, got 3
step 2 model:  final_answer("def solve(x): return x + 1")
result: final answer captured, 2 iterations
```
The model does not get to supply the `1 test failed` line. The executor does.

## Task 7: Specify and build the sandbox

**Files:** `docs/specs/0002-minimum-restricted-sandbox.md` (number is illustrative; use the next free number), `student/src/agent_smith/sandbox/worker.py`, `student/src/agent_smith/sandbox/runner.py`, `student/tests/sandbox/test_isolation.py`.

1. Design the threat boundary before implementation: allowed imports, path list, restricted builtins, no network, time and memory ceilings, process cleanup, allowed scratch area, and what standard Python alone cannot guarantee. Obtain a review of this decision. Do not confuse path checking in tools with isolation of arbitrary Python code.
2. Write failing isolation tests: import of an unauthorized module; `open` outside roots and symlink escape; direct socket; dangerous builtin; infinite loop; excessive memory; `SystemExit` and `KeyboardInterrupt`; ordinary allowed math and file access.
3. Implement a separate child process with restricted execution namespace, minimal allowlists, operating system enforcement where available, output bounds and hard termination. Test across the actual evaluation OS. Do not label the sandbox secure until escape tests and the PDF's security checks pass. If standard library controls cannot enforce no network or file access for arbitrary code, stop and escalate the design rather than claiming a false boundary.
4. Run `cd student && uv run pytest tests/sandbox -q`. Expect pass for both allowed and blocked cases; verify parent survives worker death.

## Task 8: Build basic Groq and Gemini model adapters

**Files:** `student/src/agent_smith/providers/contracts.py`, `student/src/agent_smith/providers/groq.py`, `student/src/agent_smith/providers/gemini.py`, `student/tests/providers/test_groq.py`, `student/tests/providers/test_gemini.py`.

1. Write failing tests against HTTPX MockTransport for each provider's request endpoint, auth header, messages and model body. Groq and Gemini use different HTTP shapes, but both return the shared model response with actual text, usage and provider metadata.
2. Add tests for `GROQ_API_KEY`, `GEMINI_API_KEY`, numbered key environment variables, configured `--provider-url` and model, missing credentials, timeout, rate limit, provider error and malformed JSON. Assert the key never appears in logs or error messages and that every call makes exactly one HTTP attempt by default.
3. Implement one shared client contract and two small synchronous HTTPX adapters with explicit timeouts and client cleanup. Classify provider availability errors separately from invalid requests and malformed model output. Do not implement rotation or fallback yet; that remains ELI-23.
4. Run `cd student && uv run pytest tests/providers -q`. Expect pass without real credentials or network access. Live provider requests come only after sandbox integration is proven.

Illustration: the shared call `generate(messages, model, provider_url)` yields `text` and `usage`. A mocked Groq reply and a mocked Gemini reply become the same result type. A mocked `429` becomes `provider_rate_limited`, not a second request.

## Task 9: Connect and prove the whole path

**Files:** `student/src/agent_smith/tools/mbpp.py`, `student/src/agent_smith/loop/runner.py`, `student/tests/integration/test_mbpp_path.py`. The evaluator CLI in `student/agent_mbpp.py` belongs to Release 1 after this gate.

1. Connect `run_tests` to the real restricted executor only after Task 7 passes. The sandbox supplies `final_answer` itself; tools remain plain Python for now. Generated code has no direct access to unrestricted filesystem, network, or process facilities.
2. Test one integration fixture with a fake model and real sandbox. Capture the sent code, actual observation, and final answer. Do not call the fake executor in this integration test.
3. Run `cd student && uv run pytest tests/integration/test_mbpp_path.py -q`. Expect the real sandbox to execute the candidate in the child process and the fake model to consume its actual observation.
4. Record what failed or is deferred: the live provider and evaluator CLI, remaining extraction formats, full MCP transport, `edit_file`, `run_command`, SWE bench tools, and the five model benchmark. Release 1 adds the compatible CLI and validates dumped tasks under real evaluation limits. Provider adapters from Task 8 may only drive a real generated code run after this sandbox integration gate.

## Later changes, not permission to skip requirements

The file tool workspace is a first milestone rule. Full SandboxConfig later supports several `allowed_directories` such as `/testbed` and `/tmp/agent`, as the PDF requires. MCP later exposes the independently tested tools as callable wrappers over stdio and streamable HTTP, dynamically documents unknown servers, and keeps `final_answer` built in. Any change to the security boundary needs design review and regression tests.
