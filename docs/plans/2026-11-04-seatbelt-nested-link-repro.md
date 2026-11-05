# R1 parent verification: nested protected-link write bypass

Reviewer 3aa42e9d-7463-494e-9681-f00bcbebdb1a requested real enforcement confirmation. Parent used disposable /private/tmp/sb-review-* fixtures and current PUBLIC admit_workspace + build_seatbelt_profile. No source changes, real credentials, providers, or external-tree scanning. Fixtures cleaned by trusted TemporaryDirectory parent; subprocess timeout 8 seconds; no child spawning.

Topology (each case in a fresh fixture):
- workspace/.ssh OR workspace/.git -> workspace/plain-target (directory symlink)
- workspace/plain-target/hop -> scratch/plain-secret (directory symlink)
- scratch/plain-secret/canary = fixture-only-original
- runtime_roots = private empty runtime and actual Python sys.base_prefix

Command shape from student/: .venv/bin/python with a fixture harness; launch /usr/bin/sandbox-exec with profile.arguments(), resolved real interpreter -I -S -B -c syscall_probe, and workspace/<protected>/hop/canary. Minimal environment; close_fds; stdin DEVNULL. Probe independently opens read-only and write-only with os.open, records errno or actual data/write count; trusted parent reads canary afterward.

Results:
- .ssh: admission credential_targets contains only workspace/plain-target. Child read errno 1 and write errno 1; return code 0; canary unchanged. Do not claim credential disclosure from this topology.
- .git: admission git_targets contains only workspace/plain-target. Read returns fixture-only-original (expected read permission); write SUCCEEDS with 15 bytes; return code 0; trusted canary becomes fixture-mutatediginal. This is a confirmed confined-code write bypass of protected .git subtree semantics; no host race or post-admission mutation.

Parent sent measured results to reviewer and requested a bounded final review. R1 remains BLOCKED; do not activate or treat a green suite as covering this counterexample. Resolving classification through canonical protected directory targets must not read credential contents or silently assume nested links are absent. External target-tree traversal remains prohibited; unsupported cases requiring added admission restrictions must be escalated rather than silently accepted.
