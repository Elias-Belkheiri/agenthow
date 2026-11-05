# Repository instructions

Applies throughout this repository; read this file before making changes.

## Project map and safety
- Python 3.10 application, entry points, dependencies and tests: `student/`.
- Usage and test commands: `student/README.md`; implemented contracts: `docs/specs/`.
- Keep provider calls/credentials and shell approvals in trusted parent code.
- Preserve fail-closed Seatbelt setup, per-call admission, confined file tools and exact per-command approval. Never add an unrestricted fallback or approve-all.
- Native cleanup is best-effort, not full descendant containment; never claim otherwise. Respect the documented trusted-local limitations.
- Do not install dependencies, alter services/global settings, or weaken security contracts without authorization. Preserve vendored skill licenses and avoid unrelated edits.

## Verify changes
- Add regression tests before bug fixes; run affected tests before committing.
- Full suite from `student/`: `.venv/bin/python -m pytest -q`.
- The README documents the isolated sandbox/tool/chat test command for environments with blocked dependency imports. Label isolated results accurately; they are not a full-suite pass.
- Record pre-existing failures (including the legacy regex fixture) or environment blockers honestly; do not silently fix unrelated failures or claim unrun tests passed.
- Update public usage/contracts when behavior changes.

## Git workflow: commit continuously and deliberately
- During authorized implementation, commit after each coherent, reviewed and verified change. Do not wait until the entire task ends, and do not ask for permission for every routine commit.
- Keep commits small and single-purpose; include relevant tests and documentation together. Do not commit every tool call or broken intermediate edit.
- Before editing, inspect `git status --short` and the current branch. Prefer a short-lived feature branch for new work; use a worktree only when authorized/appropriate.
- Before each commit, inspect `git diff`, stage explicit relevant paths, then inspect `git diff --cached` and run `git diff --cached --check`.
- Never stage unrelated user changes. Avoid blanket `git add .` when there are unrelated or unreviewed files.
- Use imperative, descriptive messages, preferably `feat:`, `fix:`, `docs:`, `test:`, `refactor:` or `chore:`. Never claim tests/security guarantees the evidence does not support.
- Never commit `.env`, API keys, private keys, local virtualenvs, caches, editor files or agent-session artifacts; respect `.gitignore` and inspect staged content for secrets.
- Never amend others' commits, reset/discard user work, force-push, or rewrite published history without explicit approval. Do not bypass hooks.
- Push when the user requests publication or grants standing push permission; routine commit permission alone is not permission to publish.
- For an authorized push, confirm the remote/branch, use a normal non-forced push, verify the remote commit, and report commit IDs, test results and remaining work.
