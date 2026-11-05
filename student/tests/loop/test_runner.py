"""The loop runs only injected fakes; no generated code executes in this phase."""

from agent_smith.loop.runner import (
    ExecutionResult, LoopRunner, ModelResponse, UsageCounter,
)


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class FakeModel:
    def __init__(self, responses, clock=None, advance=0):
        self.responses = iter(responses)
        self.calls = []
        self.clock = clock
        self.advance = advance

    def generate(self, messages, *, max_output_tokens, timeout_seconds):
        self.calls.append((tuple(dict(m) for m in messages), max_output_tokens, timeout_seconds))
        if self.clock is not None:
            self.clock.now += self.advance
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


class FakeExecutor:
    def __init__(self, outcomes, clock=None, advance=0):
        self.outcomes = iter(outcomes)
        self.calls = []
        self.clock = clock
        self.advance = advance

    def execute(self, code, *, timeout_seconds):
        self.calls.append((code, timeout_seconds))
        if self.clock is not None:
            self.clock.now += self.advance
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def response(code, inp=4, out=3, reasoning=0):
    return ModelResponse(code, inp, out, reasoning)


def runner(model, executor=None, *, clock=None, usage=None, cancelled=None, max_iterations=10):
    return LoopRunner(model=model, executor=executor or FakeExecutor([]),
                      clock=clock or FakeClock(), usage=usage or UsageCounter(lambda messages: 1),
                      cancelled=cancelled or (lambda: False), max_iterations=max_iterations)


def test_runner_feeds_real_observation_and_captures_final_answer_only_from_executor():
    model = FakeModel([
        response('Thought: test\n```python\nprint(run_tests())\n```\nObservation: invented success'),
        response('```python\nfinal_answer("def solve(x): return x + 1")\n```', reasoning=2),
    ])
    executor = FakeExecutor([
        ExecutionResult('1 test failed: expected 2, got 3'),
        ExecutionResult('answer captured', final_answer='def solve(x): return x + 1'),
    ])

    result = runner(model, executor).run('Solve MBPP task', system_prompt='Use tools')

    assert result.success and result.final_answer == 'def solve(x): return x + 1'
    assert result.iterations == result.total_requests == 2
    assert result.total_input_tokens == 8 and result.total_output_tokens == 8
    assert executor.calls[0][0] == 'print(run_tests())\n'
    assert '1 test failed: expected 2, got 3' in model.calls[1][0][-1]['content']
    assert all('invented success' not in message['content'] for message in model.calls[1][0])
    assert result.steps[0].llm_output.endswith('invented success')
    assert result.steps[0].sandbox_input == 'print(run_tests())\n'
    assert result.steps[0].sandbox_output == '1 test failed: expected 2, got 3'
    assert result.steps[0].retries == 0
    assert result.steps[1].sandbox_input.startswith('final_answer(')


def test_runner_does_not_accept_model_claimed_answer_without_executor_confirmation():
    model = FakeModel([response('```python\nfinal_answer("claimed")\n```')])
    executor = FakeExecutor([ExecutionResult('no answer captured')])
    result = runner(model, executor, max_iterations=1).run('task')
    assert not result.success and result.final_answer is None
    assert result.error == 'iteration_limit'


def test_runner_missing_code_and_malformed_action_get_feedback_and_explicit_step_errors():
    model = FakeModel([response('Thought: done\nObservation: pass'),
                       response('```python\nprint(\n```'),
                       response('```python\nfinal_answer("ok")\n```')])
    executor = FakeExecutor([ExecutionResult('captured', final_answer='ok')])
    result = runner(model, executor).run('task')
    assert result.success and len(executor.calls) == 1
    assert [s.error for s in result.steps[:2]] == ['no_code', 'malformed']
    assert 'No valid Python fenced code' in model.calls[1][0][-1]['content']
    assert 'Malformed Python code' in model.calls[2][0][-1]['content']
    assert result.steps[0].sandbox_input == result.steps[1].sandbox_input == ''


