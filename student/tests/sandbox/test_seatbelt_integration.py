"""Real Seatbelt feasibility evidence; not a production process supervisor."""

import errno
import os
from pathlib import Path
import platform
import stat
import subprocess
import sys
import tempfile

import pytest

from agent_smith.sandbox.seatbelt import build_seatbelt_profile


pytestmark = pytest.mark.skipif(
    platform.system() != 'Darwin', reason='Seatbelt requires macOS',
)


@pytest.fixture
def roots():
    # Never use repository files or real credentials as denial canaries.
    with tempfile.TemporaryDirectory(prefix='sb-', dir='/private/tmp') as directory:
        base = Path(directory).resolve()
        workspace = base / 'workspace "quotes" (parentheses)\nline'
        scratch = base / 'scratch'
        runtime = base / 'runtime'
        for root in (workspace, scratch, runtime):
            root.mkdir()
        yield workspace, scratch, runtime, base


def profile_for(roots):
    workspace, scratch, runtime, _ = roots
    return build_seatbelt_profile(
        workspace=workspace, scratch=scratch,
        runtime_roots=(runtime, Path(sys.base_prefix).resolve()),
    )


def run_confined(roots, command):
    workspace, scratch, _, _ = roots
    profile = profile_for(roots)
    return subprocess.run(
        ['/usr/bin/sandbox-exec', *profile.arguments(), *command],
        cwd=workspace,
        env={'PATH': '/usr/bin:/bin', 'HOME': str(scratch),
             'TMPDIR': str(scratch), 'LC_ALL': 'C'},
        stdin=subprocess.DEVNULL, capture_output=True, text=True,
        close_fds=True, timeout=10,
    )


def run_python(roots, code, *args):
    # The real uv installation, not a workspace-owned venv or startup hooks.
    return run_confined(
        roots, [str(Path(sys.executable).resolve()), '-I', '-S', '-B',
                '-c', code, *(str(arg) for arg in args)],
    )


def assert_ok(result):
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)


def test_shell_startup_and_selected_writes(roots):
    result = run_confined(roots, ['/bin/sh', '-c',
        'printf workspace > output; printf scratch > "$TMPDIR/output"; '
        '/bin/cat output "$TMPDIR/output"'])
    assert_ok(result)
    assert result.stdout == 'workspacescratch'


def test_uv_python_isolated_startup_and_stdlib(roots):
    workspace, scratch, _, _ = roots
    (workspace / 'sitecustomize.py').write_text('raise RuntimeError("untrusted")')
    (workspace / 'json.py').write_text('raise RuntimeError("untrusted")')
    result = run_python(roots, '''
import sys, json, math, ctypes, socket, subprocess, pathlib, hashlib
assert sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode
assert '' not in sys.path
pathlib.Path(sys.argv[1], 'python-output').write_text('ok')
print(json.dumps({'sqrt': math.sqrt(9), 'digest': hashlib.sha256(b'x').hexdigest()}))
''', scratch)
    assert_ok(result)
    assert '"sqrt": 3.0' in result.stdout
    assert (scratch / 'python-output').read_text() == 'ok'


def test_parameterized_paths_are_not_sbpl_source(roots):
    workspace, scratch, _, _ = roots
    profile = profile_for(roots)
    assert str(workspace) not in profile.text
    assert str(scratch) not in profile.text
    assert '(deny default)' in profile.text
    assert_ok(run_python(roots, 'import pathlib; pathlib.Path("escaped").write_text("ok")'))
    assert (workspace / 'escaped').read_text() == 'ok'


def test_outside_reads_and_writes_denied(roots):
    _, _, _, base = roots
    canary = base / 'outside-canary'
    canary.write_text('fixture-only-secret')
    result = run_python(roots, '''
import errno, os, sys
for flags in (os.O_RDONLY, os.O_WRONLY, os.O_WRONLY | os.O_CREAT):
    try:
        fd = os.open(sys.argv[1], flags, 0o600)
    except OSError as exc:
        assert exc.errno in (errno.EACCES, errno.EPERM), exc
    else:
        os.close(fd)
        raise AssertionError('outside access permitted')
print('denied')
''', canary)
    assert_ok(result)
    assert result.stdout == 'denied\n'
    assert canary.read_text() == 'fixture-only-secret'


@pytest.mark.parametrize('relative', [
    '.env', '.env.local', 'nested/.env.production', '.ssh/id_ed25519',
    '.aws/credentials', '.config/gcloud/application_default_credentials.json',
    '.azure/accessTokens.json', '.kube/config', '.netrc',
])
def test_fixture_credentials_denied(roots, relative):
    workspace, _, _, _ = roots
    canary = workspace / relative
    canary.parent.mkdir(parents=True, exist_ok=True)
    canary.write_text('fixture-only-secret')
    result = run_python(roots, '''
import errno, os, sys
for flags in (os.O_RDONLY, os.O_WRONLY):
    try:
        fd = os.open(sys.argv[1], flags)
    except OSError as exc:
        assert exc.errno in (errno.EACCES, errno.EPERM), exc
    else:
        os.close(fd)
        raise AssertionError('credential access permitted')
''', canary)
    assert_ok(result)
    assert canary.read_text() == 'fixture-only-secret'


@pytest.mark.parametrize('name', ['.env.example', '.env.sample',
                                  'nested/.env.example', 'nested/.env.sample'])
def test_exact_sample_env_templates_are_allowed(roots, name):
    workspace, _, _, _ = roots
    canary = workspace / name
    canary.parent.mkdir(parents=True, exist_ok=True)
    canary.write_text('nonsecret-example')
    result = run_python(roots, '''
from pathlib import Path
import sys
p = Path(sys.argv[1])
assert p.read_text() == 'nonsecret-example'
p.write_text('edited-example')
''', canary)
    assert_ok(result)
    assert canary.read_text() == 'edited-example'


@pytest.mark.parametrize('relative', ['.env/.env.example',
                                     '.env.local/nested/.env.sample'])
