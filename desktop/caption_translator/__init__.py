"""Caption Translator for Windows: the translation companion as a tray app."""

# Kept equal to extension/manifest.json -- tests/test_desktop.py checks it. The
# extension and the app talk to each other, so they carry one version number.
__version__ = "1.4.0"

APP_NAME = "Caption Translator"
PORT = 8100            # the extension calls http://127.0.0.1:8100; it is not configurable there
