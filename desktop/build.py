"""
Build CaptionTranslator.exe, prove it starts, and zip it with the extension.

    python desktop/build.py            (on Windows; run from anywhere)

The one place the build is defined: build_windows.bat and CI both call this.

Steps
  1. draw the icon
  2. PyInstaller -> dist/CaptionTranslator.exe (one file, no console window)
  3. run the BUILT exe with --selftest -- a frozen build fails on a missing
     hidden import only when it runs, and this is where we find out, not on a
     colleague's laptop
  4. dist/CaptionTranslator-<version>-windows.zip: the .exe, the extension
     folder, and a short how-to
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(HERE), str(ROOT / "server")]

from caption_translator import __version__, icon, store  # noqa: E402

BUILD, DIST = HERE / "build", HERE / "dist"
NAME = "CaptionTranslator"

INSTALL_TXT = f"""Caption Translator {__version__}
==========================

1. Run CaptionTranslator.exe.
   Windows may warn that the app is from an unknown publisher (it is not signed):
   "More info" -> "Run anyway".
   A settings page opens in your browser. Paste your OpenAI API key, press Save,
   then Test. A speech-bubble icon appears next to the clock.

2. Load the browser extension (once):
   edge://extensions  (or chrome://extensions)
   -> turn on Developer mode -> Load unpacked -> choose the "extension" folder
   from this zip.

3. Open Teams in the browser, join a meeting, turn on live captions.
   The panel opens by itself.

Right-click the tray icon for Settings, the transcripts folder, Start with
Windows, and Quit. Settings live in %APPDATA%\\CaptionTranslator\\settings.env.
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
        "--hidden-import", "pystray._win32",
        "--hidden-import", "anyio._backends._asyncio",
    ])
    exe = DIST / (NAME + (".exe" if sys.platform == "win32" else ""))
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
        sys.exit(f"SELFTEST of the built exe FAILED (exit {r.returncode})")
    print("selftest of the built exe: passed")


def package(exe: pathlib.Path) -> pathlib.Path:
    out = DIST / f"{NAME}-{__version__}-windows.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(exe, exe.name)
        for f in sorted((ROOT / "extension").rglob("*")):
            if f.is_file():
                z.write(f, "extension/" + f.relative_to(ROOT / "extension").as_posix())
        z.writestr("HOW-TO-INSTALL.txt", INSTALL_TXT.replace("\n", "\r\n"))
    print(f"packaged {out} ({out.stat().st_size // 1024} KB)")
    return out


def main() -> None:
    if DIST.exists():
        shutil.rmtree(DIST)
    exe = run_pyinstaller()
    if sys.platform == "win32" or os.environ.get("MCT_FORCE_SELFTEST"):
        selftest(exe)
    package(exe)


if __name__ == "__main__":
    main()
