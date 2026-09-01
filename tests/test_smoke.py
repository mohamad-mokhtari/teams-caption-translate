"""
Run the real extension against a fake Teams page and a fake service.

This is the test that would have caught both regressions that reached a live
meeting. It feeds captions to the actual content.js and asserts that the panel
shows them.

    server/.venv/bin/python tests/test_smoke.py
"""
from __future__ import annotations

import json
import pathlib
import sys

import quickjs

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOM = (ROOT / "tests" / "dom.js").read_text()
SRC = (ROOT / "extension" / "content.js").read_text()

fails: list[str] = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: {got!r}" + ("" if ok else f"  (wanted {want!r})"))
    if not ok:
        fails.append(label)


PAGE = """
// A Teams caption area, in the markup content.js was built against.
let ccWindow = document.createElement("div");
ccWindow.setAttribute("data-tid", "closed-caption-v2-window");
document.body.appendChild(ccWindow);

let lineNo = 0;
const lines = {};

/** Say something, or revise what was last said under the same id. */
function say(speaker, text, id) {
  id = id || ("line" + (++lineNo));
  let line = lines[id];
  if (!line) {
    line = document.createElement("div");
    line.setAttribute("data-tid", "closed-caption-line");
    line.id = id;
    const who = document.createElement("span");
    who.setAttribute("data-tid", "author");
    who.textContent = speaker;
    const txt = document.createElement("span");
    txt.setAttribute("data-tid", "closed-caption-text");
    line.appendChild(who);
    line.appendChild(txt);
    ccWindow.appendChild(line);
    lines[id] = line;
  }
  line.querySelector('[data-tid="closed-caption-text"]').textContent = text;
  return id;
}

/** What the panel is showing: one entry per caption row. */
function panelRows() {
  const log = document.querySelector("#mct-log");
  if (!log) return [];
  return log.querySelectorAll(".mct-seg").map(r => ({
    speaker: r.querySelector(".mct-spk").textContent,
    text:    r.querySelector(".mct-txt").textContent,
    tr:      r.querySelector(".mct-tr").textContent,
  }));
}
/**
 * Throw away the caption area and build a new one, leaving the old node in the
 * page with its last lines still in it -- which is what Teams does, and what made
 * the old liveness check believe everything was fine forever.
 */
function moveCaptionsToANewContainer() {
  ccWindow.removeAttribute("data-tid");     // the old one stops being the live one
  ccWindow = document.createElement("div");
  ccWindow.setAttribute("data-tid", "closed-caption-v2-window");
  document.body.appendChild(ccWindow);
  for (const k of Object.keys(lines)) delete lines[k];
}

/** Rows the reader can actually see, ignoring any a filter hid. */
function visibleRows() {
  const log = document.querySelector("#mct-log");
  if (!log) return [];
  return log.querySelectorAll(".mct-seg")
    .filter(r => r.style.display !== "none")
    .map(r => ({ speaker: r.querySelector(".mct-spk").textContent,
                 text: r.querySelector(".mct-txt").textContent }));
}
function click(sel) { const n = document.querySelector(sel); if (n && n.onclick) n.onclick(); }
function clickChip(name) {
  for (const c of document.querySelectorAll(".mct-chip"))
    if (c.textContent.startsWith(name)) { c.onclick(); return true; }
  return false;
}
function panelNotes() {
  const log = document.querySelector("#mct-log");
  return log ? log.querySelectorAll(".mct-note").map(n => n.textContent) : [];
}
"""

SERVICE = """
let detectAnswer = null;          // {lang, name} or null for "und"
let pinned = true;                // does the deployment configure a language?
let translatePrefix = "TR:";

routes["/config"] = () => ({
  target_lang: "fa", target_lang_name: "Persian (Farsi)", rtl: true, script: "arab",
  provider: "openai", context_segments: 3, target_lang_pinned: pinned,
  transcript_enabled: true, transcript_dir: "/tmp/x",
  languages: [
    {code:"en", name:"English",         native:"English", script:"latn", rtl:false},
    {code:"fa", name:"Persian (Farsi)", native:"Farsi",   script:"arab", rtl:true},
    {code:"es", name:"Spanish",         native:"Espanol", script:"latn", rtl:false},
  ],
});
routes["/detect"] = () => detectAnswer
  ? { lang: detectAnswer.lang, name: detectAnswer.name, confident: true, ms: 10 }
  : { lang: "und", name: "", confident: false, ms: 0 };
routes["/translate"] = (b) => ({
  translation: translatePrefix + b.text, cached: false, ms: 12,
  provider: "openai", key: b.key, passthrough: false,
});
routes["/transcript"] = () => ({ path: "/tmp/x/s.md", directory: "/tmp/x",
                                 written: 0, total: 0, bytes: 0 });
routes["/summarize"] = () => ({ summary: "s", segments: 1, ms: 1, provider: "openai" });
"""


