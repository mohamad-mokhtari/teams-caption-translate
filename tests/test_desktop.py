"""
Tests for the desktop (tray) app: everything that does not need a screen.

    server/.venv/bin/python tests/test_desktop.py

Needs pillow (the icon). The tray itself (pystray) is not imported here -- it
needs a display -- and is exercised by the self-test of the built .exe in CI.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
TMP = pathlib.Path(tempfile.mkdtemp(prefix="mct-desktop-"))
os.environ["XDG_CONFIG_HOME"] = str(TMP)          # settings go here, never the real profile
os.environ["XDG_DATA_HOME"] = str(TMP / "share")  # and menu entries here
os.environ.pop("APPDATA", None)
sys.path[:0] = [str(ROOT / "desktop"), str(ROOT / "server")]

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import main as server_main, providers, translate  # noqa: E402
from app.config import settings  # noqa: E402
from caption_translator import __version__, autostart, icon, platform_open, settings_page, store  # noqa: E402
from caption_translator.main import selftest  # noqa: E402
from caption_translator.server import ServerThread, ours_is_running, port_in_use  # noqa: E402

fails: list[str] = []
ORIGINAL = {k: getattr(settings, k) for k in store.FIELDS}


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: {got!r}" + ("" if ok else f"  (wanted {want!r})"))
    if not ok:
        fails.append(label)


def restore():
    for k, v in ORIGINAL.items():
        setattr(settings, k, v)


# ---------------------------------------------------------------------------

def test_version():
    print("Desktop: one version number with the extension")
    manifest = json.loads((ROOT / "extension" / "manifest.json").read_text())
    check("  matches manifest.json", __version__, manifest["version"])


def test_store_file():
    print("Desktop: the settings file keeps hand edits, comments and unknown keys")
    p = TMP / "edit.env"
    p.write_text("# my notes\nOPENAI_MODEL=gpt-4o-mini\nCONTEXT_SEGMENTS=12\nGLOSSARY=\"a,b\"\n")
    store.write(p, {"openai_model": "gpt-6-luna", "target_lang": "fa"})
    text = p.read_text()
    check("  comment kept", "# my notes" in text, True)
    check("  unknown key kept", "CONTEXT_SEGMENTS=12" in text, True)
    check("  updated in place, not duplicated", text.count("OPENAI_MODEL="), 1)
    check("  new key appended", "TARGET_LANG=fa" in text, True)
    check("  read back", store.read(p)["openai_model"], "gpt-6-luna")
    check("  quotes stripped on read", store.read(p)["glossary"], "a,b")
    store.write(p, {"glossary": "x\ny"})
    check("  a value cannot break onto a second line", store.read(p)["glossary"], "x y")
    check("  no temp file left behind", list(TMP.glob("*.tmp")), [])

    print("Desktop: what the page sends is validated")
    check("  empty key means keep the saved one", "openai_api_key" in store.clean({"openai_api_key": ""}), False)
    check("  booleans", store.clean({"transcript_enabled": "on"})["transcript_enabled"], True)
    check("  unknown fields dropped", store.clean({"host": "0.0.0.0"}), {})
    for bad in ({"provider": "evil"}, {"openai_model": "gpt 4; rm -rf"}, {"target_lang": "<script>"}):
        try:
            store.clean(bad); check(f"  rejects {bad}", False, True)
        except ValueError:
            check(f"  rejects {list(bad.values())[0]!r}", True, True)
    check("  a key is recognisable, never usable", store.mask("sk-proj-abcdefghijklmnop1234"), "sk-…1234")


def test_apply_is_live():
    print("Desktop: a saved model is used by the very next caption")
    bodies = []
    def handler(req):
        bodies.append(json.loads(req.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
    settings.provider, settings.openai_api_key = "openai", "sk-test"
    providers._client = httpx.Client(transport=httpx.MockTransport(handler))
    translate._cache.put(("hello", "fa", "openai"), "stale answer from the old model")
    store.apply({"openai_model": "gpt-6-luna"})
    check("  cache emptied", translate._cache.get_or_none(("hello", "fa", "openai")), None)
    check("  connection pool replaced", providers._client, None)
    providers._client = httpx.Client(transport=httpx.MockTransport(handler))
    providers.call("sys", "x")
    check("  next request uses the new model", bodies[-1]["model"], "gpt-6-luna")
    store.apply({"target_lang": "ja"})
    check("  language name follows the code", settings.target_lang_name, "Japanese")
    restore()


def client(token="tok-123"):
    settings_page.mount(server_main.app, token)
    return TestClient(server_main.app, base_url="http://127.0.0.1:8100")


def test_settings_page():
    print("Desktop: the settings page is locked to the tray's link")
    c = client()
    check("  no token: refused", c.get("/settings/data").status_code, 403)
    check("  wrong token: refused", c.get("/settings/data", headers={"x-mct-token": "nope"}).status_code, 403)
    check("  page without token explains", "tray icon" in c.get("/settings").text, True)
    H = {"x-mct-token": "tok-123"}
    check("  another website: refused",
          c.post("/settings", headers={**H, "origin": "https://evil.example"}, json={}).status_code, 403)
    check("  another host name (DNS rebinding): refused",
          c.get("/settings/data", headers={**H, "host": "evil.example:8100"}).status_code, 403)
    check("  right token: page served", "Caption Translator" in c.get("/settings?token=tok-123").text, True)

    print("Desktop: the saved key never reaches the page")
    settings.openai_api_key = "sk-proj-SECRETSECRETSECRET9876"
    raw = c.get("/settings/data", headers=H).text
    check("  not in the data", "SECRETSECRET" in raw, False)
    check("  but recognisable", json.loads(raw)["values"]["openai_api_key"], "sk-…9876")

    print("Desktop: saving writes the file and takes effect at once")
    r = c.post("/settings", headers={**H, "origin": "http://127.0.0.1:8100"}, json={"values": {
        "openai_api_key": "", "openai_model": "gpt-5.6-luna", "glossary": "AIE,RE,merge request",
        "target_lang": "fa", "provider": "openai"}})
    check("  saved", r.status_code, 200)
    saved = store.read(store.settings_path())
    check("  written to the profile file", (saved.get("openai_model"), saved.get("glossary")),
          ("gpt-5.6-luna", "AIE,RE,merge request"))
    check("  empty key field kept the saved key", settings.openai_api_key, "sk-proj-SECRETSECRETSECRET9876")
    check("  key not written when unchanged", "openai_api_key" in saved, False)
    check("  live", settings.openai_model, "gpt-5.6-luna")
    check("  bad input is a 400, not a crash",
          c.post("/settings", headers=H, json={"values": {"provider": "evil"}}).status_code, 400)

    print("Desktop: Test translates one sentence with the saved settings")
    providers._BACKENDS["openai"] = lambda system, user: "سلام به همه"
    d = c.post("/settings/test", headers=H).json()
    check("  works", (d["ok"], d["translation"], d["model"]), (True, "سلام به همه", "gpt-5.6-luna"))
    def boom(system, user):
        raise providers.ProviderError("OpenAI 401: invalid key")
    providers._BACKENDS["openai"] = boom
    check("  and says why when it does not", c.post("/settings/test", headers=H).json()["error"],
          "OpenAI 401: invalid key")
    print("Desktop: the tray menu's actions are on the page too (Linux trays have no menu)")
    quits, opened = [], []
    settings_page.mount(server_main.app, "tok-123", on_quit=lambda: quits.append(1))
    platform_open.folder = lambda path: opened.append(str(path))
    r = c.post("/settings/open-transcripts", headers=H).json()
    check("  opens the transcripts folder", opened == [r["path"]], True)
    check("  quit needs the token", c.post("/settings/quit").status_code, 403)
    check("  quit", c.post("/settings/quit", headers=H).json(), {"ok": True})
    import time
    time.sleep(0.6)                           # it lets the response go out first
    check("  and the app is told to stop", quits, [1])
    check("  start-at-login named for the system",
          c.get("/settings/data", headers=H).json()["autostart"]["label"], autostart.label())
    restore()


def test_setup_and_autostart():
    print("Desktop: first run goes straight to the key")
    settings.provider, settings.openai_api_key = "openai", ""
    check("  no key: needs setup", store.needs_setup(), True)
    settings.provider = "ollama"
    check("  local model: no key needed", store.needs_setup(), False)
    restore()

    print("Desktop: start at login, on Windows and Linux")
    check("  supported here", autostart.supported(), sys.platform == "win32" or sys.platform.startswith("linux"))
    check("  labelled for the system", autostart.label(),
          "Start with Windows" if sys.platform == "win32" else "Start when I log in")
    check("  starts in the background at sign-in", autostart.command().endswith("--background"), True)
    check("  from source: the script by absolute path, not -m (no working directory at login)",
          "run_app.py" in autostart.command(), True)
    if sys.platform.startswith("linux"):
        png = icon.ensure_png(store.data_dir())
        check("  off to begin with", autostart.is_enabled(), False)
        autostart.set_enabled(True, png)
        f = autostart.autostart_file()
        text = f.read_text()
        check("  XDG autostart entry written", f.parent.name == "autostart" and f.exists(), True)
        check("  runs in the background, with the icon",
              ("--background" in text, f"Icon={png}" in text), (True, True))
        check("  reported on", autostart.is_enabled(), True)
        autostart.set_enabled(False)
        check("  and off again: the file is the whole undo", f.exists(), False)

        print("Desktop: Linux gets an applications-menu entry that follows the binary")
        m = autostart.install_menu_entry(png)
        check("  written", m.exists() and "Exec=" in m.read_text(), True)
        before = m.read_text()
        m.write_text(before.replace("run_app.py", "moved/run_app.py"))
        autostart.install_menu_entry(png)
        check("  rewritten when the binary has moved", m.read_text(), before)

    print("Desktop: a second start can find the running one, and nobody else can")
    store.write_instance(8100, "tok-xyz")
    check("  read back", store.read_instance()["token"], "tok-xyz")
    if os.name == "posix":
        check("  readable only by this user", oct(store.instance_path().stat().st_mode & 0o777), "0o600")
    store.clear_instance()
    check("  removed on quit", store.read_instance(), None)

    print("Desktop: the icon, in every state")
    check("  sizes", [icon.draw(n).size for n in (16, 32, 64)], [(16, 16), (32, 32), (64, 64)])
    ico = TMP / "icon.ico"
    icon.write_ico(str(ico))
    check("  .ico written", ico.read_bytes()[:4], b"\x00\x00\x01\x00")


def test_server_thread():
    print("Desktop: the translator runs on a background thread and stops cleanly")
    port = 8193
    check("  port free before", port_in_use(port), False)
    srv = ServerThread(port, "t")
    check("  started", srv.start(), True)
    check("  answering", ours_is_running(port), True)
    srv.stop()
    check("  stopped", port_in_use(port), False)

    print("Desktop: the self-test the built .exe runs in CI")
    check("  passes from source", selftest(8194), 0)


test_version()
test_store_file()
test_apply_is_live()
test_settings_page()
test_setup_and_autostart()
test_server_thread()
print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