@pytest.mark.parametrize('access', ['read', 'write'])
def test_sample_env_template_under_protected_ancestor_denied(roots, relative, access):
    workspace, _, _, _ = roots
    canary = workspace / relative
    # Trusted parent creates both protected directories and the existing canary.
    canary.parent.mkdir(parents=True)
    canary.write_text('fixture-only-secret')
    result = run_python(roots, '''
import errno, os, sys
access = sys.argv[2]
try:
    fd = os.open(sys.argv[1], os.O_RDONLY if access == 'read' else os.O_WRONLY)
except OSError as exc:
    assert exc.errno in (errno.EACCES, errno.EPERM), exc
else:
    try:
        if access == 'read':
            assert os.read(fd, 100) == b'fixture-only-secret'
        else:
            assert os.write(fd, b'changed') == 7
    finally:
        os.close(fd)
    raise AssertionError('protected ancestor template ' + access + ' permitted')
print(access + ' denied')
''', canary, access)
    assert_ok(result)
    assert result.stdout == access + ' denied\n'
    assert canary.read_text() == 'fixture-only-secret'


def test_git_metadata_read_allowed_write_denied(roots):
    workspace, _, _, _ = roots
    metadata = workspace / 'nested/.git'
    metadata.mkdir(parents=True)
    canary = metadata / 'config'
    canary.write_text('fixture-only-git-config')
    result = run_python(roots, '''
import errno, os, pathlib, sys
p = pathlib.Path(sys.argv[1])
assert p.read_text() == 'fixture-only-git-config'
for action in (lambda: p.write_text('bad'), lambda: p.unlink(),
               lambda: p.rename(p.with_name('moved')),
               lambda: (p.parent / 'new-file').write_text('bad')):
    try:
        action()
    except OSError as exc:
        assert exc.errno in (errno.EACCES, errno.EPERM), exc
    else:
        raise AssertionError('metadata mutation permitted')
''', canary)
    assert_ok(result)
    assert canary.read_text() == 'fixture-only-git-config'


@pytest.mark.parametrize('child_failure', [None, 'startup', 'command'])
def test_children_inherit_filesystem_denials(roots, child_failure):
    workspace, _, _, base = roots
    allowed = workspace / 'child-allowed'
    allowed.write_text('fixture-only-allowed')
    outside = base / 'child-canary'
    outside.write_text('fixture-only-secret')
    result = run_python(roots, '''
import subprocess, sys
probe = """
import errno, os, sys
fd = os.open(sys.argv[1], os.O_RDONLY)
try:
    assert os.read(fd, 100) == b'fixture-only-allowed'
finally:
    os.close(fd)
try:
    fd = os.open(sys.argv[2], os.O_RDONLY)
except OSError as exc:
    assert exc.errno in (errno.EACCES, errno.EPERM), exc
else:
    os.close(fd)
    raise AssertionError('descendant outside access permitted')
print('child allowed read and outside denied')
"""
options = ['-I', '-S', '-B']
if sys.argv[3] == 'startup':
    options.append('--seatbelt-invalid-option')
elif sys.argv[3] == 'command':
    probe = 'raise RuntimeError("descendant command failure")\\n' + probe
p = subprocess.run([sys.executable, *options, '-c', probe, *sys.argv[1:3]],
                   capture_output=True, text=True, close_fds=True, timeout=5)
assert p.returncode == 0, (p.returncode, p.stdout, p.stderr)
assert p.stdout == 'child allowed read and outside denied\\n', (p.stdout, p.stderr)
print(p.stdout, end='')
''', allowed, outside, child_failure)
    if child_failure is None:
        assert_ok(result)
        assert result.stdout == 'child allowed read and outside denied\n'
    else:
        # Real startup/command failures must fail the same success gate.
        with pytest.raises(AssertionError):
            assert_ok(result)
        assert result.stdout == ''
        assert ('--seatbelt-invalid-option' if child_failure == 'startup'
                else 'descendant command failure') in result.stderr
    assert allowed.read_text() == 'fixture-only-allowed'
    assert outside.read_text() == 'fixture-only-secret'


@pytest.mark.parametrize('kind', ['outbound', 'loopback', 'unix', 'unix-bind'])
def test_network_sockets_denied(roots, kind):
    import socket
    _, scratch, _, _ = roots
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tcp_listener:
        tcp_listener.bind(('127.0.0.1', 0))
        tcp_listener.listen(1)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as unix_listener:
            unix_path = scratch / 'listener.sock'
            unix_listener.bind(str(unix_path))
            unix_listener.listen(1)
            result = run_python(roots, '''
import errno, socket, sys
kind = sys.argv[1]
s = socket.socket(socket.AF_UNIX if kind.startswith('unix') else socket.AF_INET,
                  socket.SOCK_STREAM)
s.settimeout(0.3)
try:
    if kind == 'unix-bind':
        s.bind(sys.argv[4] + '.child')
    elif kind == 'unix':
        s.connect(sys.argv[4])
    else:
        s.connect(('127.0.0.1', int(sys.argv[2])) if kind == 'loopback'
                  else ('192.0.2.1', 9))
except OSError as exc:
    assert exc.errno in (errno.EACCES, errno.EPERM), repr(exc)
else:
    raise AssertionError('network permitted')
finally:
    s.close()
print('network denied')
''', kind, tcp_listener.getsockname()[1], 'unused', unix_path)
    assert_ok(result)
    assert result.stdout == 'network denied\n'


@pytest.mark.parametrize('relative', ['.git', '.ssh', 'parent/.git', 'parent/.ssh'])
def test_protected_subtree_rename_denied(roots, relative):
    workspace, _, _, _ = roots
    protected = workspace / relative
    protected.mkdir(parents=True)
    (protected / 'canary').write_text('fixture-only-protected')
    # Renaming an ancestor can move a denied subtree without touching its files.
    source = workspace / relative.split('/')[0]
    result = run_python(roots, '''
import errno, os, sys
try:
    os.rename(sys.argv[1], sys.argv[2])
except OSError as exc:
    assert exc.errno in (errno.EACCES, errno.EPERM), exc
else:
    raise AssertionError('protected subtree renamed')
''', source, workspace / 'moved')
    assert_ok(result)
    assert (protected / 'canary').read_text() == 'fixture-only-protected'


def test_ordinary_directory_rename_and_rmdir_succeed(roots):
    workspace, _, _, _ = roots
    directory = workspace / 'ordinary'
    directory.mkdir()
    result = run_python(roots, """
import os, sys
os.rename(sys.argv[1], sys.argv[2])
os.rmdir(sys.argv[2])
""", directory, workspace / 'ordinary-moved')
    assert_ok(result)
    assert not directory.exists()
    assert not (workspace / 'ordinary-moved').exists()


