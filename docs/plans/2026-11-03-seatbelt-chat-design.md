# Seatbelt-backed workspace chat design

## Approved behavior

Shell commands and structured file edits operate directly in the selected workspace.
Every shell command requires fresh approval in the trusted CLI. Structured edits need
no additional approval. Network access is denied. Sandbox setup failure must never
fall back to unsandboxed execution.

## Approved trusted-local threat model (revision)

After reviewing the demonstrated limitations, the user approved proceeding with a trusted-local threat model. Defend against untrusted agent commands, not an already-unconfined malicious host process. Reject unsupported pre-existing hardlinked regular files in every writable root, fail closed on indeterminate admission, and require cooperating host processes not to introduce cross-boundary aliases, invalidate protected-ancestor enumeration, or promote writable/open inodes into protected runtime authority during a tool call and the lifetime of its descendants. Revalidate per call; scanning is admission filtering, not race-proof kernel enforcement. Unknown/copied secrets remain outside automatic classification. Direct workspace writes and individual shell approvals remain unchanged.

Targeted ancestor protection and ordinary directory cleanup passed reviewed corrections and a parent rerun of 300 focused tests. After being offered a native Seatbelt backend with best-effort cleanup versus a container/VM investigation for stronger teardown, the user chose to keep Seatbelt. This explicitly replaces the previous mandatory non-escapable lifecycle release gate: detached descendants may survive cancellation, retain access permitted by their original profile, and continue changing workspace files. Earlier descendants retain older snapshots; later admission does not retroactively update them. Do not claim cancellation makes the workspace quiescent or that all descendants were terminated. Continue implementing native execution with prominent CLI/docs warnings and best-effort process-group cleanup. Other sandbox, approval, and admission controls remain required.

## Trust boundary

The parent owns model/provider calls, API keys, conversation history, and approvals.
All model-controlled file reads, searches, writes, edits, and shell commands run in
Seatbelt-confined child processes. The existing read-only dispatcher must not remain
an alternate parent-side read path in sandbox-enabled chat.

A backend-neutral process interface separates policy from Seatbelt profile rendering.
The first backend supports macOS only. Preserve an explicitly selected legacy read-only
mode; do not silently downgrade a requested sandbox session.

Use a fresh child per tool call. Stage worker code and its dependencies in a private,
parent-owned runtime location outside workspace write permissions, and launch Python
with isolated import settings. Do not load worker code or subprocess startup hooks
from the writable workspace. Command execution uses a fixed non-login shell without
startup files. User command content is deliberately shell code, not interpolated into
an additional wrapper.

## Filesystem and runtime policy

Default-deny profile, with narrowly scoped runtime reads, selected workspace read/write,
and per-call scratch read/write. Canonicalize roots before building profile parameters;
never concatenate raw user paths into SBPL syntax. Deny file-backed network access,
network sockets, and unnecessary IPC/Mach services. Descendants inherit confinement.
Allow process creation for ordinary shell tools; approval does not relax confinement.

Credential files (.env variants, private key locations, cloud/provider credential stores)
are denied for reads and writes, including within the workspace; define precise patterns
and exemptions for sample env templates. Deny writes to .git metadata. Reject overly
broad workspaces (filesystem root, user home) and any policy whose workspace intersects
the private runtime or credential roots. Protect runtime/source interpreter paths from
workspace-write overlaps. Verify directory rename/unlink behavior does not let commands
remove or replace protected subtrees. Symlinks must not escape the allowed roots.

Hard links, previously copied secrets, privileged host services, kernel vulnerabilities,
and unknown credential filenames are not magically solved by this policy. Document
unsupported aliases and validate known hard-link scenarios before making guarantees.
If the project source itself is writable, edits may affect a future unsandboxed application
launch; warn explicitly. This is not isolation from a determined kernel attacker.

## Environment, lifecycle, and protocol

Build a minimal environment from fixed values: safe PATH, private HOME/TMPDIR, and
necessary locale settings. Do not inherit provider keys, proxy settings, SSH agent,
DYLD/PYTHON variables, or arbitrary descriptors. Close descriptors except explicit IPC.

The parent sends bounded JSON tool requests over stdin. Structured workers return
bounded JSON on a dedicated pipe; shell stdout/stderr are untrusted data, never control
messages. Validate response schemas and sizes. A child can lie about its own results;
never interpret child output as permission or approval.

Drain outputs while enforcing byte limits, wall-clock timeout, and cancellation. Start
a dedicated process group, kill it on every exit path (including normally exited leader
with background descendants), and reap the leader. Test detached descendants; do not
claim complete cleanup if process-group escape remains possible. The user-approved native backend explicitly accepts best-effort lifecycle cleanup: timeout, cancellation, or leader exit is not proof that all descendants stopped. Keep output draining and return deadlines bounded even when detached processes hold pipes open; do not add broad fork/spawn bans as a silent substitute. Scratch cleanup and later tool calls must not be described as safe because the process tree is known empty. Seatbelt does not provide a reliable hard aggregate memory limit on macOS.

## Tools and approvals

Expose read_file, list_files, search_code, run_shell(command), write_file(filepath, content),
and edit_file(filepath, old_text, new_text). Enforce bounded strings and exact schemas.
edit_file requires exactly one original-text match and uses atomic replacement where
safe. Both writes and edits execute within confinement; reject nonregular target files.

The trusted CLI displays command and canonical workspace using terminal-safe escaping.
Only an explicit yes approves that exact command once. EOF, interrupt, missing approval
callback, and noninteractive mode deny shell execution. No model argument or child
response can set approval. Do not add approve-all or model-controlled policy overrides.
Structured edits are automatic, so approval of shell commands does not imply all changes
need approval. Direct writes may destroy data and are not rolled back.

## Integration and verification

Use dispatcher-supplied tool guidance in the existing bounded chat session; preserve
history and usage behavior. Give shell tools the remaining turn budget and account for
approval time. Keep existing public read-only classes compatible where practical.

First establish the existing test baseline. Unit-test profile/path escaping, strict
arguments, environment filtering, approval denial, output bounds, and failure paths.
Run real macOS integration tests for allowed edits, forbidden outside/secret/.git access,
symlinks, network (including loopback), inherited child restrictions, output floods,
timeouts, cancellation, and descendant survival. A passing profile-rendering test is
not evidence of enforcement. Unsupported platforms must explicitly refuse execution.

## Repository constraint

The current directory has no Git repository. No design commit or isolated Git worktree
can be created without separately initializing version control; do not initialize it as
an incidental part of this feature.
