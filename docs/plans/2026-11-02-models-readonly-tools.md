# AI Models and Read-Only Tools Integration Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Integrate Groq and Gemini model calling with the Agent Smith loop and dispatch only read-only workspace tools, then verify a multi-turn chat preserves history and observations.

**Architecture:** Implement provider adapters satisfying the existing `ModelClient` protocol, then add a dispatcher for `read_file`, `list_files`, and `search_code` and connect its trusted outputs to the loop. Never execute model-generated code or expose write, shell, or test-execution tools. Add deterministic mocked tests and optionally run a short live provider chat when credentials/network are available.

**Tech Stack:** Python 3.10, uv, HTTPX, Pydantic v2 (only at external data boundaries), pytest, existing custom loop.

---

## Context and constraints

- Existing loop: `student/src/agent_smith/loop/runner.py`; it expects a `ModelClient.generate(...) -> ModelResponse` and `Executor.execute(...) -> ExecutionResult`.
- Existing read-only functions: `student/src/agent_smith/tools/files.py` (`read_file`, `list_files`) and `student/src/agent_smith/tools/search.py` (`search_code`), each accepting a `ToolContext`.
- Existing `student/src/agent_smith/tools/mbpp.py::run_tests` is intentionally excluded because it can route toward code execution.
- Current `LoopRunner` extracts Python code and passes it to an Executor. For this feature, add/define an explicit, safe tool-action protocol and do not route arbitrary extracted code into `SubprocessExecutor`. If needed, use a dedicated orchestrator path around the current runner or narrowly extend parsing to structured tool requests. No model-provided Observation may become authoritative.
- No `.git` directory exists in the workspace. Do not attempt commits or claim commit checkpoints. Record work and a TODO checklist in `docs/plans/2026-11-02-models-readonly-tools.md` or an adjacent progress log.
- Provider request formats and configuration should be validated against current docs or existing skill guidance before implementation; only mocked HTTP tests are mandatory. Live test requires credentials.

## Task 1: Establish safe tool-action contract

**Files:**
- Modify: `student/src/agent_smith/loop/extract.py`
- Create or modify: `student/src/agent_smith/tools/dispatcher.py`
- Test: `student/tests/loop/test_extract.py`
- Test: `student/tests/tools/test_dispatcher.py`

**Step 1: Write failing tests**

Test structured read-only actions for `read_file`, `list_files`, `search_code`; valid arguments; unknown tool; malformed arguments; tool-level errors; and rejection of code execution, write/edit, shell, and model-invented observation actions. Ensure no action parser returns executable Python for this path.

**Step 2: Run tests and confirm expected failures**

Run: `cd student && uv run python -m pytest tests/loop/test_extract.py tests/tools/test_dispatcher.py -q`

Expected: new dispatcher/action tests fail because the safe tool-call protocol is not implemented.

**Step 3: Implement the minimal typed action and dispatcher**

Expose only the three existing read-only functions. Validate tool name and argument shape; inject a trusted `ToolContext`; return bounded tool result content/status. Keep tool results application-generated. Do not invoke shell, executor, `run_tests`, or generated code.

**Step 4: Re-run targeted tests**

Run the same command; expect all tests pass.

## Task 2: Implement provider adapters

**Files:**
- Create: `student/src/agent_smith/providers/__init__.py`
- Create: `student/src/agent_smith/providers/contracts.py`
- Create: `student/src/agent_smith/providers/groq.py`
- Create: `student/src/agent_smith/providers/gemini.py`
- Tests: `student/tests/providers/test_groq.py`, `student/tests/providers/test_gemini.py`
- Modify: `student/pyproject.toml` to add HTTPX runtime dependency if absent

**Step 1: Write mocked HTTP tests first**

Use `httpx.MockTransport` to verify request URL/body/auth, response normalization into `ModelResponse`, usage extraction, timeout and HTTP/malformed response errors, and absence of credentials in exceptions/log records. Assert exactly one HTTP attempt per call. Cover provider URL and model override. Never call the network in unit tests.