def test_mkdir_file_creation_and_atomic_replacement_still_work(roots):
    workspace, _, _, _ = roots
    result = run_python(roots, '''
from pathlib import Path
import os
p = Path('created')
p.mkdir()
(p / 'original').write_text('before')
(p / 'temporary').write_text('after')
os.replace(p / 'temporary', p / 'original')
assert (p / 'original').read_text() == 'after'
(p / 'original').unlink()
''')
    assert_ok(result)
    assert (workspace / 'created').is_dir()


@pytest.mark.parametrize('relative', ['.git', '.ssh', 'parent/.git', 'parent/.ssh'])
def test_protected_directory_deletion_denied(roots, relative):
    workspace, _, _, _ = roots
    directory = workspace / relative
    directory.mkdir(parents=True)
    result = run_python(roots, '''
import errno, os, sys
try:
    os.rmdir(sys.argv[1])
except OSError as exc:
    assert exc.errno in (errno.EACCES, errno.EPERM), exc
else:
    raise AssertionError('protected directory deleted')
''', directory)
    assert_ok(result)
    assert directory.is_dir()


@pytest.mark.parametrize('destination', ['renamed', '.git2', 'nested/renamed'])
def test_protected_ancestor_alternate_destinations_denied(roots, destination):
    workspace, _, _, _ = roots
    parent = workspace / 'parent'
    (parent / '.git').mkdir(parents=True)
    (workspace / 'nested').mkdir()
    result = run_python(roots, '''
import errno, os, sys
try:
    os.replace(sys.argv[1], sys.argv[2])
except OSError as exc:
    assert exc.errno in (errno.EACCES, errno.EPERM), exc
else:
    raise AssertionError('protected ancestor moved')
''', parent, workspace / destination)
    assert_ok(result)
    assert (parent / '.git').is_dir()


def test_workspace_root_rename_and_replacement_denied(roots):
    workspace, _, _, base = roots
    replacement = base / 'replacement'
    replacement.mkdir()
    result = run_python(roots, '''
import errno, os, sys
for src, dst in ((sys.argv[1], sys.argv[2]), (sys.argv[3], sys.argv[1])):
    try:
        os.rename(src, dst)
    except OSError as exc:
        assert exc.errno in (errno.EACCES, errno.EPERM), exc
    else:
        raise AssertionError('workspace root replaced')
''', workspace, base / 'moved-root', replacement)
    assert_ok(result)
    assert workspace.is_dir() and replacement.is_dir()


@pytest.mark.parametrize('alias_kind', ['file', 'directory', 'credential'])
def test_symlink_escapes_denied(roots, alias_kind):
    workspace, _, _, base = roots
    outside = base / 'outside'
    outside.mkdir()
    canary = outside / 'canary'
    canary.write_text('fixture-only-secret')
    if alias_kind == 'credential':
        canary = workspace / '.env'
        canary.write_text('fixture-only-secret')
    alias = workspace / 'alias'
    alias.symlink_to(outside if alias_kind == 'directory' else canary)
    target = alias / 'canary' if alias_kind == 'directory' else alias
    result = run_python(roots, '''
import errno, os, sys
for flags in (os.O_RDONLY, os.O_WRONLY):
    try:
        fd = os.open(sys.argv[1], flags)
    except OSError as exc:
        assert exc.errno in (errno.EACCES, errno.EPERM), exc
    else:
        os.close(fd)
        raise AssertionError('symlink escaped policy')
''', target)
    assert_ok(result)
    assert canary.read_text() == 'fixture-only-secret'


@pytest.mark.parametrize('overlap', ['equal', 'workspace_contains_runtime',
                                     'runtime_contains_workspace', 'scratch_runtime'])
def test_writable_runtime_overlaps_rejected(roots, overlap):
    workspace, scratch, runtime, _ = roots
    if overlap == 'equal':
        runtime = workspace
    elif overlap == 'workspace_contains_runtime':
        runtime = workspace / 'runtime'
        runtime.mkdir()
    elif overlap == 'runtime_contains_workspace':
        workspace = runtime / 'workspace'
        workspace.mkdir()
    else:
        runtime = scratch
    with pytest.raises(ValueError, match='overlap runtime'):
        build_seatbelt_profile(workspace=workspace, scratch=scratch,
                               runtime_roots=(runtime,))


def test_private_runtime_is_read_only(roots):
    _, _, runtime, _ = roots
    canary = runtime / 'worker.py'
    canary.write_text('fixture-only-runtime')
    result = run_python(roots, '''
import errno, pathlib, sys
p = pathlib.Path(sys.argv[1])
assert p.read_text() == 'fixture-only-runtime'
try:
    p.write_text('bad')
except OSError as exc:
    assert exc.errno in (errno.EACCES, errno.EPERM), exc
else:
    raise AssertionError('runtime modified')
''', canary)
    assert_ok(result)
    assert canary.read_text() == 'fixture-only-runtime'


@pytest.mark.parametrize('relative', ['.env.examples', '.env.sample.local',
                                     '.env.example/.env', 'nested/.env.sample.local'])
def test_sample_env_exemption_is_exact(roots, relative):
    test_fixture_credentials_denied(roots, relative)


@pytest.mark.parametrize('kind', ['outside', 'credential', 'git', 'runtime'])
def test_child_cannot_create_hard_link_from_denied_source(roots, kind):
    workspace, _, runtime, base = roots
    source = (base / 'outside-link-source' if kind == 'outside' else
              workspace / ('.env' if kind == 'credential' else '.git/config'))
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text('fixture-only-link-source')
    if kind == 'runtime':
        source = runtime / 'readable-runtime'
        source.write_text('fixture-only-link-source')
    alias = workspace / 'new-alias'
    result = run_python(roots, '''
import errno, os, sys
try:
    os.link(sys.argv[1], sys.argv[2])
except OSError as exc:
    assert exc.errno in (errno.EACCES, errno.EPERM), exc
else:
    raise AssertionError('hard link from denied source created')
''', source, alias)
    assert_ok(result)
    assert not alias.exists()


