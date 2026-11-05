# Seatbelt implementation supervision

Classification: multi-seam. Execute serially because there is no Git repository and no managed worktree isolation. One writer owns the cwd at a time; reviewers are read-only. No Git initialization or commits authorized.

## Component ownership and gates

- A: Seatbelt feasibility/profile — student/src/agent_smith/sandbox/seatbelt.py and student/tests/sandbox/test_seatbelt_integration.py. Gate: baseline plus real OS denial tests; durable worker report and independent spec/security review. Checkpoint complete; NOT accepted for integration. Worker reports 56 passing characterization/enforcement tests. Independent spec and security reviews both BLOCK: fix nested .env template exemption and weak descendant evidence; global directory-unlink denial breaks TemporaryDirectory cleanup; pre-existing hardlink aliases remain unsupported; setsid/lifecycle unverified. Bounded correction/review wave completed: nested .env exemption and descendant evidence corrected; fresh reviewer accepts those fixes at source level. Parent independently ran `cd student && .venv/bin/python -m pytest tests/sandbox/test_seatbelt_integration.py -q`: 64 passed in 2.10s. This includes limitation characterizations, not full security acceptance. Integration BLOCKED pending directory-protection strategy decision, supported hardlink/alias contract, and detached lifecycle feasibility. No active worker; components B-E remain gated. User approved revising the protection strategy with security requirements unchanged. Next: bounded alternative-policy feasibility experiments and fresh review before production changes. Investigate targeted ancestor protection, supported-workspace alias admission, and detached lifecycle feasibility. No assumption of weaker protections, external-mutation exclusion, or alias acceptance is authorized by this approval; escalate requirements that Seatbelt cannot enforce.
- B: policy/process supervisor — workspace_policy.py, backend.py, process.py and their sandbox tests. Gate: environment, bounded I/O, fail closed, lifecycle tests. Pending A acceptance.
- C: confined tools/approval — tool_worker.py, tools/workspace.py, tools/approval.py and matching tests. Gate: exact edits, confined reads/writes, parent approval. Pending B acceptance.
- D: integration-only — loop/chat.py, tools/dispatcher.py, agent_chat.py and existing chat/CLI tests plus integration tests. Gate: approved shell and automatic edits, budget/history regressions. Pending A-C handoffs.
- E: final validation/docs — README and specs plus adversarial verification. Gate: independent review and exact test evidence. Pending D.

Authority: child workers may edit only assigned files, run local tests, and create temporary test fixtures. No credential reads, provider calls, global setup changes, dependency installs, subdelegation, or weakening requirements without supervisor decision. Parent arbitrates blockers and accepts handoffs.

Runtime/workspace: /Users/Elias/Desktop/AgentHoe, student package under student/. No isolation path: serial in-place work expressly recorded; no concurrent writers.

Feasibility blockers must be reported rather than bypassed: runtime paths, protected subtree mutations, external aliases, UNIX/network access, detached descendants. Child reports are evidence, not release approval.
