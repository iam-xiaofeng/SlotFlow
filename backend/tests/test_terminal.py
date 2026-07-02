"""Tests for the user-operated host terminal boundary."""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.terminal.routes import (
    bounded_int,
    handle_client_message,
    resolve_terminal_cwd,
    shell_command,
)


def test_terminal_helpers_resolve_cwd_and_shell_command(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SLOTFLOW_TERMINAL_CWD", str(tmp_path))

    assert resolve_terminal_cwd() == tmp_path.resolve(strict=False)
    assert shell_command("/bin/bash") == ["/bin/bash", "-l"]
    assert shell_command("/bin/sh") == ["/bin/sh"]
    assert bounded_int(5, minimum=8, maximum=20) == 8
    assert bounded_int(25, minimum=8, maximum=20) == 20
    assert bounded_int("25", minimum=8, maximum=20) is None


def test_terminal_input_message_writes_to_fd() -> None:
    read_fd, write_fd = os.pipe()
    try:
        handle_client_message(write_fd, json.dumps({"type": "input", "data": "echo ok\n"}))
        os.close(write_fd)
        write_fd = -1
        assert os.read(read_fd, 128) == b"echo ok\n"
    finally:
        os.close(read_fd)
        if write_fd != -1:
            os.close(write_fd)


def test_terminal_websocket_sends_ready_event(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SLOTFLOW_TERMINAL_CWD", str(tmp_path))
    monkeypatch.setenv("SLOTFLOW_TERMINAL_SHELL", "/bin/sh")
    client = TestClient(create_app())

    with client.websocket_connect("/api/terminal/ws") as websocket:
        payload = json.loads(websocket.receive_text())
        assert payload["type"] == "ready"
        assert payload["cwd"] == str(tmp_path.resolve(strict=False))
        assert payload["shell"] == "/bin/sh"
