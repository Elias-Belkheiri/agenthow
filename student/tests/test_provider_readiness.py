"""Startup checks must not read cloud-placeholder dependency contents."""

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_readiness_module_does_not_import_sdk():
    project = Path(__file__).resolve().parents[1]
    result = subprocess.run([
        sys.executable, '-c',
        'import sys; import agent_smith.providers.readiness; '
        'assert "litellm" not in sys.modules; assert "openai" not in sys.modules',
    ], cwd=project, capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr


def test_dataless_sdk_rejected_without_reading_contents(monkeypatch, tmp_path):
    from agent_smith.providers import readiness

    package = tmp_path / 'openai'
    package.mkdir()
    placeholder = package / 'missing.py'
    placeholder.write_text('raise AssertionError("must not import")')
    monkeypatch.setattr(readiness, 'find_spec', lambda name: SimpleNamespace(
        submodule_search_locations=[str(package)]))
    real_stat = readiness.os.stat

    def flagged_stat(path, *args, **kwargs):
        if str(path) == str(placeholder):
            return SimpleNamespace(st_flags=0x40000000)
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(readiness.os, 'stat', flagged_stat)
    with pytest.raises(readiness.DependencyFilesUnavailable):
        readiness.check_provider_files()


def test_local_sdk_files_allowed(monkeypatch, tmp_path):
    from agent_smith.providers import readiness

    (tmp_path / 'module.py').write_text('')
    monkeypatch.setattr(readiness, 'find_spec', lambda name: SimpleNamespace(
        submodule_search_locations=[str(tmp_path)]))
    readiness.check_provider_files()


def test_missing_sdk_left_to_normal_import_error(monkeypatch):
    from agent_smith.providers import readiness

    monkeypatch.setattr(readiness, 'find_spec', lambda name: None)
    readiness.check_provider_files()
