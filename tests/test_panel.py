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
      function toBottom() {}          // scrolling is covered by test_scroll
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


# ------------------------------------------------- when is a sentence finished?

def test_sentences() -> None:
    logic = lift("  const SENTENCE_END =", "\n  // The translation companion")
    c = quickjs.Context()
    c.eval(logic)

    print("Sentence end: recognised")
    for t in ["Hello there.", "Can you hear me?", "Stop!", "He said \u201Cyes.\u201D",
              "Wait\u2026", "\u0628\u0644\u0647\u061F", "It is done (finally.)"]:
        got = bool(c.eval(f"endsSentence({t!r})".replace("'", '"')))
        check(f"  {t!r}", got, True)

    print("Sentence end: NOT a full stop")
    # Translating on an abbreviation would cut a sentence in half mid-flow, which
    # is the exact churn this is meant to remove.
    for t in ["So the pipeline", "I think we should", "That costs 3.",
              "See Fig.", "Ask Dr.", "e.g.", "Mohamad M."]:
        got = bool(c.eval(f"endsSentence({t!r})".replace("'", '"')))
        check(f"  {t!r}", got, False)

    print("Punctuation: adapt to a stream that has none")
    # Holding 2s for a full stop that never arrives would add two seconds to every
    # line -- worse than the churn it avoids.
    check("optimistic before evidence", bool(c.eval("punctuates()")), True)
    c.eval("for (let i = 0; i < 6; i++) punctHistory.push(true);")
    check("stream punctuates", bool(c.eval("punctuates()")), True)
    c.eval("punctHistory.length = 0; for (let i = 0; i < 6; i++) punctHistory.push(false);")
    check("six bare lines -> stop waiting", bool(c.eval("punctuates()")), False)
    c.eval("punctHistory.push(true);")
    check("one full stop -> wait again", bool(c.eval("punctuates()")), True)


# ------------------------------------------------------------- scroll stickiness

def test_scroll() -> None:
    block = lift("  const STICK_PX = 40;", "\n  $jump.onclick")

    c = quickjs.Context()
    c.eval("""
      let onScroll = null;
      const $log = { scrollTop: 0, scrollHeight: 1000, clientHeight: 200,
                     addEventListener: (_, fn) => { onScroll = fn; } };
      const $jump = { style: {} };
    """ + block)

    print("Scroll: following the newest caption")
    c.eval("$log.scrollTop = 800; toBottom();")
    check("at the bottom, new caption arrives", c.eval("$log.scrollTop"), 1000)

    print("Scroll: reading something further up")
    # The reported bug: every partial caption update called this, several times a
    # second, so scrolling up to re-read a line was impossible.
    c.eval("$log.scrollTop = 100; $log.scrollHeight = 1200; stick = false;")
    c.eval("toBottom();")
    check("new caption must NOT move the view", c.eval("$log.scrollTop"), 100)
    c.eval("toBottom(); toBottom(); toBottom();")
    check("nor after several more", c.eval("$log.scrollTop"), 100)

    print("Scroll: getting back")
    c.eval("toBottom(true);")
    check("the jump pill", c.eval("$log.scrollTop"), 1200)
    check("pill hidden again", c.eval("$jump.style.display"), "none")

    print("Scroll: the bottom is a tolerance, not an equality")
    # scrollTop is fractional and content grows between frames; an equality test
    # would unstick permanently the first time it was off by half a pixel.
    c.eval("$log.scrollTop = 1200 - 200 - 20;")
    check("20px off the bottom still counts", bool(c.eval("atBottom()")), True)
    c.eval("$log.scrollTop = 1200 - 200 - 120;")
    check("120px off does not", bool(c.eval("atBottom()")), False)


# ------------------------------------------------------ how long before we translate

