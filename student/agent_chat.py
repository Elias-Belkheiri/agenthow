"""Run bounded chat with native macOS Seatbelt or explicit read-only tools."""

import argparse
import logging
import os
import shlex
import sys
from pathlib import Path

# Keep the documented source-checkout invocation usable with uv's editable install.
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from agent_smith.providers.readiness import DependencyFilesUnavailable
from agent_smith.loop.chat import ChatRunner, ChatSession
from agent_smith.tools.contracts import ToolContext
from agent_smith.tools.dispatcher import ReadOnlyToolDispatcher
from agent_smith.tools.workspace import WorkspaceToolDispatcher
from agent_smith.tools.approval import TerminalApproval

SANDBOX_WARNING = (
    'Native macOS Seatbelt: automatic direct destructive writes/edits; no rollback, '
    'including failed turns. Shell requires per-command terminal approval (never in --task). '
    'Network denied; protected paths and unsupported hardlinks refused. '
    'Trusted-local use: cooperating host processes must preserve admitted boundaries. '
    'No hard memory guarantee. BEST-EFFORT cleanup: detached survivors may keep changing '
    'allowed files after exit or timeout. Editing application source can affect future launches.'
)


def create_model_client(provider: str, *, model: str, api_url: str | None = None,
                        json_mode: bool = False):
    # Check metadata before imports can block reading cloud-evicted SDK files.
    from agent_smith.providers.readiness import check_provider_files
    check_provider_files()
    # Defer optional dependencies for help/readiness checks and injected-model tests.
    from agent_smith.providers.factory import create_model_client as factory
    return factory(provider, model=model, api_url=api_url, json_mode=json_mode)


def load_api_key(provider: str, dotenv_path: Path | None = None) -> str:
    """Load a provider key from the environment or a local .env file, without shell evaluation."""
    variable = f'{provider.upper()}_API_KEY'
    if os.environ.get(variable):
        return os.environ[variable]
    candidates = [dotenv_path] if dotenv_path is not None else [
        Path.cwd() / '.env', Path(__file__).resolve().parent.parent / '.env',
    ]
    for path in candidates:
        if path is None or not path.is_file():
            continue
        for raw in path.read_text(encoding='utf-8').splitlines():
            line = raw.strip()
            if line.startswith('export '):
                line = line[7:].lstrip()
            if not line.startswith(f'{variable}='):
                continue
            value = line.split('=', 1)[1].strip()
            try:
                parsed = shlex.split(value, comments=True)
            except ValueError:
                return ''
            key = parsed[0] if len(parsed) == 1 else ''
            if key:
                os.environ[variable] = key
                return key
    return ''


def run_repl(session: ChatSession, *, sandboxed: bool = False) -> int:
    print('Agent Smith — sandboxed coding chat. Type /help for commands.' if sandboxed
          else 'Agent Smith — read-only coding chat. Type /help for commands.')
    if sandboxed:
        print(SANDBOX_WARNING)
    try:
        while True:
            task = input('agent> ').strip()
            if not task:
                continue
            if task == '/exit':
                break
            if task == '/help':
                print('/help — commands; /reset — clear history and usage; /exit — quit')
                print(SANDBOX_WARNING if sandboxed else
                      'Can read, list, and search workspace files. Cannot edit or run code.')
                continue
            if task == '/reset':
                session.reset()
                print('Conversation reset (history and usage cleared).')
                continue
            if task.startswith('/'):
                print('Unknown command. Type /help.')
                continue
            result = session.send(task)
            if result.success:
                print(f'\n{result.final_answer}\n')
            else:
                hint = ' Use /reset to clear history and usage.' if 'limit' in (result.error or '') else ''
                print(f'Chat failed: {result.error}.{hint}', file=sys.stderr)
    except (EOFError, KeyboardInterrupt):
        print()
    print('Goodbye.')
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--provider', choices=('groq', 'gemini'), required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--provider-url')
    parser.add_argument('--workspace', type=Path, default=Path.cwd())
    parser.add_argument('--tools', choices=('sandboxed', 'read-only'), default='sandboxed',
                        help='Tool mode (default: sandboxed). ' + SANDBOX_WARNING)
    parser.add_argument('--task', help='Run one task and exit; omit for interactive chat')
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s', stream=sys.stderr)
    tools = None
    try:
        try:
            context = ToolContext(args.workspace)
            if args.tools == 'sandboxed':
                tools = WorkspaceToolDispatcher(
                    context, approval_callback=TerminalApproval() if args.task is None else None)
                if not tools.check_ready():
                    raise RuntimeError('sandbox unavailable')
            else:
                tools = ReadOnlyToolDispatcher(context)
            load_api_key(args.provider)
            model = create_model_client(args.provider, model=args.model, api_url=args.provider_url, json_mode=False)
            model.provider_name = args.provider
        except DependencyFilesUnavailable:
            print('Chat could not start: provider dependencies are cloud placeholders, '
                  'not local files. In Finder, select student/.venv and choose Download Now '
                  '(or Keep Downloaded), wait for completion, then retry. Alternatively, '
                  'recreate the virtualenv in a non-cloud-synced location.', file=sys.stderr)
            return 2
        except Exception as exc:
            logging.getLogger(__name__).error('chat_start_failed', extra={'error_type': type(exc).__name__})
            print('Chat could not start; check sandbox support, provider configuration and workspace.', file=sys.stderr)
            return 2
        if args.task is None:
            return run_repl(ChatSession(model, tools), sandboxed=args.tools == 'sandboxed')
        if args.tools == 'sandboxed':
            print(SANDBOX_WARNING, file=sys.stderr)
        result = ChatRunner(model, tools).run(args.task)
        if not result.success:
            print(f'Chat failed: {result.error}', file=sys.stderr)
            return 1
        print(result.final_answer)
        return 0
    except KeyboardInterrupt:
        print('Chat interrupted.', file=sys.stderr)
        return 130
    finally:
        if isinstance(tools, WorkspaceToolDispatcher):
            tools.close()


if __name__ == '__main__':
    raise SystemExit(main())
