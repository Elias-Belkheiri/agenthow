"""Per-call trusted-local admission, not race-proof filesystem enforcement.

The trusted parent supplies roots and must keep host processes from introducing
aliases, changing protected ancestry, or promoting writable/open inodes into
runtime authority until ALL descendants are gone. Never reuse a snapshot for a
later call. Scanning uses metadata only and never follows symlink trees.
"""

from collections import deque
from dataclasses import dataclass
import os
from pathlib import Path
import re
import stat


SYSTEM_RUNTIME = (Path('/bin'), Path('/usr/bin'), Path('/usr/lib'))
# These expressions are also the compiler's actual path-filter catalogue.
_CREDENTIAL_TREES = (
    r'/[.]env([.][^/]*)?/',
    r'/[.](ssh|aws|azure|kube)($|/)',
    r'/[.]config/gcloud($|/)',
    r'/[.]netrc($|/)',
)
_ENV_NAME = r'/[.]env($|[./])'
_ENV_SAMPLE = r'/[.]env[.](example|sample)$'
_GIT_TREE = r'/[.]git($|/)'
# A symlink at this partial catalogue prefix can hide the gcloud component
# from Seatbelt's canonical vnode path. Freeze it and protect its target suffix.
_CONFIG_LINK = r'/[.]config$'


def _matches(pattern: str, path: Path) -> bool:
    # Seatbelt's $ means actual end-of-path. Python's $ also matches BEFORE a
    # final newline, which would incorrectly exempt '.env.sample\n' files.
    # Seatbelt's path matching also recognizes ASCII case-equivalent names.
    return re.search(pattern.replace('$', r'\Z'), str(path),
                     flags=re.IGNORECASE | re.ASCII) is not None


def _credential_path(path: Path, *, directory: bool) -> bool:
    return (any(_matches(pattern, path) for pattern in _CREDENTIAL_TREES)
            or (_matches(_ENV_NAME, path)
                and (directory or not _matches(_ENV_SAMPLE, path))))


def _protected_path(path: Path, *, directory: bool) -> bool:
    return _credential_path(path, directory=directory) or _matches(_GIT_TREE, path)


def _same_node(first: Path, second: Path) -> bool:
    if first == second:
        return True
    try:
        return first.samefile(second)
    except FileNotFoundError:
        # Known credential locations and protected gcloud suffixes may not yet
        # exist. Other metadata errors still propagate and refuse admission.
        return False


def _contains(ancestor: Path, path: Path) -> bool:
    return any(_same_node(ancestor, candidate) for candidate in (path, *path.parents))


def _overlaps(first: Path, second: Path) -> bool:
    # resolve() alone does not normalize case aliases or macOS firmlinks.
    return _contains(first, second) or _contains(second, first)


def _root(path: Path) -> Path:
    if '\x00' in str(path):
        raise ValueError('NUL in sandbox root')
    root = Path(path).resolve(strict=True)
    home = Path.home().resolve(strict=True)
    broad = {Path('/usr'), Path('/System'), Path('/Library'), Path('/private'),
             Path('/var').resolve(), Path('/tmp').resolve(), Path('/Volumes'),
             Path('/Applications'), Path('/opt'), Path('/System/Volumes'),
             Path('/System/Volumes/Data'), home, *home.parents}
    if any(_same_node(root, ancestor) for ancestor in broad):
        raise ValueError('Sandbox root is too broad')
    if not root.is_dir():
        raise ValueError('Sandbox roots must be existing directories')
    return root


@dataclass(frozen=True)
class WorkspacePolicy:
    """Admission evidence for one call; compiler consumption remains private."""

    workspace: Path
    scratch: Path
    runtime_roots: tuple[Path, ...]
    protected_unlinks: tuple[Path, ...]
    credential_targets: tuple[Path, ...]
    git_targets: tuple[Path, ...]


def _protect_ancestors(path: Path, roots: tuple[Path, ...], protected: set[Path]) -> None:
    ancestors = (path, *path.parents)
    for root in roots:
        for index, ancestor in enumerate(ancestors):
            if _same_node(root, ancestor):
                protected.update(ancestors[:index + 1])
                break


