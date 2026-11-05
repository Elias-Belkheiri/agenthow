# Scope: Agent Smith

Agent Smith is a terminal chat coding agent. The current interactive milestone supports conversation and read-only code exploration; edits, command execution, and generated-code execution are not enabled in chat. MBPP, SWE bench, sandboxing, and the full evaluation requirements in `agentsmith.pdf` remain separate roadmap targets.

**Current interactive milestone:** launch `student/agent_chat.py` without `--task` for a read-only REPL with in-memory history and `/help`, `/reset`, `/exit`. See [terminal usage](../../student/README.md) and [implementation tracking](../plans/2026-11-02-interactive-terminal-chat.md). The benchmark checklist below predates this milestone and does not represent live ticket status; tickets are tracked in Linear (Agent Smith project).

**Build approach:** Skateboard (deliver a usable MBPP agent first, then grow it toward full evaluation).
**Workflow:** GA (verification, tests, review, and documentation after each feature).

## At a glance

| # | Feature | Phase | Status |
|---|---------|-------|--------|
| 1 | Stack and architecture | Foundation | in-progress |
| 2 | Coding standards and tooling | Foundation | planned |
| 3 | Agent and task contracts | Foundation | planned |
| 4 | Minimum restricted sandbox | Foundation | planned |
| 5 | MBPP solving prototype | Release 1 | planned |
| 6 | Full sandbox and MCP bridge | Release 2 | planned |
| 7 | Provider reliability and usage tracking | Release 2 | planned |
| 8 | SWE bench solving | Release 3 | planned |
| 9 | Evaluation and benchmark report | Release 3 | planned |
| 10 | Submission documentation | Release 3 | planned |

## Foundations

### 1. Stack and architecture · in-progress · GA
Choose the project structure and core architecture, then create a runnable Python 3.10 project managed with uv. Keep the agent loop and sandbox implementation under project control.
**Done when:** the architecture and dependencies are recorded, and the scaffold runs locally.
- [x] Decide the stack and architecture (spec): `/architect stack and architecture`
- [x] Scaffold from the decision: `/develop stack and architecture`
- [ ] Verify it: `/check verify stack and architecture`
- [ ] Test it: `/test stack and architecture`
- [ ] Review it (fresh model): `/check review stack and architecture`
- [ ] Document it: `/document stack and architecture`
Spec [0001](../specs/0001-stack-and-architecture.md) · code in `student/`

### 2. Coding standards and tooling
Capture conventions from the actual scaffold and add the agreed quality checks.
**Done when:** root `AGENTS.md` records project conventions, and the selected checks run cleanly.
- [ ] Capture conventions and tooling: `/audit`
- [ ] Install the tooling: `/develop tooling`

### 3. Agent and task contracts · needs a decision · GA
Define task inputs, solution outputs, step metrics, and the agent loop boundary shared by both benchmarks.
**Done when:** task and output contracts support both benchmark formats, and every agent step can record its request, code, observation, and metrics.
- [ ] Design the contracts and loop boundary (spec): `/architect agent and task contracts`

### 4. Minimum restricted sandbox · needs a decision · GA
Before the first MBPP run, isolate generated code in a restricted process. Prevent unauthorized imports, file access, network access, and dangerous builtins; bound execution time and memory. A subprocess by itself is not a sandbox.
**Done when:** generated code runs only within these limits, blocked operations are tested, and timeout or memory failures return explicit observations without crashing the agent.
- [ ] Design the minimum sandbox boundary (spec): `/architect minimum restricted sandbox`

## Release 1: MBPP solving prototype

### 5. MBPP solving prototype · needs a decision · GA
Deliver the first usable product: load an MBPP task, ask a model for Python code, execute it in the minimum sandbox, return observations, iterate, and save a solution that can be validated.
**Done when:** `python -m agent_mbpp` accepts `--task-file`, `--output`, `--model-name`, and `--provider-url`, produces a valid solution output, solves a task through a real Thought, Code, Observation loop, and enforces 10 iterations, 6,000 input tokens, 1,500 output tokens including reasoning tokens, and a 120 second task deadline. The shown exam command makes no API retries or provider fallback.
- [ ] Design the MBPP agent and prompt (spec): `/architect MBPP solving prototype`

## Release 2: Safer and more reliable agent

### 6. Full sandbox and MCP bridge · needs a decision · GA
Expand the minimum sandbox into configurable limits and an interactive CLI. Connect to MCP servers through a trusted broker, expose discovered tools as Python calls, and keep `final_answer` available in the sandbox. Generated code cannot use the network directly.
**Done when:** full sandbox security checks pass, both required MCP transports work, tool documentation reflects discovered schemas, MCP actions remain outside the code timeout, and execution failures give explicit feedback.
- [ ] Design the sandbox boundary and MCP integration (spec): `/architect full sandbox and MCP bridge`

### 7. Provider reliability and usage tracking · needs a decision · GA
The current model interface uses the direct LiteLLM Python SDK for Groq and Gemini, replacing handwritten HTTP adapters. It preserves custom loops, single-attempt calls, provider URL overrides, and reasoning-aware usage accounting; see the [migration design](../plans/2026-11-03-litellm-migration-design.md). Expand this interface with multi-key management and provider reliability. Support standard API key environment variables plus numbered keys. Normal runs may opt into rotation and fallback, but the default exam commands never retry.
**Done when:** provider configuration can switch models without changing the agent loop, `--allow-provider-fallback` enables rotation only on provider failures, and requests, retries, tokens, latency, and cumulative limits are recorded.
- [ ] Design provider abstraction and usage tracking (spec): `/architect provider reliability and usage tracking`

## Release 3: Full benchmark and submission readiness

### 8. SWE bench solving · needs a decision · GA
Extend the working agent to explore and edit Dockerized repositories, run tests, and submit a valid patch without looking up external solutions.
**Done when:** `python -m agent_swebench` accepts `--task-file`, `--output`, `--model-name`, and `--provider-url`; mandatory repository tools work independently, containers are cleaned up, and the agent can produce a patch for a task.
- [ ] Design the SWE bench workflow and tools (spec): `/architect SWE bench solving`

### 9. Evaluation and benchmark report · needs a decision · GA
Run the required evaluation flows and compare at least five models on the same three SWE bench tasks, recording results and an ablation comparison.
**Done when:** evaluation logs and solution files are preserved, hard limits are respected, required pass thresholds are met, and `BENCHMARK_REPORT.md` contains results, reliability, intermediary metrics, an ablation study, and conclusions.
- [ ] Design evaluation and benchmark reporting (spec): `/architect evaluation and benchmark report`

### 10. Submission documentation · needs a decision · GA
Explain how to install and run the project, its architecture, agent loop, sandbox, tools, benchmark results, and how AI was used.
**Done when:** the English README meets the required format and covers all required technical areas, and configuration examples contain no secrets.
- [ ] Design the submission documentation (spec): `/architect submission documentation`

## References

### Project sources
- `agentsmith.pdf`, project requirements and evaluation criteria.

### Practices and standards
- Build the smallest usable workflow first, then expand it, matching the selected Skateboard approach.
- Treat sandboxing, usage limits, and execution feedback as explicit product outcomes, based on the project requirements.

### Links
No web verified links were added. Link verification was not available in this run.

## Legend

A feature marked `needs a decision` starts with `/architect`. The first feature is the next step. Features are scoped as recommendations, and you can skip or reorder them when you already know how to proceed.

Workflow `GA` recommends `/check verify`, `/test`, `/check review`, and `/document` after development. An `Assumed` spec can still proceed, but its unresolved decision should remain visible until ratified.
