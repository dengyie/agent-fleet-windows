"""ProbeContext must degrade, never crash, and never flash a window."""

import subprocess
import sys

from connectors import probe


class _Result:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_run_executes_through_the_resolved_shell(monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return _Result(stdout="  ok  \n")

    monkeypatch.setattr(probe, "resolve_bash", lambda: "/fake/bash")
    monkeypatch.setattr(probe, "run_local", fake_run)

    assert probe.ProbeContext("m").run("printf ok") == "ok"
    argv, kwargs = calls[0]
    assert argv == ["/fake/bash", "-c", "printf ok"]
    assert kwargs["text"] is True


def test_run_returns_empty_when_no_real_shell_exists(monkeypatch):
    monkeypatch.setattr(probe, "resolve_bash", lambda: None)
    monkeypatch.setattr(probe, "run_local",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("spawned")))
    assert probe.ProbeContext("m").run("printf ok") == ""


def test_run_survives_a_none_stdout(monkeypatch):
    # subprocess leaves stdout=None when the reader thread dies on decode.
    monkeypatch.setattr(probe, "resolve_bash", lambda: "/fake/bash")
    monkeypatch.setattr(probe, "run_local", lambda *a, **k: _Result(stdout=None))
    assert probe.ProbeContext("m").run("printf ok") == ""


def test_run_swallows_transport_failures(monkeypatch):
    def boom(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="bash", timeout=1)

    monkeypatch.setattr(probe, "resolve_bash", lambda: "/fake/bash")
    monkeypatch.setattr(probe, "run_local", boom)
    assert probe.ProbeContext("m").run("sleep 60") == ""


def test_run_python_uses_the_running_interpreter(monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return _Result(stdout='{"installed": false}')

    monkeypatch.setattr(probe, "run_local", fake_run)

    assert probe.ProbeContext("m").run_python("print('x')") == '{"installed": false}'
    argv, kwargs = calls[0]
    assert argv == [sys.executable, "-"]
    assert kwargs["input"] == "print('x')"


def test_run_python_falls_back_to_empty_json(monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("no interpreter")

    monkeypatch.setattr(probe, "run_local", boom)
    assert probe.ProbeContext("m").run_python("print('x')") == "{}"


def test_remote_control_actions_are_refused():
    ctx = probe.ProbeContext("m")
    for payload in (ctx.kill_gateway(), ctx.restart_process("x"),
                    ctx.tmux_send("s", "c"), ctx.control_exec("whoami")):
        assert payload["ok"] is False
