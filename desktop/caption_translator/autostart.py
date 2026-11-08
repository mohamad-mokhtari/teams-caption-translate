"""
Start at login, and (Linux) an entry in the applications menu.

  Windows  a value under HKCU\\...\\Run -- per user, no admin, no installer
  Linux    ~/.config/autostart/caption-translator.desktop -- the XDG standard that
           GNOME, KDE, Xfce, Cinnamon and MATE all read

Either way, removing the one entry is the whole undo.
"""
from __future__ import annotations

import os
import pathlib
import shlex
import sys

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE = "CaptionTranslator"
DESKTOP_ID = "caption-translator.desktop"


def supported() -> bool:
    return sys.platform == "win32" or sys.platform.startswith("linux")


def label() -> str:
    return "Start with Windows" if sys.platform == "win32" else "Start when I log in"


def _argv() -> list[str]:
    """How to start this app again: the frozen binary, or the script from source."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    script = str(pathlib.Path(__file__).resolve().parent.parent / "run_app.py")
    exe = sys.executable
    if sys.platform == "win32":
        exe = exe.replace("python.exe", "pythonw.exe")     # no console window at sign-in
    return [exe, script]


def command() -> str:
    """The command line run at login (always --background: no settings tab at sign-in)."""
    argv = _argv() + ["--background"]
    if sys.platform == "win32":
        return " ".join(f'"{a}"' if " " in a or a.endswith(".exe") else a for a in argv)
    return " ".join(shlex.quote(a) for a in argv)


# ---------------------------------------------------------------- Linux files

def _xdg(var: str, default: str) -> pathlib.Path:
    return pathlib.Path(os.environ.get(var) or (pathlib.Path.home() / default))


def autostart_file() -> pathlib.Path:
    return _xdg("XDG_CONFIG_HOME", ".config") / "autostart" / DESKTOP_ID


def menu_file() -> pathlib.Path:
    return _xdg("XDG_DATA_HOME", ".local/share") / "applications" / DESKTOP_ID


def desktop_entry(exec_line: str, icon_path: str, autostart: bool = False) -> str:
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        "Name=Caption Translator",
        "Comment=Live translation of meeting captions",
        f"Exec={exec_line}",
        f"Icon={icon_path}",
        "Terminal=false",
        "Categories=Office;AudioVideo;",
        "StartupNotify=false",
    ]
    if autostart:
        lines += ["X-GNOME-Autostart-enabled=true", "X-KDE-autostart-after=panel"]
    return "\n".join(lines) + "\n"


def install_menu_entry(icon_path: str) -> pathlib.Path | None:
    """
    Linux only: put Caption Translator in the applications menu, pointing at
    wherever this binary is now. Rewritten when the binary has moved.
    """
    if not sys.platform.startswith("linux"):
        return None
    path = menu_file()
    text = desktop_entry(" ".join(shlex.quote(a) for a in _argv()), icon_path)
    if not path.exists() or path.read_text(encoding="utf-8") != text:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------- on / off

def is_enabled() -> bool:
    if sys.platform == "win32":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
                value, _ = winreg.QueryValueEx(k, VALUE)
                return bool(value)
        except OSError:
            return False
    if sys.platform.startswith("linux"):
        return autostart_file().exists()
    return False


def set_enabled(on: bool, icon_path: str = "") -> None:
    if sys.platform == "win32":
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if on:
                winreg.SetValueEx(k, VALUE, 0, winreg.REG_SZ, command())
            else:
                try:
                    winreg.DeleteValue(k, VALUE)
                except FileNotFoundError:
                    pass
        return
    if sys.platform.startswith("linux"):
        f = autostart_file()
        if on:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(desktop_entry(command(), icon_path, autostart=True), encoding="utf-8")
        elif f.exists():
            f.unlink()
        return
    raise RuntimeError("Starting at login is not supported on this system")
