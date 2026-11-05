"""N2b trusted terminal UI; fake TTYs only, no command execution."""
import io
import json
from pathlib import Path
import sys

import pytest

from agent_smith.tools.approval import TerminalApproval


class TTYInput(io.StringIO):
    def __init__(self, text='', *, tty=True):
        super().__init__(text)
        self.tty = tty
        self.reads = []
        self.before_read = lambda: None

    def isatty(self):
        return self.tty

    def readline(self, size=-1):
        self.before_read()
        self.reads.append(size)
        return super().readline(size)


class TTYOutput(io.StringIO):
    def __init__(self, *, tty=True):
        super().__init__()
        self.tty = tty
        self.flushed = False

    def isatty(self):
        return self.tty

    def flush(self):
        self.flushed = True
        super().flush()


def test_displays_before_accepting_and_binds_defaults_at_construction(monkeypatch):
    source, output = TTYInput('yes\n'), TTYOutput()
    monkeypatch.setattr(sys, 'stdin', source)
    monkeypatch.setattr(sys, 'stderr', output)
    approval = TerminalApproval()
    monkeypatch.setattr(sys, 'stdin', TTYInput('no\n'))
    monkeypatch.setattr(sys, 'stderr', TTYOutput())
    def check_display():
        assert output.flushed
        assert output.getvalue() == (
            'Command: "printf exact"\nWorkspace: "/workspace"\n'
            'Approve THIS command once? Type yes to approve [default: no]: ')
    source.before_read = check_display
    assert approval('printf exact', Path('/workspace')) is True
    assert len(source.reads) == 1 and 0 < source.reads[0] <= 1024


@pytest.mark.parametrize('answer,expected', [
    ('yes\n', True), ('YES\n', True), (' YeS \t\r\n', True),
    ('\n', False), (' \n', False), ('no\n', False), ('y\n', False),
    ('yes all\n', False), ('y-all\n', False), ('true\n', False),
    ('yes\x1b\n', False), ('yes\u00a0\n', False),
    ('', False), ('yes', False), ('yes\r', False),
    (' ' * 128 + 'yes\n', False), ('yes' + ' ' * 124 + '\n', True),
    ('yes' + ' ' * 125 + '\n', False),
])
def test_explicit_answer_policy(answer, expected):
    assert TerminalApproval(TTYInput(answer), TTYOutput())('true', Path('/w')) is expected


@pytest.mark.parametrize('input_tty,output_tty', [(False, True), (True, False), (False, False)])
def test_noninteractive_never_reads(input_tty, output_tty):
    source, output = TTYInput('yes\n', tty=input_tty), TTYOutput(tty=output_tty)
    assert TerminalApproval(source, output)('true', Path('/w')) is False
    assert source.reads == []
    assert output.getvalue() == ''


@pytest.mark.parametrize('side,operation', [
    ('input', 'isatty'), ('output', 'isatty'), ('output', 'write'),
    ('output', 'flush'), ('input', 'readline'),
])
@pytest.mark.parametrize('error', [OSError, ValueError, RuntimeError, KeyboardInterrupt])
def test_stream_failures_deny(monkeypatch, side, operation, error):
    source, output = TTYInput('yes\n'), TTYOutput()
    def fail(*args):
        raise error('stream failure')
    monkeypatch.setattr(source if side == 'input' else output, operation, fail)
    assert TerminalApproval(source, output)('true', Path('/w')) is False
    if operation != 'readline':
        assert source.reads == []


@pytest.mark.parametrize('side', ['input', 'output'])
def test_closed_stream_denies(side):
    source, output = TTYInput('yes\n'), TTYOutput()
    (source if side == 'input' else output).close()
    assert TerminalApproval(source, output)('true', Path('/w')) is False
    assert source.reads == []


@pytest.mark.parametrize('written', [None, 0, 1])
def test_short_display_never_reads(monkeypatch, written):
    source, output = TTYInput('yes\n'), TTYOutput()
    monkeypatch.setattr(output, 'write', lambda text: written)
    assert TerminalApproval(source, output)('true', Path('/w')) is False
    assert source.reads == []


