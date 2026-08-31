"""
Tests for the panel's open/close state machine and the transcript seal.

Both blocks under test are LIFTED VERBATIM from extension/content.js rather than
restated here. A test that quotes its own copy of the logic proves only that the
copy is self-consistent; this one breaks if someone changes the real code.

    pip install quickjs
    python3 tests/test_panel.py
"""
from __future__ import annotations

import pathlib
import sys

import quickjs

SRC = (pathlib.Path(__file__).resolve().parent.parent / "extension" / "content.js").read_text()
fails: list[str] = []


def lift(start: str, end: str, tail: str = "") -> str:
    """Pull a block out of content.js by its first and last line."""
    try:
        i = SRC.index(start)
        j = SRC.index(end, i)
    except ValueError:
        sys.exit(f"could not find this block in content.js -- has it been renamed?\n  {start.strip()}")
    return SRC[i:j] + tail


def check(label: str, got, want) -> None:
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: {got!r}" + ("" if ok else f"  (wanted {want!r})"))
    if not ok:
        fails.append(label)


# --------------------------------------------------------------- panel open/close

def test_panel() -> None:
    setter = lift("  function setPanelOpen(open, byUser = false) {", "\n  }\n", "\n  }\n")
    decide = lift("    if (captionsOn !== captionsWere) {", "\n    if (state.container) {")

    c = quickjs.Context()
    c.eval("""
      let panelOpen = false, override = null, captionsWere = false, flushes = 0;
      const panel = { style: {} }, launcher = { style: {} };
      const $log = { scrollTop: 0, scrollHeight: 0 };
      function flushTranscript() { flushes++; }
    """ + setter + "function poll(captionsOn) {" + decide + "}")

    poll = lambda on: c.eval(f"poll({str(bool(on)).lower()})")
    open_ = lambda: bool(c.eval("panelOpen"))

    print("Panel: opened from the pill with no captions running")
    poll(False);  check("idle", open_(), False)
    c.eval("setPanelOpen(true, true)")
    check("clicked the pill", open_(), True)
    for n in (1, 2, 3):
        poll(False)
        # The bug this replaces: the poller shut the panel one tick after the
        # pill opened it, because only "the user closed it" was recorded.
        check(f"+{n}s poller ran", open_(), True)
    poll(True);   check("captions appear", open_(), True)

    print("Panel: captions stop and start")
    poll(False);  check("captions off", open_(), False)
    poll(True);   check("captions on", open_(), True)

    print("Panel: dismissed by hand mid-meeting")
    c.eval("setPanelOpen(false, true)")
    check("clicked x", open_(), False)
    poll(True);   check("+1s captions still on", open_(), False)
    poll(True);   check("+2s stays dismissed", open_(), False)
    poll(False);  check("captions off", open_(), False)
    # A dismissal lasts for that caption session only -- otherwise closing it once
    # would silently disable the tool for the rest of the day.
    poll(True);   check("next session reopens", open_(), True)

    print("Panel: loaded into a meeting already running captions")
    c.eval("panelOpen=false; override=null; captionsWere=false;")
    poll(True);   check("first poll", open_(), True)

    print("Panel: the end-of-session flush fires once, not every second")
    c.eval("panelOpen=false; override=null; captionsWere=true; flushes=0;")
    poll(False);  check("on the edge", c.eval("flushes"), 1)
    poll(False); poll(False)
    check("two more idle polls", c.eval("flushes"), 1)


# ------------------------------------------------------------------- the seal

def test_seal() -> None:
    fn = lift("  function unsaved(force) {", "\n  }\n", "\n  }\n")

    c = quickjs.Context()
    c.eval("const SEAL_MS = 20000, FLUSH_MAX = 200;\nlet now = 0;\n"
           "const Date = { now: () => now };\nlet transcript = [];\n" + fn)

    def load(records):
        c.eval("transcript = " + repr(records).replace("'", '"')
               .replace("True", "true").replace("False", "false") + ";")

    print("Seal: only segments that stopped changing get written")
    load([{"id": "1", "firstSeen": 0,     "saved": False},
          {"id": "2", "firstSeen": 25000, "saved": False},
          {"id": "3", "firstSeen": 29500, "saved": False}])
    c.eval("now = 30000;")
    check("30s in, 20s seal", c.eval("unsaved(false).map(r => r.id).join(',')"), "1")
    check("force ignores the seal", c.eval("unsaved(true).map(r => r.id).join(',')"), "1,2,3")

    print("Seal: already-written segments are never resent")
    load([{"id": "1", "firstSeen": 0, "saved": True},
          {"id": "2", "firstSeen": 0, "saved": False}])
    check("one saved, one not", c.eval("unsaved(true).map(r => r.id).join(',')"), "2")

    print("Seal: a backlog is capped per request")
    # keepalive request bodies are limited to 64KB, so a long outage must drain
    # over several flushes rather than fail forever on one oversized POST.
    c.eval("transcript = Array.from({length: 500}, (_, i) => "
           "({id: String(i), firstSeen: 0, saved: false}));")
    check("500 pending", c.eval("unsaved(true).length"), 200)


test_panel()
test_seal()
print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
