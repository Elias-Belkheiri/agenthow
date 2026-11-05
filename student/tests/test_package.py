import subprocess
import sys

import agent_smith


def test_agent_smith_is_importable():
    assert agent_smith.__version__ == "0.1.0"


def test_mbpp_help_lists_evaluator_flags():
    result = subprocess.run(
        [sys.executable, "-m", "agent_mbpp", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    for flag in ("--task-file", "--output", "--model-name", "--provider-url"):
        assert flag in result.stdout


def test_mbpp_does_not_run_tasks_before_sandbox_exists(tmp_path):
    task_file = tmp_path / "task.json"
    output_file = tmp_path / "solution.json"
    task_file.write_text('{"code": "raise RuntimeError(\"executed\")"}')
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent_mbpp",
            "--task-file",
            str(task_file),
            "--output",
            str(output_file),
            "--model-name",
            "unused",
            "--provider-url",
            "https://example.invalid",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "not implemented" in result.stderr.lower()
    assert not output_file.exists()
