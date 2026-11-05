"""Trusted-parent admission: metadata only, fresh for each call, not race immunity."""

from dataclasses import FrozenInstanceError
import errno
import os
from pathlib import Path
import stat

import pytest

from agent_smith.sandbox.workspace_policy import admit_workspace
from agent_smith.sandbox.seatbelt import build_seatbelt_profile


@pytest.fixture
def roots(tmp_path):
    roots = tuple(tmp_path / name for name in ('workspace', 'scratch', 'runtime'))
    for root in roots:
        root.mkdir()
    return roots


def admit(roots):
    workspace, scratch, runtime = roots
    return admit_workspace(workspace=workspace, scratch=scratch, runtime_roots=(runtime,))


def test_immutable_canonical_snapshot_and_parameterized_compilation(roots):
    workspace, scratch, _ = roots
    (workspace / 'parent/.git').mkdir(parents=True)
    (scratch / 'other/.env.sample').mkdir(parents=True)
    (scratch / 'other/.env.sample/child').touch()
    policy = admit(roots)
    assert policy.workspace == workspace.resolve()
    assert workspace / 'parent' in policy.protected_unlinks
    assert scratch / 'other/.env.sample' in policy.protected_unlinks
    assert scratch / 'other' in policy.protected_unlinks
    with pytest.raises(FrozenInstanceError):
        policy.workspace = scratch
    profile = build_seatbelt_profile(workspace=policy.workspace, scratch=policy.scratch,
                                     runtime_roots=policy.runtime_roots)
    for root in roots:
        assert str(root) not in profile.text
    assert '(vnode-type DIRECTORY)' not in profile.text.split('; Exact admitted')[1]
    assert 'file-write-unlink' in profile.text


@pytest.mark.parametrize('root_index', [0, 1])
@pytest.mark.parametrize('relative', ['visible', '.hidden/deep/file', '.git/config', '.env'])
def test_every_writable_regular_hardlink_is_rejected(roots, root_index, relative):
    source = roots[root_index] / relative
    source.parent.mkdir(parents=True, exist_ok=True)
    source.touch()
    os.link(source, roots[2] / 'alias')
    with pytest.raises(ValueError, match='hardlink'):
        admit(roots)


def test_admission_is_fresh_on_every_public_compile(roots):
    workspace, scratch, runtime = roots
    build_seatbelt_profile(workspace=workspace, scratch=scratch, runtime_roots=(runtime,))
    source = workspace / 'new'
    source.touch()
    os.link(source, scratch / 'alias')
    with pytest.raises(ValueError, match='hardlink'):
        build_seatbelt_profile(workspace=workspace, scratch=scratch, runtime_roots=(runtime,))


@pytest.mark.parametrize('failure', ['scandir', 'lstat'])
def test_indeterminate_metadata_fails_closed(roots, monkeypatch, failure):
    (roots[0] / 'child').touch()
    def fail(*args, **kwargs):
        raise PermissionError('fixture metadata failure')
    if failure == 'scandir':
        monkeypatch.setattr(os, 'scandir', fail)
    else:
        original = Path.lstat
        def lstat(path, *args, **kwargs):
            if path.name == 'child':
                return fail()
            return original(path, *args, **kwargs)
        monkeypatch.setattr(Path, 'lstat', lstat)
    with pytest.raises(ValueError, match='admission'):
        admit(roots)


def test_scan_neither_reads_contents_nor_follows_symlinks(roots, monkeypatch):
    outside = roots[0].parent / 'outside'
    outside.mkdir()
    source = outside / 'hardlinked'
    source.touch()
    os.link(source, outside / 'alias')
    (roots[0] / 'external').symlink_to(outside, target_is_directory=True)
    (roots[0] / 'dangling').symlink_to(outside / 'absent')
    (roots[0] / 'file').touch()
    def no_content(*args, **kwargs):
        raise AssertionError('file contents read')
    monkeypatch.setattr(Path, 'open', no_content)
    admit(roots)


@pytest.mark.parametrize('root_index', [0, 1, 2])
@pytest.mark.parametrize('broad', ['/', str(Path.home()), str(Path.home().parent), '/usr'])
def test_broad_roots_rejected(roots, root_index, broad):
    selected = list(roots)
    selected[root_index] = Path(broad)
    with pytest.raises(ValueError, match='broad'):
        admit(tuple(selected))


@pytest.mark.parametrize('relative', ['.ssh', '.aws', '.azure', '.kube', '.netrc',
    '.config/gcloud', '.env', '.env.local', '.env.example/child', '.git'])