def pump(c: quickjs.Context, rounds: int = 200) -> None:
    """Run queued promise callbacks.

    quickjs only advances promises when asked. Without this the extension's
    fetches resolve into nothing: /config never lands, so `server.ok` stays false
    and no translation is ever requested. The first run of this harness looked
    exactly like the bug it was written to find, for that reason and not the real
    one -- worth remembering before trusting a red result here.
    """
    while rounds > 0 and c.execute_pending_job():
        rounds -= 1


def tick(c: quickjs.Context, ms: int) -> None:
    """Let time pass, draining promises around every timer that fires."""
    pump(c)
    c.eval(f"advance({ms})")
    pump(c)
    c.eval("flushMutations()")
    pump(c)


def boot(setup: str = "") -> quickjs.Context:
    c = quickjs.Context()
    c.eval(DOM)
    c.eval(SERVICE)
    c.eval(PAGE)
    if setup:
        c.eval(setup)
    c.eval(SRC)
    tick(c, 200)                # let loadConfig resolve
    return c


def chosen(c):
    """The language shown in the picker — what the reader would see selected."""
    return c.eval('document.querySelector("#mct-lang").value || ""')


def is_passthrough(c):
    return bool(c.eval('document.querySelector("#mct-panel").classList.contains("passthrough")'))


def rows(c):
    return c.eval("JSON.stringify(panelRows())")


# ---------------------------------------------------------------------------

def test_captions_reach_the_panel():
    print("Smoke: captions arrive, settle, and are translated")
    c = boot('detectAnswer = {lang:"en", name:"English"};')
    c.eval('say("Sarah", "Good morning everyone.")')
    tick(c, 1500)

    got = json.loads(rows(c))
    check("  one row", len(got), 1)
    if got:
        check("  speaker", got[0]["speaker"], "Sarah")
        check("  caption text", got[0]["text"], "Good morning everyone.")
        check("  translated", got[0]["tr"], "TR:Good morning everyone.")


def test_it_keeps_going():
    """
    The regression that reached a live meeting.

    A deployment with TARGET_LANG=fa, a reader on an English browser, and an
    English meeting. The panel translated the first couple of lines and then went
    quiet: the browser guess had replaced Persian with English, detection found
    the captions were English too, and pass-through correctly stopped translating
    something that no longer needed it. Every step was working; the combination
    was not.
    """
    print("Smoke: a configured language survives an English browser")
    c = boot('detectAnswer = {lang:"en", name:"English"};')
    check("  reader's language", chosen(c), "fa")
    check("  not pass-through", is_passthrough(c), False)

    said = [
        ("Sarah",  "Good morning everyone, thanks for joining us today."),
        ("Sarah",  "Today we are going to review the feature extraction pipeline."),
        ("Mohamad","Sorry, could you repeat the part about the deployment?"),
        ("Sarah",  "Of course. We deploy the new model on Friday afternoon."),
        ("Mohamad","Understood, thank you very much."),
        ("Sarah",  "Any other questions before we finish?"),
    ]
    for who, what in said:
        c.eval(f'say({who!r}, {what!r})'.replace("'", '"'))
        tick(c, 1500)

    got = json.loads(rows(c))
    check("  every line rendered", len(got), len(said))
    check("  the last line is there",
          got[-1]["text"] if got else "", said[-1][1])
    missing = [r["text"] for r in got if not r["tr"]]
    check("  every line translated", missing, [])


def test_guess_when_nothing_is_configured():
    print("Smoke: with nothing configured, the browser's language is used")
    c = boot('pinned = false; detectAnswer = {lang:"es", name:"Spanish"};')
    check("  guessed from en-US", chosen(c), "en")


