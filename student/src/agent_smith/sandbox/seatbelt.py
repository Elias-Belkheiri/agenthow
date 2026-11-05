"""Experimental Seatbelt compiler, not a launcher or lifecycle supervisor.

Only trusted parent code may choose roots. The public builder admits them anew
on every call; there is no unchecked public/raw-profile path or fallback. Paths
are sandbox-exec parameters, never SBPL source. Execution remains gated on the
separate, unresolved descendant-lifetime boundary.
"""

from dataclasses import dataclass
from pathlib import Path

from .workspace_policy import (
    WorkspacePolicy, admit_workspace, _CREDENTIAL_TREES, _ENV_NAME,
    _ENV_SAMPLE, _GIT_TREE, _CONFIG_LINK,
)


_PROFILE = r'''
(version 1)
(deny default)
(allow process-fork)
(allow process-exec)
; libSystem startup opens the root directory; literal is not recursive.
(allow file-read* (literal "/"))
(allow file-read* file-map-executable
    (subpath "/bin")
    (subpath "/usr/bin")
    (subpath "/usr/lib"))
(allow sysctl-read
    (sysctl-name "kern.ostype")
    (sysctl-name "kern.osrelease")
    (sysctl-name "kern.version")
    (sysctl-name "kern.hostname")
    (sysctl-name "hw.machine"))
(allow file-read* file-write*
    (subpath (param "WORKSPACE"))
    (subpath (param "SCRATCH")))
(allow file-read* file-write-data (literal "/dev/null"))
; Hardlinking a readable runtime/.git inode must not make it writable.
(deny file-link)
'''


@dataclass(frozen=True)
class SeatbeltProfile:
    """Immutable compiler output; not an authority accepted from model input."""

    text: str
    parameters: tuple[tuple[str, str], ...]

    def arguments(self) -> tuple[str, ...]:
        arguments = []
        for key, value in self.parameters:
            arguments.extend(('-D', f'{key}={value}'))
        return (*arguments, '-p', self.text)


def _compile_policy(policy: WorkspacePolicy) -> SeatbeltProfile:
    """Unchecked private compiler: consume only a just-admitted parent snapshot."""
    parameters = [('WORKSPACE', str(policy.workspace)), ('SCRATCH', str(policy.scratch))]
    text = _PROFILE
    for index, root in enumerate(policy.runtime_roots):
        key = f'RUNTIME_{index}'
        parameters.append((key, str(root)))
        text += f'\n(allow file-read* file-map-executable (subpath (param "{key}")))\n'
    # Share the exact catalogue with admission, including the terminal-template
    # exception and the non-exempt directory/descendant cases.
    filters = '\n    '.join(f'(regex #"{pattern}")' for pattern in _CREDENTIAL_TREES)
    text += f'''
(deny file-read* file-write*
    {filters}
    (require-all
        (regex #"{_ENV_NAME}")
        (require-not (regex #"{_ENV_SAMPLE}"))))
(deny file-write* (regex #"{_GIT_TREE}"))
; Sample regular FILES remain editable. Directories and symlinks could
; invalidate protected-ancestor enumeration, just like other protected names.
(deny file-write-create
    (require-all
        (require-not (vnode-type REGULAR-FILE))
        (regex #"{_ENV_NAME}")))
; A new .config symlink could hide the catalogue's gcloud path component.
(deny file-write-create
    (require-all (vnode-type SYMLINK) (regex #"{_CONFIG_LINK}")))
; Exact admitted protected directories and ancestors, not a global unlink ban.
'''
    for index, target in enumerate(policy.credential_targets):
        key = f'CREDENTIAL_TARGET_{index}'
        parameters.append((key, str(target)))
        text += f'(deny file-read* file-write* (subpath (param "{key}")))\n'
    for index, target in enumerate(policy.git_targets):
        key = f'GIT_TARGET_{index}'
        parameters.append((key, str(target)))
        text += f'(deny file-write* (subpath (param "{key}")))\n'
    for index, directory in enumerate(policy.protected_unlinks):
        key = f'PROTECTED_{index}'
        parameters.append((key, str(directory)))
        text += f'(deny file-write-unlink (literal (param "{key}")))\n'
    return SeatbeltProfile(text, tuple(parameters))


def build_seatbelt_profile(
    *, workspace: Path, scratch: Path, runtime_roots: tuple[Path, ...],
) -> SeatbeltProfile:
    """Fresh trusted-parent admission followed by static parameterized compilation.

    Host cooperation is required from admission through descendant cleanup; this
    is not race-proof enforcement. Never cache for a subsequent tool call. An
    admission exception must refuse execution, never select an older profile.
    """
    return _compile_policy(admit_workspace(
        workspace=workspace, scratch=scratch, runtime_roots=runtime_roots,
    ))
