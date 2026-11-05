# 0001. Agent Smith stack and architecture

**Date**: 2026-10-14
**Status**: In Progress

## Summary

Agent Smith will be a local Python command line application, organized as a layered monolith. It will use the required Python 3.10 and uv setup, with a custom agent loop, a unified Groq and Gemini provider interface, and JSON task and solution files. This keeps the first release straightforward to build while leaving clear boundaries for the later sandbox and MCP work.

## Context

Agent Smith must run as a command line program in the provided evaluation environment. It needs to solve MBPP first, then SWE bench, and must emit the task and solution formats required by the evaluator. The project requires Python 3.10 and uv, requires Pydantic task and output models, and prohibits agent orchestration libraries.

A single developer is building this project without a fixed deadline. Free tier provider access is required, but provider quotas can change. Examination allows no retries, and the shown exam commands do not pass a retry control flag. The application also executes model generated code, so process separation alone cannot be treated as a security sandbox. A minimum restricted sandbox must precede the first MBPP run; full sandbox behavior and MCP integration receive their own later specs.

## Options considered

### Option 1: Layered local Python application

One command line application with modules for the agent loop, provider adapters, benchmark contracts, sandbox execution, and MCP integration. The program stores task inputs and solution outputs as files.

**Pros**:
- Fits the provided evaluation commands and the single developer team.
- Keeps provider and sandbox boundaries testable without network services.
- Avoids database and deployment work that does not serve the benchmark.

**Cons**:
- Module boundaries require discipline as the project grows.
- The app itself does not isolate generated code from the host operating system.

### Option 2: Web service around the agent

Expose agent runs through a web API and keep benchmark adapters as clients.

**Pros**:
- Could support remote users and concurrent requests later.
- Provides a clear network interface for future integrations.

**Cons**:
- The supplied evaluation expects a command line program.
- Adds a server, deployment, and operational concerns before there is a need.

### Option 3: Separate services for providers, sandbox, and benchmarks

Run each major component as a separately deployed process with network interfaces between them.

**Pros**:
- Components can scale and deploy independently.
- Stronger process boundaries are possible when paired with operating system isolation.

**Cons**:
- Requires service orchestration and observability beyond the needs of one developer.
- Adds network failure modes and makes local evaluation more involved.

## Decision

**Chosen option**: Option 1: Layered local Python application.

Build a Python 3.10 command line monolith with distinct modules, JSON files for evaluation inputs and outputs, and a custom agent loop. Use one internal provider interface with Groq and Gemini. As of 2026-11-03, the direct LiteLLM Python SDK replaces the original handwritten HTTPX adapters (see the [migration design](../plans/2026-11-03-litellm-migration-design.md)). Use Pydantic v2 for external task and solution contracts and pytest for tests. Keep MCP integration undecided until its feature is designed. Provider fallback remains a later development-only capability.

**Implementation skills**: `httpx` (`slanycukr/riot-api-project`, `.agents/skills/httpx/`) · `pydantic` (`pydantic/skills`, `.agents/skills/pydantic/`)

## Rationale

The evaluator already provides task files and expects solution files, so local JSON files remove a database and make the output easy to inspect. A layered monolith is enough for one developer and allows the agent loop, provider handling, sandbox, and benchmark tools to evolve separately.

The PDF fixes Python 3.10, uv, Pydantic models, and a custom orchestration loop. LiteLLM supplies model access and provider translation while the project retains the orchestration loop. Synchronous requests fit the sequential Thought, Code, Observation cycle. The adapter injects explicitly bounded HTTPX clients into the SDK and closes them after each request. Pydantic validates data at external boundaries rather than becoming the base class for every internal object.

## Proposed stack

