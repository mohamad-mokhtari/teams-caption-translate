"""
The reader chooses their own language; the source is detected, never configured.

Covers the server's half (per-request target, pass-through, the language table)
and the extension's half, lifted verbatim from content.js.

    server/.venv/bin/python tests/test_language.py     # server half
    python3 tests/test_language.py                     # both, if quickjs is installed
"""
from __future__ import annotations

import os
import pathlib
import sys
from pathlib import Path

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

# Before any app import: pydantic reads the environment when Settings is
# constructed, so setting this later has no effect and the test writes into the
# real transcript folder -- which is how this was first noticed.
import tempfile  # noqa: E402
os.environ.setdefault("TRANSCRIPT_DIR", tempfile.mkdtemp(prefix="mct-lang-"))

fails: list[str] = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: {got!r}" + ("" if ok else f"  (wanted {want!r})"))
    if not ok:
        fails.append(label)


# ------------------------------------------------------------------ the table

def test_table():
    from app import languages as L

    print("Table: codes resolve the way real tags arrive")
    for tag, want in [("fa", "fa"), ("fa-IR", "fa"), ("en-US", "en"), ("pt-BR", "pt"),
                      ("zh-TW", "zh-TW"), ("ZH-tw", "zh-TW"), ("zh", "zh"),
                      ("fr_CA", "fr"), ("xx", None), ("", None)]:
        row = L.get(tag)
        check(f"  {tag!r}", row["code"] if row else None, want)

    print("Table: right-to-left follows the script, not a hand-kept list")
    for code, want in [("fa", True), ("ar", True), ("he", True), ("ur", True),
                       ("ps", True), ("ckb", True),
                       ("en", False), ("ja", False), ("ru", False)]:
        check(f"  {code}", L.is_rtl(code), want)

    print("Table: every language has a script the panel styles")
    styled = {"latn", "cyrl", "grek", "armn", "geor", "arab", "hebr", "deva",
              "beng", "guru", "taml", "telu", "thai", "hans", "hant", "jpan", "kore"}
    unstyled = sorted({l["script"] for l in L.LANGUAGES} - styled)
    check("  no unstyled scripts", unstyled, [])


# ------------------------------------------------------------- the translator

def test_passthrough():
    from app.translate import translate

    print("Server: the reader whose language matches the meeting is not translated")
    # Your scenario: a Spanish meeting with Persian, English and Spanish readers.
    # The Spanish reader must not pay for, wait on, or read a second copy of every
    # line -- and the model cannot be trusted to notice: asked to translate
    # captions into Spanish and handed Spanish, gpt-4o-mini returns English.
    out = translate("Buenos dias a todos.", target="es", source="es")
    check("  returned unchanged", out["translation"], "Buenos dias a todos.")
    check("  flagged as pass-through", out["passthrough"], True)
    check("  cost nothing", out["ms"], 0.0)
    check("  called no provider", out["provider"], "none")

    print("Server: region variants are the same language")
    out = translate("Hola.", target="es", source="es-MX")
    check("  es-MX vs es", out["passthrough"], True)

    print("Server: two unknown codes are not 'the same language'")
    # get() returns None for both; an identity test on None would make every
    # unrecognised pair a pass-through and silently stop translating.
    out = translate("", target="xx", source="yy")
    check("  not a match", out["passthrough"], False)

    print("Server: the source is only ever a hint")
    from app.translate import build_messages
    sys_hint, _ = build_messages("x", [], "Persian (Farsi)", "Spanish")
    check("  hint present", "appear to be in Spanish" in sys_hint, True)
    check("  hedged", "trust the text in front of you" in sys_hint, True)
    sys_none, _ = build_messages("x", [], "Persian (Farsi)")
    check("  omitted when unknown", "appear to be in" in sys_none, False)
    check("  never says 'from English'", "from English" in sys_none, False)


# --------------------------------------------------------------- the extension

