"""
"Start with Windows", for the current user only.

A value under HKCU\\...\\Run: no administrator rights, no installer, and nothing
for other people on the same machine. Removing the value is the whole undo.
Does nothing (and says so) anywhere other than Windows.
"""
from __future__ import annotations

import sys

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE = "CaptionTranslator"


def supported() -> bool:
    return sys.platform == "win32"


def command() -> str:
    """What Windows should run at sign-in: this .exe, or this script when not frozen."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --background'
    # Running from source: pythonw avoids a console window at sign-in.
    exe = sys.executable.replace("python.exe", "pythonw.exe")
    return f'"{exe}" -m caption_translator --background'


def is_enabled() -> bool:
    if not supported():
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            value, _ = winreg.QueryValueEx(k, VALUE)
            return bool(value)
    except OSError:
        return False


def set_enabled(on: bool) -> None:
    if not supported():
        raise RuntimeError("Start with Windows is only available on Windows")
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, VALUE, 0, winreg.REG_SZ, command())
        else:
            try:
                winreg.DeleteValue(k, VALUE)
            except FileNotFoundError:
                pass