**Step 2: Run tests and confirm failures**

Run: `cd student && uv run python -m pytest tests/providers -q`

Expected: fail because adapters do not exist.

**Step 3: Implement minimal HTTPX adapters**

Satisfy the exact `ModelClient.generate` protocol. Map provider-specific chat endpoints/responses into shared `ModelResponse`, set explicit timeouts, use configured environment API keys without logging them, close clients cleanly, and classify provider errors without retrying/fallback. Ensure output-token limit is passed through to the provider API as appropriate.

**Step 4: Verify provider tests**

Run: `cd student && uv run python -m pytest tests/providers -q`; expect all mocked tests pass.

## Task 3: Connect tool turns and preserve chat history

**Files:**
- Modify: `student/src/agent_smith/loop/runner.py` or create a dedicated `student/src/agent_smith/loop/chat.py`
- Modify: `student/src/agent_smith/loop/extract.py`
- Tests: `student/tests/loop/test_chat.py` and/or `student/tests/integration/test_model_tool_chat.py`

**Step 1: Write a failing multi-turn history test**

Fake model returns a read-only tool request on turn 1 and a final answer on turn 2. Assert turn 2 gets the system/user messages, assistant tool request, and actual dispatcher observation in correct order. Also verify an invented Observation in model text is ignored, a tool error becomes the actual next observation, and a second tool request works. Assert that no Executor is called and no generated Python is executed.

**Step 2: Run the targeted test and verify it fails**

Run: `cd student && uv run python -m pytest tests/loop/test_chat.py -q` (or the integration test path chosen above).

**Step 3: Implement bounded orchestration**

Connect provider client, existing loop budgets/cancellation where applicable, structured read-only dispatcher, and accumulated messages. Preserve complete ordered history for each model request. Append only the real tool result as observation. Handle malformed actions and provider/tool failures explicitly. Do not expose `SubprocessExecutor` on this flow.

**Step 4: Run targeted tests**

Run the same command; expect pass and inspect assertions for history ordering and no executor use.

## Task 4: Structured logging and TODO tracking

**Files:**
- Create/modify: `student/src/agent_smith/logging_config.py` or use standard module logging in touched files
- Modify: provider/dispatcher/orchestrator modules
- Modify: `docs/plans/2026-11-02-models-readonly-tools.md`

Log provider, model, turn/request number, elapsed time, token counts when available, tool name, bounded outcome/status, and classified errors. Never log API keys, auth headers, or unbounded workspace/model content. Add tests with captured logs proving secrets are absent and events are emitted. Add a dated TODO checklist to the plan for implementation completion, verification results, live-test status, and deferred work.

Run: `cd student && uv run python -m pytest tests/providers tests/tools/test_dispatcher.py tests/loop -q`.

## Task 5: Full verification and optional live chat

**Step 1: Run full tests**

Run: `cd student && uv run python -m pytest -q`.

Expected: suite passes. If pytest cannot spawn in this environment, record exact failure and use the project virtualenv executable directly if available; do not claim tests passed without evidence.

**Step 2: Run static/CLI checks if configured**

Inspect `student/pyproject.toml` and project tooling before selecting commands. Run available checks and record results.

**Step 3: Optional short live test**

Only if credentials and network are available, run one brief multi-turn chat with one configured provider. Verify the model requests a read-only tool and receives the real result before final response (or conduct a plain two-message chat if live tool prompting is unreliable). Do not print/log credentials. If unavailable, mark live test blocked/skipped and include the reason.

**Step 4: Update TODO/results**

Record exact test commands/results, live test status/provider (no secrets), implemented files, and deferred scope in `docs/plans/2026-11-02-models-readonly-tools.md`.

## TODO checklist and execution log

