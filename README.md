# AgentHow / Agent Smith

A terminal coding agent with native macOS Seatbelt-confined workspace tools,
explicit per-command shell approval, and a separate read-only mode.

- [Setup, usage, tests, and security limitations](student/README.md)
- [Implemented Seatbelt contract](docs/specs/0003-seatbelt-workspace-tools.md)
- [Project scope](docs/scope/scope.md)
- [Contributor and coding-agent instructions](AGENTS.md)

The Python application lives in `student/`. Native sandbox cleanup is **best-effort**:
detached processes can survive cancellation and continue accessing permitted files.
The separate local third-party `skills/` checkout is not part of the application
and is excluded from this repository.
