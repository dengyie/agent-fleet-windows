"""Root-cause guard: every runtime console child goes through the launch policy.

A GUI-subsystem parent (pythonw.exe under the probe's scheduled task) gives an
unflagged console child a new visible window, and a strict-UTF-8 text child can
leave ``stdout`` as ``None`` on non-ASCII output.  Both were fixed once at
individual call sites and came back with the next call site, so the invariant
is asserted here instead of being re-learned per spawn.
"""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from tools import local_process

ROOT = Path(__file__).resolve().parents[1]

# Runtime packages that spawn local children. tests/ and deploy/ are excluded:
# the former is tooling, the latter is a console-driven release pipeline.
SCANNED_PACKAGES = ("connectors", "hub", "tools")
EXCLUDED_PARTS = {"testing", "__pycache__"}
POLICY_MODULE = ROOT / "tools" / "local_process.py"

SPAWN_METHODS = {"run", "Popen", "call", "check_call", "check_output"}


def _runtime_modules():
    for package in SCANNED_PACKAGES:
        for path in sorted((ROOT / package).rglob("*.py")):
            if EXCLUDED_PARTS & set(path.parts):
                continue
            if path == POLICY_MODULE:
                continue
            yield path


def _raw_subprocess_spawns(path):
    """Yield ``subprocess.run``/``Popen`` calls that bypass the launch policy."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr not in SPAWN_METHODS:
            continue
        if not isinstance(func.value, ast.Name) or func.value.id != "subprocess":
            continue
        yield node


def test_every_runtime_spawn_declares_creationflags():
    offenders = []
    for path in _runtime_modules():
        for node in _raw_subprocess_spawns(path):
            keywords = {keyword.arg for keyword in node.keywords}
            if "creationflags" not in keywords:
                offenders.append(f"{path.relative_to(ROOT).as_posix()}:{node.lineno}")
    assert offenders == [], (
        "console children must pass creationflags (use tools.local_process): "
        + ", ".join(offenders)
    )


def test_hidden_flag_is_defined_for_this_platform():
    if sys.platform == "win32":
        assert local_process.NO_WINDOW == subprocess.CREATE_NO_WINDOW
        assert local_process.NO_WINDOW != 0
    else:
        assert local_process.NO_WINDOW == 0


def test_run_applies_hidden_window_and_lenient_decode(monkeypatch):
    captured = {}

    def fake_run(argv, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(local_process.subprocess, "run", fake_run)
    local_process.run(["echo", "hi"], capture_output=True, text=True)

    assert captured["creationflags"] == local_process.NO_WINDOW
    assert captured["errors"] == "replace"
    assert captured["text"] is True


def test_run_preserves_caller_creationflags_and_errors(monkeypatch):
    captured = {}

    def fake_run(argv, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(local_process.subprocess, "run", fake_run)
    local_process.run(["x"], capture_output=True, encoding="utf-8",
                      errors="strict", creationflags=0x00000200)

    assert captured["creationflags"] == 0x00000200 | local_process.NO_WINDOW
    assert captured["errors"] == "strict"


def test_binary_mode_never_forces_a_text_decoder(monkeypatch):
    captured = {}

    def fake_run(argv, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(local_process.subprocess, "run", fake_run)
    local_process.run(["x"], capture_output=True)

    # errors= without text mode is a ValueError in subprocess.
    assert "errors" not in captured


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only shim shadowing")
def test_resolve_bash_never_returns_the_wsl_launcher():
    resolved = local_process.resolve_bash()
    if resolved is not None:
        assert "system32" not in resolved.lower()
        assert "windowsapps" not in resolved.lower()


def test_resolve_bash_honours_override(tmp_path, monkeypatch):
    if sys.platform != "win32":
        assert local_process.resolve_bash() == "bash"
        return
    shell = tmp_path / "bash.exe"
    shell.write_text("", encoding="utf-8")
    monkeypatch.setenv("AGENT_FLEET_BASH", str(shell))
    assert local_process.resolve_bash() == str(shell)
