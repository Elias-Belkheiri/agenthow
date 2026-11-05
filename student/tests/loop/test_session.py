import json

from agent_smith.loop.chat import ReadOnlyChatSession
from agent_smith.loop.runner import ModelResponse, ToolCall
from agent_smith.tools.contracts import ToolContext
from agent_smith.tools.definitions import tool_definitions
from agent_smith.tools.dispatcher import ReadOnlyToolDispatcher


class Model:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.histories = []

    def generate(self, messages, **kwargs):
        self.histories.append(messages)
        reply = next(self.replies)
        return reply if isinstance(reply, ModelResponse) else ModelResponse(reply, 10, 4)


def tool(name, **arguments):
    return ModelResponse('', 10, 4, tool_calls=(ToolCall('call_1', name, json.dumps(arguments)),))


def test_followup_remembers_tools_and_answer_then_reset_clears(tmp_path):
    (tmp_path / 'fact.txt').write_text('blue')
    model = Model([tool('read_file', filepath='fact.txt', start_line=1, end_line=1),
                   'blue', 'You said blue', 'fresh'])
    session = ReadOnlyChatSession(model, ReadOnlyToolDispatcher(ToolContext(tmp_path)))
    session.send('Read the color')
    result = session.send('What color was it?')
    history = model.histories[-1]
    assert history[1]['content'] == 'Read the color'
    assert '1: blue' in history[3]['content']
    assert history[4] == {'role': 'assistant', 'content': 'blue'}
    assert history[-1]['content'] == 'What color was it?'
    assert result.total_requests == 3
    assert result.total_input_tokens == 30
    session.reset()
    fresh = session.send('New topic')
    assert len(model.histories[-1]) == 2
    assert fresh.total_requests == 1
    assert fresh.total_input_tokens == 10


def test_failed_turn_is_removed_but_usage_is_retained(tmp_path):
    model = Model(['', 'recovered'])
    session = ReadOnlyChatSession(model, ReadOnlyToolDispatcher(ToolContext(tmp_path)))
    assert session.send('bad prompt').error == 'invalid_action'
    result = session.send('try again')
    assert result.success
    assert len(model.histories[-1]) == 2
    assert result.total_requests == 2
    assert result.total_input_tokens == 20


def test_session_budget_is_not_reset_between_prompts(tmp_path):
    class ExpensiveModel(Model):
        def generate(self, messages, **kwargs):
            self.histories.append(messages)
            return ModelResponse('ok', 5000, 1)
    model = ExpensiveModel([])
    session = ReadOnlyChatSession(model, ReadOnlyToolDispatcher(ToolContext(tmp_path)))
    assert session.send('hello').success
    assert session.send('x' * 1000).error == 'input_token_limit'
    assert len(model.histories) == 1


def test_failed_mutating_turn_not_replayed_or_rolled_back(tmp_path):
    from agent_smith.loop.chat import ChatSession
    calls = []
    class Tools:
        system_guidance = 'direct writes; no rollback'
        tool_definitions = tool_definitions(('write_file',))
        def call(self, name, arguments, *, timeout_seconds):
            calls.append(arguments)
            (tmp_path / 'effect').write_text('changed')
            return {'ok': True, 'content': 'written', 'error': None, 'truncated': False}
    model = Model([tool('write_file', filepath='effect', content='changed'), '', 'recovered'])
    session = ChatSession(model, Tools())
    assert session.send('mutate').error == 'invalid_action'
    assert (tmp_path / 'effect').read_text() == 'changed'
    result = session.send('recover')
    assert result.success and result.total_requests == 3
    assert len(calls) == 1 and len(model.histories[-1]) == 2
