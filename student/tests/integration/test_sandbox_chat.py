"""Fake-model chat over real native tools; no provider or credentials involved."""
import io
import json
import platform

import pytest

from agent_smith.loop.chat import ChatSession
from agent_smith.loop.runner import ModelResponse
from agent_smith.tools.approval import TerminalApproval
from agent_smith.tools.contracts import ToolContext
from agent_smith.tools.workspace import WorkspaceToolDispatcher


class Model:
    def __init__(self, actions):
        self.actions = iter(actions)
        self.histories = []

    def generate(self, messages, **kwargs):
        self.histories.append(messages)
        action = next(self.actions)
        return ModelResponse(action if isinstance(action, str) else json.dumps(action), 1, 1)


def tool(name, **arguments):
    return {'tool': name, 'arguments': arguments}


class TTY(io.StringIO):
    def isatty(self):
        return True


@pytest.mark.skipif(platform.system() != 'Darwin', reason='native macOS Seatbelt only')
def test_native_chat_writes_edit_denial_approval_and_honest_failure(tmp_path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    (workspace / '.env').write_text('fixture-canary')
    (workspace / '.git').mkdir()
    (workspace / '.git' / 'config').write_text('fixture-git')
    outside = tmp_path / 'outside'
    outside.write_text('fixture-outside')
    answers = TTY('no\nyes\n')
    terminal = TTY()
    model = Model([
        tool('write_file', filepath='note', content='before'),
        tool('edit_file', filepath='note', old_text='before', new_text='after'),
        tool('run_shell', command='printf x >> count'),
        tool('run_shell', command='printf x >> count'),
        tool('read_file', filepath=str(outside), start_line=1, end_line=1),
        tool('read_file', filepath='.env', start_line=1, end_line=1),
        tool('write_file', filepath='.git/config', content='bad'),
        'invalid JSON',
        {'final': 'Previous writes remain; no rollback.'},
    ])
    with WorkspaceToolDispatcher(ToolContext(workspace),
            approval_callback=TerminalApproval(answers, terminal)) as tools:
        assert tools.check_ready(), 'actual Seatbelt enforcement/readiness failed'
        session = ChatSession(model, tools)
        result = session.send('perform actions')
        assert result.error == 'invalid_action'
        observations = [json.loads(m['content'].removeprefix('Tool observation: '))
                        for m in model.histories[-1] if m['content'].startswith('Tool observation: ')]
        assert observations[0]['ok'] and observations[1]['ok']
        assert observations[2] == {'ok': False, 'error': 'approval_denied'}
        assert observations[3]['ok']
        assert all(not obs['ok'] for obs in observations[4:])
        assert (workspace / 'note').read_text() == 'after'
        assert (workspace / 'count').read_text() == 'x'
        assert session.send('what happened?').success
        assert len(model.histories[-1]) == 2
        assert (workspace / 'count').read_text() == 'x'
    assert (workspace / '.env').read_text() == 'fixture-canary'
    assert (workspace / '.git' / 'config').read_text() == 'fixture-git'
    assert outside.read_text() == 'fixture-outside'
    assert terminal.getvalue().count('Approve THIS command once?') == 2


@pytest.mark.skipif(platform.system() != 'Darwin', reason='native macOS Seatbelt only')
def test_native_noninteractive_shell_denied_without_reading_stdin(tmp_path):
    model = Model([tool('run_shell', command='touch forbidden'), {'final': 'denied'}])
    with WorkspaceToolDispatcher(ToolContext(tmp_path)) as tools:
        assert tools.check_ready()
        assert ChatSession(model, tools).send('shell').success
        assert 'approval_denied' in model.histories[-1][-1]['content']
        assert not (tmp_path / 'forbidden').exists()


def test_long_approval_consumes_budget_without_backend_launch(monkeypatch, tmp_path):
    clock = [0.0]
    monkeypatch.setattr('agent_smith.tools.workspace.time.monotonic', lambda: clock[0])

    def approve(command, cwd):
        clock[0] += 5
        return True

    def forbidden_backend(**kwargs):
        pytest.fail('payload/backend must not launch after approval exhausted budget')

    with WorkspaceToolDispatcher(ToolContext(tmp_path), approval_callback=approve) as tools:
        monkeypatch.setattr('agent_smith.tools.workspace.SeatbeltBackend', forbidden_backend)
        assert tools.call('run_shell', {'command': 'touch forbidden'}, timeout_seconds=3) == {
            'ok': False, 'error': 'timeout'}
    assert not (tmp_path / 'forbidden').exists()


def test_chat_approval_time_exhausts_overall_budget_without_launch(monkeypatch, tmp_path):
    clock = [0.0]
    monkeypatch.setattr('agent_smith.loop.chat.time.monotonic', lambda: clock[0])
    monkeypatch.setattr('agent_smith.loop.chat.TIME_LIMIT_SECONDS', 5)

    def approve(command, cwd):
        clock[0] += 6
        return True

    def forbidden_backend(**kwargs):
        pytest.fail('no launch after approval consumed overall budget')

    model = Model([tool('run_shell', command='touch forbidden')])
    with WorkspaceToolDispatcher(ToolContext(tmp_path), approval_callback=approve) as tools:
        monkeypatch.setattr('agent_smith.tools.workspace.SeatbeltBackend', forbidden_backend)
        result = ChatSession(model, tools).send('shell')
        assert result.error == 'timeout' and result.total_requests == 1
        assert not (tmp_path / 'forbidden').exists()