def test_protected_root_paths_rejected(roots, relative):
    root = roots[0] / relative
    root.mkdir(parents=True)
    with pytest.raises(ValueError, match='protected'):
        admit((root, *roots[1:]))


def test_home_credential_ancestor_rejected(roots, monkeypatch):
    fake_home = roots[0].parent / 'home'
    config = fake_home / '.config'
    config.mkdir(parents=True)
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: fake_home))
    with pytest.raises(ValueError, match='credential'):
        admit((config, *roots[1:]))


@pytest.mark.parametrize('pair', [(0, 1), (0, 2), (1, 2)])
def test_overlapping_roots_rejected(roots, pair):
    selected = list(roots)
    selected[pair[1]] = selected[pair[0]]
    with pytest.raises(ValueError, match='overlap'):
        admit(tuple(selected))


@pytest.mark.parametrize('relative', ['a/.git', 'a/.ssh', 'a/.aws', 'a/.azure',
    'a/.kube', 'a/.netrc', 'a/.config/gcloud', 'a/.env', 'a/.env.local',
    'a/.env.example', 'a/.env.sample'])
def test_catalogue_protects_directories_and_all_ancestors_in_both_roots(roots, relative):
    for root in roots[:2]:
        (root / relative).mkdir(parents=True)
    policy = admit(roots)
    for root in roots[:2]:
        target = root / relative
        assert target in policy.protected_unlinks
        assert root / 'a' in policy.protected_unlinks
        assert root in policy.protected_unlinks


def test_sample_files_not_protected_but_sample_directory_descendants_are(roots):
    root = roots[0]
    (root / 'ordinary').mkdir()
    (root / 'ordinary/.env.example').touch()
    (root / 'protected/.env.sample').mkdir(parents=True)
    (root / 'protected/.env.sample/plain').touch()
    policy = admit(roots)
    assert root / 'ordinary' not in policy.protected_unlinks
    assert root / 'protected' in policy.protected_unlinks


def test_known_credential_roots_are_canonicalized(roots, monkeypatch):
    home = roots[0].parent / 'home'
    home.mkdir()
    target = roots[0].parent / 'credential-target'
    target.mkdir()
    (home / '.ssh').symlink_to(target, target_is_directory=True)
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: home))
    with pytest.raises(ValueError, match='credential'):
        admit((target, *roots[1:]))


def test_canonical_root_alias_cannot_hide_overlap(roots):
    alias = roots[0].parent / 'workspace-alias'
    alias.symlink_to(roots[0], target_is_directory=True)
    with pytest.raises(ValueError, match='overlap'):
        admit((roots[0], alias, roots[2]))


@pytest.mark.parametrize('relative', ['.env.example\n', '.env.sample\n'])
def test_newline_is_not_a_sample_filename_exemption(roots, relative):
    parent = roots[0] / 'parent'
    parent.mkdir()
    (parent / relative).touch()
    assert parent in admit(roots).protected_unlinks


@pytest.mark.parametrize('relative', ['.git', '.env', '.env.sample', '.config'])
@pytest.mark.parametrize('failure', ['broken', 'cycle'])
def test_protected_symlink_resolution_fails_closed(roots, relative, failure):
    link = roots[0] / relative
    link.symlink_to(link if failure == 'cycle' else roots[0] / 'absent')
    with pytest.raises(ValueError, match='admission'):
        admit(roots)


@pytest.mark.parametrize('relative', ['.git', '.env.sample', '.config'])
@pytest.mark.parametrize('conflict', ['workspace', 'runtime'])
def test_protected_symlink_target_conflicts_fail_closed(roots, relative, conflict):
    target = roots[0] if conflict == 'workspace' else roots[2]
    (roots[0] / relative).symlink_to(target, target_is_directory=True)
    if relative == '.config' and conflict == 'workspace':
        # The policy protects only the gcloud suffix, not its entire target.
        policy = admit(roots)
        assert target / 'gcloud' in policy.credential_targets
    else:
        with pytest.raises(ValueError, match='conflicts'):
            admit(roots)


def test_external_protected_directory_alias_refused_without_tree_scan(roots, monkeypatch):
    external = roots[0].parent / 'external'
    external.mkdir()
    source = external / 'not-scanned'
    source.touch()
    os.link(source, external / 'hardlink')
    (roots[0] / '.ssh').symlink_to(external, target_is_directory=True)
    original = os.scandir
    def scandir(path):
        assert Path(path) != external, 'external target tree enumerated'
        return original(path)
    monkeypatch.setattr(os, 'scandir', scandir)
    with pytest.raises(ValueError, match='outside scanned'):
        admit(roots)


