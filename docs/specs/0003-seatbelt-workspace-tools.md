# 0003. Seatbelt workspace chat tools

**Status: IMPLEMENTED — focused consolidated verification and independent source/security review completed.**

This documents the native chat backend, not production certification or a claim
of full isolation. The legacy MBPP restricted-Python executor in [0002](0002-minimum-restricted-sandbox.md)
is separate, unchanged, and neither hardened nor replaced by this backend.

> **BEST-EFFORT cleanup:** detached descendants can survive timeout, cancellation,
> and normal leader exit, retaining their original profile and continuing to
> modify allowed workspace files. Return, close, scratch cleanup, and conversation
> reset do not establish quiescence. New admission does not update old descendants.
> There is no hard aggregate memory limit. Synchronous filesystem/kernel stalls
> are not hard real-time bounded. The approved native Seatbelt choice explicitly
> accepts these limits instead of requiring container/VM-style teardown.

## Architecture and flow

1. `agent_chat.py` defaults to `--tools sandboxed`. It constructs
   `WorkspaceToolDispatcher` and checks readiness before key loading/model startup.
   `--tools read-only` explicitly selects the older read/list/search dispatcher;
   it is not an automatic fallback and does not provide this security contract.
2. The trusted parent owns provider calls, keys, conversation history, budgets,
   policy roots, and approval. Dispatcher construction copies only
   `sandbox/tool_worker.py`, `tools/contracts.py`, `tools/files.py`, and
   `tools/search.py` into a private parent-owned runtime with empty package
   initializers, outside writable roots. It freezes this snapshot for the session.
   The resolved interpreter must be inside its base installation; runtime,
   installation, workspace, and scratch must be separate.
3. Each tool call validates exact arguments and serializes a bounded payload before
   any shell approval. Shell is denied without a trusted callback returning exactly
   `True`. Approval time consumes the trusted call/remaining-turn budget.
4. A fresh scratch directory and `SeatbeltBackend` are created for each call.
   `build_seatbelt_profile` freshly admits roots and compiles parameterized SBPL
   on every execution; no cached profile authorizes a later call. Setup/admission/
   enforcement failure refuses execution, never runs an unconfined handler.
5. Before payload launch, an actual confined Python probe must fail both read and
   append of an existing host-readable/writable outside canary with EACCES/EPERM,
   successfully write/read a scratch control, and emit an exact marker. The parent
   checks control contents and unchanged canary. Missing files, failed startup,
   and mere profile rendering do not count as enforcement. The probe has at most
   two seconds within the call deadline. Readiness also executes a fixed isolated
   `ready` payload. These probes are limited checks, not proof of all policy rules.
6. Structured operations run in a fresh isolated worker using the resolved Python
   with `-I -S -B` and an explicit staged import path. No workspace startup hooks
   or provider initialization are imported. Shell runs directly as `/bin/sh -c`
   with the exact approved command, no login/startup-file wrapper. All six tools,
   including reads/search, execute under Seatbelt; no parent read-tool escape path.
7. The parent validates bounded process/worker results. Output is untrusted data,
   never approval, policy, or instructions to the trusted parent. Session history
   records tool observations; failed turns do not commit conversational history
   but still consume usage and may already have changed files. `/reset` clears
   history/usage only, keeping the same dispatcher snapshot. Close removes staging,
   not a guarantee of descendant termination. Restart stages fresh tool sources.

## Filesystem/security contract

Native macOS only, requiring executable `/usr/bin/sandbox-exec` (deprecated).
Actual enforcement was tested on the current host only; other OS/macOS versions
are not certified. `sandbox/backend.py` defines backend-neutral requests/results;
`process.py` supervises execution; `workspace_policy.py` admits namespaces;
`seatbelt.py` renders the default-deny profile. `sandbox/policy.py` belongs to the
legacy executor, not this policy.

Roots are canonicalized existing directories, with identity-aware overlap checks
for aliases/firmlinks. Filesystem root, home and ancestors, broad system roots,
credential overlaps, and writable/runtime overlaps are refused. Paths become
`sandbox-exec -D` parameters, never raw SBPL interpolation. Each admission scans
all workspace and scratch entries, including hidden ones, without following
symlink trees; any regular file with `st_nlink > 1` is refused across both writable
roots. Indeterminate metadata, broken/cyclic protected links, or incomplete
protected enumeration fail closed. Runtime hardlink creation is denied by
`deny file-link`, including links to readable runtime or `.git` inodes.

The shared ASCII case-insensitive catalogue denies reads and writes to `.env`
variants, `.ssh`, `.aws`, `.azure`, `.kube`, `.netrc`, and `.config/gcloud` path
components. Only terminal regular FILES named `.env.example` or `.env.sample`
are template exceptions; directories, symlinks, and their descendants are not.
`.git` is readable but not writable. Protected symlink chains/targets and relevant
ancestors receive targeted protection, including unlink/rename prevention;
ordinary unprotected directories remain removable. Protected-directory aliases
outside scanned writable namespaces are refused, including inherited aliases
under protected directory targets, rather than traversing external trees.
External regular-file protected targets can be denied without tree enumeration.

The profile permits workspace/scratch reads and writes, interpreter installation
and staged runtime reads/executable mapping, fixed system runtime reads from
`/bin`, `/usr/bin`, `/usr/lib`, narrow startup sysctls, `/dev/null`, and process
creation. Other access, including networking (loopback too) and unneeded IPC/Mach
services, is default-denied. Descendants inherit confinement. Shell approval does
not widen these permissions. Symlinks do not grant access outside allowed roots;
structured writes additionally reject symlinks in supplied target ancestry.

