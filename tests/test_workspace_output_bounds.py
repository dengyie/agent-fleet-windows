"""Workspace data and command output stay bounded before receipt creation."""
from pathlib import Path
import os
import sys
import time
import tracemalloc

import pytest

from tools.platform.backends.directory import DirectoryBackend, MAX_READ_BYTES, MAX_OUTPUT_BYTES
from tools.platform.backends.sandbox import SandboxBackend


def execution(tmp_path: Path, backend_type: type[DirectoryBackend], source: str) -> tuple[DirectoryBackend, list[str]]:
    script = tmp_path / "output.py"
    script.write_text(source)
    if backend_type is SandboxBackend:
        return SandboxBackend(tmp_path, launcher=[sys.executable, str(script)]), ["ls"]
    # Windows 上的 python3 常是 bash shim/WindowsApps stub，exec 链会把
    # 启动警告混进 stderr；可执行名按平台给真实解释器。
    executable = sys.executable if os.name == "nt" else "python3"
    return DirectoryBackend(tmp_path), [executable, str(script)]


def test_workspace_large_read_has_bounded_memory(tmp_path: Path) -> None:
    with (tmp_path / "large.txt").open("wb") as stream:
        for _ in range(128):
            stream.write(b"x" * 65536)
    backend = DirectoryBackend(tmp_path)
    tracemalloc.start()
    try:
        receipt = backend.read("large.txt")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert receipt.state == "succeeded"
    assert receipt.truncated
    assert receipt.result["content"] == "x" * MAX_READ_BYTES
    assert peak < 1024 * 1024


@pytest.mark.parametrize("backend_type", [DirectoryBackend, SandboxBackend])
def test_workspace_command_output_has_bounded_memory(tmp_path: Path, backend_type: type[DirectoryBackend]) -> None:
    backend, argv = execution(tmp_path, backend_type,
                              "import os\nfor _ in range(128):\n    os.write(1, b'x'*65536)\n")
    tracemalloc.start()
    try:
        receipt = backend.execute("output-test", argv)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert receipt.state == "succeeded"
    assert receipt.truncated
    assert receipt.result["output"] == "x" * MAX_OUTPUT_BYTES
    assert peak < 2 * 1024 * 1024


@pytest.mark.parametrize("extra", [0, 1])
def test_read_exact_limit_distinguishes_truncation(tmp_path: Path, extra: int) -> None:
    (tmp_path / "bounded.txt").write_bytes(b"x" * (MAX_READ_BYTES + extra))
    receipt = DirectoryBackend(tmp_path).read("bounded.txt")
    assert receipt.result["content"] == "x" * MAX_READ_BYTES
    assert receipt.truncated is bool(extra)


@pytest.mark.parametrize("backend_type", [DirectoryBackend, SandboxBackend])
def test_failed_command_preserves_exit_and_bounded_stderr(tmp_path: Path, backend_type: type[DirectoryBackend]) -> None:
    backend, argv = execution(tmp_path, backend_type,
                              "import os, sys\nos.write(2, b'failure'*100000)\nsys.exit(7)\n")
    receipt = backend.execute("failure-test", argv)
    assert receipt.state == "failed"
    assert receipt.error_code == "process_failed"
    assert receipt.result["returncode"] == 7
    assert receipt.result["output"].startswith("failure")
    assert len(receipt.result["output"].encode("utf-8")) <= MAX_OUTPUT_BYTES
    assert receipt.truncated


@pytest.mark.parametrize("backend_type", [DirectoryBackend, SandboxBackend])
@pytest.mark.parametrize("close_pipes", [False, True])
def test_workspace_timeout_reaps_process(tmp_path: Path, backend_type: type[DirectoryBackend], close_pipes: bool) -> None:
    source = "import os, time\nopen('child.pid', 'w').write(str(os.getpid()))\n"
    if close_pipes:
        source += "os.close(1)\nos.close(2)\n"
    backend, argv = execution(tmp_path, backend_type, source + "time.sleep(30)\n")
    started = time.monotonic()
    receipt = backend.execute("timeout-test", argv, timeout_s=0.5)
    assert receipt.error_code == "timeout"
    assert time.monotonic() - started < 3
    pid = int((tmp_path / "child.pid").read_text(encoding="utf-8"))
    if os.name == "nt":
        # os.kill(pid, 0) 在 Windows 走 TerminateProcess 路径（winerror 87），
        # 不是 POSIX 的存活探针；等价验证：OpenProcess + 0 等待已终止。
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        assert not handle or ctypes.windll.kernel32.WaitForSingleObject(handle, 0) == 0
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
    else:
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


@pytest.mark.parametrize("kind", ["read", "directory-exec", "sandbox-exec"])
def test_invalid_utf8_replacements_remain_within_byte_limit(tmp_path: Path, kind: str) -> None:
    if kind == "read":
        (tmp_path / "invalid.txt").write_bytes(b"\xff" * MAX_READ_BYTES)
        receipt = DirectoryBackend(tmp_path).read("invalid.txt")
        text = receipt.result["content"]
    else:
        backend_type = SandboxBackend if kind == "sandbox-exec" else DirectoryBackend
        backend, argv = execution(tmp_path, backend_type, f"import os\nos.write(1,b'\\xff'*{MAX_OUTPUT_BYTES})\n")
        receipt = backend.execute("utf8-test", argv)
        text = receipt.result["output"]
    assert receipt.state == "succeeded"
    assert receipt.truncated
    assert len(text.encode("utf-8")) <= MAX_OUTPUT_BYTES
