import json

import pytest

from agent_chat import main
from agent_smith.loop.runner import ModelResponse


def setup_model(monkeypatch):
    histories = []

    class Model:
        def generate(self, messages, **kwargs):
            histories.append(messages)
            return ModelResponse(json.dumps({'final': 'answer'}), 10, 4)

    monkeypatch.setattr('agent_chat.create_model_client', lambda *a, **kw: Model())
    monkeypatch.setattr('agent_chat.load_api_key', lambda *a: 'fake')
    return histories


def test_repl_preserves_history_handles_commands_and_reset(monkeypatch, tmp_path, capsys):
    histories = setup_model(monkeypatch)
    lines = iter(['', '/help', 'remember blue', 'what color?', '/reset', 'fresh', '/unknown', '/exit'])
    monkeypatch.setattr('builtins.input', lambda prompt: next(lines))
    assert main(['--tools', 'read-only', '--provider', 'groq', '--model', 'test', '--workspace', str(tmp_path)]) == 0
    assert len(histories) == 3
    assert histories[1][1]['content'] == 'remember blue'
    assert histories[1][-1]['content'] == 'what color?'
    assert len(histories[2]) == 2
    output = capsys.readouterr().out
    assert '/reset' in output
    assert 'Conversation reset' in output
    assert 'Unknown command' in output


@pytest.mark.parametrize('exception', [EOFError, KeyboardInterrupt])
def test_repl_exits_cleanly(monkeypatch, tmp_path, exception):
    setup_model(monkeypatch)

    def stop(prompt):
        raise exception()

    monkeypatch.setattr('builtins.input', stop)
    assert main(['--tools', 'read-only', '--provider', 'groq', '--model', 'test', '--workspace', str(tmp_path)]) == 0


def test_repl_continues_after_provider_error(monkeypatch, tmp_path, capsys):
    from agent_smith.providers.contracts import ProviderError

    class Model:
        calls = 0

        def generate(self, messages, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise ProviderError('http_429')
            return ModelResponse('{"final":"recovered"}', 1, 1)

    monkeypatch.setattr('agent_chat.create_model_client', lambda *a, **kw: Model())
    monkeypatch.setattr('agent_chat.load_api_key', lambda *a: 'fake')
    lines = iter(['hello', 'retry', '/exit'])
    monkeypatch.setattr('builtins.input', lambda prompt: next(lines))
    assert main(['--tools', 'read-only', '--provider', 'groq', '--model', 'test', '--workspace', str(tmp_path)]) == 0
    output = capsys.readouterr()
    assert 'http_429' in output.err
    assert 'recovered' in output.out


@pytest.mark.parametrize('ending', ['/exit', EOFError, KeyboardInterrupt])
def test_sandbox_repl_callback_and_close(monkeypatch, tmp_path, capsys, ending):
    from agent_smith.tools.approval import TerminalApproval
    events = []

    class Tools:
        system_guidance = 'sandbox test'

        def __init__(self, context, *, approval_callback):
            assert isinstance(approval_callback, TerminalApproval)
            events.append('callback')

        def check_ready(self):
            return True

        def close(self):
            events.append('closed')

    setup_model(monkeypatch)
    monkeypatch.setattr('agent_chat.WorkspaceToolDispatcher', Tools)
    lines = iter(['/help', ending])

    def read(prompt):
        value = next(lines)
        if isinstance(value, type):
            raise value()
        return value

    monkeypatch.setattr('builtins.input', read)
    assert main(['--provider', 'groq', '--model', 'test', '--workspace', str(tmp_path)]) == 0
    assert events == ['callback', 'closed']
    output = capsys.readouterr().out
    assert 'sandboxed coding chat' in output and 'BEST-EFFORT' in output
    assert 'Cannot edit' not in output