@pytest.mark.parametrize('kind', ['outside', 'credential', 'git'])
def test_preexisting_hard_link_alias_is_rejected_at_admission(roots, kind):
    workspace, _, _, base = roots
    source = (base / 'outside-link-source' if kind == 'outside' else
              workspace / ('.env' if kind == 'credential' else '.git/config'))
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text('fixture-only-link-source')
    alias = workspace / 'preexisting-alias'
    os.link(source, alias)
    assert source.stat().st_ino == alias.stat().st_ino
    with pytest.raises(ValueError, match='hardlink'):
        run_python(roots, 'raise AssertionError("must not launch")')
    assert source.read_text() == 'fixture-only-link-source'


def test_mach_service_lookup_denied(roots):
    import ctypes
    # Establish a real, available service without sending it any request.
    lib = ctypes.CDLL('/usr/lib/libSystem.B.dylib')
    bootstrap = ctypes.c_uint.in_dll(lib, 'bootstrap_port').value
    port = ctypes.c_uint()
    lib.bootstrap_look_up.argtypes = [ctypes.c_uint, ctypes.c_char_p,
                                      ctypes.POINTER(ctypes.c_uint)]
    assert lib.bootstrap_look_up(bootstrap, b'com.apple.cfprefsd.agent',
                                 ctypes.byref(port)) == 0
    lib.mach_port_deallocate(lib.mach_task_self(), port.value)
    result = run_python(roots, '''
import ctypes
lib = ctypes.CDLL('/usr/lib/libSystem.B.dylib')
bootstrap = ctypes.c_uint.in_dll(lib, 'bootstrap_port').value
port = ctypes.c_uint()
lib.bootstrap_look_up.argtypes = [ctypes.c_uint, ctypes.c_char_p,
                                 ctypes.POINTER(ctypes.c_uint)]
rc = lib.bootstrap_look_up(bootstrap, b'com.apple.cfprefsd.agent', ctypes.byref(port))
assert rc != 0, 'Mach service reachable'
print('mach denied')
''')
    assert_ok(result)
    assert result.stdout == 'mach denied\n'


def test_shell_workflow_and_stdlib_test_workflow(roots):
    workspace, _, _, _ = roots
    result = run_confined(roots, ['/bin/sh', '-c',
        '/bin/mkdir created; printf text > created/file; /bin/ls created; '
        '/usr/bin/grep text created/file; /bin/mv created/file created/renamed; '
        '/bin/rm created/renamed; /bin/rmdir created'])
    assert_ok(result)
    assert result.stdout == 'file\ntext\n'
    (workspace / 'test_example.py').write_text('''
import unittest
from pathlib import Path
class Example(unittest.TestCase):
    def test_file(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory, 'test-output')
            output.write_text('ok')
            self.assertEqual(output.read_text(), 'ok')
        self.assertFalse(Path(directory).exists())
''')
    result = run_confined(roots, [str(Path(sys.executable).resolve()),
        '-I', '-S', '-B', '-m', 'unittest', 'discover', '-s', '.', '-v'])
    assert_ok(result)
    assert 'Ran 1 test' in result.stderr


def test_tempfile_cleanup_succeeds(roots):
    result = run_python(roots, """
import pathlib, tempfile
with tempfile.TemporaryDirectory() as directory:
    pathlib.Path(directory, 'ordinary-file').write_text('ok')
assert not pathlib.Path(directory).exists()
print('cleanup succeeded')
""")
    assert_ok(result)
    assert result.stdout == 'cleanup succeeded\n'


# Shared admission/profile catalogue, including non-basename gcloud and sample
# DIRECTORY cases. Both writable roots need the same ancestor protection.
_PROTECTED_DIRECTORIES = [
    '.git', '.ssh', '.aws', '.azure', '.kube', '.netrc', '.config/gcloud',
    '.env', '.env.local', '.env.example', '.env.sample',
]


@pytest.mark.parametrize('relative', _PROTECTED_DIRECTORIES)
@pytest.mark.parametrize('root_index', [0, 1])
def test_protected_parent_source_and_replacement_destination_denied(roots, relative, root_index):
    root = roots[root_index]
    # Empty targets also matter: rename can replace an empty directory without
    # enumerating or accessing its children. Nonempty parents must not move.
    parent = root / 'protected-parent'
    protected = parent / relative
    protected.mkdir(parents=True)
    replacement = root / 'replacement'
    replacement.mkdir()
    result = run_python(roots, '''
import errno, os, sys
parent, protected, replacement, moved = sys.argv[1:]
for action in (lambda: os.rename(parent, moved),
               lambda: os.rmdir(parent),
               lambda: os.rmdir(protected),
               lambda: os.replace(replacement, protected),
               lambda: os.replace(replacement, parent)):
    try:
        action()
    except OSError as exc:
        assert exc.errno in (errno.EACCES, errno.EPERM), exc
    else:
        raise AssertionError('protected source or destination mutated')
''', parent, protected, replacement, root / 'moved')
    assert_ok(result)
    assert protected.is_dir() and replacement.is_dir()


@pytest.mark.parametrize('relative', _PROTECTED_DIRECTORIES)
def test_scratch_to_workspace_move_carrying_protected_content_denied(roots, relative):
    workspace, scratch, _, _ = roots
    source = scratch / 'carrier'
    (source / relative).mkdir(parents=True)
    (source / relative / 'canary').write_text('fixture-only-protected')
    result = run_python(roots, '''
import errno, os, sys
try:
    os.rename(sys.argv[1], sys.argv[2])
except OSError as exc:
    assert exc.errno in (errno.EACCES, errno.EPERM), exc
else:
    raise AssertionError('protected content carried across roots')
''', source, workspace / 'innocent')
    assert_ok(result)
    assert (source / relative / 'canary').read_text() == 'fixture-only-protected'


@pytest.mark.parametrize('relative', _PROTECTED_DIRECTORIES)
@pytest.mark.parametrize('root_index', [0, 1])
def test_new_protected_names_cannot_invalidate_enumeration(roots, relative, root_index):
    root = roots[root_index]
    destination = root / 'ordinary' / relative
    destination.parent.mkdir(parents=True)
    source = root / 'new-source'
    source.mkdir()
    result = run_python(roots, '''
import errno, os, sys
source, destination = sys.argv[1:]
for action in (lambda: os.mkdir(destination),
               lambda: os.rename(source, destination)):
    try:
        action()
    except OSError as exc:
        assert exc.errno in (errno.EACCES, errno.EPERM), exc
    else:
        raise AssertionError('new protected directory name admitted')
''', source, destination)
    assert_ok(result)
    assert source.is_dir() and not destination.exists()


