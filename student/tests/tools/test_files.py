from agent_smith.tools.contracts import MAX_OUTPUT_CHARS, ToolContext
from agent_smith.tools.files import list_files, read_file


def test_read_file_inclusive_line_numbers_and_absolute_path(tmp_path):
    (tmp_path / 'a.py').write_text('alpha\nbeta\ngamma\n')
    context = ToolContext(tmp_path)
    assert read_file('a.py', 1, 2, context).content == '1: alpha\n2: beta'
    assert read_file(str(tmp_path / 'a.py'), 2, 3, context).content == '2: beta\n3: gamma'
    assert read_file('a.py', 8, 9, context).content == ''


def test_read_file_uses_physical_newlines_and_crlf(tmp_path):
    (tmp_path / 'lines').write_bytes('one\vfind\ftwo\u2028three\u2029four\x85five\r\nsix\n'.encode('utf-8'))
    result = read_file('lines', 1, 3, ToolContext(tmp_path))
    assert result.content == '1: one\vfind\ftwo\u2028three\u2029four\x85five\n2: six'
    assert not result.truncated


def test_read_file_oversized_single_line_is_bounded(tmp_path):
    (tmp_path / 'long').write_text('x' * (MAX_OUTPUT_CHARS + 1))
    result = read_file('long', 1, 1, ToolContext(tmp_path))
    assert result.content == '' and result.truncated and result.error is None


def test_read_file_cumulative_character_cap(tmp_path):
    (tmp_path / 'wide').write_text(('x' * 400 + '\n') * 200)
    result = read_file('wide', 1, 200, ToolContext(tmp_path))
    assert result.truncated and result.error is None
    assert len(result.content) <= MAX_OUTPUT_CHARS
    assert len(result.content.splitlines()) < 200
    assert result.content.endswith('x' * 400)


def test_read_file_explicit_errors_and_bounds(tmp_path):
    context = ToolContext(tmp_path)
    (tmp_path / 'binary').write_bytes(b'\xff')
    (tmp_path / 'big').write_bytes(b'x' * (1024 * 1024 + 1))
    (tmp_path / 'many').write_text('line\n' * 300)
    for args, code in [
        (('binary', 1, 1), 'encoding_error'),
        (('big', 1, 1), 'too_large'),
        (('missing', 1, 1), 'not_found'),
        (('.', 1, 1), 'invalid_input'),
        (('../outside', 1, 1), 'path_denied'),
        (('many', 0, 1), 'invalid_input'),
        (('many', 2, 1), 'invalid_input'),
    ]:
        result = read_file(*args, context)
        assert result.error == code and result.content
    result = read_file('many', 1, 300, context)
    assert result.truncated and result.error is None
    assert len(result.content.splitlines()) <= 200


def test_list_files_glob_order_bounded_and_no_matches(tmp_path):
    (tmp_path / 'src').mkdir()
    for name in ('b.py', 'a.py', 'c.txt'):
        (tmp_path / 'src' / name).write_text('')
    context = ToolContext(tmp_path)
    assert list_files('src', '*.py', context).content.splitlines() == [
        str(tmp_path / 'src/a.py'), str(tmp_path / 'src/b.py')
    ]
    assert list_files('.', '**/*.py', context).content.splitlines() == [
        str(tmp_path / 'src/a.py'), str(tmp_path / 'src/b.py')
    ]
    assert list_files('src', '*.rs', context).content == ''
    assert list_files('.', '*.py', context).content == ''  # Nonrecursive glob.
    for index in range(210):
        (tmp_path / 'src' / f'{index:03}.py').write_text('')
    limited = list_files('src', '*.py', context)
    assert limited.truncated and len(limited.content.splitlines()) == 200


def test_list_files_reports_scan_limit_even_without_matches(tmp_path, monkeypatch):
    import agent_smith.tools.contracts as contracts

    monkeypatch.setattr(contracts, 'MAX_SCANNED_FILES', 2)
    for name in ('a.txt', 'b.txt', 'c.txt'):
        (tmp_path / name).write_text('')
    result = list_files('.', '*.py', ToolContext(tmp_path))
    assert result.truncated and result.content == ''


def test_list_files_denies_escapes_and_invalid_directory(tmp_path):
    outside = tmp_path.parent / (tmp_path.name + '-outside')
    outside.mkdir()
    (outside / 'secret.py').write_text('secret')
    (tmp_path / 'link').symlink_to(outside, target_is_directory=True)
    context = ToolContext(tmp_path)
    assert list_files('link', '*', context).error == 'path_denied'
    assert list_files('missing', '*', context).error == 'not_found'
    (tmp_path / 'ordinary.py').write_text('')
    assert list_files('ordinary.py', '*', context).error == 'invalid_input'
    assert list_files('.', '../*.py', context).error == 'invalid_input'
    assert list_files('.', '*.py', context).content == str(tmp_path / 'ordinary.py')
