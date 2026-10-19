"""Launch policy for every console child process the fleet spawns.

A GUI-subsystem parent (``pythonw.exe``, ``wscript`` with SW_HIDE, a scheduled
task) owns no console.  Windows then gives each console child a brand-new
*visible* conhost window unless the spawner passes ``CREATE_NO_WINDOW``.  The
flag therefore lives here once instead of being remembered at each call site,
so a newly added spawn cannot silently reintroduce the flash window.

The same applies to decoding: a child that emits GBK/UTF-16 (the Windows
``bash``/``python3`` shims do) must degrade to replacement characters.  Strict
UTF-8 kills the reader thread and leaves ``stdout`` as ``None``, which callers
then dereference.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from functools import lru_cache

# 0 on POSIX, where the flag does not exist (and passing 0 is accepted).
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0

_STUB_DIR_PARTS = ("windows\\system32", "windowsapps")


def _launch_policy(kwargs):
    """Return ``kwargs`` with the hidden-window and lenient-decode policy."""
    options = dict(kwargs)
    options["creationflags"] = int(options.get("creationflags", 0)) | NO_WINDOW
    text_mode = bool(
        options.get("text")
        or options.get("encoding")
        or options.get("universal_newlines")
    )
    if text_mode:
        options.setdefault("errors", "replace")
    return options


def run(argv, **kwargs):
    """``subprocess.run`` with the fleet launch policy applied."""
    return subprocess.run(argv, **_launch_policy(kwargs))


def popen(argv, **kwargs):
    """``subprocess.Popen`` with the fleet launch policy applied."""
    return subprocess.Popen(argv, **_launch_policy(kwargs))


def _is_stub_dir(path: str) -> bool:
    normalized = os.path.normcase(os.path.abspath(path))
    return any(part in normalized for part in _STUB_DIR_PARTS)


def _non_stub_path() -> str:
    entries = [
        entry
        for entry in os.environ.get("PATH", "").split(os.pathsep)
        if entry and not _is_stub_dir(entry)
    ]
    return os.pathsep.join(entries)


@lru_cache(maxsize=8)
def _resolve_bash_cached(override: str) -> str | None:
    if os.name != "nt":
        return "bash"
    if override and os.path.isfile(override):
        return override

    candidates = [shutil.which("bash", path=_non_stub_path())]

    # A Git install puts only ``cmd`` on PATH; bash lives beside it.
    git = shutil.which("git", path=_non_stub_path())
    if git:
        git_root = os.path.dirname(os.path.dirname(git))
        candidates.append(os.path.join(git_root, "bin", "bash.exe"))
        candidates.append(os.path.join(git_root, "usr", "bin", "bash.exe"))

    for root in (
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramFiles(x86)"),
        os.environ.get("LOCALAPPDATA"),
    ):
        if root:
            candidates.append(os.path.join(root, "Git", "bin", "bash.exe"))
            candidates.append(os.path.join(root, "Programs", "Git", "bin", "bash.exe"))

    for candidate in candidates:
        if candidate and os.path.isfile(candidate) and not _is_stub_dir(candidate):
            return candidate
    return None


def resolve_bash():
    """Return a real POSIX shell, or ``None`` when only the WSL stub exists.

    ``C:\\Windows\\System32\\bash.exe`` is the WSL launcher, not Git Bash: it
    exits 1 and prints a UTF-16 "WSL is not installed" notice.  Treating it as
    a shell turns every ``bash -c`` collector into a silent failure.
    """
    return _resolve_bash_cached(os.environ.get("AGENT_FLEET_BASH"))


# The shell does not move while the probe runs, and the scan stats every PATH
# entry, so callers may cache_clear when they deliberately change the lookup.
resolve_bash.cache_clear = _resolve_bash_cached.cache_clear