def test_wait() -> None:
    logic = lift("  const SENTENCE_END =", "\n  // The translation companion")
    line = lift("      const wait = endsSentence", "\n\n      const timer")

    c = quickjs.Context()
    c.eval("const SETTLE_MS = 700, HOLD_MS = 2000;\n" + logic
           + "\nfunction waitFor(text) {" + line + "\n  return wait; }")

    print("Wait: a finished sentence goes straight out")
    check("'Can you hear me?'", c.eval('waitFor("Can you hear me?")'), 700)
    check("'It is deployed.'", c.eval('waitFor("It is deployed.")'), 700)

    print("Wait: a half-finished one is held")
    # This is the whole point. The speaker pauses to think, the fragment settles,
    # and the old code translated it -- then translated the finished sentence
    # again, rewriting the Persian under the reader.
    check("'So the pipeline'", c.eval('waitFor("So the pipeline")'), 2000)
    check("'I think we should'", c.eval('waitFor("I think we should")'), 2000)

    print("Wait: an unpunctuated stream is not held at all")
    c.eval("for (let i = 0; i < 6; i++) punctHistory.push(false);")
    check("'so the pipeline'", c.eval('waitFor("so the pipeline")'), 700)


# ------------------------------------------------------------------- panel sizes

def test_sizes() -> None:
    body = lift("  function setSize(ix) {", "\n  }\n", "\n  }\n")

    c = quickjs.Context()
    c.eval("""
      const SIZES = ["small", "large", "full"];
      let sizeIx = 0, scrolled = 0;
      const classes = new Set();
      const panel = { style: {}, classList: {
        toggle: (c, on) => on ? classes.add(c) : classes.delete(c) } };
      const $max = {};
      function toBottom() { scrolled++; }
    """ + body)

    cls = lambda: sorted(c.eval("[...classes].join(',')").split(",")) if c.eval("classes.size") else []

    print("Sizes: one button cycles three")
    c.eval("setSize(0)"); check("small", cls(), [])
    c.eval("setSize(1)"); check("large", cls(), ["max"])
    c.eval("setSize(2)"); check("full", cls(), ["full"])
    c.eval("setSize(3)"); check("wraps to small", cls(), [])

    print("Sizes: a dragged or resized panel can still be resized by the button")
    # Dragging writes left/top/right/bottom and the resize handle writes
    # width/height. An inline style beats a class, so without clearing them the
    # size button does nothing at all -- exactly when someone wants a bigger view.
    c.eval("""panel.style.left = "40px"; panel.style.top = "80px";
              panel.style.right = "auto"; panel.style.bottom = "auto";
              panel.style.width = "300px"; panel.style.height = "200px";""")
    c.eval("setSize(2)")
    leftovers = [p for p in ("left", "top", "right", "bottom", "width", "height")
                 if c.eval(f'panel.style.{p} || ""')]
    check("inline styles cleared", leftovers, [])
    check("full applied", cls(), ["full"])

    print("Sizes: the tooltip names the next size, so the button is not a guess")
    c.eval("setSize(0)"); check("from small", c.eval("$max.title"), "small \u2014 click for large")
    c.eval("setSize(2)"); check("from full",  c.eval("$max.title"), "full \u2014 click for small")


# ------------------------------------------------------------------ dragging

def _drag_harness(body: str) -> quickjs.Context:
    c = quickjs.Context()
    c.eval("""
      const SIZES = ["small", "large", "full"];
      let sizeIx = 0;
      const innerWidth = 1600, innerHeight = 900;
      const handlers = {};
      const rect = { left: 1204, top: 500, width: 380, height: 300 };
      const panel = {
        style: {},
        getBoundingClientRect: () => rect,
        querySelector: () => ({ addEventListener: (t, fn) => { handlers[t] = fn; } }),
      };
      const document = { addEventListener: (t, fn) => { handlers[t] = fn; } };
      function fire(type, x, y, tag) {
        handlers[type] && handlers[type]({
          clientX: x, clientY: y, target: { tagName: tag || "SPAN" },
          preventDefault: () => {},
        });
      }
      function inline() {
        return ["left", "top", "right", "bottom"]
          .filter(p => panel.style[p]).map(p => p + ":" + panel.style[p]).join(" ");
      }
    """ + body)
    return c


