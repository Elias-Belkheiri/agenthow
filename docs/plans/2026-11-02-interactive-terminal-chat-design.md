# Interactive Terminal Chat Design

**Date:** 2026-11-02
**Status:** Approved for planning

## Goal

Make Agent Smith usable as an interactive terminal chat coding agent, with read-only workspace access in this first version.

## Current state

`student/agent_chat.py` runs one task and exits. `ReadOnlyChatRunner.run()` starts a fresh message history for each task, though it can already conduct multiple model/tool turns within a task. The existing providers and read-only tools are working, and a live Groq tool round trip was verified. The project scope still centers benchmark evaluation and does not yet describe interactive coding chat as the main product.

## Approach and alternatives

1. **Extend the existing CLI with a REPL (recommended):** keep `--task` for one-shot use; when absent, enter a prompt loop. Reuse provider setup, dispatcher, and model orchestration. This adds the desired workflow without duplicate setup.
2. Add a separate REPL command: clean separation but duplicates startup/configuration and leaves the existing entry point as the more obvious default.
3. Build a full-screen TUI: can provide richer interaction, but adds dependencies and UI behavior before the basic chat workflow is validated.

## Design

- `uv run python agent_chat.py ...` with no `--task` starts an interactive prompt loop.
- `--task` remains a one-shot mode for scripting and smoke tests.
- Keep one in-memory conversation history for the REPL session. Each user prompt, assistant response, read-only tool request, and actual tool observation is appended in order and passed to subsequent model calls.
- Add `/help`, `/reset`, and `/exit` commands. EOF and Ctrl-C exit cleanly. Blank input is ignored.
- Reuse one configured provider client and one workspace-scoped read-only dispatcher during the session.
- Only `read_file`, `list_files`, and `search_code` are available. Do not add file writes, shell execution, `run_tests`, generated-code execution, or sandbox claims.
- The terminal displays final assistant answers and concise status/errors. Existing structured logs stay on stderr and must not include API keys or full workspace contents.
- Keep API-key loading from environment/`.env`, workspace selection, model/provider configuration, and provider URL behavior unchanged.

## History/budget behavior

History persists until `/reset` or process exit. Each model/tool interaction is still bounded by current iteration, output, time, and input preflight limits. Usage totals should be session-wide (not reset for every user prompt), or the terminal should clearly report a limit and ask the user to reset/start a new session; do not silently discard prior history. When a session approaches the current budget, show a clear message and allow `/reset` to start fresh.

## Verification

- Unit-test prompt-loop input, blank lines, EOF, `/help`, `/reset`, and `/exit` without a live provider.
- Test multi-prompt context retention and reset behavior using a fake model; assert actual tool observations remain in history and no executor/write/shell paths are reachable.
- Preserve one-shot CLI tests and provider tests.
- Run a short live session against Groq with two user prompts, a read-only tool call, and a follow-up that depends on earlier context. Confirm responses and reset/exit behavior. Never print the key.
- Run the full suite; record any unrelated existing test failures separately.

## Deferred

File editing, command/test execution, generated-code sandboxing, MCP, streaming responses, persistent/on-disk history, full-screen TUI, benchmark CLI integration, and SWE-bench remain out of scope for this read-only interactive milestone.
