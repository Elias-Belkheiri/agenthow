"""Bounded model-driven chat; only trusted dispatchers execute tools."""

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from agent_smith.providers.contracts import ProviderError
from agent_smith.tools.dispatcher import ChatToolDispatcher, READ_ONLY_TOOL_GUIDANCE
from .runner import MAX_INPUT_TOKENS, MAX_ITERATIONS, MAX_OUTPUT_TOKENS, TIME_LIMIT_SECONDS, ModelClient


logger = logging.getLogger(__name__)
SYSTEM_TOOL_GUIDANCE = READ_ONLY_TOOL_GUIDANCE


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

    def run(self, task: str, *, system_prompt: str = '') -> ChatResult:
        return ChatSession(self.model, self.tools, system_prompt=system_prompt).send(task)

    def _run(self, messages: list[dict[str, str]], *, input_tokens: int = 0,
             output_tokens: int = 0, requests: int = 0) -> ChatResult:
        start = time.monotonic()
        iterations = 0

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
            estimated_input = 256 + 64 * len(messages) + sum(
                len(message['role'].encode('utf-8')) + len(message['content'].encode('utf-8'))
                for message in messages
            )
            if input_tokens + estimated_input > MAX_INPUT_TOKENS:
                return finish('input_token_limit')
            requests += 1
            iterations = turn
            began = time.monotonic()
            try:
                response = self.model.generate(
                    [dict(message) for message in messages],
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
            messages.append({'role': 'assistant', 'content': response.text})
            try:
                action: Any = json.loads(response.text)
            except (json.JSONDecodeError, TypeError):
                return finish('invalid_action')
            if not isinstance(action, dict):
                return finish('invalid_action')
            if set(action) == {'final'} and isinstance(action['final'], str) and action['final'].strip():
                return finish(answer=action['final'])
            if set(action) != {'tool', 'arguments'} or not isinstance(action['tool'], str) or not isinstance(action['arguments'], dict):
                return finish('invalid_action')
            remaining = TIME_LIMIT_SECONDS - (time.monotonic() - start)
            if remaining <= 0:
                return finish('timeout')
            outcome = self.tools.call(action['tool'], action['arguments'],
                                      timeout_seconds=min(300.0, remaining))
            # Only the dispatcher supplies this observation; model-authored claims are ignored.
            observation = json.dumps(outcome, ensure_ascii=False, sort_keys=True)
            messages.append({'role': 'user', 'content': f'Tool observation: {observation}'})
            logger.info('tool_observation_appended', extra={
                'tool_name': action['tool'] if outcome.get('ok') else 'rejected',
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
