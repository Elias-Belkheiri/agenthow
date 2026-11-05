# Interactive Terminal Chat Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make Agent Smith support an interactive terminal coding chat with read-only workspace tools and persistent in-memory conversation history.

**Architecture:** Retain `--task` as one-shot mode and make absence of `--task` launch a REPL. Add a session owner around the current `ReadOnlyChatRunner` so follow-up user prompts preserve history and session-wide budgets; `/reset` clears both. Keep provider setup and the workspace-scoped read-only dispatcher shared for the session. No generated code, file writes, shell commands, or `run_tests` are allowed.

**Tech Stack:** Python 3.10, existing argparse CLI, custom chat loop, HTTPX providers, pytest.

---

## Constraints and context

- Main entry point: `student/agent_chat.py`.
- Current model/tool orchestration: `student/src/agent_smith/loop/chat.py::ReadOnlyChatRunner`; it currently starts with a new history and counters for every `.run(task)` call.
- Existing provider adapters and read-only tools must remain reusable.
- Retain one-shot `--task` behavior for scripts. With no `--task`, start the REPL.
- REPL history is in-memory only. `/reset` clears the conversation and session usage; `/exit`, EOF, and Ctrl-C exit cleanly.
- Only `read_file`, `list_files`, and `search_code` remain exposed. Do not add execution, edits, shell, test execution, sandbox claims, or persistent history.
- API credentials should continue to load from environment/`.env` safely. Never print or log secrets.
- No `.git` directory exists, so do not attempt commit steps or claim commits.

## Task 1: Create persistent chat session API

**Files:**
- Modify: `student/src/agent_smith/loop/chat.py`
- Test: `student/tests/loop/test_chat.py`

**Step 1: Add failing tests**

Test a session with two user prompts, where the second fake-model request receives the first prompt and assistant final response in its history. Test that a tool call and actual observation from prompt one remain in context on prompt two. Test `/reset`-level session reset clears history and token/request counters. Ensure a session cannot reach any executor or non-read-only tool path.

**Step 2: Verify RED**

Run: `cd student && uv run python -m pytest tests/loop/test_chat.py -q`

Expected: fail because there is no reusable session/history API.

**Step 3: Implement minimal session support**

Add a `ReadOnlyChatSession` (or equivalent) that owns the shared system prompt and message history. Refactor the one-shot runner to operate on supplied session history without changing existing one-shot semantics. Accumulate actual input/output usage and request totals across user prompts; enforce existing cumulative budgets before sending each request. Reset must return the session to its initial system-only state and zero the session counters. Keep per-user-task iteration/time limits bounded.

**Step 4: Verify GREEN**

Run the same test command; expect pass. Re-run existing chat/provider integration tests.

## Task 2: Add interactive REPL to CLI

**Files:**
- Modify: `student/agent_chat.py`
- Test: `student/tests/test_chat_cli.py`

**Step 1: Write failing CLI tests**

Inject input/output streams or an input function. Cover a normal prompt/response, multiple prompts in one session, empty lines ignored, `/help`, `/reset`, `/exit`, EOF, and Ctrl-C. Check `--task` still executes once and exits. Assert reset causes the next model call to omit prior private conversation content while retaining system instructions.

**Step 2: Verify RED**

Run: `cd student && uv run python -m pytest tests/test_chat_cli.py -q`

Expected: fail because the CLI currently requires `--task` and has no REPL.

**Step 3: Implement interactive mode**

Make `--task` optional. Build provider and `ToolContext` once. If task was provided, preserve the one-shot path. Otherwise print a concise welcome/help hint and enter `agent> ` input loop. Dispatch each user line through the same `ReadOnlyChatSession`; print final answers and concise errors to stdout/stderr, continue after recoverable provider/task errors, and keep logs on stderr. Support `/help`, `/reset`, `/exit`, blank lines, EOF, and KeyboardInterrupt as designed. Do not expose additional tools or save transcripts.

**Step 4: Verify GREEN**

Run the CLI test file; expect all tests pass. Run `cd student && uv run python agent_chat.py --help` and verify usage documents both one-shot and interactive behavior.

## Task 3: End-to-end session history tests and safety regression

**Files:**
- Modify: `student/tests/integration/test_model_tool_chat.py`
- Modify: `student/tests/loop/test_chat.py`
- Modify: `student/tests/test_chat_cli.py`

**Step 1: Add mocked multi-prompt integration test**

Use the existing Groq MockTransport pattern with deterministic responses. Drive two user prompts in one session, with an actual `read_file` call and a follow-up question dependent on the prior answer. Assert the second HTTP request contains the full earlier conversation and actual tool observation, with the new prompt last.

**Step 2: Verify expected failure**

Run: `cd student && uv run python -m pytest tests/integration/test_model_tool_chat.py -q`.

Expected: fail until the session-aware path is connected end-to-end.

**Step 3: Verify safety boundaries**

Assert rejected `shell`, write, and `run_tests` actions remain rejected, and no executor is instantiated or invoked by the interactive session. Confirm logging does not contain prompt bodies, workspace file contents, or secrets.

