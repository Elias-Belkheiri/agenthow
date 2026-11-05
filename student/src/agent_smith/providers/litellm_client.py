"""Single-attempt model access through the direct LiteLLM Python SDK.

LiteLLM owns provider wire formats; the application owns loops, tools and budgets.
"""

import logging
import os

import httpx

# Set before importing the SDK: model metadata must not require another network call.
os.environ['LITELLM_LOCAL_MODEL_COST_MAP'] = 'True'
# The application owns credential loading; SDK development mode auto-loads .env.
os.environ['LITELLM_MODE'] = 'PRODUCTION'

import litellm
from litellm.llms.custom_httpx.http_handler import HTTPHandler
from openai import OpenAI

from agent_smith.loop.runner import ModelResponse
from .contracts import ProviderError


DEFAULT_BASES = {
    'groq': 'https://api.groq.com/openai/v1',
    'gemini': 'https://generativelanguage.googleapis.com/v1beta',
}

# SDK diagnostics can include provider response bodies or prompt text. Application
# logging supplies bounded metadata instead. Configure once, not around each call.
litellm.set_verbose = False
litellm.suppress_debug_info = True
litellm.turn_off_message_logging = True
litellm.redact_messages_in_exceptions = True
litellm.redact_user_api_key_info = True
for name in ('LiteLLM', 'LiteLLM Router', 'LiteLLM Proxy'):
    logging.getLogger(name).disabled = True
logging.getLogger('openai').setLevel(logging.WARNING)
logging.getLogger('openai._base_client').disabled = True


def _error_code(exc: Exception) -> str:
    """Inspect exception types, not credential-bearing messages.

    LiteLLM sometimes wraps transport or parsing errors in synthetic HTTP 500/422
    exceptions. Inspect their causes before classifying the outer status code.
    """
    causes = []
    current = exc
    while current is not None and all(current is not cause for cause in causes):
        causes.append(current)
        current = current.__cause__ or current.__context__
    for cause in causes:
        if isinstance(cause, ProviderError):
            return cause.code
    if any(isinstance(cause, (litellm.Timeout, httpx.TimeoutException)) for cause in causes):
        return 'timeout'
    if any(isinstance(cause, (litellm.APIConnectionError, httpx.RequestError)) for cause in causes):
        return 'network_error'
    if isinstance(exc, litellm.UnsupportedParamsError):
        return 'invalid_configuration'
    for cause in causes:
        if isinstance(cause, httpx.HTTPStatusError):
            return f'http_{cause.response.status_code}'
    for cause in causes:
        if isinstance(cause, (ValueError, TypeError, KeyError, IndexError, AttributeError,
                              litellm.APIResponseValidationError)):
            return 'invalid_response'
        if isinstance(cause, litellm.APIError) and getattr(cause, 'response', None) is None:
            return 'invalid_response'
    status = getattr(exc, 'status_code', None)
    return f'http_{status}' if type(status) is int and 400 <= status <= 599 else 'invalid_response'


