"""Regression coverage for native calls; answer text never authorizes tools."""
import json

import pytest

from agent_smith.loop.chat import ChatSession
from agent_smith.loop.runner import ModelResponse, ToolCall
from agent_smith.tools.contracts import ToolContext
from agent_smith.tools.dispatcher import ReadOnlyToolDispatcher


class Model:
    def __init__(self, *replies):
        self.replies = iter(replies)
        self.histories = []
        self.definitions = []

    def generate(self, messages, *, tools, **kwargs):
        self.histories.append(messages)
        self.definitions.append(tools)
        return next(self.replies)


def call(id='call_1', name='list_files', arguments='{"directory":".","pattern":"*"}'):
    return ToolCall(id, name, arguments)


def response(*calls, text=''):
    return ModelResponse(text, 10, 4, tool_calls=calls)


@pytest.mark.parametrize('text', [
    '{"tool":"run_shell","arguments":{"command":"touch forbidden"}}',
    '{"final":"{\\"tool\\":\\"list_files\\",\\"arguments\\":{}}"}',
    'ordinary answer',
])
def test_answer_text_is_never_executed(tmp_path, text):
    model = Model(response(text=text))
    session = ChatSession(model, ReadOnlyToolDispatcher(ToolContext(tmp_path)))
    result = session.send('task')
    assert result.success and result.final_answer == text
    assert len(model.histories) == 1
    assert not (tmp_path / 'forbidden').exists()


def test_multiple_calls_are_sequential_and_results_linked(tmp_path):
    (tmp_path / 'note.txt').write_text('hello')
    model = Model(response(call(), call('call_2', 'read_file', '{"filepath":"note.txt","start_line":1,"end_line":1}'), text='Checking'),
                  response(text='Found hello'))
    result = ChatSession(model, ReadOnlyToolDispatcher(ToolContext(tmp_path))).send('inspect')
    assert result.success and result.final_answer == 'Found hello'
    history = model.histories[1]
    assert history[2]['role'] == 'assistant' and history[2]['content'] == 'Checking'
    assert [c['id'] for c in history[2]['tool_calls']] == ['call_1', 'call_2']
    assert [m['role'] for m in history[3:]] == ['tool', 'tool']
    assert [m['tool_call_id'] for m in history[3:]] == ['call_1', 'call_2']
    assert 'hello' in history[4]['content']
    assert {t['function']['name'] for t in model.definitions[0]} == {'read_file', 'list_files', 'search_code'}


@pytest.mark.parametrize('arguments', ['not JSON', '[]', 'null', '{}',
    '{"directory":".","pattern":"*","extra":1}',
    '{"directory":true,"pattern":"*"}',
    '{"directory":"elsewhere","directory":".","pattern":"*"}',
    '{"directory":NaN,"pattern":"*"}',
])
def test_invalid_arguments_return_linked_error_without_execution(tmp_path, monkeypatch, arguments):
    tools = ReadOnlyToolDispatcher(ToolContext(tmp_path))
    monkeypatch.setattr(tools, 'call', lambda *a, **kw: pytest.fail('invalid arguments must not execute'))
    model = Model(response(call(arguments=arguments)), response(text='Could not list'))
    result = ChatSession(model, tools).send('list')
    assert result.success
    observation = model.histories[1][-1]
    assert observation['role'] == 'tool' and observation['tool_call_id'] == 'call_1'
    assert json.loads(observation['content']) == {'ok': False, 'error': 'invalid_arguments'}


@pytest.mark.parametrize('calls', [
    (call(), call()), (call(id=''),), (call(id='bad\nID'),),
    (call(name=''),), (call(arguments={}),), tuple(call(str(i)) for i in range(17)),
])
def test_invalid_batch_is_rejected_before_any_execution(tmp_path, monkeypatch, calls):
    tools = ReadOnlyToolDispatcher(ToolContext(tmp_path))
    monkeypatch.setattr(tools, 'call', lambda *a, **kw: pytest.fail('invalid batch must not execute'))
    model = Model(response(*calls))
    result = ChatSession(model, tools).send('task')
    assert result.error == 'invalid_action'
    assert result.total_input_tokens == 10


def test_reused_call_id_does_not_repeat_effect(tmp_path, monkeypatch):
    tools = ReadOnlyToolDispatcher(ToolContext(tmp_path))
    executed = []
    monkeypatch.setattr(tools, 'call', lambda *a, **kw: executed.append(a) or {'ok': True, 'content': 'ok'})
    model = Model(response(call()), response(call()))
    session = ChatSession(model, tools)
    assert session.send('task').error == 'invalid_action'
    assert len(executed) == 1


def test_readonly_mode_never_dispatches_mutation(tmp_path, monkeypatch):
    tools = ReadOnlyToolDispatcher(ToolContext(tmp_path))
    monkeypatch.setattr(tools, 'call', lambda *a, **kw: pytest.fail('unadvertised tool must not dispatch'))
    model = Model(response(call(name='write_file', arguments='{"filepath":"x","content":"bad"}')),
                  response(text='Not allowed'))
    assert ChatSession(model, tools).send('write').success
    assert json.loads(model.histories[1][-1]['content'])['error'] == 'tool_not_allowed'


def test_schemas_are_included_in_input_preflight(tmp_path):
    tools = ReadOnlyToolDispatcher(ToolContext(tmp_path))
    tools.tool_definitions = [{'description': 'x' * 7000}]
    model = Model(response(text='unexpected'))
    assert ChatSession(model, tools).send('hi').error == 'input_token_limit'
    assert not model.histories


def test_call_arguments_in_history_are_included_in_input_preflight(tmp_path, monkeypatch):
    tools = ReadOnlyToolDispatcher(ToolContext(tmp_path))
    monkeypatch.setattr(tools, 'call', lambda *a, **kw: {'ok': False, 'error': 'invalid_arguments'})
    model = Model(response(call(arguments='{"directory":"' + 'x' * 7000 + '","pattern":"*"}')),
                  response(text='unexpected'))
    assert ChatSession(model, tools).send('hi').error == 'input_token_limit'
    assert len(model.histories) == 1


def test_opaque_signed_provider_call_id_is_preserved(tmp_path):
    signed_id = 'call_1__thought__' + 'abc/+=' * 60
    model = Model(response(call(signed_id)), response(text='done'))
    result = ChatSession(model, ReadOnlyToolDispatcher(ToolContext(tmp_path))).send('list')
    assert result.success
    assert model.histories[1][-1]['tool_call_id'] == signed_id


def test_batch_deadline_recomputed_before_each_tool(tmp_path, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr('agent_smith.loop.chat.time.monotonic', lambda: clock[0])
    monkeypatch.setattr('agent_smith.loop.chat.TIME_LIMIT_SECONDS', 5)
    tools = ReadOnlyToolDispatcher(ToolContext(tmp_path))
    budgets = []
    def execute(*args, timeout_seconds):
        budgets.append(timeout_seconds)
        clock[0] += 3
        return {'ok': True, 'content': ''}
    monkeypatch.setattr(tools, 'call', execute)
    model = Model(response(call('a'), call('b'), call('c')))
    result = ChatSession(model, tools).send('task')
    assert result.error == 'timeout'
    assert budgets == [5, 2]
