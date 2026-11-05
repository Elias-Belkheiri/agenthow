"""Bounded model-driven chat; only trusted dispatchers execute tools."""

from copy import deepcopy
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

from agent_smith.providers.contracts import ProviderError
from agent_smith.sandbox.tool_worker import MAX_REQUEST_BYTES, validate_arguments
from agent_smith.tools.dispatcher import ChatToolDispatcher, READ_ONLY_TOOL_GUIDANCE
from .runner import (
    MAX_INPUT_TOKENS, MAX_ITERATIONS, MAX_OUTPUT_TOKENS, TIME_LIMIT_SECONDS,
    ModelClient, ToolCall,
)


logger = logging.getLogger(__name__)
SYSTEM_TOOL_GUIDANCE = READ_ONLY_TOOL_GUIDANCE
MAX_TOOL_CALLS_PER_TURN = 16


def _unique_object(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate argument key')
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError('non-JSON constant')


@dataclass(frozen=True)
class ChatResult:
    success: bool
    final_answer: str | None
    error: str | None
    iterations: int
    total_requests: int
    total_input_tokens: int
    total_output_tokens: int
    total_time_seconds: float


class ChatRunner:
    """Run bounded multi-turn chat with a capability-specific dispatcher."""

    def __init__(self, model: ModelClient, tools: ChatToolDispatcher):
        self.model = model
        self.tools = tools
        self.definitions = deepcopy(tools.tool_definitions)
        self.allowed_names = {t['function']['name'] for t in self.definitions
                              if 'function' in t}

    def run(self, task: str, *, system_prompt: str = '') -> ChatResult:
        return ChatSession(self.model, self.tools, system_prompt=system_prompt).send(task)

    def _run(self, messages: list[dict[str, Any]], *, input_tokens: int = 0,
             output_tokens: int = 0, requests: int = 0) -> ChatResult:
        start = time.monotonic()
        iterations = 0
        seen_ids = {call['id'] for message in messages
                    for call in message.get('tool_calls', [])}

        def finish(error: str | None = None, answer: str | None = None) -> ChatResult:
            return ChatResult(answer is not None and error is None, answer, error,
                              iterations, requests, input_tokens, output_tokens,
                              max(0.0, time.monotonic() - start))

        for turn in range(1, MAX_ITERATIONS + 1):
            remaining = TIME_LIMIT_SECONDS - (time.monotonic() - start)
            if remaining <= 0:
                return finish('timeout')
            if input_tokens >= MAX_INPUT_TOKENS or output_tokens >= MAX_OUTPUT_TOKENS:
                return finish('token_limit')
            # Conservative byte-based preflight bounds the serialized history before
            # sending it. Provider-reported usage remains authoritative afterward.
            estimated_input = 256 + 64 * len(messages) + len(json.dumps(
                {'messages': messages, 'tools': self.definitions},
                ensure_ascii=True, separators=(',', ':'),
            ).encode('utf-8'))
            if input_tokens + estimated_input > MAX_INPUT_TOKENS:
                return finish('input_token_limit')
            requests += 1
            iterations = turn
            began = time.monotonic()
            try:
                response = self.model.generate(
                    deepcopy(messages), tools=deepcopy(self.definitions),
                    max_output_tokens=MAX_OUTPUT_TOKENS - output_tokens,
                    timeout_seconds=remaining,
                )
            except ProviderError as exc:
                logger.warning('model_turn_failed', extra={'provider_error': exc.code, 'turn': turn})
                return finish(exc.code)
            except Exception as exc:
                logger.error('model_turn_failed', extra={
                    'provider_error': 'model_error', 'turn': turn,
                    'error_type': type(exc).__name__,
                })
                return finish('model_error')
            elapsed_ms = max(0.0, time.monotonic() - began) * 1000
            counts = (response.input_tokens, response.output_tokens, response.reasoning_tokens)
            if any(type(value) is not int or value < 0 for value in counts):
                return finish('invalid_usage')
            input_tokens += response.input_tokens
            output_tokens += response.output_tokens + response.reasoning_tokens
            logger.info('model_turn_completed', extra={
                'provider': getattr(self.model, 'provider_name', 'configured'),
                'model': getattr(self.model, 'model', ''),
                'turn': turn, 'request': requests, 'elapsed_ms': elapsed_ms,
                'input_tokens': response.input_tokens,
                'output_tokens': response.output_tokens,
                'reasoning_tokens': response.reasoning_tokens,
            })
            if input_tokens > MAX_INPUT_TOKENS or output_tokens > MAX_OUTPUT_TOKENS:
                return finish('token_limit')
            if time.monotonic() - start >= TIME_LIMIT_SECONDS:
                return finish('timeout')
            if not isinstance(response.text, str):
                return finish('invalid_response')
            calls = getattr(response, 'tool_calls', ())
            if not isinstance(calls, (tuple, list)) or len(calls) > MAX_TOOL_CALLS_PER_TURN:
                return finish('invalid_action')
            # Validate the entire batch's metadata before any possible mutation.
            batch_ids = set()
            for call in calls:
                if (not isinstance(call, ToolCall)
                        or not isinstance(call.id, str)
                        # IDs are opaque: Gemini's SDK may embed a base64
                        # thought signature. Preserve it for the next request.
                        or not 1 <= len(call.id) <= 65536
                        or any(not 33 <= ord(c) <= 126 for c in call.id)
                        or not isinstance(call.name, str)
                        or re.fullmatch(r'[A-Za-z0-9_-]{1,64}', call.name) is None
                        or not isinstance(call.arguments, str)
                        or call.id in seen_ids or call.id in batch_ids):
                    return finish('invalid_action')
                batch_ids.add(call.id)
            if not calls:
                if not response.text.strip():
                    return finish('invalid_action')
                messages.append({'role': 'assistant', 'content': response.text})
                return finish(answer=response.text)
            seen_ids.update(batch_ids)
            messages.append({'role': 'assistant', 'content': response.text or None,
                             'tool_calls': [call.as_dict() for call in calls]})
            for call in calls:
                remaining = TIME_LIMIT_SECONDS - (time.monotonic() - start)
                if remaining <= 0:
                    return finish('timeout')
                error = None
                arguments = None
                if call.name not in self.allowed_names:
                    error = 'tool_not_allowed'
                else:
                    try:
                        if len(call.arguments.encode('utf-8')) > MAX_REQUEST_BYTES:
                            raise ValueError('request limit')
                        arguments = json.loads(call.arguments, object_pairs_hook=_unique_object,
                                               parse_constant=_reject_constant)
                        error = validate_arguments(call.name, arguments)
                    except (ValueError, TypeError, UnicodeError, RecursionError):
                        error = 'invalid_arguments'
                # Validation/parsing time also consumes the turn deadline.
                remaining = TIME_LIMIT_SECONDS - (time.monotonic() - start)
                if remaining <= 0:
                    return finish('timeout')
                outcome = ({'ok': False, 'error': error} if error else
                           self.tools.call(call.name, arguments,
                                           timeout_seconds=min(300.0, remaining)))
                # Only trusted dispatch/validation supplies observations.
                observation = json.dumps(outcome, ensure_ascii=True, sort_keys=True)
                messages.append({'role': 'tool', 'tool_call_id': call.id,
                                 'name': call.name, 'content': observation})
                logger.info('tool_observation_appended', extra={
                    'tool_name': call.name if call.name in self.allowed_names else 'rejected',
                    'ok': outcome.get('ok', False),
                    'error_code': outcome.get('error'),
                    'result_chars': len(outcome.get('content', '')),
                })
                if time.monotonic() - start >= TIME_LIMIT_SECONDS:
                    return finish('timeout')
        return finish('iteration_limit')


class ChatSession:
    """In-memory conversation with session-wide usage and explicit reset.

    Failed turns are removed from history, but already consumed usage is kept.
    Filesystem effects are not rolled back and mutating calls are never replayed.
    """

    def __init__(self, model: ModelClient, tools: ChatToolDispatcher, *, system_prompt: str = ''):
        self._runner = ChatRunner(model, tools)
        self._instructions = '\n'.join(part for part in (system_prompt, tools.system_guidance) if part)
        self.reset()

    def reset(self) -> None:
        self._messages = [{'role': 'system', 'content': self._instructions}]
        self._input_tokens = self._output_tokens = self._requests = 0

    def send(self, task: str) -> ChatResult:
        checkpoint = len(self._messages)
        self._messages.append({'role': 'user', 'content': task})
        try:
            result = self._runner._run(
                self._messages, input_tokens=self._input_tokens,
                output_tokens=self._output_tokens, requests=self._requests,
            )
        except BaseException:
            del self._messages[checkpoint:]
            raise
        self._input_tokens = result.total_input_tokens
        self._output_tokens = result.total_output_tokens
        self._requests = result.total_requests
        if not result.success:
            del self._messages[checkpoint:]
        return result


# Public legacy names remain compatible with explicit read-only dispatchers.
ReadOnlyChatRunner = ChatRunner
ReadOnlyChatSession = ChatSession
