# R2 lifetime investigation recovery

Workflow 54165465-691c-41c0-9239-9c7cac3ac415 failed: worker c0d90d82-5aab-45a3-8488-5d4af96d6393 timed out after 1800000ms. Finalize-checkpoint steering was not consumed before termination. Parent inspected terminal transcript and saved interim report. No implementation was authorized; no source changes are reported; no Git diff exists because no repository exists.

Recovered interim checkpoint:
/Users/Elias/.pi/agent/sessions/--Users-Elias-Desktop-AgentHoe--/subagent-artifacts/outputs/54165465-691c-41c0-9239-9c7cac3ac415/seatbelt/lifetime-feasibility.md

Worker log with source excerpts and later probe not yet reflected in interim checkpoint:
/var/folders/mx/kvnlryfn3g72cyf9w8vt3m8w0000gn/T/pi-subagents-uid-501/async-subagent-runs/c0d90d82-5aab-45a3-8488-5d4af96d6393/output-0.log

Bounded candidates were launchd cleanup and syscall-level detachment prevention. No launchd job was installed. Sources include installed launchd.plist and posix_spawnattr man pages, SDK syscall definitions, /usr/share/sandbox/cvmsServer.sb and installed system profiles. Installed profiles actually use syscall-unix, syscall-number and with send-signal SIGKILL; this syntax was not invented.

Late recorded worker probe (not rerun by parent): profile `(version 1) (allow default) (deny syscall-unix (syscall-number SYS_setsid SYS_setpgid) (with send-signal SIGKILL))`. One process per action, no children. Both setsid and setpgid cases returned -9, ~0.03 seconds, wrote only initial 'started' fixture canary, then verified PID absent. Reported scratch removed. This validates those two syscall filters on the tested host only; it is NOT proof of complete process-group containment, combined filesystem-profile compatibility, parent-death cleanup, or stable public API support. Prior checkpoint statement 'no payload probes run' predates this log and is stale; do not propagate it as final state.

Review obligations before any next implementation: posix_spawn group/session attributes may establish groups through another syscall path; legacy/alternate syscalls, double fork, exec transitions, kernel-assisted group changes and normal supported subprocess workflows need assessment. Group lifetime/PID reuse, trusted watchdog on orchestrator death, kill/reap races and group ownership are not solved by two detachment filters. Do not infer launchd tracking from its process-group cleanup docs. Private/deprecated Seatbelt interfaces need explicit host capability probes and version constraints, not universal support claims.

Next step: fresh read-only review of recovered sources/probe evidence in the existing subagent protocol. No rerun of completed probes, no silent execution-mode switch. If evidence cannot support a credible bounded implementation candidate, request an explicit architecture/contract decision rather than continue open-ended research. Chat execution remains disabled.