@pytest.mark.parametrize('relative', ['.git', '.ssh', '.env.sample'])
def test_symlink_target_traversal_cannot_mutate_protected_tree(roots, relative):
    workspace, _, _, _ = roots
    protected = workspace / 'parent' / relative
    protected.mkdir(parents=True)
    canary = protected / 'canary'
    canary.write_text('fixture-only-protected')
    alias = workspace / 'alias'
    alias.symlink_to(protected, target_is_directory=True)
    result = run_python(roots, '''
import errno, pathlib, os, sys
p = pathlib.Path(sys.argv[1])
for action in (lambda: (p / 'canary').write_text('bad'),
               lambda: (p / 'canary').unlink(),
               lambda: (p / 'new').write_text('bad'),
               lambda: os.rename(p / 'canary', p.parent / 'moved')):
    try:
        action()
    except OSError as exc:
        assert exc.errno in (errno.EACCES, errno.EPERM), exc
    else:
        raise AssertionError('protected symlink target mutated')
''', alias)
    assert_ok(result)
    assert canary.read_text() == 'fixture-only-protected'


@pytest.mark.parametrize('relative', ['.env.example\n', '.env.sample\n', '.env.sample.local'])
def test_sample_exemption_requires_exact_filename(roots, relative):
    test_fixture_credentials_denied(roots, relative)


def test_unchecked_profile_alone_cannot_detect_host_introduced_alias(roots):
    # Explicit NON-guarantee, using a deliberately stale snapshot and PRIVATE
    # compiler. Production must use fresh public admission, not this test path.
    from agent_smith.sandbox.workspace_policy import admit_workspace
    from agent_smith.sandbox.seatbelt import _compile_policy
    workspace, scratch, runtime, _ = roots
    policy = admit_workspace(workspace=workspace, scratch=scratch,
        runtime_roots=(runtime, Path(sys.base_prefix).resolve()))
    source = runtime / 'fixture-only-runtime'
    source.write_text('fixture-only-original')
    alias = workspace / 'host-alias'
    os.link(source, alias)  # trusted host violates the cooperation contract
    with pytest.raises(ValueError, match='hardlink'):
        profile_for(roots)
    profile = _compile_policy(policy)
    result = subprocess.run(
        ['/usr/bin/sandbox-exec', *profile.arguments(),
         str(Path(sys.executable).resolve()), '-I', '-S', '-B', '-c',
         'from pathlib import Path; Path("host-alias").write_text("alias-write")'],
        cwd=workspace, env={'PATH': '/usr/bin:/bin', 'HOME': str(scratch),
                            'TMPDIR': str(scratch), 'LC_ALL': 'C'},
        stdin=subprocess.DEVNULL, capture_output=True, text=True,
        close_fds=True, timeout=10,
    )
    assert_ok(result)
    assert source.read_text() == 'alias-write'


@pytest.mark.parametrize('relative', ['.env', '.env.local', '.env.sample\n',
    '.env.example\n', '.netrc', '.git', '.config/gcloud', '.ssh'])
@pytest.mark.parametrize('root_index', [0, 1])
def test_protected_file_ancestors_cannot_be_relocated(roots, relative, root_index):
    root = roots[root_index]
    parent = root / 'carrier'
    canary = parent / relative
    canary.parent.mkdir(parents=True)
    canary.write_text('fixture-only-protected')
    result = run_python(roots, '''
import errno, os, sys
try:
    os.rename(sys.argv[1], sys.argv[2])
except OSError as exc:
    assert exc.errno in (errno.EACCES, errno.EPERM), exc
else:
    raise AssertionError('protected file ancestor relocated')
''', parent, root / 'moved')
    assert_ok(result)
    assert canary.read_text() == 'fixture-only-protected'


@pytest.mark.parametrize('relative,alias_relative', [('.env', '.ENV'),
    ('.ssh/canary', '.SSH/canary'), ('.git/config', '.GIT/config')])
def test_existing_protected_files_cannot_be_mutated_through_case_aliases(roots, relative, alias_relative):
    workspace, _, _, _ = roots
    canary = workspace / relative
    canary.parent.mkdir(parents=True, exist_ok=True)
    canary.write_text('fixture-only-protected')
    alias = workspace / alias_relative
    if not alias.exists():
        pytest.skip('fixture volume is case-sensitive: no case alias')
    result = run_python(roots, '''
import errno, pathlib, sys
try:
    pathlib.Path(sys.argv[1]).write_text('bad')
except OSError as exc:
    assert exc.errno in (errno.EACCES, errno.EPERM), exc
else:
    raise AssertionError('case alias mutated protected file')
''', alias)
    assert_ok(result)
    assert canary.read_text() == 'fixture-only-protected'


@pytest.mark.parametrize('sample', ['.env.example', '.env.sample'])
def test_sample_names_are_only_creatable_as_regular_files(roots, sample):
    workspace, _, _, _ = roots
    target = workspace / 'target'
    target.mkdir()
    result = run_python(roots, '''
import errno, os, pathlib, sys
sample, target = sys.argv[1:]
for action in (lambda: os.symlink(target, sample),
               lambda: os.mkfifo(sample)):
    try:
        action()
    except OSError as exc:
        assert exc.errno in (errno.EACCES, errno.EPERM), exc
    else:
        raise AssertionError('nonregular sample name created')
p = pathlib.Path(sample)
p.write_text('before')
replacement = p.with_name('temporary')
replacement.write_text('after')
os.replace(replacement, p)
assert p.read_text() == 'after'
p.unlink()
''', workspace / sample, target)
    assert_ok(result)


@pytest.mark.parametrize('relative', ['.env', '.ssh', '.git', '.env.sample'])
def test_protected_symlink_names_do_not_exempt_their_writable_targets(roots, relative):
    workspace, _, _, _ = roots
    target = workspace / 'ordinary-target'
    if relative == '.env':
        target.write_text('fixture-only-protected')
        access = workspace / relative
    else:
        target.mkdir()
        (target / 'canary').write_text('fixture-only-protected')
        access = workspace / relative / 'canary'
    (workspace / relative).symlink_to(target, target_is_directory=target.is_dir())
    result = run_python(roots, '''
import errno, pathlib, sys
try:
    pathlib.Path(sys.argv[1]).write_text('bad')
except OSError as exc:
    assert exc.errno in (errno.EACCES, errno.EPERM), exc
else:
    raise AssertionError('protected symlink name exposed writable target')
''', access)
    assert_ok(result)


