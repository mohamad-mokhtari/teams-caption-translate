"""
Entry point.

    CaptionTranslator.exe                 start, show the tray icon
    CaptionTranslator.exe --background    the same, without opening settings (sign-in start)
    CaptionTranslator.exe --selftest      start the translator, check it answers, exit 0/1
                                          (used by CI on the built .exe -- no tray, no screen)
"""
from __future__ import annotations

import logging
import os
import secrets
import sys


def _paths_first() -> None:
    """
    Everything that must happen before the server package is imported.

    The server reads its settings when app.config is first imported, so the
    settings file location has to be in the environment before that.
    """
    from . import store

    store.data_dir().mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MCT_ENV_FILE", str(store.settings_path()))

    # A windowed .exe has no console: stdout/stderr are None, and the first
    # library that prints -- uvicorn does -- crashes. Send both to a log file.
    console = sys.stdout is not None and sys.stderr is not None
    if not console:
        log = open(store.log_path(), "a", buffering=1, encoding="utf-8")   # noqa: SIM115
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log
    handlers: list[logging.Handler] = [logging.FileHandler(store.log_path(), encoding="utf-8")]
    if console:
        handlers.append(logging.StreamHandler())     # visible when run from a terminal
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    # Running from source: make server/app importable. (Frozen, it is bundled.)
    if not getattr(sys, "frozen", False):
        here = os.path.dirname(os.path.abspath(__file__))
        server = os.path.normpath(os.path.join(here, "..", "..", "server"))
        if server not in sys.path:
            sys.path.insert(0, server)


def _message(text: str) -> None:
    """A plain dialog when there is no tray to speak through yet."""
    logging.warning(text)
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, "Caption Translator", 0x40)


def selftest(port: int) -> int:
    """Start the real server in-process, as the .exe would, and check it answers."""
    import httpx

    from . import icon
    from .server import ServerThread
    token = secrets.token_urlsafe(16)
    srv = ServerThread(port, token)
    if not srv.start():
        print("SELFTEST FAIL: server did not start")
        return 1
    try:
        base = f"http://127.0.0.1:{port}"
        checks = {
            "health": httpx.get(f"{base}/health", timeout=5).json().get("status") == "ok",
            "config": "languages" in httpx.get(f"{base}/config", timeout=5).json(),
            "settings page": "Caption Translator" in httpx.get(
                f"{base}/settings?token={token}", timeout=5).text,
            "settings locked": httpx.get(f"{base}/settings/data", timeout=5).status_code == 403,
            "icon": icon.draw(32).size == (32, 32),
        }
        for name, ok in checks.items():
            print(f"SELFTEST {'ok  ' if ok else 'FAIL'} {name}")
        return 0 if all(checks.values()) else 1
    finally:
        srv.stop()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    _paths_first()

    from . import PORT, store
    from .server import ServerThread, ours_is_running, port_in_use

    if "--selftest" in argv:
        port = int(os.environ.get("MCT_SELFTEST_PORT", "8199"))
        return selftest(port)

    # One copy at a time: the extension can only talk to one translator.
    if port_in_use(PORT):
        if ours_is_running(PORT):
            _message("Caption Translator is already running — look for its icon next to the clock.")
        else:
            _message(f"Something else is using port {PORT}, which the browser extension needs. "
                     f"Close it (an old server\\run.bat window?) and start Caption Translator again.")
        return 1

    token = secrets.token_urlsafe(24)
    srv = ServerThread(PORT, token)
    if not srv.start():
        _message(f"The translator could not start. Details are in {store.log_path()}")
        return 1
    logging.info("translator running on 127.0.0.1:%s, settings in %s", PORT, store.settings_path())

    from .tray import Tray
    tray = Tray(token, on_quit=srv.stop)

    def first_moments():
        if store.needs_setup():
            tray.open_settings()                # first run: straight to where the key goes
        elif "--background" not in argv:
            tray.notify("Translating in the background. Right-click the icon for settings.")

    tray.run(first_moments)
    return 0


if __name__ == "__main__":
    sys.exit(main())
