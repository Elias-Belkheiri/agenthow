# N1 environment diagnosis and scoped continuation

Worker delivered test-only checkpoint after pytest invocation stalled 90s. Independent review correctly BLOCKED implementation acceptance: backend.py/process.py absent; additional tests needed for per-call admission and setup/probe failures. Report paths under subagent outputs cf13655e-f119-4579-97ca-8467df94714c/seatbelt/.

Parent diagnosis, no installs/global/config/source changes:
- Isolated Python startup ~0.03s; normal site startup ~0.02s; pytest import ~0.12s.
- Collection with a 3s faulthandler and -s exposed a blocked import stack from tests/conftest.py -> httpx -> idna.core (intranges import). Disabling third-party plugin autoload did not eliminate the root conftest import.
- `ls -lO` showed idna/__pycache__/intranges.cpython-310.pyc and idnadata.py as hidden,compressed,dataless. This is evidence of unavailable/recalling filesystem content, not a proved pytest or sandbox code defect. Direct idna import subsequently completed in 2.57s; no claim of persistent repair.
- Scoped collection with PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 and `--confcutdir=tests/sandbox -s --collect-only -q tests/sandbox/test_process.py` reached expected ModuleNotFoundError for agent_smith.sandbox.backend (exit2). This is verified red collection evidence for missing implementation. No full-suite pass exists.

Continue only N1 in existing approved files using command-local plugin isolation and confcutdir for these standard-library/pytest-only sandbox tests. They do not depend on the root mock_model_http fixture. Do not delete or modify root conftest or provider dependencies, change persistent config, increase timeout retries, or label isolated results full-suite success. If another import/filesystem stall occurs, preserve checkpoint and report rather than repeat blindly. Source review and independent focused verification remain required before acceptance. No activation.
