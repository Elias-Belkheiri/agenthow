import json
import logging
from types import SimpleNamespace

import httpx
import pytest

from agent_smith.loop.runner import UsageCounter
from agent_smith.providers.contracts import ProviderError
from agent_smith.providers.factory import create_model_client


def payload(provider, *, text='hello'):
    if provider == 'groq':
        return {
            'choices': [{'message': {'role': 'assistant', 'content': text}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 12, 'completion_tokens': 5, 'total_tokens': 17,
                      'completion_tokens_details': {'reasoning_tokens': 2}},
        }
    return {
        'candidates': [{'content': {'role': 'model', 'parts': [{'text': text}]}, 'finishReason': 'STOP'}],
        'usageMetadata': {'promptTokenCount': 12, 'candidatesTokenCount': 3,
                          'thoughtsTokenCount': 2, 'totalTokenCount': 17},
    }


@pytest.fixture(params=['groq', 'gemini'])
def provider(request, monkeypatch):
    name = request.param
    monkeypatch.setenv(f'{name.upper()}_API_KEY', 'not-for-logs')
    return name


def test_sdk_translates_request_and_counts_reasoning_once(provider, mock_model_http):
    requests = mock_model_http(lambda request: httpx.Response(200, json=payload(provider)))
    model_name = 'qwen/qwen3-32b' if provider == 'groq' else 'gemini-2.5-flash'
    client = create_model_client(provider, model=model_name, json_mode=True)
    messages = [{'role': 'system', 'content': 'Respond with JSON.'}, {'role': 'user', 'content': 'hi'}]
    response = client.generate(messages, max_output_tokens=20, timeout_seconds=3)

    assert response.text == 'hello'
    assert (response.input_tokens, response.output_tokens, response.reasoning_tokens) == (12, 3, 2)
    usage = UsageCounter(lambda messages: 0)
    assert usage.record(response)
    assert usage.output_tokens == 5
    assert len(requests) == 1
    request = requests[0]
    body = json.loads(request.content)
    assert request.extensions['timeout']['read'] == 3
    if provider == 'groq':
        assert str(request.url) == 'https://api.groq.com/openai/v1/chat/completions'
        assert request.headers['authorization'] == 'Bearer not-for-logs'
        assert body['model'] == model_name
        assert body['messages'] == messages
        assert body['max_tokens'] == 20
        assert body['response_format'] == {'type': 'json_object'}
    else:
        assert str(request.url) == f'https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent'
        assert request.headers['x-goog-api-key'] == 'not-for-logs'
        assert body['system_instruction']['parts'] == [{'text': 'Respond with JSON.'}]
        assert body['contents'] == [{'role': 'user', 'parts': [{'text': 'hi'}]}]
        assert body['generationConfig']['max_output_tokens'] == 20
        assert body['generationConfig']['response_mime_type'] == 'application/json'


@pytest.mark.parametrize(('name', 'url', 'expected'), [
    ('groq', 'https://example.invalid/v1', 'https://example.invalid/v1/chat/completions'),
    ('groq', 'https://example.invalid/v1/chat/completions/', 'https://example.invalid/v1/chat/completions'),
    ('gemini', 'https://example.invalid/v1beta', 'https://example.invalid/v1beta/models/gemini-2.5-flash:generateContent'),
    ('gemini', 'https://example.invalid/v1beta/models/', 'https://example.invalid/v1beta/models/gemini-2.5-flash:generateContent'),
])
def test_custom_urls_accept_bases_and_legacy_endpoints(name, url, expected, monkeypatch, mock_model_http):
    monkeypatch.setenv(f'{name.upper()}_API_KEY', 'fake')
    requests = mock_model_http(lambda request: httpx.Response(200, json=payload(name)))
    client = create_model_client(name, model='gemini-2.5-flash', api_url=url)
    client.generate([{'role': 'user', 'content': 'hi'}], max_output_tokens=20, timeout_seconds=3)
    assert str(requests[0].url) == expected


@pytest.mark.parametrize('status', [400, 401, 403, 404, 429, 500, 503])
def test_http_errors_are_single_attempt_and_credential_safe(provider, status, mock_model_http, capsys, caplog):
    requests = mock_model_http(lambda request: httpx.Response(status, json={
        'error': {'message': 'not-for-logs private prompt', 'code': status, 'status': 'UNKNOWN'},
    }))
    client = create_model_client(provider, model='gemini-2.5-flash')
    with caplog.at_level(logging.DEBUG), pytest.raises(ProviderError) as exc:
        client.generate([{'role': 'user', 'content': 'private prompt'}], max_output_tokens=20, timeout_seconds=3)
    assert exc.value.code == f'http_{status}'
    assert len(requests) == 1
    captured = capsys.readouterr()
    exposed = str(exc.value) + caplog.text + captured.out + captured.err
    assert 'not-for-logs' not in exposed
    assert 'private prompt' not in exposed


@pytest.mark.parametrize(('exception', 'code'), [
    (httpx.ReadTimeout, 'timeout'), (httpx.ConnectError, 'network_error'),
])
def test_transport_errors_are_single_attempt(provider, exception, code, mock_model_http):
    def fail(request):
        raise exception('not-for-logs', request=request)

    requests = mock_model_http(fail)
    client = create_model_client(provider, model='gemini-2.5-flash')
    with pytest.raises(ProviderError) as exc:
        client.generate([{'role': 'user', 'content': 'hi'}], max_output_tokens=20, timeout_seconds=3)
    assert exc.value.code == code
    assert len(requests) == 1


def test_text_mode_does_not_force_json(provider, mock_model_http):
    requests = mock_model_http(lambda request: httpx.Response(200, json=payload(provider)))
    client = create_model_client(provider, model='gemini-2.5-flash')
    client.generate([{'role': 'user', 'content': 'hi'}], max_output_tokens=20, timeout_seconds=3)
    body = json.loads(requests[0].content)
    assert 'response_format' not in body
    assert 'response_mime_type' not in body.get('generationConfig', {})


def test_missing_credentials_and_model_fail_without_http(provider, monkeypatch, mock_model_http):
    requests = mock_model_http(lambda request: pytest.fail('Unexpected HTTP request'))
    monkeypatch.delenv(f'{provider.upper()}_API_KEY')
    client = create_model_client(provider, model='m')
    with pytest.raises(ProviderError, match='missing_credentials'):
        client.generate([], max_output_tokens=20, timeout_seconds=3)
    monkeypatch.setenv(f'{provider.upper()}_API_KEY', 'fake')
    client = create_model_client(provider, model='')
    with pytest.raises(ProviderError, match='missing_model'):
        client.generate([], max_output_tokens=20, timeout_seconds=3)
    assert not requests


@pytest.mark.parametrize('body', [{}, {'choices': []}, {'candidates': []}])
def test_malformed_responses_are_classified(provider, body, mock_model_http):
    mock_model_http(lambda request: httpx.Response(200, json=body))
    client = create_model_client(provider, model='gemini-2.5-flash')
    with pytest.raises(ProviderError, match='invalid_response|invalid_usage'):
        client.generate([{'role': 'user', 'content': 'hi'}], max_output_tokens=20, timeout_seconds=3)


@pytest.mark.parametrize(('prompt', 'completion', 'reasoning'), [
    (None, 5, 2), (12, None, 2), (-1, 5, 2), (12, 5, -1),
    (12, 5, 6), (True, 5, 2), (12, 5.5, 2),
])
def test_invalid_sdk_usage_is_rejected(provider, monkeypatch, prompt, completion, reasoning):
    import litellm

    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='hello'))],
        usage=SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion,
                              completion_tokens_details=SimpleNamespace(reasoning_tokens=reasoning)),
    )
    monkeypatch.setattr(litellm, 'completion', lambda **kwargs: response)
    client = create_model_client(provider, model='m')
    with pytest.raises(ProviderError, match='invalid_usage'):
        client.generate([], max_output_tokens=20, timeout_seconds=3)