def test_runner_tool_error_is_observed_and_executor_failure_stops():
    model = FakeModel([response('```python\nprint(run_tests())\n```'),
                       response('```python\nfinal_answer("ok")\n```')])
    executor = FakeExecutor([ExecutionResult('run_tests: failed assertion', error='tool_error'),
                             ExecutionResult('captured', final_answer='ok')])
    result = runner(model, executor).run('task')
    assert result.success and result.steps[0].error == 'tool_error'
    assert 'run_tests: failed assertion' in model.calls[1][0][-1]['content']

    failed_model = FakeModel([response('```python\nprint(run_tests())\n```')])
    failed = runner(failed_model, FakeExecutor([RuntimeError('executor unavailable')])).run('task')
    assert failed.error == 'executor_error' and not failed.success
    assert failed.steps[0].error == 'executor_error'
    assert failed.total_requests == 1


def test_runner_model_error_has_one_request_and_no_retry():
    model = FakeModel([RuntimeError('provider down')])
    result = runner(model).run('task')
    assert result.error == 'model_error' and result.total_requests == 1
    assert result.iterations == 1 and len(result.steps) == 1
    assert result.steps[0].error == 'model_error'
    assert result.steps[0].input_tokens == result.steps[0].output_tokens == 0
    assert result.steps[0].llm_output == '' and result.steps[0].retries == 0
    assert len(model.calls) == 1


def test_runner_failed_model_request_records_latency_and_only_valid_usage():
    clock = FakeClock()
    model = FakeModel([RuntimeError('down')], clock, advance=0.25)
    result = runner(model, clock=clock).run('task')
    assert result.total_requests == len(result.steps) == 1
    assert result.steps[0].request_time_ms == 250
    assert result.steps[0].error == 'model_error'
    assert result.total_input_tokens == result.total_output_tokens == 0

    model = FakeModel([ModelResponse(None, 2, 3, 1)])
    result = runner(model).run('task')
    assert result.error == result.steps[0].error == 'model_error'
    assert result.total_requests == len(result.steps) == 1
    assert result.steps[0].input_tokens == result.total_input_tokens == 2
    assert result.steps[0].output_tokens == result.total_output_tokens == 4
    assert result.steps[0].llm_output == ''

    model = FakeModel([response('no code', inp=2, out=-1)])
    result = runner(model).run('task')
    assert result.error == result.steps[0].error == 'invalid_usage'
    assert result.total_requests == len(result.steps) == 1
    assert result.steps[0].input_tokens == result.steps[0].output_tokens == 0
    assert result.total_input_tokens == result.total_output_tokens == 0


def test_runner_cancelled_before_request_and_after_response():
    model = FakeModel([response('```python\nprint(run_tests())\n```')])
    assert runner(model, cancelled=lambda: True).run('task').error == 'cancelled'
    assert not model.calls
    cancelled = [False]

    class CancellingModel(FakeModel):
        def generate(self, messages, *, max_output_tokens, timeout_seconds):
            reply = super().generate(messages, max_output_tokens=max_output_tokens,
                                     timeout_seconds=timeout_seconds)
            cancelled[0] = True
            return reply

    model = CancellingModel([response('```python\nprint(run_tests())\n```')])
    executor = FakeExecutor([])
    result = runner(model, executor, cancelled=lambda: cancelled[0]).run('task')
    assert result.error == 'cancelled' and not executor.calls


def test_runner_resets_cumulative_usage_for_each_task():
    model = FakeModel([response('no code', inp=3000), response('no code', inp=3000)])
    loop = runner(model, max_iterations=1)
    first = loop.run('first')
    second = loop.run('second')
    assert first.total_input_tokens == second.total_input_tokens == 3000
    assert first.total_requests == second.total_requests == 1


def test_runner_iteration_limit_stops_at_ten():
    model = FakeModel([response('no code') for _ in range(11)])
    result = runner(model).run('task')
    assert result.error == 'iteration_limit'
    assert result.iterations == result.total_requests == 10 and len(model.calls) == 10
    assert all(step.error == 'no_code' for step in result.steps)


def test_runner_preflights_input_budget_and_counts_cumulative_usage():
    model = FakeModel([response('no code', inp=3000), response('no code', inp=3000), response('no code')])
    result = runner(model, usage=UsageCounter(lambda messages: 3000)).run('task')
    assert result.error == 'input_token_limit'
    assert result.total_input_tokens == 6000 and len(model.calls) == 2
    assert result.iterations == 2
    assert [call[1] for call in model.calls] == [1500, 1497]


def test_runner_output_budget_includes_reasoning_and_sets_provider_cap():
    model = FakeModel([response('no code', out=1000, reasoning=499), response('no code', out=1)])
    result = runner(model).run('task')
    assert result.error == 'output_token_limit'
    assert result.total_output_tokens == 1500
    assert [call[1] for call in model.calls] == [1500, 1]