@pytest.mark.parametrize('relative', ['.env', '.ssh', '.git', '.env.sample', '.config'])
@pytest.mark.parametrize('root_index', [0, 1])
def test_protected_symlink_creation_and_rename_are_denied(roots, relative, root_index):
    root = roots[root_index]
    target = root / 'ordinary-target'
    target.mkdir()
    source = root / 'ordinary-alias'
    source.symlink_to(target, target_is_directory=True)
    result = run_python(roots, '''
import errno, os, sys
source, target, destination = sys.argv[1:]
for action in (lambda: os.symlink(target, destination),
               lambda: os.rename(source, destination)):
    try:
        action()
    except OSError as exc:
        assert exc.errno in (errno.EACCES, errno.EPERM), exc
    else:
        raise AssertionError('unenumerated protected alias created')
''', source, target, root / relative)
    assert_ok(result)
    assert source.is_symlink() and not (root / relative).exists()


@pytest.mark.parametrize('relative', ['.env', '.ssh', '.git', '.env.sample', '.config'])
@pytest.mark.parametrize('external', [False, True])
def test_protected_symlink_chains_frozen_and_canonical_targets_denied(roots, relative, external):
    workspace, scratch, _, base = roots
    target_parent = base / 'external' if external else scratch / 'parent'
    target = target_parent / 'ordinary-target'
    target.mkdir(parents=True)
    if relative == '.config':
        protected_target = target / 'gcloud'
        protected_target.mkdir()
    else:
        protected_target = target
    canary = protected_target / 'canary'
    canary.write_text('fixture-only-protected')
    chain = workspace / 'chain'
    chain.symlink_to(target, target_is_directory=True)
    link = workspace / relative
    link.symlink_to(chain, target_is_directory=True)
    replacement = workspace / 'replacement'
    replacement.symlink_to(scratch, target_is_directory=True)
    empty = scratch / 'empty'
    empty.mkdir()
    if external:
        with pytest.raises(ValueError, match='outside scanned'):
            run_python(roots, 'raise AssertionError("must not launch")')
        assert canary.read_text() == 'fixture-only-protected'
        return
    # .git keeps read access only where an ordinary read grant already exists.
    result = run_python(roots, '''
import errno, pathlib, os, sys
link, chain, canary, target, parent, replacement, empty, relative, external = sys.argv[1:]
def denied(action):
    try:
        action()
    except OSError as exc:
        assert exc.errno in (errno.EACCES, errno.EPERM), exc
    else:
        raise AssertionError('protected alias/target mutation permitted')
for action in (lambda: os.unlink(link),
               lambda: os.replace(replacement, link),
               lambda: os.rename(link, link + '-moved'),
               lambda: os.unlink(chain),
               lambda: os.replace(replacement, chain),
               lambda: pathlib.Path(canary).write_text('bad'),
               lambda: os.rename(parent, parent + '-moved'),
               lambda: os.replace(empty, target)):
    denied(action)
if relative == '.git' and external == 'False':
    assert pathlib.Path(canary).read_text() == 'fixture-only-protected'
else:
    denied(lambda: pathlib.Path(canary).read_text())
''', link, chain, canary, protected_target, target_parent, replacement, empty, relative, external)
    assert_ok(result)
    assert canary.read_text() == 'fixture-only-protected'
    assert link.is_symlink() and chain.is_symlink() and replacement.is_symlink()


def test_partial_config_symlink_cannot_introduce_gcloud_later(roots):
    workspace, scratch, _, _ = roots
    target = scratch / 'ordinary-target'
    target.mkdir()
    link = workspace / '.config'
    link.symlink_to(target, target_is_directory=True)
    result = run_python(roots, '''
import errno, os, sys
for path in sys.argv[1:]:
    try:
        os.mkdir(path)
    except OSError as exc:
        assert exc.errno in (errno.EACCES, errno.EPERM), exc
    else:
        raise AssertionError('gcloud introduced through partial catalogue alias')
''', link / 'gcloud', target / 'gcloud')
    assert_ok(result)


def test_existing_sample_file_symlink_cannot_be_retargeted_or_promoted(roots):
    workspace, scratch, _, _ = roots
    target = scratch / 'ordinary-file'
    target.write_text('fixture-only-protected')
    link = workspace / '.env.sample'
    link.symlink_to(target)
    replacement = workspace / 'replacement'
    replacement.mkdir()
    result = run_python(roots, '''
import errno, os, pathlib, sys
link, target, replacement = sys.argv[1:]
for action in (lambda: os.unlink(link),
               lambda: os.unlink(target),
               lambda: os.replace(replacement, link),
               lambda: os.replace(replacement, target),
               lambda: pathlib.Path(target).read_text(),
               lambda: pathlib.Path(link).write_text('bad')):
    try:
        action()
    except OSError as exc:
        assert exc.errno in (errno.EACCES, errno.EPERM), exc
    else:
        raise AssertionError('sample alias target exposed or promoted')
''', link, target, replacement)
    assert_ok(result)
    assert target.read_text() == 'fixture-only-protected'


@pytest.mark.parametrize('protected_name,alias_name', [('.git', '.GIT'),
    ('.env.sample', '.ENV.SAMPLE'), ('.config', '.CONFIG')])
def test_protected_symlink_creation_cannot_use_case_equivalent_names(roots, protected_name, alias_name):
    workspace, _, _, _ = roots
    probe = workspace / 'case-probe'
    probe.touch()
    insensitive = (workspace / 'CASE-PROBE').exists()
    probe.unlink()
    if not insensitive:
        pytest.skip('fixture volume has no case-equivalent aliases')
    target = workspace / 'ordinary-target'
    target.mkdir()
    result = run_python(roots, '''
import errno, os, sys
try:
    os.symlink(sys.argv[1], sys.argv[2])
except OSError as exc:
    assert exc.errno in (errno.EACCES, errno.EPERM), exc
else:
    raise AssertionError('unenumerated case-equivalent protected alias created')
''', target, workspace / alias_name)
    assert_ok(result)
    assert not (workspace / protected_name).exists()


