from pathlib import Path

import pytest

from agent_smith.tools.contracts import ToolContext
from agent_smith.tools.dispatcher import ReadOnlyToolDispatcher


def test_dispatches_read_file_inside_workspace(tmp_path: Path):
    (tmp_path / 'notes.txt').write_text('first\nsecond\n')
    dispatcher = ReadOnlyToolDispatcher(ToolContext(tmp_path))

    result = dispatcher.call('read_file', {'filepath': 'notes.txt', 'start_line': 2, 'end_line': 2})

    assert result['ok'] is True
    assert result['content'] == '2: second'


def test_dispatches_search_tool(tmp_path: Path):
    (tmp_path / 'main.py').write_text('def target(): pass\n')
    dispatcher = ReadOnlyToolDispatcher(ToolContext(tmp_path))

    result = dispatcher.call('search_code', {'pattern': 'target', 'file_pattern': '*.py'})

    assert result['ok'] is True
    assert 'main.py:1 def target' in result['content']


@pytest.mark.parametrize('name,args', [
    ('run_tests', {}), ('write_file', {}), ('shell', {}), ('read_file', {'filepath': '../outside', 'start_line': 1, 'end_line': 2}),
])
def test_rejects_non_readonly_or_invalid_actions(tmp_path: Path, name, args):
    dispatcher = ReadOnlyToolDispatcher(ToolContext(tmp_path))

    result = dispatcher.call(name, args)

    assert result['ok'] is False
    assert result['error']