The child environment is exactly PATH=`/usr/bin:/bin`, LC_ALL=`C`, and private
scratch HOME/TMPDIR. No inherited provider keys, proxies, SSH agent, DYLD/PYTHON
variables, or arbitrary descriptors (`close_fds=True`). Interpreter/runtime
support is deliberately narrow; dependency download is unavailable. Some build
or native tool access may require future explicit policy support, not broader
read grants to make a command work.

This protects against untrusted agent commands under **trusted cooperating host**
assumptions, not a malicious unconfined host or kernel adversary. Host processes
must not introduce cross-boundary aliases, invalidate protected ancestry, or
promote writable/open inodes into protected runtime authority throughout calls
and surviving descendants' lifetimes. Scanning is admission filtering, not
race-proof enforcement. Unknown filenames and copied secrets are not discovered.
Writing application source does not change the current staged worker but can
change a future unsandboxed application launch. No claim of full isolation or
full-tree termination is made.

## Exact tools and approval boundary

Each argument object must contain exactly the listed keys, with no extras or
omissions. Values have exact types (booleans are not integers):

| Tool | Arguments |
|---|---|
| `read_file` | `{"filepath": str, "start_line": int, "end_line": int}` |
| `list_files` | `{"directory": str, "pattern": str}` |
| `search_code` | `{"pattern": str, "file_pattern": str}` |
| `write_file` | `{"filepath": str, "content": str}` |
| `edit_file` | `{"filepath": str, "old_text": str, "new_text": str}` |
| `run_shell` | `{"command": str}` |

Strings must be valid UTF-8 without NUL. Content/old/new text is at most 1 MiB
per string; other strings at most 8192 UTF-8 bytes. Only `content` and `new_text`
may be empty. Line numbers are 1..2^31-1 with end >= start. File tools reject
parent traversal/outside paths; read/list/search have additional bounded scan,
pattern, file and output limits in `tools/contracts.py`.

`write_file` creates or replaces a regular file (parent directory must exist).
`edit_file` requires an existing regular UTF-8 file, at most 1 MiB, and exactly
one match for nonempty `old_text`, counting overlapping matches. Zero/multiple
matches yield `edit_conflict`. Resulting content must also fit 1 MiB. Both reject
nonregular targets, write a same-directory temporary file, preserve existing
permission bits (new file mode 0600), and atomically replace the target. This is
not a transaction or rollback facility: automatic direct destructive writes
persist even if a later tool, model response, or conversation turn fails.

`TerminalApproval` displays JSON-escaped command and canonical workspace on
stderr. Both stdin and stderr must be TTYs. Only literal ASCII `yes`, case
insensitive, with optional surrounding space/tab/CR and terminating LF, approves
that exact command once. Input is bounded (128 characters including LF);
incomplete/oversized input or stream errors disable that callback instance.
EOF, interrupt, missing callback, and noninteractive input deny. There is no
approve-all, model-controlled approval argument, or child-output authorization.
The CLI installs approval only in the REPL; `--task` always denies shell while
allowing automatic structured edits. Piped `yes` cannot authorize shell commands.

## Protocol and lifecycle bounds

Structured requests are stdin JSON `{"name": name, "arguments": object}` capped
at 2 MiB. Workers emit stdout JSON capped at 512 KiB; stderr must be empty for a
valid worker response. The parent rejects duplicate response keys, unexpected
schemas/types/error codes, bad UTF-8, nonzero exit, or truncated/invalid worker
results. Worker responses use `{ok, content, error, truncated}` or a restricted
`{ok:false,error}` failure. Shell stdout/stderr are separate untrusted observations,
not worker control JSON; results include status, returncode, and truncation.

The backend validates argv/input bounds and positive finite timeouts up to 300s.
Direct dispatcher calls default to 10s; chat supplies its freshly computed remaining
turn budget, capped at 300s. Approval and setup consume that supplied budget. Backend byte
limits default to 1 MiB each (trusted configurable range 0..16 MiB); dispatcher
uses 2 MiB stdin and 512 KiB each stdout/stderr. Nonblocking streaming writes and
drains avoid full-pipe deadlocks; exceeding an output cap terminates the supervised
call with bounded captured bytes. Cancellation and wall-clock deadlines are
checked during supervision, including probe/launch budgeting.

Each launch starts a new session/process group. The parent observes leader exit
without reaping, attempts SIGKILL of the group on every exit path (normal exit
included), then reaps the leader. It drains only immediately available bounded
output after leader exit instead of waiting indefinitely for survivor-held pipes.
Detached descendants can escape group cleanup while retaining Seatbelt and the
old filesystem permissions. Scratch/staging removal and later admission are not
proof that those processes stopped. Admission scans and synchronous filesystem/
kernel work are not hard real-time bounded; macOS provides no reliable hard
aggregate memory limit here.

## Verification limits

The parent independently ran the exact 10-file isolated command in the README:
**504 passed in 12.67s, no skips**. This is an actual consolidated case count, not
a sum of earlier component evidence (300 filesystem, 35 backend, 136
approval+tools, 33 CLI/loop/integration), which overlaps. Native fixture tests
include actual OS denials and mock-model chat, not live provider calls. This
documentation assignment checked commands by source inspection only and did not
rerun tests. Independent final source/security review subsequently completed with no
issues found under the approved trusted-local and best-effort lifecycle contract;
that review executed no tests and does not establish production certification.

The standard full-suite command remains `python -m pytest`. The baseline legacy
`test_re_module` double-escaped regex failure is unchanged; dataless `.venv`
imports have blocked full runs. The README provides an explicit isolated focused
command with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` and `--noconftest`: it omits root
provider fixtures and is **not full-suite validation**. No live provider
verification or blanket production certification is claimed.
