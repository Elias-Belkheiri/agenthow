from pathlib import Path

import pytest

import agent_smith.tools.contracts as contracts
from agent_smith.tools.contracts import ToolContext, ToolError, ToolResult


def test_resolve_only_workspace_paths(tmp_path):
    root = tmp_path / 'testbed'
    root.mkdir()
    sibling = tmp_path / 'testbed-elsewhere'
    sibling.mkdir()
    context = ToolContext(root)
    assert context.resolve('src/a.py') == root / 'src/a.py'
    assert context.resolve(str(root / 'src/a.py')) == root / 'src/a.py'
    for value in ('../private', 'src/../a.py', str(sibling / 'file')):
        with pytest.raises(ToolError, match='path_denied'):
            context.resolve(value)
    (root / 'escape').symlink_to(sibling, target_is_directory=True)
    with pytest.raises(ToolError, match='path_denied'):
        context.resolve('escape/file')


def test_result_distinguishes_errors_and_truncation():
    result = ToolResult(content='1: alpha', truncated=True)
    assert result.error is None and result.truncated
    for code in ('invalid_input', 'not_found', 'path_denied', 'encoding_error', 'too_large'):
        failure = ToolResult(content=f'{code}: detail', error=code)
        assert failure.error == code and failure.content


def test_glob_match_consecutive_double_stars_has_bounded_work(monkeypatch):
    original = contracts.fnmatchcase
    calls = 0

    def counted(name, pattern):
        nonlocal calls
        calls += 1
        if calls > 500:
            pytest.fail('glob matching repeated the same state')
        return original(name, pattern)

    monkeypatch.setattr(contracts, 'fnmatchcase', counted)
    glob = ('**',) * 8 + ('target.py',)
    assert not contracts._glob_match(('dir',) * 8 + ('other.py',), glob)
    assert contracts._glob_match(('dir',) * 8 + ('target.py',), glob)
    assert calls <= 500


def test_invalid_workspace_root_is_explicit(tmp_path):
    with pytest.raises(ToolError, match='invalid_input'):
        ToolContext(tmp_path / 'missing')
