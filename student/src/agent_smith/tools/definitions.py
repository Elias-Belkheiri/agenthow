"""Native function descriptions; execution authority remains in dispatchers."""

from agent_smith.sandbox.tool_worker import SCHEMAS


READ_ONLY_NAMES = ('read_file', 'list_files', 'search_code')
_DESCRIPTIONS = {
    'read_file': 'Read an inclusive line range from a UTF-8 workspace file.',
    'list_files': 'List workspace files matching a glob. Use directory "." and pattern "*" for current-directory files.',
    'search_code': 'Search workspace file contents by regex and file glob.',
    'write_file': 'Create or replace a workspace file. Automatic destructive write; no rollback.',
    'edit_file': 'Replace exactly one occurrence of nonempty old_text. Automatic edit; no rollback.',
    'run_shell': 'Run a shell command in the workspace. Requires fresh exact-command terminal approval; unavailable in noninteractive tasks.',
}


def tool_definitions(names: tuple[str, ...]) -> list[dict]:
    """Build independent schemas from the same exact keys/types as the worker."""
    definitions = []
    for name in names:
        schema = SCHEMAS[name]
        properties = {key: {'type': 'integer' if kind is int else 'string'}
                      for key, kind in schema.items()}
        for key in ('start_line', 'end_line'):
            if key in properties:
                properties[key].update(minimum=1, maximum=2**31 - 1)
        definitions.append({'type': 'function', 'function': {
            'name': name, 'description': _DESCRIPTIONS[name],
            'parameters': {'type': 'object', 'properties': properties,
                           'required': list(schema), 'additionalProperties': False},
        }})
    return definitions