@pytest.mark.parametrize('relative', ['.GIT', '.SSH', '.CONFIG/GCLOUD', '.ENV.SAMPLE'])
def test_case_equivalent_protected_parent_relocation_denied(roots, relative):
    workspace, _, _, _ = roots
    parent = workspace / 'parent'
    (parent / relative).mkdir(parents=True)
    result = run_python(roots, '''
import errno, os, sys
try:
    os.rename(sys.argv[1], sys.argv[2])
except OSError as exc:
    assert exc.errno in (errno.EACCES, errno.EPERM), exc
else:
    raise AssertionError('case-equivalent protected ancestor relocated')
''', parent, workspace / 'moved')
    assert_ok(result)


def test_nested_git_alias_public_builder_denies_confirmed_write_bypass(roots):
    workspace, scratch, _, _ = roots
    target = workspace / 'plain-target'
    destination = scratch / 'plain-secret'
    target.mkdir()
    destination.mkdir()
    canary = destination / 'canary'
    canary.write_text('fixture-only-original')
    (workspace / '.git').symlink_to(target, target_is_directory=True)
    (target / 'hop').symlink_to(destination, target_is_directory=True)
    result = run_python(roots, '''
import errno, os, pathlib, sys
p = pathlib.Path(sys.argv[1])
assert p.read_text() == 'fixture-only-original'
try:
    fd = os.open(p, os.O_WRONLY)
except OSError as exc:
    assert exc.errno in (errno.EACCES, errno.EPERM), exc
else:
    try:
        os.write(fd, b'fixture-mutated')
    finally:
        os.close(fd)
    raise AssertionError('nested git alias write permitted')
''', workspace / '.git/hop/canary')
    assert_ok(result)
    assert canary.read_text() == 'fixture-only-original'


def test_config_alias_case_equivalent_gcloud_suffix_denies_nested_access(roots):
    workspace, scratch, _, _ = roots
    target = scratch / 'plain-target'
    actual_suffix = target / 'GCLOUD'
    actual_suffix.mkdir(parents=True)
    suffix = target / 'gcloud'
    if not suffix.exists():
        pytest.skip('fixture volume has no case-equivalent gcloud directory alias')
    assert suffix.samefile(actual_suffix)
    destination = workspace / 'plain-secret'
    destination.mkdir()
    canary = destination / 'canary'
    canary.write_text('fixture-only-secret')
    (actual_suffix / 'hop').symlink_to(destination, target_is_directory=True)
    (workspace / '.config').symlink_to(target, target_is_directory=True)
    ordinary = target / 'ordinary'
    ordinary.write_text('ordinary')
    result = run_python(roots, '''
import errno, pathlib, sys
alias, actual, canary, ordinary = map(pathlib.Path, sys.argv[1:])
def denied(action):
    try:
        action()
    except OSError as exc:
        assert exc.errno in (errno.EACCES, errno.EPERM), exc
    else:
        raise AssertionError('case-equivalent suffix lost credential protection')
for p in (alias / 'hop/canary', actual / 'hop/canary', canary):
    denied(lambda: p.read_text())
    denied(lambda: p.write_text('bad'))
for p in (actual / 'hop', actual, canary.parent):
    denied(lambda: p.rename(str(p) + '-moved'))
ordinary.write_text('edited')
''', workspace / '.config/gcloud', actual_suffix, canary, ordinary)
    assert_ok(result)
    assert canary.read_text() == 'fixture-only-secret'
    assert ordinary.read_text() == 'edited'


def test_symlink_hardlink_actual_chain_cannot_redirect_nested_git_write(roots):
    workspace, scratch, _, _ = roots
    target = workspace / 'plain-target'
    destination = scratch / 'plain-secret'
    other = scratch / 'other'
    for directory in (target, destination, other):
        directory.mkdir()
    canary, other_canary = destination / 'canary', other / 'canary'
    canary.write_text('fixture-only-original')
    other_canary.write_text('fixture-only-other')
    chain, decoy = workspace / 'chain', scratch / 'decoy'
    chain.symlink_to(destination, target_is_directory=True)
    try:
        os.link(chain, decoy, follow_symlinks=False)
    except NotImplementedError as exc:
        pytest.skip(f'symlink-inode hardlink syscall unsupported: {exc}')
    except OSError as exc:
        if exc.errno in (errno.ENOSYS, errno.ENOTSUP, errno.EOPNOTSUPP):
            pytest.skip(f'symlink-inode hardlink syscall unsupported: {exc}')
        raise
    chain_info, decoy_info = chain.lstat(), decoy.lstat()
    assert stat.S_ISLNK(chain_info.st_mode) and stat.S_ISLNK(decoy_info.st_mode)
    assert (chain_info.st_dev, chain_info.st_ino) == (decoy_info.st_dev, decoy_info.st_ino)
    assert chain_info.st_nlink == decoy_info.st_nlink == 2
    assert destination.lstat().st_ino != chain_info.st_ino
    (workspace / '.git').symlink_to(target, target_is_directory=True)
    (target / 'hop').symlink_to(chain, target_is_directory=True)
    result = run_python(roots, '''
import errno, pathlib, sys
chain, other, access = map(pathlib.Path, sys.argv[1:])
assert access.read_text() == 'fixture-only-original'
retarget_denied = write_denied = False
try:
    chain.unlink()
except OSError as exc:
    assert exc.errno in (errno.EACCES, errno.EPERM), exc
    retarget_denied = True
else:
    chain.symlink_to(other, target_is_directory=True)
try:
    access.write_text('fixture-mutated')
except OSError as exc:
    assert exc.errno in (errno.EACCES, errno.EPERM), exc
    write_denied = True
print(f'{retarget_denied=} {write_denied=}', flush=True)
assert retarget_denied, 'actual chain entry was retargetable'
assert write_denied, 'nested git alias write permitted'
''', chain, other, workspace / '.git/hop/canary')
    assert_ok(result)
    assert canary.read_text() == 'fixture-only-original'
    assert other_canary.read_text() == 'fixture-only-other'
    assert chain.is_symlink() and chain.readlink() == destination


