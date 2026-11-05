import pytest
import os
import subprocess
import sys
from pathlib import Path

from agent_smith.providers.factory import create_model_client


def test_factory_selects_provider_and_keeps_credentials_out_of_repr(monkeypatch):
    monkeypatch.setenv('GROQ_API_KEY', 'sensitive')
    client = create_model_client('groq', model='m')
    assert type(client).__name__ == 'LiteLLMClient'
    assert client.provider_name == 'groq'
    assert 'sensitive' not in repr(client)


def test_factory_rejects_unknown_provider():
    with pytest.raises(ValueError, match='provider'):
        create_model_client('other', model='m')


def test_factory_selects_gemini():
    client = create_model_client('gemini', model='m')
    assert type(client).__name__ == 'LiteLLMClient'
    assert client.provider_name == 'gemini'


def test_import_leaves_dotenv_loading_to_the_application():
    project = Path(__file__).resolve().parents[2]
    env = {key: value for key, value in os.environ.items() if key not in ('GROQ_API_KEY', 'LITELLM_MODE')}
    env['PYTHONPATH'] = str(project / 'src')
    result = subprocess.run([
        sys.executable, '-c',
        'import os; from agent_smith.providers.factory import create_model_client; '
        'assert os.environ.get("LITELLM_MODE") == "PRODUCTION"; '
        'assert not os.environ.get("GROQ_API_KEY")',
    ], cwd=project, env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, 'SDK import must not load .env or mutate provider credentials'
