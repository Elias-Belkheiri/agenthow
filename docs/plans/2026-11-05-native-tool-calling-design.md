# Native chat tool calling

## Approved scope

Replace prompt-encoded JSON actions in chat with LiteLLM native function tools for Groq and Gemini. Keep benchmark text generation unchanged. No legacy action parsing or automatic protocol/provider fallback.

The provider port accepts optional function definitions and returns text plus structured tool calls (ID, name, raw JSON arguments). Chat advertises only its dispatcher capabilities, records assistant tool calls and linked tool-role results, executes sequentially, and treats text without calls as the final answer. Text containing JSON is never authority to execute.

All calls remain untrusted. Validate structural metadata and duplicate IDs before executing a batch; validate argument JSON and exact tool arguments before dispatch. Invalid arguments and unknown tools produce linked error observations. Preserve sandbox admission, exact-command approvals, no rollback/replay, and single-attempt provider calls. Multiple calls share the same turn deadline; bound batch size. Token preflight includes serialized native history and tool definitions. Existing cumulative limits remain unchanged.

Tests cover both provider wire formats using mocked HTTP, real confined tool round trips with fake models, failed-turn history/usage, ID linkage, malformed/duplicate calls, multiple sequential calls, and inert tool-shaped text. Live validation is limited to Groq on this host; mocked Gemini tests are not live certification.
