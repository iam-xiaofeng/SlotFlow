"""User-operated host terminal WebSocket routes.

This terminal is intentionally separate from agent tools. It gives the human user an
interactive host shell for local setup tasks, while model-driven code execution remains
restricted to ``sandbox_exec``.
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import pty
import select
import shutil
import signal
import struct
import subprocess
import termios
import threading
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter(tags=["Terminal"])

DEFAULT_COLUMNS = 100
DEFAULT_ROWS = 30

TerminalMessageType = Literal["ready", "output", "exit"]


@router.websocket("/api/terminal/ws")
async def terminal_websocket(websocket: WebSocket) -> None:
    """Attach a browser WebSocket to a short-lived host PTY shell."""

    await websocket.accept()
    master_fd, slave_fd = pty.openpty()
    set_pty_size(master_fd, rows=DEFAULT_ROWS, columns=DEFAULT_COLUMNS)

    shell = resolve_shell()
    cwd = resolve_terminal_cwd()
    process = subprocess.Popen(
        shell_command(shell),
        cwd=cwd,
        env=terminal_environment(),
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        start_new_session=True,
        close_fds=True,
    )
    os.close(slave_fd)

    loop = asyncio.get_running_loop()
    output_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    stop_event = threading.Event()
    reader = threading.Thread(
        target=read_pty_output,
        args=(loop, output_queue, master_fd, process, stop_event),
        daemon=True,
    )
    reader.start()

    await send_terminal_json(
        websocket,
        {
            "type": "ready",
            "cwd": str(cwd),
            "shell": shell,
            "pid": process.pid,
        },
    )

    sender = asyncio.create_task(send_output(websocket, output_queue))
    try:
        while True:
            message = await websocket.receive_text()
            handle_client_message(master_fd, message)
    except WebSocketDisconnect:
        pass
    finally:
        sender.cancel()
        stop_event.set()
        terminate_process(process)
        close_fd(master_fd)
        try:
            await sender
        except (asyncio.CancelledError, RuntimeError):
            pass


def resolve_terminal_cwd() -> Path:
    raw = os.environ.get("SLOTFLOW_TERMINAL_CWD")
    if raw:
        candidate = Path(raw).expanduser().resolve(strict=False)
        if candidate.is_dir():
            return candidate
    return Path.cwd().resolve(strict=False)


def resolve_shell() -> str:
    configured = os.environ.get("SLOTFLOW_TERMINAL_SHELL") or os.environ.get("SHELL")
    if configured and Path(configured).exists():
        return configured
    return shutil.which("bash") or shutil.which("sh") or "/bin/sh"


def shell_command(shell: str) -> list[str]:
    shell_name = Path(shell).name
    if shell_name in {"bash", "zsh", "fish"}:
        return [shell, "-l"]
    return [shell]


def terminal_environment() -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault("TERM", "xterm-256color")
    env.setdefault("COLORTERM", "truecolor")
    return env


def read_pty_output(
    loop: asyncio.AbstractEventLoop,
    output_queue: asyncio.Queue[dict[str, Any] | None],
    master_fd: int,
    process: subprocess.Popen[Any],
    stop_event: threading.Event,
) -> None:
    while not stop_event.is_set():
        try:
            readable, _, _ = select.select([master_fd], [], [], 0.1)
            if not readable:
                if process.poll() is not None:
                    break
                continue
            chunk = os.read(master_fd, 4096)
        except OSError:
            break
        if not chunk:
            break
        output = chunk.decode(errors="replace")
        loop.call_soon_threadsafe(
            output_queue.put_nowait,
            {"type": "output", "data": output},
        )
    loop.call_soon_threadsafe(
        output_queue.put_nowait,
        {"type": "exit", "exit_code": process.poll()},
    )


async def send_output(
    websocket: WebSocket,
    output_queue: asyncio.Queue[dict[str, Any] | None],
) -> None:
    while True:
        payload = await output_queue.get()
        if payload is None:
            return
        await send_terminal_json(websocket, payload)
        if payload.get("type") == "exit":
            return


async def send_terminal_json(websocket: WebSocket, payload: dict[str, Any]) -> None:
    await websocket.send_text(json.dumps(payload, ensure_ascii=False))


def handle_client_message(master_fd: int, message: str) -> None:
    try:
        payload = json.loads(message)
    except json.JSONDecodeError:
        payload = {"type": "input", "data": message}
    if not isinstance(payload, dict):
        return

    message_type = payload.get("type")
    if message_type == "input":
        data = payload.get("data")
        if isinstance(data, str):
            os.write(master_fd, data.encode())
        return

    if message_type == "resize":
        rows = bounded_int(payload.get("rows"), minimum=8, maximum=200)
        columns = bounded_int(payload.get("columns"), minimum=20, maximum=400)
        if rows is not None and columns is not None:
            set_pty_size(master_fd, rows=rows, columns=columns)


def bounded_int(value: Any, *, minimum: int, maximum: int) -> int | None:
    if not isinstance(value, int):
        return None
    return min(max(value, minimum), maximum)


def set_pty_size(fd: int, *, rows: int, columns: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))


def terminate_process(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGHUP)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def close_fd(fd: int) -> None:
    try:
        os.close(fd)
    except OSError:
        pass
