"""
The translation companion, running inside the app on a background thread.

It is the same FastAPI app as `server/run.sh` starts -- imported, not copied --
plus the settings page. The tray owns the main thread; this thread owns the
server, and stopping it lets in-flight requests finish.
"""
from __future__ import annotations

import socket
import threading
import time

import httpx


def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex((host, port)) == 0


def ours_is_running(port: int) -> bool:
    """Is the thing on that port a Caption Translator, rather than something else?"""
    try:
        r = httpx.get(f"http://127.0.0.1:{port}/health", timeout=1.0)
        return r.status_code == 200 and r.json().get("status") == "ok"
    except (httpx.HTTPError, ValueError):
        return False


class ServerThread:
    def __init__(self, port: int, token: str, on_quit=None):
        import uvicorn

        from app.main import app

        from .settings_page import mount

        mount(app, token, on_quit)
        # log_config=None: uvicorn's default logging writes to a console that a
        # windowed .exe does not have. Logging goes where main.py sends it.
        self._server = uvicorn.Server(uvicorn.Config(
            app, host="127.0.0.1", port=port, log_config=None, access_log=False))
        self._thread = threading.Thread(target=self._server.run, name="translator", daemon=True)
        self.port = port

    def start(self, wait: float = 15.0) -> bool:
        self._thread.start()
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            if self._server.started:
                return True
            if not self._thread.is_alive():
                return False
            time.sleep(0.05)
        return False

    def stop(self, wait: float = 5.0) -> None:
        self._server.should_exit = True
        self._thread.join(wait)
