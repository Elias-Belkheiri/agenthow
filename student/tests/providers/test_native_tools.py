"""Native function calling through real SDK translation, mocked HTTP only."""
import json
from types import SimpleNamespace

import httpx
import pytest

from agent_smith.providers.contracts import ProviderError
from agent_smith.providers.factory import create_model_client


TOOLS = [{'type': 'function', 'function': {
    'name': 'list_files', 'description': 'List workspace files.',
    'parameters': {'type': 'object', 'properties': {
        'directory': {'type': 'string'}, 'pattern': {'type': 'string'}},
        'required': ['directory', 'pattern'], 'additionalProperties': False},
}}]


def reply(provider, *, call=True, signature=None):
    if provider == 'groq':
        message = {'role': 'assistant', 'content': None if call else 'Files: note.txt'}
        if call:
            message['tool_calls'] = [{'id': 'call_1', 'type': 'function', 'function': {
                'name': 'list_files', 'arguments': '{"directory":".","pattern":"*"}'}}]
        return {'choices': [{'message': message, 'finish_reason': 'tool_calls' if call else 'stop'}],
                'usage': {'prompt_tokens': 12, 'completion_tokens': 5, 'total_tokens': 17}}
    part = ({'functionCall': {'name': 'list_files', 'args': {'directory': '.', 'pattern': '*'}}}
            if call else {'text': 'Files: note.txt'})
    if call and signature:
        part['thoughtSignature'] = signature
    return {'candidates': [{'content': {'role': 'model', 'parts': [part]}, 'finishReason': 'STOP'}],
            'usageMetadata': {'promptTokenCount': 12, 'candidatesTokenCount': 5, 'totalTokenCount': 17}}


@pytest.mark.parametrize('provider', ['groq', 'gemini'])
def test_native_call_and_linked_tool_history(provider, monkeypatch, mock_model_http):
    monkeypatch.setenv(f'{provider.upper()}_API_KEY', 'fake')
    requests = mock_model_http(lambda request: httpx.Response(200, json=reply(provider, call=len(requests) == 1)))
    client = create_model_client(provider, model='qwen/qwen3-32b' if provider == 'groq' else 'gemini-2.5-flash', json_mode=True)
    messages = [{'role': 'user', 'content': 'List files'}]
    first = client.generate(messages, tools=TOOLS, max_output_tokens=30, timeout_seconds=3)
    assert first.text == ''
    assert len(first.tool_calls) == 1
    call = first.tool_calls[0]
    assert call.id and call.name == 'list_files'
    assert json.loads(call.arguments) == {'directory': '.', 'pattern': '*'}
    assert first.input_tokens == 12 and first.output_tokens == 5
    messages += [
        {'role': 'assistant', 'content': None, 'tool_calls': [call.as_dict()]},
        {'role': 'tool', 'tool_call_id': call.id, 'name': call.name,
         'content': '{"ok":true,"content":"note.txt"}'},
    ]
    final = client.generate(messages, tools=TOOLS, max_output_tokens=30, timeout_seconds=3)
    assert final.text == 'Files: note.txt' and not final.tool_calls
    assert len(requests) == 2
    first_body, second_body = [json.loads(r.content) for r in requests]
    if provider == 'groq':
        assert first_body['tools'] == TOOLS
        assert first_body['tool_choice'] == 'auto'
        assert 'response_format' not in first_body
        expected = [dict(m) for m in messages]
        expected[1].pop('content')  # SDK omits null content on call-only turns.
        assert second_body['messages'] == expected
        assert messages[1]['content'] is None  # SDK cannot mutate caller history.
    else:
        declarations = first_body['tools'][0]['function_declarations']
        assert declarations[0]['name'] == 'list_files'
        assert 'response_mime_type' not in first_body.get('generationConfig', {})
        parts = [p for m in second_body['contents'] for p in m['parts']]
        assert any('function_call' in p for p in parts)
        assert any(p.get('function_response', {}).get('name') == 'list_files' for p in parts)
        assert 'note.txt' in json.dumps(parts)


def test_gemini_thought_signature_survives_native_round_trip(monkeypatch, mock_model_http):
    monkeypatch.setenv('GEMINI_API_KEY', 'fake')
    signature = 'abc/+=' * 60
    requests = mock_model_http(lambda request: httpx.Response(
        200, json=reply('gemini', call=len(requests) == 1, signature=signature)))
    client = create_model_client('gemini', model='gemini-2.5-flash')
    first = client.generate([{'role': 'user', 'content': 'list'}],
                            tools=TOOLS, max_output_tokens=30, timeout_seconds=3)
    call = first.tool_calls[0]
    client.generate([
        {'role': 'user', 'content': 'list'},
        {'role': 'assistant', 'content': None, 'tool_calls': [call.as_dict()]},
        {'role': 'tool', 'tool_call_id': call.id, 'name': call.name, 'content': '{"ok":true}'},
    ], tools=TOOLS, max_output_tokens=30, timeout_seconds=3)
    body = json.loads(requests[1].content)
    part = body['contents'][1]['parts'][0]
    assert part['thoughtSignature'] == signature


@pytest.mark.parametrize('provider', ['groq', 'gemini'])
def test_unsupported_tools_is_single_attempt_without_fallback(provider, monkeypatch, mock_model_http):
    monkeypatch.setenv(f'{provider.upper()}_API_KEY', 'fake')
    requests = mock_model_http(lambda request: httpx.Response(400, json={'error': {'message': 'tools unsupported'}}))
    client = create_model_client(provider, model='test')
    with pytest.raises(ProviderError, match='http_400'):
        client.generate([{'role': 'user', 'content': 'hi'}], tools=TOOLS, max_output_tokens=30, timeout_seconds=3)
    assert len(requests) == 1


@pytest.mark.parametrize('calls', [
    [SimpleNamespace(id='', type='function', function=SimpleNamespace(name='list_files', arguments='{}'))],
    [SimpleNamespace(id='a', type='other', function=SimpleNamespace(name='list_files', arguments='{}'))],
    [SimpleNamespace(id='a', type='function', function=SimpleNamespace(name='', arguments='{}'))],
    [SimpleNamespace(id='a', type='function', function=SimpleNamespace(name='list_files', arguments={}))],
])
def test_malformed_sdk_call_metadata_is_rejected(calls, monkeypatch):
    import litellm
    monkeypatch.setenv('GROQ_API_KEY', 'fake')
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=calls))],
                               usage=SimpleNamespace(prompt_tokens=12, completion_tokens=5))
    monkeypatch.setattr(litellm, 'completion', lambda **kw: response)
    client = create_model_client('groq', model='test')
    with pytest.raises(ProviderError, match='invalid_response'):
        client.generate([], tools=TOOLS, max_output_tokens=30, timeout_seconds=3)