def test_fresh_decisions_for_identical_commands():
    source, output = TTYInput('yes\nno\nyes\n'), TTYOutput()
    approval = TerminalApproval(source, output)
    assert [approval('true', Path('/w')) for _ in range(3)] == [True, False, True]
    assert len(source.reads) == 3
    assert output.getvalue().count('Command: "true"') == 3


def test_oversized_line_tail_cannot_become_next_approval():
    source = TTYInput('x' * 129 + 'yes\nyes\n')
    approval = TerminalApproval(source, TTYOutput())
    assert approval('true', Path('/w')) is False
    assert approval('true', Path('/w')) is False
    assert len(source.reads) == 1


def test_hostile_command_and_path_are_ascii_escaped_without_mutation():
    command = 'printf "x";\nApprove?\r\x1b[2J\b\t\u202e\u2066é\\'
    cwd = Path('/workspace/\x1b[31m\n\r\b\u202e\u2066é"\\')
    source, output = TTYInput('yes\n'), TTYOutput()
    assert TerminalApproval(source, output)(command, cwd) is True
    lines = output.getvalue().split('\n')
    assert len(lines) == 3
    assert json.loads(lines[0].removeprefix('Command: ')) == command
    assert json.loads(lines[1].removeprefix('Workspace: ')) == str(cwd)
    assert all(32 <= ord(c) < 127 or c == '\n' for c in output.getvalue())


@pytest.mark.parametrize('command,cwd', [
    (None, Path('/w')), (1, Path('/w')), ('', Path('/w')),
    ('x\x00', Path('/w')), ('x' * 8193, Path('/w')), ('\ud800', Path('/w')),
    ('true', '/w'), ('true', Path('relative')), ('true', Path('/w/../other')),
    ('true', Path('/w\x00')),
])
def test_invalid_arguments_deny_without_io(command, cwd):
    source, output = TTYInput('yes\n'), TTYOutput()
    assert TerminalApproval(source, output)(command, cwd) is False
    assert source.reads == [] and output.getvalue() == ''


def test_no_filesystem_validation_or_traversal(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('approval must not consult filesystem metadata')
    for method in ('resolve', 'stat', 'lstat', 'is_dir'):
        monkeypatch.setattr(Path, method, forbidden)
    assert TerminalApproval(TTYInput('yes\n'), TTYOutput())('true', Path('/missing')) is True


def test_dispatcher_denial_and_one_use_approval(tmp_path, monkeypatch):
    from agent_smith.sandbox.backend import ProcessResult
    from agent_smith.tools.contracts import ToolContext
    from agent_smith.tools import workspace

    class FakeBackend:
        calls = []
        def __init__(self, **roots):
            pass
        def execute(self, request):
            self.calls.append(request)
            return ProcessResult('exited', 0, b'fixture')

    monkeypatch.setattr(workspace, 'SeatbeltBackend', FakeBackend)
    source, output = TTYInput('no\nyes\n\n'), TTYOutput()
    command = 'printf "exact"\n# \u202e\x1b[31m'
    with workspace.WorkspaceToolDispatcher(
        ToolContext(tmp_path), approval_callback=TerminalApproval(source, output)
    ) as dispatcher:
        assert dispatcher.call('run_shell', {'command': command})['error'] == 'approval_denied'
        assert FakeBackend.calls == []
        assert dispatcher.call('run_shell', {'command': command})['ok'] is True
        assert dispatcher.call('run_shell', {'command': command})['error'] == 'approval_denied'
    assert len(FakeBackend.calls) == 1
    assert FakeBackend.calls[0].argv == ('/bin/sh', '-c', command)
    assert output.getvalue().count('Command: ' + json.dumps(command, ensure_ascii=True)) == 3
    assert output.getvalue().count('Workspace: ' + json.dumps(str(tmp_path.resolve()))) == 3