def test_absent_usage_is_rejected(provider, mock_model_http):
    body = payload(provider)
    del body['usage' if provider == 'groq' else 'usageMetadata']
    mock_model_http(lambda request: httpx.Response(200, json=body))
    client = create_model_client(provider, model='gemini-2.5-flash')
    with pytest.raises(ProviderError, match='invalid_usage|invalid_response'):
        client.generate([{'role': 'user', 'content': 'hi'}], max_output_tokens=20, timeout_seconds=3)


def test_reasoning_details_are_optional(provider, mock_model_http):
    body = payload(provider)
    if provider == 'groq':
        del body['usage']['completion_tokens_details']
    else:
        del body['usageMetadata']['thoughtsTokenCount']
        body['usageMetadata']['totalTokenCount'] = 15
    mock_model_http(lambda request: httpx.Response(200, json=body))
    client = create_model_client(provider, model='gemini-2.5-flash')
    result = client.generate([{'role': 'user', 'content': 'hi'}], max_output_tokens=20, timeout_seconds=3)
    assert result.reasoning_tokens == 0
    assert result.output_tokens == (5 if provider == 'groq' else 3)


@pytest.mark.parametrize(('tokens', 'timeout', 'error'), [(0, 3, 'invalid_configuration'), (20, 0, 'timeout')])
def test_exhausted_budgets_do_not_send_requests(provider, tokens, timeout, error, mock_model_http):
    requests = mock_model_http(lambda request: pytest.fail('Unexpected HTTP request'))
    client = create_model_client(provider, model='m')
    with pytest.raises(ProviderError, match=error):
        client.generate([], max_output_tokens=tokens, timeout_seconds=timeout)
    assert not requests


