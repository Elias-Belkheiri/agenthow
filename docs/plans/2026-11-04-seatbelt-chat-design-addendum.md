# Approved protected-alias admission correction

User approved refusing execution when a protected directory symlink points outside the fully scanned workspace/scratch namespaces, because nested alias protection cannot safely be established without external-tree traversal. Ordinary external symlinks remain governed by existing sandbox denials; this is not blanket rejection of all symlinks.

Propagate credential/git protection transitively through nested aliases within the admitted writable namespace. Protect canonical destinations, retargetable chain nodes, and necessary ancestors. Preserve credential read/write versus git write-only semantics. Fail closed when closure is indeterminate, cyclic/unbounded, or conflicts with runtime/root authority; no fallback or external credential-tree enumeration.

Parent-confirmed regression: workspace/.git -> workspace/plain-target; plain-target/hop -> scratch/plain-secret; write through .git/hop/canary succeeds before correction. Fix under public admission/builder, not only private compiler tests. Read-only git access and ordinary cleanup must remain functional.

This approval changes only protected directory-alias support. The trusted-local threat model, live workspace writes, per-command approvals, hardlink admission, denied network, and unresolved mandatory descendant-cleanup gate remain unchanged. No chat activation is authorized by this correction alone.

Ownership: one worker edits R1 files only, then fresh read-only review; all other integration lanes remain gated.
