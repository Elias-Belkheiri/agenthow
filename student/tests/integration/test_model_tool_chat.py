"""Real SDK native tools -> dispatcher -> linked tool result -> plain answer."""
import json

import httpx
import pytest

from agent_smith.loop.chat import ReadOnlyChatSession
from agent_smith.providers.factory import create_model_client
from agent_smith.tools.contracts import ToolContext
from agent_smith.tools.dispatcher import ReadOnlyToolDispatcher


@pytest.mark.parametrize('provider', ['groq', 'gemini'])
def test_mocked_provider_chat_carries_tool_observation_into_next_request(provider, tmp_path, monkeypatch, mock_model_http):
    (tmp_path / 'facts.txt').write_text('project status: prototype\n')
    requests = []
    args = {'filepath': 'facts.txt', 'start_line': 1, 'end_line': 1}

    def respond(request):
        requests.append(json.loads(request.content))
        first = len(requests) == 1
        text = ('The project is a prototype.' if len(requests) == 2 else
                'You asked about the project status.')
        if provider == 'groq':
            message = {'role': 'assistant', 'content': None if first else text}
            if first:
                message['tool_calls'] = [{'id': 'read_1', 'type': 'function', 'function': {
                    'name': 'read_file', 'arguments': json.dumps(args)}}]
            return httpx.Response(200, json={
                'choices': [{'message': message, 'finish_reason': 'tool_calls' if first else 'stop'}],
                'usage': {'prompt_tokens': 20, 'completion_tokens': 8,
                          'completion_tokens_details': {'reasoning_tokens': 2}},
            })
        part = {'functionCall': {'name': 'read_file', 'args': args}} if first else {'text': text}
        return httpx.Response(200, json={
            'candidates': [{'content': {'role': 'model', 'parts': [part]}, 'finishReason': 'STOP'}],
            'usageMetadata': {'promptTokenCount': 20, 'candidatesTokenCount': 6,
                              'thoughtsTokenCount': 2, 'totalTokenCount': 28},
        })

    monkeypatch.setenv(f'{provider.upper()}_API_KEY', 'mock-key')
    mock_model_http(respond)
    model_name = 'qwen/qwen3-32b' if provider == 'groq' else 'gemini-2.5-flash'
    model = create_model_client(provider, model=model_name)
    session = ReadOnlyChatSession(model, ReadOnlyToolDispatcher(ToolContext(tmp_path)))
    result = session.send('Read facts')
    assert result.success and result.final_answer == 'The project is a prototype.'
    assert len(requests) == 2
    assert result.total_output_tokens == 16
    if provider == 'groq':
        history = requests[1]['messages']
        assert history[2]['tool_calls'][0]['id'] == history[3]['tool_call_id'] == 'read_1'
        assert history[3]['role'] == 'tool'
        assert '1: project status: prototype' in history[3]['content']
        assert 'response_format' not in requests[0]
    else:
        history = requests[1]['contents']
        assert history[1]['parts'][0]['function_call']['name'] == 'read_file'
        observation = history[2]['parts'][0]['function_response']
        assert observation['name'] == 'read_file'
        assert '1: project status: prototype' in observation['response']['content']
        assert 'response_mime_type' not in requests[0]['generationConfig']
    followup_result = session.send('What did I ask about?')
    assert followup_result.success and followup_result.total_output_tokens == 24
    if provider == 'groq':
        followup = requests[2]['messages']
        assert followup[1]['content'] == 'Read facts'
        assert followup[4]['content'] == 'The project is a prototype.'
        assert followup[-1]['content'] == 'What did I ask about?'
    else:
        followup = requests[2]['contents']
        assert followup[0]['parts'][0]['text'] == 'Read facts'
        assert followup[3]['parts'][0]['text'] == 'The project is a prototype.'
        assert followup[-1]['parts'][0]['text'] == 'What did I ask about?'
