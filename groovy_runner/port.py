"""Pick the port the app starts on: `python -m groovy_runner.port` prints it.

The app binds to localhost only (.streamlit/config.toml). On Windows that bind
succeeds even when another app (e.g. another Streamlit tool on its default
settings) already listens on the same port on *all* interfaces, so
Streamlit's own "port in use → try the next one" check never fires and both
apps end up on 8501. This checks for anything already accepting connections
on the port, or holding it, before Streamlit starts, and the start scripts
pass the result as --server.port.

Preferred port: GROOVY_RUNNER_PORT in the environment or .env (default 8501).
"""

from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

DEFAULT_PORT = 8501
MAX_TRIES = 50


def _preferred() -> int:
    value = os.environ.get("GROOVY_RUNNER_PORT", "")
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if not value and env_file.exists():
        for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            key, _, val = line.partition("=")
            if key.strip() == "GROOVY_RUNNER_PORT":
                value = val.split("#", 1)[0].strip().strip('"').strip("'")
    try:
        port = int(value) if value else DEFAULT_PORT
    except ValueError:
        port = DEFAULT_PORT
    return port if 1 <= port <= 65535 else DEFAULT_PORT


def is_free(port: int) -> bool:
    # 1) Something already answering on loopback (covers apps listening on all
    #    interfaces, IPv4 or IPv6) -> taken.
    for family, host in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as probe:
                probe.settimeout(0.3)
                if probe.connect_ex((host, port)) == 0:
                    return False
        except OSError:
            pass  # e.g. no IPv6 on this machine
    # 2) And we can actually bind it the way the app will (localhost).
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as test:
            if os.name == "nt":
                test.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            test.bind(("127.0.0.1", port))
    except OSError:
        return False
    return True


def find_port(start: int | None = None) -> int:
    start = start or _preferred()
    for port in range(start, min(start + MAX_TRIES, 65536)):
        if is_free(port):
            return port
    raise SystemExit(f"No free port found between {start} and {start + MAX_TRIES - 1}")


if __name__ == "__main__":
    print(find_port())
    sys.exit(0)