@pytest.mark.parametrize('message', [
    'Conversation roles must alternate user/assistant',
    'Last message must have role `user`',
])
def test_sdk_message_repair_does_not_issue_another_request(provider, message, mock_model_http):
    def respond(request):
        if len(requests) == 1:
            return httpx.Response(400, json={'error': {'message': message}})
        return httpx.Response(200, json=payload(provider))

    requests = mock_model_http(respond)
    client = create_model_client(provider, model='qwen/qwen3-32b')
    with pytest.raises(ProviderError, match='http_400'):
        client.generate([{'role': 'user', 'content': 'hi'}], max_output_tokens=20, timeout_seconds=3)
    assert len(requests) == 1


@pytest.mark.parametrize('model', ['openai/gpt-oss-120b', 'openai/gpt-oss-20b', 'qwen/qwen3-32b'])
def test_groq_preserves_the_complete_provider_model_id(model, monkeypatch, mock_model_http):
    monkeypatch.setenv('GROQ_API_KEY', 'fake')
    requests = mock_model_http(lambda request: httpx.Response(200, json=payload('groq')))
    client = create_model_client('groq', model=model)
    client.generate([{'role': 'user', 'content': 'hi'}], max_output_tokens=20, timeout_seconds=3)
    assert json.loads(requests[0].content)['model'] == model


def test_redirects_do_not_issue_another_request_or_forward_credentials(provider, mock_model_http):
    requests = mock_model_http(lambda request: httpx.Response(
        307, headers={'location': 'https://redirect.invalid/completion'},
    ))
    client = create_model_client(provider, model='gemini-2.5-flash')
    with pytest.raises(ProviderError, match='http_307'):
        client.generate([{'role': 'user', 'content': 'hi'}], max_output_tokens=20, timeout_seconds=3)
    assert len(requests) == 1


@pytest.mark.parametrize('bad_count', [True, -1, 1.5, '12', None])
def test_raw_usage_cannot_be_coerced_into_valid_counts(provider, bad_count, mock_model_http):
    body = payload(provider)
    if provider == 'groq':
        body['usage']['prompt_tokens'] = bad_count
    else:
        body['usageMetadata']['promptTokenCount'] = bad_count
    mock_model_http(lambda request: httpx.Response(200, json=body))
    client = create_model_client(provider, model='gemini-2.5-flash')
    with pytest.raises(ProviderError, match='invalid_usage'):
        client.generate([{'role': 'user', 'content': 'hi'}], max_output_tokens=20, timeout_seconds=3)


def test_missing_completion_usage_cannot_be_replaced_by_reasoning(provider, mock_model_http):
    body = payload(provider)
    if provider == 'groq':
        del body['usage']['completion_tokens']
    else:
        del body['usageMetadata']['candidatesTokenCount']
    mock_model_http(lambda request: httpx.Response(200, json=body))
    client = create_model_client(provider, model='gemini-2.5-flash')
    with pytest.raises(ProviderError, match='invalid_usage'):
        client.generate([{'role': 'user', 'content': 'hi'}], max_output_tokens=20, timeout_seconds=3)


@pytest.mark.parametrize('total', ['17', None, True, -1, 16, 'missing'])
def test_gemini_total_must_be_valid_before_reasoning_normalization(total, monkeypatch, mock_model_http):
    monkeypatch.setenv('GEMINI_API_KEY', 'fake')
    body = payload('gemini')
    if total == 'missing':
        del body['usageMetadata']['totalTokenCount']
    else:
        body['usageMetadata']['totalTokenCount'] = total
    mock_model_http(lambda request: httpx.Response(200, json=body))
    client = create_model_client('gemini', model='gemini-2.5-flash')
    with pytest.raises(ProviderError, match='invalid_usage'):
        client.generate([{'role': 'user', 'content': 'hi'}], max_output_tokens=20, timeout_seconds=3)


@pytest.mark.parametrize('candidate_tokens', [3, 5])
def test_gemini_accepts_exclusive_and_inclusive_reasoning_counts(candidate_tokens, monkeypatch, mock_model_http):
    monkeypatch.setenv('GEMINI_API_KEY', 'fake')
    body = payload('gemini')
    body['usageMetadata']['candidatesTokenCount'] = candidate_tokens
    mock_model_http(lambda request: httpx.Response(200, json=body))
    client = create_model_client('gemini', model='gemini-2.5-flash')
    result = client.generate([{'role': 'user', 'content': 'hi'}], max_output_tokens=20, timeout_seconds=3)
    assert (result.input_tokens, result.output_tokens, result.reasoning_tokens) == (12, 3, 2)


def test_empty_text_preserves_reported_consumption_for_the_loop(provider, mock_model_http):
    mock_model_http(lambda request: httpx.Response(200, json=payload(provider, text='')))
    client = create_model_client(provider, model='gemini-2.5-flash')
    result = client.generate([{'role': 'user', 'content': 'hi'}], max_output_tokens=20, timeout_seconds=3)
    assert result.text == ''
    assert result.input_tokens == 12
    assert result.output_tokens + result.reasoning_tokens == 5