def _resolve_protected_link(path: Path) -> tuple[Path, set[Path], set[Path]]:
    """Strict metadata-only resolution, retaining every retargetable chain node."""
    # Ask the kernel to validate the original spelling too (e.g. file/../dir
    # is not traversable). This follows metadata, not directory contents.
    path.stat()
    pending = deque(path.parts[1:])
    current = Path('/')
    links: set[Path] = set()
    directories: set[Path] = set()
    hops = 0
    while pending:
        component = pending.popleft()
        if component == '..':
            current = current.parent
            continue
        candidate = current / component
        metadata = candidate.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            hops += 1
            if hops > 40:
                raise ValueError('Filesystem admission: cyclic or excessive protected symlink chain')
            links.add(candidate)
            target = Path(os.readlink(candidate))
            if target.is_absolute():
                current = Path('/')
                parts = target.parts[1:]
            else:
                parts = target.parts
            pending.extendleft(reversed(parts))
        else:
            current = candidate
            if stat.S_ISDIR(metadata.st_mode):
                directories.add(current)
    return current, links, directories


def _scan(root: Path, nodes: dict[Path, os.stat_result],
          children: dict[Path, list[Path]]) -> None:
    """Index the writable namespace once, without following directory aliases."""
    nodes[root] = root.lstat()
    pending = [root]
    while pending:
        directory = pending.pop()
        children[directory] = []
        with os.scandir(directory) as entries:
            for entry in entries:
                path = directory / entry.name
                metadata = path.lstat()
                if stat.S_ISREG(metadata.st_mode) and metadata.st_nlink > 1:
                    raise ValueError(f'Unsupported writable hardlink: {path}')
                nodes[path] = metadata
                children[directory].append(path)
                if stat.S_ISDIR(metadata.st_mode):
                    pending.append(path)


def _protection_closure(
    writable: tuple[Path, ...], runtime: tuple[Path, ...],
    nodes: dict[Path, os.stat_result], children: dict[Path, list[Path]], protected: set[Path],
    credentials: set[Path], git: set[Path],
) -> None:
    # Physical identities normalize case/firmlink spellings back to the scanned
    # namespace. Each (node, classification) is visited at most once; an active
    # revisit is a directory-alias cycle, not an excuse to truncate protection.
    identities = {(info.st_dev, info.st_ino): path for path, info in nodes.items()}
    active: set[tuple[Path, bool]] = set()
    done: set[tuple[Path, bool]] = set()
    resolved: dict[Path, Path] = {}

    def inside(path: Path) -> bool:
        return any(path == root or root in path.parents for root in writable)

    def add_target(target: Path, targets: set[Path]) -> None:
        if any(_contains(target, root) for root in writable):
            raise ValueError('Protected symlink target conflicts with writable root')
        if any(_overlaps(target, root) for root in (*runtime, *SYSTEM_RUNTIME)):
            raise ValueError('Protected symlink target conflicts with runtime')
        targets.add(target)

    def resolve(path: Path) -> Path:
        if path in resolved:
            return resolved[path]
        target, chain, directories = _resolve_protected_link(path)
        info = target.stat()
        canonical = identities.get((info.st_dev, info.st_ino), target)
        if any(_overlaps(canonical, root) for root in (*runtime, *SYSTEM_RUNTIME)):
            raise ValueError('Protected symlink target conflicts with runtime')
        if stat.S_ISDIR(info.st_mode):
            # A protected directory may only traverse already scanned trees.
            # Ancestors needed to spell the roots are not external tree walks.
            if (not inside(canonical)
                    or any(not inside(directory) and not any(
                        _contains(directory, root) for root in writable
                    ) for directory in directories)
                    or any(not inside(link) for link in chain)):
                raise ValueError('Filesystem admission: protected directory alias outside scanned namespaces')
        # Even a directory consumed by '..' can redirect resolution if the
        # confined command replaces it with a symlink. Freeze those spellings
        # as well as the final target and every retargetable chain node.
        for node in chain | directories:
            _protect_ancestors(node, writable, protected)
            node_info = node.lstat()
            normalized = identities.get((node_info.st_dev, node_info.st_ino), node)
            _protect_ancestors(normalized, writable, protected)
        _protect_ancestors(canonical, writable, protected)
        resolved[path] = canonical
        return canonical

    # Seeds are lexical catalogue entries. Inheritance below canonical targets
    # supplies the otherwise invisible ordinary-name outbound aliases.
    seeds: list[tuple[Path, bool]] = []
    for path, info in nodes.items():
        directory = stat.S_ISDIR(info.st_mode)
        link = stat.S_ISLNK(info.st_mode)
        if _credential_path(path, directory=directory or link):
            seeds.append((path, True))
        if _matches(_GIT_TREE, path):
            seeds.append((path, False))
        if link and _matches(_CONFIG_LINK, path) and not _protected_path(path, directory=True):
            target = resolve(path)
            suffix = target / 'gcloud'
            if suffix in nodes:
                seeds.append((suffix, True))
            else:
                # Only this missing suffix is protected, not the whole .config
                # target. Its parent is frozen against replacement/promotion.
                try:
                    suffix_info = suffix.lstat()
                except FileNotFoundError:
                    add_target(suffix, credentials)
                    _protect_ancestors(suffix, writable, protected)
                else:
                    _protect_ancestors(suffix, writable, protected)
                    # Directory children are keyed by scanned spelling. Links
                    # must still resolve from their actual spelling, not a decoy.
                    if stat.S_ISDIR(suffix_info.st_mode):
                        suffix = identities[(suffix_info.st_dev, suffix_info.st_ino)]
                    seeds.append((suffix, True))

    for seed in seeds:
        pending = [(seed, False)]
        while pending:
            state, exiting = pending.pop()
            path, credential = state
            if exiting:
                active.remove(state)
                done.add(state)
                continue
            if state in active:
                raise ValueError('Filesystem admission: cyclic protected directory aliases')
            if state in done:
                continue
            active.add(state)
            pending.append((state, True))
            info = nodes.get(path)
            if info is None:
                # External regular-file targets need no enumeration. Directory
                # targets were refused by resolve() before reaching this state.
                info = path.lstat()
            directory = stat.S_ISDIR(info.st_mode)
            _protect_ancestors(path if directory else path.parent, writable, protected)
            targets = credentials if credential else git
            if stat.S_ISLNK(info.st_mode):
                target = resolve(path)
                add_target(target, targets)
                pending.append(((target, credential), False))
            elif directory:
                if state == seed:
                    add_target(path, targets)
                pending.extend(((child, credential), False) for child in children[path])