def test_detection_note():
    print("Smoke: detection adds one divider, and only one")
    c = boot('detectAnswer = {lang:"es", name:"Spanish"};')
    for i in range(6):
        c.eval(f'say("Carlos", "Buenos dias a todos, esta es la linea numero {i} de la reunion.")')
        tick(c, 1500)
    notes = json.loads(c.eval("JSON.stringify(panelNotes())"))
    check("  one divider", len(notes), 1)
    check("  names both languages",
          bool(notes) and "Spanish" in notes[0] and "Persian" in notes[0], True)


def test_passthrough():
    print("Smoke: a reader whose language matches is not translated for")
    c = boot('detectAnswer = {lang:"fa", name:"Persian (Farsi)"};')
    for i in range(6):
        c.eval(f'say("Ali", "This is line number {i} of a reasonably long meeting sentence.")')
        tick(c, 1500)
    got = json.loads(rows(c))
    check("  captions still shown", len(got), 6)
    check("  marked pass-through", is_passthrough(c), True)

    # Detection needs a couple of lines of text before it can answer, so the lines
    # before it lands are translated -- unavoidable, and hidden by the CSS the
    # moment pass-through turns on. What matters is that it then STOPS.
    before = c.eval("calls.filter(x=>x.path=='/translate').length")
    for i in range(6):
        c.eval(f'say("Ali", "Another perfectly ordinary sentence, number {i + 10} of this meeting.")')
        tick(c, 1500)
    check("  translating stops once it is known",
          c.eval("calls.filter(x=>x.path=='/translate').length"), before)
    check("  captions keep arriving", len(json.loads(rows(c))), 12)

    print("Smoke: and it says why, rather than just going quiet")
    # The lane vanishing with no explanation is what made a correct state look
    # like a broken tool.
    why = c.eval('document.querySelector("#mct-why").textContent')
    check("  explains itself", "already in Persian (Farsi)" in why, True)
    check("  offers the way out", "pick a different one" in why, True)



def test_summary_round_trip():
    """
    Reported from a real meeting: after showing a colleague the Summary tab and
    coming back, only one person's new captions appeared.
    """
    print("Smoke: two speakers, a trip to Summary, and back")
    c = boot('detectAnswer = {lang:"en", name:"English"};')

    for who, what in [("Mohamad", "Good morning, can everybody hear me clearly today?"),
                      ("Reza",    "Yes, I can hear you perfectly well thank you.")]:
        c.eval(f'say("{who}", "{what}")')
        tick(c, 1500)
    check("  both speakers before", len(json.loads(c.eval("JSON.stringify(visibleRows())"))), 2)

    c.eval('click("#mct-tab-sum")');  tick(c, 300)
    c.eval('click("#mct-tab-live")'); tick(c, 300)

    for who, what in [("Mohamad", "Right, let me carry on with the next point please."),
                      ("Reza",    "Sure, go ahead, I am following along with you.")]:
        c.eval(f'say("{who}", "{what}")')
        tick(c, 1500)

    seen = json.loads(c.eval("JSON.stringify(visibleRows())"))
    check("  all four visible after", len(seen), 4)
    check("  both speakers still visible",
          sorted({r["speaker"] for r in seen}), ["Mohamad", "Reza"])


