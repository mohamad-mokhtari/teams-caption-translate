"""Open a folder or show a notification the way each system expects."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys


def folder(path) -> None:
    if sys.platform == "win32":
        os.startfile(path)                       # noqa: S606 -- a folder in the user's profile
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def notify(title: str, message: str, icon_path: str = "") -> bool:
    """Linux desktop notification via notify-send, where the tray cannot notify."""
    exe = shutil.which("notify-send")
    if not exe:
        return False
    args = [exe, "--app-name", title]
    if icon_path:
        args += ["--icon", icon_path]
    try:
        subprocess.Popen(args + [title, message], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False