def test_drag() -> None:
    body = lift("  // Drag by the header", "\n  loadConfig();")
    c = _drag_harness(body)

    print("Drag: a click that does not move must change nothing")
    # The bug: mousedown set right:auto and bottom:auto without setting left/top.
    # A fixed element with all four insets auto falls to its static position --
    # the end of <body>, off the page. One click and the panel was gone, with no
    # way back but a reload, which also discards the transcript.
    c.eval('fire("mousedown", 1300, 510)')
    check("nothing written on mousedown", c.eval("inline()"), "")
    c.eval('fire("mouseup", 1300, 510)')
    check("nothing written after mouseup", c.eval("inline()"), "")

    print("Drag: a 2px wobble is still a click")
    c.eval('fire("mousedown", 1300, 510); fire("mousemove", 1302, 511);')
    check("below the threshold", c.eval("inline()"), "")
    c.eval('fire("mouseup", 1302, 511)')

    print("Drag: a real drag re-anchors and moves")
    c.eval('fire("mousedown", 1300, 510); fire("mousemove", 1100, 400);')
    check("left set",   c.eval("panel.style.left"),   "1004px")
    check("top set",    c.eval("panel.style.top"),    "390px")
    check("right freed",  c.eval("panel.style.right"),  "auto")
    check("bottom freed", c.eval("panel.style.bottom"), "auto")

    print("Drag: cannot be thrown off screen and lost")
    # A panel dragged outside the viewport cannot be grabbed again -- the same lost
    # panel as the bug above, just more slowly.
    c.eval('fire("mousemove", -9000, -9000)')
    check("clamped left",  c.eval("parseInt(panel.style.left)"), 120 - 380)
    check("clamped top",   c.eval("parseInt(panel.style.top)"), 0)
    c.eval('fire("mousemove", 9000, 9000)')
    check("clamped right", c.eval("parseInt(panel.style.left)"), 1600 - 120)
    check("clamped bottom",c.eval("parseInt(panel.style.top)"), 900 - 32)
    c.eval('fire("mouseup", 0, 0)')

    print("Drag: the header buttons are not drag handles")
    c.eval("panel.style = {};")
    c.eval('fire("mousedown", 1300, 510, "BUTTON"); fire("mousemove", 1000, 300);')
    check("a button press does not drag", c.eval("inline()"), "")

    print("Drag: full screen is not draggable")
    c.eval("panel.style = {}; sizeIx = 2;")
    c.eval('fire("mousedown", 800, 10); fire("mousemove", 400, 300);')
    check("full screen ignores the header", c.eval("inline()"), "")


# ----------------------------------------------- the scroll position survives hiding

def test_log_hide() -> None:
    block = lift("  let logScroll = 0, logStick = true;", "\n  function meta(")

    c = quickjs.Context()
    c.eval("""
      const $log = { scrollTop: 0, scrollHeight: 2000, clientHeight: 200, style: {} };
      const $jump = { style: {} };
      let stick = true;
      function toBottom(force) { if (force) stick = true;
        if (stick) { $log.scrollTop = $log.scrollHeight; $jump.style.display = "none"; } }
      // display:none discards scrollTop, exactly as a browser does.
      function hide() { hideLog(); $log.scrollTop = 0; }
    """ + block)

    print("Hiding: reading something further up, then a round trip to Summary")
    # This is the reported bug. Hiding an element drops its scroll position, so
    # coming back put the reader at the oldest caption with no warning.
    c.eval("$log.scrollTop = 640; stick = false;")
    c.eval("hide()")
    check("scrollTop wiped while hidden", c.eval("$log.scrollTop"), 0)
    c.eval("showLog()")
    check("put back where they were", c.eval("$log.scrollTop"), 640)
    check("still not following", bool(c.eval("stick")), False)
    check("jump pill still offered", c.eval("$jump.style.display"), "block")

    print("Hiding: following the newest line, then a round trip")
    c.eval("stick = true; $log.scrollTop = 2000;")
    c.eval("hide(); $log.scrollHeight = 2600; showLog()")
    # Following the conversation means the NEWEST line, not the line that was
    # newest when the tab was left.
    check("goes to the new bottom", c.eval("$log.scrollTop"), 2600)
    check("pill hidden", c.eval("$jump.style.display"), "none")

    print("Hiding: hiding twice must not overwrite the saved position")
    # Summary tab then collapse, or any double-hide: the second call sees a
    # scrollTop of 0 and would save that as the place to return to.
    c.eval("stick = false; $log.scrollTop = 1234;")
    c.eval("hide(); hide(); showLog()")
    check("original position kept", c.eval("$log.scrollTop"), 1234)


test_panel()
test_seal()
test_sentences()
test_wait()
test_scroll()
test_log_hide()
test_sizes()
test_drag()
print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
