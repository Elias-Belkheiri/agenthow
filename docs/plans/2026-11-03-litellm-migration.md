# LiteLLM Migration Implementation Plan

> Execute this plan task-by-task in the current session with verification checkpoints.

**Goal:** Replace manual Groq/Gemini model transport with a verified LiteLLM client.

**Architecture:** Preserve `ModelClient` and `ModelResponse` and implement one SDK
adapter selected by the factory. Python loops retain tool execution, history,
usage budgets, and task control. Provider wire translation belongs to LiteLLM.

**Tech Stack:** Python 3.10, uv, LiteLLM Python SDK, HTTPX mocked transport, pytest.

---

### Task 1: Verify and lock the SDK dependency

**Files:** `student/pyproject.toml`, `student/uv.lock`.

1. Inspect PyPI release metadata for Python 3.10 and macOS/Linux wheel support.
2. Pin the selected stable release with `uv add 'litellm==<verified-version>'`.
3. Verify import using `uv run python -c 'import litellm'`.
4. Inspect the installed provider transformations for usage semantics, URL handling,
   logging controls, and retries before implementing the adapter.

### Task 2: Replace provider implementation using test-first checks

**Files:** create `student/src/agent_smith/providers/litellm_client.py`, modify
`student/src/agent_smith/providers/factory.py`, retire `providers/groq.py` and
`providers/gemini.py`. Migrate `student/tests/providers/`.

1. Add factory tests demonstrating both providers use one client; run them and
   confirm failure against the old factory.
2. Add HTTP-boundary tests using the real SDK and mocked transport. Cover both
   response formats, JSON mode, URL overrides, system messages, token limits,
   errors, timeouts, one attempt, and credential-safe application logs.
3. Implement `generate()` with explicit provider and key, `num_retries=0`,
   `max_retries=0`, output limit, timeout, and optional JSON response format.
4. Validate input/completion/reasoning usage; return completion-minus-reasoning as
   `output_tokens`, and reasoning as `reasoning_tokens`.
5. Run `uv run python -m pytest tests/providers -q` until green.

### Task 3: Preserve end-to-end chat behavior

**Files:** `student/tests/integration/test_model_tool_chat.py`, CLI tests as needed.

1. Migrate the mocked provider/tool integration test to the new SDK client.
2. Run `uv run python -m pytest tests/integration tests/loop tests/test_chat_cli.py tests/test_chat_repl.py -q`.
3. Assert actual file observations reach the next request and persist into the
   following user prompt; usage includes reasoning once.

### Task 4: Documentation and final verification

**Files:** `student/README.md`, `docs/specs/0001-stack-and-architecture.md`,
`docs/scope/scope.md`.

1. Document installation, SDK transport boundary, URL overrides, and usage policy.
2. Run `uv run python -m pytest -q`, compare failures with the recorded baseline.
3. Run `uv run python agent_chat.py --help` and a bounded live chat if available.
4. Review changed files and report verification evidence and known baseline failure.

This workspace is not a Git repository, so worktree and commit steps do not apply.

## Completed implementation

All four tasks are complete. Pinned LiteLLM 1.106.6 and declared OpenAI as a direct
dependency for configuring the SDK's Groq transport. Added HTTP policy hooks after
review demonstrated that SDK retry flags alone do not prevent message repair.
The tests mock `httpx.HTTPTransport.handle_request`, retaining real redirect and
event-hook behavior. See the design document for final test and live-smoke evidence.
