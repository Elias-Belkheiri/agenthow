# R2 decision gate: spawn-attribute escape confirmed

Parent inspected saved report:
/Users/Elias/.pi/agent/sessions/--Users-Elias-Desktop-AgentHoe--/subagent-artifacts/outputs/c18600ca-7be2-4ec2-b515-97f2ed83837e/seatbelt/spawn-attribute-probe.md

Measured candidate profile only: allow default plus deny syscall-unix SYS_setsid/SYS_setpgid with SIGKILL. Ordinary os.posix_spawn control inherited pgid/sid and exited zero. SETPGROUP(0) spawned child with pgid equal to child PID; SETSID spawned child with both pgid/sid equal to child PID. Both exited zero with identities verified. This confirms escape from the candidate's group/session restrictions, not escape from filesystem confinement. Native Python3.13 wrapper invoked real macOS posix_spawn; this mechanism is kernel-level, not proof tied to the project's Python3.10 wrapper. All three child PIDs reaped and checked absent. No production changes.

Parent accepts decisive negative evidence for the investigated two-filter solution. R2 is NOT accepted; no production supervisor or shell activation. Do not silently block all posix_spawn/fork calls or accept detached survivors. No repeated probe or further open-ended hardening is authorized at this gate.

User architecture/capability decision required:
1. Continue Seatbelt native-macOS backend with explicitly reduced lifecycle guarantee (best-effort cleanup, detached survivors possible). This weakens approved cancellation/termination semantics and requires explicit approval; not selected.
2. Investigate Linux container/VM execution boundary with PID/cgroup-based teardown and a live workspace mount to retain direct file writes. Requires runtime availability/setup approval and changes command environment to Linux; a shared mount alone does not prove filesystem protections. This is a candidate, not a selected or validated implementation.

Parent recommendation: if non-escapable descendant cleanup remains mandatory, investigate the container/VM boundary rather than claim native Seatbelt/group cleanup solves it. Preserve reviewed filesystem policy and 300-pass evidence as completed work, not full-feature readiness. All integration lanes remain gated pending user choice.
