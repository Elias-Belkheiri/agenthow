# LiteLLM model interface migration

**Date:** 2026-11-03
**Status:** Implemented and verified

## Goal and boundary

Replace handwritten Groq and Gemini HTTP adapters with the direct LiteLLM Python
completion API behind the existing `ModelClient.generate()` port. The user approved
the Python-native SDK approach. Retain Python 3.10, uv, the custom loops, read-only
tool dispatch, session history, CLI arguments, and single-attempt request policy.

This migration covers the existing two providers. Multi-key rotation and fallback
remain a separate provider-reliability milestone. LiteLLM supplies model transport
and response normalization; application code owns orchestration and budgets.

## Architecture

`create_model_client()` returns one `LiteLLMClient` configured with provider, model,
environment API key, optional provider URL, and JSON mode. The client calls
`litellm.completion()` synchronously. It translates SDK responses into the existing
`ModelResponse`, and SDK failures into credential-safe `ProviderError` codes.
Both handwritten provider modules are retired, with tests migrated to the SDK path.

Use an explicitly selected release with a Python 3.10-compatible wheel, locked by
uv. Pinning avoids unreviewed changes to token accounting or transport behavior.
Use bundled model metadata and disable SDK debug output and message logging.

**Verified release:** LiteLLM 1.106.6 imports successfully on Python 3.10.19 and
provides macOS and Linux wheels. Its native Groq parser assumes the optional
`service_tier` field exists, so Groq uses the SDK's OpenAI-compatible route and
Gemini uses its native route. An explicit OpenAI SDK client configures Groq's HTTP
transport; all model calls still go through `litellm.completion()`.

## Behavior and data flow

- Preserve bare model identifiers at the CLI; prepend a separate SDK routing
  prefix and supply the provider explicitly, preserving literal model namespaces
  such as Groq's `openai/gpt-oss-120b`.
- Accept Groq base URLs and the existing full `/chat/completions` URL, normalizing
  the latter to a base URL. Normalize Gemini's existing `/models` suffix to the SDK
  base URL convention, verified against the selected SDK release.
- Send the remaining output budget and request timeout; disable both LiteLLM and
  underlying SDK retries. Prove single-attempt behavior with mocked HTTP transport.
  Inject HTTPX clients with redirects disabled, a one-request guard, and a response
  hook rejecting HTTP failures before SDK message-repair logic. SDK retry flags
  alone do not disable its message-repair loop.
- Preserve JSON mode for chat and free text for the benchmark loop.
- Normalize completion tokens as a total that already includes reasoning. Expose
  non-reasoning tokens and reasoning tokens separately to satisfy the existing
  additive `ModelResponse` contract without double-counting.
- Validate raw usage before SDK coercion, then validate normalized usage. Gemini's
  required total counter distinguishes inclusive/exclusive candidate reasoning
  counts. Reject absent/invalid usage instead of reporting zero consumption.
- Surface stable HTTP, timeout, network, and invalid-response codes. Do not expose
  raw SDK exception messages, request configuration, or credentials in errors/logs.

## Verification

Test both providers through real LiteLLM translation with mocked HTTP responses:
authentication, endpoints, message history, JSON mode, output limits, reasoning
accounting, errors, malformed responses, and single-attempt failures. Exercise the
read-only tool round trip and subsequent user prompt through this same path.
Run the Python suite and CLI help, plus a bounded live smoke test if credentials
are available. Record the existing sandbox regex failure separately (baseline:
219 passed, 1 skipped, 1 failed in `test_re_module`).

## Verification results (2026-11-03)

- `uv sync --locked`: succeeds on Python 3.10.19.
- `uv run python agent_chat.py --help`: preserves the documented CLI.
- `uv run python -m pytest -q --tb=short`: **305 passed, 1 skipped, 1 failed**.
  The only failure is the unchanged baseline `tests/sandbox/test_isolation.py::test_re_module`
  over-escaped regex fixture. The SDK also emits a Pydantic `ReadOnly` schema warning.
- Live Groq `qwen/qwen3.8-27b` session: read `pyproject.toml` lines 6–9 via the
  actual dispatcher, answered `agent-smith`, then used retained context to answer
  the Python-version follow-up (`>=3.10,<3.11`). Three provider requests total,
  770 input tokens and 59 output tokens; actual tool observation retained.
- Review-driven HTTP-boundary regressions verify SDK message-repair errors do not
  retry, redirects do not forward credentials, full Groq model namespaces survive,
  raw usage cannot be coerced into valid counts, and Gemini inclusive/exclusive
  reasoning counters normalize to the same total. Empty text responses preserve
  reported usage so the loop can count consumption before rejecting the action.

## References

- `agentsmith.pdf`: Python 3.10, custom orchestration, usage, no examination retries.
- https://docs.litellm.ai/docs/completion/input
- https://docs.litellm.ai/docs/providers/groq
- https://docs.litellm.ai/docs/providers/gemini