@pytest.mark.parametrize('relative', ['.git', '.ssh', '.env.sample', '.config'])
def test_transitive_aliases_canonical_access_and_mutation_are_confined(roots, relative):
    workspace, scratch, _, _ = roots
    first = workspace / 'first-parent/first'
    second = scratch / 'second-parent/second'
    final = workspace / 'final-parent/final'
    for directory in (first, second, final):
        directory.mkdir(parents=True)
    (workspace / relative).symlink_to(first, target_is_directory=True)
    if relative == '.config':
        first = first / 'gcloud'
        first.mkdir()
    chain_one = scratch / 'chain-parent/one'
    chain_two = workspace / 'chain-parent/two'
    chain_one.parent.mkdir()
    chain_two.parent.mkdir()
    chain_one.symlink_to(second, target_is_directory=True)
    chain_two.symlink_to(final, target_is_directory=True)
    (first / 'hop').symlink_to(chain_one, target_is_directory=True)
    (second / 'hop').symlink_to(chain_two, target_is_directory=True)
    canary = final / 'canary'
    canary.write_text('fixture-only-original')
    # An inherited sample regular FILE must not weaken the protection.
    (final / '.env.sample').write_text('fixture-only-original')
    replacement = scratch / 'replacement'
    replacement.symlink_to(scratch, target_is_directory=True)
    empty = scratch / 'empty'
    empty.mkdir()
    alias_access = workspace / relative
    if relative == '.config':
        alias_access /= 'gcloud'
    result = run_python(roots, '''
import errno, os, pathlib, sys
first, second, final, chain_one, chain_two, replacement, empty, access, relative = map(pathlib.Path, sys.argv[1:])
def denied(action):
    try:
        action()
    except OSError as exc:
        assert exc.errno in (errno.EACCES, errno.EPERM), exc
    else:
        raise AssertionError('transitive protection bypassed')
for p in (access / 'hop/hop/canary', final / 'canary', final / '.env.sample'):
    if str(relative) == '.git':
        assert p.read_text() == 'fixture-only-original'
    else:
        denied(lambda: p.read_text())
    denied(lambda: p.write_text('bad'))
    denied(lambda: p.unlink())
for link in (first / 'hop', second / 'hop', chain_one, chain_two):
    denied(lambda: os.unlink(link))
    denied(lambda: os.replace(replacement, link))
    denied(lambda: os.rename(link, str(link) + '-moved'))
for directory in (first, second, final, second.parent, final.parent, chain_one.parent, chain_two.parent):
    denied(lambda: os.rename(directory, str(directory) + '-moved'))
    denied(lambda: os.replace(empty, directory))
for directory in (first, second, final):
    denied(lambda: os.symlink(empty, directory / 'new-outbound'))
    denied(lambda: os.rename(replacement, directory / 'new-outbound'))
    denied(lambda: os.rename(empty, directory / 'new-directory'))
''', first, second, final, chain_one, chain_two, replacement, empty, alias_access, relative)
    assert_ok(result)
    assert canary.read_text() == 'fixture-only-original'
    assert replacement.is_symlink() and empty.is_dir()
    assert (first / 'hop').is_symlink() and (second / 'hop').is_symlink()


@pytest.mark.parametrize('credential_first', [False, True])
def test_mixed_git_and_credential_closures_keep_stronger_denials(roots, credential_first):
    workspace, scratch, _, _ = roots
    git_target = workspace / 'git-target'
    shared = scratch / 'shared'
    final = workspace / 'final'
    for directory in (git_target, shared, final):
        directory.mkdir()
    aliases = [(workspace / '.git', git_target), (scratch / '.ssh', shared)]
    for link, target in reversed(aliases) if credential_first else aliases:
        link.symlink_to(target, target_is_directory=True)
    (git_target / 'hop').symlink_to(shared, target_is_directory=True)
    (shared / 'hop').symlink_to(final, target_is_directory=True)
    git_only = git_target / 'readable'
    git_only.write_text('fixture-only-git')
    canary = final / 'canary'
    canary.write_text('fixture-only-secret')
    result = run_python(roots, '''
import errno, os, pathlib, sys
assert pathlib.Path(sys.argv[1]).read_text() == 'fixture-only-git'
for p in sys.argv[2:]:
    for flags in (os.O_RDONLY, os.O_WRONLY):
        try:
            fd = os.open(p, flags)
        except OSError as exc:
            assert exc.errno in (errno.EACCES, errno.EPERM), exc
        else:
            os.close(fd)
            raise AssertionError('mixed classification lost credential denial')
''', git_only, canary, workspace / '.git/hop/hop/canary', scratch / '.ssh/hop/canary')
    assert_ok(result)
    assert canary.read_text() == 'fixture-only-secret'


@pytest.mark.parametrize('relative', ['.git', '.ssh', '.env.sample', '.config'])
@pytest.mark.parametrize('external_return', [False, True])
def test_nested_external_directory_alias_refuses_public_execution(roots, relative, external_return, monkeypatch):
    workspace, scratch, _, base = roots
    first = workspace / 'plain-target'
    final = scratch / 'plain-secret'
    external = base / 'external'
    for directory in (first, final, external):
        directory.mkdir()
    (workspace / relative).symlink_to(first, target_is_directory=True)
    if relative == '.config':
        first = first / 'gcloud'
        first.mkdir()
    (external / 'return').symlink_to(final, target_is_directory=True)
    (first / 'hop').symlink_to(external / 'return' if external_return else external,
                             target_is_directory=True)
    marker = workspace / 'must-not-launch'
    original = os.scandir
    def scandir(path):
        assert Path(path) != external, 'external directory enumerated'
        return original(path)
    monkeypatch.setattr(os, 'scandir', scandir)
    with pytest.raises(ValueError, match='outside scanned'):
        run_python(roots, 'from pathlib import Path; Path("must-not-launch").touch()')
    assert not marker.exists()


def test_dotdot_resolution_directory_cannot_be_replaced_to_redirect_alias(roots):
    workspace, scratch, _, _ = roots
    target = workspace / 'target'
    traversed = scratch / 'traversed'
    target.mkdir()
    traversed.mkdir()
    (workspace / '.git').symlink_to(str(traversed) + '/../../' + workspace.name + '/target')
    replacement = scratch / 'replacement'
    replacement.symlink_to(workspace, target_is_directory=True)
    result = run_python(roots, '''
import errno, os, sys
traversed, replacement = sys.argv[1:]
for action in (lambda: os.rmdir(traversed),
               lambda: os.rename(traversed, traversed + '-moved'),
               lambda: os.replace(replacement, traversed)):
    try:
        action()
    except OSError as exc:
        assert exc.errno in (errno.EACCES, errno.EPERM), exc
    else:
        raise AssertionError('protected traversal directory mutated')
''', traversed, replacement)
    assert_ok(result)
    assert traversed.is_dir() and replacement.is_symlink()
