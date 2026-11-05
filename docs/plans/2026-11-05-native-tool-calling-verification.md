# Native tool calling verification — 2026-11-05

## Commands and observed results

All tests ran from `student/` using the existing Python 3.10 virtual environment; no dependencies or system settings were changed.

- `.venv/bin/python -m pytest -q tests/providers tests/loop tests/test_chat_cli.py tests/test_chat_repl.py tests/integration`: **199 passed**, one Pydantic ReadOnly TypedDict warning.
- README isolated 10-file suite plus `tests/loop/test_native_chat.py`, with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` and `--noconftest`: **528 passed**. This is an isolated result, not full-suite validation.
- `.venv/bin/python -m pytest -q`: **827 passed, 1 skipped, 1 failed**, one warning. The sole failure is the documented pre-existing `tests/sandbox/test_isolation.py::test_re_module` double-escaped regex fixture (`[]` instead of matching digits), untouched by this migration. This is NOT a full-suite pass.

The new provider tests initially failed because `generate()` lacked the `tools` parameter. The new chat tests initially failed because the old loop did not send native definitions or execute structured calls. Both sets passed after implementation. A subsequent signed-ID regression failed against the initial restrictive ID check, then passed after allowing bounded opaque printable IDs. Mocked Gemini round trips preserve SDK thought signatures as verified on the wire.

## Live smoke test

Ran the existing CLI with `--provider groq --model qwen/qwen3.8-27b --workspace . --task` asking to list current-directory files with `list_files`, with instructions not to read file contents, write, edit, or run shell commands. It emitted model-turn / tool-observation / model-turn events and returned:

- README.md
- agent_chat.py
- agent_mbpp.py
- pyproject.toml
- uv.lock

Exit status 0. This verifies one actual confined native tool round trip with Groq, not all model behaviors. Gemini was tested through real SDK translation with mocked HTTP, not live credentials.

## Review and boundaries

Source/diff review checked capability-specific schemas, plain benchmark compatibility, deep-copied history/schema inputs, ID-linked history, duplicate-ID rejection before batch execution, argument validation before dispatch, sequential shared-deadline execution, inert JSON answer text, usage retention and failed-history removal, and unchanged sandbox/approval implementations. No independent reviewer or production-security certification is claimed.

The pinned LiteLLM SDK unconditionally imports an optional MCP gateway when tools are supplied unless `_skip_mcp_handler=True` is passed. This pinned escape hatch is used to retain application-owned execution without adding MCP dependencies or SDK-managed execution. Tests exercise actual HTTP translation for both providers.

Existing limitations remain: 6,000 cumulative input tokens, 1,500 cumulative output tokens, 120 seconds per prompt, conservative byte-based preflight (including schemas/history), no automatic provider/protocol fallback or mutation replay, and best-effort native cleanup rather than full descendant containment. The long synthetic seven-operation native safety test raises only its test-local input limit to reach all existing safety assertions under the larger native wire history. Production budgets are unchanged.
