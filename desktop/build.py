"""
Build Caption Translator for this OS, prove it starts, and package it with the extension.

    python desktop/build.py      Windows -> CaptionTranslator.exe + ...-windows.zip
                                 Linux   -> CaptionTranslator     + ...-linux-x86_64.tar.gz

The one place the build is defined: build_windows.bat and CI both call this.

Steps
  1. draw the icon
  2. PyInstaller -> one file, no console window
  3. run the BUILT program with --selftest -- a frozen build fails on a missing
     hidden import only when it runs, and this is where we find out, not on a
     colleague's laptop. (MCT_SKIP_SELFTEST=1 skips it.)
  4. package: the program, the extension folder, and a short how-to
"""
from __future__ import annotations

import io
import os
import pathlib
import shutil
import subprocess
import sys
import tarfile
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(HERE), str(ROOT / "server")]

from caption_translator import __version__, icon, store  # noqa: E402

BUILD, DIST = HERE / "build", HERE / "dist"
NAME = "CaptionTranslator"
WINDOWS = sys.platform == "win32"

INSTALL_WINDOWS = rf"""Caption Translator {__version__} for Windows
=======================================

1. Run CaptionTranslator.exe.
   It is not code-signed yet, so Windows shows "Windows protected your PC":
   click "More info", then "Run anyway". (Asked once per version.)
   A settings page opens in your browser. Paste your OpenAI API key, press Save,
   then Test. A speech-bubble icon appears next to the clock.

2. Load the browser extension (once):
   edge://extensions  (or chrome://extensions)
   -> turn on Developer mode -> Load unpacked -> choose the "extension" folder
   from this zip.

3. Open Teams in the browser, join a meeting, turn on live captions.
   The panel opens by itself.

Right-click the tray icon for Settings, the transcripts folder, Start with
Windows, and Quit -- all of which are also on the settings page.
Settings live in %APPDATA%\CaptionTranslator\settings.env.
"""

INSTALL_LINUX = f"""Caption Translator {__version__} for Linux
=====================================

1. Run it:
       ./CaptionTranslator
   (If your file manager will not start it: right-click -> Properties -> allow
   executing as a program, or run `chmod +x CaptionTranslator` once.)
   A settings page opens in your browser. Paste your OpenAI API key, press Save,
   then Test. From then on it is also in your applications menu.

2. Load the browser extension (once), in Chrome or Edge:
   chrome://extensions  (or edge://extensions)
   -> turn on Developer mode -> Load unpacked -> choose the "extension" folder.

3. Open Teams in the browser, join a meeting, turn on live captions.
   The panel opens by itself.

The tray icon (next to the clock, on desktops that show tray icons) opens the
settings when clicked. Everything else -- Open transcripts folder, Start when I
log in, Quit -- is on the settings page. No tray icon on your desktop (e.g.
GNOME without the "AppIndicator and KStatusNotifierItem Support" extension)? It
still translates: start Caption Translator again any time to open its settings.

Settings live in ~/.config/caption-translator/settings.env.
Needs glibc 2.35 or newer: Ubuntu 22.04+, Debian 12+, Fedora 36+ and similar.
"""


def run_pyinstaller() -> pathlib.Path:
    import PyInstaller.__main__

    BUILD.mkdir(exist_ok=True)
    ico = BUILD / "icon.ico"
    icon.write_ico(str(ico))
    PyInstaller.__main__.run([
        str(HERE / "run_app.py"),
        "--name", NAME, "--onefile", "--windowed", "--noconfirm", "--clean",
        "--icon", str(ico),
        "--distpath", str(DIST), "--workpath", str(BUILD / "pyi"), "--specpath", str(BUILD),
        "--paths", str(ROOT / "server"), "--paths", str(HERE),
        # Imported by name at runtime, so PyInstaller cannot see them statically.
        "--collect-submodules", "uvicorn",
        "--collect-submodules", "app",
        "--collect-submodules", "caption_translator",
        "--hidden-import", "anyio._backends._asyncio",
        # The tray backend for this OS, also loaded by name. On Linux only the X11
        # backend can be bundled (AppIndicator needs the system's own GTK).
        *(["--hidden-import", "pystray._win32"] if WINDOWS else
          ["--hidden-import", "pystray._xorg", "--hidden-import", "Xlib.ext",
           "--collect-submodules", "Xlib"]),
    ])
    exe = DIST / (NAME + (".exe" if WINDOWS else ""))
    if not exe.exists():
        sys.exit(f"build failed: {exe} not produced")
    return exe


def selftest(exe: pathlib.Path) -> None:
    """Run the built program. Windowed, it has no console: read its log instead."""
    log = store.log_path()
    if log.exists():
        log.unlink()
    r = subprocess.run([str(exe), "--selftest"], timeout=120)
    print(log.read_text(encoding="utf-8") if log.exists() else "(no log written)")
    if r.returncode != 0:
        sys.exit(f"SELFTEST of the built program FAILED (exit {r.returncode})")
    print("selftest of the built program: passed")


def _extension_files():
    for f in sorted((ROOT / "extension").rglob("*")):
        if f.is_file():
            yield f, "extension/" + f.relative_to(ROOT / "extension").as_posix()


def package(exe: pathlib.Path) -> pathlib.Path:
    if WINDOWS:
        out = DIST / f"{NAME}-{__version__}-windows.zip"
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(exe, exe.name)
            for f, name in _extension_files():
                z.write(f, name)
            z.writestr("HOW-TO-INSTALL.txt", INSTALL_WINDOWS.replace("\n", "\r\n"))
    else:
        # A tarball keeps the executable bit, so it runs straight after unpacking.
        out = DIST / f"{NAME}-{__version__}-linux-x86_64.tar.gz"
        top = f"{NAME}-{__version__}"
        with tarfile.open(out, "w:gz") as t:
            t.add(exe, f"{top}/{exe.name}")
            for f, name in _extension_files():
                t.add(f, f"{top}/{name}")
            data = INSTALL_LINUX.encode()
            info = tarfile.TarInfo(f"{top}/HOW-TO-INSTALL.txt")
            info.size, info.mode = len(data), 0o644
            t.addfile(info, io.BytesIO(data))
    print(f"packaged {out} ({out.stat().st_size // 1024} KB)")
    return out


def main() -> None:
    if DIST.exists():
        shutil.rmtree(DIST)
    exe = run_pyinstaller()
    if os.environ.get("MCT_SKIP_SELFTEST") != "1":
        selftest(exe)
    package(exe)


if __name__ == "__main__":
    main()
