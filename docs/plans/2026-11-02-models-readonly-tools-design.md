# AI models and read-only tools integration design

**Date:** 2026-11-02
**Status:** Approved for planning

## Goal

Wire model calls into the existing Agent Smith loop and provide read-only workspace tools, then verify a complete multi-turn conversation including history and tool observations. Record implementation progress and deferred work in a TODO list.

## Scope and approach

Use the existing `ModelClient` and `LoopRunner` interfaces. Implement Groq and Gemini adapters behind that interface, with provider configuration and explicit timeouts. Keep each request single-attempt by default; do not add key rotation or provider fallback in this milestone.

Add a small dispatcher for the existing `read_file`, `list_files`, and `search_code` functions. Only these read-only tools are exposed. Do not expose `run_tests`, write/edit operations, shell commands, or generated-code execution. Keep the sandbox as an integration boundary: this feature does not make or imply a security clearance for the current sandbox.

The loop should append the assistant action and actual tool observation to the conversation before the next model request, preserving full conversation history. Tool calls and observations must be represented by application-generated records, not trusted as model-invented observations.

## Logging and privacy

Record structured, useful per-turn metadata: provider/model, request number, elapsed time, token usage when available, action/tool name, bounded tool outcome, and classified errors. Never log API keys, authorization headers, or raw credential-bearing request configuration. Keep logging bounded; do not dump arbitrary workspace contents or unbounded model responses by default. Persist chat history only where the existing run/output contract requires it; avoid new long-term transcript storage unless required by the evaluator.

## Verification

- Unit-test Groq and Gemini HTTP behavior with mocked transport: endpoint/body/auth shape, normalized response/usage, errors, timeouts, no credential leakage, and one attempt.
- Unit-test read-only dispatch, argument validation, tool errors, and workspace boundary behavior.
- Add a mocked multi-turn integration test: model turn 1 requests a read-only tool, the application executes it, turn 2 receives the same prior context plus the actual observation, and the model returns a final answer. Assert ordering and history contents.
- Run the full test suite.
- If provider credentials and network access are available, perform a brief live chat with one configured provider, verify at least two turns and the final response, and report the provider and outcome without printing secrets. Live testing is supplementary, not a substitute for deterministic tests.

## Deferred

Evaluator-compatible MBPP end-to-end execution, generated-code execution, sandbox hardening/security claims, write tools, MCP, provider retries/key rotation/fallback, and SWE-bench remain out of scope.

## Alternatives considered

1. **Both providers behind the existing interface (chosen):** aligns with project architecture and allows mocked tests without provider credentials.
2. One provider only: smaller initial change, but leaves the planned provider interface incomplete.
3. General-purpose tool-calling framework: more adaptable, but excessive for three read-only tools and the current custom loop.
