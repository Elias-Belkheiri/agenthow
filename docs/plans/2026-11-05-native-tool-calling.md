# Native Tool Calling Implementation Plan

> Execute directly in this session, test-first, with verified incremental commits.

**Goal:** Make chat invoke native provider tools rather than parsing actions from answer text.

**Architecture:** Extend the existing model port with optional tools and normalized call metadata. Retain trusted dispatchers and native confinement; migrate only chat history/protocol. Benchmark generation retains its text contract.

**Tech Stack:** Python 3.10, pinned LiteLLM, existing HTTPX clients, pytest.

### Task 1: Native provider contract
- Add failing provider tests in `student/tests/providers/test_native_tools.py` for Groq/Gemini tool schemas, call-only replies, plain answers, linked history, invalid metadata, and single-attempt unsupported-tool errors.
- Extend `student/src/agent_smith/loop/runner.py` with `ToolCall` and backward-compatible `ModelResponse.tool_calls`; add optional tools to the provider method and protocol.
- Modify `student/src/agent_smith/providers/litellm_client.py` to pass tools, omit JSON response format in native mode, preserve structured history, and normalize tool calls.
- Run provider and benchmark loop tests, inspect/stage explicit paths and commit.

### Task 2: Native chat and regression migration
- Add failing native chat tests for sequential batches, invalid arguments/IDs, inert JSON text, budgets, and failed mutation history.
- Add capability schemas under `student/src/agent_smith/tools/`; expose exact definitions on both dispatchers without changing execution policies.
- Replace JSON-action parsing in `student/src/agent_smith/loop/chat.py` with validated native calls, assistant/tool history and plain final text. Disable chat JSON mode in `student/agent_chat.py`.
- Migrate existing fake-model and HTTP integration fixtures to native calls; preserve all existing safety assertions.
- Update `student/README.md` and the public Seatbelt contract for the new chat protocol.
- Run affected tests, isolated 10-file suite and full suite; record baseline failures honestly. Verify a harmless live Groq listing. Inspect diff/staged diff and commit the coherent migration.

## Verification commands

From `student/`: `.venv/bin/python -m pytest -q tests/providers tests/loop tests/test_chat_cli.py tests/test_chat_repl.py tests/integration` then `.venv/bin/python -m pytest -q`. The README isolated command remains available if dependency imports block collection. No dependencies or global settings are to be changed.
