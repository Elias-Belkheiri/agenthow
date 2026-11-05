"""External evaluator contracts from agentsmith.pdf pages 20–25."""

import json

import pytest
from pydantic import ValidationError

from agent_smith.contracts import (
    MBPPTaskInput,
    SWEBenchSolutionOutput,
    SWEBenchStepMetrics,
    SWEBenchTaskInput,
    SolutionOutput,
    StepMetrics,
)


MBPP_TASK = {
    "task_id": 12,
    "task_definition": "Return the square of x.",
    "function_definition": "def square(x):",
    "test_imports": ["import math"],
    "test_list": ["assert square(2) == 4"],
}
SWE_TASK = {
    "instance_id": "sympy__sympy-23534",
    "problem_statement": "Fix symbolic calculation",
    "docker_image": "swebench/sweb.eval.x86_64.sympy_1776_sympy-23534:latest",
    "eval_script": "pytest tests/test_symbolic.py",
}
STEP = {
    "step": 1,
    "input_tokens": 10,
    "output_tokens": 4,
    "request_time_ms": 52.5,
    "api_url": "https://example.invalid/v1",
    "model_name": "test-model",
    "llm_output": "raw model response",
    "sandbox_input": "print(1)",
    "sandbox_output": "1\n",
    "retries": 0,
}
SOLUTION = {
    "task_id": "12",
    "benchmark": "mbpp",
    "success": True,
    "solution": "def square(x): return x*x",
    "system_prompt": "Full system instructions",
    "iterations": 1,
    "total_requests": 1,
    "total_input_tokens": 10,
    "total_output_tokens": 4,
    "total_time_seconds": 0.5,
    "steps": [STEP],
}


def test_mbpp_task_input_validates_id_and_defaults():
    task = MBPPTaskInput.model_validate({key: MBPP_TASK[key] for key in MBPP_TASK if key not in ("test_imports", "test_list")})
    assert task.task_id == 12
    assert task.test_imports == []
    assert task.test_list == []
    assert MBPPTaskInput.model_validate(MBPP_TASK).test_list == ["assert square(2) == 4"]
    assert MBPPTaskInput.model_validate_json(task.model_dump_json()) == task
    task.test_list.append("local")
    assert MBPPTaskInput.model_validate({key: MBPP_TASK[key] for key in MBPP_TASK if key not in ("test_imports", "test_list")}).test_list == []


@pytest.mark.parametrize("bad_id", [0, -3, "bad", True, 2.5])
def test_mbpp_task_input_rejects_invalid_id(bad_id):
    with pytest.raises(ValidationError):
        MBPPTaskInput.model_validate({**MBPP_TASK, "task_id": bad_id})


@pytest.mark.parametrize("missing", ["task_definition", "function_definition"])
def test_mbpp_task_input_rejects_missing_definition(missing):
    with pytest.raises(ValidationError):
        MBPPTaskInput.model_validate({key: value for key, value in MBPP_TASK.items() if key != missing})


def test_swebench_task_input_requires_shape_and_defaults_hints_and_repo():
    task = SWEBenchTaskInput.model_validate(SWE_TASK)
    assert task.hints_text == ""
    assert task.repo == ""
    assert SWEBenchTaskInput.model_validate({**SWE_TASK, "hints_text": "hint", "repo": "sympy/sympy"}).repo == "sympy/sympy"
    assert SWEBenchTaskInput.model_validate_json(task.model_dump_json()) == task


@pytest.mark.parametrize("missing", ["instance_id", "problem_statement", "docker_image", "eval_script"])
def test_swebench_task_input_rejects_missing_required_field(missing):
    with pytest.raises(ValidationError):
        SWEBenchTaskInput.model_validate({key: value for key, value in SWE_TASK.items() if key != missing})


@pytest.mark.parametrize("field", ["instance_id", "problem_statement", "docker_image", "eval_script"])
def test_swebench_task_input_rejects_empty_required_field(field):
    with pytest.raises(ValidationError):
        SWEBenchTaskInput.model_validate({**SWE_TASK, field: ""})


def test_step_metrics_includes_raw_output_sandbox_retries_and_timestamp():
    step = StepMetrics.model_validate(STEP)
    assert step.step == 1
    assert step.llm_output == "raw model response"
    assert step.sandbox_input == "print(1)"
    assert step.sandbox_output == "1\n"
    assert step.retries == 0
    assert step.timestamp
    assert StepMetrics.model_validate_json(step.model_dump_json()) == step