| Layer | Choice | Reason |
|---|---|---|
| Product shape | Local command line application | Matches the evaluator interface and does not require a running service. (basis: `agentsmith.pdf`) |
| Architecture | Layered monolith | One deployment unit with clear internal boundaries fits one developer and the benchmark workload. (basis: monolith first) |
| Language | Python 3.10 | Required by the project. |
| Package management | uv | Required by the project. |
| Command line | `argparse` | Provides the required CLI without another runtime dependency. |
| Agent orchestration | Project owned Python loop | The project prohibits libraries that reimplement agent orchestration. |
| Task and solution data | JSON input and output files, validated with Pydantic v2 | Matches evaluator contracts and makes results inspectable. (basis: `agentsmith.pdf`, installed Pydantic skill) |
| Model API client | LiteLLM 1.106.6 behind `ModelClient.generate()` | Unified synchronous provider interface, with explicit HTTPX request/response policy hooks and normalized usage. |
| Providers | Configurable Groq and Gemini | Both use LiteLLM; Groq uses its OpenAI-compatible route and Gemini its native route. Automatic fallback is deferred. |
| Evaluator entry points | `python -m agent_mbpp` and `python -m agent_swebench`, accepting `--task-file`, `--output`, `--model-name`, and `--provider-url` | Keeps both benchmark commands compatible with the PDF. The SWE bench entry point arrives with that feature. (basis: `agentsmith.pdf`) |
| Provider settings | Standard `GROQ_API_KEY` and `GEMINI_API_KEY` plus numbered variables such as `GROQ_API_KEY_1` and `GEMINI_API_KEY_1`; optional `--provider` | Accepts evaluator supplied credentials and supports multiple keys per provider without storing secrets. |
| Provider fallback | No API retries, key rotation, or provider fallback by default; explicit `--allow-provider-fallback` enables them in development | The exam commands need no extra flag to respect the no retries rule. Only provider availability failures permit fallback in development. (basis: `agentsmith.pdf`) |
| Sandbox execution boundary | A restricted subprocess before the first generated code run | Allows timeout termination and separation of execution state, but is not sufficient security isolation by itself. Minimum controls precede MBPP; full hardening is designed separately. (basis: `agentsmith.pdf`) |
| Persistence | Filesystem only for task and solution files | Database persistence is not required by the evaluator. |
| MCP | Deferred to the MCP feature spec | The engineer asked to choose the MCP client and server when that feature is reached. |
| Testing | pytest | Provides concise unit and integration testing for adapters and tool behavior. |
| Logging | Python standard library logging; solution JSON stores required per step metrics | Avoids a separate logging service for a local evaluation tool. |
| Deployment | Run locally through uv and the supplied evaluation scripts | Matches the required evaluation environment. |

Provider failure handling must distinguish provider availability failures from task or model output failures. By default each model request gets one API attempt with no key rotation or provider fallback. Only an explicit development flag enables rotation or Groq to Gemini fallback for rate limits, quota exhaustion, timeouts, or provider errors. Invalid task data, malformed model output, and sandbox errors never trigger provider fallback. Every attempt counts toward requests and usage; the evaluator entry points require no special flag to disable retries.

The minimum sandbox precedes MBPP generated code execution and must deny unauthorized imports, paths, network access, and dangerous builtins while enforcing process timeout and memory limits. A subprocess alone is not a complete security boundary. The later sandbox spec must harden and test these controls, make them configurable, and support the interactive CLI and full MCP requirements. Its sandbox owns the MCP client and dynamically discovered wrappers; a trusted broker reaches external MCP servers without giving generated code network access. MCP server actions occur outside the sandbox code timeout, while a separate overall task deadline bounds the entire run. MCP library and transport implementation remain open until that feature.

The first MBPP release must count cumulative input and output tokens, including reasoning tokens, and enforce 10 iterations, 6,000 input tokens, 1,500 output tokens, and a 120 second task deadline. Detailed metrics and provider reliability behavior can grow later.

## Consequences

**Positive**:
- The initial project can run with the evaluator's file based interface.
- Provider adapters can be tested independently while the agent loop remains project owned.
- No database, server, or deployment platform is needed for the initial work.

**Negative / tradeoffs**:
- Provider outages cannot trigger automatic fallback during examination; resilience requires an explicit development flag.
- Python level restrictions and a subprocess alone do not provide strong protection against hostile code. The sandbox feature must prove the required restrictions and state their limits.
- Free tier availability and quotas are controlled by the providers and may change without notice.

**Neutral**:
- MCP transport and SDK choices remain open until the MCP feature is designed.
- A web interface, service deployment, database, and asynchronous request handling are not part of this foundation.

## Follow-up

- [ ] Design and verify minimum sandbox controls before any MBPP generated code execution, then expand them in the later sandbox and MCP feature spec.
- [ ] Decide the MCP client SDK and server implementation when designing the MCP feature, including compatibility with Python 3.10 and both required transports.
- [ ] Capture the HTTPX conventions from `.agents/skills/httpx/` in root `AGENTS.md` before implementation.
- [ ] Capture the Pydantic conventions from `.agents/skills/pydantic/` in root `AGENTS.md` before implementation.

## References

**Project sources**:
- `agentsmith.pdf`, language, package manager, orchestration, sandbox, provider, and evaluation requirements.
- Installed HTTPX skill at `.agents/skills/httpx/`.
- Installed Pydantic skill at `.agents/skills/pydantic/`.

**Practices & standards**:
- Monolith first, avoid distributed components until scale or team boundaries require them.
- Use explicit request timeouts and close reusable HTTP clients.
- Validate untrusted external data at system boundaries.

**Links**:
- Python 3.10 documentation: https://docs.python.org/3.10/
- uv project documentation: https://docs.astral.sh/uv/concepts/projects/
- Pydantic model documentation: https://docs.pydantic.dev/latest/concepts/models/
- HTTPX quick start: https://www.python-httpx.org/quickstart/
- Groq text generation and rate limits: https://console.groq.com/docs/text-chat · https://console.groq.com/docs/rate-limits
- Gemini API and rate limits: https://ai.google.dev/gemini-api/docs/generate-content/get-started · https://ai.google.dev/gemini-api/docs/rate-limits