@pytest.mark.parametrize('relative,credential', [('.ssh', True), ('.env.sample', True),
                                               ('.git', False), ('.config', True)])
def test_canonical_protected_targets_and_ancestors_cross_scratch(roots, relative, credential):
    target = roots[1] / 'parent/target'
    target.mkdir(parents=True)
    chain = roots[0] / 'ordinary-chain'
    chain.symlink_to(target, target_is_directory=True)
    link = roots[0] / relative
    link.symlink_to(chain, target_is_directory=True)
    policy = admit(roots)
    expected = target / 'gcloud' if relative == '.config' else target
    assert expected in (policy.credential_targets if credential else policy.git_targets)
    assert link in policy.protected_unlinks
    assert target in policy.protected_unlinks
    assert target.parent in policy.protected_unlinks


def test_sample_symlink_to_regular_file_cannot_be_promoted_to_directory(roots):
    target = roots[1] / 'ordinary-file'
    target.touch()
    link = roots[0] / '.env.sample'
    link.symlink_to(target)
    policy = admit(roots)
    assert target in policy.credential_targets
    assert link in policy.protected_unlinks


def test_protected_link_with_nondirectory_path_component_is_rejected(roots):
    (roots[0] / 'file').touch()
    (roots[0] / 'directory').mkdir()
    (roots[0] / '.git').symlink_to('file/../directory')
    with pytest.raises(ValueError, match='admission'):
        admit(roots)


@pytest.mark.parametrize('relative', ['.GIT', '.SSH', '.CONFIG/GCLOUD', '.ENV.SAMPLE'])
def test_admission_catalogue_matches_seatbelt_case_equivalence(roots, relative):
    parent = roots[0] / 'parent'
    (parent / relative).mkdir(parents=True)
    assert parent in admit(roots).protected_unlinks


def test_config_alias_case_equivalent_gcloud_suffix_closes_nested_alias(roots):
    workspace, scratch, _ = roots
    target = scratch / 'plain-target'
    actual_suffix = target / 'GCLOUD'
    actual_suffix.mkdir(parents=True)
    suffix = target / 'gcloud'
    if not suffix.exists():
        pytest.skip('fixture volume has no case-equivalent gcloud directory alias')
    assert suffix.samefile(actual_suffix)
    destination = workspace / 'secret-parent/plain-secret'
    destination.mkdir(parents=True)
    (actual_suffix / 'hop').symlink_to(destination, target_is_directory=True)
    (destination / 'canary').touch()
    (workspace / '.config').symlink_to(target, target_is_directory=True)
    policy = admit(roots)
    assert {actual_suffix, destination} <= set(policy.credential_targets)
    assert {suffix, actual_suffix, actual_suffix / 'hop', destination,
            destination.parent, target} <= set(policy.protected_unlinks)
    assert target not in policy.credential_targets


@pytest.mark.parametrize('overlap', ['equal', 'nested_runtime'])
def test_case_equivalent_root_aliases_do_not_hide_overlap(roots, overlap):
    workspace = roots[0]
    alias = workspace.with_name(workspace.name.upper())
    if not alias.exists():
        pytest.skip('fixture volume has no case-equivalent aliases')
    if overlap == 'equal':
        selected = (workspace, alias, roots[2])
    else:
        runtime = workspace / 'runtime'
        runtime.mkdir()
        selected = (alias, roots[1], runtime)
    with pytest.raises(ValueError, match='overlap'):
        admit(selected)


@pytest.mark.parametrize('relative,credential', [('.git', False), ('.ssh', True),
                                               ('.env.sample', True)])
def test_nested_alias_closure_uses_only_scanned_metadata(roots, monkeypatch, relative, credential):
    workspace, scratch, _ = roots
    first = workspace / 'plain-target'
    second = scratch / 'parent/plain-secret'
    third = workspace / 'other/final'
    for directory in (first, second, third):
        directory.mkdir(parents=True)
    (workspace / relative).symlink_to(first, target_is_directory=True)
    (first / 'hop').symlink_to(second, target_is_directory=True)
    chain = scratch / 'chain'
    chain.symlink_to(third, target_is_directory=True)
    (second / 'hop').symlink_to(chain, target_is_directory=True)
    (third / 'canary').touch()
    def no_content(*args, **kwargs):
        raise AssertionError('file contents read')
    monkeypatch.setattr(Path, 'open', no_content)
    policy = admit(roots)
    targets = policy.credential_targets if credential else policy.git_targets
    assert {first, second, third} <= set(targets)
    assert {first / 'hop', second / 'hop', chain, second.parent, third.parent} <= set(policy.protected_unlinks)


