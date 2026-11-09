"""Metadata-only checks before importing the provider SDKs.

macOS cloud storage can evict virtualenv files. Reading a dataless placeholder
(including during import) can block waiting for hydration; stat does not read it.
This is a diagnostic, not a hard timeout for arbitrary filesystem/kernel stalls.
"""

from importlib.util import find_spec
import os
import stat


class DependencyFilesUnavailable(RuntimeError):
    """SDK files exist only as cloud placeholders, not local contents."""


def check_provider_files() -> None:
    """Reject evicted SDK files without importing SDKs or opening their files."""
    dataless = getattr(stat, 'SF_DATALESS', 0x40000000)
    for name in ('openai', 'litellm'):
        spec = find_spec(name)
        if spec is None:
            continue  # Preserve the normal missing-dependency error.
        for root in spec.submodule_search_locations or ():
            def scan_error(error):
                raise error

            for directory, _, files in os.walk(root, onerror=scan_error):
                for filename in files:
                    info = os.stat(os.path.join(directory, filename))
                    if getattr(info, 'st_flags', 0) & dataless:
                        raise DependencyFilesUnavailable()
