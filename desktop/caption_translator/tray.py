"""
The tray icon and its menu. The only part of the app a person sees day to day.

Left-click opens the settings. The icon's colour says the state at a glance --
green translating, amber needs a key, red not running -- and the tooltip says it
in words.

Windows has the full menu. On Linux the backend that can be bundled into one
file (X11 system tray) supports a click and nothing else -- no menu, no
notifications -- so a click opens Settings, every menu item is ALSO on the
settings page, and notifications go through notify-send. Run from source on a
desktop with AppIndicator, the menu appears there too.
"""
from __future__ import annotations

import threading
import webbrowser

from . import APP_NAME, PORT, __version__, autostart, icon, platform_open, store


class Tray:
    def __init__(self, token: str, on_quit):
        import pystray

        self._pystray = pystray
        self._token = token
        self._on_quit = on_quit
        self.icon = pystray.Icon(
            "caption-translator", icon.draw(64, self._state()), self._tooltip(),
            menu=pystray.Menu(
                pystray.MenuItem("Settings…", lambda: self.open_settings(), default=True),
                pystray.MenuItem("Open transcripts folder", lambda: self.open_transcripts()),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(autostart.label(), lambda: self._toggle_autostart(),
                                 checked=lambda _: autostart.is_enabled(),
                                 visible=autostart.supported()),
                pystray.MenuItem(lambda _: self._status_line(), None, enabled=False),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Quit", lambda: self.quit()),
            ))
        self._refresh_timer = None
        # Does this system's tray have a menu? If not, the settings page has it all.
        self.has_menu = bool(getattr(self.icon, "HAS_MENU", True))

    # -- state ------------------------------------------------------------
    def _state(self) -> str:
        return "setup" if store.needs_setup() else "ok"

    def _status_line(self) -> str:
        from app.config import settings
        if store.needs_setup():
            return "Needs an API key — open Settings"
        model = {"openai": settings.openai_model, "anthropic": settings.anthropic_model,
                 "ollama": settings.ollama_model}.get(settings.provider, "")
        return f"Translating with {model}"

    def _tooltip(self) -> str:
        return f"{APP_NAME} {__version__} — {self._status_line()}"[:127]   # Windows limit

    def refresh(self) -> None:
        """Repaint icon and tooltip; cheap, so it simply runs every few seconds."""
        try:
            self.icon.icon = icon.draw(64, self._state())
            self.icon.title = self._tooltip()
            self.icon.update_menu()
        except Exception:                      # a tray that cannot repaint must not crash the app
            pass
        self._refresh_timer = threading.Timer(5.0, self.refresh)
        self._refresh_timer.daemon = True
        self._refresh_timer.start()

    # -- actions ----------------------------------------------------------
    def open_settings(self) -> None:
        webbrowser.open(f"http://127.0.0.1:{PORT}/settings?token={self._token}")

    def open_transcripts(self) -> None:
        from app.transcript import directory
        path = directory()
        path.mkdir(parents=True, exist_ok=True)
        platform_open.folder(path)

    def _toggle_autostart(self) -> None:
        autostart.set_enabled(not autostart.is_enabled(), icon.ensure_png(store.data_dir()))
        self.icon.update_menu()

    def notify(self, message: str) -> None:
        if getattr(self.icon, "HAS_NOTIFICATION", False):
            try:
                self.icon.notify(message, APP_NAME)
                return
            except Exception:
                pass
        platform_open.notify(APP_NAME, message, icon.ensure_png(store.data_dir()))

    def quit(self) -> None:
        if self._refresh_timer:
            self._refresh_timer.cancel()
        self.icon.stop()
        self._on_quit()

    def run(self, setup) -> None:
        """Blocks until Quit. `setup` runs once the icon is visible."""
        def _ready(icon_):
            icon_.visible = True
            self.refresh()
            setup()
        self.icon.run(setup=_ready)
