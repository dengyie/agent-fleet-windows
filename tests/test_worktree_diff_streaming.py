"""Git result collection keeps memory bounded by the published byte budget."""
from pathlib import Path
import os
import subprocess
import sys
import time
import tracemalloc

import pytest

from tools import worktree


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    return tmp_path


def test_large_git_patch_has_bounded_parent_memory(repository: Path) -> None:
    with (repository / "large.txt").open("wb") as stream:
        for _ in range(8192):
            stream.write(b"x" * 1023 + b"\n")
    tracemalloc.start()
    try:
        patch = worktree.diff_patch(repository)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 2 * 1024 * 1024, f"bounded patch collector allocated {peak} bytes"
    assert len(patch.encode("utf-8")) <= 102400
    assert patch.endswith("\n…[truncated]")


@pytest.mark.parametrize("kind", ["patch", "stat"])
def test_git_diff_marker_is_inside_utf8_budget(repository: Path, kind: str) -> None:
    for index in range(12):
        (repository / f"result-{index}.txt").write_text(
            "🌲" * 100 + "\n", encoding="utf-8")
    collector = worktree.diff_patch if kind == "patch" else worktree.diff_stat
    result = collector(repository, max_bytes=120)
    assert len(result.encode("utf-8")) <= 120
    assert result.endswith("\n…[truncated]")


def fake_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: str) -> None:
    """Install a stand-in ``git`` whose non-``add`` invocations run script.

    Windows ``CreateProcess`` only resolves ``git.exe`` — a shebang script or
    ``git.bat`` on PATH is invisible to the product's ``["git", ...]`` argv —
    so there we swap the binary via monkeypatched spawn sites instead of PATH.
    """
    if os.name == "nt":
        script_file = tmp_path / "_fake_git.py"
        script_file.write_text(
            "import os, sys, time\n"
            "if sys.argv[1] == 'add':\n    sys.exit(0)\n" + script)
        real_popen, real_run = subprocess.Popen, subprocess.run

        def swap(argv, *args, **kwargs):
            if argv and argv[0] == "git":
                argv = [sys.executable, str(script_file), *argv[1:]]
            if argv and argv[0] == sys.executable:
                # _git() passes capture_output/text; keep those semantics.
                if "creationflags" not in kwargs:
                    kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                return real_popen(argv, *args, **kwargs)
            return real_popen(argv, *args, **kwargs)

        def swap_run(argv, *args, **kwargs):
            if argv and argv[0] == "git":
                argv = [sys.executable, str(script_file), *argv[1:]]
            return real_run(argv, *args, **kwargs)

        monkeypatch.setattr(worktree.subprocess, "Popen", swap)
        monkeypatch.setattr(worktree.subprocess, "run", swap_run)
        monkeypatch.setattr("tools.bounded_process.subprocess.Popen", swap)
        monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ.get("PATH", ""))
        return
    binary = tmp_path / "git"
    binary.write_text(f"#!{sys.executable}\nimport os, sys, time\n"
                      "if sys.argv[1] == 'add':\n    sys.exit(0)\n" + script)
    binary.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ.get("PATH", ""))


def test_large_stderr_is_drained_and_failure_is_not_hidden(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_git(tmp_path, monkeypatch,
             "for _ in range(128):\n"
             "    os.write(1, b'x' * 65536)\n"
             "    os.write(2, b'failure' * 10000)\n"
             "sys.exit(7)\n")
    tracemalloc.start()
    try:
        with pytest.raises(worktree.WorktreeError, match="git diff 失败") as error:
            worktree.diff_patch(tmp_path)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 2 * 1024 * 1024
    assert len(str(error.value)) < 350


@pytest.mark.parametrize("close_pipes", [False, True])
def test_diff_deadline_reaps_child_and_retains_cause(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                   close_pipes: bool) -> None:
    fake_git(tmp_path, monkeypatch,
             "open('child.pid', 'w').write(str(os.getpid()))\n"
             + ("os.close(1)\nos.close(2)\n" if close_pipes else "")
             + "time.sleep(30)\n")
    monkeypatch.setattr(worktree, "DIFF_TIMEOUT_S", 0.5)
    started = time.monotonic()
    with pytest.raises(worktree.WorktreeError, match="超时") as error:
        worktree.diff_patch(tmp_path)
    assert time.monotonic() - started < 3
    assert isinstance(error.value.__cause__, subprocess.TimeoutExpired)
    pid = int((tmp_path / "child.pid").read_text(encoding="utf-8"))
    if os.name == "nt":
        # os.kill(pid, 0) 在 Windows 是 TerminateProcess 语义（winerror 87），
        # 不是存活探针；等价验证：SYNCHRONIZE 句柄立即等待即返回已终止。
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x00100000, False, pid)
        assert not handle or ctypes.windll.kernel32.WaitForSingleObject(handle, 0) == 0
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
    else:
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


def test_invalid_utf8_diff_is_bounded_valid_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_git(tmp_path, monkeypatch, "os.write(1, b'\\xff' * 200)\n")
    patch = worktree.diff_patch(tmp_path, max_bytes=120)
    assert len(patch.encode("utf-8")) <= 120
    assert patch.endswith("\n…[truncated]")
