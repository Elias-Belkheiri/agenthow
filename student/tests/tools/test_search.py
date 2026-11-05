from agent_smith.tools.contracts import ToolContext
from agent_smith.tools.search import search_code


def test_search_code_absolute_paths_order_and_no_matches(tmp_path):
    (tmp_path / 'b.py').write_text('def solve():\n  pass\n')
    (tmp_path / 'a.py').write_text('def solve(x):\n  return x\n')
    context = ToolContext(tmp_path)
    assert search_code('def solve', '*.py', context).content.splitlines() == [
        f'{tmp_path}/a.py:1 def solve(x):', f'{tmp_path}/b.py:1 def solve():'
    ]
    assert search_code('missing', '*.py', context).content == ''
    assert search_code('', '*.py', context).error == 'invalid_input'
    assert search_code('x' * 513, '*.py', context).error == 'invalid_input'
    (tmp_path / 'literal.py').write_text('a+b\naab\n')
    assert search_code('a+b', 'literal.py', context).content == f'{tmp_path}/literal.py:1 a+b'


def test_search_code_uses_physical_newlines_and_crlf(tmp_path):
    (tmp_path / 'lines.py').write_bytes('one\vfind\ftwo\u2028three\u2029four\x85five\r\nfind six\n'.encode('utf-8'))
    result = search_code('find', 'lines.py', ToolContext(tmp_path))
    assert result.content == (
        f'{tmp_path}/lines.py:1 one\vfind\ftwo\u2028three\u2029four\x85five\n'
        f'{tmp_path}/lines.py:2 find six'
    )
    assert not result.truncated


def test_search_code_binary_long_lines_and_result_cap(tmp_path):
    (tmp_path / 'binary.py').write_bytes(b'hello\xff')
    (tmp_path / 'long.py').write_text('find ' + 'x' * 3000 + '\n')
    (tmp_path / 'many.py').write_text('find\n' * 300)
    result = search_code('find', '*.py', ToolContext(tmp_path))
    assert result.error is None and result.truncated
    assert len(result.content.splitlines()) <= 200
    assert all(len(line) < 1024 for line in result.content.splitlines())
    assert 'binary.py' not in result.content


def test_search_code_reports_oversized_file_truncation(tmp_path):
    (tmp_path / 'large.py').write_bytes(b'x' * (1024 * 1024 + 1))
    result = search_code('x', '*.py', ToolContext(tmp_path))
    assert result.truncated and result.content == ''


def test_search_code_reports_scan_limit_without_matches(tmp_path, monkeypatch):
    import agent_smith.tools.contracts as contracts

    monkeypatch.setattr(contracts, 'MAX_SCANNED_FILES', 2)
    for name in ('a.py', 'b.py', 'c.py'):
        (tmp_path / name).write_text('other')
    result = search_code('find', '*.py', ToolContext(tmp_path))
    assert result.truncated and result.content == ''


def test_search_code_ignores_symlinks_outside_workspace(tmp_path):
    outside = tmp_path.parent / (tmp_path.name + '-outside.py')
    outside.write_text('unique_secret')
    (tmp_path / 'linked.py').symlink_to(outside)
    assert search_code('unique_secret', '*.py', ToolContext(tmp_path)).content == ''
    assert search_code('unique_secret', '../*.py', ToolContext(tmp_path)).error == 'invalid_input'