def test_extension():
    try:
        import quickjs
    except ImportError:
        print("\n(skipping the extension half: pip install quickjs)")
        return

    src = (ROOT / "extension" / "content.js").read_text()

    def lift(start, end, tail=""):
        i = src.index(start)
        return src[i:src.index(end, i)] + tail

    helpers = lift("  const targetRow  =", "\n  /**\n   * Preferences live in extension storage")
    guess   = lift("  function guessTarget(fallback) {", "\n  }\n", "\n  }\n")
    trclass = lift("  const trClass = (error", "\n  function applyPassthrough")

    c = quickjs.Context()
    c.eval("""
      const prefs = { target: "" };
      const langs = { list: [], byCode: {} };
      const caption = { lang: "", name: "", asked: false, samples: [] };
      let navigator = { languages: [] };
      const LANGS = [
        {code:"en",name:"English",script:"latn",rtl:false},
        {code:"fa",name:"Persian (Farsi)",script:"arab",rtl:true},
        {code:"es",name:"Spanish",script:"latn",rtl:false},
        {code:"ja",name:"Japanese",script:"jpan",rtl:false},
        {code:"zh-TW",name:"Chinese (Traditional)",script:"hant",rtl:false},
      ];
      langs.list = LANGS;
      langs.byCode = Object.fromEntries(LANGS.map(l => [l.code, l]));
    """ + helpers + guess + trclass)

    print("Extension: nothing is translated when the reader matches the meeting")
    c.eval('prefs.target = "es"; caption.lang = "es";')
    check("  Spanish reader, Spanish meeting", bool(c.eval("passthrough()")), True)
    c.eval('prefs.target = "fa";')
    check("  Persian reader, same meeting", bool(c.eval("passthrough()")), False)

    print("Extension: pass-through needs BOTH sides known")
    c.eval('caption.lang = ""; prefs.target = "fa";')
    check("  before detection has run", bool(c.eval("passthrough()")), False)

    print("Extension: the row's classes follow the reader's language")
    c.eval('prefs.target = "fa";')
    check("  Persian", c.eval("trClass()"), "mct-tr rtl s-arab")
    c.eval('prefs.target = "ja";')
    check("  Japanese", c.eval("trClass()"), "mct-tr s-jpan")
    c.eval('prefs.target = "zh-TW";')
    check("  Chinese (Traditional)", c.eval("trClass()"), "mct-tr s-hant")
    check("  an error still shows as one", c.eval('trClass("boom")'), "mct-tr s-hant err")

    print("Extension: a first-time reader gets their own browser language")
    # Falling back to the service default would show everyone Persian, which is
    # right for one person and wrong for the rest of the company.
    c.eval('navigator = { languages: ["fr-CA", "en-GB"] };')
    check("  fr-CA unavailable -> en-GB", c.eval('guessTarget("fa")'), "en")
    c.eval('navigator = { languages: ["es-419"] };')
    check("  es-419 -> es", c.eval('guessTarget("fa")'), "es")
    c.eval('navigator = { languages: ["zh-TW"] };')
    check("  exact match wins", c.eval('guessTarget("fa")'), "zh-TW")
    c.eval('navigator = { languages: ["xx-YY"] };')
    check("  nothing matches -> the default", c.eval('guessTarget("fa")'), "fa")


# --------------------------------------------- a language change mid-meeting

def test_language_change():
    from app import transcript as T
    T._sessions.clear()

    n = [0]
    def rec(kind, text, tr="", spk="", ts="15:00:14"):
        n[0] += 1
        return {"id": str(n[0]), "kind": kind, "t": f"2026-09-01T{ts}Z",
                "speaker": spk, "text": text, "translation": tr}

    print("File: ten lines in Italian, then Persian from eleven on")
    # The panel used to wipe the Italian when the reader switched. The file never
    # did, and the two must agree: those lines were translated, they are correct,
    # and they are the part of a meeting someone scrolls back to check.
    r = T.append("2026-09-01_1500_x", "01/09/2026, 15:00", [
        rec("line", "Good morning.", "Buongiorno.", "Sarah"),
        rec("note", "Now translating into Persian (Farsi)", ts="15:02:03"),
        rec("line", "We deploy on Friday.", "\u062c\u0645\u0639\u0647 \u062f\u06cc\u067e\u0644\u0648\u06cc.", "Sarah", "15:02:05"),
    ], target_name="Italian")
    body = Path(r["path"]).read_text()

    check("  Italian kept", "Buongiorno." in body, True)
    check("  Persian added", "\u062c\u0645\u0639\u0647 \u062f\u06cc\u067e\u0644\u0648\u06cc." in body, True)
    check("  the change is marked", "Now translating into Persian" in body, True)
    check("  marked in the right place",
          body.index("Buongiorno.") < body.index("Now translating") < body.index("\u062c\u0645\u0639\u0647"), True)
    check("  header names the reader's language, not the server default",
          "**Reading in:** Italian" in body, True)

    print("File: a speaker is named again after a divider")
    # Sarah speaks on both sides of the note. Without resetting, the second run
    # would inherit the heading from before the break and read as unattributed.
    check("  Sarah named twice", body.count("**Sarah**"), 2)

    print("File: no doubled rule when a note is the very first thing written")
    T._sessions.clear()
    r2 = T.append("2026-09-01_1600_y", "", [rec("note", "Captions are in English", ts="16:00:01")])
    head = Path(r2["path"]).read_text()
    check("  one rule after the header", head.count("---"), 1)


test_table()
test_passthrough()
test_language_change()
test_extension()
print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