@pytest.mark.parametrize("missing", ["step", "input_tokens", "output_tokens", "request_time_ms", "api_url", "model_name", "llm_output", "sandbox_input", "sandbox_output", "retries"])
def test_mbpp_step_metrics_requires_pdf_fields(missing):
    with pytest.raises(ValidationError):
        StepMetrics.model_validate({key: value for key, value in STEP.items() if key != missing})


@pytest.mark.parametrize("field,value", [("step", 0), ("input_tokens", -1), ("output_tokens", -1), ("request_time_ms", -1), ("retries", -1)])
def test_step_metrics_rejects_invalid_counts(field, value):
    with pytest.raises(ValidationError):
        StepMetrics.model_validate({**STEP, field: value})


def test_swebench_step_metrics_serializes_pdf_defaults():
    step = SWEBenchStepMetrics.model_validate({key: STEP[key] for key in ("step", "input_tokens", "output_tokens", "request_time_ms")})
    data = step.model_dump()
    for key in ("api_url", "model_name", "llm_output", "sandbox_input", "sandbox_output"):
        assert data[key] == ""
    assert data["retries"] == 0
    assert data["timestamp"]


def test_mbpp_solution_output_round_trips_json_with_all_pdf_fields():
    output = SolutionOutput.model_validate(SOLUTION)
    encoded = output.model_dump_json()
    decoded = json.loads(encoded)
    assert decoded["task_id"] == "12"
    assert decoded["benchmark"] == "mbpp"
    assert decoded["system_prompt"] == "Full system instructions"
    assert decoded["total_requests"] == 1
    assert decoded["total_input_tokens"] == 10
    assert decoded["total_output_tokens"] == 4
    assert decoded["total_time_seconds"] == 0.5
    assert decoded["steps"][0]["llm_output"] == STEP["llm_output"]
    assert decoded["steps"][0]["sandbox_input"] == STEP["sandbox_input"]
    assert decoded["steps"][0]["sandbox_output"] == STEP["sandbox_output"]
    assert decoded["steps"][0]["retries"] == 0
    assert decoded["steps"][0]["timestamp"]
    assert decoded["timestamp"]
    assert decoded["error"] is None
    assert SolutionOutput.model_validate_json(encoded) == output


@pytest.mark.parametrize("missing", ["task_id", "benchmark", "success", "solution", "system_prompt", "iterations", "total_requests", "total_input_tokens", "total_output_tokens", "total_time_seconds"])
def test_mbpp_solution_output_requires_pdf_fields(missing):
    with pytest.raises(ValidationError):
        SolutionOutput.model_validate({key: value for key, value in SOLUTION.items() if key != missing})


@pytest.mark.parametrize("field", ["iterations", "total_requests", "total_input_tokens", "total_output_tokens", "total_time_seconds"])
def test_solution_output_rejects_negative_totals(field):
    with pytest.raises(ValidationError):
        SolutionOutput.model_validate({**SOLUTION, field: -1})


def test_swebench_solution_output_round_trips_with_defaults_and_patch():
    payload = {**SOLUTION, "task_id": SWE_TASK["instance_id"], "benchmark": "swebench", "solution": "diff --git a/f.py b/f.py\n", "steps": [{key: STEP[key] for key in ("step", "input_tokens", "output_tokens", "request_time_ms")}]}
    del payload["system_prompt"]
    output = SWEBenchSolutionOutput.model_validate(payload)
    decoded = json.loads(output.model_dump_json())
    assert decoded["system_prompt"] == ""
    assert decoded["steps"][0]["llm_output"] == ""
    assert decoded["steps"][0]["sandbox_output"] == ""
    assert SWEBenchSolutionOutput.model_validate_json(output.model_dump_json()) == output


@pytest.mark.parametrize("model,benchmark", [(SolutionOutput, "swebench"), (SWEBenchSolutionOutput, "mbpp")])
def test_solution_output_rejects_wrong_benchmark(model, benchmark):
    with pytest.raises(ValidationError):
        model.model_validate({**SOLUTION, "benchmark": benchmark})


def test_malformed_nested_metrics_fail_as_validation_error():
    with pytest.raises(ValidationError):
        SolutionOutput.model_validate({**SOLUTION, "steps": [{**STEP, "retries": "not an int"}]})
    with pytest.raises(ValidationError):
        SWEBenchTaskInput.model_validate_json('{"instance_id": 12}')
