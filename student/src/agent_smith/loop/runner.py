"""MBPP Thought → Code → Observation loop over injected, trusted ports.

No generated action is evaluated here. Only a future reviewed sandbox executor
may implement the execution port; its observation and final answer are authoritative.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from agent_smith.contracts import StepMetrics
from agent_smith.loop.extract import extract_action


MAX_ITERATIONS = 10
MAX_INPUT_TOKENS = 6000
MAX_OUTPUT_TOKENS = 1500
TIME_LIMIT_SECONDS = 120


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str

    def as_dict(self) -> dict[str, Any]:
        return {'id': self.id, 'type': 'function',
                'function': {'name': self.name, 'arguments': self.arguments}}


@dataclass(frozen=True)
class ModelResponse:
    text: str
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int = 0  # Additional tokens, not already in output_tokens.
    tool_calls: tuple[ToolCall, ...] = ()


@dataclass(frozen=True)
class ExecutionResult:
    observation: str
    final_answer: str | None = None
    error: str | None = None


class ModelClient(Protocol):
    def generate(
        self, messages: list[dict[str, Any]], *, max_output_tokens: int,
        timeout_seconds: float, tools: list[dict[str, Any]] | None = None,
    ) -> ModelResponse: ...


class Executor(Protocol):
    def execute(self, code: str, *, timeout_seconds: float) -> ExecutionResult: ...


class UsageCounter:
    """Preflight prompt tokens; reconcile totals using provider-reported usage."""

    def __init__(self, count_input: Callable[[list[dict[str, str]]], int]):
        self.count_input = count_input
        self.input_tokens = 0
        self.output_tokens = 0

    def record(self, response: ModelResponse) -> bool:
        counts = (response.input_tokens, response.output_tokens, response.reasoning_tokens)
        if any(type(value) is not int or value < 0 for value in counts):
            return False
        self.input_tokens += response.input_tokens
        self.output_tokens += response.output_tokens + response.reasoning_tokens
        return True


class LoopStep(StepMetrics):
    error: str | None = None


@dataclass(frozen=True)
class LoopResult:
    success: bool
    final_answer: str | None
    error: str | None
    iterations: int
    total_requests: int
    total_input_tokens: int
    total_output_tokens: int
    total_time_seconds: float
    steps: list[LoopStep] = field(default_factory=list)


class LoopRunner:
    def __init__(
        self, *, model: ModelClient, executor: Executor, clock: Callable[[], float],
        usage: UsageCounter, cancelled: Callable[[], bool], max_iterations: int = MAX_ITERATIONS,
        api_url: str = '', model_name: str = '',
    ):
        if not 1 <= max_iterations <= MAX_ITERATIONS:
            raise ValueError('MBPP max_iterations must be between 1 and 10')
        self.model = model
        self.executor = executor
        self.clock = clock
        self.usage = usage
        self.cancelled = cancelled
        self.max_iterations = max_iterations
        self.api_url = api_url
        self.model_name = model_name

    def run(self, task: str, *, system_prompt: str = '') -> LoopResult:
        self.usage.input_tokens = 0
        self.usage.output_tokens = 0
        start = self.clock()
        messages = [{'role': 'system', 'content': system_prompt},
                    {'role': 'user', 'content': task}]
        steps: list[LoopStep] = []
        requests = 0
        iterations = 0

        def remaining() -> float:
            return TIME_LIMIT_SECONDS - (self.clock() - start)

        def finish(error: str | None = None, answer: str | None = None) -> LoopResult:
            return LoopResult(
                success=answer is not None and error is None, final_answer=answer,
                error=error, iterations=iterations, total_requests=requests,
                total_input_tokens=self.usage.input_tokens,
                total_output_tokens=self.usage.output_tokens,
                total_time_seconds=max(0.0, self.clock() - start), steps=steps,
            )

        for iteration in range(1, self.max_iterations + 1):
            if self.cancelled():
                return finish('cancelled')
            if remaining() <= 0:
                return finish('timeout')
            if self.usage.input_tokens >= MAX_INPUT_TOKENS:
                return finish('input_token_limit')
            if self.usage.output_tokens >= MAX_OUTPUT_TOKENS:
                return finish('output_token_limit')
            try:
                predicted = self.usage.count_input(messages)
            except Exception:
                return finish('invalid_usage')
            if type(predicted) is not int or predicted < 0:
                return finish('invalid_usage')
            if self.usage.input_tokens + predicted > MAX_INPUT_TOKENS:
                return finish('input_token_limit')
            step = LoopStep(
                step=iteration, input_tokens=0, output_tokens=0,
                request_time_ms=0, api_url=self.api_url,
                model_name=self.model_name, llm_output='',
                sandbox_input='', sandbox_output='', retries=0,
            )
            request_start = self.clock()
            if self.cancelled():
                return finish('cancelled')
            if remaining() <= 0:
                return finish('timeout')
            requests += 1
            iterations = iteration
            steps.append(step)
            try:
                reply = self.model.generate(
                    [dict(message) for message in messages],
                    max_output_tokens=MAX_OUTPUT_TOKENS - self.usage.output_tokens,
                    timeout_seconds=remaining(),
                )
            except Exception:
                step.error = 'model_error'
                return finish(step.error)
            finally:
                step.request_time_ms = max(0.0, self.clock() - request_start) * 1000
            try:
                valid_usage = self.usage.record(reply)
            except (AttributeError, TypeError):
                step.error = 'model_error'
                return finish(step.error)
            if not valid_usage:
                step.error = 'invalid_usage'
                return finish(step.error)
            step.input_tokens = reply.input_tokens
            step.output_tokens = reply.output_tokens + reply.reasoning_tokens
            if not isinstance(reply.text, str):
                step.error = 'model_error'
                return finish(step.error)
            step.llm_output = reply.text
            if self.usage.input_tokens > MAX_INPUT_TOKENS:
                step.error = 'input_token_limit'
                return finish(step.error)
            if self.usage.output_tokens > MAX_OUTPUT_TOKENS:
                step.error = 'output_token_limit'
                return finish(step.error)
            if self.cancelled():
                step.error = 'cancelled'
                return finish(step.error)
            if remaining() <= 0:
                step.error = 'timeout'
                return finish(step.error)

            action = extract_action(reply.text)
            if action.code is None:
                step.error = action.status
                observation = action.feedback or action.status
                step.sandbox_output = observation
            else:
                step.sandbox_input = action.code
                # Never pass the raw model reply back: it may contain a forged Observation.
                messages.append({'role': 'assistant', 'content': f'```python\n{action.code}```'})
                if self.cancelled():
                    step.error = 'cancelled'
                    return finish(step.error)
                if remaining() <= 0:
                    step.error = 'timeout'
                    return finish(step.error)
                try:
                    outcome = self.executor.execute(action.code, timeout_seconds=remaining())
                except Exception:
                    step.error = 'executor_error'
                    step.sandbox_output = 'Executor failed.'
                    return finish(step.error)
                step.sandbox_output = outcome.observation
                step.error = outcome.error
                observation = outcome.observation
                if self.cancelled():
                    step.error = 'cancelled'
                    return finish(step.error)
                if remaining() <= 0:
                    step.error = 'timeout'
                    return finish(step.error)
                if outcome.final_answer is not None and outcome.error is None:
                    return finish(answer=outcome.final_answer)
            messages.append({'role': 'user', 'content': f'Observation: {observation}'})
        return finish('iteration_limit')