@pytest.mark.parametrize('failure', ['external', 'external-return', 'cycle', 'broken', 'runtime', 'root'])
def test_transitively_protected_aliases_fail_closed(roots, monkeypatch, failure):
    workspace, scratch, runtime = roots
    target = workspace / 'plain-target'
    target.mkdir()
    (workspace / '.git').symlink_to(target, target_is_directory=True)
    external = workspace.parent / 'external'
    external.mkdir()
    (external / 'return').symlink_to(scratch, target_is_directory=True)
    destination = {
        'external': external, 'external-return': external / 'return',
        'cycle': target, 'broken': scratch / 'missing', 'runtime': runtime,
        'root': scratch,
    }[failure]
    (target / 'hop').symlink_to(destination, target_is_directory=True)
    original = os.scandir
    def scandir(path):
        assert Path(path) != external, 'external target tree enumerated'
        return original(path)
    monkeypatch.setattr(os, 'scandir', scandir)
    with pytest.raises(ValueError, match='admission|conflicts'):
        admit(roots)


def test_cross_root_directory_alias_cycle_refused(roots):
    first, second = roots[0] / 'first', roots[1] / 'second'
    first.mkdir()
    second.mkdir()
    (roots[0] / '.git').symlink_to(first, target_is_directory=True)
    (first / 'hop').symlink_to(second, target_is_directory=True)
    (second / 'back').symlink_to(first, target_is_directory=True)
    with pytest.raises(ValueError, match='cyclic'):
        admit(roots)


def test_shared_nested_destination_is_not_mistaken_for_cycle(roots):
    first, second = roots[0] / 'first', roots[1] / 'second'
    first.mkdir()
    second.mkdir()
    (roots[0] / '.git').symlink_to(first, target_is_directory=True)
    for name in ('hop', 'other-hop'):
        (first / name).symlink_to(second, target_is_directory=True)
    assert second in admit(roots).git_targets


def test_transitive_resolution_metadata_error_refuses_admission(roots, monkeypatch):
    first, second = roots[0] / 'first', roots[1] / 'second'
    first.mkdir()
    second.mkdir()
    (roots[0] / '.git').symlink_to(first, target_is_directory=True)
    (first / 'hop').symlink_to(second, target_is_directory=True)
    original = os.readlink
    def readlink(path, *args, **kwargs):
        if Path(path) == first / 'hop':
            raise PermissionError('fixture metadata failure')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(os, 'readlink', readlink)
    with pytest.raises(ValueError, match='admission'):
        admit(roots)


@pytest.mark.parametrize('relative', ['.git', '.env', '.env.sample'])
def test_external_protected_regular_file_targets_remain_supported(roots, relative):
    external = roots[0].parent / 'external-file'
    external.touch()
    (roots[0] / relative).symlink_to(external)
    policy = admit(roots)
    assert external in (policy.git_targets if relative == '.git' else policy.credential_targets)


@pytest.mark.parametrize('chain_relative', ['chain', 'chain-parent/chain'])
def test_symlink_hardlink_preserves_actual_chain_and_decoy_spellings(roots, chain_relative):
    workspace, scratch, _ = roots
    target = workspace / 'plain-target'
    destination = scratch / 'plain-secret'
    target.mkdir()
    destination.mkdir()
    chain = workspace / chain_relative
    chain.parent.mkdir(exist_ok=True)
    chain.symlink_to(destination, target_is_directory=True)
    decoy = scratch / 'decoy-parent/decoy'
    decoy.parent.mkdir()
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
    policy = admit(roots)
    assert {chain, chain.parent, decoy, decoy.parent} <= set(policy.protected_unlinks)
    assert {target, destination} <= set(policy.git_targets)


def test_protected_resolution_freezes_directory_consumed_by_dotdot(roots):
    target = roots[0] / 'target'
    traversed = roots[1] / 'traversed'
    target.mkdir()
    traversed.mkdir()
    (roots[0] / '.git').symlink_to(str(traversed) + '/../../' + roots[0].name + '/target')
    # Scratch and workspace are siblings in this fixture.
    assert traversed in admit(roots).protected_unlinks