**Step 4: Run targeted suite**

Run: `cd student && uv run python -m pytest tests/loop/test_chat.py tests/test_chat_cli.py tests/integration/test_model_tool_chat.py tests/providers -q`.

Expected: pass.

## Task 4: Docs, live smoke test, and final verification

**Files:**
- Modify: `docs/plans/2026-11-02-interactive-terminal-chat.md`
- Modify: `docs/scope/scope.md` only if appropriate to make the primary product intent explicit; preserve benchmark requirements as future evaluation targets.

**Step 1: Document how to launch**

Document interactive usage from `student/`, one-shot usage with `--task`, provider/model selection, workspace scope, slash commands, and explicit read-only limitations. Do not include credentials.

**Step 2: Run automated checks**

Run: `cd student && uv run python -m pytest -q` and `cd student && uv lock --check`.

Expected: new tests pass; record unrelated existing failures (currently `tests/sandbox/test_isolation.py::test_re_module` has an over-escaped regex fixture) without silently modifying unrelated sandbox tests.

**Step 3: Live smoke test**

Use the available Groq key via `.env` without printing it. Start interactive mode, ask two prompts, ensure one uses a read-only tool and the second depends on prior context, then test `/reset` and `/exit`. Capture only answers, status, HTTP status summaries, and safe metadata. Record result without transcript secrets or key values.

**Step 4: Update TODO/status**

Mark each checklist item, record exact verification output, live-test provider/model and outcome, and list deferred editing/execution/MCP/sandbox functionality.

## TODO checklist

- [x] Add reusable conversation/session state with cumulative usage budgets.
- [x] Add prompt-loop tests for multi-prompt history and reset.
- [x] Make `--task` optional and implement REPL commands `/help`, `/reset`, `/exit`.
- [x] Preserve one-shot CLI behavior and `.env` credential loading.
- [x] Add mocked end-to-end multi-prompt provider/tool test.
- [x] Verify no write, shell, test-running, or generated-code execution capabilities are reachable.
- [x] Run targeted and full test suites; document unrelated failures.
- [x] Run a brief live Groq interactive session and record safe results.
- [x] Document terminal usage and read-only limitations.
- [x] Keep write/execute tools, sandboxing, MCP, and persistent transcripts deferred.

## Review and execution log

- Plan review: preserve per-prompt iteration/time limits but keep cumulative request/token usage per session. Failed prompt/model/tool turns roll back their history; actual consumed usage remains. No Git repository exists, so no commit/worktree steps were attempted.
- Implemented `ReadOnlyChatSession` in `student/src/agent_smith/loop/chat.py`, reusing the existing read-only loop. Added a post-response deadline check. One-shot mode uses a fresh session; the REPL reuses one session until reset/exit.
- Updated `student/agent_chat.py`: optional `--task`, interactive commands, blank-input handling, clean EOF/Ctrl-C exit, recoverable errors, and budget-reset guidance.
- Added session and CLI tests; extended the mocked HTTP integration test to verify a second user prompt sees earlier tool observations and final answer.
- Independent read-only review of session/REPL changes: no issues found. No Git diff baseline was available; review inspected current source directly.
- Live Groq testing initially revealed occasional malformed JSON responses. Added opt-in Groq JSON-object response format for the chat CLI, with a regression test. Generic provider defaults remain unchanged for future non-JSON callers. Gemini still relies on the JSON prompt and may return `invalid_action`, which is handled without a crash.
- Final live smoke test with `qwen/qwen3.8-27b`: read `pyproject.toml`, correctly identified `agent-smith`, remembered the codeword `cobalt` in the next user prompt, then after `/reset` answered that no codeword was provided. All four model requests returned HTTP 200; `/exit` quit cleanly. No secrets were printed. The terminal input was piped through the actual REPL; interactive EOF/Ctrl-C are separately covered by unit tests.
- Usage docs: `student/README.md`. Updated `docs/scope/scope.md` to identify terminal chat as the primary product and preserve benchmark goals separately. Tickets remain in Linear; this file tracks implementation evidence only.
- Remaining backlog: native tool-call protocol/provider compatibility, exact tokenizer-aware budget accounting and larger configurable chat limits, Gemini live verification, streamed/richer UI, secret-file policy, sandboxing, edits/execution, MCP, and benchmark interfaces. Current workspace reads may transmit sensitive files if explicitly selected; README warns users to select trusted workspaces.

### Final verification

- `cd student && uv run python -m pytest -q`: **219 passed, 1 skipped, 1 failed**. Only failure is the unchanged pre-existing `tests/sandbox/test_isolation.py::test_re_module` regex fixture.
- `cd student && uv run python -m pytest -q -k 'not test_re_module'`: **219 passed, 1 skipped, 1 deselected**.
- `cd student && uv lock --check`: passed.
- `cd student && uv run python agent_chat.py --help`: passed; `--task` is optional and documents interactive mode.
