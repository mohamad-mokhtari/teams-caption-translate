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
    """A plain dialog (Windows) or notification (Linux) when there is no tray yet."""
    logging.warning(text)
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, "Caption Translator", 0x40)
    else:
        from . import platform_open
        platform_open.notify("Caption Translator", text)


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
        # The tray library loads its backend by name at runtime -- exactly what a
        # frozen build loses without noticing. Checked wherever there is a screen
        # (always on Windows; on Linux under a display, e.g. Xvfb in CI).
        if sys.platform == "win32" or os.environ.get("DISPLAY"):
            try:
                import pystray
                backend = pystray.Icon.__module__
                print(f"SELFTEST info tray backend: {backend}")
                checks["tray backend"] = backend in ("pystray._win32", "pystray._xorg",
                                                     "pystray._appindicator", "pystray._gtk")
            except Exception as e:                     # noqa: BLE001 -- report, do not crash
                print(f"SELFTEST info tray backend failed to load: {e!r}")
                checks["tray backend"] = False
        else:
            print("SELFTEST skip tray backend (no display)")
        for name, ok in checks.items():
            print(f"SELFTEST {'ok  ' if ok else 'FAIL'} {name}")
        return 0 if all(checks.values()) else 1
    finally:
        srv.stop()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    _paths_first()

    import threading
    import webbrowser

    from . import PORT, autostart, icon, platform_open, store
    from .server import ServerThread, ours_is_running, port_in_use

    if "--selftest" in argv:
        port = int(os.environ.get("MCT_SELFTEST_PORT", "8199"))
        return selftest(port)

    # One copy at a time: the extension can only talk to one translator. Starting
    # it again is how a person finds its settings when there is no tray menu --
    # so a second start opens the running one's settings rather than complaining.
    if port_in_use(PORT):
        if ours_is_running(PORT):
            inst = store.read_instance()
            if inst and inst.get("token"):
                webbrowser.open(f"http://127.0.0.1:{PORT}/settings?token={inst['token']}")
                return 0
            _message("Caption Translator is already running \u2014 look for its icon next to the clock.")
        else:
            _message(f"Something else is using port {PORT}, which the browser extension needs. "
                     f"Close it (an old server/run.sh or run.bat window?) and start Caption Translator again.")
        return 1

    token = secrets.token_urlsafe(24)
    done = threading.Event()
    holder: dict = {}

    def stop_server():
        srv.stop()
        store.clear_instance()
        done.set()

    def quit_all():                        # from the settings page's Quit
        tray_ = holder.get("tray")
        if tray_ is not None:
            tray_.quit()                   # stops the icon, then stop_server
        else:
            stop_server()

    srv = ServerThread(PORT, token, on_quit=quit_all)
    if not srv.start():
        _message(f"The translator could not start. Details are in {store.log_path()}")
        return 1
    store.write_instance(PORT, token)
    logging.info("translator running on 127.0.0.1:%s, settings in %s", PORT, store.settings_path())

    png = icon.ensure_png(store.data_dir())
    autostart.install_menu_entry(png)      # Linux: in the applications menu, where the binary is now
    if autostart.is_enabled():
        try:
            autostart.set_enabled(True, png)   # follow the binary if it was moved
        except OSError:
            pass
    settings_url = f"http://127.0.0.1:{PORT}/settings?token={token}"

    try:
        from .tray import Tray
        tray = Tray(token, on_quit=stop_server)
        holder["tray"] = tray
    except Exception as e:                 # no display, no tray support: keep translating anyway
        logging.warning("no tray icon available: %s", e)
        tray = None

    def first_moments():
        if store.needs_setup():
            webbrowser.open(settings_url)  # first run: straight to where the key goes
        elif "--background" not in argv:
            how = ("Right-click the icon for settings." if tray.has_menu else
                   "Click the icon for settings \u2014 or start Caption Translator again.")
            tray.notify(f"Translating in the background. {how}")

    if tray is not None:
        try:
            tray.run(first_moments)        # blocks until Quit
            return 0
        except Exception as e:
            logging.warning("tray icon failed: %s", e)
            holder.pop("tray", None)

    # No tray. The translator runs regardless; Settings (with Quit) is one more
    # start of the app away, and is opened now unless this is a sign-in start.
    if "--background" not in argv or store.needs_setup():
        webbrowser.open(settings_url)
    platform_open.notify("Caption Translator", "Translating in the background. Start Caption "
                         "Translator again any time to open its settings.", png)
    try:
        done.wait()
    except KeyboardInterrupt:
        stop_server()
    return 0


if __name__ == "__main__":
    sys.exit(main())
