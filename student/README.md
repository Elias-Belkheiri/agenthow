# Agent Smith: terminal coding chat

Agent Smith chats about code with **sandboxed workspace tools by default** on
native macOS. Benchmark entry points remain separate and are not complete.

> **BEST-EFFORT cleanup, not full isolation:** detached descendants may survive
> timeout, cancellation, or normal exit and keep modifying allowed workspace
> files under their old profile. Returning, closing, or `/reset` does not prove
> quiescence; later admission does not update old descendants. There is no hard
> aggregate memory limit, and synchronous filesystem/kernel stalls are not hard
> real-time bounded. The native Seatbelt choice explicitly accepts these limits.

## Start a session

From `student/` (examples only; model availability varies):

```bash
uv sync --locked
uv run python agent_chat.py --provider groq --model qwen/qwen3.8-27b --workspace .
```

`--tools sandboxed` is the default. Startup checks admission and actual enforcement
before loading keys or starting the model; unsupported platforms or setup failures
refuse startup, never silently fall back. Explicit legacy read-only selection:

```bash
uv run python agent_chat.py --provider groq --model qwen/qwen3.8-27b --workspace . --tools read-only
```

Read-only mode exposes only read/list/search, without Seatbelt credential filtering;
choose a trusted workspace without sensitive data. In either mode, files read by
tools can be sent to the provider.

Set `GROQ_API_KEY` in your environment or project-root `.env`. Gemini is also
supported with `--provider gemini`, an available Gemini model, and `GEMINI_API_KEY`.
Keys are never printed. `.env` is not shell-executed. Don't share or commit credentials.

The sandboxed tools are `read_file`, `list_files`, `search_code`, `write_file`,
`edit_file`, and `run_shell`. Writes create/replace files; edits require exactly
one occurrence of nonempty `old_text` (overlapping matches count) or fail with
`edit_conflict`. Structured writes/edits need **no approval** and are direct,
potentially destructive changes: **no rollback, even when a conversation turn fails**.

Shell approval is per exact command, displayed with its canonical workspace.
Both input (stdin) and approval output (stderr) must be TTYs. Only literal `yes`
(case-insensitive, surrounding ASCII whitespace allowed) approves once; EOF,
interrupt, malformed input, or noninteractive use denies. There is no approve-all;
approval never expands sandbox permissions. Piped input cannot approve commands.

One-shot noninteractive task (structured edits work; **shell is always denied**):

```bash
uv run python agent_chat.py --provider groq --model qwen/qwen3.8-27b --workspace . --task "Write a short project summary to SUMMARY.md using write_file."
```

Logs go to stderr and answers to stdout. Without `--task`, the basic line-oriented
REPL accepts questions at `agent>`:

- `/help`: commands and capabilities.
- `/reset`: clear in-memory history and session usage, not files or descendants.
- `/exit`, Ctrl-D (EOF), or Ctrl-C: quit (cleanup remains best-effort).

History contains questions, answers, and tool observations, is bounded, and is not
saved to disk. Failed turns leave history unchanged but retain consumed usage and
filesystem effects. An empty or structurally malformed model response may yield
`invalid_action`; rephrase or reset. Tool argument errors are returned to the model
as linked tool results; tools are never executed from JSON printed in answer text.
There are no automatic provider retries. Reset keeps the same tool runtime snapshot;
restart the application to stage a fresh one.

## Security scope

The backend uses deprecated macOS `/usr/bin/sandbox-exec`, tested on the current
host only, not certified across macOS releases. Default-deny profiles confine all
six tools and descendants; network is denied. A fresh per-call metadata scan
rejects indeterminate admission and regular files with multiple hardlinks across
workspace and scratch, including hidden files. Credential catalogue paths (`.env`
variants, `.ssh`, `.aws`, `.azure`, `.kube`, `.netrc`, `.config/gcloud`) are denied
read/write; `.git` writes are denied. Only regular FILES named `.env.example` or
`.env.sample` are template exceptions, not directories or symlinks. Protected-directory
aliases outside scanned writable namespaces are refused, not recursively followed.

Workers use a private staged source snapshot outside write roots, isolated Python
imports, and a minimal environment without provider keys. PATH is fixed to
`/usr/bin:/bin`; interpreter/runtime support is narrow. Dependency downloads are
unavailable; some build/native tools may need future explicit policy support,
not wider read grants.

