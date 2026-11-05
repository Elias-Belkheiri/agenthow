from agent_chat import load_api_key, main


def test_load_api_key_reads_dotenv_without_overriding_environment(monkeypatch, tmp_path):
    monkeypatch.delenv('GROQ_API_KEY', raising=False)
    env_file = tmp_path / '.env'
    env_file.write_text('GROQ_API_KEY="from-file"\n')

    assert load_api_key('groq', env_file) == 'from-file'
    monkeypatch.setenv('GROQ_API_KEY', 'from-environment')
    assert load_api_key('groq', env_file) == 'from-environment'


def test_cli_uses_readonly_runner_and_prints_final_answer(monkeypatch, tmp_path, capsys):
    class Model:
        def generate(self, messages, *, tools, max_output_tokens, timeout_seconds):
            return type('Response', (), {
                'text': 'ok', 'input_tokens': 1, 'tool_calls': (),
                'output_tokens': 1, 'reasoning_tokens': 0,
            })()

    monkeypatch.setattr('agent_chat.load_api_key', lambda *a: 'fake')
    monkeypatch.setattr('agent_chat.create_model_client', lambda *args, **kwargs: Model())
    code = main(['--tools', 'read-only', '--provider', 'groq', '--model', 'test', '--workspace', str(tmp_path), '--task', 'hello'])
    assert code == 0
    assert 'ok' in capsys.readouterr().out


def test_sandbox_default_checks_ready_before_provider_and_closes(monkeypatch, tmp_path, capsys):
    events = []

    class Tools:
        def __init__(self, context, *, approval_callback):
            assert approval_callback is None
            events.append('create')

        def check_ready(self):
            events.append('ready')
            return False

        def close(self):
            events.append('close')

    monkeypatch.setattr('agent_chat.WorkspaceToolDispatcher', Tools)
    monkeypatch.setattr('agent_chat.load_api_key', lambda *a: events.append('key'))
    monkeypatch.setattr('agent_chat.create_model_client', lambda *a, **kw: events.append('model'))
    assert main(['--provider', 'groq', '--model', 'test', '--workspace', str(tmp_path), '--task', 'x']) == 2
    assert events == ['create', 'ready', 'close']
    assert 'could not start' in capsys.readouterr().err


def sandbox_cli_fakes(monkeypatch, *, ready=True, error=False):
    from agent_smith.loop.runner import ModelResponse
    events = []

    class Tools:
        system_guidance = 'sandbox fake'
        tool_definitions = []

        def __init__(self, context, *, approval_callback):
            self.callback = approval_callback
            events.append(self)

        def check_ready(self):
            if error:
                raise RuntimeError('secret-bearing startup error')
            return ready

        def close(self):
            events.append('closed')

    class Model:
        def generate(self, messages, **kwargs):
            events.append('request')
            return ModelResponse('ok', 1, 1)

    monkeypatch.setattr('agent_chat.WorkspaceToolDispatcher', Tools)
    monkeypatch.setattr('agent_chat.load_api_key', lambda *a: 'fake')
    monkeypatch.setattr('agent_chat.create_model_client', lambda *a, **kw: Model())
    return events


def test_sandbox_one_shot_warning_and_close(monkeypatch, tmp_path, capsys):
    events = sandbox_cli_fakes(monkeypatch)
    assert main(['--provider', 'groq', '--model', 'test', '--workspace', str(tmp_path), '--task', 'hello']) == 0
    assert events[0].callback is None
    assert events[1:] == ['request', 'closed']
    warning = capsys.readouterr().err
    for text in ['Seatbelt', 'no rollback', 'per-command', 'Network denied', 'protected paths',
                 'hardlinks', 'memory', 'BEST-EFFORT', 'detached survivors']:
        assert text in warning


def test_sandbox_setup_exception_redacted_and_closed(monkeypatch, tmp_path, capsys):
    events = sandbox_cli_fakes(monkeypatch, error=True)
    assert main(['--provider', 'groq', '--model', 'test', '--workspace', str(tmp_path), '--task', 'x']) == 2
    assert events[1:] == ['closed']
    assert 'secret-bearing' not in capsys.readouterr().err


def test_sandbox_closes_on_model_failure(monkeypatch, tmp_path):
    events = sandbox_cli_fakes(monkeypatch)

    class Model:
        def generate(self, *a, **kw):
            raise RuntimeError('secret')

    monkeypatch.setattr('agent_chat.create_model_client', lambda *a, **kw: Model())
    assert main(['--provider', 'groq', '--model', 'test', '--workspace', str(tmp_path), '--task', 'x']) == 1
    assert events[-1] == 'closed'


def test_help_documents_modes_and_risks(capsys):
    import pytest
    with pytest.raises(SystemExit) as exit:
        main(['--help'])
    assert exit.value.code == 0
    output = capsys.readouterr().out
    assert '--tools {sandboxed,read-only}' in output
    assert 'BEST-EFFORT' in output and 'no rollback' in ' '.join(output.split())


def test_unsupported_platform_never_requests_provider_or_falls_back(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr('agent_smith.sandbox.process.platform.system', lambda: 'Linux')

    def forbidden(*args, **kwargs):
        raise AssertionError('no provider or read-only fallback allowed')

    monkeypatch.setattr('agent_chat.load_api_key', forbidden)
    monkeypatch.setattr('agent_chat.create_model_client', forbidden)
    monkeypatch.setattr('agent_chat.ReadOnlyToolDispatcher', forbidden)
    assert main(['--provider', 'groq', '--model', 'test', '--workspace', str(tmp_path), '--task', 'x']) == 2
    assert 'could not start' in capsys.readouterr().err


def test_sandbox_one_shot_actual_dispatcher_denies_shell(monkeypatch, tmp_path, capsys):
    import platform
    import pytest
    from agent_smith.loop.runner import ModelResponse, ToolCall
    if platform.system() != 'Darwin':
        pytest.skip('native macOS Seatbelt only')
    calls = []

    class Model:
        def generate(self, messages, **kwargs):
            calls.append(messages)
            if len(calls) == 1:
                return ModelResponse('', 1, 1, tool_calls=(
                    ToolCall('call_1', 'run_shell', '{"command":"touch forbidden"}'),))
            return ModelResponse('denied', 1, 1)

    monkeypatch.setattr('agent_chat.load_api_key', lambda *a: 'fake')
    monkeypatch.setattr('agent_chat.create_model_client', lambda *a, **kw: Model())
    monkeypatch.setattr('builtins.input', lambda *a: pytest.fail('task must never read approval'))
    assert main(['--provider', 'groq', '--model', 'test', '--workspace', str(tmp_path), '--task', 'x']) == 0
    assert 'approval_denied' in calls[-1][-1]['content']
    assert not (tmp_path / 'forbidden').exists()
