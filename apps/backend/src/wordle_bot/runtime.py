"""Supervise web, worker and platform scheduler in one container."""

import os
import signal
import subprocess
import sys
import threading
from collections.abc import Sequence
from contextlib import suppress
from types import FrameType


def supervise(commands: Sequence[Sequence[str]], *, shutdown_timeout: float = 30) -> int:
    stopping = threading.Event()
    children: list[subprocess.Popen[bytes]] = []

    def stop(signum: int, frame: FrameType | None) -> None:
        stopping.set()

    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGTERM, signal.SIGINT)}
    result = 0
    try:
        for command in commands:
            children.append(subprocess.Popen(command, start_new_session=True))
        while not stopping.wait(0.2):
            if any(child.poll() is not None for child in children):
                # Even a clean child exit is unexpected while the service is running.
                result = 1
                break
    finally:
        for child in children:
            if child.poll() is None:
                with suppress(ProcessLookupError):
                    os.killpg(child.pid, signal.SIGTERM)
        for child in children:
            try:
                child.wait(timeout=shutdown_timeout)
            except subprocess.TimeoutExpired:
                with suppress(ProcessLookupError):
                    os.killpg(child.pid, signal.SIGKILL)
                child.wait()
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    return result


def combined_commands(*, admin_panel_enabled: bool = False) -> list[list[str]]:
    commands = [
        [sys.executable, "-m", "wordle_bot.cli", "serve", "--skip-migrate"],
        [sys.executable, "-m", "wordle_bot.cli", "worker", "--skip-migrate"],
    ]
    if admin_panel_enabled:
        commands.append([sys.executable, "-m", "wordle_bot.cli", "scheduler", "--skip-migrate"])
    return commands