class LiteLLMClient:
    def __init__(self, provider: str, *, model: str, api_url: str | None = None,
                 json_mode: bool = False):
        if provider not in DEFAULT_BASES:
            raise ValueError(f'unsupported provider: {provider}')
        self.provider_name = provider
        self.model = model
        self.json_mode = json_mode
        self._api_key = os.getenv(f'{provider.upper()}_API_KEY', '')
        self.api_url = (api_url or DEFAULT_BASES[provider]).rstrip('/')
        suffix = '/chat/completions' if provider == 'groq' else '/models'
        self._api_base = self.api_url.removesuffix(suffix)

    def _validate_http_response(self, response: httpx.Response) -> None:
        """Enforce request policy before SDK repair or lossy usage coercion.

        This hook validates usage only; SDKs still build requests and normalize
        model content. Raising a code-only error also prevents message-repair
        retries based on the provider's raw error text.
        """
        response.read()
        if not response.is_success:
            raise ProviderError(f'http_{response.status_code}')
        try:
            body = response.json()
            if not isinstance(body, dict):
                raise ProviderError('invalid_response')
            if self.provider_name == 'groq':
                usage = body.get('usage')
                required = ('prompt_tokens', 'completion_tokens')
                details = (usage or {}).get('completion_tokens_details') if isinstance(usage, dict) else None
                reasoning = (details or {}).get('reasoning_tokens', 0) if isinstance(details, dict) or details is None else None
            else:
                usage = body.get('usageMetadata')
                required = ('promptTokenCount', 'candidatesTokenCount', 'totalTokenCount')
                reasoning = usage.get('thoughtsTokenCount', 0) if isinstance(usage, dict) else None
            if not isinstance(usage, dict):
                raise ProviderError('invalid_usage')
            counts = [usage.get(name) for name in required] + [reasoning]
            if any(type(value) is not int or value < 0 for value in counts):
                raise ProviderError('invalid_usage')
            if self.provider_name == 'gemini':
                tool_tokens = usage.get('toolUsePromptTokenCount', 0)
                if type(tool_tokens) is not int or tool_tokens < 0:
                    raise ProviderError('invalid_usage')
                base_total = usage['promptTokenCount'] + usage['candidatesTokenCount'] + tool_tokens
                if usage['totalTokenCount'] not in (base_total, base_total + reasoning):
                    raise ProviderError('invalid_usage')
        except (ValueError, TypeError, AttributeError):
            raise ProviderError('invalid_response') from None

    def generate(self, messages: list[dict[str, str]], *, max_output_tokens: int,
                 timeout_seconds: float) -> ModelResponse:
        if not self._api_key:
            raise ProviderError('missing_credentials')
        if not self.model:
            raise ProviderError('missing_model')
        if timeout_seconds <= 0:
            raise ProviderError('timeout')
        if type(max_output_tokens) is not int or max_output_tokens <= 0:
            raise ProviderError('invalid_configuration')

        attempted = False

        def single_attempt(request: httpx.Request) -> None:
            nonlocal attempted
            if attempted:
                raise ProviderError('request_limit')
            attempted = True

        try:
            with httpx.Client(timeout=timeout_seconds, follow_redirects=False, event_hooks={
                'request': [single_attempt], 'response': [self._validate_http_response],
            }) as http_client:
                # Groq's API is OpenAI-compatible. Its native SDK route assumes
                # an optional service_tier field, so use the compatible route.
                sdk_provider = 'openai' if self.provider_name == 'groq' else 'gemini'
                client = (OpenAI(api_key=self._api_key, base_url=self._api_base,
                                 http_client=http_client, max_retries=0)
                          if self.provider_name == 'groq' else HTTPHandler(client=http_client))
                response = litellm.completion(
                    # Routing gets its own prefix; preserve model namespaces
                    # such as Groq's literal openai/gpt-oss-120b identifier.
                    model=f'{sdk_provider}/{self.model}',
                    custom_llm_provider=sdk_provider,
                    api_key=self._api_key,
                    api_base=self._api_base,
                    client=client,
                    messages=[dict(message) for message in messages],
                    max_tokens=max_output_tokens,
                    timeout=timeout_seconds,
                    num_retries=0,
                    max_retries=0,
                    stream=False,
                    caching=False,
                    drop_params=False,
                    **({'response_format': {'type': 'json_object'}} if self.json_mode else {}),
                )
        except Exception as exc:
            # Never attach the SDK exception: its text may contain credentials.
            raise ProviderError(_error_code(exc)) from None

        try:
            text = response.choices[0].message.content
            # Gemini normalizes empty text to None. Let the loop reject the
            # empty action after recording its reported token consumption.
            if text is None:
                text = ''
            if not isinstance(text, str):
                raise ProviderError('invalid_response')
            usage = response.usage
            input_tokens = usage.prompt_tokens
            completion_tokens = usage.completion_tokens
            details = getattr(usage, 'completion_tokens_details', None)
            reasoning_tokens = getattr(details, 'reasoning_tokens', None)
            if reasoning_tokens is None:
                reasoning_tokens = 0
            counts = (input_tokens, completion_tokens, reasoning_tokens)
            if any(type(value) is not int or value < 0 for value in counts):
                raise ProviderError('invalid_usage')
            # The SDK fills absent usage with zeros. A nonempty text completion
            # must consume prompt and completion tokens; never accept that default.
            if input_tokens == 0 or completion_tokens == 0:
                raise ProviderError('invalid_usage')
            if reasoning_tokens > completion_tokens:
                raise ProviderError('invalid_usage')
            # LiteLLM includes reasoning in completion_tokens for both providers.
            return ModelResponse(text, input_tokens, completion_tokens - reasoning_tokens,
                                 reasoning_tokens)
        except ProviderError:
            raise
        except (AttributeError, TypeError, KeyError, IndexError):
            raise ProviderError('invalid_response') from None
