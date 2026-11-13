"""Bounded POSIX child output with deadline and process-group ownership."""
from __future__ import annotations

import math
import os
import selectors
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Mapping, Sequence

from tools import local_process


def decode_prefix(raw: bytes, limit: int) -> tuple[str, bool]:
    """Preserve valid text while counting replacement characters in the budget."""
    text = raw[:limit].decode("utf-8", errors="replace")
    encoded = text.encode("utf-8")
    truncated = len(raw) > limit or len(encoded) > limit
    return encoded[:limit].decode("utf-8", errors="ignore"), truncated


def _read_ready(selector, timeout: float) -> None:
    for key, _ in selector.select(timeout):
        chunk = os.read(key.fd, 65536)
        if not chunk:
            selector.unregister(key.fileobj)
            continue
        target, limit = key.data
        target.extend(chunk[:max(0, limit - len(target))])


def _drain(proc: subprocess.Popen[bytes], deadline: float, stdout_limit: int,
           stderr_limit: int | None, timeout_s: float) -> tuple[bytes, bytes]:
    output, error = bytearray(), bytearray()
    assert proc.stdout is not None
    streams = [(proc.stdout, output, stdout_limit)]
    if stderr_limit is not None:
        assert proc.stderr is not None
        streams.append((proc.stderr, error, stderr_limit))
    if os.name == "nt":
        # Windows 匿名管道不能进 select/selectors（非阻塞模式也不支持），
        # 与 supervisor.WindowsGroupOps 同一方案：daemon 读线程 + 带超时
        # 的队列取块，主循环保住 deadline 检查点。
        _drain_windows(streams, deadline, timeout_s)
    else:
        with selectors.DefaultSelector() as selector:
            for stream, target, limit in streams:
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, (target, limit))
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(proc.args, timeout_s)
                _read_ready(selector, remaining)
    proc.wait(timeout=max(0.001, deadline - time.monotonic()))
    return bytes(output), bytes(error)


def _drain_windows(streams, deadline: float, timeout_s: float) -> None:
    results = [bytearray() for _ in streams]
    eofs = [False] * len(streams)

    def reader(index, stream, limit):
        try:
            while True:
                chunk = stream.read(65536)
                if not chunk:
                    break
                results[index].extend(chunk[:max(0, limit - len(results[index]))])
        except Exception:
            pass
        finally:
            eofs[index] = True

    threads = []
    for index, (stream, _, limit) in enumerate(streams):
        thread = threading.Thread(target=reader, args=(index, stream, limit), daemon=True)
        thread.start()
        threads.append(thread)
    while not all(eofs):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired((), timeout_s)
        time.sleep(min(0.05, max(0.001, remaining)))
    for index, (_, target, _) in enumerate(streams):
        target.extend(results[index])


def _reap_group(proc: subprocess.Popen[bytes]) -> None:
    # POSIX：进程组 SIGKILL（覆盖孙进程）；Windows：无进程组概念，直接
    # terminate 杀主进程，孙进程由任务对象自身生命周期约束。
    if os.name == "nt":
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        proc.poll()
    proc.wait(timeout=2)


def run_bounded(argv: Sequence[str], *, cwd: Path, timeout_s: float,
                stdout_limit: int, stderr_limit: int | None = None,
                env: Mapping[str, str] | None = None,
                preexec_fn: Callable[[], None] | None = None) -> subprocess.CompletedProcess[bytes]:
    """Retain at most each limit; drain excess and check the complete exit.

    A missing stderr limit merges stderr into stdout. Callers that need a
    truncation flag request one extra byte. All processes share an owned group
    that is reaped on success, failure, cancellation or timeout. This bounds
    parent memory; child memory/isolation belongs to the caller's launcher.
    """
    limits = (stdout_limit,) if stderr_limit is None else (stdout_limit, stderr_limit)
    if any(type(limit) is not int or limit < 0 for limit in limits):
        raise ValueError("invalid_output_limit")
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ValueError("invalid_process_timeout")
    deadline = time.monotonic() + timeout_s
    # Windows 的 Popen 不接受 preexec_fn/start_new_session（POSIX 专属）。
    posix_only = {} if os.name == "nt" else {
        "start_new_session": True, "preexec_fn": preexec_fn}
    with local_process.popen(list(argv), cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT if stderr_limit is None else subprocess.PIPE,
                          **posix_only) as proc:
        try:
            output, error = _drain(proc, deadline, stdout_limit, stderr_limit, timeout_s)
            return subprocess.CompletedProcess(proc.args, proc.returncode, output, error)
        finally:
            _reap_group(proc)