This is a **trusted-local, cooperating-host** contract, not adversarial host/kernel
resistance. Host processes must preserve admitted aliases, protected ancestry, and
runtime authority throughout surviving descendants' lifetimes. Unknown/copied
secrets are not discovered. Editing the application's own source can affect future
unsandboxed launches. See [the implemented contract](../docs/specs/0003-seatbelt-workspace-tools.md).

## Model interface

Model requests use **LiteLLM 1.106.6**, pinned in `pyproject.toml` and `uv.lock`.
Python 3.10 is required. The direct SDK connects to your selected provider with
your existing API key; no gateway server or LiteLLM account is needed.

`src/agent_smith/providers/litellm_client.py` implements the existing
`ModelClient.generate()` interface. LiteLLM handles provider request/response
translation. Agent Smith owns the agent loops, history, tool execution, budgets,
and concise error codes. Groq uses LiteLLM's OpenAI-compatible route; Gemini uses
its native Google AI Studio route. This avoids an optional `service_tier` field
assumption in the pinned SDK's Groq-specific parser. CLI model names remain the
provider's literal names, including namespaces such as `openai/gpt-oss-120b`.

- Both SDK retry settings are zero. An HTTP request hook enforces one attempt,
  including SDK message-repair paths; redirects are rejected. There is no key
  rotation or provider fallback in this milestone.
- Raw usage fields are validated before SDK coercion. Completion totals are
  normalized so reasoning tokens count exactly once. Missing or invalid usage
  returns `invalid_usage`; malformed responses return `invalid_response`.
- Chat uses native function calling for both providers: explicit tool schemas,
  assistant `tool_calls`, and linked `tool` results. Ordinary text is a final
  answer, not executable JSON. Choose a model that supports function calling;
  unsupported requests fail without a legacy-protocol fallback. Chat does not
  force JSON output; the benchmark text-generation interface is unchanged.
- Read-only mode advertises only read/list/search; sandboxed mode advertises all
  six tools. Calls are validated and executed sequentially, at most 16 per model
  turn, sharing the remaining deadline. Invalid/duplicate call IDs reject the
  batch before execution. Unknown tools and invalid arguments produce error
  results. Native calls do not bypass sandbox admission or shell approval.
- Native history preserves tool IDs (including Gemini SDK thought signatures).
  Failed turns discard history but keep consumed usage and filesystem effects;
  they do not automatically replay mutations. The existing session-wide 6,000
  input / 1,500 output-token limits and 120-second per-prompt deadline remain.
  Conservative input preflight includes tool schemas and full serialized history,
  so a session can reach `input_token_limit` before the next request. Use `/reset`
  to clear history/usage, not filesystem changes.
- The pinned SDK's optional MCP gateway is explicitly skipped for these local
  function tools. The application, not an SDK agent or gateway, executes tools.
  No new dependency is required.
- SDK development-mode `.env` loading and verbose prompt/error logging are
  disabled. The CLI retains its own environment/`.env` key-loading behavior.
  Bundled model metadata avoids a separate pricing-metadata download.

`--provider-url` accepts a Groq API base such as
`https://api.groq.com/openai/v1` or the legacy full `/chat/completions` endpoint.
For Gemini, use `https://generativelanguage.googleapis.com/v1beta`; the legacy
base ending in `/v1beta/models` is also accepted.

## Tests

Standard full suite, from `student/`:

```bash
python -m pytest
```

The legacy MBPP sandbox's `test_re_module` has a known baseline double-escaped
regex failure, unchanged here. Dataless `.venv` dependency imports have also blocked
full runs; do not confuse focused passes with full-suite validation.

Isolated focused sandbox/tool/CLI/mock-model integration checks (not a full suite):

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest --noconftest -q \
  tests/sandbox/test_workspace_policy.py tests/sandbox/test_seatbelt_integration.py \
  tests/sandbox/test_process.py tests/tools/test_workspace.py tests/tools/test_approval.py \
  tests/test_chat_cli.py tests/test_chat_repl.py tests/loop/test_chat.py \
  tests/loop/test_session.py tests/integration/test_sandbox_chat.py
```

This disables plugin autoload and omits unrelated root provider fixtures. The
parent independently ran this exact 10-file isolated suite: **504 passed in 12.67s,
no skips**. This is not full-suite or live provider validation. Earlier component
runs (300 filesystem, 35 backend, 136 approval+tools, 33 CLI/loop/integration)
overlap and must not be added to that count. Independent final source/security review
completed with no issues found under the documented trusted-local, best-effort contract;
that review did not independently execute tests or certify production safety.
Provider tests use mocked HTTP; no live provider verification is claimed here.