def admit_workspace(
    *, workspace: Path, scratch: Path, runtime_roots: tuple[Path, ...],
) -> WorkspacePolicy:
    """Validate trusted roots and scan EVERY writable entry, including hidden ones.

    Failure is final for this call: callers must not launch or fall back. Runtime
    roots are a trusted-parent allowlist (interpreter and staged worker only),
    not model arguments. Symlink nodes are examined, never their target trees.
    This deliberately does not identify copied secrets or arbitrary filenames.
    """
    try:
        workspace, scratch = _root(workspace), _root(scratch)
        runtime = tuple(_root(root) for root in runtime_roots)
        home = Path.home().resolve(strict=True)
        credential_paths = tuple(home / relative for relative in (
            '.ssh', '.aws', '.azure', '.kube', '.netrc', '.config/gcloud', '.env',
        ))
        # Keep lexical ancestors protected too when a known credential root is
        # itself a host symlink. Resolution reads metadata, never contents.
        credential_roots = (*credential_paths, *(
            path.resolve(strict=False) for path in credential_paths
        ))
        for root in (workspace, scratch, *runtime):
            if any(_overlaps(root, credential) for credential in credential_roots):
                raise ValueError('Sandbox root intersects a credential root')
            if _credential_path(root, directory=True):
                raise ValueError('Sandbox root is protected')
        for writable in (workspace, scratch):
            if _protected_path(writable, directory=True):
                raise ValueError('Writable root is protected')
            if any(_overlaps(writable, root) for root in (*runtime, *SYSTEM_RUNTIME)):
                raise ValueError('Writable roots must not overlap runtime')
        if _overlaps(workspace, scratch):
            raise ValueError('Workspace and scratch must not overlap')
        for index, root in enumerate(runtime):
            if any(_overlaps(root, other) for other in runtime[index + 1:]):
                raise ValueError('Runtime roots must not overlap')
        protected: set[Path] = set()
        credentials: set[Path] = set()
        git: set[Path] = set()
        writable = (workspace, scratch)
        nodes: dict[Path, os.stat_result] = {}
        children: dict[Path, list[Path]] = {}
        for root in writable:
            protected.add(root)
            _scan(root, nodes, children)
        _protection_closure(writable, runtime, nodes, children, protected, credentials, git)
        return WorkspacePolicy(workspace, scratch, runtime, tuple(sorted(protected)),
                               tuple(sorted(credentials)), tuple(sorted(git)))
    except (OSError, RuntimeError) as exc:
        raise ValueError('Filesystem admission could not determine policy') from exc