- [x] Add safe structured read-only action protocol and dispatcher.
- [x] Add mocked Groq adapter and tests.
- [x] Add mocked Gemini adapter and tests.
- [x] Connect multi-turn orchestration with complete history and actual observations.
- [x] Verify no generated-code execution or write/shell tool exposure in this chat path.
- [x] Add redacted structured logging and secret-leak tests.
- [x] Run targeted and broad test suites; record results below.
- [x] Check live test prerequisites. No Groq/Gemini API key environment variables are configured, so a live provider call was skipped.
- [x] Record deferred sandbox, evaluator CLI, provider fallback/rotation, MCP, and SWE-bench work.

### What was implemented

- Provider adapters: `student/src/agent_smith/providers/{groq,gemini}.py`, normalized to the existing `ModelResponse` contract, one HTTP attempt per call, injectable HTTPX transports for deterministic tests, and generic errors that do not include credentials.
- Safe dispatch: `student/src/agent_smith/tools/dispatcher.py` allowlists only `read_file`, `list_files`, and `search_code`; `run_tests`, writes, and shell commands are rejected.
- Conversation path: `student/src/agent_smith/loop/chat.py` carries the complete ordered message history and appends only the dispatcher's real result. This path never calls the generated-code executor.
- Entry point: from `student/`, run `uv run python agent_chat.py --provider groq --model <model> --workspace <directory> --task "<question>"` (or use `--provider gemini`). API credentials come from `GROQ_API_KEY` / `GEMINI_API_KEY`; if unset, the CLI safely reads the matching key from `.env` in the working or project root directory without shell evaluation. Optional `--provider-url` overrides the endpoint. Output goes to stdout; bounded metadata logs go to stderr.
- HTTPX is now a runtime dependency and `uv.lock` is updated. Pytest is configured to load `src/` so tests can import the source package.

### Verification results (2026-11-02)

- Targeted provider, dispatcher, chat, and CLI tests — **16 passed**, with a separate provider-backed integration test also passing.
- `cd student && uv run python -m pytest -q` — **212 passed, 1 skipped, 1 failed**. The remaining failure is the pre-existing `tests/sandbox/test_isolation.py::test_re_module`: its fixture passes `r'\\d+'` to Python's regex, matching a literal backslash-d rather than digits, so the expected `123` is absent. No sandbox behavior was changed for this feature.
- `cd student && uv run python -m pytest -q -k 'not test_re_module'` — **212 passed, 1 skipped, 1 deselected**.
- `cd student && uv lock --check` — passed.
- `cd student && uv run python agent_chat.py --help` — passed after making the source-checkout entry point add `src/` to its import path.
- Live provider test — **passed** on 2026-11-02 using the Groq key from `.env` and model `qwen/qwen3.8-27b`. The CLI was run with `GROQ_API_KEY` explicitly unset, confirming `.env` loading works. The model called `read_file` on `pyproject.toml`, received the tool output, then answered with the correct project name and Python version constraint. Two HTTP requests returned 200; the key was not printed or logged.
- Initial live attempts exposed two useful setup issues: `llama-3.3-70b-versatile` is not available on this account (404), and `openai/gpt-oss-20b` tried a native tool call unsupported by the text-JSON protocol (400). The available-model endpoint showed `qwen/qwen3.8-27b`; successful run used that model. API errors were reported without exposing credentials.
- The first Qwen attempt returned argument names that differed from the strict dispatcher. System guidance now includes exact argument schemas for all three tools; the repeat live run succeeded.
- The chat runner reports provider token usage and applies output/turn/time bounds. It uses a conservative serialized-byte preflight before requests, then reconciles provider-reported usage. Exact tokenizer-specific MBPP accounting remains a separate evaluator concern.
- Independent review flagged the initial preflight happening after the provider request; this was fixed by adding the preflight check and a regression test proving oversized prompts are rejected before any model request.

### Deferred

- Do not expose generated-code execution until the sandbox boundary is reviewed and tested; this feature does not establish sandbox security.
- MBPP evaluator output/CLI integration, provider retry/key rotation/fallback, MCP, write/edit and shell tools, SWE-bench, and benchmark reporting remain out of scope.
