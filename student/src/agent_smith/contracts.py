"""Pydantic contracts for the MBPP and SWE-bench evaluator JSON files."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class MBPPTaskInput(BaseModel):
    """Input for MBPP task evaluation."""

    task_id: int = Field(strict=True, gt=0)
    task_definition: str
    function_definition: str
    test_imports: list[str] = Field(default_factory=list)
    test_list: list[str] = Field(default_factory=list)


class SWEBenchTaskInput(BaseModel):
    """Input for a SWE-bench task provided by the evaluator."""

    instance_id: str = Field(min_length=1)
    problem_statement: str = Field(min_length=1)
    docker_image: str = Field(min_length=1)
    eval_script: str = Field(min_length=1)
    hints_text: str = ""
    repo: str = ""


class StepMetrics(BaseModel):
    """MBPP step: all metrics and provenance fields are required except timestamp."""

    step: int = Field(ge=1)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    request_time_ms: float = Field(ge=0)
    api_url: str
    model_name: str
    llm_output: str
    sandbox_input: str
    sandbox_output: str
    retries: int = Field(ge=0)
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())


class SWEBenchStepMetrics(StepMetrics):
    """SWE-bench allows empty provenance fields on steps where they do not apply."""

    api_url: str = ""
    model_name: str = ""
    llm_output: str = ""
    sandbox_input: str = ""
    sandbox_output: str = ""
    retries: int = Field(default=0, ge=0)


class SolutionOutput(BaseModel):
    """MBPP evaluator output; system_prompt and totals must be supplied."""

    task_id: str
    benchmark: Literal["mbpp"]
    success: bool
    solution: str
    system_prompt: str
    iterations: int = Field(ge=0)
    total_requests: int = Field(ge=0)
    total_input_tokens: int = Field(ge=0)
    total_output_tokens: int = Field(ge=0)
    total_time_seconds: float = Field(ge=0)
    steps: list[StepMetrics] = Field(default_factory=list)
    error: str | None = None
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())


class SWEBenchSolutionOutput(SolutionOutput):
    """SWE-bench evaluator output, with its PDF-specified optional defaults."""

    benchmark: Literal["swebench"]
    system_prompt: str = ""
    steps: list[SWEBenchStepMetrics] = Field(default_factory=list)