def test_filter_survives_summary():
    """The same trip, but with a speaker filter deliberately left on."""
    print("Smoke: a filter left on, then Summary and back")
    c = boot('detectAnswer = {lang:"en", name:"English"};')
    for who, what in [("Mohamad", "Good morning, can everybody hear me clearly today?"),
                      ("Reza",    "Yes, I can hear you perfectly well thank you.")]:
        c.eval(f'say("{who}", "{what}")')
        tick(c, 1500)

    c.eval('clickChip("Reza")'); tick(c, 100)
    check("  filtered to Reza", len(json.loads(c.eval("JSON.stringify(visibleRows())"))), 1)

    c.eval('click("#mct-tab-sum")');  tick(c, 300)
    c.eval('click("#mct-tab-live")'); tick(c, 300)
    c.eval('say("Mohamad", "Another sentence from me while the filter is still on.")')
    tick(c, 1500)

    seen = json.loads(c.eval("JSON.stringify(visibleRows())"))
    check("  still Reza only", sorted({r["speaker"] for r in seen}), ["Reza"])
    check("  the chip still says so",
          bool(c.eval('document.querySelectorAll(".mct-chip").some('
                      'x => x.textContent.startsWith("Reza") && x.classList.contains("on"))')), True)

    print("Smoke: a filter keeps saying who it is hiding")
    # It used to say so in the status line, which emit() rewrites on every
    # caption -- so a filter left on by a stray click on the chip row became
    # invisible within a second, and looked like one person's captions had
    # stopped arriving.
    check("  banner is up",
          bool(c.eval('document.querySelector("#mct-filtered").classList.contains("on")')), True)
    check("  names who",
          "Showing only Reza" in c.eval('document.querySelector("#mct-filtered-txt").textContent'), True)

    c.eval('say("Reza", "A few more sentences go by while the filter is still set.")')
    tick(c, 1500)
    c.eval('say("Reza", "And another one after that, just to be sure about it.")')
    tick(c, 1500)
    check("  still up several captions later",
          bool(c.eval('document.querySelector("#mct-filtered").classList.contains("on")')), True)

    print("Smoke: and offers the way out")
    c.eval('click("#mct-filtered-clear")'); tick(c, 100)
    check("  banner gone",
          bool(c.eval('document.querySelector("#mct-filtered").classList.contains("on")')), False)
    check("  everyone back",
          sorted({r["speaker"] for r in json.loads(c.eval("JSON.stringify(visibleRows())"))}),
          ["Mohamad", "Reza"])



def test_captions_move():
    """
    Reported from a real meeting: captions kept appearing in Teams but stopped
    reaching the panel, and only reloading the page and rejoining fixed it.

    Teams can start writing captions into a new element while the old one stays
    in the page holding its last few lines. The old check asked whether our
    container still HELD caption text and read that as healthy -- so it never
    looked for the new one.
    """
    print("Smoke: Teams moves its captions to a new element")
    c = boot('detectAnswer = {lang:"en", name:"English"};')
    c.eval('say("Sarah", "This is the first sentence, before anything moves.")')
    tick(c, 1500)
    check("  captured before", len(json.loads(rows(c))), 1)

    c.eval("moveCaptionsToANewContainer()")
    tick(c, 2000)
    c.eval('say("Sarah", "And this one arrives after Teams rebuilt the caption area.")')
    tick(c, 2000)

    got = json.loads(rows(c))
    check("  captured after the move", len(got), 2)
    check("  the new line is there",
          got[-1]["text"] if got else "",
          "And this one arrives after Teams rebuilt the caption area.")
    check("  and it was translated", bool(got and got[-1]["tr"]), True)


def test_reconnect_button():
    """The button used to re-check the translator only, which was never the thing
    that had broken."""
    print("Smoke: the reconnect button re-finds the captions")
    c = boot('detectAnswer = {lang:"en", name:"English"};')
    c.eval('say("Sarah", "One sentence to get us attached to something.")')
    tick(c, 1500)

    c.eval("moveCaptionsToANewContainer()")
    c.eval('click("#mct-retry")')
    tick(c, 300)
    c.eval('say("Sarah", "A sentence right after pressing reconnect.")')
    tick(c, 1500)

    got = json.loads(rows(c))
    check("  back to capturing", len(got), 2)
    check("  without a page reload", got[-1]["text"] if got else "",
          "A sentence right after pressing reconnect.")


def test_no_reattach_thrash():
    """A shadow-piercing search must not mistake our own container's contents for
    captions living somewhere else."""
    print("Smoke: a healthy meeting does not re-attach on a loop")
    c = boot('detectAnswer = {lang:"en", name:"English"};')
    c.eval("logLines.length = 0;")
    for i in range(5):
        c.eval(f'say("Sarah", "An ordinary sentence, number {i}, in a healthy meeting.")')
        tick(c, 1500)
    n = c.eval('logLines.filter(l => l[1].indexOf("re-attaching") >= 0).length')
    check("  no re-attaches", n, 0)


test_captions_move()
test_reconnect_button()
test_no_reattach_thrash()
test_summary_round_trip()
test_filter_survives_summary()
test_captions_reach_the_panel()
test_it_keeps_going()
test_guess_when_nothing_is_configured()
test_detection_note()
test_passthrough()
print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
