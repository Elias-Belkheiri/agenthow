import json

import httpx
import pytest

from agent_smith.loop.chat import ReadOnlyChatRunner, ReadOnlyChatSession
from agent_smith.providers.factory import create_model_client
from agent_smith.tools.contracts import ToolContext
from agent_smith.tools.dispatcher import ReadOnlyToolDispatcher


@pytest.mark.parametrize('provider', ['groq', 'gemini'])
def test_mocked_provider_chat_carries_tool_observation_into_next_request(provider, tmp_path, monkeypatch, mock_model_http):
    (tmp_path / 'facts.txt').write_text('project status: prototype\n')
    requests = []
    replies = [
        json.dumps({'tool': 'read_file', 'arguments': {
            'filepath': 'facts.txt', 'start_line': 1, 'end_line': 1,
        }}),
        json.dumps({'final': 'The project is a prototype.'}),
        json.dumps({'final': 'You asked about the project status.'}),
    ]

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        content = replies.pop(0)
        if provider == 'groq':
            return httpx.Response(200, json={
                'choices': [{'message': {'content': content}}],
                'usage': {'prompt_tokens': 20, 'completion_tokens': 8,
                          'completion_tokens_details': {'reasoning_tokens': 2}},
            })
        return httpx.Response(200, json={
            'candidates': [{'content': {'role': 'model', 'parts': [{'text': content}]}, 'finishReason': 'STOP'}],
            'usageMetadata': {'promptTokenCount': 20, 'candidatesTokenCount': 6,
                              'thoughtsTokenCount': 2, 'totalTokenCount': 28},
        })

    monkeypatch.setenv(f'{provider.upper()}_API_KEY', 'mock-key')
    mock_model_http(respond)
    model_name = 'qwen/qwen3-32b' if provider == 'groq' else 'gemini-2.5-flash'
    model = create_model_client(provider, model=model_name, json_mode=True)
    session = ReadOnlyChatSession(model, ReadOnlyToolDispatcher(ToolContext(tmp_path)))
    result = session.send('Read facts')

    assert result.success
    assert result.final_answer == 'The project is a prototype.'
    assert len(requests) == 2
    assert result.total_output_tokens == 16
    if provider == 'groq':
        second_history = requests[1]['messages']
    else:
        second_history = [{'role': m['role'], 'content': ''.join(p.get('text', '') for p in m['parts'])}
                          for m in requests[1]['contents']]
    # Gemini sends system instructions separately from the conversational history.
    offset = 1 if provider == 'groq' else 0
    assert json.loads(second_history[1 + offset]['content'])['tool'] == 'read_file'
    assert '1: project status: prototype' in second_history[2 + offset]['content']
    followup_result = session.send('What did I ask about?')
    assert followup_result.success
    assert followup_result.total_output_tokens == 24
    if provider == 'groq':
        followup = requests[2]['messages']
    else:
        followup = [{'role': m['role'], 'content': ''.join(p.get('text', '') for p in m['parts'])}
                    for m in requests[2]['contents']]
    assert followup[offset]['content'] == 'Read facts'
    assert 'prototype' in followup[2 + offset]['content']
    assert json.loads(followup[3 + offset]['content'])['final'] == 'The project is a prototype.'
    assert followup[-1]['content'] == 'What did I ask about?'
