import json
from pathlib import Path

from agent_smith.loop.chat import ReadOnlyChatRunner
from agent_smith.loop.runner import ModelResponse
from agent_smith.tools.contracts import ToolContext
from agent_smith.tools.dispatcher import ReadOnlyToolDispatcher


class FakeModel:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.histories = []

    def generate(self, messages, *, max_output_tokens, timeout_seconds):
        self.histories.append(messages)
        return ModelResponse(next(self.replies), 10, 4)


def test_system_guidance_documents_exact_readonly_tool_arguments():
    from agent_smith.loop.chat import SYSTEM_TOOL_GUIDANCE

    assert 'filepath' in SYSTEM_TOOL_GUIDANCE
    assert 'start_line' in SYSTEM_TOOL_GUIDANCE
    assert 'end_line' in SYSTEM_TOOL_GUIDANCE
    assert 'directory' in SYSTEM_TOOL_GUIDANCE
    assert 'file_pattern' in SYSTEM_TOOL_GUIDANCE


def test_readonly_tool_round_trip_preserves_complete_history(tmp_path: Path):
    (tmp_path / 'readme.txt').write_text('the answer is 42\n')
    model = FakeModel([
        json.dumps({'tool': 'read_file', 'arguments': {'filepath': 'readme.txt', 'start_line': 1, 'end_line': 1}}),
        json.dumps({'final': 'The answer is 42.'}),
    ])
    runner = ReadOnlyChatRunner(model, ReadOnlyToolDispatcher(ToolContext(tmp_path)))

    result = runner.run('Find the answer', system_prompt='Use read-only tools.')

    assert result.success and result.final_answer == 'The answer is 42.'
    assert len(model.histories) == 2
    second_turn = model.histories[1]
    assert second_turn[0]['role'] == 'system'
    assert second_turn[0]['content'].startswith('Use read-only tools.')
    assert second_turn[1] == {'role': 'user', 'content': 'Find the answer'}
    assert json.loads(second_turn[2]['content'])['tool'] == 'read_file'
    assert '1: the answer is 42' in second_turn[3]['content']


def test_logs_turn_metadata_without_prompt_content_or_secrets(tmp_path: Path, caplog):
    caplog.set_level('INFO')
    model = FakeModel([json.dumps({'final': 'safe response'})])
    model.provider_name = 'test-provider'
    ReadOnlyChatRunner(model, ReadOnlyToolDispatcher(ToolContext(tmp_path))).run('sensitive task text')

    records = [record for record in caplog.records if record.message == 'model_turn_completed']
    assert len(records) == 1
    assert records[0].provider == 'test-provider'
    assert records[0].input_tokens == 10
    assert 'sensitive task text' not in caplog.text


def test_oversized_prompt_is_rejected_before_provider_request(tmp_path: Path):
    class CountingModel:
        requests = 0

        def generate(self, *args, **kwargs):
            self.requests += 1
            return ModelResponse('{"final":"unexpected"}', 1, 1)

    model = CountingModel()
    result = ReadOnlyChatRunner(model, ReadOnlyToolDispatcher(ToolContext(tmp_path))).run('x' * 7000)

    assert result.error == 'input_token_limit'
    assert model.requests == 0


def test_model_exception_text_is_not_logged(tmp_path: Path, caplog):
    caplog.set_level('INFO')

    class BrokenModel:
        def generate(self, *args, **kwargs):
            raise RuntimeError('secret-token-value')

    result = ReadOnlyChatRunner(BrokenModel(), ReadOnlyToolDispatcher(ToolContext(tmp_path))).run('task')

    assert result.error == 'model_error'
    assert 'secret-token-value' not in caplog.text


def test_rejected_tool_is_recorded_as_real_observation(tmp_path: Path):
    model = FakeModel([
        json.dumps({'tool': 'shell', 'arguments': {'command': 'echo unsafe'}}),
        json.dumps({'final': 'I cannot use shell.'}),
    ])
    result = ReadOnlyChatRunner(model, ReadOnlyToolDispatcher(ToolContext(tmp_path))).run('Inspect')

    assert result.success
    assert 'tool_not_allowed' in model.histories[1][-1]['content']


def test_capability_guidance_observations_and_trusted_budget(monkeypatch):
    from agent_smith.loop.chat import ChatRunner
    clock = [0.0]
    monkeypatch.setattr('agent_smith.loop.chat.time.monotonic', lambda: clock[0])
    monkeypatch.setattr('agent_smith.loop.chat.TIME_LIMIT_SECONDS', 400)
    calls = []

    class Tools:
        system_guidance = 'custom trusted capabilities'

        def call(self, name, arguments, *, timeout_seconds):
            calls.append((name, arguments, timeout_seconds))
            return {'ok': False, 'error': 'approval_denied', 'stdout': 'untrusted output'}

    class Model(FakeModel):
        def generate(self, *args, **kwargs):
            clock[0] += 20
            return super().generate(*args, **kwargs)

    model = Model(['{"tool":"run_shell","arguments":{"command":"true"}}', '{"final":"done"}'])
    assert ChatRunner(model, Tools()).run('task').success
    assert calls == [('run_shell', {'command': 'true'}, 300.0)]
    assert model.histories[0][0]['content'] == Tools.system_guidance
    assert 'approval_denied' in model.histories[1][-1]['content']
    assert 'untrusted output' in model.histories[1][-1]['content']


def test_remaining_budget_recomputed_and_timeout_after_execution(monkeypatch):
    from agent_smith.loop.chat import ChatRunner
    clock = [0.0]
    monkeypatch.setattr('agent_smith.loop.chat.time.monotonic', lambda: clock[0])
    monkeypatch.setattr('agent_smith.loop.chat.TIME_LIMIT_SECONDS', 10)
    budgets = []

    class Tools:
        system_guidance = 'test tools'

        def call(self, name, arguments, *, timeout_seconds):
            budgets.append(timeout_seconds)
            clock[0] += 8
            return {'ok': False, 'error': 'timeout'}

    class Model(FakeModel):
        def generate(self, *args, **kwargs):
            clock[0] += 3
            return super().generate(*args, **kwargs)

    model = Model(['{"tool":"run_shell","arguments":{"command":"true"}}'])
    result = ChatRunner(model, Tools()).run('task')
    assert result.error == 'timeout'
    assert budgets == [7]
    assert len(model.histories) == 1


def test_sandbox_guidance_exact_schemas_no_policy_overrides():
    from agent_smith.tools.workspace import WorkspaceToolDispatcher
    from agent_smith.loop.chat import ChatRunner, ReadOnlyChatRunner, ChatSession, ReadOnlyChatSession
    assert ChatRunner is ReadOnlyChatRunner
    assert ChatSession is ReadOnlyChatSession
    guidance = WorkspaceToolDispatcher.system_guidance
    actions = [json.loads(line.split(': ', 1)[1]) for line in guidance.splitlines() if line.startswith('- ')]
    assert {a['tool']: set(a['arguments']) for a in actions} == {
        'read_file': {'filepath', 'start_line', 'end_line'},
        'list_files': {'directory', 'pattern'}, 'search_code': {'pattern', 'file_pattern'},
        'write_file': {'filepath', 'content'}, 'edit_file': {'filepath', 'old_text', 'new_text'},
        'run_shell': {'command'},
    }
    assert 'Only read-only' not in guidance
    assert 'no rollback' in guidance and 'Network access is denied' in guidance