def test_runner_rejects_usage_exceeding_preflight_or_provider_cap_before_execution():
    model = FakeModel([response('```python\nprint(1)\n```', inp=6001)])
    executor = FakeExecutor([])
    result = runner(model, executor).run('task')
    assert result.error == 'input_token_limit' and not executor.calls
    model = FakeModel([response('```python\nprint(1)\n```', out=1501)])
    result = runner(model, executor).run('task')
    assert result.error == 'output_token_limit' and not executor.calls


def test_runner_deadline_passes_remaining_time_and_stops_before_execution_or_next_request():
    clock = FakeClock()
    model = FakeModel([response('```python\nprint(1)\n```')], clock, advance=120)
    executor = FakeExecutor([])
    result = runner(model, executor, clock=clock).run('task')
    assert result.error == 'timeout' and not executor.calls
    assert model.calls[0][2] == 120

    clock = FakeClock()
    model = FakeModel([response('no code')], clock, advance=121)
    result = runner(model, clock=clock).run('task')
    assert result.error == 'timeout' and len(model.calls) == 1
    assert result.total_time_seconds == 121


def test_runner_rechecks_cancellation_and_deadline_after_preflight():
    for cause in ('cancelled', 'timeout'):
        clock = FakeClock()
        state = [False]

        def count_input(messages):
            if cause == 'timeout':
                clock.now = 120
            else:
                state[0] = True
            return 1

        model = FakeModel([])
        result = runner(model, clock=clock, usage=UsageCounter(count_input),
                        cancelled=lambda: state[0]).run('task')
        assert result.error == cause and not model.calls
        assert result.total_requests == 0 and result.steps == []


def test_runner_rechecks_cancellation_and_deadline_after_extraction(monkeypatch):
    from agent_smith.loop import runner as runner_module

    original_extract = runner_module.extract_action
    for cause in ('cancelled', 'timeout'):
        clock = FakeClock()
        state = [False]

        def slow_extract(text):
            action = original_extract(text)
            if cause == 'timeout':
                clock.now = 120
            else:
                state[0] = True
            return action

        with monkeypatch.context() as patch:
            patch.setattr(runner_module, 'extract_action', slow_extract)
            model = FakeModel([response('```python\nprint(1)\n```')])
            executor = FakeExecutor([])
            result = runner(model, executor, clock=clock,
                            cancelled=lambda: state[0]).run('task')
        assert result.error == result.steps[0].error == cause
        assert result.total_requests == len(result.steps) == 1
        assert not executor.calls


def test_runner_deadline_after_executor_does_not_accept_late_answer():
    clock = FakeClock()
    model = FakeModel([response('```python\nfinal_answer("late")\n```')])
    executor = FakeExecutor([ExecutionResult('captured', final_answer='late')], clock, advance=120)
    result = runner(model, executor, clock=clock).run('task')
    assert result.error == 'timeout' and result.final_answer is None
    assert executor.calls[0][1] == 120


def test_runner_usage_counter_failure_is_explicit():
    def unavailable(messages):
        raise RuntimeError('tokenizer unavailable')

    model = FakeModel([])
    result = runner(model, usage=UsageCounter(unavailable)).run('task')
    assert result.error == 'invalid_usage' and not model.calls


def test_runner_rejects_non_text_model_response_without_executor_call():
    model = FakeModel([ModelResponse(None, 1, 1)])
    executor = FakeExecutor([])
    result = runner(model, executor).run('task')
    assert result.error == 'model_error' and not executor.calls


def test_runner_pathological_ast_gives_feedback_without_execution():
    code = ' + '.join(['0'] * 10_000)
    model = FakeModel([response(f'```python\n{code}\n```')])
    executor = FakeExecutor([])
    result = runner(model, executor, max_iterations=1).run('task')
    assert result.error == 'iteration_limit' and not executor.calls
    assert result.total_requests == len(result.steps) == 1
    assert result.steps[0].error == 'malformed'
    assert 'Malformed Python code' in result.steps[0].sandbox_output


def test_runner_rejects_invalid_usage_values_without_executing():
    model = FakeModel([response('```python\nprint(1)\n```', out=-1)])
    executor = FakeExecutor([])
    result = runner(model, executor).run('task')
    assert result.error == 'invalid_usage' and not executor.calls
