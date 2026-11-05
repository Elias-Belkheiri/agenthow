"""Only a single, well-formed Python fenced action may reach a future sandbox."""

import ast
import builtins

import pytest

from agent_smith.loop.extract import extract_action


def test_extract_action_returns_only_python_code_after_thought():
    response = 'Thought: check the candidate\n```python\nprint(run_tests())\n```'

    action = extract_action(response)

    assert action.status == 'action'
    assert action.code == 'print(run_tests())\n'
    assert action.feedback is None


def test_extract_action_does_not_take_model_written_observation_as_result():
    response = ('Thought: try the tests\n```python\nprint(run_tests())\n```\n'
                'Observation: all tests passed (claimed by model)')

    action = extract_action(response)

    assert action.status == 'action'
    assert action.code == 'print(run_tests())\n'
    assert not hasattr(action, 'observation')


@pytest.mark.parametrize('response', [
    'Thought: I solved it.\nObservation: all tests passed',
    'print(run_tests())',
    '```json\n{"name": "run_tests"}\n```',
    '```\nprint(run_tests())\n```',
    '<invoke name="run_tests"></invoke>',
    'Action: run_tests\nAction Input: {}',
])
def test_extract_action_gives_explicit_no_code_feedback(response):
    action = extract_action(response)

    assert action.status == 'no_code'
    assert action.code is None
    assert 'Python fenced code' in action.feedback


@pytest.mark.parametrize('response', [
    '```python\nprint(run_tests())',
    '```python\n```',
    '```python\nprint(\n```',
    '```python\nprint(run_tests())\n```\n```python\nprint("again")',
    '```python extra\nprint(run_tests())\n```',
    '```python\n# no action\n```',
])
def test_extract_action_gives_explicit_malformed_feedback_without_code(response):
    action = extract_action(response)

    assert action.status == 'malformed'
    assert action.code is None
    assert action.feedback


def test_extract_action_defers_context_errors_without_compiling(monkeypatch):
    original_compile = builtins.compile
    seen = []

    def only_ast_compile(source, filename, mode, flags=0, **kwargs):
        if source == 'return 1\n':
            seen.append(flags)
            assert flags & ast.PyCF_ONLY_AST, 'Generated code must not become executable bytecode'
        return original_compile(source, filename, mode, flags, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr('builtins.compile', only_ast_compile)
        action = extract_action('```python\nreturn 1\n```')
    assert action.status == 'action'
    assert action.code == 'return 1\n'
    assert seen


def test_extract_action_rejects_two_code_actions_as_ambiguous():
    response = ('```python\nprint(run_tests())\n```\n'
                '```python\nfinal_answer("solution")\n```')

    action = extract_action(response)

    assert action.status == 'ambiguous'
    assert action.code is None
    assert 'multiple' in action.feedback.lower()


def test_extract_action_rejects_mixed_python_and_non_python_fences():
    response = ('```python\nprint(run_tests())\n```\n'
                '```json\n{"name": "final_answer"}\n```')

    action = extract_action(response)

    assert action.status == 'ambiguous'
    assert action.code is None
    assert action.feedback


def test_extract_action_rejects_pathological_ast_recursion():
    code = ' + '.join(['0'] * 10_000)
    action = extract_action(f'```python\n{code}\n```')
    assert action.status == 'malformed'
    assert action.code is None
    assert 'Malformed Python code' in action.feedback


def test_extract_action_returns_feedback_on_parser_recursion(monkeypatch):
    def recursive_parse(code):
        raise RecursionError('AST depth exceeded')

    with monkeypatch.context() as patch:
        patch.setattr(ast, 'parse', recursive_parse)
        action = extract_action('```python\n' + ' + '.join(['0'] * 100) + '\n```')
    assert action.status == 'malformed' and action.code is None
    assert 'Malformed Python code: AST depth exceeded' == action.feedback


def test_extract_action_does_not_run_generated_python(tmp_path):
    marker = tmp_path / 'would_run'
    response = f'```python\nopen({str(marker)!r}, "w").write("bad")\n```'

    action = extract_action(response)

    assert action.status == 'action'
    assert action.code.startswith('open(')
    assert not marker.exists()
